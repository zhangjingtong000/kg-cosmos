"""删除 CONTRIBUTED_TO 边（2026-09-12 用户决策）。

【为什么删】
  T3.1 做 CREATED_BY 方向归一时，把 165 条「Person → 会议」的边改标成了
  `CONTRIBUTED_TO`，避免"作者 CREATED_BY 会议"这种错误方向。副作用是引入了
  一个**本体外的关系类型**：`sync_graph.py` 的白名单把它收了进去（本体变 16 类），
  而 `l5_ontology_audit.py` 仍按 15 类审计，于是这 165 条成了 L4 的"越界边"。

  用户决策：**删除**。理由是 L5 通过率仅 32.1%，且「人 → 会议」在 15 类本体里
  无处安放。删除后两处口径统一回 15 类。

【安全设计】
  · 默认 dry-run，只打印将删除什么
  · `--apply` 才写文件；写之前把整份 graphs/ 复制到
    `data/quality/backup/graphs-<ts>/`
  · 同步更新每个快照 `stats.edges` 计数，避免统计与实际不符

用法：
  python tools/apply_contributed_to_removal.py            # dry-run
  python tools/apply_contributed_to_removal.py --apply
"""
import argparse
import json
import shutil
import sys
import time
from pathlib import Path

GRAPHS = Path(r"D:\kg-demo\data\corpus\graphs")
BACKUP_ROOT = Path(r"D:\kg-demo\data\quality\backup")
TARGET = "CONTRIBUTED_TO"


def main():
    ap = argparse.ArgumentParser(description="删除 CONTRIBUTED_TO 边")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    files = sorted(GRAPHS.glob("*.json"))
    if not files:
        raise SystemExit(f"没有找到快照：{GRAPHS}")

    plan = []
    total_before = total_after = removed = 0
    for f in files:
        d = json.loads(f.read_text(encoding="utf-8"))
        edges = d.get("edges") or []
        keep = [e for e in edges if str(e.get("type", "")).upper() != TARGET]
        n = len(edges) - len(keep)
        total_before += len(edges)
        total_after += len(keep)
        removed += n
        if n:
            plan.append((f, d, keep, len(edges), n))

    print(f"快照 {len(files)} 个；边 {total_before} → {total_after}（删除 {removed} 条 {TARGET}）")
    for f, _, keep, before, n in plan:
        print(f"  {f.name}: {before} → {len(keep)}  (-{n})")

    if removed == 0:
        print("没有可删除的边")
        return 0
    if not args.apply:
        print("\n[dry-run] 未写文件。加 --apply 生效。")
        return 1

    ts = time.strftime("%Y%m%d-%H%M%S")
    bak = BACKUP_ROOT / f"graphs-{ts}"
    bak.mkdir(parents=True, exist_ok=True)
    for f in files:
        shutil.copy2(f, bak / f.name)
    print(f"\n已备份 {len(files)} 个快照到 {bak}")

    for f, d, keep, before, n in plan:
        d["edges"] = keep
        if isinstance(d.get("stats"), dict):
            d["stats"]["edges"] = len(keep)
        # 记录这次删改，便于审计与回滚
        d.setdefault("_audit", []).append({
            "op": "remove_type",
            "type": TARGET,
            "removed": n,
            "reason": "T3.1 relabel 产物，本体外类型，L5 通过率 32.1%（用户决策 2026-09-12）",
            "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        f.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  已写出 {f.name}（-{n}）")

    print(f"\n完成：删除 {removed} 条。回滚时把 {bak} 覆盖回 {GRAPHS} 即可。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
