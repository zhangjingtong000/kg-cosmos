# -*- coding: utf-8 -*-
"""
把 CREATED_BY 用修正模板重跑的分数合并回 relation-verify-all.json。

    python tools/merge_created_by_scores.py

为什么要合并：T3.1 把 CREATED_BY 方向统一成 Author → Work 之后，L5 的
hypothesis 模板没同步改，导致方向全反、通过率从 39.6% 掉到 26.0%。
修正模板后单独重跑了 CREATED_BY（tools/relation_verifier.py --all
--relation CREATED_BY），需要把新分数替换回主文件，否则主文件里这一类
仍是错的口径。

其余 14 类模板与数据都没变，分数可原样复用，因此不必整份重跑。
"""
import collections
import json
import os
import shutil
from datetime import datetime

Q = r'D:\kg-demo\data\quality'
ALL = os.path.join(Q, 'relation-verify-all.json')
CB = os.path.join(Q, 'relation-verify-rel-CREATED_BY.json')


def main():
    with open(ALL, encoding='utf-8') as f:
        allv = json.load(f)
    with open(CB, encoding='utf-8') as f:
        cbv = json.load(f)

    # 键必须含 paper：同一对 (source, target) 可能出现在多篇论文里
    # （如 `Xindong Wu → A Comprehensive Survey on …` 在综述与其原文中都有）。
    # 旧版只用 source\x00target，后写入的会覆盖先写入的，导致 477 条分数错配。
    new_cb = {(e['paper'], e['source'], e['target']): e for e in cbv['edges']}
    print(f'主文件 {allv["count"]} 条；CREATED_BY 新分数 {len(new_cb)} 条')

    # 备份
    bak = os.path.join(Q, 'baseline', 'relation-verify-all.PRE-CB-FIX.json')
    if not os.path.exists(bak):
        shutil.copy2(ALL, bak)
        print(f'已备份到 {bak}')

    replaced = missing = 0
    for e in allv['edges']:
        if e.get('type') != 'CREATED_BY':
            continue
        n = new_cb.get((e['paper'], e['source'], e['target']))
        if n is None:
            missing += 1
            continue
        # hypothesis 必须一起替换：它是要写进论文与证据面板的文本，
        # 只换分数会让数据自相矛盾（分数按新方向算，句子却是旧方向）。
        e['entail_prob'] = n['entail_prob']
        e['pass'] = n['pass']
        e['hypothesis'] = n['hypothesis']
        replaced += 1
    print(f'替换 {replaced} 条，未匹配 {missing} 条')

    # 重算整体与 by_type 汇总
    def summarize(edges):
        hits = collections.Counter(e.get('hit') for e in edges)
        by = collections.defaultdict(list)
        for e in edges:
            by[e['type']].append(e['entail_prob'])
        import statistics as st
        rows = []
        for t, ps in sorted(by.items(), key=lambda x: -len(x[1])):
            ps_sorted = sorted(ps)
            n = len(ps_sorted)
            q = lambda p: ps_sorted[min(int(n * p), n - 1)]
            rows.append({
                'type': t, 'n': n,
                'mean': round(st.mean(ps_sorted), 4),
                'median': round(st.median(ps_sorted), 4),
                'p25': round(q(0.25), 4), 'p75': round(q(0.75), 4),
                'pass_rate': round(sum(1 for x in ps_sorted if x >= allv['threshold']) / n, 4),
            })
        return hits, rows

    hits, rows = summarize(allv['edges'])
    allv['hit_distribution'] = {k: hits.get(k, 0) for k in ('both', 'one', 'none')}
    allv['by_type'] = rows
    allv['created_by_template_fix'] = {
        'merged_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'reason': 'T3.1 方向归一后模板未同步，导致 CREATED_BY 语义反转',
        'replaced_edges': replaced,
        'source': os.path.basename(CB),
    }

    # ── 自校验：合并结果必须与来源文件逐条一致，且 hypothesis 与边的方向同向 ──
    # 这一层是补上来的：此前只替换分数、漏了 hypothesis，脚本"成功"退出
    # 但产出了自相矛盾的数据（分数按新方向算、句子是旧方向 8746 条），
    # 而且没有任何下游会报错。宁可在这里中止，也不要把坏数据写出去。
    import re as _re
    _pat = _re.compile(r'^(.*?) was created by (.*?)\.$')
    bad_orient = bad_sync = 0
    for e in allv['edges']:
        if e.get('type') != 'CREATED_BY':
            continue
        n = new_cb.get((e['paper'], e['source'], e['target']))
        if n is None:
            continue
        if (e['hypothesis'] != n['hypothesis']
                or str(e['entail_prob']) != str(n['entail_prob'])
                or str(e['pass']) != str(n['pass'])):
            bad_sync += 1
        m = _pat.match(e['hypothesis'] or '')
        if (not m or m.group(1).strip() != e['target'].strip()
                or m.group(2).strip() != e['source'].strip()):
            bad_orient += 1
    print(f'自校验：与来源文件不一致 {bad_sync} 条；hypothesis 语序与方向不符 {bad_orient} 条')
    if bad_sync or bad_orient:
        raise SystemExit('!! 合并结果不自洽，已中止写出（未修改主文件）')

    with open(ALL, 'w', encoding='utf-8') as f:
        json.dump(allv, f, ensure_ascii=False, indent=2)

    cb = next((r for r in rows if r['type'] == 'CREATED_BY'), None)
    if cb:
        print(f'\n合并后 CREATED_BY: n={cb["n"]} 通过率={cb["pass_rate"]:.1%} '
              f'median={cb["median"]:.4f}')
    print(f'已写出 {ALL}')


if __name__ == '__main__':
    main()
