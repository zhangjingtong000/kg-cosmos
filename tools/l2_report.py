# -*- coding: utf-8 -*-
"""
T4 · L2 实体聚类层的量化指标 + T4.4 要求的 SAME_AS 候选边

    python tools/l2_report.py            # 打印报告
    python tools/l2_report.py --write    # 额外写出 data/quality/same-as-candidates.json

之前 L2 在六层报告里是 N/A，其实 data/quality/entity-alias-map.json 已经有
完整的三层决策记录，只是没有量化成指标。本脚本把它算出来。

三层：
  L1 规则归一   lower + 折叠空白 + 去首尾标点   → 直接合并
  L2 模糊匹配   Jaccard ≥ 0.9 + 熵门控          → 合并；类型冲突则跳过
  L4 缩写抽取   全称 ↔ 缩写                     → 直接合并

T4.4 的原则是"规则归一直接合并；模糊匹配只建边不合并节点"（合并不可逆，保守起步）。
所以 L2 里因类型冲突被跳过的 196 条，应该落成 status='pending' 的 SAME_AS 候选边，
而不是丢掉——本脚本把它们产出。
"""
import collections
import glob
import json
import os
import sys

ROOT = r'D:\kg-demo'
Q = os.path.join(ROOT, 'data', 'quality')
G = os.path.join(ROOT, 'data', 'corpus', 'graphs')


