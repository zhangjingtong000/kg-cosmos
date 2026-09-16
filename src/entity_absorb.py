# -*- coding: utf-8 -*-
r"""1C：把「长标题被拆成多个实体」的碎片吸收回长名。

> ## ⚠️ 启用前的必读：本模块**尚未接进生产管线**，且判据在全库尺度上偏宽
>
> **实测收益**（人工金标，两篇）：
>   2012.00456v1  F1 0.3918 → 0.3941（+0.0023，吸收 4 对）
>   2401.04507v1  F1 0.4030 → 0.4109（+0.0079，吸收 8 对）
>   **平均 +0.0051**
>
> **实测风险**（全库 18780 个实体）：可吸收 **2078 对**，且抽样就见到错：
>   · `Attention Vectors` → `Sparse Attention Vectors`  ← **Sparse 是限定词，含义变了**
>   · `Balažević` → `Balažević Et Al.`
>   · `Bilinear & Tensor` → `Bilinear Tensor Operator`
>   而 `Baeza-Yates` → `Ricardo A. Baeza-Yates` 这类**人名补齐是该合的**。
>
> 即：「短名是长名的连续子词序列」这个判据**分不清「人名补齐」与「概念加限定词」**。
> 曾试过放宽（允许单词碎片），在金标上 F1 还能再 +0.0047、金标零误吸收 ——
> 但全库吸收数暴涨到 **3961 对（21.09%）**，出现 `Adversarial` →
> `Self-Adversarial`（**意思相反**）、`2011` → `CAiSE 2011` 这类明显错误，
> 已回退。**教训：任何归一规则都要同时看全库影响面，只在小样本上验证会过拟合。**
>
> **结论**：收益 +0.51pp 与全库 11% 的合并率不匹配，判据需要先收紧
> （例如只对人名形态生效）再考虑接入。在此之前**不要在生产链路里调用**。


**问题**（金标 FP 归因，43/244）：抽取器把一个完整标题拆成了几个实体 ——
  · `Scholarly Knowledge Graph`
  · `Survey Article Tables`
  · `tables`
  都是金标实体 `Creating a Scholarly Knowledge Graph from Survey Article Tables`
  的片段。

**为什么不能只靠 v6 消歧**：v6 判的是「两个名字是否同一实体」（对称），
而这里是「**短名应当被长名吸收**」（有方向），且判据依赖**同页**这个证据范围。

**吸收条件（三条同时满足，刻意保守）**：
  1. 短名的实词序列是长名实词序列的**连续子序列**（词边界对齐，
     所以 `tag` ⊄ `tagger`、`graph` ⊄ `graphneuralnetwork`）；
  2. 两者的证据**有共同页码**（同一页出现的才可能是同一个东西的碎片）；
  3. 短名实词数 ≥2，避免把 `China` 这种单词也吸走 ——
     除非它带逗号（`ShenYang, China` 这种地名形态，允许吸收 `China`）。

**不删除任何东西**：吸收只是把短名的证据并入长名、并把短名标为
`absorbed_into`。**节点数不变**，所以图规模与既有指标口径不受影响 ——
这点很重要，因为 `docs/KNOWN_DATA_DEFECTS.md §6` 的原则是「要么不改，
要么改前改后两套指标一起留档」。
"""

import re


def tokens(name: str):
    """切成小写实词（去掉括号内容）。"""
    body = re.sub(r"\([^)]*\)", " ", name or "")
    return [t for t in re.split(r"[^0-9A-Za-z]+", body.lower()) if t]


def is_contiguous_subsequence(short_toks, long_toks) -> bool:
    """短的实词序列是否是长的**连续**子序列。"""
    if not short_toks or len(short_toks) >= len(long_toks):
        return False
    n = len(short_toks)
    for k in range(len(long_toks) - n + 1):
        if long_toks[k:k + n] == short_toks:
            return True
    return False


