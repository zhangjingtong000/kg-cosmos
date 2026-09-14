# -*- coding: utf-8 -*-
r"""方向判据（独立于 T3.1 补丁的「见证」通道）。

**要解决什么**：六层评测没有一层读「原文里谁是施事」——
L3 只看端点在不在节点集内、L4 只看谓词在不在本体内，而 L5 用 NLI 比较
正/反向 hypothesis，实测对 2348 条真实反转只能拦住 **23.9%**
（`docs/L5_SIX_LAYER_REPORT.md §4.5`）。

于是需要一层纯规则、零成本、可复现的方向判据。本模块实现其中最强的一支：
**从关系自带的 `description` 里读谓词语义**，与边声明的方向比对。

**为什么这条通道是"独立见证"**：T3.1 补丁改过 `source`/`target`，但
`description` 是抽取时的原始描述句。实测把它与
`data/quality/baseline/relation-verify-all.PRE-T3.json` 逐字比对，
**2174/2174 条完全相同** —— 也就是说，本通道的证据在补丁前后没被碰过，
用它验证方向不构成循环论证。

**三处判定次序修正**（2026-09-14，评审实测 A 组自洽率 84.17% → 100%）：

  ① **主动系表优先于被动标记**。`PASSIVE_MARK` 允许助动词后插 0–3 个词，
     于是 `is listed as an author of` 同时命中被动（`is ... listed`）与
     主动系表（`is listed as an author`），旧实现被动先判 → **判反**。
  ② **无助动词的被动式必须单独识别**。`X authored by Y` / `X written by Y`
     没有 AUX 开头，旧实现既不判被动、又被 `AGENT_VERB` 的 `authored` 抓成
     主动 → **判反**。
  ③ **`authored by` / `written by` 从主动表移出，归被动**。

**判据的边界**（如实声明）：本通道只判「description 里的谓词把谁当施事」，
对不含谓词信息、或两个端点根本没在同一句 description 里出现的边，
一律返回 `unknown` —— **不猜**。可判比例实测约 16%。
"""

import re

# ── 助动词与被动 ────────────────────────────────────────────────────────
_AUX = (r"(?:was|were|is|are|has been|have been|had been|been|"
        r"can be|could be|may be|might be|will be|would be)")

_PASSIVE_PARTICIPLE = (
    r"(created|authored|co-authored|introduced|proposed|developed|written|"
    r"published|presented|designed|built|constructed|attributed|listed|cited|"
    r"used|applied|utilized|measured|evaluated|supported|enabled|caused|"
    r"produced|defined|described|reported|included|contained|adopted|extended|"
    r"incorporated|formalized|performed|learnt|learned|trained|deemed|ranked|"
    r"compared)"
)

# 带助动词的被动：`was created`、`is listed`
PASSIVE_WITH_AUX = re.compile(
    r"^" + _AUX + r"\s+(\w+\s+){0,3}" + _PASSIVE_PARTICIPLE + r"\b", re.I)

# 无助动词的被动：`authored by`、`written by`、`created by`
# （第 ② 处修正：旧实现漏了这一类）
PASSIVE_BARE = re.compile(
    r"^(?:" + _PASSIVE_PARTICIPLE + r")\s+by\b", re.I)

# ── 主动系表（第 ① 处修正：必须在被动之前判）──────────────────────────
# `X is listed as an author of Y` 里 X 是**作者**，是主动角色。
# 若不先判，`is ... listed` 会命中被动而判反。
COPULA_AGENT = re.compile(
    r"^(?:is|are|was|were)\s+"
    r"(?:listed as|the|an?)?\s*"
    r"(?:author|authors|co-?author|creator|creators|writer|writers|"
    r"developer|developers|designer|designers|owner|owners|maintainer|"
    r"contributor|contributors)\b", re.I)

