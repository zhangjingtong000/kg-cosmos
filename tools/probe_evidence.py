# -*- coding: utf-8 -*-
"""探测证据链数据的结构：实体/关系是否带出处（论文、页码、原句）。"""
import json
import os

ROOT = r'D:\kg-demo'
P = os.path.join(ROOT, 'data', 'corpus', 'graphs', '2302.05019v1.json')

d = json.load(open(P, encoding='utf-8'))
print('== corpus/graphs/2302.05019v1.json ==')
print('顶层类型', type(d).__name__)
if isinstance(d, dict):
    for k, v in d.items():
        if isinstance(v, list):
            print(f'  {k}: list[{len(v)}]')
            if v:
                print('    样例:', json.dumps(v[0], ensure_ascii=False)[:500])
        elif isinstance(v, dict):
            print(f'  {k}: dict keys={list(v.keys())[:14]}')
        else:
            print(f'  {k}: {type(v).__name__} = {str(v)[:160]}')

for name in ('entities.jsonl', 'relations.jsonl'):
    p = os.path.join(ROOT, 'data', 'distill', name)
    if not os.path.exists(p):
        continue
    print(f'\n== distill/{name} ==')
    with open(p, encoding='utf-8') as f:
        for i, line in enumerate(f):
            if i >= 2:
                break
            try:
                o = json.loads(line)
            except Exception as e:
                print('  解析失败', e)
                continue
            print(f'  记录{i} 键:', list(o.keys()))
            print('   ', json.dumps(o, ensure_ascii=False)[:600])
