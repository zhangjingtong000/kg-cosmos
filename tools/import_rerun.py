"""把重跑后的 channel-cloud-<paper>.json 导入 Neo4j，精确替换旧的来源数据。

做法：
  1. 按证据里的 source_id 找出属于这些论文的节点，DETACH DELETE
  2. 导入新数据的节点与边（Neo4jStore.import_data 自带本体白名单与端点匹配）

注意：删除节点会连带删除它与其他论文共享的边。同一实体在多篇里出现时，
Neo4j 中只有一份节点（MERGE by name），因此删除会波及跨文献关系。这是精确
替换的固有代价；若需完全干净，应清库后重跑全部语料（见文档说明）。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, r"D:\kg-demo\src")
from graph_store import Neo4jStore  # noqa: E402

PAPERS = ["2506.09566v1", "2406.08223v2", "2510.20345v1", "1703.08098v9", "2202.07412v1"]
GOLD_DIR = Path(r"D:\kg-demo\data\gold")

store = Neo4jStore(uri="bolt://localhost:7687", user="neo4j", password="kgdemo2026")
print(f"导入前: {store.stats()}")

with store.driver.session() as s:
    # 统计待删除规模
    for pid in PAPERS:
        rec = s.run(
            "MATCH (n) WHERE any(ev IN coalesce(n.evidence, []) "
            "WHERE ev CONTAINS $pid) RETURN count(n) AS c", pid=f'"{pid}"').single()
        print(f"  {pid}: {rec['c']} 个节点带该来源证据")

    total_deleted = 0
    for pid in PAPERS:
        res = s.run(
            "MATCH (n) WHERE any(ev IN coalesce(n.evidence, []) "
            "WHERE ev CONTAINS $pid) DETACH DELETE n RETURN count(n) AS c",
            pid=f'"{pid}"').single()
        total_deleted += res["c"]
        print(f"  已删除 {pid} 的节点: {res['c']}")
    print(f"合计删除 {total_deleted} 个节点")

print(f"删除后: {store.stats()}")

imported_nodes = imported_edges = 0
for pid in PAPERS:
    path = GOLD_DIR / f"channel-cloud-{pid}.json"
    if not path.exists():
        print(f"  [skip] {pid} 缺文件")
        continue
    d = json.loads(path.read_text(encoding="utf-8"))
    # 证据里的 source_id 需与快照一致
    for n in d["nodes"]:
        for ev in n.get("evidence", []):
            ev["source_id"] = pid
    for e in d["edges"]:
        for ev in e.get("evidence", []):
            ev["source_id"] = pid
    store.import_data(d["nodes"], d["edges"])
    imported_nodes += len(d["nodes"])
    imported_edges += len(d["edges"])
    print(f"  导入 {pid}: {len(d['nodes'])} 节点 / {len(d['edges'])} 关系")

print(f"\n导入合计: {imported_nodes} 节点 / {imported_edges} 关系")
print(f"导入后: {store.stats()}")

# 验证：这些论文的节点与边是否完整
with store.driver.session() as s:
    for pid in PAPERS:
        n = s.run("MATCH (n) WHERE any(ev IN coalesce(n.evidence, []) "
                  "WHERE ev CONTAINS $pid) RETURN count(n) AS c",
                  pid=f'"{pid}"').single()["c"]
        e = s.run("MATCH ()-[r]->() WHERE any(ev IN coalesce(r.evidence, []) "
                  "WHERE ev CONTAINS $pid) RETURN count(r) AS c",
                  pid=f'"{pid}"').single()["c"]
        print(f"  验证 {pid}: {n} 节点 / {e} 关系")

store.close()
