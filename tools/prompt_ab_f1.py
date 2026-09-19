# -*- coding: utf-8 -*-
r"""B5 的最后一步：**用金标口径给 6 次 A/B 抽取各算一次 F1**。

## 为什么单独写这个（而不是改 `prompt_ab_multiseed.py`）

`prompt_ab_multiseed.py` 的收尾提示写的是「本脚本刻意不做」这一步：

> ★ 下一步（**本脚本刻意不做**）：用 `l5_report` 口径重算每次的 F1

而 `tools/l5_report.py` 读的是**写死路径**（`data/quality/relation-verify-all.json`
+ `data/corpus/graphs`），**不接受输入图** ⇒ 那句话指的是「**口径要一致**」，
不是「去调用那个脚本」。本脚本因此做两件事：

1. **原样 import `gold_eval` 的判据**（`gold_sets` / `relation_golds` / `prf` / `fold`）
   —— 口径是**借来的**，不是又抄一份；
2. 只把「读哪个文件」这一段换成 A/B 的产物
   （`_prompt-ab-runs/{old,new}-s{0,1,2}.json`）。

`gold_eval.load_channel` 的 `.json` 分支读的正是 `nodes[].name/type` 与
`edges[].source/target/type`，与 A/B 产物**结构一致** —— 所以能直接对上。

## 判据（来自 `prompt_ab_multiseed.py` 的 docstring）

```
若 新 prompt 的 3 个 seed 的 F1 全都 < 旧 prompt 的 3 个 seed 的最小值
   ⇒ 效应大于噪声（区间不重叠）
若两者区间大幅重叠
   ⇒ 那 4.5pp 很可能只是 seed 噪声
```

用法：
    python tools/prompt_ab_f1.py
    python tools/prompt_ab_f1.py --json
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'src'))

from gold_eval import fold, gold_sets, load_gold, prf, relation_golds  # noqa: E402
from entity_filter import filter_entities  # noqa: E402
from entity_absorb import find_absorptions  # noqa: E402

RUNS = ROOT / 'data' / 'quality' / '_prompt-ab-runs'
PAPER = '2012.00456v1'
VARIANTS = ('old', 'new')
SEEDS = (0, 1, 2)
CALIBERS = ('strict', 'mid', 'union')


def name_of(n):
    return (n.get('name') or n.get('id') or '').strip()


def load_graph(p):
    """读一个 A/B 产物 → (原始节点 list, 实体名→类型, 关系三元组集合)。

    ⚠️ 归一判据必须与 `gold_eval.load_channel` 完全一致，否则两边不可比。
    原始节点 list 单独留着，是为了下面能用**生产同款后处理**再算一档。
    """
    d = json.loads(Path(p).read_text(encoding='utf-8'))
    nodes = [n for n in (d.get('nodes') or d.get('entities') or []) if name_of(n)]
    ents = {}
    for n in nodes:
        ents.setdefault(fold(name_of(n)), n.get('type'))
    rels = set()
    for e in (d.get('edges') or d.get('relations') or []):
        s, t, ty = e.get('source'), e.get('target'), e.get('type')
        if s and t and ty:
            rels.add((fold(s), str(ty).upper(), fold(t)))
    return nodes, ents, rels


def prod_entity_set(nodes):
    """跑**与 `_exp_prompt_ab.py` 完全同一条**后处理：过滤 → 吸收 → 去被吸收者。

    为什么必须再算这一档：`OPTIMIZATION_PLAN.md` 的「实体 F1 0.3918」是
    **过滤后**的数，而 A/B 产物里存的是**未过滤**的原始节点（553–592 个）。
    两个口径差出来的是「后处理」而非「prompt」，混着引用就是拿苹果比橘子。
    """
    kept, dropped = filter_entities(nodes)
    find_absorptions(kept)          # 原地给 kept 的元素打 absorbed_into 标记
    absorbed = {name_of(n) for n in kept if n.get('absorbed_into')}
    names = [name_of(n) for n in kept if name_of(n) not in absorbed]
    return {fold(x) for x in names if fold(x)}, len(dropped), len(absorbed)


def score(p):
    g = load_gold(PAPER)
    ge, _gr = gold_sets(g)
    nodes, ce_raw, cr = load_graph(p)

    def ef1(ce):
        tp = len(set(ge) & ce)
        return prf(tp, len(ce - set(ge)), len(set(ge) - ce))

    pe, re_, fe = ef1(set(ce_raw))
    ce_prod, n_drop, n_absorb = prod_entity_set(nodes)
    pp, rp, fp_ = ef1(ce_prod)

    rgs = relation_golds(PAPER) or {c: set() for c in CALIBERS}
    rel = {}
    for cal in CALIBERS:
        gset = rgs.get(cal, set())
        t = len(gset & cr)
        rel[cal] = {'F1': prf(t, len(cr - gset), len(gset - cr))[2],
                    'gold_n': len(gset)}

    return {'file': Path(p).name,
            'n_raw_e': len(ce_raw), 'n_prod_e': len(ce_prod),
            'n_dropped': n_drop, 'n_absorbed': n_absorb, 'n_pred_r': len(cr),
            'entity_raw': {'P': pe, 'R': re_, 'F1': fe, 'gold_n': len(ge)},
            'entity_prod': {'P': pp, 'R': rp, 'F1': fp_, 'gold_n': len(ge)},
            'relation': rel}


def spread(vals):
    return {'mean': round(sum(vals) / len(vals), 4), 'min': min(vals), 'max': max(vals)}


def interval_relation(o, n):
    """两组的区间关系。

    ⚠️ **方向要分别判**：实体 F1 的假设是"new 更低"（−4.5pp），
    而关系 F1 的假设是"new 更高"（+39%）。只判一个方向会把另一半读反。
    """
    if n['max'] < o['min']:
        return '★ 区间不重叠（new 更低）'
    if n['min'] > o['max']:
        return '★ 区间不重叠（new 更高）'
    return '区间重叠'


def main():
    ap = argparse.ArgumentParser(description='B5：用金标口径算 A/B 每次的 F1')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()

    per = {}
    for v in VARIANTS:
        for s in SEEDS:
            p = RUNS / f'{v}-s{s}.json'
            if not p.exists() or p.stat().st_size == 0:
                per[f'{v}-s{s}'] = None
                continue
            per[f'{v}-s{s}'] = score(p)

    done = {k: v for k, v in per.items() if v}

    if a.json:
        out = {'paper': PAPER, 'per_run': per}
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return

    g = load_gold(PAPER)
    ge, _ = gold_sets(g)
    rgs = relation_golds(PAPER) or {}
    print(f'B5 · prompt A/B 的金标 F1（单篇 {PAPER}）')
    print('  变体：old = 裸类型名 / new = 每类附中文语义定义（两者都是 KG_EXTRACT_STYLE=combined）')
    print(f'  金标规模：实体 {len(ge)}；关系 '
          + ' / '.join(f'{c} {len(rgs.get(c, set()))}' for c in CALIBERS))
    print(f'  已有产物：{len(done)}/6'
          + ('' if len(done) == 6 else f'（缺：{", ".join(k for k, v in per.items() if not v)}）'))
    print()
    print('  run       原始F1  生产F1 | 关系 strict  mid    union  | 实体 原始→生产(丢/吸) 关系')
    print('  ' + '-' * 94)
    for k, v in per.items():
        if not v:
            print(f'  {k:<9} （未产出）')
            continue
        r = v['relation']
        print(f'  {k:<9} {v["entity_raw"]["F1"]:<7} {v["entity_prod"]["F1"]:<7}| '
              f'{r["strict"]["F1"]:<12} {r["mid"]["F1"]:<6} {r["union"]["F1"]:<6} | '
              f'{v["n_raw_e"]}→{v["n_prod_e"]}(丢{v["n_dropped"]}/吸{v["n_absorbed"]}) '
              f'{v["n_pred_r"]}')

    print()
    summary = {}
    for v in VARIANTS:
        got = [per[f'{v}-s{s}'] for s in SEEDS if per.get(f'{v}-s{s}')]
        if not got:
            continue
        summary[v] = {
            'entity_raw': spread([x['entity_raw']['F1'] for x in got]),
            'entity_prod': spread([x['entity_prod']['F1'] for x in got]),
            'rel_strict': spread([x['relation']['strict']['F1'] for x in got]),
            'rel_mid': spread([x['relation']['mid']['F1'] for x in got]),
            'rel_union': spread([x['relation']['union']['F1'] for x in got]),
            'n_runs': len(got),
        }

    if len(summary) == 2:
        o, n = summary['old'], summary['new']
        print('  分组（均值 · [min, max]，各 3 个 seed）：')
        for label, key in (
                ('实体 F1（原始图）', 'entity_raw'),
                ('实体 F1（生产后处理）', 'entity_prod'),
                ('关系 F1 strict', 'rel_strict'),
                ('关系 F1 mid', 'rel_mid'),
                ('关系 F1 union', 'rel_union')):
            so, sn = o[key], n[key]
            print(f'    {label:<22} old {so["mean"]:.4f} [{so["min"]:.4f}, {so["max"]:.4f}]  '
                  f'new {sn["mean"]:.4f} [{sn["min"]:.4f}, {sn["max"]:.4f}]   → '
                  f'{interval_relation(so, sn)}')
        print()
        print('  ★ 判据（`prompt_ab_multiseed.py` docstring）：'
              'new 的 3 个 seed 全落在 old 区间之外 ⇒ 效应大于噪声。')
        rp = interval_relation(o['entity_prod'], n['entity_prod'])
        print('    ⇒ 实体 F1 的 −4.5pp（生产口径）：'
              + ('**效应大于噪声，原结论成立**' if rp == '★ 区间不重叠（new 更低）'
                 else '**区间重叠 ⇒ 落在 seed 噪声内，原结论不成立**'))


if __name__ == '__main__':
    main()
