# -*- coding: utf-8 -*-
"""核实 T3 三个子项是否已 apply，以及 T4（L2 实体聚类）已有的基础。"""
import json
import os

Q = r'D:\kg-demo\data\quality'

# T4 的基础：别名映射表
p = os.path.join(Q, 'entity-alias-map.json')
with open(p, encoding='utf-8') as f:
    am = json.load(f)
print('=== entity-alias-map.json（T4 的基础）===')
print(' 顶层键:', list(am.keys()))
print(' summary:', json.dumps(am.get('summary'), ensure_ascii=False))
alias = am.get('alias') or {}
print(f' alias 条数: {len(alias)}')
for k, v in list(alias.items())[:6]:
    print(f'   {k!r} -> {v!r}'[:150])
audit = am.get('audit') or []
print(f' audit 条数: {len(audit)}')
import collections
c = collections.Counter(a.get('action') for a in audit)
print(' audit action 分布:', dict(c.most_common(12)))
c2 = collections.Counter(a.get('layer') for a in audit)
print(' audit layer 分布:', dict(c2))

# T3.3：claims 是否已生效（图里还有没有 CONTRADICTS 边）
p2 = os.path.join(Q, 'claims.json')
with open(p2, encoding='utf-8') as f:
    cl = json.load(f)
print()
print('=== claims.json（T3.3）===')
print(' note:', (cl.get('note') or '')[:120])
print(' total:', cl.get('total'))
print(' stats:', json.dumps(cl.get('stats'), ensure_ascii=False))
st = collections.Counter(c.get('status') for c in cl.get('claims', []))
print(' status 分布:', dict(st))
print(' 样例:', json.dumps(cl['claims'][0], ensure_ascii=False)[:260])

# T3.1：CREATED_BY 的计划与补丁
for name in ('created-by-plan.json', 'created-by-patch.json'):
    p3 = os.path.join(Q, name)
    with open(p3, encoding='utf-8') as f:
        d = json.load(f)
    print()
    print(f'=== {name} ===')
    if isinstance(d, dict):
        for k, v in d.items():
            if isinstance(v, dict):
                print(f'  {k}: dict keys={list(v.keys())[:8]}')
            elif isinstance(v, list):
                print(f'  {k}: list[{len(v)}]' + (f' 样例 {json.dumps(v[0], ensure_ascii=False)[:160]}' if v else ''))
            else:
                print(f'  {k}: {v!r}'[:140])
