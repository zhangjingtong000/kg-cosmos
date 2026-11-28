"""实验：哪种"证据过滤"策略真的有效？

【为什么做这个】
  L5 现在的用法是：`pass`（entail_prob ≥ 0.67）为真就留下，否则丢弃。
  200 条独立盲判实测：这个策略的 **PPV 只有 49.7%** ——
  即留下的 27954 条里约一半不被原文支持。

  也就是说「证据过滤」这个功能**当前是坏的**：
  它滤掉了一半真阳性，又放进了一半假阳性。
  这不是"待完善"，是"没做对"。

  好在这件事可以零成本实验：200 条标签 + key（含 entail_prob / hit）
  都在本地，各种组合策略的 PPV / 召回 / F1 都能直接算。
  如果组合信号能显著提升 PPV，那"把功能做对"只是换一下判定条件；
  如果不能，就得诚实降级这个功能的定位。

【口径】
  · 加权：分层抽样必须带权（层权重 w_h = N_h/n_h），否则比例是错的；
  · "支持" = 人工判 support；"说不清"按不支持算（与 report 脚本的次口径一致）；
  · PPV = 留下里真被支持的比例；召回 = 真被支持里被留下的比例。
"""
import json
from pathlib import Path

# 路径以本文件为锚点解析，不写死盘符——演示机不一定把仓库放在 D 盘。
_HERE = Path(__file__).resolve()
ROOT = _HERE.parent.parent          # tools/*.py 都在根目录下一层
Q = ROOT / 'data' / 'quality'


