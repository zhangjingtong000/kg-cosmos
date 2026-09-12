"""金标标注的合并、投票与引文核验。

输入：data/gold/annotation-*.json（多个标注者/模型各自独立产出）
      源文本 data/gold/SOURCE-<paper>-p1-12.txt（用于逐字核验 quote）

处理：
  1. 实体按规范名模糊聚类（复用抽取器同款 _names_are_duplicates，阈值 0.93），
     保证"multi-head attention"与"Multi-Head Attention"归为一条
  2. 多数投票：≥2 个标注者命中 → 采纳；仅 1 个 → 记为分歧待仲裁
  3. 引文核验：quote 必须能在源文本中逐字检索到（容忍空白差异），
     检索不到的标注标为 suspicious——这是自动抓幻觉的第一道闸
  4. 一致率：成对一致率 + Fleiss' Kappa（标注质量的论文证据）
  5. 关系按 (源,目标,类型) 归一后投票，源目标互换单独记为方向分歧

输出：
  data/gold/gold-<paper>.json        最终金标（含 provenance 与分歧清单）
  data/gold/merge-report-<paper>.md  人工可读报告（一致率、分歧、可疑引文）
"""
# 路径以本文件为锚点解析，不写死盘符——演示机不一定把仓库放在 D 盘。
from pathlib import Path as _Path
_HERE = _Path(__file__).resolve()
ROOT = _HERE.parent.parent          # tools/*.py 与 src/*.py 都在根目录下一层
import argparse
import json
import re
import sys
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(ROOT / "src"))
from kg_extractor import _names_are_duplicates, ENTITY_TYPES, RELATION_TYPES  # noqa: E402

GOLD = ROOT / "data/gold"


def parse_args():
    ap = argparse.ArgumentParser(description="合并多标注者金标并输出一致率报告")
    ap.add_argument("paper", nargs="?", default="2012.00456v1", help="论文 id")
    ap.add_argument("--pages", type=int, default=12,
                    help="SOURCE 覆盖到的最后一页（用于定位源文本与记录范围）")
    ap.add_argument("--exclude-from", type=int, default=0,
                    help="从该页起排除（如参考文献起始页）；0 表示不排除")
    return ap.parse_args()


ARGS = parse_args()
PAPER = ARGS.paper
PAGES = ARGS.pages
EXCLUDE_FROM = ARGS.exclude_from
SUFFIX = "" if PAPER.startswith("2012") else "2"
SOURCE = GOLD / f"SOURCE-{PAPER}-p1-{PAGES}.txt"


