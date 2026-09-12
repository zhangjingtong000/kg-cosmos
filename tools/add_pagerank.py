"""为 `web/kg-data/nebula.json` 增补 PageRank 字段（T4.5，本地零成本）。

【为什么是增量而不是重新生成】
  `tools/build_nebula_data.py` 从 Neo4j 读取并**重跑 Louvain**。
  重跑会改变社区编号与布局，使 `web/kg-nebula.html` 的现有引用失效，
  且需要 Neo4j 在线。本脚本只从 nebula.json **已有的节点与边**计算中心性，
  不触碰任何既有字段（布局、社区、类型全部原样保留）。

【写回什么】
  nodes[i].p  —— PageRank（alpha=0.85，无权，无向图）
  meta.pagerank —— 计算参数与被算图的规模，供前端校验

【为什么还要检验与 degree 的相关性】
  T6.4 的着色引擎用「Partition × Ranking」两个维度。
  若 PageRank 与 degree 的 Spearman 相关接近 1，则二者提供同一信息，
  把 Ranking 做成可切换的两档就没有意义——这个检查必须在动手做引擎前做。

用法：
  python tools/add_pagerank.py            # 计算并写回
  python tools/add_pagerank.py --dry-run  # 只报告，不写文件
"""
# 路径以本文件为锚点解析，不写死盘符——演示机不一定把仓库放在 D 盘。
from pathlib import Path as _Path
_HERE = _Path(__file__).resolve()
ROOT = _HERE.parent.parent          # tools/*.py 与 src/*.py 都在根目录下一层
import argparse
import json
from pathlib import Path

import networkx as nx

NEBULA = ROOT / "web/kg-data/nebula.json"


def parse_args():
    ap = argparse.ArgumentParser(description="为 nebula.json 增补 PageRank")
    ap.add_argument("--dry-run", action="store_true", help="只计算不写回")
    ap.add_argument("--alpha", type=float, default=0.85)
    return ap.parse_args()


ARGS = parse_args()


def spearman(xs, ys):
    """Spearman 秩相关（避免引入 scipy 依赖）。"""
    n = len(xs)
    if n < 2:
        return float("nan")

    def ranks(v):
        order = sorted(range(n), key=lambda i: v[i])
        r = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    dx = sum((rx[i] - mx) ** 2 for i in range(n)) ** 0.5
    dy = sum((ry[i] - my) ** 2 for i in range(n)) ** 0.5
    return num / (dx * dy) if dx and dy else float("nan")


def main():
    d = json.loads(NEBULA.read_text(encoding="utf-8"))
    nodes, edges = d["nodes"], d["edges"]
    print(f"载入 {len(nodes)} 节点 / {len(edges)} 边")

    G = nx.Graph()
    G.add_nodes_from(n["id"] for n in nodes)
    ids = set(G)
    kept = 0
    for e in edges:
        s, t = e[0], e[1]
        if s != t and s in ids and t in ids:
            G.add_edge(s, t)
            kept += 1
    print(f"构图：{G.number_of_nodes()} 节点 / {G.number_of_edges()} 边"
          f"（原始边 {len(edges)}，采纳 {kept}）")

    print(f"计算 PageRank (alpha={ARGS.alpha}) ...")
    pr = nx.pagerank(G, alpha=ARGS.alpha, weight=None)

    deg = dict(G.degree())
    prs = [pr[n] for n in G]
    degs = [deg[n] for n in G]

    # ── 冗余性检查（决定 T6.4 的 Ranking 维度是否值得做两档）──
    # 全图 ρ 会被大量 degree=1 的节点拉高（它们的秩几乎只由并列位置决定），
    # 因此必须同时报"高连接子集"上的 ρ —— 那才是可视化真正区分得开的部分。
    rho = spearman(prs, degs)
    hi = [n for n in G if deg[n] >= 10]
    rho_hi = spearman([pr[n] for n in hi], [deg[n] for n in hi]) if len(hi) > 2 else float("nan")

    print(f"\n── PageRank 与 degree 的冗余性检查 ──")
    print(f"  全图 ({len(degs)} 节点)          Spearman ρ = {rho:.4f}")
    print(f"  degree ≥ 10 子集 ({len(hi)} 节点)  Spearman ρ = {rho_hi:.4f}")
    if abs(rho_hi) >= 0.95:
        print("  ⚠️ 高连接子集上 ρ ≥ 0.95 ⇒ 两者在该区间提供同一信息，")
        print("     T6.4 的 Ranking 维度**不应**做成 PageRank/degree 两档切换")
    else:
        print("  ✅ 高连接子集上 ρ < 0.95 ⇒ PageRank 提供了 degree 之外的信息，")
        print("     T6.4 的 Ranking 维度可以做两档切换")

    # 分歧最大的节点：**只在 degree ≥ 20 的节点内比较**，
    # 否则结果全是 degree=1 的并列排序噪声。
    print(f"\n── degree ≥ 20 的节点里，PageRank 排名优于 degree 排名最多者（桥接实体）──")
    sub = [n for n in G if deg[n] >= 20]
    sub_pr = {n: i for i, n in enumerate(sorted(sub, key=lambda x: -pr[x]))}
    sub_deg = {n: i for i, n in enumerate(sorted(sub, key=lambda x: -deg[x]))}
    gap = sorted(sub, key=lambda n: (sub_deg[n] - sub_pr[n]), reverse=True)
    for n in gap[:10]:
        print(f"  {n[:40]:40s} deg={deg[n]:5d}(#{sub_deg[n]:4d})"
              f"  pr={pr[n]:.6f}(#{sub_pr[n]:4d})  Δ={sub_deg[n]-sub_pr[n]:+4d}")

    print(f"\n── PageRank Top 12 ──")
    for n in sorted(G, key=lambda x: -pr[x])[:12]:
        print(f"  {n[:46]:46s} pr={pr[n]:.6f}  deg={deg[n]}")

    if ARGS.dry_run:
        print("\n[dry-run] 未写回文件")
        return

    for n in nodes:
        n["p"] = round(pr.get(n["id"], 0.0), 8)
    d["meta"]["pagerank"] = {
        "alpha": ARGS.alpha,
        "nodes": G.number_of_nodes(),
        "edges": G.number_of_edges(),
        "spearman_vs_degree": round(rho, 4),
        "generated_by": "tools/add_pagerank.py",
    }

    NEBULA.write_text(
        json.dumps(d, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8")
    print(f"\n已写回 {NEBULA}  ({NEBULA.stat().st_size/1024/1024:.2f} MB)")


if __name__ == "__main__":
    main()
