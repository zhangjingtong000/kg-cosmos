# -*- coding: utf-8 -*-
"""L5 人工盲判 · 清单生成（零模型 / 零 GPU / 不读取任何判别器分数给标注者）。

【为什么需要它】
  全库 43867 条的清单（data/quality/l5-validity-sample.jsonl）只覆盖 17 篇论文，
  而「这一篇的数据好不好」必须在这篇自己的边上抽样。本脚本把单篇 graph.json
  变成两样东西：
    · 静态盲判清单  web/l5-data/<name>-sheet.json   ← 给浏览器直接 fetch（无需起服务）
    · 服务端清单    tmp/l5-single/<name>-sheet.jsonl ← 给 tools/l5_single_review.py
    · 设计摘要      tmp/l5-single/<name>-design.json ← 含层级/探针，**绝不可给标注者**

【两条硬规则（这决定了界面长什么样）】
  1. 清单不含 premise。旧界面把「判别器读到的前提句」单独高亮成一块，
     标注者被引导去判「这句 premise 支不支持」，而不是「原文支不支持这条边」；
     更糟的是 premise 是「同时含两个端点的句子」，标黄位置等于把机器算的
     端点命中程度（hit）泄漏给标注者 ⇒ 锚定 + 破盲同时发生。所以 premise 一律不下发，
     evidence 段落原样给出（保留换行、不做任何高亮）。
  2. 清单不含 entail_prob / pass / hit / stratum。分数只进 design.json（tmp/ 下）。

【分层】
  hit_premise(3) × 类型族(4) = 12 层。hit 与支持率强相关（全库实测
  both 61.3% / one 22.3% / none 9.1%），所以它是必须控制的分层变量；
  类型族是第二维度（产出类/结构类/相关类/低频类）。
  `hit_premise=none` 的层**全取**，作为隐藏探针（期望人工几乎全判不支持）。

用法：
  E:\\Python313\\python.exe tools\\l5_single_sample.py --hash ad690dfb --n 150
  E:\\Python313\\python.exe tools\\l5_single_sample.py --validity        # 全库 200 条转静态
"""
import argparse
import json
import math
import random
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import audit_single_l5 as A                      # noqa: E402  纯函数，无模型加载
from l5_validity_sample import TYPE_ZH           # noqa: E402

Q = ROOT / "data" / "quality"
WEB_DATA = ROOT / "web" / "l5-data"
DESIGN_DIR = ROOT / "tmp" / "l5-single"
SEC_PER_ITEM = 20      # 每条盲判的保守耗时（含翻原文），与 l5_validity_sample.py 一致

FAM = {
    "A_PROD": {"PRODUCES", "CREATED_BY"},
    "B_STRUCT": {"USES", "APPLIES_TO", "PART_OF", "LOCATED_AT", "ENABLES"},
    "C_REL": {"RELATED_TO"},
}
FAM_ZH = {"A_PROD": "产出类", "B_STRUCT": "结构类", "C_REL": "相关类", "D_LOW": "低频类"}


def family(t):
    for k, v in FAM.items():
        if t in v:
            return k
    return "D_LOW"


def _endpoint_hit(text, source, target):
    """端点在这段文本里字面出现了几个（2/1/0）。判据与 audit_single_l5.has 同源。"""
    low = A.nkey(text)
    lowt = (text or "").lower()

    def one(name):
        k = A.nkey(name)
        if k and len(k) > 3 and k in low:
            return True
        toks = [w for w in re.split(r"\W+", (name or "").lower()) if len(w) > 3]
        return bool(toks) and all(w in lowt for w in toks)

    return int(one(source)) + int(one(target))


