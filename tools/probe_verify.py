# -*- coding: utf-8 -*-
"""完整列出 relation-verify-all.json 的结构、汇总表与 per-edge 字段。"""
# 路径以本文件为锚点解析，不写死盘符——演示机不一定把仓库放在 D 盘。
from pathlib import Path as _Path
_HERE = _Path(__file__).resolve()
ROOT = _HERE.parent.parent          # tools/*.py 与 src/*.py 都在根目录下一层
import json

P = str(ROOT / 'data/quality/relation-verify-all.json')
with open(P, encoding='utf-8') as f:
    d = json.load(f)

print('顶层键:', list(d.keys()))
print()
for k, v in d.items():
    if isinstance(v, list):
        print(f'  {k}: list[{len(v)}]')
        if v:
            print('    样例:', json.dumps(v[0], ensure_ascii=False)[:400])
    elif isinstance(v, dict):
        print(f'  {k}: dict keys={list(v.keys())[:12]}')
    else:
        print(f'  {k}: {v!r}')

print()
print('=== by_type 全表 ===')
bt = d.get('by_type') or []
if bt:
    print(f'{"type":<16}{"n":>7}{"mean":>9}{"median":>9}{"p25":>9}{"p75":>9}{"pass":>8}')
    for r in bt:
        print(f'{r.get("type","?"):<16}{r.get("n",0):>7}{r.get("mean",0):>9.3f}'
              f'{r.get("median",0):>9.4f}{r.get("p25",0):>9.4f}{r.get("p75",0):>9.4f}'
              f'{r.get("pass_rate",0):>8.3f}')
    print(f'共 {len(bt)} 种关系类型')
