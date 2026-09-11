"""L5 三元组验证层：用本地判别模型判定「关系能否被原文支持」。

为什么用这个而不是主观标注（见 docs/PHASE_REVIEW_2026-09-11.md）：
  4 个 LLM 标注者对「这条关系存在吗」的 Krippendorff α ≈ 0（接近随机），
  分母没有共识 ⇒ P/R/F1 无解释力。
  改为问「这条关系能被它自带的原文证据支持吗」——有唯一判定标准、可复现、零 API 成本。

模型（调研选定，RED^FM / ACL 2023 开源）：
  Babelscape/mdeberta-v3-base-triplet-critic-xnli
  输入格式（模型卡原文）："<句子> [SEP] <subject> <predicate> <object>"
  判据（模型卡原文）：confidence = softmax(logits)[1]，阈值 0.75

本项目适配：
  句  = 边的证据文本（evidence[].text，逐字来自 PDF）
  三元组 = "<source> <relation_type> <target>"
  即判定「在给定的原文片段中，source 与 target 之间是否存在该类型的关系」。

用法：
  # 先小样本验证（T1.3）
  python tools/triplet_critic.py --sample 50
  # 全量
  python tools/triplet_critic.py --all
  # 只看某类型
  python tools/triplet_critic.py --all --relation RELATED_TO
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

# ── 代理与镜像（本机实测：hf-mirror.com 需经 127.0.0.1:7897 代理）──
os.environ.setdefault("HTTP_PROXY", "http://127.0.0.1:7897")
os.environ.setdefault("HTTPS_PROXY", "http://127.0.0.1:7897")
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

GRAPHS = Path(r"D:\kg-demo\data\corpus\graphs")
OUT_DIR = Path(r"D:\kg-demo\data\quality")
MODEL_ID = "Babelscape/mdeberta-v3-base-triplet-critic-xnli"
THRESHOLD = 0.75


def parse_args():
    ap = argparse.ArgumentParser(description="三元组验证（本地 Triplet Critic）")
    ap.add_argument("--sample", type=int, default=0, help="随机抽 N 条做验证")
    ap.add_argument("--all", action="store_true", help="全量运行")
    ap.add_argument("--relation", default="", help="只处理该关系类型")
    ap.add_argument("--paper", default="", help="只处理该论文")
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--max-len", type=int, default=512)
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--limit", type=int, default=0, help="总条数上限（调试用）")
    return ap.parse_args()


ARGS = parse_args()


def norm(n: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (n or "").lower())


def load_edges():
    """从快照读全部边，附带证据文本。"""
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
            text = ""
            page = None
            if isinstance(ev, list):
                for item in ev:
                    if isinstance(item, dict) and item.get("text"):
                        text = str(item["text"])
                        page = item.get("page")
                        break
            rows.append({
                "paper": f.stem,
                "source": e.get("source") or "",
                "target": e.get("target") or "",
                "type": e.get("type") or "UNKNOWN",
                "evidence": text,
                "page": page,
            })
    return rows


def build_input(edge):
    """模型卡规定的输入格式：<句子> [SEP] <subject> <predicate> <object>"""
    sentence = " ".join((edge["evidence"] or "").split())
    triplet = f'{edge["source"]} {edge["type"]} {edge["target"]}'
    return f"{sentence} [SEP] {triplet}"


def main():
    t0 = time.time()
    print(f"=== L5 三元组验证（{MODEL_ID}）===")
    print(f"HF_ENDPOINT={os.environ.get('HF_ENDPOINT')}  "
          f"HTTPS_PROXY={os.environ.get('HTTPS_PROXY')}")

    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    print(f"torch {torch.__version__}  cuda={torch.cuda.is_available()}")
    device = ARGS.device if torch.cuda.is_available() else "cpu"
    print(f"device={device}")

    print("\n加载 tokenizer / model ...")
    tok = AutoTokenizer.from_pretrained(MODEL_ID)
    model = AutoModelForSequenceClassification.from_pretrained(MODEL_ID)
    model.to(device).eval()
    print(f"  id2label = {model.config.id2label}")
    print(f"  num_labels = {model.config.num_labels}")
    print(f"  参数量 ≈ {sum(p.numel() for p in model.parameters())/1e6:.1f}M")

    # 加载边
    rows = load_edges()
    print(f"\n候选边 {len(rows)} 条")
    if ARGS.sample:
        import random
        random.seed(42)
        rows = random.sample(rows, min(ARGS.sample, len(rows)))
    if ARGS.limit:
        rows = rows[:ARGS.limit]
    print(f"本次处理 {len(rows)} 条（batch={ARGS.batch}）")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    results = []
    bs = ARGS.batch

    with torch.no_grad():
        for i in range(0, len(rows), bs):
            chunk = rows[i:i + bs]
            texts = [build_input(e) for e in chunk]
            enc = tok(texts, padding=True, truncation=True,
                      max_length=ARGS.max_len, return_tensors="pt").to(device)
            logits = model(**enc).logits
            probs = torch.softmax(logits, dim=-1)[:, 1]  # Label_1 = 三元组成立
            for e, p in zip(chunk, probs.tolist()):
                e["confidence"] = round(float(p), 4)
                e["pass"] = bool(p >= THRESHOLD)
                results.append(e)
            if (i // bs) % 20 == 0:
                el = time.time() - t0
                done = min(i + bs, len(rows))
                eta = el / done * (len(rows) - done) if done else 0
                print(f"  {done}/{len(rows)}  {el:.0f}s  ETA {eta:.0f}s")

    # ── 统计 ──
    import statistics as st
    print(f"\n── 总体 ──")
    confs = [r["confidence"] for r in results]
    print(f"  均值 {st.mean(confs):.3f}  中位数 {st.median(confs):.3f}  "
          f"≥{THRESHOLD} 占比 {sum(r['pass'] for r in results)/len(results)*100:.1f}%")

    print(f"\n── 按关系类型 ──")
    by_type = {}
    for r in results:
        by_type.setdefault(r["type"], []).append(r["confidence"])
    print(f"  {'type':18s} {'n':>7s} {'mean':>7s} {'p25':>7s} {'median':>7s} "
          f"{'p75':>7s} {'pass%':>7s}")
    stats_rows = []
    for t, cs in sorted(by_type.items(), key=lambda x: -len(x[1])):
        cs_sorted = sorted(cs)
        q = lambda p: cs_sorted[min(int(len(cs_sorted) * p), len(cs_sorted) - 1)]  # noqa: E731
        passed = sum(1 for c in cs if c >= THRESHOLD) / len(cs) * 100
        print(f"  {t:18s} {len(cs):7d} {st.mean(cs):7.3f} {q(0.25):7.3f} "
              f"{st.median(cs):7.3f} {q(0.75):7.3f} {passed:6.1f}%")
        stats_rows.append({"type": t, "n": len(cs), "mean": round(st.mean(cs), 4),
                           "p25": round(q(0.25), 4), "median": round(st.median(cs), 4),
                           "p75": round(q(0.75), 4), "pass_rate": round(passed / 100, 4)})

    tail = "sample" if ARGS.sample else ("rel-" + ARGS.relation if ARGS.relation else "all")
    out = OUT_DIR / f"triplet-critic-{tail}.json"
    out.write_text(json.dumps({
        "model": MODEL_ID, "threshold": THRESHOLD, "device": device,
        "count": len(results), "elapsed_s": round(time.time() - t0, 1),
        "overall": {"mean": round(st.mean(confs), 4), "median": round(st.median(confs), 4),
                    "pass_rate": round(sum(r["pass"] for r in results) / len(results), 4)},
        "by_type": stats_rows,
        "edges": results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告已写出: {out}")
    print(f"总耗时 {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
