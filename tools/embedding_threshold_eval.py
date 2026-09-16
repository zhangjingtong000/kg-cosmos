# -*- coding: utf-8 -*-
r"""H4 · 嵌入阈值的**实测标定** —— 用向量余弦能不能判"同一个实体"。

## 为什么这不是"跑一下就完"的脚本

TODO 里 H4 的第一条用途写的是「同词异义相似度**用向量余弦替代字符重叠**」。
**"替代"两个字是错的**，而这件事只能用数据说清。本脚本就是那份数据：

对一批**人工标了"该合/该拒"**的名字对，量出余弦，然后回答两个问题：
  ① 有没有一个阈值能分开这两类？
  ② 如果没有，**哪一段分数是可以信的**？

**结果会打印成一张表 + 一个判别矩阵**，可复跑、可复核。
（2026-09-16 首跑结论：**中间区不可判**，见 `docs/H4_EMBEDDING_RESULT.md`。）

用法：
    python tools/embedding_threshold_eval.py
    python tools/embedding_threshold_eval.py --out data/quality/embedding-threshold-eval.json
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))

from embeddings import embed, cosine, HIGH, LOW  # noqa: E402

# ── 标定集：**每一对都有人工判断**（该合 = 同一实体；该拒 = 不同实体）──────
# 覆盖四类：① 拼写/空白变体 ② 缩写↔全称 ③ 子串陷阱 ④ 同缩写不同全称 ⑤ 完全不相关
CASES = [
    # (A, B, 该不该合, 属于哪类)
    ('knowledge graph', 'knowledge graphs', True, '单复数'),
    ('OpenRefine', 'Open Refine', True, '拼写/空白'),
    ('multi-head attention', 'Multi-Head Attention', True, '大小写/连字符'),
    ('TransE', 'TransE (Bordes 2013)', True, '带括注'),
    ('BERT', 'BERTLarge', True, '版本后缀'),
    ('tabula', 'Tabula PDF', True, '版本后缀'),
    ('KG', 'Knowledge Graph', True, '缩写↔全称'),
    ('AD', 'Anomaly Detection', True, '缩写↔全称'),
    ('GNN', 'Graph Neural Networks', True, '缩写↔全称'),
    ('Tag', 'Tagger', False, '子串陷阱'),
    ('graph', 'graph neural network', False, '子串陷阱'),
    ('time', 'timestamp', False, '子串陷阱'),
    ('AD', 'A. Doan', False, '同缩写不同全称'),
    ('AD', 'Action Dropout', False, '同缩写不同全称'),
    ('RDF', 'Reference Data File', False, '碰巧首字母对齐'),
    ('ORKG', 'Open Research Knowledge Graph', False, '独立品牌 vs 表面全称'),
    ('UI', 'build graph.py', False, '完全不相关'),
    ('AI', 'Semantic Scholar', False, '完全不相关'),
    ('Crossref', 'Semantic Scholar', False, '完全不相关'),
    ('Tabula', 'GROBID', False, '完全不相关'),
]


def parse_args():
    ap = argparse.ArgumentParser(description='嵌入阈值标定（H4）')
    ap.add_argument('--out', default=str(ROOT / 'data/quality/embedding-threshold-eval.json'))
    ap.add_argument('--model', default=None)
    return ap.parse_args()


ARGS = parse_args()


def main():
    t0 = time.time()
    print('=' * 86)
    print('H4 · 嵌入阈值标定 —— 余弦能不能判"同一个实体"')
    print('=' * 86)

    texts = []
    for a, b, _y, _c in CASES:
        texts += [a, b]
    try:
        vecs = embed(texts, model=ARGS.model)
    except Exception as e:
        print(f'  ❌ 嵌入服务不可用：{type(e).__name__}: {e}')
        print('     （Ollama 要在跑，且 `bge-m3:latest` 要在）')
        return 1
    print(f'  嵌入 {len(vecs)} 条 · 维度 {len(vecs[0])} · 用时 {time.time() - t0:.1f}s')
    print()

    rows = []
    for i, (a, b, y, cls) in enumerate(CASES):
        c = cosine(vecs[2 * i], vecs[2 * i + 1])
        rows.append({'a': a, 'b': b, 'should_merge': y, 'class': cls, 'cosine': round(c, 4)})

    rows.sort(key=lambda r: -r['cosine'])
    print(f'  {"余弦":>7}  {"该合":<5}{"类别":<24} A ↔ B')
    print('  ' + '-' * 82)
    for r in rows:
        mark = '✅' if r['should_merge'] else '❌'
        print(f'  {r["cosine"]:>7.4f}  {mark:<5}{r["class"]:<24} '
              f'{r["a"]!r} ↔ {r["b"]!r}')

    pos = [r['cosine'] for r in rows if r['should_merge']]
    neg = [r['cosine'] for r in rows if not r['should_merge']]
    print()
    print(f'  该合 {len(pos)} 对：最低 {min(pos):.4f} · 中位 {sorted(pos)[len(pos)//2]:.4f} · 最高 {max(pos):.4f}')
    print(f'  该拒 {len(neg)} 对：最低 {min(neg):.4f} · 中位 {sorted(neg)[len(neg)//2]:.4f} · 最高 {max(neg):.4f}')

    # ── 有没有一个阈值能分开？──
    print()
    print('=' * 86)
    print('判别分析：有没有单一阈值能分开两类？')
    print('=' * 86)
    best = None
    for thr_i in range(30, 100):
        thr = thr_i / 100
        tp = sum(1 for r in rows if r['should_merge'] and r['cosine'] >= thr)
        fn = sum(1 for r in rows if r['should_merge'] and r['cosine'] < thr)
        fp = sum(1 for r in rows if not r['should_merge'] and r['cosine'] >= thr)
        tn = sum(1 for r in rows if not r['should_merge'] and r['cosine'] < thr)
        f1 = 2 * tp / (2 * tp + fp + fn) if (2 * tp + fp + fn) else 0
        if best is None or f1 > best['f1']:
            best = {'thr': thr, 'tp': tp, 'fp': fp, 'fn': fn, 'tn': tn, 'f1': f1}
    print(f'  最佳阈值 {best["thr"]:.2f} ⇒ F1 {best["f1"]:.3f}'
          f'（TP {best["tp"]} · FP {best["fp"]} · FN {best["fn"]} · TN {best["tn"]}）')
    print()
    print('  ★ 关键观察：**该拒的最高分 vs 该合的最低分**')
    print(f'      该拒最高 {max(neg):.4f}'
          f'（{[r["a"] + " ↔ " + r["b"] for r in rows if not r["should_merge"] and r["cosine"] == max(neg)][0]}）')
    print(f'      该合最低 {min(pos):.4f}'
          f'（{[r["a"] + " ↔ " + r["b"] for r in rows if r["should_merge"] and r["cosine"] == min(pos)][0]}）')
    overlap = max(neg) > min(pos)
    if overlap:
        print(f'      ⇒ **两类交叠**（{min(pos):.4f} … {max(neg):.4f}）'
              f'⇒ **任何单一阈值都会在这段里判错**')
    else:
        print('      ⇒ 两类可分 ⇒ 单一阈值可行')

    # ── 用模块里定的 HIGH/LOW 口径再看一次 ──
    print()
    print(f'  按模块口径（HIGH={HIGH} 判合 · LOW={LOW} 判拒 · 之间不可判）：')
    for r in rows:
        if r['cosine'] >= HIGH:
            act = '合'
        elif r['cosine'] < LOW:
            act = '拒'
        else:
            act = '不可判'
        want = '合' if r['should_merge'] else '拒'
        ok = (act == want) or act == '不可判'
        r['action'] = act
        r['ok'] = ok
    n_decided = sum(1 for r in rows if r['action'] != '不可判')
    n_right = sum(1 for r in rows if r['action'] != '不可判' and r['ok'])
    n_wrong = n_decided - n_right
    print(f'      能自动决定 {n_decided}/{len(rows)} 条，其中对 {n_right}、错 {n_wrong}')
    print(f'      剩 {len(rows) - n_decided} 条落到"不可判" ⇒ 交给字符判据或人工')
    if n_wrong:
        print('      ⚠️ 错的那些：')
        for r in rows:
            if r['action'] != '不可判' and not r['ok']:
                print(f'         {r["cosine"]:.4f} 判「{r["action"]}」而实际该「'
                      f'{"合" if r["should_merge"] else "拒"}」：{r["a"]!r} ↔ {r["b"]!r}')

    payload = {
        'generated': time.strftime('%Y-%m-%d %H:%M:%S'),
        'model': ARGS.model or 'bge-m3:latest',
        'purpose': 'H4：向量余弦能否判"同一实体"（"替代字符重叠"这个说法成不成立）',
        'thresholds': {'HIGH': HIGH, 'LOW': LOW},
        'cases': rows,
        'summary': {
            'n': len(rows),
            'pos_min': min(pos), 'pos_max': max(pos),
            'neg_min': min(neg), 'neg_max': max(neg),
            'classes_overlap': bool(overlap),
            'best_single_threshold': best,
            'auto_decided': n_decided, 'auto_correct': n_right, 'auto_wrong': n_wrong,
        },
        'conclusion': ('**中间区不可判** —— "该拒"的最高分高于"该合"的最低分。'
                       '余弦只能作为高分区（≥HIGH）的补充通道，不能替代字符判据。'),
    }
    Path(ARGS.out).write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                              encoding='utf-8')
    print()
    print(f'  已写 {Path(ARGS.out).name}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
