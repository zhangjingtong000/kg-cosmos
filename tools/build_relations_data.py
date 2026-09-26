# -*- coding: utf-8 -*-
r"""生成 `web/kg-data/relations.json` —— 16 类关系的**可读说明**（K4 的数据侧）。

## 为什么要它

界面上一堆 `PART_OF` / `APPLIES_TO` / `MEASURES`，**用户看不懂区别**。
而项目其实**已经有完整的语义定义**，只是躺在 `src/ontology.py` 里没上界面：

    RELATION_ENDPOINT_CONSTRAINTS[type]['semantics']  ← 中文语义（一句话）
    RELATION_ENDPOINT_CONSTRAINTS[type]['kind']       ← directed / symmetric
    RELATION_ENDPOINT_CONSTRAINTS[type]['source_core'] / ['target_core']
                                                      ← 端点类型约束
    REL_HYPOTHESIS_TEMPLATE[type]                     ← NLI 判定用的英文假设模板
    REL_PHRASE_TEMPLATE[type]                         ← 短语模板

**这次的教训**：这些是"已经算出来、界面上从没说过"的又一批东西，
  与 I8 那三条同类（见 `docs/SHOWCASE_FEATURES.md`）。

## 顺带把"方向性"显式化

`kind` 有三类：`directed`（有向）/ `symmetric`（对称，定义上不判方向）/
其余。**这解释了 L7 为什么只对 3 类做方向校验** —— 因为其余类型
**定义上就没有反向可判**。这是"关系类型说明"里最该讲清的一点。

用法：
    python tools/build_relations_data.py
"""
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))

OUT = ROOT / 'web/kg-data/relations.json'
GRAPHS = ROOT / 'data/corpus/graphs'

import ontology as O  # noqa: E402


def main():
    cons = O.RELATION_ENDPOINT_CONSTRAINTS
    hypo = getattr(O, 'REL_HYPOTHESIS_TEMPLATE', {})
    phrase = getattr(O, 'REL_PHRASE_TEMPLATE', {})

    # 实测条数（治理前全量口径，与 data/corpus/graphs 一致）
    cnt = Counter()
    doc_cnt = Counter()          # 关系类型 -> 出现在几篇里
    for p in sorted(GRAPHS.glob('*.json')):
        try:
            d = json.loads(p.read_text(encoding='utf-8-sig'))
        except Exception:
            continue
        seen = set()
        for e in (d.get('edges') or []):
            t = str(e.get('type') or '?')
            cnt[t] += 1
            # 字段错位的那批：description 里才是真类型
            desc = str(e.get('description') or '').strip()
            if t not in O.RELATION_TYPES and desc in O.RELATION_TYPES:
                cnt[t] -= 1
                cnt[desc] += 1
                seen.add(desc)
            else:
                seen.add(t)
        for t in seen:
            doc_cnt[t] += 1

    total = sum(cnt.get(t, 0) for t in O.RELATION_TYPES)

    rels = []
    for t in sorted(O.RELATION_TYPES):
        c = cons.get(t) or {}
        src_core = sorted(c.get('source_core') or [])
        tgt_core = sorted(c.get('target_core') or [])
        rels.append({
            'type': t,
            'semantics': c.get('semantics') or '',
            'kind': c.get('kind') or 'directed',
            'hypothesis': hypo.get(t) or '',
            'phrase': phrase.get(t) or '',
            'source_core': src_core,
            'target_core': tgt_core,
            'count': cnt.get(t, 0),
            'papers': doc_cnt.get(t, 0),
            'share': round(cnt.get(t, 0) / total * 100, 2) if total else 0,
        })
    rels.sort(key=lambda r: -r['count'])

    kinds = Counter(r['kind'] for r in rels)
    payload = {
        'generated_from': ['src/ontology.py（唯一来源）',
                           'data/corpus/graphs/*.json（实测条数）'],
        'caliber': ('条数是**治理前全量**口径（与 data/corpus/graphs 一致）；'
                    '已按同期修正规则把"字段错位"的边归位到正确类型。'
                    '"papers" = 该类型出现在几篇论文里。'
                    # ⚠️ 2026-09-26 补：**这条说明此前只讲了"经过什么处理"，
                    #    没讲"这个数字的定义"** —— 于是 `total_edges` 被当成
                    #    "图的全部边数"引用过（45708），而它其实是
                    #    `sum(cnt[t] for t in RELATION_TYPES)`。
                    #    定义不同的两个数放在一起当同一件事，就是口径事故的起点。
                    '`stats.total_edges` 的定义是 **本体 16 类各自的条数之和**'
                    '（= sum over `RELATION_TYPES`），**不等于图的全部边数**：'
                    '还有 48 条边修正后 type 仍越界（`CONTAINS` / `friend` / `HAS` …），'
                    '它们不计入本汇总。要"图的全部边数"请用 45708'
                    '（`tools/graph_source.load_graph()` 的 `len(edges)`）。'),
        'stats': {
            'types': len(rels),
            'total_edges': total,
            'by_kind': dict(kinds),
            'directed': kinds.get('directed', 0),
            'symmetric': kinds.get('symmetric', 0),
        },
        'relations': rels,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding='utf-8')

    print(f'已写 {OUT.relative_to(ROOT)}（{OUT.stat().st_size / 1024:.1f} KB）')
    print()
    print('=' * 84)
    print(f'{len(rels)} 类关系 · 合计 {total} 条边 · '
          f'有向 {kinds.get("directed", 0)} 类 / 对称 {kinds.get("symmetric", 0)} 类')
    print('=' * 84)
    print(f'{"类型":<15}{"条数":>7}{"占比":>7}{"篇数":>5}  方向      语义')
    for r in rels:
        print(f'{r["type"]:<15}{r["count"]:>7}{r["share"]:>6.2f}%{r["papers"]:>5}  '
              f'{r["kind"]:<9} {r["semantics"][:44]}')
    print()
    print('★ 「L7 只对 3 类做方向校验」的原因就在这里：')
    print(f'  {kinds.get("symmetric", 0)} 类是 symmetric（定义上不判方向），'
          f'另有若干类语义不可逆但两端类型重叠 ⇒ 只有 3 类有可靠的方向指纹。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
