# -*- coding: utf-8 -*-
"""单篇抽取质量评测：回答「这一篇跑出来的数据是好是坏」。

为什么单独写一个：现有的六层评测（`tools/l5_report.py`）是**全库口径**，
读的是 `data/corpus/graphs/` 与 `relation-verify-all.json`，没法对
"刚上传的这一篇"单独下结论。而用户要的正是单篇的判断。

**评测项全部是确定性规则，不依赖任何模型**，因此任何人在任何机器上都能
复现同样的数字：

  L1  实体名是否出现在**它自己的证据**里（不会写的实体 = 抽出来的噪声）
  L4  谓词是否落在 15 类本体之内（越界 = 抽取器没按 schema 走）
  结构 自环 / 重复边 / 孤立节点比例 / 平均度 / 连通分量
  证据 带证据的实体与关系占比（这个项目的立身之本）
  规模 实体数 / 关系数 / 关系类型分布

用法：
  python tools/audit_single_graph.py data/ad690dfb/graph.json
  python tools/audit_single_graph.py --compare data/ad690dfb/graph.json data/3e38f803/graph.json
"""
import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))

try:
    from ontology import RELATION_TYPES
    ONTOLOGY = set(RELATION_TYPES)
except Exception:                                   # 本体读不到就显式说明
    ONTOLOGY = set()

_norm = re.compile(r'[^0-9a-z\u4e00-\u9fff]+')


def norm(s: str) -> str:
    return _norm.sub('', (s or '').lower())


def load(p):
    d = json.loads(Path(p).read_text(encoding='utf-8-sig'))
    return d


def audit(d):
    nodes = d.get('nodes') or []
    edges = d.get('edges') or []
    r = {'file_id': d.get('id'), 'mode': d.get('mode'),
         'source': (d.get('source') or {}).get('name'),
         'stats': d.get('stats') or {}}

    r['n_nodes'] = len(nodes)
    r['n_edges'] = len(edges)

    names = {}
    for n in nodes:
        k = norm(n.get('name'))
        if k:
            names.setdefault(k, []).append(n)

    # ── L1：实体名是否出现在它自己的证据里 ─────────────────────────────
    with_ev = 0
    l1_hit = 0
    for n in nodes:
        evs = n.get('evidence') or []
        if not evs:
            continue
        with_ev += 1
        key = norm(n.get('name'))
        if not key:
            continue
        blob = norm(' '.join((e.get('text') or '') for e in evs))
        # 实体名可能被拆写过，用「名字里最长的连续片段」做宽松匹配
        if key in blob or (len(key) >= 4 and key[:4] in blob):
            l1_hit += 1
    r['nodes_with_evidence'] = with_ev
    r['evidence_coverage_nodes'] = round(100.0 * with_ev / max(1, len(nodes)), 1)
    r['L1_name_in_evidence'] = round(100.0 * l1_hit / max(1, with_ev), 1)

    # ── L4：谓词是否在本体内 ─────────────────────────────────────────
    types = Counter((e.get('type') or '?') for e in edges)
    if ONTOLOGY:
        in_onto = sum(c for t, c in types.items() if t in ONTOLOGY)
        r['L4_in_ontology'] = round(100.0 * in_onto / max(1, len(edges)), 1)
        r['off_ontology_types'] = sorted(t for t in types if t not in ONTOLOGY)
    r['relation_types_used'] = len(types)
    r['top_relation_types'] = types.most_common(6)

    # ── 结构 ────────────────────────────────────────────────────────
    self_loops = sum(1 for e in edges
                     if norm(e.get('source')) and norm(e.get('source')) == norm(e.get('target')))
    seen = set()
    dup = 0
    for e in edges:
        k = (norm(e.get('source')), norm(e.get('target')), (e.get('type') or '').lower())
        if k in seen:
            dup += 1
        seen.add(k)

    deg = defaultdict(int)
    for e in edges:
        deg[norm(e.get('source'))] += 1
        deg[norm(e.get('target'))] += 1
    isolated = sum(1 for n in nodes if deg.get(norm(n.get('name')), 0) == 0)

    parent = {}
    def find(x):
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x
    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    for n in nodes:
        parent.setdefault(norm(n.get('name')), norm(n.get('name')))
    for e in edges:
        a, b = norm(e.get('source')), norm(e.get('target'))
        if a and b:
            union(a, b)
    comps = len({find(k) for k in parent}) if parent else 0

    r['self_loops'] = self_loops
    r['duplicate_edges'] = dup
    r['isolated_nodes'] = isolated
    r['isolated_pct'] = round(100.0 * isolated / max(1, len(nodes)), 1)
    r['components'] = comps
    r['largest_component_pct'] = (
        round(100.0 * max(Counter(find(k) for k in parent).values()) / max(1, len(parent)), 1)
        if parent else 0.0)
    r['avg_degree'] = round(sum(deg.values()) / max(1, len(nodes)), 2)

    # ── 证据 ────────────────────────────────────────────────────────
    e_with_ev = sum(1 for e in edges if e.get('evidence'))
    r['evidence_coverage_edges'] = round(100.0 * e_with_ev / max(1, len(edges)), 1)
    return r


