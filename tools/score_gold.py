"""金标评分：计算抽取系统的实体/关系 P/R/F1 与四类错误分布。

关键设计（避免系统性低估）：
  金标只覆盖论文的一部分页（如 1-12 页），而系统抽取结果覆盖全文。若直接拿
  全文结果与局部金标比较，Recall 会被严重低估。因此本项目按**文本范围对齐**：
  先用金标覆盖范围内的文本构建参考语料，再判断系统抽取的每条证据是否落在
  该范围内——只有范围内的抽取项才进入评测。

评分口径（与 docs/GOLD_STANDARD.md §3 一致）：
  实体：规范名归一化后完全相等记命中；另报 ≥0.93 相似度的宽松口径
  关系：(源, 目标, 类型) 归一后全等记命中，源目标互换记方向错误
  错误分类：幻觉 / 类型错误 / 边界错误（名字相近但不等）/ 遗漏
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, r"D:\kg-demo\src")
from kg_extractor import _names_are_duplicates, ENTITY_TYPES, RELATION_TYPES  # noqa: E402

GOLD_DIR = Path(r"D:\kg-demo\data\gold")
GRAPHS = Path(r"D:\kg-demo\data\corpus\graphs")
PAPER = "2012.00456v1"
PAGES = 12

LENIENT = 0.93  # 宽松口径阈值，与抽取去重阈值一致


def norm_name(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def norm_quote(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def load_gold():
    path = GOLD_DIR / f"gold-{PAPER}.json"
    if not path.exists():
        raise SystemExit(f"缺少金标文件 {path}，先跑 tools/merge_gold.py")
    return json.loads(path.read_text(encoding="utf-8"))


def load_system():
    snap = json.loads((GRAPHS / f"{PAPER}.json").read_text(encoding="utf-8"))
    return snap["nodes"], snap["edges"]


def in_scope(node_or_edge, scope_text: str) -> bool:
    """系统抽取项是否落在金标覆盖的文本范围内。

    判据：它的任一证据文本片段能在金标覆盖范围内找到逐字匹配（容忍前缀截断）。
    """
    for ev in node_or_edge.get("evidence", []):
        if not isinstance(ev, dict):
            continue
        text = norm_quote(ev.get("text", ""))
        if not text:
            continue
        # 证据文本可能因分块被截断，取前 80 字符做定位
        probe = text[:80]
        if probe and probe in scope_text:
            return True
    return False


def main():
    gold = load_gold()
    nodes, edges = load_system()

    # 用金标引文构建"范围内文本"的近似参考：以 quote 为准，再补源文本
    source = (GOLD_DIR / f"SOURCE-{PAPER}-p1-{PAGES}.txt")
    scope_text = norm_quote(source.read_text(encoding="utf-8")) if source.exists() else ""
    if not scope_text:
        raise SystemExit(f"缺少范围文本 {source}")

    sys_nodes = [n for n in nodes if in_scope(n, scope_text)]
    sys_edges = [e for e in edges if in_scope(e, scope_text)]

    gold_entities = gold.get("entities", [])
    gold_relations = gold.get("relations", [])

    # ── 实体评分（严格 + 宽松）──
    gold_norm = {norm_name(e["name"]): e for e in gold_entities}
    gold_used, matched_strict, matched_lenient = set(), [], []
    type_errors, boundary_errors = [], []

    for n in sys_nodes:
        key = norm_name(n.get("name", ""))
        if not key:
            continue
        if key in gold_norm and key not in gold_used:
            gold_used.add(key)
            matched_strict.append(n)
            if n.get("type") != gold_norm[key].get("type"):
                type_errors.append({
                    "name": n.get("name"), "system_type": n.get("type"),
                    "gold_type": gold_norm[key].get("type")})
            continue
        # 宽松：与某个尚未命中的金标实体名称相近
        hit = None
        for gkey, g in gold_norm.items():
            if gkey in gold_used:
                continue
            if _names_are_duplicates(key, gkey):
                hit = (gkey, g)
                break
        if hit:
            gold_used.add(hit[0])
            matched_lenient.append(n)
            if n.get("type") != hit[1].get("type"):
                type_errors.append({
                    "name": n.get("name"), "system_type": n.get("type"),
                    "gold_type": hit[1].get("type")})
        else:
            # 名称与某金标实体高度相近但未达阈值 -> 边界错误
            near = [g["name"] for g in gold_entities
                    if norm_name(g["name"]) and (norm_name(g["name"]) in key or key in norm_name(g["name"]))]
            if near:
                boundary_errors.append({"name": n.get("name"), "near_gold": near[:3]})

    tp_strict = len(matched_strict)
    tp_lenient = tp_strict + len(matched_lenient)
    fp = len(sys_nodes) - tp_lenient
    fn = len(gold_entities) - len(gold_used)
    precision = tp_lenient / len(sys_nodes) if sys_nodes else 0.0
    recall = tp_lenient / len(gold_entities) if gold_entities else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    # ── 关系评分 ──
    gold_rel = {(norm_name(r["source"]), norm_name(r["target"]), r["type"]) for r in gold_relations}
    gold_rel_used = set()
    rel_tp, rel_direction_wrong, rel_fp = 0, 0, 0
    for e in sys_edges:
        key = (norm_name(e.get("source", "")), norm_name(e.get("target", "")), e.get("type"))
        if key in gold_rel and key not in gold_rel_used:
            gold_rel_used.add(key)
            rel_tp += 1
            continue
        swapped = (key[1], key[0], key[2])
        if swapped in gold_rel and swapped not in gold_rel_used:
            gold_rel_used.add(swapped)
            rel_direction_wrong += 1
            continue
        rel_fp += 1
    rel_fn = len(gold_rel) - len(gold_rel_used)
    rel_p = rel_tp / len(sys_edges) if sys_edges else 0.0
    rel_r = rel_tp / len(gold_rel) if gold_rel else 0.0
    rel_f1 = 2 * rel_p * rel_r / (rel_p + rel_r) if (rel_p + rel_r) else 0.0

    report = {
        "paper_id": PAPER,
        "gold_pages": f"1-{PAGES}",
        "annotators": gold.get("annotators"),
        "annotation_agreement": gold.get("stats", {}).get("mean_pairwise_jaccard"),
        "scope": {
            "system_total_nodes": len(nodes), "system_total_edges": len(edges),
            "in_scope_nodes": len(sys_nodes), "in_scope_edges": len(sys_edges),
        },
        "entities": {
            "gold": len(gold_entities), "system_in_scope": len(sys_nodes),
            "tp_strict": tp_strict, "tp_lenient": tp_lenient, "fp": fp, "fn": fn,
            "precision": round(precision, 4), "recall": round(recall, 4),
            "f1": round(f1, 4),
            "type_errors": len(type_errors), "boundary_errors": len(boundary_errors),
        },
        "relations": {
            "gold": len(gold_rel), "system_in_scope": len(sys_edges),
            "tp": rel_tp, "fp": rel_fp, "fn": rel_fn,
            "direction_errors": rel_direction_wrong,
            "precision": round(rel_p, 4), "recall": round(rel_r, 4), "f1": round(rel_f1, 4),
        },
        "error_details": {
            "type_errors": type_errors[:50],
            "boundary_errors": boundary_errors[:50],
        },
    }

    out = GOLD_DIR / f"score-{PAPER}.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"=== 金标评分：{PAPER}（金标范围 1-{PAGES} 页）===")
    print(f"标注者 {gold.get('annotators')}，一致率 "
          f"{(gold.get('stats', {}).get('mean_pairwise_jaccard') or 0)*100:.1f}%")
    print(f"范围对齐：系统全文 {len(nodes)}/{len(edges)} -> 范围内 "
          f"{len(sys_nodes)}/{len(sys_edges)}")
    print(f"\n实体：金标 {len(gold_entities)}，系统 {len(sys_nodes)}")
    print(f"  TP {tp_lenient}（严格 {tp_strict} + 宽松 {len(matched_lenient)}）"
          f"  FP {fp}  FN {fn}")
    print(f"  P {precision:.3f}  R {recall:.3f}  F1 {f1:.3f}")
    print(f"  类型错误 {len(type_errors)}  边界错误 {len(boundary_errors)}  遗漏 {fn}")
    print(f"\n关系：金标 {len(gold_rel)}，系统 {len(sys_edges)}")
    print(f"  TP {rel_tp}  FP {rel_fp}  FN {rel_fn}  方向错误 {rel_direction_wrong}")
    print(f"  P {rel_p:.3f}  R {rel_r:.3f}  F1 {rel_f1:.3f}")
    print(f"\n报告已写出: {out}")


if __name__ == "__main__":
    main()
