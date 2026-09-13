"""修正关系召回：把"实体别名"与"谓词近义"纳入匹配后再算一遍。

【为什么要重算】
  `l9_relation_recall.py` 用的是**严格归一化**（去标点+小写）。
  但实测发现：
    · 金标写 `LLMs`，图里写 `Large Language Model`
    · 金标写 `Knowledge Graph Embeddings`，图里可能写 `KG Embedding`
    · 谓词也可能不同（金标 `ENABLES`，图里 `USES`）

  更关键的证据：拿三段原文去查"云端当初抽了什么"，发现**云端从每段抽了
  19–30 条边**，而金标只列了 2–5 条，且写法对不上 ⇒ 严格匹配把大量
  真实命中的边判成了"漏"，**47.6% 这个数可能严重低估**。

  本脚本用三档宽松度重算，看结论有多稳：
    L1 严格（现行）：端点精确、谓词必须一致
    L2 别名：端点按别名/包含关系匹配，谓词仍须一致
    L3 宽松：端点按别名匹配，**谓词不要求一致**（只问"这两个实体之间有没有边"）
"""
import json
import re
from pathlib import Path

ROOT = Path(r'D:\kg-demo')
Q = ROOT / 'data' / 'quality'
DIR = Q / '_l9rel'

NON_ENTITY = re.compile(
    r'^(this|these|those|it|its|the)\s+\w+'
    r'|^\d[\d\s,]*\s*(papers?|tables?|sections?|articles?|methods?)$'
    r'|^(it|they|we|our|their)$', re.I)

ALIAS = {
    'llm': 'largelanguagemodel', 'llms': 'largelanguagemodel',
    'largelanguagemodels': 'largelanguagemodel',
    'kg': 'knowledgegraph', 'kgs': 'knowledgegraph',
    'knowledgegraphs': 'knowledgegraph',
    'knowlmie': 'knowlmiedataset',
    'ner': 'namedentityrecognition',
    'kgc': 'knowledgegraphcompletion',
}


def norm(s):
    s = re.sub(r'\[\d+\]', ' ', str(s or ''))
    s = re.sub(r'\(\d{4}[a-z]?\)', ' ', s)
    return re.sub(r'[^0-9a-zA-Z\u4e00-\u9fff]+', '', s.lower())


def anorm(s):
    n = norm(s)
    return ALIAS.get(n, n)


def is_entity(x):
    s = str(x or '').strip()
    return bool(s) and len(s) >= 2 and not NON_ENTITY.match(s) \
        and bool(re.search(r'[A-Za-z\u4e00-\u9fff]', s))


def eq(a, b):
    """别名相等，或（足够长时）互相包含。"""
    x, y = anorm(a), anorm(b)
    if not x or not y:
        return False
    if x == y:
        return True
    if min(len(x), len(y)) >= 10 and (x in y or y in x):
        return True
    return False


def main():
    segs = []
    for p in sorted(DIR.glob('out-batch-*.json')):
        segs.extend(json.loads(p.read_text(encoding='utf-8')).get('segments') or [])
    gold = []
    for s in segs:
        for t in (s.get('triples') or []):
            if is_entity(t['source']) and is_entity(t['target']):
                gold.append(t)
    print(f'参评金标 {len(gold)} 条\n')

    edges = json.loads((Q / 'relation-verify-all.json').read_text(encoding='utf-8'))['edges']
    exact_pairs = {(norm(e['source']), norm(e['target'])) for e in edges}
    exact_tri = {(norm(e['source']), str(e.get('type', '')).upper(), norm(e['target']))
                 for e in edges}
    # 别名索引：把每个端点名映射到别名键，便于 O(1) 查
    pair_keys = {(anorm(e['source']), anorm(e['target'])) for e in edges}
    tri_keys = {(anorm(e['source']), str(e.get('type', '')).upper(), anorm(e['target']))
                for e in edges}
    node_keys = {anorm(e['source']) for e in edges} | {anorm(e['target']) for e in edges}

    hit = {'L1_tri': 0, 'L2_tri': 0, 'L3_pair': 0, 'L2_pair': 0}
    for t in gold:
        a1, b1 = norm(t['source']), norm(t['target'])
        a2, b2 = anorm(t['source']), anorm(t['target'])
        r = str(t.get('relation', '')).upper()
        if (a1, r, b1) in exact_tri:
            hit['L1_tri'] += 1
        if (a2, r, b2) in tri_keys:
            hit['L2_tri'] += 1
        if (a1, b1) in exact_pairs:
            hit['L2_pair'] += 1
        if (a2, b2) in pair_keys:
            hit['L3_pair'] += 1

    n = len(gold)
    print(f'{"档位":44s} {"命中":>5s} {"召回":>8s}')
    print('-' * 60)
    rows = [
        ('L1 严格：端点精确 + 谓词一致（现行）', hit['L1_tri']),
        ('L2 端点按别名/包含 + 谓词一致', hit['L2_tri']),
        ('L2b 端点精确 + 只要端点对存在（不看谓词）', hit['L2_pair']),
        ('L3 端点按别名 + 只要端点对存在（不看谓词）', hit['L3_pair']),
    ]
    for name, h in rows:
        print(f'{name:44s} {h:5d} {h/n*100:7.1f}%')

    print()
    lo, hi = hit['L1_tri'] / n, hit['L3_pair'] / n
    print(f'⇒ 关系召回的区间：**{lo*100:.1f}% – {hi*100:.1f}%**')
    print(f'   下界只认"端点与谓词都对"，上界只问"这两个实体之间有没有边"。')
    print(f'   论文里应报这个区间，而不是单点——因为"关系抽到没有"本身')
    print(f'   就依赖"算不算同一个关系"的判定，而这个判定有实质歧义。')

    out = {'n_gold': n, 'L1_strict_triple': hit['L1_tri'],
           'L2_alias_triple': hit['L2_tri'],
           'L2b_exact_pair': hit['L2_pair'],
           'L3_alias_pair': hit['L3_pair'],
           'recall_range': [round(lo, 4), round(hi, 4)],
           'note': ('严格匹配会把别名写法（LLMs vs Large Language Model）判成漏，'
                    '从而低估召回。实测云端从金标所在的三段里各抽了 19–30 条边，'
                    '而金标只列了 2–5 条 ⇒ 单点数字不可靠，应报区间。')}
    (Q / 'l9-relation-recall-corrected.json').write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'\n报告已写出: {Q / "l9-relation-recall-corrected.json"}')


if __name__ == '__main__':
    main()
