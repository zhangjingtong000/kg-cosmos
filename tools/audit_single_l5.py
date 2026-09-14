# -*- coding: utf-8 -*-
"""单篇 L5 评测：这一篇抽出来的关系，有多少条**真的被原文支持**。

为什么需要它：`tools/audit_single_graph.py` 测的 L1/L4 是**自洽型**指标
（只说明图内部一致），一个漏掉 90% 内容的抽取器也能拿满分。L5 才是
"证据是否真的支持这条边"——它需要 NLI 判别器，所以单独一个脚本。

模型与模板都沿用项目既有的 L5 实现（`relation_verifier.py`，现存于
`data/quality/backup/` 下）：
  · 模型：`Babelscape/mdeberta-v3-base-triplet-critic-xnli`（本机 HF cache 里）
  · premise  = 从证据里选出的**同时含两个端点**的那句话
  · hypothesis = 按关系类型的英文模板生成
  · 阈值 0.67（与全库口径一致，见 docs/L5_SIX_LAYER_REPORT.md）

**不 import 那个文件**：它在模块级执行 `parse_args()`，会吃掉调用方的 argv。
这里复刻所需函数（含 CREATED_BY 的方向约定注释，那是踩过坑的）。

用法：
  python tools/audit_single_l5.py data/ad690dfb/graph.json
  python tools/audit_single_l5.py --compare data/ad690dfb/graph.json data/3e38f803/graph.json
  python tools/audit_single_l5.py data/ad690dfb/graph.json --limit 200
"""
import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HF_HUB = Path.home() / '.cache' / 'huggingface' / 'hub'
TRIPLET_DIR = next(
    HF_HUB.glob('models--Babelscape--mdeberta-v3-base-triplet-critic-xnli/snapshots/*'),
    None)

THRESHOLD = 0.67          # 与全库 L5 口径一致

REL_TEMPLATE = {
    'PART_OF': '{s} is part of {t}.',
    'USES': '{s} uses {t}.',
    'PRODUCES': '{s} produces {t}.',
    'MEASURES': '{s} measures {t}.',
    'EVALUATES': '{s} evaluates {t}.',
    'COMPARES_WITH': '{s} is compared with {t}.',
    'SUPPORTS': '{s} supports {t}.',
    'CONTRADICTS': '{s} contradicts {t}.',
    'CAUSES': '{s} causes {t}.',
    'ENABLES': '{s} enables {t}.',
    'APPLIES_TO': '{s} is applied to {t}.',
    'LOCATED_AT': '{s} is located at {t}.',
    # 方向约定：T3.1 已把 CREATED_BY 统一为 Author(Person) → Work(Document)。
    # 所以 hypothesis 必须是「作品由作者创作」。反过来会把语义判反 ——
    # 实测方向归一后通过率从 39.6% 掉到 26.0%，改回本模板才恢复。
    'CREATED_BY': '{t} was created by {s}.',
    'CITES': '{s} cites {t}.',
    'RELATED_TO': '{s} is related to {t}.',
}


def nkey(n):
    return re.sub(r'[^a-z0-9]+', '', (n or '').lower())


def split_sentences(text):
    text = re.sub(r'\s+', ' ', text or '').strip()
    if not text:
        return []
    parts = re.split(r'(?<=[.!?])\s+(?=[A-Z(\[])', text)
    return [p.strip() for p in parts if len(p.strip()) > 15]


def pick_evidence_sentence(evidence, source, target):
    """选出同时含两个端点的句子。返回 (句子, both/one/none)。"""
    sents = split_sentences(evidence)
    if not sents:
        return '', 'none'
    s_k, t_k = nkey(source), nkey(target)
    s_toks = [w for w in re.split(r'\W+', (source or '').lower()) if len(w) > 3]
    t_toks = [w for w in re.split(r'\W+', (target or '').lower()) if len(w) > 3]

    def has(sent, key, toks):
        low = nkey(sent)
        if key and len(key) > 3 and key in low:
            return True
        return bool(toks) and all(w in sent.lower() for w in toks)

    for sent in sents:
        if has(sent, s_k, s_toks) and has(sent, t_k, t_toks):
            return sent, 'both'
    for sent in sents:
        if has(sent, s_k, s_toks) or has(sent, t_k, t_toks):
            return sent, 'one'
    return max(sents, key=len), 'none'


def load_model(device_pref):
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    if TRIPLET_DIR is None:
        raise SystemExit('找不到 L5 模型（HF cache 里没有 triplet-critic）')
    device = device_pref if (device_pref == 'cpu' or torch.cuda.is_available()) else 'cpu'
    tok = AutoTokenizer.from_pretrained(str(TRIPLET_DIR))
    model = AutoModelForSequenceClassification.from_pretrained(str(TRIPLET_DIR))
    model.to(device).eval()
    id2label = {int(k): str(v) for k, v in model.config.id2label.items()}
    sup = next((i for i, v in id2label.items() if 'entail' in v.lower()), None)
    if sup is None:
        sup = next((i for i, v in id2label.items()
                    if v.lower() in ('label_1', 'true', '1')), len(id2label) - 1)
    print(f'  模型 id2label={id2label} 支持类={sup} device={device} '
          f'参数量≈{sum(p.numel() for p in model.parameters()) / 1e6:.1f}M')
    return model, tok, device, sup


