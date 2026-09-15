"""L5 关系证据支持度验证层（本地 Triplet Critic，零 API 成本）。

【模型选择：经量化校准后确定】
  用 Triplet Critic 官方带标签校准集实测（11998 行 en_triplet_entailment.tsv，
  premise=原句 / hypothesis=三元组 / label=是否被支持）：

    模型                                  ROC-AUC   宏平均 F1   准确率
    Babelscape/...-triplet-critic-xnli    0.9851     0.943      0.979   ← 采用
    cross-encoder/nli-deberta-v3-base     0.6370     0.427      0.494   ← 弃用

  弃用 cross-encoder 的决定性理由：其模型卡逐字 `language: en`，中文实测
  AUC 0.5445（≈随机），且出现灾难性假阴性（「知识图谱用于刻画概念及其相互关系。」
  → 0.003），会批量误删正确关系。Triplet Critic 是多语言模型（含 zh）。
  校准记录：data/quality/verifier-calibration.json

【阈值：不照抄文献，本机校准】
  模型卡写 0.75，RED^FM 论文附录 A 写 "we set a lower threshold, 0.1"
  （0.75 属 cRocoDiLe 的 NLI 过滤阶段）。两者语境不同，必须本机校准。
  本脚本用官方 EN 校准集自动搜索宏平均 F1 最优阈值。

【为什么需要这一层】
  4 个 LLM 标注者对「这条关系存在吗」的一致性接近随机（成对集合相似度
  实体 61.4% vs 关系 24.6%），分母无共识 ⇒ 关系 P/R/F1 无解释力。
  改问「这条关系能被它自带的原文证据支持吗」——有唯一判定标准、可复现、零成本。

【关键设计（实测驱动）】
  证据段落中位数 594 字符、且与实体对弱相关（如 `King ~~ Man` 的证据是论文摘要）。
  整段喂模型会被 512-token 截断并引入无关内容
  ⇒ 先从段落抽取同时含两个端点的句子。

用法：
  python tools/relation_verifier.py --calibrate     # 仅校准阈值
  python tools/relation_verifier.py --all           # 全量（自动校准后跑）
  python tools/relation_verifier.py --all --relation RELATED_TO
"""
# 路径以本文件为锚点解析，不写死盘符——演示机不一定把仓库放在 D 盘。
from pathlib import Path as _Path
_HERE = _Path(__file__).resolve()
ROOT = _HERE.parent.parent          # tools/*.py 与 src/*.py 都在根目录下一层
import argparse
import json
import os
import re
import time
from pathlib import Path

# 代理：本机实测 requests / hf_hub 必须显式传（WinINET 代理不自动生效）
PROXY = "http://127.0.0.1:7897"
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ.setdefault(_k, PROXY)

GRAPHS = ROOT / "data/corpus/graphs"
OUT_DIR = ROOT / "data/quality"

HF_HUB = Path(r"C:\Users\Administrator\.cache\huggingface\hub")
TRIPLET_DIR = next(
    HF_HUB.glob("models--Babelscape--mdeberta-v3-base-triplet-critic-xnli/snapshots/*"),
    None,
)

# 关系类型 → hypothesis 模板
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
    # 方向约定：T3.1 已把 CREATED_BY 统一为 Author(Person) → Work(Document)。
    # 所以 hypothesis 必须是「作品由作者创作」，而不是「作者被作品创作」。
    # 旧模板 "{s} was created by {t}." 与统一后的方向正好相反，会把语义判反
    # （实测：方向归一后通过率从 39.6% 掉到 26.0%，改回本模板后恢复）。
    "CREATED_BY": "{t} was created by {s}.",
    "CITES": "{s} cites {t}.",
    "RELATED_TO": "{s} is related to {t}.",
    # 2026-09-14 本体扩展：论文 -> 发表场所
    "PUBLISHED_IN": "{s} was published in {t}.",
}


