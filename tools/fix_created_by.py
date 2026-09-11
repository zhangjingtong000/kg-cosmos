"""CREATED_BY 方向一致性诊断与修复方案生成（本地零成本）。

【已确诊问题】
  1. 方向混乱：Triplet Critic 判定该类型 P(entailment) 中位数仅 0.001（全类型最低），
     both 命中率 20.5%（最低）。端点同一性同时出现：
       Person -> Document  1647 条   （作者→作品，正常）
       Document -> Person  1156 条   （作品→作者，方向反了）
       Person -> Method    1154 条
       Method/System/Concept -> Person  约 1200 条（反向）
  2. 语义错误样本：`S. Riedel -> ECML`（会议被当作"被创建物"）
  3. 两路独立判别器都指向它：
       cross-encoder (英文)  mean 0.103 / median 0.001
       Triplet Critic        mean 0.576 / median 0.655 但 both 最低
     ⇒ 交叉确认该类型确有问题。

【本脚本做什么】
  只做**诊断与方案生成**，不改数据。输出：
  - 方向冲突统计（同一实体对是否双向都存在）
  - 端点类型组合 × 方向 的完整交叉表
  - 按「作者应为 Person、被创建物不应是会议/期刊」的规则给出分类：
      keep      方向正确，保留
      reverse   方向反了，应反转
      drop      语义错误（如把会议当被创建物），应删除
      unknown   无法判定，保留待人工
  - 写出可审阅的方案文件，供后续 apply 步骤执行

用法：
  python tools/fix_created_by.py --report
  python tools/fix_created_by.py --report --top 40
"""
import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

GRAPHS = Path(r"D:\kg-demo\data\corpus\graphs")
OUT = Path(r"D:\kg-demo\data\quality")


def parse_args():
    ap = argparse.ArgumentParser(description="CREATED_BY 方向诊断")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--top", type=int, default=20)
    return ap.parse_args()


ARGS = parse_args()


def norm(n: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (n or "").lower())


# 作者侧应为 Person（或 Organization）；被创建物不应是这些"场所/出版载体"
VENUE_HINTS = re.compile(
    r"\b(conference|workshop|symposium|journal|proceedings|meeting|"
    r"ecml|icml|iclr|neurips|nips|acl|emnlp|naacl|aaai|ijcai|kdd|sigir|www|"
    r"cvpr|iccv|eccv|springer|acm|ieee|elsevier|arxiv)\b", re.IGNORECASE)
AUTHOR_SIDE = {"Person", "Organization"}
# 合理的"被创建物"：作品/方法/系统等
CREATABLE = {"Document", "Method", "System", "Tool", "Artifact", "Dataset",
             "Component", "Concept", "Process", "Metric", "Material"}


def load_nodes():
    """name(norm) -> type，跨快照取出现最多的类型。"""
    votes = defaultdict(Counter)
    for f in sorted(GRAPHS.glob("*.json")):
        try:
            snap = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        for n in snap.get("nodes", []):
            k = norm(n.get("name", ""))
            if k:
                votes[k][n.get("type") or "?"] += 1
    return {k: c.most_common(1)[0][0] for k, c in votes.items()}


def main():
    types = load_nodes()
    print(f"=== CREATED_BY 方向诊断（{len(types)} 个唯一实体类型已载入）===\n")

    edges = []
    for f in sorted(GRAPHS.glob("*.json")):
        try:
            snap = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        for e in snap.get("edges", []):
            if (e.get("type") or "") != "CREATED_BY":
                continue
            s, t = e.get("source") or "", e.get("target") or ""
            ks, kt = norm(s), norm(t)
            if not ks or not kt:
                continue
            edges.append({"paper": f.stem, "s": s, "t": t, "ks": ks, "kt": kt,
                          "st": types.get(ks, "?"), "tt": types.get(kt, "?")})
    print(f"CREATED_BY 边总数: {len(edges)}\n")

    # ── 1. 双向冲突检测 ──
    pairs = set((e["ks"], e["kt"]) for e in edges)
    both_way = [(a, b) for (a, b) in pairs if (b, a) in pairs]
    print("── 1. 双向冲突（同一实体对两个方向都存在）──")
    print(f"   有向实体对 {len(pairs)}，双向冲突 {len(both_way)} 对"
          f"（占 {len(both_way)/len(pairs)*100:.1f}%）")
    for a, b in both_way[:ARGS.top]:
        print(f"     {a[:40]:40s} <-> {b[:40]}")
    print()

    # ── 2. 端点类型组合 × 方向 ──
    combo = Counter((e["st"], e["tt"]) for e in edges)
    print("── 2. 端点类型组合 Top 20（源类型 → 目标类型）──")
    print(f"   {'source':14s} -> {'target':14s} {'n':>6s}  方向判读")
    for (a, b), c in combo.most_common(20):
        if a in AUTHOR_SIDE and b in CREATABLE:
            v = "✅ 正确（作者→作品）"
        elif b in AUTHOR_SIDE and a in CREATABLE:
            v = "❌ 反向（应反转）"
        elif b in AUTHOR_SIDE and a in AUTHOR_SIDE:
            v = "⚠️ 作者→作者，可疑"
        else:
            v = "… 待判"
        print(f"   {a:14s} -> {b:14s} {c:6d}  {v}")
    print()

    # ── 3. 分类 ──
    cls = Counter()
    buckets = defaultdict(list)
    for e in edges:
        st, tt, s, t = e["st"], e["tt"], e["s"], e["t"]
        if VENUE_HINTS.search(t) and st in AUTHOR_SIDE:
            k = "drop"          # 把会议/期刊当被创建物
        elif st in AUTHOR_SIDE and tt in CREATABLE:
            k = "keep"
        elif tt in AUTHOR_SIDE and st in CREATABLE:
            k = "reverse"
        elif st in AUTHOR_SIDE and tt in AUTHOR_SIDE:
            k = "unknown"
        else:
            k = "unknown"
        cls[k] += 1
        if len(buckets[k]) < 400:
            buckets[k].append(e)

    print("── 3. 分类结果 ──")
    for k in ("keep", "reverse", "drop", "unknown"):
        c = cls.get(k, 0)
        print(f"   {k:8s} {c:6d}  ({c/len(edges)*100:5.1f}%)")
    print()

    for k in ("drop", "reverse", "unknown"):
        rows = buckets.get(k, [])
        if not rows:
            continue
        print(f"── 样本：{k}（前 {min(ARGS.top, len(rows))} 条）──")
        for e in rows[:ARGS.top]:
            print(f"   [{e['st']:12s} -> {e['tt']:12s}] {e['s'][:38]:38s} => {e['t'][:38]}")
        print()

    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / "created-by-plan.json"
    out.write_text(json.dumps({
        "total": len(edges),
        "bidirectional_pairs": len(both_way),
        "classification": dict(cls),
        "type_combos": [{"source_type": a, "target_type": b, "n": c}
                        for (a, b), c in combo.most_common()],
        "actions": {k: [{"paper": e["paper"], "source": e["s"], "target": e["t"],
                         "source_type": e["st"], "target_type": e["tt"]}
                        for e in buckets.get(k, [])]
                    for k in ("keep", "reverse", "drop", "unknown")},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"方案已写出: {out}")
    print("\n注：本脚本只诊断，不改数据。确认方案后再执行 apply 步骤。")


if __name__ == "__main__":
    main()
