# -*- coding: utf-8 -*-
"""
⚠️ 已废弃：本脚本的产物已回退，不要使用。

   它当初的立论是误判——我只看了 comm-0.json 的第一个节点 (32.52, 0, 0)
   就断定"坐标是扁平的"。实际数据是完整的 3D 力导向布局（x ±181 / y ±166 /
   z ±18，1104 个节点里 1103 个 y 非零）。那个节点恰好在原点，因为它是全社区
   度数最高的枢纽，力导向本来就把它放在原点附近。

   这里生成的"均匀填充球体"丢掉了原始的中心-外围拓扑，因此整体回退。
   脚本保留，仅为记录其中的两个算法实现：连通优先采样、生成树优先的度数上限。

重做社区内部视图（L1）的数据：web/kg-data/comm-{i}.json

    python tools/build_community_3d.py

为什么要重做
------------
原来的 comm-*.json 有两个致命问题（已在 web/kg-nebula.html?c=17 实测确认）：

1. **坐标是扁平的**。节点形如 {"x":32.52,"y":0.0,"z":0.0}，全部落在一个
   平面上。3D 里一旋转就互相重叠，屏幕上是一张密不透风的线网。
2. **节点数没受控**。社区 0 直接塞进 1104 个实体 / 2400 条内部边，标签
   预算（Top-22）之外的部分全靠猜。

本次做法
--------
· 节点：复用连通优先采样（见 build_atlas_data.py 的 pick_connected），
  从社区内度数最高的节点出发贪心扩张，保证取出的 K 个实体**始终连通**。
  这样 L1 是一个完整结构，而不是一堆飘散的点。
· 坐标：networkx spring_layout(dim=3) 算三维布局，再把半径按名次重映射到
  [0.30R, R]，得到一个**均匀填充的球状点云**。均匀填充是为了避免力导向
  常见的"中心结块"，同时保留方向信息。
· 边：取选中节点之间的全部社区内边（不做度数上限，因为社区内边天然稀疏，
  实测边/节点在 1.2~2.2 之间）。
"""
import json
import os
import math
import collections

import numpy as np
import networkx as nx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, 'web', 'kg-data')
SRC = os.path.join(DATA, 'force-full.json')
INDEX = os.path.join(DATA, 'community-index.json')
FILAMENTS = os.path.join(DATA, 'filaments.json')

K = 130           # 每个社区呈现的实体数上限
CAP = 4           # 每个节点的边数上限（社区 0 这类通用概念池需要）
SEED = 42
INNER_RATIO = 0.30   # 最内层半径占 R 的比例

# 关系类型优先级：数字越小越优先保留
PRIORITY = {
    'PART_OF': 0, 'CREATED_BY': 0, 'CITES': 0,
    'APPLIES_TO': 1, 'USES': 1, 'PRODUCES': 1, 'EVALUATES': 1,
    'COMPARES_WITH': 1, 'IMPROVES': 1,
    'LOCATED_AT': 2, 'PUBLISHED_IN': 2,
    'RELATED_TO': 9,
}


def pw(t):
    return PRIORITY.get(t, 5)


def cap_degree(edges, cap, degree_of, node_ids):
    """
    每节点最多 cap 条边；先用生成树锁定连通性，再按优先级贪心补边。

    为什么必须先锁生成树：单纯按优先级砍边会砍掉桥边，抽样后图会重新
    碎成多个分量，L1 里就出现飘在边上的孤点。
    """
    adj = collections.defaultdict(list)
    for i, (s, t, _) in enumerate(edges):
        adj[s].append(i)
        adj[t].append(i)

    chosen = set()
    cnt = collections.Counter()

    if node_ids:
        start = max(node_ids, key=lambda v: len(adj.get(v, ())))
        seen = {start}
        stack = [start]
        while stack:
            u = stack.pop()
            for i in adj.get(u, ()):
                s, t, _ = edges[i]
                v = t if s == u else s
                if v in seen:
                    continue
                seen.add(v)
                stack.append(v)
                if i not in chosen:
                    chosen.add(i)
                    cnt[s] += 1
                    cnt[t] += 1

    for i in sorted(range(len(edges)),
                    key=lambda i: (pw(edges[i][2]),
                                   -max(degree_of.get(edges[i][0], 0),
                                        degree_of.get(edges[i][1], 0)))):
        if i in chosen:
            continue
        s, t, _ = edges[i]
        if cnt[s] >= cap or cnt[t] >= cap:
            continue
        chosen.add(i)
        cnt[s] += 1
        cnt[t] += 1

    return [edges[i] for i in sorted(chosen)]