def allocate(keys, Ns, n, probe_keys, floor=3):
    """比例分配 + 每层下限；探针层全取。返回 {key: n_h}。"""
    fixed = {k: Ns[k] for k in probe_keys if Ns[k] > 0}
    pool = [k for k in keys if Ns[k] > 0 and k not in fixed]
    rem = max(0, n - sum(fixed.values()))
    Npool = sum(Ns[k] for k in pool) or 1
    if not pool or rem <= 0:
        return {**{k: 0 for k in keys}, **fixed}
    base = {k: min(Ns[k], max(min(floor, Ns[k]), int(round(rem * Ns[k] / Npool)))) for k in pool}
    guard = 0
    while sum(base.values()) != rem and guard < 10000:
        guard += 1
        d = rem - sum(base.values())
        if d > 0:
            cand = [k for k in pool if base[k] < Ns[k]]
            if not cand:
                break
            k = max(cand, key=lambda k: Ns[k] / (base[k] + 1))
            base[k] += 1
        else:
            cand = [k for k in pool if base[k] > 1]
            if not cand:
                break
            k = max(cand, key=lambda k: base[k] - rem * Ns[k] / Npool)
            base[k] -= 1
    return {**{k: 0 for k in keys}, **fixed, **base}


def write_sheet(name, sheet, design, note):
    WEB_DATA.mkdir(parents=True, exist_ok=True)
    DESIGN_DIR.mkdir(parents=True, exist_ok=True)
    j = WEB_DATA / f"{name}-sheet.json"
    jl = DESIGN_DIR / f"{name}-sheet.jsonl"
    dj = DESIGN_DIR / f"{name}-design.json"
    j.write_text(json.dumps({"name": name, "note": note, "n": len(sheet),
                             "items": sheet}, ensure_ascii=False), encoding="utf-8")
    jl.write_text("\n".join(json.dumps(s, ensure_ascii=False) for s in sheet), encoding="utf-8")
    dj.write_text(json.dumps(design, ensure_ascii=False, indent=2), encoding="utf-8")
    return j, jl, dj


def threshold_table(n, pi=0.4003, deff=0.768):
    """给定 n，给出 95%CI 半宽与 80% power 下可分辨的最小差（与全库 40.0% 比较）。"""
    hw = 1.96 * math.sqrt(pi * (1 - pi) * deff / n)
    mde = (1.959964 + 0.8416212) * math.sqrt(2 * pi * (1 - pi) * deff / n)
    return round(hw, 4), round(mde, 4)


