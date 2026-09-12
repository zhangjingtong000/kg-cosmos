"""L7 关系方向层：15 类关系的端点类型约束审计（纯规则，零模型、零成本）。

【与 tools/l7_direction_verify.py 的关系】
  那份是**原型**，只做 CREATED_BY。本脚本把
  `src/ontology.py` 的 `RELATION_ENDPOINT_CONSTRAINTS` 15 类全部跑一遍，
  并在 CREATED_BY 上与它的结果对账（口径必须一致，不一致就是有人改了一边）。
  两者共用同一张表，所以不存在「同一规则写两处」。

【判据（比"合规率"多一分小心）】
  现有 L7 原型把三种完全不同的东西混进同一个"违规"数字：
    ① 方向真的反了（源是被创作物、目标是作者）
    ② 端点类型扮演了不可能的角色（例如 Person 被当成"被创作物"）
    ③ 端点类型是 `Unknown`（从边反建出来的节点，类型本来就无从得知）
  实测这三者的处理方式必须分开：把 ③ 算成违规会让合规率从 93.2% 假摔到 67.1%
  （l7_direction_verify.py 的注释里记着这笔账）。本脚本因此定义四类裁决：

    unjudgeable        端点不在节点表内，或类型为 Unknown —— **不判**
    compliant_directed 正向命中方向指纹（source_core → target_core）
    reversed           反向命中方向指纹 —— 这才是**方向错误**
    compliant_role     未命中方向指纹，但两端类型都在允许集内（合法但方向不判）
    role_violation     端点类型不在允许集内 —— 是**端点角色问题**，交 L1/L3，
                       不算方向错误

  于是有两个分开的指标：
    direction_compliance = compliant_directed / (compliant_directed + reversed)
        ↑ L7 真正要报的数，只统计方向指纹覆盖到的边
    direction_coverage   = (compliant_directed + reversed) / resolvable
        ↑ 这个数太小就说明"该关系在类型上判不了方向"，别拿它的比例下结论

【方向指纹为什么必须由人定】
  tools/l7_direction_audit.py 已证伪统计法：16 类的"主导端点类型对"占比全 < 60%
  （最高 CREATED_BY 40.2%），不存在稳定主导。表里的 core 是按关系语义手工定的，
  数据只用来核对覆盖率与误杀率。

用法：
  python tools/l7_relation_constraints_audit.py
  python tools/l7_relation_constraints_audit.py --by-type CREATED_BY
"""
import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))

from ontology import (  # noqa: E402
    RELATION_ENDPOINT_CONSTRAINTS, RELATION_TYPES, SYMMETRIC_RELATIONS,
    validate_constraints,
)

GRAPH = ROOT / 'data' / 'corpus' / 'graphs'
VERIFY = ROOT / 'data' / 'quality' / 'relation-verify-all.json'
OUT = ROOT / 'data' / 'quality' / 'l7-relation-constraints.json'
LEGACY = ROOT / 'data' / 'quality' / 'l7-direction-verification.json'


def parse_args():
    ap = argparse.ArgumentParser(description="L7 端点类型约束审计（15 类关系）")
    ap.add_argument("--by-type", default="", help="只看某一个关系类型")
    ap.add_argument("--show", type=int, default=5, help="每类打印的样例条数")
    return ap.parse_args()


ARGS = parse_args()


def load_node_types():
    """节点名 -> 类型。同名取第一个出现的，与 l7_direction_verify.py 口径一致。"""
    t = {}
    for f in sorted(GRAPH.glob('*.json')):
        snap = json.loads(f.read_text(encoding='utf-8'))
        for n in snap.get('nodes', []):
            nm = (n.get('name') or '').strip()
            if nm and nm not in t:
                t[nm] = n.get('type') or 'Concept'
    return t


def verdict(c, ts, tt):
    """一条边（两端类型已解析）的裁决。返回裁决名。"""
    if c['kind'] == 'unconstrained':
        return 'unconstrained'
    if c['kind'] == 'symmetric':
        ok = ts in c['source_allowed'] and tt in c['target_allowed']
        return 'compliant_role' if ok else 'role_violation'
    # directed：先看方向指纹（core 非空且两侧不相交才有意义，表自检保证这一点）
    core_s, core_t = c['source_core'], c['target_core']
    if core_s and core_t:
        if ts in core_s and tt in core_t:
            return 'compliant_directed'
        if tt in core_s and ts in core_t:
            return 'reversed'
    ok = ts in c['source_allowed'] and tt in c['target_allowed']
    return 'compliant_role' if ok else 'role_violation'


