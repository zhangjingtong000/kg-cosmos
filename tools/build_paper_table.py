# -*- coding: utf-8 -*-
r"""L17 · **结构化字段抽成表**（行 = 论文，列 = 字段，可导出）。

## TODO 的原话

> | **L17** | ⬜ **结构化字段抽成表** |
> **Consensus `Study Snapshot`**（Population / Sample size / Duration / Location / Methods）；
> **Elicit `Extract Data`**（自定义列 → 导出） |
> 我们有实体与关系，**但没有"按字段抽成一张表"** ★★

## 一个必须先说清的差异：字段**不能照抄**

Consensus 的 `Study Snapshot` 字段是
**Population / Sample size / Duration / Location / Methods** ——
那是**医学与社科综述**的字段（"多少人、随访多久、在哪个医院"）。
**本项目是 NLP/ML 领域**，照抄这五列会得到**一列全空**的表。

⇒ 所以这一页的字段是**本领域真正存在的那些**，而且**从图里真的能取到**：

| 列 | 取自哪种关系 | 它回答什么 |
|---|---|---|
| **用了什么方法** | `USES` | 这篇论文用了哪些方法/工具 |
| **在什么上做** | `APPLIES_TO` | 用在什么任务/对象/领域上 |
| **用什么指标** | `MEASURES` | 报告了哪些指标 |
| **谁做的** | `CREATED_BY` | 作者/机构 |
| **和谁比** | `COMPARES_WITH` | 对比对象 |
| **发表在哪** | `PUBLISHED_IN` | 会议/期刊（**A8 改写后的那些边**） |
| **提出了什么** | `PRODUCES` | 产出的方法/数据集 |

**⇒ 这不是"我们凑了几列"，而是"图谱里本来就有的关系，按论文摊平成一张表"。**

## ⚠️ 三个诚实处理

1. **每格都带出处**（页码）—— 这一页的价值一半在于**能点回去**，
   否则它与"我手打一张表"没有区别；
2. **空列照实留空**，并统计"有几篇填了这一列" ——
   **一列只填了 1 篇，就不该当结论用**（页面上直接标出来）；
3. **本表是"抽取结果的摊平"，不是"论文事实的权威"** ——
   抽取有漏有错（L3 端点完整性 96.17%），**页面上要写明这一条**。

用法：
    python tools/build_paper_table.py
    python tools/build_paper_table.py --top 40      # 每格最多留几条
"""
import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))

OUT = ROOT / 'web' / 'kg-data' / 'paper-table.json'
GRAPHS = ROOT / 'data' / 'corpus' / 'graphs'
INDEX = ROOT / 'web' / 'kg-data' / 'corpus-index.json'

# 列定义：`关系类型 → 列名`。
# **顺序有讲究**：越靠前的越可能填满（先给人看到有内容的列）。
COLUMNS = [
    ('USES', '用了什么方法'),
    ('APPLIES_TO', '在什么上做'),
    ('MEASURES', '用什么指标'),
    ('PRODUCES', '提出了什么'),
    ('COMPARES_WITH', '和谁比'),
    ('CREATED_BY', '谁做的'),
    ('PUBLISHED_IN', '发表在哪'),
]


def parse_args():
    ap = argparse.ArgumentParser(description='L17 结构化字段表')
    ap.add_argument('--top', type=int, default=40, help='每格最多留几条')
    ap.add_argument('--graphs', default='',
                    help='图目录（默认优先用 A8 改写后的那份，见下）')
    return ap.parse_args()


def pick_graphs_dir(explicit):
    """选图源：**优先用 A8 改写后的那份**。

    为什么默认用它：`PUBLISHED_IN`（"发表在哪"）**只存在于改写后的图里** ——
    A8 是**另写一份、不动原图**，所以原图里这一列恒为 0/17，
    而它恰恰是这张表最该有的一列（学术综述的标配字段）。

    **已知差异**：改写后的图与原图**只差 1019 条边的 `type`** ——
    这一点由 `tools/apply_venue_rewrite.py` 的**内建自检逐字节证明**
    （17 篇节点/边数一致 · `type` 之外零差异）。

    ⇒ 页面上会写明**用的是哪一份**（这是诚实，也是"两个口径"的一部分）。
    """
    if explicit:
        return Path(explicit), 'explicit'
    rewritten = ROOT / 'data' / 'quality' / 'graphs-published-in'
    if rewritten.exists() and any(rewritten.glob('*.json')):
        return rewritten, 'A8 改写后（另写一份；与原图只差 1019 条边的 type）'
    return GRAPHS, '原图（改写后的那份不存在）'


