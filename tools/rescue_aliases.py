"""零成本补救：用现有快照重建图谱，导入前对边做端点规范化。

背景：旧管线的边有 44% 因端点失配被丢弃，但**悬空本身不代表关系无效**——
端点大多只是大小写/标点/后缀差异（deduplicate 改名造成）。用修好的
canonicalize_relation_endpoints 在导入时映射，可把边保留率从 55.9% 提到 93.2%，
**无需重新抽取、零 API 成本**。

做法（清库重建，避免旧悬空数据残留）：
  1. 备份现有图谱统计（节点/关系数）
  2. 清空 Neo4j
  3. 逐篇读取快照 → 端点规范化 → 过滤仍无法解析的边 → 导入
  4. 报告每篇的保留率与合并后的总量

用法：python tools/rescue_aliases.py [--dry-run]
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, r"D:\kg-demo\src")
from graph_store import Neo4jStore  # noqa: E402
from kg_extractor import canonicalize_relation_endpoints, deduplicate_entities  # noqa: E402

DRY = "--dry-run" in sys.argv
GRAPHS = Path(r"D:\kg-demo\data\corpus\graphs")
SKIP = ("work-", "probe-", "cmp-", "ctrl-", "bench", "diag", "range", "gold-",
        "cost", "opt", "size", "conc", "local", "prompt", "v3", "ch-", "count-",
        "tmp-", "speedtest", "count-")

# 已用修复后管线重跑的 5 篇：优先用它们（channel-cloud-*.json）
RERUN = {p.stem.replace("channel-cloud-", "")
         for p in Path(r"D:\kg-demo\data\gold").glob("channel-cloud-*.json")}

store = Neo4jStore(uri="bolt://localhost:7687", user="neo4j", password="kgdemo2026")
print(f"重建前: {store.stats()}")

# 收集所有篇的数据源
sources = {}
for p in sorted(GRAPHS.glob("*.json")):
    if p.name.startswith(SKIP):
        continue
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        continue
    if not d.get("nodes"):
        continue
    sources[p.stem] = ("snapshot", d)

# 重跑过的用新数据覆盖
for pid in RERUN:
    p = Path(r"D:\kg-demo\data\gold") / f"channel-cloud-{pid}.json"
    if p.exists():
        sources[pid] = ("rerun", json.loads(p.read_text(encoding="utf-8")))

print(f"\n待导入 {len(sources)} 篇：{len(RERUN)} 篇来自重跑，"
      f"{len(sources)-len(RERUN)} 篇来自旧快照（端点规范化补救）")

if DRY:
    for pid, (kind, d) in sorted(sources.items()):
        print(f"  {pid:18} {kind:9} {len(d['nodes']):5} 节点 / {len(d['edges']):5} 边")
    store.close()
    sys.exit(0)

# 清库
with store.driver.session() as s:
    deleted = s.run("MATCH (n) DETACH DELETE n RETURN count(n) AS c").single()["c"]
print(f"\n已清空旧数据: {deleted} 个节点")
print(f"清空后: {store.stats()}")

report = []
for i, (pid, (kind, d)) in enumerate(sorted(sources.items()), 1):
    nodes = d["nodes"]
    edges_raw = d["edges"]
    # 去重（重跑数据已去重；快照也是去重后的，这里幂等）
    nodes = deduplicate_entities([dict(n) for n in nodes], "cloud")
    for n in nodes:
        for ev in n.get("evidence", []):
            ev["source_id"] = pid
    known = {n["name"].strip().casefold() for n in nodes if n.get("name")}
    canon = canonicalize_relation_endpoints([dict(e) for e in edges_raw], nodes)
    edges, dropped = [], 0
    for e in canon:
        if (e.get("source", "").strip().casefold() in known
                and e.get("target", "").strip().casefold() in known):
            for ev in e.get("evidence", []):
                ev["source_id"] = pid
            edges.append(e)
        else:
            dropped += 1
    store.import_data(nodes, edges)
    keep = len(edges) / max(1, len(edges_raw)) * 100
    report.append((pid, kind, len(nodes), len(edges_raw), len(edges), keep, dropped))
    print(f"  [{i}/{len(sources)}] {pid:18} {len(nodes):5} 节点 "
          f"边 {len(edges_raw):5} -> {len(edges):5}（保留 {keep:5.1f}%，丢弃 {dropped}）")

print(f"\n最终: {store.stats()}")
print("\n=== 汇总 ===")
print(f"{'论文':<18}{'来源':<10}{'节点':>7}{'边':>7}{'保留':>7}{'保留率':>8}")
for pid, kind, n, e0, e1, keep, _ in report:
    print(f"{pid:<18}{kind:<10}{n:>7}{e1:>7}{e0-e1:>7}{keep:>7.1f}%")
tot0 = sum(r[3] for r in report)
tot1 = sum(r[4] for r in report)
print(f"{'合计':<18}{'':<10}{sum(r[2] for r in report):>7}{tot1:>7}{tot0-tot1:>7}"
      f"{tot1/max(1,tot0)*100:>7.1f}%")

store.close()