def main():
    problems = validate_constraints()
    if problems:
        print('!! 约束表自检未通过，先修表：')
        for p in problems:
            print('   ·', p)
        return 1

    ntype = load_node_types()
    d = json.loads(VERIFY.read_text(encoding='utf-8'))
    edges = d['edges']
    print(f'端点类型表 {len(ntype)} 个名字；边 {len(edges)} 条；'
          f'约束 {len(RELATION_ENDPOINT_CONSTRAINTS)} 类\n')

    by_type = defaultdict(lambda: {
        'stats': Counter(), 'bad_pairs': Counter(), 'rev_pairs': Counter(),
        'rev_samples': [], 'role_samples': [],
    })
    total_stats = Counter()
    unknown_names = Counter()

    for e in edges:
        r = e['type']
        if r not in RELATION_ENDPOINT_CONSTRAINTS:
            total_stats['out_of_ontology'] += 1
            continue
        c = RELATION_ENDPOINT_CONSTRAINTS[r]
        s, t = e.get('source'), e.get('target')
        ts, tt = ntype.get(s), ntype.get(t)
        b = by_type[r]
        if ts is None or tt is None or ts == 'Unknown' or tt == 'Unknown':
            b['stats']['unjudgeable'] += 1
            total_stats['unjudgeable'] += 1
            for nm, ty in ((s, ts), (t, tt)):
                if ty is None or ty == 'Unknown':
                    unknown_names[nm] += 1
            continue
        v = verdict(c, ts, tt)
        b['stats'][v] += 1
        total_stats[v] += 1
        if v == 'reversed':
            b['rev_pairs'][(ts, tt)] += 1
            if len(b['rev_samples']) < 8:
                b['rev_samples'].append({
                    'source': s, 'source_type': ts, 'target': t, 'target_type': tt,
                    'type': r, 'l5': e.get('entail_prob'),
                    'description': (e.get('description') or '')[:120],
                })
        elif v == 'role_violation':
            b['bad_pairs'][(ts, tt)] += 1
            if len(b['role_samples']) < 8:
                b['role_samples'].append({
                    'source': s, 'source_type': ts, 'target': t, 'target_type': tt,
                    'type': r, 'l5': e.get('entail_prob'),
                    'description': (e.get('description') or '')[:120],
                })

    # ── 逐类输出 ──
    report = {'layer': 'L7', 'criterion': 'endpoint type constraint',
              'table_source': 'src/ontology.py',
              'edges_total': len(edges), 'by_type': {}}
    hdr = (f"{'关系类型':14s}{'方向可判':>8s}{'可判定':>7s}{'合规(向)':>9s}"
           f"{'反向':>6s}{'角色违规':>9s}{'不可判':>7s}{'方向合规':>9s}"
           f"{'方向覆盖':>9s}{'角色合规':>9s}")
    print('=' * len(hdr))
    print(hdr)
    print('-' * len(hdr))

    for r in sorted(RELATION_TYPES,
                    key=lambda x: -sum(by_type[x]['stats'].values())):
        if ARGS.by_type and r != ARGS.by_type:
            continue
        st = by_type[r]['stats']
        c = RELATION_ENDPOINT_CONSTRAINTS[r]
        resolvable = sum(v for k, v in st.items() if k != 'unjudgeable')
        judged = st['compliant_directed'] + st['reversed']
        if c['kind'] == 'directed' and judged:
            dir_comp = st['compliant_directed'] / judged
        else:
            dir_comp = None
        coverage = judged / resolvable if resolvable else None
        denom_role = sum(v for k, v in st.items()
                         if k in ('compliant_directed', 'compliant_role', 'role_violation'))
        role_comp = ((st['compliant_directed'] + st['compliant_role']) / denom_role
                     if denom_role else None)
        verdict_label = {'judgeable': '✔', 'unjudgeable': '—'}[c['reversal']]
        print(f"{r:14s}{verdict_label:>8s}{resolvable:7d}"
              f"{st['compliant_directed']:9d}{st['reversed']:6d}"
              f"{st['role_violation']:9d}{st['unjudgeable']:7d}"
              f"{(f'{dir_comp*100:.1f}%' if dir_comp is not None else 'n/a'):>9s}"
              f"{(f'{coverage*100:.1f}%' if coverage is not None else 'n/a'):>9s}"
              f"{(f'{role_comp*100:.1f}%' if role_comp is not None else 'n/a'):>9s}")
        report['by_type'][r] = {
            'kind': c['kind'],
            'reversal': c['reversal'],
            'semantics': c['semantics'],
            'basis': c['basis'],
            'evidence': c['evidence'],
            'source_core': sorted(c['source_core']),
            'target_core': sorted(c['target_core']),
            'source_allowed': sorted(c['source_allowed']),
            'target_allowed': sorted(c['target_allowed']),
            'n_total': sum(st.values()),
            'n_unjudgeable': st['unjudgeable'],
            'n_resolvable': resolvable,
            'n_compliant_directed': st['compliant_directed'],
            'n_compliant_role': st['compliant_role'],
            'n_reversed': st['reversed'],
            'n_role_violation': st['role_violation'],
            'n_unconstrained': st['unconstrained'],
            'direction_compliance': round(dir_comp, 4) if dir_comp is not None else None,
            'direction_coverage': round(coverage, 4) if coverage is not None else None,
            'role_compliance': round(role_comp, 4) if role_comp is not None else None,
            'reversed_pairs': [[list(k), v] for k, v in by_type[r]['rev_pairs'].most_common(10)],
            'role_violation_pairs': [[list(k), v] for k, v in
                                     by_type[r]['bad_pairs'].most_common(10)],
            'reversed_samples': by_type[r]['rev_samples'],
            'role_violation_samples': by_type[r]['role_samples'],
        }

    print('-' * len(hdr))
    print(f"{'合计':14s}{'':>8s}"
          f"{sum(total_stats[k] for k in ('compliant_directed','compliant_role','reversed','role_violation','unconstrained')):7d}"
          f"{total_stats['compliant_directed']:9d}{total_stats['reversed']:6d}"
          f"{total_stats['role_violation']:9d}{total_stats['unjudgeable']:7d}")
    print(f"\n  本体外关系类型的边: {total_stats['out_of_ontology']}")
    print("  方向可判（✔）的关系类型: "
          + ', '.join(sorted(r for r, c in RELATION_ENDPOINT_CONSTRAINTS.items()
                             if c['reversal'] == 'judgeable')))
    print("  对称、按定义不判方向: " + ', '.join(sorted(SYMMETRIC_RELATIONS - {'RELATED_TO'})))
    print("  不可判定: " + ', '.join(sorted(
        r for r, c in RELATION_ENDPOINT_CONSTRAINTS.items()
        if c['kind'] != 'directed' and r not in SYMMETRIC_RELATIONS)))

    # ── 与 L7 原型的 CREATED_BY 对账 ──
    cb = report['by_type'].get('CREATED_BY', {})
    if cb and LEGACY.exists():
        old = json.loads(LEGACY.read_text(encoding='utf-8'))
        print('\n=== 与 tools/l7_direction_verify.py（CREATED_BY 原型）对账 ===')
        print(f"  可判定   : 本脚本 {cb['n_resolvable']}  vs  原型 {old['edges_resolvable']}"
              f"  {'✅' if cb['n_resolvable'] == old['edges_resolvable'] else '❌'}")
        print(f"  合规     : 本脚本 {cb['n_compliant_directed']}  vs  原型 {old['compliant']}"
              f"  {'✅' if cb['n_compliant_directed'] == old['compliant'] else '❌'}")
        print(f"  反向     : 本脚本 {cb['n_reversed']}  vs  原型违规分类 reversed "
              f"{old['violation_classes']['reversed']}"
              f"  {'✅' if cb['n_reversed'] == old['violation_classes']['reversed'] else '❌'}")
        print(f"  原型合规率 {old['compliance_rate']*100:.1f}%（含角色问题）"
              f"  →  本脚本方向合规率 {cb['direction_compliance']*100:.1f}%"
              f"（只算方向指纹覆盖的 {cb['n_compliant_directed'] + cb['n_reversed']} 条）")
        report['reconcile_with_prototype'] = {
            'prototype_compliance_rate': old['compliance_rate'],
            'prototype_resolvable': old['edges_resolvable'],
            'prototype_compliant': old['compliant'],
            'prototype_reversed': old['violation_classes']['reversed'],
            'match_resolvable': cb['n_resolvable'] == old['edges_resolvable'],
            'match_compliant': cb['n_compliant_directed'] == old['compliant'],
            'match_reversed': cb['n_reversed'] == old['violation_classes']['reversed'],
            'direction_only_compliance': cb['direction_compliance'],
        }

    # ── 样例（人工抽查用） ──
    for r, info in sorted(report['by_type'].items(),
                          key=lambda kv: -kv[1]['n_reversed']):
        if not info['n_reversed']:
            continue
        print(f"\n── {r}: 判为方向反转 {info['n_reversed']} 条，"
              f"Top 类型对 {info['reversed_pairs'][:3]} ──")
        for s in info['reversed_samples'][:ARGS.show]:
            print(f"   {s['source'][:34]!r}({s['source_type']}) → "
                  f"{s['target'][:34]!r}({s['target_type']})  {s['description'][:60]}")

    if unknown_names:
        print('\n=== 因端点缺失/类型 Unknown 而无法判定的高频名字 Top 8 ===')
        for nm, c in unknown_names.most_common(8):
            print(f'  {c:5d}  {nm[:56]!r}')

    report['total'] = dict(total_stats)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'\n报告已写出: {OUT}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
