# -*- coding: utf-8 -*-
"""L5 域内效度 · 第二判定者（本地 Ollama，完全离线，零云端支出）。

【这个脚本回答什么、不回答什么】
  回答：**把判定器换掉，结论会变多少？**（判定器敏感度）
        以及 **两个独立模型在哪里分歧？**（分歧挖掘，用来挑出最该送人工看的边）
  不回答：真实准确率。两个模型一致 ≠ 正确 —— 它们可能共享同一种偏置
        （比如都倾向「前提里出现这两个词就算支持」）。所以本脚本的输出
        只能当作**红旗/上界**，真值仍须人工盲判（见 l5_validity_review.py）。

【为什么它不违反「零云端 API 支出」】
  用的是本机 `~/.ollama` 里**已经落盘的**模型，推理在 127.0.0.1 上完成，
  不需要外网。已核实的本机可用判定者（2026-09-12 文件核对）：

    qwen3.5:9b                          6.59 GB  ← 通用 LLM，与 NLI 判别器架构完全独立
    qwen3-vl:4b                         3.30 GB
    yemifo/qwen25-vl-3b-instruct4k      1.93 GB
    bge-m3                              1.16 GB  ← 可做 embedding 层证据相似度
    nomic-embed-text                    0.27 GB
    cross-encoder/nli-deberta-v3-base   0.75 GB  ← 第二个 NLI（modelscope 缓存）
    Babelscape/...triplet-critic-xnli   1.12 GB  ← 现行判别器

  ⚠ 这与任务书里「本地可用的模型只有已缓存的 triplet critic」不符，以文件为准。

用法：
  ollama serve                                  # 另开一个终端（离线即可，不需外网）
  python tools/l5_validity_second_judge.py --n 200
  python tools/l5_validity_second_judge.py --n 200 --model qwen3.5:9b --report
"""
# 路径以本文件为锚点解析，不写死盘符——演示机不一定把仓库放在 D 盘。
from pathlib import Path as _Path
_HERE = _Path(__file__).resolve()
ROOT = _HERE.parent.parent          # tools/*.py 与 src/*.py 都在根目录下一层
import argparse
import json
import random
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

Q = ROOT / "data/quality"
VERIFY = Q / "relation-verify-all.json"
OUT = Q / "l5-second-judge.json"
HOST = "http://127.0.0.1:11434"

TYPE_ZH = {
    "PART_OF": "是……的一部分", "USES": "使用", "PRODUCES": "产出",
    "MEASURES": "度量", "EVALUATES": "评估", "COMPARES_WITH": "与……比较",
    "SUPPORTS": "支持", "CONTRADICTS": "与……矛盾", "CAUSES": "导致",
    "ENABLES": "使能", "APPLIES_TO": "应用于", "LOCATED_AT": "位于",
    "CREATED_BY": "由……创作", "CITES": "引用", "RELATED_TO": "与……相关",
}

# 判定提示词：只问「原文是否明确支持这条断言」，并强制先给理由再给结论，
# 避免模型直接顺着「看起来合理」给 yes。
PROMPT = """你是严格的关系抽取校验员。只根据给定的【原文】判断，不许使用外部知识。

【原文】
{premise}

【待判断言】
{hypothesis}
（关系类型：{type}，含义：{type_zh}）

判定规则：
- 只有当【原文】**明确陈述**了这条断言时才答 YES。
- 只要断言里的两个实体有一个没有在【原文】里出现，答 NO。
- 如果【原文】只提到两者相关、但没有陈述该具体关系，答 NO。
- 注意方向：主宾语颠倒要答 NO。

先写一行「理由：...」（不超过 40 字），再写一行「结论：YES」或「结论：NO」。"""


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--model", default="qwen3.5:9b")
    ap.add_argument("--host", default=HOST)
    ap.add_argument("--sample-from", choices=("all", "pass", "near"), default="all",
                    help="all=全图随机；pass=只抽判别器判通过的（验精度）；"
                         "near=只抽 0.5–0.8 之间的（分歧最密集区）")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--report", action="store_true", help="只读已落盘结果出报告")
    return ap.parse_args()


def ollama_ok(host):
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=3) as r:
            return json.load(r)
    except Exception:
        return None


def ask(host, model, prompt, timeout):
    body = json.dumps({"model": model, "prompt": prompt, "stream": False,
                       "options": {"temperature": 0, "num_predict": 120}}).encode()
    req = urllib.request.Request(f"{host}/api/generate", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r).get("response", "")


def verdict_of(text):
    m = re.findall(r"结论\s*[:：]\s*(YES|NO|是|否)", text or "", re.I)
    if m:
        v = m[-1].upper()
        return "support" if v in ("YES", "是") else "reject"
    if re.search(r"\bYES\b", (text or "").upper()):
        return "support"
    if re.search(r"\bNO\b", (text or "").upper()):
        return "reject"
    return "unclear"


