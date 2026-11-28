"""
kg_extractor.py — LLM-powered entity & relationship extraction

Supports:
  - Local:  Ollama (text: qwen3.5:9b, vision: qwen3-vl:4b)
  - Cloud:  SiliconFlow API (text: Qwen3-32B, vision: Qwen3-VL-32B)
            DeepSeek via any OpenAI-compatible endpoint

Usage:
  python kg_extractor.py --text "The encoder uses self-attention..." --mode local
  python kg_extractor.py --file sample.txt --mode cloud --compare
"""

import argparse, json, os, sys, time, base64, re, threading, unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

from graph_store import merge_evidence


def _load_registry_env() -> None:
    """补齐进程内可能缺失的机器级环境变量。

    批量脚本经常从非交互 shell 启动，未必继承到注册表里的密钥；这里做一次
    兜底读取，避免"本地有 key、脚本却报 401"的假故障。必须在读取配置常量
    之前调用。

    ⚠️ 2026-09-11 硬约束变更：**禁止任何云端 API 支出**（用户明确不再充值）。
    因此不再把云端密钥变量注入进程——否则任何 `import kg_extractor` 的脚本
    都会自动获得密钥，存在误调用即扣费的风险。
    云端密钥必须由调用方**显式**通过环境变量传入（显式 export 不受此处影响，
    因为 os.environ.setdefault 只在缺失时写入）。
    """
    BLOCKED = {
        "DEEPSEEK_API_KEY",       # DeepSeek 官方
        "ANTHROPIC_AUTH_TOKEN",   # 兼容网关密钥（历史回退源，见下方 DS_API_KEY）
        "COMMAND_API_KEY",        # 命令平台网关
        "SF_API_KEY",             # SiliconFlow
        "OPENCODE_GO_API_KEY_1",
        "OPENCODE_GO_API_KEY_2",
        "OPENCODE_GO_API_KEY_3",
        "OPENCODE_GO_API_KEY_4",
    }
    try:
        import winreg
    except ImportError:
        return
    locations = [
        (winreg.HKEY_LOCAL_MACHINE,
         r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
        (winreg.HKEY_CURRENT_USER, r"Environment"),
    ]
    # 机器级 + 用户级两处都扫；setdefault 保证已存在的进程内变量不被覆盖。
    # ⚠️ 这段循环**必须留在本函数里**：2026-11-28 之前它被错误地粘在了
    # `relation_types_block()` 的 `return` 之后 —— 既永远不执行（`_load_registry_env`
    # 实际什么都没做），又在那里引用未定义的 `locations` / `BLOCKED` / `winreg`。
    for hive, path in locations:
        try:
            with winreg.OpenKey(hive, path) as key:
                index = 0
                while True:
                    try:
                        name, value, _ = winreg.EnumValue(key, index)
                    except OSError:
                        break
                    if name not in BLOCKED:
                        os.environ.setdefault(name, str(value))
                    index += 1
        except OSError:
            continue

# ── 关系类型的语义定义（2026-09-14）─────────────────────────────────────
#
# **为什么要这一块**：`COMBINED_PREFIX` 里原先只列 15 个**裸类型名**，
# 而 L4 实测谓词 macro_f1 = 0.1724 —— 比「全猜 RELATED_TO」的基线 0.22 还差。
# 模型拿不到「PART_OF 和 APPLIES_TO 到底差在哪」的信息，只能猜。
#
# 定义放在这里而不是 `ontology.py`：后者是**从本模块导入**的
# （它自己的注释写明「真正的单一来源仍是 kg_extractor」），
# 反向导入会循环。`ontology.py` 的 RELATION_ENDPOINT_CONSTRAINTS
# 从这里取 semantics，保持单一来源。
RELATION_SEMANTICS = {
    "PART_OF": "source 是 target 的组成部分或子集（整体-部分），不是『属于某类别』",
    "USES": "source 使用了 target 这个工具/方法/数据（施动者用被用物）",
    "PRODUCES": "source 产出/生成 target（source 是生产者，target 是产物）",
    "MEASURES": "source 度量/衡量 target 这个指标或对象",
    "EVALUATES": "source 评测/检验 target 的效果或质量",
    "COMPARES_WITH": "source 与 target 被相互比较（对称，双方并列）",
    "SUPPORTS": "source 为 target 提供支持或证据（不是『使用』）",
    "CONTRADICTS": "source 与 target 的结论相互矛盾（对称）",
    "CAUSES": "source 导致 target（有因果与时序先后，不可反）",
    "ENABLES": "source 使 target 成为可能（前置条件，不是直接因果）",
    "APPLIES_TO": "source 是被应用的方法/技术，target 是它被应用的领域/任务",
    "LOCATED_AT": "source 是有位置的实体，target 是它所在的地点/机构",
    "CREATED_BY": "source 是创作者（人/机构），target 是被创作物。"
                  "注意方向是 Author → Work，不是 Work → Author",
    "CITES": "source 引用 target（学术引用，单向）",
    "RELATED_TO": "**兜底类型，尽量不用**。仅当以上 14 类都不合适时才用",
    # 2026-09-14 新增：补上「论文 → 发表场所」。此前本体没有这一类，
    # 模型只能把「发表于」硬套成 PART_OF / LOCATED_AT（见 RELATION_TYPES 的注释）。
    "PUBLISHED_IN": "source 是论文/作品，target 是它发表的会议、期刊或出版场所。"
                    "**注意**：这是「论文发表于会议」，不是「论文是会议的一部分」，"
                    "也不要写成 LOCATED_AT（那会变成「论文位于某地」）",
}


def relation_types_block(bare: bool = False) -> str:
    """生成 prompt 里的关系类型清单。

    bare=True 时退回旧的裸列表（`KG_BARE_RELATION_TYPES=1`），
    便于 A/B 对照与快速回滚。
    """
    if bare:
        return ", ".join(RELATION_TYPES)
    lines = []
    for t in RELATION_TYPES:
        sem = RELATION_SEMANTICS.get(t, "")
        lines.append(f"{t}: {sem}" if sem else t)
    return "\n".join(lines)


_load_registry_env()

# ── Config ──────────────────────────────────────────────
SF_API_KEY = os.environ.get("SF_API_KEY", "")
SF_BASE   = "https://api.siliconflow.cn/v1"
DS_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DS_BASE   = os.environ.get("DS_BASE", "https://api.deepseek.com/v1")
# "deepseek-chat" is the official alias (2026-09: resolves to DeepSeek-V4-Flash);
# point DS_MODEL at any OpenAI-compatible chat model to swap endpoints.
DS_MODEL  = os.environ.get("DS_MODEL", "deepseek-chat")
# 兼容不同网关的路由/会话头，形如 "x-opencode-session:ses_abc,k: v"。
DS_EXTRA_HEADERS = os.environ.get("DS_EXTRA_HEADERS", "")
# V4-Flash 是推理模型：思维链与正文共享输出预算。预算给太小会出现
# "HTTP 200 + finish_reason=length + content 为空"——这正是 2026-09-10
# 那次 111 chunk 全部零产出的真实机制，必须留足预算。
DS_MAX_TOKENS = int(os.environ.get("DS_MAX_TOKENS", "8192"))
OLLAMA    = "http://localhost:11434"

# ★★ 2026-09-19：本地 Ollama 的**超时与重试对齐 `call_openai_compat` 已有的策略**
#     （见本文件 `call_openai_compat` 的 `timeout: int = 300, retries: int = 2`）。
#
#     **为什么必须改**：这两个数就是首版中文图只有 **95 节点 / 90 边**的直接原因
#     （首版读数，现已无产物可回溯；现 4 篇中文图为 **179/165、269/294、1021/1042、
#      224/340** 节点/边，见 `data/corpus/graphs/jsjkx*.json`）。
#     `call_ollama` / `call_ollama_json` 原来写死 `timeout=120` **且不重试**，
#     实测 `jsjkx2025-kg-rag-qa` 的 35 个 chunk 里 **14 个 ReadTimeout(120s)**
#     —— 该 PDF 现已**不在** `data/corpus/`（`data/corpus/_manifest.json` 仍登记该 id，
#     但 33 个落盘 PDF 里没有它；中文期刊现有 5 篇为 completion-llm / kbqa /
#     negative-sampling / plm-survey / embedding-ontology）。
#     ⇒ 40% 的正文从未进入模型（英文同类一篇约 500+ 节点）。
#     ⚠️ 而**最慢的单 chunk 实测 118s** —— 120s 几乎注定切掉它，且没有第二次机会。
#
#     **不给新数字**：300 / 2 就是本文件 `call_openai_compat` 那条路径已有的策略，
#     这里只是让 `_ollama_generate` 这条路径**对齐它**。
#     （原写「让 **788/804** 这条路径对齐它」—— 那是当时 `_ollama_generate` 里
#      超时取值与重试两处的行号。文件演化后已漂移（现为 786 取 `timeout`、
#      804 作 HTTP 重试等待），且会继续漂，故 2026-09-19 改成写函数名。）
#     两条都可用环境变量覆盖。
OLLAMA_TIMEOUT = int(os.environ.get("KG_OLLAMA_TIMEOUT", "300"))
OLLAMA_RETRIES = int(os.environ.get("KG_OLLAMA_RETRIES", "2"))

MODELS = {
    "local": {
        "text":   "qwen3.5:9b",
        "vision": "qwen3-vl:4b",
    },
    "cloud": {
        "text":   "deepseek-chat",       # DeepSeek-V3, fast + cheap
        "vision": "Qwen/Qwen3-VL-32B-Instruct",  # via SiliconFlow for images
    },
    "ds": {
        "text":   "deepseek-chat",
        "vision": None,  # DeepSeek doesn't have vision API
    },
}

# ── SFT recording (distillation dataset) ─────────────────
# When KG_RECORD_DIR points at a directory, every extraction call appends its
# (prompt, raw response) pair to per-task JSONL files.  Cloud-channel runs then
# double as the teacher dataset for QLoRA distillation; no extra annotation.

RECORD_DIR = os.environ.get("KG_RECORD_DIR", "")
DS_API_KEY = DS_API_KEY or os.environ.get("ANTHROPIC_AUTH_TOKEN", "") \
    or os.environ.get("COMMAND_API_KEY", "")


def record_pair(task: str, prompt: str, response: str, meta: Optional[dict] = None) -> None:
    """Append one extraction call to the distillation JSONL when recording is on.

    Recording is auxiliary: any failure is logged and never breaks extraction.
    """
    if not RECORD_DIR:
        return
    try:
        record = {
            "task": task,
            "prompt": prompt,
            "response": response,
            "meta": meta or {},
            "ts": time.time(),
        }
        target = Path(RECORD_DIR)
        target.mkdir(parents=True, exist_ok=True)
        with open(target / f"{task}.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as exc:
        print(f"[record] failed to write {task} pair: {exc}", flush=True)

# ── Prompt Templates ─────────────────────────────────────
ENTITY_TYPES = [
    "Concept", "Method", "System", "Component", "Process",
    "Artifact", "Dataset", "Metric", "Tool", "Material",
    "Person", "Organization", "Location", "Event", "Document",
]

RELATION_TYPES = [
    "PART_OF", "USES", "PRODUCES", "MEASURES", "EVALUATES",
    "COMPARES_WITH", "SUPPORTS", "CONTRADICTS", "CAUSES", "ENABLES",
    "APPLIES_TO", "LOCATED_AT", "CREATED_BY", "CITES", "RELATED_TO",
    # 2026-09-14 新增。学术图谱里最常见的关系之一，此前本体里**没有**，
    # 于是「论文/作者 ↔ 发表场所」被硬套成 PART_OF / LOCATED_AT / CREATED_BY：
    #     TuckER   —[PART_OF]→ EMNLP-IJCNLP   （「Tucker 是 EMNLP 的一部分」不通）
    #     TransNet —[LOCATED_AT]→ IJCAI
    #     R. Das   —[CREATED_BY]→ EACL        （「R. Das 由 EACL 创作」完全错）
    # 实测规模见 `data/quality/venue-rewrite-plan.json`：可改写 1019 条
    # （PART_OF 745 + LOCATED_AT 274）、待判 129 条（CREATED_BY）。
    #
    # ⚠️ **加上它会让 L4（谓词是否在本体内）的分母变化** —— 原来判「越界」的
    # 谓词现在合规了。改前改后的 L4 必须两套口径一起留档
    # （`docs/KNOWN_DATA_DEFECTS.md §6` 的原则），否则六层指标失去可比性。
    "PUBLISHED_IN",
]


def build_entity_response_schema() -> dict:
    """JSON Schema for a constrained local entity-extraction response."""
    return {
        "type": "object",
        "properties": {
            "entities": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "type": {"type": "string", "enum": ENTITY_TYPES},
                        "description": {"type": "string"},
                    },
                    "required": ["name", "type", "description"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["entities"],
        "additionalProperties": False,
    }


def build_local_json_payload(model: str, prompt: str, schema: Optional[dict],
                             images: Optional[list[str]] = None) -> dict:
    """Build a bounded, non-thinking Ollama request with structured output."""
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "think": False,
        "keep_alive": "5m",
        "options": {"temperature": 0, "num_predict": 2048, "num_ctx": 4096},
    }
    if schema is not None:
        payload["format"] = schema
    if images:
        payload["images"] = images
    return payload


def _repair_truncated_json(text: str) -> Optional[str]:
    """Close an output truncated by num_predict instead of losing the chunk.

    Structured output stops mid-token when the prediction budget runs out.
    The longest balanced prefix is salvaged: open containers are closed so
    already-complete records survive, while the partial trailing element is
    dropped.  Returns None when no balanced prefix exists.
    """
    closers = {"{": "}", "[": "]"}
    for cut in range(len(text), 0, -1):
        prefix = text[:cut]
        stack: list[str] = []
        in_string = escaped = False
        balanced = True
        for ch in prefix:
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch in "{[":
                stack.append(ch)
            elif ch in "}]":
                if not stack or closers[stack[-1]] != ch:
                    balanced = False
                    break
                stack.pop()
        if not balanced or in_string or not stack:
            continue
        repaired = prefix + "".join(closers[c] for c in reversed(stack))
        try:
            json.loads(repaired)
            return repaired
        except json.JSONDecodeError:
            continue
    return None


def decode_local_json_response(raw: str) -> dict:
    """Decode a local JSON response, accepting a Markdown JSON fence if present.

    Truncated structured output (prediction budget exhausted) is repaired to
    its longest balanced prefix: complete records are kept, the partial tail
    element is discarded.
    """
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.endswith("```"):
            text = text[:-3].strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        repaired = _repair_truncated_json(text)
        if repaired is None:
            raise
        print(f"[extract] recovered truncated JSON ({len(text)} -> {len(repaired)} chars)",
              flush=True)
        return json.loads(repaired)


def parse_structured_entities(raw: dict) -> list[dict]:
    """Accept only schema-compatible entities with a supported domain type."""
    entities = []
    for entity in raw.get("entities", []):
        if not isinstance(entity, dict):
            continue
        name = str(entity.get("name", "")).strip()
        entity_type = str(entity.get("type", "")).strip()
        description = str(entity.get("description", "")).strip()
        if name and entity_type in ENTITY_TYPES and not _is_structural_reference(name):
            entities.append({"name": name, "type": entity_type, "description": description})
    return entities


def _is_structural_reference(name: str) -> bool:
    """Exclude document-internal labels; their page/chunk remains evidence metadata."""
    return bool(re.fullmatch(r"(?:figure|fig\.?|table|equation|eq\.?)\s*\d+[a-z]?", name.strip(), re.IGNORECASE))


def build_relation_response_schema() -> dict:
    """JSON Schema for a constrained local relationship-extraction response."""
    return {
        "type": "object",
        "properties": {
            "relations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "source": {"type": "string"},
                        "target": {"type": "string"},
                        "type": {"type": "string", "enum": RELATION_TYPES},
                        "description": {"type": "string"},
                    },
                    "required": ["source", "target", "type", "description"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["relations"],
        "additionalProperties": False,
    }


def parse_structured_relations(raw: dict) -> list[dict]:
    """Accept only schema-compatible relationships with a supported type."""
    relations = []
    for relation in raw.get("relations", []):
        if not isinstance(relation, dict):
            continue
        source = str(relation.get("source", "")).strip()
        target = str(relation.get("target", "")).strip()
        relation_type = str(relation.get("type", "")).strip()
        description = str(relation.get("description", "")).strip()
        if source and target and relation_type in RELATION_TYPES:
            relations.append({
                "source": source,
                "target": target,
                "type": relation_type,
                "description": description,
            })
    return relations


def retain_relations_with_known_endpoints(relations: list[dict], entity_names: list[str]) -> list[dict]:
    """Keep only relations whose endpoints exactly resolve to extracted entities."""
    known = {name.strip().casefold() for name in entity_names if name and name.strip()}
    return [
        relation for relation in relations
        if relation.get("source", "").strip().casefold() in known
        and relation.get("target", "").strip().casefold() in known
    ]


def canonicalize_relation_endpoints(relations: list[dict], entities: list[dict]) -> list[dict]:
    """把关系端点改写到去重后的规范实体名。

    背景（2026-09-11 发现）：`deduplicate_entities` 在跨 chunk 合并时会把实体名
    改成"最长的那个变体"（`existing["name"] = e["name"]`），但关系端点不会同步
    改写。于是合并后大量关系的端点与被保留的实体名失配。实测第一篇的云端输出：
    779 条关系里 **328 条（42%）的端点在实体集合中根本找不到**——这些边在写入
    Neo4j 时会被 `MATCH (a {name})` 静默丢弃，是真正的关系损失。

    做法：先按归一化名称精确匹配，再对未命中的端点做一次保守的模糊匹配
    （复用 `_names_are_duplicates`，已带长度保护）。匹配不到则保留原名，
    由调用方的端点过滤决定去留。
    """
    names = [n.get("name", "") for n in entities if n.get("name")]
    exact = {name.strip().casefold(): name for name in names}
    normalized = {re.sub(r"[^a-z0-9]+", "", name.lower()): name for name in names}
    cache: dict[str, str] = {}
    out = []
    for rel in relations:
        new_rel = dict(rel)
        for field in ("source", "target"):
            raw = (rel.get(field) or "").strip()
            if not raw:
                continue
            hit = exact.get(raw.casefold()) or normalized.get(
                re.sub(r"[^a-z0-9]+", "", raw.lower()))
            if hit is None and raw not in cache:
                norm_raw = re.sub(r"[^a-z0-9]+", "", raw.lower())
                hit = next((c for n, c in normalized.items()
                            if _names_are_duplicates(norm_raw, n)), raw)
                cache[raw] = hit
            elif hit is None:
                hit = cache[raw]
            new_rel[field] = hit
        out.append(new_rel)
    return out


def retain_semantic_table_entities(entities: list[dict]) -> list[dict]:
    """Remove table-only row markers that cannot be explored as knowledge entities."""
    generic_markers = {"base", "big", "small", "large", "default", "baseline"}
    retained = []
    for entity in entities:
        name = entity.get("name", "").strip()
        if name.casefold() in generic_markers:
            continue
        if re.fullmatch(r"\([a-z0-9]{1,3}\)", name, re.IGNORECASE):
            continue
        retained.append(entity)
    return retained


ENTITY_PROMPT = """---Role---
You are a Knowledge Graph Specialist. Extract all entities from the text.

---Rules---
1. Only extract entities EXPLICITLY mentioned. Do not infer or imagine.
2. Entity names in Title Case, consistent across the text.
3. Use third person only.
4. Output format (one per line, NO markdown, NO JSON):
   entity<|>EntityName<|>EntityType<|>Brief description

---Entity Types---
Use ONLY these types: {entity_types}

---Text---
{text}

---Output---
Output all entities, then <|COMPLETE|> on the final line."""

# 变体 B（2026-09-10）：只收紧 Concept 的边界，其余规则不动。
# 依据：ENTITY_PROMPT_V2 的教训是"全面收紧"会砍掉论文自己命名的流程步骤
# （Five-Step Methodology 等金标实体）。而实际噪声集中在 Concept 类——
# Input / Output / Scope / Explanation / Field / Criterion / Rule / Related Work
# 这些论文的结构与元语言名词，全被塞进了 Concept。
# 对标微软 GraphRAG：它的类型集窄（ORGANIZATION/PERSON/GEO），因此不产生这类噪声。
ENTITY_PROMPT_V3 = """---Role---
You are a Knowledge Graph Specialist. Extract all entities from the text.

---Rules---
1. Only extract entities EXPLICITLY mentioned. Do not infer or imagine.
2. Entity names in Title Case, consistent across the text.
3. Use third person only.
4. Output format (one per line, NO markdown, NO JSON):
   entity<|>EntityName<|>EntityType<|>Brief description

---Entity Types---
Use ONLY these types: {entity_types}

---Concept: strict boundary---
Use Concept ONLY for a named idea, paradigm, problem, or principle that the paper
treats as a subject in its own right — e.g. "Crowdsourcing", "Scholarly Communication",
"Human-in-the-Loop".
Do NOT use Concept for:
- the paper's own structure or meta-language: "Related Work", "Future Work",
  "Introduction", "Results", "Scope", "Explanation", "Input", "Output", "Field";
- enumerated labels: "Criterion 2", "Rule 3", "Step 4", "Table Number";
- generic noun phrases with no specific referent: "the approach", "domain experts",
  "research problem", "bibliographic data", "high-quality knowledge".
If a name is only the paper's scaffolding rather than something the paper discusses,
leave it out entirely — do not fall back to Concept.

---Text---
{text}

---Output---
Output all entities, then <|COMPLETE|> on the final line."""

# 动机：金标评分显示 Recall 0.97 但 Precision 0.26，且 230 个 FP **零幻觉**——
# 它们全部有原文依据，只是抽取器把论文的结构元素（Criterion 2 / Rule 3 /
# Input / Output / Related Work / Table Number）和泛指短语也当成了实体。
# 这属于任务定义缺口，用排除规则比事后字符串黑名单更准（黑名单会误杀
# "Tables"、"Inclusion Criteria" 这类真正被阐述的对象）。
ENTITY_PROMPT_V2 = """---Role---
You are a Knowledge Graph Specialist building a knowledge graph of THIS PAPER'S SUBJECT MATTER. Extract the named entities that the paper actually discusses, studies, proposes, or compares.

---Rules---
1. Only extract entities EXPLICITLY named in the text. Do not infer or imagine.
2. Entity names in Title Case, consistent across the text.
3. Use third person only.
4. Output format (one per line, NO markdown, NO JSON):
   entity<|>EntityName<|>EntityType<|>Brief description

---What to extract---
Objects the paper is ABOUT: named systems, tools, platforms, methods, algorithms,
datasets, benchmarks, metrics, organizations, people, locations, documents, and
the paper's own named workflow steps and produced artifacts.

---What NOT to extract (important)---
- Generic noun phrases with no specific referent: "the approach", "domain experts",
  "research problem", "bibliographic data", "structured data", "high-quality knowledge".
- The paper's own structure and meta-language: "Related Work", "Future Work",
  "Input", "Output", "Results", "Scope", "Explanation", "Section 3", "Table Number",
  "Table Title", "Column", "Row", "Cell", "Abbreviation", "Citation Style".
- Enumerated labels: "Criterion 2", "Rule 3", "Step 4", "Figure 1", "Table 2".
- Statistics and placeholders: "95% CI", "R0", single letters or symbols.
- Names appearing ONLY in the reference list or in a DOI/citation string.
- Example/dummy values used purely to illustrate a figure or table.

---Test---
Before outputting a name, ask: "if a reader asked what this paper is about,
would this name be part of the answer?" If no, leave it out.

---Entity Types---
Use ONLY these types: {entity_types}

---Text---
{text}

---Output---
Output all entities, then <|COMPLETE|> on the final line."""

RELATION_PROMPT = """---Role---
You are a Knowledge Graph Specialist. Now extract relationships between the given entities.
---Entities---
{entity_list}

---Rules---
1. Only connect entities that have a direct, explicit relationship.
2. Decompose multi-entity relationships into binary pairs.
3. Use ONLY these relationship types: {relation_types}
4. Output format (one per line, NO markdown, NO JSON):
   relation<|>SourceEntity<|>TargetEntity<|>RelationType<|>Brief explanation

---Text (for context)---
{text}

---Output---
Output all relationships, then <|COMPLETE|> on the final line."""

# ── 成本优化通道（2026-09-10）───────────────────────────────
# 背景：云端按输入/输出 token 计费，实测单价（用户账户，非高峰）
#   输出 4 元/M、输入未命中 1 元/M、输入缓存命中 0.02 元/M
# 命中缓存比未命中便宜 50 倍，因此"把固定说明放前缀、变量放尾部"是最大杠杆。
# 同时把实体与关系两次调用合并成一次，省掉一次重复正文与一份固定说明。
# 该前缀保持字节级稳定，禁止在其中插入任何变量。
COMBINED_PREFIX = """---Role---
You are a Knowledge Graph Specialist. Read the text and extract both entities and the relationships between them.

---Entity Types (use ONLY these)---
Concept, Method, System, Component, Process, Artifact, Dataset, Metric, Tool, Material, Person, Organization, Location, Event, Document

---Relation Types (use ONLY these)---
__RELATION_BLOCK__

---Output Format---
One entity per line, then one relation per line. NO markdown, NO JSON, no prose, no headings.
entity<|>EntityName<|>EntityType<|>Brief description
relation<|>SourceEntity<|>TargetEntity<|>RelationType<|>Brief explanation

---Rules---
1. Only extract what is EXPLICITLY stated in the text. Never infer or imagine.
2. Entity names in Title Case and consistent across the whole text.
3. Use third person only. Keep descriptions under 20 words.
4. Every relation must reference two entities that appear in your entity lines above.
5. Decompose multi-entity statements into binary pairs.
6. If the text is tabular evidence, extract the semantic entities it names and their relations.
7. Finish by writing <|COMPLETE|> on its own final line.

---Text---
"""

# 关系类型定义由常量表生成（不依赖输入文本，故仍满足 COMBINED_PREFIX
# 的"字节级稳定、不含变量"纪律）。KG_BARE_RELATION_TYPES=1 可退回裸列表。
if os.environ.get("KG_BARE_RELATION_TYPES", "0").strip().lower() in ("1", "true", "yes"):
    COMBINED_PREFIX = COMBINED_PREFIX.replace(
        "__RELATION_BLOCK__", relation_types_block(bare=True))
else:
    COMBINED_PREFIX = COMBINED_PREFIX.replace(
        "__RELATION_BLOCK__", relation_types_block())
assert "__RELATION_BLOCK__" not in COMBINED_PREFIX


def build_combined_prompt(text: str) -> str:
    """合并抽取提示词：固定前缀 + 变量文本（前缀稳定才能吃到缓存）。"""
    return COMBINED_PREFIX + text + "\n\n---Output---\n"


def parse_combined(raw: str) -> tuple[list[dict], list[dict]]:
    """解析合并输出：实体行与关系行混排，按行首标记严格分流。

    必须按标记分流，不能让关系解析器看到实体行——实体行有四段
    （entity<|>Name<|>Type<|>Desc），正好满足关系解析器 ">= 4 段" 的条件，
    否则每个实体都会被误解成一条关系。实测踩过这个坑（2026-09-10）。
    """
    entity_lines, relation_lines = [], []
    for line in raw.split("\n"):
        stripped = line.strip().lstrip("-").strip()
        if not stripped or "<|COMPLETE|>" in stripped or "<|>" not in stripped:
            continue
        head = stripped.split("<|>", 1)[0].strip().lower()
        if head == "relation":
            relation_lines.append(stripped)
        elif head == "entity":
            entity_lines.append(stripped)
        else:
            # 兜底：无标记行按首段是否为已知类型猜测归属，猜不出就丢弃。
            entity_lines.append(stripped)
    return parse_entities("\n".join(entity_lines)), parse_relations("\n".join(relation_lines))


def extract_chunk_once(chunk_text: str, model_text: str, mode: str,
                       model_vis: Optional[str] = None,
                       image_path: Optional[str] = None,
                       content_kind: str = "text") -> dict:
    """单次调用完成实体+关系抽取（成本优化通道）。

    与 extract_chunk 的输出结构保持一致，便于流水线无差别替换。
    视觉上下文仍需一次额外调用（仅当 chunk 带图）。
    """
    full_text = chunk_text
    vision_s = 0.0
    if image_path and model_vis:
        t_v = time.time()
        prompt_img = ("Describe this image in detail. Focus on any entities, "
                      "labels, data, or relationships visible.")
        img_context = call_ollama(model_vis, prompt_img, image_path) if mode == "local" \
            else call_siliconflow(model_vis, prompt_img, image_path)
        full_text = chunk_text + "\n\n---Image Context---\n" + img_context
        vision_s = time.time() - t_v

    t1 = time.time()
    prompt = build_combined_prompt(full_text)
    if mode == "local":
        # ⚠️⚠️ **这里必须用 `call_ollama`（纯文本），不能用 `call_ollama_json`**
        # —— 2026-09-18 修，而它此前**从来没跑通过**。
        #
        # **根因**：`COMBINED_PREFIX` 的 `---Output Format---` 明确写着
        #   「One entity per line, then one relation per line.
        #     **NO markdown, NO JSON, no prose, no headings.**」
        #   并要求 `entity<|>Name<|>Type<|>Desc` / `relation<|>Src<|>Tgt<|>Type<|>Why`。
        #
        #   而这里原来调 `call_ollama_json(...)` ⇒ 它**先做 JSON 解码**并返回 `dict`
        #   ⇒ 而 `parse_combined(raw)` 要的是**按 `<|>` 分流的纯文本 str**
        #   ⇒ **类型与格式双重不匹配 ⇒ 必然 `JSONDecodeError`。**
        #
        # **实测**（B5 那一轮 6 次抽取、111 chunks）：
        #   ```
        #   pipeline.ExtractionFailed: 111 chunks 全部零产出
        #     （首因：JSONDecodeError: Expecting value: line 1 column 1 (char 0)）
        #   ⇒ 每次耗时 34–45 分钟，而 **rc=0** —— 主流程以为成功了
        #   ```
        #   ⇒ 而 `tools/check_prompt_ab.py` 抓住了它
        #     （六份产物与基线**逐字节相同**）。
        #
        # **⇒ 教训**：`prompt 里写的格式` 与 `解析它的函数` **必须是同一套**。
        #   而"跑完 rc=0"**不能证明**这件事 —— **要看产物变了没有**。
        raw = call_ollama(model_text, prompt)
    else:
        raw = call_deepseek(model_text, prompt)
    record_pair("combined", prompt, raw,
                {"model": model_text, "mode": mode, "kind": content_kind})
    entities, relations = parse_combined(raw)
    if content_kind == "table":
        entities = retain_semantic_table_entities(entities)
    relations = retain_relations_with_known_endpoints(
        relations, [e["name"] for e in entities])
    dt = time.time() - t1
    print(f"    [combined] {len(entities)} entities, {len(relations)} relations ({dt:.1f}s)")
    return {
        "nodes": entities,
        "edges": relations,
        "timing": {"entities_s": dt, "relations_s": 0.0, "vision_s": vision_s},
    }

LOCAL_ENTITY_PROMPT = """Extract every explicitly named knowledge-graph entity in the text.
Include named technical terms, components, methods, systems, datasets, metrics, people, organizations,
places, events, documents, and other concrete concepts that are stated in the text. For example, an
algorithm is a Method, a module or layer is a Component, and a named model or framework is a System.
Use only the permitted entity types. Do not extract instructions, prompt fragments, or facts not stated.
Do not extract internal labels such as Figure 2, Table 3, or Equation 1; retain those only as evidence context.
If the text explicitly names one or more entities, do not return an empty entity array.
Return JSON only in the exact shape:
{{"entities": [{{"name": "...", "type": "...", "description": "..."}}]}}.

Permitted entity types: {entity_types}

Text:
{text}"""

LOCAL_TABLE_ENTITY_PROMPT = """This is tabular evidence extracted from a document.
Extract explicitly named systems, methods, datasets, metrics, people, organizations, and concrete concepts
from row and column labels. Treat numeric values as evidence in an entity description, never as standalone
entities. Exclude configuration markers, row IDs, and generic labels such as base, big, (A), or (B).
Extract at most five high-salience entities. When the table contains semantic named rows or headers,
do not return an empty entity array.
Use only the permitted entity types. Do not extract table labels such as Table 1.
Return JSON only in the exact shape:
{{"entities": [{{"name": "...", "type": "...", "description": "..."}}]}}.

Permitted entity types: {entity_types}

Table:
{text}"""

LOCAL_RELATION_PROMPT = """Extract direct, explicitly stated binary relationships between the supplied entities.
Use only the permitted relationship types. Do not create relationships from instructions or inferred facts.
For source and target, copy an entity name from the supplied list exactly; never abbreviate, combine, or invent names.
Return JSON only in the exact shape:
{{"relations": [{{"source": "...", "target": "...", "type": "...", "description": "..."}}]}}.

Entities:
{entity_list}

Permitted relationship types: {relation_types}

Text:
{text}"""


def build_local_entity_prompt(text: str, content_kind: str = "text") -> str:
    """Select an extraction instruction tailored to prose or a detected table."""
    template = LOCAL_TABLE_ENTITY_PROMPT if content_kind == "table" else LOCAL_ENTITY_PROMPT
    return template.format(entity_types=", ".join(ENTITY_TYPES), text=text)


# ── Backend Adapters ─────────────────────────────────────

def _ollama_generate(payload: dict, timeout: Optional[int] = None,
                     retries: Optional[int] = None) -> dict:
    """POST `{OLLAMA}/api/generate`，**按 `call_openai_compat` 的策略重试瞬时失败**。

    2026-09-19 加。此前 `call_ollama` / `call_ollama_json` 各自
    `requests.post(..., timeout=120)` 且**不重试** —— 慢 chunk 一次 ReadTimeout
    就整段正文丢失，而首版中文图的 14/35 失败全是这一类。

    重试判据与退避**与本文件 `call_openai_compat` 一致**（不发明新策略）：
      · `Timeout` / `ConnectionError` ⇒ 等 `10 * attempt` 秒后重试；
      · HTTP 429/500/502/503/504      ⇒ 等 `15 * attempt` 秒后重试。
    非瞬时错误（400/404 等）与重试耗尽**一律原样抛出** —— 失败必须可见，
    绝不能被吞成"这个 chunk 没有实体"（那正是残图的另一种形态）。
    """
    import requests
    timeout = OLLAMA_TIMEOUT if timeout is None else timeout
    retries = OLLAMA_RETRIES if retries is None else retries
    attempt = 0
    while True:
        try:
            r = requests.post(f"{OLLAMA}/api/generate", json=payload, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except (requests.Timeout, requests.ConnectionError) as exc:
            attempt += 1
            if attempt > retries:
                raise
            wait = 10 * attempt
            print(f"[ollama] {type(exc).__name__}（timeout={timeout}s），"
                  f"retry {attempt}/{retries} in {wait}s", flush=True)
            time.sleep(wait)
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if status not in (429, 500, 502, 503, 504):
                raise
            attempt += 1
            if attempt > retries:
                raise
            wait = 15 * attempt
            print(f"[ollama] HTTP {status}, retry {attempt}/{retries} in {wait}s",
                  flush=True)
            time.sleep(wait)


def call_ollama(model: str, prompt: str, image_path: Optional[str] = None) -> str:
    """Call local Ollama model. **返回的是答案，不是思考过程。**

    ⚠️⚠️ 2026-09-18 修了两个问题，而**第二个很危险**：

    **① `enable_thinking` 是无效字段。**
       原来写的是 `options: {..., "enable_thinking": False}`。
       **实测（直接打 Ollama，同一 prompt）**：
       ```
       combined + think:False             ⇒ status=200 · response=**1781** · thinking=0
       combined + enable_thinking:False   ⇒ status=200 · response=**0**   · thinking=8237
       ```
       ⇒ **关思考要写在顶层的 `think`，不是 `options` 里。**

    **② 原来在 `response` 为空时**回退到 `thinking`**：
       ```python
       return data.get("response") or data.get("thinking", "")   # ← 原样
       ```
       ⇒ 那等于**把模型的思考过程当成答案交出去**。
       实测后果：`combined` 那条路拿到的是以 `Thinking Process:` 开头的 7953 字，
       而 `parse_combined` 按 `<|>` 分流 ⇒ **解析出 0 条实体**（**静默的零产出**）。
       ⇒ **现在去掉那个回退**：`response` 为空就**返回空串**，
         让上层（`pipeline` 的零产出判定）**明确失败**，而不是把思考当答案。

    **★ 这条与本轮另一条是同一个家族**：
    "看起来有输出" ≠ "输出的是答案"。

    **③ 2026-09-19：超时从写死的 120s 改成 `OLLAMA_TIMEOUT`（默认 300s）并重试。**
       原来 `requests.post(..., timeout=120)` 且不重试 —— 慢 chunk（实测最慢 118s）
       一次 ReadTimeout 就丢整段正文。现在与 `call_openai_compat` 同一策略。
    """
    payload = {"model": model, "prompt": prompt, "stream": False,
               # ⚠️ **顶层 `think`** —— 不是 `options.enable_thinking`（那个无效）
               "think": False,
               "options": {"temperature": 0.1, "num_predict": 2048, "num_ctx": 4096}}
    if image_path:
        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()
        payload["images"] = [img_b64]
    # ⚠️ 统一走 `_ollama_generate`：**300s + 重试 2 次**（见 docstring ③）
    data = _ollama_generate(payload)
    # ⚠️ **不回退到 `thinking`** —— 思考不是答案（见 docstring ②）
    return (data.get("response") or "").strip()


def call_ollama_json(model: str, prompt: str, schema: Optional[dict] = None,
                     image_path: Optional[str] = None) -> dict:
    """Call Ollama with a JSON Schema and return the decoded object.

    2026-09-19：超时/重试与 `call_ollama` **同一条路径**（`_ollama_generate`，
    默认 300s + 2 次重试）。原来这里同样写死 `timeout=120` 且不重试 ——
    而**实体与关系两次调用都走这个函数**，所以它是 14/35 失败的主入口。
    """
    images = None
    if image_path:
        with open(image_path, "rb") as f:
            images = [base64.b64encode(f.read()).decode()]
    payload = build_local_json_payload(model, prompt, schema, images)
    response = (_ollama_generate(payload).get("response") or "").strip()
    if not response:
        raise ValueError(f"{model} returned no structured response")
    return decode_local_json_response(response)


def call_siliconflow(model: str, prompt: str, image_path: Optional[str] = None) -> str:
    """Call SiliconFlow API (OpenAI-compatible). Used for vision models."""
    return call_openai_compat(SF_BASE, SF_API_KEY, model, prompt, image_path)


def call_deepseek(model: str, prompt: str) -> str:
    """Call DeepSeek chat model.

    注意：endpoint 与 model 的切换靠环境变量 DS_BASE / DS_MODEL，但**必须使用
    传入的 model 参数**——此前这里忽略了参数、硬用模块级 DS_MODEL，导致
    "换通道却仍打旧模型"的静默错配。
    """
    return call_openai_compat(DS_BASE, DS_API_KEY, model or DS_MODEL, prompt, None)


class EmptyExtractionResponse(RuntimeError):
    """服务端返回了结构合法但没有内容的响应（限流/降级/预算耗尽）。

    这类响应绝不能被当作"抽取结果为空"继续向下游传播：它会让流水线
    写出空快照、把失败记成完成，并静默烧掉配额。必须显式失败。
    """


def _extract_message_content(body: object) -> str:
    """从 OpenAI 兼容响应体中取出正文，结构异常一律抛错而不是返回空串。

    覆盖四种真实故障形态：
      1. 网关限流/降级返回 200 + {"error": {...}}（无 choices）；
      2. choices 为空或缺 message；
      3. 正文为空但思维链非空（推理模型的预算全被 reasoning 吃掉）；
      4. 正文与思维链都为空（预算耗尽/空转）。
    """
    if not isinstance(body, dict):
        raise EmptyExtractionResponse(f"响应不是 JSON 对象: {type(body).__name__}")
    if body.get("error"):
        raise EmptyExtractionResponse(f"服务端 error 字段: {str(body['error'])[:300]}")
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise EmptyExtractionResponse(f"响应缺少 choices: {str(body)[:300]}")
    choice = choices[0] if isinstance(choices[0], dict) else {}
    finish = choice.get("finish_reason")
    message = choice.get("message")
    if not isinstance(message, dict):
        raise EmptyExtractionResponse(f"choices[0].message 缺失: {str(choice)[:300]}")

    content = message.get("content")
    if not isinstance(content, str):
        content = ""
    reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
    if not isinstance(reasoning, str):
        reasoning = ""

    # 预算耗尽时正文可能被截断；截断的 JSON 由 decoder 的最长平衡前缀修复，
    # 所以这里只要正文非空就交给下游，不因为 finish_reason=length 而丢掉结果。
    if content.strip():
        return content

    # 正文为空但思维链里有内容：说明预算几乎全被推理吃掉。把思维链交回下游，
    # 由 decode 层尝试抢救其中的 JSON；抢救不出来就是显式失败，绝不静默成空。
    if reasoning.strip():
        raise EmptyExtractionResponse(
            f"正文为空、思维链占满预算（finish_reason={finish}, "
            f"reasoning 长度 {len(reasoning)}）：请提高 DS_MAX_TOKENS"
        )

    raise EmptyExtractionResponse(f"正文与思维链均为空（finish_reason={finish}）")


def _parse_extra_headers(raw: str) -> dict:
    """解析 "k1:v1,k2:v2" 形式的附加请求头。"""
    headers = {}
    for item in raw.split(","):
        if ":" not in item:
            continue
        key, value = item.split(":", 1)
        key, value = key.strip(), value.strip()
        if key and value:
            headers[key] = value
    return headers


# 每个线程一个 requests.Session：连接与 TLS 会话复用，省掉每次请求的握手。
# 实测（2026-09-10 并发扫描）：高并发时握手开销会明显吃掉吞吐，而
# requests.post 每次都新建连接。Session 不做跨线程共享，避免竞态。
_SESSIONS = threading.local()


def _session_for(base_url: str):
    import requests
    sessions = getattr(_SESSIONS, "by_host", None)
    if sessions is None:
        sessions = {}
        _SESSIONS.by_host = sessions
    session = sessions.get(base_url)
    if session is None:
        session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=8, pool_maxsize=32, max_retries=0
        )
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        sessions[base_url] = session
    return session


def call_openai_compat(base_url: str, api_key: str, model: str, prompt: str,
                       image_path: Optional[str] = None,
                       timeout: int = 300, retries: int = 2) -> str:
    """Call any OpenAI-compatible API, retrying transient gateway failures.

    返回的正文经过结构校验：限流降级、缺 choices、正文为空一律抛
    EmptyExtractionResponse，不返回空串。

    ⚠️ 2026-09-11 起默认禁止云端调用：必须显式设置 KG_ALLOW_CLOUD=1 才放行。
    背景：用户 DeepSeek 账户余额已耗尽且明确不再充值，任何意外调用都是净损失。
    放开方式（仅在明确需要时）：
        $env:KG_ALLOW_CLOUD='1'
    """
    if os.environ.get("KG_ALLOW_CLOUD", "0").strip().lower() not in ("1", "true", "yes"):
        raise RuntimeError(
            "云端 API 调用已被硬禁用（KG_ALLOW_CLOUD 未设为 1）。"
            "用户约束：不再有任何云端 API 支出。"
            "如确需调用，请显式设置 $env:KG_ALLOW_CLOUD='1'。"
        )
    import requests
    session = _session_for(base_url)
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    headers.update(_parse_extra_headers(DS_EXTRA_HEADERS))
    if image_path:
        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()
        messages = [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}}
        ]}]
    else:
        messages = [{"role": "user", "content": prompt}]
    payload = {"model": model, "messages": messages, "temperature": 0.1,
               "max_tokens": DS_MAX_TOKENS}

    attempt = 0
    while True:
        try:
            r = session.post(f"{base_url}/chat/completions", json=payload,
                             headers=headers, timeout=timeout)
            r.raise_for_status()
            try:
                body = r.json()
            except ValueError as exc:
                raise EmptyExtractionResponse(
                    f"响应不是合法 JSON（HTTP {r.status_code}）: {r.text[:200]}"
                ) from exc
            return _extract_message_content(body)
        except (requests.Timeout, requests.ConnectionError) as exc:
            attempt += 1
            if attempt > retries:
                raise
            wait = 10 * attempt
            print(f"[api] {type(exc).__name__}, retry {attempt}/{retries} in {wait}s",
                  flush=True)
            time.sleep(wait)
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if status not in (429, 500, 502, 503, 504):
                raise
            attempt += 1
            if attempt > retries:
                raise
            wait = 15 * attempt
            print(f"[api] HTTP {status}, retry {attempt}/{retries} in {wait}s", flush=True)
            time.sleep(wait)
        except EmptyExtractionResponse as exc:
            # 网关限流时可能以 200 返回 error 体；这类"软失败"同样值得退避重试。
            attempt += 1
            if attempt > retries:
                raise
            wait = 20 * attempt
            print(f"[api] 空响应（{str(exc)[:120]}），retry {attempt}/{retries} in {wait}s",
                  flush=True)
            time.sleep(wait)


