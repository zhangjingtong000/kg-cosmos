"""CONTRADICTS 重建模：从「图上的边」改为「claim 四元组记录」。

【为什么必须重建】
  L5 证据支持度实测（Triplet Critic，阈值 0.67）：
    CONTRADICTS 通过率 **6.6%**（243 条中仅 16 条），median P = 0.235
    而其它类型最低也有 39.6%（CREATED_BY），语义类均在 57%–82%。
  ⇒ **比次低类型低一个数量级**，机器可验证地确认：这些边基本都不是真矛盾。
  抽样也印证：`DKN -> End-to-End Training`、`Re-training -> Computation resource`、
  `Citation Format -> Standard Implementation` —— 都不是矛盾关系。

【主流做法（调研已核实 GraphRAG 源码）】
  GraphRAG 不把矛盾建模成边，而是用 claim：
    `claim_extraction.py` prompt 原文要求
      - Claim Status: **TRUE / FALSE / SUSPECTED**
      - Claim Date: ISO-8601 区间
      - Claim Source Text: **all** quotes relevant to the claim
    且默认 `CLAIM_EXTRACTION_ENABLED = False`。
  另据子调研：跨文档矛盾在学术上属未成型任务（arXiv 精确检索
  "multi-document contradiction" 0 结果）；最接近的是 ContraSciView /
  RevCI 提出的**分级分歧强度**，说明二值"矛盾"过粗。

【本脚本做什么】
  1. 从快照抽出全部 CONTRADICTS 边，转成 claim 记录：
       {subject, object, relation_type, status, confidence,
        evidence_quote, source_paper, page, verdict_reason}
  2. 依据 L5 置信度给出初始 status：
       P < 0.4        → REJECTED（明确不是矛盾）
       0.4 ≤ P < 0.67 → SUSPECTED（存疑，待验证）
       P ≥ 0.67       → SUSPECTED（罕见，仍待验证——不能自动认定）
     **没有任何一条自动标成 CONFIRMED**：因为本地判别器在这一类上
     本就不可靠（通过率 6.6%），自动确认会造成假结论。
  3. 在快照中把 CONTRADICTS 边标记 `_filtered`（移出图库），
     并在 `_filter_reason` 注明已转为 claim 记录。
  4. 产出 data/quality/claims.json —— 后续可用人工或独立模型复核，
     再派生真正的争议视图。

【安全设计】
  · 默认 dry-run；`--apply` 才写
  · 只标记，不删除边（快照保留审计痕迹）

用法：
  python tools/rebuild_contradicts.py            # dry-run
  python tools/rebuild_contradicts.py --apply
"""
# 路径以本文件为锚点解析，不写死盘符——演示机不一定把仓库放在 D 盘。
from pathlib import Path as _Path
_HERE = _Path(__file__).resolve()
ROOT = _HERE.parent.parent          # tools/*.py 与 src/*.py 都在根目录下一层
import argparse
import json
import re
from collections import Counter
from pathlib import Path

GRAPHS = ROOT / "data/corpus/graphs"
OUT = ROOT / "data/quality"
VERIFY = OUT / "relation-verify-all.json"


def parse_args():
    ap = argparse.ArgumentParser(description="CONTRADICTS 重建模为 claim")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--top", type=int, default=15)
    return ap.parse_args()


ARGS = parse_args()


def nkey(n: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (n or "").lower())


def main():
    print("=== CONTRADICTS 重建模" + ("｜APPLY" if ARGS.apply else "｜dry-run") + " ===\n")

    # 置信度索引（双向，容忍方向差异）
    conf = {}
    if VERIFY.exists():
        v = json.loads(VERIFY.read_text(encoding="utf-8"))
        for e in v.get("edges", []):
            if (e.get("type") or "") != "CONTRADICTS":
                continue
            k = (e.get("paper"), nkey(e.get("source", "")), nkey(e.get("target", "")))
            p = e.get("entail_prob", 0.0)
            if k not in conf or p > conf[k]:
                conf[k] = p
    print(f"CONTRADICTS 置信度索引 {len(conf)} 条")

    claims = []
    stats = Counter()
    per_file = {}

    for f in sorted(GRAPHS.glob("*.json")):
        snap = json.loads(f.read_text(encoding="utf-8"))
        paper = f.stem
        touched = 0
        for e in snap.get("edges", []):
            if (e.get("type") or "") != "CONTRADICTS":
                continue
            s = e.get("source") or ""
            t = e.get("target") or ""
            k = (paper, nkey(s), nkey(t))
            p = conf.get(k)
            if p is None:
                p = conf.get((paper, nkey(t), nkey(s)))
            if p is None:
                p = 0.0
                stats["no_confidence"] += 1
            else:
                stats["with_confidence"] += 1

            # 初始状态：不自动确认任何一条
            if p < 0.4:
                status = "REJECTED"
                reason = f"evidence support {p:.3f} < 0.4 — 本地判别器判定原文不支持该矛盾"
            elif p < 0.67:
                status = "SUSPECTED"
                reason = f"evidence support {p:.3f} 处于灰区（0.4–0.67）— 需独立复核"
            else:
                status = "SUSPECTED"
                reason = f"evidence support {p:.3f} ≥ 0.67，但该类判别器整体通过率仅 6.6%，仍需复核"

            ev = e.get("evidence") or []
            quote, page = "", None
            if isinstance(ev, list):
                for it in ev:
                    if isinstance(it, dict) and it.get("text"):
                        quote = str(it["text"])[:600]
                        page = it.get("page")
                        break

            claims.append({
                "subject": s, "object": t, "relation_type": "CONTRADICTS",
                "status": status, "confidence": round(p, 4),
                "verdict_reason": reason,
                "evidence_quote": quote, "source_paper": paper, "page": page,
                "description": (e.get("description") or "")[:300],
            })
            stats[status] += 1
            if ARGS.apply:
                e["_filtered"] = True
                e["_filter_reason"] = f"converted-to-claim (status={status})"
                touched += 1
        if ARGS.apply and touched:
            f.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
        per_file[paper] = touched

    print(f"\n── 转换结果 ──")
    print(f"   claim 记录 {len(claims)}")
    for k in ("REJECTED", "SUSPECTED", "CONFIRMED"):
        print(f"     {k:10s} {stats.get(k, 0)}")
    print(f"   有置信度 {stats['with_confidence']} / 无 {stats['no_confidence']}")

    print(f"\n── 样本（前 {ARGS.top} 条）──")
    for c in claims[:ARGS.top]:
        print(f"   [{c['status']:8s} P={c['confidence']:.3f}] "
              f"{c['subject'][:28]:28s} -> {c['object'][:28]}")

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "claims.json").write_text(json.dumps({
        "note": "CONTRADICTS 由「图上的边」改为 claim 记录；状态依据 L5 证据支持度初判，"
                "未自动确认任何一条。后续需人工或独立模型复核后再派生争议视图。",
        "total": len(claims), "stats": dict(stats), "claims": claims,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nclaim 记录已写出: {OUT / 'claims.json'}")

    if not ARGS.apply:
        print("\n（dry-run，未改数据。加 --apply 标记快照中的 CONTRADICTS 边）")
    else:
        changed = sum(1 for v in per_file.values() if v)
        print(f"\n已标记 {stats.get('REJECTED', 0) + stats.get('SUSPECTED', 0)} 条边"
              f"（{changed} 个文件）为 _filtered，移出图库但保留审计痕迹")


if __name__ == "__main__":
    main()
