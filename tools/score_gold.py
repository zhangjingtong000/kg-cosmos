"""多通道金标评分：P/R/F1 + 显著性过滤指标 + 四类错误分布。

通道来源：
  cloud  -> data/corpus/graphs/<paper>.json（已入库的云端抽取快照）
  local  -> data/gold/channel-local-<paper>.json（tools/extract_channel.py 产出）
  <其他> -> data/gold/channel-<mode>-<paper>.json

双口径设计（2026-09-10 定）：
  口径 A「知识实体」：以人工/多标注者金标为准，衡量"抽出的东西是不是论文阐述的对象"。
  口径 B「显著性过滤」：统计系统输出中属于论文结构词/元语言/泛指短语的比例。
       该比例不需要额外金标，且直接量化了"抽取器分不清知识实体与论文零件"这一缺口。
        实测云端 230 个 FP 中零幻觉、绝大多数是结构词与泛指短语，故这个指标比
        单纯压低 Precision 更有解释力。

用法：
  python tools/score_gold.py                # 只评云端（已入库快照）
  python tools/score_gold.py local cloud    # 对比两个通道
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, r"D:\kg-demo\src")
from kg_extractor import _names_are_duplicates  # noqa: E402

GOLD_DIR = Path(r"D:\kg-demo\data\gold")
GRAPHS = Path(r"D:\kg-demo\data\corpus\graphs")
PAPER = "2012.00456v1"
PAGES = 12

# ── 显著性指标用的噪声判据（论文结构/元语言/泛指/编号）──
NOISE_EXACT = {
    "related work", "future work", "present work", "previous work", "input", "output",
    "results", "result", "scope", "explanation", "section", "subsection", "appendix",
    "abstract", "introduction", "conclusion", "table number", "table title",
    "table structure", "table header", "column", "columns", "row", "rows", "cell",
    "cells", "cell type", "cell value", "header", "headers", "caption",
    "abbreviation", "abbreviations", "citation style", "author name", "author names",
    "image format", "pdf format", "markup format", "two-column layout",
    "numerical reference", "non-literal value", "human judgment", "scientific research",
    "research direction", "inclusion criterion", "tabular structure", "structured data",
    "high-quality knowledge", "method automation", "semi-automatic", "english",
    "findable", "accessible", "interoperable", "reusable", "field", "ground truth",
    "survey articles", "bibliographic metadata", "domain expert", "domain experts",
    "research problem", "research question", "semantic data", "semi-structured data",
}
NOISE_NUMBERED = re.compile(
    r"^(criterion|rule|step|table|figure|fig\.?|eq\.?|equation|section|column|row)\s*"
    r"(\d+|[a-z]\b)", re.IGNORECASE)
NOISE_GENERIC = re.compile(
    r"^(the|a|an|this|these|those)\s+\w+$|^(domain|research|bibliographic|semantic|"
    r"semi-structured)\s+\w+$", re.IGNORECASE)


def noise_kind(name: str):
    low = (name or "").strip().lower()
    if low in NOISE_EXACT:
        return "meta-term"
    if NOISE_NUMBERED.match((name or "").strip()):
        return "numbered"
    if NOISE_GENERIC.match((name or "").strip()):
        return "generic"
    return None


def norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (name or "").lower())


def in_scope(item) -> bool:
    return any(ev.get("page") is not None and ev["page"] <= PAGES
               for ev in item.get("evidence", []) if isinstance(ev, dict))


def load_channel(mode: str):
    if mode == "cloud":
        path = GRAPHS / f"{PAPER}.json"
        if not path.exists():
            return None
        snap = json.loads(path.read_text(encoding="utf-8"))
        return snap["nodes"], snap["edges"], "已入库云端快照"
    path = GOLD_DIR / f"channel-{mode}-{PAPER}.json"
    if not path.exists():
        print(f"  [skip] 通道 {mode} 无数据（缺 {path.name}）")
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["nodes"], data["edges"], f"{data.get('model')} ({data.get('elapsed_s')}s)"


def score(mode: str, gold: dict):
    loaded = load_channel(mode)
    if loaded is None:
        return None
    nodes, edges, note = loaded

    sys_nodes = [n for n in nodes if in_scope(n)]
    sys_edges = [e for e in edges if in_scope(e)]
    gold_entities = gold["entities"]
    gold_norm = {norm(e["name"]): e for e in gold_entities}

    used, tp_strict, tp_lenient = set(), 0, 0
    type_errors, boundary_errors = [], []
    for n in sys_nodes:
        key = norm(n.get("name", ""))
        if not key:
            continue
        if key in gold_norm and key not in used:
            used.add(key)
            tp_strict += 1
            tp_lenient += 1
            if n.get("type") != gold_norm[key].get("type"):
                type_errors.append({"name": n.get("name"),
                                    "system": n.get("type"), "gold": gold_norm[key].get("type")})
            continue
        hit = next((g for g in gold_norm if g not in used and _names_are_duplicates(key, g)), None)
        if hit:
            used.add(hit)
            tp_lenient += 1
            if n.get("type") != gold_norm[hit].get("type"):
                type_errors.append({"name": n.get("name"),
                                    "system": n.get("type"), "gold": gold_norm[hit].get("type")})
        else:
            near = [g["name"] for g in gold_entities
                    if norm(g["name"]) and (norm(g["name"]) in key or key in norm(g["name"]))]
            if near:
                boundary_errors.append({"name": n.get("name"), "near_gold": near[:3]})

    # 显著性指标：多少输出是论文结构词/泛指
    noise = [(n.get("name"), noise_kind(n.get("name"))) for n in sys_nodes]
    noise = [(nm, k) for nm, k in noise if k]

    fp = len(sys_nodes) - tp_lenient
    fn = len(gold_entities) - len(used)
    p = tp_lenient / len(sys_nodes) if sys_nodes else 0.0
    r = tp_lenient / len(gold_entities) if gold_entities else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0

    gold_rel = {(norm(x["source"]), norm(x["target"]), x["type"]) for x in gold["relations"]}
    rel_used, rel_tp, rel_dir, rel_fp = set(), 0, 0, 0
    for e in sys_edges:
        key = (norm(e.get("source", "")), norm(e.get("target", "")), e.get("type"))
        if key in gold_rel and key not in rel_used:
            rel_used.add(key); rel_tp += 1
        elif (key[1], key[0], key[2]) in gold_rel and (key[1], key[0], key[2]) not in rel_used:
            rel_used.add((key[1], key[0], key[2])); rel_dir += 1
        else:
            rel_fp += 1
    rel_fn = len(gold_rel) - len(rel_used)
    rel_p = rel_tp / len(sys_edges) if sys_edges else 0.0
    rel_r = rel_tp / len(gold_rel) if gold_rel else 0.0
    rel_f1 = 2 * rel_p * rel_r / (rel_p + rel_r) if (rel_p + rel_r) else 0.0

    return {
        "mode": mode, "note": note,
        "scope": {"nodes": len(sys_nodes), "edges": len(sys_edges)},
        "entities": {
            "gold": len(gold_entities), "system": len(sys_nodes),
            "tp_strict": tp_strict, "tp_lenient": tp_lenient, "fp": fp, "fn": fn,
            "precision": round(p, 4), "recall": round(r, 4), "f1": round(f1, 4),
            "type_errors": len(type_errors), "boundary_errors": len(boundary_errors),
        },
        "significance": {
            "noise_count": len(noise),
            "noise_rate": round(len(noise) / len(sys_nodes), 4) if sys_nodes else 0.0,
            "kinds": {k: sum(1 for _, kk in noise if kk == k)
                      for k in {kk for _, kk in noise}},
        },
        "relations": {
            "gold": len(gold_rel), "system": len(sys_edges),
            "tp": rel_tp, "fp": rel_fp, "fn": rel_fn, "direction_errors": rel_dir,
            "precision": round(rel_p, 4), "recall": round(rel_r, 4), "f1": round(rel_f1, 4),
        },
        "error_details": {"type_errors": type_errors[:30], "boundary_errors": boundary_errors[:30]},
    }


def main():
    modes = sys.argv[1:] or ["cloud"]
    gold_path = GOLD_DIR / f"gold-{PAPER}.json"
    if not gold_path.exists():
        raise SystemExit(f"缺少金标 {gold_path}")
    gold = json.loads(gold_path.read_text(encoding="utf-8"))

    print(f"=== 金标评分：{PAPER}（金标 {len(gold['entities'])} 实体 / "
          f"{len(gold['relations'])} 关系，范围 1-{PAGES} 页）===")
    print(f"标注者 {gold.get('annotators')}，平均一致率 "
          f"{(gold.get('stats', {}).get('mean_pairwise_jaccard') or 0)*100:.1f}%\n")

    results = []
    for mode in modes:
        r = score(mode, gold)
        if r is None:
            continue
        results.append(r)
        e, s, rel = r["entities"], r["significance"], r["relations"]
        print(f"── 通道 {mode} ── {r['note']}")
        print(f"  抽取范围: {r['scope']['nodes']} 实体 / {r['scope']['edges']} 关系")
        print(f"  实体   P {e['precision']:.3f}  R {e['recall']:.3f}  F1 {e['f1']:.3f}"
              f"   (TP {e['tp_lenient']} / FP {e['fp']} / FN {e['fn']})")
        print(f"         类型错误 {e['type_errors']}  边界错误 {e['boundary_errors']}")
        print(f"  显著性: 结构/泛指项 {s['noise_count']} 条，占输出 "
              f"{s['noise_rate']*100:.1f}%  {s['kinds']}")
        print(f"  关系   P {rel['precision']:.3f}  R {rel['recall']:.3f}  F1 {rel['f1']:.3f}"
              f"   (TP {rel['tp']} / FP {rel['fp']} / FN {rel['fn']} / 方向错 {rel['direction_errors']})")
        print()

    out = GOLD_DIR / f"score-{PAPER}.json"
    out.write_text(json.dumps({
        "paper_id": PAPER, "gold_pages": f"1-{PAGES}",
        "annotators": gold.get("annotators"),
        "agreement": gold.get("stats", {}).get("mean_pairwise_jaccard"),
        "channels": results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告已写出: {out}")


if __name__ == "__main__":
    main()