def pick_connected(comm_nodes, adj, k):
    """
    连通优先的贪心采样：从社区内度数最高的节点出发，每步加入"与已选集合
    连接最多"的节点（并列取社区内度数高的），保证选出的节点集连通。
    """
    by_id = {n['id']: n for n in comm_nodes}
    intra_deg = {nid: len(adj.get(nid, ())) for nid in by_id}
    ranked = sorted(by_id, key=lambda nid: (-intra_deg[nid], -by_id[nid].get('d', 0)))
    if not ranked:
        return []
    prio = {nid: i for i, nid in enumerate(ranked)}

    selected = [ranked[0]]
    remaining = set(ranked)
    remaining.discard(ranked[0])
    link = collections.Counter()
    for v in adj.get(ranked[0], ()):
        link[v] += 1

    while len(selected) < k and remaining:
        best = max(remaining, key=lambda v: (link.get(v, 0), -prio[v]))
        if link.get(best, 0) == 0:
            break
        selected.append(best)
        remaining.discard(best)
        for v in adj.get(best, ()):
            if v in remaining:
                link[v] += 1
    return selected


def ball_positions(ids, edges, radius, seed):
    """
    三维布局 + 球状重映射。

    spring_layout 给出拓扑合理的相对位置，但力导向普遍会在中心结块。
    这里按每个点的原始半径排序，把半径重映射成"按名次均匀分布"，
    得到一个填充均匀的球状点云（方向保留，密度均匀）。
    """
    G = nx.Graph()
    G.add_nodes_from(ids)
    G.add_edges_from(edges)
    n = len(ids)
    if n == 0:
        return {}, 0.0
    if n == 1:
        return {ids[0]: (0.0, 0.0, 0.0)}, radius

    iters = 90 if n < 400 else 50
    pos = nx.spring_layout(G, dim=3, iterations=iters, seed=seed, k=None)

    arr = np.array([pos[i] for i in ids], dtype=float)
    arr -= arr.mean(axis=0)
    norms = np.linalg.norm(arr, axis=1)
    norms[norms < 1e-9] = 1e-9

    # 按名次重映射半径：最内 30%R，最外 100%R
    order = np.argsort(np.argsort(norms))          # 名次
    t = order / max(1, n - 1)
    target = radius * (INNER_RATIO + (1.0 - INNER_RATIO) * t)
    arr *= (target / norms)[:, None]

    out = {nid: (float(arr[k, 0]), float(arr[k, 1]), float(arr[k, 2]))
           for k, nid in enumerate(ids)}
    return out, radius