# ────────────────────────── 单篇 ──────────────────────────
def build_single(h, n, seed, floor):
    gpath = ROOT / "data" / h / "graph.json"
    if not gpath.exists():
        raise SystemExit(f"缺少单篇图 {gpath}")
    g = json.loads(gpath.read_text(encoding="utf-8-sig"))
    edges = g.get("edges") or []
    rows, skipped, dump_idx = [], 0, 0
    for i, e in enumerate(edges):
        t = (e.get("type") or "").upper()
        evs = e.get("evidence") or []
        evs = evs if isinstance(evs, list) else []
        tmpl = A.REL_TEMPLATE.get(t)
        if tmpl is None or not evs:
            skipped += 1
            continue
        text = " ".join((x.get("text") or "") for x in evs)
        # premise 只用于**离线**分层与诊断，绝不下发给标注者
        sent, kind = A.pick_evidence_sentence(text, e.get("source"), e.get("target"))
        if not sent:
            skipped += 1
            continue
        paper = next((x.get("source_name") for x in evs if x.get("source_name")), None)
        page = next((x.get("page") for x in evs if x.get("page")), None)
        rows.append({
            "edge": e, "type": t, "premise": sent, "hit_premise": kind,
            "hit_evidence": _endpoint_hit(text, e.get("source"), e.get("target")),
            "evidence": text, "paper": Path(paper).stem if paper else None,
            "page": page, "idx": i, "dump_idx": dump_idx,
        })
        # dump_idx 与 audit_single_l5.py --dump 的 <hash>-E{i:05d} 一一对应：
        # 两个脚本的跳过条件与遍历顺序完全相同（模板内外 → 有无 evidence → 能否切句）。
        dump_idx += 1
    N = len(rows)
    cells = Counter((r["hit_premise"], family(r["type"])) for r in rows)
    keys = [(h_, f) for h_ in ("both", "one", "none") for f in ("A_PROD", "B_STRUCT", "C_REL", "D_LOW")]
    Ns = {k: cells.get(k, 0) for k in keys}
    probe_keys = [k for k in keys if k[0] == "none"]
    nh = allocate(keys, Ns, n, probe_keys, floor=floor)

    by_cell = {}
    for r in rows:
        by_cell.setdefault((r["hit_premise"], family(r["type"])), []).append(r)
    rnd = random.Random(seed)
    picked = []
    for k in keys:
        if nh.get(k, 0) <= 0 or not by_cell.get(k):
            continue
        picked.extend(rnd.sample(by_cell[k], min(nh[k], len(by_cell[k]))))
    rnd.shuffle(picked)                      # 打乱顺序：同层连续出现会造成锚定

    sheet, meta = [], []
    for i, r in enumerate(picked, 1):
        sid = f"S{i:04d}"
        e = r["edge"]
        sheet.append({
            "id": sid, "paper": r["paper"], "page": r["page"],
            "source": e.get("source"), "target": e.get("target"),
            "type": r["type"], "type_zh": TYPE_ZH.get(r["type"], "（本体外类型）"),
            "hypothesis": A.REL_TEMPLATE[r["type"]].format(s=e.get("source"), t=e.get("target")),
            "evidence": r["evidence"],        # 原样，无高亮、无 premise
        })
        meta.append({"id": sid, "stratum": f"{r['hit_premise']}|{family(r['type'])}",
                     "hit_premise": r["hit_premise"], "hit_evidence": r["hit_evidence"],
                     "type": r["type"], "edge_idx": r["idx"], "dump_idx": r["dump_idx"]})
    hw, mde = threshold_table(len(sheet))
    design = {
        "name": h, "graph": str(gpath), "population": N, "skipped": skipped,
        "sampled": len(sheet), "seed": seed, "floor": floor,
        "strata": {f"{k[0]}|{k[1]}": {"N_h": Ns[k], "n_h": nh.get(k, 0),
                                      "w_h": round(Ns[k] / nh[k], 4) if nh.get(k, 0) else None}
                   for k in keys},
        "family_zh": FAM_ZH,
        "probe_hit_none": [m["id"] for m in meta if m["hit_premise"] == "none"],
        "hit_evidence_dist": dict(Counter(m["hit_evidence"] for m in meta)),
        "per_item": meta,
        "ci_halfwidth_95": hw, "mde80_vs_0.4003": mde,
        "est_minutes": round(len(sheet) * SEC_PER_ITEM / 60, 1),
        "has_discriminator_scores": False,
        "note": "仅本文件含分层与探针，绝不可给标注者。单篇无 entail_prob（见 2506.09566v1 不在全库 17 篇内）。",
    }
    return sheet, design, N, skipped


