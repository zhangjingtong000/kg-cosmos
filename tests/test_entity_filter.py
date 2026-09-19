# -*- coding: utf-8 -*-
r"""N19：`entity_filter` —— 实体噪声过滤的三条规则与两条边界。

## 为什么值得测

这个模块的每条正则都**已经踩过一次坑**（见模块 docstring）：
`corr` 误杀 `Correction`、`eds?` 误杀 `ED-CNN`、`no\.?` 误杀 `No Skills`、
`2015 Conference On EMNLP` 被当成量词短语。
⇒ 规则很"脆"，改动一行正则就可能把**正经实体**杀掉，而症状是静默的
（图谱里少东西，不会有人报错）。

## 覆盖

  · 该丢的六类（参考文献作者 / 文献编号 / 引用尾词 / 纯数字 / 年份 / 量词短语）；
  · **必须留住**的假阳性对照组（上面那四个坑各一条）；
  · 严格档泛指词默认开的语义、以及可关；
  · 「证据来自参考文献段」这条**依赖上下文**的判据（只看前 60 字符）；
  · `filter_entities` 不修改入参、丢弃记录带理由、`summary` 汇总。

## ⚠️ 一处"现状记录"

`R_YEAR` 上方的注释写「不含 `1980s` 这类年代」，但正则 `^(19|20)\d{2}[a-z]?$`
里的 `[a-z]?` 让 `1980s` **照样命中**（同一支也是 `2024a` 想要的语义）。
见 `test_decade_1980s_is_currently_dropped_as_year`。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))

from entity_filter import (  # noqa: E402
    GENERIC_STRICT, explain, filter_entities, summary)


# ── ① 该丢的 ────────────────────────────────────────────────────────────
@pytest.mark.parametrize('name,reason_part', [
    ('Zander, P. O.', '参考文献作者'),
    ('Kelleher, J. D.', '参考文献作者'),
    ("O'Neil, S.", '参考文献作者'),
    ('arXiv:2306.10723', '文献编号'),
    ('doi:10.1000/1', '文献编号'),
    ('ISBN: 1234', '文献编号'),
    ('et al.', '引用尾词'),
    ('Vol. 3', '引用尾词'),
    ('eds. 12', '引用尾词'),
    ('2015', '纯数字'),
    ('2024a', '年份'),
    ('92 survey articles', '量词短语'),
    ('2 626 papers', '量词短语'),
    ('  ', '空名'),
])
def test_noise_is_dropped_with_the_right_reason(name, reason_part):
    got = explain(name)
    assert got and reason_part in got, f'{name!r} 应当因「{reason_part}」被丢，实际 {got!r}'


# ── ② 必须留住的（每条都对应模块 docstring 里记着的一次误杀）────────────
@pytest.mark.parametrize('name', [
    'ED-CNN',                      # 曾被 `eds?` 误杀
    'No Skills',                   # 曾被 `no\\.?` 误杀
    'Correction',                  # 曾被 arXiv 的 `corr` 前缀误杀
    '2015 Conference On EMNLP',    # 曾被当成量词短语（那是合法会议实体）
    'Literature',                  # 泛指词表刻意收得很窄
    'Systems',
    'TransE',
    'China, Beijing',
    '3.14',
])
def test_real_entities_survive(name):
    got = explain(name)
    assert got is None, f'{name!r} 被误杀（理由 {got!r}）—— 这正是历史上踩过的坑'


def test_quantity_phrase_requires_a_lowercase_noun():
    """`92 survey articles` 丢，`2015 Conference On EMNLP` **留** —— 差别只在大写。"""
    assert explain('92 survey articles') is not None
    assert explain('2015 Conference On EMNLP') is None


def test_decade_1980s_is_currently_dropped_as_year():
    """⚠️ **现状记录**：`R_YEAR` 的注释写「不含 `1980s` 这类年代」，
    但 `[a-z]?` 让 `1980s` 照样命中 —— 同一条支路也是 `2024a` 需要的语义。

    年代很可能是个正经概念（如「1980s 的方法」），被当噪声杀掉属于误伤。
    **若将来把 `[a-z]?` 收紧（例如只允许 a-d 这类版本后缀），这条会红。**
    """
    assert explain('1980s') == '年份', (
        '当前实现把 1980s 判成年份（与注释声明不符）—— 改了就更新这条')


# ── ③ 泛指词：默认开、可关 ───────────────────────────────────────────────
def test_generic_words_dropped_by_default():
    assert 'pdf' in GENERIC_STRICT and 'pdf documents' in GENERIC_STRICT
    assert explain('pdf documents') == '泛指名词（严格档）'


def test_generic_check_can_be_disabled():
    assert explain('pdf', strict_generic=False) is None, (
        '关掉严格档后泛指词必须留下（这一档留给人工，见模块 docstring）')


# ── ④ 上下文判据：证据来自参考文献段 ─────────────────────────────────────
def test_evidence_from_reference_section_drops_the_entity():
    assert explain('TransE', 'References\n[1] Vaswani et al.') == '证据来自参考文献段'
    assert explain('TransE', 'Bibliography of the field') == '证据来自参考文献段'


def test_body_mention_of_references_is_not_enough():
    """正文里提到 "the references" 不该误判 —— 只认**段首**标记。"""
    assert explain('TransE', 'In the body we discuss the references of this paper') is None
    assert explain('TransE', 'The references section lists 40 papers') is None


def test_reference_head_is_checked_in_the_first_60_chars_only():
    """标记必须在**前 60 字符**内 —— 避免长正文里偶然出现段首词。"""
    long_text = 'x' * 70 + ' references ' + 'y' * 10
    assert explain('TransE', long_text) is None


# ── ⑤ filter_entities / summary ─────────────────────────────────────────
def test_filter_entities_splits_and_does_not_mutate_input():
    nodes = [
        {'name': 'TransE', 'type': 'Method'},
        {'name': 'Zander, P. O.', 'type': 'Author'},
        {'id': 'pdf', 'type': 'Artifact'},                    # id 字段兜底
    ]
    snapshot = [dict(n) for n in nodes]
    kept, dropped = filter_entities(nodes)
    assert [n['name'] for n in kept] == ['TransE']
    assert [(n.get('name') or n.get('id')) for n in dropped] == ['Zander, P. O.', 'pdf']
    assert nodes == snapshot, 'filter_entities 不该修改入参'


def test_filter_entities_uses_id_when_name_is_absent():
    """数据面用 `id`、抽取产物用 `name` —— 第一版只认 name，全库 100% 被误判成空名。"""
    kept, dropped = filter_entities([{'id': 'TransE'}, {'id': 'arXiv:1111.2222'}])
    assert [n['id'] for n in kept] == ['TransE']
    assert dropped[0]['drop_reason'] == '文献编号（arXiv/DOI/ISBN）'


def test_dropped_records_keep_their_fields_and_reason():
    _kept, dropped = filter_entities([{'name': '2015', 'type': 'Year', 'evidence': []}])
    assert dropped[0]['type'] == 'Year', '丢弃记录要保留原字段，便于审计'
    assert dropped[0]['drop_reason'] == '纯数字'


def test_summary_counts_by_reason():
    nodes = [{'name': '2015'}, {'name': '2024a'}, {'name': 'et al.'}, {'name': 'TransE'}]
    _kept, dropped = filter_entities(nodes)
    s = summary(dropped)
    assert s['引用尾词'] == 1
    assert sum(s.values()) == 3
    assert summary([]) == {}
