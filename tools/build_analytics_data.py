"""汇总图谱质量数据，供 web/analytics.html（T6.5 分析面板）使用。

数据源全部是已产出的离线结果，本脚本只做汇总与派生，不重算模型：
  web/kg-data/nebula.json                        规模、社区、模块度
  data/quality/l5-six-layer.json                 六层指标
  data/quality/relation-verify-all.json          按关系类型的分布（治理后）
  data/quality/baseline/relation-verify-all.PRE-T3.json   同口径的治理前对照
  data/quality/same-as-candidates.json           L2 待复核候选

治理前后必须**同口径**对比：PRE-T3 与当前文件都是同一判别器、同一阈值
（0.67）算出的 entail_prob，差别只在图是否治理过，因此可以直接比。
L3（端点）的治理效果无法在此重算——它需要按图结构检查端点是否存在，
故直接引用 `docs/L5_SIX_LAYER_REPORT.md` 已核实过的数字，并在数据里注明来源。

用法：
  python tools/build_analytics_data.py
"""
# 路径以本文件为锚点解析，不写死盘符——演示机不一定把仓库放在 D 盘。
from pathlib import Path as _Path
_HERE = _Path(__file__).resolve()
ROOT = _HERE.parent.parent          # tools/*.py 与 src/*.py 都在根目录下一层
import json
from collections import Counter
from pathlib import Path

WEB = ROOT / "web/kg-data"
QUAL = ROOT / "data/quality"
OUT = WEB / "analytics.json"

PASS = (True, "True")


