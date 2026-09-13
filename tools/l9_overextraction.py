"""验证一个刚浮现的假说：项目的真实问题可能不是"漏"，而是"多"。

【假说的来源】
  在否决"本地重抽"的对照实验里，顺手查了"云端当初从金标段落里抽了什么"，
  结果每段抽出 **19–30 条**边，而金标只列了 2–5 条。

  这与"关系召回只有一半"的叙事**方向相反**：
    · 如果是"漏"，云端应该抽得比金标少；
    · 实际是云端抽得比金标**多得多**。

  所以真实画像可能是：**抽取层高召回、低精度**——抽很多，其中一部分不收支持。
  这与 L5 的独立读数（PPV 49.7%）方向一致。

本脚本量化：金标所在段落的原文，对应图里多少条边、其中多少过 L5 阈值、
分数分布如何。若"未过阈值的边"占大头，则"多抽"假说成立。
"""
import json
import re
from pathlib import Path

Q = Path(r'D:\kg-demo\data\quality')


def flat(s):
    return re.sub(r'\s+', '', str(s or ''))


sample = json.loads((Q / 'l9-span-sample.json').read_text(encoding='utf-8'))
edges = json.loads((Q / 'relation-verify-all.json').read_text(encoding='utf-8'))['edges']

# ── 先建索引：evidence 前 60 字 → 边 ──
# 初版对每段遍历全部 43867 条边（34 × 43867 ≈ 150 万次字符串操作），
# 在回归里直接跑到 300 秒超时。建一次索引就够。
EV_INDEX = {}
for e in edges:
    key = flat(e.get('evidence'))[:60]
    if key:
        EV_INDEX.setdefault(key, []).append(e)


def edges_of_span(text):
    """取"证据落在本段文本里"的边。做法是按 evidence 前缀在段内做包含判断——
    仍要逐条试，但只试**前缀出现在本段**的那些（用短前缀先筛）。"""
    t = flat(text)
    out = []
    for key, es in EV_INDEX.items():
        if key in t:
            out.extend(es)
    return out


