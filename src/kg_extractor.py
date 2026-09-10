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

import argparse, json, os, sys, time, base64, re, threading
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

from graph_store import merge_evidence


def _load_registry_env() -> None:
    """补齐进程内可能缺失的机器级环境变量。

    批量脚本经常从非交互 shell 启动，未必继承到注册表里的密钥；这里做一次
    兜底读取，避免"本地有 key、脚本却报 401"的假故障。必须在读取配置常量
    之前调用。
    """
    try:
        import winreg
    except ImportError:
        return
    locations = [
        (winreg.HKEY_LOCAL_MACHINE,
         r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
        (winreg.HKEY_CURRENT_USER, r"Environment"),
    ]
    for hive, path in locations:
        try:
            with winreg.OpenKey(hive, path) as key:
                index = 0
                while True:
                    try:
                        name, value, _ = winreg.EnumValue(key, index)
                    except OSError:
                        break
                    os.environ.setdefault(name, str(value))
                    index += 1
        except OSError:
            continue


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
PART_OF, USES, PRODUCES, MEASURES, EVALUATES, COMPARES_WITH, SUPPORTS, CONTRADICTS, CAUSES, ENABLES, APPLIES_TO, LOCATED_AT, CREATED_BY, CITES, RELATED_TO

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
        raw = call_ollama_json(model_text, prompt)
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

def call_ollama(model: str, prompt: str, image_path: Optional[str] = None) -> str:
    """Call local Ollama model."""
    import requests
    payload = {"model": model, "prompt": prompt, "stream": False,
               "options": {"temperature": 0.1, "num_predict": 2048, "num_ctx": 4096, "enable_thinking": False}}
    if image_path:
        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode()
        payload["images"] = [img_b64]
    r = requests.post(f"{OLLAMA}/api/generate", json=payload, timeout=120)
    r.raise_for_status()
    data = r.json()
    return data.get("response") or data.get("thinking", "")


def call_ollama_json(model: str, prompt: str, schema: Optional[dict] = None,
                     image_path: Optional[str] = None) -> dict:
    """Call Ollama with a JSON Schema and return the decoded object."""
    import requests
    images = None
    if image_path:
        with open(image_path, "rb") as f:
            images = [base64.b64encode(f.read()).decode()]
    payload = build_local_json_payload(model, prompt, schema, images)
    r = requests.post(f"{OLLAMA}/api/generate", json=payload, timeout=120)
    r.raise_for_status()
    response = r.json().get("response", "").strip()
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
    """
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

def chunk_text(text: str, max_chars: int = 500, overlap_ratio: float = 0.3) -> list[str]:
    """
    Structure-aware chunking with sliding window overlap.

    - Splits on paragraph/sentence boundaries
    - Keeps paragraphs intact when possible
    - Overlap: last N chars of previous chunk prepended to next chunk
    - Falls back to sentence splitting for very long paragraphs
    """
    if len(text) <= max_chars:
        return [text]

    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks = []
    current = ""

    for para in paragraphs:
        # If adding this paragraph fits, do it
        if len(current) + len(para) + 2 <= max_chars:
            current = (current + "\n\n" + para).strip() if current else para
        else:
            # Current chunk is full → save it
            if current:
                chunks.append(current)
            # If this paragraph alone is too long, split by sentences
            if len(para) > max_chars:
                sentences = [s.strip() + "." for s in para.replace("!", ".").replace("?", ".").split(".") if s.strip()]
                sub = ""
                for sent in sentences:
                    if len(sub) + len(sent) <= max_chars:
                        sub = (sub + " " + sent).strip() if sub else sent
                    else:
                        if sub:
                            chunks.append(sub)
                        sub = sent
                if sub:
                    current = sub
                else:
                    current = ""
            else:
                current = para

    if current:
        chunks.append(current)

    # Apply sliding window overlap
    if overlap_ratio > 0 and len(chunks) > 1:
        overlapped = [chunks[0]]
        overlap_chars = int(max_chars * overlap_ratio)
        for i in range(1, len(chunks)):
            prev = chunks[i - 1]
            if len(prev) > overlap_chars:
                prefix = prev[-overlap_chars:]
                # Start overlap at a word boundary
                space_idx = prefix.find(" ")
                if space_idx > 0:
                    prefix = prefix[space_idx:].strip()
                overlapped.append(prefix + "\n\n" + chunks[i])
            else:
                overlapped.append(chunks[i])
        chunks = overlapped

    return chunks


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


def _names_are_duplicates(a: str, b: str) -> bool:
    """Conservatively detect name variants without collapsing related terms.

    2026-09-10 修复：子串规则原本没有任何长度保护，导致短缩写与任意包含
    其字母序列的长名字被判为同一实体——实测 `norm('UI')='ui'` 是
    `norm('build graph.py')='buildgraphpy'` 的子串（bUIldgraphpy），于是
    "UI" 被当成 "build graph.py" 的变体。这类误合并同时污染抽取去重与
    金标聚类，且越短的缩写（UI/RDF/AI/LLM）越容易中招。

    现要求子串匹配同时满足以下任一条件：
      - 短名足够长（≥5 字符，足以排除缩写）；
      - 两者共享的前缀占长名的比例 ≥0.4（即"全称 = 简称 + 后缀"的形态，
        如 BERT / BERTLarge 共享前缀 bert 占 4/9）。
    实测反例 UI / build graph.py：前缀仅 'b'，占比 0.08，正确拒绝。
    """
    normalized_a = re.sub(r"[^a-z0-9]+", "", a.lower())
    normalized_b = re.sub(r"[^a-z0-9]+", "", b.lower())
    if not normalized_a or not normalized_b:
        return False

    if normalized_a in normalized_b or normalized_b in normalized_a:
        shorter, longer = sorted((normalized_a, normalized_b), key=len)
        if len(shorter) >= 5:
            return True
        common = 0
        for x, y in zip(shorter, longer):
            if x != y:
                break
            common += 1
        return common / len(longer) >= 0.4

    return SequenceMatcher(None, normalized_a, normalized_b).ratio() >= 0.93


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