def parse_args():
    ap = argparse.ArgumentParser(description="关系证据支持度验证（本地 Triplet Critic）")
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--calibrate", action="store_true", help="仅用官方数据校准阈值")
    ap.add_argument("--relation", default="")
    ap.add_argument("--paper", default="")
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--max-len", type=int, default=256)
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--show", type=int, default=0)
    ap.add_argument("--threshold", type=float, default=0.0, help="手动阈值；0=自动校准")
    ap.add_argument("--calib-n", type=int, default=4000)
    return ap.parse_args()


ARGS = parse_args()


def nkey(n: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (n or "").lower())


def split_sentences(text: str):
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return []
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z(\[])", text)
    return [p.strip() for p in parts if len(p.strip()) > 15]


def pick_evidence_sentence(evidence: str, source: str, target: str):
    """从长证据段落中选出同时含两个端点的句子。返回 (句子, both/one/none)。"""
    sents = split_sentences(evidence)
    if not sents:
        return "", "none"
    s_k, t_k = nkey(source), nkey(target)
    s_toks = [w for w in re.split(r"\W+", (source or "").lower()) if len(w) > 3]
    t_toks = [w for w in re.split(r"\W+", (target or "").lower()) if len(w) > 3]

    def has(sent, key, toks):
        low = nkey(sent)
        if key and len(key) > 3 and key in low:
            return True
        return bool(toks) and all(w in sent.lower() for w in toks)

    for sent in sents:
        if has(sent, s_k, s_toks) and has(sent, t_k, t_toks):
            return sent, "both"
    for sent in sents:
        if has(sent, s_k, s_toks) or has(sent, t_k, t_toks):
            return sent, "one"
    return max(sents, key=len), "none"


def load_model():
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    device = ARGS.device if torch.cuda.is_available() else "cpu"
    tok = AutoTokenizer.from_pretrained(str(TRIPLET_DIR))
    model = AutoModelForSequenceClassification.from_pretrained(str(TRIPLET_DIR))
    model.to(device).eval()
    id2label = {int(k): str(v) for k, v in model.config.id2label.items()}
    sup_idx = next((i for i, v in id2label.items() if "entail" in v.lower()), None)
    if sup_idx is None:
        sup_idx = next((i for i, v in id2label.items()
                        if v.lower() in ("label_1", "true", "1")), len(id2label) - 1)
    print(f"  模型 id2label = {id2label}  支持类下标 = {sup_idx}")
    print(f"  参数量 ≈ {sum(p.numel() for p in model.parameters())/1e6:.1f}M  device={device}")
    return model, tok, device, sup_idx


def infer(model, tok, device, sup_idx, premises, hypotheses):
    import torch
    out = []
    bs = ARGS.batch
    with torch.no_grad():
        for i in range(0, len(premises), bs):
            enc = tok(premises[i:i + bs], hypotheses[i:i + bs],
                      padding=True, truncation=True, max_length=ARGS.max_len,
                      return_tensors="pt").to(device)
            probs = torch.softmax(model(**enc).logits, dim=-1)
            out.extend(probs[:, sup_idx].tolist())
    return out


def roc_auc(scores, labels):
    pairs = sorted(zip(scores, labels), key=lambda x: x[0])
    ranks, i = {}, 0
    while i < len(pairs):
        j = i
        while j + 1 < len(pairs) and pairs[j + 1][0] == pairs[i][0]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[k] = avg
        i = j + 1
    n1 = sum(labels)
    n0 = len(labels) - n1
    if not n1 or not n0:
        return None
    rs = sum(ranks[k] for k in range(len(pairs)) if pairs[k][1] == 1)
    return round((rs - n1 * (n1 + 1) / 2) / (n1 * n0), 4)


def prf(probs, labels, thr):
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
    nprec = tn / (tn + fn) if (tn + fn) else 0.0
    nrec = tn / (tn + fp) if (tn + fp) else 0.0
    nf1 = 2 * nprec * nrec / (nprec + nrec) if (nprec + nrec) else 0.0
    return {"threshold": round(thr, 4), "precision": round(prec, 4),
            "recall": round(rec, 4), "f1": round(f1, 4),
            "neg_f1": round(nf1, 4), "macro_f1": round((f1 + nf1) / 2, 4),
            "accuracy": round((tp + tn) / (tp + fp + fn + tn), 4),
            "tp": tp, "fp": fp, "fn": fn, "tn": tn}


