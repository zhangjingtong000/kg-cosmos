"""决定性实验：本地模型重抽，到底能不能改善漏抽？

【为什么必须先做这个，而不是直接跑 4.6 小时】
  漏抽归因发现 2024+ 的新论文漏了 73%，于是想"重抽那 9 篇"。
  但有个致命前提没验证：

      原图是**云端 DeepSeek** 抽的；重抽只能用**本地 qwen3.5:9b**。
      本地模型明显更弱 ⇒ 重抽**可能不但没改善，反而更差**。

  本地冒烟测试已经露出苗头：给它一段含
  `LLMs enable three key mechanisms: (1) Generative knowledge modeling…`
  的文本，它抽出的 2 条关系里**没有**那三个 —— 与云端漏掉的是同一类。

  所以本实验：取**已知漏抽**的段落，分别列出
     (a) 云端当初抽出了什么（从图里查）
     (b) 本地模型现在抽出什么
  如果 (b) 不比 (a) 多，那"重抽"这条路**就是无效的**，不该花 4.6 小时。

用法：
  python tools/local_reextract_feasibility.py            # 默认取 3 段
  python tools/local_reextract_feasibility.py --n 6
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(r'D:\kg-demo')
Q = ROOT / 'data' / 'quality'
DIR = Q / '_l9rel'
sys.path.insert(0, str(ROOT / 'src'))

NON_ENTITY = re.compile(
    r'^(this|these|those|it|its|the)\s+\w+'
    r'|^\d[\d\s,]*\s*(papers?|tables?|sections?|articles?|methods?)$'
    r'|^(it|they|we|our|their)$', re.I)


def norm(s):
    s = re.sub(r'\[\d+\]', ' ', str(s or ''))
    return re.sub(r'[^0-9a-zA-Z\u4e00-\u9fff]+', '', s.lower())


# 已知的等价写法。**必须有这个，否则会得出错误结论**：
# 实测本地模型把 `LLMs` 抽成 `Large Language Models`，与金标写法不同，
# 归一化后不匹配 ⇒ 初版比对误报"本地命中 0 条"，
# 差点据此得出"本地重抽无效、不要跑"的错误结论。
ALIAS = {
    'llms': 'largelanguagemodels',
    'llm': 'largelanguagemodels',
    'largelanguagemodel': 'largelanguagemodels',
    'knowlmie': 'knowlmiedataset',
    'kg': 'knowledgegraph',
    'kgs': 'knowledgegraphs',
}


def alias_norm(s):
    n = norm(s)
    return ALIAS.get(n, n)


def loose_eq(a: str, b: str) -> bool:
    """两实体名是否指同一事物：别名相等，或一方包含另一方（长度够长时）。"""
    x, y = alias_norm(a), alias_norm(b)
    if not x or not y:
        return False
    if x == y:
        return True
    # 包含关系：`generativeknowledgemodeling` ⊂ `generativeknowledgemodeling` 之类
    if len(x) >= 8 and len(y) >= 8 and (x in y or y in x):
        return True
    return False


def is_entity(x):
    s = str(x or '').strip()
    return bool(s) and len(s) >= 2 and not NON_ENTITY.match(s) \
        and bool(re.search(r'[A-Za-z\u4e00-\u9fff]', s))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--n', type=int, default=3, help='对照几段')
    args = ap.parse_args()

    from kg_extractor import MODELS, extract_chunk  # noqa: E402

    sample = json.loads((Q / 'l9-span-sample.json').read_text(encoding='utf-8'))
    spans = {s['id']: s for s in (sample.get('spans') or [])}
    edges = json.loads((Q / 'relation-verify-all.json').read_text(encoding='utf-8'))['edges']
    pair = {}
    for e in edges:
        pair.setdefault(alias_norm(e['source']), set()).add(
            (alias_norm(e['target']), e.get('type')))

    # 收集所有标注段落里的三元组，挑"金标里端点都像实体、但图里没有"的段
    segs = []
    for p in sorted(DIR.glob('out-batch-*.json')):
        segs.extend(json.loads(p.read_text(encoding='utf-8')).get('segments') or [])

    def miss_count(seg):
        n = 0
        for t in (seg.get('triples') or []):
            if not (is_entity(t['source']) and is_entity(t['target'])):
                continue
            a = alias_norm(t['source'])
            b = alias_norm(t['target'])
            if b not in {x[0] for x in pair.get(a, set())}:
                n += 1
        return n

    ranked = sorted(segs, key=miss_count, reverse=True)
    picked = [s for s in ranked if miss_count(s) > 0 and s['id'] in spans][:args.n]
    if not picked:
        print('没有可对照的段落（都有命中）。')
        return 1

    print(f'对照 {len(picked)} 段（按"漏得多"排序）\n')
    tot_cloud = tot_local = tot_gold = 0
    tot_both = tot_only_local = tot_only_cloud = 0
    for seg in picked:
        sid = seg['id']
        text = (spans[sid].get('text') or '')[:1400]
        gold = [t for t in (seg.get('triples') or [])
                if is_entity(t['source']) and is_entity(t['target'])]
        # 云端当初抽出来的（该段涉及的金标端点，在图里有没有边）
        cloud_hit = []
        for t in gold:
            a = alias_norm(t['source'])
            b = alias_norm(t['target'])
            if b in {x[0] for x in pair.get(a, set())}:
                cloud_hit.append(t)
        t0 = time.time()
        res = extract_chunk(text, MODELS['local']['text'], 'local',
                            MODELS['local'].get('vision'), None, 'text')
        dt = time.time() - t0
        local = (res or {}).get('edges') or []

        # ── 关键：找出"云端当初从这一段抽了什么" ──
        # 方法：图里每条边带 evidence，取 evidence 落在本段文本里的边。
        # 这比"是否命中金标"公平得多——金标只列了一条，而一段话可以有多种
        # 合理表述；实测本地抽的关系都合理，却因与金标写法不同而被判"未命中"。
        tneedle = re.sub(r'\s+', '', text)[:160]
        cloud_edges = []
        for e in edges:
            ev = re.sub(r'\s+', '', str(e.get('evidence') or ''))
            if ev and len(ev) >= 20 and ev[:60] in re.sub(r'\s+', '', text):
                cloud_edges.append(e)

        print(f'── {sid}  （本地耗时 {dt:.0f}s）')
        print(f'   金标 {len(gold)} 条；**云端从本段抽了 {len(cloud_edges)} 条**；'
              f'本地抽了 {len(local)} 条')
        print(f'   云端抽的:')
        for e in cloud_edges[:8]:
            print(f'      [{e.get("type")}] {str(e.get("source"))[:30]!r} → '
                  f'{str(e.get("target"))[:30]!r}   (entail={e.get("entail_prob")})')
        print(f'   本地抽的:')
        for e in local[:8]:
            print(f'      [{e.get("type")}] {str(e.get("source"))[:30]!r} → '
                  f'{str(e.get("target"))[:30]!r}')

        # 两边都抽到的（宽松匹配）
        def same(a1, b1, a2, b2):
            return (loose_eq(a1, a2) and loose_eq(b1, b2)) or \
                   (loose_eq(a1, b2) and loose_eq(b1, a2))
        both = 0
        for e in local:
            for c in cloud_edges:
                if same(str(e.get('source', '')), str(e.get('target', '')),
                        str(c.get('source', '')), str(c.get('target', ''))):
                    both += 1
                    break
        only_local = len(local) - both
        only_cloud = len(cloud_edges) - both
        print(f'   两边都有 {both} 条；**只有本地有 {only_local} 条**；'
              f'只有云端有 {only_cloud} 条')
        tot_cloud += len(cloud_edges)
        tot_local += len(local)
        tot_both += both
        tot_only_local += only_local
        tot_only_cloud += only_cloud
        tot_gold += len(gold)
        print()

    print('=' * 66)
    print(f'合计：金标 {tot_gold} 条')
    print(f'      云端从这些段抽出 {tot_cloud} 条；本地抽出 {tot_local} 条')
    print(f'      两边都有 {tot_both} 条；**只有本地有 {tot_only_local} 条**；'
          f'只有云端有 {tot_only_cloud} 条')
    print()
    if tot_only_local > tot_only_cloud:
        print('⇒ **本地重抽能补回一些云端漏掉的**，值得跑 9 篇。')
        print(f'   实测本地独有 {tot_only_local} 条 vs 云端独有 {tot_only_cloud} 条。')
    else:
        print('⇒ 本地重抽**没有净增益**（本地独有的不比云端独有得多）。')
        print('   注意这里比的是**两条管线的产出**，而不是"是否命中金标"——')
        print('   金标只列了每段的一条关系，而一段话可以有多种合理表述，')
        print('   拿金标当唯一答案会把两边都判成"未命中"（实测本地抽的关系')
        print('   大多合理，只是与金标写法不同）。')
    print()
    print('⚠️ 无论结论如何，本实验的样本很小（3 段），只用于**否决明显无效的方案**，')
    print('   不足以证明"重抽一定有用"。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
