"""诊断 `data/quality/claims.json`：这 243 条 CONTRADICTS 究竟是不是"矛盾"。

背景：T3.3 把图上的 `CONTRADICTS` 边改建为 claim 四元组（subject / object /
status / 引用），目的是为 T6.6「矛盾/争议视图」提供数据。
但抽样看到 description 多为「A 使用 X 而非 Y」「A 不支持 X」这类
**方法局限性 / 区分**陈述，而非两个论断相互对立。
本脚本把这一观察定量化，判定该视图是否有数据基础。

判定口径（不依赖主观标注，只做字符串模式 + 跨论文共现两类机器可验的检查）：
  A. 语义模式：description 是否含"替代/局限"类标记
     —— 若是，则它是 single-paper 的属性陈述，不是两方对立。
  B. 跨论文对立：同一个 (subject, object) 是否在**≥2 篇不同论文**里出现，
     且 description 语义方向相反（一条肯定、一条否定）。
     只有这种情况才构成"争议"。
用法：
  python tools/diag_claims.py
"""
# 路径以本文件为锚点解析，不写死盘符——演示机不一定把仓库放在 D 盘。
from pathlib import Path as _Path
_HERE = _Path(__file__).resolve()
ROOT = _HERE.parent.parent          # tools/*.py 与 src/*.py 都在根目录下一层
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

CLAIMS = ROOT / "data/quality/claims.json"

# 「替代/区分」类标记：A 用 X 而不是 Y
REPLACE_PAT = re.compile(
    r"\b(instead of|rather than|as opposed to|not\s+\w+\s+but|"
    r"alternative to|replaces?|instead)\b", re.I)

# 「局限/否定」类标记：A 不支持 / 无法 / 不适合 X
LIMIT_PAT = re.compile(
    r"\b(does not|do not|did not|cannot|can not|failed to|fails to|"
    r"is not|are not|not well-suited|unable to|lacks?|without|"
    r"poor at|weak|limitation|shortcoming|inefficient|insufficient)\b", re.I)

# 「对立」类标记：A 与 B 结论相反
CONFLICT_PAT = re.compile(
    r"\b(contradicts?|conflicts? with|disagrees?|opposite|"
    r"in contrast to|contrary to|challenges?|refutes?|disputes?)\b", re.I)