# ── Parsing ──────────────────────────────────────────────

def parse_entities(raw: str) -> list[dict]:
    """Parse entity lines into structured list.

    真实观测到的云端输出形态（2026-09-10 实测，DeepSeek V4-Flash 按
    ENTITY_PROMPT 的样例输出）是四段：
        Name<|>Name<|>Type<|>Description
    而旧解析器假定三段（Name<|>Type<|>Desc），把第二段当类型做白名单校验，
    于是**每一个实体都被丢弃**——这正是那次"111 个 chunk 全部 0 实体"的
    真正原因（限流只是让另外 25 篇直接 429）。

    这里不再假定位置，而是按"哪一段是类型"来定位，同时兼容：
      - 'entity<|>' 前缀（本地提示词的旧格式）
      - 三段与四段的两种排布
      - 类型写在第 1 或第 3 段
    """
    known_types = {t.lower() for t in ENTITY_TYPES}
    entities = []
    for line in raw.strip().split("\n"):
        line = line.strip().lstrip("-").strip()
        if not line or "<|COMPLETE|>" in line or "<|>" not in line:
            continue
        if line.lower().startswith("entity<|>"):
            line = line[len("entity<|>"):]
        parts = [p.strip() for p in line.split("<|>")]

        type_idx = next(
            (i for i, p in enumerate(parts) if p.lower() in known_types), None
        )
        if type_idx is None:
            continue  # 类型不在本体里：明确丢弃，绝不透传到下游

        name = next((p for i, p in enumerate(parts) if i != type_idx and p), "")
        if not name:
            continue
        description = next(
            (p for i, p in enumerate(parts)
             if i != type_idx and p and p != name), ""
        )
        entities.append({
            "name": name,
            "type": parts[type_idx],
            "description": description,
        })
    return entities


