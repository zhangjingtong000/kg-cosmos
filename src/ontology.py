"""项目本体的**唯一来源**。所有脚本从这里取常量，不要各写一份。

【为什么要有这个文件】
  `docs/ERROR_CASES.md` 案例 12 记录了一类反复出现的问题：**同一条规则写在两处，
  就一定会在某处错开，而且不报错**。这个项目已经因此栽过三次：

    1. 本体关系类型：`sync_graph.py` 的入库白名单含 `CONTRIBUTED_TO`（16 类），
       而 `l5_ontology_audit.py` 按 15 类审计 —— 同一批边入库时合法、
       审计时越界，L4 报 99.1% 却没人知道原因。
    2. L5 判定：`relation_verifier.py` 写 `pass` 字段（用未舍入分数），
       `l5_report.py` 现场比 `entail_prob >= thr`（用 4 位小数），
       43867 条里有 3 条判定相反。
    3. L7 的 `AUTHOR`/`CREATABLE` 从 `apply_created_by_fix.py` 复制到
       `l7_direction_verify.py` —— 等于用补丁的假设去验证补丁。

  本模块把这几组常量收成一份，并配一个自检脚本
  （`tools/check_ontology_consistency.py`）去**相减**各处结果，
  让分歧显形而不是潜伏。

【怎么用】
    from ontology import ENTITY_TYPES, RELATION_TYPES, ALLOWED_ENTITY_LABELS
  路径由各脚本自行 `sys.path.insert(0, .../src)`，与既有写法一致。

【重要区分：本体 vs 派生标签】
  `ENTITY_TYPES` 是**抽取器能产出的 15 类**，它必须与
  `kg_extractor.ENTITY_TYPES` 完全一致（tests/test_ontology_whitelist.py 守这条）。
  `DERIVED_ENTITY_LABELS` 是**后处理写入**的标签，不属于本体。
  两者分开，是为了让"本体 == 抽取器能力"这条不变量继续成立。
"""
from __future__ import annotations

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# 抽取器本体 —— 真正的单一来源仍是 kg_extractor，这里只做统一出口，
# 避免为了"抽常量"去动那个 58KB 的核心文件。
from kg_extractor import ENTITY_TYPES as _KG_ENTITY_TYPES          # noqa: E402
from kg_extractor import RELATION_TYPES as _KG_RELATION_TYPES      # noqa: E402

ENTITY_TYPES = frozenset(_KG_ENTITY_TYPES)
RELATION_TYPES = frozenset(_KG_RELATION_TYPES)

# ── 派生标签 ────────────────────────────────────────────────────────────
# `Unknown`：从引用它的边反建出来的端点（tools/backfill_missing_endpoints.py，
# 2026-09-12 用户决策）。这些实体在原文里出现过、却没被抽成节点，
# 类型确实无从得知；与其猜一个（实测邻居推断会把
# `Document-Level Relation Extraction` 猜成 `Person`），不如如实标 Unknown。
DERIVED_ENTITY_LABELS = frozenset({"Unknown"})

# 存储层实际允许出现的实体标签 = 本体 ∪ 派生
ALLOWED_ENTITY_LABELS = ENTITY_TYPES | DERIVED_ENTITY_LABELS

# ── L7 方向校验的端点类型约束表（15 类关系的本体设计决策）──────────────
#
# 【这张表解决什么】
#   L7 的判据是"方向由端点类型约束住"。已确证六层都不看方向（L3 只看端点是否
#   在节点集内、L4 只看谓词是否在本体内；L5 实测只能拦住 23.9% 的方向反转，
#   见 docs/L5_SIX_LAYER_REPORT.md §4.5）。所以需要一层纯规则、零成本、可复现
#   的方向判据，本表是那一层的**唯一来源**。
#
# 【为什么不是"从数据统计推导"】
#   已证伪：tools/l7_direction_audit.py 实测 16 类的"主导端点类型对"占比全部
#   < 60%（最高 CREATED_BY 也才 40.2%），端点类型分布极其分散，不存在稳定的
#   "主导方向"可供统计判断。所以本表是**本体设计决策**——由关系语义决定两侧
#   允许扮演什么角色；数据只用于两件事：
#     (1) 事后核对：允许集是否把现有数据大面积误杀（各条 evidence 字段）；
#     (2) 承认判不了：core 为空即表示"该关系的类型分布本身双向"，
#         不硬编一个方向，避免把抽取噪声误报成方向错误。
#
# 【字段】
#   semantics      一句话语义。与 tools/verify_direction.py 的 REL_TEMPLATE 对齐：
#                  source 是模板里的 {s}，target 是 {t}。
#   kind           directed      方向敏感，可判
#                  symmetric     语义对称，**不判方向**（沿用 verify_direction.py
#                                既有的 SYMMETRIC 划分，不另立一套）
#                  unconstrained 兜底或多义关系，无有效类型约束
#   source_core    方向指纹·源角色（窄）
#   target_core    方向指纹·目标角色（窄）。两者**不相交**时方向由类型唯一决定：
#                  正向命中 = 合规，反向命中 = 方向错（reversed）。
#                  为空集 = 该关系在类型上判不了方向，只做角色检查。
#   source_allowed 角色允许集（宽），判"端点类型扮这个角色合理吗"，**不判方向**。
#   target_allowed 故意取宽：端点类型是抽取出来的标签、本身有噪声
#                  （docs/L5_SIX_LAYER_REPORT.md §5.1），约束太严会把类型噪声
#                  误报成方向错误。
#   basis          依据：semantics（语义常识）/ convention（既有约定）/ data（数据）
#   evidence       数据佐证（2026-09-12，relation-verify-all.json 全量 43867 条边）
#   reversal       "judgeable" / "unjudgeable"：本关系能否做方向判定
#   template_symmetric  L5 的假设句模板反转后语义是否不变（口径与 tools/verify_direction.py 的 SYMMETRIC 一致）
#
# 【口径说明（本项目特有，必须知道）】
#   `Event` 在这套图里**实际装的是发表 venue（AAAI / ICLR / NeurIPS / ACL …）**，
#   不是"事件"。实测 PART_OF 的 target=Event 588 条、CITES 的 target=Event 104 条，
#   描述几乎全是 "The paper was published in XXX"。把它从任何关系的允许集里删掉
#   都会制造大批假阳性，所以除少数明确不适用的位置外一律保留，并在报告里单列。
_AGENT = frozenset({"Person", "Organization"})
_WORK = frozenset({
    "Document", "Method", "System", "Tool", "Artifact", "Dataset",
    "Component", "Concept", "Process", "Metric", "Material",
})
_TECH = frozenset({
    "Method", "System", "Component", "Tool", "Artifact", "Dataset",
    "Process", "Material",
})
_KNOW = frozenset({"Concept", "Metric"})
_ALL = ENTITY_TYPES