def kappa(pairs):
    n = len(pairs)
    if not n:
        return None
    po = sum(1 for a, b in pairs if a == b) / n
    pa = sum(1 for a, _ in pairs if a) / n
    pb = sum(1 for _, b in pairs if b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return None if pe >= 1 else round((po - pe) / (1 - pe), 4)


def report(A):
    if not OUT.exists():
        raise SystemExit(f"还没有 {OUT}。先跑一次：\n"
                         f"  ollama serve\n"
                         f"  python tools/l5_validity_second_judge.py --n 200")
    d = json.loads(OUT.read_text(encoding="utf-8"))
    rows = d["items"]
    agree = sum(1 for r in rows if r["judge2"] == r["judge1"])
    pairs = [(r["judge2"] == "support", bool(r["pass"])) for r in rows]
    dis = [r for r in rows if (r["judge2"] == "support") != bool(r["pass"])]
    print(f"=== 第二判定者 vs 现行判别器（model={d['model']}, n={len(rows)}）===")
    print(f"  一致率 {agree/len(rows)*100:.1f}%   Cohen's κ = {kappa(pairs)}")
    print(f"  判别器 pass 率 {sum(1 for r in rows if r['pass'])/len(rows)*100:.1f}%"
          f"   第二判定者 support 率 "
          f"{sum(1 for r in rows if r['judge2']=='support')/len(rows)*100:.1f}%")
    print(f"  分歧 {len(dis)} 条（其中判别器通过、二判否定 "
          f"{sum(1 for r in dis if r['pass'])} 条 ← 最可疑，优先送人工）")
    if dis:
        print("\n  分歧清单（按 entail_prob 降序，前 15 条）:")
        for r in sorted(dis, key=lambda x: -x["entail_prob"])[:15]:
            print(f"   P={r['entail_prob']:.3f} hit={r['hit']:5s} 2判={r['judge2']:7s} "
                  f"[{r['type']}] {r['source'][:28]} -> {r['target'][:28]}")
    print("\n  读法：κ 低 ⇒ 这个「63.7%」高度依赖选了哪个判定器，"
          "不可当质量结论；κ 高也不能证明判得对（两者可能同偏置）。")


def main():
    A = parse_args()
    if A.report:
        return report(A)
    tags = ollama_ok(A.host)
    if tags is None:
        raise SystemExit(
            f"连不上 Ollama（{A.host}）。\n"
            f"  模型文件已在磁盘上（qwen3.5:9b 6.59 GB，见 ~/.ollama/models/blobs），"
            f"只是服务没起。\n"
            f"  离线启动（不需要外网）：另开一个终端执行  ollama serve\n"
            f"  确认可用：ollama list  →  应看到 qwen3.5:9b")
    names = [m.get("name") for m in (tags.get("models") or [])]
    print(f"Ollama 可用，已有模型：{names}")
    if A.model not in names:
        raise SystemExit(f"模型 {A.model} 不在列表里；用 --model 换一个：{names}")

    edges = json.loads(VERIFY.read_text(encoding="utf-8"))["edges"]
    pool = edges
    if A.sample_from == "pass":
        pool = [e for e in edges if e["pass"]]
    elif A.sample_from == "near":
        pool = [e for e in edges if 0.5 <= e["entail_prob"] <= 0.8]
    rnd = random.Random(A.seed)
    sample = rnd.sample(pool, min(A.n, len(pool)))
    print(f"从 {len(pool)} 条中抽 {len(sample)} 条（sample-from={A.sample_from}, seed={A.seed}）")

    items = []
    t0 = time.time()
    for i, e in enumerate(sample, 1):
        prompt = PROMPT.format(premise=e["premise"], hypothesis=e["hypothesis"],
                               type=e["type"], type_zh=TYPE_ZH.get(e["type"], "（本体外类型）"))
        try:
            raw = ask(A.host, A.model, prompt, A.timeout)
        except (urllib.error.URLError, TimeoutError) as ex:
            raw = f"（调用失败：{ex}）"
        v = verdict_of(raw)
        items.append({"paper": e["paper"], "source": e["source"], "target": e["target"],
                      "type": e["type"], "premise": e["premise"],
                      "hypothesis": e["hypothesis"], "hit": e["hit"],
                      "entail_prob": e["entail_prob"], "pass": bool(e["pass"]),
                      "judge1": "support" if e["pass"] else "reject",
                      "judge2": v, "raw": raw.strip()[:400]})
        if i % 10 == 0 or i == len(sample):
            print(f"  {i}/{len(sample)}  {time.time()-t0:.0f}s")
    OUT.write_text(json.dumps(
        {"model": A.model, "sample_from": A.sample_from, "seed": A.seed,
         "n": len(items), "note": "judge2 是第二模型判定，不是金标；只用于敏感度与分歧挖掘",
         "items": items}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写出 {OUT}\n再跑一次加 --report 看一致性："
          f"\n  python tools/l5_validity_second_judge.py --report")


if __name__ == "__main__":
    main()
