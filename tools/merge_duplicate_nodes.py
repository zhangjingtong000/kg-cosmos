"""合并被拆分的同名节点（保守分层策略）。

根因：Neo4jStore 用 `MERGE (x:Label {name})`，标签算进节点身份，于是同名实体
被不同论文标成不同类型时会生成多个节点（KG 同时是 Concept/Dataset/Artifact）。
后果：度数被拆散（KG 实际 828 度被拆成 323/323/182），社区检测与中心性分析失真。

分层策略（只在证据支持时才合并）：
  第 1 层：同名 + 同标签            → 纯重复，安全合并
  第 2 层：同名 + 跨标签 + 证据重叠  → 同一实体被标了不同类型，安全合并
  第 3 层：同名 + 跨标签 + 证据不重叠 → 可能是语义不同的实体，只报告不合并

用法：python tools/merge_duplicate_nodes.py [--dry-run]
"""
# 路径以本文件为锚点解析，不写死盘符——演示机不一定把仓库放在 D 盘。
from pathlib import Path as _Path
_HERE = _Path(__file__).resolve()
ROOT = _HERE.parent.parent          # tools/*.py 与 src/*.py 都在根目录下一层
import sys

sys.path.insert(0, str(ROOT / "src"))
from graph_store import Neo4jStore  # noqa: E402

DRY = "--dry-run" in sys.argv
store = Neo4jStore(uri="bolt://localhost:7687", user="neo4j", password="kgdemo2026")
print(f"合并前: {store.stats()}")

GROUPS = """
MATCH (n) WHERE n.name IS NOT NULL
WITH toLower(trim(n.name)) AS key, collect(n) AS ns, count(*) AS c
WHERE c > 1
RETURN key, ns, c ORDER BY c DESC
"""


def first_label(node):
    """Neo4j 返回的 labels 是 frozenset，不能下标取。"""
    try:
        return next(iter(node.labels)) if node.labels else "?"
    except TypeError:
        return "?"


# 标签优先级：越靠前越"具体"，同名实体保留最具体的标签。
# 依据：一个被多篇论文标成不同类型的概念实体（如 KG = Concept/Dataset/Artifact），
# 保留 Concept 比保留 Artifact 更接近它的语义。Document/Artifact 常是"顺带抽到"
# 的笼统标签（论文标题、DOI 记录），优先级最低。
LABEL_PRIORITY = [
    "System", "Method", "Dataset", "Tool", "Metric", "Component", "Process",
    "Concept", "Organization", "Person", "Location", "Event", "Material",
    "Artifact", "Document",
]

# 类型名本身被当作实体名的污染项，参与合并但不作为代表标签来源
POLLUTED_NAMES = {
    "concept", "method", "system", "component", "process", "artifact", "dataset",
    "metric", "tool", "material", "person", "organization", "location", "event",
    "document", "entity", "relation", "relationship", "table", "figure",
}


def label_rank(node):
    lb = first_label(node)
    try:
        return LABEL_PRIORITY.index(lb)
    except ValueError:
        return len(LABEL_PRIORITY)


def evidence_keys(node):
    out = set()
    for ev in node.get("evidence") or []:
        if isinstance(ev, str):
            out.add(ev)
    return out