def _constraint(semantics, kind, source_core, target_core, source_allowed,
                target_allowed, basis, evidence, reversal,
                template_symmetric=False):
    return {
        "semantics": semantics,
        "kind": kind,
        "source_core": frozenset(source_core),
        "target_core": frozenset(target_core),
        "source_allowed": frozenset(source_allowed),
        "target_allowed": frozenset(target_allowed),
        "basis": basis,
        "evidence": evidence,
        "reversal": reversal,
        "template_symmetric": template_symmetric,
    }


RELATION_ENDPOINT_CONSTRAINTS = {
    # ── 2026-09-14 新增：补上「论文 → 发表场所」这一类 ──────────────────
    #
    # **为什么之前没有**：学术图谱里最常见的关系之一，本体却漏了。
    # 抽取器只能把「发表于」硬套进最接近的类型，实测污染三类：
    #     PART_OF     827 条   TuckER —[PART_OF]→ EMNLP-IJCNLP
    #     LOCATED_AT  323 条   TransNet —[LOCATED_AT]→ IJCAI
    #     CREATED_BY  192 条   R. Das —[CREATED_BY]→ EACL
    # 规模与逐条清单见 `data/quality/venue-rewrite-plan.json`。
    #
    # venue 端点在数据里的实际类型分布（实测 1019 条可改写边）：
    #     Event 811 · Document 124 · Unknown 78 · 类型未知 6
    # 所以 target_allowed 取 {Event, Document, Organization, Location} ——
    # 前两个覆盖 91.8%，后两个是 venue 可能被标成的类型（学会/地点）。
    "PUBLISHED_IN": _constraint(
        semantics="source 是论文/作品，target 是它发表的会议、期刊或出版场所。"
                  "这是「论文发表于会议」，**不是**「论文是会议的一部分」"
                  "（那会写成 PART_OF），也**不是**「论文位于某地」（LOCATED_AT）。",
        kind="directed",
        # ⚠️ **方向的适用边界**：论文与期刊/论文集**同型**（都是 Document），
        # 只有会议（Event）作为 target 时才分得开。所以 core 取 {Event}
        # 以保证指纹不相交（自检要求：有指纹就必须标 judgeable），
        # 但**实际只有 Event 类 venue 的方向可判**，Document 类判不了 ——
        # 这一点写进 evidence，避免被读成"这一类方向全都可判"。
        source_core=_WORK,
        target_core=frozenset({"Event"}),
        source_allowed=_WORK,
        target_allowed=frozenset({"Event", "Document", "Organization", "Location"}),
        basis="ontology-extension",
        evidence="本体扩展的动因实测：被误套为 PART_OF 745 条、LOCATED_AT 274 条、"
                 "CREATED_BY 129 条（其中 target 命中 venue 判据）；"
                 "venue 端点类型 Event 811 / Document 124，占 91.8%。"
                 "**方向可判性有边界**：Document 类 venue（期刊/论文集）与论文同型，"
                 "方向指纹判不了；只有 Event 类 venue（811 条，占 79.6%）可判。",
        reversal="judgeable",
    ),
    # ── 方向真正可判的（core 与 core 不相交，方向由类型唯一决定）──
    "CREATED_BY": _constraint(
        semantics="source 是创作者（作者/机构），target 是被创作物。"
                  "沿用 T3.1 统一后的 Author → Work 约定，与 REL_TEMPLATE 的 "
                  '"{t} was created by {s}." 一致。',
        kind="directed",
        source_core=_AGENT,
        target_core=_WORK,
        source_allowed=_AGENT,
        target_allowed=_WORK,
        basis="convention",
        # 数字已实测复核（2026-09-12，可判定 6241 条口径，已排除 Unknown 端点）：
        #   source ∈ Author      : 5911/6241 = 94.7%
        #   target ∈ Creatable   : 5972/6241 = 95.7%   ← 原写 92.0%，实测不符已修
        #   两者都合法（=方向合规）: 5819/6241 = 93.2%
        evidence="可判定 6241 条中 source 侧 Person/Organization 覆盖 94.7%、"
                 "target 落 11 类产物占 95.7%。",
        reversal="judgeable",
    ),
    "LOCATED_AT": _constraint(
        semantics="source 是有位置的实体，target 是它所在的地点/场所/机构。",
        kind="directed",
        source_core=_ALL - {"Location", "Organization", "Event"},
        target_core={"Location", "Organization", "Event"},
        source_allowed=_ALL,
        target_allowed=_ALL - {"Person", "Method", "Metric", "Material"},
        basis="semantics",
        evidence="可判定 590 条中 target=Event 241（venue）+ Location 136"
                 " + Organization 17 = 66.8%；反向（地点作 source）少见。"
                 "注意 target=Document 65 条实为「在附录/第 III-C 节」，"
                 "target=Concept 44 条实为数学空间隐喻（on a torus / vector space），"
                 "二者语义扩张但方向未错，故保留在允许集内、只作弱合规。",
        reversal="judgeable",
    ),
    "PRODUCES": _constraint(
        semantics="source 产出/生成 target（source 是生产者，target 是产物）。",
        kind="directed",
        source_core={"Method", "System", "Process", "Person"},
        target_core={"Concept", "Artifact"},
        # ⚠️ 2026-09-12 放宽 allowed（依据：58 条 role_violation 的独立判读）。
        # 判读发现 5 条假阳性，成因集中，全部是"允许集排掉了正常用法"：
        #   · NER → 人名/地名：命名实体识别的产物就是被识别出的实体
        #     ⇒ target 纳入 Person / Location
        #   · 会议 → 论文：会议发表论文（source 是 Event）
        #     ⇒ source 纳入 Event
        #   · Loss → Gradient：损失函数产生梯度（source 是 Metric）
        #     ⇒ source 纳入 Metric
        # 注意：只动 allowed，**不动 source_core / target_core**——
        # 方向指纹是另一回事（_DIRECTION_GUARD 钉着它），放宽 allowed
        # 只是让更多边通过角色检查，不改变方向判定。
        source_allowed=_ALL - {"Location", "Material"},
        target_allowed=_ALL - {"Organization", "Event"},
        basis="semantics",
        evidence="可判定 1519 条：source 侧 Method 438（28.8%）/ Concept 304（20.0%）/"
                 " System 210（13.8%）/ Process 172（11.3%）/ Person 115（7.6%）；"
                 "target 侧 Concept 903（59.4%）/ Artifact 215（14.2%）。"
                 "Component（6.8% vs 4.5%）、Document、Tool、Dataset 两侧占比接近，"
                 "不进指纹——实测把 Component 放进去会把「Encoder and quantization "
                 "produces quantized representation」这类正确边误报成反转。"
                 "Concept 在 target 侧占比是 source 侧的 3 倍，故只进 target 指纹。",
        reversal="judgeable",
    ),
    "EVALUATES": _constraint(
        semantics="source 评测/报告 target 的效果或质量。注意「用数据集评估方法」"
                  "在本图里既被写成「数据集 → 方法」也被写成「方法 → 数据集」，"
                  "两种读法都合乎英文原文（dataset for evaluating methods）。",
        kind="directed",
        source_core=frozenset(),
        target_core=frozenset(),
        source_allowed=_ALL - {"Location", "Event", "Material"},
        target_allowed=_ALL - {"Person", "Location", "Event", "Material", "Organization"},
        basis="data",
        evidence="可判定 302 条：Method→Dataset 91（30.1%）为最大单格，但 "
                 "Dataset→Method 也有 2 条、Process→Method 3 条。实测按 "
                 "core={Method,Document,Person}→{Dataset,Concept} 判出的 8 条「反转」"
                 "经人工逐条核对**全部是假阳性**（FewRel 数据集用于评估方法、"
                 "Evaluation 评估 Edge Prior 等，方向本身没错），故撤销方向指纹。",
        reversal="unjudgeable",
    ),
    "APPLIES_TO": _constraint(
        semantics="source 是被应用的方法/技术，target 是它被应用到的对象或领域。",
        kind="directed",
        source_core=frozenset(),
        target_core=frozenset(),
        # 2026-09-12 放宽：独立判读里有 1 条假阳性是
        # 「Data → Screening」（`Early accumulated data underwent screening`），
        # 即数据/物料作为"被处理的对象"，source 是 Material 属正常用法 ⇒ 纳入。
        source_allowed=_ALL - {"Location", "Event"},
        target_allowed=_ALL,
        basis="semantics",
        evidence="可判定 2521 条：source 侧 Method 1038（41.2%）/ Concept 635（25.2%）；"
                 "target 侧 Concept 1423（56.4%）/ Process 424（16.8%）/ Method 256。"
                 "Concept、Process、Method 在两侧都显著，任何切法都会把另一半合法用法"
                 "误判——实测把 Concept 排除出源角色后，Concept→Process 的 98 条"
                 "「知识图谱用于问答」全部被误报为方向反转。故不做方向判定。",
        reversal="unjudgeable",
    ),
    # ── 方向敏感但类型判不了向（core 为空：正反两侧类型都常见）──
    "PART_OF": _constraint(
        semantics="source 是 target 的组成部分/下级结构。",
        kind="directed",
        source_core=frozenset(),
        target_core=frozenset(),
        source_allowed=_ALL,
        target_allowed=_ALL - {"Person"},
        basis="semantics",
        evidence="可判定 4001 条，最高格 Concept→Concept 仅 17.3%；"
                 "Document→Event 305 与 Method→Event 98 实为「发表于某会议」，"
                 "Person→Organization 99 实为 affiliation，Person 也可作整体"
                 "（成员是机构的一部分），故正反两侧同族，类型无法定方向。",
        reversal="unjudgeable",
    ),
    "USES": _constraint(
        semantics="source 使用/依赖 target 作为手段、数据或组件。",
        kind="directed",
        source_core=frozenset(),
        target_core=frozenset(),
        source_allowed=_ALL - {"Location", "Event", "Material"},
        target_allowed=_ALL - {"Person"},
        basis="semantics",
        evidence="可判定 4857 条：Method→Concept 821（16.9%）与 Concept→Method 168"
                 "并存，Method/System/Process/Concept 在两侧都常见，类型定不了向。"
                 "Person 在 source 侧 7.3%、target 侧仅 0.8%，故只排除「被使用物是"
                 "人」这一种角色——但实测它多数是谓词误用（requires human experts）"
                 "而非方向错误，所以只进角色审计、不进方向指标。Organization 与"
                 "Material 作被使用物语义合法（使用数据提供方的数据、使用文本内容），"
                 "**不排除**，否则会误杀「Federated learning combines features from "
                 "different data providers」这类正确边。",
        reversal="unjudgeable",
    ),
    "SUPPORTS": _constraint(
        semantics="source 支持 target（证据/方法支持论断或流程）。",
        kind="directed",
        source_core=frozenset(),
        target_core=frozenset(),
        source_allowed=_ALL - {"Location", "Material"},
        target_allowed=_ALL - {"Location", "Material"},
        basis="semantics",
        evidence="可判定 560 条：Concept→Concept 101 且反向对 97（几乎自对称），"
                 "两端均为「论断/方法/过程」，类型上无排他性。",
        reversal="unjudgeable",
    ),
    "CAUSES": _constraint(
        semantics="source 是原因，target 是结果。语义不可逆，但两端类型重叠。",
        kind="directed",
        source_core=frozenset(),
        target_core=frozenset(),
        source_allowed=_ALL - {"Location", "Material"},
        # 2026-09-12 放宽：独立判读里 2 条假阳性都是
        # `KG selection / KG discovery poses a challenge for humans`——
        # 「某事对人是挑战」是 CAUSES 的正常用法，target 排除 Person 过严。
        target_allowed=_ALL - {"Location"},
        basis="semantics",
        evidence="可判定仅 196 条：Concept→Concept 50、Concept→Metric 24、"
                 "Process→Concept 15、Concept→Process 10 —— Concept/Process 两侧"
                 "都是常见角色，样本量也低于 200，不足以支撑方向指纹。",
        reversal="unjudgeable",
    ),
    "ENABLES": _constraint(
        semantics="source 使能 target（提供条件/能力，使 target 成立或发生）。",
        kind="directed",
        source_core=frozenset(),
        target_core=frozenset(),
        # ⚠️ 2026-09-12 大幅放宽——这是全表假阳性率最高的一类。
        # 独立判读的 5 条样本**全部**是假阳性，成因完全一致：
        # 允许集把 Person / Document / Material 从两端排掉，而这三类恰是
        #   · 人被使能：`Advancing hardware capabilities enabled researchers to…`
        #   · 文献被使能：`Pre-test served as foundation for reviews`
        #   · 物质被使能：`Bio2Token holds potential for biomolecular complex design`
        # 的正常用法。Organization 与 Person 同族（"条件使组织能做某事"），一并纳入。
        # 这 5 条占第二批样本的 45%，是"约束太严"最主要的来源。
        source_allowed=_ALL - {"Location", "Event"},
        target_allowed=_ALL - {"Location", "Event"},
        basis="semantics",
        evidence="可判定 739 条：Concept→Concept 162 与 Concept→Process 92 为主，"
                 "Concept/Method/Process 两侧都常见；Person 侧仅 0.5%。",
        reversal="unjudgeable",
    ),
    "CITES": _constraint(
        semantics="source 引用 target（引用者 → 被引用的文献/方法/作者）。",
        kind="directed",
        source_core=frozenset(),
        target_core=frozenset(),
        source_allowed=_ALL - {"Location", "Material"},
        target_allowed=_ALL - {"Location", "Material"},
        basis="semantics",
        evidence="可判定 639 条：Document→Person 79 与 Person→Document 69 并存，"
                 "Document 与 Person 两侧都是常见角色，类型定不了向；"
                 "target=Event 104 条实为「发表于某会议」，属谓词误用而非方向错，"
                 "故保留在允许集内。",
        reversal="unjudgeable",
    ),
    # ── 对称：语义反转后含义不变，按定义不判方向 ──
    "COMPARES_WITH": _constraint(
        semantics="source 与 target 互相比对，谁在左谁在右语义相同。",
        kind="symmetric",
        source_core=frozenset(),
        target_core=frozenset(),
        source_allowed=_ALL - {"Location", "Event", "Material"},
        target_allowed=_ALL - {"Location", "Event", "Material"},
        basis="semantics",
        evidence="可判定 374 条：Method↔Method 89/93、Concept↔Concept 59/56、"
                 "System↔System 43/43 —— 同类互比为绝对主流，正反两向完全对称。"
                 "verify_direction.py 亦把本类列入 SYMMETRIC。",
        template_symmetric=True,
        reversal="unjudgeable",
    ),
    "CONTRADICTS": _constraint(
        semantics="source 与 target 互相矛盾，矛盾无方向。",
        kind="symmetric",
        source_core=frozenset(),
        target_core=frozenset(),
        source_allowed=_ALL - {"Location", "Material", "Person", "Organization"},
        target_allowed=_ALL - {"Location", "Material", "Person", "Organization"},
        basis="semantics",
        evidence="可判定 176 条：Concept→Concept 57，其反向对 55 —— 正反几乎相等，"
                 "是本图对称性最干净的一类。verify_direction.py 亦列入 SYMMETRIC。",
        template_symmetric=True,
        reversal="unjudgeable",
    ),
    # ── 多义/兜底：明确不判 ──
    "MEASURES": _constraint(
        semantics="source 度量/刻画 target。本图内至少四种读法混用："
                  "指标→概念（plausibility score measures …）、"
                  "方法→指标（TransH has an MRR of 303，实为 has-value）、"
                  "概念→指标（knowledge quality measures …）、"
                  "以及「某指标的定义式」。方向不唯一，不做方向判定。",
        kind="unconstrained",
        source_core=frozenset(),
        target_core=frozenset(),
        # ⚠️ kind=unconstrained 时 verdict() 提前返回，这两个字段**不参与判定**。
        # 原先它们写的是"实测出现的 10 类"（排除 Person/Organization/Location/
        # Event/Material），看着像一条约束，实际从不生效——读者会误以为
        # MEASURES 受类型约束。改成全集（诚实：不约束），
        # 那 10 类的观察结果挪到 basis 里作为信息保留。
        source_allowed=_ALL,
        target_allowed=_ALL,
        basis="data",
        evidence="可判定 537 条：Metric→Concept 55 与 Concept→Metric 43 几乎相等，"
                 "Method→Metric 54 与 Method→Concept 54 并列 —— 正反两向的语义"
                 "都成立，任何单向约束都会把另一半误报成方向错误。",
        reversal="unjudgeable",
    ),
    "RELATED_TO": _constraint(
        semantics="兜底关系：抽取器在两实体有联系但说不清属于哪一类时使用，"
                  "没有可判定的方向语义。",
        kind="unconstrained",
        source_core=frozenset(),
        target_core=frozenset(),
        source_allowed=_ALL,
        target_allowed=_ALL,
        basis="data",
        evidence="可判定 6625 条，最高格 Person→Person 仅 17.6%，"
                 "需 13 个 source 类型才覆盖 99% —— 全图最分散的一类。"
                 "verify_direction.py 亦列入 SYMMETRIC（模板反转后语义不变）。",
        template_symmetric=True,
        reversal="unjudgeable",
    ),
}

