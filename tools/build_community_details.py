"""生成社区内部视图数据（点击星云后飞入所见）。

【为什么要单独生成】
  概览视图是"社区级"的：一团星云 = 一个社区，跨社区关系 = 纤维。
  但"进入星云内部"需要**社区内的实体与边**——这是另一层数据，概览里刻意不画
  （否则上万条边会糊成一片）。本脚本按社区分别落盘，前端点击后按需加载。

【布局算法（每个社区独立算）】
  用极坐标径向排布：半径由 PageRank 排序决定（越重要越靠中心），
  角度按黄金角展开避免成行成列。这样既体现"核心-边缘"结构，
  又不依赖力导向（1104 个节点在浏览器里跑力导向会卡）。

【输出】web/kg-data/comm-<i>.json
  nodes: [{id, t, d, r (PageRank), x, y, z}]   x/y/z 已算好
  edges: [[src, tgt, type]]                    社区内部边（去重）

用法：
  python tools/build_community_details.py
  python tools/build_community_details.py --max-nodes 1200 --min-degree 1
"""
# 路径以本文件为锚点解析，不写死盘符——演示机不一定把仓库放在 D 盘。
from pathlib import Path as _Path
_HERE = _Path(__file__).resolve()
ROOT = _HERE.parent.parent          # tools/*.py 与 src/*.py 都在根目录下一层
import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

import networkx as nx

sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))          # 为了 import graph_source
from graph_store import Neo4jStore  # noqa: E402,F401  （保留导入：向后兼容）
from graph_source import load_graph  # noqa: E402

OUT = ROOT / "web/kg-data"
FIL = OUT / "filaments.json"


def parse_args():
    ap = argparse.ArgumentParser(description="生成社区内部视图数据")
    ap.add_argument("--max-nodes", type=int, default=1200, help="单社区最多导出节点数")
    ap.add_argument("--min-degree", type=int, default=1, help="内部边最少度数门槛")
    ap.add_argument("--max-edges", type=int, default=4000, help="单社区最多边数")
    # 2026-09-15：数据源从 Neo4j 换成 data/corpus/graphs/*.json。
    # 默认与 build_filament_data.py 一致地做治理还原，否则两个脚本口径不同，
    # 社区详情里的边会与星云对不上。
    ap.add_argument("--no-governed", action="store_true",
                    help="不做 entity_filter 治理还原（与 filaments 保持一致时不要用）")
    return ap.parse_args()


ARGS = parse_args()


