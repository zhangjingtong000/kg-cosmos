# -*- coding: utf-8 -*-
"""L5 人工盲判 · 把两问式标签算回**真实支持率 / PPV / 错误归因**。

【为什么必须有它】
  55.0% / 59.5% 是判别器的通过率，判别器自身 PPV 只有 49.7%；
  单篇要下「这一篇数据好不好」的结论，只能靠人工盲判标签 → 加权还原到该篇总体。
  本脚本吃 web/l5-review.html（两问式）导出的标签，输出：
    · 真实支持率（Horvitz–Thompson 加权）+ 层内 bootstrap 95%CI + 「说不清」双口径
    · 分层明细、隐藏探针（hit_premise=none）支持率
    · 错误归因：Q2「端点都在但关系不成立」= 抽取错误；「端点缺失」= 引错段/定位错误
    · Q2（人判端点在不在） vs 机器 hit_evidence 的一致率 = 本轮标注质量校验
    · --validity 模式下额外给 PPV / NPV / 召回 / F1 / 域内加权 AUC / κ / 阈值灵敏度

用法：
  python tools/l5_single_report.py --hash ad690dfb --labels "C:\\Users\\me\\Downloads\\l5-labels-ad690dfb-2026-09-20.json"
  python tools/l5_single_report.py --validity --labels tmp/l5-single/l5-labels-validity.json
  # 单篇若已用 audit_single_l5.py --dump 落过分数，可加 --key 算该篇 PPV
"""
import argparse
import json
import math
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
from l5_validity_report import auc_weighted, kappa   # noqa: E402  纯函数

Q = ROOT / "data" / "quality"
DESIGN_DIR = ROOT / "tmp" / "l5-single"
THRESHOLDS = (0.5, 0.6, 0.67, 0.75, 0.8)
LIBRARY_SUPPORT = (0.4003, 0.3407, 0.4597)      # 全库 200 条实测：真实支持率与 95%CI
Q2_LABEL = {"both_asserted": "两端点都在·有断言", "both_listed": "两端点都在·仅罗列",
            "one_endpoint": "只有一个端点", "no_endpoint": "两端点都没出现",
            "cannot_tell": "分不清", "skipped": "跳过", "": "未答"}


def load_labels(path):
    txt = Path(path).read_text(encoding="utf-8-sig")
    recs = []
    s = txt.lstrip()
    if s.startswith("[") or s.startswith("{"):
        j = json.loads(txt)
        if isinstance(j, list):
            recs = j
        elif isinstance(j, dict) and isinstance(j.get("labels"), list):
            recs = j["labels"]
        elif "id" in j:
            recs = [j]
        else:
            recs = list(j.values())
    else:
        recs = [json.loads(l) for l in txt.splitlines() if l.strip()]
    out = {}
    for r in recs:
        if not isinstance(r, dict):
            continue
        v = r.get("verdict") or r.get("q1")
        if r.get("id") is None or v not in ("support", "reject", "unclear"):
            continue
        out[r["id"]] = {"verdict": v, "q2": (r.get("q2") or ""), "reason": r.get("reason") or ""}
    return out


