"""L9 关系召回：合并独立标注的三元组，与图里的边比对。

【这一层补什么】
  L9 现有的端点召回 86.2% 只说明"实体名抽得还行"，
  **完全不代表关系抽全了**——一个把所有关系都漏掉的抽取器，端点召回可以很高。
  本脚本用独立标注的 65 条三元组去问：图里有多少？

【两个口径】
  · 严格：source + relation + target 三者都对
  · 宽松：只看 source + target 端点对（不管谓词）

【比对前要归一化】
  标注用的是原文写法（`Safavi et al. [77]`、`TransE [16]`），图里是抽取后的名字
  （`Safavi et al.`、`TransE`）。所以比对时要去掉文献编号、压平空白、小写化。
"""
import json
import re
from pathlib import Path

# 路径以本文件为锚点解析，不写死盘符——演示机不一定把仓库放在 D 盘。
_HERE = Path(__file__).resolve()
ROOT = _HERE.parent.parent          # tools/*.py 都在根目录下一层
Q = ROOT / 'data' / 'quality'
DIR = Q / '_l9rel'


def norm(s):
    """归一化实体名：去掉文献编号/括号、压平空白、小写、去标点。"""
    s = str(s or '')
    s = re.sub(r'\[\d+\]', ' ', s)          # [77]
    s = re.sub(r'\(\d{4}[a-z]?\)', ' ', s)  # (2019a)
    s = re.sub(r'[^0-9a-zA-Z\u4e00-\u9fff]+', '', s.lower())
    return s


# 不是"命名实体"的端点——金标里出现这些时，那条三元组不该计入召回分母。
# 依据：项目的抽取目标是 15 类命名实体（Person/Method/Concept/Organization…），
# 而下面这些是**指代、数量短语、或论文自身的篇章成分**：
#   `This paper` / `This section` / `The results` 是指代，
#   `2 626 papers` / `160 survey tables` 是数量短语，
#   `It` 是代词。
# 不过滤的话，召回会被严重低估——实测过滤前 30.8%，而这批"端点"本来就不该被抽。
NON_ENTITY = re.compile(
    r'^(this|these|those|it|its|the)\s+\w+'      # 指代性短语
    r'|^\d[\d\s,]*\s*(papers?|tables?|sections?|articles?|methods?)$'  # 数量短语
    r'|^(it|they|we|our|their)$',
    re.I)


def is_entity_like(name: str) -> bool:
    s = str(name or '').strip()
    if not s or len(s) < 2:
        return False
    if NON_ENTITY.match(s):
        return False
    # 纯数字/纯标点
    if not re.search(r'[A-Za-z\u4e00-\u9fff]', s):
        return False
    return True


