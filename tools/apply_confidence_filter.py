"""按 L5 证据支持度过滤低置信度关系（本地零成本，可回滚）。

【依据】data/quality/relation-verify-all.json（Triplet Critic 全量验证）
  阈值 0.67（官方校准集宏平均 F1 最优；不照抄文献的 0.75/0.1）

【为什么只过滤两个类型，而不是全类型】
  实测按类型通过率（≥0.67）：
    LOCATED_AT 81.9% / PRODUCES 78.8% / ENABLES 77.5% / APPLIES_TO 76.0%
    PART_OF 75.7% / USES 68.1% / MEASURES 64.8% / CAUSES 60.7% / SUPPORTS 57.0%
    CITES 52.0% / EVALUATES 52.2% / COMPARES_WITH 43.0%
    RELATED_TO 51.6% / CREATED_BY 39.6%
  ⇒ 语义关系类型（≥57%）整体可用，**不该动**。
    只对两个"兜底/边缘"类型应用阈值：
      RELATED_TO  双峰（<0.1 占 50.1%、>0.9 占 38.2%）⇒ 该按置信度切
      CREATED_BY  通过率最低，且方向问题刚修过，剩余低分边多为伪实体
    **CONTRADICTS 不在这里处理** —— 它通过率仅 6.6%，整类需要重建模
    （claim + status + 引用四元组），不是"过滤"能解决的。

【安全设计】
  · 默认 dry-run；`--apply` 才写
  · **不删除边**，而是标记 `_filtered` + `_filter_reason`，保留完整审计痕迹
    （便于后续回滚与"被过滤边"的独立分析）
  · 写前备份 data/quality/backup/filter-<ts>/
  · 产出被过滤边清单 data/quality/filtered-edges.json

【匹配口径】
  验证结果用的是**归一化前**的实体名，快照已被 entity_normalize.py 改过。
  因此匹配时必须同样做 NFKC+小写+标点折叠，用
  (paper, type, norm(source), norm(target)) 作键。

用法：
  python tools/apply_confidence_filter.py                # dry-run
  python tools/apply_confidence_filter.py --apply
  python tools/apply_confidence_filter.py --rollback <dir>
"""
import argparse
import json
import re
import shutil
import time
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

GRAPHS = Path(r"D:\kg-demo\data\corpus\graphs")
OUT = Path(r"D:\kg-demo\data\quality")
BACKUP = OUT / "backup"
VERIFY = OUT / "relation-verify-all.json"

# 只过滤这两个类型（理由见模块 docstring）
FILTER_TYPES = {"RELATED_TO", "CREATED_BY"}


def parse_args():
    ap = argparse.ArgumentParser(description="按证据支持度过滤关系")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--rollback", default="")
    ap.add_argument("--threshold", type=float, default=0.0,
                    help="覆盖阈值（默认用验证文件里的 0.67）")
    return ap.parse_args()


ARGS = parse_args()


