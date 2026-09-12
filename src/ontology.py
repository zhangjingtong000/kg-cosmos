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

# ── L7 方向校验的端点类型约束 ──────────────────────────────────────────
# 只覆盖 CREATED_BY：方向约定是 T3.1 定下的 Author → Work。
# 其余 14 类的合法端点类型属**本体设计决策**，未定义——自动统计推导已证伪
# （16 类的"主导端点类型对"占比全 < 60%，不存在稳定主导）。
#
# 注意这里与 tools/apply_created_by_fix.py 的关系：那份是**做改动的规则**，
# 这里只做**校验**。两者目前取值相同，但校验层若只复用改动层的常量，
# 就变成"用补丁的假设验证补丁"（审计指出过这一点）。
# 把定义提到本模块、让改动层也 import，至少保证两边不会再各自漂移。
CREATED_BY_AUTHOR_TYPES = frozenset({"Person", "Organization"})
CREATED_BY_CREATABLE_TYPES = frozenset({
    "Document", "Method", "System", "Tool", "Artifact", "Dataset",
    "Component", "Concept", "Process", "Metric", "Material",
})

# ── L5 判别器 ──────────────────────────────────────────────────────────
# 阈值由官方 11998 行校准集自搜得到（宏平均 F1 最优），刻意不照抄模型卡的 0.75
# ——RED^FM 论文附录 A 实际写的是 0.1，0.75 属 cRocoDiLe 的 NLI 过滤阶段。
L5_MODEL = "Babelscape/mdeberta-v3-base-triplet-critic-xnli"
L5_THRESHOLD = 0.67
L5_ROC_AUC = 0.9851
L5_CALIB_N = 4000


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