def parse_relations(raw: str) -> list[dict]:
    """Parse relation lines, enforcing the 15-type ontology whitelist.

    历史缺陷（2026-09-10 验收发现）：本函数从不校验关系类型，模型自由发挥的
    类型（`CONTAINS`、`fuses`、`for`、`NYT10`、`Trump`…）会一路写进 Neo4j。
    实测量级：73 种关系类型里 58 种越界。实体侧早有白名单，关系侧必须有同等待遇。

    字段顺序仍自动探测（部分模型把类型放在首段），但类型必须命中白名单，
    否则整行丢弃。
    """
    relations = []
    known_types = {t.lower() for t in RELATION_TYPES}

    for line in raw.strip().split("\n"):
        line = line.strip().lstrip("-").strip()
        if not line or "<|COMPLETE|>" in line or "<|>" not in line:
            continue
        if line.lower().startswith("relation<|>"):
            line = line[len("relation<|>"):]
        parts = [p.strip() for p in line.split("<|>")]

        type_idx = next(
            (i for i, p in enumerate(parts) if p.lower() in known_types), None
        )
        if type_idx is None:
            continue  # 类型不在本体里：丢弃，绝不污染图谱

        rest = [p for i, p in enumerate(parts) if i != type_idx]
        # 需要源与目标两个名字（描述可选，可能缺）
        if len(rest) < 2 or not rest[0] or not rest[1]:
            continue
        relations.append({
            "source": rest[0],
            "target": rest[1],
            "type": parts[type_idx],
            "description": rest[2] if len(rest) > 2 else "",
        })
    return relations


