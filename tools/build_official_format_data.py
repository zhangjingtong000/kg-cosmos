"""生成与官方 large-graph 数据集字段对齐的 KG 数据。

【目的】
  用户要求"把 3D Force Graph 的效果一模一样搬过来，不要自己写代码"。
  官方 large-graph 示例用的是：
      nodes: [{ id, user, description }]
      links: [{ source, target }]
      .nodeAutoColorBy('user')
      .nodeLabel(node => `${node.user}: ${node.description}`)
  为了让示例代码**一个字都不用改**，这里把 KG 数据映射成同样的字段名：
      user        ← 社区 id（用于 nodeAutoColorBy 着色）
      description ← 实体类型 + 连接度（用于 nodeLabel 文案）

【输出】web/kg-data/official-format-<tag>.json

用法：
  python tools/build_official_format_data.py --top 1500
  python tools/build_official_format_data.py --full
"""
# 路径以本文件为锚点解析，不写死盘符——演示机不一定把仓库放在 D 盘。
from pathlib import Path as _Path
_HERE = _Path(__file__).resolve()
ROOT = _HERE.parent.parent          # tools/*.py 与 src/*.py 都在根目录下一层
import argparse
import json
from collections import Counter
from pathlib import Path

DATA = ROOT / "web/kg-data"


def parse_args():
    ap = argparse.ArgumentParser(description="生成官方字段格式的 KG 数据")
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--top", type=int, default=1500)
    return ap.parse_args()


ARGS = parse_args()


def main():
    src = DATA / ("force-full.json" if ARGS.full else f"force-top{ARGS.top}.json")
    if not src.exists():
        raise SystemExit(f"缺少 {src}，先运行 tools/build_force_data.py")
    d = json.loads(src.read_text(encoding="utf-8"))
    print(f"源: {src.name}  {len(d['nodes'])} 节点 / {len(d['links'])} 边")

    nodes = []
    for n in d["nodes"]:
        comm = n.get("community", -1)
        nodes.append({
            "id": n["id"],
            # 官方字段：user 用于 nodeAutoColorBy 着色
            "user": f"community-{comm}" if comm >= 0 else "unclustered",
            # 官方字段：description 用于 nodeLabel 文案
            "description": f"{n.get('type', '?')} · 连接度 {n.get('degree', 0)}",
            # 保留原始字段，便于后续按需使用（不影响官方代码）
            "type": n.get("type"),
            "degree": n.get("degree"),
            "community": comm,
        })
    links = [{"source": l["source"], "target": l["target"]} for l in d["links"]]

    out = {
        "nodes": nodes,
        "links": links,
    }
    tag = "full" if ARGS.full else f"top{ARGS.top}"
    p = DATA / f"official-format-{tag}.json"
    p.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")),
                 encoding="utf-8")

    users = Counter(n["user"] for n in nodes)
    print(f"\n已写出 {p} ({p.stat().st_size/1024/1024:.2f} MB)")
    print(f"  nodes {len(nodes)}  links {len(links)}")
    print(f"  user 取值 {len(users)} 种（用于 nodeAutoColorBy），Top5: "
          + ", ".join(f"{k}({v})" for k, v in users.most_common(5)))
    print(f"  样例节点: {json.dumps(nodes[0], ensure_ascii=False)}")


if __name__ == "__main__":
    main()
