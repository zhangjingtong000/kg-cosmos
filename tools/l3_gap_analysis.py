"""核实 L3 缺口的真实构成：31.6% 的边端点缺失，到底缺在哪、缺的是什么。

L3（关系端点层）= 68.4%，即 43867 条边里有 13843 条至少有一个端点不在
已抽取的节点集内。这份统计要回答三个问题，它们决定补救方案的选项：

  ① 缺的是源端还是目标端？（决定是否与关系方向有关）
  ② 哪些关系类型缺得最多？（决定是否可以先集中修几类）
  ③ 缺的端点在**原文里有名字但没被抽成节点**，还是**根本是抽取噪声**？
     —— 用"该名字是否在 17 篇快照的任一节点表里出现过"来区分：
        出现过 = 只是没进这一篇的节点集（可召回）
        从未出现 = 确实没被抽出来（要重抽取）
"""
import json
from collections import Counter, defaultdict
from pathlib import Path

GRAPH = Path(r'D:\kg-demo\data\corpus\graphs')
VERIFY = Path(r'D:\kg-demo\data\quality\relation-verify-all.json')


def main():
    d = json.loads(VERIFY.read_text(encoding='utf-8'))
    edges = d['edges']

    # 全部快照里的节点名（合并后）
    all_nodes = set()
    per_paper = defaultdict(set)
    for f in sorted(GRAPH.glob('*.json')):
        snap = json.loads(f.read_text(encoding='utf-8'))
        for n in snap.get('nodes', []):
            nm = (n.get('name') or '').strip()
            if nm:
                all_nodes.add(nm)
                per_paper[f.stem].add(nm)
    print(f'全部快照节点名（并集）: {len(all_nodes)}')

    miss_src = miss_tgt = miss_both = 0
    by_type = Counter()
    by_type_miss = Counter()
    never_seen = Counter()
    seen_other_paper = 0
    examples_never = []
    examples_other = []

    for e in edges:
        s, t = e['source'], e['target']
        ms = s not in all_nodes
        mt = t not in all_nodes
        by_type[e['type']] += 1
        if not (ms or mt):
            continue
        by_type_miss[e['type']] += 1
        if ms and mt:
            miss_both += 1
        elif ms:
            miss_src += 1
        else:
            miss_tgt += 1
        # 判断"从未出现"还是"只在别的论文里"
        for name, is_miss in ((s, ms), (t, mt)):
            if not is_miss:
                continue
            owner = [p for p, ns in per_paper.items() if name in ns]
            if owner:
                seen_other_paper += 1
                if len(examples_other) < 6:
                    examples_other.append((name, owner[:2]))
            else:
                never_seen[name] += 1
                if len(examples_never) < 10:
                    examples_never.append((e['type'], name))

    total = len(edges)
    miss_total = miss_src + miss_tgt + miss_both
    print(f'\n=== L3 缺口构成（共 {total} 条边，{miss_total} 条有缺失，'
          f'{miss_total/total*100:.1f}%）===')
    print(f'  仅缺源端 : {miss_src:6d}  ({miss_src/total*100:.1f}%)')
    print(f'  仅缺目标 : {miss_tgt:6d}  ({miss_tgt/total*100:.1f}%)')
    print(f'  两端都缺 : {miss_both:6d}  ({miss_both/total*100:.1f}%)')
    print(f'\n  缺失端点总计 {miss_src + miss_tgt + miss_both*2} 个次')

    print(f'\n  其中"在别的论文里是节点"的（可召回）: {seen_other_paper}')
    print(f'  其中"任何论文都没抽出来"的（需重抽取）: {sum(never_seen.values())}')

    print('\n=== 按关系类型：缺失率最高的 12 类（n>=50）===')
    rows = [(t, by_type_miss[t], by_type[t]) for t in by_type if by_type[t] >= 50]
    rows.sort(key=lambda r: -(r[1] / r[2]))
    print(f'  {"类型":16s} {"缺失":>7s} {"总数":>7s} {"缺失率":>8s}')
    for t, m, n in rows[:12]:
        print(f'  {t:16s} {m:7d} {n:7d} {m/n*100:7.1f}%')

    print('\n=== 缺失率最低的 5 类（说明哪些类型端点抽得好）===')
    for t, m, n in rows[-5:]:
        print(f'  {t:16s} {m:7d} {n:7d} {m/n*100:7.1f}%')

    print('\n=== 出现最多的"从未被抽成节点"的名字 Top 12 ===')
    for name, c in never_seen.most_common(12):
        print(f'  {c:5d}  {name[:70]!r}')

    print('\n=== 样例：只在别的论文里是节点的 ===')
    for name, owner in examples_other:
        print(f'  {name[:52]!r}  ← 出现在 {owner}')

    out = {
        'edges_total': total,
        'missing_edges': miss_total,
        'miss_source_only': miss_src, 'miss_target_only': miss_tgt,
        'miss_both': miss_both,
        'recoverable_seen_elsewhere': seen_other_paper,
        'never_extracted': sum(never_seen.values()),
        'by_type': {t: {'miss': by_type_miss[t], 'n': by_type[t],
                        'rate': round(by_type_miss[t] / by_type[t], 4)}
                    for t, m, n in rows},
        'top_never_extracted': never_seen.most_common(40),
    }
    p = Path(r'D:\kg-demo\data\quality\l3-gap-analysis.json')
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'\n报告已写出: {p}')


if __name__ == '__main__':
    main()
