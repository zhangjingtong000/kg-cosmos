# -*- coding: utf-8 -*-
"""
为「分面式社区图谱」（faceted community atlas）生成前端数据。

    python tools/build_atlas_data.py

产物：web/kg-data/atlas.json

设计依据（见 tools/diag_subgraph.py、tools/diag_atlas.py）：
  · 按度数取样是全图密度的最坏情况——Top-150 得到 6.40 条边/节点，
    屏幕上每个节点平均射出 12.8 条线，物理上无法辨认（已实测截图确认）。
  · 社区内部的边天然稀疏（Louvain 本就按"内部稠密"划分）。
  · 在"每社区取 Top-K 枢纽"之上再叠加"每节点最多 CAP 条边"的上限，
    可把每格的边/节点压到 1.7 以下（可读阈值约 2.5）。
  · K=40 / CAP=4 时 18 个社区全部达标，全局边/节点 ≈ 1.0。

社区卡片只有约 340x336 px，因此每格是一个"结构缩略图"：
只画 K 个枢纽的骨架 + 若干关键实体的名字，完整内容留到点击后的社区视图。
"""
import json
import os
import collections
import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, 'web', 'kg-data', 'force-full.json')
OUT = os.path.join(ROOT, 'web', 'kg-data', 'atlas.json')

K = 40             # 每个社区显示的枢纽实体数
CAP = 4            # 每个节点最多保留的边数
TOP_PAIRS = 50     # 社区间主干保留多少对

# 关系类型优先级：数字越小越优先保留
PRIORITY = {
    'PART_OF': 0, 'CREATED_BY': 0, 'CITES': 0,
    'APPLIES_TO': 1, 'USES': 1, 'PRODUCES': 1, 'EVALUATES': 1,
    'COMPARES_WITH': 1, 'IMPROVES': 1,
    'LOCATED_AT': 2, 'PUBLISHED_IN': 2,
    'RELATED_TO': 9,
}

# 18 色深色背景调色板（按社区规模降序分配）
PALETTE = [
    '#6ea8ff', '#7ee787', '#ffa657', '#ff7b9c', '#a78bfa', '#4dd4c4',
    '#ffd866', '#f78c6c', '#82aaff', '#c3e88d', '#f07178', '#89ddff',
    '#ffcb6b', '#c792ea', '#b2f2bb', '#ffb4ab', '#8bd5ff', '#d0bfff',
]


def pw(t):
    return PRIORITY.get(t, 5)


def cap_degree(edges, cap, degree_of, node_ids):
    """
    每节点最多 cap 条边。

    先用生成树锁定连通性，再按 (关系优先级, 端点最大度数) 贪心补边。
    为什么必须先锁生成树：单纯按优先级砍边会把桥边砍掉——实测这样即使
    节点选取本身是连通的，抽样后仍会碎成 2~10 个分量，缩略图里出现
    飘在边上的孤点。生成树边优先级高于 cap，是连通性的下界。
    """
    adj = collections.defaultdict(list)
    for i, e in enumerate(edges):
        adj[e['source']].append(i)
        adj[e['target']].append(i)

    chosen = set()
    cnt = collections.defaultdict(int)

    # ① 生成树（BFS）：从连边最多的节点出发，每个新节点锁定一条边
    if node_ids:
        start = max(node_ids, key=lambda v: len(adj.get(v, ())))
        seen = {start}
        stack = [start]
        while stack:
            u = stack.pop()
            for i in adj.get(u, ()):
                e = edges[i]
                v = e['target'] if e['source'] == u else e['source']
                if v in seen:
                    continue
                seen.add(v)
                stack.append(v)
                if i not in chosen:
                    chosen.add(i)
                    cnt[u] += 1
                    cnt[v] += 1

    # ② 其余边按优先级补足，受 cap 约束
    ordered = sorted(
        range(len(edges)),
        key=lambda i: (pw(edges[i].get('type', '?')),
                       -max(degree_of.get(edges[i]['source'], 0),
                            degree_of.get(edges[i]['target'], 0))),
    )
    for i in ordered:
        if i in chosen:
            continue
        e = edges[i]
        s, t = e['source'], e['target']
        if cnt[s] >= cap or cnt[t] >= cap:
            continue
        chosen.add(i)
        cnt[s] += 1
        cnt[t] += 1

    return [edges[i] for i in sorted(chosen)]


