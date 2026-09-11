"""关系抽取分层诊断：定位 F1=0.038 的误差来源。

调研（docs/GOLD_METRIC_DIAGNOSIS.md §5.2）指出关系 F1 是三层误差相乘的结果，
本身不说明系统差。本脚本把它拆成可分别归因的四层：

  L3 端点层    端点实体是否被正确抽出（关系错可能只是端点没抽到）
  L4a 谓词层   端点在、谓词类型被换成大类后能否匹配（谓词选择误差）
  L4b 方向层   端点匹配但头尾颠倒（方向误差）
  L5 三元组层  完全严格匹配

并做谓词口径消融：
  - exact      谓词逐字相等
  - normalized 谓词大小写/下划线归一
  - family     谓词映射到语义家族（如 USES/APPLIES_TO→使用族）

同时用 Krippendorff's α 重算标注者间一致性（4 方标注量不等，
Fleiss' κ 要求等量、Cohen's κ 只支持 2 人，α 是唯一规范选择）。

用法：
  python tools/diagnose_relations.py --paper 2012.00456v1 --pages 12
"""
import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, r"D:\kg-demo\src")
from kg_extractor import _names_are_duplicates  # noqa: E402

GOLD_DIR = Path(r"D:\kg-demo\data\gold")
GRAPHS = Path(r"D:\kg-demo\data\corpus\graphs")

# ── 谓词语义家族（基于本体 15 类关系）──
REL_FAMILY = {
    "PART_OF": "structure", "USES": "use", "APPLIES_TO": "use",
    "PRODUCES": "causal", "CAUSES": "causal", "ENABLES": "causal",
    "MEASURES": "measure", "EVALUATES": "measure",
    "COMPARES_WITH": "compare", "SUPPORTS": "stance", "CONTRADICTS": "stance",
    "LOCATED_AT": "location", "CREATED_BY": "attribution", "CITES": "attribution",
    "RELATED_TO": "generic",
}


def parse_args():
    ap = argparse.ArgumentParser(description="关系抽取分层诊断")
    ap.add_argument("--paper", default="2012.00456v1")
    ap.add_argument("--pages", type=int, default=12)
    ap.add_argument("--exclude-from", type=int, default=0)
    ap.add_argument("--modes", nargs="*", default=["cloud", "local"])
    return ap.parse_args()


ARGS = parse_args()
PAPER, PAGES, EXCLUDE_FROM = ARGS.paper, ARGS.pages, ARGS.exclude_from


def norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def norm_rel(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (t or "").lower()).strip("_")


def family(t: str) -> str:
    return REL_FAMILY.get((t or "").upper().strip(), "other")


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
        p = GRAPHS / f"{PAPER}.json"
        if not p.exists():
            return None
        s = json.loads(p.read_text(encoding="utf-8"))
        return s["nodes"], s["edges"], "已入库云端快照"
    p = GOLD_DIR / f"channel-{mode}-{PAPER}.json"
    if not p.exists():
        return None
    d = json.loads(p.read_text(encoding="utf-8"))
    return d["nodes"], d["edges"], f"{d.get('model')}"


# ── 匹配（三级回退：归一精确 → 相似度）──
def build_matcher(gold_names):
    """返回 resolve(name) -> gold_key 或 None。"""
    gn = {norm(n): n for n in gold_names}

    def resolve(name):
        k = norm(name)
        if not k:
            return None
        if k in gn:
            return k
        for g in gn:
            if _names_are_duplicates(k, g):
                return g
        return None

    return resolve


