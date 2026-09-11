# -*- coding: utf-8 -*-
"""对比 13:50 的探针文件与 15:13 的全量文件，定位 早先的阶段计划 表格数字的来源。"""
import json
import collections

PROBE = r'D:\kg-demo\data\gold\probe-related-to.json'
FULL = r'D:\kg-demo\data\quality\relation-verify-all.json'

with open(PROBE, encoding='utf-8') as f:
    p = json.load(f)
print('=== probe-related-to.json ===')
print('顶层类型:', type(p).__name__)
if isinstance(p, dict):
    for k, v in p.items():
        if isinstance(v, list):
            print(f'  {k}: list[{len(v)}]')
            if v:
                print('    样例:', json.dumps(v[0], ensure_ascii=False)[:320])
        elif isinstance(v, dict):
            print(f'  {k}: dict keys={list(v.keys())[:10]}')
        else:
            print(f'  {k}: {v!r}')
elif isinstance(p, list):
    print('长度:', len(p))
    print('样例:', json.dumps(p[0], ensure_ascii=False)[:320])

# 全量文件里 RELATED_TO 的 hit 分布
with open(FULL, encoding='utf-8') as f:
    d = json.load(f)
hit = collections.Counter(e.get('hit') for e in d['edges'] if e.get('type') == 'RELATED_TO')
print('\n=== 全量文件 RELATED_TO 的 hit 分布 ===')
for k, v in hit.items():
    print(f'  {k}: {v}')
n = sum(hit.values())
print(f'  合计 {n}，both 占 {hit.get("both",0)/n:.1%}')

# 按 hit 分组的 entail_prob 分位数（全量）
print('\n=== 全量文件：按 hit 分组的 entail_prob 分位数 ===')
for tname in ('RELATED_TO', 'CREATED_BY', 'USES'):
    rows = [e for e in d['edges'] if e.get('type') == tname]
    print(f'\n{tname}  共 {len(rows)}')
    for h in ('both', 'one', 'none'):
        vals = sorted(e['entail_prob'] for e in rows if e.get('hit') == h)
        if not vals:
            continue
        m = len(vals)
        print(f'  hit={h:<5} n={m:<6} median={vals[m//2]:.4f}  mean={sum(vals)/m:.4f}')
