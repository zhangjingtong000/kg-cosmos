# -*- coding: utf-8 -*-
r"""L16 · **立场聚合视图**（把支持/反对聚合成一个立场标签）。

## TODO 的原话

> | **L16** | ⬜ **立场聚合视图** |
> **Consensus `Consensus Meter`**：把 top 20 结论聚合为
> **Yes / No / Possibly / Mixed** |
> 我们**有支持/反对的材料，但没有"归纳成一句话立场"**。
> 这是主要看点的直接补强 ★★

## 数据从哪来（**不调模型**）

图里**本来就有**这两类关系：
```
SUPPORTS     797 条    [Survey] → [Knowledge Graph Completion]
CONTRADICTS  243 条    [TransE]  → [Many-to-1 Relationships]
```
⇒ **`source` 是"谁支持/反对"，`target` 是"被支持/反对的那个主张"**（实测确认）。
⇒ 所以"立场"可以**直接从图上数出来**，不需要再调一次模型。

**★ 这是刻意的**：本项目有一条纪律 ——
**不用 LLM 当裁判**（四个模型标注者对关系层的一致性 α 是**负的**，
用一个 α 为负的裁判打分没有意义）。
⇒ 立场标签**必须由可核对的计数得出**，而不是"再问一次模型"。

## 判据（**机械、可核对、可复算**）

```
S = 支持数（指向该主张的 SUPPORTS 边）
O = 反对数（指向该主张的 CONTRADICTS 边）
T = S + O

T < MIN_EVIDENCE            ⇒ Possibly（**证据太少**）
O / T >= MIXED_RATIO        ⇒ Mixed（**争议明显**）
S / T >= SIDED_RATIO        ⇒ Yes
O / T >= SIDED_RATIO        ⇒ No
其余                         ⇒ Mixed（两边都有，但没到阈值）
```

阈值是**拍的**，所以：
1. **每个标签都同时给出 S/O/T** —— 让人能自己判断，而不是只信一个词；
2. **页面上写明阈值** —— 改一个数会改哪些标签，读者能自己推。

用法：
    python tools/build_stance_data.py
    python tools/build_stance_data.py --top 30
"""
import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))

OUT = ROOT / 'web' / 'kg-data' / 'stance.json'
GRAPHS = ROOT / 'data' / 'quality' / 'graphs-published-in'
GRAPHS_FALLBACK = ROOT / 'data' / 'corpus' / 'graphs'

# ── 阈值（**拍下来的，所以要在产物与页面上都写明**）──────────────────
MIN_EVIDENCE = 3      # 少于这么多条边 ⇒ Possibly（证据不足）
MIXED_RATIO = 0.34    # 反对占 T 的比例 >= 这个 ⇒ Mixed（争议明显）
SIDED_RATIO = 0.67    # 一侧占 T 的比例 >= 这个 ⇒ Yes / No
TOP = 20


def parse_args():
    ap = argparse.ArgumentParser(description='L16 立场聚合')
    ap.add_argument('--top', type=int, default=TOP)
    return ap.parse_args()


