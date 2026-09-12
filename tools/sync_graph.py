"""把全部快照同步到 Neo4j（清库后完整重导）。

【为什么必须重导】
  快照经过 4 轮改动，Neo4j 里还是旧数据：
    1. apply_created_by_fix.py   反转 2348 条 CREATED_BY、重标 165 条为 CONTRIBUTED_TO
    2. entity_normalize.py       合并 386 个重复实体名（改节点名 + 边端点）
    3. normalize_types.py        统一 primary_type（1224 个节点）
    4. apply_confidence_filter.py 标记 9792 条低支持边为 _filtered
  不重导的话，M3 前端读到的全是旧图。

【导入规则】
  · 节点：全部导入
  · 边：**跳过 `_filtered` 标记的低支持边**（它们仅在快照里保留以便审计）
  · 复用 Neo4jStore.import_data（自带本体白名单、端点规范化、证据合并）

【安全设计】
  · 默认 dry-run，打印将要导入的规模
  · `--apply` 才清库并导入
  · 清库前把图库统计写入 data/quality/backup/graph-stats-<ts>.json

用法：
  python tools/sync_graph.py            # dry-run
  python tools/sync_graph.py --apply
"""
import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, r"D:\kg-demo\src")
from graph_store import Neo4jStore  # noqa: E402

GRAPHS = Path(r"D:\kg-demo\data\corpus\graphs")
OUT = Path(r"D:\kg-demo\data\quality")
BACKUP = OUT / "backup"

NEO4J = dict(uri="bolt://localhost:7687", user="neo4j", password="kgdemo2026")

# 本体（15 类关系）
# CONTRIBUTED_TO 于 2026-09-12 按用户决策移除：它是 T3.1 做 CREATED_BY 方向归一
# 时 relabel 出的产物（Person → 会议），L5 通过率仅 32.1%，且在 15 类本体里
# 无处安放。此前它被收进白名单（本体临时变 16 类），而 l5_ontology_audit.py
# 仍按 15 类审计，导致这 165 条边在 L4 里被算成"越界"。删除后两处口径统一。
ONTOLOGY_RELS = {
    "PART_OF", "USES", "PRODUCES", "MEASURES", "EVALUATES", "COMPARES_WITH",
    "SUPPORTS", "CONTRADICTS", "CAUSES", "ENABLES", "APPLIES_TO", "LOCATED_AT",
    "CREATED_BY", "CITES", "RELATED_TO",
}
ONTOLOGY_TYPES = {
    "Concept", "Method", "System", "Component", "Process", "Artifact",
    "Dataset", "Metric", "Tool", "Material", "Person", "Organization",
    "Location", "Event", "Document",
}

# 后处理专用标签（不属于本体）。从边反建出来的端点标 Unknown
# （见 tools/backfill_missing_endpoints.py），它不能被算进"本体 15 类"，
# 否则 l8 的类型统计、白名单测试都会跟着错——这正是 ERROR_CASES 案例 12
# 说的"同一规则写两处"。与 src/graph_store.py 的 DERIVED_LABELS 保持一致。
DERIVED_TYPES = {"Unknown"}


def parse_args():
    ap = argparse.ArgumentParser(description="同步快照到 Neo4j")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--no-reset", action="store_true",
                    help="不清库，直接续跑导入（MERGE 幂等，用于中断后续传）")
    return ap.parse_args()


ARGS = parse_args()


