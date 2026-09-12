"""模拟「从边反建节点」之后 L1 与 L3 会变成什么样。

关键点：反建出来的节点**没有自己的 evidence 字段**，
而 L1 测的是"实体名是否字面出现在自己的证据引文里" ——
若不处理，L3 上去了但 L1 会掉。好在被反建的名字有 93% 本来就出现在
**引用它的那些边的 evidence 里**，所以可以把这些 evidence 汇总给新节点。
本脚本量化两种做法（带证据 / 不带证据）的后果。
"""
# 路径以本文件为锚点解析，不写死盘符——演示机不一定把仓库放在 D 盘。
from pathlib import Path as _Path
_HERE = _Path(__file__).resolve()
ROOT = _HERE.parent.parent          # tools/*.py 与 src/*.py 都在根目录下一层
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

VERIFY = ROOT / 'data/quality/relation-verify-all.json'
GRAPH = ROOT / 'data/corpus/graphs'

JUNK = re.compile(
    r'^(summary|task|tasks|table|figure|fig\.?|section|appendix|abstract|'
    r'introduction|conclusion|results?|method|methods|approach|model|models|'
    r'example|examples|input|output|data|dataset|datasets|entity|entities|'
    r'relation|relations|knowledge|experts?|others?|misc|etc|na|none|unknown|'
    r'[a-z]|m|h|dr\.?|mr\.?|ms\.?)$', re.I)


def norm(s):
    return re.sub(r'[^a-z0-9]+', '', (s or '').lower())


def looks_like_entity(nm):
    s = nm.strip()
    if len(s) < 3 or not any(ch.isalpha() for ch in s):
        return False
    if JUNK.match(s):
        return False
    if len(s) <= 4 and s.islower():
        return False
    return True


def main():
    edges = json.loads(VERIFY.read_text(encoding='utf-8'))['edges']

    known = set()
    node_ev = {}          # 已有节点 -> 它自己的证据正文（归一化）
    for f in sorted(GRAPH.glob('*.json')):
        snap = json.loads(f.read_text(encoding='utf-8'))
        for n in snap.get('nodes', []):
            nm = (n.get('name') or '').strip()
            if not nm:
                continue
            known.add(nm)
            if nm not in node_ev:
                # 与 tools/l5_report.py 保持**逐字相同**的口径：
                # 取 evidence 数组的第一个元素的 text 字段。
                # 初版把整个数组 str() 了，算出的 L1 与权威报告差 12 个点
                # （80.9% vs 92.8%），会让下面的对比数字失去意义。
                ev = (n.get('evidence') or [{}])[0]
                node_ev[nm] = norm(ev.get('text') or '')

    # 候选反建名字：证据命中 + 形态过滤
    occ = Counter()
    ev_hit = Counter()
    backfill_ev = defaultdict(list)     # 新名字 -> 引用它的边的 evidence（归一化）
    for e in edges:
        ev = norm(e.get('evidence') or '') + norm(e.get('premise') or '')
        for side in ('source', 'target'):
            nm = e[side]
            if nm in known:
                continue
            occ[nm] += 1
            if norm(nm) and norm(nm) in ev:
                ev_hit[nm] += 1
                if len(backfill_ev[nm]) < 3:
                    backfill_ev[nm].append(ev)
    cand = {n for n in occ if ev_hit[n] > 0 and looks_like_entity(n)}
    print(f'待反建名字: {len(cand)}')

    # ── 原状 ──
    total = len(edges)
    l3_before = sum(1 for e in edges if e['source'] in known and e['target'] in known)
    # L1 原状：节点名是否出现在**自己的** evidence 里
    l1_tot = len(known)
    l1_hit = sum(1 for n in known if norm(n) and norm(n) in node_ev.get(n, ''))
    print(f'\n原状: L3 = {l3_before}/{total} = {l3_before/total*100:.1f}%'
          f'   L1 = {l1_hit}/{l1_tot} = {l1_hit/l1_tot*100:.1f}%')

    # ── 反建后 ──
    new_known = known | cand
    l3_after = sum(1 for e in edges if e['source'] in new_known and e['target'] in new_known)
    print(f'\n反建后 L3 = {l3_after}/{total} = {l3_after/total*100:.1f}%'
          f'   （+{(l3_after-l3_before)/total*100:.1f} 个百分点）')

    # 新节点的 L1：带证据 vs 不带
    good_new = sum(1 for n in cand
                   if norm(n) and any(norm(n) in ev for ev in backfill_ev.get(n, [])))
    l1_new_with = l1_hit + good_new
    l1_new_without = l1_hit
    tot_new = l1_tot + len(cand)
    print(f'\n新节点 L1 对比（新节点共 {len(cand)} 个）：')
    print(f'  不给新节点写 evidence : L1 = {l1_new_without}/{tot_new} = '
          f'{l1_new_without/tot_new*100:.1f}%（比原状掉 '
          f'{(l1_hit/l1_tot - l1_new_without/tot_new)*100:.1f} 个点）')
    print(f'  把引用它的边的 evidence 汇总给它: L1 = {l1_new_with}/{tot_new} = '
          f'{l1_new_with/tot_new*100:.1f}%'
          f'（其中 {good_new}/{len(cand)} 个能自证）')

    # 剩余的 L3 缺口是什么
    still = [e for e in edges
             if e['source'] not in new_known or e['target'] not in new_known]
    left_names = Counter()
    for e in still:
        for side in ('source', 'target'):
            if e[side] not in new_known:
                left_names[e[side]] += 1
    print(f'\n剩余缺口 {len(still)} 条边，涉及 {len(left_names)} 个名字')
    print('  剩下的 Top 10（都是形态过滤挡掉的，即非实体或碎片）：')
    for n, c in left_names.most_common(10):
        print(f'    {c:5d}  {n[:56]!r}')

    out = {
        'backfill_names': len(cand),
        'l3_before_pct': round(l3_before / total * 100, 2),
        'l3_after_pct': round(l3_after / total * 100, 2),
        'l1_before_pct': round(l1_hit / l1_tot * 100, 2),
        'l1_after_without_evidence_pct': round(l1_new_without / tot_new * 100, 2),
        'l1_after_with_evidence_pct': round(l1_new_with / tot_new * 100, 2),
        'new_nodes_self_evidenced': good_new,
        'remaining_gap_edges': len(still),
        'remaining_gap_names': len(left_names),
    }
    p = ROOT / 'data/quality/l3-backfill-simulation.json'
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'\n报告已写出: {p}')


if __name__ == '__main__':
    main()