def nkey(n: str) -> str:
    s = unicodedata.normalize("NFKC", n or "").lower()
    s = re.sub(r"[\u2018\u2019\u02bc\u2032`]", "'", s)
    s = re.sub(r"[\u2010-\u2015\u2212]", "-", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def main():
    if ARGS.rollback:
        src = Path(ARGS.rollback)
        files = list(src.glob("*.json"))
        for b in files:
            shutil.copy2(b, GRAPHS / b.name)
        print(f"已回滚 {len(files)} 个文件")
        return

    if not VERIFY.exists():
        raise SystemExit(f"缺少验证结果 {VERIFY}，先跑 tools/relation_verifier.py --all")
    v = json.loads(VERIFY.read_text(encoding="utf-8"))
    thr = ARGS.threshold or v.get("threshold") or 0.67
    print(f"=== 证据支持度过滤" + ("｜APPLY" if ARGS.apply else "｜dry-run") + " ===")
    print(f"阈值 {thr}（来自 {VERIFY.name}，官方校准 AUC {v.get('calib_roc_auc')}）")
    print(f"只过滤: {sorted(FILTER_TYPES)}\n")

    # ⚠️ 验证结果用的是**归一化前**的实体名，而快照已被 entity_normalize.py 改过。
    #    必须先应用同一份别名映射，否则大量边匹配不上（实测未匹配率 21.8%）。
    alias = {}
    amap = OUT / "entity-alias-map.json"
    if amap.exists():
        alias = json.loads(amap.read_text(encoding="utf-8")).get("alias", {})
        print(f"已载入别名映射 {len(alias)} 条（用于对齐验证数据与已归一化的快照）")

    def canon(name: str) -> str:
        return alias.get(name, name)

    # 建置信度索引
    #   ⚠️ 同时记录**两个方向**：apply_created_by_fix.py 把 2348 条 CREATED_BY
    #   边反转了方向，而验证是在反转**之前**做的。若只按原方向索引，
    #   这批边会全部匹配不上（实测未匹配 2348 条 = 恰好等于被反转的边数）。
    #   CREATED_BY 的"证据是否支持"与方向无关（NLI 判定的是 A 与 B 之间的
    #   创建关系是否被原文支持），因此两个方向共用同一置信度是合理的。
    conf = {}

    def put(k, p):
        if k not in conf or p > conf[k]:
            conf[k] = p

    for e in v.get("edges", []):
        paper, t = e.get("paper"), e.get("type")
        a = nkey(canon(e.get("source", "")))
        b = nkey(canon(e.get("target", "")))
        p = e.get("entail_prob", 0.0)
        put((paper, t, a, b), p)
        put((paper, t, b, a), p)
    print(f"置信度索引 {len(conf)} 条（验证文件 {len(v.get('edges', []))} 条，双向展开）")

    stats = Counter()
    filtered_rows = []
    per_file = {}
    pending = []          # (path, snap)：先标记到内存，备份之后再落盘

    for f in sorted(GRAPHS.glob("*.json")):
        snap = json.loads(f.read_text(encoding="utf-8"))
        paper = f.stem
        matched = 0
        for e in snap.get("edges", []):
            t = e.get("type") or "?"
            if t not in FILTER_TYPES:
                continue
            k = (paper, t, nkey(e.get("source", "")), nkey(e.get("target", "")))
            p = conf.get(k)
            if p is None:
                stats["unmatched"] += 1
                continue
            matched += 1
            stats["matched"] += 1
            if p < thr:
                stats["filtered"] += 1
                stats[f"filtered_{t}"] += 1
                filtered_rows.append({"paper": paper, "type": t,
                                      "source": e.get("source"), "target": e.get("target"),
                                      "entail_prob": p})
                e["_filtered"] = True
                e["_filter_reason"] = f"entail_prob {p:.4f} < {thr}"
        per_file[f.name] = matched
        if matched:
            pending.append((f, snap))

    print(f"\n── 结果 ──")
    print(f"   参与判定 {stats['matched']}   未匹配到置信度 {stats['unmatched']}")
    print(f"   将过滤   {stats['filtered']}"
          f"（RELATED_TO {stats.get('filtered_RELATED_TO', 0)}"
          f" / CREATED_BY {stats.get('filtered_CREATED_BY', 0)}）")
    total_before = stats["matched"]
    if total_before:
        print(f"   占参与判定的 {stats['filtered']/total_before*100:.1f}%")

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "filtered-edges.json").write_text(json.dumps({
        "threshold": thr, "types": sorted(FILTER_TYPES),
        "stats": dict(stats), "filtered": filtered_rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"   被过滤清单: {OUT / 'filtered-edges.json'}")

    if not ARGS.apply:
        print("\n（dry-run，未改数据。加 --apply 写入）")
        return

    bk = BACKUP / time.strftime("filter-%Y%m%d-%H%M%S")
    bk.mkdir(parents=True, exist_ok=True)
    # 备份"过滤前"状态：去掉本次要写入的 _filtered/_filter_reason 标记
    copied = 0
    for f, _ in pending:
        base = json.loads(f.read_text(encoding="utf-8"))
        for e in base.get("edges", []):
            e.pop("_filtered", None)
            e.pop("_filter_reason", None)
        (bk / f.name).write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")
        copied += 1
    # 备份完成后才落盘
    for f, snap in pending:
        f.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
    print(f"\n备份 {copied} 个文件（过滤前状态）→ {bk}")
    print(f"回滚: python tools/apply_confidence_filter.py --rollback \"{bk}\"")


if __name__ == "__main__":
    main()