def calibrate(model, tok, device, sup_idx):
    tsv = TRIPLET_DIR / "Triplet-Critic-Data" / "en_triplet_entailment.tsv"
    if not tsv.exists():
        raise SystemExit(f"缺少校准数据 {tsv}，先运行 tools/fetch_hf_models.py")
    P, H, L = [], [], []
    with open(tsv, encoding="utf-8") as f:
        for i, line in enumerate(f):
            if i == 0:
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 3:
                continue
            P.append(parts[0][:1200]); H.append(parts[1])
            L.append(1 if parts[2].strip().lower() == "true" else 0)
            if len(L) >= ARGS.calib_n:
                break
    print(f"\n── 阈值校准（官方数据 {len(L)} 条：支持 {sum(L)} / 不支持 {len(L)-sum(L)}）──")
    t0 = time.time()
    probs = infer(model, tok, device, sup_idx, P, H)
    a = roc_auc(probs, L)
    print(f"  推理 {time.time()-t0:.1f}s   ROC-AUC = {a}")
    best, curve = None, []
    for i in range(1, 100):
        r = prf(probs, L, i / 100)
        curve.append(r)
        if best is None or r["macro_f1"] > best["macro_f1"]:
            best = r
    print(f"  宏平均 F1 最优阈值 = {best['threshold']:.2f}"
          f"   (正类 F1 {best['f1']:.3f} / 负类 F1 {best['neg_f1']:.3f}"
          f" / 宏F1 {best['macro_f1']:.3f} / 准确率 {best['accuracy']:.3f})")
    for thr in (0.1, 0.5, 0.67, 0.75, 0.9):
        r = prf(probs, L, thr)
        print(f"    thr={thr:<5} 正类P {r['precision']:.3f} R {r['recall']:.3f} F1 {r['f1']:.3f}"
              f"  宏F1 {r['macro_f1']:.3f}  (TP{r['tp']} FP{r['fp']} FN{r['fn']} TN{r['tn']})")
    return best["threshold"], a, best, curve


def load_edges():
    rows = []
    files = sorted(GRAPHS.glob("*.json"))
    if ARGS.paper:
        files = [f for f in files if f.stem == ARGS.paper]
    for f in files:
        try:
            snap = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  [skip] {f.name}: {e}")
            continue
        for e in snap.get("edges", []):
            if ARGS.relation and (e.get("type") or "") != ARGS.relation:
                continue
            ev = e.get("evidence") or []
            text, page = "", None
            if isinstance(ev, list):
                for it in ev:
                    if isinstance(it, dict) and it.get("text"):
                        text = str(it["text"]); page = it.get("page"); break
            rows.append({"paper": f.stem, "source": e.get("source") or "",
                         "target": e.get("target") or "",
                         "type": e.get("type") or "UNKNOWN",
                         "description": (e.get("description") or "").strip(),
                         "evidence": text, "page": page})
    return rows


