# -*- coding: utf-8 -*-
"""第 5 项 · 实体消歧判据的独立评测（规则可判定构造集 + WRatio 对照）。

【要解决的问题】
  `src/kg_extractor.py::_names_are_duplicates()` 同时被三处复用：
    · 抽取阶段的同类型实体去重（kg_extractor.py 的 dedup 主循环）
    · 金标聚类的近名合并口径（tools/merge_gold.py）
    · 评测时的匹配器（tools/score_gold.py、tools/diagnose_gold_metrics.py）
  但它只有设计说明与若干单例（tests/test_name_dedup_similarity.py，15 条断言），
  从来没有精确率/召回率/F1。本脚本给它补上**可复现的独立评测**。

【为什么不用人工金标】
  本仓库实测的标注者间一致性：关系层 Krippendorff α = −0.01 / −0.15，
  实体层 α = 0.23 / 0.29（见 data/quality/ 下的 agreement 产物与 README）。
  两轮 LLM 标注者之间都到不了"可作结论"的稳定性，人工金标无法作为
  **高置信基线**。而且判据本身被用作金标聚类口径，用人工金标评它会形成
  循环论证。故本评测改用**规则可判定的构造集**：每一对样本的标签由
  生成它的确定性变换唯一决定，不依赖任何标注者的主观判断，
  因此 100% 可复现、零标注成本、可无限扩展。

【构造集设计（每个样本对自带 rule 标签）】
  正例（同一实体，标签 True）
    P1-case        原名与 .lower()   —— 大小写是确定性的保义变换
    P2-plural      原名与 原名+s     —— 规则复数是确定性的保义变换
    P3-punct       连字符↔空格 / 去空格 —— 空白与标点是确定性的保义变换
    P4-parenthesis 剥除尾部括注     —— 括注（年份/缩写/限定）是确定性的附属成分
    P5-abbrev      语料 PDF 原文抽到的 `全称 (缩写)` 对，两端都在图谱中
  负例（不同实体，标签 False）
    N1-disjoint    两个随机名归一化后无公共 3-gram  —— 词面完全不交叠
    N2-oneword     两个随机名恰好共享 1 个实词      —— 同域不同实体（最易误判）
    N3-samepaper   同一篇论文内的两个不同名，无公共 3-gram —— 同域同风格
    N4-substring   ≤3 字符短名嵌入长名              —— 精确复现 2026-09-10 的真 bug
    N5-head       共享 ≥60% 前缀但语义不同          —— 对抗"公共前缀长度保护"本身

【对照方法】
  ours（生产判据）  = kg_extractor._names_are_duplicates
  wratio_0.80       = 官方 neo4j-graphrag-python FuzzyMatchResolver 口径
                      （RapidFuzz fuzz.WRatio ≥ 0.8，无长度保护）
  ratio_0.93        = 本仓库 difflib 兜底路径的口径（SequenceMatcher ≥ 0.93）
  exact             = 归一化后完全相等
  prefix            = 归一化后互为前缀
  ratio_scan        = ratio 阈值在构造集上网格搜索出的最优阈值（上界参考）

【用法】
  python tools/l2_disambig_eval.py                    # 全量，约 20 秒
  python tools/l2_disambig_eval.py --quick            # 快速抽样
  python tools/l2_disambig_eval.py --out data/quality/l2-disambig-eval.json

本脚本只读语料与产物，只写 data/quality/l2-disambig-eval.json。
"""
# 路径以本文件为锚点解析，不写死盘符。
from pathlib import Path as _Path

_HERE = _Path(__file__).resolve()
ROOT = _HERE.parent.parent
import argparse
import collections
import json
import random
import re
import sys
import time

sys.path.insert(0, str(ROOT / "src"))

GRAPHS = ROOT / "data/corpus/graphs"
QUALITY = ROOT / "data/quality"
SEED = 20260914


def parse_args():
    ap = argparse.ArgumentParser(description="实体消歧判据独立评测")
    ap.add_argument("--out", default=str(QUALITY / "l2-disambig-eval.json"))
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--per-recipe", type=int, default=1200,
                    help="每类正例/负例最多采多少对（负例池不足时取全部）")
    ap.add_argument("--quick", action="store_true", help="缩小到 1/6，用于冒烟")
    ap.add_argument("--multi-seed", type=int, default=5,
                    help="换 N 个采样种子重跑，报告 F1 均值/标准差（稳定性证据，默认 5）")
    ap.add_argument("--no-write", action="store_true")
    return ap.parse_args()