def main():
    if not FIL.exists():
        raise SystemExit("先运行 tools/build_filament_data.py 生成 filaments.json")
    fil = json.loads(FIL.read_text(encoding="utf-8"))
    clouds = fil["clouds"]
    print(f"filaments.json: {len(clouds)} 个社区")

    # ── 数据源（2026-09-15）──────────────────────────────────────────────
    # 原先**硬依赖 Neo4j**，而那个实例已经不在了 ⇒ 社区详情无法重建。
    # 现在统一走 `tools/graph_source.py`。
    # **默认开治理还原**，与 `build_filament_data.py` 保持一致 ——
    # 否则两个脚本口径不同，社区详情里的边会与星云对不上。
    nodes, edges = load_graph(governed=not ARGS.no_governed)
    print(f"载入 {len(nodes)} 节点 / {len(edges)} 边")

    G = nx.Graph()
    ntype = {}
    for n in nodes:
        G.add_node(n["name"])
        ntype[n["name"]] = n.get("type") or "Concept"
    # ── 保留真实边类型（2026-09-15 修）──────────────────────────────────
    # `nx.Graph()` 一条边只能有一个属性，而**同一对实体可能有多条不同类型的边**
    # （例如 A—USES→B 与 A—EVALUATES→B 同时存在）。所以另外维护一个
    # `(小, 大) -> [类型...]` 的字典来记住它们。
    #
    # **为什么要修**：此前 `out_edges.append([m, nb, "RELATED"])` **硬编码**
    # 把类型写成 `"RELATED"` —— 实测 18 个社区的 **16569 条边全是 `RELATED`**。
    # 于是社区详情里**看不出任何关系语义**，`CONTRADICTS`（争议）自然也不存在。
    # 这正是"概览里有类型、详情里没有"的根因。
    pair_types = {}
    for e in edges:
        s, t = e["source"], e["target"]
        if s != t and s in G and t in G:
            G.add_edge(s, t)
            key = (s, t) if s <= t else (t, s)
            ty = str(e.get("type") or "RELATED_TO")
            lst = pair_types.setdefault(key, [])
            if ty not in lst:
                lst.append(ty)

    print("Louvain（与概览一致，seed=42）...")
    comms = sorted(nx.community.louvain_communities(G, seed=42), key=len, reverse=True)

    print("计算 PageRank（用于径向布局的重要性排序）...")
    pr = nx.pagerank(G, weight=None)

    index = []
    for i, (cm, cloud) in enumerate(zip(comms[:len(clouds)], clouds)):
        members = [m for m in sorted(cm) if G.degree(m) >= ARGS.min_degree]
        members.sort(key=lambda m: -pr.get(m, 0))
        if len(members) > ARGS.max_nodes:
            members = members[:ARGS.max_nodes]

        # ── 极坐标径向布局 ──
        n = len(members)
        R = 30.0 + 4.6 * math.sqrt(n)          # 社区半径随规模增长
        pos = {}
        out_nodes = []
        for k, m in enumerate(members):
            # 半径：按重要性排序的序号开方 → 核心密、边缘疏
            frac = (k + 0.5) / n
            r = R * (0.16 + 0.84 * math.sqrt(frac))
            theta = k * 2.399963229728653        # 黄金角
            # 轻微 z 扰动，保留一点体积感（内部视图仍以平面为主）
            z = math.sin(k * 1.7) * R * 0.10
            x = math.cos(theta) * r
            y = math.sin(theta) * r * 0.92
            pos[m] = (x, y, z)
            out_nodes.append({
                "id": m, "t": ntype.get(m, "Concept"),
                "d": G.degree(m), "r": round(pr.get(m, 0.0), 8),
                "x": round(x, 2), "y": round(y, 2), "z": round(z, 2),
            })

        # ── 内部边 ──
        mset = set(members)
        seen = set()
        out_edges = []
        for m in members:
            for nb in G.neighbors(m):
                if nb not in mset:
                    continue
                key = (m, nb) if m < nb else (nb, m)
                if key in seen:
                    continue
                seen.add(key)
                # 用真实类型（2026-09-15 修：此前硬编码 "RELATED"，
                # 导致 18 个社区 16569 条边全是 RELATED，社区详情看不出关系语义）。
                # 同一对实体若有多条不同类型的边，**每种类型各出一条** ——
                # 否则会把 CONTRADICTS 这类少数类型挤掉（这正是当初丢信息的地方）。
                tys = pair_types.get(key) or ["RELATED_TO"]
                for ty in tys:
                    out_edges.append([m, nb, ty])
                    if len(out_edges) >= ARGS.max_edges:
                        break
                if len(out_edges) >= ARGS.max_edges:
                    break
            if len(out_edges) >= ARGS.max_edges:
                break

        payload = {
            "id": cloud["id"],
            "name": cloud["name"],
            "color": cloud["color"],
            "size": cloud["size"],
            "entityCount": cloud["entityCount"],
            "typeCount": cloud.get("typeCount", {}),
            "core": cloud.get("core", []),
            "meta": {"nodes": len(out_nodes), "edges": len(out_edges),
                     "radius": round(R, 1), "shown": len(members),
                     "total": cloud["entityCount"]},
            "nodes": out_nodes,
            "edges": out_edges,
        }
        (OUT / f"comm-{i}.json").write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8")
        index.append({"i": i, "id": cloud["id"], "name": cloud["name"],
                      "nodes": len(out_nodes), "edges": len(out_edges),
                      "radius": round(R, 1)})
        if i < 6 or i % 6 == 0:
            print(f"   comm-{i:<2d} {cloud['name'][:30]:30s} "
                  f"{len(out_nodes):5d} 节点 {len(out_edges):5d} 边  R={R:.0f}")

    (OUT / "community-index.json").write_text(
        json.dumps({"communities": index}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"\n已写出 {len(index)} 个 comm-*.json + community-index.json")


if __name__ == "__main__":
    main()
