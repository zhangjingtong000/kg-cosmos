"""实体质量诊断：判定实体名是否在抽取阶段就坏了（本地零成本）。

【为什么必须先诊断】
  CREATED_BY 方向修复的干跑暴露：反转后得到 `Bollacker -> Freebase`、
  `Suchanek -> YAGO`，但这些都是**残缺的实体名**（应为 Kurt Bollacker）。
  这说明实体层的问题比关系层更深 —— 若实体名本身是断裂的子串，
  则"实体归一化"用规则救不回来，必须回到抽取层。

【四项可判定的诊断（全部用 PDF 原文验证，不靠主观）】
  D1 截断（truncation）
      判定：实体名每次在原文出现时，其左右边界都紧邻字母/数字
            （即它是某个更长词的真子串，从未独立成词）
      例：`Convolutional 2D Knowledge Graph Embeddi` ← "Embedding" 被截
  D2 粘连（gluing）
      判定：名字内含"无空格拼接"痕迹（如 `realworldobjectsrepresented...`）
      例：`realworldobjectsrepresentedinknowledgegrap`
  D3 无区分度短名
      判定：长度 ≤ 3 或（长度 ≤ 6 且不含空格且是常见词）
      例：`B`、`C`、`Wang`、`Chen`
  D4 疑似幻觉
      判定：在原文中**完全找不到**（任何归一化形式都找不到）

数据源：data/corpus/*.pdf（PyMuPDF 抽取全文）
输出：data/quality/entity-quality.json + 控制台报告

用法：
  python tools/diagnose_entities.py
  python tools/diagnose_entities.py --min-freq 2   # 只看出现≥2次的实体
"""
# 路径以本文件为锚点解析，不写死盘符——演示机不一定把仓库放在 D 盘。
from pathlib import Path as _Path
_HERE = _Path(__file__).resolve()
ROOT = _HERE.parent.parent          # tools/*.py 与 src/*.py 都在根目录下一层
import argparse
import json
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

CORPUS = ROOT / "data/corpus"
GRAPHS = CORPUS / "graphs"
OUT = ROOT / "data/quality"


def parse_args():
    ap = argparse.ArgumentParser(description="实体质量诊断")
    ap.add_argument("--min-freq", type=int, default=1, help="只诊断出现≥N次的实体")
    ap.add_argument("--top", type=int, default=25, help="每类打印样本数")
    ap.add_argument("--workers", type=int, default=0, help="预留；PDF 解析串行")
    return ap.parse_args()


ARGS = parse_args()


def norm_spaces(s: str) -> str:
    """保留词边界的宽松归一：小写 + 折叠空白 + 去首尾标点。"""
    s = (s or "").lower()
    s = re.sub(r"[\u2010-\u2015\u2212]", "-", s)   # 各种连字符归一
    s = re.sub(r"\s+", " ", s).strip()
    return s.strip(" \t\n.,;:!?\"'()[]{}<>|/\\")


