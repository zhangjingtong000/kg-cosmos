"""实体归一化风险评估：每种归一化规则会产生多少冲突（本地零成本，只读）。

【为什么要先做这个】
  归一化不可避免要合并节点，而**合并不可逆**。在写任何数据之前，
  必须知道每条规则的冲突规模与性质。本脚本对 4 类规则分别量化：

  R1 大小写归一      `Knowledge Graph` / `knowledge graph`
  R2 复数归一        `Graph` / `Graphs`
  R3 标点/空白归一   `BERT` / `BERT.` / 双空格
  R4 缩写↔全称       `KG` / `Knowledge Graph`（用 PDF 原文抽 (abbr) 模式）

  对每条规则报告：
    - 会合并多少组、涉及多少实体名
    - **危险合并**（合并后类型冲突，或名字差异大到可疑）
    - 样例，供人工判断

  判定"危险"的两条：
    a) 类型冲突：合并组内出现不同类型（如 `Apple`(Organization) vs `apple`(Material)）
    b) 短名卷入：合并涉及长度 ≤3 的名字（缩写歧义高）

用法：
  python tools/plan_entity_normalization.py
  python tools/plan_entity_normalization.py --top 25
"""
import argparse
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

CORPUS = Path(r"D:\kg-demo\data\corpus")
GRAPHS = CORPUS / "graphs"
OUT = Path(r"D:\kg-demo\data\quality")


def parse_args():
    ap = argparse.ArgumentParser(description="实体归一化风险评估")
    ap.add_argument("--top", type=int, default=20)
    return ap.parse_args()


ARGS = parse_args()


def load_entities():
    freq = Counter()
    types = defaultdict(Counter)
    for f in sorted(GRAPHS.glob("*.json")):
        try:
            snap = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        for n in snap.get("nodes", []):
            nm = (n.get("name") or "").strip()
            if not nm:
                continue
            freq[nm] += 1
            types[nm][n.get("type") or "?"] += 1
    return freq, {k: c.most_common(1)[0][0] for k, c in types.items()}


# ── 归一化规则 ──────────────────────────────────────────
def r1_case(n):
    return n.lower()


def r2_plural(n):
    """保守复数归一：仅处理规则复数，不动 -ies/-ves 等易错形态。"""
    low = n.lower().rstrip(".")
    if low.endswith("s") and not low.endswith("ss") and len(low) > 4:
        return low[:-1]
    return low


def r3_punct(n):
    s = unicodedata.normalize("NFKC", n).lower()
    s = re.sub(r"[\u2018\u2019\u02bc\u2032`]", "'", s)
    s = re.sub(r"[\u2010-\u2015\u2212]", "-", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s.strip(" \t\n.,;:!?\"'()[]{}<>|/\\")


RULES = [("R1 大小写", r1_case), ("R2 复数", r2_plural), ("R3 标点空白", r3_punct)]


def analyze(freq, ntype, keyfn):
    groups = defaultdict(list)
    for nm in freq:
        groups[keyfn(nm)].append(nm)
    multi = {k: v for k, v in groups.items() if len(v) > 1}
    n_pairs = sum(len(v) - 1 for v in multi.values())
    type_conflict = 0
    short_involved = 0
    details = []
    for k, members in multi.items():
        ts = {ntype.get(m, "?") for m in members}
        conflict = len(ts) > 1
        short = any(len(re.sub(r"[^a-z0-9]", "", m.lower())) <= 3 for m in members)
        if conflict:
            type_conflict += 1
        if short:
            short_involved += 1
        details.append({
            "key": k, "members": [(m, freq[m], ntype.get(m, "?")) for m in members],
            "type_conflict": conflict, "short_involved": short,
            "weight": sum(freq[m] for m in members),
        })
    details.sort(key=lambda d: -d["weight"])
    return {"groups": len(multi), "pairs": n_pairs, "type_conflict": type_conflict,
            "short_involved": short_involved, "details": details}


def extract_abbreviations():
    """从 PDF 原文抽 `Full Name (ABBR)` 与 `ABBR (Full Name)` 两种模式。"""
    import fitz
    pats = [
        re.compile(r"([A-Z][A-Za-z][A-Za-z\- ]{6,60}?)\s*\(([A-Z]{2,8})s?\)"),
        re.compile(r"\b([A-Z]{2,8})s?\s*\(([A-Z][A-Za-z][A-Za-z\- ]{6,60}?)\)"),
    ]
    pairs = Counter()
    for p in sorted(CORPUS.glob("*.pdf")):
        try:
            doc = fitz.open(p)
            text = "\n".join(pg.get_text() for pg in doc)
            doc.close()
        except Exception:
            continue
        text = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text)
        text = re.sub(r"\s+", " ", text)
        for i, pat in enumerate(pats):
            for m in pat.finditer(text):
                if i == 0:
                    full, abbr = m.group(1).strip(), m.group(2).strip()
                else:
                    abbr, full = m.group(1).strip(), m.group(2).strip()
                full = re.sub(r"^(the|a|an)\s+", "", full, flags=re.I).strip()
                if 2 <= len(abbr) <= 8 and len(full) >= 6:
                    pairs[(full, abbr)] += 1
    return pairs