def load_key(path, gkey=None):
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        return {}
    if p.suffix.lower() == ".jsonl":          # audit_single_l5.py --dump 的 key 文件
        out = {}
        for l in p.read_text(encoding="utf-8").splitlines():
            if l.strip():
                r = json.loads(l)
                out[r["id"]] = r
        return out
    return json.loads(p.read_text(encoding="utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hash", default="")
    ap.add_argument("--validity", action="store_true")
    ap.add_argument("--labels", required=True)
    ap.add_argument("--sheet", default="")
    ap.add_argument("--design", default="")
    ap.add_argument("--key", default="", help="单篇：audit_single_l5.py --dump 的 key 文件")
    ap.add_argument("--boot", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    name = "validity" if a.validity else a.hash
    if not name:
        raise SystemExit("要用 --hash <单篇> 或 --validity")
    design_p = Path(a.design) if a.design else DESIGN_DIR / f"{name}-design.json"
    sheet_p = Path(a.sheet) if a.sheet else DESIGN_DIR / f"{name}-sheet.jsonl"
    design = json.loads(design_p.read_text(encoding="utf-8"))
    sheet = [json.loads(l) for l in sheet_p.read_text(encoding="utf-8").splitlines() if l.strip()]
    labels = load_labels(a.labels)
    if not labels:
        raise SystemExit(f"{a.labels} 里没有可识别的标注（需要 id + verdict）")

    key_path = a.key or (str(Q / "l5-validity-key.json") if a.validity else "")
    key = load_key(key_path)
    per = {m["id"]: m for m in design.get("per_item", [])}
    Ns = {k: v["N_h"] for k, v in design["strata"].items()}
    nh = {k: v["n_h"] for k, v in design["strata"].items()}

    rows = []
    for s in sheet:
        r = labels.get(s["id"])
        if not r:
            continue
        m = per.get(s["id"], {})
        kk = key.get(s["id"], {})
        h = m.get("stratum") or kk.get("stratum")
        if not h or h not in Ns or not nh.get(h):
            continue
        hp = m.get("hit_premise") or kk.get("hit")
        he = m.get("hit_evidence")
        p = kk.get("entail_prob")
        # 单篇 dump 的 id 形如 <hash>-E00012；靠 design 里的 dump_idx 关联
        if p is None and a.key and m.get("dump_idx") is not None:
            p = key.get(f"{name}-E{m['dump_idx']:05d}", {}).get("entail_prob")
        rows.append({
            "id": s["id"], "stratum": h, "w": Ns[h] / nh[h],
            "y": 1 if r["verdict"] == "support" else 0,
            "unclear": 1 if r["verdict"] == "unclear" else 0,
            "q2": r["q2"], "hp": hp, "he": he, "p": p,
            "pass": (p is not None and p >= 0.67),
            "type": s["type"], "source": s["source"], "target": s["target"],
        })
    if not rows:
        raise SystemExit("没有任何标注能对上清单（id 不匹配？）")

    print(f"=== L5 盲判报告 · {name} ===")
    print(f"  清单 {len(sheet)} 条 · 已标 {len(rows)} 条"
          f"{'  ⚠ 未标完，估计有缺失偏差' if len(rows) < len(sheet) else ''}")
    u = [r for r in rows if r["unclear"]]
    decided = [r for r in rows if not r["unclear"]]
    print(f"  其中「说不清」{len(u)} 条（{len(u)/len(rows)*100:.1f}%）")

    def ht(rs):
        num = sum(r["w"] * r["y"] for r in rs)
        den = sum(r["w"] for r in rs)
        return (num / den if den else None), num, den

    out = {"name": name, "n_sheet": len(sheet), "n_labeled": len(rows),
           "n_unclear": len(u), "labels": str(a.labels),
           "population": design.get("population"), "source_labels": str(a.labels)}

    # ── 1. 真实支持率 ──
    rate, _, _ = ht(decided)
    rate_u0, _, _ = ht(rows)                    # 「说不清」算不支持
    rates = [r["y"] for r in decided]
    naive = sum(rates) / len(rates) if rates else None
    print(f"\n── 1. 真实支持率（加权还原到该篇总体）──")
    print(f"   主口径（剔除「说不清」归一）: {rate*100:.1f}%   朴素未加权 {naive*100:.1f}%")
    print(f"   「说不清」算不支持        : {rate_u0*100:.1f}%")
    out.update({"true_support_rate": round(rate, 4),
                "true_support_rate_unclear_as_reject": round(rate_u0, 4),
                "naive_unweighted": round(naive, 4)})

    # ── 2. 分层明细 ──
    print(f"\n── 2. 分层（层权重来自 design，勿与标注者共享）──")
    byh = defaultdict(list)
    for r in rows:
        byh[r["stratum"]].append(r)
    print(f"   {'层':<16}{'n_h':>5}{'N_h':>7}{'支持':>7}")
    for h, rs in sorted(byh.items()):
        k2 = sum(x["y"] for x in rs)
        print(f"   {h:<16}{len(rs):>5}{Ns[h]:>7}{k2/len(rs)*100:>6.0f}%")

    # ── 3. 探针 ──
    probe = [r for r in decided if r["hp"] == "none"]
    if probe:
        pr = sum(r["y"] for r in probe) / len(probe)
        print(f"\n── 3. 隐藏探针 hit_premise=none ──")
        print(f"   n={len(probe)}，判「支持」{sum(r['y'] for r in probe)} 条 = {pr*100:.1f}%"
              f"（期望≈0；>20% 说明这一轮标注不可信）")
        out["probe_none_support_rate"] = round(pr, 4)

    # ── 4. 两问交叉与错误归因 ──
    print(f"\n── 4. 第 2 问（证据级）分布与错误归因 ──")
    q2c = Counter(r["q2"] or "未答" for r in rows)
    for k2, v in q2c.most_common():
        print(f"   {Q2_LABEL.get(k2, k2):<18}{v:>5} 条 ({v/len(rows)*100:.0f}%)")
    rej = [r for r in decided if r["y"] == 0]
    miss = [r for r in rej if r["q2"] in ("one_endpoint", "no_endpoint")]
    wrong = [r for r in rej if r["q2"] in ("both_asserted", "both_listed")]
    print(f"   不成立 {len(rej)} 条中：端点缺失（引错段/定位错）{len(miss)} 条"
          f" = {len(miss)/max(1,len(rej))*100:.0f}% ｜ 端点齐但关系不成立（抽取错）{len(wrong)} 条"
          f" = {len(wrong)/max(1,len(rej))*100:.0f}%")
    out["q2_distribution"] = dict(q2c)
    out["error_attribution"] = {"reject_n": len(rej), "endpoint_missing": len(miss),
                                "assertion_wrong": len(wrong)}

    # ── 5. 标注质量：人判端点 vs 机器端点命中 ──
    cmp_rows = [r for r in rows if r["he"] is not None and r["q2"] and r["q2"] != "skipped"]
    if cmp_rows:
        def human_he(r):
            return {"both_asserted": 2, "both_listed": 2, "one_endpoint": 1,
                    "no_endpoint": 0}.get(r["q2"])
        ok = sum(1 for r in cmp_rows if human_he(r) == r["he"])
        print(f"\n── 5. 标注质量：人判端点数 vs 机器 hit_evidence ──")
        print(f"   可比 {len(cmp_rows)} 条，一致 {ok} 条 = {ok/len(cmp_rows)*100:.1f}%"
              f"（低一致率说明标注者没在核对端点，或证据段落本身难读）")
        out["q2_vs_evidence_hit_agreement"] = round(ok / len(cmp_rows), 4)

    # ── 6. 判别器性能（只有拿到分数时）──
    scored = [r for r in decided if r["p"] is not None]
    if scored:
        pos = [r for r in scored if r["pass"]]
        neg = [r for r in scored if not r["pass"]]
        ppv, _, _ = ht(pos)
        npv, _, _ = ht(neg)
        tp = sum(r["w"] * r["y"] for r in pos)
        fn = sum(r["w"] * (1 - r["y"]) for r in pos)
        tn = sum(r["w"] * (1 - r["y"]) for r in neg)
        fp = sum(r["w"] * r["y"] for r in neg)
        rec = tp / (tp + fp) if (tp + fp) else None
        f1 = (2 * ppv * rec / (ppv + rec)) if (ppv and rec and ppv + rec) else None
        acc = (tp + tn) / max(tp + tn + fn + fp, 1e-9)
        auc = auc_weighted([r["p"] for r in scored], [r["y"] for r in scored],
                           [r["w"] for r in scored])
        kp = kappa([(r["y"] == 1, bool(r["pass"])) for r in scored])
        print(f"\n── 6. 判别器在{'本篇' if not a.validity else '全库'}分布上的性能（阈值 0.67，n={len(scored)}）──")
        print(f"   PPV（判通过里真被支持）= {ppv*100:.1f}%   NPV = {npv*100:.1f}%")
        print(f"   召回 = {rec*100:.1f}%   F1 = {f1:.3f}   加权准确率 = {acc*100:.1f}%")
        print(f"   域内加权 AUC = {auc:.4f}   判别器 vs 人工 κ = {kp:.3f}")
        out.update({"ppv": round(ppv, 4), "npv": round(npv, 4), "recall": round(rec, 4),
                    "f1": round(f1, 4) if f1 else None, "weighted_accuracy": round(acc, 4),
                    "in_domain_auc": round(auc, 4) if auc else None,
                    "cohens_kappa": round(kp, 4) if kp is not None else None})
        sens = []
        print(f"   {'thr':>5}{'PPV':>8}{'召回':>8}{'F1':>8}")
        for t in THRESHOLDS:
            sel = [r for r in scored if r["p"] >= t]
            if not sel:
                continue
            pt, _, _ = ht(sel)
            tp2 = sum(r["w"] * r["y"] for r in sel)
            fn2 = sum(r["w"] * (1 - r["y"]) for r in sel)
            rc2 = tp2 / (tp2 + fn2) if (tp2 + fn2) else None
            f2 = (2 * pt * rc2 / (pt + rc2)) if (pt and rc2 and pt + rc2) else None
            print(f"   {t:>5}{pt*100:>7.1f}%{rc2*100:>7.1f}%{(f2 or 0):>8.3f}")
            sens.append({"threshold": t, "ppv": round(pt, 4), "recall": round(rc2, 4)})
        out["threshold_sensitivity"] = sens
    else:
        print(f"\n── 6. 判别器性能 ──")
        print("   本篇无 entail_prob（该论文不在全库 relation-verify-all.json 的 17 篇内），"
              "PPV 不适用。")
        print("   要拿到 PPV：E:\\Python313\\python.exe tools\\audit_single_l5.py "
              f"data/{name}/graph.json --dump tmp/l5-single/{name}-dump.jsonl（CPU，需放开模型加载）")
        out["ppv"] = None

    # ── 7. bootstrap CI + 与全库对比 ──
    rnd = random.Random(a.seed)
    boots_rate = []
    for _ in range(a.boot):
        smp = []
        for h, rs in byh.items():
            smp += [rs[rnd.randrange(len(rs))] for _ in range(len(rs))]
        rr = sum(r["w"] * r["y"] for r in smp) / sum(r["w"] for r in smp)
        boots_rate.append(rr)
    boots_rate.sort()
    lo, hi = boots_rate[int(len(boots_rate) * .025)], boots_rate[int(len(boots_rate) * .975)]
    print(f"\n── 7. 层内 bootstrap {a.boot} 次 · 95%CI ──")
    print(f"   真实支持率 {rate*100:.1f}%  95%CI [{lo*100:.1f}%, {hi*100:.1f}%]")
    out["ci_true_support_rate"] = [round(lo, 4), round(hi, 4)]
    if not a.validity:
        lib, llo, lhi = LIBRARY_SUPPORT
        se = (hi - lo) / (2 * 1.96)
        z = (rate - lib) / se if se else 0.0
        print(f"   对比全库（200 条盲判）{lib*100:.1f}% [{llo*100:.1f}, {lhi*100:.1f}]："
              f"差 {(rate-lib)*100:+.1f}pp，z={z:.2f}"
              f"{'（显著）' if abs(z) > 1.96 else '（不显著，差异在抽样误差内）'}")
        out["vs_library"] = {"library_rate": lib, "diff_pp": round((rate - lib) * 100, 1),
                             "z": round(z, 2), "significant": bool(abs(z) > 1.96)}
    mde = design.get("mde80_vs_0.4003")
    print(f"   本清单设计的 MDE(80% power) = {mde*100:.1f}pp" if mde else "")

    out_p = Path(a.out) if a.out else DESIGN_DIR / f"{name}-report.json"
    out_p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n机器可读报告: {out_p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
