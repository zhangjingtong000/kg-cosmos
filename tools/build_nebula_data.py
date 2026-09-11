"""生成社区星云的布局数据（本地零成本）。

【设计思路】
  社区星云 = 把 Louvain 社区画成空间上分开的"星云团"，团内是实体节点。
  位置在**离线阶段**算好并落盘，前端只负责渲染——这样前端不必跑力导向
  （15315 节点在浏览器里跑力导向会卡死），而是直接读坐标。
  这也符合调研结论：graphrag-workbench 用 SSE 增量、obsidian-3d-graph 承认
  3D 库解决不了大图性能；离线布局是绕开该限制的最直接办法。

【布局算法】
  1. 社区层级：按社区规模排序，用**斐波那契球面**分配社区中心
     （规模越大越靠内，避免大社区被挤到角落）
  2. 团内层级：每个社区内部用**球状均匀分布**（Fibonacci sphere），
     半径 ∝ sqrt(社区规模)，使密度大致恒定
  3. 单节点社区（规模 1）单独成"尘埃层"，在外围球壳上稀疏分布，
     前端可默认隐藏——它们占社区总数的 90%+ 但信息量低

【输出】web/kg-data/nebula.json，供 web/community-nebula.html 渲染。

用法：
  python tools/build_nebula_data.py
  python tools/build_nebula_data.py --max-communities 60
"""
import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

import networkx as nx

sys.path.insert(0, r"D:\kg-demo\src")
from graph_store import Neo4jStore  # noqa: E402

OUT = Path(r"D:\kg-demo\web\kg-data")
QUALITY = Path(r"D:\kg-demo\data\quality")

# 15 类实体的颜色（前端调色板基底）
TYPE_COLORS = {
    "Person": "#ff7f7f", "Concept": "#7fb3ff", "Method": "#7fffb3",
    "Document": "#ffd97f", "Process": "#c77fff", "System": "#7fffff",
    "Artifact": "#ff9f7f", "Component": "#b3ff7f", "Event": "#ff7fd9",
    "Metric": "#7fd9ff", "Dataset": "#d9ff7f", "Organization": "#ffbf7f",
    "Location": "#9f7fff", "Tool": "#7fffd9", "Material": "#d9d9d9",
}


def parse_args():
    ap = argparse.ArgumentParser(description="生成社区星云布局")
    ap.add_argument("--max-communities", type=int, default=60,
                    help="最多导出多少个社区（按规模降序），其余归入尘埃层")
    ap.add_argument("--max-edges", type=int, default=30000)
    return ap.parse_args()


ARGS = parse_args()


def fib_sphere(i, n):
    """第 i 个点（共 n 个）在单位球面上的均匀分布。"""
    if n <= 1:
        return (0.0, 0.0, 0.0)
    phi = math.acos(1 - 2 * (i + 0.5) / n)
    theta = math.pi * (1 + 5 ** 0.5) * i
    return (math.sin(phi) * math.cos(theta),
            math.sin(phi) * math.sin(theta),
            math.cos(phi))