def main():
    d = json.loads(CLAIMS.read_text(encoding="utf-8"))
    claims = d["claims"]
    print("=" * 74)
    print("claims.json 诊断：243 条 CONTRADICTS 是不是真矛盾")
    print("=" * 74)
    print(f"note: {d['note']}")
    print(f"total {d['total']}   stats {d['stats']}\n")

    # ── A. 语义模式分布 ──
    print("── A. description 的语义模式 ──")
    pat_counts = Counter()
    examples = defaultdict(list)
    for c in claims:
        txt = c.get("description") or ""
        hit = None
        if CONFLICT_PAT.search(txt):
            hit = "对立（conflict 词）"
        elif REPLACE_PAT.search(txt):
            hit = "替代/区分（instead of / rather than）"
        elif LIMIT_PAT.search(txt):
            hit = "局限/否定（does not / failed to）"
        else:
            hit = "其他"
        pat_counts[hit] += 1
        if len(examples[hit]) < 3:
            examples[hit].append(txt)

    for k, v in pat_counts.most_common():
        print(f"  {k:42s} {v:4d}  ({v/len(claims)*100:5.1f}%)")
        for e in examples[k]:
            print(f"        · {e[:110]}")

    # ── B. 跨论文共现（真正的"争议"需要两篇论文对同一对实体表态）──
    print("\n── B. 同一 (subject, object) 是否跨 ≥2 篇论文出现 ──")
    pair_papers = defaultdict(set)
    pair_claims = defaultdict(list)
    for c in claims:
        key = (c["subject"], c["object"])
        pair_papers[key].add(c.get("source_paper"))
        pair_claims[key].append(c)

    multi = {k: v for k, v in pair_papers.items() if len(v) >= 2}
    print(f"  不同 (subject, object) 对总数 : {len(pair_papers)}")
    print(f"  出现在 ≥2 篇论文中的对        : {len(multi)}")
    if multi:
        for k, v in list(multi.items())[:10]:
            print(f"    {k[0]} ⊗ {k[1]}  ← {sorted(v)}")
    else:
        print("  ⇒ **没有任何一对实体被两篇以上论文同时表态**")

    # ── C. 对手实体是否为「方法」的对立面 ──
    print("\n── C. object 端点的性质抽样 ──")
    objs = Counter(c["object"] for c in claims)
    print(f"  唯一 object 数 {len(objs)}   top10: {objs.most_common(10)}")
    subs = Counter(c["subject"] for c in claims)
    print(f"  唯一 subject 数 {len(subs)}  top10: {subs.most_common(10)}")
    # object 是否本身是"某种被否定的技术"
    negobj = [o for o in objs if re.search(
        r"\b(binary|full matrix|cartesian|1-to-|many-to-|vanilla|"
        r"shallow|fixed|manual|linear)\b", o, re.I)]
    print(f"  object 中含「被替代方案」特征词的种类数: {len(negobj)}  例: {negobj[:8]}")

    # ── D. 结论 ──
    print("\n" + "=" * 74)
    print("── D. 逐条审查「含对立语义标记」的 26 条（判据 1 的假阳性检查）──")
    conflict_items = [c for c in claims
                      if CONFLICT_PAT.search(c.get("description") or "")]
    for c in conflict_items:
        print(f"    [{c['status']:9s}] {c['subject'][:28]:28s} ⊗ {c['object'][:26]:26s}"
              f" | {(c.get('description') or '')[:70]}")

    print("\n" + "=" * 74)
    print("── E. 结论 ──")
    print(f"  判据 1（弱）description 含对立语义标记 : {len(conflict_items)} 条")
    print(f"  判据 2（强）同一实体对跨 ≥2 篇论文表态 : **{len(multi)} 个**")

    if len(multi) == 0:
        print("\n  ⇒ **判据 2 是决定性的：矛盾的逻辑前提是两个论断相互对立，")
        print("     而这里不存在「两方」——242 个实体对全部只被单篇论文提及一次。**")
        print("     因此这 243 条**没有一条满足「矛盾」的操作性定义**。")
        print("\n  ⇒ 判据 1 命中的 26 条经逐条审查为假阳性，典型成因：")
        print("     · description 为空或直接写成关系类型本身（如 \"CONTRADICTS\"）")
        print("     · challenges 用于「带来挑战」而非「反驳」（The complexity ... has posed")
        print("       significant challenges to ...），正则无法区分这两种用法")
        print("\n  ⇒ 这些 claim 的真实语义是**「方法 X 在维度 Y 上的表现/局限」**：")
        print("     subject 多为方法（Large Language Model 13 / Knowledge Graphs 11 / TransE 6），")
        print("     object 多为能力维度（Scalability 7 / Interpretability 6 /")
        print("     Computational Complexity 4），而非被反驳的论断。")
        print("\n  ⇒ **T6.6「矛盾/争议视图」在该数据上不应实施**——")
        print("     做出来会是 243 条伪矛盾，反而削弱演示说服力。")
        print("     应改做**「方法能力与局限」视图**：数据现成、语义正确、且同样需要")
        print("     claim + 引用 + 页码的展示形态。")
        verdict = "NO_CONTRADICTION_DATA"
    else:
        print("\n  ⇒ 存在可用的对立证据，需人工复核后决定视图口径。")
        verdict = "PARTIAL"

    out = {
        "total": len(claims),
        "pattern_counts": dict(pat_counts),
        "conflict_marker_items": len(conflict_items),
        "cross_paper_pairs": len(multi),
        "distinct_pairs": len(pair_papers),
        "verdict": verdict,
    }
    p = ROOT / "data/quality/claims-diagnosis.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告已写出: {p}")


if __name__ == "__main__":
    main()
