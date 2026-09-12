# -*- coding: utf-8 -*-
"""探测 filtered-edges.json 等治理产物的结构，确认 T2.4 是否已有半成品。"""
# 路径以本文件为锚点解析，不写死盘符——演示机不一定把仓库放在 D 盘。
from pathlib import Path as _Path
_HERE = _Path(__file__).resolve()
ROOT = _HERE.parent.parent          # tools/*.py 与 src/*.py 都在根目录下一层
import json
import os

Q = str(ROOT / 'data/quality')
TARGETS = ['filtered-edges.json', 'created-by-plan.json', 'claims.json',
           'entity-alias-map.json', 'type-normalization-plan.json', 'entity-quality.json']

for name in TARGETS:
    p = os.path.join(Q, name)
    if not os.path.exists(p):
        print(f'--- {name}: 不存在')
        continue
    with open(p, encoding='utf-8') as f:
        d = json.load(f)
    print(f'--- {name}  ({os.path.getsize(p)/1024:.0f} KB)')
    if isinstance(d, dict):
        for k, v in list(d.items())[:12]:
            if isinstance(v, list):
                print(f'    {k}: list[{len(v)}]')
                if v:
                    print(f'      样例: {json.dumps(v[0], ensure_ascii=False)[:220]}')
            elif isinstance(v, dict):
                print(f'    {k}: dict keys={list(v.keys())[:10]}')
            else:
                print(f'    {k}: {v!r}'[:200])
    elif isinstance(d, list):
        print(f'    list[{len(d)}]')
        if d:
            print(f'    样例: {json.dumps(d[0], ensure_ascii=False)[:250]}')
    print()
