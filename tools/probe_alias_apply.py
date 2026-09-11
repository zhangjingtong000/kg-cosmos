# -*- coding: utf-8 -*-
"""核实 entity-alias-map.json 的 392 条映射是否已应用到 corpus/graphs 的节点名。"""
import glob
import json
import os

ROOT = r'D:\kg-demo'
Q = os.path.join(ROOT, 'data', 'quality')
G = os.path.join(ROOT, 'data', 'corpus', 'graphs')

with open(os.path.join(Q, 'entity-alias-map.json'), encoding='utf-8') as f:
    am = json.load(f)
alias = am['alias']

names = set()
for p in sorted(glob.glob(os.path.join(G, '*.json'))):
    with open(p, encoding='utf-8') as f:
        d = json.load(f)
    for n in d.get('nodes', []):
        if n.get('name'):
            names.add(n['name'])

print(f'corpus 快照里的节点名 {len(names)} 个')

still = [k for k in alias if k in names]
gone = [k for k in alias if k not in names]
print(f'\nalias 表 {len(alias)} 条：')
print(f'  变体名**仍存在**于 corpus：{len(still)}')
print(f'  变体名**已消失**（说明已 apply）：{len(gone)}')

if still:
    print('\n仍存在的变体（前 12，说明映射未落地或映射方向不同）：')
    for k in still[:12]:
        print(f'  {k!r} -> {alias[k]!r}')
if gone:
    print('\n已消失的变体（前 8）：')
    for k in gone[:8]:
        print(f'  {k!r} -> {alias[k]!r}')

# 规范名是否都在
canon = set(alias.values())
missing_canon = [c for c in canon if c not in names]
print(f'\n规范名 {len(canon)} 个，其中 {len(missing_canon)} 个不在 corpus 节点名里')
if missing_canon[:5]:
    print('  例:', missing_canon[:5])