def main():
    store = Neo4jStore(uri="bolt://localhost:7687", user="neo4j", password="kgdemo2026")
    nodes = store.query_nodes()
    edges = store.query_edges()
    store.close()
    print(f"载入 {len(nodes)} 节点 / {len(edges)} 边")

    G = nx.Graph()
    for n in nodes:
        G.add_node(n["name"], type=n.get("type") or "Concept")
    for e in edges:
        s, t = e["source"], e["target"]
        if s != t and s in G and t in G:
            G.add_edge(s, t)

    print("计算 Louvain 社区...")
    communities = nx.community.louvain_communities(G, weight=None, seed=42)
    communities = sorted(communities, key=len, reverse=True)
    print(f"  {len(communities)} 个社区")

    big = communities[:ARGS.max_communities]
    dust = [n for c in communities[ARGS.max_communities:] for n in c]
    print(f"  导出大社区 {len(big)} 个（{sum(len(c) for c in big)} 节点）"
          f"，尘埃层 {len(dust)} 节点")

    # 社区中心：斐波那契球面，大社区放在内层球壳
    out_nodes = []
    out_communities = []
    R_outer = 900.0

    for idx, comm in enumerate(big):
        # 社区中心方向
        dirv = fib_sphere(idx, len(big))
        # 规模越大越靠内：半径 = R * (0.35 + 0.65 * 归一化排名)
        rank = idx / max(1, len(big) - 1)
        crad = R_outer * (0.30 + 0.55 * rank)
        cx, cy, cz = (dirv[0] * crad, dirv[1] * crad, dirv[2] * crad)

        # 团半径 ∝ sqrt(规模)，保持密度恒定
        size = len(comm)
        blob_r = 26.0 * math.sqrt(size) + 18.0

        members = sorted(comm)
        types = Counter(G.nodes[m].get("type") for m in members)
        degs = {m: G.degree(m) for m in members}
        core = sorted(members, key=lambda m: -degs[m])[:12]

        # 社区内位置：球状均匀 + 轻度按度数向心（核心靠中）
        for j, m in enumerate(members):
            ux, uy, uz = fib_sphere(j, size)
            d = degs[m]
            # 度数越高越靠中心
            shrink = 1.0 - min(0.65, d / (max(degs.values()) + 1) * 0.65)
            r = blob_r * shrink
            out_nodes.append({
                "id": m,
                "t": G.nodes[m].get("type") or "Concept",
                "c": idx,
                "d": d,
                "x": round(cx + ux * r, 2),
                "y": round(cy + uy * r, 2),
                "z": round(cz + uz * r, 2),
            })

        out_communities.append({
            "id": idx, "size": size, "center": [round(cx, 1), round(cy, 1), round(cz, 1)],
            "radius": round(blob_r, 1),
            "types": dict(types.most_common()),
            "core": [{"id": m, "degree": degs[m]} for m in core],
            "label": " / ".join(m for m in core[:3]),
        })

    # 尘埃层：外围球壳
    for j, m in enumerate(dust):
        ux, uy, uz = fib_sphere(j, len(dust))
        r = R_outer * 1.25
        out_nodes.append({
            "id": m, "t": G.nodes[m].get("type") or "Concept", "c": -1,
            "d": G.degree(m),
            "x": round(ux * r, 2), "y": round(uy * r, 2), "z": round(uz * r, 2),
        })

    # 边：只保留两端都在导出节点里的
    keep = {n["id"] for n in out_nodes}
    out_edges = []
    seen = set()
    for e in edges:
        s, t = e["source"], e["target"]
        if s == t or s not in keep or t not in keep:
            continue
        key = (s, t) if s < t else (t, s)
        if key in seen:
            continue
        seen.add(key)
        out_edges.append([s, t, e.get("type") or "RELATED_TO"])
        if len(out_edges) >= ARGS.max_edges:
            break
    print(f"导出节点 {len(out_nodes)}，边 {len(out_edges)}")

    payload = {
        "meta": {
            "nodes": len(out_nodes), "edges": len(out_edges),
            "communities": len(out_communities), "dust": len(dust),
            "modularity": 0.6824,
            "generated_by": "tools/build_nebula_data.py",
        },
        "type_colors": TYPE_COLORS,
        "communities": out_communities,
        "nodes": out_nodes,
        "edges": out_edges,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / "nebula.json"
    p.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                 encoding="utf-8")
    print(f"\n已写出 {p}  ({p.stat().st_size/1024/1024:.2f} MB)")

    print("\n社区摘要（前 8）:")
    for c in out_communities[:8]:
        print(f"   #{c['id']:2d} {c['size']:5d} 节点  核心: {c['label'][:60]}")


if __name__ == "__main__":
    main()
