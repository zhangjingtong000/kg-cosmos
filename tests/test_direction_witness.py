# -*- coding: utf-8 -*-
r"""N19：`direction_witness` —— 方向判据（纯规则、零成本、可复现）。

## 为什么值得测

六层评测里**没有一层读"原文里谁是施事"**，这个模块是补那一层的最强通道：
从边自带的 `description` 里读谓词语义，和边声明的 source/target 比对。
它被 `graph_sanitize` 的 `KG_SANITIZE=3` 用来**翻转**判为 `inverted` 的边 ——
**判错的代价是把对的边改反**，而且是写进图里的。

模块 docstring 记着三处"判定次序修正"（评审实测自洽率 84.17% → 100%），
本文件把这三处**逐条钉成断言**，改动次序就会红。

## ✅ 一处真 bug 的修复记录（2026-09-19）

第 ① 处修正点名的例子 `X is listed as an author of Y` **曾长期仍被判 `inverted`**：
`COPULA_AGENT` 的正则 `(?:listed as|the|an?)?` 只能吃掉**一个**可选片段，
吃下 `listed as` 之后就匹配不到后面的 `an author` ⇒ 落到被动分支 ⇒ **判反**
（`KG_SANITIZE=3` 会把方向正确的边翻转，数据损坏级）。
现拆成两段可同时命中的可选组 `(?:listed\s+as\s+)?(?:the|an?)?\s*`，
原例与 `a co-author` / `the author` 一并判 `consistent`；
本文件原先记录旧行为的断言已同步翻转为"必须判对"（改回旧写法即报红）。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))

import direction_witness as DW  # noqa: E402


# ── ① 判定表（desc, source, target, 期望 verdict）──────────────────────
CONSISTENT = [
    ('TransE proposes Knowledge Graph embedding', 'TransE', 'Knowledge Graph'),
    ('X uses Y', 'X', 'Y'),
    ('X is a part of Y', 'X', 'Y'),                 # 系表归属：仍是 S→T
    ('X is an author of Y', 'X', 'Y'),
    ('X is the author of Y', 'X', 'Y'),
    ('X is a co-author of Y', 'X', 'Y'),
    ('X is listed as author of Y', 'X', 'Y'),       # 无冠词形态
    # ⚠️ Bug ① 的原例：带冠词的主动系表曾被误判为被动而判反（2026-09-19 修）
    ('X is listed as an author of Y', 'X', 'Y'),
    ('X is listed as a co-author of Y', 'X', 'Y'),
    ('X is listed as the author of Y', 'X', 'Y'),
    # ⚠️ Bug ① 的同源形态：`one of the` 也曾落到被动分支判反（2026-09-19 一并修）
    ('X is listed as one of the authors of Y', 'X', 'Y'),
    ('X is listed as one of the co-authors of Y', 'X', 'Y'),
    ('X is one of the authors of Y', 'X', 'Y'),
    ('X is one of the co-authors of Y', 'X', 'Y'),
    ('X is one of the writers of Y', 'X', 'Y'),     # 角色词表内其它词也要吃
    ('X authored Y', 'X', 'Y'),                     # 主动形态（不带 by ⇒ 不是被动）
    ('Y was created by X', 'X', 'Y'),               # 被动式，但文本里 T 在前 ⇒ 仍判对
    ('RotatE was authored by TransE', 'TransE', 'RotatE'),   # 端点顺序决定结论
]

INVERTED = [
    ('X was authored by Y', 'X', 'Y'),
    ('X authored by Y', 'X', 'Y'),                  # 无助动词的被动（第 ② 处修正）
    ('X written by Y', 'X', 'Y'),
    ('X used by Y', 'X', 'Y'),
    ('X was created by Y', 'X', 'Y'),               # 真被动反例：不能被 Bug ① 的修复弄坏
    ('X was carefully created by Y', 'X', 'Y'),     # 助动词后最多插 3 个词
    ('X has been created by Y', 'X', 'Y'),
    ('X can be used by Y', 'X', 'Y'),
    ('X proposes Y', 'Y', 'X'),                     # 边声明反了
]

UNKNOWN = [
    ('A and B are related', 'A', 'B'),              # 连接词开头 ⇒ 不猜
    ('A or B', 'A', 'B'),
    ('A then B', 'A', 'B'),                         # 没有谓词
    ('A', 'A', 'B'),                                # 只有一端出现
    ('nothing to see here', 'A', 'B'),
    ('A B', 'A', 'B'),                              # 中间是空的
]


@pytest.mark.parametrize('desc,s,t', CONSISTENT)
def test_consistent(desc, s, t):
    assert DW.desc_verdict(desc, s, t) == 'consistent', (
        f'{desc!r} 的施事是 source ⇒ 应当 consistent')


@pytest.mark.parametrize('desc,s,t', INVERTED)
def test_inverted(desc, s, t):
    assert DW.desc_verdict(desc, s, t) == 'inverted', (
        f'{desc!r} 的施事是 target ⇒ 应当 inverted')


@pytest.mark.parametrize('desc,s,t', UNKNOWN)
def test_unknown_never_guesses(desc, s, t):
    assert DW.desc_verdict(desc, s, t) == 'unknown', (
        f'{desc!r} 判不出来 ⇒ 必须 unknown（宁可不判，不许猜）')


# ── ② 三处"次序修正"逐条钉住 ────────────────────────────────────────────
def test_copula_agent_without_article_is_consistent():
    """① 主动系表优先于被动标记（无冠词形态）。"""
    assert DW.COPULA_AGENT.match('is listed as author of') is not None
    assert DW.desc_verdict('X is listed as author of Y', 'X', 'Y') == 'consistent'


def test_copula_agent_with_article_is_consistent():
    """① 主动系表优先于被动标记（**带冠词的原例**，2026-09-19 修）。

    旧写法 `(?:listed as|the|an?)?` 只能吃**一个**可选片段：吃下 `listed as`
    之后就要求紧跟 `author`，而原文是 `an author` ⇒ 主动系表不命中
    ⇒ 落到 `PASSIVE_WITH_AUX`（`is ... listed`）⇒ `inverted`
    ⇒ `KG_SANITIZE=3` 会把**方向正确**的边翻转（数据损坏级）。

    现写法把 `listed as` 与冠词拆成两段独立的可选组（可同时命中）。
    这条断言就是**负向哨兵**：把正则改回旧样子，它必须报红。
    """
    assert DW.split_desc('X is listed as an author of Y', 'X', 'Y')[1].strip() == \
        'is listed as an author of'
    assert DW.COPULA_AGENT.match('is listed as an author of') is not None, (
        '带冠词的主动系表必须命中 —— 命不中就会落到被动分支判反')
    assert DW.desc_verdict('X is listed as an author of Y', 'X', 'Y') == 'consistent'
    assert DW.desc_verdict('X is listed as a co-author of Y', 'X', 'Y') == 'consistent'
    assert DW.desc_verdict('X is listed as the author of Y', 'X', 'Y') == 'consistent'


def test_bare_passive_is_recognized():
    """② 无助动词的被动必须单独识别（`authored by` / `written by`）。"""
    assert DW.PASSIVE_BARE.match('authored by') is not None
    assert DW.PASSIVE_BARE.match('written by') is not None
    assert DW.desc_verdict('X authored by Y', 'X', 'Y') == 'inverted'


def test_bare_passive_wins_over_the_agent_verb_list():
    """③ 的实质是**次序**，不只是词表。

    模块 docstring 说「`authored by` / `written by` 从主动表移出」——
    实际 `authored by` **仍在** `AGENT_VERB` 里（`written by` 确实不在），
    且 2026-09-19 起 `authored` 的主动词形也在（覆盖 `X authored Y`），
    但 `PASSIVE_BARE` 排在其前，所以 `authored by` 的结果仍是被动。
    这里把"靠次序兜住"这件事钉住：**谁把 PASSIVE_BARE 挪到后面，方向就会判反。**
    """
    assert DW.AGENT_VERB.match('authored by') is not None, (
        '现状：`authored by` 仍在主动表里（注释说不完全准确）—— 改了请更新本测试')
    assert DW.AGENT_VERB.match('written by') is None
    assert DW.desc_verdict('X authored by Y', 'X', 'Y') == 'inverted'


# ── ③ split_desc / witness 的返回约定 ──────────────────────────────────
def test_split_desc_reports_endpoint_order():
    assert DW.split_desc('X proposes Y', 'X', 'Y')[0] == 'S_T'
    assert DW.split_desc('Y proposes X', 'X', 'Y')[0] == 'T_S'


def test_split_desc_falls_back_to_normalized_text():
    """大小写/连字符差异下先按原样找不到，必须回落到归一化查找。"""
    order, mid = DW.split_desc('transe proposes Knowledge-Graph', 'TransE', 'Knowledge Graph')
    assert order == 'S_T'
    assert 'proposes' in mid, f'归一化后的中间片段：{mid!r}'


def test_split_desc_returns_none_when_an_endpoint_is_missing():
    assert DW.split_desc('A only', 'A', 'B') == (None, '')
    assert DW.split_desc('', 'A', 'B') == (None, '')


def test_witness_is_none_for_unknown():
    assert DW.witness('A and B are related', 'A', 'B') is None


def test_witness_carries_reviewable_evidence():
    w = DW.witness('TransE was authored by RotatE', 'TransE', 'RotatE')
    assert w['verdict'] == 'inverted'
    assert w['endpoint_order_in_text'] == 'S_T'
    assert 'authored by' in w['witness_text'], f'见证文本要能人工复核：{w}'
    assert w['source'] == 'description'
    assert w['independent_of_T3_patch'] is True, (
        '这条通道的价值就在于它没被 T3.1 补丁改过（否则是循环论证）')


def test_verdict_vocabulary_is_exactly_three_values():
    """取值约定：`consistent` / `inverted` / `unknown` —— **没有 `reversed`**。

    `graph_sanitize` 按这三个值分流；第一版按 `reversed` 写，永远不命中、
    又落不进任何分支 ⇒ 静默丢失（见 test_graph_sanitize.py 的注释）。
    """
    got = {DW.desc_verdict(d, s, t) for d, s, t in
           CONSISTENT + INVERTED + UNKNOWN}
    assert got == {'consistent', 'inverted', 'unknown'}, got


def test_mid_is_truncated_before_matching():
    """`mid` 先被截到 90 字符再匹配 —— 超长描述不会带来意外的谓词命中。"""
    long_mid = 'x' * 200
    assert DW.desc_verdict('A' + long_mid + 'B', 'A', 'B') == 'unknown'
