# -*- coding: utf-8 -*-
"""对比治理前(PRE-T3)与治理后(now)的 L5 结果。"""
# 路径以本文件为锚点解析，不写死盘符——演示机不一定把仓库放在 D 盘。
from pathlib import Path as _Path
_HERE = _Path(__file__).resolve()
ROOT = _HERE.parent.parent          # tools/*.py 与 src/*.py 都在根目录下一层
import json

Q = str(ROOT / 'data/quality')
new = json.load(open(Q + r'\relation-verify-all.json', encoding='utf-8'))
old = json.load(open(Q + r'\baseline\relation-verify-all.PRE-T3.json', encoding='utf-8'))

print(f'{"":16}{"治理前(PRE-T3)":>20}{"治理后(now)":>20}')
print(f'{"边数":16}{old["count"]:>20}{new["count"]:>20}')
print()
print('端点命中分布：')
for k in ('both', 'one', 'none'):
    o = old['hit_distribution'].get(k, 0)
    n = new['hit_distribution'].get(k, 0)
    print(f'  {k:<6}{o:>9} ({100*o/old["count"]:5.1f}%){n:>12} ({100*n/new["count"]:5.1f}%)')

print(f'\n阈值 {new["threshold"]}   ROC-AUC {new["calib_roc_auc"]}')

ot = {r['type']: r for r in old['by_type']}
nt = {r['type']: r for r in new['by_type']}
core = ['USES', 'RELATED_TO', 'PART_OF', 'CREATED_BY', 'APPLIES_TO', 'PRODUCES',
        'ENABLES', 'LOCATED_AT', 'MEASURES', 'CITES', 'SUPPORTS',
        'COMPARES_WITH', 'EVALUATES', 'CAUSES', 'CONTRADICTS']
print()
print(f'{"type":<16}{"n旧":>7}{"pass旧":>9}{"n新":>7}{"pass新":>9}{"变化":>9}')
for t in core:
    o, n = ot.get(t), nt.get(t)
    if not o or not n:
        continue
    d = 100 * n['pass_rate'] - 100 * o['pass_rate']
    print(f'{t:<16}{o["n"]:>7}{100*o["pass_rate"]:>8.1f}%{n["n"]:>7}'
          f'{100*n["pass_rate"]:>8.1f}%{d:>+8.1f}')

# 整体通过率
def overall(d):
    tot = sum(r['n'] for r in d['by_type'] if r['type'] in core)
    ps = sum(r['n'] * r['pass_rate'] for r in d['by_type'] if r['type'] in core)
    return ps / tot if tot else 0.0

print()
print(f'本体 15 类整体通过率：治理前 {overall(old):.1%}  →  治理后 {overall(new):.1%}')
