"""验证一个疑似漏洞：L5 能否发现**关系方向被反转**？

【问题从哪来】
  一次质量评测框架调研（见 docs/MAINSTREAM_SOLUTIONS_SURVEY.md）指出：
  L3 只判「端点是否属于已知实体集」、L4 只判「谓词是否在 15 类本体内」，
  两者都不看方向；而 L5 用的 hypothesis 模板是否真的对方向敏感，
  从未被验证过。若 L5 也不敏感，则「方向错误」在六层里**无处可查**——
  这在 T3.1 刚做过全量方向归一之后尤其要紧：归一本身错了也没人会发现。

【怎么验（不依赖主观标注）】
  取每条边已有的 premise，用**同一个模板**生成两个 hypothesis：
    正向 = template(s=source, t=target)
    反向 = template(s=target, t=source)
  两个都送同一个判别器打分。若正向分系统性高于反向分，说明判别器确实
  在看方向；若两者无差别，则方向错误会静默通过。

【注意对称关系】
  RELATED_TO / COMPARES_WITH / CONTRADICTS 的模板本身近似对称
  （"X is related to Y" 反转后语义几乎不变），它们**不应**被计入判断。
  结论只建立在 12 个非对称关系上。

用法：
  python tools/verify_direction.py                 # 每类抽 60 条
  python tools/verify_direction.py --per-type 150
"""
# 路径以本文件为锚点解析，不写死盘符——演示机不一定把仓库放在 D 盘。
from pathlib import Path as _Path
_HERE = _Path(__file__).resolve()
ROOT = _HERE.parent.parent          # tools/*.py 与 src/*.py 都在根目录下一层
import argparse
import json
import random
from collections import defaultdict
from pathlib import Path

VERIFY = ROOT / "data/quality/relation-verify-all.json"
OUT = ROOT / "data/quality/direction-sensitivity.json"
HF_HUB = Path(r"C:\Users\Administrator\.cache\huggingface\hub")

# 与 tools/relation_verifier.py 保持逐字一致（改这里必须同步改那边）
REL_TEMPLATE = {
    "PART_OF": "{s} is part of {t}.",
    "USES": "{s} uses {t}.",
    "PRODUCES": "{s} produces {t}.",
    "MEASURES": "{s} measures {t}.",
    "EVALUATES": "{s} evaluates {t}.",
    "COMPARES_WITH": "{s} is compared with {t}.",
    "SUPPORTS": "{s} supports {t}.",
    "CONTRADICTS": "{s} contradicts {t}.",
    "CAUSES": "{s} causes {t}.",
    "ENABLES": "{s} enables {t}.",
    "APPLIES_TO": "{s} is applied to {t}.",
    "LOCATED_AT": "{s} is located at {t}.",
    "CREATED_BY": "{t} was created by {s}.",
    "CITES": "{s} cites {t}.",
    "RELATED_TO": "{s} is related to {t}.",
    # 2026-09-14 本体扩展（唯一来源仍是 src/ontology.py 的 REL_HYPOTHESIS_TEMPLATE，
    # 这里只是同步一份供本脚本离线使用；check_ontology_consistency.py 会逐字校验）
    "PUBLISHED_IN": "{s} was published in {t}.",
}

# 模板语义近似对称的关系：反转后句子含义基本不变，不能用来判断方向敏感性
SYMMETRIC = {"RELATED_TO", "COMPARES_WITH", "CONTRADICTS"}

THRESHOLD = 0.67            # 与 relation-verify-all.json 一致


def parse_args():
    ap = argparse.ArgumentParser(description="L5 方向敏感性验证")
    ap.add_argument("--per-type", type=int, default=60)
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--max-len", type=int, default=256)
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--seed", type=int, default=42)
    return ap.parse_args()


ARGS = parse_args()


def load_model():
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    cand = sorted(HF_HUB.glob(
        "models--Babelscape--mdeberta-v3-base-triplet-critic-xnli/snapshots/*"))
    if not cand:
        raise SystemExit("未找到 triplet critic 模型缓存")
    d = cand[-1]
    device = ARGS.device if torch.cuda.is_available() else "cpu"
    tok = AutoTokenizer.from_pretrained(str(d))
    model = AutoModelForSequenceClassification.from_pretrained(str(d))
    model.to(device).eval()
    id2label = {int(k): str(v) for k, v in model.config.id2label.items()}
    sup = next((i for i, v in id2label.items() if "entail" in v.lower()), None)
    if sup is None:
        sup = next((i for i, v in id2label.items()
                    if v.lower() in ("label_1", "true", "1")), len(id2label) - 1)
    print(f"模型 {d.name[:16]}…  id2label={id2label}  支持类={sup}  device={device}")
    return model, tok, device, sup


