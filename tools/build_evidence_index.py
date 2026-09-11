# -*- coding: utf-8 -*-
"""
按社区导出实体的页级证据链。

    python tools/build_evidence_index.py

产物：web/kg-data/evidence-{i}.json
    { "实体名": [ {"src": 论文文件, "page": 页码, "text": 原文片段}, ... ] }

数据来源 data/corpus/graphs/*.json（每篇论文的抽取结果），其中每个节点都带
evidence: {source_id, source_name, page, chunk_idx, text}。

按社区切分而不是出一个大文件：进入某个社区时才加载对应的那一个（约几百 KB），
避免首屏被几 MB 的证据表拖慢。
"""
import collections
import glob
import json
import os

ROOT = r'D:\kg-demo'
GRAPHS = os.path.join(ROOT, 'data', 'corpus', 'graphs')
FULL = os.path.join(ROOT, 'web', 'kg-data', 'force-full.json')
OUT = os.path.join(ROOT, 'web', 'kg-data')

MAX_EVIDENCE = 2      # 每个实体最多保留几条证据
TEXT_LIMIT = 220      # 原文片段截断长度


def main():
    with open(FULL, encoding='utf-8') as f:
        full = json.load(f)
    comm_of = {n['id']: n.get('community', -1) for n in full['nodes']}
    print(f'全图 {len(comm_of)} 个实体，{len(set(comm_of.values()))} 个社区分组')

    ev = collections.defaultdict(list)
    seen = collections.defaultdict(set)
    files = sorted(glob.glob(os.path.join(GRAPHS, '*.json')))
    print(f'扫描 {len(files)} 篇论文的抽取结果…')
    for p in files:
        with open(p, encoding='utf-8') as f:
            d = json.load(f)
        for n in d.get('nodes', []):
            name = n.get('name')
            if not name:
                continue
            for e in (n.get('evidence') or []):
                key = (e.get('source_id'), e.get('page'), e.get('chunk_idx'))
                if key in seen[name]:
                    continue
                seen[name].add(key)
                txt = (e.get('text') or '').strip().replace('\n', ' ')
                ev[name].append({
                    'src': e.get('source_name') or e.get('source_id') or '?',
                    'page': e.get('page'),
                    'text': txt[:TEXT_LIMIT] + ('…' if len(txt) > TEXT_LIMIT else ''),
                })
    print(f'聚合到 {len(ev)} 个实体的证据')

    by_comm = collections.defaultdict(dict)
    miss = 0
    for name, lst in ev.items():
        c = comm_of.get(name)
        if c is None:
            miss += 1
            continue
        by_comm[c][name] = lst[:MAX_EVIDENCE]
    print(f'社区表里没有的实体名 {miss} 个（多半是归一化前后的写法差异）')

    total = 0.0
    for c, table in sorted(by_comm.items()):
        path = os.path.join(OUT, f'evidence-{c}.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(table, f, ensure_ascii=False, separators=(',', ':'))
        kb = os.path.getsize(path) / 1024
        total += kb
        print(f'  evidence-{c}.json  {len(table):>5} 实体  {kb:>7.1f} KB')
    print(f'合计 {total:.0f} KB')


if __name__ == '__main__':
    main()
