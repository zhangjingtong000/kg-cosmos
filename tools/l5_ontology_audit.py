# -*- coding: utf-8 -*-
"""
T2.5 · 评估 15 类本体是否完备。

    python tools/l5_ontology_audit.py

背景：早先的阶段计划 §T2 记录了一个反直觉现象——越界关系类型的 entailment
均值（0.664）反而高于本体边（0.476），据此怀疑"15 类本体划分本身可能不完备"。
本脚本用权威数据的真实数字核对这个说法，并给出可判断的结论。

另：库中 L4 显示"0 违规"，是因为 safe_relation_type 把越界类型强制改写成
RELATED_TO；原始抽取里越界边有数千条。本脚本按原始类型统计。
"""
# 路径以本文件为锚点解析，不写死盘符——演示机不一定把仓库放在 D 盘。
from pathlib import Path as _Path
_HERE = _Path(__file__).resolve()
ROOT = _HERE.parent.parent          # tools/*.py 与 src/*.py 都在根目录下一层
import collections
import json
import os
import re
import sys

import numpy as np

sys.path.insert(0, os.path.join(str(ROOT), 'src'))
from ontology import RELATION_TYPES   # noqa: E402

Q = str(ROOT / 'data/quality')
VERIFY = os.path.join(Q, 'relation-verify-all.json')

# 15 类本体，取值来自 src/ontology.py（唯一来源）。
# 不再就地抄一份——同一规则写两处的下场见 docs/ERROR_CASES.md 案例 12。
# APPLIES_TO 属于本体：早先漏掉它会把 3850 条本体边错算成越界，
# 使"越界边得分更高"的结论失真。
ONTOLOGY = set(RELATION_TYPES)
# 语义上看起来合理、值得考虑收编进本体的候选
CANDIDATES = ['CONTRIBUTED_TO', 'CONTAINS', 'REPRESENTED_BY', 'ADDRESSES',
              'PREDICTS', 'EVALUATED_ON', 'OUTPERFORMS', 'IMPROVES',
              'EXTENDS', 'BASED_ON', 'INTRODUCES', 'PROPOSES']


