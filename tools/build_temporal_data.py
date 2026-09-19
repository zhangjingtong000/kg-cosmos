# -*- coding: utf-8 -*-
r"""F1 · 时间演化数据（**按论文年份逐年累积建图**）。

## 时间信息从哪来

图快照本身**没有时间字段**（`data/corpus/graphs/*.json` 里既没有 `year` 也没有 `date`）。
时间在 `web/kg-data/corpus-index.json` 的 `year` 里 ——
而它**与 arXiv id 的前两位一致**（`2202.…` ⇒ 2022）。实测两者对得上：
```
已入库 **20 篇**（2026-09-19 读数，见 `web/kg-data/temporal.json` → `totals.papers`
与 `frames`）：2017:1 · 2020:2 · 2021:1 · 2022:2 · 2023:2 · 2024:5 · 2025:6 · 2026:1
（原写「已入库 17 篇：…2025:4」⇒ 7 帧 —— 那是中文图进图之前的读数。）
⚠️ 同期 `data/corpus/graphs/` 已有 **21** 个快照、`corpus-index.json.stats.in_graph=21`，
本产物**少一篇**（`jsjkx2025-kg-kbqa`：图 17:17 落盘、索引 21:05 更新，
而本 JSON 生成于 19:40 之前）⇒ **重跑本脚本即可对齐**，届时 `totals.papers` 与
`frames` 的逐年分布都会变，注释以重跑后的产物为准。
```
⇒ **8 个年份**，可以做成 **8 帧**。

## ⚠️ 一个刻意的取舍：**不算社区**

逐年重跑 Louvain 是可行的，**但社区编号会逐年漂** ——
"2017 年的社区 3"与"2020 年的社区 3"**不是同一个东西**，
把它们画成同一种颜色会让人以为"这个社区一直在"，**而那是假的**。

⇒ 所以本数据**只出"节点 / 边 / 新增"三样**，**不碰社区**。
要跨年比较社区，需要**社区对齐**（另一件事），不能靠编号。

## 什么是"新增"

**当年首次出现的实体 / 关系**（相对**之前所有年份的并集**）。
那才是"时间演化"真正的看点 —— 图一年年变大是显然的，
**"什么在这个时候出现"**才是信息。

用法：
    python tools/build_temporal_data.py
"""
import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))

OUT = ROOT / 'web' / 'kg-data' / 'temporal.json'
INDEX = ROOT / 'web' / 'kg-data' / 'corpus-index.json'
GRAPHS = ROOT / 'data' / 'corpus' / 'graphs'


def parse_args():
    ap = argparse.ArgumentParser(description='F1 时间演化数据')
    ap.add_argument('--max-new', type=int, default=60, help='每帧最多列多少个新增实体')
    return ap.parse_args()


def year_of(paper_id):
    """从 arXiv id 推年份（与 `corpus-index.json` 的 `year` 一致，已实测对得上）。"""
    s = str(paper_id or '')
    if len(s) >= 2 and s[:2].isdigit():
        n = int(s[:2])
        if 0 <= n <= 99:
            # arXiv 的 YYMM：07 之后是 20xx，之前是 19xx（本项目语料全在 20xx）
            return 2000 + n if n < 90 else 1900 + n
    return None


