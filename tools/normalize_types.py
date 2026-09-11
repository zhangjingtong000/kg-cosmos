"""类型归一化：同一实体名只保留一个 primary_type。

【问题】实体归一化后仍有 75 组「同名多类型」，例如：
    naturallanguageprocessing  {Concept: 12, Method: 4}
    arxiv                      {System: 8, Document: 4, Organization: 3}
    figure1                    {Document: 7, Artifact: 5}
  这不是命名问题，而是**本体类型标注不一致**——同一实体在不同论文/chunk
  被标成了不同类型。

【主流做法（调研已核实）】
  LightRAG / nano-graphrag：多数投票定 `primary_type`，并把全部类型写进
  `types` 数组保留信息（不丢证据）。
  ⚠️ 不要用 `apoc.refactor.mergeNodes(properties:'discard')` —— 会丢 list 属性。

【本脚本策略（保守，可回滚）】
  1. 每个归一名（大小写+标点归一）内按出现频次投票，取最高者为 primary_type
  2. **平票时保留原类型不动**（不猜）
  3. 把该名下出现过的全部类型写入节点的 `types` 数组 + `type_votes` 计数
  4. 只改 `type` 字段，不动 `name`（命名已由 entity_normalize.py 处理）
  5. 输出冲突清单，供人工审阅高频冲突

【安全设计】
  · 默认 dry-run，`--apply` 才写
  · 写前备份到 data/quality/backup/types-<ts>/
  · `--rollback <dir>` 恢复

用法：
  python tools/normalize_types.py                 # dry-run
  python tools/normalize_types.py --apply
  python tools/normalize_types.py --rollback <dir>
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

# 本体 15 类（与 graph_store.ALLOWED_LABELS 一致）；出现其它值说明有越界标签
ONTOLOGY = {
    "Concept", "Method", "System", "Component", "Process", "Artifact",
    "Dataset", "Metric", "Tool", "Material", "Person", "Organization",
    "Location", "Event", "Document",
}


def parse_args():
    ap = argparse.ArgumentParser(description="类型归一化")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--rollback", default="")
    ap.add_argument("--top", type=int, default=20)
    return ap.parse_args()


ARGS = parse_args()


def nkey(n: str) -> str:
    """归一键：NFKC + 小写 + 折叠空白 + 去标点。与 entity_normalize 的口径一致。"""
    s = unicodedata.normalize("NFKC", n or "").lower()
    s = re.sub(r"[\u2018\u2019\u02bc\u2032`]", "'", s)
    s = re.sub(r"[\u2010-\u2015\u2212]", "-", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def load():
    """返回 [(file, snap)] 与按归一键聚合的类型投票。"""
    snaps = []
    votes = defaultdict(Counter)
    for f in sorted(GRAPHS.glob("*.json")):
        try:
            snap = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  [skip] {f.name}: {e}")
            continue
        snaps.append((f, snap))
        for n in snap.get("nodes", []):
            k = nkey(n.get("name", ""))
            if k:
                votes[k][n.get("type") or "?"] += 1
    return snaps, votes


def main():
    if ARGS.rollback:
        src = Path(ARGS.rollback)
        files = list(src.glob("*.json"))
        for b in files:
            shutil.copy2(b, GRAPHS / b.name)
        print(f"已回滚 {len(files)} 个文件")
        return

    print("=== 类型归一化" + ("｜APPLY" if ARGS.apply else "｜dry-run") + " ===\n")
    snaps, votes = load()
    print(f"实体归一键 {len(votes)}")

    # 越界类型检查
    oob = Counter()
    for k, c in votes.items():
        for t, n in c.items():
            if t not in ONTOLOGY:
                oob[t] += n
    if oob:
        print(f"\n⚠️ 越界类型（不在 15 类本体内）: {len(oob)} 种")
        for t, n in oob.most_common(10):
            print(f"   {t:24s} {n}")

    # 冲突统计
    conflict = {k: c for k, c in votes.items() if len(c) > 1}
    tie = {k: c for k, c in conflict.items()
           if len(c.most_common(2)) > 1 and c.most_common(2)[0][1] == c.most_common(2)[1][1]}
    print(f"\n── 冲突规模 ──")
    print(f"   同名多类型组        {len(conflict)}")
    print(f"   其中最高票平票      {len(tie)}  ← 平票不动（不猜）")
    settled = len(conflict) - len(tie)
    print(f"   可确定 primary_type {settled}")

    print(f"\n── 高频冲突样本（按总出现次数，前 {ARGS.top}）──")
    ranked = sorted(conflict.items(), key=lambda x: -sum(x[1].values()))
    for k, c in ranked[:ARGS.top]:
        is_tie = k in tie
        mark = "  ⚠️平票不动" if is_tie else ""
        top = c.most_common(1)[0]
        print(f"   {sum(c.values()):4d}× {k[:40]:40s} {dict(c.most_common(4))}"
              f"  → {top[0]}{mark}")

    # 应用
    if not ARGS.apply:
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "type-normalization-plan.json").write_text(json.dumps({
            "keys": len(votes), "conflict": len(conflict), "tie": len(tie),
            "out_of_ontology": dict(oob),
            "plan": {k: {"primary": c.most_common(1)[0][0], "votes": dict(c),
                         "tie": k in tie}
                     for k, c in conflict.items()},
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n计划已写出: {OUT / 'type-normalization-plan.json'}")
        print("\n（dry-run，未改数据。加 --apply 写入）")
        return

    bk = BACKUP / time.strftime("types-%Y%m%d-%H%M%S")
    bk.mkdir(parents=True, exist_ok=True)
    for f, _ in snaps:
        shutil.copy2(f, bk / f.name)

    stats = Counter()
    for f, snap in snaps:
        touched = False
        for n in snap.get("nodes", []):
            k = nkey(n.get("name", ""))
            if not k or k not in conflict:
                continue
            c = conflict[k]
            if k in tie:
                continue                      # 平票不动
            primary = c.most_common(1)[0][0]
            old = n.get("type")
            if old != primary:
                stats[f"{old}->{primary}"] += 1
            n["type"] = primary
            n["types"] = sorted(c)
            n["type_votes"] = dict(c)
            touched = True
            stats["nodes"] += 1
        if touched:
            f.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")

    print(f"\n── 已写入 ──")
    print(f"   改动节点 {stats['nodes']}")
    print(f"   类型变更分布 Top 12:")
    for k, v in sorted(((k, v) for k, v in stats.items() if k != "nodes"),
                       key=lambda x: -x[1])[:12]:
        print(f"     {k:34s} {v}")
    print(f"   备份: {bk}")
    print(f"   回滚: python tools/normalize_types.py --rollback \"{bk}\"")


if __name__ == "__main__":
    main()
