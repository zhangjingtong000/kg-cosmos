"""实体归一化（保守版）：L1 大小写 + L2 安全复数 + L4 缩写白名单。

【设计原则：保守优先，因为合并不可逆】
  见 tools/plan_entity_normalization.py 的风险评估：
    R1 大小写 163 组 / 类型冲突 52
    R2 复数   494 组 / 类型冲突 246   ← 冲突率过高，必须过滤后才能用
    R3 标点   171 组 / 类型冲突 58
    R4 缩写   抽到 512 对，其中 101 对两端都在图谱，但**存在假配对**

【三层过滤器（每一层都为了拦住误合并）】
  F1 空集保护：不合并"名字去掉标点后为空"的键
  F2 短名保护：合并组内任一名字去标点后长度 ≤3 ⇒ 跳过
     （`KG` vs `Kg` 看似安全，但短缩写歧义高，留给 R4 白名单处理）
  F3 语义敏感对黑名单：大小写差异会改变语义的名字对，禁止合并
     （`ComplEx`(方法) vs `Complex`(概念) 是不同实体，不是大小写变体）

【L4 缩写白名单】
  只采用"两端都是图谱中真实实体 + 全称长度≥6 + 缩写≥2 且全大写 +
  全称不是 <10 个高频期刊/会议栏目词"的对。
  实测把 `Findings <-> EMNLP`（Findings 是栏目名，非实体）、
  `Mean Rank <-> Mr`（Mr 是"先生"）这两类假配对滤掉。

【合并方式】
  组内取**频次最高**的名字作为 canonical，其余作为 alias。
  只改节点名与边端点，不删除任何边（先合并节点，边由端点名映射自动收敛）。

【安全设计】
  · 默认 dry-run；`--apply` 才写
  · 写前备份到 data/quality/backup/normalize-<ts>/
  · 产出 alias 映射表（可复用、可审计）

用法：
  python tools/entity_normalize.py                # dry-run
  python tools/entity_normalize.py --apply        # 写入
  python tools/entity_normalize.py --rollback <dir>
"""
import argparse
import json
import re
import shutil
import time
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

GRAPHS = Path(r"D:\kg-demo\data\corpus\graphs")
CORPUS = Path(r"D:\kg-demo\data\corpus")
OUT = Path(r"D:\kg-demo\data\quality")
BACKUP = OUT / "backup"

# ── F3 语义敏感对黑名单：大小写差异改变含义，禁止合并 ──
# 判据：先把组内名字折叠到"只留字母"的形式，若折叠后相同但**大小写模式不同**，
# 说明差异全在字母大小写上——这类必须逐个审查（模型名 vs 普通词）。
# 已知会误合并的对（实测）：
#   ComplEx(方法, 复数双线性模型) vs Complex(概念, "复数")  ← 不同实体
#   Semantics(概念) vs SEMANTiCS(会议)                      ← 不同实体
SENSITIVE_PAIRS = [
    {"complex", "complext"},          # 实测样本
    {"complex", "complex"},           # 占位，实际由下面的显式名单处理
]
# 显式禁止合并的名字对（全部小写比较；组内出现任一对即跳过）
F3_FORBIDDEN = [
    {"complext", "complex"},          # ComplEx vs Complex
    {"complext", "complext"},         # 占位
    {"semantics", "semantics"},       # 由 SENSITIVE 处理
]
# 更稳健的表述：把"字母序列完全相同、仅大小写不同"的对，若其一是已知模型名则跳过
KNOWN_MODEL_NAMES = {
    "complex",    # ComplEx（复数双线性模型）
    "distmult", "rotate", "quate", "transe", "transh", "transr", "transd",
    "tucker", "rescal", "hole", "analogy", "simplE".lower(), "convkb", "kbGAT".lower(),
}
# 已确认的"大小写变体但语义不同"对（小写后比较）
# ⚠️ 注意：ComplEx 的小写同样是 "complex"，与普通词 Complex 撞键。
# 因此这里拦的是 (complex, complex)——即"组内出现已知模型名 + 普通词"的情形。
# 该名单只包含确实撞键的模型名，避免误伤 arXiv/ArXiv 这类安全合并。
F3_SKIP_LOWER = [
    ("complex", "complex"),
]
# 保留旧的组级敏感集合以兼容
SENSITIVE = SENSITIVE_PAIRS
# 高频"栏目/虚词"名，不参与缩写全称合并
STOPWORD_FULL = re.compile(
    r"^(findings?|proceedings|volume|pages?|track|session|workshop|"
    r"preface|editorial|abstract|keywords?)$", re.IGNORECASE)


def parse_args():
    ap = argparse.ArgumentParser(description="实体归一化（保守版）")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--rollback", default="")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--type-dominance", type=float, default=0.80,
                    help="L2 单复数组内，主导类型票数占比低于此值才判为真类型冲突而跳过")
    return ap.parse_args()


