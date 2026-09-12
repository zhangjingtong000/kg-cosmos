# -*- coding: utf-8 -*-
"""L4 谓词选择层：**谓词选对了吗**（不是"谓词在不在 15 类内"）。

    python tools/l4_predicate_report.py --smoke          # 每类 30 条，约 1 分钟
    python tools/l4_predicate_report.py --per-type 150   # 分层抽样，约 5 分钟
    python tools/l4_predicate_report.py --full           # 全量（batch=64 约 1.5 小时）
    python tools/l4_predicate_report.py --full --reuse-l5
    python tools/l4_predicate_report.py --per-type 200 --form phrase   # 形态消融

【补的是什么】
  现行 L4 = 99.5% 只判「谓词是否落在本体 15 类内」。把一条本该 USES 的边标成
  PART_OF **照样通过**。docs/L5_SIX_LAYER_REPORT.md §6.5 第 3 条已如实声明：
  「本项目的 L4 比报告版窄……选错但落在本体内会被判通过」。
  本脚本补的是报告版第 4 层：**谓词类型是否匹配**。

【算法（一句话）】
  对每条边，让**同一条 premise** 与 15 个假设句模板各配一个 hypothesis，
  取 entail_prob 最大的类作为预测谓词；与标注谓词一致记对。

【模板从哪来】
  src/ontology.py 的 REL_HYPOTHESIS_TEMPLATE（唯一来源，15 类，方向已锁）。
  `--form phrase` 换成 REL_PHRASE_TEMPLATE（名词短语形态，与官方校准集同形），
  用于形态消融 —— 0.9851 的 ROC-AUC 是名词短语形态上标定的。

【复用的合法性（实测，不是推断）】
  relation-verify-all.json 的 `hypothesis` 字段 = 主口径模板按 (source,target)
  代入的结果，实测 **43630/43630 逐字一致**（脚本启动时复核，不一致就报错退出）。
  ⇒ 因此「标注谓词」那一项的分数**就是** L5 的 entail_prob，`--reuse-l5` 可直接
     小用，只算其余 14 类（省 1/15 算力）。

【独立性与循环：必须写进论文的三件事】
  ① **不构成「用模型验证模型」**。L4 的标签 y 来自**抽取器**（LLM，见
     src/kg_extractor.py），不是本判别器产生的；判别器只是打分器。
     同一个判别器既做 L5 又做 L4 不违例——两次推理的**任务不同**
     （L5 是单假设蕴含判定；L4 是 15 个竞争假设的判别/argmax），
     共享编码器 ≠ 循环论证。
  ② **但 L4 与 L5 共享同一批分数，因此不独立**。L4 候选集里标注类那一格
     就是 L5 的 entail_prob（①的复核已证）。所以二者**不能当作两条独立证据**；
     报 L4 时必须说明它的分母与 L5 同源。
  ③ **真正的自证风险在证据选择，不在模型**。`pick_evidence_sentence()` 优先挑
     「同时含两个端点」的句子，而模板句里又直接嵌了这两个端点的字面串 ⇒
     argmax 可能退化成"端点共现检测"。已实测：判别器分数有八成可由
     「端点词在不在句子里」解释（`hit` vs `entail_prob` 的 AUC = 0.8177，
     见 docs/L5_VALIDITY_PLAN.md §1.1）。对策有三个，本脚本全部内建：
       · 按 `hit`（both/one/none）**分层报告**，主口径另列 hit=both 子集；
       · `--form phrase` 形态消融（换形态后一致率大跌 ⇒ 分数由措辞而非语义驱动）；
       · 交叉统计 B（标注类分数 ≥ 阈值却输给别的类）—— 这正是"模板区分度不足"
         的量化，也是 L5 通过率 63.7% 与 L4 选对率并不矛盾的原因。

【口径三选一，别混用】
  · `--full`          无抽样误差，但仍有模型偏差；论文主数字用它。
  · `--per-type N`    分层抽样 + 层权还原 + 层内 bootstrap CI；用于快速回归。
  · `--smoke`         仅验证脚本能跑通（每类 30 条），数字不可引用。
"""
# 路径以本文件为锚点解析，不写死盘符——演示机不一定把仓库放在 D 盘。
from pathlib import Path as _Path
_HERE = _Path(__file__).resolve()
ROOT = _HERE.parent.parent          # tools/*.py 与 src/*.py 都在根目录下一层
import argparse
import json
import os
import random
import statistics
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(str(ROOT))
sys.path.insert(0, str(ROOT / "src"))
from ontology import (  # noqa: E402
    L5_MODEL, L5_THRESHOLD, REL_HYPOTHESIS_TEMPLATE, REL_PHRASE_TEMPLATE,
    RELATION_TYPES,
)