# ── 主动谓词 ────────────────────────────────────────────────────────────
# 第 ③ 处修正：`authored` / `written` 已移出（它们的 `X by Y` 形态是被动）
AGENT_VERB = re.compile(
    r"^(authored by|wrote|proposes|proposed|introduces|introduced|"
    r"develops|developed|creates|created|publishes|published|presents|presented|"
    r"produces|produced|uses|use|utilizes|utilized|enables|enabled|causes|caused|"
    r"supports|supported|evaluates|evaluated|measures|measured|cites|cited|"
    r"compares|compared|outperforms|improves|addresses|applies|applied|"
    r"contains|contain|covers|cover|includes|include|extends|extend|"
    r"summarizes|summarize|reports|report|lists|list|describes|describe|"
    r"models|model|represents|represent|provides|provide|achieves|achieve|"
    r"is an author of|is the author of|are authors of|"
    r"contributed to)\b", re.I)

# `X is a part of Y` —— 系表归属，X 归 Y，仍是 S→T
COPULA_MEMBER = re.compile(
    r"^(is|are|was|were|be)\s+(a|an|the)?\s*"
    r"(part|component|element|subset|subclass|subtype|instance|member|branch|"
    r"category|type|kind|form|example|variant|version|species|class|"
    r"dataset|model|method|technique|approach|tool|system|framework|metric|"
    r"database|knowledge graph|algorithm|architecture)\b", re.I)

CONJ = re.compile(r"^(and|or|,|;|versus|vs\.?|along|as well as|between)\b", re.I)


def norm(s):
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def split_desc(desc, s, t):
    """把 description 切成 (端点顺序, 中间谓词片段)。

    先按原样找；找不到再按归一化文本找（抽取的描述句常有标点/大小写差异）。
    """
    desc = desc or ""
    i, j = desc.find(s or ""), desc.find(t or "")
    if i >= 0 and j >= 0 and i != j:
        return (("S_T", desc[i + len(s):j]) if i < j
                else ("T_S", desc[j + len(t):i]))
    nd, ns, nt = norm(desc), norm(s), norm(t)
    if not ns or not nt:
        return None, ""
    a, b = nd.find(ns), nd.find(nt)
    if a < 0 or b < 0 or a == b:
        return None, ""
    return (("S_T", nd[a + len(ns):b]) if a < b
            else ("T_S", nd[b + len(nt):a]))


def desc_verdict(desc, s, t):
    """读 description 里的谓词，判断它支持哪个方向。

    返回：
      · `"consistent"` —— description 的施事与边声明的 source 一致
      · `"inverted"`   —— 施事是 target（方向反了）
      · `"unknown"`    —— 判不出（不含谓词信息 / 两端没同时出现 / 连接词开头）
    """
    order, mid = split_desc(desc, s, t)
    if order is None:
        return "unknown"
    m = re.sub(r"\s+", " ", (mid or "").strip().lower())[:90]
    if not m:
        return "unknown"
    if CONJ.match(m):
        return "unknown"

    # ⚠️ 次序即判据：这三行的先后本身就是修复内容，不要调换。
    # ① 主动系表最优先（`is listed as an author of` 不能被当成被动）
    if COPULA_AGENT.match(m):
        sem = ("S", "T")
    # ② 无助动词的被动（`authored by` / `written by`）
    elif PASSIVE_BARE.match(m):
        sem = ("T", "S")
    # ③ 带助动词的被动
    elif PASSIVE_WITH_AUX.match(m):
        sem = ("T", "S")
    elif AGENT_VERB.match(m):
        sem = ("S", "T")
    elif COPULA_MEMBER.match(m):
        sem = ("S", "T")
    else:
        return "unknown"

    graph = ("S", "T") if order == "S_T" else ("T", "S")
    return "consistent" if sem == graph else "inverted"


def witness(desc, s, t, rel_type=None):
    """给出一条边的方向见证（带可复核的证据文本）。

    返回 None 表示本通道判不了；否则返回 dict，`text` 是**原文片段**，
    便于人工复核这条判定从哪来的。
    """
    v = desc_verdict(desc, s, t)
    if v == "unknown":
        return None
    order, mid = split_desc(desc, s, t)
    return {
        "verdict": v,
        "endpoint_order_in_text": order,
        "witness_text": re.sub(r"\s+", " ", (mid or "").strip())[:160],
        "source": "description",
        "independent_of_T3_patch": True,   # description 未被 T3.1 改写
    }
