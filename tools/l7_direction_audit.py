"""L7 候选：关系方向的独立审计层。

【为什么需要它】
  `tools/verify_direction.py` 实测：在本体 12 类非对称关系上，让 L5 判别器
  比较正/反向 hypothesis，正向只比反向平均高 +0.126，阈值 0.67 下
  **只有 23.9% 的方向反转会被拦住**。也就是说 L5 不是方向校验层，
  而 L3（端点是否属于已知实体集）和 L4（谓词是否在 15 类内）根本不看方向
  —— **六层里没有任何一层能可靠发现方向错误**。
  这在 T3.1 刚做过全量方向归一之后尤其危险：归一本身错了也没人会发现。

【怎么审（数据驱动，不凭空设计本体约束）】
  不预设"哪类关系该朝哪个方向"，而是**从数据里读出主流**：
  对每个关系类型统计 (源类型 → 目标类型) 的分布，取占比最高的类型对为主导。
  若某条边的类型对 (X → Y) 不是主导，而它的反向 (Y → X) **恰好是主导**，
  则该边被标为「疑似方向反转」——这是可机器复现的判据，不涉及主观判断。

  局限必须说清：一条边与主流方向相反**不等于**它错了（少数正确用法存在）；
  也可能源实体在该类型下本就少见。所以输出的是**审计队列**，不是判决。

用法：
  python tools/l7_direction_audit.py                 # 全部关系类型
  python tools/l7_direction_audit.py --min-support 30
"""
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

VERIFY = Path(r"D:\kg-demo\data\quality\relation-verify-all.json")
NEBULA = Path(r"D:\kg-demo\web\kg-data\nebula.json")
OUT = Path(r"D:\kg-demo\data\quality\l7-direction-audit.json")


def parse_args():
    ap = argparse.ArgumentParser(description="关系方向审计（L7 候选）")
    ap.add_argument("--min-support", type=int, default=30,
                    help="某关系类型至少多少条边才做方向审计")
    ap.add_argument("--dominance", type=float, default=0.60,
                    help="主导类型对的最低占比；低于此值说明该关系方向本身不固定")
    ap.add_argument("--show", type=int, default=12)
    return ap.parse_args()


ARGS = parse_args()


def main():
    nb = json.loads(NEBULA.read_text(encoding="utf-8"))
    ntype = {n["id"]: n.get("t") or "Concept" for n in nb["nodes"]}
    print(f"节点类型表：{len(ntype)} 个")

    d = json.loads(VERIFY.read_text(encoding="utf-8"))
    edges = d["edges"]
    print(f"边：{len(edges)} 条")

    # 按关系类型收集端点类型对（只保留两端类型都已知的边）
    pairs = defaultdict(Counter)
    known = defaultdict(list)
    missing = 0
    for e in edges:
        st = ntype.get(e["source"])
        tt = ntype.get(e["target"])
        if st is None or tt is None:
            missing += 1
            continue
        pairs[e["type"]][(st, tt)] += 1
        known[e["type"]].append(e)
    print(f"两端类型已知的边：{sum(len(v) for v in known.values())}"
          f"（{missing} 条因端点不在 nebula 节点表里被跳过）\n")

    report = {"min_support": ARGS.min_support, "dominance": ARGS.dominance,
              "skipped_missing_type": missing, "by_type": {}}
    flagged_total = 0
    print("=" * 96)
    print(f"{'关系类型':16s} {'边数':>6s} {'主导类型对':38s} {'占比':>7s}"
          f" {'反向=主导':>9s} {'审计率':>8s}")
    print("-" * 96)

    for rtype, cnt in sorted(pairs.items(), key=lambda kv: -sum(kv[1].values())):
        total = sum(cnt.values())
        if total < ARGS.min_support:
            continue
        (dom, dom_n) = cnt.most_common(1)[0]
        dom_pct = dom_n / total
        rev = (dom[1], dom[0])
        rev_n = cnt.get(rev, 0)

        # 疑似反转：本边类型对不是主导，但它的反向是主导
        flipped = 0
        for e in known[rtype]:
            st, tt = ntype[e["source"]], ntype[e["target"]]
            if (st, tt) != dom and (tt, st) == dom:
                flipped += 1

        note = "方向不固定" if dom_pct < ARGS.dominance else ""
        print(f"{rtype:16s} {total:6d} {str(dom):38s} {dom_pct*100:6.1f}%"
              f" {rev_n:9d} {flipped/total*100:7.1f}%  {note}")
        report["by_type"][rtype] = {
            "n": total, "dominant_pair": list(dom), "dominant_pct": round(dom_pct, 4),
            "reverse_pair_count": rev_n, "suspected_flipped": flipped,
            "suspected_flipped_pct": round(flipped / total, 4),
            "direction_fixed": dom_pct >= ARGS.dominance,
            "top_pairs": [[list(k), v] for k, v in cnt.most_common(5)],
        }
        flagged_total += flipped

    print("-" * 96)
    print(f"疑似方向反转的边合计：{flagged_total}")

    # 列出最可疑的具体边（供人工抽查）
    print(f"\n{'='*96}\n── 疑似方向反转的边（示例，按 L5 分数降序）──")
    sus = []
    for rtype, info in report["by_type"].items():
        if not info["direction_fixed"]:
            continue
        dom = tuple(info["dominant_pair"])
        for e in known[rtype]:
            st, tt = ntype[e["source"]], ntype[e["target"]]
            if (st, tt) != dom and (tt, st) == dom:
                sus.append((float(e["entail_prob"]), rtype, e["source"], st,
                            e["target"], tt))
    sus.sort(reverse=True)
    for p, rtype, s, st, t, tt in sus[:ARGS.show]:
        print(f"  L5={p:.3f}  {rtype:14s} {s[:26]:26s}({st:10s}) → {t[:26]:26s}({tt})")
    print(f"  … 共 {len(sus)} 条")

    report["suspected_total"] = flagged_total
    report["suspected_with_high_l5"] = sum(1 for x in sus if x[0] >= 0.67)
    print(f"\n其中 L5 已通过（≥0.67）的：{report['suspected_with_high_l5']} 条"
          f" —— 这些是方向可能错、但 L5 拦不住的边")

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告已写出: {OUT}")


if __name__ == "__main__":
    main()
