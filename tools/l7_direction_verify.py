"""L7 方向校验层（原型，只覆盖 CREATED_BY）。

【为什么需要这一层】
  已确证：六层里**没有任何一层能可靠发现关系方向错误**（见
  `docs/L5_SIX_LAYER_REPORT.md` §4.5）——
  L3 只判端点在不在节点集内、L4 只判谓词在不在本体内，两者都不看方向；
  L5 用 NLI 比较正/反向 hypothesis，实测**只能拦住 23.9%** 的方向反转
  （720 条样本，正向仅比反向平均高 +0.1260）。
  而 T3.1 已经用规则反转了 2348 条 CREATED_BY 边 —— **这个改动无法被验证**。

【本层的判据：端点类型约束，而不是语义模型】
  方向可以由**端点的类型**约束住：`CREATED_BY` 在 T3.1 里被统一成
  `Author(Person/Organization) → Work(...)`，这正是
  `tools/apply_created_by_fix.py` 里已有的 `AUTHOR` / `CREATABLE` 定义。
  于是"方向对不对"变成一个纯规则、零成本、可复现的检查。
  本层**只做 CREATED_BY**（保留作为对账基线）：其余 14 类的端点类型约束已定义在
  `src/ontology.py::RELATION_ENDPOINT_CONSTRAINTS`，逐类审计由
  `tools/l7_relation_constraints_audit.py` 产出，完整报告见
  `docs/L7_ENDPOINT_CONSTRAINTS.md`。自动统计推导已证伪——
  16 类的"主导端点类型对"占比全 < 60%，见 l7_direction_audit.py 的结论）。

用法：
  python tools/l7_direction_verify.py
"""
# 路径以本文件为锚点解析，不写死盘符——演示机不一定把仓库放在 D 盘。
from pathlib import Path as _Path
_HERE = _Path(__file__).resolve()
ROOT = _HERE.parent.parent          # tools/*.py 与 src/*.py 都在根目录下一层
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(ROOT / 'tools'))

GRAPH = ROOT / 'data/corpus/graphs'
OUT = ROOT / 'data/quality/l7-direction-verification.json'

# ── 端点类型约束来自 src/ontology.py（唯一来源）──
# 早先这里是从 tools/apply_created_by_fix.py 抄一份，等于
# "用补丁的假设去验证补丁"（对抗评审指出过）。现在两边都从 ontology 取，
# 至少保证不会再各自漂移；效度问题另见报告里的说明。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src'))
from ontology import (  # noqa: E402
    CREATED_BY_AUTHOR_TYPES as AUTHOR,
    CREATED_BY_CREATABLE_TYPES as CREATABLE,
)


def load_nodes():
    """节点名 -> 类型（同名取第一个出现的，与 l5_report 口径一致）。"""
    t = {}
    for f in sorted(GRAPH.glob('*.json')):
        snap = json.loads(f.read_text(encoding='utf-8'))
        for n in snap.get('nodes', []):
            nm = (n.get('name') or '').strip()
            if nm and nm not in t:
                t[nm] = n.get('type') or 'Concept'
    return t


def load_edges():
    out = []
    for f in sorted(GRAPH.glob('*.json')):
        snap = json.loads(f.read_text(encoding='utf-8'))
        for e in snap.get('edges', []):
            if str(e.get('type', '')).upper() == 'CREATED_BY':
                out.append((f.stem, e.get('source'), e.get('target')))
    return out


