"""用官方校准数据量化对比两个判别模型，并确定最优阈值。

为什么需要这个（这是此前缺失的验证环节）：
  我此前选定 `cross-encoder/nli-deberta-v3-base` 时**从未与人工标注交叉验证过**，
  只看样本"觉得合理"。属可用性驱动的妥协（见 docs/ERROR_CASES.md 案例 4）。
  现在有了 Triplet Critic 官方发布的带标签校准集，可以做真正的量化对比。

数据：`Triplet-Critic-Data/en_triplet_entailment.tsv`（11998 行）
  格式：premise \t hypothesis \t label
  label：True（10972，91.5%）/ False（1025，8.5%）
  ⚠️ 类别极度不平衡 ⇒ **准确率无意义**（全猜 True 即 91.5%）。
     必须用 Precision / Recall / F1 / PR 曲线。

两个候选：
  A. Babelscape/mdeberta-v3-base-triplet-critic-xnli  —— 专为三元组支持判定训练（RED^FM, ACL 2023）
  B. cross-encoder/nli-deberta-v3-base               —— 通用 NLI cross-encoder（当前采用）

报告内容：
  · 两个模型在各自"负面类"上的 P/R/F1（在不同阈值下）
  · 各自的最优阈值（最大化 F1）
  · 两者的判定一致性
  · 阈值文献矛盾的裁定（模型卡 0.75 vs 论文 0.1）

用法：
  python tools/calibrate_verifier.py
  python tools/calibrate_verifier.py --max 3000     # 快速抽样
"""
import argparse
import json
import os
import time
from pathlib import Path

PROXY = "http://127.0.0.1:7897"
for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ[k] = PROXY
os.environ.pop("HF_ENDPOINT", None)

OUT = Path(r"D:\kg-demo\data\quality")

TRIPLET_DIR = next(
    Path(r"C:\Users\Administrator\.cache\huggingface\hub").glob(
        "models--Babelscape--mdeberta-v3-base-triplet-critic-xnli/snapshots/*"
    ),
    None,
)
CROSS_DIR = Path(
    r"C:\Users\Administrator\.cache\modelscope\hub\models\cross-encoder\nli-deberta-v3-base"
)


def parse_args():
    ap = argparse.ArgumentParser(description="判别模型量化校准")
    ap.add_argument("--max", type=int, default=0, help="最多用多少条（0=全部）")
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--device", default="cuda")
    return ap.parse_args()


ARGS = parse_args()


def load_tsv():
    tsv = TRIPLET_DIR / "Triplet-Critic-Data" / "en_triplet_entailment.tsv"
    if not tsv.exists():
        raise SystemExit(f"校准数据不存在: {tsv}\n先运行 tools/fetch_hf_models.py")
    premises, hypotheses, labels = [], [], []
    with open(tsv, encoding="utf-8") as f:
        for i, line in enumerate(f):
            if i == 0:
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 3:
                continue
            premises.append(parts[0])
            hypotheses.append(parts[1])
            labels.append(1 if parts[2].strip().lower() == "true" else 0)
    return premises, hypotheses, labels