def strip_punct(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def extract_pdf_text() -> dict:
    """每篇 PDF 抽全文，返回 {paper_stem: {"text":..,"low":..}}。"""
    import fitz
    cache = {}
    pdfs = sorted(CORPUS.glob("*.pdf"))
    print(f"PDF 文件 {len(pdfs)} 个，开始抽取全文...")
    t0 = time.time()
    for p in pdfs:
        try:
            doc = fitz.open(p)
            parts = []
            for page in doc:
                parts.append(page.get_text())
            doc.close()
            text = "\n".join(parts)
            # 连字符断行修复（PDF 常见：`embed-\nding` → `embedding`）
            text = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1\2", text)
            cache[p.stem] = {"text": text, "low": text.lower()}
        except Exception as e:
            print(f"  [skip] {p.name}: {type(e).__name__}: {str(e)[:60]}")
    print(f"  完成 {len(cache)} 篇，{time.time()-t0:.1f}s\n")
    return cache


def variants(name: str) -> list:
    """生成用于原文查找的等价变体，消除编码差异造成的假阳性。

    实测假阳性来源：`Google's Knowledge Graph` 里的 `'`(U+2019) 与原文
    的 ASCII `'` 不匹配，被误判为"原文中找不到"。同样的问题也出现在
    连字符、不间断空格、重音符号上。
    """
    base = norm_spaces(name)
    if not base:
        return []
    outs = {base}
    # 撇号族 → 统一成 '
    outs.add(re.sub(r"[\u2018\u2019\u02bc\u2032`]", "'", base))
    # 连字符族 → 统一成 -
    outs.add(re.sub(r"[\u2010-\u2015\u2212]", "-", base))
    # 去撇号
    outs.add(re.sub(r"['\u2018\u2019]", "", base))
    # 去重音（NFKD 分解后剔除组合符号）
    import unicodedata
    outs.add(unicodedata.normalize("NFKD", base)
             .encode("ascii", "ignore").decode("ascii"))
    return [o for o in outs if o]


def classify_name(name: str, low_text: str) -> dict:
    """对单个实体名做判定。

    类别互斥，优先级：hallucinated > truncated > glued > short > clean
      · hallucinated：所有编码变体在原文中都找不到
      · truncated   ：能找到，但**每次**都以子串形式出现（从未独立成词）
      · glued       ：无空格长串
      · short       ：长度≤3，或长度≤6且无空格
    """
    key = norm_spaces(name)
    flat = strip_punct(name)
    res = {"name": name, "truncated": False, "glued": False, "short": False,
           "hallucinated": False, "occurrences": 0, "standalone": 0, "category": "clean"}

    if not flat:
        res["hallucinated"] = True
        res["category"] = "hallucinated"
        return res

    is_short = len(flat) <= 3 or (len(flat) <= 6 and " " not in key)
    letters = re.sub(r"[^a-z]", "", key)
    is_glued = len(letters) >= 18 and " " not in key and "-" not in key

    # 多编码变体查找：任一变体找到即可
    occ = standalone = 0
    first_bad = None
    if low_text:
        for v in variants(name):
            pat = re.compile(re.escape(v))
            for m in pat.finditer(low_text):
                occ += 1
                b = low_text[m.start() - 1] if m.start() > 0 else " "
                a = low_text[m.end()] if m.end() < len(low_text) else " "
                if not (b.isalnum() or a.isalnum()):
                    standalone += 1
                elif first_bad is None:
                    first_bad = low_text[max(0, m.start() - 25):m.end() + 25].replace("\n", " ")
            if occ:
                break   # 找到就停，避免重复计数

    res["occurrences"] = occ
    res["standalone"] = standalone

    if occ == 0:
        res["hallucinated"] = True
        res["category"] = "hallucinated"
    elif standalone == 0:
        res["truncated"] = True
        res["example_context"] = first_bad
        res["category"] = "truncated"
    elif is_glued:
        res["glued"] = True
        res["category"] = "glued"
    elif is_short:
        res["short"] = True
        res["category"] = "short"
    return res


def main():
    # 收集实体：名字 → 出现次数、类型
    freq = Counter()
    types = defaultdict(Counter)
    for f in sorted(GRAPHS.glob("*.json")):
        try:
            snap = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        for n in snap.get("nodes", []):
            nm = n.get("name") or ""
            if not nm.strip():
                continue
            k = norm_spaces(nm)
            freq[nm] += 1
            types[nm][n.get("type") or "?"] += 1

    targets = [nm for nm, c in freq.most_common() if c >= ARGS.min_freq]
    print(f"=== 实体质量诊断 ===")
    print(f"唯一实体名 {len(freq)}，本次诊断 {len(targets)}（频次≥{ARGS.min_freq}）\n")

    cache = extract_pdf_text()
    all_low = "\n".join(v["low"] for v in cache.values())
    print(f"原文总长度 {len(all_low)} 字符\n")

    t0 = time.time()
    results = []
    for i, nm in enumerate(targets):
        r = classify_name(nm, all_low)
        r["freq"] = freq[nm]
        r["type"] = types[nm].most_common(1)[0][0]
        results.append(r)
        if i % 2000 == 0 and i:
            print(f"  已诊断 {i}/{len(targets)}  {time.time()-t0:.0f}s")
    print(f"诊断完成 {len(results)} 个，{time.time()-t0:.1f}s\n")

    # ── 汇总（互斥分类）──
    n = len(results)
    cats = Counter(r["category"] for r in results)
    order = ("clean", "short", "hallucinated", "truncated", "glued")

    print("── 总体（按唯一实体名计，互斥分类）──")
    for k in order:
        c = cats.get(k, 0)
        print(f"   {k:14s} {c:6d}  {c/n*100:5.1f}%")

    # 按频次加权（对图谱影响）
    tot_freq = sum(r["freq"] for r in results)
    wcats = Counter()
    for r in results:
        wcats[r["category"]] += r["freq"]
    print(f"\n── 按出现频次加权（总频次 {tot_freq}，反映对图谱的实际影响）──")
    for k in order:
        c = wcats.get(k, 0)
        print(f"   {k:14s} {c:6d}  {c/tot_freq*100:5.1f}%")
    flags, wflags = cats, wcats

    for k in ("truncated", "glued", "hallucinated", "short"):
        rows = [r for r in results if r[k]]
        rows.sort(key=lambda x: -x["freq"])
        if not rows:
            continue
        print(f"\n── {k} 样本（按频次降序前 {min(ARGS.top, len(rows))}）──")
        for r in rows[:ARGS.top]:
            extra = f"  ctx={r.get('example_context','')[:60]}" if r.get("example_context") else ""
            print(f"   ×{r['freq']:<4} [{r['type']:11s}] {r['name'][:58]:58s}{extra}")

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "entity-quality.json").write_text(json.dumps({
        "unique_names": len(freq), "diagnosed": n,
        "flags_by_name": dict(flags), "flags_by_freq": dict(wflags),
        "total_freq": tot_freq,
        "top_issues": {k: [{"name": r["name"], "freq": r["freq"], "type": r["type"],
                            "context": r.get("example_context", "")}
                           for r in sorted([x for x in results if x[k]],
                                           key=lambda x: -x["freq"])[:100]]
                       for k in ("truncated", "glued", "hallucinated", "short")},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告已写出: {OUT / 'entity-quality.json'}")


if __name__ == "__main__":
    main()