def main():
    a = parse_args()
    t0 = time.time()
    gdir, gnote = pick_graphs_dir(a.graphs)
    print('=' * 92)
    print('L17 · 结构化字段抽成表（行 = 论文，列 = 关系类型）')
    print('=' * 92)
    print(f'  图源：{gdir.relative_to(ROOT) if str(gdir).startswith(str(ROOT)) else gdir}')
    print(f'        （{gnote}）')

    meta = {}
    if INDEX.exists():
        idx = json.loads(INDEX.read_text(encoding='utf-8'))
        meta = {p['id']: p for p in (idx.get('papers') or [])}

    relset = {c[0] for c in COLUMNS}
    papers = []
    col_filled = defaultdict(int)          # 每列被几篇填过
    for f in sorted(gdir.glob('*.json')):
        pid = f.stem
        try:
            d = json.loads(f.read_text(encoding='utf-8'))
        except Exception as e:
            print(f'  ⚠️ {pid} 读失败：{type(e).__name__}')
            continue
        cells = defaultdict(list)
        for e in (d.get('edges') or []):
            t = str(e.get('type') or '')
            if t not in relset:
                continue
            ev = (e.get('evidence') or [{}])
            page = next((x.get('page') for x in ev if x.get('page')), None)
            cells[t].append({
                's': str(e.get('source'))[:70],
                't': str(e.get('target'))[:70],
                'page': page,
            })
        # 每格**去重 + 截断**（同一对可能重复；太多则只留前 N）
        out_cells = {}
        for rel, _label in COLUMNS:
            items = cells.get(rel) or []
            seen, uniq = set(), []
            for it in items:
                k = (it['s'], it['t'])
                if k in seen:
                    continue
                seen.add(k)
                uniq.append(it)
            if uniq:
                col_filled[rel] += 1
            out_cells[rel] = {'n': len(uniq), 'items': uniq[:a.top]}
        m = meta.get(pid) or {}
        papers.append({
            'id': pid,
            'title': (m.get('title') or '')[:150],
            'year': m.get('year'),
            'cells': out_cells,
        })
        print(f'    {pid:<16} ' + ' · '.join(
            f'{lab}{out_cells[rel]["n"]}' for rel, lab in COLUMNS))
    n = len(papers) or 1
    print()
    print('  ★ 每列被几篇填过（**只填了 1–2 篇的列，不该当结论用**）')
    cols = []
    for rel, label in COLUMNS:
        c = col_filled.get(rel, 0)
        cols.append({'key': rel, 'label': label, 'papers': c,
                     'coverage': round(c / n, 4)})
        warn = '  ⚠️ 覆盖太低，只能当线索' if c <= 2 else ''
        print(f'      {label:<12} {c:>2} / {n} 篇（{c / n:.0%}）{warn}')

    payload = {
        'generated': time.strftime('%Y-%m-%d %H:%M:%S'),
        'purpose': 'L17：把图谱里的关系按论文摊平成一张可导出的表',
        'design': {
            'fields_are_not_copied':
                'Consensus 的 `Study Snapshot` 字段是 **Population / Sample size / '
                'Duration / Location / Methods** —— 那是**医学与社科**的字段。'
                '本项目是 NLP/ML，照抄会得到**一列全空**的表。'
                '⇒ 这里每一列都是**本领域真实存在、且从图里真的能取到**的关系类型。',
            'not_a_summary':
                '这一页是**抽取结果的摊平**，不是"论文事实的权威"。'
                '抽取有漏有错（L3 端点完整性 96.17%），**页面上写明了这一条**。',
            'every_cell_has_source':
                '每格都带页码 —— 这一页的价值一半在于**能点回去**，'
                '否则它与"我手打一张表"没有区别。',
            'empty_columns_stay_empty':
                '空列照实留空，并统计"有几篇填了这一列" ⇒ '
                '**一列只填了 1–2 篇时，页面上直接标出"只能当线索"**。',
        },
        'columns': cols,
        'papers': papers,
        'coverage_note': f'共 {len(papers)} 篇；覆盖率见 columns。',
        'elapsed_s': round(time.time() - t0, 1),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(',', ':')),
                   encoding='utf-8')
    print()
    print(f'  已写出 {OUT.relative_to(ROOT)}  '
          f'({OUT.stat().st_size / 1024:.0f} KB · {time.time() - t0:.1f}s)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