# ────────────────────────── 全库既有清单转静态 ──────────────────────────
def build_validity():
    src = Q / "l5-validity-sample.jsonl"
    items = [json.loads(l) for l in src.read_text(encoding="utf-8").splitlines() if l.strip()]
    d = json.loads((Q / "l5-validity-design.json").read_text(encoding="utf-8"))
    key = json.loads((Q / "l5-validity-key.json").read_text(encoding="utf-8"))
    sheet = [{"id": s["id"], "paper": s["paper"], "page": s.get("page"),
              "source": s["source"], "target": s["target"], "type": s["type"],
              "type_zh": s.get("type_zh") or TYPE_ZH.get(s["type"], ""),
              "hypothesis": s["hypothesis"], "evidence": s.get("evidence") or ""}
             for s in items]            # 剥离 premise：去掉锚定与 hit 泄漏
    # per_item 只进 design（tmp/），供报告做「人判端点数 vs 机器端点命中」的标注质量校验
    per_item = [{"id": s["id"], "stratum": key[s["id"]]["stratum"],
                 "hit_premise": key[s["id"]]["hit"],
                 "hit_evidence": _endpoint_hit(s.get("evidence") or "", s["source"], s["target"]),
                 "type": s["type"]} for s in items if s["id"] in key]
    hw, mde = threshold_table(len(sheet))
    design = {
        "name": "validity", "population": d["population"], "sampled": len(sheet),
        "strata": d["strata"], "probe_hit_none": d["probe_hit_none"],
        "per_item": per_item,
        "hit_evidence_dist": dict(Counter(m["hit_evidence"] for m in per_item)),
        "ci_halfwidth_95": hw, "mde80_vs_0.4003": mde,
        "est_minutes": round(len(sheet) * SEC_PER_ITEM / 60, 1),
        "has_discriminator_scores": True,
        "key": str(Q / "l5-validity-key.json"),
        "note": "id 与 data/quality/l5-validity-key.json 对齐；层权重见其 design。",
    }
    return sheet, design


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hash", default="", help="单篇图目录名，如 ad690dfb")
    ap.add_argument("--validity", action="store_true", help="把全库 200 条既有清单转成静态盲判清单")
    ap.add_argument("--n", type=int, default=150)
    ap.add_argument("--seed", type=int, default=20260914)
    ap.add_argument("--floor", type=int, default=3, help="非探针层最低配额")
    ap.add_argument("--pdf", action="store_true", help="把用到的 PDF 复制进 web/l5-data/")
    a = ap.parse_args()
    if not a.hash and not a.validity:
        raise SystemExit("要么 --hash <单篇>，要么 --validity")

    if a.validity:
        sheet, design = build_validity()
        j, jl, dj = write_sheet("validity", sheet, design, "L5 全库 200 条盲判清单（已剥离 premise）")
        print(f"=== 全库清单转静态 ===\n  {len(sheet)} 条 → {j}")
        hw, mde = design["ci_halfwidth_95"], design["mde80_vs_0.4003"]
        print(f"  95%CI 半宽 ±{hw*100:.1f}pp   MDE(80% power) {mde*100:.1f}pp   预计 {design['est_minutes']:.0f} 分钟")
        papers = sorted({s["paper"] for s in sheet})
        print(f"  覆盖 {len(papers)} 篇论文（翻原文需把 corpus 里的 PDF 放进 web/l5-data/，或用 --pdf）")
        if a.pdf:
            import shutil
            WEB_DATA.mkdir(parents=True, exist_ok=True)
            n = 0
            for p in papers:
                sp = ROOT / "data" / "corpus" / f"{p}.pdf"
                if sp.exists():
                    shutil.copy2(sp, WEB_DATA / sp.name)
                    n += 1
            print(f"  已复制 {n} 篇 PDF → {WEB_DATA}")
        print(f"  设计摘要（勿给标注者）：{dj}")
        return 0

    sheet, design, N, skipped = build_single(a.hash, a.n, a.seed, a.floor)
    j, jl, dj = write_sheet(a.hash, sheet, design, f"{a.hash} 单篇盲判清单（已剥离 premise）")
    print(f"=== {a.hash} 单篇盲判清单 ===")
    print(f"  图内边 {N} 条（跳过 {skipped}）· 抽出 {len(sheet)} 条")
    for k, v in design["strata"].items():
        if v["N_h"]:
            print(f"    {k:<14} N_h={v['N_h']:>4}  n_h={v['n_h']:>3}  w_h={v['w_h']}")
    print(f"  隐藏探针（hit_premise=none）{len(design['probe_hit_none'])} 条：{design['probe_hit_none']}")
    print(f"  95%CI 半宽 ±{design['ci_halfwidth_95']*100:.1f}pp"
          f"   MDE(80% power, vs 40.0%) {design['mde80_vs_0.4003']*100:.1f}pp"
          f"   预计 {design['est_minutes']:.0f} 分钟")
    print(f"  静态清单 {j}\n  服务端清单 {jl}\n  设计摘要（勿给标注者）{dj}")
    if a.pdf:
        import shutil
        paper = next((s["paper"] for s in sheet if s.get("paper")), None)
        if paper and (ROOT / "data" / "corpus" / f"{paper}.pdf").exists():
            WEB_DATA.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / "data" / "corpus" / f"{paper}.pdf", WEB_DATA / f"{paper}.pdf")
            print(f"  已复制原文 {paper}.pdf → {WEB_DATA}")
    print(f"\n门槛（判 100/150/200 条）：")
    for n in (100, 150, 200, 300):
        hw2, mde2 = threshold_table(n)
        print(f"  n={n:>3}  95%CI ±{hw2*100:.1f}pp  MDE {mde2*100:.1f}pp  {n*SEC_PER_ITEM/60:.0f} 分钟")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
