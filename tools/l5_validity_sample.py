# -*- coding: utf-8 -*-
"""L5 域内效度抽检 · 第一步：分层抽样，产出**盲判清单**。

【要回答什么问题】
  现有 L5 = 63.7%（27954/43867）是判别器在自己数据上的**通过率**，
  它的阈值来自**官方英文校准集**，而那个校准集与本项目数据分布不同
  （见 tools/l5_validity_probe.py 的实测）。所以 63.7% 从未在本项目分布上被验证过。
  本脚本抽样，供人工盲判，从而得到本项目分布上的**真实支持率**与
  判别器的**精确率 / 召回率 / AUC**。

【分层设计（这是全案的关键，别改）】
  两个分层变量：

    A. entail_prob 分箱（5 箱）
       [0.00,0.40) [0.40,0.60) [0.60,0.67) [0.67,0.80) [0.80,1.00)
       —— 0.5 / 0.6 / 0.67 / 0.75 / 0.8 五个待考察阈值**全部落在箱边界上**，
          所以同一份人工标签可以**重算任意阈值的性能**，不需要重新抽样。
          （这是本设计相对「按 pass/fail 分层」的决定性优势：按 pass 分层只有
            阈值两侧的两层，换阈值就得重抽。）

    B. hit ∈ {both, one, none}（端点字面命中数：2 / 1 / 0）
       —— 实测 entail_prob 与 hit 高度共线（用 hit 区分 both/none 的 AUC = 0.8177），
          所以它是必须控制的分层变量，否则抽样会被端点缺失程度带偏。
          三个水平都保留、不合并：`none` 层正是判别器出假阳性的地方，
          合并进 `one` 会把最有信息量的层冲淡。

  共 5 × 3 = 15 层。

  层内用固定种子随机抽样；层权重 w_h = N_h / n_h 用于还原总体。

【谁判、怎么保证不循环】
  判定者**不能**是判别器本身。见 docs/L5_VALIDITY_PLAN.md §3：
  严格效度只能来自人工盲判，本脚本产出的清单就是为了这件事。
  清单**不含** entail_prob / pass / hit —— 这三个字段另存 key 文件，
  盲判界面（web/l5-review.html）只读清单、不读 key。

用法：
  python tools/l5_validity_sample.py                 # 默认 200 条
  python tools/l5_validity_sample.py --n 300 --seed 20260912
"""
# 路径以本文件为锚点解析，不写死盘符——演示机不一定把仓库放在 D 盘。
from pathlib import Path as _Path
_HERE = _Path(__file__).resolve()
ROOT = _HERE.parent.parent          # tools/*.py 与 src/*.py 都在根目录下一层
import argparse
import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path

VERIFY = ROOT / "data/quality/relation-verify-all.json"
OUT_DIR = ROOT / "data/quality"
SHEET = OUT_DIR / "l5-validity-sample.jsonl"
KEY = OUT_DIR / "l5-validity-key.json"
DESIGN = OUT_DIR / "l5-validity-design.json"

BINS = [0.00, 0.40, 0.60, 0.67, 0.80, 1.0001]
SEC_PER_ITEM = 20   # 单条盲判的保守耗时估计（含可能打开 PDF）

# 关系类型的中文释义，给标注者看的（只做提示，不改变判据）
TYPE_ZH = {
    "PART_OF": "是……的一部分", "USES": "使用", "PRODUCES": "产出",
    "MEASURES": "度量", "EVALUATES": "评估", "COMPARES_WITH": "与……比较",
    "SUPPORTS": "支持", "CONTRADICTS": "与……矛盾", "CAUSES": "导致",
    "ENABLES": "使能", "APPLIES_TO": "应用于", "LOCATED_AT": "位于",
    "CREATED_BY": "由……创作（注意方向：作者 → 作品）", "CITES": "引用",
    "RELATED_TO": "与……相关",
}


def bin_of(p: float) -> int:
    for i in range(len(BINS) - 1):
        if BINS[i] <= p < BINS[i + 1]:
            return i
    return len(BINS) - 2


def hit_group(e) -> str:
    h = e.get("hit")
    return h if h in ("both", "one", "none") else "none"


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=200, help="总样本量")
    ap.add_argument("--floor", type=int, default=0,
                    help="每层最低配额；0 = 自动取 min(8, n/层数)")
    ap.add_argument("--mode", choices=("prop", "equal"), default="prop",
                    help="prop=按层大小比例分配（默认，估总体率方差最小）；"
                         "equal=各层等量（比层间差异更准，但总体率的设计效应差）")
    ap.add_argument("--seed", type=int, default=20260912)
    return ap.parse_args()