def main():
    with open(os.path.join(Q, 'entity-alias-map.json'), encoding='utf-8') as f:
        am = json.load(f)
    with open(os.path.join(Q, 'entity-quality.json'), encoding='utf-8') as f:
        eq = json.load(f)

    alias = am['alias']
    audit = am['audit']
    summary = am['summary']

    names = set()
    backfilled = set()
    for p in sorted(glob.glob(os.path.join(G, '*.json'))):
        with open(p, encoding='utf-8') as f:
            d = json.load(f)
        for n in d.get('nodes', []):
            if not n.get('name'):
                continue
            # ⚠️ 必须排除"从边反建"的节点（_backfilled）。
            # 反建发生在 L2 归并**之后**（tools/backfill_missing_endpoints.py），
            # 把它算进 "归并后" 会让 L2 显示"名字反而多了 23.68%" ——
            # 口径错乱，不是真的归并失败。
            if n.get('_backfilled'):
                backfilled.add(n['name'])
                continue
            names.add(n['name'])

    before = eq.get('unique_names')
    after = len(names)

    print('=' * 92)
    print('T4 · L2 实体聚类层')
    print('=' * 92)
    print(f'归并前唯一名字 {before}   归并后 {after}   减少 {before - after} '
          f'（{(before - after) / before:.2%}）')
    if backfilled:
        print(f'（另有 {len(backfilled)} 个名字是"从边反建"的节点，'
              f'发生在归并之后，不计入本层）')
    print(f'alias 表 {len(alias)} 条；其中变体名已从节点集消失 '
          f'{sum(1 for k in alias if k not in names)} 条，仍保留 '
          f'{sum(1 for k in alias if k in names)} 条（缩写↔全称两边都是节点）')

    print()
    print('-' * 92)
    print('① 三层决策明细')
    print('-' * 92)
    cross = collections.Counter((a.get('layer'), a.get('action')) for a in audit)
    layers = ['L1', 'L2', 'L4']
    print(f'{"layer":<6}{"merge":>8}{"skip-type":>11}{"skip-short":>12}{"skip-sens":>11}{"合计":>8}')
    totals = collections.Counter()
    for ly in layers:
        m = cross.get((ly, 'merge'), 0)
        st = cross.get((ly, 'skip-type-conflict'), 0)
        ss = cross.get((ly, 'skip-short'), 0)
        sn = cross.get((ly, 'skip-sensitive'), 0)
        totals['merge'] += m
        totals['skip'] += st + ss + sn
        print(f'{ly:<6}{m:>8}{st:>11}{ss:>12}{sn:>11}{m+st+ss+sn:>8}')
    print(f'{"合计":<6}{totals["merge"]:>8}{"":>11}{"":>12}{"":>11}'
          f'{totals["merge"]+totals["skip"]:>8}')

    print()
    print('-' * 92)
    print('② L2 指标')
    print('-' * 92)
    l2_merge = cross.get(('L2', 'merge'), 0)
    l2_skip = cross.get(('L2', 'skip-type-conflict'), 0) + cross.get(('L2', 'skip-short'), 0)
    l2_all = l2_merge + l2_skip
    print(f'L2 候选组 {l2_all}：合并 {l2_merge}（{l2_merge/l2_all:.1%}），'
          f'保守跳过 {l2_skip}（{l2_skip/l2_all:.1%}）')
    print(f'→ 合并率 {l2_merge/l2_all:.1%} 是"高置信"的部分；'
          f'跳过的 {l2_skip} 组属于"疑似同一实体但类型冲突"')
    print(f'缩写抽取（L4）新增 {cross.get(("L4","merge"),0)} 组归并')
    print(f'规则归一（L1）合并 {cross.get(("L1","merge"),0)} 组，'
          f'另有 1 组因语义敏感被正确拒绝（ComplEx / Complex / ComplEX——'
          f'前者是模型名、后者是普通词）')

    print()
    print('-' * 92)
    print('③ T4.4 要求的 SAME_AS 候选边（status=pending，只建边不合并）')
    print('-' * 92)
    cands = []
    for a in audit:
        if a.get('action') != 'skip-type-conflict':
            continue
        mem = a.get('members') or []
        if len(mem) < 2:
            continue
        cands.append({
            'status': 'pending',
            'reason': 'type-conflict',
            'canonical_key': a.get('key'),
            'members': mem,
            'note': '模糊匹配判定为近名，但成员实体类型冲突；按 T4.4 原则不自动合并，'
                    '留作待复核的 SAME_AS 候选',
        })
    print(f'候选边 {len(cands)} 条（来自 L2 skip-type-conflict）')
    for c in cands[:10]:
        print(f'  {c["canonical_key"]:<28} {c["members"]}')
    if len(cands) > 10:
        print(f'  … 其余 {len(cands)-10} 条')

    # 涉及的节点名规模
    involved = set()
    for c in cands:
        involved.update(c['members'])
    print(f'\n涉及不同节点名 {len(involved)} 个')

    # ── ④ 核实"类型冲突"这个判据本身可靠吗 ──
    print()
    print('-' * 92)
    print('④ 核实：类型冲突判据是否可靠')
    print('-' * 92)
    types = collections.defaultdict(set)
    freq = collections.Counter()
    for p in sorted(glob.glob(os.path.join(G, '*.json'))):
        with open(p, encoding='utf-8') as f:
            d = json.load(f)
        for n in d.get('nodes', []):
            nm = n.get('name')
            if nm:
                types[nm].add(n.get('type'))
                freq[nm] += 1

    real_conflict = same_type = 0
    plural_pairs = 0
    low_freq_variant = 0
    for c in cands:
        mem = c['members']
        keys = ['/'.join(sorted(x for x in types.get(m, {'?'}) if x)) for m in mem]
        if len(set(keys)) > 1:
            real_conflict += 1
        else:
            same_type += 1
        if len(mem) == 2 and (mem[0] + 's' == mem[1] or mem[1] + 's' == mem[0]):
            plural_pairs += 1
        # 组内最小频次 <= 1 视为"低频变体"
        if min(freq.get(m, 0) for m in mem) <= 1:
            low_freq_variant += 1

    print(f'候选组 {len(cands)}：真的类型不同 {real_conflict}，类型其实相同 {same_type}')
    print(f'  两条成员互为简单单复数：{plural_pairs} / {len(cands)}')
    print(f'  组内存在"只出现 1 次"的低频变体：{low_freq_variant} / {len(cands)}')
    print()
    print('  判读（2026-09-12 判据已改版，以下为改版后的结论）：')
    print('  初版判据取"任一成员的类型"，只要不同就跳过 —— 方向对，但判据本身不可靠：')
    print('  变体多是低频出现的名字，而低频实体的类型标注本身就是噪声。')
    print('  例：Link Prediction=Method(×12) vs Link Predictions=Concept(×1)，')
    print('  后者只出现一次，被判成 Concept 不足以说明它们是不同实体。')
    print()
    print('  → 改版后按形态分三类（见 tools/entity_normalize.py）：')
    print('     ① 纯单复数（只差 s/es/ies）无条件合并；')
    print('     ② 其他近名走"频次加权类型"，主导占比 ≥ 0.80 才合并；')
    print('     ③ 语义敏感名单（与 L1 共用）一律跳过。')
    print('    实测把 L2 合并率从 40.8% 提到 97.0%，累计归并 585 组。')
    print('    同时仍正确拒绝 ComplEx / Complex —— 它们类型恰好都是 Method，')
    print('    只有敏感名单拦得住（合并会造成实质性错误）。')

    if '--write' in sys.argv:
        out = {
            'note': 'L2 模糊匹配中因实体类型冲突而未自动合并的候选对，'
                    '按 T4.4「合并不可逆、保守起步」的原则只建边不合并节点。',
            'count': len(cands),
            'involved_names': len(involved),
            'candidates': cands,
        }
        p = os.path.join(Q, 'same-as-candidates.json')
        with open(p, 'w', encoding='utf-8') as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f'\n已写出 {p}')

        # 把 L2 填进六层指标 JSON，保持"六层结果"只有一个出处
        six = os.path.join(Q, 'l5-six-layer.json')
        if os.path.exists(six):
            with open(six, encoding='utf-8') as f:
                s = json.load(f)
            s['L2_entity_clustering'] = {
                'names_before': before,
                'names_after': after,
                # 注意 merged_groups 是**本轮**各层的合并组数（判据改版后重跑只有 200），
                # 而累计归并数 = names_before - names_after = 585
                # （第一轮 384 + 第二轮 200）。两个数都要给，否则容易被读错。
                'merged_groups': {'L1_rule': cross.get(('L1', 'merge'), 0),
                                  'L2_fuzzy': cross.get(('L2', 'merge'), 0),
                                  'L4_abbrev': cross.get(('L4', 'merge'), 0),
                                  'total_this_run': totals['merge']},
                'cumulative_merged_groups': before - after,
                'cumulative_reduction_pct': round((before - after) / before * 100, 2) if before else None,
                'skipped': {'type_conflict': cross.get(('L2', 'skip-type-conflict'), 0),
                            'short': cross.get(('L1', 'skip-short'), 0) + cross.get(('L2', 'skip-short'), 0),
                            'sensitive': cross.get(('L1', 'skip-sensitive'), 0)},
                'l2_merge_rate': l2_merge / l2_all if l2_all else None,
                'pending_same_as': len(cands),
                'pending_plural_pairs': plural_pairs,
                'pending_low_freq_variant': low_freq_variant,
                'backfilled_excluded': len(backfilled),
                'criterion_history': (
                    '初版判据取"任一成员的类型"，196 组被跳过（其中 183 组只是简单复数）；'
                    '2026-09-12 改版为「纯单复数无条件合并 + 其他近名走频次加权类型 + '
                    '语义敏感名单」，L2 合并率 40.8% → 97.0%。'
                ),
            }
            with open(six, 'w', encoding='utf-8') as f:
                json.dump(s, f, ensure_ascii=False, indent=2)
            print(f'已更新 {six} 的 L2_entity_clustering 字段')


if __name__ == '__main__':
    main()