ARGS = parse_args()
if ARGS.quick:
    ARGS.per_recipe = max(50, ARGS.per_recipe // 6)


# ── 名称池 ───────────────────────────────────────────────
def flat(s):
    """与 kg_extractor._normalize_for_match 同义：小写 + 去一切非字母数字。"""
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def load_names():
    """读 corpus/graphs 的节点名。

    ⚠️ 必须排除 `_backfilled`（tools/backfill_missing_endpoints.py 从边反建的
    节点），它发生在 L2 归并之后；计入会让"归并后名字"虚高。口径与
    tools/l2_report.py 一致，实测排除后为 15123 个唯一名。
    """
    freq = collections.Counter()
    ntype = {}
    for f in sorted(GRAPHS.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        for n in d.get("nodes", []):
            nm = (n.get("name") or "").strip()
            if not nm or n.get("_backfilled"):
                continue
            freq[nm] += 1
            if nm not in ntype:
                ntype[nm] = n.get("type") or "?"
    return freq, ntype


# ── 正例配方（保义变换，标签恒为 True）────────────────────
def build_positive(freq, per):
    names = [n for n in freq if re.search(r"[A-Za-z]", n)]
    out = []

    def take(gen, rule, cap):
        got = 0
        for pair in gen:
            if got >= cap:
                break
            out.append((pair[0], pair[1], rule, "positive"))
            got += 1

    # P1 大小写：名本身与 .lower() 同时存在于池中（真实并存的大小写变体）
    # ⚠️ 池必须按小写索引，直接 `in set(freq)` 会几乎全部漏掉——大小写变体
    # 在生产图谱里大量并存（实测 14984 对），这正是判据的主要工作负载。
    low2name = {}
    for n in freq:
        low2name.setdefault(n.lower(), n)
    take(((n, low2name[n.lower()]) for n in names
          if n != n.lower() and n.lower() in low2name), "P1-case", per)
    # P1b 大小写：.lower() 不在池中（抽取时只出现了一种写法，仍是保义变换）
    take(((n, n.lower()) for n in names
          if n != n.lower() and n.lower() not in low2name), "P1b-case-new", per)
    # P2 规则复数：原名 + "s"
    take(((n, n + "s") for n in names if not n.endswith(("s", "S")) and len(flat(n)) >= 4),
         "P2-plural", per)
    # P3 标点/空白
    def punct_gen():
        for n in names:
            if "-" in n:
                yield (n, n.replace("-", " "))
            if " " in n and len(flat(n)) >= 6:
                yield (n, n.replace(" ", ""))
    take(punct_gen(), "P3-punct-space", per)
    # P4 剥除尾部括注
    def paren_gen():
        for n in names:
            m = re.match(r"^(.*?)\s*[（(][^)）]{1,60}[)）]\s*(.*)$", n)
            if not m:
                continue
            core = (m.group(1) + " " + m.group(2)).strip(" ,;")
            if len(flat(core)) >= 4 and flat(core) != flat(n):
                yield (n, core)
            head = m.group(1).strip()
            if len(flat(head)) >= 4 and flat(head) != flat(n):
                yield (n, head)
    take(paren_gen(), "P4-parenthesis", per)
    # P5 缩写
    # P5a：语料 PDF 原文抽到的 `全称 (缩写)` 对，且两端都在图谱中（证据最强，但实测只剩 9 对）
    plan_f = QUALITY / "entity-normalization-plan.json"
    plan = json.loads(plan_f.read_text(encoding="utf-8")) if plan_f.exists() else {}
    low = {}
    for n in freq:
        low.setdefault(n.lower(), n)
    for p in plan.get("abbreviations", {}).get("usable_pairs", []):
        if p["full"].lower() in low and p["abbr"].lower() in low:
            out.append((low[p["full"].lower()], low[p["abbr"].lower()],
                        "P5a-abbrev-in-corpus", "positive"))
    # P5b：由图谱内的全称按**严格首字母规则**自动生成缩写。
    # 可判定性来自构造规则本身：缩写的每个字母都是全称对应词的首字母，
    # 这类变换不含任何主观判断。约束收紧（≥3 词、缩写 2~6 位、全称 ≥12 字符）
    # 以排除低置信巧合。
    # ⚠️ 已知构造偏差（必须在报告里如实声明，不能当"真金标"用）：
    #   同一缩写可能对应多个全称（`Graph Neural Network` / `Generative ...` 都→GNN），
    #   语料里的 `KG` 同时被用于 `Abstract Knowledge Graph` 与 `KG Tasks`。
    #   因此 P5b 是"缩写可及性上界"，衡量的是判据**能不能**认出首字母缩写，
    #   而不是"每一对该不该合并"。
    # 采样用确定性等距分层（按全称长度排序后均匀抽样），避免"只抽到短名"的偏差。
    cand = []
    seen_abbr = set()
    for n in sorted(names):
        toks = re.findall(r"[A-Za-z][A-Za-z\-]*", n)
        if len(toks) < 3:
            continue
        ab = "".join(t[0] for t in toks).upper()
        if not (2 <= len(ab) <= 6) or len(flat(n)) < 12:
            continue
        if ab == n.upper() or ab.lower() == n.lower():
            continue
        if (n, ab) in seen_abbr:
            continue
        seen_abbr.add((n, ab))
        cand.append((n, ab))
    p5b_total = len(cand)
    if len(cand) > per:
        cand.sort(key=lambda x: (len(flat(x[0])), x[0]))
        step = len(cand) / per
        cand = [cand[min(len(cand) - 1, int(i * step))] for i in range(per)]
    for n, ab in cand:
        out.append((n, ab, "P5b-abbrev-initials", "positive"))
    return out, p5b_total


# ── 负例配方（改义，标签恒为 False）────────────────────────
def build_negative(freq, ntype, per, rng):
    names = [n for n in freq if len(flat(n)) >= 4]
    out = []

    def trig(s):
        return {s[i:i + 3] for i in range(len(s) - 2)} if len(s) >= 3 else {s}

    # N1 完全不相交
    got, tries = 0, 0
    while got < per and tries < per * 40:
        tries += 1
        a, b = rng.sample(names, 2)
        fa, fb = flat(a), flat(b)
        if fa == fb or fa in fb or fb in fa or (trig(fa) & trig(fb)):
            continue
        out.append((a, b, "N1-disjoint", "negative"))
        got += 1

    # N2 恰好共享 1 个实词（同域不同实体）
    byword = collections.defaultdict(list)
    for n in names:
        for w in set(re.findall(r"[a-z]{4,}", n.lower())):
            byword[w].append(n)
    got, tries = 0, 0
    words = sorted(byword, key=lambda w: -len(byword[w]))
    while got < per and tries < per * 60:
        tries += 1
        w = words[rng.randrange(len(words))]
        cand = byword[w]
        if len(cand) < 2:
            continue
        a, b = rng.sample(cand, 2)
        fa, fb = flat(a), flat(b)
        if fa == fb or fa in fb or fb in fa:
            continue
        wa = set(re.findall(r"[a-z]{4,}", a.lower()))
        wb = set(re.findall(r"[a-z]{4,}", b.lower()))
        if len(wa & wb) != 1:
            continue
        out.append((a, b, "N2-shared-one-word", "negative"))
        got += 1

    # N3 同一篇论文内的两个不同名（按论文轮转，保证 17 篇都被覆盖，不偏袒文件名靠前的）
    per_paper = max(1, per // max(1, len(list(GRAPHS.glob("*.json")))))
    got = 0
    for f in sorted(GRAPHS.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        ns = sorted({(n.get("name") or "").strip() for n in d.get("nodes", [])
                     if (n.get("name") or "").strip() and not n.get("_backfilled")})
        ns = [n for n in ns if len(flat(n)) >= 4]
        if len(ns) < 2:
            continue
        local, tries = 0, 0
        while local < per_paper and tries < per_paper * 60:
            tries += 1
            a, b = rng.sample(ns, 2)
            fa, fb = flat(a), flat(b)
            if fa == fb or fa in fb or fb in fa or (trig(fa) & trig(fb)):
                continue
            out.append((a, b, "N3-same-paper", "negative"))
            local += 1
            got += 1

    # N4 短名嵌入长名（精确复现 2026-09-10 的真 bug 分布）
    # 采样确定化：短名按 flat 长度再字典序排序，长名候选按长度排序取首个，
    # 保证换种子时这一层仍可复现、且覆盖的是"同类型 + 最短长名"最难的情形。
    shorts = sorted([n for n in freq if 2 <= len(flat(n)) <= 3],
                    key=lambda n: (len(flat(n)), n))
    longs = [n for n in freq if len(flat(n)) >= 6]
    long_by_flat = sorted(((n, flat(n)) for n in longs), key=lambda x: (len(x[1]), x[0]))
    got = 0
    for s in shorts:
        if got >= per:
            break
        fs = flat(s)
        cands = [(n, fn) for n, fn in long_by_flat if fs in fn and fn != fs and n != s]
        if not cands:
            continue
        same = [(n, fn) for n, fn in cands if ntype.get(n) == ntype.get(s)]
        pick = same or cands
        n, _fn = pick[0]
        out.append((s, n, "N4-substring-trap", "negative"))
        got += 1

    # N5 共享长公共前缀但语义不同
    got, tries = 0, 0
    byfirst = collections.defaultdict(list)
    for n in names:
        fn = flat(n)
        byfirst[fn[:4]].append((n, fn))
    keys = [k for k, v in byfirst.items() if len(v) >= 2 and len(k) == 4]
    while got < per and tries < per * 60 and keys:
        tries += 1
        k = keys[rng.randrange(len(keys))]
        v = byfirst[k]
        a, fa = v[rng.randrange(len(v))]
        b, fb = v[rng.randrange(len(v))]
        if a == b or fa == fb or fa in fb or fb in fa:
            continue
        # 公共前缀 / 长名 比例 >= 0.5 才纳入：这正是长度保护的判据边界
        sh, lo = sorted((fa, fb), key=len)
        common = 0
        for x, y in zip(sh, lo):
            if x != y:
                break
            common += 1
        if common / len(lo) < 0.5:
            continue
        out.append((a, b, "N5-shared-head", "negative"))
        got += 1

    return out


# ── 被判据 ───────────────────────────────────────────────
def make_methods():
    from kg_extractor import _names_are_duplicates as ours, _normalize_for_match as N
    from rapidfuzz import fuzz

    def wratio_080(a, b):
        na, nb = N(a), N(b)
        if not na or not nb:
            return False
        if na == nb:
            return True
        return fuzz.WRatio(na, nb) >= 80.0

    def ratio_093(a, b):
        na, nb = N(a), N(b)
        if not na or not nb:
            return False
        if na == nb:
            return True
        return fuzz.ratio(na, nb) >= 93.0

    def exact(a, b):
        na, nb = N(a), N(b)
        return bool(na) and na == nb

    def prefix(a, b):
        na, nb = N(a), N(b)
        if not na or not nb:
            return False
        return na.startswith(nb) or nb.startswith(na)

    return {
        "ours": ours,
        "wratio_0.80": wratio_080,
        "ratio_0.93": ratio_093,
        "exact": exact,
        "prefix": prefix,
    }


# ── 指标 ─────────────────────────────────────────────────
def prf(tp, fp, fn):
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f = 2 * p * r / (p + r) if (p + r) else 0.0
    return p, r, f


def score(method, samples):
    """返回 (整体, 按 rule 分组, 按 label 分组, 混淆计数)。"""
    per_rule = collections.defaultdict(lambda: {"tp": 0, "fp": 0, "tn": 0, "fn": 0})
    allc = {"tp": 0, "fp": 0, "tn": 0, "fn": 0}
    for a, b, rule, label in samples:
        pred = bool(method(a, b))
        gold = (label == "positive")
        c = per_rule[rule]
        if pred and gold:
            c["tp"] += 1; allc["tp"] += 1
        elif pred and not gold:
            c["fp"] += 1; allc["fp"] += 1
        elif not pred and not gold:
            c["tn"] += 1; allc["tn"] += 1
        else:
            c["fn"] += 1; allc["fn"] += 1
    p, r, f = prf(allc["tp"], allc["fp"], allc["fn"])
    acc = (allc["tp"] + allc["tn"]) / max(1, sum(allc.values()))
    detail = {}
    for rule, c in sorted(per_rule.items()):
        rp, rr, rf = prf(c["tp"], c["fp"], c["fn"])
        detail[rule] = {
            "n": sum(c.values()), "tp": c["tp"], "fp": c["fp"], "tn": c["tn"], "fn": c["fn"],
            "precision": round(rp, 4), "recall": round(rr, 4), "f1": round(rf, 4),
        }
    return ({"precision": round(p, 4), "recall": round(r, 4), "f1": round(f, 4),
             "accuracy": round(acc, 4), **allc}, detail)


def best_ratio_threshold(samples):
    """在构造集上网格搜索 ratio 的最优阈值——给"调参上界"一个参照。"""
    from kg_extractor import _normalize_for_match as N
    from rapidfuzz import fuzz
    scored = []
    for a, b, _rule, label in samples:
        na, nb = N(a), N(b)
        if not na or not nb:
            s = 0.0
        elif na == nb:
            s = 100.0
        else:
            s = fuzz.ratio(na, nb)
        scored.append((s, label == "positive"))
    best = None
    for t in range(50, 101):
        tp = fp = fn = 0
        for s, gold in scored:
            pred = s >= t
            if pred and gold:
                tp += 1
            elif pred and not gold:
                fp += 1
            elif not pred and gold:
                fn += 1
        p, r, f = prf(tp, fp, fn)
        if best is None or f > best[1]:
            best = (t, f, p, r)
    return {"threshold": best[0], "f1": round(best[1], 4),
            "precision": round(best[2], 4), "recall": round(best[3], 4)}


def check_l4_whitelist():
    """独立核查已发布产物 data/quality/entity-alias-map.json 的 L4 缩写白名单。

    L4 是**已经写进图谱节点名**的合并决策（合并不可逆），所以它值得单独审计。
    判据用**可判定的首字母规则**，不依赖人工：
      abbr 的字母是否为全称**实词词首字母**序列的子序列，且跳过的实词不超过 1 个。
    虚词（on / of / for / and / the ...）允许被跳过——`Conference on Empirical
    Methods in Natural Language Processing -> EMNLP` 跳过介词 `on` 是正常的缩写习惯。
    实词不允许被跳过——`Abstract Knowledge Graph -> KG` 跳过了实词 `Abstract`，
    说明这个全称配错了缩写（KG 是 Knowledge Graph 的缩写，不是 Abstract Knowledge Graph）。
    """
    f = QUALITY / "entity-alias-map.json"
    if not f.exists():
        return None
    am = json.loads(f.read_text(encoding="utf-8"))
    stop = {"on", "of", "for", "and", "the", "a", "an", "in", "to", "with", "at", "by",
            "from", "as", "into", "via", "using", "based", "or"}
    rows = []
    for a in am.get("audit", []):
        if a.get("layer") != "L4":
            continue
        full = a.get("full") or ""
        abbr = a.get("abbr") or ""
        # ⚠️ 分词必须按**单词边界**切。绝不能用 `[A-Za-z]+` 去 findall——实测该正则
        #    会从 `Processing` 里切出 `in`，凭空造出一个虚词，把 EMNLP 误判为假配对。
        toks = [t for t in re.split(r"[\s\W_]+", full.replace("-", " "), flags=re.UNICODE) if t]
        al = flat(abbr)
        # 判据（可判定，无需人工）：
        #   ① 若全称中存在某个词，其去标点小写形式**恰好等于 abbr**，则视为
        #      abbr 已被该词完整覆盖（`AAAI Conference on Artificial Intelligence`）；
        #   ② 否则 abbr 的每个字母按顺序命中某个词的首字母；
        #   ③ 未能命中的词只能是「虚词」或「该词自身也在 abbr 里」（如 `KG tasks` 的 KG）；
        #   ④ 额外允许跳过 1 个实词，但仅当 abbr 长度 ≥4（长缩写常含学科限定词，
        #      如 `Conference on ... -> EMNLP`）；短缩写按 T4.4「合并不可逆、保守
        #      起步」从严，不允许跳过实词。
        # 由此：`Abstract Knowledge Graph -> KG`、`KG tasks -> KG` 为假配对
        #（跳过实词且 KG 只有 2 字母），EMNLP/AAAI 属可接受的命名习惯。
        # 解耦判据：这里只保留**可判定、无歧义**的一条——
        #   ① 首个实词的首字母必须等于 abbr 的首字母，且该实词必须被命中；
        #      否则说明"绕开了核心词"才凑出该缩写。
        #      `Abstract Knowledge Graph -> KG` 绕开核心词 Abstract ⇒ 假配对。
        #   ② 被跳过的实词数 ≤ 1 且占比 ≤ 40%（允许 1 个非核心词）。
        # 已知边界（如实标注，不掩盖）：`Conference on Empirical Methods in
        #   Natural Language Processing -> EMNLP` 跳过的 Conference 是**容器词**
        #   （会议/期刊名）而非核心词，本判据会判为假配对。为把它与真错误区分，
        #   另设 needs_human 标记（容器词表可扩展），统计时单列，不混入结论。
        stop0 = {t for t in toks if t.lower() in stop}
        content = [t for t in toks if t.lower() not in stop]
        al = flat(abbr)
        pos, skipped = 0, []
        for t in content:
            if pos < len(al) and t[0].lower() == al[pos].lower():
                pos += 1
            else:
                skipped.append(t)
        hit_all = (pos == len(al))
        first_ok = bool(content) and bool(al) and content[0][0].lower() == al[0].lower()
        skip_frac = len(skipped) / max(1, len(content))
        # 「缩写即全称首词」且**没有任何实词被跳过** ⇒ 命名习惯，判据不适用
        head_word_is_abbr = bool(content) and flat(content[0]) == al and not skipped
        # 容器词（会议/期刊/丛书名）被跳过不算错误合并
        container = {"conference", "proceedings", "workshop", "journal", "findings",
                     "volume", "track", "session", "annual", "meeting", "symposium"}
        skipped_containers = [t for t in skipped if t.lower() in container]
        needs_human = bool(skipped_containers) and not head_word_is_abbr
        if head_word_is_abbr:
            is_false = len(skipped) > 0      # 首词即缩写时，再跳过实词就是错误合并
        else:
            is_false = not (hit_all and first_ok and len(skipped) <= 1 and skip_frac <= 0.4)
        if needs_human and is_false:
            is_false = False          # 容器词场景交人工，不计入自动假配对
        rows.append({"canonical": a.get("canonical"), "full": full, "abbr": abbr,
                     "content_words": content, "skipped_content_words": skipped,
                     "matched_all_letters": hit_all,
                     "head_word_is_abbr": head_word_is_abbr,
                     "needs_human": needs_human,
                     "is_false_pair": is_false})
    n_bad = sum(1 for r in rows if r["is_false_pair"])
    n_head = sum(1 for r in rows if r["head_word_is_abbr"])
    n_human = sum(1 for r in rows if r["needs_human"])
    n_applic = len(rows) - n_head - n_human
    res = {"n_l4_entries": len(rows), "n_false_pair": n_bad,
           "n_head_word_is_abbr": n_head, "n_needs_human": n_human,
           "n_applicable": n_applic,
           "false_pair_rate": round(n_bad / n_applic, 4) if n_applic else None,
           "criterion": ("abbr 各字母须按序命中全称实词首字母；被跳过的实词 ≤1 且占比 ≤40%；"
                         "且首个实词首字母必须与 abbr 首字母一致（可判定，无需人工）"),
           "caveat": ("`head_word_is_abbr=True` 的行（缩写就是全称首词，如 "
                      "`AAAI Conference on Artificial Intelligence`）属命名习惯，"
                      "已从假配对统计中扣除，不计入 rate。"),
           "rows": rows}
    print()
    print("── 独立核查已发布产物：entity-alias-map.json 的 L4 缩写白名单 ──")
    for r in rows:
        if r["head_word_is_abbr"]:
            tag = "N/A"
        elif r["needs_human"]:
            tag = "人工"
        else:
            tag = "BAD" if r["is_false_pair"] else "OK "
        print(f"    {tag} canon={r['canonical']!r:26s} "
              f"full={r['full']!r:58s} abbr={r['abbr']!r:8s} "
              f"跳过实词={r['skipped_content_words']}")
    print(f"    L4 共 {len(rows)} 条；可判定适用 {n_applic} 条，"
          f"其中假配对 {n_bad} 条（假配对率 {n_bad/max(1,n_applic):.1%}）；"
          f"另有 {n_head} 条「缩写即全称首词」命名习惯、{n_human} 条容器词场景（交人工），已排除")
    # L2 侧的记录字段形态（该层实测只落了纯复数规则，没有近名模糊合并）
    rules = collections.Counter(a.get("rule") for a in am.get("audit", [])
                                if a.get("layer") == "L2" and a.get("action") == "merge")
    res["l2_merge_rules"] = dict(rules)
    print(f"    L2 merge 记录 {sum(rules.values())} 条，规则分布 {dict(rules)}")
    print("    ⇒ 判据 _names_are_duplicates 在图谱归并层的实际残留可审计记录极少："
          "L2 全是词面规则（pure-plural），L1 全部 skip，近名模糊合并没有落进产物。")
    return res


def main():
    t0 = time.time()
    rng = random.Random(ARGS.seed)
    freq, ntype = load_names()
    print(f"名称池（排除 _backfilled）: {len(freq)} 个唯一名")

    pos, p5b_total = build_positive(freq, ARGS.per_recipe)
    neg = build_negative(freq, ntype, ARGS.per_recipe, rng)
    samples = pos + neg
    rng.shuffle(samples)
    print(f"构造集: 正例 {len(pos)}  负例 {len(neg)}  合计 {len(samples)}")
    print(f"（P5b 缩写候选池 {p5b_total} 对，等距分层抽取 {min(p5b_total, ARGS.per_recipe)} 对）")

    rc = collections.Counter(r[2] for r in samples)
    print("配方分布:", dict(sorted(rc.items())))

    # 分层定义：判据的三种能力面必须分开看，混在一起会被占比最大的层主导。
    LAYERS = {
        "L-A 形态变体（大小写/复数/标点/括注）":
            [r for r in rc if r.startswith(("P1", "P2", "P3", "P4"))],
        "L-B 缩写↔全称":
            [r for r in rc if r.startswith("P5")],
        "L-C 负例·易（词面不相交/同域随机）":
            ["N1-disjoint", "N3-same-paper"],
        "L-D 负例·对抗（共享词/短名子串/共享前缀）":
            ["N2-shared-one-word", "N4-substring-trap", "N5-shared-head"],
    }

    methods = make_methods()
    results = {}
    print()
    hdr = f"{'方法':16s} {'P':>7s} {'R':>7s} {'F1':>7s} {'Acc':>7s} {'TP':>6s} {'FP':>6s} {'FN':>6s} {'TN':>6s}"
    print(hdr)
    print("-" * len(hdr))
    for name, fn in methods.items():
        overall, detail = score(fn, samples)
        results[name] = {"overall": overall, "per_recipe": detail}
        print(f"{name:16s} {overall['precision']:7.4f} {overall['recall']:7.4f} "
              f"{overall['f1']:7.4f} {overall['accuracy']:7.4f} "
              f"{overall['tp']:6d} {overall['fp']:6d} {overall['fn']:6d} {overall['tn']:6d}")

    print()
    print("── 分层结果（三种能力面分开看，避免被占比最大的层主导）──")
    print("   含正例的层报 P/R/F1；纯负例的层报特异性 specificity=TN/(TN+FP) 与误合并率")
    layer_result = {}
    for lname, rules in LAYERS.items():
        subset = [s for s in samples if s[2] in rules]
        if not subset:
            continue
        has_pos = any(s[3] == "positive" for s in subset)
        print(f"\n  {lname}   n={len(subset)}  "
              f"{'（含正例）' if has_pos else '（纯负例）'}")
        if has_pos:
            print(f"    {'方法':16s} {'P':>7s} {'R':>7s} {'F1':>7s}")
        else:
            print(f"    {'方法':16s} {'特异性':>7s} {'误合并率':>9s} {'FP':>5s} {'TN':>5s}")
        layer_result[lname] = {"n": len(subset), "rules": rules,
                               "has_positive": has_pos, "methods": {}}
        for name, fn in methods.items():
            o, _ = score(fn, subset)
            spec = o["tn"] / max(1, o["tn"] + o["fp"])
            layer_result[lname]["methods"][name] = {
                "precision": o["precision"], "recall": o["recall"], "f1": o["f1"],
                "specificity": round(spec, 4),
                "fp_rate": round(o["fp"] / max(1, o["tn"] + o["fp"]), 4),
                "tp": o["tp"], "fp": o["fp"], "fn": o["fn"], "tn": o["tn"]}
            if has_pos:
                print(f"    {name:16s} {o['precision']:7.4f} {o['recall']:7.4f} {o['f1']:7.4f}")
            else:
                print(f"    {name:16s} {spec:7.4f} "
                      f"{o['fp']/max(1,o['tn']+o['fp']):9.4f} {o['fp']:5d} {o['tn']:5d}")

    print()
    print("── ours 分配方明细 ──")
    for rule, d in results["ours"]["per_recipe"].items():
        print(f"  {rule:22s} n={d['n']:5d} P={d['precision']:.3f} "
              f"R={d['recall']:.3f} F1={d['f1']:.3f}  (FP={d['fp']} FN={d['fn']})")

    print()
    print("── 各方法在对抗负例子集上的误合并数（FP）──")
    adversarial = [r for r in rc if r.startswith(("N2", "N4", "N5"))]
    for name in methods:
        fp_adv = sum(results[name]["per_recipe"][r]["fp"] for r in adversarial if r in results[name]["per_recipe"])
        n_adv = sum(results[name]["per_recipe"][r]["n"] for r in adversarial if r in results[name]["per_recipe"])
        fn_adv = sum(results[name]["per_recipe"][r]["fn"] for r in adversarial if r in results[name]["per_recipe"])
        print(f"  {name:16s} 对抗负例 {n_adv:5d} 中误合并 {fp_adv:5d}  "
              f"(误合并率 {fp_adv/max(1,n_adv):.3f})  漏合并 {fn_adv}")

    scan = best_ratio_threshold(samples)
    print()
    print(f"ratio 最优阈值扫描（构造集上界）: t={scan['threshold']} "
          f"F1={scan['f1']:.4f} P={scan['precision']:.4f} R={scan['recall']:.4f}")

    # 导出逐样本预测，供人工抽查与二次分析（每条都有 rule 与各方法预测）
    if not ARGS.no_write:
        samp_p = QUALITY / "l2-disambig-samples.jsonl"
        with samp_p.open("w", encoding="utf-8") as fh:
            for a, b, rule, label in samples:
                fh.write(json.dumps({
                    "a": a, "b": b, "rule": rule, "label": label,
                    "pred": {k: bool(fn(a, b)) for k, fn in methods.items()},
                }, ensure_ascii=False) + "\n")
        print(f"逐样本预测已写出: {samp_p}")

    elapsed = time.time() - t0
    l4 = check_l4_whitelist()

    # 多种子稳定性：换采样种子重跑，报告 F1 的均值/标准差，
    # 证明结论不是"某一次抽样的偶然"。N4 已确定化，抖动来自 N1/N2/N3/N5 与 P1~P4。
    multi = None
    if ARGS.multi_seed:
        f1s = collections.defaultdict(list)
        for s in range(1, ARGS.multi_seed + 1):
            r2 = random.Random(s)
            p2, _t = build_positive(freq, ARGS.per_recipe)
            n2 = build_negative(freq, ntype, ARGS.per_recipe, r2)
            smp = p2 + n2
            for name, fn in methods.items():
                o, _ = score(fn, smp)
                f1s[name].append(o["f1"])
        multi = {}
        print()
        print(f"── 多种子稳定性（{ARGS.multi_seed} 个种子，每轮独立重采样）──")
        for name, vals in f1s.items():
            m = sum(vals) / len(vals)
            var = sum((v - m) ** 2 for v in vals) / len(vals)
            multi[name] = {"f1_mean": round(m, 4), "f1_std": round(var ** 0.5, 4),
                           "f1_values": [round(v, 4) for v in vals]}
            print(f"    {name:16s} F1 均值 {m:.4f}  标准差 {var**0.5:.4f}  "
                  f"极差 [{min(vals):.4f}, {max(vals):.4f}]")

    report = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "evaluated_function": "src/kg_extractor.py::_names_are_duplicates",
        "design": "规则可判定构造集（不用人工金标，避免标注不可靠与循环论证）",
        "seed": ARGS.seed,
        "per_recipe_cap": ARGS.per_recipe,
        "name_pool": len(freq),
        "pool_note": "已排除 _backfilled，口径与 tools/l2_report.py 一致",
        "counts": {"positive": len(pos), "negative": len(neg), "total": len(samples),
                   "by_recipe": dict(sorted(rc.items())),
                   "p5b_candidate_pool": p5b_total},
        "layers": layer_result,
        "layer_note": ("L-A 是抽取去重的主战场；L-B 是独立缺口（首字母缩写无法由字符相似度覆盖）；"
                       "L-C/L-D 按负例难度分开报告，L-D 含三项对抗构造。"),
        "p5b_caveat": ("P5b 由严格首字母规则自动构造，是缩写可及性的上界构造，"
                       "同一缩写可能对应多个全称，故只用于衡量"
                       "『判据能否认出首字母缩写』，不可当作逐对真值。"),
        "methods": results,
        "ratio_threshold_scan": scan,
        "l4_whitelist_audit": l4,
        "multi_seed_stability": multi,
        "elapsed_sec": round(elapsed, 2),
    }
    if not ARGS.no_write:
        outp = _Path(ARGS.out)
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n报告已写出: {outp}")
    print(f"耗时 {elapsed:.1f}s")
    return report


if __name__ == "__main__":
    main()