# 语义对称的关系。
#
# ⚠️ 必须是**显式字面量**，不能写成从表里派生：
#     SYMMETRIC_RELATIONS = frozenset(n for n, c in ... if c["template_symmetric"])
# 那样下面 validate_constraints 的自检就变成"自己和自己比"，**恒为真、永不报错**
# ——自检形同虚设。对抗评审实测指出过这一点，并复现了它。
# 口径来源：tools/verify_direction.py 的 SYMMETRIC 划分；
# check_ontology_consistency.py 会把它与表中 template_symmetric 相减。
SYMMETRIC_RELATIONS = frozenset({"COMPARES_WITH", "CONTRADICTS", "RELATED_TO"})

# 方向指纹的**外部断言表**。
#
# 为什么需要它：validate_constraints 里的检查全是"表内部自洽性"
# （类型名合法、core 与 allowed 相容、两侧不相交……）。实测把这些**全部满足**
# 之后，把 CREATED_BY 的 source_core/target_core 对调，自检依然返回 0 条问题——
# 因为"改反了"本身并不违反任何内部一致性。
# 方向是一个**外部事实**（"谁创建了谁"），只能用外部断言钉住，不能靠自洽性推出来。
# 这三类是全表仅有的、有方向指纹的关系（见 RELATION_ENDPOINT_CONSTRAINTS 的 kind）。
_DIRECTION_GUARD = {
    "CREATED_BY": (frozenset({"Person", "Organization"}), _WORK),
    "PRODUCES": (frozenset({"Method", "System", "Process", "Person"}),
                 frozenset({"Concept", "Artifact"})),
    "LOCATED_AT": (_ALL - frozenset({"Location", "Organization", "Event"}),
                   frozenset({"Location", "Organization", "Event"})),
}