def main():
    t0 = time.time()
    print("=== L5 关系证据支持度验证（本地 Triplet Critic，零 API 成本）===")
    if TRIPLET_DIR is None or not Path(TRIPLET_DIR).exists():
        raise SystemExit("未找到 Triplet Critic 模型，先运行 tools/fetch_hf_models.py")
    print(f"模型: {TRIPLET_DIR}")

    model, tok, device, sup_idx = load_model()
    thr, auc, best, curve = calibrate(model, tok, device, sup_idx)
    if ARGS.threshold > 0:
        thr = ARGS.threshold
        print(f"\n使用手动指定阈值 {thr}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if ARGS.calibrate:
        (OUT_DIR / "verifier-threshold.json").write_text(json.dumps({
            "model": str(TRIPLET_DIR), "calib_n": ARGS.calib_n,
            "roc_auc": auc, "chosen_threshold": thr,
            "best_by_macro_f1": best, "curve": curve,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n阈值已写出: {OUT_DIR / 'verifier-threshold.json'}")
        return

    rows = load_edges()
    print(f"\n候选边 {len(rows)} 条")
    if ARGS.sample:
        import random
        random.seed(42)
        rows = random.sample(rows, min(ARGS.sample, len(rows)))
    if ARGS.limit:
        rows = rows[:ARGS.limit]
    print(f"本次处理 {len(rows)} 条（batch={ARGS.batch}, max_len={ARGS.max_len}）\n")

    for r in rows:
        sent, hit = pick_evidence_sentence(r["evidence"], r["source"], r["target"])
        r["premise"] = sent
        r["hit"] = hit
        tmpl = REL_TEMPLATE.get(r["type"], "{s} is related to {t}.")
        r["hypothesis"] = tmpl.format(s=r["source"], t=r["target"])

    t1 = time.time()
    probs = infer(model, tok, device, sup_idx,
                  [r["premise"] for r in rows], [r["hypothesis"] for r in rows])
    print(f"推理完成 {len(rows)} 条，{time.time()-t1:.1f}s")
    for r, p in zip(rows, probs):
        r["entail_prob"] = round(float(p), 4)
        r["pass"] = bool(p >= thr)

    import statistics as st
    print("\n── 端点命中 ──")
    hits = {}
    for r in rows:
        hits[r["hit"]] = hits.get(r["hit"], 0) + 1
    for k in ("both", "one", "none"):
        c = hits.get(k, 0)
        print(f"   {k:5s} {c:6d}  {c/len(rows)*100:5.1f}%")

    print(f"\n── 按关系类型（阈值 {thr:.2f}）──")
    by_type = {}
    for r in rows:
        by_type.setdefault(r["type"], []).append(r)
    print(f"  {'type':18s} {'n':>7s} {'mean':>7s} {'median':>7s} {'p25':>7s} {'p75':>7s} {'pass%':>7s}")
    stats_rows = []
    for t, rs in sorted(by_type.items(), key=lambda x: -len(x[1])):
        ps = sorted(x["entail_prob"] for x in rs)
        q = lambda p: ps[min(int(len(ps) * p), len(ps) - 1)]  # noqa: E731
        passed = sum(1 for x in rs if x["pass"]) / len(rs)
        print(f"  {t:18s} {len(rs):7d} {st.mean(ps):7.3f} {st.median(ps):7.3f} "
              f"{q(0.25):7.3f} {q(0.75):7.3f} {passed*100:6.1f}%")
        stats_rows.append({"type": t, "n": len(rs), "mean": round(st.mean(ps), 4),
                           "median": round(st.median(ps), 4), "p25": round(q(0.25), 4),
                           "p75": round(q(0.75), 4), "pass_rate": round(passed, 4)})

    both = [r for r in rows if r["hit"] == "both"]
    if both:
        print(f"\n── 仅端点齐备（both, n={len(both)}）──")
        print(f"   mean {st.mean([r['entail_prob'] for r in both]):.3f}   "
              f"通过率 {sum(1 for r in both if r['pass'])/len(both)*100:.1f}%")

    if ARGS.show:
        print(f"\n── 明细（高分 {ARGS.show} 条 / 低分 {ARGS.show} 条）──")
        for label, seq in (("高分", sorted(rows, key=lambda x: -x["entail_prob"])[:ARGS.show]),
                           ("低分", sorted(rows, key=lambda x: x["entail_prob"])[:ARGS.show])):
            print(f"  ==== {label} ====")
            for r in seq:
                print(f"   [{r['type']}] {r['source'][:34]} -> {r['target'][:34]}"
                      f"  P={r['entail_prob']:.3f} hit={r['hit']}")
                print(f"      前提: {r['premise'][:150]}")

    tail = "sample" if ARGS.sample else ("rel-" + ARGS.relation if ARGS.relation else "all")
    out = OUT_DIR / f"relation-verify-{tail}.json"
    out.write_text(json.dumps({
        "model": str(TRIPLET_DIR), "id2label": str(model.config.id2label),
        "support_index": sup_idx, "threshold": thr, "calib_roc_auc": auc,
        "calib_n": ARGS.calib_n, "device": device, "count": len(rows),
        "hit_distribution": hits, "by_type": stats_rows, "edges": rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告已写出: {out}  （总耗时 {time.time()-t0:.0f}s）")


if __name__ == "__main__":
    main()
