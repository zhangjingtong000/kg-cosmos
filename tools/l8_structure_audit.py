"""结构指标审计 + 随机图基线（补六层未覆盖的部分）。

【为什么补这一层】
  六层全是**内部一致性**校验（实体在不在引文里、端点在不在节点集里、
  谓词在不在本体里、有没有引文）。调研报告（见 MAINSTREAM_SOLUTIONS_SURVEY
  §3.1 / §3.9）指出还缺一类**结构指标 + 随机图基线**——GraphRAG-Bench 的
  `indexing_eval` 就是用 density / connectivity / clustering coefficient
  这一组来评图谱构建质量的，而且必须**绑定同规模随机图基线**，
  否则"模块度 0.675""平均度 5.1"这类数字没有参照系，读者无法判断好坏。

  本脚本只做**规则计算**，不调任何模型，成本 ¥0、秒级。

【口径声明（重要）】
  使用的是 `web/kg-data/nebula.json`：它是**概览层导出**，
  边已按无向去重（原图 44032 条有向边 → 此处 21186 条无向边），
  且不含尘埃层以外的截断。因此下面的密度/聚类系数是**无向简化图**上的值，
  不能与有向图口径混用。

用法：
  python tools/l8_structure_audit.py
"""
import json
import random
import statistics as st
from collections import Counter, defaultdict
from pathlib import Path

import networkx as nx

WEB = Path(r"D:\kg-demo\web\kg-data")
QUAL = Path(r"D:\kg-demo\data\quality")
OUT = QUAL / "l8-structure-audit.json"

ONTOLOGY_ENTITIES = 15      # 本体实体类型数
ONTOLOGY_RELATIONS = 15     # 本体关系类型数
N_RANDOM = 10               # 随机图基线重复次数


