"""本体白名单的回归测试（实体标签 + 关系类型）。

背景（2026-09-10 验收发现）：
  - `parse_relations` 从不校验关系类型，模型自由发挥的类型（`CONTAINS`、`fuses`、
    `NYT10`、`Trump`…）一路写进 Neo4j，实测 73 种类型里 58 种越界。
  - 存储层 `safe_label` 只校验标识符合法性，不校验本体成员，导致 `system`（小写）、
    `ORKG` 这类标签落库。
本文件把两道闸都钉住。
"""

import pytest

from kg_extractor import RELATION_TYPES, parse_combined, parse_relations
from graph_store import Neo4jStore


# ── 解析层 ────────────────────────────────────────────────

def test_out_of_ontology_relation_type_is_dropped():
    raw = ("TransE<|>WMT 2014<|>EVALUATES<|>valid\n"
           "TransE<|>WMT 2014<|>fuses<|>invalid\n"
           "TransE<|>WMT 2014<|>CONTAINS<|>invalid\n")
    relations = parse_relations(raw)
    assert len(relations) == 1
    assert relations[0]["type"] == "EVALUATES"


def test_relation_type_can_appear_first():
    raw = "EVALUATES<|>TransE<|>WMT 2014<|>desc"
    relations = parse_relations(raw)
    assert relations[0]["source"] == "TransE"
    assert relations[0]["target"] == "WMT 2014"
    assert relations[0]["type"] == "EVALUATES"


def test_all_ontology_relation_types_recognised():
    for t in RELATION_TYPES:
        relations = parse_relations(f"A<|>B<|>{t}<|>desc")
        assert len(relations) == 1, f"{t} 未被识别"
        assert relations[0]["type"] == t


def test_relation_with_missing_description_still_parses():
    relations = parse_relations("A<|>B<|>USES")
    assert len(relations) == 1
    assert relations[0]["description"] == ""


def test_relation_with_missing_endpoint_is_dropped():
    assert parse_relations("A<|><|>USES<|>desc") == []
    assert parse_relations("<|>B<|>USES<|>desc") == []


def test_combined_output_does_not_leak_entity_lines_into_relations():
    """实体行有四段，曾满足关系解析器 '>=4 段' 从而被误解成关系。"""
    raw = ("entity<|>TransE<|>Method<|>desc\n"
           "entity<|>WMT 2014<|>Dataset<|>desc\n"
           "relation<|>TransE<|>WMT 2014<|>EVALUATES<|>desc\n")
    entities, relations = parse_combined(raw)
    assert len(entities) == 2
    assert len(relations) == 1
    assert relations[0]["type"] == "EVALUATES"


# ── 存储层 ────────────────────────────────────────────────

class _StubStore(Neo4jStore):
    """只借用归一函数，不建立数据库连接。"""

    def __init__(self):  # noqa: D107 - 有意跳过父类连接逻辑
        pass


def test_store_falls_back_for_out_of_ontology_relation_type():
    # 直接验证白名单集合本身，避免复制实现细节
    assert "EVALUATES" in Neo4jStore.ALLOWED_RELATION_TYPES
    assert "CONTAINS" not in Neo4jStore.ALLOWED_RELATION_TYPES
    assert "fuses".upper() not in Neo4jStore.ALLOWED_RELATION_TYPES
    assert len(Neo4jStore.ALLOWED_RELATION_TYPES) == len(RELATION_TYPES)


def test_store_label_whitelist_matches_entity_ontology():
    from kg_extractor import ENTITY_TYPES
    assert Neo4jStore.ALLOWED_LABELS == set(ENTITY_TYPES)
    assert "system" not in Neo4jStore.ALLOWED_LABELS  # 大小写漂移由归一函数处理
    assert "ORKG" not in Neo4jStore.ALLOWED_LABELS
