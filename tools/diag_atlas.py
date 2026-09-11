# -*- coding: utf-8 -*-
"""
为「分面式社区图谱」确定每格的抽样参数。

核心问题：每个社区卡片只有约 300x340 px，里面能放多少节点/边才不糊？
沿用主诊断的判据：边/节点 <= 2.5 为可读。

只读诊断，不修改任何数据。

用法：
    python tools/diag_atlas.py
"""
import json
import collections
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, 'web', 'kg-data',
                   os.environ.get('ATLAS_SRC', 'force-full.json'))

# 关系类型优先级：数字越小越优先保留（结构性/有向关系优先于泛化关联）
PRIORITY = {
    'PART_OF': 0, 'CREATED_BY': 0, 'CITES': 0,
    'APPLIES_TO': 1, 'USES': 1, 'PRODUCES': 1, 'EVALUATES': 1,
    'COMPARES_WITH': 1, 'IMPROVES': 1,
    'LOCATED_AT': 2, 'PUBLISHED_IN': 2,
    'RELATED_TO': 9,      # 已知信息量最低（双峰分布），最后保留
}


def pw(t):
    return PRIORITY.get(t, 5)


def load():
    with open(RAW, encoding='utf-8') as f:
        return json.load(f)


def cap_degree(edges, cap, degree_of):
    """
    度数上限抽样：按 (关系优先级, 端点最大度数) 排序贪心加边，
    每个节点最多保留 cap 条边。保证 边/节点 <= cap。
    """
    ordered = sorted(
        edges,
        key=lambda e: (pw(e.get('type', '?')),
                       -max(degree_of.get(e['source'], 0), degree_of.get(e['target'], 0))),
    )
    cnt = collections.defaultdict(int)
    out = []
    for e in ordered:
        s, t = e['source'], e['target']
        if cnt[s] >= cap or cnt[t] >= cap:
            continue
        cnt[s] += 1
        cnt[t] += 1
        out.append(e)
    return out


def induced(ids, edges):
    keep = set(ids)
    seen = set()
    out = []
    for e in edges:
        s, t = e['source'], e['target']
        if s not in keep or t not in keep or s == t:
            continue
        k = (s, t) if s < t else (t, s)
        if k in seen:
            continue
        seen.add(k)
        out.append(e)
    return out


def ratio(n, m):
    return m / n if n else 0.0


def main():
    raw = load()
    nodes, links = raw['nodes'], raw['links']
    by_id = {x['id']: x for x in nodes}
    degree_of = {x['id']: x.get('degree', 0) for x in nodes}

    comm_nodes = collections.defaultdict(list)
    for x in nodes:
        comm_nodes[x.get('community', -1)].append(x)

    sizes = sorted(comm_nodes.items(), key=lambda kv: -len(kv[1]))
    print('=' * 92)
    print('各社区规模')
    print('=' * 92)
    for c, lst in sizes:
        print(f'  社区 {c:>3}: {len(lst):5d} 实体')

    print()
    print('=' * 92)
    print('每格抽样方案对比（目标：边/节点 <= 2.5）')
    print('=' * 92)
    header = f'{"社区":>6} {"规模":>5} | ' + ' | '.join(
        f'K={k:<3} cap=-  ' for k in (20, 30, 40)) + ' | ' + ' | '.join(
        f'K=40 cap={d}' for d in (2, 3, 4))
    print(header)

    recommend = {}
    for c, lst in sizes:
        if c == -1:
            continue
        rst = sorted(lst, key=lambda x: -x.get('degree', 0))
        intra_all = induced([x['id'] for x in lst], links)
        row = f'{c:>6} {len(lst):>5} | '
        cells = []
        for K in (20, 30, 40):
            ids = [x['id'] for x in rst[:K]]
            sub = induced(ids, intra_all)
            cells.append(f'{len(sub):>4}/{ratio(len(ids), len(sub)):>4.2f}')
        row += ' | '.join(f'{x:<10}' for x in cells) + ' | '
        capped_cells = []
        for d in (2, 3, 4):
            ids = [x['id'] for x in rst[:40]]
            sub = induced(ids, intra_all)
            cap = cap_degree(sub, d, degree_of)
            capped_cells.append(f'{len(cap):>4}/{ratio(len(ids), len(cap)):>4.2f}')
        row += ' | '.join(f'{x:<10}' for x in capped_cells)
        print(row)

        # 记录推荐参数：K=40, cap=3
        ids = [x['id'] for x in rst[:40]]
        sub = induced(ids, intra_all)
        cap = cap_degree(sub, 3, degree_of)
        recommend[c] = (len(ids), len(cap), ratio(len(ids), len(cap)))

    print()
    print('=' * 92)
    print('推荐参数 K=40 / cap=3 的实际表现')
    print('=' * 92)
    bad = [(c, v) for c, v in recommend.items() if v[2] > 2.5]
    good = [(c, v) for c, v in recommend.items() if v[2] <= 2.5]
    print(f'  达标社区 {len(good)}/{len(recommend)}')
    if bad:
        print('  超标:', ', '.join(f'#{c}({v[2]:.2f})' for c, v in bad))
    tot_n = sum(v[0] for v in recommend.values())
    tot_m = sum(v[1] for v in recommend.values())
    print(f'  合计 {tot_n} 节点 / {tot_m} 边 → 全局边/节点 = {ratio(tot_n, tot_m):.2f}')

    # ── 社区间主干：用于卡片之间的连线 ──
    print()
    print('=' * 92)
    print('社区间主干（卡片之间的连线用）')
    print('=' * 92)
    pair = collections.Counter()
    for e in links:
        cs = by_id.get(e['source'], {}).get('community', -1)
        ct = by_id.get(e['target'], {}).get('community', -1)
        if cs == ct or cs < 0 or ct < 0:
            continue
        pair[(min(cs, ct), max(cs, ct))] += 1
    print(f'  社区对总数: {len(pair)}  （18 社区全连接也只有 153 对）')
    print('  Top15 最强社区对:')
    for (a, b), n in pair.most_common(15):
        print(f'    社区#{a:<3} <-> 社区#{b:<3}  {n:5d} 条跨社区关系')
    vals = sorted(pair.values(), reverse=True)
    print(f'  分布: max={vals[0]}, 中位={vals[len(vals)//2]}, min={vals[-1]}')
    # 如果只保留 Top-P 的社区对连线
    for P in (20, 30, 50):
        kept = sum(vals[:P])
        total = sum(vals)
        print(f'  保留 Top-{P:>2} 社区对 → 覆盖 {kept}/{total} = {100.0*kept/total:.1f}% 跨社区关系')


if __name__ == '__main__':
    main()
