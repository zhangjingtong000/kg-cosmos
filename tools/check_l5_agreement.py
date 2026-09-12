"""算两个 LLM 标注者之间的一致性（决定 40.0% 这个结论可不可信）。

背景：第一位标注者（10 个并行子代理）给出总体支持率 40.0%、
判别器 PPV 49.7%、κ(vs 判别器) 0.236 —— 与本项目一直报的"通过率 63.7%"
差距极大。若这个结论成立，会显著改变论文结论，所以必须先检验标注本身。

判读准则（事先定好，避免事后找理由）：
  · 两标注者 Cohen's κ ≥ 0.6  → 任务可稳定标注，40.0% 可作结论
  · κ 在 0.4–0.6              → 中等一致，结论需标注为"有标注噪声"
  · κ < 0.4                   → 任务本身不可稳定标注，
                                40.0% 只能报为"一次标注的读数"，不能当结论
"""
import json
import math
from pathlib import Path

Q = Path(r'D:\kg-demo\data\quality')
LABELS = Q / 'l5-validity-labels.jsonl'
RAW2 = Q / '_l5batch' / 'second-annotator.json'


def cohen_kappa(a, b):
    """a/b 为同长度的标签序列，取值 {0,1}（unclear 已剔除）。"""
    n = len(a)
    if n == 0:
        return None, None, None
    agree = sum(1 for x, y in zip(a, b) if x == y) / n
    pa1 = sum(a) / n
    pb1 = sum(b) / n
    pe = pa1 * pb1 + (1 - pa1) * (1 - pb1)
    k = (agree - pe) / (1 - pe) if abs(1 - pe) > 1e-12 else 0.0
    return agree, pe, k


def main():
    first = {}
    for line in LABELS.read_text(encoding='utf-8').splitlines():
        if line.strip():
            r = json.loads(line)
            first[r['id']] = r['verdict']
    second = {r['id']: r['verdict'] for r in json.loads(RAW2.read_text(encoding='utf-8'))}

    ids = sorted(set(first) & set(second))
    print(f'两标注者共同标注 {len(ids)} 条\n')

    a, b = [], []
    both_unclear = 0
    for i in ids:
        x, y = first[i], second[i]
        if x == 'unclear' and y == 'unclear':
            both_unclear += 1
        # 主口径：把 unclear 当"不支持"（与 report 脚本的次口径一致），
        # 这样不会因为剔除样本而改变比较基数
        a.append(1 if x == 'support' else 0)
        b.append(1 if y == 'support' else 0)

    agree, pe, k = cohen_kappa(a, b)
    print(f'  一致率      {agree*100:.1f}%')
    print(f'  期望一致率  {pe*100:.1f}%（随机水平）')
    print(f"  Cohen's κ   {k:.3f}")
    print(f'  支持率：标注者1 {sum(a)/len(a)*100:.1f}%  vs  标注者2 {sum(b)/len(b)*100:.1f}%')
    print(f'  两人都判 unclear 的 {both_unclear} 条\n')

    # 逐条分歧
    print('分歧明细：')
    diff = [(i, first[i], second[i]) for i in ids if first[i] != second[i]]
    for i, x, y in diff:
        print(f'  {i}  第一={x:8s} 第二={y}')
    print(f'\n  共 {len(diff)} 条分歧（{len(diff)/len(ids)*100:.0f}%）')

    print('\n=== 判读（准则事先定好）===')
    if k >= 0.6:
        verdict = '任务可稳定标注，40.0% 可作结论'
    elif k >= 0.4:
        verdict = '中等一致，结论需标注"有标注噪声"'
    else:
        verdict = ('任务本身**不可稳定标注**：40.0% 只能报为"一次标注的读数"，'
                   '不能当结论；这也意味着不能用它去否定判别器')
    print(f'  κ={k:.3f} ⇒ {verdict}')

    out = {
        'n': len(ids), 'agreement': round(agree, 4), 'expected_agreement': round(pe, 4),
        'cohen_kappa': round(k, 4),
        'rate_annotator1': round(sum(a) / len(a), 4),
        'rate_annotator2': round(sum(b) / len(b), 4),
        'both_unclear': both_unclear,
        'disagreements': [{'id': i, 'first': x, 'second': y} for i, x, y in diff],
        'reading': verdict,
        'note': '两个标注者都是 LLM（不同会话），不是人类标注者。'
                'κ 衡量的是"这个判定任务能否被稳定复现"，与标注者是不是人无关，'
                '但**不能**据此声称达到了人类金标的水准。',
    }
    (Q / 'l5-annotator-agreement.json').write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'\n报告已写出: {Q / "l5-annotator-agreement.json"}')


if __name__ == '__main__':
    main()