def main():
    files = sorted(GRAPHS.glob("*.json"))
    print(f"=== 同步快照 → Neo4j" + ("｜APPLY" if ARGS.apply else "｜dry-run") + " ===")
    print(f"快照 {len(files)} 个\n")

    nodes_all, edges_ok, edges_filtered = [], [], 0
    oob_edges, loops = [], []
    dangling = []
    type_counter = Counter()
    rel_counter = Counter()
    node_names = set()          # 用于悬空边检测（与 import_data 的匹配口径一致）

    # 先收集全部节点名
    snaps = []
    for f in files:
        snap = json.loads(f.read_text(encoding="utf-8"))
        snaps.append((f, snap))
        for n in snap.get("nodes", []):
            nm = (n.get("name") or "").strip()
            if nm:
                node_names.add(nm.lower())

    for f, snap in snaps:
        for n in snap.get("nodes", []):
            n.pop("_merged_from", None)          # 中间标记不入库
            nodes_all.append(n)
            type_counter[n.get("type")] += 1
        for e in snap.get("edges", []):
            if e.get("_filtered"):
                edges_filtered += 1
                continue                          # 低支持边不入库（快照仍保留）
            t = e.get("type") or "?"
            if t not in ONTOLOGY_RELS:
                # 越界关系类型：LLM 把实体名/任务名填进了类型字段
                # （实测如 `DistMult -> DistMult`、`Recommendation Systems -> ...`）
                # 这是抽取错误，不是缺本体；标记后不入库，实体本身仍保留。
                e["_filtered"] = True
                e["_filter_reason"] = f"out-of-ontology relation type: {t}"
                oob_edges.append((t, e.get("source"), e.get("target")))
                continue
            if e.get("source") == e.get("target"):
                e["_filtered"] = True
                e["_filter_reason"] = "self-loop"
                loops.append((t, e.get("source")))
                continue
            # 悬空边：端点从未被抽成节点（关系抽取与实体抽取的边界不一致）
            # 实测占全部边的 ~55%，且 17 篇论文一致出现，属系统性问题。
            # 补建节点需要编造 type（不可验证），因此选择丢弃并如实记录。
            src = (e.get("source") or "").strip().lower()
            tgt = (e.get("target") or "").strip().lower()
            miss = []
            if src not in node_names:
                miss.append("source")
            if tgt not in node_names:
                miss.append("target")
            if miss:
                e["_filtered"] = True
                e["_filter_reason"] = f"dangling endpoints ({'+'.join(miss)} never extracted as nodes)"
                dangling.append((t, e.get("source"), e.get("target"), "+".join(miss)))
                continue
            e.pop("_fix", None)
            edges_ok.append(e)
            rel_counter[t] += 1

    # 孤立节点统计（节点在，但没有任何入边）
    incident = set()
    for e in edges_ok:
        incident.add((e.get("source") or "").strip().lower())
        incident.add((e.get("target") or "").strip().lower())
    iso = sum(1 for n in nodes_all
              if (n.get("name") or "").strip().lower() not in incident)

    print(f"节点 {len(nodes_all)}   边 {len(edges_ok)}")
    print(f"  跳过 _filtered（低支持）: {edges_filtered}")
    print(f"  跳过越界关系类型:        {len(oob_edges)} 条 / {len(set(x[0] for x in oob_edges))} 种")
    print(f"  跳过自环:                {len(loops)}")
    print(f"  跳过悬空边:              {len(dangling)}"
          f"（源缺 {sum(1 for d in dangling if 'source' in d[3])}"
          f" / 目标缺 {sum(1 for d in dangling if 'target' in d[3])}）")
    print(f"  入库后孤立节点:          {iso}")
    if oob_edges:
        oc = Counter(x[0] for x in oob_edges)
        print(f"  越界类型 Top 6: {dict(oc.most_common(6))}")
    print(f"\n实体类型分布: {dict(type_counter.most_common(8))}")
    print(f"关系类型 Top 8: {dict(rel_counter.most_common(8))}")

    oob_types = [t for t in type_counter
                 if t not in ONTOLOGY_TYPES and t not in DERIVED_TYPES]
    print(f"\n越界实体类型: {oob_types or '无 ✅'}")
    print(f"越界关系类型: {'无 ✅（已全部标记过滤）' if not oob_edges else '见上'}")

    # 审计：把丢弃原因写盘（可复现、可核账）
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "sync-filter-audit.json").write_text(json.dumps({
        "nodes": len(nodes_all), "edges_kept": len(edges_ok),
        "skipped": {
            "low_confidence": edges_filtered,
            "out_of_ontology": len(oob_edges),
            "self_loop": len(loops),
            "dangling": len(dangling),
        },
        "isolated_nodes": iso,
        "dangling_by_type": dict(Counter(d[0] for d in dangling).most_common()),
        "dangling_samples": [{"type": d[0], "source": d[1], "target": d[2],
                              "missing": d[3]} for d in dangling[:300]],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n审计已写出: {OUT / 'sync-filter-audit.json'}")

    if not ARGS.apply:
        print("\n（dry-run，未动图库。加 --apply 执行）")
        return

    store = Neo4jStore(**NEO4J)
    print(f"\n导入前: {store.stats()}")

    # 记录清库前状态（续跑模式下不清库，只留档）
    BACKUP.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    (BACKUP / f"graph-stats-{ts}.json").write_text(
        json.dumps(store.stats(), ensure_ascii=False, indent=2), encoding="utf-8")

    print("清库..." if not ARGS.no_reset else "跳过清库（续跑模式）...")
    if not ARGS.no_reset:
        with store.driver.session() as s:
            s.run("MATCH (n) DETACH DELETE n")
        print(f"清库后: {store.stats()}")

    print("导入中...")
    t0 = time.time()
    store.import_data(nodes_all, edges_ok)
    print(f"导入完成 {time.time()-t0:.1f}s")
    print(f"导入后: {store.stats()}")

    # 一致性校验。
    # 这里的白名单**必须引用上面的 ONTOLOGY_RELS**，不能另抄一份：
    # 本轮就吃过这个亏——入库白名单含 CONTRIBUTED_TO（16 类），
    # 而 l5_ontology_audit.py 按 15 类审计，同一个类型在两处口径不同。
    with store.driver.session() as s:
        bad = s.run(
            "MATCH ()-[r]->() WHERE NOT type(r) IN $ok RETURN count(r) AS c",
            ok=sorted(ONTOLOGY_RELS)).single()["c"]
        loops = s.run("MATCH (a)-[r]->(a) RETURN count(r) AS c").single()["c"]
        noev = s.run(
            "MATCH ()-[r]->() WHERE r.evidence IS NULL OR size(r.evidence)=0 "
            "RETURN count(r) AS c").single()["c"]
    print(f"\n── 校验 ──")
    print(f"   越界关系类型 {bad}   自环 {loops}   无证据边 {noev}")
    store.close() if hasattr(store, "close") else None
    print("完成。")


if __name__ == "__main__":
    main()
