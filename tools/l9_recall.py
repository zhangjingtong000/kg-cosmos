"""L9 端点召回层：从原文段落出发，回答"该抽的抽到了吗"。

【为什么必须补这一层（对抗评审的 blocker 之一）】
  现有六层**全部是自洽型判定**：L1 判"名字是否出现在自己的证据里"、
  L3 判"端点在不在节点集内"、L4 判"谓词在不在本体里"、L6 判"有没有引文"——
  它们只能说明图**内部一致**，无法回答最要紧的那个问题：
  **17 篇原文里的实体与关系，抽全了没有？**
  一个把 90% 内容漏掉的抽取器，六层指标可以全部接近满分。

【可行的零成本替代】
  从快照的 chunk 里**按固定种子分层抽样**段落，逐段人工（或独立模型）列出其中的
  实体名，再与图里的节点表比对，得到**操作性端点召回**。
  它不是严格金标，但它是**唯一不依赖抽取管线自身**的证据来源，
  而且可复现（种子固定、清单落盘）。

  本脚本负责：抽样、导出待标注清单、以及在标注回填后计算召回。
  标注结果写在 data/quality/l9-gold-spans.json：
      {"spans": [{"id": ..., "text": ..., "entities": ["...", "..."]}]}

用法：
  python tools/l9_recall.py --sample     # 抽样并导出待标注清单
  python tools/l9_recall.py --score      # 读标注并计算召回
"""
# 路径以本文件为锚点解析，不写死盘符——演示机不一定把仓库放在 D 盘。
from pathlib import Path as _Path
_HERE = _Path(__file__).resolve()
ROOT = _HERE.parent.parent          # tools/*.py 与 src/*.py 都在根目录下一层
import argparse
import json
import random
import re
import sys
from pathlib import Path

GRAPH = ROOT / 'data/corpus/graphs'
Q = ROOT / 'data/quality'
SAMPLE_OUT = Q / 'l9-span-sample.json'
GOLD = Q / 'l9-gold-spans.json'
SEED = 20260912


def norm(s):
    return re.sub(r'[^a-z0-9\u4e00-\u9fff]+', '', (s or '').lower())


def load_graph_names():
    known = set()
    for f in sorted(GRAPH.glob('*.json')):
        d = json.loads(f.read_text(encoding='utf-8'))
        for n in d.get('nodes', []):
            nm = (n.get('name') or '').strip()
            if nm:
                known.add(nm)
    return known


def collect_spans(per_paper=2):
    """每篇抽 per_paper 个 chunk，作为待标注段落。

    注意：快照文件里**没有存 chunk 文本**（只有 nodes/edges/stats），
    所以这里从原始 PDF 用与抽取管线**同一套**切块参数重新切
    （pdf_parser.chunk_document，max_chars=500 / overlap 0.3），
    保证抽到的段落与当初喂给抽取器的是同一批文本。
    """
    sys.path.insert(0, str(ROOT / 'src'))
    try:
        from pdf_parser import parse_pdf, chunk_document
    except Exception as e:
        print(f'无法导入 pdf_parser：{e}')
        return []

    rnd = random.Random(SEED)
    spans = []
    pdfs = sorted((ROOT / 'data/corpus').glob('*.pdf'))
    stems_with_graph = {f.stem for f in GRAPH.glob('*.json')}
    for pdf in pdfs:
        if pdf.stem not in stems_with_graph:
            continue
        try:
            parsed = parse_pdf(str(pdf))
            chunks = chunk_document(parsed).get('chunks') or []
        except Exception as e:
            print(f'  跳过 {pdf.name}：{type(e).__name__}: {e}')
            continue
        texts = []
        for c in chunks:
            t = c.get('text') if isinstance(c, dict) else c
            if t and len(t.strip()) >= 200:
                texts.append(t.strip())
        if not texts:
            continue
        pick = rnd.sample(texts, min(per_paper, len(texts)))
        for i, t in enumerate(pick):
            spans.append({
                'id': f'{pdf.stem}#{i}',
                'paper': pdf.stem,
                'text': t[:1200],
                'entities': [],          # 待标注
            })
    return spans


def main():
    ap = argparse.ArgumentParser(description='L9 端点召回层')
    ap.add_argument('--sample', action='store_true')
    ap.add_argument('--score', action='store_true')
    args = ap.parse_args()

    if args.sample or not (args.sample or args.score):
        spans = collect_spans()
        SAMPLE_OUT.write_text(json.dumps(
            {'seed': SEED, 'count': len(spans),
             'note': '逐段在 entities 里填该段中出现的实体名（原文用词，不要归一化）',
             'spans': spans}, ensure_ascii=False, indent=2), encoding='utf-8')
        print(f'已抽样 {len(spans)} 个段落 → {SAMPLE_OUT}')
        print('下一步：把每段的 entities 填好（或复制成 l9-gold-spans.json），'
              '再跑 --score')
        # 顺带打印几段，便于直接看
        for s in spans[:3]:
            print(f"\n── {s['id']} ──\n{s['text'][:320]}…")
        return 0

    # ── 打分 ──
    if not GOLD.exists():
        print(f'缺少标注文件 {GOLD}')
        print(f'可先跑 --sample 生成 {SAMPLE_OUT}，填好 entities 后另存为上述路径')
        return 1
    gold = json.loads(GOLD.read_text(encoding='utf-8'))
    known = load_graph_names()
    known_norm = {norm(n): n for n in known}

    tot = hit = 0
    per = []
    for s in gold.get('spans', []):
        ents = s.get('entities') or []
        if not ents:
            continue
        h = sum(1 for e in ents if norm(e) in known_norm)
        tot += len(ents)
        hit += h
        per.append({'id': s['id'], 'gold': len(ents), 'matched': h,
                    'missed': [e for e in ents if norm(e) not in known_norm]})

    print('=== L9 端点召回层（操作性金标）===\n')
    print(f'标注段落 {len(per)} 段，金标实体 {tot} 个')
    if tot:
        print(f'端点在图中命中 {hit} 个 ⇒ **端点召回 = {hit/tot:.1%}**')
    print()
    for p in per:
        flag = '✅' if p['matched'] == p['gold'] else '⚠️ '
        print(f"  {flag} {p['id']:26s} {p['matched']}/{p['gold']}"
              + (f"   漏: {p['missed'][:6]}" if p['missed'] else ''))

    out = {'spans': len(per), 'gold_entities': tot, 'matched': hit,
           'endpoint_recall': round(hit / tot, 4) if tot else None,
           'per_span': per,
           'note': '操作性金标：段落由固定种子抽取，实体由独立于抽取管线的标注给出。'
                   '不是严格金标，但能回答"原文里的东西抽到没有"。'}
    (Q / 'l9-recall.json').write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                      encoding='utf-8')
    print(f'\n报告已写出: {Q / "l9-recall.json"}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
