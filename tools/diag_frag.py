# -*- coding: utf-8 -*-
"""
统计每个社区缩略子图的碎片化程度——决定布局参数与归一化策略。

背景：卡片里主体只占约 55% 面积（实测 atlas-card1.jpg），
原因是少数孤立/碎片节点把包围盒撑大，等比缩放后主体被压缩。

用法：
    python tools/diag_frag.py
"""
import json
import collections
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ATLAS = os.path.join(ROOT, 'web', 'kg-data', 'atlas.json')


def main():
    with open(ATLAS, encoding='utf-8') as f:
        d = json.load(f)

    print(f'{"cid":>4} {"n":>3} {"m":>3} {"iso":>4} {"comps":>6} {"largest":>8}  component sizes')
    tot_iso = 0
    tot_n = 0
    fragmented = []
    for c in d['communities']:
        n = len(c['nodes'])
        m = len(c['edges'])
        tot_n += n
        deg = collections.Counter()
        for a, b, _ in c['edges']:
            deg[a] += 1
            deg[b] += 1
        iso = sum(1 for i in range(n) if deg[i] == 0)
        tot_iso += iso

        par = list(range(n))

        def find(x):
            while par[x] != x:
                par[x] = par[par[x]]
                x = par[x]
            return x

        for a, b, _ in c['edges']:
            ra, rb = find(a), find(b)
            if ra != rb:
                par[ra] = rb
        comps = collections.Counter(find(i) for i in range(n))
        sizes = sorted(comps.values(), reverse=True)
        coverage = sizes[0] / n
        if coverage < 0.9:
            fragmented.append((c['cid'], coverage, len(comps), iso))
        print(f'{c["cid"]:>4} {n:>3} {m:>3} {iso:>4} {len(comps):>6} {sizes[0]:>8}  {sizes[:9]}')

    print()
    print(f'孤立节点合计 {tot_iso} / {tot_n} = {100.0*tot_iso/tot_n:.1f}%')
    print(f'最大连通分量覆盖率 < 90% 的社区：{len(fragmented)} 个')
    for cid, cov, nc, iso in fragmented:
        print(f'   社区 {cid:>3}: 主分量覆盖 {100*cov:.0f}%，{nc} 个分量，{iso} 个孤立点')


if __name__ == '__main__':
    main()
