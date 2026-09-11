"""金标指标诊断：判定 P=0.26 是口径问题还是质量问题。

背景（2026-09 调研发现）：
  实体 P≈0.26 / R≈0.95 ⇒ 系统输出实体数约为金标的 3.7 倍。这是
  「金标覆盖不全 / 匹配口径漏配」的典型指纹，需在论文定论前证伪。

四项低成本诊断（零 API 成本）：
  D1 规模指纹        |S| / |G| 绝对值与比值、TP/FP/FN 分解
  D2 匹配口径消融    严格 / 仅归一化 / 相似度 / 类型感知 四档，报档间差值
  D3 FP 反查原文     256 个 FP 中有多少带原文证据、实体名是否出现在证据里
  D4 金标污染检验    仲裁新增的「单票翻转」实体被系统命中的比例（评测集泄漏）

用法：
  python tools/diagnose_gold_metrics.py --paper 2012.00456v1 --pages 12
  python tools/diagnose_gold_metrics.py --paper 2401.04507v1 --pages 10 --modes cloud
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, r"D:\kg-demo\src")
from kg_extractor import _names_are_duplicates  # noqa: E402

GOLD_DIR = Path(r"D:\kg-demo\data\gold")
GRAPHS = Path(r"D:\kg-demo\data\corpus\graphs")


def parse_args():
    ap = argparse.ArgumentParser(description="金标指标诊断")
    ap.add_argument("--paper", default="2012.00456v1")
    ap.add_argument("--pages", type=int, default=12)
    ap.add_argument("--exclude-from", type=int, default=0)
    ap.add_argument("--modes", nargs="*", default=["cloud", "local"])
    return ap.parse_args()


ARGS = parse_args()
PAPER = ARGS.paper
PAGES = ARGS.pages
EXCLUDE_FROM = ARGS.exclude_from


def norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def tokens(name: str) -> str:
    """保留词边界的宽松归一（用于'实体名是否出现在证据里'的判定）。"""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", (name or "").lower())).strip()


def in_scope(item) -> bool:
    for ev in item.get("evidence", []) or []:
        if not isinstance(ev, dict):
            continue
        page = ev.get("page")
        if page is None:
            continue
        if EXCLUDE_FROM and page >= EXCLUDE_FROM:
            continue
        if page <= PAGES:
            return True
    return False


def load_channel(mode: str):
    if mode == "cloud":
        path = GRAPHS / f"{PAPER}.json"
        if not path.exists():
            return None
        snap = json.loads(path.read_text(encoding="utf-8"))
        return snap["nodes"], snap["edges"], "已入库云端快照"
    path = GOLD_DIR / f"channel-{mode}-{PAPER}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["nodes"], data["edges"], f"{data.get('model')}"


def evidence_text(node) -> str:
    parts = []
    for ev in node.get("evidence", []) or []:
        if isinstance(ev, dict) and ev.get("text"):
            parts.append(str(ev["text"]))
    return " \n ".join(parts)


def name_in_evidence(name: str, ev_text: str) -> bool:
    """实体名（或其主要词）是否字面出现在自带证据里。"""
    if not ev_text.strip():
        return False
    nt = tokens(name)
    if not nt:
        return False
    hay = tokens(ev_text)
    if nt in hay:
        return True
    # 退一步：去掉停用词后的主词序列全部出现
    words = [w for w in nt.split() if len(w) > 2]
    if words and all(w in hay for w in words):
        return True
    return False


# ── 四档匹配口径 ─────────────────────────────────────────────
def match_strict(sys_name, gold_norm, used):
    k = norm(sys_name)
    return k if (k in gold_norm and k not in used) else None


def match_normalized(sys_name, gold_norm, used):
    return match_strict(sys_name, gold_norm, used)


def match_similarity(sys_name, gold_norm, used):
    k = norm(sys_name)
    if k in gold_norm and k not in used:
        return k
    return next((g for g in gold_norm if g not in used and _names_are_duplicates(k, g)), None)


def match_type_aware(sys_name, sys_type, gold_norm, used):
    k = norm(sys_name)
    cands = [g for g in gold_norm if g not in used]
    if k in gold_norm and k not in used:
        if gold_norm[k].get("type") == sys_type:
            return k
        return None
    for g in cands:
        if gold_norm[g].get("type") != sys_type:
            continue
        if _names_are_duplicates(k, g):
            return g
    return None


SCHEMES = [
    ("strict-exact", "严格精确匹配（原始名逐字相等）", match_strict),
    ("similarity", "相似度匹配（_names_are_duplicates，现有口径）", match_similarity),
    ("type-aware", "类型感知匹配（名字相似且类型一致）", match_type_aware),
]


def run_scheme(fn, sys_nodes, gold_entities, with_type=False):
    gold_norm = {norm(e["name"]): e for e in gold_entities}
    used, tp, type_err = set(), 0, 0
    for n in sys_nodes:
        nm = n.get("name", "")
        if not norm(nm):
            continue
        if with_type:
            hit = fn(nm, n.get("type"), gold_norm, used)
        else:
            hit = fn(nm, gold_norm, used)
        if hit:
            used.add(hit)
            tp += 1
            if n.get("type") != gold_norm[hit].get("type"):
                type_err += 1
    s, g = len(sys_nodes), len(gold_entities)
    p = tp / s if s else 0.0
    r = tp / g if g else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    return {"tp": tp, "fp": s - tp, "fn": g - len(used), "type_errors": type_err,
            "precision": round(p, 4), "recall": round(r, 4), "f1": round(f1, 4)}


def main():
    gold = json.loads((GOLD_DIR / f"gold-{PAPER}.json").read_text(encoding="utf-8"))
    gold_entities = gold["entities"]
    stats = gold.get("stats", {})
    arb = gold.get("arbitration", {}) or {}
    disputed = {d["name"] for d in gold.get("disputed_entities", [])}
    arb_accepted = set(arb.get("accepted", []))

    print(f"=== 金标指标诊断：{PAPER}（{PAGES} 页）===")
    print(f"金标实体 {len(gold_entities)}，其中 disputed {len(disputed)}，"
          f"仲裁接受 {len(arb_accepted)}")
    print(f"金标 stats: accepted={stats.get('entity_accepted')} "
          f"disputed={stats.get('entity_disputed')} final={stats.get('entity_final')}")
    print()

    report = {"paper_id": PAPER, "gold_entities": len(gold_entities), "channels": []}

    for mode in ARGS.modes:
        loaded = load_channel(mode)
        if loaded is None:
            print(f"[skip] 通道 {mode} 无数据\n")
            continue
        nodes, edges, note = loaded
        sys_nodes = [n for n in nodes if in_scope(n)]
        s, g = len(sys_nodes), len(gold_entities)

        print(f"── 通道 {mode} ── {note}")
        # ── D1 规模指纹 ──
        print(f"  D1 规模指纹: |S|={s}  |G|={g}  |S|/|G|={s/g:.2f}"
              f"   （若远大于 1，P 低是分母效应）")

        # ── D2 匹配口径消融 ──
        print(f"  D2 匹配口径消融:")
        abla = {}
        for key, desc, fn in SCHEMES:
            r = run_scheme(fn, sys_nodes, gold_entities, with_type=(key == "type-aware"))
            abla[key] = r
            print(f"       {key:14s} P {r['precision']:.3f}  R {r['recall']:.3f}  "
                  f"F1 {r['f1']:.3f}   (TP {r['tp']} / FP {r['fp']} / FN {r['fn']})")
        print(f"       → 口径间 P 差值 {abla['similarity']['precision'] - abla['strict-exact']['precision']:+.3f}"
              f"，R 差值 {abla['similarity']['recall'] - abla['strict-exact']['recall']:+.3f}")

        # ── D3 FP 反查原文 ──
        gold_norm = {norm(e["name"]): e for e in gold_entities}
        used = set()
        fps = []
        for n in sys_nodes:
            k = norm(n.get("name", ""))
            if not k:
                continue
            if k in gold_norm and k not in used:
                used.add(k)
                continue
            hit = next((gg for gg in gold_norm if gg not in used and _names_are_duplicates(k, gg)), None)
            if hit:
                used.add(hit)
            else:
                fps.append(n)
        with_ev = [n for n in fps if evidence_text(n).strip()]
        name_in = [n for n in with_ev if name_in_evidence(n.get("name", ""), evidence_text(n))]
        print(f"  D3 FP 反查原文: FP {len(fps)} 条")
        print(f"       带原文证据 {len(with_ev)} 条（{len(with_ev)/len(fps)*100:.1f}%）"
              f"，其中实体名字面出现在证据里 {len(name_in)} 条"
              f"（{len(name_in)/len(with_ev)*100 if with_ev else 0:.1f}%）")
        types = {}
        for n in fps:
            types[n.get("type")] = types.get(n.get("type"), 0) + 1
        print(f"       FP 类型分布: {dict(sorted(types.items(), key=lambda x: -x[1]))}")

        # ── D4 金标污染检验（评测集泄漏）──
        # 单独评估：把「仲裁翻转接受」的实体排除后，指标如何变化
        clean_gold = [e for e in gold_entities if e["name"] not in arb_accepted]
        r_clean = run_scheme(match_similarity, sys_nodes, clean_gold)
        hit_arb = 0
        tmp_used = set()
        gold_norm_all = {norm(e["name"]): e for e in gold_entities}
        for n in sys_nodes:
            k = norm(n.get("name", ""))
            hit = None
            if k in gold_norm_all and k not in tmp_used:
                hit = k
            else:
                hit = next((gg for gg in gold_norm_all if gg not in tmp_used
                            and _names_are_duplicates(k, gg)), None)
            if hit:
                tmp_used.add(hit)
                if gold_norm_all[hit]["name"] in arb_accepted:
                    hit_arb += 1
        print(f"  D4 金标污染检验: 系统命中「仲裁翻转」实体 {hit_arb}/{len(arb_accepted)} 个"
              f"（{hit_arb/len(arb_accepted)*100 if arb_accepted else 0:.0f}%）")
        print(f"       排除这 {len(arb_accepted)} 个后重算: "
              f"P {r_clean['precision']:.3f}  R {r_clean['recall']:.3f}  F1 {r_clean['f1']:.3f}"
              f"   (R 从 {abla['similarity']['recall']:.3f} 变化"
              f" {r_clean['recall'] - abla['similarity']['recall']:+.3f})")
        print()

        report["channels"].append({
            "mode": mode, "note": note, "system": s, "gold": g,
            "size_ratio": round(s / g, 3),
            "ablation": abla,
            "fp": {"total": len(fps), "with_evidence": len(with_ev), "name_in_evidence": len(name_in),
                   "type_dist": types},
            "leakage": {"arb_accepted": len(arb_accepted), "hit_arb": hit_arb,
                        "clean_gold_metrics": r_clean},
        })

    out = GOLD_DIR / f"diagnose-{PAPER}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告已写出: {out}")


if __name__ == "__main__":
    main()
