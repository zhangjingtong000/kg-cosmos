"""关系端点规范化的回归测试。

背景（2026-09-11 发现）：`deduplicate_entities` 在跨 chunk 合并时把实体名改成
"最长的变体"，但关系端点不会同步改写。实测第一篇云端输出 779 条关系中
**335 条（43%）的端点在实体集合里找不到** —— 写 Neo4j 时 `MATCH (a {name})`
匹配不到，这些边被静默丢弃，是真实的关系损失。

`canonicalize_relation_endpoints` 在去重之后把端点重写到规范名，挽回这些边。
本文件钉住该行为，同时确认它不会引入错误的合并。
"""

from kg_extractor import canonicalize_relation_endpoints, deduplicate_entities


def build(entities, edges):
    nodes = deduplicate_entities([dict(e) for e in entities], "cloud")
    canon = canonicalize_relation_endpoints([dict(e) for e in edges], nodes)
    known = {n["name"].strip().casefold() for n in nodes}
    return nodes, canon, {n["name"] for n in nodes}, known


def test_case_variant_endpoint_is_rewritten():
    """大小写变体被去重合并后，引用变体的边必须被挽救。"""
    entities = [
        {"name": "Open Research Knowledge Graph", "type": "System", "description": "a"},
        {"name": "Open research knowledge graph", "type": "System", "description": "b"},
        {"name": "Tabula", "type": "Tool", "description": "c"},
    ]
    edges = [{"source": "Open research knowledge graph", "target": "Tabula",
              "type": "USES", "description": "x"}]
    nodes, canon, names, known = build(entities, edges)

    assert "Open Research Knowledge Graph" in names
    assert canon[0]["source"] == "Open Research Knowledge Graph"
    assert canon[0]["source"].casefold() in known


def test_suffix_variant_endpoint_is_rewritten():
    """带后缀的变体（Tabula PDF → Tabula）同样要重写。"""
    entities = [
        {"name": "Tabula", "type": "Tool", "description": "a"},
        {"name": "Tabula PDF", "type": "Tool", "description": "b"},
        {"name": "GROBID", "type": "Tool", "description": "c"},
    ]
    edges = [{"source": "Tabula PDF", "target": "GROBID", "type": "USES", "description": "x"}]
    nodes, canon, names, known = build(entities, edges)
    assert canon[0]["source"].casefold() in known
    assert canon[0]["target"] == "GROBID"


def test_short_abbreviation_is_not_merged_into_endpoint():
    """ORKG 这类缩写不得被并进全称（长度保护），端点保持原样。"""
    entities = [
        {"name": "Open Research Knowledge Graph", "type": "System", "description": "a"},
        {"name": "ORKG", "type": "System", "description": "b"},
        {"name": "GROBID", "type": "Tool", "description": "c"},
    ]
    edges = [{"source": "ORKG", "target": "GROBID", "type": "USES", "description": "x"}]
    nodes, canon, names, known = build(entities, edges)
    assert "ORKG" in names
    assert canon[0]["source"] == "ORKG"


def test_unresolvable_endpoint_is_left_untouched():
    """匹配不到的端点保留原名，交由调用方的端点过滤决定去留。"""
    entities = [{"name": "Tabula", "type": "Tool", "description": "a"}]
    edges = [{"source": "Nonexistent Thing", "target": "Tabula",
              "type": "USES", "description": "x"}]
    nodes, canon, names, known = build(entities, edges)
    assert canon[0]["source"] == "Nonexistent Thing"
    assert canon[0]["source"].casefold() not in known


def test_original_relation_objects_are_not_mutated():
    """规范化必须返回新对象，不能就地修改输入（调用方可能还要用原始数据）。"""
    entities = [
        {"name": "Tabula", "type": "Tool", "description": "a"},
        {"name": "Tabula PDF", "type": "Tool", "description": "b"},
    ]
    edges = [{"source": "Tabula PDF", "target": "Tabula", "type": "USES", "description": "x"}]
    _nodes, canon, _names, _known = build(entities, edges)
    assert edges[0]["source"] == "Tabula PDF"          # 输入未被改动
    assert canon[0] is not edges[0]                    # 返回的是副本


def test_multiword_and_hyphen_normalisation():
    """归一化应忽略连字符与空格差异。"""
    entities = [
        {"name": "Human-in-the-Loop Methodology", "type": "Method", "description": "a"},
        {"name": "Tabula", "type": "Tool", "description": "b"},
    ]
    edges = [{"source": "Human in the Loop Methodology", "target": "Tabula",
              "type": "USES", "description": "x"}]
    nodes, canon, names, known = build(entities, edges)
    assert canon[0]["source"].casefold() in known
