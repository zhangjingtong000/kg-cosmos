# -*- coding: utf-8 -*-
"""
T2.4 · 产出"被原文支持的关系"子图，并在其上重算 L1–L6 六层指标。

    python tools/l5_report.py            # 打印报告
    python tools/l5_report.py --json     # 额外写出 data/quality/l5-six-layer.json

设计要点：
  · 六层里 L5 是本次新建的；L1/L3/L4/L6 之前只有零散数字，这里在**同一口径**
    上一次性重算，避免此前"不同文档引用不同运行结果"的问题。
  · 口径分两个对照：全集（44172）与 L5 支持子集（entail_prob >= 0.67）。
  · 节点级与边级分开：L1 是节点级（实体名是否出现在自己的证据引文里），
    L3/L4/L5/L6 是边级。
  · L2（实体聚类层）本阶段未建，报告中显式标注为 N/A，不假装有数字。
"""
import collections
import glob
import json
import os
import re
import sys

ROOT = r'D:\kg-demo'
Q = os.path.join(ROOT, 'data', 'quality')
GRAPHS = os.path.join(ROOT, 'data', 'corpus', 'graphs')
VERIFY = os.path.join(Q, 'relation-verify-all.json')

# 15 类本体（与 早先的阶段计划 §4.1 L4 的白名单一致）。
# 注意 APPLIES_TO 属于本体——早先漏掉它会把 3850 条本体边错算成越界。
ONTOLOGY = {
    'RELATED_TO', 'CREATED_BY', 'USES', 'PART_OF', 'APPLIES_TO', 'PRODUCES',
    'ENABLES', 'CITES', 'MEASURES', 'LOCATED_AT', 'SUPPORTS', 'COMPARES_WITH',
    'EVALUATES', 'CAUSES', 'CONTRADICTS',
}

_norm_re = re.compile(r'[^0-9a-z\u4e00-\u9fff]+')


def norm(s):
    return _norm_re.sub('', (s or '').lower())


