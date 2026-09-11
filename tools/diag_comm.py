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
    print(f'\n坐标维度检查（防止只看第一个节点就误判布局）：')
    for f, _, _, _ in rows:
        d = json.load(open(os.path.join(D, f), encoding='utf-8'))
        ns = d.get('nodes') or []
        if not ns:
            continue
        xs = [n.get('x', 0) for n in ns]
        ys = [n.get('y', 0) for n in ns]
        zs = [n.get('z', 0) for n in ns]
        nz_y = sum(1 for v in ys if abs(v) > 1e-9)
        nz_z = sum(1 for v in zs if abs(v) > 1e-9)
        print(f'  {f:<14} x[{min(xs):>8.1f},{max(xs):>8.1f}] '
              f'y[{min(ys):>8.1f},{max(ys):>8.1f}] '
              f'z[{min(zs):>8.1f},{max(zs):>8.1f}]  '
              f'非零 y {nz_y}/{len(ns)}  非零 z {nz_z}/{len(ns)}')
    d = json.load(open(os.path.join(D, rows[0][0]), encoding='utf-8'))
    print(f'\n{rows[0][0]} 的字段: {list(d.keys())}')
    if d.get('nodes'):
        print(' node 样例:', json.dumps(d['nodes'][0], ensure_ascii=False)[:180])
    if d.get('edges'):
        print(' edge 样例:', json.dumps(d['edges'][0], ensure_ascii=False)[:120])
