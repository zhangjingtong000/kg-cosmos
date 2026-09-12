"""RELATED_TO 泛关系诊断 + GraphRAG prune_graph 适配性检验（零 API 成本）。

背景（调研结论，需本地验证后才能照搬）：
  GraphRAG main 分支新增 index/operations/prune_graph.py，默认
  min_edge_weight_pct = 40 —— 砍掉权重低于第 40 百分位的边。
  其假设是「低权重 = 噪声」。

  本项目的 RELATED_TO 有两种可能，处理方式完全相反：
    · 权重要是「偏高」（高频共现）→ 剪枝保它不砍它
    · 权重要是「偏低」（单篇单次）→ 剪枝能砍掉一大批
  所以必须先统计分布，再决定是否照搬。**不测就照搬是本次复盘中要避免的错误。**

同时检验「RELATED_TO 是不是 hub 的副产物」：
  remove_ego_nodes 思路 —— 移除最高度节点后，看 RELATED_TO 是否大量消失。

数据源（全部本地，零成本）：
  data/corpus/graphs/<paper>.json   每篇论文的抽取快照（等价于 GraphRAG 的 relationships 表）

用法：
  python tools/probe_related_to.py
  python tools/probe_related_to.py --top 30
"""
# 路径以本文件为锚点解析，不写死盘符——演示机不一定把仓库放在 D 盘。
from pathlib import Path as _Path
_HERE = _Path(__file__).resolve()
ROOT = _HERE.parent.parent          # tools/*.py 与 src/*.py 都在根目录下一层
import argparse
import json
import re
import statistics as st
from collections import Counter, defaultdict
from pathlib import Path

GRAPHS = ROOT / "data/corpus/graphs"
OUT = ROOT / "data/gold/probe-related-to.json"


def parse_args():
    ap = argparse.ArgumentParser(description="RELATED_TO 泛关系诊断")
    ap.add_argument("--top", type=int, default=20, help="打印前 N 条明细")
    return ap.parse_args()


ARGS = parse_args()


def norm(n: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (n or "").lower())


def load_all():
    """读全部快照；若快照无 weight，则在本脚本内按 GraphRAG 口径重算。

    GraphRAG 口径（graph_extractor.py 原文）：
      同一 (source, target) 再现时 weight += 本次得分；首次默认 1.0
    本项目抽取阶段未记录 per-edge 分数，故 weight 用「跨 chunk/跨论文出现次数」
    代理 —— 这正是 GraphRAG 里 weight 累加的实际语义（重复出现 → 权重高）。
    """
    edges, nodes = [], {}
    files = sorted(GRAPHS.glob("*.json"))
    for f in files:
        try:
            snap = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  [skip] {f.name}: {e}")
            continue
        paper = f.stem
        for n in snap.get("nodes", []):
            key = norm(n.get("name", ""))
            if key:
                nodes[key] = n
        for e in snap.get("edges", []):
            s, t = norm(e.get("source", "")), norm(e.get("target", ""))
            if not s or not t:
                continue
            edges.append({"paper": paper, "s": s, "t": t,
                          "type": e.get("type") or "UNKNOWN",
                          "raw_type": e.get("type")})
    return files, nodes, edges


