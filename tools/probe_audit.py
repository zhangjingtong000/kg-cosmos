# -*- coding: utf-8 -*-
"""读取 sync-filter-audit.json 与 filtered-edges.json 的统计，确认当前图的口径。"""
# 路径以本文件为锚点解析，不写死盘符——演示机不一定把仓库放在 D 盘。
from pathlib import Path as _Path
_HERE = _Path(__file__).resolve()
ROOT = _HERE.parent.parent          # tools/*.py 与 src/*.py 都在根目录下一层
import json
import os

Q = str(ROOT / 'data/quality')

with open(os.path.join(Q, 'sync-filter-audit.json'), encoding='utf-8') as f:
    a = json.load(f)
print('=== sync-filter-audit.json ===')
if isinstance(a, dict):
    for k, v in a.items():
        if isinstance(v, list):
            print(f'  {k}: list[{len(v)}]')
            if v:
                print('    样例:', json.dumps(v[0], ensure_ascii=False)[:260])
        elif isinstance(v, dict):
            print(f'  {k}:')
            for k2, v2 in list(v.items())[:14]:
                print(f'      {k2}: {v2!r}'[:160])
        else:
            print(f'  {k}: {v!r}'[:200])

with open(os.path.join(Q, 'filtered-edges.json'), encoding='utf-8') as f:
    fe = json.load(f)
print()
print('=== filtered-edges.json ===')
print('  threshold:', fe.get('threshold'))
print('  types:', fe.get('types'))
print('  stats:', json.dumps(fe.get('stats'), ensure_ascii=False))
print('  filtered 条数:', len(fe.get('filtered', [])))

# 过滤掉的边的类型分布
import collections
c = collections.Counter(e.get('type') for e in fe.get('filtered', []))
print('  被过滤边的类型分布（前 12）:')
for t, n in c.most_common(12):
    print(f'    {t:<20} {n}')