def main():
    d = json.loads((WEB / "nebula.json").read_text(encoding="utf-8"))
    nodes, edges = d["nodes"], d["edges"]
    print(f"载入 {len(nodes)} 节点 / {len(edges)} 边（概览层导出，无向去重后）")

    G = nx.Graph()
    for n in nodes:
        G.add_node(n["id"], t=n.get("t") or "Concept")
    for e in edges:
        if e[0] != e[1]:
            G.add_edge(e[0], e[1], type=e[2] if len(e) > 2 else "RELATED_TO")

    N = G.number_of_nodes()
    M = G.number_of_edges()
    density = 2 * M / (N * (N - 1)) if N > 1 else 0.0
    cc = nx.average_clustering(G)
    comps = sorted(nx.connected_components(G), key=len, reverse=True)
    degrees = [d_ for _, d_ in G.degree()]
    isolated = sum(1 for x in degrees if x == 0)
    print(f"节点 {N} 边 {M} 密度 {density:.3e} 平均聚类系数 {cc:.4f}")
    print(f"连通分量 {len(comps)} 个，最大分量 {len(comps[0])} 节点"
          f"（{len(comps[0])/N*100:.1f}%），孤立节点 {isolated}")

    # ── 随机图基线：同 N 同 M 的 G(N,M)，重复 N_RANDOM 次 ──
    # 这是让上述数字"有参照系"的关键：图谱的聚类系数远高于随机图，
    # 才说明社区结构不是随机的产物。
    print(f"\n生成 {N_RANDOM} 个同规模随机图基线（N={N}, M={M}）…")
    rng = random.Random(42)
    r_cc, r_comp = [], []
    for i in range(N_RANDOM):
        R = nx.gnm_random_graph(N, M, seed=rng.randint(0, 10 ** 9))
        r_cc.append(nx.average_clustering(R))
        r_comp.append(len(sorted(nx.connected_components(R), key=len, reverse=True)[0]))
    cc_mu, cc_sd = st.mean(r_cc), st.pstdev(r_cc)
    ratio = cc / cc_mu if cc_mu else float("inf")
    print(f"  随机图聚类系数 {cc_mu:.5f} ± {cc_sd:.5f} ⇒ 实测/随机 = {ratio:.1f}×")
    print(f"  随机图最大分量 {st.mean(r_comp):.0f} ± {st.pstdev(r_comp):.0f} 节点"
          f"（实测 {len(comps[0])}）")

    # ── 简洁度与谓词空间（KGHeartBeat 口径）──
    rel_types = Counter(e[2] for e in edges if len(e) > 2)
    distinct_rel = len(rel_types)
    conciseness = ONTOLOGY_RELATIONS / distinct_rel if distinct_rel else 0.0
    print(f"\n关系谓词：实际出现 {distinct_rel} 种，本体定义 {ONTOLOGY_RELATIONS} 种")
    print(f"  简洁度（本体数/实际数）= {conciseness:.3f}"
          f"（=1 表示谓词空间恰好等于本体；越小说明膨胀越严重）")
    print("  ⚠️ **口径警告**：这里用的是入库后的数据，越界谓词已被 `safe_relation_type`")
    print("     强制改写成 RELATED_TO，所以看不出原始膨胀。原始抽取里有 147 种越界")
    print("     类型（402 条边，见 L5_SIX_LAYER_REPORT §3.3 发现 1），")
    print("     按那一口径的真实简洁度约为 15/161 ≈ 0.09 —— 差了一个数量级。")
    print("     论文里报简洁度时必须写明用的是「入库前」还是「入库后」的谓词空间。")
    top_rel = rel_types.most_common(8)
    print(f"  最高频：{top_rel}")

    # ── 类型冲突：同名不同类 ──
    name_types = defaultdict(set)
    for n in nodes:
        name_types[n["id"]].add(n.get("t") or "Concept")
    conflict = {k: sorted(v) for k, v in name_types.items() if len(v) > 1}
    print(f"\n同名不同类型的实体：{len(conflict)} 个")
    for k, v in list(conflict.items())[:5]:
        print(f"  {k!r} → {v}")

    # ── 标签质量：空名 / 过短 / 纯符号 ──
    def bad_label(s):
        s = (s or "").strip()
        if len(s) < 2:
            return True
        return not any(ch.isalnum() for ch in s)
    bad = sum(1 for n in nodes if bad_label(n["id"]))
    print(f"\n标签异常（<2 字符或无字母数字）：{bad} / {N} = {bad/N*100:.2f}%")

    # ── 度数分布 ──
    ds = sorted(degrees, reverse=True)
    p = lambda q: ds[min(int(N * q), N - 1)]
    print(f"\n度数：max {ds[0]} / p99 {p(.01)} / p90 {p(.10)} / median {p(.50)}"
          f" / mean {st.mean(ds):.2f}")
    top1pct = sum(ds[:max(1, N // 100)]) / (2 * M) * 100 if M else 0
    print(f"  度数前 1% 的节点占总度数的 {top1pct:.1f}%")

    out = {
        "source": "web/kg-data/nebula.json",
        "caliber": "无向去重简化图（概览层导出），不可与有向图口径混用",
        "scale": {"nodes": N, "edges": M,
                  "density": density, "avg_clustering": round(cc, 5)},
        "connectivity": {
            "components": len(comps),
            "largest_component": len(comps[0]),
            "largest_component_pct": round(len(comps[0]) / N * 100, 2),
            "isolated_nodes": isolated,
        },
        "random_baseline": {
            "n_runs": N_RANDOM, "n": N, "m": M,
            "avg_clustering_mean": round(cc_mu, 6),
            "avg_clustering_sd": round(cc_sd, 6),
            "clustering_ratio_vs_random": round(ratio, 2),
            "largest_component_mean": round(st.mean(r_comp), 1),
        },
        "predicate_space": {
            "distinct_types": distinct_rel,
            "ontology_types": ONTOLOGY_RELATIONS,
            "conciseness": round(conciseness, 4),
            "caliber_warning": ("入库后口径：越界谓词已被 safe_relation_type 改写为 "
                                "RELATED_TO。入库前有 147 种越界类型，"
                                "那一口径的简洁度约 15/161 ≈ 0.09。"),
            "top": top_rel,
        },
        "entity_types": {
            "ontology_types": ONTOLOGY_ENTITIES,
            "distinct_types": len({n.get("t") for n in nodes}),
            "name_type_conflicts": len(conflict),
        },
        "labels": {"abnormal": bad, "abnormal_pct": round(bad / N * 100, 3)},
        "degree": {
            "max": ds[0], "p99": p(.01), "p90": p(.10),
            "median": p(.50), "mean": round(st.mean(ds), 3),
            "top1pct_degree_share": round(top1pct, 2),
        },
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告已写出: {OUT}")


if __name__ == "__main__":
    main()
