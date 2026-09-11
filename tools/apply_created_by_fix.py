"""CREATED_BY 方向修复：可回滚的 apply 步骤。

【前置诊断】
  tools/fix_created_by.py --report 产出 data/quality/created-by-plan.json：
    keep 3924 (43.3%) / reverse 2684 (29.6%) / drop 165 (1.8%) / unknown 2286 (25.2%)
  tools/_check_created_by_safety.py 产出 data/quality/created-by-safety.json：
    reverse 中 398 条反转后与已有正向边重合（合并），2286 条成为唯一新边
    drop 中仅 4 条有替代关系，161 条删除会丢信息

【本脚本的策略（保守优先）】
  1. **只反转高置信度的 reverse**：源实体是被创建物（Document/Method/System/...），
     目标实体是 Person/Organization，且不含引文式文本（避免 Bordes et al., 2013 这类
     混入引文的伪实体被当成作者）。
  2. 反转后若与已有正向边重合 → 标记 merge（只留一条），不产生重复边。
  3. **drop 的边不删除**，而是改标为 `CONTRIBUTED_TO`——因为它表达的是
     "作者与该会议/期刊有关联"，删除会丢信息（161/165 无替代关系）。
  4. unknown 一律不动。
  5. 全程写审计日志 + 输出补丁文件，可反向执行。

【安全设计】
  · `--dry-run` 默认开：只产出补丁与统计，不改任何数据
  · `--apply` 才实际写回快照；写回前备份原文件到 data/quality/backup/
  · 补丁文件同时记录 before/after，便于人工抽查与回滚

用法：
  python tools/apply_created_by_fix.py                 # dry-run（默认）
  python tools/apply_created_by_fix.py --apply         # 实际写入
  python tools/apply_created_by_fix.py --rollback <backup_dir>
"""
import argparse
import json
import re
import shutil
import time
from collections import Counter, defaultdict
from pathlib import Path

GRAPHS = Path(r"D:\kg-demo\data\corpus\graphs")
OUT = Path(r"D:\kg-demo\data\quality")
BACKUP = OUT / "backup"

AUTHOR = {"Person", "Organization"}
CREATABLE = {"Document", "Method", "System", "Tool", "Artifact", "Dataset",
             "Component", "Concept", "Process", "Metric", "Material"}
VENUE = re.compile(
    r"\b(conference|workshop|symposium|journal|proceedings|meeting|"
    r"ecml|icml|iclr|neurips|nips|acl|emnlp|naacl|aaai|ijcai|kdd|sigir|www|"
    r"cvpr|iccv|eccv|springer|acm|ieee|elsevier|arxiv)\b", re.IGNORECASE)
# 引文式文本：`Bordes et al., 2013` / `Mikolov et al. (2013)` / `Toutanova And Chen`
CITATIONISH = re.compile(r"et\s+al|\(\d{4}\)|,\s*\d{4}\b|\band\b.*\band\b", re.IGNORECASE)


def parse_args():
    ap = argparse.ArgumentParser(description="CREATED_BY 方向修复")
    ap.add_argument("--apply", action="store_true",
                    help="实际写入（默认只 dry-run，不改数据）")
    ap.add_argument("--rollback", default="", help="从备份目录回滚")
    ap.add_argument("--top", type=int, default=15)
    return ap.parse_args()


def norm(n: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (n or "").lower())


ARGS = parse_args()


def load_types():
    votes = defaultdict(Counter)
    for f in sorted(GRAPHS.glob("*.json")):
        try:
            snap = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        for n in snap.get("nodes", []):
            k = norm(n.get("name", ""))
            if k:
                votes[k][n.get("type") or "?"] += 1
    return {k: c.most_common(1)[0][0] for k, c in votes.items()}


def rollback(src: Path):
    files = list(src.glob("*.json"))
    if not files:
        raise SystemExit(f"备份目录没有 json: {src}")
    for b in files:
        shutil.copy2(b, GRAPHS / b.name)
        print(f"  已恢复 {b.name}")
    print(f"回滚完成，共 {len(files)} 个文件")