VERIFY = ROOT / "data" / "quality" / "relation-verify-all.json"
OUT = ROOT / "data" / "quality" / "l4-predicate.json"

# 离线：模型只从本地 HF 缓存读，删掉代理，保证"演示现场无外网"也跑得通
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ.pop(_k, None)

ONTOLOGY = sorted(RELATION_TYPES)          # 15 类，固定顺序 = 候选集顺序
IDX = {t: i for i, t in enumerate(ONTOLOGY)}
NCLS = len(ONTOLOGY)


def parse_args():
    ap = argparse.ArgumentParser(description="L4 谓词选择评测（本地 NLI，零 API）")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--full", action="store_true", help="全量 43630 条")
    g.add_argument("--per-type", type=int, default=0, help="每类抽 N 条（分层）")
    g.add_argument("--smoke", action="store_true", help="每类 30 条，仅验证可跑")
    ap.add_argument("--form", choices=("sentence", "phrase"), default="sentence",
                    help="sentence=主口径完整句模板；phrase=名词短语（校准集形态）")
    ap.add_argument("--reuse-l5", action="store_true",
                    help="标注类分数直接取 L5 的 entail_prob，只算其余 14 类")
    ap.add_argument("--batch", type=int, default=64,
                    help="实测 batch=64 约 121 对/秒，batch=32 约 34 对/秒")
    ap.add_argument("--max-len", type=int, default=256)
    ap.add_argument("--device", default="cuda")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--boot", type=int, default=1000, help="bootstrap 次数（抽样模式）")
    ap.add_argument("--limit", type=int, default=0, help="调试用：总边数上限")
    ap.add_argument("--tie-eps", type=float, default=1e-4,
                    help="平局判定：|最高分 − 标注类分| ≤ 此值算平局")
    return ap.parse_args()


ARGS = parse_args()
TEMPLATES = REL_HYPOTHESIS_TEMPLATE if ARGS.form == "sentence" else REL_PHRASE_TEMPLATE


# ──────────────────────────── 数据 ────────────────────────────
def load_edges():
    """读权威边集。只保留『本体 15 类内 + premise 非空』——这是 L4 的有效分母。

    本体外 237 条（161 种类型里的长尾）不作为候选：它们的标注谓词不在候选集里，
    放进 argmax 只会制造一个恒错的类别，把准确率压低却说明不了任何事。
    它们的数量单独报出，避免"分母被悄悄缩小"。
    """
    d = json.loads(VERIFY.read_text(encoding="utf-8"))
    all_edges = d["edges"]
    rows = [e for e in all_edges
            if e.get("type") in IDX and (e.get("premise") or "").strip()]
    outside = [e for e in all_edges if e.get("type") not in IDX]
    no_premise = [e for e in all_edges
                  if e.get("type") in IDX and not (e.get("premise") or "").strip()]
    print(f"权威边 {len(all_edges)} 条（阈值 {d['threshold']}）")
    print(f"  本体内且 premise 非空 {len(rows)}  ← L4 有效分母")
    print(f"  本体外（标注谓词不在候选集）{len(outside)}  无 premise {len(no_premise)}")
    return d, rows, len(all_edges), len(outside), len(no_premise)