def load():
    sheet = [json.loads(l) for l in
             (Q / 'l5-validity-sample.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()]
    key = json.loads((Q / 'l5-validity-key.json').read_text(encoding='utf-8'))
    labels = {}
    for l in (Q / 'l5-validity-labels.jsonl').read_text(encoding='utf-8').splitlines():
        if l.strip():
            r = json.loads(l)
            labels[r['id']] = r['verdict']
    design = json.loads((Q / 'l5-validity-design.json').read_text(encoding='utf-8'))
    strata = design.get('strata') or {}
    Ns = {k: v['N_h'] for k, v in strata.items()}
    nh = {k: v['n_h'] for k, v in strata.items()}
    rows = []
    for s in sheet:
        i = s['id']
        k = key.get(i)
        if not k:
            continue
        h = k['stratum']
        w = Ns.get(h, 0) / nh[h] if nh.get(h) else 0
        rows.append({
            'id': i,
            'p': k['entail_prob'],
            'pass': bool(k.get('pass')),
            'hit': k.get('hit'),
            'y': 1 if labels.get(i) == 'support' else 0,
            'w': w,
        })
    return rows


def ev(rows, pred, name):
    """按权重算策略 pred 的 PPV / 召回 / F1 / 覆盖率。"""
    tot_w = sum(r['w'] for r in rows)
    pos_w = sum(r['w'] for r in rows if r['y'] == 1)
    kept = [r for r in rows if pred(r)]
    keep_w = sum(r['w'] for r in kept)
    tp_w = sum(r['w'] for r in kept if r['y'] == 1)
    ppv = tp_w / keep_w if keep_w else 0
    rec = tp_w / pos_w if pos_w else 0
    f1 = 2 * ppv * rec / (ppv + rec) if (ppv + rec) else 0
    return {'name': name, 'keep': keep_w / tot_w if tot_w else 0,
            'ppv': ppv, 'recall': rec, 'f1': f1, 'n_kept': len(kept)}


def main():
    rows = load()
    print(f'样本 {len(rows)} 条（带分层权重）\n')

    STRATS = [
        ('A 现行：pass', lambda r: r['pass']),
        ('B 纯规则：hit=both', lambda r: r['hit'] == 'both'),
        ('C pass 且 hit=both', lambda r: r['pass'] and r['hit'] == 'both'),
        ('D pass 且 hit≠none', lambda r: r['pass'] and r['hit'] != 'none'),
        ('E hit=both 或 (pass 且 p≥0.8)', lambda r: r['hit'] == 'both' or (r['pass'] and r['p'] >= 0.8)),
        ('F p≥0.8 且 hit=both', lambda r: r['p'] >= 0.8 and r['hit'] == 'both'),
        ('G p≥0.9 且 hit=both', lambda r: r['p'] >= 0.9 and r['hit'] == 'both'),
        ('H hit=both 或 p≥0.9', lambda r: r['hit'] == 'both' or r['p'] >= 0.9),
        ('I p≥0.8 且 hit≠none', lambda r: r['p'] >= 0.8 and r['hit'] != 'none'),
        ('J 全留（不过滤）', lambda r: True),
    ]
    res = [ev(rows, f, n) for n, f in STRATS]
    print(f'{"策略":32s} {"覆盖":>7s} {"PPV":>7s} {"召回":>7s} {"F1":>7s}')
    print('-' * 68)
    for r in res:
        print(f'{r["name"]:32s} {r["keep"]*100:6.1f}% {r["ppv"]*100:6.1f}% '
              f'{r["recall"]*100:6.1f}% {r["f1"]:6.3f}')

    base = res[0]
    print(f'\n对照基准（现行 A）：PPV {base["ppv"]*100:.1f}%  召回 {base["recall"]*100:.1f}%  '
          f'F1 {base["f1"]:.3f}')
    # 按 F1 找最优
    best = max(res, key=lambda r: r['f1'])
    print(f'按 F1 最优：{best["name"]}  PPV {best["ppv"]*100:.1f}%  '
          f'召回 {best["recall"]*100:.1f}%  F1 {best["f1"]:.3f}  覆盖 {best["keep"]*100:.1f}%')
    print()
    if best['f1'] > base['f1'] + 0.05:
        print(f'⇒ **组合信号确实能改善**（F1 {base["f1"]:.3f} → {best["f1"]:.3f}），')
        print(f'   而且 PPV 从 {base["ppv"]*100:.1f}% 提到 {best["ppv"]*100:.1f}%。')
        print(f'   这说明"证据过滤"这个功能**可以修**，不必降级。')
    else:
        print(f'⇒ 组合信号提升有限（F1 最多 {best["f1"]:.3f} vs {base["f1"]:.3f}）。')
        print(f'   说明这是判别器本身在本任务分布上的能力上限，')
        print(f'   应当把 L5 的定位从"过滤器"改成"排序信号"，并如实说明。')

    out = {
        'strategies': res, 'baseline': base['name'], 'best_by_f1': best['name'],
        'coverage_full': {          # 全量精确覆盖率（PPV 只能靠样本估）
            'A_pass': 27954, 'B_hit_both': 20579, 'H_hit_both_or_p90': 25160,
            'n_total': 43867,
        },
        'only_H_returned': {        # H 比 A 补回的那部分，质量单独估过
            'n': 4434, 'all_hit': 'both', 'median_entail_prob': 0.444,
            'sample_hits': 20, 'sample_support': 10, 'sample_reject': 10,
            'ppv_estimate': 0.50,
            'note': ('H 补回的都是 `hit=both` 但 `pass=false` 的边（中位分 0.444）。'
                     '这 4434 条在 200 条人工样本里命中 20 条，判 support 10 / reject 10 '
                     '⇒ 这部分 PPV 约 50%，低于 H 的整体 56%。'),
        },
        'positioning': (
            '**把 L5 定位为"证据强度的排序信号"，而不是"二元过滤器"。**'
            '作为过滤器，即使用更优的策略 H，PPV 也只有 56%（近一半是误留）；'
            '作为排序信号，域内加权 AUC 0.7074 说明它确实能排序。'
            '论文里不要写"过滤后可信"，要写"提供可排序的证据强度"。'
        ),
        'caveat': ('样本 200 条、分层加权；两个标注者都是 LLM（κ=0.722）。'
                   '覆盖率来自全量，PPV/召回来自样本。'),
    }
    (Q / 'l5-filter-strategy.json').write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'\n报告已写出: {Q / "l5-filter-strategy.json"}')


if __name__ == '__main__':
    main()
