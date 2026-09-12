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

# ── 第二批：另外 5 类的 role_violation 样本（2026-09-12 补判）──
# 为什么只有 11 条：这 5 类各自的 role_violation 本来就很少
# （APPLIES_TO 1 / CAUSES 3 / CITES 1 / ENABLES 5 / LOCATED_AT 1）。
# 另外 5 类（SUPPORTS / MEASURES / COMPARES_WITH / CONTRADICTS / RELATED_TO）
# 的 role_violation 是 **0**，没有样本可判——但"零违规"要分两种含义，
# 见下面的 tautology 检查。
JUDGED_B = [
    ("APPLIES_TO", "false_positive"),   # Data → Screening：数据作为被处理对象是合法用法
    ("CAUSES", "false_positive"),       # KG Selection → Human：target 排除 Person 过严
    ("CAUSES", "predicate_wrong"),      # Energy → Carbon Footprint：原因应是过程而非物质
    ("CAUSES", "false_positive"),       # KG Discovery → Human：同上
    ("CITES", "true_violation"),        # Figure 11 → US：位置与引用语义不相干
    ("ENABLES", "false_positive"),      # Pre-Test → Reviews：target 排除 Document 过严
    ("ENABLES", "false_positive"),      # Bio2Token → Biomolecular Complexes：排除 Material 过严
    ("ENABLES", "false_positive"),      # Hardware Capabilities → Researchers：排除 Person 过严
    ("ENABLES", "false_positive"),      # Navigli → Relation Extraction：排除 Person 过严
    ("ENABLES", "false_positive"),      # Huguet Cabot → Relation Extraction：同上
    ("LOCATED_AT", "true_violation"),   # Fast Learning Algorithm → Neural Computation：实为"发表于"
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

    # ── 第二批：另外 5 类，与上面合并统计，但也单列（质量差异很大）──
    b_rel, b_total = {}, Counter()
    for rel, verdict in JUDGED_B:
        b_rel.setdefault(rel, Counter())[verdict] += 1
        b_total[verdict] += 1
    nb = len(JUDGED_B)
    print(f'\n=== 第二批：另外 5 类（n={nb}）===')
    print(f'  {"关系":12s} {"真违规":>7s} {"谓词错":>7s} {"假阳性":>7s}')
    for rel, c in b_rel.items():
        print(f'  {rel:12s} {c.get("true_violation",0):7d} '
              f'{c.get("predicate_wrong",0):7d} {c.get("false_positive",0):7d}')
    bfp = b_total.get('false_positive', 0)
    print(f'  合计假阳性 {bfp}/{nb} = {bfp/nb*100:.1f}%（样本仅 {nb} 条，CI 很宽）')
    print()
    print(f'  ⇒ **两批的假阳性率差一个数量级**（{fp/n*100:.1f}% vs {bfp/nb*100:.1f}%），')
    print(f'     说明 15 类约束表的**内部质量不均**：')
    print(f'     USES / PRODUCES / PART_OF 基本可用，而 ENABLES / CAUSES / APPLIES_TO 太严。')
    print(f'     `ENABLES` 的 5 条**全部**是假阳性，成因一致——')
    print(f'     允许集把 `Person` / `Document` / `Material` 从两端都排掉了，')
    print(f'     而这三类恰是"人被使能 / 文献被使能 / 物质被使能"的正常用法。')
    print(f'     `CAUSES` 的 target 排除 `Person` 也误伤了"某事对人是挑战"这类边。')

    # ── 恒等式检查：零违规 ≠ 约束正确 ──
    print(f'\n=== "零 role_violation" 的两种含义 ===')
    import sys as _sys
    _sys.path.insert(0, str(ROOT / 'src'))
    from ontology import ENTITY_TYPES as _ET, RELATION_ENDPOINT_CONSTRAINTS as _C
    taut = [k for k, c in _C.items()
            if set(c['source_allowed']) >= set(_ET) and set(c['target_allowed']) >= set(_ET)]
    print(f'  两侧允许集都是全集（永不触发）的关系: {len(taut)} 类 —— {sorted(taut)}')
    print(f'  这些类的 role_compliance 是**恒等式**，不提供质量信息；')
    print(f'  其余"零违规"的类有具体限制，100% 表示确实没违规。')
    print(f'  （审计表里 {sorted(taut)} 已显示为 n/a 而非 100%，这一点报告处理正确。）')

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
        # ── 第二批：另外 5 类（样本少，但结论强）──
        'batch_b': {
            'n': nb,
            'by_relation': {k: dict(v) for k, v in b_rel.items()},
            'total': dict(b_total),
            'false_positive_rate': round(bfp / nb, 4),
            'note': ('这 5 类各自的 role_violation 本就很少（APPLIES_TO 1 / CAUSES 3 / '
                     'CITES 1 / ENABLES 5 / LOCATED_AT 1），所以只有 11 条样本，'
                     'CI 很宽；但 ENABLES 的 5 条**全部**是假阳性且成因一致，这个信号是明确的。'),
        },
        # ── 两批的差异：约束表内部质量不均 ──
        'quality_imbalance': {
            'batch_a_fp_rate': round(fp / n, 4),
            'batch_b_fp_rate': round(bfp / nb, 4),
            'reading': ('两批假阳性率差一个数量级 ⇒ 15 类约束表**内部质量不均**：'
                        'USES / PRODUCES / PART_OF 基本可用；'
                        'ENABLES / CAUSES / APPLIES_TO 的允许集太严。'),
        },
        # ── 恒等式检查：零违规 ≠ 约束正确 ──
        'tautological_constraints': {
            'relations': sorted(taut),
            'note': ('这些关系两侧允许集都是全集，role_compliance 是**恒等式**、'
                     '不提供质量信息。审计表里它们已显示为 n/a 而非 100%（处理正确）。'
                     '其余"零违规"的类有具体限制，100% 表示确实没违规。'),
        },
        'conclusion': (
            f'约束假阳性率：前三类 {fp/n*100:.1f}%、后五类 {bfp/nb*100:.1f}% ⇒ 内部质量不均；'
            f'role_violation 里 {pw/n*100:.0f}% 实为谓词选错，'
            f'该指标名应读作"端点类型或谓词问题"。'
        ),
        'proposes': [
            'PRODUCES：target 纳入 Person/Location（NER 的产出就是实体）；source 纳入 Event（会议发表论文）与 Metric（Loss 产生 Gradient）',
            'ENABLES：两端都纳入 Person/Document/Material（人被使能、文献被使能、物质被使能都是正常用法）',
            'CAUSES：target 纳入 Person（"某事对人是挑战"是正常用法）',
            'APPLIES_TO：source 纳入 Material（数据/物料作为被处理对象）',
            '报告里把 role_violation 改称"端点类型或谓词问题"',
        ],
        'caveat': ('第一批 58 条、第二批 11 条；两批都是单一独立判定者、seed=7。'
                   '第二批样本量小，比例估计的置信区间很宽，'
                   '但"ENABLES 五条全是假阳性且成因一致"这一点是明确的。'),
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'\n报告已写出: {OUT}')


if __name__ == '__main__':
    main()