def infer(model, tok, device, sup, premises, hypotheses):
    import torch
    out = []
    with torch.no_grad():
        for i in range(0, len(premises), ARGS.batch):
            enc = tok(premises[i:i + ARGS.batch], hypotheses[i:i + ARGS.batch],
                      padding=True, truncation=True, max_length=ARGS.max_len,
                      return_tensors="pt").to(device)
            probs = torch.softmax(model(**enc).logits, dim=-1)
            out.extend(probs[:, sup].tolist())
    return out


def main():
    d = json.loads(VERIFY.read_text(encoding="utf-8"))
    edges = d["edges"]
    print(f"载入 {len(edges)} 条已打分的边（阈值 {d['threshold']}）")

    # 抽样：每类等量，优先 hit=both（premise 同时含两个端点，方向判断才有意义）
    rng = random.Random(ARGS.seed)
    by_type = defaultdict(list)
    for e in edges:
        if e.get("premise") and e.get("hit") == "both":
            by_type[e["type"]].append(e)
    sample = []
    for t, rows in sorted(by_type.items()):
        rng.shuffle(rows)
        sample.extend(rows[:ARGS.per_type])
    print(f"抽样 {len(sample)} 条，覆盖 {len(by_type)} 类关系\n")

    model, tok, device, sup = load_model()

    premises = [e["premise"] for e in sample]
    fwd = [REL_TEMPLATE.get(e["type"], "{s} is related to {t}.").format(
        s=e["source"], t=e["target"]) for e in sample]
    rev = [REL_TEMPLATE.get(e["type"], "{s} is related to {t}.").format(
        s=e["target"], t=e["source"]) for e in sample]

    print("跑正向 hypothesis ...")
    p_fwd = infer(model, tok, device, sup, premises, fwd)
    print("跑反向 hypothesis ...")
    p_rev = infer(model, tok, device, sup, premises, rev)

    # 按关系类型汇总
    agg = defaultdict(lambda: {"n": 0, "fwd": 0.0, "rev": 0.0, "fwd_win": 0,
                               "flip": 0, "delta_sum": 0.0})
    for e, a, b in zip(sample, p_fwd, p_rev):
        k = e["type"]
        g = agg[k]
        g["n"] += 1
        g["fwd"] += a
        g["rev"] += b
        g["delta_sum"] += (a - b)
        if a > b:
            g["fwd_win"] += 1
        # 关键指标：正向通过、反向不通过 ⇒ 方向错误会被 L5 抓住
        if a >= THRESHOLD and b < THRESHOLD:
            g["flip"] += 1

    print("\n" + "=" * 100)
    print(f"{'关系类型':18s} {'n':>4s} {'正向均值':>9s} {'反向均值':>9s} {'Δ':>8s}"
          f" {'正向更高':>9s} {'阈值翻转':>10s}  说明")
    print("-" * 100)

    # 只在本体 15 类内做判定。数据里还有 100+ 种越界关系类型
    # （`for`/`are`/`Google's Knowledge Graph` …，多为单样本），
    # 它们既没有稳定语义、样本量也不足，混进来会污染结论。
    ONTOLOGY = set(REL_TEMPLATE)

    def show(k, note):
        g = agg[k]
        n = g["n"]
        print(f"{k:18s} {n:4d} {g['fwd']/n:9.4f} {g['rev']/n:9.4f}"
              f" {(g['fwd']-g['rev'])/n:+8.4f} {g['fwd_win']/n*100:8.1f}%"
              f" {g['flip']:5d}/{n:<4d}  {note}")

    def total(keys):
        n = sum(agg[k]["n"] for k in keys)
        if not n:
            return 0, 0.0, 0.0, 0, 0
        f = sum(agg[k]["fwd"] for k in keys)
        r = sum(agg[k]["rev"] for k in keys)
        w = sum(agg[k]["fwd_win"] for k in keys)
        fl = sum(agg[k]["flip"] for k in keys)
        return n, (f - r) / n, w / n * 100, fl, fl / n * 100

    print("── 本体内 · 非对称（判定依据）──")
    asym_keys = [k for k in ONTOLOGY if k not in SYMMETRIC]
    for k in sorted(asym_keys, key=lambda x: -(agg[x]["fwd"] - agg[x]["rev"]) / agg[x]["n"]):
        show(k, "非对称")
    n_a, d_a, w_a, f_a, fr_a = total(asym_keys)
    print("-" * 100)
    print(f"{'非对称小计':18s} {n_a:4d} {'':>9s} {'':>9s} {d_a:+8.4f} {w_a:8.1f}%"
          f" {f_a:5d}/{n_a:<4d}")

    print("\n── 本体内 · 对称（模板反转后语义不变，不计入判定）──")
    sym_keys = sorted(SYMMETRIC & ONTOLOGY)
    for k in sym_keys:
        show(k, "对称")
    n_s, d_s, w_s, f_s, _ = total(sym_keys)
    print(f"{'对称小计':18s} {n_s:4d} {'':>9s} {'':>9s} {d_s:+8.4f} {w_s:8.1f}%"
          f" {f_s:5d}/{n_s:<4d}")

    off_keys = [k for k in agg if k not in ONTOLOGY]
    n_o, d_o, w_o, f_o, _ = total(off_keys)
    print(f"\n── 本体外（{len(off_keys)} 类，仅汇总，不参与判定）──")
    print(f"{'本体外小计':18s} {n_o:4d} {'':>9s} {'':>9s} {d_o:+8.4f} {w_o:8.1f}%"
          f" {f_o:5d}/{n_o:<4d}")

    print("\n" + "=" * 100)
    print("── 结论 ──")
    verdict = ("L5 对方向敏感" if (d_a > 0.10 and w_a > 65)
               else "L5 对方向不敏感")
    print(f"  本体内非对称关系（{len(asym_keys)} 类 / {n_a} 条）：")
    print(f"    正向比反向平均高 {d_a:+.4f}；正向更高的占 {w_a:.1f}%")
    print(f"    阈值 {THRESHOLD} 下，{f_a} 条（{fr_a:.1f}%）会因方向反转"
          f"而从「通过」翻为「不通过」")
    print(f"  ⇒ 判定：**{verdict}**")
    if verdict == "L5 对方向不敏感":
        print("     六层中没有任何一层能发现方向错误，须在文档中声明该盲区。")
    else:
        print(f"     但仍有 {100-fr_a:.1f}% 的方向错误不会被 L5 拦住——")
        print("     即 L5 对方向只是部分敏感，不能当作方向校验层。")
        weakest = min(asym_keys, key=lambda x: (agg[x]["fwd"] - agg[x]["rev"]) / agg[x]["n"])
        print(f"     最弱的是 {weakest}：Δ={(agg[weakest]['fwd']-agg[weakest]['rev'])/agg[weakest]['n']:+.4f}，"
              f"翻转仅 {agg[weakest]['flip']}/{agg[weakest]['n']}")

    out = {
        "threshold": THRESHOLD,
        "per_type_sampled": ARGS.per_type,
        "ontology_asymmetric": {
            "types": sorted(asym_keys), "n": n_a,
            "mean_delta": round(d_a, 4),
            "forward_win_pct": round(w_a, 2),
            "threshold_flip": f_a,
            "threshold_flip_pct": round(fr_a, 2),
        },
        "ontology_symmetric": {
            "types": sym_keys, "n": n_s, "mean_delta": round(d_s, 4),
        },
        "out_of_ontology": {
            "n_types": len(off_keys), "n": n_o, "mean_delta": round(d_o, 4),
        },
        "by_type": {k: {"n": v["n"],
                        "mean_forward": round(v["fwd"] / v["n"], 4),
                        "mean_reverse": round(v["rev"] / v["n"], 4),
                        "mean_delta": round((v["fwd"] - v["rev"]) / v["n"], 4),
                        "forward_win": v["fwd_win"],
                        "threshold_flip": v["flip"]}
                    for k, v in agg.items()},
        "verdict": verdict,
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告已写出: {OUT}")


if __name__ == "__main__":
    main()
