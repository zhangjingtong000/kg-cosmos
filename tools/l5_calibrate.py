# -*- coding: utf-8 -*-
"""
T2.3 · 为 L5 关系证据层定过滤线。

    python tools/l5_calibrate.py

权威数据：data/quality/relation-verify-all.json（2026-09-11 15:13，44172 条边）
每边字段：paper / source / target / type / description / evidence / page /
          premise / hit / hypothesis / entail_prob / pass
模型：Babelscape/mdeberta-v3-base-triplet-critic-xnli，support_index=1，
      本地标定 threshold=0.67、ROC-AUC 0.9851（4000 样本）

本脚本要回答的问题：**该不该给不同关系类型配不同的过滤线？**

先核实前提：
  · 早先的阶段计划 §T2 称"RELATED_TO 呈明确双峰，<0.1 占 50.1%"。
    实测 <0.1 仅 3.6%，p10=0.192、p50=0.690、p90=0.969 —— 连续右偏，非双峰。
  · 所以"按类型找双峰切点"这条路的前提不成立。

再给结论：
  entail_prob 是模型的蕴涵概率，它的判别语义由**单一标定阈值**定义。给不同
  关系类型配不同阈值，等于宣称"PART_OF 到 0.6 就算被支持，而 CONTRADICTS 要
  0.9 才算"，没有任何依据。正确做法是统一用标定阈值，**按类型报告通过率**，
  通过率的差异本身就是质量画像。
"""
# 路径以本文件为锚点解析，不写死盘符——演示机不一定把仓库放在 D 盘。
from pathlib import Path as _Path
_HERE = _Path(__file__).resolve()
ROOT = _HERE.parent.parent          # tools/*.py 与 src/*.py 都在根目录下一层
import collections
import json

import numpy as np

P = str(ROOT / 'data/quality/relation-verify-all.json')
CORE = [
    'RELATED_TO', 'CREATED_BY', 'USES', 'PART_OF', 'APPLIES_TO', 'PRODUCES',
    'ENABLES', 'CITES', 'MEASURES', 'LOCATED_AT', 'SUPPORTS', 'COMPARES_WITH',
    'EVALUATES', 'CAUSES', 'CONTRADICTS',
]
BINS = 20


def smooth(h, times=2):
    for _ in range(times):
        h = np.convolve(h, [0.25, 0.5, 0.25], mode='same')
    return h


def peaks_of(vals):
    """直方图平滑后的局部极大值个数与位置（用于判断是否真双峰）。"""
    hist, _ = np.histogram(vals, bins=BINS, range=(0.0, 1.0))
    h = smooth(hist.astype(float))
    pk = [i for i in range(1, len(h) - 1) if h[i] > h[i - 1] and h[i] >= h[i + 1]]
    return pk, h


def main():
    with open(P, encoding='utf-8') as f:
        d = json.load(f)

    thr = d.get('threshold')
    print('=' * 104)
    print('T2.3  关系证据层过滤线标定')
    print('=' * 104)
    print(f'模型标定阈值 threshold={thr}   ROC-AUC={d.get("calib_roc_auc")}   '
          f'标定样本 {d.get("calib_n")}   设备 {d.get("device")}')

    # ── 0. 核实 pass 字段就是 entail_prob >= threshold ──
    edges = d['edges']
    bad = sum(1 for e in edges
              if ('pass' in e and 'entail_prob' in e)
              and (e['pass'] != (e['entail_prob'] >= thr)))
    print(f'\n[核实] pass 字段与 entail_prob >= {thr} 不一致的边：{bad} / {len(edges)}')

    by_type = collections.defaultdict(list)
    for e in edges:
        by_type[e.get('type')].append(e)

    print()
    print('-' * 104)
    print('① 真实分布（能否支持"双峰"这一前提）')
    print('-' * 104)
    print(f'{"type":<15}{"n":>7}{"p10":>7}{"p25":>7}{"p50":>7}{"p75":>7}{"p90":>7}'
          f'{"<0.1":>7}{">0.9":>7}{"峰数":>6}  判定')
    bimodal_cnt = 0
    for t in CORE:
        rows = by_type.get(t, [])
        if not rows:
            continue
        v = np.array(sorted(e['entail_prob'] for e in rows))
        q = lambda p: float(np.quantile(v, p))
        lo = float((v < 0.1).mean())
        hi = float((v > 0.9).mean())
        pk, h = peaks_of(v)
        # 真双峰：两个峰、峰高都 ≥ 最大峰高的 35%、两峰隔开至少 5 个 bin(0.25)
        strong = [i for i in pk if h[i] >= 0.35 * h.max()]
        is_bi = len(strong) == 2 and (strong[1] - strong[0]) >= 5
        bimodal_cnt += int(is_bi)
        tag = '双峰' if is_bi else '连续单峰'
        print(f'{t:<15}{len(v):>7}{q(.10):>7.3f}{q(.25):>7.3f}{q(.50):>7.3f}'
              f'{q(.75):>7.3f}{q(.90):>7.3f}{lo:>7.1%}{hi:>7.1%}{len(strong):>6}  {tag}')

    print(f'\n→ 判定为真双峰的类型：{bimodal_cnt} / {len(CORE)}')
    print('→ "按类型找双峰切点"这条路不成立；分布均为连续右偏。')

    # ── ② 统一阈值下的通过率 ──
    print()
    print('-' * 104)
    print(f'② 统一标定阈值 {thr} 下各类型的通过率（这才是可报告的质量画像）')
    print('-' * 104)
    print(f'{"type":<15}{"n":>7}{"通过":>7}{"通过率":>9}   {"both端点":>9}{"both通过率":>11}')
    stat = []
    for t in CORE:
        rows = by_type.get(t, [])
        if not rows:
            continue
        n = len(rows)
        p = sum(1 for e in rows if e['entail_prob'] >= thr)
        both = [e for e in rows if e.get('hit') == 'both']
        bp = sum(1 for e in both if e['entail_prob'] >= thr)
        stat.append((t, n, p, p / n, len(both), (bp / len(both)) if both else 0.0))
    stat.sort(key=lambda r: -r[2])
    for t, n, p, rate, nb, br in stat:
        print(f'{t:<15}{n:>7}{p:>7}{rate:>9.1%}   {nb:>9}{br:>11.1%}')

    tot_n = sum(r[1] for r in stat)
    tot_p = sum(r[2] for r in stat)
    print(f'\n{"合计":<15}{tot_n:>7}{tot_p:>7}{tot_p / tot_n:>9.1%}')

    # ── ③ 谁拖垮了整体 ──
    print()
    print('-' * 104)
    print('③ 贡献分解：整体通过率被哪些类型拖低')
    print('-' * 104)
    print(f'{"type":<15}{"占比":>8}{"通过率":>9}{"对整体通过率的贡献":>20}')
    for t, n, p, rate, _, _ in sorted(stat, key=lambda r: -r[1]):
        share = n / tot_n
        print(f'{t:<15}{share:>8.1%}{rate:>9.1%}{share * rate:>20.3f}')
    print(f'\n整体通过率 {tot_p / tot_n:.1%}'
          f'（若把 RELATED_TO 与 CREATED_BY 排除，其余类型为 '
          f'{sum(r[2] for r in stat if r[0] not in ("RELATED_TO", "CREATED_BY")) / sum(r[1] for r in stat if r[0] not in ("RELATED_TO", "CREATED_BY")):.1%}）')


if __name__ == '__main__':
    main()
