# -*- coding: utf-8 -*-
r"""N19：`entity_absorb` 的**计划与执行**（判据层已由 test_entity_absorb.py 覆盖）。

## 为什么再写一个文件

`test_entity_absorb.py` 钉的是 A5 那一条判据（`extra_is_person_form`）。
但 `find_absorptions` 一共**六道条件**，`apply_absorptions` 还负责"证据怎么并、
短名怎么标"——这些才是**真正会改图**的部分，之前一处没测：

    ① 短名实词序列是长名的**连续**子序列（词边界对齐）；
    ② 两节点证据有**共同页码**；
    ③ 短名实词数 ≥2（或名字里带逗号）；
    ④ 多出的部分不含数字、且不全是载体词（版本/载体 ⇒ 不是同一实体）；
    ⑤ 多出的部分是人名补齐成分（A5）；
    ⑥ 长度比 ≥ 0.34（短名至少占长名一半以上）。

## 一个反直觉点（已写进断言）

条件 ② 是"**两边都有页码时**才要求有交集"：两边都没页码 ⇒ 这条不生效，
照样吸收。所以"没证据"不等于"更安全"。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))

from entity_absorb import (  # noqa: E402
    apply_absorptions, extra_tokens, find_absorptions,
    is_contiguous_subsequence, tokens)


def _node(name, pages=(), **kw):
    d = {'name': name, 'id': name,
         'evidence': [{'page': p, 'chunk_idx': 1} for p in pages]}
    d.update(kw)
    return d


# ── 词级工具 ────────────────────────────────────────────────────────────
def test_tokens_drops_brackets_and_punctuation():
    assert tokens('Chinese-Alpaca-2 (7B)') == ['chinese', 'alpaca', '2']
    assert tokens('W.-Y. Ma') == ['w', 'y', 'ma']
    assert tokens('') == [] and tokens(None) == []


@pytest.mark.parametrize('short,long_,expect', [
    (['knowledge', 'graph'], ['creating', 'a', 'knowledge', 'graph', 'from', 'x'], True),
    (['a', 'b'], ['a', 'b'], False),                       # 等长 ⇒ 不是"被吸收"
    (['b', 'a'], ['a', 'b', 'c'], False),                  # 不连续
    ([], ['a'], False),
    # 词边界对齐是 `tokens()` 的职责：切分之后 `graph` 与 `graphneuralnetwork`
    # 是两个不同的词，所以字符串层面的"包含"在这里不成立。
    (tokens('tag'), tokens('tagger'), False),
    (tokens('graph'), tokens('graphneuralnetwork'), False),
    (tokens('knowledge graph'), tokens('Knowledge Graph from Survey Tables'), True),
])
def test_contiguous_subsequence_respects_word_boundaries(short, long_, expect):
    assert is_contiguous_subsequence(short, long_) is expect


def test_too_short_a_short_name_is_blocked_by_the_length_ratio():
    """条件 ⑥：`Graph` 不该吸进 `Graph Neural Network`（占比 5/19 < 0.34）。"""
    nodes = [_node('Graph', [1]), _node('Graph Neural Network', [1])]
    assert find_absorptions(nodes) == []


def test_extra_tokens_returns_what_the_long_name_adds():
    assert extra_tokens(['review'], ['review', 'papers']) == ['papers']
    assert extra_tokens(['knowledge', 'graph'],
                        ['creating', 'a', 'knowledge', 'graph', 'from']) == \
        ['creating', 'a', 'from']
    assert extra_tokens(['x'], ['y', 'z']) == []


# ── find_absorptions 的六道条件 ─────────────────────────────────────────
def test_person_completion_on_the_same_page_is_absorbed():
    nodes = [_node('Balažević', [1]), _node('Balažević Et Al.', [1])]
    plan = find_absorptions(nodes)
    assert [(s, l) for s, l, _ in plan] == [('Balažević', 'Balažević Et Al.')], plan
    assert '同页' in plan[0][2], f'理由里要能看出判据：{plan[0][2]}'


def test_no_shared_page_blocks_the_absorption():
    """条件 ②：同一页出现的才可能是同一个东西的碎片。"""
    nodes = [_node('Balažević', [1]), _node('Balažević Et Al.', [2])]
    assert find_absorptions(nodes) == []


def test_page_check_is_skipped_when_neither_side_has_pages():
    """⚠️ 反直觉但确实是当前语义：两边都**没有页码**时，条件 ② 不生效 ⇒ 仍会吸收。"""
    nodes = [_node('Balažević'), _node('Balažević Et Al.')]
    assert [s for s, _l, _r in find_absorptions(nodes)] == ['Balažević'], (
        '注意区别：一边有页码、另一边没有 ⇒ 也不拦（只有"都有且无交集"才拦）')


def test_one_side_with_pages_also_skips_the_check():
    nodes = [_node('Balažević', [1]), _node('Balažević Et Al.')]
    assert [s for s, _l, _r in find_absorptions(nodes)] == ['Balažević']


def test_single_word_short_name_is_not_absorbed_without_a_comma():
    """条件 ③：`China` 不该被 `ShenYang, China` 吸走（单词碎片曾经暴涨到 21%）。"""
    nodes = [_node('China', [3]), _node('ShenYang, China', [3])]
    assert find_absorptions(nodes) == []


@pytest.mark.parametrize('short,long_', [
    ('Deep Learning', 'Deep Learning-based Models'),        # 限定词改了含义
    ('Structured Data', 'Semi-Structured Data'),
    ('literature review', 'literature review papers'),      # 载体词（④）
    ('Chinese-Alpaca-2 (7B)', 'Chinese-Alpaca-2-16K (7B)'),  # 版本标记（④）
])
def test_qualifier_carrier_and_version_are_not_absorbed(short, long_):
    nodes = [_node(short, [5]), _node(long_, [5])]
    assert find_absorptions(nodes) == [], f'{short!r} → {long_!r} 不该被吸收'


def test_one_short_name_absorbs_at_most_one_long_name():
    """`break` 的语义：一个短名只吸到**第一个**匹配到的长名，不会一对多。"""
    nodes = [_node('Alpha Beta', [1]), _node('Alpha Beta Dr.', [1]),
             _node('Alpha Beta Prof.', [1])]
    plan = find_absorptions(nodes)
    assert len(plan) == 1, f'一个短名只允许出现一次：{plan}'
    assert plan[0][1] == 'Alpha Beta Dr.'


# ── apply_absorptions：只加字段、不删节点 ───────────────────────────────
def test_apply_merges_evidence_and_marks_the_short_name():
    short = _node('Balažević', [1])
    long_ = _node('Balažević Et Al.', [1, 2])
    nodes = [short, long_]
    out = apply_absorptions(nodes, [('Balažević', 'Balažević Et Al.', 'r')])
    assert out is nodes and len(nodes) == 2, (
        '吸收**不删节点**（图规模与既有指标口径必须不变）')
    assert short['absorbed_into'] == 'Balažević Et Al.'
    pages = sorted(e['page'] for e in long_['evidence'])
    assert pages == [1, 2], f'(page,chunk_idx) 已存在的证据不许重复堆叠：{long_["evidence"]}'


def test_apply_is_idempotent():
    short = _node('Balažević', [1])
    long_ = _node('Balažević Et Al.', [1])
    nodes = [short, long_]
    plan = [('Balažević', 'Balažević Et Al.', 'r')]
    apply_absorptions(nodes, plan)
    apply_absorptions(nodes, plan)
    assert len(long_['evidence']) == 1, f'{long_["evidence"]}'


def test_apply_skips_names_that_are_not_in_the_graph():
    nodes = [_node('Balažević', [1])]
    out = apply_absorptions(nodes, [('Ghost', 'Balažević Et Al.', 'r')])
    assert out is nodes and len(nodes) == 1


def test_apply_does_not_touch_edges():
    """本层只动节点上的 `absorbed_into` 与证据；边由 `graph_sanitize` 重指。"""
    nodes = [_node('Balažević', [1]), _node('Balažević Et Al.', [1])]
    edges = [{'source': 'Balažević', 'target': 'TransE', 'type': 'USES'}]
    snapshot = [dict(e) for e in edges]
    apply_absorptions(nodes, [('Balažević', 'Balažević Et Al.', 'r')])
    assert edges == snapshot