# ── Chunking ──────────────────────────────────────────────

# `chunk_text` 统一由 pdf_parser 提供（2026-09-12 去重）。
# 此处曾有一份与 pdf_parser 逐行重复的实现，唯一差别是空文本时返回
# 含空串的列表而非空列表——那会往下游多送一个空 chunk。
# 两份实现并存本身就是 ERROR_CASES 案例 12 说的「同一规则写两处」，
# 所以合并成一份而不是各留一份。
from pdf_parser import chunk_text  # noqa: F401  (re-export 供既有调用方使用)


def deduplicate_entities(entities: list[dict], mode: str = "local") -> list[dict]:
    """
    Merge entities with identical or similar names.
    - Case-insensitive exact name → merge (keep longer description)
    - Same type + >70% char overlap → merge
    - Cross-type: keep separate (e.g. "Transformer" system vs "Transformer" organization)
    """
    # Phase 1: Case-insensitive exact name dedup
    seen = {}
    for e in entities:
        key = e["name"].lower().strip()
        if key in seen:
            existing = seen[key]
            # Normalize to Title Case
            if e["name"] and existing["name"] and e["name"][0].isupper() and not existing["name"][0].isupper():
                existing["name"] = e["name"]
            # Keep longer description
            if len(e.get("description", "")) > len(existing.get("description", "")):
                existing["description"] = e["description"]
            existing["evidence"] = merge_evidence(
                existing.get("evidence", []),
                e.get("evidence", []),
            )
        else:
            e["name"] = e["name"].strip()
            e["evidence"] = merge_evidence(e.get("evidence", []))
            seen[key] = e

    unique = list(seen.values())
    if len(unique) <= 1:
        return unique

    # Phase 2: Same-type fuzzy dedup
    by_type = {}
    for e in unique:
        t = e["type"]
        if t not in by_type:
            by_type[t] = []
        by_type[t].append(e)

    merged = []
    for t, group in by_type.items():
        if len(group) <= 1:
            merged.extend(group)
            continue
        names = [e["name"] for e in group]
        duplicates = set()
        for i in range(len(names)):
            if i in duplicates: continue
            for j in range(i+1, len(names)):
                if j in duplicates: continue
                ni, nj = names[i].lower(), names[j].lower()
                if _names_are_duplicates(ni, nj):
                    duplicates.add(j)
        for i, e in enumerate(group):
            if i not in duplicates:
                merged.append(e)

    return merged


