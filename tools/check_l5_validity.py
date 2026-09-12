"""L5 域内效度：可自动化部分的检查 + 人工盲判进度追踪。

【这层要回答什么】
  对外报的 L5 = 63.7% 是**判别器判通过的比例**，既不是精度也不是召回；
  而阈值 0.67 来自**官方英文校准集**，本项目数据上的效度从未被验证过。

【为什么只能部分自动化】
  真正的效度需要**判别器之外**的判定者。零预算下没有现成的非模型真值：
  本机另一个模型（Ollama）能回答"换判定器结论变多少"，但两个模型一致 ≠ 正确
  ——它们可能共享同一偏置。所以严格效度只能由人盲判给出。
  本脚本因此做两件事：
    ① 断言**可自动化**的那些结论（表述敏感性、方向盲区、规则假阳性下界）；
    ② 报告人工盲判的**进度**，让"这层还没做完"是可见的，而不是被当成做完了。

用法：
  python tools/check_l5_validity.py            # 读已有产物，快
  python tools/check_l5_validity.py --probe    # 顺带重跑小规模探针（约 80 秒）
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
Q = ROOT / 'data' / 'quality'
PROBE = Q / 'l5-validity-probe.json'
SAMPLE = Q / 'l5-validity-sample.jsonl'
KEY = Q / 'l5-validity-key.json'
LABELS = Q / 'l5-validity-labels.jsonl'      # 人工盲判落盘（可不存在）


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--probe', action='store_true', help='顺带重跑探针（慢）')
    args = ap.parse_args()

    fails, notes = [], []

    if args.probe:
        import subprocess
        print('重跑探针（n=30）…')
        subprocess.run([sys.executable, str(ROOT / 'tools' / 'l5_validity_probe.py'),
                        '--n', '30'], cwd=str(ROOT), check=False)

    if not PROBE.exists():
        print(f'缺少 {PROBE.name}，先跑 tools/l5_validity_probe.py')
        return 1
    d = json.loads(PROBE.read_text(encoding='utf-8'))
    p = d['probes']
    print('=== ① 可自动化结论 ===\n')

    # P1：假设句表述的敏感性——若极差很大，说明 63.7% 只是措辞的产物
    forms = {k: v for k, v in p['P1_hypothesis_form'].items() if isinstance(v, dict)}
    rates = {k: v['pass_rate'] for k, v in forms.items()}
    spread = (max(rates.values()) - min(rates.values())) * 100
    print(f'  P1 假设句形态通过率极差: {spread:.1f}pp  '
          f'({", ".join(f"{k}={v*100:.1f}%" for k, v in rates.items())})')
    if spread > 25:
        fails.append(f'P1 表述极差 {spread:.1f}pp 过大 ⇒ 63.7% 主要是措辞的产物')
    else:
        notes.append(f'P1 极差 {spread:.1f}pp，未发现"措辞主导"')

    # P2：前提形态（单句 vs 整段）——标定与运行口径不一致的证据
    p2 = {k: v for k, v in p['P2_premise_form'].items() if isinstance(v, dict)}
    if len(p2) == 2:
        a, b = list(p2.values())
        delta = (b['pass_rate'] - a['pass_rate']) * 100
        print(f'  P2 前提形态: 单句 {a["pass_rate"]*100:.1f}% vs 整段 {b["pass_rate"]*100:.1f}%'
              f'  Δ={delta:+.1f}pp')
        notes.append(f'P2 标定口径（整段）与运行口径（单句）相差 {delta:+.1f}pp')

    # P3：方向盲区——L5 能拦住多少方向反转
    p3 = p['P3_direction']
    print(f'  P3 方向反转的翻转率: {p3["flip_rate"]*100:.1f}%  '
          f'（mean Δ={p3["mean_delta"]:+.4f}, n={p3["n"]}）')
    if p3['flip_rate'] > 0.5:
        fails.append('P3 翻转率异常高，与"L5 拦不住方向错误"的既有结论不符')
    else:
        notes.append(f'P3 只拦住 {p3["flip_rate"]*100:.1f}% 的方向反转'
                     f' ⇒ 约 {100-p3["flip_rate"]*100:.0f}% 被静默放过')

    # P4：规则型假阳性下界（完全独立于模型权重，只依赖字面规则）
    p4 = p['P4_rule_based_fp_floor']
    strict = p4['selfloop_asym_pass'] / p4['pass_total'] * 100
    loose = p4['selfloop_pass'] / p4['pass_total'] * 100
    print(f'  P4 规则型假阳性下界（分母=通过集 {p4["pass_total"]}）: '
          f'严格 {p4["selfloop_asym_pass"]} 条 = {strict:.2f}%，'
          f'宽松 {p4["selfloop_pass"]} 条 = {loose:.2f}%')
    notes.append(f'P4 给出了一个**独立于模型**的假阳性下界（≥{strict:.2f}%）'
                 f'——这是本层唯一不依赖任何模型权重的证据')

    # ── ② 人工盲判进度 ──
    print('\n=== ② 人工盲判进度（严格效度只能来自这里）===\n')
    n_sample = 0
    if SAMPLE.exists():
        n_sample = sum(1 for _ in SAMPLE.open(encoding='utf-8'))
    n_label = 0
    if LABELS.exists():
        n_label = sum(1 for _ in LABELS.open(encoding='utf-8'))
    print(f'  抽样清单 {n_sample} 条  →  {LABELS.name}')
    print(f'  已判      {n_label} 条')
    if n_sample and n_label >= n_sample:
        print('  ✅ 盲判已完成，可跑 tools/l5_validity_report.py 出 PPV/召回')
    elif n_label:
        print(f'  ⏳ 进行中（{n_label/n_sample*100:.0f}%）；'
              f'跑 tools/l5_validity_review.py 继续')
    else:
        print('  ⏳ 尚未开始。启动盲判界面：')
        print('       python tools/l5_validity_review.py   → http://127.0.0.1:8097/')
        print('     这是本层唯一能给出**严格效度**的路径；'
              '在它完成之前，L5 的 63.7% 只能声明为"通过率"，不能声明为"准确率"。')

    print('\n=== 结论 ===\n')
    for n in notes:
        print(f'  · {n}')
    print()
    print(f'  ⚠️ **本层未完成**：严格效度需要人工盲判 {n_sample} 条（约 3–6 小时），')
    print(f'     当前进度 {n_label}/{n_sample}。在此之前只有上列**代理证据**，')
    print(f'     它们能证明"结论对措辞不敏感""方向确实拦不住"，')
    print(f'     但**不能**给出 L5 的真实精度。')

    (Q / 'l5-validity-check.json').write_text(json.dumps({
        'probe': {'forms_spread_pp': round(spread, 2), 'rates': rates,
                  'p2_delta_pp': round(delta, 2) if len(p2) == 2 else None,
                  'p3_flip_rate': p3['flip_rate'], 'p4_strict_ppm': round(strict, 3)},
        'annotation': {'sampled': n_sample, 'labeled': n_label,
                       'complete': bool(n_sample and n_label >= n_sample)},
        'notes': notes,
        'caveat': '严格效度需人工盲判；代理证据不能替代。',
    }, ensure_ascii=False, indent=2), encoding='utf-8')

    if fails:
        print('\n未通过：')
        for f in fails:
            print('  · ' + f)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