def main():
    a = parse_args()
    t0 = time.time()
    gdir = GRAPHS if GRAPHS.exists() and any(GRAPHS.glob('*.json')) else GRAPHS_FALLBACK
    print('=' * 92)
    print('L16 · 立场聚合（把支持/反对聚成一个标签）')
    print('=' * 92)
    print(f'  图源：{gdir.relative_to(ROOT)}')
    print(f'  阈值：T < {MIN_EVIDENCE} ⇒ Possibly · O/T >= {MIXED_RATIO} ⇒ Mixed · '
          f'一侧 >= {SIDED_RATIO} ⇒ Yes/No')

    sup = defaultdict(list)      # 主张 → 支持者 list
    con = defaultdict(list)      # 主张 → 反对者 list
    n_sup = n_con = 0
    for f in sorted(gdir.glob('*.json')):
        try:
            d = json.loads(f.read_text(encoding='utf-8'))
        except Exception:
            continue
        for e in (d.get('edges') or []):
            t = str(e.get('type') or '').upper()
            if t not in ('SUPPORTS', 'CONTRADICTS'):
                continue
            claim = str(e.get('target') or '').strip()
            who = str(e.get('source') or '').strip()
            if not claim or not who:
                continue
            ev = (e.get('evidence') or [{}])
            page = next((x.get('page') for x in ev if x.get('page')), None)
            rec = {'who': who[:70], 'paper': f.stem, 'page': page}
            if t == 'SUPPORTS':
                sup[claim].append(rec)
                n_sup += 1
            else:
                con[claim].append(rec)
                n_con += 1
    print(f'\n  SUPPORTS {n_sup} 条 · CONTRADICTS {n_con} 条')
    print(f'  涉及 {len(set(sup) | set(con))} 个不同的"主张"')

    claims = []
    for claim in set(sup) | set(con):
        S, O = len(sup.get(claim) or []), len(con.get(claim) or [])
        T = S + O
        if T < MIN_EVIDENCE:
            verdict = 'Possibly'
        elif O / T >= MIXED_RATIO:
            verdict = 'Mixed'
        elif S / T >= SIDED_RATIO:
            verdict = 'Yes'
        elif O / T >= SIDED_RATIO:
            verdict = 'No'
        else:
            verdict = 'Mixed'
        claims.append({
            'claim': claim, 'S': S, 'O': O, 'T': T, 'verdict': verdict,
            # 每条都带出处 ⇒ 立场**可回溯**
            'supporters': (sup.get(claim) or [])[:12],
            'opponents': (con.get(claim) or [])[:12],
        })
    claims.sort(key=lambda x: (-x['T'], -x['O'], x['claim']))
    top = claims[:a.top]

    print(f'\n  ★ top {len(top)} 个主张（按"总证据量"排）')
    for c in top:
        bar = '支持' + '█' * min(20, c['S']) + f' {c["S"]}'
        bar2 = '反对' + '█' * min(20, c['O']) + f' {c["O"]}'
        print(f'    [{c["verdict"]:<8}] {c["claim"][:44]:<46} {bar} · {bar2}')

    dist = defaultdict(int)
    for c in claims:
        dist[c['verdict']] += 1
    print(f'\n  ★ 全部 {len(claims)} 个主张的立场分布')
    for k in ('Yes', 'No', 'Mixed', 'Possibly'):
        print(f'      {k:<9} {dist.get(k, 0):>3}')

    payload = {
        'generated': time.strftime('%Y-%m-%d %H:%M:%S'),
        'purpose': 'L16：把支持/反对聚合成一个立场标签（对标 Consensus Meter）',
        'design': {
            'no_llm_judge':
                '**立场不调模型，直接从图上数** —— 本项目有一条纪律：'
                '**不用 LLM 当裁判**（四个模型标注者对关系层的一致性 α 是负的，'
                '用一个 α 为负的裁判打分没有意义）。'
                '⇒ 标签必须由**可核对的计数**得出。',
            'data_is_already_there':
                '图里本来就有 `SUPPORTS` 与 `CONTRADICTS`；'
                '**`source` 是"谁支持/反对"，`target` 是"被支持/反对的那个主张"**（实测确认）。',
            'thresholds_are_guessed':
                f'阈值是**拍的**：T < {MIN_EVIDENCE} ⇒ Possibly；'
                f'O/T >= {MIXED_RATIO} ⇒ Mixed；一侧 >= {SIDED_RATIO} ⇒ Yes/No。'
                '⇒ 所以**每个标签都同时给出 S/O/T**，让人能自己判断，而不是只信一个词。',
            'every_claim_traceable':
                '每个主张都带支持者/反对者的**论文名与页码** ⇒ 立场**可回溯到原文**。',
        },
        'thresholds': {'min_evidence': MIN_EVIDENCE,
                       'mixed_ratio': MIXED_RATIO, 'sided_ratio': SIDED_RATIO},
        'counts': {'suports_edges': n_sup, 'contradicts_edges': n_con,
                   'claims_total': len(claims)},
        'distribution': {k: dist.get(k, 0)
                         for k in ('Yes', 'No', 'Mixed', 'Possibly')},
        'claims': top,
        'elapsed_s': round(time.time() - t0, 1),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(',', ':')),
                   encoding='utf-8')
    print(f'\n  已写出 {OUT.relative_to(ROOT)}  '
          f'({OUT.stat().st_size / 1024:.0f} KB · {time.time() - t0:.1f}s)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