def main():
    with open(VERIFY, encoding='utf-8') as f:
        v = json.load(f)
    thr = v['threshold']
    edges = v['edges']
    print('=' * 104)
    print('T2.4  L1–L6 六层指标（统一口径重算）')
    print('=' * 104)
    print(f'数据源 {os.path.basename(VERIFY)}   边 {len(edges)}   '
          f'L5 阈值 {thr}（ROC-AUC {v["calib_roc_auc"]}）')

    # ── 载入全部论文快照：节点证据 + 节点全集 ──
    node_ev = {}          # 归一化实体名 → 证据文本（取第一条）
    all_names = set()     # 节点全集（原始名）
    norm_names = set()
    files = sorted(glob.glob(os.path.join(GRAPHS, '*.json')))
    for p in files:
        with open(p, encoding='utf-8') as f:
            d = json.load(f)
        for n in d.get('nodes', []):
            nm = n.get('name')
            if not nm:
                continue
            all_names.add(nm)
            norm_names.add(norm(nm))
            if nm not in node_ev:
                ev = (n.get('evidence') or [{}])[0]
                node_ev[nm] = ev.get('text') or ''
    print(f'论文快照 {len(files)} 篇，节点名 {len(all_names)} 个（归一化后 {len(norm_names)}）')

    # ── L1 实体存在层（节点级）──
    l1_hit = l1_tot = 0
    l1_miss_samples = []
    for nm, txt in node_ev.items():
        l1_tot += 1
        if norm(nm) and norm(nm) in norm(txt):
            l1_hit += 1
        elif len(l1_miss_samples) < 5:
            l1_miss_samples.append(nm)
    l1 = l1_hit / l1_tot if l1_tot else 0.0

    # ── 边级四层 ──
    def layer_stats(rows, label):
        n = len(rows)
        if not n:
            return None
        l3 = sum(1 for e in rows
                 if e['source'] in all_names and e['target'] in all_names)
        l4 = sum(1 for e in rows if e.get('type') in ONTOLOGY)
        l5 = sum(1 for e in rows if e.get('entail_prob', 0) >= thr)
        l6 = sum(1 for e in rows if (e.get('evidence') or '').strip()
                 and e.get('page') is not None)
        both = sum(1 for e in rows if e.get('hit') == 'both')
        return {'label': label, 'n': n, 'l3': l3, 'l4': l4, 'l5': l5, 'l6': l6,
                'both': both}

    supported = [e for e in edges if e.get('entail_prob', 0) >= thr]
    stats_all = layer_stats(edges, '全集')
    stats_sup = layer_stats(supported, 'L5 支持子集')

    print()
    print('-' * 104)
    print('六层指标')
    print('-' * 104)
    print(f'L1 实体存在层（节点级）       {l1_hit}/{l1_tot} = {l1:.1%}'
          f'   实体名未字面出现在自身证据引文中')
    print(f'L2 实体聚类层                 N/A —— 本阶段未建（T4）')
    for st in (stats_all, stats_sup):
        if not st:
            continue
        n = st['n']
        print(f'L3 关系端点层  [{st["label"]}]   {st["l3"]}/{n} = {st["l3"]/n:.1%}'
              f'   两端都在节点集内')
        print(f'L4 关系谓词层  [{st["label"]}]   {st["l4"]}/{n} = {st["l4"]/n:.1%}'
              f'   谓词在本体 15 类内')
        print(f'L5 三元组验证  [{st["label"]}]   {st["l5"]}/{n} = {st["l5"]/n:.1%}'
              f'   entail_prob >= {thr}')
        print(f'L6 证据层      [{st["label"]}]   {st["l6"]}/{n} = {st["l6"]/n:.1%}'
              f'   有页面级引文')
        print(f'   端点双命中（hit=both）        {st["both"]}/{n} = {st["both"]/n:.1%}')
        print()

    # ── 对照：过滤前后的边数与平均分 ──
    print('-' * 104)
    print('过滤效果对照')
    print('-' * 104)
    dropped = [e for e in edges if e.get('entail_prob', 0) < thr]
    for label, rows in (('保留（L5 支持）', supported), ('丢弃（L5 不支持）', dropped)):
        if not rows:
            continue
        dots = collections.Counter(e.get('type') for e in rows)
        print(f'{label}: {len(rows)} 条   端点双命中 {sum(1 for e in rows if e.get("hit")=="both")/len(rows):.1%}')
        print('   主要类型: ' + ', '.join(f'{t} {c}' for t, c in dots.most_common(5)))

    if '--json' in sys.argv:
        out = {
            'source': os.path.basename(VERIFY),
            'threshold': thr,
            'edges_total': len(edges),
            'L1_entity_presence': {'hit': l1_hit, 'total': l1_tot, 'rate': l1},
            'L2_entity_clustering': None,
            'L3_endpoints': {'all': stats_all['l3'] / stats_all['n'],
                             'supported': stats_sup['l3'] / stats_sup['n']},
            'L4_predicate': {'all': stats_all['l4'] / stats_all['n'],
                             'supported': stats_sup['l4'] / stats_sup['n']},
            'L5_triplet': {'all': stats_all['l5'] / stats_all['n'],
                           'supported': 1.0},
            'L6_evidence': {'all': stats_all['l6'] / stats_all['n'],
                            'supported': stats_sup['l6'] / stats_sup['n']},
            'hit_both': {'all': stats_all['both'] / stats_all['n'],
                         'supported': stats_sup['both'] / stats_sup['n']},
            'edges_supported': len(supported),
            'edges_dropped': len(dropped),
        }
        p = os.path.join(Q, 'l5-six-layer.json')
        with open(p, 'w', encoding='utf-8') as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f'\n已写出 {p}')


if __name__ == '__main__':
    main()