# ── v6 的辅助判据（2026-09-14）──────────────────────────────────────────
# 评测见 tools/l2_disambig_eval.py；改动记录见本函数 docstring。

# 称谓/头衔：不参与首字母缩写匹配，否则 "Dr" 会匹配 "D. R. Radev"
_TITLES = frozenset({
    "dr", "mr", "mrs", "ms", "prof", "sir", "miss", "phd", "md",
})


def _fold_flat(name: str) -> str:
    """NFKD 折叠 + 小写 + 去非字母数字。

    折叠重音是关键：`Rocktäschel` 与 `Rocktaschel` 是同一人，
    原实现因 ä ≠ a 而漏合并（评测负例池里实测 18 对）。
    """
    if not name:
        return ""
    n = unicodedata.normalize("NFKD", name)
    n = "".join(ch for ch in n if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "", n.lower())


def _tokens_lower(name: str):
    """切成小写实词（去掉标点与括号内容）。

    ── 2026-09-16（BUG-1 修复）：**拆驼峰** ─────────────────────────────
    原实现是「先 `lower()` 再按非字母数字切分」—— 于是**驼峰信息在被切之前
    就已经丢了**：`BERTLarge` 切成 `['bertlarge']` 一个词，
    而 `BERT Large`（带空格）切成 `['bert','large']` 两个词。

    后果是一个**自相矛盾**的行为：
      · `BERT` vs `BERT Large` → `_word_boundary_contains` 成立 ⇒ 合并 ✅
      · `BERT` vs `BERTLarge`  → 不成立 ⇒ 只能靠"短名长度 ≥5"的兜底，
        而 `bert` 只有 4 ⇒ **漏合** ❌
    **同一个东西，写法差一个空格，判据结果就相反。**

    修法：在 `lower()` **之前**按驼峰边界插空格。两条规则——
      · `(?<=[a-z0-9])(?=[A-Z])`  小写/数字 → 大写（`TabulaPDF` → `Tabula PDF`）
      · `(?<=[A-Z])(?=[A-Z][a-z])` 连续大写 → 大写+小写（`BERTLarge` → `BERT Large`）

    **全大写词不受影响**：`PDF`/`GNN`/`RDF` 里没有"大写后跟大写+小写"的位置，
    所以不会被拆开 —— 这正是我们要的（缩写必须保持整体）。
    """
    body = re.sub(r"\([^)]*\)", " ", name or "")
    body = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", body)
    body = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", body)
    return [t for t in re.split(r"[^0-9A-Za-z]+", body.lower()) if t]


