# -*- coding: utf-8 -*-
"""核实：被我覆盖之前，comm-*.json 的坐标到底是不是扁平的。"""
# 路径以本文件为锚点解析，不写死盘符——演示机不一定把仓库放在 D 盘。
from pathlib import Path as _Path
_HERE = _Path(__file__).resolve()
ROOT = _HERE.parent.parent          # tools/*.py 与 src/*.py 都在根目录下一层
import subprocess
import json

REV = '7dfebc0~1'   # 我改动之前的提交
FILES = ['comm-0.json', 'comm-17.json', 'comm-9.json']

for f in FILES:
    r = subprocess.run(
        ['git', '-C', str(ROOT), 'show', f'{REV}:web/kg-data/{f}'],
        capture_output=True)
    if r.returncode != 0:
        print(f, '读取失败', r.stderr.decode('utf-8', 'ignore')[:200])
        continue
    d = json.loads(r.stdout.decode('utf-8'))
    ns = d['nodes']
    xs = [n['x'] for n in ns]
    ys = [n['y'] for n in ns]
    zs = [n['z'] for n in ns]

    def rng(v):
        return f'{min(v):>9.2f} ~ {max(v):>9.2f}'

    print(f'== {f}   nodes={len(ns)}  edges={len(d.get("edges", []))}')
    print(f'   x  {rng(xs)}')
    print(f'   y  {rng(ys)}')
    print(f'   z  {rng(zs)}')
    print(f'   非零 y 个数 {sum(1 for v in ys if abs(v) > 1e-9)} / {len(ys)}')
    print(f'   非零 z 个数 {sum(1 for v in zs if abs(v) > 1e-9)} / {len(zs)}')
    print(f'   前 3 个节点 {json.dumps(ns[:3], ensure_ascii=False)}')
    if 'meta' in d:
        print(f'   meta {json.dumps(d["meta"], ensure_ascii=False)}')
    print()