def merge_into(s, keep, others):
    """把 others 合并进 keep：转移边（同类型同方向去重）、证据并集、删除 others。"""
    moved = 0
    keep_id = keep.element_id
    for n in others:
        eid = n.element_id
        for cypher, params in (
            ("MATCH (x)-[r]->(y) WHERE elementId(x) = $eid "
             "RETURN type(r) AS t, elementId(y) AS yid, r.description AS d, r.evidence AS ev",
             {"eid": eid}),
            ("MATCH (x)<-[r]-(y) WHERE elementId(x) = $eid "
             "RETURN type(r) AS t, elementId(y) AS yid, r.description AS d, r.evidence AS ev",
             {"eid": eid}),
        ):
            outgoing = "->" in cypher.split("WHERE")[0]
            for rel in s.run(cypher, **params):
                if rel["yid"] == keep_id:
                    continue
                if outgoing:
                    merge_q = ("MATCH (a), (b) WHERE elementId(a) = $aid "
                               "AND elementId(b) = $bid ")
                    merge_q += f"MERGE (a)-[nr:`{rel['t']}`]->(b) "
                else:
                    merge_q = ("MATCH (a), (b) WHERE elementId(a) = $aid "
                               "AND elementId(b) = $bid ")
                    merge_q += f"MERGE (b)-[nr:`{rel['t']}`]->(a) "
                merge_q += ("ON CREATE SET nr.description = $d, nr.evidence = [] "
                            "SET nr.evidence = coalesce(nr.evidence, []) + "
                            "[x IN coalesce($ev, []) WHERE NOT x IN coalesce(nr.evidence, [])]")
                s.run(merge_q, aid=keep_id, bid=rel["yid"], d=rel["d"], ev=rel["ev"] or [])
                moved += 1

    all_ev = []
    for n in [keep] + others:
        for e in (n.get("evidence") or []):
            if e not in all_ev:
                all_ev.append(e)
    s.run("MATCH (x) WHERE elementId(x) = $aid SET x.evidence = $ev",
          aid=keep_id, ev=all_ev)
    s.run("MATCH (x) WHERE elementId(x) IN $eids DETACH DELETE x",
          eids=[n.element_id for n in others])
    return moved


def degree(s, element_id):
    return s.run("MATCH (x)-[r]-() WHERE elementId(x) = $eid RETURN count(r) AS d",
                 eid=element_id).single()["d"]


stats = {"layer1": 0, "layer2": 0, "layer3": 0, "edges": 0, "groups": 0, "polluted": 0}
with store.driver.session() as s:
    groups = list(s.run(GROUPS))
print(f"同名组 {len(groups)}\n")

with store.driver.session() as s:
    for g in groups:
        ns = g["ns"]
        labels = [first_label(n) for n in ns]
        ev_sets = [evidence_keys(n) for n in ns]
        key = g["key"]

        same_label = len(set(labels)) == 1
        overlap = any(ev_sets[i] & ev_sets[j]
                      for i in range(len(ev_sets))
                      for j in range(i + 1, len(ev_sets)))

        if same_label:
            layer = "layer1"
        elif overlap:
            layer = "layer2"
        else:
            layer = "layer3"

        stats["groups"] += 1
        if layer == "layer3":
            stats["layer3"] += 1
            # 类型名本身（concept/entity/document...）作为实体名属污染项，
            # 合并它们没有意义（名字不携带语义），只报告。
            if key in POLLUTED_NAMES:
                stats["polluted"] += 1
                continue
            # 其余同名跨标签实体：按标签优先级合并到最具体的标签
            best = min(ns, key=lambda n: (label_rank(n), -degree(s, n.element_id)))
        else:
            best, best_deg = None, -1
            for n in ns:
                d = degree(s, n.element_id)
                if d > best_deg:
                    best_deg, best = d, n

        others = [n for n in ns if n.element_id != best.element_id]

        if DRY:
            print(f"  [dry-{layer}] {key[:36]:36} {g['c']} -> 1"
                  f"（保留标签 {first_label(best)}）")
            stats[layer] += len(others)
            continue

        stats["edges"] += merge_into(s, best, others)
        stats[layer] += len(others)

print("\n=== 结果 ===")
print(f"  处理组数: {stats['groups']}")
print(f"  第 1 层（同名同标签）合并: {stats['layer1']} 个节点")
print(f"  第 2 层（跨标签但证据重叠）合并: {stats['layer2']} 个节点")
print(f"  第 3 层（跨标签、按标签优先级）合并: {stats['layer3']} 个节点")
print(f"  跳过（类型名污染项）: {stats['polluted']} 组")
if not DRY:
    print(f"  转移边数: {stats['edges']}")
    print(f"  合并后: {store.stats()}")
store.close()