def looks_like_term(s):
    """过滤句子片段/幻觉实体，只留适合当社区标题的术语。"""
    if not s or len(s) < 2 or len(s) > 34:
        return False
    if s.endswith('.') or s.endswith(',') or s.endswith(';'):
        return False
    if s.isdigit():
        return False
    if s.count(' ') > 5:          # 太长的词组多半是句子片段
        return False
    return True


def pick_connected(comm_nodes, adj, K):
    """
    连通优先的贪心采样，返回选中的节点 id 列表。

    为什么不用"按度数取 Top-K"：
      社区内全局度数最高的那批节点，彼此之间往往没有直接的边——它们是
      通过社区里度数更低的节点间接相连的（全局度数高的节点，其连接主要
      跨社区）。实测按度数取样后，每社区的主连通分量只覆盖 22%~88% 的
      节点，20% 的节点是孤立点，缩略图里主体只占约 55% 面积，其余全是
      散点。

    这里改为从社区内度数最高的节点出发，每步加入"与已选集合连接最多"
    的节点，使取出的 K 个节点**始终连通**，缩略图因此是一个完整的结构
    而非一盘散沙。
    """
    by_id = {x['id']: x for x in comm_nodes}
    intra_deg = {nid: len(adj.get(nid, ())) for nid in by_id}
    ranked = sorted(by_id, key=lambda nid: (-intra_deg[nid], -by_id[nid].get('degree', 0)))
    if not ranked:
        return []
    prio = {nid: i for i, nid in enumerate(ranked)}

    seed = ranked[0]
    selected = [seed]
    remaining = set(ranked)
    remaining.discard(seed)

    link = collections.Counter()
    for v in adj.get(seed, ()):
        link[v] += 1

    while len(selected) < K and remaining:
        best = max(remaining, key=lambda v: (link.get(v, 0), -prio[v]))
        if link.get(best, 0) == 0:
            break                      # 主分量已取尽：宁缺毋滥，保持纯连通
        selected.append(best)
        remaining.discard(best)
        for v in adj.get(best, ()):
            if v in remaining:
                link[v] += 1
    return selected


def count_components(n, edges):
    """无向图的连通分量数。edges 为 [a, b, type] 索引对。"""
    par = list(range(n))

    def find(x):
        while par[x] != x:
            par[x] = par[par[x]]
            x = par[x]
        return x

    for a, b, _ in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            par[ra] = rb
    return len({find(i) for i in range(n)})