def extra_tokens(short_toks, long_toks):
    """长名里**多出来的**那部分实词（短名是连续子序列时）。

    例：short=[review] long=[review,papers] → ['papers']
    """
    n = len(short_toks)
    for k in range(len(long_toks) - n + 1):
        if long_toks[k:k + n] == short_toks:
            return long_toks[:k] + long_toks[k + n:]
    return []


# 「载体词」：多出这些说明长名指的是**另一种东西**（文章/文档），不是同一个
# 实体的完整写法。实测教训：「literature review」（金标实体）曾被吸收进
# 「literature review papers」（不是金标）—— 纯损失。
CARRIER_WORDS = frozenset({
    'papers', 'paper', 'articles', 'article', 'documents', 'document',
    'files', 'file', 'reports', 'report', 'books', 'book',
})

# A5（2026-09-16）：人名补齐的成分。**这两个集合是"允许被吸收"的全部可能**——
# 删除或放宽它们会让 1C 重新把"论文标题片段"吸进实体（实测 1404 对里抽样 0 对该合）。
TITLE_WORDS = frozenset({
    'mr', 'mrs', 'ms', 'dr', 'prof', 'professor', 'sir', 'miss',
})
ET_AL = frozenset({'et', 'al', 'others'})


_PERSON_PIECE = re.compile(r'^(?:[A-Za-z]\.-?)+$')      # 'W.-Y.' / 'A.'
_TITLE_PIECES = frozenset({'mr.', 'mrs.', 'ms.', 'dr.', 'prof.', 'professor.',
                           'sir.', 'mr', 'mrs', 'ms', 'dr', 'prof', 'sir'})
_ETAL_PIECES = frozenset({'et', 'al', 'others'})


