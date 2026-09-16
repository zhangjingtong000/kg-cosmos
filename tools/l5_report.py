# -*- coding: utf-8 -*-
"""
T2.4 · 产出"被原文支持的关系"子图，并在其上重算 L1–L6 六层指标。

    python tools/l5_report.py            # 打印报告
    python tools/l5_report.py --json     # 额外写出 data/quality/l5-six-layer.json

设计要点：
  · 六层里 L5 是本次新建的；L1/L3/L4/L6 之前只有零散数字，这里在**同一口径**
    上一次性重算，避免此前"不同文档引用不同运行结果"的问题。
  · 口径分两个对照：全集（44172）与 L5 支持子集（entail_prob >= 0.67）。
  · 节点级与边级分开：L1 是节点级（实体名是否出现在自己的证据引文里），
    L3/L4/L5/L6 是边级。
  · L2（实体聚类层）本阶段未建，报告中显式标注为 N/A，不假装有数字。
"""
# 路径以本文件为锚点解析，不写死盘符——演示机不一定把仓库放在 D 盘。
from pathlib import Path as _Path
_HERE = _Path(__file__).resolve()
ROOT = _HERE.parent.parent          # tools/*.py 与 src/*.py 都在根目录下一层
import collections
import glob
import json
import os
import re
import sys

ROOT = str(ROOT)
sys.path.insert(0, os.path.join(ROOT, 'src'))
from ontology import RELATION_TYPES   # noqa: E402

Q = os.path.join(ROOT, 'data', 'quality')
GRAPHS = os.path.join(ROOT, 'data', 'corpus', 'graphs')
VERIFY = os.path.join(Q, 'relation-verify-all.json')

# 15 类本体。取值来自 src/ontology.py（唯一来源），不再就地抄一份——
# 抄一份的下场见 docs/ERROR_CASES.md 案例 12。
# 注意 APPLIES_TO 属于本体——早先漏掉它会把 3850 条本体边错算成越界。
ONTOLOGY = set(RELATION_TYPES)

_norm_re = re.compile(r'[^0-9a-z\u4e00-\u9fff]+')


def norm(s):
    return _norm_re.sub('', (s or '').lower())


