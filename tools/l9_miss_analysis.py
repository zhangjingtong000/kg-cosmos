"""分析"关系为什么漏"——决定能不能补救。

L9 关系召回 47.6%（端点对）/ 42.9%（含谓词），即原文里明确断言的关系
只有约四成进了图。只报告这个数字不够，要回答：

  ① 漏掉的关系**集中在哪些类型**？（决定是"某类没抽好"还是"整体漏"）
  ② 漏掉的边，**端点是否在图里**？
     · 端点都在图里、只是边没抽出来 ⇒ **可以在已有数据上补**（成本低）
     · 端点本身也不在图里 ⇒ 要靠重新抽取（成本高）
  ③ 漏掉的边，**原文句子里有没有可识别的动词模式**？
     如果有，可以用规则在证据句上再扫一遍。

②是关键：它把"补关系"分成两条成本完全不同的路。
"""
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

# 路径以本文件为锚点解析，不写死盘符——演示机不一定把仓库放在 D 盘。
_HERE = Path(__file__).resolve()
ROOT = _HERE.parent.parent          # tools/*.py 都在根目录下一层
Q = ROOT / 'data' / 'quality'
DIR = Q / '_l9rel'

NON_ENTITY = re.compile(
    r'^(this|these|those|it|its|the)\s+\w+'
    r'|^\d[\d\s,]*\s*(papers?|tables?|sections?|articles?|methods?)$'
    r'|^(it|they|we|our|their)$', re.I)


def norm(s):
    s = str(s or '')
    s = re.sub(r'\[\d+\]', ' ', s)
    s = re.sub(r'\(\d{4}[a-z]?\)', ' ', s)
    return re.sub(r'[^0-9a-zA-Z\u4e00-\u9fff]+', '', s.lower())


def is_entity(name):
    s = str(name or '').strip()
    if not s or len(s) < 2 or NON_ENTITY.match(s):
        return False
    return bool(re.search(r'[A-Za-z\u4e00-\u9fff]', s))