def score_relations(sys_edges, gold_rels, gold_names, scheme):
    """scheme: exact | normalized | family"""
    resolve = build_matcher(gold_names)

    gold_keys = set()
    for g in gold_rels:
        s, t = resolve(g["source"]), resolve(g["target"])
        if s is None or t is None:
            continue  # 金标端点不在实体表里，跳过（数据问题）
        typ = g["type"]
        if scheme == "exact":
            gold_keys.add((s, t, typ))
        elif scheme == "normalized":
            gold_keys.add((s, t, norm_rel(typ)))
        else:
            gold_keys.add((s, t, family(typ)))

    used = set()
    tp = fp = direction = pred_err = 0
    hits = []
    for e in sys_edges:
        s, t = resolve(e.get("source")), resolve(e.get("target"))
        typ = e.get("type")
        if scheme == "exact":
            key = (s, t, typ)
            rkey = (t, s, typ)
        elif scheme == "normalized":
            key = (s, t, norm_rel(typ))
            rkey = (t, s, norm_rel(typ))
        else:
            key = (s, t, family(typ))
            rkey = (t, s, family(typ))

        if s is not None and t is not None and key in gold_keys and key not in used:
            used.add(key); tp += 1
            hits.append((e.get("source"), e.get("target"), typ))
        elif s is not None and t is not None and rkey in gold_keys and rkey not in used:
            used.add(rkey); direction += 1
        else:
            fp += 1
            # 端点对得上、只是谓词不同的情况单列（谓词选择误差）
            if s is not None and t is not None:
                gold_preds = {k[2] for k in gold_keys if k[0] == s and k[1] == t}
                sys_pred = key[2]
                if gold_preds and sys_pred not in gold_preds:
                    pred_err += 1

    fn = len(gold_keys) - len(used)
    n = len(sys_edges)
    p = tp / n if n else 0.0
    r = tp / len(gold_keys) if gold_keys else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    return {"scheme": scheme, "gold_rel": len(gold_keys), "system_rel": n,
            "tp": tp, "fp": fp, "fn": fn, "direction_errors": direction,
            "predicate_errors": pred_err,
            "precision": round(p, 4), "recall": round(r, 4), "f1": round(f1, 4)}


def krippendorff_alpha(units_by_annotator, value_of=None):
    """名义型 Krippendorff's α，支持缺失值。

    units_by_annotator: {annotator: set(unit) 或 {unit: value}}
    对集合型标注，直接把"单元是否被该标注者标注"作为 0/1 值。
    """
    if value_of is None:
        value_of = lambda present: 1 if present else 0  # noqa: E731

    all_units = set()
    for s in units_by_annotator.values():
        all_units |= set(s)

    # 每个单元的标注值列表
    unit_vals = {}
    for u in all_units:
        vals = []
        for ann, s in units_by_annotator.items():
            if u in s:
                vals.append(value_of(True))
            else:
                vals.append(value_of(False))
        unit_vals[u] = vals

    # 观测不一致度 Do
    do_num, do_den = 0.0, 0.0
    for u, vals in unit_vals.items():
        m = len(vals)
        if m < 2:
            continue
        pairs = Counter()
        for i in range(m):
            for j in range(m):
                if i != j:
                    pairs[(vals[i], vals[j])] += 1
        disagree = sum(c for (a, b), c in pairs.items() if a != b)
        do_num += disagree / (m - 1)
        do_den += m

    # 期望不一致度 De（基于边际分布）
    marg = Counter()
    total = 0
    for vals in unit_vals.values():
        for v in vals:
            marg[v] += 1
            total += 1
    if total < 2:
        return None
    de = 0.0
    for a in marg:
        for b in marg:
            if a != b:
                de += marg[a] * marg[b]
    de /= total * (total - 1)

    if do_den == 0:
        return None
    do = do_num / do_den
    if de == 0:
        return None
    return 1 - do / de


def pairwise_jaccard(sets):
    names = list(sets)
    vals = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, b = sets[names[i]], sets[names[j]]
            if not a and not b:
                continue
            vals.append(len(a & b) / len(a | b))
    return sum(vals) / len(vals) if vals else 0.0