def infer(model, tok, device, sup_idx, premises, hyps, batch=32, max_len=256):
    import torch
    scores = []
    with torch.no_grad():
        for i in range(0, len(premises), batch):
            enc = tok(premises[i:i + batch], hyps[i:i + batch],
                      return_tensors='pt', padding=True, truncation=True,
                      max_length=max_len)
            enc = {k: v.to(device) for k, v in enc.items()}
            logits = model(**enc).logits
            probs = torch.softmax(logits, dim=-1)[:, sup_idx]
            scores.extend(probs.detach().cpu().tolist())
    return scores


def evaluate(path, model, tok, device, sup_idx, limit=0, batch=32):
    d = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    edges = d.get('edges') or []
    if limit:
        edges = edges[:limit]

    premises, hyps, kinds, types = [], [], [], []
    skipped = 0
    for e in edges:
        t = (e.get('type') or '').upper()
        tpl = REL_TEMPLATE.get(t)
        evs = e.get('evidence') or []
        if tpl is None or not evs:
            skipped += 1
            continue
        text = ' '.join((x.get('text') or '') for x in evs)
        sent, kind = pick_evidence_sentence(text, e.get('source'), e.get('target'))
        if not sent:
            skipped += 1
            continue
        premises.append(sent[:1400])
        hyps.append(tpl.format(s=e.get('source'), t=e.get('target')))
        kinds.append(kind)
        types.append(t)
    scores = infer(model, tok, device, sup_idx, premises, hyps, batch=batch)

    supported = [s for s in scores if s >= THRESHOLD]
    by_type = defaultdict(list)
    for t, s in zip(types, scores):
        by_type[t].append(s)
    by_kind = defaultdict(lambda: [0, 0])
    for k, s in zip(kinds, scores):
        by_kind[k][1] += 1
        if s >= THRESHOLD:
            by_kind[k][0] += 1

    res = {
        'path': str(path),
        'mode': d.get('mode'),
        'edges_total': len(edges),
        'edges_scored': len(scores),
        'edges_skipped': skipped,
        'supported': len(supported),
        'support_rate': round(100.0 * len(supported) / max(1, len(scores)), 1),
        'median_prob': round(sorted(scores)[len(scores) // 2], 4) if scores else None,
        'by_type': {t: {'n': len(v),
                        'support_rate': round(100.0 * sum(1 for x in v if x >= THRESHOLD) / len(v), 1),
                        'median': round(sorted(v)[len(v) // 2], 4)}
                    for t, v in sorted(by_type.items(), key=lambda kv: -len(kv[1]))},
        'by_endpoint_in_sentence': {k: {'n': v[1],
                                        'support_rate': round(100.0 * v[0] / max(1, v[1]), 1)}
                                    for k, v in sorted(by_kind.items())},
    }
    return res, scores


def show(r, label=''):
    if label:
        print(f'\n{"=" * 74}\n{label}\n{"=" * 74}')
    print(f"  {r['path']}   通道 {r['mode'] or '（未记录）'}")
    print(f"  待判边 {r['edges_total']} · 实判 {r['edges_scored']} · 跳过 {r['edges_skipped']}")
    print(f"  **L5 真实支持率 {r['support_rate']}%**（阈值 {THRESHOLD}）"
          f"   中位 entail_prob = {r['median_prob']}")
    print(f"  端点都在同一句: {r['by_endpoint_in_sentence'].get('both')}")
    print(f"  只有一端在同一句: {r['by_endpoint_in_sentence'].get('one')}")
    print('  按关系类型（前 8）:')
    for t, v in list(r['by_type'].items())[:8]:
        print(f'    {t:<16} n={v["n"]:<5} 支持率 {v["support_rate"]:>5}%  中位 {v["median"]}')


def main():
    ap = argparse.ArgumentParser(description='单篇 L5：关系是否真的被原文支持')
    ap.add_argument('graphs', nargs='+')
    ap.add_argument('--compare', action='store_true')
    ap.add_argument('--limit', type=int, default=0, help='每篇只判前 N 条（快速试跑）')
    ap.add_argument('--batch', type=int, default=32)
    ap.add_argument('--device', default='cpu')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()

    model, tok, device, sup = load_model(a.device)
    outs = []
    for p in a.graphs:
        r, _ = evaluate(p, model, tok, device, sup, limit=a.limit, batch=a.batch)
        outs.append(r)
        if not a.json:
            show(r, Path(p).parent.name)

    if a.json:
        print(json.dumps(outs, ensure_ascii=False, indent=2))
    elif a.compare and len(outs) == 2:
        A, B = outs
        print(f'\n{"=" * 74}\n并排对比\n{"=" * 74}')
        print(f'  {"指标":<20} {"A":>12} {"B":>12}')
        for label, key in (('待判边', 'edges_total'), ('实判边', 'edges_scored'),
                           ('L5 支持率 %', 'support_rate'), ('中位概率', 'median_prob')):
            print(f'  {label:<20} {str(A.get(key)):>12} {str(B.get(key)):>12}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
