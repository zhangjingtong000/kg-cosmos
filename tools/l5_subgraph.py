# -*- coding: utf-8 -*-
"""
T2.4 · 产出「被原文支持的关系」子图（可落盘），并在其上重算 L1–L6。

    python tools/l5_subgraph.py

这是 T2.4 里之前漏掉的一步：上一版只在内存里算了"L5 支持子集"的指标，
没有把子图真正导出。本脚本补上，并顺带把六层指标写在同一个产物里，
保证"六层结果"只有一个出处。

输入：data/quality/relation-verify-all.json（由 relation_verifier.py --all 生成）
输出：data/quality/subgraph-supported.json
        meta / nodes / edges / six_layer

子图定义：entail_prob >= threshold 的边，加上它们两端涉及的节点。
注意这是"边级"过滤，不做悬空端点剔除——悬空是 L3 的事，二者正交（见报告 §3.2）。
"""
import collections
import glob
import json
import os
import re
from datetime import datetime

ROOT = r'D:\kg-demo'
Q = os.path.join(ROOT, 'data', 'quality')
G = os.path.join(ROOT, 'data', 'corpus', 'graphs')
VERIFY = os.path.join(Q, 'relation-verify-all.json')
OUT = os.path.join(Q, 'subgraph-supported.json')

ONTOLOGY = {
    'RELATED_TO', 'CREATED_BY', 'USES', 'PART_OF', 'APPLIES_TO', 'PRODUCES',
    'ENABLES', 'CITES', 'MEASURES', 'LOCATED_AT', 'SUPPORTS', 'COMPARES_WITH',
    'EVALUATES', 'CAUSES', 'CONTRADICTS',
}
_norm = re.compile(r'[^0-9a-z\u4e00-\u9fff]+')
EVID_LIMIT = 600


def norm(s):
    return _norm.sub('', (s or '').lower())


def main():
    with open(VERIFY, encoding='utf-8') as f:
        v = json.load(f)
    thr = v['threshold']
    edges = v['edges']
    print(f'读入 {len(edges)} 条边，阈值 {thr}')

    # ── 节点全集与节点证据（用于 L1）──
    node_meta = {}       # name -> {type, evidence, degree}
    for p in sorted(glob.glob(os.path.join(G, '*.json'))):
        with open(p, encoding='utf-8') as f:
            d = json.load(f)
        for n in d.get('nodes', []):
            nm = n.get('name')
            if not nm:
                continue
            if nm not in node_meta:
                ev = (n.get('evidence') or [{}])[0]
                node_meta[nm] = {
                    'type': n.get('type'),
                    'evidence': (ev.get('text') or '')[:EVID_LIMIT],
                }

    # ── L1 实体存在层（节点级）──
    l1_hit = l1_tot = 0
    for nm, m in node_meta.items():
        l1_tot += 1
        if norm(nm) and norm(nm) in norm(m['evidence']):
            l1_hit += 1
    l1 = l1_hit / l1_tot if l1_tot else 0.0

    # ── 过滤 ──
    kept = [e for e in edges if e.get('entail_prob', 0) >= thr]
    print(f'保留 {len(kept)} 条（{len(kept)/len(edges):.1%}）')

    used = set()
    for e in kept:
        used.add(e['source'])
        used.add(e['target'])

    # ── 子图节点 ──
    deg = collections.Counter()
    for e in kept:
        deg[e['source']] += 1
        deg[e['target']] += 1
    nodes = []
    for nm in sorted(used):
        m = node_meta.get(nm) or {}
        nodes.append({
            'id': nm,
            'type': m.get('type') or 'Unknown',
            'degree': deg.get(nm, 0),
            'has_evidence': bool(m.get('evidence')),
        })

    # ── 子图边 ──
    out_edges = []
    for e in kept:
        out_edges.append({
            'source': e['source'],
            'target': e['target'],
            'type': e.get('type'),
            'entail_prob': round(e.get('entail_prob', 0), 4),
            'hit': e.get('hit'),
            'paper': e.get('paper'),
            'page': e.get('page'),
        })

    # ── 六层指标（在子图上）──
    def rate(rows, fn):
        return (sum(1 for e in rows if fn(e)) / len(rows)) if rows else 0.0

    names = set(node_meta)
    six = {
        'L1_entity_presence': {'hit': l1_hit, 'total': l1_tot, 'rate': round(l1, 4)},
        'L2_entity_clustering': None,   # 由 tools/l2_report.py 写入 l5-six-layer.json
        'L3_endpoints': round(rate(kept, lambda e: e['source'] in names and e['target'] in names), 4),
        'L4_predicate': round(rate(kept, lambda e: e.get('type') in ONTOLOGY), 4),
        'L5_triplet': round(rate(kept, lambda e: e.get('entail_prob', 0) >= thr), 4),
        'L6_evidence': round(rate(edges, lambda e: (e.get('evidence') or '').strip()
                                  and e.get('page') is not None), 4),
        'hit_both': round(rate(kept, lambda e: e.get('hit') == 'both'), 4),
    }

    payload = {
        'meta': {
            'generated': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'source': os.path.basename(VERIFY),
            'threshold': thr,
            'model_roc_auc': v.get('calib_roc_auc'),
            'edges_total': len(edges),
            'edges_kept': len(kept),
            'edges_dropped': len(edges) - len(kept),
            'nodes': len(nodes),
            'note': '边级 L5 过滤后的子图；未做悬空端点剔除（那是 L3 的维度）。',
        },
        'six_layer': six,
        'nodes': nodes,
        'edges': out_edges,
    }
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, separators=(',', ':'))

    print()
    print('─' * 88)
    print('子图')
    print('─' * 88)
    print(f'  节点 {len(nodes)}   边 {len(kept)}   丢弃 {len(edges)-len(kept)}')
    print(f'  文件 {OUT}  ({os.path.getsize(OUT)/1024/1024:.1f} MB)')
    print()
    print('─' * 88)
    print('六层指标（子图口径）')
    print('─' * 88)
    print(f'  L1 实体存在  {six["L1_entity_presence"]["rate"]:.1%}'
          f'  ({l1_hit}/{l1_tot})')
    print(f'  L2 实体聚类  见 l5-six-layer.json（由 tools/l2_report.py 写入）')
    print(f'  L3 关系端点  {six["L3_endpoints"]:.1%}')
    print(f'  L4 关系谓词  {six["L4_predicate"]:.1%}')
    print(f'  L5 三元组    {six["L5_triplet"]:.1%}')
    print(f'  L6 证据      {six["L6_evidence"]:.1%}')
    print(f'  （子图内端点双命中 {six["hit_both"]:.1%}）')


if __name__ == '__main__':
    main()