def main():
    with open(VERIFY, encoding='utf-8') as f:
        v = json.load(f)
    edges = v['edges']
    thr = v['threshold']

    in_o, out_o = [], []
    for e in edges:
        (in_o if e.get('type') in ONTOLOGY else out_o).append(e)

    print('=' * 96)
    print('T2.5  本体完备性审计')
    print('=' * 96)
    print(f'总边 {len(edges)}   本体内 {len(in_o)}（{len(in_o)/len(edges):.1%}）   '
          f'本体外 {len(out_o)}（{len(out_o)/len(edges):.1%}）')

    def desc(rows, label):
        if not rows:
            return
        p = np.array([e['entail_prob'] for e in rows])
        print(f'{label:<12} n={len(rows):>6}  mean={p.mean():.4f}  median={np.median(p):.4f}  '
              f'p25={np.quantile(p,.25):.4f}  p75={np.quantile(p,.75):.4f}  '
              f'通过率={(p>=thr).mean():.1%}')

    print()
    print('-' * 96)
    print('① 本体内 vs 本体外：entail_prob 分布')
    print('-' * 96)
    desc(in_o, '本体内')
    desc(out_o, '本体外')
    print('\n（早先的阶段计划称越界边均值 0.664 高于本体边 0.476，上表为实测核对）')

    print()
    print('-' * 96)
    print('② 本体外关系类型的完整分布（越界类型共多少种、各多少条）')
    print('-' * 96)
    cnt = collections.Counter(e.get('type') for e in out_o)
    print(f'越界类型共 {len(cnt)} 种')
    print(f'{"type":<42}{"n":>6}{"mean":>9}{"通过率":>9}')
    for t, n in cnt.most_common(25):
        rows = [e for e in out_o if e.get('type') == t]
        p = np.array([e['entail_prob'] for e in rows])
        print(f'{t[:40]:<42}{n:>6}{p.mean():>9.4f}{(p>=thr).mean():>9.1%}')
    tail = [t for t, n in cnt.items() if n == 1]
    print(f'\n只出现 1 次的越界类型：{len(tail)} 种'
          f'（占越界类型 {len(tail)/len(cnt):.0%}，合计 {len(tail)} 条边）')

    print()
    print('-' * 96)
    print('③ 越界类型里"看着合理、值得考虑收编"的候选')
    print('-' * 96)
    for c in CANDIDATES:
        rows = [e for e in out_o if e.get('type') == c]
        if not rows:
            continue
        p = np.array([e['entail_prob'] for e in rows])
        print(f'{c:<18} n={len(rows):>4}  mean={p.mean():.4f}  通过率={(p>=thr).mean():.1%}'
              f'  例：{rows[0]["source"][:26]} → {rows[0]["target"][:26]}')

    print()
    print('-' * 96)
    print('④ 结论判据')
    print('-' * 96)
    pi = np.array([e['entail_prob'] for e in in_o])
    po = np.array([e['entail_prob'] for e in out_o])
    d = po.mean() - pi.mean()
    se = np.sqrt(po.var(ddof=1) / len(po) + pi.var(ddof=1) / len(pi))
    t = d / se if se > 0 else 0.0
    print(f'本体内 {len(pi)} 条  mean={pi.mean():.4f}   中位={np.median(pi):.4f}')
    print(f'本体外 {len(po)} 条  mean={po.mean():.4f}   中位={np.median(po):.4f}')
    print(f'差值 {d:+.4f}   Welch t = {t:+.2f}   越界占比 {len(po)/len(edges):.2%}')

    # 把越界类型分成"形如关系名（全大写/下划线）"与"其余（多为实体名/短语）"
    looks_rel = [k for k in cnt if re.fullmatch(r'[A-Z][A-Z_]{2,}', k or '')]
    n_rel = sum(cnt[k] for k in looks_rel)
    other = len(po) - n_rel
    print(f'\n越界类型的构成：')
    print(f'  形如关系名（全大写/下划线）  {len(looks_rel):>3} 种 / {n_rel:>4} 条')
    print(f'  其余（实体名或短语）          {len(cnt) - len(looks_rel):>3} 种 / {other:>4} 条')
    if looks_rel:
        print('    其中: ' + ', '.join(f'{k}({cnt[k]})' for k in sorted(looks_rel, key=lambda x: -cnt[x])[:8]))

    print('\n判定：')
    if len(po) / len(edges) < 0.02:
        print(f'  越界边只占 {len(po)/len(edges):.2%}；{len(cnt)} 种越界类型里 '
              f'{len([k for k in cnt if cnt[k]==1])} 种只出现 1 次。')
        print('  这些类型名绝大多数是**实体名被当成了关系类型**（Representation Learning /')
        print('  Entity / Stock / Position ...），属于抽取噪声。')
        print(f'  形如关系名的只有 {len(looks_rel)} 种 / {n_rel} 条。')
        if d > 0.02:
            print(f'\n  ⚠️ 越界边均值比本体内**高** {d:+.4f}（t={t:+.2f}）。')
            print('     但 n 很小，几十条高分噪声即可造成该差距，不足以据此扩本体。')
        elif d < -0.02:
            print(f'\n  越界边均值比本体内**低** {d:+.4f}（t={t:+.2f}）——')
            print('     这说明越界更多是抽取噪声，而不是本体漏掉了真实且可验证的语义。')
            print('     **"本体不完备"不成立。**')
        else:
            print(f'\n  两者均值差异 {d:+.4f} 很小，"越界边得分更高"这一说法不成立。')
        if n_rel and looks_rel:
            print(f'\n  值得单独记录：形如关系名的类型里 '
                  + ', '.join(f'{k}({cnt[k]})' for k in sorted(looks_rel, key=lambda x: -cnt[x])[:5])
                  + '。这些可列为"待观察候选"，但样本量都不足以支撑扩本体。')
    else:
        print('  越界边占比不可忽略，建议逐类复核后决定是否扩本体。')


if __name__ == '__main__':
    main()
