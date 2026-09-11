# -*- coding: utf-8 -*-
"""检查 kg-nebula.html 内部视图所用的 comm-*.json 规模。"""
import json
import glob
import os

D = r'D:\kg-demo\web\kg-data'
rows = []
for p in sorted(glob.glob(os.path.join(D, 'comm-*.json'))):
    try:
        d = json.load(open(p, encoding='utf-8'))
    except Exception as e:
        print(os.path.basename(p), 'ERR', e)
        continue
    n = len(d.get('nodes', []))
    m = len(d.get('links', d.get('edges', [])))
    rows.append((os.path.basename(p), n, m, (m / n) if n else 0))

print(f'{"file":<14} {"nodes":>6} {"links":>6} {"m/n":>6}')
for f, n, m, r in rows:
    flag = '  毛线球' if r > 2.5 else ''
    print(f'{f:<14} {n:>6} {m:>6} {r:>6.2f}{flag}')
tot_n = sum(r[1] for r in rows)
tot_m = sum(r[2] for r in rows)
print(f'\n合计 {tot_n} 节点 / {tot_m} 关系 → 全局 {tot_m/tot_n:.2f}')
if rows:
    print(f'\n单个文件结构示例 ({rows[0][0]}):')
    d = json.load(open(os.path.join(D, rows[0][0]), encoding='utf-8'))
    print(' 顶层键:', list(d.keys()))
    if d.get('nodes'):
        print(' node 样例:', json.dumps(d['nodes'][0], ensure_ascii=False)[:200])
    if d.get('links'):
        print(' link 样例:', json.dumps(d['links'][0], ensure_ascii=False)[:200])
