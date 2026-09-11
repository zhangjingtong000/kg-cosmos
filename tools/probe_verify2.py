# -*- coding: utf-8 -*-
"""
定位 relation-verify-all.json 与 早先的阶段计划.md 表格之间的数字矛盾。

早先的阶段计划 §T2 写：RELATED_TO median 0.098、<0.1 占 50.1%；
而本文件 by_type 写：RELATED_TO median 0.6899。
两者不可能同时成立，必须搞清是哪一份口径/哪一次运行。
"""
import json
import os
import time
import collections

P = r'D:\kg-demo\data\quality\relation-verify-all.json'
st = os.stat(P)
print('文件修改时间:', time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(st.st_mtime)))
print('大小 %.1f MB' % (st.st_size / 1024 / 1024))

with open(P, encoding='utf-8') as f:
    d = json.load(f)

e0 = d['edges'][0]
print('\nedge 字段:', list(e0.keys()))
print('edge 样例（去掉长 evidence）:')
print(json.dumps({k: (v[:80] + '…' if isinstance(v, str) and len(v) > 80 else v)
                  for k, v in e0.items()}, ensure_ascii=False, indent=2))

# 找分数字段
score_key = None
for k in e0:
    if isinstance(e0[k], (int, float)) and k not in ('page', 'chunk_idx'):
        score_key = k
        break
print('\n推测分数字段:', score_key)

if score_key:
    for tname in ('RELATED_TO', 'CREATED_BY', 'USES', 'CONTRADICTS'):
        vals = [e[score_key] for e in d['edges'] if e.get('type') == tname and score_key in e]
        if not vals:
            continue
        vals.sort()
        n = len(vals)
        lo = sum(1 for v in vals if v < 0.1) / n
        hi = sum(1 for v in vals if v > 0.9) / n
        print(f'\n{tname}  n={n}')
        print(f'  median={vals[n//2]:.4f}  mean={sum(vals)/n:.4f}')
        print(f'  <0.1 占 {lo:.1%}    >0.9 占 {hi:.1%}    中间 {1-lo-hi:.1%}')
