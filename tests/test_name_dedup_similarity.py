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


# ── 2026-09-16（BUG-1）：首字母缩写**只作候选、不进自动合并** ───────────────
#
# 上面 `test_short_abbreviation_is_not_a_variant_of_unrelated_long_name` 里的
# `RDF`/`Reference Data File` 那条，与 docstring 里「首字母缩写只作候选」本来是
# 一致的，但代码里第 ② 步一直在自动合并 ⇒ 长期失败。
# 修掉自动合并后，**候选这个信号必须有出口**，否则等于把信息丢了。
# 下面两条盯的就是那个出口。


def test_initialism_candidates_are_offered_for_review():
    """候选函数要把「疑似 缩写↔全称」找出来 —— **这是信号，不是判定**。"""
    from kg_extractor import initialism_candidates
    names = ["GNN", "Graph Neural Networks",
             "RDF", "Reference Data File"]
    got = {(c['short'], c['long']) for c in initialism_candidates(names)}
    assert ('GNN', 'Graph Neural Networks') in got, (
        '真缩写没被列为候选 —— 信号丢了')
    assert ('RDF', 'Reference Data File') in got, (
        '碰巧首字母对齐的也要列出来（正因为判据分不清，才需要人看）')
    # 但**都不该被自动合并**
    from kg_extractor import _names_are_duplicates as dup
    assert dup("GNN", "Graph Neural Networks") is False
    assert dup("RDF", "Reference Data File") is False


def test_initialism_ambiguity_is_the_reason_we_do_not_auto_merge():
    """**这条测试记录的是"为什么不能自动合并"的实证。**

    真实语料里「一短多长」是常态：`'AD'` 同时对得上 `A. Doan` / `Action Dropout` /
    `Anomaly Detection` / `Appendix D` … 自动合并会成片吞掉别的实体，而**不可逆**。
    在全库上量过一次：2142 条候选里 **93.6% 落在歧义短名上**。

    这里用一个最小例子把这个性质钉住 —— 不需要全库，只要"一短多长"时
    候选函数**两个都给出来**（而不是替我们选一个）。
    """
    from kg_extractor import initialism_candidates
    names = ["AD", "A. Doan", "Action Dropout", "Anomaly Detection", "Appendix D"]
    longs = {c['long'] for c in initialism_candidates(names) if c['short'] == 'AD'}
    assert len(longs) >= 3, (
        f"'AD' 应当对上多个长名（这正是不能自动合并的原因），实际只有 {longs}")
    # 每个候选都带"需人工确认"的说明 —— 不能让人误以为它已经判定过了
    for c in initialism_candidates(names):
        assert '不可逆' in c['note'] or '人工' in c['note'], (
            f'候选缺少"需人工确认"的说明：{c}')