def show(r, label=''):
    if label:
        print(f'\n{"=" * 74}\n{label}\n{"=" * 74}')
    print(f"  源文件      {r.get('source')}   通道 {r.get('mode') or '（未记录）'}")
    st = r.get('stats') or {}
    if st:
        print(f"  规模        {st.get('pdf_pages')} 页 / {st.get('chunks')} 块 / "
              f"耗时 {st.get('extraction_time_s')}s")
    print(f"  产出        {r['n_nodes']} 实体 · {r['n_edges']} 关系 · "
          f"{r['relation_types_used']} 种关系类型")
    print('  ── 质量 ──')
    print(f"  L1 实体名出现在自己的证据里   {r.get('L1_name_in_evidence')}%"
          f"   （{r['nodes_with_evidence']}/{r['n_nodes']} 个实体带证据）")
    print(f"  L4 谓词落在本体 15 类之内     {r.get('L4_in_ontology')}%"
          + (f"   越界: {r.get('off_ontology_types')}" if r.get('off_ontology_types') else "   （无越界）"))
    print(f"  证据覆盖    实体 {r['evidence_coverage_nodes']}% · 关系 {r['evidence_coverage_edges']}%")
    print('  ── 结构 ──')
    print(f"  孤立节点 {r['isolated_nodes']}（{r['isolated_pct']}%） · "
          f"自环 {r['self_loops']} · 重复边 {r['duplicate_edges']}")
    print(f"  连通分量 {r['components']} · 最大分量占 {r['largest_component_pct']}% · "
          f"平均度 {r['avg_degree']}")
    print(f"  主要关系类型 {r['top_relation_types']}")


def main():
    ap = argparse.ArgumentParser(description='单篇抽取质量评测（确定性规则，零模型）')
    ap.add_argument('graphs', nargs='+', help='一份或多份 graph.json')
    ap.add_argument('--compare', action='store_true', help='并排对比')
    ap.add_argument('--json', action='store_true', help='只输出 JSON')
    a = ap.parse_args()

    results = [audit(load(p)) for p in a.graphs]
    if a.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0

    if a.compare and len(results) == 2:
        A, B = results
        nameA = f"本地 {A.get('mode') or ''}".strip() if (A.get('mode') == 'local') else f"A（{Path(a.graphs[0]).parent.name}）"
        nameB = f"云端 {B.get('mode') or ''}".strip() if (B.get('mode') == 'cloud') else f"B（{Path(a.graphs[1]).parent.name}）"
        show(A, nameA)
        show(B, nameB)
        print(f'\n{"=" * 74}\n并排对比\n{"=" * 74}')
        rows = [
            ('实体数', 'n_nodes'), ('关系数', 'n_edges'),
            ('关系类型数', 'relation_types_used'),
            ('L1 实体名在证据里 %', 'L1_name_in_evidence'),
            ('L4 谓词在本体内 %', 'L4_in_ontology'),
            ('实体证据覆盖 %', 'evidence_coverage_nodes'),
            ('关系证据覆盖 %', 'evidence_coverage_edges'),
            ('孤立节点 %', 'isolated_pct'),
            ('最大分量占 %', 'largest_component_pct'),
            ('平均度', 'avg_degree'),
            ('自环', 'self_loops'), ('重复边', 'duplicate_edges'),
        ]
        print(f'  {"指标":<22} {nameA:>14} {nameB:>14}')
        for label, key in rows:
            print(f'  {label:<22} {str(A.get(key)):>14} {str(B.get(key)):>14}')
    else:
        for p, r in zip(a.graphs, results):
            show(r, f'{p}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
