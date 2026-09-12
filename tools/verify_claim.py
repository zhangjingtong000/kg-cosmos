# -*- coding: utf-8 -*-
"""
核实三件事：
  1. T2.4 的"产出子图"到底有没有真的产出文件（filtered-edges.json 的 matched 是什么）
  2. T3.1 修复 CREATED_BY 方向之后，效果有没有被验证过
  3. 这一轮我到底改没改图数据 / 生产代码
"""
# 路径以本文件为锚点解析，不写死盘符——演示机不一定把仓库放在 D 盘。
from pathlib import Path as _Path
_HERE = _Path(__file__).resolve()
ROOT = _HERE.parent.parent          # tools/*.py 与 src/*.py 都在根目录下一层
import collections
import glob
import json
import os
import subprocess

ROOT = str(ROOT)
Q = os.path.join(ROOT, 'data', 'quality')
G = os.path.join(ROOT, 'data', 'corpus', 'graphs')

print('=' * 92)
print('① filtered-edges.json 里 matched / filtered 到底是什么')
print('=' * 92)
with open(os.path.join(Q, 'filtered-edges.json'), encoding='utf-8') as f:
    fe = json.load(f)
print(' threshold:', fe['threshold'])
print(' types    :', fe['types'])
print(' stats    :', json.dumps(fe['stats'], ensure_ascii=False))
print(' filtered 列表长度:', len(fe['filtered']))
print(' 顶层还有哪些键:', list(fe.keys()))

with open(os.path.join(Q, 'relation-verify-all.json'), encoding='utf-8') as f:
    va = json.load(f)
edges = va['edges']
print(f'\n 全量边 {len(edges)}')
tc = collections.Counter(e['type'] for e in edges)
print(f' RELATED_TO {tc["RELATED_TO"]}   CREATED_BY {tc["CREATED_BY"]}   两者合计 '
      f'{tc["RELATED_TO"] + tc["CREATED_BY"]}')
print(f' 两者中通过 L5 的: '
      f'{sum(1 for e in edges if e["type"] in ("RELATED_TO","CREATED_BY") and e["entail_prob"] >= 0.67)}')
print(f' 两者中未通过的: '
      f'{sum(1 for e in edges if e["type"] in ("RELATED_TO","CREATED_BY") and e["entail_prob"] < 0.67)}')

print()
print('=' * 92)
print('② T3.1 修完 CREATED_BY 方向后，有没有验证过')
print('=' * 92)
with open(os.path.join(Q, 'created-by-patch.json'), encoding='utf-8') as f:
    patch = json.load(f)
print(' mode:', patch.get('mode'), ' generated_at:', patch.get('generated_at'))
print(' stats:', json.dumps(patch.get('stats'), ensure_ascii=False))
print(' 改动文件数:', len(patch.get('files') or []))

# 现在的 corpus 里 CREATED_BY 的方向分布
for label, pat in (('source_type=Person & target_type=Document', ('Person', 'Document')),
                   ('source_type=Document & target_type=Person', ('Document', 'Person'))):
    n = 0
    for p in sorted(glob.glob(os.path.join(G, '*.json'))):
        with open(p, encoding='utf-8') as f:
            d = json.load(f)
        tmap = {x.get('name'): x.get('type') for x in d.get('nodes', [])}
        for e in d.get('edges', []):
            if e.get('type') != 'CREATED_BY':
                continue
            st, tt = tmap.get(e['source']), tmap.get(e['target'])
            if (st, tt) == pat:
                n += 1
    print(f'  {label}: {n}')

print()
print('=' * 92)
print('③ 我这一轮（T2/T4 那几次提交）改过哪些文件')
print('=' * 92)
for rev in ('ef3fa9c', '416271d'):
    r = subprocess.run(['git', '-C', ROOT, 'show', '--stat', '--oneline', rev],
                       capture_output=True, text=True, encoding='utf-8')
    print(r.stdout.strip()[:900])
    print()
