"""把本项目 KG 转成 graphrag-workbench 需要的 4 个数据文件。

graphrag-workbench 的 GraphDataLoader 从 <basePath> 读取：
    entities.json          [{id, human_readable_id, title, type, description,
                             text_unit_ids, frequency, degree}]
    relationships.json     [{id, human_readable_id, source, target, description,
                             weight, combined_degree, text_unit_ids}]
    communities.json       [{id, human_readable_id, community, level, parent,
                             children, title, entity_ids, relationship_ids, ...}]
    community_reports.json [{id, human_readable_id, community, level, title,
                             summary, full_content, rank, ...}]

这样就能**跳过 GraphRAG 与 OpenAI**，直接用真实 KG 看它的 3D 布局效果。

输出：graphrag-workbench/public/api/data/*.json

用法：
  python tools/build_workbench_data.py --top 1500
"""
# 路径以本文件为锚点解析，不写死盘符——演示机不一定把仓库放在 D 盘。
from pathlib import Path as _Path
_HERE = _Path(__file__).resolve()
ROOT = _HERE.parent.parent          # tools/*.py 与 src/*.py 都在根目录下一层
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

SRC = ROOT / "web/kg-data"
DST = ROOT / "graphrag-workbench/public/api/data"


def parse_args():
    ap = argparse.ArgumentParser(description="生成 graphrag-workbench 数据")
    ap.add_argument("--top", type=int, default=1500)
    return ap.parse_args()


ARGS = parse_args()


def main():
    src = SRC / f"force-top{ARGS.top}.json"
    if not src.exists():
        raise SystemExit(f"缺少 {src}，先运行 tools/build_force_data.py --top {ARGS.top}")
    d = json.loads(src.read_text(encoding="utf-8"))
    nodes, links = d["nodes"], d["links"]
    print(f"源: {src.name}  {len(nodes)} 节点 / {len(links)} 边")

    # 度数（已在数据里）
    deg = {n["id"]: n.get("degree", 0) for n in nodes}
    comm_of = {n["id"]: n.get("community", -1) for n in nodes}

    # ── entities.json ──
    entities = []
    for i, n in enumerate(nodes):
        entities.append({
            "id": n["id"],
            "human_readable_id": str(i),
            "title": n["id"],
            "type": n.get("type") or "unnamed",
            "description": f"{n.get('type','?')} · 连接度 {n.get('degree',0)}",
            "text_unit_ids": [],
            "frequency": max(1, n.get("degree", 0)),
            "degree": n.get("degree", 0),
        })

    # ── relationships.json ──
    rels = []
    for i, l in enumerate(links):
        s, t = l["source"], l["target"]
        rels.append({
            "id": f"r{i}",
            "human_readable_id": str(i),
            "source": s,
            "target": t,
            "description": l.get("type") or "RELATED",
            "weight": 1,
            "combined_degree": deg.get(s, 0) + deg.get(t, 0),
            "text_unit_ids": [],
        })

    # ── communities.json ──
    # 只保留有实体的社区；level 用 0（单层），parent=null
    by_comm = defaultdict(list)
    for n in nodes:
        c = n.get("community", -1)
        if c >= 0:
            by_comm[c].append(n["id"])
    # 社区标题用该社区度数最高的几个实体名拼
    communities = []
    reports = []
    for cid in sorted(by_comm, key=lambda k: -len(by_comm[k])):
        members = by_comm[cid]
        core = sorted(members, key=lambda m: -deg.get(m, 0))[:3]
        title = " / ".join(core)
        communities.append({
            "id": f"c{cid}",
            "human_readable_id": str(cid),
            "community": str(cid),
            "level": 0,
            "parent": None,
            "children": [],
            "title": title,
            "entity_ids": members,
            "relationship_ids": [],
            "text_unit_ids": [],
            "period": "",
            "size": len(members),
        })
        reports.append({
            "id": f"report-{cid}",
            "human_readable_id": str(cid),
            "community": str(cid),
            "level": 0,
            "title": title,
            "summary": f"{len(members)} 个实体。核心：{title}",
            "full_content": "",
            "rank": len(members),
            "rank_explanation": "",
            "findings": [],
        })

    # ── 写盘 ──
    DST.mkdir(parents=True, exist_ok=True)
    for name, data in (("entities.json", entities),
                       ("relationships.json", rels),
                       ("communities.json", communities),
                       ("community_reports.json", reports)):
        p = DST / name
        p.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                     encoding="utf-8")
        print(f"  ✅ {name:24s} {len(data):5d} 条  {p.stat().st_size/1024:.0f} KB")

    print(f"\n输出目录: {DST}")
    print(f"  实体 {len(entities)}  关系 {len(rels)}  社区 {len(communities)}")
    print(f"  度数 Top3: " + ", ".join(f"{n['id']}({n['degree']})" for n in
          sorted(nodes, key=lambda x: -x.get('degree', 0))[:3]))


if __name__ == "__main__":
    main()