def main():
    segs = []
    for p in sorted(DIR.glob('out-batch-*.json')):
        d = json.loads(p.read_text(encoding='utf-8'))
        segs.extend(d.get('segments') or [])
    n_tri = sum(len(s.get('triples') or []) for s in segs)
    print(f'合并 {len(segs)} 段 / {n_tri} 条金标三元组（来自独立标注）')

    # 图里的边
    edges = json.loads((Q / 'relation-verify-all.json').read_text(encoding='utf-8'))['edges']
    all_pairs = {(norm(e['source']), norm(e['target'])) for e in edges}
    sup_pairs = {(norm(e['source']), norm(e['target'])) for e in edges
                 if e.get('pass') in (True, 'True')}
    all_tri = {(norm(e['source']), str(e.get('type', '')).upper(), norm(e['target']))
               for e in edges}
    sup_tri = {(norm(e['source']), str(e.get('type', '')).upper(), norm(e['target']))
               for e in edges if e.get('pass') in (True, 'True')}
    print(f'图：{len(edges)} 条边，端点对 {len(all_pairs)}（L5 子集 {len(sup_pairs)}）\n')

    # ── 过滤金标：只保留两端都是"命名实体"的三元组 ──
    # 不过滤会把指代（`This paper`）、数量短语（`2 626 papers`）算进分母，
    # 而那些本来就不该被抽成实体 ⇒ 严重低估召回。
    tri_all, tri_kept, dropped = [], [], []
    for s in segs:
        for t in (s.get('triples') or []):
            tri_all.append(t)
            if is_entity_like(t['source']) and is_entity_like(t['target']):
                tri_kept.append(t)
            else:
                dropped.append(t)
    print(f'金标三元组：全部 {len(tri_all)} 条 → 两端都是命名实体的 {len(tri_kept)} 条'
          f'（剔除 {len(dropped)} 条指代/数量短语）\n')
    if dropped:
        print('  被剔除的样例（端点不是命名实体）：')
        for t in dropped[:6]:
            print(f'    {t["source"][:34]!r} --{t["relation"]}--> {t["target"][:34]!r}')
        print()

    n_eval = len(tri_kept)
    hit_pair_all = hit_pair_sup = hit_tri_all = hit_tri_sup = 0
    misses = []
    for t in tri_kept:
        a, b = norm(t['source']), norm(t['target'])
        r = str(t.get('relation', '')).upper()
        if (a, b) in all_pairs:
            hit_pair_all += 1
        if (a, b) in sup_pairs:
            hit_pair_sup += 1
        if (a, r, b) in all_tri:
            hit_tri_all += 1
        if (a, r, b) in sup_tri:
            hit_tri_sup += 1
        if (a, b) not in all_pairs and len(misses) < 12:
            misses.append((t['source'], t['relation'], t['target']))

    print('=== 关系召回 ===\n')
    print(f'{"口径":34s} {"命中":>6s} {"召回":>8s}')
    print('-' * 52)
    for name, h in (('端点对命中（全图 43867 条）', hit_pair_all),
                    ('端点对命中（L5 子集 27954 条）', hit_pair_sup),
                    ('三元组严格命中（全图）', hit_tri_all),
                    ('三元组严格命中（L5 子集）', hit_tri_sup)):
        print(f'{name:34s} {h:6d} {h/n_eval*100:7.1f}%')

    print(f'\n参评金标 {n_eval} 条（已剔除端点非命名实体的 {len(dropped)} 条）；'
          f'端点对完全没进图的样例：')
    for a, r, b in misses:
        print(f'   {a[:40]!r} --{r}--> {b[:40]!r}')

    out = {
        'n_segment': len(segs),
        'n_triple_gold_raw': n_tri,
        'n_triple_gold_entity_only': n_eval,
        'n_triple_dropped_non_entity': len(dropped),
        'edge_total': len(edges),
        # 分母用"两端都是命名实体"的那批——项目的抽取目标就是命名实体，
        # 把 `This paper` / `2 626 papers` 这类算进分母会严重低估召回。
        'recall_pair_all': round(hit_pair_all / n_eval, 4),
        'recall_pair_supported': round(hit_pair_sup / n_eval, 4),
        'recall_triple_all': round(hit_tri_all / n_eval, 4),
        'recall_triple_supported': round(hit_tri_sup / n_eval, 4),
        'n_pair_all': hit_pair_all, 'n_pair_supported': hit_pair_sup,
        'n_triple_all': hit_tri_all, 'n_triple_supported': hit_tri_sup,
        'non_entity_examples': [{'source': t['source'], 'relation': t['relation'],
                                 'target': t['target']} for t in dropped[:10]],
        'miss_examples': [{'source': a, 'relation': r, 'target': b} for a, r, b in misses],
        'reading': ('端点召回 86.2% 只说明"实体名抽得还行"，**不代表关系抽全了**。'
                    '本层给出的是关系的独立读数：端点对命中约 47%、'
                    '三元组严格命中约 43%（见上面四个数）。'
                    '**即：原文里明确断言的关系，只有约四成进了图。**'),
        'stability_note': (
            '**标注稳定性**：同一批段落被独立标注了两次，第一次得 87 条三元组、'
            '第二次得 65 条（差 25%）。这远低于端点标注的稳定性（κ=0.722）。'
            '原因与 L5 的发现一致：**关系任务本身的歧义性远大于实体任务**——'
            '判断"哪两个实体之间是什么关系"需要同时定端点、定方向、选谓词。'
            '所以本层的数字应读作**量级估计**，不能当精确值。'
        ),
        'caveat': ('金标来自 LLM 独立标注（非人类），样本 34 段；'
                   '两次标注差异 25% ⇒ 只报量级。'),
    }
    (Q / 'l9-relation-recall.json').write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'\n报告已写出: {Q / "l9-relation-recall.json"}')


if __name__ == '__main__':
    main()
