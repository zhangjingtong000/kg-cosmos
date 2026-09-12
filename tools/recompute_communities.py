"""重算 Louvain 社区结构与图指标（本地零成本）。

边数从 41745 降到 22892（过滤悬空/低支持/越界/自环），社区结构必须重算。
数据源：Neo4j 当前图库（已同步）。
"""
# 路径以本文件为锚点解析，不写死盘符——演示机不一定把仓库放在 D 盘。
from pathlib import Path as _Path
_HERE = _Path(__file__).resolve()
ROOT = _HERE.parent.parent          # tools/*.py 与 src/*.py 都在根目录下一层
import json
import sys
from collections import Counter
from pathlib import Path

import networkx as nx

sys.path.insert(0, str(ROOT / "src"))
from graph_store import Neo4jStore  # noqa: E402

OUT = ROOT / "data/quality"

store = Neo4jStore(uri="bolt://localhost:7687", user="neo4j", password="kgdemo2026")
nodes = store.query_nodes()
edges = store.query_edges()
print(f"载入 {len(nodes)} 节点 / {len(edges)} 边\n")

G = nx.Graph()
for n in nodes:
    G.add_node(n["name"], type=n.get("type"))
for e in edges:
    s, t = e["source"], e["target"]
    if s == t:
        continue
    if G.has_edge(s, t):
        G[s][t]["weight"] += 1
    else:
        G.add_edge(s, t, weight=1, rtype=e.get("type"))

print(f"图: {G.number_of_nodes()} 节点 / {G.number_of_edges()} 边（去重后）")

# 连通性
comps = list(nx.connected_components(G))
comps.sort(key=len, reverse=True)
lcc = comps[0]
print(f"连通分量 {len(comps)}，最大分量 {len(lcc)} ({len(lcc)/G.number_of_nodes()*100:.1f}%)")

# Louvain
communities = nx.community.louvain_communities(G, weight="weight", seed=42)
mod = nx.community.modularity(G, communities, weight="weight")
print(f"\nLouvain: {len(communities)} 社区，模块度 {mod:.4f}")

sizes = sorted((len(c) for c in communities), reverse=True)
print(f"社区规模: 最大 {sizes[0]} ({sizes[0]/G.number_of_nodes()*100:.1f}%)"
      f"  中位 {sizes[len(sizes)//2]}  最小 {sizes[-1]}")

# 度数
deg = dict(G.degree())
top = sorted(deg.items(), key=lambda x: -x[1])[:12]
print(f"\n度数 Top 12（枢纽实体）:")
for nm, d in top:
    print(f"   {nm[:46]:46s} {d}")

# 中心性（Top 10 by PageRank）
pr = nx.pagerank(G, weight="weight")
print(f"\nPageRank Top 10:")
for nm, v in sorted(pr.items(), key=lambda x: -x[1])[:10]:
    print(f"   {nm[:46]:46s} {v:.5f}")

# 社区摘要（前 6 个社区的核心实体）
print(f"\n前 6 个社区的核心实体（按度）:")
for i, c in enumerate(sorted(communities, key=len, reverse=True)[:6], 1):
    sub = G.subgraph(c)
    core = sorted(((n, sub.degree(n)) for n in c), key=lambda x: -x[1])[:6]
    types = Counter(G.nodes[n].get("type") for n in c)
    print(f"\n   社区 {i}: {len(c)} 个实体  主类型 {types.most_common(3)}")
    print(f"      核心: {', '.join(f'{n}({d})' for n, d in core)}")

OUT.mkdir(parents=True, exist_ok=True)
(OUT / "community-structure.json").write_text(json.dumps({
    "nodes": G.number_of_nodes(), "edges": G.number_of_edges(),
    "components": len(comps), "lcc": len(lcc),
    "modularity": round(mod, 4), "communities": len(communities),
    "sizes": sizes[:100],
    "top_degree": [{"name": n, "degree": d} for n, d in top],
    "top_pagerank": [{"name": n, "score": round(v, 6)}
                     for n, v in sorted(pr.items(), key=lambda x: -x[1])[:100]],
    "community_cores": [
        {"id": i, "size": len(c),
         "types": dict(Counter(G.nodes[n].get("type") for n in c)),
         "core": [{"name": n, "degree": G.subgraph(c).degree(n)}
                  for n, _ in sorted(((n, G.subgraph(c).degree(n)) for n in c),
                                     key=lambda x: -x[1])[:15]]}
        for i, c in enumerate(sorted(communities, key=len, reverse=True)[:30], 1)
    ],
}, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"\n报告已写出: {OUT / 'community-structure.json'}")
store.close()
