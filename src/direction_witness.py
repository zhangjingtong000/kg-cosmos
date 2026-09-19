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

     ⚠️ **补记（2026-09-19）**：上面这个原例当时**并没有真修好** ——
     `COPULA_AGENT` 写成 `(?:listed as|the|an?)?`，**只能吃一个可选片段**：
     吃下 `listed as` 之后就要求紧跟 `author`，而原文是 `an author`
     ⇒ 主动系表不命中 ⇒ 仍落到被动分支 ⇒ `X is listed as an author of Y`
     **继续判 `inverted`**（只有无冠词的 `is listed as author of` 才判对）。
     后果是 `KG_SANITIZE=3` 会把**方向正确**的边翻转，属数据损坏级。
     现把 `listed as` 与冠词拆成**两段都可选、可同时命中**：
     `(?:listed\s+as\s+)?(?:one\s+of\s+)?(?:the|an?|...)?\s*` ⇒ 原例判 `consistent`。
     `one of the` 段是同一处 bug 的同源形态：`X is listed as one of the authors
     of Y` 原先也落到被动分支判反，一并修掉。
     既有测试 `tests/test_direction_witness.py` 曾把旧行为钉成"现状记录"，
     同步改成"已修"的断言。
  ② **无助动词的被动式必须单独识别**。`X authored by Y` / `X written by Y`
     没有 AUX 开头，旧实现既不判被动、又被 `AGENT_VERB` 的 `authored` 抓成
     主动 → **判反**。
  ③ **`authored by` / `written by` 从主动表移出，归被动**。
     （2026-09-19 补记：`authored` 的**主动形态** `X authored Y` 放回主动表 ——
     它后面不接 `by`，本就不是被动；`authored by` 仍由排在前面的
     `PASSIVE_BARE` 拦下，判定结果不变。）

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
#
# ⚠️（2026-09-19）这几段必须是**独立的可选组**，不能合成一个
# `(?:listed as|the|an?)?`：合成后只能吃一个片段，`listed as` 与 `an`
# 无法同时命中，带冠词的原例就落到被动分支判反（真 bug，已修）。
# `one of` 段同理 —— `X is listed as one of the authors of Y` 也曾落到
# 被动分支判反，是同一处 bug 的同源形态（2026-09-19 一并修）。
COPULA_AGENT = re.compile(
    r"^(?:is|are|was|were)\s+"
    r"(?:listed\s+as\s+)?"          # 被动式里的 "listed as"
    r"(?:one\s+of\s+)?"             # "one of the authors" 里的 "one of"
    r"(?:the|an?|its|their|his|her)?\s*"   # 冠词 / 所有格
    r"(?:author|authors|co-?authors?|creator|creators|writer|writers|"
    r"developer|developers|designer|designers|owner|owners|maintainer|"
    r"contributor|contributors)\b", re.I)

# ── 主动谓词 ────────────────────────────────────────────────────────────
# 第 ③ 处修正：`written` 已移出（`X written by Y` 的 `X by Y` 形态是被动）。
# ⚠️（2026-09-19）`authored` 以**主动词形**留在表里，覆盖 `X authored Y`
# 这种真主动句（旧实现只列了 `authored by`，于是 `X authored Y` 判 unknown）。
# 它的被动形态 `authored by` 靠**排在其前的 `PASSIVE_BARE`** 兜住 ⇒ 仍判被动，
# 所以本表的这一项**不会**把被动判反；次序不要动。
AGENT_VERB = re.compile(
    r"^(authored by|authored|wrote|proposes|proposed|introduces|introduced|"
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