def main():
    A = parse_args()
    data = json.loads(VERIFY.read_text(encoding="utf-8"))
    edges = data["edges"]
    N = len(edges)
    print(f"=== L5 域内效度抽检 · 分层抽样 ===\n总体 {N} 条（阈值 {data['threshold']}，"
          f"官方校准 AUC {data['calib_roc_auc']}）")

    cells = defaultdict(list)
    for e in edges:
        cells[(bin_of(e["entail_prob"]), hit_group(e))].append(e)

    keys = [(i, g) for i in range(len(BINS) - 1) for g in ("both", "one", "none")]
    Ns = {k: len(cells[k]) for k in keys}

    # ---- 分配 ----
    nstrata = sum(1 for k in keys if Ns[k] > 0)
    floor = A.floor or max(1, min(3, A.n // max(nstrata, 1)))
    if A.mode == "equal":
        base = {k: (A.n // nstrata if Ns[k] else 0) for k in keys}
    else:
        base = {k: (max(floor, int(round(A.n * Ns[k] / N))) if Ns[k] else 0) for k in keys}
    base = {k: min(v, Ns[k]) for k, v in base.items()}
    rem = A.n - sum(base.values())
    if rem < 0:
        # 下限把总量顶超了：从「相对比例分得最多」的层里回收
        while rem < 0:
            cand = [k for k in keys if base[k] > 1]
            if not cand:
                raise SystemExit(f"--n={A.n} 太小：{nstrata} 层每层至少要 1 条")
            k = max(cand, key=lambda k: base[k] - A.n * Ns[k] / N)
            base[k] -= 1
            rem += 1
    tot = sum(Ns.values())
    while rem > 0:
        k = max(keys, key=lambda k: Ns[k] / (base[k] + 1))
        if base[k] >= Ns[k]:
            break
        base[k] += 1
        rem -= 1
    nh = base

    print(f"\n{'score bin':>16} {'hit':>6} {'N_h':>7} {'n_h':>5} {'w_h':>8}")
    for i in range(len(BINS) - 1):
        for g in ("both", "one", "none"):
            k = (i, g)
            if Ns[k] == 0:
                continue
            print(f"  [{BINS[i]:.2f},{BINS[i+1]:.2f}) {g:>6} {Ns[k]:7d} {nh[k]:5d} "
                  f"{Ns[k]/max(nh[k],1):8.1f}")

    # ---- 抽样 ----
    rnd = random.Random(A.seed)
    picked = []
    for k in keys:
        if nh[k] <= 0 or not cells[k]:
            continue
        picked.extend(rnd.sample(cells[k], min(nh[k], len(cells[k]))))
    rnd.shuffle(picked)   # 打乱顺序，避免同层连续出现造成锚定

    sheet, keymap = [], {}
    for idx, e in enumerate(picked, 1):
        sid = f"V{idx:04d}"
        sheet.append({
            "id": sid,
            "paper": e["paper"],
            "page": e.get("page"),
            "source": e["source"],
            "target": e["target"],
            "type": e["type"],
            "type_zh": TYPE_ZH.get(e["type"], "（本体外类型，需特别留意）"),
            "hypothesis": e["hypothesis"],
            "premise": e["premise"],
            "evidence": e.get("evidence") or "",
        })
        keymap[sid] = {
            "entail_prob": e["entail_prob"], "pass": bool(e["pass"]),
            "hit": e["hit"], "type": e["type"],
            "stratum": f"{bin_of(e['entail_prob'])}|{hit_group(e)}",
        }

    # ---- 设计摘要：预期精度 + 隐藏探针 ----
    probe = [s["id"] for s in sheet if keymap[s["id"]]["hit"] == "none"]
    asy = {"PART_OF", "USES", "PRODUCES", "MEASURES", "EVALUATES", "COMPARES_WITH",
           "SUPPORTS", "CONTRADICTS", "CAUSES", "ENABLES", "APPLIES_TO", "LOCATED_AT",
           "CREATED_BY", "CITES"}
    import re
    nk = lambda x: re.sub(r"[^a-z0-9]+", "", (x or "").lower())
    selfloop = [s["id"] for s in sheet
                if nk(s["source"]) == nk(s["target"]) and nk(s["source"])]

    n = len(sheet)
    hw_srs = 1.96 * math.sqrt(0.64 * 0.36 / n)
    pass_ids = [s["id"] for s in sheet if keymap[s["id"]]["pass"]]
    print(f"\n抽出 {n} 条（其中判别器判通过 {len(pass_ids)} 条）")
    print(f"  · 隐藏探针 A（hit=none，前提里两个端点都没出现）: {len(probe)} 条")
    print(f"    期望：人工几乎全判「不支持」。若「支持」≥3 条 ⇒ 本轮标注本身不可信。")
    print(f"  · 隐藏探针 B（自环 source==target）: {len(selfloop)} 条")
    print(f"  · 简单随机抽样同规模下的 95% CI 半宽 ≈ ±{hw_srs*100:.1f}pp")
    print(f"  · 预计盲判耗时 ≈ {n*SEC_PER_ITEM/60:.0f} 分钟（按 {SEC_PER_ITEM}s/条）")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    SHEET.write_text("\n".join(json.dumps(s, ensure_ascii=False) for s in sheet),
                     encoding="utf-8")
    KEY.write_text(json.dumps(keymap, ensure_ascii=False, indent=2), encoding="utf-8")
    DESIGN.write_text(json.dumps({
        "seed": A.seed, "n": n, "population": N,
        "bins": BINS, "hit_group": "both | notboth",
        "strata": {f"{k[0]}|{k[1]}": {"N_h": Ns[k], "n_h": nh[k],
                                      "w_h": round(Ns[k] / nh[k], 4) if nh[k] else None}
                   for k in keys},
        "pass_strata": [f"{k[0]}|{k[1]}" for k in keys if k[0] >= 3],
        "probe_hit_none": probe, "probe_selfloop": selfloop,
        "expected_ci_halfwidth_srs": round(hw_srs, 4),
        "est_annotation_minutes": round(n * SEC_PER_ITEM / 60, 1),
        "sheet": str(SHEET), "key": str(KEY),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n盲判清单: {SHEET}\n隐藏 key : {KEY}\n设计摘要 : {DESIGN}")
    print(f"\n下一步: python tools/l5_validity_review.py  然后浏览器打开它打印的地址")


if __name__ == "__main__":
    main()