def main():
    if ARGS.rollback:
        print(f"=== 从 {ARGS.rollback} 回滚 ===")
        rollback(Path(ARGS.rollback))
        return

    apply = ARGS.apply
    print("=== CREATED_BY 方向修复" + ("（实际写入）" if apply else "（dry-run，不改数据）") + " ===")

    ntype = load_types()
    plans = []          # 每文件的边级改动
    stats = Counter()
    examples = defaultdict(list)
    pairs_added = set()  # 用于反转后去重检测

    # 先收集所有文件的 CREATED_BY 正向边键，用于判断反转后是否重合
    existing = defaultdict(set)
    snaps = {}
    for f in sorted(GRAPHS.glob("*.json")):
        try:
            snap = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  [skip] {f.name}: {e}")
            continue
        snaps[f] = snap
        for e in snap.get("edges", []):
            if (e.get("type") or "") == "CREATED_BY":
                existing[f.stem].add((norm(e.get("source", "")), norm(e.get("target", ""))))

    for f, snap in snaps.items():
        changed = []
        new_edges = []
        for e in snap.get("edges", []):
            if (e.get("type") or "") != "CREATED_BY":
                new_edges.append(e)
                continue
            s, t = e.get("source") or "", e.get("target") or ""
            ks, kt = norm(s), norm(t)
            st, tt = ntype.get(ks, "?"), ntype.get(kt, "?")

            # 规则 1：venue 作为被创建物 → 改标签而非删除
            if VENUE.search(t) and st in AUTHOR:
                ne = dict(e)
                ne["type"] = "CONTRIBUTED_TO"
                ne["_fix"] = "venue-relabel"
                new_edges.append(ne)
                stats["relabel"] += 1
                if len(examples["relabel"]) < ARGS.top:
                    examples["relabel"].append((s, t, "CREATED_BY→CONTRIBUTED_TO"))
                changed.append({"op": "relabel", "source": s, "target": t,
                                "from": "CREATED_BY", "to": "CONTRIBUTED_TO"})
                continue

            # 规则 2：反向 → 反转（排除引文式伪实体，保守）
            if tt in AUTHOR and st in CREATABLE:
                if CITATIONISH.search(s) or CITATIONISH.search(t):
                    new_edges.append(e)
                    stats["reverse_skipped_citationish"] += 1
                    if len(examples["skip"]) < ARGS.top:
                        examples["skip"].append((s, t, "含引文式文本，保守不动"))
                    continue
                if (kt, ks) in existing[f.stem]:
                    # 反转后与已有正向边重合 → 只保留一条
                    stats["merge"] += 1
                    changed.append({"op": "merge", "source": s, "target": t,
                                    "note": "反转后与已有正向边重合，丢弃本条"})
                    if len(examples["merge"]) < ARGS.top:
                        examples["merge"].append((s, t, "反向副本，丢弃"))
                    continue
                ne = dict(e)
                ne["source"], ne["target"] = t, s
                ne["_fix"] = "reverse"
                new_edges.append(ne)
                stats["reverse"] += 1
                if len(examples["reverse"]) < ARGS.top:
                    examples["reverse"].append((s, t, f"{t} -> {s}"))
                changed.append({"op": "reverse", "before": {"source": s, "target": t},
                                "after": {"source": t, "target": s}})
                continue

            new_edges.append(e)
            stats["keep"] += 1

        if changed:
            plans.append({"file": str(f), "changed": changed, "new_edges": new_edges})

    print(f"\n── 计划改动 ──")
    for k in ("reverse", "merge", "relabel", "reverse_skipped_citationish", "keep"):
        print(f"   {k:32s} {stats.get(k, 0):6d}")
    print()
    for k in ("reverse", "merge", "relabel", "skip"):
        rows = examples.get(k, [])
        if rows:
            print(f"── 样本 {k} ──")
            for s, t, note in rows:
                print(f"   {s[:40]:40s} | {t[:34]:34s} | {note}")
            print()

    OUT.mkdir(parents=True, exist_ok=True)
    patch = OUT / "created-by-patch.json"
    patch.write_text(json.dumps({
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "mode": "apply" if apply else "dry-run",
        "stats": dict(stats),
        "files": [{"file": p["file"], "changes": p["changed"]} for p in plans],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"补丁已写出: {patch}")

    if not apply:
        print("\n（dry-run 结束，未改动任何数据。加 --apply 才写入）")
        return

    # 备份 + 写入
    bk = BACKUP / time.strftime("created-by-%Y%m%d-%H%M%S")
    bk.mkdir(parents=True, exist_ok=True)
    for p in plans:
        src = Path(p["file"])
        shutil.copy2(src, bk / src.name)
        snap = json.loads(src.read_text(encoding="utf-8"))
        snap["edges"] = p["new_edges"]
        src.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
        print(f"  已写入 {src.name}（备份 {bk.name}）")
    print(f"\n完成。回滚命令：python tools/apply_created_by_fix.py --rollback \"{bk}\"")


if __name__ == "__main__":
    main()