def read(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def pass_rate(edges):
    if not edges:
        return 0.0, 0
    n = sum(1 for e in edges if e.get("pass") in PASS)
    return n / len(edges), n


def by_type_rows(edges, threshold):
    import statistics as st
    buckets = {}
    for e in edges:
        buckets.setdefault(e["type"], []).append(float(e["entail_prob"]))
    rows = []
    for t, ps in buckets.items():
        ps.sort()
        n = len(ps)
        rows.append({
            "type": t, "n": n,
            "mean": round(st.mean(ps), 4),
            "median": round(st.median(ps), 4),
            "pass_rate": round(sum(1 for x in ps if x >= threshold) / n, 4),
        })
    rows.sort(key=lambda r: -r["n"])
    return rows


def main():
    neb = read(WEB / "nebula.json")
    six = read(QUAL / "l5-six-layer.json")
    cur = read(QUAL / "relation-verify-all.json")
    pre = read(QUAL / "baseline" / "relation-verify-all.PRE-T3.json")
    thr = cur["threshold"]

    # ── 规模 ──
    comms = [c["size"] for c in neb["communities"]]
    scale = {
        "nodes": neb["meta"]["nodes"],
        "edges": neb["meta"]["edges"],
        "communities": neb["meta"]["communities"],
        "dust": neb["meta"].get("dust", 0),
        "modularity": neb["meta"].get("modularity"),
        "largest_community": max(comms) if comms else 0,
        "median_community": sorted(comms)[len(comms) // 2] if comms else 0,
    }

    # ── 六层 ──
    def pct(v):
        return round(v * 100, 1) if isinstance(v, (int, float)) else None

    L2 = six.get("L2_entity_clustering") or {}
    six_layer = [
        {"id": "L1", "name": "实体存在层", "value": pct(six["L1_entity_presence"]["rate"]),
         "detail": f"{six['L1_entity_presence']['hit']}/{six['L1_entity_presence']['total']} 个实体名字面出现在自己的证据引文中",
         "basis": "节点级"},
        # L2 是节点级、没有百分比，用 figure 承载"585 组"这个数
        # （前端原先把它写成硬编码的 `384 组`，改数据后就成了 stale 数字）。
        {"id": "L2", "name": "实体聚类层", "value": None,
         "figure": f"{L2.get('cumulative_merged_groups', '?')} 组",
         "detail": f"累计归并 {L2.get('cumulative_merged_groups', '?')} 组"
                   f"（{L2.get('names_before', '?')} → {L2.get('names_after', '?')} 名，"
                   f"减少 {L2.get('cumulative_reduction_pct', '?')}%）；"
                   f"本轮重跑合并 {L2.get('merged_groups', {}).get('total_this_run', '?')} 组",
         "basis": "节点级"},
        # L3 必须并列两个口径：闭合口径含人工反建的节点（定义恒等），
        # 抽取口径才是"只算独立抽取出来的节点"。
        {"id": "L3", "name": "关系端点层",
         "value": pct(six["L3_endpoints"]["all"]),
         "detail": f"闭合口径 {pct(six['L3_endpoints']['all'])}%（含从边反建的节点，属人工构造）"
                   f" · 抽取口径 {pct(six.get('L3_endpoints_extracted_caliber', {}).get('all', 0))}%"
                   f"（只算独立抽取的节点）",
         "basis": f"{cur['count']} 条边"},
        {"id": "L4", "name": "关系谓词层", "value": pct(six["L4_predicate"]["all"]),
         "detail": "谓词落在本体 15 类内",
         "basis": f"{cur['count']} 条边"},
        # L5 的 detail 必须带上 PPV —— 否则面板上只显示 63.7%，
        # 会被读成"63.7% 的边有原文支持"（那是错的）。
        {"id": "L5", "name": "三元组验证层", "value": pct(six["L5_triplet"]["all"]),
         "figure": f"{pct(six['L5_triplet']['all'])}% 通过率",
         "detail": f"判别器判通过 {pct(six['L5_triplet']['all'])}%"
                   f"（这是通过率，不是支持率）；独立盲判实测 "
                   f"PPV 49.7%、真实支持率 40.0%",
         "basis": f"{cur['count']} 条边"},
        {"id": "L6", "name": "证据层", "value": pct(six["L6_evidence"]["all"]),
         "detail": "每条边都有页面级引文",
         "basis": f"{cur['count']} 条边"},
    ]

    # ── 治理前后（同口径：同判别器、同阈值，只差图有没有治理过）──
    cur_rate, cur_n = pass_rate(cur["edges"])
    pre_rate, pre_n = pass_rate(pre["edges"])
    cur_cb = [e for e in cur["edges"] if e["type"] == "CREATED_BY"]
    pre_cb = [e for e in pre["edges"] if e["type"] == "CREATED_BY"]
    cur_cb_r, _ = pass_rate(cur_cb)
    pre_cb_r, _ = pass_rate(pre_cb)

    # ── L5 域内效度（2026-09-12 新增）──
    # 这是本项目最重要的一个发现，必须让它在面板上可见：
    # 63.7% 只是"判别器判通过的比例"，而它在**本项目数据分布**上的
    # 真实支持率是 40.0%、PPV 只有 49.7%。
    # 不把它放进面板，演示时屏幕上就只会看到一个被误解的 63.7%。
    l5v = {}
    p_val = QUAL / "l5-validity-check.json"
    p_rep = QUAL / "l5-validity-report.json"      # 权威：含 bootstrap 95% CI
    p_agr = QUAL / "l5-annotator-agreement.json"
    if p_val.exists() or p_rep.exists():
        v = read(p_val) if p_val.exists() else {}
        rep = read(p_rep) if p_rep.exists() else {}
        l5v = {
            # 优先取权威报告（它是加权 + bootstrap 的产物），取不到才退回常量
            "true_support_rate": rep.get("true_support_rate", 0.40),
            "ppv": rep.get("ppv", 0.497),
            "npv": rep.get("npv", 0.230),
            "recall": rep.get("recall", 0.793),
            "f1": rep.get("f1", 0.611),
            "weighted_accuracy": rep.get("weighted_accuracy", 0.596),
            "domain_auc": rep.get("in_domain_auc", 0.7074),
            "calib_auc": cur.get("calib_roc_auc"),
            "kappa_vs_human": rep.get("cohens_kappa", 0.236),
            "ci": rep.get("ci"),                 # 95% 置信区间（层内 bootstrap 1000 次）
            "confusion": rep.get("confusion") or
                         {"tp": 13778, "fp": 13960, "tn": 12082, "fn": 3604},
            "threshold_sensitivity": rep.get("threshold_sensitivity")
                                     or v.get("threshold_sensitivity", []),
            "note": ("63.7% 是判别器**判通过的比例**，不是支持率、更不是精度。"
                     "200 条分层抽样的独立盲判给出：真实支持率 40.0%、"
                     "PPV 49.7%、域内 AUC 0.7074（官方校准集上是 0.9851）。"
                     "判通过的边里约一半不被原文支持，假阳性比真阳性还多。"),
            "caveat": ("两个标注者都是 LLM（不同会话，κ=0.722）。"
                       "κ 说明任务可稳定复现，不等于达到人类金标水准。"),
        }
        if p_agr.exists():
            a = read(p_agr)
            l5v["annotator_agreement"] = {
                "n": a.get("n"), "cohen_kappa": a.get("cohen_kappa"),
                "agreement": a.get("agreement"),
                "rate_annotator1": a.get("rate_annotator1"),
                "rate_annotator2": a.get("rate_annotator2"),
            }
        # 过滤策略对比：现行 pass 不是最优的，而且即使用最优策略 PPV 也只有 56%
        # ⇒ L5 的正确定位是"排序信号"而不是"二元过滤器"。
        p_str = QUAL / "l5-filter-strategy.json"
        if p_str.exists():
            st = read(p_str)
            l5v["filter_strategies"] = st.get("strategies")
            l5v["filter_coverage_full"] = st.get("coverage_full")
            l5v["filter_only_h"] = st.get("only_H_returned")
            l5v["positioning"] = st.get("positioning")

    # ── L7 角色违规归因（2026-09-12 新增）──
    l7r = {}
    p_l7 = QUAL / "l7-role-violation-audit.json"
    if p_l7.exists():
        a7 = read(p_l7)
        l7r = {
            "n": a7.get("n"),
            "by_relation": a7.get("by_relation"),
            "total": a7.get("total"),
            "false_positive_rate": a7.get("false_positive_rate"),
            "predicate_wrong_rate": a7.get("predicate_wrong_rate"),
            "true_violation_rate": a7.get("true_violation_rate"),
            "conclusion": a7.get("conclusion"),
            "proposes": a7.get("proposes"),
            "note": ("`role_violation` 里 58.6% 其实是**谓词选错**，"
                     "不是端点类型不对 ⇒ 该指标名应读作"
                     "「端点类型或谓词问题」，不能写成「端点类型违规率」。"),
        }

    governance = {
        "note": "同判别器、同阈值，唯一变量是图是否经过 T3/T4 治理",
        "rows": [
            {"label": "L5 总体通过率", "before": round(pre_rate * 100, 1),
             "after": round(cur_rate * 100, 1),
             "delta": round((cur_rate - pre_rate) * 100, 1)},
            {"label": "CREATED_BY 通过率", "before": round(pre_cb_r * 100, 1),
             "after": round(cur_cb_r * 100, 1),
             "delta": round((cur_cb_r - pre_cb_r) * 100, 1)},
            # L3 需要按图结构查端点，不在本脚本能力内，沿用已核实的报告数字
            {"label": "L3 关系端点", "before": 58.7, "after": 68.5, "delta": 9.8,
             "source": "docs/L5_SIX_LAYER_REPORT.md §0（需图结构，本面板不重算）"},
        ],
        "edge_count": {"before": pre["count"], "after": cur["count"]},
    }

    # ── 按关系类型 ──
    rows = by_type_rows(cur["edges"], thr)
    ontology = [r for r in rows if r["n"] >= 50]
    by_type = {
        "threshold": thr,
        "rows": rows,
        "note": ("只统计边数 ≥50 的类型；越界类型（治理后仍有 "
                 f"{len([r for r in rows if r['n'] < 50])} 种，多为个位数样本）不单列"),
    }

    hits = cur.get("hit_distribution", {})

    # 社区数的两个口径必须一起给：Louvain 实际检测出 60 个社区，
    # 前端只导出规模最大的 18 个（其余是碎片）。只报一个数就会被读成前后矛盾
    # ——导航页写 18、本面板写 60，审计正是抓到了这一点。
    communities_total = scale.get("communities")
    communities_exported = 18
    dust = scale.get("dust")

    # L9 端点召回层：本项目**唯一非自洽型**的指标。
    # 六层全部判"图内部是否一致"，回答不了"原文里的东西抽到没有"；
    # L9 从原文段落出发（操作性金标，独立于抽取管线）回答这个问题。
    l9 = {}
    l9p = QUAL / "l9-recall.json"
    if l9p.exists():
        d9 = read(l9p)
        l9 = {
            "endpoint_recall": d9.get("endpoint_recall"),
            "gold_entities": d9.get("gold_entities"),
            "matched": d9.get("matched"),
            "spans": d9.get("spans"),
            "note": d9.get("note"),
            "limitation": "只覆盖端点召回，未覆盖关系召回；样本 7 段，非多人标注。",
        }

    payload = {
        "meta": {
            "generated_by": "tools/build_analytics_data.py",
            "sources": [
                "web/kg-data/nebula.json", "data/quality/l5-six-layer.json",
                "data/quality/relation-verify-all.json",
                "data/quality/baseline/relation-verify-all.PRE-T3.json",
                "data/quality/l9-recall.json",
            ],
            "threshold": thr,
            "model_roc_auc": cur.get("calib_roc_auc"),
            "model": cur.get("model", "").split("snapshots")[0].split("models--")[-1].replace("--", "/").rstrip("/\\"),
            "calibers": {
                "overview_graph": {
                    "nodes": scale.get("nodes"), "edges": scale.get("edges"),
                    "meaning": "无向去重后的简化图，供三维界面渲染",
                },
                "eval_full": {
                    "edges": six.get("edges_total"),
                    "meaning": "含方向与重复的评测全集，六层指标在此口径上计算",
                },
                "l5_supported": {
                    "edges": six.get("edges_supported"),
                    "meaning": "经本地判别器判为原文支持的子集",
                },
                "l3_extracted_caliber": six.get("L3_endpoints_extracted_caliber"),
                "note": "三套规模数字含义不同、不可互相替代；跨口径比较前先看这里。",
            },
        },
        "scale": scale,
        "community_calibers": {
            "louvain_total": communities_total,
            "exported_to_frontend": communities_exported,
            "dust_nodes": dust,
            "note": f"Louvain 共检出 {communities_total} 个社区，其中规模最大的 "
                    f"{communities_exported} 个导出到前端；另有 {dust} 个碎片节点"
                    f"未归入任何社区。导航页显示的 18 即此处的 exported_to_frontend。",
        },
        "six_layer": six_layer,
        # 这两段是 2026-09-12 补的：让"63.7% 不是质量"这个最重要的发现
        # 在面板上可见，而不是只躺在 docs 里。
        "l5_validity": l5v,
        "l7_role_audit": l7r,
        "l9_recall": l9,
        "governance": governance,
        "by_type": by_type,
        "hit_distribution": hits,
        "supported_subgraph": {
            "nodes": 16308,
            "edges": six.get("edges_supported"),
            "dropped": six.get("edges_dropped"),
        },
    }

    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    print("规模:", scale)
    print(f"L5 总体 {pre_rate:.1%} → {cur_rate:.1%}"
          f"（{pre['count']} → {cur['count']} 条）")
    print(f"CREATED_BY {pre_cb_r:.1%} → {cur_cb_r:.1%}")
    print(f"关系类型 {len(rows)} 种，其中 n>=50 的 {len(ontology)} 种")
    print(f"已写出 {OUT}  ({OUT.stat().st_size/1024:.0f} KB)")


if __name__ == "__main__":
    main()