def main():
    gold = json.loads((GOLD_DIR / f"gold-{PAPER}.json").read_text(encoding="utf-8"))
    gold_rels = gold["relations"]
    gold_names = [e["name"] for e in gold["entities"]]

    print(f"=== 关系抽取分层诊断：{PAPER}（{PAGES} 页）===")
    print(f"金标 {len(gold_names)} 实体 / {len(gold_rels)} 关系\n")

    report = {"paper_id": PAPER, "channels": []}

    for mode in ARGS.modes:
        loaded = load_channel(mode)
        if loaded is None:
            print(f"[skip] 通道 {mode} 无数据\n")
            continue
        nodes, edges, note = loaded
        # 关系端点先归一到该通道自己的实体表（模拟存储层已做的 canonicalize）
        node_names = [n.get("name", "") for n in nodes if in_scope(n)]
        resolve_self = build_matcher(node_names)
        sys_edges = []
        for e in edges:
            if not in_scope(e):
                continue
            s = resolve_self(e.get("source", "")) or norm(e.get("source", ""))
            t = resolve_self(e.get("target", "")) or norm(e.get("target", ""))
            sys_edges.append({"source": e.get("source"), "target": e.get("target"),
                              "type": e.get("type"), "_s": s, "_t": t})

        print(f"── 通道 {mode} ── {note}（范围内 {len(sys_edges)} 条关系）")

        # L3 端点层：系统关系的端点有多少能被解析到金标实体
        res = build_matcher(gold_names)
        ep_ok = sum(1 for e in sys_edges
                    if res(e["source"]) is not None and res(e["target"]) is not None)
        gold_ep_ok = sum(1 for g in gold_rels
                         if res(g["source"]) is not None and res(g["target"]) is not None)
        print(f"  L3 端点层: 系统关系端点可解析 {ep_ok}/{len(sys_edges)}"
              f"（{ep_ok/len(sys_edges)*100 if sys_edges else 0:.1f}%）"
              f"；金标关系端点可解析 {gold_ep_ok}/{len(gold_rels)}")

        rows = {}
        for scheme in ("exact", "normalized", "family"):
            r = score_relations(sys_edges, gold_rels, gold_names, scheme)
            rows[scheme] = r
            print(f"  L4/L5 {scheme:10s} P {r['precision']:.3f}  R {r['recall']:.3f}"
                  f"  F1 {r['f1']:.3f}   (TP {r['tp']} / FP {r['fp']} / FN {r['fn']}"
                  f" / 方向错 {r['direction_errors']} / 谓词错 {r['predicate_errors']})")
        print(f"       → 谓词归一使 F1 从 {rows['exact']['f1']:.3f} 升到"
              f" {rows['normalized']['f1']:.3f}（{rows['normalized']['f1']-rows['exact']['f1']:+.3f}）"
              f"；归入语义家族后 {rows['family']['f1']:.3f}"
              f"（{rows['family']['f1']-rows['exact']['f1']:+.3f}）")
        print()

        report["channels"].append({
            "mode": mode, "note": note, "system_rel": len(sys_edges),
            "endpoint_resolvable": ep_ok, "layers": rows,
        })

    # ── 标注者间一致性：Jaccard vs Krippendorff's α ──
    pref = "annotation2-" if PAPER.startswith("2401") else "annotation-"
    ann_files = sorted(GOLD_DIR.glob(f"{pref}*.json"))
    anns = {}
    for f in ann_files:
        d = json.loads(f.read_text(encoding="utf-8"))
        anns[d.get("annotator", f.stem)] = {
            "entities": {norm(e["name"]) for e in d["entities"] if norm(e["name"])},
            "relations": {(norm(r["source"]), norm(r["target"]), r["type"])
                          for r in d.get("relations", [])},
        }
    if anns:
        print("── 标注者间一致性 ──")
        ents = {k: v["entities"] for k, v in anns.items()}
        rels = {k: v["relations"] for k, v in anns.items()}
        pj_e = pairwise_jaccard(ents)
        pj_r = pairwise_jaccard(rels)
        print(f"  实体: 成对集合相似度 {pj_e*100:.1f}%"
              f"   Krippendorff α {krippendorff_alpha(ents):.4f}")
        print(f"  关系: 成对集合相似度 {pj_r*100:.1f}%"
              f"   Krippendorff α {krippendorff_alpha(rels):.4f}")
        print(f"  （α 用 0/1 值域：单元被该标注者收录=1，未收录=0；支持各标注者标注量不等）")
        report["agreement"] = {
            "annotators": list(anns), "pairwise_jaccard_entity": round(pj_e, 4),
            "krippendorff_alpha_entity": krippendorff_alpha(ents),
            "pairwise_jaccard_relation": round(pj_r, 4),
            "krippendorff_alpha_relation": krippendorff_alpha(rels),
        }
        print()

    out = GOLD_DIR / f"diagnose-rel-{PAPER}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告已写出: {out}")


if __name__ == "__main__":
    main()