# 注意这里与 tools/apply_created_by_fix.py 的关系：那份是**做改动的规则**，
# 这里只做**校验**。两者取值必须同源，否则就成了"用补丁的假设验证补丁"
# （审计指出过）。现在两边都 import 本模块的同一张表。
#
# ⚠️ 但要清楚：**同源不等于独立**。T3.1 正是用这套 AUTHOR/CREATABLE
# 反转了 2348 条 CREATED_BY 边，所以 L7 在 CREATED_BY 上报出的高合规率里，
# 有相当一部分是"用同一套规则改完之后再用同一套规则检查"的必然结果。
# 详见 docs/L7_ENDPOINT_CONSTRAINTS.md 顶部的循环论证声明。
CREATED_BY_AUTHOR_TYPES = RELATION_ENDPOINT_CONSTRAINTS["CREATED_BY"]["source_allowed"]
CREATED_BY_CREATABLE_TYPES = RELATION_ENDPOINT_CONSTRAINTS["CREATED_BY"]["target_allowed"]


def validate_constraints() -> list[str]:
    """表自身的完整性检查。返回问题列表（空 = 通过）。

    检查项都是会静默出错的那几种：
      · 15 类关系是否全覆盖 / 有没有多出来（本体改了但表没跟）
      · 集合里有没有写错字（拼错的类型名会让约束静默失效，永远判合规）
      · core 非空时是否真的不相交（相交则"反向命中"会被正向先吃掉，
        reversed 永远为 0 —— 看起来一切正常，实际这层什么都没查）
      · kind / reversal 是否自洽
      · L4 假设句模板表是否 15 类全覆盖、占位符是否合法（新增）
      · CREATED_BY 的模板方向是否仍是「作品 by 作者」（这条被改反过一次，见 §1.1c）
    """
    problems = []

    def _placeholders(tpl: str) -> list:
        out, i = [], 0
        while True:
            a = tpl.find("{", i)
            if a < 0:
                return out
            b = tpl.find("}", a)
            if b < 0:
                return out
            out.append(tpl[a:b + 1])
            i = b + 1

    for name in sorted(RELATION_TYPES):
        c = RELATION_ENDPOINT_CONSTRAINTS.get(name)
        if c is None:
            problems.append(f"{name}: 表中缺失")
            continue
        for field in ("source_core", "target_core", "source_allowed", "target_allowed"):
            bad = c[field] - ENTITY_TYPES
            if bad:
                problems.append(f"{name}.{field}: 非本体类型 {sorted(bad)}")
        if c["kind"] not in ("directed", "symmetric", "unconstrained"):
            problems.append(f"{name}.kind: 非法取值 {c['kind']}")
        if c["kind"] != "directed":
            if c["source_core"] or c["target_core"]:
                problems.append(f"{name}: 非 directed 关系不应有方向指纹")
            if c["reversal"] != "unjudgeable":
                problems.append(f"{name}: 非 directed 关系的 reversal 应为 unjudgeable")
        if c["source_core"] and c["target_core"]:
            overlap = c["source_core"] & c["target_core"]
            if overlap:
                problems.append(
                    f"{name}: 方向指纹两侧相交 {sorted(overlap)} —— "
                    f"reversed 判定会恒为 0")
            if c["reversal"] != "judgeable":
                problems.append(f"{name}: 有方向指纹却标了 unjudgeable")
        if bool(c["source_core"]) != bool(c["target_core"]):
            problems.append(f"{name}: 方向指纹只有一侧")
        # 方向指纹必须是允许集的子集，否则指纹命中会掉进 role_violation
        for core, allowed, side in ((c["source_core"], c["source_allowed"], "source"),
                                    (c["target_core"], c["target_allowed"], "target")):
            if core - allowed:
                problems.append(
                    f"{name}: {side} 方向指纹不在允许集内 {sorted(core - allowed)}")
    extra = set(RELATION_ENDPOINT_CONSTRAINTS) - RELATION_TYPES
    if extra:
        problems.append(f"表中有本体外的关系：{sorted(extra)}")

    # ── 方向守卫（外部断言，不是内部自洽性）──
    # 交换 source/target 之后所有内部检查依然通过，所以必须单独钉。
    for name, (want_src, want_tgt) in _DIRECTION_GUARD.items():
        c = RELATION_ENDPOINT_CONSTRAINTS.get(name)
        if c is None:
            problems.append(f"方向守卫引用了表中不存在的关系 {name}")
            continue
        if c["source_core"] != want_src:
            problems.append(
                f"{name}.source_core 与方向守卫不符 —— 期望 {sorted(want_src)}，"
                f"实际 {sorted(c['source_core'])}（方向很可能被写反了）")
        if c["target_core"] != want_tgt:
            problems.append(
                f"{name}.target_core 与方向守卫不符 —— 期望 {sorted(want_tgt)}，"
                f"实际 {sorted(c['target_core'])}（方向很可能被写反了）")

    # ── SYMMETRIC 与表的一致性（两条都能真正失败的检查）──
    # 原写法是 `SYMMETRIC_RELATIONS != {从表里按 template_symmetric 派生}`，
    # 而 SYMMETRIC_RELATIONS 当时就是从同一表达式派生的 ⇒ 恒等、永不报错。
    # 现在 SYMMETRIC_RELATIONS 是显式字面量，这两条才有意义。
    derived = {n for n, c in RELATION_ENDPOINT_CONSTRAINTS.items()
               if c["template_symmetric"]}
    if SYMMETRIC_RELATIONS != derived:
        problems.append(
            f"SYMMETRIC_RELATIONS 与表中 template_symmetric 派生集合不一致："
            f"字面量独有 {sorted(SYMMETRIC_RELATIONS - derived)}、"
            f"表独有 {sorted(derived - SYMMETRIC_RELATIONS)}")
    if SYMMETRIC_RELATIONS - RELATION_TYPES:
        problems.append(
            f"SYMMETRIC_RELATIONS 含本体外关系："
            f"{sorted(SYMMETRIC_RELATIONS - RELATION_TYPES)}")

    # ── kind=unconstrained 时 allowed 不参与判定，必须显式声明 ──
    # verdict() 对 unconstrained 提前返回，于是它的 source_allowed/target_allowed
    # 是**死字段**——看起来有约束、实际从不用。要么让它等于全集（诚实留痕），
    # 要么就该把 kind 改成别的。不检查的话读者会以为 MEASURES 受约束。
    for name, c in RELATION_ENDPOINT_CONSTRAINTS.items():
        if c["kind"] == "unconstrained":
            if (c["source_allowed"] != _ALL or c["target_allowed"] != _ALL):
                problems.append(
                    f"{name}: kind=unconstrained 但 allowed 不是全集 —— "
                    f"这两个字段在该 kind 下不参与判定，留着具体值会被误读为有效约束")

    # ── L4 假设句模板表自身的完整性 ──
    # 这两张表是 tools/l4_predicate_report.py 的输入，也是
    # tools/relation_verifier.py / tools/verify_direction.py 里 REL_TEMPLATE 的
    # 唯一来源。缺一类会让 argmax 静默少一个候选（预测分布整体偏移却不报错），
    # 所以覆盖性必须显式检查。
    for tbl_name, tbl in (("REL_HYPOTHESIS_TEMPLATE", REL_HYPOTHESIS_TEMPLATE),
                          ("REL_PHRASE_TEMPLATE", REL_PHRASE_TEMPLATE)):
        missing = RELATION_TYPES - set(tbl)
        if missing:
            problems.append(f"{tbl_name} 缺 {sorted(missing)}")
        surplus = set(tbl) - RELATION_TYPES
        if surplus:
            problems.append(f"{tbl_name} 含本体外关系 {sorted(surplus)}")
        for name in sorted(tbl):
            tpl = tbl[name]
            ph = _placeholders(tpl)
            if "{s}" not in ph or "{t}" not in ph:
                problems.append(f"{tbl_name}[{name}] 占位符不全: {tpl!r}")
            bad = [x for x in ph if x not in ("{s}", "{t}")]
            if bad:
                problems.append(f"{tbl_name}[{name}] 含非法占位符 {bad}")

    # 方向守卫：CREATED_BY 的 source 是创作者、target 是被创作物。
    # 写成 "{s} was created by {t}." 会把语义判反（实测通过率 39.6% → 26.0%），
    # 所以这里**逐字**锁死，不接受"看起来差不多"的改写。
    if REL_HYPOTHESIS_TEMPLATE.get("CREATED_BY") != "{t} was created by {s}.":
        problems.append('REL_HYPOTHESIS_TEMPLATE["CREATED_BY"] 必须是 '
                        '"{t} was created by {s}."（方向：Author→Work）')
    if REL_PHRASE_TEMPLATE.get("CREATED_BY") != "{s} created {t}":
        problems.append('REL_PHRASE_TEMPLATE["CREATED_BY"] 必须是 "{s} created {t}"'
                        "（方向：Author→Work）")

    return problems

