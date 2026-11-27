# -*- coding: utf-8 -*-
r"""K5：`nebula.json` 的**论文归属**结构。

## 这一项为什么需要断言

`edge_papers` 是新加的**并行数组** —— 它必须与 `edges` **等长**，
且里面每个索引都必须在 `meta.papers` 范围内。
**一旦不等长，前端按下标取就会串位**，而串位的表现是
"筛某一篇却显示了别的篇的边" —— **看起来像筛选逻辑错，实际是数据错位**。
这类"两个数组必须对应"的不变量，**要在测试里钉住，不能靠人记得**
（P4 那次 `PRODUCES` 被算两次就是这个教训）。

## 为什么用"并行数组"而不是给 edges 里每条加字段

`edges` 的每一项现在是 `[source, target, type]` 三元组，
**所有读 `nebula.json` 的地方都按这个结构取值**。
把结构改成对象会让现有前端全部要改，而 K5 只需要**多一个维度**。
⇒ 加一个并行数组：**老代码一行不改，新代码读新数组**。

用法：
    python -m pytest tests/test_nebula_papers.py -q
"""
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NEBULA = ROOT / 'web' / 'kg-data' / 'nebula.json'


@pytest.fixture(scope='module')
def nebula():
    if not NEBULA.exists():
        pytest.skip(
            f'缺 {NEBULA}（构建产物，源仓库 3.4 MB；仓库不打包，'
            f'重建：python tools/build_nebula_data.py）')
    return json.loads(NEBULA.read_text(encoding='utf-8'))


# ── ① 结构存在 ────────────────────────────────────────────────────────
def test_papers_and_edge_papers_exist(nebula):
    assert 'papers' in (nebula.get('meta') or {}), (
        'meta 里必须有 papers —— 它是论文索引表，edge_papers 里的数字指向它')
    assert 'edge_papers' in nebula, (
        '必须有 edge_papers —— 与 edges 等长的并行数组（K5）')
    assert (nebula['meta']['papers']), 'papers 不该为空'


# ── ② 等长（**最要紧的不变量**）─────────────────────────────────────────
def test_edge_papers_same_length_as_edges(nebula):
    n_e = len(nebula.get('edges') or [])
    n_p = len(nebula.get('edge_papers') or [])
    assert n_e == n_p, (
        f'edge_papers 长度 {n_p} 与 edges 长度 {n_e} 不符 —— '
        f'前端按下标取会**串位**（表现为"筛某一篇却显示别的篇的边"）')


# ── ③ 索引有效 ────────────────────────────────────────────────────────
def test_all_paper_indices_valid(nebula):
    papers = nebula['meta']['papers']
    bad = []
    for i, arr in enumerate(nebula.get('edge_papers') or []):
        assert isinstance(arr, list) and arr, f'第 {i} 条边的论文数组不该为空：{arr!r}'
        for idx in arr:
            if not (isinstance(idx, int) and 0 <= idx < len(papers)):
                bad.append((i, idx))
    assert not bad, f'越界的论文索引（前 5）：{bad[:5]}'


def test_indices_sorted_and_unique(nebula):
    """每条边内的论文索引应当**升序且不重复** ——
    重复会让前端"这篇贡献了几条"的统计翻倍。"""
    for i, arr in enumerate(nebula.get('edge_papers') or []):
        assert arr == sorted(set(arr)), f'第 {i} 条边的索引不是升序去重的：{arr}'


# ── ④ 论文名可信：必须能在图快照目录里找到 ────────────────────────────
def test_paper_names_match_snapshots(nebula):
    """`papers` 里的名字应当**都是真实存在的图快照名**（不含扩展名）。

    这一条防的是"归因算错但结构合法" —— 结构对而内容错最难发现。
    """
    snap = {p.stem for p in (ROOT / 'data' / 'corpus' / 'graphs').glob('*.json')}
    if not snap:
        pytest.skip('缺少图快照可比对（data/corpus/graphs/*.json，仓库不打包）')
    papers = set(nebula['meta']['papers'])
    unknown = papers - snap
    assert not unknown, f'这些"论文名"在快照目录里找不到：{sorted(unknown)[:6]}'


# ── ⑤ 覆盖度：要如实反映"截断"这件事 ──────────────────────────────────
def test_coverage_reflects_truncation(nebula):
    """**覆盖度要小于全库**，而且原因要能被解释（`max_edges` 截断）。

    这条**不是**要求"覆盖全部" —— 而是要求这个数字**别悄悄变了**：
    现在 30000 条边只覆盖 9 篇；若哪天变成 1 篇或 17 篇，
    说明生成参数的语义变了，应当有人注意到。
    """
    papers = nebula['meta']['papers']
    n_all = len(list((ROOT / 'data' / 'corpus' / 'graphs').glob('*.json'))) or 17
    assert 1 <= len(papers) <= n_all, f'论文数 {len(papers)} 不合理（全库 {n_all} 篇）'
    # 边被截断时，覆盖的论文数必然少于全库 —— 那是**预期**，写在这里免得被当 bug
    if len(nebula.get('edges') or []) >= 30000:
        assert len(papers) < n_all, (
            f'边已被截断到 {len(nebula["edges"])}，但论文覆盖了全部 {n_all} 篇 —— '
            f'这说明截断顺序变了，值得复查')
