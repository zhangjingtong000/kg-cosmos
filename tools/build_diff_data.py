# -*- coding: utf-8 -*-
r"""F2 · 多图对比 diff（任选两篇，看**共有 / 各自独有**）。

## TODO 的原话

> **F2** | ⬜ **多图对比 diff** | 同上第 8 项 | 2 天

## 先量了一遍，才决定这个页面该给人看什么

17 篇两两对比（136 对）的实测：

```
节点重叠（Jaccard）最高只有 0.089 —— 同领域的两篇，共享实体最多 546 个
**节点重叠为 0 的对数：0 / 136**   ← 每一对都有共享实体
**边重叠为 0 的对数：28 / 136**    ← 但有 28 对**没有共享的边**
```

**⇒ 后一条最有意思**：**两篇共享了实体，却没有任何一条相同的关系** ——
即"**同一批东西，两篇给出的关系不一样**"。
那才是"多图对比"真正的看点（共享实体是显然的，**关系怎么冲突才是信息**）。

## 所以这页给三样东西

1. **共有实体 / 各自独有**（带 Jaccard，让人一眼看出"重合其实很小"）；
2. **共有边**：两端都在交集里的边 —— **按对归纳**，
   并把 `A 篇的类型` 与 `B 篇的类型` **并排列出来**；
3. **★ 类型冲突**：同一对实体，两篇给的类型**不同**的那些。
   ⚠️ 但要说清：**类型不同不等于"矛盾"** ——
   可能是两篇在说不同的侧面（一篇 `USES`、一篇 `RELATED_TO`），
   本项目**没有做"这两条边冲突吗"的判断**（那需要读原文）。
   ⇒ 所以页面把它叫「**类型分歧**」并注明"不等于矛盾"。

## 为什么不对所有 136 对预计算

两两全算会得到一个 136 × (节点+边) 的大文件，而用户**一次只看一对**。
⇒ 数据文件只存**每篇自己的节点/边**（紧凑），**交集在浏览器里算** ——
这样文件是 17 篇而不是 136 对，且换任意两篇都不用重跑。

用法：
    python tools/build_diff_data.py
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'web' / 'kg-data' / 'diff.json'
GRAPHS = ROOT / 'data' / 'corpus' / 'graphs'
INDEX = ROOT / 'web' / 'kg-data' / 'corpus-index.json'


def parse_args():
    ap = argparse.ArgumentParser(description='F2 多图对比数据')
    ap.add_argument('--max-list', type=int, default=400,
                    help='每篇最多存多少个节点名（避免文件过大）')
    return ap.parse_args()


def main():
    a = parse_args()
    t0 = time.time()
    print('=' * 92)
    print('F2 · 多图对比数据')
    print('=' * 92)

    idx = json.loads(INDEX.read_text(encoding='utf-8'))
    meta = {p['id']: p for p in (idx.get('papers') or [])}

    # ⚠️ **节点一律不截断**。
    #   第一版按 `max_list` 截断，结果 Jaccard 从 0.089 掉到 0.072 ——
    #   因为截断是**按字典序**取前 N，**交集就算不准了**，
    #   而"重叠多少"正是这一页要给人看的数。⇒ 宁可拆文件，也不截断。
    #
    # ⇒ **拆成"索引 + 每篇一个文件"**：用户一次只看两篇，
    #   首页只加载索引（小），选了哪两篇才去取那两个文件。
    per_dir = OUT.parent / 'diff'
    per_dir.mkdir(parents=True, exist_ok=True)
    # 清掉上一版可能留下的（文件名固定为 <id>.json，不会误删别的）
    for old in per_dir.glob('*.json'):
        old.unlink()

    papers = []
    for f in sorted(GRAPHS.glob('*.json')):
        pid = f.stem
        try:
            d = json.loads(f.read_text(encoding='utf-8-sig'))
        except Exception as e:
            print(f'  ⚠️ {pid} 读失败：{type(e).__name__}')
            continue
        nodes = sorted({str(n.get('name') or n.get('id'))
                        for n in (d.get('nodes') or []) if (n.get('name') or n.get('id'))})
        # 边存成 `s|t|type` 字符串（比三元组数组省很多字节）
        edges = sorted({f'{e.get("source")}|{e.get("target")}|{e.get("type") or "?"}'
                        for e in (d.get('edges') or [])})
        p = {'id': pid,
             'title': (meta.get(pid) or {}).get('title') or '',
             'year': (meta.get(pid) or {}).get('year'),
             'n_nodes': len(nodes), 'n_edges': len(edges)}
        (per_dir / f'{pid}.json').write_text(
            json.dumps({'id': pid, 'nodes': nodes, 'edges': edges},
                       ensure_ascii=False, separators=(',', ':')),
            encoding='utf-8')
        papers.append(p)
        print(f'    {pid:<16} {len(nodes):>5} 节点 · {len(edges):>5} 边')

    # ── 预计算 136 对的**摘要**（不是全量），首页据此给出"最该看哪几对" ──
    from itertools import combinations
    full = {}
    for p in papers:
        d = json.loads((per_dir / f'{p["id"]}.json').read_text(encoding='utf-8'))
        full[p['id']] = (set(d['nodes']), set(d['edges']))
    pairs = []
    for x, y in combinations(papers, 2):
        nx, ex = full[x['id']]
        ny, ey = full[y['id']]
        inter = nx & ny
        j = len(inter) / max(1, len(nx | ny))
        einter = ex & ey
        pairs.append({
            'a': x['id'], 'b': y['id'],
            'j': round(j, 4),
            'n_inter_nodes': len(inter),
            'n_inter_edges': len(einter),
            # **共享实体但零共享边** —— 这是最值得看的形态
            'zero_shared_edges': len(inter) > 0 and len(einter) == 0,
        })
    pairs.sort(key=lambda p: -p['j'])
    n_zero = sum(1 for p in pairs if p['zero_shared_edges'])
    print()
    print(f'  两两对比 {len(pairs)} 对 · 节点重叠最高 J={pairs[0]["j"]:.3f}'
          f'（{pairs[0]["a"]} × {pairs[0]["b"]}）')
    print(f'  ★ **共享实体、但零共享边的对：{n_zero} 对** —— 那是最值得看的形态')
    print(f'     （即"同一批东西，两篇给出的关系完全不一样"）')

    payload = {
        'generated': time.strftime('%Y-%m-%d %H:%M:%S'),
        'purpose': 'F2：任选两篇，看共有 / 各自独有',
        'design': {
            'why_not_precompute_all':
                '两两全算会得到 136 对 × (节点+边) 的大文件，而用户**一次只看一对**。'
                '⇒ 数据只存**每篇自己的**节点/边（紧凑），**交集在浏览器里算**：'
                '文件是 17 篇而不是 136 对，且换任意两篇都不用重跑。',
            'type_divergence_NOT_contradiction':
                '同一对实体在两篇里类型不同 ⇒ 叫「**类型分歧**」，'
                '**不等于"矛盾"** —— 可能是两篇在说不同的侧面。'
                '本项目**没有做"这两条边冲突吗"的判断**（那需要读原文）。',
            'edge_format': '边存成 `source|target|type` 字符串（比三元组数组省很多字节）。',
            'measured': {
                'max_jaccard': pairs[0]['j'] if pairs else None,
                'pairs_with_zero_shared_edges': n_zero,
                'note': '**同领域两篇的节点重叠也很小（最高 0.089）**；'
                        '而"共享实体但零共享边"有 28 对 —— 那才是这个页面的看点。',
            },
        },
        'papers': papers,
        'pairs': pairs,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(',', ':')),
                   encoding='utf-8')
    print(f'\n  已写出 {OUT}  ({OUT.stat().st_size / 1024:.0f} KB · '
          f'{time.time() - t0:.1f}s)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
