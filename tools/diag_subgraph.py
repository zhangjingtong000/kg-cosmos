# -*- coding: utf-8 -*-
"""
诊断 2D 视图的子图选取：为什么"按度数取 Top-N"必然得到毛线球。

只读诊断，不修改任何数据。

用法：
    python tools/diag_subgraph.py
"""
import json
import collections
import os

RAW = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   'web', 'kg-data', 'force-top3000.json')


def load():
    with open(RAW, encoding='utf-8') as f:
        return json.load(f)


def build_index(nodes, links):
    """返回 (adj, undirected_edge_set)。"""
    adj = collections.defaultdict(set)
    edges = set()
    for l in links:
        s, t = l['source'], l['target']
        if s == t:
            continue
        edges.add((s, t))
        adj[s].add(t)
        adj[t].add(s)
    return adj, edges


def induced(keep_ids, links):
    """诱导子图（无向去重，保留首次出现的边类型）。"""
    keep = set(keep_ids)
    seen = set()
    out = []
    for l in links:
        s, t = l['source'], l['target']
        if s not in keep or t not in keep or s == t:
            continue
        k = (s, t) if s < t else (t, s)
        if k in seen:
            continue
        seen.add(k)
        out.append(l)
    return out


def stats(name, nodes, edges):
    n = len(nodes)
    m = len(edges)
    if n == 0:
        print(f'{name}: 空')
        return
    comm = {x['id']: x.get('community', -1) for x in nodes}
    cross = sum(1 for e in edges if comm.get(e['source']) != comm.get(e['target']))
    dens = 2.0 * m / (n * (n - 1)) if n > 1 else 0.0
    # 连通分量
    adj = collections.defaultdict(set)
    for e in edges:
        adj[e['source']].add(e['target'])
        adj[e['target']].add(e['source'])
    seen, comps = set(), 0
    for x in nodes:
        if x['id'] in seen:
            continue
        comps += 1
        stack = [x['id']]
        seen.add(x['id'])
        while stack:
            u = stack.pop()
            for v in adj[u]:
                if v not in seen:
                    seen.add(v)
                    stack.append(v)
    types = collections.Counter(e.get('type', '?') for e in edges)
    print(f'\n{name}')
    print(f'  节点 {n:5d}   边 {m:5d}   平均度 {2*m/n:5.2f}   密度 {dens:.4f}   连通分量 {comps}')
    print(f'  跨社区边 {cross} ({100.0*cross/m:.1f}%)   边类型种类 {len(types)}')
    print(f'  边/节点 = {m/n:.2f}   ' + ('← 毛线球区间' if m / n > 2.5 else '← 可读区间'))
    print('  Top5 边类型: ' + ', '.join(f'{t}:{c}' for t, c in types.most_common(5)))


def main():
    raw = load()
    nodes, links = raw['nodes'], raw['links']
    print('=' * 78)
    print('数据总览')
    print('=' * 78)
    print('meta:', raw['meta'])
    nc = collections.Counter(x.get('community', -1) for x in nodes)
    print(f'社区数（含 -1）: {len(nc)}   无社区(-1): {nc.get(-1, 0)}')
    print('最大 8 个社区:', nc.most_common(8))
    tc = collections.Counter(x.get('type', '?') for x in nodes)
    print('Top10 实体类型:', tc.most_common(10))

    # ── 方案 A：按度数取 Top-N（当前实现）──
    by_deg = sorted(nodes, key=lambda x: -x.get('degree', 0))
    for n in (60, 150, 300):
        sub = by_deg[:n]
        stats(f'[A] 按度数 Top-{n}', sub, induced([x["id"] for x in sub], links))

    # ── 方案 B：按社区配额（新写的 pickByCommunity）──
    for n in (60, 150):
        by_comm = collections.defaultdict(list)
        for x in nodes:
            by_comm[x.get('community', -1)].append(x)
        for lst in by_comm.values():
            lst.sort(key=lambda x: -x.get('degree', 0))
        total = len(nodes)
        picked = []
        for ci, lst in by_comm.items():
            q = max(2, round(n * len(lst) / total))
            picked.extend(lst[:q])
        picked = picked[:n]
        stats(f'[B] 社区配额 Top-{n}', picked, induced([x["id"] for x in picked], links))

    # ── 方案 C：单个社区内部 ──
    for ci in nc.most_common(4):
        if ci[0] == -1:
            continue
        sub = [x for x in nodes if x.get('community') == ci[0]]
        stats(f'[C] 单社区 #{ci[0]}', sub[:400], induced([x['id'] for x in sub[:400]], links))

    # ── 方案 D：只保留社区内边（跨社区边整体砍掉）──
    keep150 = set(x['id'] for x in by_deg[:150])
    sub150 = [x for x in by_deg[:150]]
    intra = [e for e in induced([x['id'] for x in sub150], links)
             if {x['id']: x.get('community', -1) for x in sub150}[e['source']]
             == {x['id']: x.get('community', -1) for x in sub150}[e['target']]]
    stats('[A+] 度数 Top-150 但仅社区内边', sub150, intra)

    # ── 方案 E：从社区内枢纽出发的 2-hop 邻域 ──
    adj, _ = build_index(nodes, links)
    seed = next(x for x in by_deg if x.get('community', -1) >= 0 and 20 <= x.get('degree', 0) <= 60)
    hop1 = adj[seed['id']]
    hop2 = set()
    for v in hop1:
        hop2 |= adj[v]
    two = {seed['id']} | hop1 | hop2
    for cap in (40, 80, 150):
        ids = list(two)[:cap]
        idx = {x['id']: x for x in nodes}
        stats(f'[E] 2-hop 邻域（种子 {seed["id"][:28]}… 度{seed["degree"]}）cap={cap}',
              [idx[i] for i in ids if i in idx], induced(ids, links))


if __name__ == '__main__':
    main()
