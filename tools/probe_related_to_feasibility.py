"""RELATED_TO 细化可行性探查：确认原始证据句是否保留（决定能否做细化实验）。

调研提出的关键判别标准（REPORT.md §9，子代理 B）：
  把 RELATED_TO 的关系描述/证据句做聚类 ——
    · 若子类成员数远超随机期望（非均匀）→ 是「未细分的真关系集合」，应细化
    · 若簇大小高度均匀且簇内语义杂乱 → 确是无信息垃圾桶，应降权而非细化
  但这一切的前提是：**RELATED_TO 边是否保留了原始证据句/描述**。
    有 → 立刻能做细化实验
    没有 → 需回原文重新定位，成本高得多

本脚本回答这个前提问题，并给出证据句的可分辨性预估。
"""
# 路径以本文件为锚点解析，不写死盘符——演示机不一定把仓库放在 D 盘。
from pathlib import Path as _Path
_HERE = _Path(__file__).resolve()
ROOT = _HERE.parent.parent          # tools/*.py 与 src/*.py 都在根目录下一层
import json
import re
import statistics as st
from collections import Counter
from pathlib import Path

GRAPHS = ROOT / "data/corpus/graphs"


def norm(n: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (n or "").lower())


def main():
    print("=== RELATED_TO 细化可行性探查 ===\n")
    rows = []
    for f in sorted(GRAPHS.glob("*.json")):
        try:
            snap = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        for e in snap.get("edges", []):
            if (e.get("type") or "") != "RELATED_TO":
                continue
            ev = e.get("evidence") or []
            text, page = "", None
            if isinstance(ev, list):
                for it in ev:
                    if isinstance(it, dict) and it.get("text"):
                        text = str(it["text"]).strip()
                        page = it.get("page")
                        break
            rows.append({"paper": f.stem, "s": e.get("source"), "t": e.get("target"),
                         "desc": (e.get("description") or "").strip(),
                         "evidence": text, "page": page,
                         "text": (e.get("text") or "").strip()})

    print(f"RELATED_TO 边总数 {len(rows)}")
    with_ev = [r for r in rows if r["evidence"]]
    with_desc = [r for r in rows if r["desc"]]
    with_any = [r for r in rows if r["evidence"] or r["desc"] or r["text"]]
    print(f"  带 evidence 文本 : {len(with_ev):6d}  ({len(with_ev)/len(rows)*100:.1f}%)")
    print(f"  带 description   : {len(with_desc):6d}  ({len(with_desc)/len(rows)*100:.1f}%)")
    print(f"  两者至少有一个   : {len(with_any):6d}  ({len(with_any)/len(rows)*100:.1f}%)")

    if with_ev:
        lens = [len(r["evidence"]) for r in with_ev]
        print(f"\n  证据文本长度  中位数 {st.median(lens):.0f}  均值 {st.mean(lens):.0f}  "
              f"最短 {min(lens)}  最长 {max(lens)}")
        uniq = len({r["evidence"] for r in with_ev})
        print(f"  去重后证据句数 {uniq}  （{uniq/len(with_ev)*100:.1f}% 唯一）")

    # 判定：证据句是否可分辨（能否支撑细化）
    print(f"\n── 可分辨性评估 ──")
    if len(with_ev) / len(rows) > 0.9:
        print("  ✅ 证据句几乎全覆盖 → **细化实验可立刻进行**，不需要回原文定位")
    elif with_any:
        print("  ⚠️ 部分覆盖 → 可做细化，但需说明覆盖比例")
    else:
        print("  ❌ 无证据句 → 细化需回原文重新定位，成本显著上升")

    # 抽样展示（供人工判断语义是否可细分）
    print(f"\n── 样本（20 条，看证据句能否支持细化）──")
    seen = set()
    shown = 0
    for r in with_ev:
        k = (norm(r["s"]), norm(r["t"]))
        if k in seen:
            continue
        seen.add(k)
        print(f"\n  [{r['paper']} p{r['page']}]")
        print(f"    {r['s']}  ~~  {r['t']}")
        ev = " ".join(r["evidence"].split())
        print(f"    证据: {ev[:180]}{'...' if len(ev) > 180 else ''}")
        shown += 1
        if shown >= 20:
            break

    # ── 端点类型组合（决定细化方向）──
    print(f"\n── RELATED_TO 端点类型组合 Top 15 ──")
    nodes = {}
    for f in sorted(GRAPHS.glob("*.json")):
        try:
            snap = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        for n in snap.get("nodes", []):
            k = norm(n.get("name", ""))
            if k:
                nodes[k] = n.get("type")
    combo = Counter()
    for r in rows:
        combo[(nodes.get(norm(r["s"]), "?"), nodes.get(norm(r["t"]), "?"))] += 1
    for (a, b), c in combo.most_common(15):
        print(f"   {a:12s} -> {b:12s}  {c:5d}  ({c/len(rows)*100:4.1f}%)")


if __name__ == "__main__":
    main()