# ── L5 判别器 ──────────────────────────────────────────────────────────
# 阈值由官方 11998 行校准集自搜得到（宏平均 F1 最优），刻意不照抄模型卡的 0.75
# ——RED^FM 论文附录 A 实际写的是 0.1，0.75 属 cRocoDiLe 的 NLI 过滤阶段。
L5_MODEL = "Babelscape/mdeberta-v3-base-triplet-critic-xnli"
L5_THRESHOLD = 0.67
L5_ROC_AUC = 0.9851
L5_CALIB_N = 4000

# ── L4 谓词选择评测：15 类关系 → NLI 假设句模板（唯一来源）───────────────
#
# 【为什么需要这张表】
#   现行 L4 只判「谓词是否落在 15 类内」，一条本该 USES 的边标成 PART_OF
#   **照样通过** —— 99.5% 因此接近自证（docs/L5_SIX_LAYER_REPORT.md §6.5 第 3 条
#   已如实声明「本项目 L4 比报告版窄，等价于本体依从率而非谓词匹配」）。
#   要判「选对没有」，就得把 15 类各自变成一个**可问 NLI 的假设句**，
#   用同一条 premise 与 15 个模板分别算 entail_prob，取最大者为预测谓词。
#
# 【方向约定：{s}=source，{t}=target】
#   与 RELATION_ENDPOINT_CONSTRAINTS[*]["semantics"] 逐类对齐，也与
#   tools/verify_direction.py 的 REL_TEMPLATE 完全一致（check_ontology_consistency.py
#   负责把两处相减，写两处必漂移，见 docs/ERROR_CASES.md 案例 12）。
#   `CREATED_BY` 必须是 "{t} was created by {s}."：T3.1 已把方向统一为
#   Author(source) → Work(target)，写成 "{s} was created by {t}" 会把语义判反
#   ——实测该类通过率 39.6% → 26.0%（docs/L5_SIX_LAYER_REPORT.md §1.1c）。
#
# 【与 relation-verify-all.json 的 hypothesis 字段的关系】
#   该字段就是本表按每条边的 (source, target) 代入的结果，实测
#   **43630/43630 逐字一致**（tools/l4_predicate_report.py 的 [核实] 一节复核）。
#   ⇒ L4 候选集里「标注谓词」那一项的分数**就是** L5 的 entail_prob，
#     可以直接复用、不必重算（省 1/15 算力）。这也意味着 L4 与 L5
#     **共享同一批分数、并不独立**，报告时必须声明（见该脚本的循环论证注释）。
REL_HYPOTHESIS_TEMPLATE = {
    "PART_OF": "{s} is part of {t}.",
    "USES": "{s} uses {t}.",
    "PRODUCES": "{s} produces {t}.",
    "MEASURES": "{s} measures {t}.",
    "EVALUATES": "{s} evaluates {t}.",
    "COMPARES_WITH": "{s} is compared with {t}.",
    "SUPPORTS": "{s} supports {t}.",
    "CONTRADICTS": "{s} contradicts {t}.",
    "CAUSES": "{s} causes {t}.",
    "ENABLES": "{s} enables {t}.",
    "APPLIES_TO": "{s} is applied to {t}.",
    "LOCATED_AT": "{s} is located at {t}.",
    "CREATED_BY": "{t} was created by {s}.",
    "CITES": "{s} cites {t}.",
    "RELATED_TO": "{s} is related to {t}.",
    # 2026-09-14 新增（本体扩展）。方向是 论文 → 发表场所。
    # 动词用 published in / appeared in —— 与 PART_OF（is part of）刻意区分开，
    # 否则 NLI 会把两类混为一谈。
    "PUBLISHED_IN": "{s} was published in {t}.",
}