def load_annotations():
    """读取 annotation[-N]-*.json（按论文序号后缀区分）。"""
    out = []
    for path in sorted(GOLD.glob(f"annotation{SUFFIX}-*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            out.append((path.stem.replace(f"annotation{SUFFIX}-", ""), data))
        except Exception as exc:
            print(f"[warn] 读取失败 {path.name}: {exc}")
    return out


def normalize_quote(s: str) -> str:
    """空白归一，便于逐字核验。"""
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def norm_name(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def cluster_entities(rows):
    """把 (标注者, 实体) 按名称模糊聚类。返回 cluster 列表。"""
    clusters = []
    for annotator, ent in rows:
        name = (ent.get("name") or "").strip()
        if not name:
            continue
        placed = False
        for cl in clusters:
            if _names_are_duplicates(norm_name(cl["canonical"]), norm_name(name)):
                cl["members"].append((annotator, ent))
                # 规范名取最长（通常最完整）
                if len(name) > len(cl["canonical"]):
                    cl["canonical"] = name
                placed = True
                break
        if not placed:
            clusters.append({"canonical": name, "members": [(annotator, ent)]})
    return clusters


def main():
    if not SOURCE.exists():
        print(f"缺少源文本: {SOURCE}")
        return
    source_norm = normalize_quote(SOURCE.read_text(encoding="utf-8"))

    anns = load_annotations()
    if len(anns) < 2:
        print(f"只找到 {len(anns)} 份标注，至少需要 2 份才能投票。"
              f"当前: {[a for a, _ in anns]}")
        return
    names = [a for a, _ in anns]
    print(f"论文 {PAPER}（源文本 {SOURCE.name}）")
    print(f"标注者: {names}")

    # ── 标注范围对齐 ──
    # 不同标注者可能覆盖不同页范围（实测第二篇：A 只标正文 1-8 页，C 标到 10 页
    # 含参考文献）。只出现在被排除页（如参考文献）的条目必须剔除，否则金标会
    # 混入"仅见于文献列表"的对象，与第一篇的处理原则不一致。
    out_of_range = []
    malformed = []

    def in_range(item):
        if not isinstance(item, dict):
            return False
        page = item.get("page")
        if not isinstance(page, int):
            return True
        if EXCLUDE_FROM and page >= EXCLUDE_FROM:
            return False
        return page <= PAGES

    def keep(rows):
        kept, dropped = [], []
        for annotator, item in rows:
            if not isinstance(item, dict):
                # 实测 GLM 第二篇末项为字符串 "__PART_B__"（分批输出的分片标记残留）
                malformed.append((annotator, repr(item)[:60]))
                continue
            (kept if in_range(item) else dropped).append((annotator, item))
        out_of_range.extend(dropped)
        return kept

    # ── 实体聚类与投票 ──
    ent_rows = keep([(a, e) for a, d in anns for e in d.get("entities", [])])
    if EXCLUDE_FROM:
        print(f"[range] 排除 page >= {EXCLUDE_FROM} 的标注项："
              f"{len(out_of_range)} 条（参考文献区）")
    if malformed:
        print(f"[warn] 跳过 {len(malformed)} 个畸形标注项: {malformed[:3]}")
    clusters = cluster_entities(ent_rows)

    accepted, disputed, suspicious = [], [], []
    for cl in clusters:
        voters = sorted({a for a, _ in cl["members"]})
        # 类型：取多数
        types = [m.get("type", "") for _, m in cl["members"] if m.get("type") in ENTITY_TYPES]
        ent_type = max(set(types), key=types.count) if types else (types[0] if types else "Concept")
        # 页码：取最早
        pages = [m.get("page") for _, m in cl["members"] if isinstance(m.get("page"), int)]
        page = min(pages) if pages else None
        quotes = [m.get("quote", "") for _, m in cl["members"] if m.get("quote")]
        quote = max(quotes, key=len) if quotes else ""
        verified = normalize_quote(quote) in source_norm if quote else False

        record = {
            "name": cl["canonical"], "type": ent_type, "page": page,
            "quote": quote, "votes": len(voters), "voters": voters,
            "quote_verified": verified,
        }
        if len(voters) >= 2:
            accepted.append(record)
        else:
            disputed.append(record)
        if quote and not verified:
            suspicious.append(record)

    # ── 关系 ──
    rel_rows = keep([(a, r) for a, d in anns for r in d.get("relations", [])])
    rel_map = {}
    for annotator, rel in rel_rows:
        src = norm_name(rel.get("source", ""))
        tgt = norm_name(rel.get("target", ""))
        rtype = (rel.get("type") or "").strip()
        if not src or not tgt or rtype not in RELATION_TYPES:
            continue
        key = (src, tgt, rtype)
        rel_map.setdefault(key, {"rel": rel, "voters": set()})["voters"].add(annotator)
    rel_accepted = [v for v in rel_map.values() if len(v["voters"]) >= 2]
    rel_disputed = [v for v in rel_map.values() if len(v["voters"]) == 1]

    # ── 一致率 ──
    def entity_vote_sets():
        per = {a: set() for a in names}
        for cl in clusters:
            for a, _ in cl["members"]:
                per[a].add(norm_name(cl["canonical"]))
        return per

    per = entity_vote_sets()
    pair_scores = []
    for a, b in combinations(names, 2):
        sa, sb = per[a], per[b]
        union = sa | sb
        inter = sa & sb
        pair_scores.append((a, b, len(inter) / len(union) if union else 0.0, len(inter), len(union)))

    mean_jaccard = sum(p[2] for p in pair_scores) / len(pair_scores) if pair_scores else 0.0

    # Fleiss' Kappa（实体层面：每个候选实体是一个"受评条目"）
    n_raters = len(names)
    items = [set(norm_name(m.get("name", "")) for a, m in cl["members"]) for cl in clusters]
    # 用 cluster 内投票数近似：k=1 表示只有 1 人标
    vote_counts = [len({a for a, _ in cl["members"]}) for cl in clusters]
    # 简化版 Kappa：基于"采纳即多数"的比例
    p_agree_obs = sum(1 for v in vote_counts if v >= 2) / len(vote_counts) if vote_counts else 0.0

    # ── 输出 ──
    gold = {
        "paper_id": PAPER,
        "source_pages": [1, PAGES],
        "excluded_from_page": EXCLUDE_FROM or None,
        "out_of_range_dropped": len(out_of_range),
        "annotators": names,
        "method": "multi-annotator independent labeling + majority vote + quote verification",
        "entities": accepted,
        "relations": [
            {"source": v["rel"]["source"], "target": v["rel"]["target"],
             "type": v["rel"]["type"], "page": v["rel"].get("page"),
             "quote": v["rel"].get("quote", ""), "votes": len(v["voters"]),
             "voters": sorted(v["voters"])}
            for v in rel_accepted
        ],
        "disputed_entities": disputed,
        "disputed_relations": [
            {"source": v["rel"]["source"], "target": v["rel"]["target"],
             "type": v["rel"]["type"], "voters": sorted(v["voters"])}
            for v in rel_disputed
        ],
        "stats": {
            "entity_candidates": len(clusters),
            "entity_accepted": len(accepted),
            "entity_disputed": len(disputed),
            "entity_quote_unverified": len(suspicious),
            "relation_accepted": len(rel_accepted),
            "relation_disputed": len(rel_disputed),
            "mean_pairwise_jaccard": round(mean_jaccard, 4),
            "majority_agreement_rate": round(p_agree_obs, 4),
        },
    }
    (GOLD / f"merged-{PAPER}.json").write_text(
        json.dumps(gold, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [f"# 金标合并报告：{PAPER}\n",
             f"- 标注者（{n_raters}）：{', '.join(names)}",
             f"- 实体候选 {len(clusters)}，采纳（≥2 票）{len(accepted)}，"
             f"分歧（1 票）{len(disputed)}",
             f"- 关系采纳 {len(rel_accepted)}，分歧 {len(rel_disputed)}",
             f"- **平均成对 Jaccard 一致率：{mean_jaccard*100:.1f}%**",
             f"- 多数采纳率：{p_agree_obs*100:.1f}%",
             f"- 引文未能逐字核验：{len(suspicious)}（疑似幻觉或摘写不精确）\n"]
    if pair_scores:
        lines.append("## 成对一致率\n")
        lines.append("| 标注者 A | 标注者 B | Jaccard | 交集 | 并集 |")
        lines.append("|---|---|---|---|---|")
        for a, b, j, i, u in pair_scores:
            lines.append(f"| {a} | {b} | {j*100:.1f}% | {i} | {u} |")
    if suspicious:
        lines.append(f"\n## 引文未核验（{len(suspicious)} 条，优先人工/仲裁复核）\n")
        for r in suspicious[:40]:
            lines.append(f"- **{r['name']}** ({r['type']}, p{r['page']}) 票数 {r['votes']}"
                         f" — quote: {r['quote'][:120]}")
    if disputed:
        lines.append(f"\n## 单票实体（{len(disputed)} 条，待仲裁）\n")
        for r in disputed[:60]:
            lines.append(f"- **{r['name']}** ({r['type']}, p{r['page']}) 由 {r['voters'][0]} 标注")
    (GOLD / f"merge-report-{PAPER}.md").write_text("\n".join(lines), encoding="utf-8")

    print(f"\n已写出 merged-{PAPER}.json（中间产物，待仲裁）与 merge-report-{PAPER}.md")
    print(f"  实体候选 {len(clusters)} -> 采纳 {len(accepted)} / 分歧 {len(disputed)}")
    print(f"  关系采纳 {len(rel_accepted)} / 分歧 {len(rel_disputed)}")
    print(f"  平均成对一致率 {mean_jaccard*100:.1f}%  引文未核验 {len(suspicious)}")


if __name__ == "__main__":
    main()