def main():
    a = parse_args()
    t0 = time.time()
    print('=' * 92)
    print('F1 · 时间演化数据（按论文年份逐年累积）')
    print('=' * 92)

    idx = json.loads(INDEX.read_text(encoding='utf-8'))
    meta = {p['id']: p for p in (idx.get('papers') or [])}

    # ── 读每篇快照，按年份分组 ──
    per_year = {}
    for f in sorted(GRAPHS.glob('*.json')):
        pid = f.stem
        y = (meta.get(pid) or {}).get('year') or year_of(pid)
        if not y:
            print(f'  ⚠️ {pid} 推不出年份，跳过')
            continue
        try:
            d = json.loads(f.read_text(encoding='utf-8-sig'))
        except Exception as e:
            print(f'  ⚠️ {pid} 读失败：{type(e).__name__}')
            continue
        per_year.setdefault(int(y), []).append({
            'id': pid,
            'title': (meta.get(pid) or {}).get('title') or '',
            'nodes': [str(n.get('name') or n.get('id'))
                      for n in (d.get('nodes') or []) if (n.get('name') or n.get('id'))],
            'edges': [(str(e.get('source')), str(e.get('target')), str(e.get('type') or '?'))
                      for e in (d.get('edges') or [])],
        })

    years = sorted(per_year)
    print(f'  年份 {years}')
    for y in years:
        ps = per_year[y]
        print(f'    {y}：{len(ps)} 篇 · '
              f'{sum(len(p["nodes"]) for p in ps)} 节点（含跨篇重复）· '
              f'{sum(len(p["edges"]) for p in ps)} 边')

    # ── 逐年累积，算出"新增" ──
    seen_nodes, seen_edges = set(), set()
    frames = []
    for y in years:
        ps = per_year[y]
        year_nodes = {n for p in ps for n in p['nodes']}
        year_edges = {e for p in ps for e in p['edges']}
        new_nodes = sorted(year_nodes - seen_nodes)
        new_edges = year_edges - seen_edges
        seen_nodes |= year_nodes
        seen_edges |= year_edges
        # 新增边的**类型**分布 —— 比列一堆边名更有信息
        new_types = Counter(t for _s, _t, t in new_edges)
        # 新增实体里**度数最高**的几个（更可能是有意义的），用累积后的度数
        deg = Counter()
        for s, t, _ty in seen_edges:
            deg[s] += 1
            deg[t] += 1
        top_new = sorted(new_nodes, key=lambda n: -deg.get(n, 0))[:12]
        frames.append({
            'year': y,
            'papers': [{'id': p['id'], 'title': p['title'][:90]} for p in ps],
            'n_papers': len(ps),
            'n_nodes': len(seen_nodes),      # **累积后的规模**
            'n_edges': len(seen_edges),
            'n_new_nodes': len(new_nodes),
            'n_new_edges': len(new_edges),
            'new_types': dict(new_types.most_common(8)),
            'top_new_nodes': top_new,
            'new_nodes_sample': new_nodes[:a.max_new],
        })
        print(f'    {y}：累积 {len(seen_nodes)} 节点 / {len(seen_edges)} 边 · '
              f'新增 {len(new_nodes)} 实体 / {len(new_edges)} 关系')

    payload = {
        'generated': time.strftime('%Y-%m-%d %H:%M:%S'),
        'purpose': 'F1：按论文年份逐年累积，看"什么什么时候出现"',
        'design': {
            'time_source':
                '图快照本身**没有时间字段** —— 时间来自 `corpus-index.json` 的 `year`，'
                '而它与 arXiv id 的前两位一致（`2202.…` ⇒ 2022）。实测两者完全对得上。',
            'what_is_new':
                '**当年首次出现的实体/关系**（相对之前所有年份的并集）。'
                '图一年年变大是显然的，**"什么在这个时候出现"才是信息。**',
            'NO_communities':
                '**刻意不算社区** —— 逐年重跑 Louvain 的**社区编号会漂**，'
                '"2017 的社区 3"与"2020 的社区 3"不是同一个东西；'
                '把它们画成同一种颜色会让人以为"这个社区一直在"，而那是假的。'
                '要跨年比社区需要**社区对齐**（另一件事）。',
            'cumulative':
                '每帧的 `n_nodes`/`n_edges` 是**累积到该年**的规模，'
                '`n_new_*` 是**当年新增**。两者不要混读。',
        },
        'years': years,
        'totals': {'papers': sum(len(per_year[y]) for y in years),
                   'nodes': len(seen_nodes), 'edges': len(seen_edges)},
        'frames': frames,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(',', ':')),
                   encoding='utf-8')
    print(f'\n  已写出 {OUT}  ({OUT.stat().st_size / 1024:.0f} KB · '
          f'{time.time() - t0:.1f}s)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