def _is_initialism(short: str, long_: str) -> bool:
    """短名是不是长名的首字母缩写。

    例：`GNN` ← `Graph Neural Networks`；`KG` ← `Knowledge Graphs`；
        `EMNLP` ← `Conference on Empirical Methods in Natural Language Processing`。

    两条保护（实测必需）：
      · 短名长度 2–8，且短名里没有小写混排（缩写通常全大写，但这里已折成小写，
        所以改用「长度 + 首字母对齐」判断）；
      · 跳过的实词 ≤1 个、且跳过比例 ≤40% —— 否则 `KG` 会匹配上
        `Abstract Knowledge Graph`（跳过了 Abstract，比例 33% 侥幸通过，
        所以要再要求**首词必须对齐**）。
    """
    sl = _fold_flat(short)
    if not (2 <= len(sl) <= 8):
        return False
    toks = [t for t in _tokens_lower(long_) if t not in _TITLES]
    if not (2 <= len(toks) <= 12):
        return False
    initials = "".join(t[0] for t in toks)
    if initials != sl:
        return False
    # 首词首字母必须对齐（GNN/KG 都满足；Abstract Knowledge Graph 的 initials
    # 是 akg，与 kg 不等，已经被上面挡掉——这条是双保险）
    return toks[0][0] == sl[0]


def initialism_candidates(names):
    r"""在名字集合里找出「疑似 缩写 ↔ 全称」的候选对，**供人工审核**。

    ## 为什么它只能是"候选"

    见 `_names_are_duplicates` 的 ② 段：**「首字母对齐」不足以判定**，
    纯字符串判据拿不到"这个缩写是否另有全称"的信息，于是无法区分：

    ```
    KG   ← Knowledge Graphs                      该合
    RDF  vs Reference Data File                  不该合（RDF 真全称是
                                                 Resource Description Framework）
    ORKG vs Open Research Knowledge Graph        不该合（ORKG 已是独立名称，
                                                 合了它就从图谱里消失）
    ```

    ⇒ 自动合并已被废止（BUG-1），**但这个信号不能丢**：
    这些对正好是"最值得人工看一眼"的。本函数把它们列出来。

    ## 复杂度

    **不两两比**：全库约 2.4 万节点，两两是 5.6 亿对。
    改为先给"长名"建 `首字母串 → 名字` 的索引，再拿每个候选短名去查
    —— **一趟建索引 + 一趟查询**。

    返回 `[{'short','long','note'}]`，按 (短名, 长名) 排序、去重。
    """
    idx = {}
    for n in names:
        toks = [t for t in _tokens_lower(n) if t not in _TITLES]
        if 2 <= len(toks) <= 12:
            idx.setdefault("".join(t[0] for t in toks), []).append(n)

    out, seen = [], set()
    for n in names:
        s = _fold_flat(n)
        if not (2 <= len(s) <= 8):
            continue
        for m in idx.get(s, []):
            if m == n or _fold_flat(m) == s:
                continue
            if not _is_initialism(n, m):
                continue
            key = (s, _fold_flat(m))
            if key in seen:
                continue
            seen.add(key)
            out.append({
                'short': n, 'long': m,
                'note': '首字母对齐。**合并不可逆** —— 需人工确认该缩写是否另有全称、'
                        '或本身已是独立名称（如 ORKG）。',
            })
    out.sort(key=lambda d: (d['short'].lower(), d['long'].lower()))
    return out