def main():
    ntype = load_nodes()
    edges = load_edges()
    print(f'节点 {len(ntype)} 个；CREATED_BY 边 {len(edges)} 条\n')

    both_ok = src_bad = tgt_bad = unknown = 0
    bad_samples = []
    bad_pair = Counter()
    unknown_names = Counter()

    for paper, s, t in edges:
        ts, tt = ntype.get(s), ntype.get(t)
        # `Unknown` 是"从边反建、类型未知"的节点（backfill_missing_endpoints.py）。
        # 它的类型未知 ⇒ 端点类型约束**根本无法判定**，既不能说合规也不能说违规。
        # 必须走 unknown 分支，否则 4304 个反建节点会全部被算成"方向违规"——
        # 实测那样会把合规率从 93.2% 假摔到 67.1%，把"端点类型未知"
        # 误报成"方向错误"。
        if ts is None or tt is None or ts == 'Unknown' or tt == 'Unknown':
            unknown += 1
            for nm, ty in ((s, ts), (t, tt)):
                if ty is None or ty == 'Unknown':
                    unknown_names[nm] += 1
            continue
        ok_s, ok_t = ts in AUTHOR, tt in CREATABLE
        if ok_s and ok_t:
            both_ok += 1
        else:
            if not ok_s:
                src_bad += 1
            if not ok_t:
                tgt_bad += 1
            bad_pair[(ts, tt)] += 1
            if len(bad_samples) < 12:
                bad_samples.append((paper, s, ts, t, tt))

    resolvable = len(edges) - unknown
    print('=== L7 方向合规（CREATED_BY，判据 = 端点类型约束）===')
    print(f'  约束: source ∈ {sorted(AUTHOR)}')
    print(f'        target ∈ {sorted(CREATABLE)}\n')
    print(f'  可判定的边      : {resolvable} / {len(edges)}'
          f'（{unknown} 条因端点不在节点表内无法判定）')
    if resolvable:
        print(f'  方向合规        : {both_ok} / {resolvable} = '
              f'{both_ok/resolvable*100:.1f}%')
        print(f'    source 不合法 : {src_bad}  ({src_bad/resolvable*100:.1f}%)')
        print(f'    target 不合法 : {tgt_bad}  ({tgt_bad/resolvable*100:.1f}%)')

    print('\n=== 违规最多的端点类型组合 Top 10 ===')
    for (ts, tt), c in bad_pair.most_common(10):
        print(f'  {c:5d}  {ts:14s} → {tt}')

    print('\n=== 违规样例（前 12 条）===')
    for paper, s, ts, t, tt in bad_samples:
        print(f'  [{paper}] {s[:30]!r}({ts}) → {t[:30]!r}({tt})')

    # ── 违规分类：这决定"下一步能不能自动修" ──
    import re
    CITATIONISH = re.compile(r'et\s+al|\bet\b.*\bal\b|\(\d{4}\)|,\s*\d{4}\b',
                             re.IGNORECASE)
    cls = Counter()
    cls_samples = {'reversed': [], 'citation_endpoint': [], 'other': []}
    for paper, s, t in edges:
        ts, tt = ntype.get(s), ntype.get(t)
        # `Unknown` 是反建出来的节点类型（见 src/ontology.py 的 DERIVED_ENTITY_LABELS），
        # 与"端点不在节点表内"一样**不可判定**，必须一起跳过。
        # 早先这里只跳过 None，于是 2425 条 Unknown 端点的边全部掉进违规分类，
        # 三类之和被抬到 2847，而实际可判定违规只有 422 —— 报告里那 2352 条
        # 「其他（约束可能偏紧）」绝大多数是这么来的，会把读数的人引向
        # "约束太严"的错误结论。
        if ts is None or tt is None or ts == 'Unknown' or tt == 'Unknown':
            continue
        ok_s, ok_t = ts in AUTHOR, tt in CREATABLE
        if ok_s and ok_t:
            continue
        # ① 完全颠倒：源是"被创作物"、目标是"作者"
        if ts in CREATABLE and tt in AUTHOR:
            cls['reversed'] += 1
            if len(cls_samples['reversed']) < 6:
                cls_samples['reversed'].append((s, ts, t, tt))
        # ② 端点本身是引文式文本（`Mikolov et al.`）——是端点质量问题，不是方向问题
        elif CITATIONISH.search(s) or CITATIONISH.search(t):
            cls['citation_endpoint'] += 1
            if len(cls_samples['citation_endpoint']) < 6:
                cls_samples['citation_endpoint'].append((s, ts, t, tt))
        else:
            cls['other'] += 1
            if len(cls_samples['other']) < 6:
                cls_samples['other'].append((s, ts, t, tt))

    print('\n=== 违规分类（决定能不能自动修）===')
    print(f'  ① 方向完全颠倒（源是被创作物、目标是作者）: {cls["reversed"]} 条')
    for s, ts, t, tt in cls_samples['reversed']:
        print(f'       {s[:30]!r}({ts}) → {t[:30]!r}({tt})')
    print(f'  ② 端点本身是引文式文本（如 "Mikolov et al."）: {cls["citation_endpoint"]} 条')
    for s, ts, t, tt in cls_samples['citation_endpoint']:
        print(f'       {s[:30]!r}({ts}) → {t[:30]!r}({tt})')
    print(f'  ③ 其他（约束可能偏紧，需人工看）: {cls["other"]} 条')
    for s, ts, t, tt in cls_samples['other']:
        print(f'       {s[:30]!r}({ts}) → {t[:30]!r}({tt})')

    if unknown_names:
        print('\n=== 因端点缺失而无法判定的高频名字 Top 8 ===')
        for nm, c in unknown_names.most_common(8):
            print(f'  {c:5d}  {nm[:56]!r}')

    out = {
        'layer': 'L7',
        'relation': 'CREATED_BY',
        'criterion': 'endpoint type constraint (reuses apply_created_by_fix.py)',
        'author_types': sorted(AUTHOR),
        'creatable_types': sorted(CREATABLE),
        'edges_total': len(edges),
        'edges_resolvable': resolvable,
        'edges_unknown_endpoint': unknown,
        'compliant': both_ok,
        'compliance_rate': round(both_ok / resolvable, 4) if resolvable else None,
        'source_violations': src_bad,
        'target_violations': tgt_bad,
        'violation_classes': dict(cls),
        'top_bad_pairs': [[list(k), v] for k, v in bad_pair.most_common(20)],
        'note': ('本层只覆盖 CREATED_BY：其余 14 类的端点类型约束见 '
                 'src/ontology.py 的 RELATION_ENDPOINT_CONSTRAINTS，'
                 '逐类结果由 tools/l7_relation_constraints_audit.py 产出'
                 '（其中 CREATED_BY 与本脚本对账一致）。'
                 '违规分三类：方向颠倒（可自动反转）、引文式端点（端点质量问题）、'
                 '其他（约束可能偏紧）。**分类已排除 Unknown 端点**（类型不可知，'
                 '不判），三类之和等于 source+target 违规数。本脚本只报告，不改数据。'),
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'\n报告已写出: {OUT}')


if __name__ == '__main__':
    main()
