"""给 L3「从边反建节点」算不同质量阈值下的覆盖率，用于选阈值。

已核实的事实：
  · 4754 个唯一缺失名字 / 15571 个端点次
  · 93.1% 的缺失名字（按次数 94.8%）在原文证据里逐字出现过 ⇒ 是真实体
  · 但它们从未被任何论文抽成节点 ⇒ 只能"从边反建"或"重新抽取"
  · 从邻居猜 type **不可靠**（实测把 'Document-Level Relation Extraction' 猜成 Person）

本脚本给出三档阈值的覆盖，并统计各档里"看起来不是实体"的名字占比，
供决定"反建到多宽"。
"""
import json
import re
from collections import Counter
from pathlib import Path

VERIFY = Path(r'D:\kg-demo\data\quality\relation-verify-all.json')
GRAPH = Path(r'D:\kg-demo\data\corpus\graphs')

# 明显的"非实体"词：论文零件、泛指词、碎片
JUNK = re.compile(
    r'^(summary|task|tasks|table|figure|fig\.?|section|appendix|abstract|'
    r'introduction|conclusion|results?|method|methods|approach|model|models|'
    r'example|examples|input|output|data|dataset|datasets|entity|entities|'
    r'relation|relations|knowledge|experts?|others?|misc|etc|na|none|unknown|'
    r'[a-z]|m|h|dr\.?|mr\.?|ms\.?)$', re.I)


def norm(s):
    return re.sub(r'[^a-z0-9]+', '', (s or '').lower())


def looks_like_entity(nm: str) -> bool:
    s = nm.strip()
    if len(s) < 3:
        return False
    if not any(ch.isalpha() for ch in s):
        return False
    if JUNK.match(s):
        return False
    # 单个短词且全小写常见词，基本不是实体
    if len(s) <= 4 and s.islower():
        return False
    return True


def main():
    edges = json.loads(VERIFY.read_text(encoding='utf-8'))['edges']
    known = set()
    for f in sorted(GRAPH.glob('*.json')):
        snap = json.loads(f.read_text(encoding='utf-8'))
        for n in snap.get('nodes', []):
            nm = (n.get('name') or '').strip()
            if nm:
                known.add(nm)

    occ = Counter()
    ev_hit = Counter()
    for e in edges:
        for side, other_side in (('source', 'target'), ('target', 'source')):
            nm = e[side]
            if nm in known:
                continue
            occ[nm] += 1
            ev = norm(e.get('evidence') or '') + norm(e.get('premise') or '')
            if norm(nm) and norm(nm) in ev:
                ev_hit[nm] += 1

    total_slots = sum(occ.values())
    print(f'缺失名字 {len(occ)} 个 / 端点次 {total_slots}\n')

    print(f'{"档位":34s} {"名字数":>6s} {"端点次":>7s} {"覆盖":>7s} {"其中像噪声的":>10s}')
    print('-' * 72)

    def row(label, pred):
        names = [n for n in occ if pred(n)]
        slots = sum(occ[n] for n in names)
        junk = sum(1 for n in names if not looks_like_entity(n))
        print(f'{label:34s} {len(names):6d} {slots:7d} {slots/total_slots*100:6.1f}% '
              f'{junk:6d} ({junk/max(1,len(names))*100:4.1f}%)')
        return names, slots

    row('全集（不筛）', lambda n: True)
    row('仅证据命中', lambda n: ev_hit[n] > 0)
    row('证据命中 + 形态过滤', lambda n: ev_hit[n] > 0 and looks_like_entity(n))
    row('证据命中 + 形态 + 出现>=2', lambda n: ev_hit[n] > 0 and looks_like_entity(n) and occ[n] >= 2)
    row('证据命中 + 形态 + 出现>=3', lambda n: ev_hit[n] > 0 and looks_like_entity(n) and occ[n] >= 3)
    row('证据命中 + 形态 + 出现>=5', lambda n: ev_hit[n] > 0 and looks_like_entity(n) and occ[n] >= 5)
    row('证据命中 + 形态 + 出现>=10', lambda n: ev_hit[n] > 0 and looks_like_entity(n) and occ[n] >= 10)

    print('\n=== 被形态过滤挡掉的 Top 20（确认这个过滤器在挡什么）===')
    blocked = sorted([n for n in occ if not looks_like_entity(n)],
                     key=lambda n: -occ[n])[:20]
    for n in blocked:
        print(f'  {occ[n]:5d}  {n[:56]!r}')

    print('\n=== 通过"证据命中+形态过滤"的 Top 15（确认留下的是什么）===')
    kept = sorted([n for n in occ if ev_hit[n] > 0 and looks_like_entity(n)],
                  key=lambda n: -occ[n])[:15]
    for n in kept:
        print(f'  {occ[n]:5d}  {n[:56]!r}')

    out = {
        'unique_missing': len(occ), 'slots': total_slots,
        'tiers': {
            'all': sum(occ.values()),
            'evidence_hit': sum(occ[n] for n in occ if ev_hit[n] > 0),
            'evidence+shape': sum(occ[n] for n in occ if ev_hit[n] > 0 and looks_like_entity(n)),
            'evidence+shape+occ2': sum(occ[n] for n in occ if ev_hit[n] > 0 and looks_like_entity(n) and occ[n] >= 2),
            'evidence+shape+occ3': sum(occ[n] for n in occ if ev_hit[n] > 0 and looks_like_entity(n) and occ[n] >= 3),
            'evidence+shape+occ5': sum(occ[n] for n in occ if ev_hit[n] > 0 and looks_like_entity(n) and occ[n] >= 5),
        },
    }
    p = Path(r'D:\kg-demo\data\quality\l3-backfill-tiers.json')
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'\n报告已写出: {p}')


if __name__ == '__main__':
    main()
