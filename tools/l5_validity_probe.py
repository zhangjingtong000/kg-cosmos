# -*- coding: utf-8 -*-
"""L5 域内效度探针：**不需要任何人工标注**，判断 63.7% 这个数字有多可信。

【为什么要做这个】
  L5 的阈值 0.67 与 ROC-AUC 0.9851 是在官方英文校准集
  （`Triplet-Critic-Data/en_triplet_entailment.tsv`，11997 行）上定的。
  实测该校准集与本项目数据在**两个维度**上分布不同：

    维度            官方校准集         本项目            差异
    premise 字符    中位 485，多句     中位 142，单句    我们的前提被自己抽的句子切短了
    hypothesis 形式 名词短语           完整句子模板      形式不同
                    "CBS Corporation   "Knowledge Graphs
                     subsidiary The CW" is related to KG Completion."

  ⇒ 「校准时必须成立的前提」与「运行时喂进去的前提」不是同一种东西。
     这个脚本用**同一批边**做消融：语义等价的不同表述、不同前提构造，
     看通过率会不会变。若会变，则 63.7% 是**表述口径的产物**，不是质量度量。

【三类探针，全部无需人工标注】
  P1 假设形式消融  同前提、同语义、换 hypothesis 写法（去句点 / 名词短语 / 中文 / 同义动词）
  P2 前提形式消融  同假设、换 premise 构造（我们抽的单句 vs 校准同款的整段截断）
  P3 方向反转      同前提、主宾语对调（复用 §4.5 的思路，此处重测当前数据）

【这个脚本能证明什么、不能证明什么】
  能：证明该指标对**表述口径**的敏感度（口径稳健性 / construct validity 的一部分）。
      若 5 种等价表述的通过率极差 >10pp，则任何把 63.7% 当作「关系正确率」的陈述都不成立。
  不能：给出准确率。形式不变 ≠ 判得对。真正的效度仍需人工盲判（见
      `tools/l5_validity_sample.py` + `l5_validity_review.py`）。

用法：
  python tools/l5_validity_probe.py                 # 默认 500 条抽样
  python tools/l5_validity_probe.py --n 1000
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
from pathlib import Path

TRIPLET = next(
    Path.home().joinpath(".cache/huggingface/hub").glob(
        "models--Babelscape--mdeberta-v3-base-triplet-critic-xnli/snapshots/*"),
    None)
VERIFY = ROOT / "data/quality/relation-verify-all.json"
OUT = ROOT / "data/quality/l5-validity-probe.json"
THR = 0.67

# 每种关系给出 5 种**语义等价**的 hypothesis 写法（互不相同）。
#   current = 现行模板（`relation_verifier.REL_TEMPLATE`，标定阈值时用的那一条）
#   nodot   = 去掉句末句点（贴近官方校准集的「名词短语」形状）
#   passive = 被动式（动词类关系用；无被动式的用名词短语）
#   zh      = 中文模板（模型自称多语言，实测中文是否一致要单独看）
#   syn     = 同义动词替换
FORMS = {
    "RELATED_TO": {
        "current": "{s} is related to {t}.",
        "nodot": "{s} is related to {t}",
        "passive": "{s} related to {t}",
        "zh": "{s}与{t}相关。",
        "syn": "{s} is associated with {t}.",
    },
    "USES": {
        "current": "{s} uses {t}.",
        "nodot": "{s} uses {t}",
        "passive": "{t} is used by {s}.",
        "zh": "{s}使用{t}。",
        "syn": "{s} utilizes {t}.",
    },
    "PART_OF": {
        "current": "{s} is part of {t}.",
        "nodot": "{s} is part of {t}",
        "passive": "{s} part of {t}",
        "zh": "{s}是{t}的一部分。",
        "syn": "{s} belongs to {t}.",
    },
    "PRODUCES": {
        "current": "{s} produces {t}.",
        "nodot": "{s} produces {t}",
        "passive": "{t} is produced by {s}.",
        "zh": "{s}产生{t}。",
        "syn": "{s} generates {t}.",
    },
    "MEASURES": {
        "current": "{s} measures {t}.",
        "nodot": "{s} measures {t}",
        "passive": "{t} is measured by {s}.",
        "zh": "{s}度量{t}。",
        "syn": "{s} quantifies {t}.",
    },
    "EVALUATES": {
        "current": "{s} evaluates {t}.",
        "nodot": "{s} evaluates {t}",
        "passive": "{t} is evaluated by {s}.",
        "zh": "{s}评估{t}。",
        "syn": "{s} assesses {t}.",
    },
    "COMPARES_WITH": {
        "current": "{s} is compared with {t}.",
        "nodot": "{s} is compared with {t}",
        "passive": "{s} compared with {t}",
        "zh": "{s}与{t}相比较。",
        "syn": "{s} is contrasted with {t}.",
    },
    "SUPPORTS": {
        "current": "{s} supports {t}.",
        "nodot": "{s} supports {t}",
        "passive": "{t} is supported by {s}.",
        "zh": "{s}支持{t}。",
        "syn": "{s} facilitates {t}.",
    },
    "CONTRADICTS": {
        "current": "{s} contradicts {t}.",
        "nodot": "{s} contradicts {t}",
        "passive": "{t} is contradicted by {s}.",
        "zh": "{s}与{t}矛盾。",
        "syn": "{s} conflicts with {t}.",
    },
    "CAUSES": {
        "current": "{s} causes {t}.",
        "nodot": "{s} causes {t}",
        "passive": "{t} is caused by {s}.",
        "zh": "{s}导致{t}。",
        "syn": "{s} leads to {t}.",
    },
    "ENABLES": {
        "current": "{s} enables {t}.",
        "nodot": "{s} enables {t}",
        "passive": "{t} is enabled by {s}.",
        "zh": "{s}使能{t}。",
        "syn": "{s} allows {t}.",
    },
    "APPLIES_TO": {
        "current": "{s} is applied to {t}.",
        "nodot": "{s} is applied to {t}",
        "passive": "{s} applied to {t}",
        "zh": "{s}应用于{t}。",
        "syn": "{s} is used for {t}.",
    },
    "LOCATED_AT": {
        "current": "{s} is located at {t}.",
        "nodot": "{s} is located at {t}",
        "passive": "{s} located at {t}",
        "zh": "{s}位于{t}。",
        "syn": "{s} is situated in {t}.",
    },
    "CREATED_BY": {  # 方向约定：Author(s) → Work(t)，句式必须「作品由作者创作」
        "current": "{t} was created by {s}.",
        "nodot": "{t} was created by {s}",
        "passive": "{s} created {t}.",
        "zh": "{t}由{s}创作。",
        "syn": "{t} was proposed by {s}.",
    },
    "CITES": {
        "current": "{s} cites {t}.",
        "nodot": "{s} cites {t}",
        "passive": "{t} is cited by {s}.",
        "zh": "{s}引用{t}。",
        "syn": "{s} references {t}.",
    },
}

# 反向模板：主宾语对调（只对非对称关系有意义）
INVERSE = {
    "USES": "{t} uses {s}.", "PART_OF": "{t} is part of {s}.",
    "PRODUCES": "{t} produces {s}.", "MEASURES": "{t} measures {s}.",
    "EVALUATES": "{t} evaluates {s}.", "COMPARES_WITH": "{t} is compared with {s}.",
    "SUPPORTS": "{t} supports {s}.", "CONTRADICTS": "{t} contradicts {s}.",
    "CAUSES": "{t} causes {s}.", "ENABLES": "{t} enables {s}.",
    "APPLIES_TO": "{t} is applied to {s}.", "LOCATED_AT": "{t} is located at {s}.",
    "CREATED_BY": "{s} was created by {t}.", "CITES": "{t} cites {s}.",
    "RELATED_TO": "{t} is related to {s}.",
}


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=500)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--max-len", type=int, default=256)
    ap.add_argument("--calib-paragraph-chars", type=int, default=1200,
                    help="前提整段截断长度，与 relation_verifier.calibrate() 的 parts[0][:1200] 对齐")
    return ap.parse_args()


ARGS = parse_args()


def load_model():
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    dev = "cuda" if torch.cuda.is_available() else "cpu"
    tok = AutoTokenizer.from_pretrained(str(TRIPLET))
    mdl = AutoModelForSequenceClassification.from_pretrained(str(TRIPLET))
    mdl.to(dev).eval()
    i2l = {int(k): str(v) for k, v in mdl.config.id2label.items()}
    sup = next((i for i, v in i2l.items() if "entail" in v.lower()), None)
    if sup is None:
        sup = next((i for i, v in i2l.items()
                    if v.lower() in ("label_1", "true", "1")), len(i2l) - 1)
    print(f"  模型 {i2l}  支持类={sup}  device={dev}")
    return mdl, tok, dev, sup


def infer(mdl, tok, dev, sup, premises, hyps):
    import torch
    out = []
    with torch.no_grad():
        for i in range(0, len(premises), ARGS.batch):
            enc = tok(premises[i:i + ARGS.batch], hyps[i:i + ARGS.batch],
                      padding=True, truncation=True, max_length=ARGS.max_len,
                      return_tensors="pt").to(dev)
            p = torch.softmax(mdl(**enc).logits, dim=-1)
            out.extend(p[:, sup].tolist())
    return out


def summarize(name, probs, base=None):
    n = len(probs)
    passed = sum(1 for x in probs if x >= THR)
    mean = sum(probs) / n
    d = ""
    if base is not None:
        delta = (sum(1 for x in probs if x >= THR) - base) / n * 100
        d = f"  Δ通过率 {delta:+6.1f}pp"
    print(f"  {name:26s} n={n:5d}  mean={mean:.3f}  通过率={passed/n*100:5.1f}%{d}")
    return {"n": n, "mean": round(mean, 4), "pass_rate": round(passed / n, 4)}


def main():
    t0 = time.time()
    print("=== L5 域内效度探针（无人工标注）===")
    data = json.loads(VERIFY.read_text(encoding="utf-8"))
    edges = data["edges"]
    rnd = random.Random(ARGS.seed)
    sample = rnd.sample(edges, min(ARGS.n, len(edges)))
    print(f"  从 {len(edges)} 条边抽样 {len(sample)} 条（seed={ARGS.seed}）\n")

    mdl, tok, dev, sup = load_model()
    result = {"seed": ARGS.seed, "n": len(sample), "threshold": THR,
              "source": str(VERIFY), "probes": {}}

    # ---------- P1 假设形式消融 ----------
    print("── P1 假设形式消融（前提固定 = 现行抽出的单句）──")
    premises = [e["premise"] for e in sample]
    p1 = {}
    base_pass = None
    for key in ("current", "nodot", "passive", "zh", "syn"):
        hyps = []
        for e in sample:
            f = FORMS.get(e["type"])
            if f:
                hyps.append(f[key].format(s=e["source"], t=e["target"]))
            else:  # 本体外类型：退化为 RELATED_TO 模板
                tpl = {"current": "{s} is related to {t}.",
                       "nodot": "{s} is related to {t}",
                       "passive": "{s} related to {t}",
                       "zh": "{s}与{t}相关。",
                       "syn": "{s} is associated with {t}."}[key]
                hyps.append(tpl.format(s=e["source"], t=e["target"]))
        probs = infer(mdl, tok, dev, sup, premises, hyps)
        if key == "current":
            base_pass = sum(1 for x in probs if x >= THR)
            base_mean = sum(probs) / len(probs)
        p1[key] = summarize(key, probs, base_pass)
        if key == "current":
            p1[key]["_hyps_head"] = hyps[:3]
    result["probes"]["P1_hypothesis_form"] = p1
    spread = (max(v["pass_rate"] for v in p1.values())
              - min(v["pass_rate"] for v in p1.values())) * 100
    print(f"  ⇒ 5 种等价表述的通过率极差 = {spread:.1f}pp"
          f"（mean 极差 {(max(v['mean'] for v in p1.values())-min(v['mean'] for v in p1.values())):.3f}）")
    result["p1_pass_rate_spread_pp"] = round(spread, 2)

    # ---------- P2 前提形式消融 ----------
    print("\n── P2 前提形式消融（假设固定 = 现行模板）──")
    hyps_cur = [FORMS.get(e["type"], {}).get("current", "{s} is related to {t}.")
                .format(s=e["source"], t=e["target"]) for e in sample]
    p2 = {}
    p2["ours_sentence"] = summarize("抽出的单句(现行)", infer(mdl, tok, dev, sup, premises, hyps_cur), base_pass)
    full = [(e.get("evidence") or "")[:ARGS.calib_paragraph_chars] for e in sample]
    p2["full_paragraph"] = summarize(f"整段截断[:{ARGS.calib_paragraph_chars}]",
                                     infer(mdl, tok, dev, sup, full, hyps_cur), base_pass)
    result["probes"]["P2_premise_form"] = p2

    # ---------- P3 方向反转 ----------
    print("\n── P3 方向反转（同前提，主宾语对调）──")
    inv_pairs = [(i, e) for i, e in enumerate(sample) if e["type"] in INVERSE]
    if inv_pairs:
        ip = [e["premise"] for _, e in inv_pairs]
        ih = [INVERSE[e["type"]].format(s=e["source"], t=e["target"]) for _, e in inv_pairs]
        fw = [e["entail_prob"] for _, e in inv_pairs]
        rv = infer(mdl, tok, dev, sup, ip, ih)
        flip = sum(1 for f, r in zip(fw, rv) if f >= THR and r < THR)
        print(f"  正向(hit=现有分) 通过率 {sum(1 for f in fw if f>=THR)/len(fw)*100:.1f}%"
              f"  反向通过率 {sum(1 for r in rv if r>=THR)/len(rv)*100:.1f}%")
        print(f"  因反转由通过翻为不通过: {flip}/{len(fw)} = {flip/len(fw)*100:.1f}%"
              f"   mean Δ = {sum(a-b for a,b in zip(fw,rv))/len(fw):+.4f}")
        result["probes"]["P3_direction"] = {
            "n": len(fw), "flip_rate": round(flip / len(fw), 4),
            "mean_delta": round(sum(a - b for a, b in zip(fw, rv)) / len(fw), 4)}

    # ---------- P4 规则型假阳性下界（不需模型）----------
    print("\n── P4 规则型「几乎必错」边（不需模型，纯规则）──")
    ASY = set(INVERSE) - {"RELATED_TO"}
    def nk(x):
        return re.sub(r"[^a-z0-9]+", "", (x or "").lower())
    allp = [e for e in edges if e["pass"]]
    selfloop_asym = [e for e in allp if e["type"] in ASY and nk(e["source"]) == nk(e["target"]) and nk(e["source"])]
    one_char = [e for e in allp if len(nk(e["source"])) <= 1 or len(nk(e["target"])) <= 1]
    numeric = [e for e in allp if re.fullmatch(r"[0-9.,%\-]+", (e["source"] or "").strip())
               or re.fullmatch(r"[0-9.,%\-]+", (e["target"] or "").strip())]
    sl_all = [e for e in allp if nk(e["source"]) == nk(e["target"]) and nk(e["source"])]
    strict = {id(e) for e in selfloop_asym}
    loose = strict | {id(e) for e in one_char} | {id(e) for e in numeric}
    print(f"  自环(含对称)被判通过        {len(sl_all):5d}  ({len(sl_all)/len(allp)*100:.2f}% of 通过集)")
    print(f"  其中非对称类型自环(逻辑必错) {len(selfloop_asym):5d}  ({len(selfloop_asym)/len(allp)*100:.2f}%)")
    print(f"  单字符端点                  {len(one_char):5d}")
    print(f"  纯数字端点                  {len(numeric):5d}")
    print(f"  ⇒ 严格下界 {len(strict)} 条 = {len(strict)/len(allp)*100:.2f}% of 通过集；"
          f"宽松下界 {len(loose)} 条 = {len(loose)/len(allp)*100:.2f}%")
    result["probes"]["P4_rule_based_fp_floor"] = {
        "pass_total": len(allp), "selfloop_pass": len(sl_all),
        "selfloop_asym_pass": len(selfloop_asym), "one_char_pass": len(one_char),
        "numeric_pass": len(numeric), "strict_floor": len(strict), "loose_floor": len(loose)}

    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果已写出: {OUT}  （耗时 {time.time()-t0:.0f}s）")


if __name__ == "__main__":
    main()