def main():
    freq, ntype = load_entities()
    print(f"=== 实体归一化风险评估 ===\n唯一实体名 {len(freq)}\n")

    report = {"unique": len(freq), "rules": {}}
    for label, fn in RULES:
        r = analyze(freq, ntype, fn)
        report["rules"][label] = {"groups": r["groups"], "pairs": r["pairs"],
                                  "type_conflict": r["type_conflict"],
                                  "short_involved": r["short_involved"]}
        print(f"── {label} ──")
        print(f"   合并组 {r['groups']}   涉及名 {r['pairs']+r['groups']}"
              f"   类型冲突组 {r['type_conflict']}   涉短名组 {r['short_involved']}")
        for d in r["details"][:ARGS.top]:
            flag = ""
            if d["type_conflict"]:
                flag += " ⚠️类型冲突"
            if d["short_involved"]:
                flag += " ⚠️短名"
            mem = " | ".join(f"{m}×{f}({t})" for m, f, t in d["members"][:4])
            print(f"     [{d['weight']:>3}] {mem}{flag}")
        print()

    # R4 缩写
    print("── R4 缩写↔全称（PDF 原文抽取）──")
    pairs = extract_abbreviations()
    print(f"   原文抽到候选对 {len(pairs)} 组")
    names_low = {n.lower(): n for n in freq}
    usable, unusable = [], []
    for (full, abbr), c in pairs.most_common():
        has_full = full.lower() in names_low
        has_abbr = abbr.lower() in names_low
        if has_full and has_abbr and full.lower() != abbr.lower():
            usable.append((full, abbr, c, names_low[full.lower()], names_low[abbr.lower()]))
        else:
            unusable.append((full, abbr, c))
    print(f"   两端都存在于图谱中、可安全应用: {len(usable)} 组")
    for full, abbr, c, nf, na in usable[:ARGS.top]:
        print(f"     ×{c}  {nf}  <->  {na}")
    print(f"\n   其余 {len(unusable)} 组：至少一端不在图谱中（记录备查，不应用）")
    for full, abbr, c in unusable[:min(8, len(unusable))]:
        print(f"     ×{c}  {full} ({abbr})")

    report["abbreviations"] = {
        "extracted": len(pairs), "usable": len(usable),
        "usable_pairs": [{"full": f, "abbr": a, "count": c}
                         for f, a, c, _, _ in usable],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "entity-normalization-plan.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告已写出: {OUT / 'entity-normalization-plan.json'}")
    print("\n（本脚本只评估，不改数据）")


if __name__ == "__main__":
    main()
