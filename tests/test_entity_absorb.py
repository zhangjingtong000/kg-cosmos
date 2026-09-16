# -*- coding: utf-8 -*-
r"""A5：1C 吸收判据收紧成「只对人名形态生效」。

## 这个文件测什么

`entity_absorb.find_absorptions()` 原来会把**任何**"短名是长名的连续子序列"判为同指，
在 23.7k 个实体上给出 **1404 对**。抽样看**几乎全错**：

    `Deep Learning`      → `Deep Learning-based Models`         （含义变了）
    `Structured Data`    → `Semi-Structured Data`               （**`Semi-` 改了含义**）
    `Question Answering` → `Knowledge Graph Question Answering` （**上义词，不是同一实体**）
    `Knowledge Graphs`   → `Traversing Knowledge Graphs in Vector Space`（论文标题片段）
    `Nobel Prize`        → `Nobel Prize in Physics`             （限定）

⇒ 根因：**"长名包含短名"在有名词短语上不足以判定同指** ——
多一个词通常意味着**更具体的另一个概念**。

⇒ A5 加第六道条件 `extra_is_person_form`：**只有"多出来的部分是人名补齐成分"才吸收**。
收紧后 **1404 → 1 对**（`Balažević` → `Balažević Et Al.`）。

## 两条必须钉住的边界

**① 单个字母不算人名首字母。** 实测混着：
    `Score Function` → `Score function f(h, r, t)`    extra=['f']  ← **数学符号**
    `Relation Matrix` → `Relation Matrix R`           extra=['r']  ← **数学符号**
**② 明知会漏掉 `Baeza-Yates` → `Ricardo A. Baeza-Yates`** 那一类，
    **且刻意不放宽** —— 放宽就得允许"首字母大写的单词"，
   而那会让 `Sparse Attention Vectors` 也通过。两者在字符串上无法区分。
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))

from entity_absorb import (  # noqa: E402
    extra_is_person_form, extra_is_version_or_carrier, find_absorptions, tokens)


def _extra_ok(short, long_):
    # ⚠️ **传原始字符串，不是 `tokens()` 的结果** —— 这个函数刻意在原始字符串上工作，
    # 因为 `tokens()` 会把**括号与点**都去掉，而正是它们区分"人名首字母"与"数学公式"：
    #     tokens('Score function f(h, r, t)') == ['score','function','f']  ⇒ extra ['f']
    #     tokens('W.-Y. Ma')                  == ['w','y','ma']            ⇒ extra ['w']
    # 两者在 tokens 层面完全一样。（第一版测试传了 tokens()，于是 14 个断言全 AttributeError。）
    return extra_is_person_form(short, long_)


# ── ① 该吸收的（额外部分确实是"人名补齐"）──────────────────────────────
@pytest.mark.parametrize('short,long_', [
    ('Balažević', 'Balažević Et Al.'),
    ('Y. Ma', 'W.-Y. Ma'),                    # 带点的连字符首字母
    ('Ma', 'Ma Dr.'),                          # 称谓
])
def test_person_补全_should_be_absorbable(short, long_):
    assert _extra_ok(short, long_) is True, (
        f'{short!r} → {long_!r} 的额外部分是人名成分，应当允许吸收')


def test_accent_ocr_variant_is_NOT_this_layer_s_job():
    """`C. Ré` → `C. R´e` 是**重音符号的 OCR 变体**，不是"人名补齐"。

    它被本判据拒绝**是应该的** —— `'c. ré'` 根本不在 `'c. r´e'` 里（`é` vs `´e`），
    所以判据连"短名是不是子串"都不成立。

    ⇒ 那属于**归一化层**（`_fold_flat` 的 NFKD 折叠就是干这个的），
    **不是 1C 吸收层的职责**。把它放进吸收测试会掩盖真正的问题归属。
    """
    assert _extra_ok('C. Ré', 'C. R´e') is False


# ── ② **单个字母不算**（它是数学符号，不是人名首字母）─────────────────
@pytest.mark.parametrize('short,long_', [
    ('Score Function', 'Score function f(h, r, t)'),
    ('Relation Matrix', 'Relation Matrix R'),
])
def test_single_letter_is_math_not_person(short, long_):
    assert _extra_ok(short, long_) is False, (
        f'{short!r} → {long_!r} 的额外部分是**单个字母**（数学符号），不该当人名首字母')


# ── ③ ★ 抽样证明"全是错的"那些，必须被拒 ──────────────────────────────
@pytest.mark.parametrize('short,long_', [
    ('Deep Learning', 'Deep Learning-based Models'),
    ('Structured Data', 'Semi-Structured Data'),            # Semi- 改了含义
    ('Question Answering', 'Knowledge Graph Question Answering'),  # 上义词
    ('KG Completion', 'KG Completion Benchmark'),
    ('Knowledge Graphs', 'Uncertain Temporal Knowledge Graphs'),
    ('Nobel Prize', 'Nobel Prize in Physics'),
    ('Table Extraction', 'Table Extraction Tools'),
    ('Projection Matrix', 'Relational Projection Matrix'),
])
def test_qualifier_or_hyponym_must_be_rejected(short, long_):
    """**这是 A5 的核心断言**：限定词/上义词/载体词都不该被吸收。

    它们都满足"短名是长名的连续子序列"，**但多出来的词改变了所指**。
    """
    assert _extra_ok(short, long_) is False, (
        f'{short!r} → {long_!r} 多出来的部分改变了含义，不该被吸收')


# ── ④ 全库影响面：从 1404 收紧到 23 ──────────────────────────────────
# ⚠️ **这条实测要 125 秒**（`find_absorptions` 在全库 23716 个节点上按实词数分桶比较）。
# 放进默认 pytest 会把总时长从 12 秒拉到 140 秒 ⇒ 用环境变量控制：
#     $env:KG_SLOW_TESTS='1'; python -m pytest tests -q
# **改动吸收判据时务必跑一次** —— 它是"判据有没有被放宽"的唯一直接度量。
@pytest.mark.skipif(not os.environ.get('KG_SLOW_TESTS'),
                    reason='慢测试（约 125 秒）；设 KG_SLOW_TESTS=1 才跑')
def test_full_corpus_absorptions_are_now_tiny():
    """**全库影响面必须极小** —— 这是"收紧"是否生效的直接度量。

    改之前是 1404 对；改之后实测 **23 对**，且抽样看全是人名
    （`Balažević`/`J. Wang`/`Y. Ma`/`D'Souza`/`Van Der Lei`）。
    断言用 `< 60`（留余量，因为语料还会变），
    **但如果它回到几百对，说明判据被放宽了**。
    """
    import json
    names = []
    for f in sorted((ROOT / 'data' / 'corpus' / 'graphs').glob('*.json')):
        try:
            d = json.loads(f.read_text(encoding='utf-8-sig'))
        except Exception:
            continue
        for n in (d.get('nodes') or []):
            nm = n.get('name') or n.get('id')
            if nm:
                names.append({'name': str(nm), 'id': str(nm),
                              'evidence': n.get('evidence') or []})
    if len(names) < 1000:
        pytest.skip('图快照不足，跳过全库影响面检查')
    plan = find_absorptions(names)
    assert len(plan) < 60, (
        f'全库吸收对数 {len(plan)} 偏多 —— 判据可能被放宽了（改前 1404、改后 23）。'
        f'样本：{plan[:8]}')
    # **而且它们应当几乎全是人名形态** —— 这正是 A5 的目的
    non_person = [(s, l) for s, l, _r in plan
                  if not extra_is_person_form(s, l)]
    assert len(non_person) <= 2, (
        f'吸收对里有 {len(non_person)} 对不是人名形态：{non_person[:5]}')


# ── ⑤ 原有的"版本/载体"条件不能被这次改动破坏 ─────────────────────────
def test_version_and_carrier_still_rejected():
    """回归：`extra_is_version_or_carrier` 是既有条件，A5 只是在它后面**再加一道**。"""
    assert extra_is_version_or_carrier(tokens('7B'), tokens('16K 7B')) is True
    assert extra_is_version_or_carrier(tokens('review'), tokens('review papers')) is True
    assert extra_is_version_or_carrier(tokens('TransE'), tokens('TransE model')) is False
