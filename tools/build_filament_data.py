"""生成真实社区数据，替换 prototype-semantic-filaments.html 里的固定假数据。

【为什么这么做】
  web/prototype-semantic-filaments.html 是一版**视觉设计正确**的原型：
    · 每个社区画成 FBM 噪声"气体面纱"（3 层，朝向相机）
    · 跨社区关系画成"纤维"（CatmullRom 弯曲 + 粒子尘 + 流动光点），
      按关系数分 strong/medium/weak 三档，粗细与透明度不同
    · 悬浮时高亮当前团 + 邻接团，弱纤维隐藏
  但它用的是写死的 5 个假社区（attention/transformer/...）。
  本脚本产出同样结构的**真实数据**，供其直接替换。

【数据来源】
  Neo4j 当前图库（治理后：15315 节点 / 22892 边）
  + Louvain 社区（模块度 0.6824）

【输出结构（与原型一致）】
  clouds: [{id, name, summary, position:[x,y,z], size, color, entityCount, core:[...]}]
  links:  [{source, target, strength, relationCount}]

用法：
  python tools/build_filament_data.py
  python tools/build_filament_data.py --top 18
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
from graph_store import Neo4jStore  # noqa: E402

OUT = ROOT / "web/kg-data"

# 与原型一致的社区配色（柔和、低饱和，避免过曝）
PALETTE = [
    "#7ecbff", "#d7a7ff", "#ffd27a", "#a7e3b5", "#f3a6b9",
    "#9fd8ff", "#c7b3ff", "#ffc79a", "#9fe0c8", "#ffb3c7",
    "#8fd0e8", "#e0b3ff", "#ffd9a0", "#b3e8c0", "#ffa8b8",
    "#a0c8ff", "#cfc0ff", "#ffe0a8", "#a8e8d0", "#ffc0d0",
]


def parse_args():
    ap = argparse.ArgumentParser(description="生成真实社区纤维数据")
    ap.add_argument("--top", type=int, default=18, help="导出多少个社区（按规模）")
    ap.add_argument("--strong", type=int, default=60, help="strong 档的最小跨社区关系数")
    ap.add_argument("--medium", type=int, default=20, help="medium 档的最小跨社区关系数")
    ap.add_argument("--max-links", type=int, default=120)
    return ap.parse_args()


ARGS = parse_args()


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

    print("Louvain...")
    comms = sorted(nx.community.louvain_communities(G, seed=42), key=len, reverse=True)
    top = comms[:ARGS.top]
    print(f"  社区 {len(comms)} 个，导出前 {len(top)} 个"
          f"（{sum(len(c) for c in top)} 节点）")

    # 节点 → 社区 id
    node2comm = {}
    for ci, c in enumerate(comms):
        for m in c:
            node2comm[m] = ci

    # 跨社区关系计数
    cross = Counter()
    for e in edges:
        s, t = e["source"], e["target"]
        if s == t:
            continue
        a, b = node2comm.get(s), node2comm.get(t)
        if a is None or b is None or a == b:
            continue
        if a >= ARGS.top or b >= ARGS.top:
            continue
        cross[(min(a, b), max(a, b))] += 1
    print(f"  跨社区关系对 {len(cross)} 组")

    # ── 布局：平面上把社区分散开（分层环 + 碰撞松弛 + 自动定相机）──
    # 前一版用黄金角螺旋，r ∝ sqrt(i) 导致**中心区极度扎堆**
    # （近中心团心距仅 2–3，而星云半径 1.4–3.4 必然重叠成一片）。
    # 改为：先按序放入**分层环**（半径只取少数几档，角度错开），
    # 再做碰撞松弛把重叠的团推开，最后依实际包围盒自动推算相机距离。
    n_c = len(top)
    sizes = [1.4 + 2.0 * math.sqrt(len(c) / len(top[0])) for c in top]

    def build_positions(spread_factor):
        """角度错开的分层环：同环内均匀铺开，环间错开半个角步。"""
        rings = [3, 6, 9, 12, 15, 18, 21][:max(1, math.ceil(n_c / 3.5))]
        pos, k = [], 0
        for ri, cap in enumerate(rings):
            if k >= n_c:
                break
            take = min(cap, n_c - k)
            r = spread_factor * (0.62 + 0.95 * ri)
            off = (ri * 0.5) * (2 * math.pi / max(1, take))
            for j in range(take):
                th = off + j * 2 * math.pi / take
                pos.append((math.cos(th) * r, math.sin(th) * r * 0.86,
                            ((k % 5) - 2) * 0.5))
                k += 1
        return pos

    spread_factor = 1.0
    for _attempt in range(24):
        pos = build_positions(spread_factor)
        # 碰撞松弛：把重叠的团沿连线推开
        for _it in range(220):
            moved = False
            for a in range(n_c):
                for b in range(a + 1, n_c):
                    ax, ay, _ = pos[a]; bx, by, _ = pos[b]
                    dx, dy = bx - ax, by - ay
                    d = math.hypot(dx, dy) or 1e-6
                    need = (sizes[a] + sizes[b]) * 1.18   # 留 18% 余量
                    if d < need:
                        push = (need - d) / 2 + 0.02
                        ux, uy = dx / d, dy / d
                        pos[a] = (ax - ux * push, ay - uy * push, pos[a][2])
                        pos[b] = (bx + ux * push, by + uy * push, pos[b][2])
                        moved = True
            if not moved:
                break
        # 检查是否仍严重重叠
        worst = 1.0
        for a in range(n_c):
            for b in range(a + 1, n_c):
                d = math.hypot(pos[a][0] - pos[b][0], pos[a][1] - pos[b][1])
                worst = min(worst, d / max(0.01, (sizes[a] + sizes[b]) * 0.95))
        if worst >= 1.0:
            break
        spread_factor *= 1.16
    print(f"  布局: spread_factor={spread_factor:.2f}  最小间距比={worst:.2f}（≥1 即无重叠）")

    clouds = []
    for i, comm in enumerate(top):
        x, y, z = pos[i]

        members = sorted(comm)
        degs = {m: G.degree(m) for m in members}
        core = sorted(members, key=lambda m: -degs[m])[:10]
        tcount = Counter(G.nodes[m].get("type") for m in members)
        size = sizes[i]
        clouds.append({
            "id": f"c{i}",
            "name": core[0] if core else f"社区 {i+1}",
            "summary": " / ".join(core[1:4]) if len(core) > 1 else "",
            "position": [round(x, 2), round(y, 2), round(z, 2)],
            "size": round(size, 2),
            "color": PALETTE[i % len(PALETTE)],
            "entityCount": len(comm),
            "topType": tcount.most_common(1)[0][0],
            "typeCount": dict(tcount.most_common()),
            "core": [{"id": m, "degree": degs[m]} for m in core],
        })

    # ── 纤维：按跨社区关系数分档 ──
    links = []
    for (a, b), cnt in cross.most_common(ARGS.max_links):
        strength = ("strong" if cnt >= ARGS.strong
                    else "medium" if cnt >= ARGS.medium
                    else "weak")
        links.append({
            "source": f"c{a}", "target": f"c{b}",
            "strength": strength, "relationCount": cnt,
        })
    sc = Counter(l["strength"] for l in links)
    print(f"  纤维 {len(links)} 条: {dict(sc)}")

    # ── 自动推算相机距离（不再靠猜）──
    # 目标：让星云整体占满约 78% 的画面。取水平与垂直两个方向所需距离的较大者。
    xs = [c["position"][0] for c in clouds]
    ys = [c["position"][1] for c in clouds]
    margin = 1.35                                    # 额外留白（含标签高度）
    half_w = (max(xs) - min(xs)) / 2 * margin + max(sizes)
    half_h = (max(ys) - min(ys)) / 2 * margin + max(sizes)
    fov_v = 42.0                                     # 与原型一致
    aspect = 16 / 9
    tan_v = math.tan(math.radians(fov_v / 2))
    cam_v = half_h / tan_v
    cam_h = half_w / (tan_v * aspect)
    cam_z = max(cam_v, cam_h) * 0.95                  # 0.95：稍微贴近，填满 78%
    print(f"  包围盒 X±{half_w:.1f} Y±{half_h:.1f} → 相机距离 {cam_z:.1f}")

    payload = {
        "meta": {
            "communities": len(top), "nodes": sum(len(c) for c in top),
            "links": len(links), "crossPairs": len(cross),
            "source": "Neo4j 治理后图库 + Louvain",
            "cameraZ": round(cam_z, 1),
            "cameraZPortrait": round(cam_z * 2.1, 1),
            "bounds": {"x": [round(min(xs), 1), round(max(xs), 1)],
                       "y": [round(min(ys), 1), round(max(ys), 1)]},
        },
        "clouds": clouds,
        "links": links,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / "filaments.json"
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写出 {p} ({p.stat().st_size/1024:.1f} KB)")

    print("\n社区（前 10）:")
    for c in clouds[:10]:
        print(f"   {c['id']:>3s} {c['name'][:34]:34s} {c['entityCount']:5d} 实体"
              f"  size={c['size']:.2f} pos={c['position']}")
    print("\n纤维（前 12）:")
    for l in links[:12]:
        print(f"   {l['source']} -> {l['target']}  {l['strength']:6s} {l['relationCount']:4d}")


if __name__ == "__main__":
    main()