def extra_is_person_form(short: str, long_: str) -> bool:
    """多出来的部分是否**只含"人名补齐"成分** —— A5（2026-09-16）新增的第六道条件。

    ## ⚠️ 这个函数**必须用原始字符串**，不能用 `tokens()`

    `tokens()` 会做两件事，而**正是被它去掉的那两样东西在区分人名与公式**：
      · `re.sub(r"\\([^)]*\\)", " ", ...)` ⇒ **括号内容整段丢掉**；
      · `re.split(r"[^0-9A-Za-z]+", ...)` ⇒ **点也被切掉**。

    于是：
        `tokens('Score function f(h, r, t)')` = `['score','function','f']`  ⇒ extra `['f']`
        `tokens('W.-Y. Ma')`                  = `['w','y','ma']`            ⇒ extra `['w']`
    **`['f']` 与 `['w']` 在 tokens 层面完全一样** —— 一个是数学符号，一个是人名首字母。
    ⇒ 所以本函数**在原始字符串上工作**：`'f(h, r, t)'` 含 `(`，
      `'W.-Y.'` 匹配"带点的首字母"。

    ## 为什么必须加这一条：实测那 1404 对里抽样看**几乎全是错的**

    `tools/_probe/_probe_absorb_1c.py` + `_probe_absorb_1c_step2.py`（只读）量出：

        1404 对：③ 人名成分 32（2.3%）· ④ 限定词 121 · ⑤ 其它 1251（89%）

    而 ⑤ 里**随机抽 14 对，0 对该合**：
        `Deep Learning`      → `Deep Learning-based Models`         （含义变了）
        `Structured Data`    → `Semi-Structured Data`               （**`Semi-` 改了含义**）
        `Question Answering` → `Knowledge Graph Question Answering` （**上义词，不是同一实体**）
        `KG Completion`      → `KG Completion Benchmark`
        `Named Entity Recognition` → `Neural Architectures For …`   （**论文标题片段**）
    ④ 与"含功能词"的那 270 对同样全是错的：
        `Nobel Prize` → `Nobel Prize in Physics`（限定）
        `Knowledge Graphs` → `Traversing Knowledge Graphs in Vector Space`（论文标题）

    ⇒ **"长名包含短名"在有名词短语上根本不足以判定同指** ——
    多一个词通常意味着**更具体的另一个概念**，而不是"同一个东西的完整写法"。
    ⇒ 所以 1C 收紧成**只吸收人名补齐**（唯一一类"多出来的部分**不改变所指**"的情形）。
    收紧后 **1404 → 1 对**（`Balažević` → `Balažević Et Al.`）。

    ## 该保留的（A5 原意）

        `Balažević` → `Balažević Et Al.`      额外部分 `'Et Al.'`  ⇒ et al.
        `Y. Ma`     → `W.-Y. Ma`              额外部分 `'W.-Y.'`   ⇒ 带点的首字母
        `C. Ré`     → `C. R´e`                额外部分 `'´'`（重音符，无实词）⇒ 空

    ## ⚠️ 一个**明知会漏掉**的情形，以及为什么不放宽

    A5 的原文举了另一个该合的例子：`Baeza-Yates` → `Ricardo A. Baeza-Yates`。
    **本判据认不出它** —— `Ricardo` 既不是"带点的首字母"也不是称谓，会被排除。

    **为什么不为了它放宽**：要认它，就得允许"额外部分是**首字母大写的单词**"。
    但那样 `Sparse Attention Vectors`（额外 `Sparse`）也会通过 ——
    而 `Sparse` 同样是首字母大写的单词。**两者在字符串上无法区分。**

    ⇒ 取舍：**保住"零误吸收"，放弃那一类漏吸收**（合并不可逆，宁可少不可错）。
    将来要认那一类，得用**外部信息**（姓名表、或它在语料里的上下文形态），
    **不能靠"首字母大写"这个弱信号**。
    """
    s, l = (short or '').strip(), (long_ or '').strip()
    if not s or not l:
        return False
    i = l.lower().find(s.lower())
    if i < 0:
        return False
    extra = (l[:i] + ' ' + l[i + len(s):]).strip()
    if not extra:
        return False
    # 公式的特征：有括号（`f(h, r, t)`）。tokens() 会把括号丢掉，这里必须自己挡。
    if '(' in extra or ')' in extra:
        return False
    for piece in extra.split():
        p = piece.strip(' ,;:')
        if not p:
            continue
        # 1) **带点的首字母形态**（W.- / A.）—— 必须在去点之前试：
        #    'W.-' 去掉末尾的点会变成 'W'，就认不出来了。
        if _PERSON_PIECE.match(p):
            continue
        # 2) 称谓 / et al. —— 这里要**去掉末尾的点**再比：
        #    'Et Al.' 切出来是 ['Et', 'Al.']，而集合里存的是 'al'。
        q = p.strip('.').lower()
        if q in _TITLE_PIECES or q in _ETAL_PIECES:
            continue
        return False
    return True


def extra_is_version_or_carrier(short_toks, long_toks) -> bool:
    """多出来的部分是否表示「不同版本」或「不同载体」。

    · **含数字** ⇒ 版本/规格差异（`7B` vs `16K`、`v1` vs `v2`）→ 不是同一实体。
      实测教训：金标实体 `Chinese-Alpaca-2 (7B)` 曾被吸收进同样是金标的
      `Chinese-Alpaca-2-16K (7B)`。
    · **全是载体词** ⇒ 指的是文章而不是概念（`review` vs `review papers`）。
    """
    extra = extra_tokens(short_toks, long_toks)
    if not extra:
        return False
    if any(any(ch.isdigit() for ch in t) for t in extra):
        return True
    if all(t in CARRIER_WORDS for t in extra):
        return True
    return False


def pages_of(node):
    """从证据里取页码集合。"""
    out = set()
    for e in node.get("evidence") or []:
        p = e.get("page")
        if p is not None:
            out.add(p)
    return out


