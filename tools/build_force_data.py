"""生成 3D Force Graph 用的图数据（前端友好，按规模分档）。

【为什么分档】
  力导向布局的计算量大致与边数成正比，浏览器里 2 万条边会明显卡顿。
  先给出几档，用实测挑一个"稳定可交互"的规模，而不是凭感觉设限。

【输出】web/kg-data/force-<tag>.json
  nodes: [{id, type, degree, community, paperCount}]
  links: [{source, target, type}]
  meta:  规模与来源信息

用法：
  python tools/build_force_data.py --full        # 全量（22892 边 / 15315 节点）
  python tools/build_force_data.py --top 3000    # 度数最高的 3000 节点
"""
# 路径以本文件为锚点解析，不写死盘符——演示机不一定把仓库放在 D 盘。
from pathlib import Path as _Path
_HERE = _Path(__file__).resolve()
ROOT = _HERE.parent.parent          # tools/*.py 与 src/*.py 都在根目录下一层
import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))          # 为了 import graph_source
from graph_store import Neo4jStore  # noqa: E402,F401  （保留导入：向后兼容）
from graph_source import load_graph  # noqa: E402

OUT = ROOT / "web/kg-data"


def parse_args():
    ap = argparse.ArgumentParser(description="生成 3D Force Graph 数据")
    ap.add_argument("--full", action="store_true", help="导出全量")
    ap.add_argument("--top", type=int, default=0, help="按度数取前 N 个节点")
    ap.add_argument("--tag", default="", help="输出文件名后缀")
    # 2026-09-15：数据源从 Neo4j 换成 data/corpus/graphs/*.json。
    # 默认与 filaments.json 一致地做治理还原（否则社区着色会对不上）。
    ap.add_argument("--no-governed", action="store_true",
                    help="不做 entity_filter 治理还原（与 filaments 保持一致时不要用）")
    return ap.parse_args()


ARGS = parse_args()


def main():
    # ── 数据源（2026-09-15）──────────────────────────────────────────────
    # 这是**第四个**原先硬依赖 Neo4j 的脚本（另外三个是 build_nebula_data /
    # build_filament_data / build_community_details）。那个实例已不存在
    # ⇒ 统一走 `tools/graph_source.py`（从 data/corpus/graphs/*.json 读）。
    # **默认开治理还原**，与 filaments.json 保持一致 —— 否则社区着色会对不上。
    nodes, edges = load_graph(governed=not ARGS.no_governed)
    print(f"载入 {len(nodes)} 节点 / {len(edges)} 边")

    # 度数（按去重后的无向边计）
    deg = Counter()
    peer = defaultdict(set)
    for e in edges:
        s, t = e["source"], e["target"]
        if s == t:
            continue
        deg[s] += 1
        deg[t] += 1
        peer[s].add(t)
        peer[t].add(s)

    # 社区归属（用于着色）：复用 filaments.json 的 18 个社区
    comm_of = {}
    fil = OUT / "filaments.json"
    if fil.exists():
        fil_d = json.loads(fil.read_text(encoding="utf-8"))
        for ci, c in enumerate(fil_d["clouds"]):
            for core in c.get("core", []):
                comm_of.setdefault(core["id"], ci)
        # 用 comm-*.json 补全全部成员
        for i in range(len(fil_d["clouds"])):
            p = OUT / f"comm-{i}.json"
            if p.exists():
                try:
                    d = json.loads(p.read_text(encoding="utf-8"))
                    for n in d.get("nodes", []):
                        comm_of.setdefault(n["id"], i)
                except Exception:
                    pass

    # 选取节点集
    type_of = {n["name"]: (n.get("type") or "Concept") for n in nodes}
    if ARGS.top and not ARGS.full:
        keep = {n for n, _ in deg.most_common(ARGS.top)}
        tag = ARGS.tag or f"top{ARGS.top}"
    else:
        keep = set(type_of)
        tag = ARGS.tag or "full"

    out_nodes = [{
        "id": nm,
        "type": type_of.get(nm, "Concept"),
        "degree": deg.get(nm, 0),
        "community": comm_of.get(nm, -1),
    } for nm in keep]

    out_links = []
    seen = set()
    for e in edges:
        s, t = e["source"], e["target"]
        if s == t or s not in keep or t not in keep:
            continue
        key = (s, t) if s < t else (t, s)
        if key in seen:
            continue
        seen.add(key)
        out_links.append({"source": s, "target": t, "type": e.get("type") or "RELATED"})

    payload = {
        "meta": {"nodes": len(out_nodes), "links": len(out_links),
                 "source": "Neo4j 治理后图库", "tag": tag,
                 "communities": len(set(comm_of.values())) if comm_of else 0},
        "nodes": out_nodes,
        "links": out_links,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / f"force-{tag}.json"
    p.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                 encoding="utf-8")
    print(f"\n已写出 {p} ({p.stat().st_size/1024/1024:.2f} MB)")
    print(f"  节点 {len(out_nodes)}  边 {len(out_links)}  社区着色覆盖 "
          f"{sum(1 for n in out_nodes if n['community'] >= 0)}/{len(out_nodes)}")
    print(f"  度数 Top5: " + ", ".join(f"{n}({d})" for n, d in deg.most_common(5)))


if __name__ == "__main__":
    main()
