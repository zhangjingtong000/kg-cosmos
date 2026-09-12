# -*- coding: utf-8 -*-
"""核实 L2 里被跳过的 196 组"类型冲突"到底是不是真的类型不同。"""
# 路径以本文件为锚点解析，不写死盘符——演示机不一定把仓库放在 D 盘。
from pathlib import Path as _Path
_HERE = _Path(__file__).resolve()
ROOT = _HERE.parent.parent          # tools/*.py 与 src/*.py 都在根目录下一层
import collections
import glob
import json
import os

ROOT = str(ROOT)
Q = os.path.join(ROOT, 'data', 'quality')
G = os.path.join(ROOT, 'data', 'corpus', 'graphs')

# 名字 -> 出现过的类型集合
types = collections.defaultdict(set)
freq = collections.Counter()
for p in sorted(glob.glob(os.path.join(G, '*.json'))):
    with open(p, encoding='utf-8') as f:
        d = json.load(f)
    for n in d.get('nodes', []):
        nm = n.get('name')
        if nm:
            types[nm].add(n.get('type'))
            freq[nm] += 1

with open(os.path.join(Q, 'same-as-candidates.json'), encoding='utf-8') as f:
    sa = json.load(f)

print(f'SAME_AS 候选 {sa["count"]} 条\n')
print(f'{"成员":<42}{"类型":<34}{"频次"}')
rows = []
for c in sa['candidates']:
    mem = c['members']
    tset = []
    for m in mem:
        tset.append('/'.join(sorted(x for x in types.get(m, {'?'}) if x)))
    is_conflict = len(set('/'.join(sorted(types.get(m, {'?'}))) for m in mem)) > 1
    rows.append((c['canonical_key'], mem, tset, is_conflict,
                 [freq.get(m, 0) for m in mem]))

con = [r for r in rows if r[3]]
noc = [r for r in rows if not r[3]]
print(f'真的类型不同：{len(con)} 条')
print(f'类型其实相同（说明"冲突"判据另有原因）：{len(noc)} 条\n')

print('=== 真的类型不同（前 12）===')
for k, mem, tset, _, fr in con[:12]:
    print(f'  {k:<26} ' + ' | '.join(f'{m}={t}(×{f})' for m, t, f in zip(mem, tset, fr))[:150])

print('\n=== 类型相同（前 12）===')
for k, mem, tset, _, fr in noc[:12]:
    print(f'  {k:<26} ' + ' | '.join(f'{m}={t}(×{f})' for m, t, f in zip(mem, tset, fr))[:150])

# 单复数对的数量估计
import re
plural = 0
for k, mem, tset, isc, fr in rows:
    if len(mem) == 2 and (mem[0] + 's' == mem[1] or mem[1] + 's' == mem[0]):
        plural += 1
print(f'\n两条成员且互为简单单复数的组：{plural} / {len(rows)}')
