"""清空 Neo4j 中的图谱数据。

依据：2026-09-10 核实的现状——库内 1103 个节点分属两篇文献，但这两篇的快照
是空的（0 节点），快照与图谱互相矛盾；且抽取器存在"云端实体被 100% 丢弃"的
解析 bug（已修）。旧数据无法作为"入库完成"的证据，保留只会污染后续统计。

用法：python tools/reset_graph.py            # 交互确认
      python tools/reset_graph.py --yes      # 直接清空
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from graph_store import Neo4jStore  # noqa: E402

URI = "bolt://localhost:7687"
USER = "neo4j"
PASSWORD = "kgdemo2026"


def main() -> None:
    store = Neo4jStore(uri=URI, user=USER, password=PASSWORD)
    before = store.stats()
    print(f"清空前: {before}")

    if "--yes" not in sys.argv:
        answer = input("确认清空所有图谱节点与关系？输入 yes 继续: ").strip().lower()
        if answer != "yes":
            print("已取消")
            return

    with store.driver.session() as s:
        s.run("MATCH (n) DETACH DELETE n")
        remaining = s.run("MATCH (n) RETURN count(n) AS c").single()["c"]

    print(f"清空后: nodes={remaining} stats={store.stats()}")
    store.close()


if __name__ == "__main__":
    main()
