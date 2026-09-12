"""统计 L7 三类 role_violation 的独立判读结果，并给出结论。

【为什么这一步重要】
  L7 的 15 类约束里原本只有 3 类做过人工精度抽查，其余只做了覆盖率核对。
  补了 USES / PRODUCES / PART_OF 三类的 role_violation 判读后，
  可以回答一个此前没答案的问题：
  **role_violation 到底反映的是"约束太严"还是"抽取真的错了"？**

判读口径（4 类裁决）：
  true_violation  —— 组合确实说不通，约束判得对
  false_positive  —— 组合其实说得通，**约束定得太严**
  predicate_wrong —— 组合说不通，但根因是**谓词选错**（不是端点类型的错）
  unclear
"""
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
Q = ROOT / 'data' / 'quality'
OUT = Q / 'l7-role-violation-audit.json'

# 独立判读结果（来自一个独立会话，输入是 58 条盲态样本）
JUDGED = [
    # USES (20)
    ("USES", "predicate_wrong"), ("USES", "predicate_wrong"), ("USES", "predicate_wrong"),
    ("USES", "true_violation"), ("USES", "predicate_wrong"), ("USES", "true_violation"),
    ("USES", "predicate_wrong"), ("USES", "true_violation"), ("USES", "predicate_wrong"),
    ("USES", "predicate_wrong"), ("USES", "predicate_wrong"), ("USES", "predicate_wrong"),
    ("USES", "predicate_wrong"), ("USES", "true_violation"), ("USES", "predicate_wrong"),
    ("USES", "true_violation"), ("USES", "predicate_wrong"), ("USES", "predicate_wrong"),
    ("USES", "predicate_wrong"), ("USES", "predicate_wrong"),
    # PRODUCES (20)
    ("PRODUCES", "false_positive"), ("PRODUCES", "predicate_wrong"), ("PRODUCES", "predicate_wrong"),
    ("PRODUCES", "predicate_wrong"), ("PRODUCES", "false_positive"), ("PRODUCES", "true_violation"),
    ("PRODUCES", "predicate_wrong"), ("PRODUCES", "predicate_wrong"), ("PRODUCES", "true_violation"),
    ("PRODUCES", "predicate_wrong"), ("PRODUCES", "false_positive"), ("PRODUCES", "false_positive"),
    ("PRODUCES", "predicate_wrong"), ("PRODUCES", "predicate_wrong"), ("PRODUCES", "true_violation"),
    ("PRODUCES", "predicate_wrong"), ("PRODUCES", "false_positive"), ("PRODUCES", "predicate_wrong"),
    ("PRODUCES", "predicate_wrong"), ("PRODUCES", "predicate_wrong"),
    # PART_OF (18)
    ("PART_OF", "true_violation"), ("PART_OF", "predicate_wrong"), ("PART_OF", "predicate_wrong"),
    ("PART_OF", "true_violation"), ("PART_OF", "true_violation"), ("PART_OF", "predicate_wrong"),
    ("PART_OF", "true_violation"), ("PART_OF", "true_violation"), ("PART_OF", "true_violation"),
    ("PART_OF", "true_violation"), ("PART_OF", "true_violation"), ("PART_OF", "predicate_wrong"),
    ("PART_OF", "true_violation"), ("PART_OF", "predicate_wrong"), ("PART_OF", "predicate_wrong"),
    ("PART_OF", "true_violation"), ("PART_OF", "predicate_wrong"), ("PART_OF", "true_violation"),
]


def main():
    by_rel = {}
    total = Counter()
    for rel, verdict in JUDGED:
        by_rel.setdefault(rel, Counter())[verdict] += 1
        total[verdict] += 1

    n = len(JUDGED)
    print(f'共判读 {n} 条 role_violation 样本\n')
    print(f'{"关系":10s} {"true_violation":>15s} {"predicate_wrong":>16s} '
          f'{"false_positive":>15s} {"合计":>6s}')
    for rel, c in by_rel.items():
        m = sum(c.values())
        print(f'{rel:10s} {c.get("true_violation",0):15d} '
              f'{c.get("predicate_wrong",0):16d} {c.get("false_positive",0):15d} {m:6d}')
    print(f'{"合计":10s} {total.get("true_violation",0):15d} '
          f'{total.get("predicate_wrong",0):16d} {total.get("false_positive",0):15d} {n:6d}')

    fp = total.get('false_positive', 0)
    pw = total.get('predicate_wrong', 0)
    tv = total.get('true_violation', 0)
    print(f'\n=== 结论 ===\n')
    print(f'  约束定得太严（假阳性）      {fp}/{n} = {fp/n*100:.1f}%')
    print(f'  谓词选错（不是端点类型的错） {pw}/{n} = {pw/n*100:.1f}%')
    print(f'  真的端点类型违规            {tv}/{n} = {tv/n*100:.1f}%')
    print()
    print(f'  ⇒ **约束本身的假阳性率只有 {fp/n*100:.1f}%，说明表的允许集定得不算太严。**')
    print(f'  ⇒ 但 `role_violation` 里有 {pw/n*100:.0f}% 其实是**谓词选错**——')
    print(f'     这个指标名有误导性：它捕捉的主要不是"端点类型不对"，')
    print(f'     而是"关系类型选错了"。报告里应把它读作')
    print(f'     "端点类型或谓词问题"，而不是"端点类型违规"。')

    # PRODUCES 的假阳性集中在哪
    print(f'\n  PRODUCES 的 {by_rel.get("PRODUCES", Counter()).get("false_positive",0)} 条假阳性集中在三类：')
    print('    · NER → 人名/地名（NER 的产出就是被识别出的实体，target 允许集缺 Person/Location）')
    print('    · 会议 → 论文（会议发表论文，source 允许集缺 Event）')
    print('    · Loss → Gradient（source 允许集缺 Metric）')

    out = {
        'n': n,
        'by_relation': {k: dict(v) for k, v in by_rel.items()},
        'total': dict(total),
        'false_positive_rate': round(fp / n, 4),
        'predicate_wrong_rate': round(pw / n, 4),
        'true_violation_rate': round(tv / n, 4),
        'conclusion': (
            f'约束假阳性率仅 {fp/n*100:.1f}%，允许集定得不算太严；'
            f'但 role_violation 里 {pw/n*100:.0f}% 实为谓词选错，'
            f'该指标名应读作"端点类型或谓词问题"。'
        ),
        'proposes': [
            'PRODUCES 的 target 允许集应纳入 Person / Location（NER 类方法的产出就是实体）',
            'PRODUCES 的 source 允许集应纳入 Event（会议发表论文）与 Metric（Loss 产生 Gradient）',
            '报告里把 role_violation 改称"端点类型或谓词问题"',
        ],
        'caveat': '样本 58 条、单一独立判定者、seed=7；结论是量级判断而非精确比例。',
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'\n报告已写出: {OUT}')


if __name__ == '__main__':
    main()