ARGS = parse_args()


def flat(n: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (n or "").lower())


def canonical_key(n: str) -> str:
    """R1+R3：大小写 + 标点空白归一，用于分组。"""
    s = unicodedata.normalize("NFKC", n or "").lower()
    s = re.sub(r"[\u2018\u2019\u02bc\u2032`]", "'", s)
    s = re.sub(r"[\u2010-\u2015\u2212]", "-", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s.strip(" \t\n.,;:!?\"'()[]{}<>|/\\")


def plural_key(n: str) -> str:
    k = canonical_key(n)
    if k.endswith("s") and not k.endswith("ss") and len(k) > 4:
        return k[:-1]
    return k


def load_corpus():
    freq, types = Counter(), defaultdict(Counter)
    for f in sorted(GRAPHS.glob("*.json")):
        try:
            snap = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        for n in snap.get("nodes", []):
            nm = (n.get("name") or "").strip()
            if nm:
                freq[nm] += 1
                types[nm][n.get("type") or "?"] += 1
    return freq, types


def extract_abbreviations():
    """从 PDF 抽 `Full (ABBR)` / `ABBR (Full)`，返回 Counter[(full, abbr)]。"""
    import fitz
    pats = [
        re.compile(r"([A-Z][A-Za-z][A-Za-z\- ]{6,60}?)\s*\(([A-Z]{2,8})s?\)"),
        re.compile(r"\b([A-Z]{2,8})s?\s*\(([A-Z][A-Za-z][A-Za-z\- ]{6,60}?)\)"),
    ]
    pairs = Counter()
    for p in sorted(CORPUS.glob("*.pdf")):
        try:
            doc = fitz.open(p)
            t = "\n".join(pg.get_text() for pg in doc)
            doc.close()
        except Exception:
            continue
        t = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", t)
        t = re.sub(r"\s+", " ", t)
        for i, pat in enumerate(pats):
            for m in pat.finditer(t):
                full, abbr = ((m.group(1), m.group(2)) if i == 0
                              else (m.group(2), m.group(1)))
                full = re.sub(r"^(the|a|an|in)\s+", "", full.strip(), flags=re.I).strip()
                abbr = abbr.strip()
                if 2 <= len(abbr) <= 8 and len(full) >= 6 and abbr.isupper():
                    pairs[(full, abbr)] += 1
    return pairs


def is_sensitive(members):
    """组内是否存在"大小写变体但语义不同"的风险。

    F3：组内出现已确认的同名不同实体对（如 `Complex` / `ComplEx`）。
    F3b：组内卷入已知模型名，且同时存在它的原始大小写与全小写通用词
         —— 这时无法安全判别，宁可不动。

    **L1 与 L2 必须共用这一条**：初版只有 L1 调它，L2 没有，
    于是 `ComplEx` / `Complex` / `ComplEX` 在前者被拦住、在后者被合并，
    把 T4 保守策略的收益吃掉了。同一规则写两遍就会这样分裂。
    """
    lows = sorted({m.lower() for m in members})
    if any(set(pair) <= set(lows) for pair in F3_SKIP_LOWER):
        return True
    if any(l in KNOWN_MODEL_NAMES for l in lows):
        if any(l == l.lower() and l in KNOWN_MODEL_NAMES for l in lows):
            others = [l for l in lows if l not in KNOWN_MODEL_NAMES]
            if others:
                return True
    return False


def build_alias_map(freq, types):
    """产出 {alias_name: canonical_name} 与审计记录。"""
    alias = {}
    audit = []

    # ── L1+L3：大小写/标点归一 ──
    groups = defaultdict(list)
    for nm in freq:
        k = canonical_key(nm)
        if k:
            groups[k].append(nm)
    l1_groups = 0
    for k, members in groups.items():
        if len(members) < 2:
            continue
        # F2 短名保护
        if any(len(flat(m)) <= 3 for m in members):
            audit.append({"layer": "L1", "action": "skip-short", "key": k,
                          "members": members})
            continue
        # F3 语义敏感：组内出现已确认"大小写变体但语义不同"的对 ⇒ 跳过
        if is_sensitive(members):
            audit.append({"layer": "L1", "action": "skip-sensitive", "key": k,
                          "members": members})
            continue
        canon = max(members, key=lambda m: (freq[m], len(m)))
        for m in members:
            if m != canon:
                alias[m] = canon
        l1_groups += 1
        audit.append({"layer": "L1", "action": "merge", "canonical": canon,
                      "aliases": [m for m in members if m != canon]})

    # ── L2：安全复数（仅在不与已有合并冲突时应用）──
    l2_groups = 0
    pg = defaultdict(list)
    for nm in freq:
        if nm in alias:
            continue
        pg[plural_key(nm)].append(nm)
    for k, members in pg.items():
        if len(members) < 2:
            continue
        if any(len(flat(m)) <= 3 for m in members):
            audit.append({"layer": "L2", "action": "skip-short", "key": k,
                          "members": members})
            continue
        # 与 L1 共用同一条敏感词保护（初版 L2 漏了，见 is_sensitive 的注释）
        if is_sensitive(members):
            audit.append({"layer": "L2", "action": "skip-sensitive", "key": k,
                          "members": members})
            continue
        # 类型判据改为**频次加权**（2026-09-12），并区分两类近名。
        #
        # 旧判据取每个成员**各自**的最高频类型，只要不同就跳过：
        #     tsets = [types[m].most_common(1)[0][0] for m in members]
        #     if len(set(tsets)) > 1: skip
        # 问题是成员的票数可能只有 1：`Link Prediction`=Method×12 与
        # `Link Predictions`=Concept×1 —— 后者只出现过一次，它的类型标注本身
        # 就是噪声，不足以说明这是两个不同实体。196 个跳过组里有 183 组是
        # 简单单复数对、171 组含"只出现 1 次"的低频变体。
        #
        # 但**不能只是放宽阈值**：实测把阈值降到 0.5 能合并 181 组，代价是把
        # `Complex`（数学概念）并进了 `ComplEx`（知识图谱嵌入模型）——这两个词
        # 经 flat() 归一后字面完全相同，任何字面判据都分不开，只有类型能分。
        # 而"正确拒绝 Complex/ComplEx"正是 T4 保守策略的收益之一。
        #
        # 所以按形态分两类处理：
        #   ① **纯单复数**（`X` → `Xs`/`Xes`/`Xies`）：词干完全一致，
        #      几乎必然是同一实体，**无条件合并**，类型差异视为噪声。
        #   ② **其他近名**（词干有变化，或归一后字面相同如 Complex/ComplEx）：
        #      改走**频次加权类型**——把组内所有成员的类型计数累加，看主导占比。
        #      占比高（Method 12 vs Concept 1 = 92%）⇒ 少数派是噪声，合并；
        #      占比低（各 50%）⇒ 真类型冲突，跳过。
        def pure_plural_group(members):
            """组内是否只差**规则复数词尾**（`X` → `Xs`/`Xes`/`Xies`）。

            基准必须用 `plural_key` 得到的**词干**，不能用"最长成员"：
            最长成员通常就是复数形式，于是 `singular in (plural + 's')` 永远
            不成立，纯单复数组会被全部误判（初版就是这个错，198 组里判出 0 组）。

            同时这个写法天然排除了"lower 后字面完全相同"的情形：
            `ComplEx` / `ComplEX` / `Complex` 的 canonical 与词干都是 `complex`，
            没有任何成员等于 `complex + s`，因此 `saw_stem_change` 保持 False
            ⇒ 返回 False，交给类型判据处理。这正是需要的——`Complex`（数学概念）
            与 `ComplEx`（知识图谱嵌入模型）字面完全一样，只能靠类型区分。
            """
            stem = plural_key(members[0])
            saw_stem_change = False
            for m in members:
                c = canonical_key(m)
                if c == stem:
                    continue                       # 单数形式（词干本身）
                if c in (stem + 's', stem + 'es'):
                    saw_stem_change = True
                    continue
                if stem.endswith('y') and c == stem[:-1] + 'ies':
                    saw_stem_change = True
                    continue
                return False
            return saw_stem_change

        if pure_plural_group(members):
            canon = max(members, key=lambda m: (freq[m], len(m)))
            for m in members:
                if m != canon and m not in alias:
                    alias[m] = canon
            l2_groups += 1
            audit.append({"layer": "L2", "action": "merge",
                          "canonical": canon,
                          "aliases": [m for m in members if m != canon],
                          "rule": "pure-plural"})
            continue

        weighted = Counter()
        for m in members:
            weighted.update(types[m])
        total_votes = sum(weighted.values()) or 1
        dom_type, dom_votes = weighted.most_common(1)[0]
        dom_share = dom_votes / total_votes
        if dom_share < ARGS.type_dominance:
            audit.append({"layer": "L2", "action": "skip-type-conflict", "key": k,
                          "members": members,
                          "weighted_types": dict(weighted),
                          "dominant": dom_type,
                          "dominant_share": round(dom_share, 3)})
            continue
        canon = max(members, key=lambda m: (freq[m], len(m)))
        for m in members:
            if m != canon and m not in alias:
                alias[m] = canon
        l2_groups += 1
        audit.append({"layer": "L2", "action": "merge", "canonical": canon,
                      "aliases": [m for m in members if m != canon]})

    # ── L4：缩写白名单 ──
    pairs = extract_abbreviations()
    low2name = {}
    for nm in freq:
        low2name.setdefault(nm.lower(), nm)
    l4_pairs = 0
    for (full, abbr), c in pairs.most_common():
        if STOPWORD_FULL.match(full):
            continue
        nf = low2name.get(full.lower())
        na = low2name.get(abbr.lower())
        if not nf or not na or nf == na:
            continue
        # 断言：全称与缩写不能只是大小写变体（那属 L1）
        if canonical_key(nf) == canonical_key(na):
            continue
        if len(flat(na)) <= 1:
            continue
        # 取全称作 canonical（更具可读性）
        canon, al = (nf, na) if freq[nf] >= freq[na] else (na, nf)
        if al in alias:
            continue
        alias[al] = canon
        l4_pairs += 1
        audit.append({"layer": "L4", "action": "merge", "canonical": canon,
                      "aliases": [al], "evidence_count": c, "full": full, "abbr": abbr})

    return alias, audit, {"l1_groups": l1_groups, "l2_groups": l2_groups,
                          "l4_pairs": l4_pairs, "abbr_extracted": len(pairs)}


def apply_alias(alias):
    """把 alias 应用回快照：改节点名 + 改边端点。"""
    stats = Counter()
    changed_files = []
    for f in sorted(GRAPHS.glob("*.json")):
        snap = json.loads(f.read_text(encoding="utf-8"))
        touched = False
        for n in snap.get("nodes", []):
            nm = (n.get("name") or "").strip()
            if nm in alias:
                n["name"] = alias[nm]
                n["_merged_from"] = nm
                stats["nodes"] += 1
                touched = True
        for e in snap.get("edges", []):
            for side in ("source", "target"):
                v = (e.get(side) or "").strip()
                if v in alias:
                    e[side] = alias[v]
                    stats[f"edge_{side}"] += 1
                    touched = True
        if touched:
            f.write_text(json.dumps(snap, ensure_ascii=False), encoding="utf-8")
            changed_files.append(f.name)
    return stats, changed_files


def main():
    if ARGS.rollback:
        src = Path(ARGS.rollback)
        files = list(src.glob("*.json"))
        for b in files:
            shutil.copy2(b, GRAPHS / b.name)
        print(f"已回滚 {len(files)} 个文件")
        return

    print("=== 实体归一化（保守版）" + ("｜APPLY" if ARGS.apply else "｜dry-run") + " ===\n")
    freq, types = load_corpus()
    print(f"唯一实体名 {len(freq)}")

    alias, audit, summary = build_alias_map(freq, types)
    print(f"\n── 归一化规模 ──")
    print(f"   L1 大小写/标点合并组 {summary['l1_groups']}")
    print(f"   L2 安全复数合并组   {summary['l2_groups']}")
    print(f"   L4 缩写对采用       {summary['l4_pairs']}（原文抽到 {summary['abbr_extracted']} 对）")
    print(f"   合计 alias 条目     {len(alias)}")

    by_layer = defaultdict(list)
    for a in audit:
        by_layer[(a.get("layer"), a.get("action"))].append(a)
    print(f"\n── 审计（按层×动作）──")
    for (layer, action), rows in sorted(by_layer.items()):
        print(f"   {layer} {action:20s} {len(rows)}")

    print(f"\n── 样本：实际合并 ──")
    merged = [a for a in audit if a.get("action") == "merge"]
    for a in merged[:ARGS.top]:
        print(f"   [{a['layer']}] {a.get('canonical')}  ←  {a.get('aliases')}")

    print(f"\n── 样本：被拦截（安全过滤生效）──")
    skipped = [a for a in audit if a.get("action", "").startswith("skip")]
    for a in skipped[:min(15, len(skipped))]:
        print(f"   [{a['layer']}] {a['action']}: {a.get('key')} → {a.get('members', [])[:4]}")

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "entity-alias-map.json").write_text(json.dumps({
        "alias": alias, "summary": summary,
        "audit": audit[:3000],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n别名表已写出: {OUT / 'entity-alias-map.json'}")

    if not ARGS.apply:
        print("\n（dry-run，未改数据。加 --apply 写入）")
        return

    bk = BACKUP / time.strftime("normalize-%Y%m%d-%H%M%S")
    bk.mkdir(parents=True, exist_ok=True)
    for f in GRAPHS.glob("*.json"):
        shutil.copy2(f, bk / f.name)
    stats, changed = apply_alias(alias)
    print(f"\n── 已写入 ──")
    for k, v in stats.items():
        print(f"   {k:16s} {v}")
    print(f"   改动文件 {len(changed)} 个")
    print(f"   备份: {bk}")
    print(f"   回滚: python tools/entity_normalize.py --rollback \"{bk}\"")


if __name__ == "__main__":
    main()