def _plural_same(fa: str, fb: str) -> bool:
    """规范复数：只差一个 s / es（act/acts、class/classes）。"""
    for x, y in ((fa, fb), (fb, fa)):
        if x + "s" == y or x + "es" == y:
            return True
    return False


def _paren_stripped(name: str):
    """返回 (主名, 括注内容)。没有括注则括注为 None。"""
    m = re.search(r"\(([^)]*)\)", name or "")
    if not m:
        return name or "", None
    return (name[:m.start()] + name[m.end():]).strip(), m.group(1).strip().lower()


def _word_boundary_contains(a: str, b: str) -> bool:
    """a 的实词序列是否是 b 的实词序列的**连续子序列**（词边界对齐）。

    这比字符级 startswith 严得多：`Tag` 的 tokens 是 ['tag']，
    `Tagger` 是 ['tagger'] —— 子序列不成立，拒绝；
    而 `BERT` ⊂ `BERT Large` 成立（['bert'] ⊂ ['bert','large']），接受。
    """
    ta, tb = _tokens_lower(a), _tokens_lower(b)
    if not ta or not tb or len(ta) >= len(tb):
        return False
    for k in range(len(tb) - len(ta) + 1):
        if tb[k:k + len(ta)] == ta:
            return True
    return False


def _names_are_duplicates(a: str, b: str) -> bool:
    """保守地判断两个名字是否同一实体的变体，避免把相关词折叠到一起。

    ── 修订史 ──────────────────────────────────────────────────────────
    2026-09-10：子串规则原本没有任何长度保护，导致 `norm('UI')='ui'` 是
      `norm('build graph.py')='buildgraphpy'` 的子串，于是 "UI" 被判为其变体。
    2026-09-11：借鉴 Neo4j 官方 `neo4j-graphrag-python` 的 `FuzzyMatchResolver`
      （RapidFuzz `fuzz.WRatio`，阈值 0.8；实测比 difflib 快 4.4 倍）。
      但官方**没有长度保护**——同一对 `UI`/`build graph.py` 在 WRatio 下得 0.900
      会被误合并，我们的长度保护给 0.286 正确拒绝。故取两者之长。

    2026-09-14（本版）：用 `tools/l2_disambig_eval.py` 建了评测后发现两件事 ——
      · **误合并**：第三条「公共前缀 ≥0.4」**没有长度保护**（此前 docstring 声称
        修过，实际只给前两条加了）。`tag` vs `tagger` → 3/6=0.5 ≥ 0.4 被合并；
        `time` ⊂ `timestamp`、`graph` ⊂ `graphneuralnetwork` 同理。
      · **漏合并更严重**：`P5b-abbrev-initials` n=1200、`P5a` n=6，**F1 全是 0.000**
        ——判据完全认不出首字母缩写。`GNN`/`GCN`/`KG` 在论文里极常见，
        认不出就全是重复实体。

    现行判据（按先后）：
      ① flat 相等（NFKD 折叠后同形）—— 覆盖大小写 / 标点 / 重音差异；
      ② 首字母缩写（带首词对齐 + 跳过实词限制 + 称谓黑名单）；
      ③ 规范复数（+s / +es）；
      ④ 受限括注剥除（双方都有括注且不同则不剥）；
      ⑤ 词边界包含（`BERT` ⊂ `BERT Large`）；
      ⑥ 短名 ≥5 且占长名 ≥0.5 的后缀形态（`BERT`/`BERTLarge`）；
      ⑦ 模糊相似度 ≥0.93。

    **刻意不做**：首字母缩写只作候选、不进自动合并 —— 合并不可逆（T4.4）。
    **2026-09-16 实测**：真实语料里 93.6% 的缩写候选是「一短多长」的歧义对
    （见下方 ② 段的数字），自动合并会成片吞掉别的实体。
    """
    fa, fb = _fold_flat(a), _fold_flat(b)
    if not fa or not fb:
        return False

    # ① flat 相等（含 NFKD 重音折叠）
    if fa == fb:
        return True

    # ② 首字母缩写 —— **不再自动合并**（2026-09-16，BUG-1 修复）
    #
    # 本函数的 docstring 一直写着「**刻意不做**：首字母缩写只作候选、
    # 不进自动合并 —— 合并不可逆（T4.4）」，**但代码在这里 return True**。
    # 文档与实现矛盾，三个单元测试长期失败：
    #
    #     _names_are_duplicates('RDF', 'Reference Data File')  → 误合 ❌
    #     _names_are_duplicates('ORKG','Open Research Knowledge Graph') → 误合 ❌
    #         （后果：`ORKG` 这个被广泛使用的独立名称从图谱里**消失**）
    #
    # **为什么"首字母对齐"不足以判定**：纯字符串判据拿不到
    # 「这个缩写是否另有全称」的信息，于是无法区分：
    #     · `KG`   ← `Knowledge Graphs`              （该合）
    #     · `RDF`  ← 真全称是 Resource Description Framework，
    #                而 `Reference Data File` 只是**碰巧**首字母对齐（不该合）
    #     · `ORKG` ← 表面上是 `Open Research Knowledge Graph`，
    #                但它**已经是一个独立名称**，合了就没了（不该合）
    #
    # ⇒ **保守是对的，但信号不能丢**：改用 `initialism_candidates()`
    #    输出候选对供人工审核（见该函数）。
    #
    # ── 在真实语料上量过之后，才敢下这个结论（2026-09-16）──────────────
    #
    # 评测集里的 `L-B 缩写↔全称` 有 1206 条、**全是正例、且都是 1:1 的干净缩写**
    # （`JEF`←`Joint Extraction Framework` 这类）。在那个合成样本上，
    # 自动合并的 recall 是 **0.7048**。
    #
    # **但真实语料不是那样。** 拿当时的全库 **19877** 个实体名扫一遍候选
    # （2026-09-16 读数，属历史快照；当时的"全库"口径未写明。现全库为
    #  **20785** 节点，见 `web/kg-data/analytics.json` → `scale.nodes`）：
    #
    #     候选对 2142 条 · 涉及 298 个不同的短名
    #       · 只对应 1 个长名：137 个（46.0%）—— 这些合并还算安全
    #       · 对应 ≥2 个长名：161 个（54.0%）—— **合并必然丢掉别的东西**
    #     歧义最重的：'P. S.' → 60 个 · 'S. M.' → 60 个 · 'es' → 55 个 · 'rp' → 46 个
    #     ⇒ **落在歧义短名上的候选对占 93.6%**
    #
    # **93.6%** —— 这就是"自动合并缩写"的真实风险。
    # 合成样本上的 0.7048 recall 是在一个**只含 6.4% 情形**的分布上测出来的。
    # 而合并**不可逆**（T4.4）：把 `AD` 合进 `Anomaly Detection`，
    # 就同时丢掉了 `A. Doan` / `Action Dropout` / `Appendix D` 等 30 多个不同的实体。
    #
    # 原实现（**已废止，按 `docs/ARCHIVE_POLICY.md` 留存**）：
    #     if _is_initialism(a, b) or _is_initialism(b, a):
    #         return True

    # ③ 规范复数
    if _plural_same(fa, fb):
        return True

    # ④ 受限括注剥除
    pa, ca = _paren_stripped(a)
    pb, cb = _paren_stripped(b)
    if ca is not None or cb is not None:
        if _fold_flat(pa) and _fold_flat(pa) == _fold_flat(pb):
            # 双方都有括注且内容不同 → 不剥（Washington (Person)/(State)）
            if not (ca and cb and ca != cb):
                return True

    # ⑤⑥ 子串路径：收紧，删掉原来的裸"公共前缀 ≥0.4"
    if fa in fb or fb in fa:
        shorter, longer = (fa, fb) if len(fa) <= len(fb) else (fb, fa)
        if _word_boundary_contains(a, b) or _word_boundary_contains(b, a):
            return True
        # 后缀形态：短名够长且占比够大（BERT / BERTLarge）
        if len(shorter) >= 5 and len(shorter) / len(longer) >= 0.5:
            return True
        return False

    # ⑦ 模糊相似度
    return _similarity(fa, fb) >= 0.93


def _normalize_for_match(s: str) -> str:
    """名称归一化：小写、去标点。与 RapidFuzz 的 default_process 语义一致。"""
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


# RapidFuzz 可用时优先使用（Neo4j 官方 resolver 同款算法，实测快 4.4 倍）；
# 未安装则回退 difflib，行为一致，仅性能下降。
try:
    from rapidfuzz import fuzz as _rf_fuzz

    def _similarity(a: str, b: str) -> float:
        return _rf_fuzz.ratio(a, b) / 100.0

except ImportError:  # pragma: no cover - 依赖缺失时的兜底路径
    def _similarity(a: str, b: str) -> float:
        from difflib import SequenceMatcher as _SM
        return _SM(None, a, b).ratio()


# ── Pipeline ─────────────────────────────────────────────