def run_model(path, premises, hypotheses, positive_label_names=("entailment", "true", "label_1")):
    """跑一个模型，返回负面类（label=0）的概率列表。

    约定：校准集 label=1 表示 True（三元组被支持），label=0 表示 False。
    我们需要 P(不支持) = 1 - P(支持)。
    """
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    device = ARGS.device if torch.cuda.is_available() else "cpu"
    print(f"\n加载 {path.name if hasattr(path,'name') else path}")
    tok = AutoTokenizer.from_pretrained(str(path))
    model = AutoModelForSequenceClassification.from_pretrained(str(path))
    model.to(device).eval()
    id2label = {int(k): str(v) for k, v in model.config.id2label.items()}
    print(f"  id2label = {id2label}  params≈{sum(p.numel() for p in model.parameters())/1e6:.0f}M")

    # 定位"支持/蕴含"那一类：优先 entailment，其次 label_1，再退化到标签数-1
    sup_idx = None
    for i, v in id2label.items():
        if "entail" in v.lower():
            sup_idx = i
            break
    if sup_idx is None:
        for i, v in id2label.items():
            if v.lower() in ("label_1", "true", "1"):
                sup_idx = i
                break
    if sup_idx is None:
        sup_idx = len(id2label) - 1
    print(f"  支持类下标 = {sup_idx} ({id2label[sup_idx]})")

    probs = []
    t0 = time.time()
    n = len(premises)
    bs = ARGS.batch
    with torch.no_grad():
        for i in range(0, n, bs):
            enc = tok(premises[i:i + bs], hypotheses[i:i + bs],
                      padding=True, truncation=True, max_length=256,
                      return_tensors="pt").to(device)
            out = torch.softmax(model(**enc).logits, dim=-1)
            probs.extend(out[:, sup_idx].tolist())
            if (i // bs) % 50 == 0:
                print(f"    {min(i+bs,n)}/{n}  {time.time()-t0:.0f}s")
    print(f"  完成 {n} 条，{time.time()-t0:.1f}s")
    del model
    torch.cuda.empty_cache()
    return probs, id2label, sup_idx


def prf_at(probs, labels, thr):
    """以 thr 为「支持」阈值：prob>=thr 判支持(1)，否则不支持(0)。

    返回**正类（支持）**与**负类（不支持）**两套指标。
    ⚠️ 本校准集正类占 ~89%，类别极不平衡：
       - 负面类 F1 会被极端阈值带偏（全判正类时负面类 P 极低但 R 高）
       - 因此主指标用**正类 F1**与**宏平均 F1**，并同时报告准确率作参考
    """
    tp = fp = fn = tn = 0
    for p, y in zip(probs, labels):
        pred = 1 if p >= thr else 0
        if pred == 1 and y == 1:
            tp += 1
        elif pred == 1 and y == 0:
            fp += 1
        elif pred == 0 and y == 1:
            fn += 1
        else:
            tn += 1
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    # 负类（不支持）指标
    nprec = tn / (tn + fn) if (tn + fn) else 0.0
    nrec = tn / (tn + fp) if (tn + fp) else 0.0
    nf1 = 2 * nprec * nrec / (nprec + nrec) if (nprec + nrec) else 0.0
    acc = (tp + tn) / (tp + fp + fn + tn) if (tp + fp + fn + tn) else 0.0
    return {"threshold": round(thr, 4),
            "precision": round(prec, 4), "recall": round(rec, 4), "f1": round(f1, 4),
            "neg_precision": round(nprec, 4), "neg_recall": round(nrec, 4),
            "neg_f1": round(nf1, 4), "macro_f1": round((f1 + nf1) / 2, 4),
            "accuracy": round(acc, 4),
            "tp": tp, "fp": fp, "fn": fn, "tn": tn}


def roc_auc(scores_, labels):
    """ROC-AUC（Mann-Whitney U，含并列处理）。与阈值无关，衡量真实区分能力。"""
    pairs = sorted(zip(scores_, labels), key=lambda x: x[0])
    ranks = {}
    i = 0
    while i < len(pairs):
        j = i
        while j + 1 < len(pairs) and pairs[j + 1][0] == pairs[i][0]:
            j += 1
        avg_rank = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[k] = avg_rank
        i = j + 1
    n1 = sum(labels)
    n0 = len(labels) - n1
    if n1 == 0 or n0 == 0:
        return None
    rank_sum_pos = sum(ranks[k] for k in range(len(pairs)) if pairs[k][1] == 1)
    u = rank_sum_pos - n1 * (n1 + 1) / 2
    return round(u / (n1 * n0), 4)


def best_threshold(probs, labels, key="macro_f1"):
    """扫描阈值，按指定指标取最优（默认宏平均 F1，对不平衡更稳健）。"""
    best = None
    for i in range(1, 100):
        r = prf_at(probs, labels, i / 100)
        if best is None or r[key] > best[key]:
            best = r
    return best


def main():
    print("=== 判别模型量化校准（用 Triplet Critic 官方带标签数据）===")
    premises, hypotheses, labels = load_tsv()
    if ARGS.max:
        premises, hypotheses, labels = premises[:ARGS.max], hypotheses[:ARGS.max], labels[:ARGS.max]
    n = len(labels)
    pos = sum(labels)
    print(f"样本 {n}  支持(1)={pos} ({pos/n*100:.1f}%)  不支持(0)={n-pos} ({(n-pos)/n*100:.1f}%)")
    print(f"⚠️ 类别不平衡：全猜'支持'的基线准确率 = {pos/n*100:.1f}% ⇒ 准确率无意义，看负面类 F1")

    report = {"n": n, "positive": pos, "negative": n - pos, "models": []}

    for name, path in (("triplet-critic", TRIPLET_DIR),
                       ("nli-cross-encoder", CROSS_DIR)):
        if path is None or not Path(path).exists():
            print(f"\n[skip] {name}: 目录不存在")
            continue
        probs, id2label, sup_idx = run_model(Path(path), premises, hypotheses)
        entry = {"name": name, "path": str(path), "id2label": id2label,
                 "support_index": sup_idx}

        a = roc_auc(probs, labels)
        entry["roc_auc"] = a
        mean_score = sum(probs) / len(probs)
        entry["mean_score"] = round(mean_score, 4)

        # 分数分布（用于判断是否饱和）
        def hist(vals, bins=10):
            h = [0] * bins
            for v in vals:
                h[min(int(v * bins), bins - 1)] += 1
            return h
        entry["score_hist_all"] = hist(probs)
        entry["score_hist_pos"] = hist([x for x, y in zip(probs, labels) if y == 1])
        entry["score_hist_neg"] = hist([x for x, y in zip(probs, labels) if y == 0])

        best = best_threshold(probs, labels, key="macro_f1")
        entry["best_threshold_by_macro_f1"] = best
        print(f"\n  ── {name} ──")
        print(f"   ROC-AUC = {a}   （与阈值无关，衡量真实区分能力）")
        print(f"   分数均值 {mean_score:.4f}")
        print(f"   正类分布 {entry['score_hist_pos']}")
        print(f"   负类分布 {entry['score_hist_neg']}")
        print(f"   宏平均 F1 最优阈值 = {best['threshold']:.2f}")
        print(f"     正类  P {best['precision']:.3f}  R {best['recall']:.3f}  F1 {best['f1']:.3f}")
        print(f"     负类  P {best['neg_precision']:.3f}  R {best['neg_recall']:.3f}  F1 {best['neg_f1']:.3f}")
        print(f"     宏平均 F1 {best['macro_f1']:.3f}   准确率 {best['accuracy']:.3f}")
        print(f"   参考阈值（正类 F1 / 宏 F1 / 准确率）：")
        ref = {}
        for thr in (0.1, 0.3, 0.5, 0.7, 0.75, 0.9):
            r = prf_at(probs, labels, thr)
            ref[str(thr)] = r
            print(f"     {thr:<5}  P {r['precision']:.3f} R {r['recall']:.3f} F1 {r['f1']:.3f}"
                  f"   |  宏F1 {r['macro_f1']:.3f}  准确率 {r['accuracy']:.3f}"
                  f"  (TP{r['tp']} FP{r['fp']} FN{r['fn']} TN{r['tn']})")
        entry["reference_thresholds"] = ref
        entry["raw_probs_sample"] = [round(p, 4) for p in probs[:50]]
        report["models"].append(entry)

    # 一致性对比
    if len(report["models"]) == 2:
        a = report["models"][0]["raw_probs_sample"]
        b = report["models"][1]["raw_probs_sample"]
        agree = sum(1 for x, y in zip(a, b) if (x >= 0.5) == (y >= 0.5))
        print(f"\n── 两模型在支持阈值 0.5 下的一致率（前 50 条）: {agree}/{len(a)} = {agree/len(a)*100:.0f}%")
        report["agreement_at_0.5"] = round(agree / len(a), 4)

    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / "verifier-calibration.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告已写出: {out}")


if __name__ == "__main__":
    main()
