# -*- coding: utf-8 -*-
r"""实体质量过滤器（纯规则，零成本，零模型）。

**来源**：用项目自带的多人人工金标（`data/gold/`）评测后，
第一篇的 244 个 FP 归因如下（一次性探针实测）：

  · ③ 参考文献作者（姓, 首字母）  46 个 —— 单一最大来源
  · ④ 纯数字 / 年份 / arXiv        5 个
  · ⑤ 量词短语与泛指名词         ~60 个（`92 survey articles`、`PDF documents`…）

这些**不是关系抽取的错误，是实体的噪声**：它们出现在参考文献段或泛指语境里，
被抽取器当成实体收了进来。

**三条规则的正则都经过一次全库试跑修正**（一次性扫描脚本）：
第一版有 4 处误判 ——
  · `Correction` 匹配了 `corr`（arXiv 前缀太宽）；
  · `ED-CNN` 匹配了 `eds?`、`No Skills` 匹配了 `no\.?`（引用尾词没要求句点）；
  · `2015 Conference On EMNLP` 被当成量词短语（**那是合法的会议实体**）。
修正后：arXiv 后必须跟数字、引用尾词后必须跟句点、量词短语要求数字后接**小写**名词。

**刻意保守**：泛指名词只收「完全匹配」的明确泛指词，且**默认不启用**
（`GENERIC_STRICT` 可开）。因为 `Literature` / `Systems` 这类词在有的论文里
确实是正经概念，靠词表杀容易误伤 —— 这一档留给人工或留作单独统计。

用法：
    from entity_filter import filter_entities, explain
    kept, dropped = filter_entities(nodes)      # dropped 带原因，便于审计
"""

import re

# ── 规则 ────────────────────────────────────────────────────────────────
# 姓, 首字母（可多个缩写）：Zander, P. O. / Kelleher, J. D.
R_SURNAME_INITIAL = re.compile(r"^[A-Z][A-Za-z'\-]+,\s*[A-Z]\.(\s*[A-Z]\.?)*\s*$")
# arXiv:2306.10723 / doi:10.xxxx —— **必须跟数字**，否则 Correction 会被误杀
R_IDENTIFIER = re.compile(r"^(arxiv|doi|isbn|issn)\s*[:\s]\s*\d", re.I)
# 引用尾词 —— **必须跟句点**，否则 ED-CNN / No Skills 会被误杀
R_REFTAIL = re.compile(r"^(et\s+al|ibid|vol|pp|no|eds?)\.[\s\d]*$", re.I)
# 纯数字
R_PURE_NUM = re.compile(r"^\d+$")
# 年份：2015 / 2024a（**不含 1980s 这类年代**，那可能是正经概念）
R_YEAR = re.compile(r"^(19|20)\d{2}[a-z]?$")
# 量词短语：数字 + **小写**名词（`92 survey articles`、`2 626 papers`）。
# 大写开头的是会议/期刊名（`2015 Conference On EMNLP`），**不能杀**。
R_QUANT = re.compile(r"^\d[\d\s,]*\s+[a-z]")

# 明确泛指词（完全匹配才命中）。默认**不启用** —— 见模块 docstring。
GENERIC_STRICT = {
    "pdf", "pdfs", "pdf documents", "pdf articles", "pdf files",
    "domain experts", "domain expert", "review article", "review articles",
    "research problem", "research problems", "research question",
    "research questions", "bibliographic data", "bibliographic information",
    "natural text", "survey paper", "survey papers", "survey article",
    "survey articles", "survey tables",
}


def _name_of(node) -> str:
    """取实体名。

    **两种数据源的字段名不同**，必须都兼容：
      · 抽取产物 / 金标（`data/gold/`）：用 `name`
      · 前端数据面（`web/kg-data/nebula.json`）：用 `id`
    第一版只认 `name`，在全库 **20785** 个实体上命中率 100%（全被判"空名"），
    幸好先用 `_ref_filter_scan.py` 试跑才发现。
    （原写 **18780** —— 那是旧 Neo4j 快照的节点数；现全库以
     `web/kg-data/analytics.json` 的 `scale.nodes` 为准。）
    """
    return (node.get("name") or node.get("id") or "").strip()


def explain(name: str, evidence: str = "", strict_generic: bool = True):
    """返回命中的原因字符串；没命中返回 None。

    evidence 传入时，额外检查「证据来自参考文献段」。
    """
    n = (name or "").strip()
    if not n:
        return "空名"

    if R_SURNAME_INITIAL.match(n):
        return "参考文献作者（姓, 首字母）"
    if R_IDENTIFIER.match(n):
        return "文献编号（arXiv/DOI/ISBN）"
    if R_REFTAIL.match(n):
        return "引用尾词"
    if R_PURE_NUM.match(n):
        return "纯数字"
    if R_YEAR.match(n):
        return "年份"
    if R_QUANT.match(n):
        return "量词短语（数字+小写名词）"

    # 证据来自参考文献段：只在前 60 字符里找段首标记，避免正文里提到
    # "the references" 就被误判。
    if evidence:
        head = evidence[:60]
        if re.match(r"^\s*(bibliography|references)\b", head, re.I):
            return "证据来自参考文献段"

    if strict_generic and n.lower() in GENERIC_STRICT:
        return "泛指名词（严格档）"
    return None


def filter_entities(nodes, strict_generic: bool = True):
    """按规则把实体分成 保留 / 丢弃 两组。

    `nodes` 是含 name 与可选 evidence 的 dict 列表。
    **不修改入参**；返回 (kept, dropped)，dropped 里带 drop_reason 便于审计。
    """
    kept, dropped = [], []
    for n in nodes or []:
        name = _name_of(n)
        evs = n.get("evidence") or []
        ev = " ".join((e.get("text") or "") for e in evs) if evs else ""
        reason = explain(name, ev, strict_generic)
        if reason:
            rec = dict(n)
            rec["drop_reason"] = reason
            dropped.append(rec)
        else:
            kept.append(n)
    return kept, dropped


def summary(dropped):
    """按原因汇总，便于写进报告。"""
    from collections import Counter
    c = Counter(d.get("drop_reason") for d in dropped or [])
    return dict(c.most_common())
