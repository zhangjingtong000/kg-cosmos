"""验证 T3.1 的方向归一是否真的提升了语义支持度（而不是改错）。

【为什么这是最关键的验证】
  六层里没有任何一层能可靠发现方向错误（见 tools/verify_direction.py：
  本体 12 类非对称关系上，阈值 0.67 下只有 23.9% 的方向反转会被 L5 拦住）。
  而 T3.1 刚刚用**规则**（AUTHOR → CREATABLE）反转了 2348 条 CREATED_BY 边
  —— 这个改动本身从未被独立验证过。规则说"应该反过来"，
  语义判别器是否同意？本脚本直接回答这个问题。

【为什么这个样本比随机抽样强】
  `created-by-patch.json` 逐条记录了被反转边的 before/after，
  且 `baseline/relation-verify-all.PRE-T3.json` 保留了改动前的 L5 分数。
  同一条边的 premise 不变，只有 hypothesis 的方向变了，
  所以分数差**纯粹**来自方向 —— 这是一次天然的对照实验，
  比 verify_direction.py 的随机抽样更贴近真实使用场景。

用法：
  python tools/l7_verify_t31_direction.py
"""
import json
from pathlib import Path

QUAL = Path(r"D:\kg-demo\data\quality")
PATCH = QUAL / "created-by-patch.json"
PRE = QUAL / "baseline" / "relation-verify-all.PRE-T3.json"
POST = QUAL / "relation-verify-all.json"

THRESHOLD = 0.67


def load_index(path):
    """建 (paper, source, target, type) → entail_prob 索引。"""
    d = json.loads(path.read_text(encoding="utf-8"))
    idx = {}
    for e in d["edges"]:
        if e["type"] != "CREATED_BY":
            continue
        idx[(e["paper"], e["source"], e["target"])] = float(e["entail_prob"])
    return idx


def main():
    patch = json.loads(PATCH.read_text(encoding="utf-8"))
    print(f"补丁：{patch['generated_at']}  mode={patch['mode']}")
    print(f"stats: {json.dumps(patch['stats'], ensure_ascii=False)}\n")

    print("载入 PRE-T3 的 L5 分数（55MB，稍等）…")
    pre = load_index(PRE)
    print(f"  CREATED_BY 边 {len(pre)} 条")
    print("载入当前 L5 分数 …")
    post = load_index(POST)
    print(f"  CREATED_BY 边 {len(post)} 条\n")

    rows = []
    unmatched = 0
    for f in patch["files"]:
        paper = Path(f["file"]).stem
        for c in f["changes"]:
            if c["op"] != "reverse":
                continue
            b, a = c["before"], c["after"]
            k_before = (paper, b["source"], b["target"])
            k_after = (paper, a["source"], a["target"])
            s_before = pre.get(k_before)
            s_after = post.get(k_after)
            if s_before is None or s_after is None:
                unmatched += 1
                continue
            rows.append((b["source"], b["target"], s_before, s_after))

    print(f"成功配对 {len(rows)} 条反转边（{unmatched} 条因索引缺失跳过）\n")
    if not rows:
        print("没有可配对的样本")
        return

    up = sum(1 for _, _, x, y in rows if y > x)
    down = sum(1 for _, _, x, y in rows if y < x)
    same = len(rows) - up - down
    m_before = sum(x for _, _, x, _ in rows) / len(rows)
    m_after = sum(y for _, _, _, y in rows) / len(rows)

    # 阈值下的通过率变化（这是最终有意义的指标）
    pass_before = sum(1 for _, _, x, _ in rows if x >= THRESHOLD)
    pass_after = sum(1 for _, _, _, y in rows if y >= THRESHOLD)

    print("=" * 78)
    print(f"  反转前平均分 {m_before:.4f}  →  反转后平均分 {m_after:.4f}"
          f"   （Δ {m_after-m_before:+.4f}）")
    print(f"  分数上升 {up} 条（{up/len(rows)*100:.1f}%）"
          f" · 下降 {down} 条（{down/len(rows)*100:.1f}%） · 持平 {same} 条")
    print(f"  阈值 {THRESHOLD} 下通过率：{pass_before}/{len(rows)}"
          f"（{pass_before/len(rows)*100:.1f}%）"
          f" → {pass_after}/{len(rows)}（{pass_after/len(rows)*100:.1f}%）"
          f"   Δ {pass_after-pass_before:+d} 条")
    print("=" * 78)

    print("\n── 判读 ──")
    if m_after > m_before and up > down:
        print("  方向归一**得到语义判别器支持**：反转后平均分上升、")
        print("  且上升的条数多于下降的。T3.1 的方向约定与'原文怎么说的'一致。")
    elif m_after < m_before and down > up:
        print("  ⚠️ 方向归一**与语义判别器相悖**：反转后平均分下降、")
        print("  下降条数多于上升。需要复核 T3.1 的 AUTHOR → CREATABLE 约定")
        print("  是否与原文表述习惯相反。")
    else:
        print("  方向归一与语义分数**基本无关**：分数升降条数接近，")
        print("  说明 L5 对这类方向变化不敏感（与 verify_direction.py 的结论一致）。")

    # 反例：反转后掉到阈值以下的边（这些是"规则反了"的候选）
    worst = sorted(rows, key=lambda r: r[3] - r[2])[:12]
    print("\n── 反转后分数下降最多的 12 条（方向可能被改错的候选）──")
    for s, t, x, y in worst:
        print(f"  {s[:30]:30s} → {t[:30]:30s}   {x:.4f} → {y:.4f}  (Δ{y-x:+.4f})")

    out = {
        "paired": len(rows), "unmatched": unmatched,
        "mean_before": round(m_before, 4), "mean_after": round(m_after, 4),
        "delta": round(m_after - m_before, 4),
        "up": up, "down": down, "same": same,
        "pass_before": pass_before, "pass_after": pass_after,
        "threshold": THRESHOLD,
    }
    (QUAL / "l7-t31-direction-verification.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告已写出: {QUAL / 'l7-t31-direction-verification.json'}")


if __name__ == "__main__":
    main()