def main():
    files, nodes, edges = load_all()
    print(f"=== RELATED_TO 泛关系诊断（{len(files)} 篇快照 / "
          f"{len(edges)} 条记录 / {len(nodes)} 个唯一实体名）===\n")

    # ── 1. 权重代理：同一 (s,t,type) 的出现次数 vs 同一 (s,t) 的不同类型数 ──
    by_triple = Counter()
    by_pair = defaultdict(set)
    for e in edges:
        by_triple[(e["s"], e["t"], e["type"])] += 1
        by_pair[(e["s"], e["t"])].add(e["type"])

    type_counts = Counter(e["type"] for e in edges)
    total = len(edges)

    print("── 1. 关系类型分布（Top 15）──")
    for t, c in type_counts.most_common(15):
        print(f"   {t:20s} {c:7d}  {c/total*100:5.1f}%")
    related = type_counts.get("RELATED_TO", 0)
    print(f"\n   RELATED_TO 占比 {related/total*100:.1f}%  ({related}/{total})\n")

    # ── 2. (s,t) 冗余：同一实体对是否已有具体关系 ──
    pair_has_specific = {}
    for (s, t), types in by_pair.items():
        pair_has_specific[(s, t)] = any(x != "RELATED_TO" for x in types)
    rt_pairs = [(s, t) for (s, t), types in by_pair.items() if "RELATED_TO" in types]
    redundant = sum(1 for p in rt_pairs if pair_has_specific[p])
    print("── 2. (s,t) 冗余率（R1 冗余删除的依据）──")
    print(f"   含 RELATED_TO 的实体对 {len(rt_pairs)}，其中同时另有具体关系的 "
          f"{redundant} 条 = {redundant/len(rt_pairs)*100 if rt_pairs else 0:.1f}%")
    print(f"   → 这一部分可直接删（同一对已有更具体的关系）\n")

    # ── 3. GraphRAG prune_graph 适配性：RELATED_TO 的「权重」vs 其他类型 ──
    def weights_of(type_name):
        return [by_triple[(e["s"], e["t"], e["type"])]
                for e in edges if e["type"] == type_name]

    print("── 3. GraphRAG prune_graph 适配性检验 ──")
    print("   （weight 代理 = 同一 (s,t,type) 的跨快照出现次数，等价于 GraphRAG 的累加语义）")
    rows = []
    for t in [x for x, _ in type_counts.most_common()]:
        w = weights_of(t)
        if not w:
            continue
        rows.append((t, len(w), st.median(w), st.mean(w), max(w),
                     sum(1 for x in w if x == 1)))
    print(f"   {'type':20s} {'n':>7s} {'median':>7s} {'mean':>7s} {'max':>5s} {'=1占比':>8s}")
    for t, n, med, mean, mx, ones in rows[:16]:
        print(f"   {t:20s} {n:7d} {med:7.1f} {mean:7.2f} {mx:5d} {ones/n*100:7.1f}%")

    rt_w = weights_of("RELATED_TO")
    other_w = [by_triple[(e["s"], e["t"], e["type"])]
               for e in edges if e["type"] != "RELATED_TO"]
    if rt_w and other_w:
        print(f"\n   RELATED_TO 权重中位数 {st.median(rt_w):.1f} "
              f"vs 其他类型 {st.median(other_w):.1f}")
        verdict = ("偏高 → 剪枝不会砍它，GraphRAG 默认参数对本项目无效"
                   if st.median(rt_w) >= st.median(other_w)
                   else "偏低 → 剪枝能砍掉一批，可考虑照搬 min_edge_weight_pct")
        print(f"   判读：RELATED_TO 权重{verdict}")
    print()

    # ── 4. 端点度数集中度：RELATED_TO 是不是 hub 的副产物 ──
    deg_all = Counter()
    deg_rt = Counter()
    for e in edges:
        deg_all[e["s"]] += 1; deg_all[e["t"]] += 1
        if e["type"] == "RELATED_TO":
            deg_rt[e["s"]] += 1; deg_rt[e["t"]] += 1

    print("── 4. RELATED_TO 是否 hub 的副产物 ──")
    print(f"   全部关系涉及 {len(deg_all)} 个端点，RELATED_TO 涉及 {len(deg_rt)} 个端点")
    top_all = [d for _, d in deg_all.most_common(50)]
    top_rt = [d for _, d in deg_rt.most_common(50)]
    print(f"   度数 Top50 端点：全图平均 {st.mean(top_all):.0f} 度，"
          f"其中 RELATED_TO 平均 {st.mean(top_rt):.0f} 条")
    # 移除 top-k hub 后 RELATED_TO 的剩余比例
    for k in (10, 50, 100):
        hubs = {n for n, _ in deg_all.most_common(k)}
        left = sum(1 for e in edges
                   if e["type"] == "RELATED_TO" and e["s"] not in hubs and e["t"] not in hubs)
        base = sum(1 for e in edges if e["type"] == "RELATED_TO")
        print(f"   移除度数 Top{k} hub 后 RELATED_TO 剩 {left}/{base} = {left/base*100:.1f}%")
    print()

    # ── 5. 明细样本（供人工判断）──
    print(f"── 5. RELATED_TO 样本（前 {ARGS.top} 条）──")
    rt_edges = [e for e in edges if e["type"] == "RELATED_TO"]
    seen = set()
    shown = 0
    for e in rt_edges:
        k = (e["s"], e["t"])
        if k in seen:
            continue
        seen.add(k)
        n1, n2 = nodes.get(e["s"], {}), nodes.get(e["t"], {})
        print(f"   {n1.get('name', e['s'])[:42]:42s} -> {n2.get('name', e['t'])[:42]:42s}"
              f"  ({e['paper']})")
        shown += 1
        if shown >= ARGS.top:
            break

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "files": len(files), "edges": total, "unique_nodes": len(nodes),
        "type_counts": dict(type_counts.most_common()),
        "related_to_share": round(related / total, 4) if total else 0,
        "pair_redundancy": {"pairs_with_related_to": len(rt_pairs),
                            "also_has_specific": redundant},
        "weights": [{"type": t, "n": n, "median": med, "mean": round(mean, 3),
                     "max": mx, "share_eq_1": round(ones / n, 4)}
                    for t, n, med, mean, mx, ones in rows],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告已写出: {OUT}")


if __name__ == "__main__":
    main()