def main():
    with open(SRC, encoding='utf-8') as f:
        raw = json.load(f)

    nodes, links = raw['nodes'], raw['links']
    by_id = {x['id']: x for x in nodes}
    degree_of = {x['id']: x.get('degree', 0) for x in nodes}
    comm_of = {x['id']: x.get('community', -1) for x in nodes}

    comm_nodes = collections.defaultdict(list)
    for x in nodes:
        comm_nodes[x.get('community', -1)].append(x)

    # 只保留真实社区（Louvain 结果），-1 是未归类
    real = {c: lst for c, lst in comm_nodes.items() if c >= 0}
    unassigned = comm_nodes.get(-1, [])
    order = sorted(real.items(), key=lambda kv: -len(kv[1]))

    print(f'源数据: {len(nodes)} 实体 / {len(links)} 关系')
    print(f'真实社区 {len(order)} 个，未归类 {len(unassigned)} 个')

    # 预先按社区收集"全部内部边"（不只 Top-K），供连通采样建邻接表
    intra_all = collections.defaultdict(list)
    for e in links:
        cs = comm_of.get(e['source'], -1)
        ct = comm_of.get(e['target'], -1)
        if cs == ct and cs >= 0 and e['source'] != e['target']:
            intra_all[cs].append(e)

    communities = []
    for rank, (cid, lst) in enumerate(order):
        rst = sorted(lst, key=lambda x: -x.get('degree', 0))   # 仅用于挑选主题词
        adj = collections.defaultdict(set)
        for e in intra_all[cid]:
            adj[e['source']].add(e['target'])
            adj[e['target']].add(e['source'])

        keep_ids = pick_connected(lst, adj, K)
        kept = [by_id[i] for i in keep_ids]
        keep_set = set(keep_ids)

        # 社区内部边（诱导子图，无向去重）
        seen = set()
        intra = []
        for e in links:
            s, t = e['source'], e['target']
            if s not in keep_set or t not in keep_set or s == t:
                continue
            key = (s, t) if s < t else (t, s)
            if key in seen:
                continue
            seen.add(key)
            intra.append(e)

        capped = cap_degree(intra, CAP, degree_of, keep_ids)

        idx = {nid: i for i, nid in enumerate(keep_ids)}
        edges = [[idx[e['source']], idx[e['target']], e.get('type', '?')] for e in capped]

        # 社区主题词：度数最高的若干"像术语"的实体
        keywords = []
        for x in rst:
            if looks_like_term(x['id']) and x['id'] not in keywords:
                keywords.append(x['id'])
            if len(keywords) >= 4:
                break
        label = ' · '.join(keywords[:2]) if keywords else f'社区 {cid}'

        ratio = len(edges) / len(kept) if kept else 0
        ncomp = count_components(len(kept), edges)
        communities.append({
            'cid': cid,
            'size': len(lst),
            'color': PALETTE[rank % len(PALETTE)],
            'label': label,
            'keywords': keywords[:4],
            'nodes': [{'i': x['id'], 't': x.get('type', '?'), 'd': x.get('degree', 0)}
                      for x in kept],
            'edges': edges,
            'shown': len(kept),
            'missing': len(lst) - len(kept),
        })
        flag = 'OK ' if (ratio <= 2.5 and ncomp == 1) else '!\u00a0\u00a0'
        print(f'  {flag}社区 {cid:>3}  规模 {len(lst):>5}  '
              f'显示 {len(kept):>2} 实体 / {len(edges):>2} 关系  '
              f'边节点比 {ratio:.2f}  分量 {ncomp}  | {label[:40]}')

    # ── 社区间主干 ──
    pair = collections.Counter()
    for e in links:
        cs = by_id.get(e['source'], {}).get('community', -1)
        ct = by_id.get(e['target'], {}).get('community', -1)
        if cs == ct or cs < 0 or ct < 0:
            continue
        pair[(min(cs, ct), max(cs, ct))] += 1
    pos = {c['cid']: i for i, c in enumerate(communities)}
    backbone = [[pos[a], pos[b], n] for (a, b), n in pair.most_common(TOP_PAIRS)
                if a in pos and b in pos]

    tot_n = sum(c['shown'] for c in communities)
    tot_m = sum(len(c['edges']) for c in communities)
    payload = {
        'meta': {
            'generated': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'source': os.path.basename(SRC),
            'total_nodes': len(nodes),
            'total_links': len(links),
            'communities': len(communities),
            'unassigned': len(unassigned),
            'K': K, 'cap': CAP, 'top_pairs': TOP_PAIRS,
            'shown_nodes': tot_n, 'shown_edges': tot_m,
            'edge_per_node': round(tot_m / tot_n, 3) if tot_n else 0,
        },
        'communities': communities,
        'backbone': backbone,
        'unassigned_top': [x['id'] for x in
                           sorted(unassigned, key=lambda x: -x.get('degree', 0))[:12]],
    }

    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, separators=(',', ':'))

    kb = os.path.getsize(OUT) / 1024
    print()
    print(f'已写出 {OUT}')
    print(f'  实体 {tot_n} / 关系 {tot_m} → 全局边节点比 {payload["meta"]["edge_per_node"]}')
    print(f'  社区间主干 {len(backbone)} 对')
    print(f'  文件 {kb:.1f} KB')


if __name__ == '__main__':
    main()