def verify_hypothesis_field(rows):
    """核实 `hypothesis` 字段 == 主口径模板代入结果。

    这一步是 `--reuse-l5` 的**前提**：只有逐字一致，L5 的 entail_prob 才
    正好是 L4 候选集里"标注类"那一格的分数。不一致就必须拒绝复用。
    """
    bad = 0
    for e in rows:
        want = REL_HYPOTHESIS_TEMPLATE[e["type"]].format(s=e["source"], t=e["target"])
        if e.get("hypothesis") != want:
            bad += 1
    print(f"\n[核实] hypothesis 字段 == REL_HYPOTHESIS_TEMPLATE 代入结果："
          f"{len(rows)-bad}/{len(rows)} 一致")
    return bad


def pick_sample(rows, per_type, seed):
    """按谓词分层抽样，层内再按 hit 分层配平。

    【为什么按谓词分层而不是按 hit 分层】
      混淆矩阵是按谓词的 15×15。按 hit 比例抽的话，CONTRADICTS(243)、CAUSES(280)
      这些类在样本里几乎是空的，它们的召回与混淆格全是噪声。按谓词分层保证
      **每一类都有 n_t 条**，小类几乎全查、大类只取一部分，靠层权 w=N_t/n_t 还原。
    【为什么层内还要管 hit】
      hit 与分数高度共线（AUC 0.8177），任其比例失衡会让"整体一致率"变成
      hit 分布的产物。层内按 hit 比例分配、每格至少 1 条（若非空）。
    """
    by_type = defaultdict(list)
    for e in rows:
        by_type[e["type"]].append(e)
    rng = random.Random(seed)
    sample, weights = [], {}
    for t in ONTOLOGY:
        pool = by_type[t][:]
        rng.shuffle(pool)
        n_take = min(per_type, len(pool))
        sub = defaultdict(list)
        for e in pool:
            sub[e.get("hit", "none")].append(e)
        # 按 hit 比例配额，非空格至少 1 条，再按比例补足到 n_take
        quota, left = {}, n_take
        for h in ("both", "one", "none"):
            if sub[h] and left > 0:
                quota[h] = 1
                left -= 1
        for h in ("both", "one", "none"):
            if not sub[h] or left <= 0:
                continue
            share = int(round(n_take * len(sub[h]) / len(pool))) - quota.get(h, 0)
            quota[h] = quota.get(h, 0) + max(0, share)
        # 配额可能超/欠，按池大小顺序补齐
        for h in sorted(sub, key=lambda x: -len(sub[x])):
            while sum(quota.values()) < n_take and quota.get(h, 0) < len(sub[h]):
                quota[h] = quota.get(h, 0) + 1
        for h, k in quota.items():
            sample.extend(sub[h][:k])
        weights[t] = len(pool) / max(1, min(n_take, len(pool)))
    return sample, weights


# ──────────────────────────── 推理 ────────────────────────────
def load_model():
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    snap = sorted((Path.home() / ".cache/huggingface/hub").glob(
        "models--Babelscape--mdeberta-v3-base-triplet-critic-xnli/snapshots/*"))[-1]
    dev = ARGS.device if torch.cuda.is_available() else "cpu"
    if dev == "cuda":
        free, total = torch.cuda.mem_get_info()
        print(f"  显存空闲 {free/2**20:.0f}MiB / {total/2**20:.0f}MiB")
        if free < 2500 * 2**20:
            print("  ⚠️ 可用显存偏低，batch=64 可能 OOM；先关掉占显存的程序，"
                  "或改用 --batch 32（速度约慢 3.6 倍）")
    tok = AutoTokenizer.from_pretrained(str(snap))
    model = AutoModelForSequenceClassification.from_pretrained(str(snap))
    model.to(dev).eval()
    i2l = {int(k): str(v) for k, v in model.config.id2label.items()}
    sup = next((i for i, v in i2l.items() if "entail" in v.lower()), None)
    if sup is None:
        sup = next((i for i, v in i2l.items()
                    if v.lower() in ("label_1", "true", "1")), len(i2l) - 1)
    print(f"  {L5_MODEL}  id2label={i2l}  支持类={sup}  device={dev}")
    return model, tok, dev, sup