# 名词短语形态（与官方校准集 en_triplet_entailment.tsv 同形：
# "CBS Corporation subsidiary The CW"）。**不是主口径**，只用于「假设句形态消融」：
# 0.9851 的 ROC-AUC 就是在名词短语 hypothesis 上标定的，而 L5 运行时用的是
# 上面的完整句模板 —— 两者形态不同，该 AUC 不可直接迁移
# （docs/L5_VALIDITY_PLAN.md §1.2 已实测记录这一错配）。
# 方向仍与主表一致：CREATED_BY 是「作者 created 作品」。
REL_PHRASE_TEMPLATE = {
    "PART_OF": "{s} part of {t}",
    "USES": "{s} uses {t}",
    "PRODUCES": "{s} produces {t}",
    "MEASURES": "{s} measures {t}",
    "EVALUATES": "{s} evaluates {t}",
    "COMPARES_WITH": "{s} compared with {t}",
    "SUPPORTS": "{s} supports {t}",
    "CONTRADICTS": "{s} contradicts {t}",
    "CAUSES": "{s} causes {t}",
    "ENABLES": "{s} enables {t}",
    "APPLIES_TO": "{s} applied to {t}",
    "LOCATED_AT": "{s} located at {t}",
    "CREATED_BY": "{s} created {t}",
    "CITES": "{s} cites {t}",
    "RELATED_TO": "{s} related to {t}",
    # 2026-09-14 本体扩展。与 PART_OF（`{s} part of {t}`）刻意区别开。
    "PUBLISHED_IN": "{s} published in {t}",
}


def summary() -> dict:
    """给自检脚本用的快照。"""
    return {
        "entity_types": sorted(ENTITY_TYPES),
        "relation_types": sorted(RELATION_TYPES),
        "derived_entity_labels": sorted(DERIVED_ENTITY_LABELS),
        "allowed_entity_labels": sorted(ALLOWED_ENTITY_LABELS),
        "created_by_author": sorted(CREATED_BY_AUTHOR_TYPES),
        "created_by_creatable": sorted(CREATED_BY_CREATABLE_TYPES),
        "l5_threshold": L5_THRESHOLD,
        "l5_model": L5_MODEL,
    }


if __name__ == "__main__":
    import json
    print(json.dumps(summary(), ensure_ascii=False, indent=2))