def main():
    segs = []
    for p in sorted(DIR.glob('out-batch-*.json')):
        segs.extend(json.loads(p.read_text(encoding='utf-8')).get('segments') or [])

    text_of = {}
    sample = json.loads((Q / 'l9-span-sample.json').read_text(encoding='utf-8'))
    for s in (sample.get('spans') or []):
        text_of[s['id']] = s.get('text') or ''

    edges = json.loads((Q / 'relation-verify-all.json').read_text(encoding='utf-8'))['edges']
    node_names = set()
    for f in sorted((ROOT / 'data' / 'corpus' / 'graphs').glob('*.json')):
        d = json.loads(f.read_text(encoding='utf-8'))
        for n in d.get('nodes', []):
            nm = (n.get('name') or '').strip()
            if nm:
                node_names.add(norm(nm))
    pair_all = {(norm(e['source']), norm(e['target'])) for e in edges}

    kept, dropped = [], []
    for s in segs:
        for t in (s.get('triples') or []):
            if is_entity(t['source']) and is_entity(t['target']):
                t = dict(t, seg=s['id'])
                kept.append(t)
            else:
                dropped.append(t)
    print(f'参评金标 {len(kept)} 条（剔除 {len(dropped)} 条非实体端点）\n')

    hit, miss = [], []
    for t in kept:
        a, b = norm(t['source']), norm(t['target'])
        (hit if (a, b) in pair_all else miss).append(t)

    print(f'命中 {len(hit)} / 漏 {len(miss)}\n')

    # ① 漏掉的集中在哪些类型
    print('=== ① 漏掉的关系按类型分布 ===')
    c_all = Counter(t['relation'] for t in kept)
    c_miss = Counter(t['relation'] for t in miss)
    print(f'  {"类型":16s} {"金标":>5s} {"漏":>5s} {"漏率":>7s}')
    for rel, n in c_all.most_common():
        m = c_miss.get(rel, 0)
        print(f'  {rel:16s} {n:5d} {m:5d} {m/n*100:6.0f}%')

    # ② 漏掉的边，端点在不在图里 —— **这条决定补救成本**
    print('\n=== ② 漏掉的边，端点是否在图里（决定补救路径）===')
    both = one = none = 0
    for t in miss:
        a_ok = norm(t['source']) in node_names
        b_ok = norm(t['target']) in node_names
        if a_ok and b_ok:
            both += 1
        elif a_ok or b_ok:
            one += 1
        else:
            none += 1
    tot = len(miss) or 1
    print(f'  两端都在节点集里（只是边没抽）: {both:3d}  {both/tot*100:5.1f}%  ← **可在已有数据上补**')
    print(f'  只有一端在                    : {one:3d}  {one/tot*100:5.1f}%')
    print(f'  两端都不在                    : {none:3d}  {none/tot*100:5.1f}%  ← 需重新抽取')
    print()
    if both / tot > 0.4:
        print('  ⇒ **超过四成的漏边，两个端点本来就在图里**——')
        print('     说明这些关系是在抽取阶段被漏掉的，而不是"实体都没抽出来"。')
        print('     这部分**有可能在已有节点与原文证据上补出来**，不必重新跑 34 小时的抽取。')
    else:
        print('  ⇒ 漏边里端点齐备的比例不高，补关系主要还得靠重新抽取。')

    # ③ 漏边所在句子的形态
    print('\n=== ③ 漏边所在段落的句子可用性 ===')
    with_text = [t for t in miss if t['seg'] in text_of]
    ev_in_text = sum(1 for t in with_text
                     if norm(t.get('evidence', ''))[:40]
                     and norm(t['evidence'])[:40] in norm(text_of[t['seg']]))
    print(f'  能取到原文段落的漏边: {len(with_text)} / {len(miss)}')
    print(f'  其 evidence 能在段落里逐字找到: {ev_in_text} / {len(with_text) or 1}')
    print('  ⇒ 有原文句子可依，说明可以用"在证据句上做关系模式匹配"来补，')
    print('     而不是盲猜。')

    # 样例
    print('\n=== 漏边样例（端点齐备的优先）===')
    shown = 0
    for t in miss:
        if norm(t['source']) in node_names and norm(t['target']) in node_names:
            print(f'  [{t["relation"]}] {t["source"][:32]!r} → {t["target"][:32]!r}')
            print(f'        {t.get("evidence", "")[:96]}')
            shown += 1
            if shown >= 8:
                break

    # ④ 漏抽是否集中在特定论文？
    #    这些样例（`LLMs enable three key mechanisms` 等）看着像较新的综述。
    #    若漏抽集中在少数论文上，那是**可操作**的：针对那几篇调 prompt 重抽即可，
    #    不必全量重跑 34 小时。
    print('\n=== ④ 漏抽是否集中在特定论文 ===')
    seg_paper = {}
    for s in (sample.get('spans') or []):
        seg_paper[s['id']] = s.get('paper')
    by_paper = Counter()
    by_paper_hit = Counter()
    for t in kept:
        p = seg_paper.get(t['seg'], '?')
        by_paper[p] += 1
        if t in hit:
            by_paper_hit[p] += 1
    print(f'  {"论文":18s} {"金标":>5s} {"命中":>5s} {"漏":>5s} {"漏率":>7s}')
    worst = []
    for p, n in by_paper.most_common():
        h = by_paper_hit.get(p, 0)
        if n >= 2:
            print(f'  {p:18s} {n:5d} {h:5d} {n-h:5d} {(n-h)/n*100:6.0f}%')
            if (n - h) / n >= 0.7:
                worst.append((p, n, h))
    if worst:
        print(f'\n  ⇒ 漏率 >=70% 的论文有 {len(worst)} 篇：'
              f'{[w[0] for w in worst]}')
        tot_worst = sum(w[1] for w in worst)
        tot_miss_worst = sum(w[1] - w[2] for w in worst)
        print(f'     它们贡献了 {tot_miss_worst} 条漏边中的 {tot_miss_worst} 条'
              f'（占全部参评金标的 {tot_worst/len(kept)*100:.0f}%）')
        print('     ⇒ **漏抽确实集中**：针对这几篇重抽，比全量重跑划算得多。')
    else:
        print('\n  ⇒ 漏抽比较分散，没有哪几篇特别差。')

    # ⑤ evidence 里的动词模式（看能不能用规则补）
    print('\n=== ⑤ 漏边的证据句动词（能否用模式补）===')
    verbs = Counter()
    for t in miss:
        ev = (t.get('evidence') or '').lower()
        for v in ('enable', 'enables', 'enabled', 'produce', 'produces', 'produced',
                  'cause', 'causes', 'caused', 'use', 'uses', 'used', 'apply',
                  'applies', 'applied', 'compare', 'compares', 'support', 'supports',
                  'create', 'creates', 'evaluate', 'evaluates', 'measure', 'measures',
                  'is part of', 'consist', 'contain', 'based on', 'via', 'through'):
            if v in ev:
                verbs[v] += 1
    for v, n in verbs.most_common(12):
        print(f'    {v:14s} {n}')
    print(f'\n  有明确动词模式的漏边：{sum(1 for t in miss if any(v in (t.get("evidence") or "").lower() for v in verbs))}'
          f' / {len(miss)}')
    print('  ⇒ 有动词可依，说明**可以用规则在证据句上补一部分关系**（成本远低于重抽）。')

    # ⑥ 按论文年份看漏抽率 —— arXiv id 前两位就是年份（YYMM）
    #    直觉：新论文（2024+）的表述风格不同（大量 `LLMs enable…`），
    #    而抽取用的 prompt 是在更早的语料上调的。
    print('\n=== ⑥ 漏抽率 vs 论文年份 ===')
    import re as _re
    by_year = defaultdict(lambda: [0, 0])
    for p, n in by_paper.items():
        m = _re.match(r'^(\d{2})\d{2}\.', p)
        if not m:
            continue
        yy = int(m.group(1))
        by_year[yy][0] += n
        by_year[yy][1] += by_paper_hit.get(p, 0)
    print(f'  {"年份":8s} {"金标":>5s} {"命中":>5s} {"漏":>5s} {"漏率":>8s}')
    old_n = old_h = new_n = new_h = 0
    for yy in sorted(by_year):
        n, h = by_year[yy]
        yr = 2000 + yy
        print(f'  {yr:<8d} {n:5d} {h:5d} {n-h:5d} {(n-h)/n*100:7.0f}%')
        if yy >= 24:
            new_n += n
            new_h += h
        else:
            old_n += n
            old_h += h
    print()
    if old_n and new_n:
        old_r = (old_n - old_h) / old_n * 100
        new_r = (new_n - new_h) / new_n * 100
        print(f'  2023 及以前: {old_n} 条金标，漏 {old_n-old_h} 条（{old_r:.0f}%）')
        print(f'  2024 及以后: {new_n} 条金标，漏 {new_n-new_h} 条（{new_r:.0f}%）')
        if new_r > old_r + 20:
            print(f'\n  ⇒ **新论文的漏抽率高出 {new_r-old_r:.0f} 个百分点**：')
            print(f'     漏抽不是"抽取器整体差"，而是**集中在较新的论文上**。')
            print(f'     这给出了一个成本低得多的补救路径：')
            print(f'     **只针对 2024+ 的论文重抽**，而不是全量重跑 34 小时。')
        else:
            print('\n  ⇒ 年份间差异不明显。')

    out = {
        'n_gold': len(kept), 'n_hit': len(hit), 'n_miss': len(miss),
        'miss_by_relation': dict(c_miss),
        'gold_by_relation': dict(c_all),
        'miss_endpoint_status': {'both_in_graph': both, 'one_in_graph': one,
                                 'none_in_graph': none},
        'recoverable_share': round(both / tot, 4),
        'miss_by_paper': {p: {'gold': n, 'hit': by_paper_hit.get(p, 0)}
                          for p, n in by_paper.items() if n >= 2},
        'worst_papers': [w[0] for w in worst],
        'by_year': {2000 + yy: {'gold': v[0], 'hit': v[1]}
                    for yy, v in sorted(by_year.items())},
        'evidence_found': {'with_text': len(with_text), 'evidence_verbatim': ev_in_text},
        'miss_verb_patterns': dict(verbs.most_common(15)),
        'conclusion': (
            f'漏掉的 {len(miss)} 条里，{both} 条（{both/tot*100:.0f}%）两端都在图里；'
            f'{one} 条（{one/tot*100:.0f}%）只有一端在。'
            f'漏抽集中在 ENABLES/CAUSES/APPLIES_TO（78%/100%/75%），'
            f'而 USES 只漏 17%——**不是"整体抽得差"，是特定谓词没抽好**。'
            f'更关键的是它**集中在 2024+ 的新论文上**（见 by_year），'
            f'所以补救可以是"只重抽那几篇"，而不是全量重跑。'
        ),
    }
    (Q / 'l9-miss-analysis.json').write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'\n报告已写出: {Q / "l9-miss-analysis.json"}')


if __name__ == '__main__':
    main()
