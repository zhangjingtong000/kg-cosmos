# -*- coding: utf-8 -*-
"""L5 域内效度抽检 · 第三步：把人工盲判标签还原成**本项目分布上**的指标。

【这一步在纠正什么】
  现在对外报的 L5 = 63.7% 是**判别器判通过的比例**（27954/43867），
  它既不是精度也不是召回，而且阈值 0.67 来自官方英文校准集。
  本脚本用人工盲判标签给出真正能写进论文的四个数：

    真实支持率   Σ w·y / Σ w                      —— 全图 43867 条里到底有多少条被原文支持
    PPV（精度）  Σ w·1[p≥0.67]·y / Σ w·1[p≥0.67]  —— 被判通过的边里，有多少条真的被支持
    召回         估计的真阳性中被判通过的比例
    域内 AUC     用 entail_prob 排序、人工标签当真值的加权 AUC
                  （与官方校准集的 0.9851 并列，才看得出差别）

【加权怎么来的】
  样本按 (entail_prob 分箱 × hit) 分层抽出，层内简单随机。
  每条的权重 w = N_h / n_h（该层总体大小 / 该层样本数），
  所有比例都用 Horvitz–Thompson 估计量 Σ w·x / Σ w 还原到总体。
  置信区间用**层内 bootstrap**（每层有放回重抽 n_h 条，1000 次）。

【隐藏探针】
  抽样清单里没有告诉标注者哪些边是「前提里两个端点都没出现」的。
  这一子集人工若大量判「支持」，说明这一轮标注本身不可信 —— 见 §探针。
  这不是苛责标注者：`hit=none` 的边被判通过的正是判别器最可疑的一批输出。

用法：
  python tools/l5_validity_report.py                       # 读 data/quality 下的标签
  python tools/l5_validity_report.py --labels tmp/x.jsonl  # 换一份标签（测试用）
"""
# 路径以本文件为锚点解析，不写死盘符——演示机不一定把仓库放在 D 盘。
from pathlib import Path as _Path
_HERE = _Path(__file__).resolve()
ROOT = _HERE.parent.parent          # tools/*.py 与 src/*.py 都在根目录下一层
import argparse
import json
import math
import random
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(str(ROOT))
Q = ROOT / "data" / "quality"
VERIFY = Q / "relation-verify-all.json"
THRESHOLDS = (0.5, 0.6, 0.67, 0.75, 0.8)


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sheet", default=str(Q / "l5-validity-sample.jsonl"))
    ap.add_argument("--key", default=str(Q / "l5-validity-key.json"))
    ap.add_argument("--labels", default=str(Q / "l5-validity-labels.jsonl"))
    ap.add_argument("--design", default=str(Q / "l5-validity-design.json"))
    ap.add_argument("--boot", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default=str(Q / "l5-validity-report.json"))
    return ap.parse_args()


def auc_weighted(scores, labels, weights):
    """加权 ROC-AUC（Mann–Whitney 形式，处理并列取平均秩）。"""
    pr = sorted(zip(scores, labels, weights), key=lambda x: x[0])
    tot_p = sum(w for s, y, w in pr if y == 1)
    tot_n = sum(w for s, y, w in pr if y == 0)
    if not tot_p or not tot_n:
        return None
    i, num = 0, 0.0
    while i < len(pr):
        j = i
        while j + 1 < len(pr) and pr[j + 1][0] == pr[i][0]:
            j += 1
        wpos = sum(pr[k][2] for k in range(i, j + 1) if pr[k][1] == 1)
        wneg = sum(pr[k][2] for k in range(i, j + 1) if pr[k][1] == 0)
        below_n = sum(pr[k][2] for k in range(0, i) if pr[k][1] == 0)
        num += wpos * (below_n + 0.5 * wneg)
        i = j + 1
    return num / (tot_p * tot_n)