def find_absorptions(nodes):
    """返回 [(short_name, long_name, reason)]，只报不改。

    复杂度：按实词数分桶，只比「长名实词数 > 短名实词数」的桶，避免 O(n²) 全比。
    """
    items = []
    for n in nodes or []:
        name = n.get("name") or n.get("id") or ""
        toks = tokens(name)
        if not toks:
            continue
        items.append({"name": name, "toks": toks, "pages": pages_of(n), "node": n})

    by_len = {}
    for it in items:
        by_len.setdefault(len(it["toks"]), []).append(it)

    out = []
    for it in items:
        st, sp = it["toks"], it["pages"]
        # 逐级找更长的候选（长 1 到长 4 个实词就够，再长几乎不可能同指）
        for extra in range(1, 5):
            for cand in by_len.get(len(st) + extra, []):
                ct = cand["toks"]
                if not is_contiguous_subsequence(st, ct):
                    continue
                # 条件 2：证据有共同页码
                if sp and cand["pages"] and not (sp & cand["pages"]):
                    continue
                # 条件 3：短名长度门限。
                #
                # **2026-09-14 试过放宽，结论是回退**。经过：先在小样本上看到
                # 「允许单词碎片」能把金标 F1 从 0.3941 抬到 0.3988、且金标零
                # 误吸收，差点就改了；幸亏同时查了全库影响面 ——
                # **3961 对（21.09%）**，而且明显有错：
                #     `2011` → `CAiSE 2011`（年份吸进会议名）
                #     `Adversarial` → `Self-Adversarial`（**意思相反**）
                #     `Accuracy` → `Search Accuracy`
                # 即：**在 2 篇金标上的 +0.0047 是过拟合，代价是全库 1/5 的实体
                # 被激进合并。** 所以维持「≥2 实词（或含逗号）」这条门限。
                #
                # 教训：只在小样本上验证不够，**任何归一规则都要同时看全库影响面**。
                if len(st) < 2 and "," not in it["name"]:
                    continue
                # 长度比：短名至少占长名一半，避免 "Data" 吸进超长标题
                if len("".join(st)) / max(1, len("".join(ct))) < 0.34:
                    continue
                # 条件 4：多出的部分是版本/载体标记 → 不是同一个实体
                if extra_is_version_or_carrier(st, ct):
                    continue
                # 条件 5（A5，2026-09-16）：**只有"人名补齐"才吸收**。
                #
                # 实测（`tools/_probe/_probe_absorb_1c*.py`）：全库 1404 对里
                # 人名仅 32 对（2.3%），其余抽样看**几乎全错** ——
                # `Structured Data`→`Semi-Structured Data`（`Semi-` 改了含义）、
                # `Question Answering`→`KG Question Answering`（上义词）、
                # `Knowledge Graphs`→`Traversing Knowledge Graphs in Vector Space`（论文标题）。
                #
                # ⇒ "长名包含短名"在有名词短语上**不足以判定同指**：
                # 多一个词通常意味着**更具体的另一个概念**。
                # ⇒ 只保留"多出来的部分不改变所指"的那一类。
                if not extra_is_person_form(it["name"], cand["name"]):
                    continue
                out.append((it["name"], cand["name"],
                            f'{"".join(st)} ⊂ {"".join(ct)}（同页 {sorted(sp & cand["pages"])}）'))
                break            # 一个短名只吸到最近的长名
    return out


def apply_absorptions(nodes, plan):
    """按 plan 执行吸收：把短名的证据并入长名，短名标 absorbed_into。

    **不删节点** —— 只加字段，保持图规模不变。
    """
    by_name = {}
    for n in nodes or []:
        by_name[n.get("name") or n.get("id")] = n
    for short, long_, _ in plan:
        s, l = by_name.get(short), by_name.get(long_)
        if not s or not l:
            continue
        merged = list(l.get("evidence") or [])
        seen = {(e.get("page"), e.get("chunk_idx")) for e in merged}
        for e in (s.get("evidence") or []):
            k = (e.get("page"), e.get("chunk_idx"))
            if k not in seen:
                merged.append(e)
                seen.add(k)
        l["evidence"] = merged
        s["absorbed_into"] = long_
    return nodes
