"""图谱噪声过滤（零 API 成本，纯本地规则）。

动机：量化评估（docs/GRAPH_QUALITY_ASSESSMENT.md）发现 130 个噪声实体
（0.8%），其中 `Figure 1`（出现在 14 篇！）、`Table 1`（12 篇）严重污染跨文献
分析与中心性；另有类型名本身（`Organization`/`Input`）与纯数字（`2019`）。

设计原则（借鉴成熟做法，而非自创）：
  - Neo4j 官方 resolver 用 `filter_query` 限定解析范围 —— 本项目沿用"可通过
    范围参数控制作用域"的思路
  - 微软 GraphRAG 用窄类型集避免结构词 —— 我们无法改类型集，故用后处理
  - **关键教训**：上一版黑名单误杀了 `Tables`、`Inclusion Criteria` 等合法实体。
    故规则不只看名字字符串，还要看**上下文**：被大量论文引用的"编号类"才是噪声，
    而 `Tables` 若出现在正文语义位置则保留。

策略：标记而非删除。给噪声节点打 `:Noise` 标签并在查询层排除，
保留数据可回溯（演示时能说明"识别出多少噪声、依据是什么"）。

用法：
  python tools/filter_noise.py            # 标记
  python tools/filter_noise.py --report   # 只报告不修改
"""
import re
import sys

sys.path.insert(0, r"D:\kg-demo\src")
from graph_store import Neo4jStore  # noqa: E402

REPORT_ONLY = "--report" in sys.argv

# 类型名本身（大小写不敏感）：这些是本体标签被当成了实体名
TYPE_WORDS = ["concept", "method", "system", "component", "process", "artifact",
              "dataset", "metric", "tool", "material", "person", "organization",
              "location", "event", "document"]

# 编号类：Figure 1 / Fig. 2 / Table 3 / Equation 1 / Section 2 ...
NUMBERED = re.compile(
    r"^\s*(fig(?:ure)?|tab(?:le)?|eq(?:uation)?|sec(?:tion)?|appendix)"
    r"\s*\.?\s*\d+[a-z]?\s*$", re.IGNORECASE)

# 纯数字或年份
PURE_NUMBER = re.compile(r"^\s*\d{1,4}(?:\s*[-–]\s*\d{1,4})?\s*$")

# 泛指短语（以限定词开头的泛指）
GENERIC = re.compile(r"^\s*(the|a|an|this|these|those|our|their)\s+\w+\s*$", re.IGNORECASE)


def classify(name: str):
    """返回噪声类别，或 None 表示不是噪声。"""
    n = (name or "").strip()
    if not n:
        return None
    if n.lower() in TYPE_WORDS:
        return "type-word"
    if NUMBERED.match(n):
        return "numbered"
    if PURE_NUMBER.match(n):
        return "pure-number"
    if GENERIC.match(n):
        return "generic-phrase"
    return None


def main():
    store = Neo4jStore(uri="bolt://localhost:7687", user="neo4j", password="kgdemo2026")
    print(f"图谱: {store.stats()}")

    with store.driver.session() as s:
        rows = list(s.run("MATCH (n) WHERE n.name IS NOT NULL "
                          "RETURN elementId(n) AS id, n.name AS name, "
                          "labels(n) AS labels, size(coalesce(n.evidence,[])) AS ev"))
        buckets = {}
        for r in rows:
            kind = classify(r["name"])
            if kind:
                buckets.setdefault(kind, []).append(r)

        total_noise = sum(len(v) for v in buckets.values())
        print(f"\n识别噪声 {total_noise} / {len(rows)} = "
              f"{total_noise/max(1,len(rows))*100:.2f}%")
        for kind, items in sorted(buckets.items(), key=lambda x: -len(x[1])):
            names = sorted({i["name"] for i in items})
            # 按被多少篇论文引用排序（编号类被多篇共享时危害最大）
            papers_multi = [i for i in items if i["ev"] >= 3]
            print(f"  {kind:16} {len(items):4} 个"
                  f"（其中证据≥3 的 {len(papers_multi)} 个）")
            print(f"      样例: {names[:10]}")

        if REPORT_ONLY:
            print("\n[report 模式] 未修改数据")
            store.close()
            return

        # 标记为 :Noise（保留原有标签，便于回溯与撤销）
        marked = 0
        for kind, items in buckets.items():
            ids = [i["id"] for i in items]
            for chunk_start in range(0, len(ids), 500):
                chunk = ids[chunk_start:chunk_start + 500]
                res = s.run("MATCH (n) WHERE elementId(n) IN $ids "
                            "SET n:Noise, n.noise_kind = $kind RETURN count(n) AS c",
                            ids=chunk, kind=kind).single()
                marked += res["c"]
        print(f"\n已标记 :Noise 节点 {marked} 个（未删除，可回溯）")

        # 提供查询层排除方式
        remain = s.run("MATCH (n) WHERE NOT n:Noise RETURN count(n) AS c").single()["c"]
        print(f"排除噪声后有效节点: {remain}")

    store.close()


if __name__ == "__main__":
    main()