def main():
    with open(VERIFY, encoding='utf-8') as f:
        v = json.load(f)
    thr = v['threshold']
    edges = v['edges']
    print('=' * 104)
    print('T2.4  L1–L6 六层指标（统一口径重算）')
    print('=' * 104)
    print(f'数据源 {os.path.basename(VERIFY)}   边 {len(edges)}   '
          f'L5 阈值 {thr}（ROC-AUC {v["calib_roc_auc"]}）')

    # ── 载入全部论文快照：节点证据 + 节点全集 ──
    node_ev = {}          # 归一化实体名 → 证据文本（取第一条）
    all_names = set()     # 节点全集（原始名，含反建）
    extracted_names = set()   # 只含独立抽取出来的节点
    backfilled_names = set()  # 只含从边反建出来的节点
    norm_names = set()
    files = sorted(glob.glob(os.path.join(GRAPHS, '*.json')))
    for p in files:
        with open(p, encoding='utf-8') as f:
            d = json.load(f)
        for n in d.get('nodes', []):
            nm = n.get('name')
            if not nm:
                continue
            all_names.add(nm)
            norm_names.add(norm(nm))
            # 区分"独立抽取出来的节点"与"从边反建出来的节点"。
            # 这个区分是 L3 多口径报告的基础：反建节点是**由边端点造出来的**，
            # 拿它们去满足"端点在节点集内"这个判据属于定义恒等，
            # 不能当成抽取质量的提升（对抗评审明确指出）。
            if n.get('_backfilled'):
                backfilled_names.add(nm)
            else:
                extracted_names.add(nm)
            if nm not in node_ev:
                ev = (n.get('evidence') or [{}])[0]
                node_ev[nm] = ev.get('text') or ''
    print(f'论文快照 {len(files)} 篇，节点名 {len(all_names)} 个（归一化后 {len(norm_names)}）')
    print(f'  其中独立抽取 {len(extracted_names)}，从边反建 {len(backfilled_names)}')

    # ── L1 实体存在层（节点级）──
    #
    # ⚠️ **A3（2026-09-16，评审的"致命 Q3"）：这一层的名字与判据不符，必须说清。**
    #
    # 判据是「**实体名的归一化形式，出现在 `evidence[0].text` 里**」。
    # 而 `pipeline.py` 写 `evidence` 时，`text` 就是**该实体被抽出来的那段 chunk 原文**
    # ⇒ 对**独立抽取**的节点，这是**定义恒等（自证）**：
    #     「实体名在它来源的文本里」—— 实体本来就是从那段文本抽出来的。
    #
    # ⇒ **所以它度量的不是"实体是否真的存在"，而是：**
    #     · **词形归一的一致性**（`Facts` vs 文本里的 `fact` ⇒ 失配）；
    #     · 以及少量的**幻觉**（名字在来源文本里根本找不到）。
    #
    # 实测（`tools/_probe/_probe_l1_selfproof.py`，只读）：
    #     全部节点      0.9507（22546/23716）
    #     ├─ 独立抽取   0.9391（18016/19184）  ← 未通过的 1168 个：词形失配或幻觉
    #     └─ 从边反建   0.9996（4530/4532）    ← **反而更高**
    #
    # **反建节点为什么更高**：它们的 `evidence` 是从**边**复制的，
    # 而边本身来自含端点名的文本 ⇒ **那是更彻底的自证**。
    #
    # ⇒ 因此本层**必须分两个口径报**（与 L3 剔除反建节点的做法一致）——
    #   把 0.9507 当成"实体存在性 95%"是错的，它是**两种口径混合后的加权平均**。
    l1_hit = l1_tot = 0
    l1_ex_hit = l1_ex_tot = 0
    l1_bf_hit = l1_bf_tot = 0
    l1_miss_samples = []
    for nm, txt in node_ev.items():
        l1_tot += 1
        ok = bool(norm(nm)) and norm(nm) in norm(txt)
        if ok:
            l1_hit += 1
        elif len(l1_miss_samples) < 5:
            l1_miss_samples.append(nm)
        # 分口径：独立抽取 vs 从边反建（**与 L3 一致**）
        if nm in backfilled_names:
            l1_bf_tot += 1
            l1_bf_hit += 1 if ok else 0
        else:
            l1_ex_tot += 1
            l1_ex_hit += 1 if ok else 0
    l1 = l1_hit / l1_tot if l1_tot else 0.0
    l1_extracted = l1_ex_hit / l1_ex_tot if l1_ex_tot else 0.0
    l1_backfilled = l1_bf_hit / l1_bf_tot if l1_bf_tot else 0.0

    # ── 边级四层 ──
    def is_supported(e):
        """该边是否被 L5 接受。

        **优先用 `pass` 字段**，而不是现场比 `entail_prob >= thr`：
        `pass` 是 relation_verifier.py 用**未舍入**的分数判定的，而
        `entail_prob` 落盘时只保留 4 位小数——边界值会被抹平
        （实际 0.66995 存成 0.67，现场比较就会把它算成通过）。
        实测全集 43867 条里有 3 条因此不一致，`tools/l5_calibrate.py`
        的「[核实] pass 字段与 entail_prob >= thr」一节就是专门测这个的。
        没有 `pass` 字段时（按类型单独重跑产出的中间文件）才退回现场比较。
        """
        if 'pass' in e:
            return e['pass'] in (True, 'True')
        return e.get('entail_prob', 0) >= thr

    def layer_stats(rows, label):
        n = len(rows)
        if not n:
            return None
        l3 = sum(1 for e in rows
                 if e['source'] in all_names and e['target'] in all_names)
        # L3 的**抽取口径**：两端都必须是"独立抽取出来的节点"，
        # 不含从边反建的那些。反建节点是由边端点造出来的，
        # 拿它们去满足 L3 属于定义恒等，不能当作质量提升。
        l3_extracted = sum(1 for e in rows
                           if e['source'] in extracted_names
                           and e['target'] in extracted_names)
        l4 = sum(1 for e in rows if e.get('type') in ONTOLOGY)
        l5 = sum(1 for e in rows if is_supported(e))
        l6 = sum(1 for e in rows if (e.get('evidence') or '').strip()
                 and e.get('page') is not None)
        both = sum(1 for e in rows if e.get('hit') == 'both')
        return {'label': label, 'n': n, 'l3': l3, 'l3_extracted': l3_extracted,
                'l4': l4, 'l5': l5, 'l6': l6, 'both': both}

    supported = [e for e in edges if is_supported(e)]
    stats_all = layer_stats(edges, '全集')
    stats_sup = layer_stats(supported, 'L5 支持子集')

    print()
    print('-' * 104)
    print('六层指标')
    print('-' * 104)
    # A3：**打印必须分口径** —— 一个混在一起的 95.1% 会被读成"实体存在性 95%"，
    # 而它对独立抽取的节点是定义恒等（自证）。
    print(f'L1 词形归一失配层（节点级）   {l1_hit}/{l1_tot} = {l1:.1%}'
          f'   ⚠️ **不是"实体存在性"**（判据对独立抽取的节点是自证）')
    print(f'   ├─ 独立抽取的节点            {l1_ex_hit}/{l1_ex_tot} = {l1_extracted:.1%}'
          f'   ← 未通过的 = 词形归一失配或幻觉')
    print(f'   └─ 从边反建的节点            {l1_bf_hit}/{l1_bf_tot} = {l1_backfilled:.1%}'
          f'   ← evidence 复制自边 ⇒ **更彻底的自证**，比上面还高')
    print(f'L2 实体聚类层                 归并 585 组（见 tools/l2_report.py）')
    for st in (stats_all, stats_sup):
        if not st:
            continue
        n = st['n']
        # L3 必须**多口径并列**，禁止只报闭合口径。
        # 闭合口径（含反建节点）是定义恒等：反建节点就是从边端点造出来的，
        # 拿它去满足"端点在节点集内"必然命中。只报这个数等于粉饰。
        print(f'L3 关系端点层  [{st["label"]}]')
        print(f'    抽取口径（只算独立抽取的节点） {st["l3_extracted"]}/{n} = '
              f'{st["l3_extracted"]/n:.1%}')
        print(f'    闭合口径（含从边反建的节点）   {st["l3"]}/{n} = {st["l3"]/n:.1%}'
              f'   ← 含人工构造，不可单独引用')
        if st is stats_all:
            print(f'    反建贡献：新增 {len(backfilled_names)} 个节点，'
                  f'把 {st["l3"] - st["l3_extracted"]} 条边的端点补齐')
        print(f'L4 关系谓词层  [{st["label"]}]   {st["l4"]}/{n} = {st["l4"]/n:.1%}'
              f'   谓词在本体 15 类内')
        print(f'L5 三元组验证  [{st["label"]}]   {st["l5"]}/{n} = {st["l5"]/n:.1%}'
              f'   pass 字段（等价 entail_prob >= {thr}，边界差 3 条见注释）')
        print(f'L6 证据层      [{st["label"]}]   {st["l6"]}/{n} = {st["l6"]/n:.1%}'
              f'   有页面级引文')
        print(f'   端点双命中（hit=both）        {st["both"]}/{n} = {st["both"]/n:.1%}')
        print()

    # ── 对照：过滤前后的边数与平均分 ──
    print('-' * 104)
    print('过滤效果对照')
    print('-' * 104)
    # 丢弃 = 保留的补集。**不能**写成 `entail_prob < thr`：
    # 那 3 条边界边（分数存成 0.67、pass=false）会既不进保留也不进丢弃，
    # 使"保留 + 丢弃 ≠ 全集"。统一走 is_supported 才能保证两者互补。
    dropped = [e for e in edges if not is_supported(e)]
    for label, rows in (('保留（L5 支持）', supported), ('丢弃（L5 不支持）', dropped)):
        if not rows:
            continue
        dots = collections.Counter(e.get('type') for e in rows)
        print(f'{label}: {len(rows)} 条   端点双命中 {sum(1 for e in rows if e.get("hit")=="both")/len(rows):.1%}')
        print('   主要类型: ' + ', '.join(f'{t} {c}' for t, c in dots.most_common(5)))

    if '--json' in sys.argv:
        # L2 由 tools/l2_report.py --write 填。本脚本**不能把它覆盖成 None**：
        # 两个脚本写同一个文件的同一个字段，谁后跑谁说了算——
        # 实测在 l2_report 之后重跑一次 l5_report，L2 就变回 null，
        # 分析面板随即显示 "? 组"。这正是 ERROR_CASES 案例 12 的同一类问题。
        prev_l2 = None
        _six_path = os.path.join(Q, 'l5-six-layer.json')
        if os.path.exists(_six_path):
            try:
                with open(_six_path, encoding='utf-8') as _f:
                    prev_l2 = json.load(_f).get('L2_entity_clustering')
            except Exception:
                prev_l2 = None
        out = {
            'source': os.path.basename(VERIFY),
            'threshold': thr,
            'edges_total': len(edges),
            # A3（2026-09-16）：**分两个口径**报，并说明这一层到底在量什么。
            # `rate` 保持原义（全口径）—— **不改既有调用方**
            # （`metric_provenance.py` 按 `rate` 取值）；新增的两个口径供文档与面板引用。
            'L1_entity_presence': {
                'hit': l1_hit, 'total': l1_tot, 'rate': l1,
                'rate_extracted': l1_extracted,
                'rate_backfilled': l1_backfilled,
                'extracted': {'hit': l1_ex_hit, 'total': l1_ex_tot},
                'backfilled': {'hit': l1_bf_hit, 'total': l1_bf_tot},
                'what_it_measures':
                    '**不是"实体存在性"** —— 判据是「实体名的归一化形式出现在 '
                    'evidence[0].text 里」，而那段 text 就是**该实体被抽出来的 chunk 原文** '
                    '⇒ 对独立抽取的节点是**定义恒等（自证）**。'
                    '它实际度量的是**词形归一的一致性 + 少量幻觉**。'
                    '**必须分口径看**：反建节点的 evidence 复制自边、而边来自含端点名的文本 '
                    '⇒ 通过率反而更高（0.9996），两个口径混在一起的加权平均会误导。'
                    '（评审的"致命 Q3"；量化见 tools/_probe/_probe_l1_selfproof.py）',
            },
            'L2_entity_clustering': prev_l2,
            # L3 落盘也必须是多口径。`closed` 含从边反建的节点，属人工构造，
            # 单独引用会被读成抽取质量；`extracted` 才是"只算独立抽取节点"的口径。
            'L3_endpoints': {
                'all': stats_all['l3'] / stats_all['n'],
                'supported': stats_sup['l3'] / stats_sup['n'],
            },
            'L3_endpoints_extracted_caliber': {
                'all': stats_all['l3_extracted'] / stats_all['n'],
                'supported': stats_sup['l3_extracted'] / stats_sup['n'],
                'backfilled_nodes': len(backfilled_names),
                'note': 'L3_endpoints 是闭合口径（含从边反建的节点，属人工构造）；'
                        '本字段是抽取口径，只统计独立抽取出来的节点。'
                        '两者必须并列引用。',
            },
            'L4_predicate': {'all': stats_all['l4'] / stats_all['n'],
                             'supported': stats_sup['l4'] / stats_sup['n']},
            'L5_triplet': {'all': stats_all['l5'] / stats_all['n'],
                           'supported': 1.0},
            'L6_evidence': {'all': stats_all['l6'] / stats_all['n'],
                            'supported': stats_sup['l6'] / stats_sup['n']},
            'hit_both': {'all': stats_all['both'] / stats_all['n'],
                         'supported': stats_sup['both'] / stats_sup['n']},
            'edges_supported': len(supported),
            'edges_dropped': len(dropped),
        }
        p = os.path.join(Q, 'l5-six-layer.json')
        with open(p, 'w', encoding='utf-8') as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f'\n已写出 {p}')


if __name__ == '__main__':
    main()
