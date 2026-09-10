"""名称去重相似度的回归测试。

背景（2026-09-10 发现）：子串规则无长度保护，`norm('UI')='ui'` 被判为
`norm('build graph.py')='buildgraphpy'` 的变体（bUIldgraphpy），导致缩写与
任意含其字母序列的长名字被错误合并。该函数同时用于抽取去重与金标聚类。
"""

from kg_extractor import _names_are_duplicates


def test_short_abbreviation_is_not_a_variant_of_unrelated_long_name():
    """缩写不得因为字母恰好嵌在长名字里而被判为同一实体。"""
    assert _names_are_duplicates("UI", "build graph.py") is False
    assert _names_are_duplicates("build graph.py", "UI") is False
    assert _names_are_duplicates("AI", "Semantic Scholar") is False  # semAntIcScholar? 兜底
    assert _names_are_duplicates("RDF", "Reference Data File") is False


def test_true_full_name_and_acronym_still_merge():
    """真正的全称/缩写关系必须继续合并（长名包含短名）。"""
    assert _names_are_duplicates("multi-head attention", "Multi-Head Attention") is True
    assert _names_are_duplicates("transformers", "Transformer") is True
    assert _names_are_duplicates("Open Research Knowledge Graph",
                                 "Open Research Knowledge Graph (ORKG)") is True


def test_length_ratio_guard_allows_genuine_containment():
    """长度接近的包含关系（如带后缀的版本名）仍应合并。"""
    assert _names_are_duplicates("BERT", "BERTLarge") is True  # 比例 0.5 且短名 4 -> 需接近
    assert _names_are_duplicates("tabula", "Tabula PDF") is True


def test_high_similarity_still_merges():
    assert _names_are_duplicates("knowledge graph", "knowledge graphs") is True
    assert _names_are_duplicates("OpenRefine", "Open Refine") is True


def test_unrelated_names_do_not_merge():
    assert _names_are_duplicates("Crossref", "Semantic Scholar") is False
    assert _names_are_duplicates("Tabula", "GROBID") is False
    assert _names_are_duplicates("", "anything") is False
    assert _names_are_duplicates("!!!", "...") is False
