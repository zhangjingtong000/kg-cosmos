"""清理节点合并的副产物：自环边与同类型同方向的重复边。"""
import sys

sys.path.insert(0, r"D:\kg-demo\src")
from graph_store import Neo4jStore  # noqa: E402

store = Neo4jStore(uri="bolt://localhost:7687", user="neo4j", password="kgdemo2026")
print(f"清理前: {store.stats()}")

with store.driver.session() as s:
    loops = s.run("MATCH (a)-[r]->(a) DELETE r RETURN count(r) AS c").single()["c"]
    print(f"删除自环边: {loops}")

    # 同类型同方向重复边：保留一条（证据最多的），把其余的证据并入后删除
    dup_groups = list(s.run("""
        MATCH (a)-[r]->(b)
        WITH a, b, type(r) AS t, collect(r) AS rs
        WHERE size(rs) > 1
        RETURN elementId(a) AS aid, elementId(b) AS bid, t, [x IN rs | elementId(x)] AS rids
    """))
    merged_ev = removed = 0
    for g in dup_groups:
        keep_id, drop_ids = g["rids"][0], g["rids"][1:]
        # 把所有证据并入保留的那条
        s.run("""
            MATCH (a)-[r]->(b)
            WHERE elementId(a) = $aid AND elementId(b) = $bid AND type(r) = $t
            WITH collect(r) AS rs
            WITH rs[0] AS keep, rs[1..] AS drops
            SET keep.evidence = reduce(acc = coalesce(keep.evidence, []), d IN drops |
                acc + [e IN coalesce(d.evidence, []) WHERE NOT e IN acc])
            FOREACH (d IN drops | DELETE d)
        """, aid=g["aid"], bid=g["bid"], t=g["t"])
        removed += len(drop_ids)
    print(f"合并同类型同方向重复边: 删除 {removed} 条（证据已并入保留边）")

    remain = s.run("MATCH (a)-[r]->(a) RETURN count(r) AS c").single()["c"]
    dup2 = s.run("""
        MATCH (a)-[r]->(b) WITH a, b, type(r) AS t, count(*) AS c
        WHERE c > 1 RETURN count(*) AS groups
    """).single()["groups"]
    print(f"验证: 自环 {remain}，残留重复组 {dup2}")

print(f"清理后: {store.stats()}")
store.close()