def extract_chunk(chunk_text: str, model_text: str, mode: str, model_vis: str = None,
                  image_path: Optional[str] = None, content_kind: str = "text") -> dict:
    """Extract entities + relations from a single text chunk."""
    # Image context
    full_text = chunk_text
    if image_path:
        t0 = time.time()
        prompt_img = "Describe this image in detail. Focus on any entities, labels, data, or relationships visible."
        img_context = call_ollama(model_vis, prompt_img, image_path) if mode == "local" \
                 else call_siliconflow(model_vis, prompt_img, image_path)
        full_text = chunk_text + "\n\n---Image Context---\n" + img_context
        print(f"    [vision] {model_vis}  ({time.time()-t0:.1f}s)")

    # Phase 1: Entities
    t1 = time.time()
    if mode == "local":
        ep = build_local_entity_prompt(full_text, content_kind)
        raw_entities = call_ollama_json(model_text, ep)
        record_pair("entities", ep, raw_entities, {"model": model_text, "mode": mode, "kind": content_kind})
        entities = parse_structured_entities(raw_entities)
    else:
        ep = ENTITY_PROMPT.format(entity_types=", ".join(ENTITY_TYPES), text=full_text)
        raw_entities = call_deepseek(model_text, ep)
        record_pair("entities", ep, raw_entities, {"model": model_text, "mode": mode, "kind": content_kind})
        entities = parse_entities(raw_entities)
    if content_kind == "table":
        entities = retain_semantic_table_entities(entities)
    dt1 = time.time() - t1
    print(f"    [entities] {len(entities)} entities ({dt1:.1f}s)")

    # Phase 2: Relations
    t2 = time.time()
    entity_names = [e["name"] for e in entities]
    if not entity_names:
        return {"nodes": entities, "edges": [], "timing": {"entities_s": dt1, "relations_s": 0}}

    if mode == "local":
        rp = LOCAL_RELATION_PROMPT.format(
            entity_list="\n".join(f"- {n}" for n in entity_names),
            relation_types=", ".join(RELATION_TYPES),
            text=full_text,
        )
        raw_relations = call_ollama_json(model_text, rp)
        record_pair("relations", rp, raw_relations, {"model": model_text, "mode": mode, "kind": content_kind})
        relations = parse_structured_relations(raw_relations)
    else:
        rp = RELATION_PROMPT.format(
            entity_list="\n".join(f"- {n}" for n in entity_names),
            relation_types=", ".join(RELATION_TYPES),
            text=full_text,
        )
        raw_relations = call_deepseek(model_text, rp)
        record_pair("relations", rp, raw_relations, {"model": model_text, "mode": mode, "kind": content_kind})
        relations = parse_relations(raw_relations)
    relations = retain_relations_with_known_endpoints(relations, entity_names)
    dt2 = time.time() - t2
    print(f"    [relations] {len(relations)} relations ({dt2:.1f}s)")

    return {"nodes": entities, "edges": relations, "timing": {"entities_s": dt1, "relations_s": dt2}}


def extract(text: str, mode: str = "local", image_path: Optional[str] = None,
            max_chunk_chars: int = 500) -> dict:
    """Run full pipeline: chunk → extract per chunk → merge → dedup → global relations."""
    model_text = MODELS[mode]["text"]
    model_vis = MODELS[mode]["vision"]

    # Chunk long text
    chunks = chunk_text(text, max_chars=max_chunk_chars)
    use_chunking = len(chunks) > 1
    if use_chunking:
        print(f"  [chunking] {len(text)} chars → {len(chunks)} chunks")

    all_nodes = []
    all_edges = []
    total_e_s, total_r_s = 0, 0

    for ci, chunk in enumerate(chunks):
        prefix = f"  [chunk {ci+1}/{len(chunks)}] " if use_chunking else "  "
        print(f"{prefix}{len(chunk)} chars")
        res = extract_chunk(chunk, model_text, mode, model_vis, image_path)
        all_nodes.extend(res["nodes"])
        all_edges.extend(res["edges"])
        total_e_s += res["timing"]["entities_s"]
        total_r_s += res["timing"]["relations_s"]

    # Deduplicate
    if use_chunking:
        before_n = len(all_nodes)
        before_e = len(all_edges)
        all_nodes = deduplicate_entities(all_nodes, mode)
        # Dedup edges: same src/tgt/type → keep one
        seen_edges = set()
        deduped_edges = []
        for e in all_edges:
            key = (e["source"].lower(), e["target"].lower(), e["type"].lower())
            if key not in seen_edges:
                seen_edges.add(key)
                deduped_edges.append(e)
        all_edges = deduped_edges
        print(f"  [dedup] nodes: {before_n}→{len(all_nodes)}, edges: {before_e}→{len(all_edges)}")

    return {
        "nodes": all_nodes,
        "edges": all_edges,
        "timing": {"entities_s": round(total_e_s, 1), "relations_s": round(total_r_s, 1)},
        "model": mode,
        "model_text": model_text,
        "model_vision": model_vis if image_path else None,
        "chunks": len(chunks),
    }


def extract_from_pdf(pdf_path: str, mode: str = "cloud", max_chunk_chars: int = 500) -> dict:
    """Full pipeline: PDF → parse → chunk → extract → merge → JSON."""
    from pdf_parser import parse_pdf, chunk_document

    print(f"Parsing PDF: {pdf_path}")
    parsed = parse_pdf(pdf_path)
    chunked = chunk_document(parsed, max_chars=max_chunk_chars)

    print(f"  {parsed['total_pages']} pages, {chunked['total_chunks']} chunks\n")

    all_nodes = []
    all_edges = []
    total_e_s, total_r_s = 0, 0

    for ch in chunked["chunks"]:
        print(f"  [chunk {ch['chunk_idx']}/{chunked['total_chunks']}] page {ch['page']}, {ch['char_count']} chars")
        # Use first image on the page for vision context (if any)
        img_path = ch["images"][0]["path"] if ch["images"] else None
        res = extract_chunk(
            ch["text"], MODELS[mode]["text"], mode, MODELS[mode]["vision"], img_path,
            content_kind=ch.get("kind", "text"),
        )
        all_nodes.extend(res["nodes"])
        all_edges.extend(res["edges"])
        total_e_s += res["timing"]["entities_s"]
        total_r_s += res["timing"]["relations_s"]

    # Deduplicate
    before_n, before_e = len(all_nodes), len(all_edges)
    all_nodes = deduplicate_entities(all_nodes, mode)
    seen_edges = set()
    deduped_edges = []
    for e in all_edges:
        key = (e["source"].lower(), e["target"].lower(), e["type"].lower())
        if key not in seen_edges:
            seen_edges.add(key)
            deduped_edges.append(e)
    all_edges = deduped_edges
    print(f"\n  [dedup] nodes: {before_n}→{len(all_nodes)}, edges: {before_e}→{len(all_edges)}")

    return {
        "source": pdf_path,
        "nodes": all_nodes,
        "edges": all_edges,
        "timing": {"entities_s": round(total_e_s, 1), "relations_s": round(total_r_s, 1)},
        "model": mode,
        "model_text": MODELS[mode]["text"],
        "chunks": chunked["total_chunks"],
        "tables_found": len(chunked.get("tables", [])),
    }


# ── CLI ──────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="KG Entity & Relation Extractor")
    parser.add_argument("--text", help="Input text to extract from")
    parser.add_argument("--file", help="Input text file path")
    parser.add_argument("--pdf", help="Input PDF file path (full pipeline)")
    parser.add_argument("--image", help="Optional image file for multimodal extraction")
    parser.add_argument("--mode", choices=["local", "cloud"], default="cloud",
                        help="Model backend (default: cloud)")
    parser.add_argument("--compare", action="store_true",
                        help="Run both local and cloud and compare results")
    parser.add_argument("--output", help="Output JSON file path")
    parser.add_argument("--max-chars", type=int, default=500,
                        help="Max chars per chunk (default: 500)")
    args = parser.parse_args()

    # PDF mode
    if args.pdf:
        result = extract_from_pdf(args.pdf, args.mode, args.max_chars)
        output_json = json.dumps(result, ensure_ascii=False, indent=2)
        if args.output:
            Path(args.output).write_text(output_json, encoding="utf-8")
            print(f"\nSaved to {args.output}")
        else:
            print(f"\n── RESULT ({len(result['nodes'])} nodes, {len(result['edges'])} edges) ──")
            print(json.dumps({"nodes": result["nodes"][:5], "edges": result["edges"][:5], "...": f"({len(result['nodes'])} total)"}, ensure_ascii=False, indent=2))
        return

    # Load text
    text = args.text
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")

    if not text or len(text.strip()) < 10:
        print("Error: need --text, --file, or --pdf")
        sys.exit(1)

    print(f"Input: {len(text)} chars")
    print()

    if args.compare:
        results = {}
        for mode in ["local", "cloud"]:
            print(f"── {mode.upper()} ──")
            try:
                results[mode] = extract(text, mode, args.image)
                print()
            except Exception as e:
                print(f"  FAILED: {e}\n")
                results[mode] = {"error": str(e)}

        # Comparison summary
        print("══ COMPARISON ══")
        for mode, r in results.items():
            if "error" in r:
                print(f"  {mode}: ERROR — {r['error']}")
            else:
                t = r["timing"]
                print(f"  {mode}: {len(r['nodes'])} entities, {len(r['edges'])} relations, "
                      f"{t['entities_s']}s + {t['relations_s']}s = {t['entities_s']+t['relations_s']}s")
    else:
        results = extract(text, args.mode, args.image, args.max_chars)

    # Output
    output_json = json.dumps(results, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(output_json, encoding="utf-8")
        print(f"\nSaved to {args.output}")
    else:
        print(f"\n── RESULT ──")
        print(output_json)


if __name__ == "__main__":
    main()