def kappa(pairs):
    """Cohen's κ（二分类：支持 / 非支持）。pairs = [(human_is_pos, disc_is_pos), ...]"""
    n = len(pairs)
    if n == 0:
        return None
    po = sum(1 for a, b in pairs if a == b) / n
    pa = sum(1 for a, _ in pairs if a) / n
    pb = sum(1 for _, b in pairs if b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return None if pe >= 1 else (po - pe) / (1 - pe)


def main():
    A = parse_args()
    sheet = [json.loads(l) for l in Path(A.sheet).read_text(encoding="utf-8").splitlines() if l.strip()]
    key = json.loads(Path(A.key).read_text(encoding="utf-8"))
    lp = Path(A.labels)
    lab = {}
    if lp.exists():
        for l in lp.read_text(encoding="utf-8").splitlines():
            if l.strip():
                r = json.loads(l)
                lab[r["id"]] = r          # 后写覆盖先写：允许改判
    design = json.loads(Path(A.design).read_text(encoding="utf-8"))
    full = json.loads(VERIFY.read_text(encoding="utf-8"))
    edges = full["edges"]
    N = len(edges)

    strata = design["strata"]
    Ns = {k: v["N_h"] for k, v in strata.items()}
    nh = {k: v["n_h"] for k, v in strata.items()}

    rows = []
    for s in sheet:
        r = lab.get(s["id"])
        if not r:
            continue
        h = key[s["id"]]["stratum"]
        rows.append({"id": s["id"], "stratum": h, "w": Ns[h] / nh[h],
                     "y": 1 if r["verdict"] == "support" else 0,
                     "unclear": 1 if r["verdict"] == "unclear" else 0,
                     "reason": r.get("reason") or "",
                     "p": key[s["id"]]["entail_prob"],
                     "pass": key[s["id"]]["pass"], "hit": key[s["id"]]["hit"],
                     "type": key[s["id"]]["type"],
                     "source": s["source"], "target": s["target"]})

    print("=== L5 域内效度报告（人工盲判 → 本项目分布）===")
    print(f"  清单 {len(sheet)} 条，已标 {len(rows)} 条，未标 {len(sheet)-len(rows)} 条")
    if not rows:
        raise SystemExit("还没有任何标注。先运行：\n"
                         "  python tools/l5_validity_sample.py\n"
                         "  python tools/l5_validity_review.py")
    if len(rows) < len(sheet):
        print(f"  ⚠ 只标了 {len(rows)/len(sheet)*100:.0f}%，下面的估计有额外缺失偏差；"
              f"建议标完再引用。")

    u = [r for r in rows if r["unclear"]]
    decided = [r for r in rows if not r["unclear"]]
    print(f"  其中「说不清」{len(u)} 条（{len(u)/len(rows)*100:.1f}%）。"
          f"主口径把「说不清」剔除并重新归一化权重；「说不清」算作不支持的口径另列。")

    def ht(rs, keyf=lambda r: 1.0):
        num = sum(r["w"] * keyf(r) * r["y"] for r in rs)
        den = sum(r["w"] * keyf(r) for r in rs)
        return (num / den) if den else None, num, den

    # ---------- 主指标 ----------
    out = {"n_sheet": len(sheet), "n_labeled": len(rows), "n_unclear": len(u),
           "population": N, "source_labels": str(lp)}
    print("\n── 1. 总体真实支持率（剔「说不清」后归一）──")
    rate, num, den = ht(decided)
    hw = 1.96 * math.sqrt(max(rate * (1 - rate), 1e-9) / max(len(decided), 1))
    print(f"   Σw·y / Σw = {rate*100:.1f}%   （朴素未加权 {sum(r['y'] for r in decided)/len(decided)*100:.1f}%）")
    print(f"   与现行 L5 通过率 63.7% 对比：这是**全图三元组被原文支持的比例**")
    out["true_support_rate"] = round(rate, 4)

    print("\n── 2. 判别器在本项目分布上的性能（阈值 0.67）──")
    pos = [r for r in decided if r["pass"]]
    neg = [r for r in decided if not r["pass"]]
    ppv, _, _ = ht(pos)
    npv, _, _ = ht(neg)
    tp_w = sum(r["w"] * r["y"] for r in pos)
    fn_w = sum(r["w"] * (1 - r["y"]) for r in pos)
    tn_w = sum(r["w"] * (1 - r["y"]) for r in neg)
    fp_w = sum(r["w"] * r["y"] for r in neg)
    recall = tp_w / (tp_w + fp_w) if (tp_w + fp_w) else None
    f1 = (2 * ppv * recall / (ppv + recall)) if (ppv and recall and ppv + recall) else None
    acc = (tp_w + tn_w) / (tp_w + tn_w + fn_w + fp_w)
    a = auc_weighted([r["p"] for r in decided], [r["y"] for r in decided], [r["w"] for r in decided])
    k = kappa([(r["y"] == 1, bool(r["pass"])) for r in decided])
    print(f"   PPV（精度，判通过里真被支持） = {ppv*100:.1f}%   样本内未加权 "
          f"{sum(r['y'] for r in pos)/max(len(pos),1)*100:.1f}%（n={len(pos)}）")
    print(f"   NPV（判不通过里真的不支持）   = {npv*100:.1f}%")
    print(f"   召回（被支持者中被判通过）    = {recall*100:.1f}%")
    print(f"   F1 = {f1:.3f}   加权准确率 = {acc*100:.1f}%")
    print(f"   域内加权 AUC = {a:.4f}      ← 对比官方校准集 {full['calib_roc_auc']}")
    print(f"   判别器 vs 人工 的 Cohen's κ = {k:.3f}"
          f"   （<0.4 轻微 / 0.4–0.6 中等 / >0.6 显著）")
    print(f"   加权混淆：TP {tp_w:.0f}  FP {fn_w:.0f}  TN {tn_w:.0f}  FN {fp_w:.0f}（按总体规模计）")
    out.update({"ppv": round(ppv, 4), "npv": round(npv, 4), "recall": round(recall, 4),
                "f1": round(f1, 4) if f1 else None, "weighted_accuracy": round(acc, 4),
                "in_domain_auc": round(a, 4) if a else None, "cohens_kappa": round(k, 4)})

    # ---------- 3. 阈值灵敏度 ----------
    print("\n── 3. 阈值灵敏度（同一份人工标签重算，无需重新抽样）──")
    print(f"   {'thr':>5} {'全图通过':>9} {'通过率':>7} {'PPV':>7} {'召回':>7} {'F1':>6}")
    sens = []
    for t in THRESHOLDS:
        pop_pass = sum(1 for e in edges if e["entail_prob"] >= t)
        sel = [r for r in decided if r["p"] >= t]
        if not sel:
            continue
        p_t, _, _ = ht(sel)
        tp = sum(r["w"] * r["y"] for r in sel)
        fn = sum(r["w"] * (1 - r["y"]) for r in sel)
        rc = tp / (tp + fn) if (tp + fn) else None
        f = (2 * p_t * rc / (p_t + rc)) if (p_t and rc and p_t + rc) else None
        print(f"   {t:>5} {pop_pass:>9} {pop_pass/N*100:>6.1f}% {p_t*100:>6.1f}% "
              f"{rc*100:>6.1f}% {f:>6.3f}")
        sens.append({"threshold": t, "population_pass": pop_pass,
                     "pass_rate": round(pop_pass / N, 4), "ppv": round(p_t, 4),
                     "recall": round(rc, 4), "f1": round(f, 4) if f else None})
    out["threshold_sensitivity"] = sens
    print("   读法：通过率随阈值单调下降是必然的；要看的是一起变化的 **PPV**。"
          "若 PPV 在 0.5→0.8 之间几乎不动，说明该阈值不是「质量旋钮」而是「数量旋钮」。")

    # ---------- 4. 隐藏探针 ----------
    print("\n── 4. 隐藏探针（检验这一轮标注本身可不可信）──")
    probe = [r for r in decided if r["hit"] == "none"]
    if probe:
        pr = sum(r["y"] for r in probe) / len(probe)
        print(f"   hit=none（前提句里两个端点都没字面出现）n={len(probe)}，"
              f"人工判「支持」{sum(r['y'] for r in probe)} 条 = {pr*100:.1f}%")
        print(f"   期望接近 0%。若 >20%，说明标注者把「差不多的意思」当成了支持，"
              f"或界面误导 —— 该轮标签需复核。")
        out["probe_hit_none_support_rate"] = round(pr, 4)

    # ---------- 5. 代理指标面板 ----------
    print("\n── 5. 代理指标（每一个都独立于判别器权重，看它单独能顶多少）──")
    nk = lambda x: re.sub(r"[^a-z0-9]+", "", (x or "").lower())
    ASY = {"PART_OF", "USES", "PRODUCES", "MEASURES", "EVALUATES", "COMPARES_WITH",
           "SUPPORTS", "CONTRADICTS", "CAUSES", "ENABLES", "APPLIES_TO", "LOCATED_AT",
           "CREATED_BY", "CITES"}

    # 多论文重复提及：同一 (s,t,r) 在 ≥2 篇论文里都抽到
    papers = defaultdict(set)
    for e in edges:
        papers[(e["source"], e["type"], e["target"])].add(e["paper"])
    multi = {k for k, v in papers.items() if len(v) >= 2}
    sheetmap = {s["id"]: s for s in sheet}

    proxies = {
        "hit=both → 预测支持": lambda r: r["hit"] == "both",
        "hit≠none → 预测支持": lambda r: r["hit"] != "none",
        "自环/单字符/数字 → 预测不支持": lambda r: not (
            nk(r["source"]) == nk(r["target"]) and nk(r["source"])
            or len(nk(r["source"])) <= 1 or len(nk(r["target"])) <= 1
            or re.fullmatch(r"[0-9.,%\-]+", (r["source"] or "").strip())
            or re.fullmatch(r"[0-9.,%\-]+", (r["target"] or "").strip())),
        "多论文独立提及(≥2篇) → 预测支持": lambda r: (
            sheetmap[r["id"]]["source"], sheetmap[r["id"]]["type"],
            sheetmap[r["id"]]["target"]) in multi,
        "判别器 pass → 预测支持": lambda r: bool(r["pass"]),
    }
    panel = []
    print(f"   {'代理':<32} {'覆盖':>6} {'命中率':>7} {'与人工一致':>9} {'κ':>7}")
    for name, f in proxies.items():
        sub = [r for r in decided if f(r)]
        if not sub:
            print(f"   {name:<32} {0:>6}")
            continue
        prec = sum(r["y"] for r in sub) / len(sub)
        agree = sum(1 for r in decided if f(r) == (r["y"] == 1)) / len(decided)
        kk = kappa([((f(r) is True), (r["y"] == 1)) for r in decided])
        print(f"   {name:<32} {len(sub)/len(decided)*100:>5.0f}% {prec*100:>6.1f}% "
              f"{agree*100:>8.1f}% {kk:>7.3f}")
        panel.append({"proxy": name, "coverage": round(len(sub) / len(decided), 4),
                      "precision": round(prec, 4), "agreement": round(agree, 4),
                      "kappa": round(kk, 4) if kk is not None else None})
    out["proxy_panel"] = panel

    # ---------- 6. 错误画像 ----------
    print("\n── 6. 判别器假阳性的错误类型（人工给的原因标签）──")
    reasons = Counter(r["reason"] for r in decided if r["pass"] and r["y"] == 0 and r["reason"])
    for k2, v in reasons.most_common():
        print(f"   {k2:<20} {v}")
    if not reasons:
        print("   （没有标注者给「不支持」标原因）")
    out["fp_reason_distribution"] = dict(reasons)

    # ---------- 7. bootstrap CI ----------
    if A.boot:
        rnd = random.Random(A.seed)
        byh = defaultdict(list)
        for r in decided:
            byh[r["stratum"]].append(r)
        boots = defaultdict(list)
        for _ in range(A.boot):
            smp = []
            for h, rs in byh.items():
                smp += [rs[rnd.randrange(len(rs))] for _ in range(len(rs))]
            smp = [dict(r) for r in smp]
            rr = sum(r["w"] * r["y"] for r in smp) / sum(r["w"] for r in smp)
            posb = [r for r in smp if r["pass"]]
            pb = (sum(r["w"] * r["y"] for r in posb) / sum(r["w"] for r in posb)) if posb else None
            boots["rate"].append(rr)
            if pb is not None:
                boots["ppv"].append(pb)

        def ci(v):
            v = sorted(v)
            return (round(v[int(len(v) * .025)], 4), round(v[int(len(v) * .975)], 4))

        print(f"\n── 7. 层内 bootstrap {A.boot} 次 · 95% CI ──")
        print(f"   真实支持率 {rate*100:.1f}%  95%CI [{ci(boots['rate'])[0]*100:.1f}%, {ci(boots['rate'])[1]*100:.1f}%]")
        print(f"   PPV        {ppv*100:.1f}%  95%CI [{ci(boots['ppv'])[0]*100:.1f}%, {ci(boots['ppv'])[1]*100:.1f}%]")
        out["ci"] = {"true_support_rate": ci(boots["rate"]), "ppv": ci(boots["ppv"])}
        print(f"   ⇒ 论文里应写：**L5 判通过的 {sum(1 for e in edges if e['pass'])} 条边中，"
              f"人工核验有 {ppv*100:.1f}%（95%CI {ci(boots['ppv'])[0]*100:.1f}–{ci(boots['ppv'])[1]*100:.1f}%）"
              f"能被原文支持；全图真实支持率 {rate*100:.1f}%。**")

    Path(A.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n机器可读报告: {A.out}")


if __name__ == "__main__":
    main()