def main():
    with open(SRC, encoding='utf-8') as f:
        raw = json.load(f)
    with open(INDEX, encoding='utf-8') as f:
        index = {c['i']: c for c in json.load(f)['communities']}
    with open(FILAMENTS, encoding='utf-8') as f:
        fil = json.load(f)
    # 社区颜色沿用概览层的定义，保证两层配色一致
    color_of = {}
    core_of = {}
    type_of = {}
    for c in fil['clouds']:
        try:
            ci = int(str(c['id']).lstrip('c'))
        except ValueError:
            continue
        color_of[ci] = c.get('color')
        core_of[ci] = c.get('core') or []
        type_of[ci] = c.get('typeCount') or {}

    nodes, links = raw['nodes'], raw['links']
    comm_of = {n['id']: n.get('community', -1) for n in nodes}
    deg_of = {n['id']: n.get('degree', 0) for n in nodes}
    type_by_id = {n['id']: n.get('type', '?') for n in nodes}

    # 社区内邻接表（只用社区内部边）
    adj = collections.defaultdict(lambda: collections.defaultdict(set))
    intra_edges = collections.defaultdict(list)
    for e in links:
        s, t = e['source'], e['target']
        cs, ct = comm_of.get(s, -1), comm_of.get(t, -1)
        if cs != ct or cs < 0 or s == t:
            continue
        adj[cs][s].add(t)
        adj[cs][t].add(s)
        intra_edges[cs].append((s, t, e.get('type', '?')))

    print(f'{"cid":>4} {"规模":>6} {"显示":>5} {"边":>5} {"m/n":>5} {"半径":>7}  主实体')
    total_shown = 0
    for ci in sorted(index):
        meta = index[ci]
        radius = float(meta.get('radius') or 100.0)

        members = [n for n in nodes if comm_of.get(n['id']) == ci]
        if not members:
            print(f'{ci:>4}  (空社区，跳过)')
            continue
        member_payload = [{'id': n['id'], 'd': deg_of.get(n['id'], 0)} for n in members]

        keep = pick_connected(member_payload, adj[ci], K)
        keep_set = set(keep)
        order = {nid: i for i, nid in enumerate(keep)}

        edges3 = [(s, t, ty) for (s, t, ty) in intra_edges[ci]
                  if s in keep_set and t in keep_set]
        # 去重（无向），保留首次出现的类型
        seen = set()
        dedup = []
        for s, t, ty in edges3:
            key = (s, t) if s < t else (t, s)
            if key in seen:
                continue
            seen.add(key)
            dedup.append((key[0], key[1], ty))
        # 度数上限：社区 0 这类"通用概念池"的 Top-130 之间高度互联
        # （未加限时 735 边 / 130 节点 = 5.65，远超可读阈值），必须压下来。
        edges = [(s, t) for (s, t, _) in cap_degree(dedup, CAP, deg_of, keep)]

        pos, R = ball_positions(keep, edges, radius, SEED + ci)

        # core：保留概览层的核心实体（按社区内度数排序）
        core = [nid for nid in keep][:12]
        core_sorted = sorted(keep, key=lambda nid: -len(adj[ci].get(nid, ())))[:10]

        out_nodes = []
        for nid in keep:
            x, y, z = pos[nid]
            rr = math.sqrt(x * x + y * y + z * z) / max(R, 1e-6)
            out_nodes.append({
                'id': nid,
                't': type_by_id.get(nid, '?'),
                'd': len(adj[ci].get(nid, ())),      # 社区内度数：决定点的大小与星等
                'g': deg_of.get(nid, 0),             # 全局度数（详情用）
                'r': round(rr, 4),
                'x': round(x, 3), 'y': round(y, 3), 'z': round(z, 3),
            })

        payload = {
            'id': meta['id'],
            'name': meta['name'],
            'color': color_of.get(ci, '#8fb4ff'),
            'size': radius,
            'entityCount': len(members),
            'typeCount': type_of.get(ci) or {},
            'core': core_sorted,
            'meta': {
                'shown': len(keep),
                'edges': len(edges),
                'radius': round(R, 2),
                'source': 'community-3d',
                'layout': 'spring3d + ball remap',
            },
            'nodes': out_nodes,
            'edges': [[order[s], order[t]] for (s, t) in edges],
        }
        path = os.path.join(DATA, f'comm-{ci}.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, separators=(',', ':'))

        ratio = len(edges) / len(keep) if keep else 0
        total_shown += len(keep)
        top = ', '.join(core_sorted[:3])[:52]
        flag = 'OK ' if ratio <= 2.5 else '!  '
        print(f'{flag}{ci:>2} {len(members):>6} {len(keep):>5} {len(edges):>5} '
              f'{ratio:>5.2f} {R:>7.1f}  {top}')

    print(f'\n合计呈现 {total_shown} 个实体（原为 8147）')
    print('已重写 web/kg-data/comm-0.json … comm-17.json')


if __name__ == '__main__':
    main()
