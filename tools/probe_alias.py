# -*- coding: utf-8 -*-
"""看清 entity-alias-map.json 的三层决策：L1 归一 / L2 模糊 / L4 缩写，各自跳过了什么。"""
import collections
import json
import os

Q = r'D:\kg-demo\data\quality'
with open(os.path.join(Q, 'entity-alias-map.json'), encoding='utf-8') as f:
    am = json.load(f)

audit = am['audit']
print('=== 按 layer×action 交叉 ===')
cross = collections.Counter((a.get('layer'), a.get('action')) for a in audit)
for (ly, ac), n in sorted(cross.items()):
    print(f'  {ly:<4} {ac:<22} {n}')

print('\n=== 每类各看 3 个样例 ===')
shown = collections.defaultdict(int)
for a in audit:
    key = (a.get('layer'), a.get('action'))
    if shown[key] >= 3:
        continue
    shown[key] += 1
    print(f'\n[{key[0]} / {key[1]}]')
    print('  key     :', a.get('key'))
    mem = a.get('members') or []
    print('  members :', mem[:8], ('…共 %d' % len(mem)) if len(mem) > 8 else '')
    for k in ('reason', 'note', 'canonical', 'type', 'detail'):
        if k in a:
            print(f'  {k:<8}:', str(a[k])[:150])

print('\n=== 被合并的 384 条里，组大小分布 ===')
sizes = [len(a.get('members') or []) for a in audit if a.get('action') == 'merge']
if sizes:
    c = collections.Counter(sizes)
    print('  组大小 -> 组数:', dict(sorted(c.items())[:12]))
    print(f'  涉及名字总数 {sum(sizes)}，平均每组 {sum(sizes)/len(sizes):.2f} 个名字')

print('\n=== alias 表本身 ===')
alias = am['alias']
print(f'  {len(alias)} 条映射')
tgt = collections.Counter(alias.values())
print('  最常被指到的规范名（Top 8）:')
for k, n in tgt.most_common(8):
    print(f'    {k!r}  <- {n} 个变体')