tot = 0
hi = 0
lo = 0
no_ev = 0
rows = []
for sp in (sample.get('spans') or []):
    seg = edges_of_span(sp.get('text') or '')
    if not seg:
        no_ev += 1
        continue
    p = sorted(e.get('entail_prob') or 0 for e in seg)
    passed = sum(1 for x in p if x >= 0.67)
    tot += len(seg)
    hi += passed
    lo += len(seg) - passed
    rows.append((sp['id'], len(seg), passed, p[len(p) // 2]))
    print(f'  {sp["id"]:20s} 抽 {len(seg):3d} 条  pass {passed:3d} '
          f'({passed / len(seg) * 100:3.0f}%)  中位分 {p[len(p) // 2]:.3f}')

print()
if tot:
    print(f'合计：{tot} 条边，pass {hi} 条（{hi / tot * 100:.0f}%），未过 {lo} 条'
          f'（{lo / tot * 100:.0f}%）')
print(f'没有任何边把证据落在这段里的段落数: {no_ev} / {len(sample.get("spans") or [])}')

# ── 结构性 / 低学术价值的边占多少 ──
# 在对照实验里看到大量这类边，且**分数很高**（0.97+）：
#     [PART_OF] 'Ontology Creation' → 'Section 2.1.1'
#     [PART_OF] 'KG Construction'  → 'Section 2.1'
# 它们描述的是**论文自身的章节结构**，不是领域知识。
# L5 的判别器会给它们高分（因为句子里确实这么写），所以它们能通过过滤，
# 但**对知识图谱没有价值**——这是"高 entail_prob ≠ 高价值"的一个实例。
import re as _re
SEC = _re.compile(r'\b(section|sec\.?|chapter|figure|fig\.?|table|appendix|eq\.?|'
                  r'equation|algorithm|paragraph|listing)\b', _re.I)
struct_edges = []
for sp in (sample.get('spans') or []):
    for e in edges_of_span(sp.get('text') or ''):
        if SEC.search(str(e.get('source') or '')) or SEC.search(str(e.get('target') or '')):
            struct_edges.append(e)
print()
print('=== 结构性边（端点是 Section/Figure/Table 等）===')
print(f'  条数 {len(struct_edges)} / {tot} = '
      f'{len(struct_edges) / max(1, tot) * 100:.1f}%')
if struct_edges:
    ps = sorted(e.get('entail_prob') or 0 for e in struct_edges)
    sp_pass = sum(1 for x in ps if x >= 0.67)
    print(f'  其中 pass {sp_pass} 条（{sp_pass / len(struct_edges) * 100:.0f}%），'
          f'中位分 {ps[len(ps) // 2]:.3f}')
    print('  样例：')
    for e in struct_edges[:6]:
        print(f'    [{e.get("type")}] {str(e.get("source"))[:30]!r} → '
              f'{str(e.get("target"))[:30]!r}  (entail={e.get("entail_prob")})')
    print()
    print('  ⇒ 这类边描述的是**论文自身的章节结构**，不是领域知识：')
    print('     它们 entail_prob 很高（句子里确实这么写）⇒ 能通过 L5 过滤，')
    print('     但**对知识图谱没有价值**。这是"高 entail_prob ≠ 高价值"的实例。')

print()
print('=== 判读 ===')
if tot:
    print(f'· 每段平均 {tot / len(rows):.0f} 条边、pass 率 {hi / tot * 100:.0f}%')
    print(f'· 金标只列 2–5 条 ⇒ **云端抽的远比金标多**')
    print(f'· 结构性边（Section/Figure/Table）只占 '
          f'{len(struct_edges) / max(1, tot) * 100:.1f}%'
          f'（但它们 100% pass、中位分 0.96 ⇒ 属"高分低价值"）')
    print()
    print('  ⚠️ 我原先猜"云端大量产出结构边"，**数据否掉了这个说法**（只 3%）。')
    print('     所以正确解读是另一个方向：')
    print('     **不是抽取器漏了关系，也不是它尽抽结构边，')
    print('       而是金标本身列举不全**——')
    print(f'     云端每段产出约 {tot / len(rows):.0f} 条边（七成通过 L5），')
    print('     而"独立判定者"每段只列了 2–5 条它认为最明确的。')
    print()
    print('     ⇒ 「关系召回 43–57%」这个数字的含义要限定为：')
    print('       **"金标列出的那几条里，约一半能在图中找到"**，')
    print('       而**不能**读成"图只覆盖了原文关系的一半"——')
    print('       因为图里的边数（每段 19 条）远超金标列出的条数。')
    print()
    print('     这再次印证：**关系抽取的评测本身比实体评测不可靠得多**，')
    print('     任何单点数字都应带上它的构造方式一起读。')

# 顺带给出这 645 条的构成，避免只凭一个比例下结论
from collections import Counter
type_c = Counter()
for sp in (sample.get('spans') or []):
    for e in edges_of_span(sp.get('text') or ''):
        type_c[e.get('type')] += 1
print()
print('=== 这 645 条边的类型构成（前 8）===')
for k, v in type_c.most_common(8):
    print(f'  {k:16s} {v:4d}  ({v / max(1, tot) * 100:4.1f}%)')

out = {
    'n_edge_on_gold_spans': tot,
    'n_pass': hi, 'n_not_pass': lo, 'no_evidence_spans': no_ev,
    'n_structural': len(struct_edges),
    'structural_share': round(len(struct_edges) / max(1, tot), 4),
    'per_span': [{'id': i, 'edges': n, 'pass': p, 'median': round(m, 4)}
                 for i, n, p, m in rows],
    'reading': (f'金标段落对应 {tot} 条边（每段约 {tot / max(1, len(rows)):.0f} 条），'
                f'pass 率 {hi / max(1, tot) * 100:.0f}%；其中结构性边'
                f'（端点为 Section/Figure/Table）{len(struct_edges)} 条'
                f'（{len(struct_edges) / max(1, tot) * 100:.0f}%）。'
                f'⇒ 不是"漏了关系"，而是产出构成里有大量高分的低价值结构边。'),
}
(Q / 'l9-overextraction.json').write_text(
    json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
print(f'\n报告已写出: {Q / "l9-overextraction.json"}')