def score_grid(model, tok, dev, sup, rows):
    """返回 scores[i][j] = P(第 i 条边的 premise 蕴含 第 j 类模板句)。

    未算的格置 None（`--reuse-l5` 时标注类那一格直接取 L5 的分）。
    """
    import torch
    scores = [[None] * NCLS for _ in rows]
    tasks = []
    for i, e in enumerate(rows):
        lab = IDX[e["type"]]
        if ARGS.reuse_l5:
            scores[i][lab] = float(e.get("entail_prob", 0.0))
        for j in range(NCLS):
            if ARGS.reuse_l5 and j == lab:
                continue
            tasks.append((i, j))
    total = len(tasks)
    if total == 0:
        return scores, 0.0
    print(f"\n需要推理 {total} 对（{len(rows)} 边 × {NCLS} 类"
          f"{'，复用 L5 标注类分数' if ARGS.reuse_l5 else ''}）"
          f"  batch={ARGS.batch} max_len={ARGS.max_len}")
    t0 = time.time()
    with torch.no_grad():
        for s in range(0, total, ARGS.batch):
            chunk = tasks[s:s + ARGS.batch]
            P = [rows[i]["premise"] for i, _ in chunk]
            H = [TEMPLATES[ONTOLOGY[j]].format(s=rows[i]["source"],
                                               t=rows[i]["target"])
                 for i, j in chunk]
            enc = tok(P, H, padding=True, truncation=True,
                      max_length=ARGS.max_len, return_tensors="pt").to(dev)
            pr = torch.softmax(model(**enc).logits, dim=-1)[:, sup].tolist()
            for (i, j), p in zip(chunk, pr):
                scores[i][j] = float(p)
            if (s // ARGS.batch) % 50 == 0:
                el = time.time() - t0
                done = min(s + ARGS.batch, total)
                rate = done / el if el else 0
                eta = (total - done) / rate if rate else 0
                print(f"  {done}/{total}  {el:.0f}s  {rate:.0f} 对/秒  ETA {eta/60:.1f}min",
                      flush=True)
    el = time.time() - t0
    print(f"推理完成 {total} 对，{el:.0f}s（{total/el:.1f} 对/秒）")
    return scores, el


# ──────────────────────────── 分析 ────────────────────────────
def build_rows(rows, scores, weights):
    """每条边 → (label, pred, pred2, rank_of_label, s_label, s_max, hit, w)。"""
    out = []
    for e, sc in zip(rows, scores):
        lab = IDX[e["type"]]
        order = sorted(range(NCLS), key=lambda j: -sc[j])
        rank = order.index(lab) + 1
        out.append({
            "paper": e.get("paper"), "source": e.get("source"),
            "target": e.get("target"), "label": e["type"],
            "pred": ONTOLOGY[order[0]], "pred2": ONTOLOGY[order[1]],
            "rank": rank, "s_label": sc[lab], "s_max": sc[order[0]],
            "s_pred2": sc[order[1]], "hit": e.get("hit", "none"),
            "margin": sc[order[0]] - sc[lab],
            # 平局只在「输掉」时才有意义：pred == label 时 s_max == s_label 恒成立，
            # 若不加这个前提，全部「对」都会被算成平局（初版就这样错过一次）。
            "tie": (order[0] != lab) and abs(sc[order[0]] - sc[lab]) <= ARGS.tie_eps,
            "pass_l5": sc[lab] >= L5_THRESHOLD,
            "w": weights.get(e["type"], 1.0),
            "premise": e.get("premise"),
        })
    return out


def wsum(items, key=None):
    return sum((x["w"] if key is None else x["w"] * key(x)) for x in items)


def accuracy(rs, filt=None):
    sub = [r for r in rs if filt is None or filt(r)]
    tot = wsum(sub)
    return (wsum(sub, lambda r: 1.0 if r["pred"] == r["label"] else 0.0) / tot
            if tot else float("nan"))


def macro_f1(rs):
    """15 类宏平均 F1（加权计数）。混淆矩阵行列都用层权还原。"""
    tp = defaultdict(float)
    fp = defaultdict(float)
    fn = defaultdict(float)
    for r in rs:
        if r["pred"] == r["label"]:
            tp[r["label"]] += r["w"]
        else:
            fp[r["pred"]] += r["w"]
            fn[r["label"]] += r["w"]
    f1s = {}
    for c in ONTOLOGY:
        p = tp[c] / (tp[c] + fp[c]) if (tp[c] + fp[c]) else 0.0
        rc = tp[c] / (tp[c] + fn[c]) if (tp[c] + fn[c]) else 0.0
        f1s[c] = 2 * p * rc / (p + rc) if (p + rc) else 0.0
    return statistics.mean(f1s.values()), f1s, tp, fp, fn


def confusion(rs):
    """加权混淆矩阵 cm[label][pred]。"""
    cm = defaultdict(lambda: defaultdict(float))
    for r in rs:
        cm[r["label"]][r["pred"]] += r["w"]
    return cm


def mrr(rs):
    tot = wsum(rs)
    return wsum(rs, lambda r: 1.0 / r["rank"]) / tot if tot else float("nan")


def bootstrap_acc(rs, B, seed):
    """层内 bootstrap：按 label 层内有放回重抽同样条数，重算加权准确率。"""
    layers = defaultdict(list)
    for r in rs:
        layers[r["label"]].append(r)
    rng = random.Random(seed)
    vals = []
    for _ in range(B):
        tot = cor = 0.0
        for lab, pool in layers.items():
            k = len(pool)
            for _ in range(k):
                r = pool[rng.randrange(k)]
                tot += r["w"]
                if r["pred"] == r["label"]:
                    cor += r["w"]
        vals.append(cor / tot if tot else 0.0)
    vals.sort()
    return (vals[int(0.025 * len(vals))], vals[int(0.975 * len(vals)) - 1])


def fmt_confusion(rs):
    """行归一化混淆矩阵（每行 = 该类真实边的去向，对角 = 召回）。"""
    cm = confusion(rs)
    row_tot = {c: sum(cm[c].values()) for c in ONTOLOGY}
    head = "标注\\预测".ljust(14) + "".join(f"{c[:5]:>6}" for c in ONTOLOGY)
    lines = [head, "-" * len(head)]
    for c in ONTOLOGY:
        cells = []
        for p in ONTOLOGY:
            v = cm[c].get(p, 0.0)
            cells.append(f"{v/row_tot[c]*100:5.0f} " if row_tot[c] else "    . ")
        lines.append(f"{c:14s}" + "".join(f"{x:>6}" for x in cells)
                     + f"  n={int(row_tot[c])}")
    return "\n".join(lines), cm


def main():
    t_all = time.time()
    print("=" * 104)
    print("L4 谓词选择层：谓词选对了吗（本地 Triplet Critic，零 API，离线）")
    print("=" * 104)
    print(f"口径 = {'全量' if ARGS.full else ('smoke 每类30' if ARGS.smoke else f'分层抽样 每类{ARGS.per_type}')}"
          f"   模板形态 = {ARGS.form}   复用L5 = {ARGS.reuse_l5}")

    _, rows, n_all, n_out, n_nop = load_edges()
    bad = verify_hypothesis_field(rows)
    if ARGS.reuse_l5 and bad:
        raise SystemExit("hypothesis 字段与模板不一致，不能复用 L5 分数——"
                         "请去掉 --reuse-l5 并对全部 15 类重算")

    if ARGS.limit:
        rows = rows[:ARGS.limit]
    if ARGS.smoke:
        sample, weights = pick_sample(rows, 30, ARGS.seed)
        mode = "smoke"
    elif ARGS.per_type:
        sample, weights = pick_sample(rows, ARGS.per_type, ARGS.seed)
        mode = f"sample-{ARGS.per_type}"
    else:
        sample, weights = rows, {}
        mode = "full"
    print(f"\n本次评测 {len(sample)} 条（{len(rows)} 条有效分母）")
    if weights:
        pat = defaultdict(int)
        for e in sample:
            pat[e["type"]] += 1
        print("  每类条数: " + ", ".join(f"{t}:{pat[t]}" for t in ONTOLOGY))

    model, tok, dev, sup = load_model()
    scores, elapsed = score_grid(model, tok, dev, sup, sample)
    rs = build_rows(sample, scores, weights)

    # ── 主指标 ──
    acc = accuracy(rs)
    acc_both = accuracy(rs, lambda r: r["hit"] == "both")
    mf1, f1s, tp, fp, fn = macro_f1(rs)
    top2 = wsum(rs, lambda r: 1.0 if r["label"] in (r["pred"], r["pred2"]) else 0.0) / wsum(rs)
    print("\n" + "=" * 104)
    print("① 主指标（预测谓词 = 15 类 entail_prob 的 argmax）")
    print("=" * 104)
    print(f"  top-1 一致率（加权）      {acc:.1%}")
    print(f"  top-1 一致率（hit=both）  {acc_both:.1%}   ← 主口径对照：端点齐备时")
    print(f"  top-2 命中率              {top2:.1%}   标注类排在前二的比例")
    print(f"  MRR                       {mrr(rs):.4f}")
    print(f"  宏平均 F1（15 类）        {mf1:.4f}")

    # 平凡基线
    cnt = Counter(r["label"] for r in rs)
    wcnt = defaultdict(float)
    for r in rs:
        wcnt[r["label"]] += r["w"]
    tot_w = sum(wcnt.values())
    mode_cls = max(wcnt, key=wcnt.get)
    print(f"\n  平凡基线（全预测众数类 {mode_cls}）  {wcnt[mode_cls]/tot_w:.1%}")
    print(f"  平凡基线（全预测 RELATED_TO）      {wcnt.get('RELATED_TO',0)/tot_w:.1%}")
    print(f"  随机（按类先验抽）期望              "
          f"{sum((wcnt[c]/tot_w)**2 for c in ONTOLOGY):.1%}")

    # ── 循环/自证风险的量化 ──
    print("\n" + "=" * 104)
    print("② 与 L5 的关系：为什么 L5 通过率 63.7% 与 L4 一致率不矛盾")
    print("=" * 104)
    agree = [r for r in rs if r["pred"] == r["label"]]
    disag = [r for r in rs if r["pred"] != r["label"]]
    A = (wsum(agree, lambda r: 1.0 if r["s_label"] < L5_THRESHOLD else 0.0)
         / wsum(agree)) if agree else float("nan")
    B = (wsum(disag, lambda r: 1.0 if r["s_label"] >= L5_THRESHOLD else 0.0)
         / wsum(disag)) if disag else float("nan")
    C = (wsum(disag, lambda r: 1.0 if r["s_max"] < L5_THRESHOLD else 0.0)
         / wsum(disag)) if disag else float("nan")
    print(f"  标注类分数 ≥{L5_THRESHOLD} 的边占        "
          f"{wsum(rs, lambda r: 1.0 if r['s_label']>=L5_THRESHOLD else 0.0)/wsum(rs):.1%}"
          f"   ← 这就是 L5 的通过率口径")
    print(f"  A 猜对（argmax=标注 但标注类自身 <{L5_THRESHOLD}）  {A:.1%} 的「对」")
    print(f"  B 模板区分度不足（标注类 ≥{L5_THRESHOLD} 却输给别的类）{B:.1%} 的「错」")
    print(f"  C 全部低分（连最高分都 <{L5_THRESHOLD}）              {C:.1%} 的「错」")
    print("  读法：A 高 ⇒ 一致率被噪声抬起来；B 高 ⇒ 多数「错」只是竞争激烈、")
    print("        标注类本身仍被证据支持；C 高 ⇒ 这些边确实无证据可言。")

    # ── 近乎平局：argmax 在打平时谁赢取决于候选集的**顺序**，不是语义 ──
    # 实测大量错例是 标注类 1.000 / 最高分 1.000 这种并列（如 LOCATED_AT 输给
    # PART_OF：「Warsaw → Poland」在 "Warsaw is located at Poland." 与
    # "Warsaw is part of Poland." 上分数完全相同）。不把这一部分单独摘出来，
    # 就会把「argmax 的任意性」误读成「模型判错了谓词」。
    ties = [r for r in rs if r["tie"]]
    TOT = wsum(rs)
    cor_w = wsum([r for r in rs if r["pred"] == r["label"]])
    tie_w = wsum(ties)
    acc_nt = cor_w / (TOT - tie_w) if TOT > tie_w else float("nan")
    mg = sorted(r["margin"] for r in rs)
    print("\n" + "=" * 104)
    print(f"②b 近乎平局（pred≠标注 且 |最高分 − 标注类分| ≤ {ARGS.tie_eps:g}）")
    print("=" * 104)
    print(f"  因近乎平局而判错   {tie_w/TOT:.1%}（加权，{len(ties)}/{len(rs)} 条）")
    print(f"  排除这些后的 top-1 {acc_nt:.1%}"
          f"   上界（把平局也算对）{(cor_w+tie_w)/TOT:.1%}")
    print(f"  margin 分位 p25/p50/p75  "
          f"{mg[int(.25*len(mg))]:.3f} / {mg[int(.50*len(mg))]:.3f} / {mg[int(.75*len(mg))]:.3f}")
    print("  读法：若这一项很低（实测约 0.2%）⇒ **argmax 的任意性不是一致率低的主因**，")
    print("        真正的主因是模板之间语义重叠（见上面 B：标注类 ≥阈值却输给别的类）。")

    # ── hit 分层 ──
    print("\n" + "=" * 104)
    print("③ 按端点命中分层（hit 与分数高度共线，必须分层看）")
    print("=" * 104)
    for h in ("both", "one", "none"):
        sub = [r for r in rs if r["hit"] == h]
        if sub:
            print(f"  hit={h:5s} n={len(sub):6d}  一致率 {accuracy(rs, lambda r, _h=h: r['hit']==_h):.1%}")

    # ── 混淆矩阵 ──
    mat, cm = fmt_confusion(rs)
    print("\n" + "=" * 104)
    print("④ 混淆矩阵（行归一化 %，行为标注谓词、列为预测谓词）")
    print("=" * 104)
    print(mat)

    # ── 每类 P/R/F1 ──
    print("\n" + "=" * 104)
    print("⑤ 每类精确率/召回/F1（加权）")
    print("=" * 104)
    print(f"  {'谓词':16s}{'n':>7}{'召回':>8}{'精确':>8}{'F1':>8}   最常被误判成")
    per_class = []
    for c in ONTOLOGY:
        n = int(sum(cm[c].values()))
        p = tp[c] / (tp[c] + fp[c]) if (tp[c] + fp[c]) else 0.0
        rc = tp[c] / (tp[c] + fn[c]) if (tp[c] + fn[c]) else 0.0
        off = sorted(((k, v) for k, v in cm[c].items() if k != c),
                     key=lambda x: -x[1])
        top_off = f"{off[0][0]}({off[0][1]:.0f})" if off else "—"
        print(f"  {c:16s}{n:7d}{rc:8.1%}{p:8.1%}{f1s[c]:8.3f}   {top_off}")
        per_class.append({"type": c, "n": n, "recall": round(rc, 4),
                          "precision": round(p, 4), "f1": round(f1s[c], 4),
                          "top_confusion": top_off})

    # ── 抽样模式的 CI ──
    ci = None
    if weights:
        lo, hi = bootstrap_acc(rs, ARGS.boot, ARGS.seed)
        ci = (round(lo, 4), round(hi, 4))
        print(f"\n  分层 bootstrap {ARGS.boot} 次：top-1 一致率 95% CI "
              f"[{lo:.1%}, {hi:.1%}]（层内重抽，已按层权还原）")

    # ── 错例样本（供人工核对，不参与指标）──
    print("\n" + "=" * 104)
    print("⑥ 错例样本（预测≠标注 但标注类分数仍高 = 模板区分度不足的实例）")
    print("=" * 104)
    ex = sorted(disag, key=lambda r: -r["s_label"])[:8]
    for r in ex:
        print(f"  标注 {r['label']:14s} 预测 {r['pred']:14s} "
              f"标注类分 {r['s_label']:.4f} / 最高分 {r['s_max']:.4f}  hit={r['hit']}")
        print(f"     {r['source'][:36]} → {r['target'][:36]}")
        print(f"     前提: {(r['premise'] or '')[:110]}")

    out = {
        "source": str(VERIFY), "model": L5_MODEL, "threshold": L5_THRESHOLD,
        "mode": mode, "form": ARGS.form, "reuse_l5": ARGS.reuse_l5,
        "device": dev, "batch": ARGS.batch, "max_len": ARGS.max_len,
        "seed": ARGS.seed, "elapsed_s": round(elapsed, 1),
        "edges_total": n_all, "evaluated": len(sample),
        "outside_ontology": n_out, "no_premise": n_nop,
        "hypothesis_field_mismatch": bad,
        "template_form": ARGS.form,
        "templates": {k: TEMPLATES[k] for k in ONTOLOGY},
        "metrics": {
            "top1_accuracy": round(acc, 4),
            "top1_accuracy_hit_both": round(acc_both, 4),
            "top1_accuracy_excluding_ties": round(acc_nt, 4),
            "top1_accuracy_ties_as_correct": round((cor_w + tie_w) / TOT, 4),
            "tie_lost_share": round(tie_w / TOT, 4),
            "tie_eps": ARGS.tie_eps,
            "margin_p50": round(mg[int(.50 * len(mg))], 4),
            "top2_hit_rate": round(top2, 4),
            "mrr": round(mrr(rs), 4),
            "macro_f1": round(mf1, 4),
            "baseline_mode_class": {"class": mode_cls,
                                    "accuracy": round(wcnt[mode_cls]/tot_w, 4)},
            "baseline_related_to": round(wcnt.get("RELATED_TO", 0)/tot_w, 4),
            "bootstrap_ci95": ci,
        },
        "l5_cross": {
            "label_class_pass_rate": round(
                wsum(rs, lambda r: 1.0 if r["s_label"] >= L5_THRESHOLD else 0.0)
                / wsum(rs), 4),
            "A_guessed_right_but_below_thr": round(A, 4),
            "B_label_supported_but_lost": round(B, 4),
            "C_all_below_thr": round(C, 4),
        },
        "by_hit": {h: {"n": len([r for r in rs if r["hit"] == h]),
                       "accuracy": round(accuracy(rs, lambda r, _h=h: r["hit"] == _h), 4)}
                   for h in ("both", "one", "none")
                   if [r for r in rs if r["hit"] == h]},
        "per_class": per_class,
        "confusion": {c: {p: round(v, 1) for p, v in cm[c].items()} for c in ONTOLOGY},
        "note": "top-1 一致率是「与抽取器标注谓词的一致率」，不是与人工金标的准确率；"
                "标注谓词由 LLM 抽取器产生且当时未给谓词定义，故本指标是两个模型的"
                "一致率（机器版 inter-annotator agreement），论文里必须这样写。",
    }
    # 全量与抽样分开落盘：口径不同，混在一个文件里会被后来者误当成同一件事
    # （项目已有 relation-verify-all.json / relation-verify-sample.json 的惯例）。
    out_path = ROOT / "data" / "quality" / (
        "l4-predicate.json" if mode == "full" else f"l4-predicate-{mode}.json")
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写出 {out_path}  （总耗时 {time.time()-t_all:.0f}s）")
    if mode != "full":
        print("  注意：本文件是**抽样**口径，权威数字应来自 --full 的 l4-predicate.json")

if __name__ == "__main__":
    main()
