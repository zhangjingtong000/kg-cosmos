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
import re
from collections import Counter
from pathlib import Path

WEB = ROOT / "web/kg-data"
QUAL = ROOT / "data/quality"
OUT = WEB / "analytics.json"

PASS = (True, "True")


def read(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def plain_text(obj):
    """递归地把 markdown 粗体剥成纯文本 —— **这份 payload 的文字会直接进 DOM**。

    为什么必须在**这一层**做（既不在生成脚本里、也不在前端）：
      · 生成脚本（如 `tools/l9_relation_recall.py`）里的 `**` **是有用的** ——
        同一段 `reading` 也会被文档引用，那里**需要**粗体；
      · 而前端这条路（`analytics.json` → `analytics.html` 的 `textContent`）
        **只会把 `**` 原样显示出来**，变成页面上的字面星号。
    ⇒ **转换要发生在"进 DOM 之前"这一层**，也就是这里。

    ⚠️ 实测踩到（2026-09-19）：`verify_all.py` 的「DOM 文案守门」报
    `analytics.html` 有 1 处 markdown 泄漏，来源是 `l9_relation_recall.reading`
    里的 `**不代表关系抽全了**`。**静态扫描源码是扫不到它的** ——
    那段文字在 JSON 数据里，渲染之后才进 DOM。
    ⇒ 这也是"**文案从数据进 DOM**"这一类泄漏的唯一正确修法所在。
    """
    if isinstance(obj, str):
        return re.sub(r'\*\*(.+?)\*\*', r'\1', obj)
    if isinstance(obj, dict):
        return {k: plain_text(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [plain_text(v) for v in obj]
    return obj


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


def _load_relations():
    """读 `web/kg-data/relations.json`（由 `tools/build_relations_data.py` 生成）。

    拿不到就返回 None —— 前端会跳过那个区块，**不让整份 analytics.json 失败**
    （这与项目里"进度上报绝不能反过来把抽取搞挂"是同一条纪律：
    一个附加区块不该成为主链路的故障点）。
    """
    p = WEB / "relations.json"
    if not p.exists():
        print("  ⚠️ 没有 relations.json（先跑 tools/build_relations_data.py）"
              "—— 关系语义区块将不出现在分析面板里")
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"  ⚠️ relations.json 读失败：{type(e).__name__}: {e}")
        return None


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
    # L9 端点召回 + **关系召回**（2026-09-12 补后者）。
    # 关系召回是本项目最根本的一个读数：端点召回 86.2% 看着不错，
    # 但原文明确断言的关系只有约四成进了图。
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
    l9r = {}
    l9rp = QUAL / "l9-relation-recall.json"
    if l9rp.exists():
        dr = read(l9rp)
        l9r = {
            "recall_pair_all": dr.get("recall_pair_all"),
            "recall_triple_all": dr.get("recall_triple_all"),
            "recall_pair_supported": dr.get("recall_pair_supported"),
            "recall_triple_supported": dr.get("recall_triple_supported"),
            "n_gold_raw": dr.get("n_triple_gold_raw"),
            "n_gold_entity_only": dr.get("n_triple_gold_entity_only"),
            "n_dropped_non_entity": dr.get("n_triple_dropped_non_entity"),
            "n_segment": dr.get("n_segment"),
            "reading": dr.get("reading"),
            "stability_note": dr.get("stability_note"),
            "caveat": dr.get("caveat"),
            "endpoint_recall": l9.get("endpoint_recall") if l9 else None,
        }
        # 漏抽归因：这是**可操作**的部分——不是"整体抽得差"，
        # 而是集中在特定谓词与 2024+ 的新论文上。
        p_miss = QUAL / "l9-miss-analysis.json"
        if p_miss.exists():
            m = read(p_miss)
            l9r["miss_analysis"] = {
                "n_miss": m.get("n_miss"),
                "miss_by_relation": m.get("miss_by_relation"),
                "miss_endpoint_status": m.get("miss_endpoint_status"),
                "by_year": m.get("by_year"),
                "worst_papers": m.get("worst_papers"),
                "conclusion": m.get("conclusion"),
            }
        # ── 别名修正后的区间（**以这个为准**，别再只报单点）──
        # 严格匹配会把 `LLMs` vs `Large Language Model` 判成漏，从而低估。
        p_corr = QUAL / "l9-relation-recall-corrected.json"
        if p_corr.exists():
            c = read(p_corr)
            l9r["recall_range"] = c.get("recall_range")
            l9r["recall_tiers"] = {
                "L1_strict_triple": c.get("L1_strict_triple"),
                "L2_alias_triple": c.get("L2_alias_triple"),
                "L2b_exact_pair": c.get("L2b_exact_pair"),
                "L3_alias_pair": c.get("L3_alias_pair"),
            }
            l9r["range_note"] = c.get("note")
        # ── 产出构成：把"召回约一半"的解读限定住 ──
        p_over = QUAL / "l9-overextraction.json"
        if p_over.exists():
            o = read(p_over)
            l9r["overextraction"] = {
                "n_edge": o.get("n_edge_on_gold_spans"),
                "n_pass": o.get("n_pass"),
                "n_not_pass": o.get("n_not_pass"),
                "n_structural": o.get("n_structural"),
                "structural_share": o.get("structural_share"),
                "per_span_avg": round(
                    (o.get("n_edge_on_gold_spans") or 0)
                    / max(1, len(o.get("per_span") or [])), 1),
                "reading": o.get("reading"),
            }
        # 本地重抽已被实测否决——把依据也带上，避免误以为"还能再抽一遍"
        l9r["local_reextract_rejected"] = {
            "note": ("原图由云端 DeepSeek 抽取；重抽只能用本地 qwen3.5:9b。"
                     "实测三段对照：云端各抽 30/19/21 条边，本地只抽 6/2/3 条 "
                     "⇒ 本地产出约为云端的 1/5–1/10，重抽不会改善。"),
            "tool": "tools/local_reextract_feasibility.py",
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
        "l9_relation_recall": l9r,
        "governance": governance,
        "by_type": by_type,
        "hit_distribution": hits,
        "supported_subgraph": {
            "nodes": 16308,
            "edges": six.get("edges_supported"),
            "dropped": six.get("edges_dropped"),
        },
        # K4（2026-09-16）：16 类关系的可读说明（语义/方向性/端点约束/假设模板）。
        # **塞进同一份 payload 而不是让前端再 fetch 一个文件** ——
        # 这个页面已经读 analytics.json，多一个请求只增加"某个文件没生成好"的失败面。
        # 数据由 `tools/build_relations_data.py` 生成；这里只做"如果它在，就带上"。
        "relations": _load_relations(),
    }

    # ★ 剥掉所有 markdown 粗体 —— 这份 payload 的文字会**直接进 DOM**（2026-09-19 修）。
    #   上游 `reading` / `caliber` 等字段里的 `**` 是给 **文档渲染** 用的，
    #   而前端走 `textContent` ⇒ 会把 `**` 原样显示成字面星号。
    #   ⇒ 实测：`analytics.html` 因此被「DOM 文案守门」判红。
    payload = plain_text(payload)

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
