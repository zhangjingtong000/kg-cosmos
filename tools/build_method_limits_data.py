"""把 claims.json 导成「方法能力与局限」视图的数据。

【为什么不是「矛盾/争议视图」】
  T3.3 把 243 条 `CONTRADICTS` 边改建为 claim 四元组，计划供 T6.6 争议视图使用。
  但 `tools/diag_claims.py` 的诊断结论是**这些记录没有一条构成矛盾**：
  243 条对应 242 个不同的 (subject, object) 对，**没有任何一对被 ≥2 篇论文
  同时表态** —— 矛盾的逻辑前提是两方对立，而这里不存在两方。
  它们的真实语义是「方法 X 在能力维度 Y 上的表现/局限」，
  例如 `TransE ⊗ Many-to-1 Relationships`（"TransE is not well-suited for …"）
  和 `Bilinear ⊗ Full Matrix`（"uses diagonal matrix rather than full matrix"）。

  所以本视图按**维度**（object）聚合展示，而不是按"争议"组织；
  subject 是方法、object 是能力维度，这两端恰好构成一张可读的能力矩阵。

【status / confidence 怎么处理】
  它们是在「这条是不是矛盾」这个假设下算出来的（REJECTED 190 / SUSPECTED 53，
  confidence = L5 判定原文是否支持"X contradicts Y"）。
  既然前提本身不成立，这两个字段**不应作为主信息展示**，
  但也不能删——它们是这条记录来源的可追溯元数据。
  页面把它放在二级信息区并注明口径。

用法：
  python tools/build_method_limits_data.py
"""
import json
from collections import Counter, defaultdict
from pathlib import Path

CLAIMS = Path(r"D:\kg-demo\data\quality\claims.json")
OUT = Path(r"D:\kg-demo\web\kg-data\method-limits.json")

MAX_EVIDENCE = 700


def main():
    d = json.loads(CLAIMS.read_text(encoding="utf-8"))
    claims = d["claims"]
    print(f"载入 {len(claims)} 条 claim")

    by_dim = defaultdict(list)
    for c in claims:
        by_dim[c["object"]].append({
            "subject": c["subject"],
            "description": c.get("description") or "",
            "paper": c.get("source_paper") or "",
            "page": c.get("page"),
            "evidence": (c.get("evidence_quote") or "")[:MAX_EVIDENCE],
            "status": c.get("status") or "",
            "confidence": c.get("confidence"),
        })

    dimensions = [
        {"name": k, "count": len(v),
         "items": sorted(v, key=lambda x: (x["subject"], x["paper"]))}
        for k, v in sorted(by_dim.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    ]

    subj_counter = Counter(c["subject"] for c in claims)
    papers = sorted({c.get("source_paper") for c in claims if c.get("source_paper")})

    payload = {
        "meta": {
            "total": len(claims),
            "dimensions": len(dimensions),
            "subjects": len(subj_counter),
            "papers": len(papers),
            "paper_list": papers,
            "status_counts": dict(Counter(c["status"] for c in claims)),
            # 把"为什么改视图"写进数据本身，前端直接展示，避免脱离脚本就丢失语境
            "note": ("这 243 条原本被抽成 CONTRADICTS 边。诊断为误标："
                     "243 条对应 242 个不同的 (方法, 维度) 对，"
                     "没有任何一对被两篇以上论文同时表态，"
                     "即不存在构成矛盾所需的『两方』。"
                     "它们的真实语义是「方法在某个能力维度上的表现或局限」。"),
            "source": "data/quality/claims.json → tools/build_method_limits_data.py",
            "top_subjects": subj_counter.most_common(12),
        },
        "dimensions": dimensions,
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    print(f"维度 {len(dimensions)} 个，方法 {len(subj_counter)} 个，论文 {len(papers)} 篇")
    print("维度 Top 10:", [(x['name'], x['count']) for x in dimensions[:10]])
    print(f"已写出 {OUT}  ({OUT.stat().st_size/1024:.0f} KB)")


if __name__ == "__main__":
    main()
