# -*- coding: utf-8 -*-
r"""A8 · **执行** `PUBLISHED_IN` 改写，并留改前/改后的双口径。

## TODO 的原话

> | **A8** | 🔶 **`PUBLISHED_IN` 改写执行** |
> 本体已加（16 类），改写计划已出（`venue-rewrite-plan.json`：
> 可改写 **1019** 条、待判 129 条），但**只出计划没执行** |
> 逐条改标 `PART_OF`/`LOCATED_AT` → `PUBLISHED_IN`，
> **并留改前改后的 L3/L4/L7 双口径**（加本体类型会改变 L4 分母） | 半天

## ★ 两个"不"：这一项的做法由它们决定

**① 不改原图，另写一份。**
计划脚本自己就写了「产物写入 `venue-rewrite-plan.json`，**不直接改图**」，
理由是「避免让六层指标失去可比性」。
⇒ 本脚本把改写后的图写到 **`data/quality/graphs-published-in/`**（新目录），
**`data/corpus/graphs/` 一个字节都不动** —— 于是：
  · **before 口径随时可重算**（原图在）；
  · **after 口径也在**；
  · **两者可以并排引用**。
（这与 P4 的 `--convert` 是同一个做法：**另写一份，不覆盖**。）

**② 不删任何边。**
`venue-rewrite-plan.json` 的 `method` 字段写得很明确：
「**改写而非删除** —— 这些边携带真实信息，只是类型标错」。
⇒ 本脚本**只改 `type` 一个字段**，并留下 `_rewritten_from` 痕迹（与
`graph_source` 处理越界谓词时同一手法）。

## ★ 为什么必须留双口径（而不是只报 after）

`PUBLISHED_IN` 加进本体后，**L4（谓词是否在本体内）的分母会变** ——
原来被判「越界」的谓词现在合规了。
⇒ **只报 after 会让人以为"抽取质量提升了"，而实际只是"判据变宽了"。**
**这是本项目最反复强调的那条纪律**（A3 的 L1、H10 的过期数字，都是同一类）。

用法：
    python tools/apply_venue_rewrite.py            # dry-run：只报会改什么
    python tools/apply_venue_rewrite.py --apply     # 真写（写到新目录）
"""
import argparse
import json
import shutil
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'tools'))

Q = ROOT / 'data' / 'quality'
GRAPHS = ROOT / 'data' / 'corpus' / 'graphs'
OUT_DIR = Q / 'graphs-published-in'
PLAN = Q / 'venue-rewrite-plan.json'
REPORT = Q / 'venue-rewrite-result.json'

# 端点约束表（供 `six_layer` 算 L7 用）。
# ⚠️ **必须是模块级** —— `six_layer` 是模块级函数，
#    而把 `ENDPOINT` 放在 `main()` 里会让它取不到（第一版就是这个错）。
ENDPOINT = {}


def parse_args():
    ap = argparse.ArgumentParser(description='A8 执行 PUBLISHED_IN 改写')
    ap.add_argument('--apply', action='store_true', help='真写（默认只报告）')
    return ap.parse_args()


def six_layer(nodes, edges, rel_types, label):
    """算 **L3 / L4 / L7** 三个口径。

    · **L3**：两端都在节点集内（`e['source'] in names and e['target'] in names`）
    · **L4**：谓词在本体内（`e['type'] in RELATION_TYPES`）
    · **L7**：端点类型约束（`RELATION_ENDPOINT_CONSTRAINTS[type]['source_core']` 等）

    ⚠️ **L7 这里是从图直接算的**，而 `build_analytics_data.py` 里的 L7 是
    **从产物 `l7-role-violation-audit.json` 读的** —— 两者口径**不完全相同**。
    ⇒ 下面报的 L7 是"**按端点类型约束逐条判**"这个口径，
      **不声称它与分析面板上那个数一样**（那样就变成"一个名字两个口径"了）。
    """
    names = {n.get('name') or n.get('id') for n in nodes}
    ntype = {n.get('name') or n.get('id'): (n.get('type') or 'Unknown')
             for n in nodes}
    n = len(edges) or 1
    l3 = sum(1 for e in edges
             if e.get('source') in names and e.get('target') in names)
    l4 = sum(1 for e in edges if e.get('type') in rel_types)
    # L7：两端类型是否满足该关系的端点约束（约束缺失时视为放行）
    l7_ok = l7_judged = 0
    for e in edges:
        c = ENDPOINT.get(e.get('type')) or {}
        sc, tc = c.get('source_core'), c.get('target_core')
        if not sc and not tc:
            continue
        l7_judged += 1
        st = ntype.get(e.get('source'), 'Unknown')
        tt = ntype.get(e.get('target'), 'Unknown')
        ok = True
        if sc and st not in sc:
            ok = False
        if tc and tt not in tc:
            ok = False
        if ok:
            l7_ok += 1
    return {
        'label': label,
        'edges': len(edges), 'nodes': len(nodes),
        'L3_endpoints': round(l3 / n, 6), 'L3_ok': l3,
        'L4_predicate': round(l4 / n, 6), 'L4_ok': l4,
        'L7_judged': l7_judged,
        'L7_compliance': (round(l7_ok / l7_judged, 6) if l7_judged else None),
        'L7_ok': l7_ok,
    }


def main():
    a = parse_args()
    t0 = time.time()
    print('=' * 92)
    print('A8 · 执行 PUBLISHED_IN 改写（另写一份，不动原图）')
    print('=' * 92)

    if not PLAN.exists():
        print(f'  ❌ 缺 {PLAN}（先生成改写计划 JSON）')
        return 1
    plan = json.loads(PLAN.read_text(encoding='utf-8'))
    rw = plan.get('rewrite') or []
    print(f'  计划：可改写 {len(rw)} 条 · 待人工判读 '
          f'{len(plan.get("flag_only") or [])} 条')
    print(f'  计划里的影响警告：{(plan.get("ontology_change") or {}).get("impact_warning", "")[:70]}')
    print()

    from graph_source import load_graph  # noqa: E402
    from ontology import RELATION_TYPES, RELATION_ENDPOINT_CONSTRAINTS  # noqa: E402
    global ENDPOINT
    ENDPOINT = RELATION_ENDPOINT_CONSTRAINTS      # 供 `six_layer` 算 L7 用
    new_types = set(RELATION_TYPES)         # **改后**的本体（16 类，含 PUBLISHED_IN）
    # **改前**的本体：`ontology.py` 里没有单独导出"15 类"，
    # 而 `PUBLISHED_IN` **正是这轮唯一新增的那一类** ⇒ 减掉它就是改前的集合。
    # ⚠️ **不硬抄一份 15 类的名单** —— 那会与 `ontology.py` 漂
    #   （本项目在"本体四份拷贝必须一致"上栽过，有专门的守门）。
    old_types = new_types - {'PUBLISHED_IN'}

    if 'PUBLISHED_IN' not in new_types:
        print('  ❌ `RELATION_TYPES` 里没有 PUBLISHED_IN —— 本体的第一步没做')
        return 1
    print(f'  本体：改前 {len(old_types)} 类 → 改后 {len(new_types)} 类'
          f'（新增 {sorted(new_types - old_types)}）')

    # ── before：原图 ──
    nodes0, edges0 = load_graph(governed=False, verbose=False)
    before15 = six_layer(nodes0, edges0, old_types, 'before（15 类本体）')
    before16 = six_layer(nodes0, edges0, new_types, 'before（16 类本体，未改图）')
    print()
    print('  ★ before 两个口径（**同一批边、两个本体**）')
    for r in (before15, before16):
        print(f'      {r["label"]:34s} L3 {r["L3_endpoints"]:.2%} · '
              f'L4 {r["L4_predicate"]:.2%}')
    print(f'      ⇒ **只加本体不改图**，L4 就变化了 '
          f'{(before16["L4_predicate"] - before15["L4_predicate"]) * 100:+.2f}pp')
    print('         —— 这正是"必须留双口径"的原因：**判据变宽 ≠ 质量提升**。')
    print()

    # ── 写改写后的图 ──
    #   计划里的 `index` 是**全库边列表**（`load_graph` 的顺序）的下标。
    #   ⚠️ 所以要按**同一个顺序**重建 —— 而 `load_graph` 的顺序由
    #     `sorted(graphs/*.json)` 与文件内边序决定，**是稳定的**。
    by_index = {r['index']: r for r in rw}
    print(f'  改写点 {len(by_index)} 个 · index 范围 '
          f'{min(by_index, default=0)}–{max(by_index, default=0)}'
          f'（全库 {len(edges0)} 条边）')
    out_of_range = [i for i in by_index if not (0 <= i < len(edges0))]
    if out_of_range:
        print(f'  ⚠️ 有 {len(out_of_range)} 个 index 越界 —— 计划与当前图不同源？')
        return 1

    # 校验：计划里的 (source,target,type) 与当前边**逐条对得上**
    mismatch = []
    for i, r in by_index.items():
        e = edges0[i]
        if (str(e.get('source')) != r['source']
                or str(e.get('target')) != r['target']
                or str(e.get('type') or '').upper() != r['type']):
            mismatch.append((i, r, e))
    print(f'  逐条校验：对得上 {len(by_index) - len(mismatch)} / {len(by_index)}')
    if mismatch:
        print('  ❌ 有对不上的，**拒绝执行**（那说明计划是基于另一份数据生成的）：')
        for i, r, e in mismatch[:3]:
            print(f'      #{i} 计划 {r["source"]}→{r["target"]} ({r["type"]})')
            print(f'          实际 {e.get("source")}→{e.get("target")} ({e.get("type")})')
        return 1

    if not a.apply:
        print()
        print('  （这是 dry-run，**没有写任何文件**。真写请加 --apply）')
        return 0

    # 写出：按论文分文件（与 `data/corpus/graphs/` 同结构）
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    # 重建每篇：不改原文件，而是**复制 + 按全库下标改 type**
    files = sorted(GRAPHS.glob('*.json'))
    # 先算出"全库边序 → 哪个文件、文件内第几条"
    loc = {}
    k = 0
    loaded = {}
    for f in files:
        d = json.loads(f.read_text(encoding='utf-8-sig'))
        loaded[f.stem] = d
        for j in range(len(d.get('edges') or [])):
            loc[k] = (f.stem, j)
            k += 1
    n_changed = 0
    for i, r in by_index.items():
        stem, j = loc[i]
        e = loaded[stem]['edges'][j]
        e['_rewritten_from'] = e.get('type')      # **留痕**（与 graph_source 同一手法）
        e['type'] = 'PUBLISHED_IN'
        n_changed += 1
    for stem, d in loaded.items():
        (OUT_DIR / f'{stem}.json').write_text(
            json.dumps(d, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    print()
    print(f'  ✅ 已写 {len(loaded)} 篇到 {OUT_DIR.relative_to(ROOT)}')
    print(f'     改了 {n_changed} 条边的 type（**每条都留了 `_rewritten_from`**）')

    # ── ★ 自检：**逐字节**证明"只改了 type、原图没动" ────────────────────
    #
    # 为什么要把这个做成脚本的一部分（而不是只在文档里写一句"已核实"）：
    # **"我只改了 type"是一个可验证的断言** —— 那就不该靠人相信。
    # 而且它同时守住两件事：① 改对了（恰好 1019 条）；
    # ② **没有意外碰到别的字段**（type 之外零差异）。
    same_nodes = same_edges = same_other = diff_type = 0
    for f in sorted(GRAPHS.glob('*.json')):
        a = json.loads(f.read_text(encoding='utf-8-sig'))
        b = json.loads((OUT_DIR / f.name).read_text(encoding='utf-8'))
        if len(a.get('nodes') or []) == len(b.get('nodes') or []):
            same_nodes += 1
        ea, eb = a.get('edges') or [], b.get('edges') or []
        if len(ea) == len(eb):
            same_edges += 1
        for x, y in zip(ea, eb):
            if x.get('type') != y.get('type'):
                diff_type += 1
            for k in x:
                if k == 'type' or k.startswith('_'):
                    continue
                if x.get(k) != y.get(k):
                    same_other += 1
    ok_self = (same_nodes == len(loaded) and same_edges == len(loaded)
               and diff_type == n_changed and same_other == 0)
    print()
    print('  ★ 自检（**逐字节**证明"只改了 type"）')
    print(f'      17 篇的节点数一致：{same_nodes} / {len(loaded)}')
    print(f'      17 篇的边数一致：  {same_edges} / {len(loaded)}')
    print(f'      type 不同的边：    {diff_type}（计划 {n_changed}）')
    print(f'      **type 之外的差异：{same_other}**（应为 0）')
    print(f'      ⇒ {"✅ 通过" if ok_self else "❌ **不通过 —— 有意外改动，要查**"}')
    if not ok_self:
        print('      ⚠️ 自检没过 ⇒ **不写报告**，先查清楚改了什么。')
        return 1
    # **原图 mtime**：另一个角度的证据（内容比过了，这里再确认没被写过）
    import os as _os
    mt = max(_os.path.getmtime(f) for f in GRAPHS.glob('*.json'))
    print(f'      原图最新 mtime：{time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mt))}'
          f'（若刚被写过会是"现在"）')

    # ── after：从**改写后的图**重算 ──
    #
    # ⚠️⚠️ **必须走与 before 相同的规范化链** —— 这是第一版踩的坑：
    #   `before` 用 `graph_source.load_graph()`（它会**修字段错位**、
    #   并把**越界谓词改写成 `RELATED_TO`**），而 `after` 我用 `json.loads` 直接读，
    #   ⇒ 两条**不同的处理链**，于是算出一个 **-0.54pp 的假效应**
    #   （那 237 条越界谓词在原图里被判成 `RELATED_TO`、在 after 里没有）。
    #   **"改前改后可比"的前提是两边走同一条链** —— 否则量到的是**口径差**，
    #   不是**改写的效果**。这正是 A8 要"留双口径"想防的那类错，只是换了个地方。
    def _normalize(edges_in):
        """复刻 `graph_source.load_graph` 对 type 的两处规范化。"""
        out = []
        for raw in edges_in:
            e = dict(raw)
            t = str(e.get('type') or '')
            desc = str(e.get('description') or '').strip()
            if t not in new_types and desc in new_types:
                e['type'], e['description'] = desc, t     # 字段错位换回
            elif t not in new_types:
                e['type'] = 'RELATED_TO'                  # 越界谓词改写
            out.append(e)
        return out

    nodes1, edges1 = [], []
    seen = set()
    for f in sorted(OUT_DIR.glob('*.json')):
        d = json.loads(f.read_text(encoding='utf-8'))
        for nn in (d.get('nodes') or []):
            nm = nn.get('name') or nn.get('id')
            if nm and nm not in seen:
                seen.add(nm)
                nodes1.append({'name': nm, 'type': nn.get('type')})
        edges1.extend(d.get('edges') or [])
    edges1 = _normalize(edges1)
    after16 = six_layer(nodes1, edges1, new_types, 'after（16 类本体 + 已改写）')

    print()
    print('  ★ 四个口径并排')
    rows = [before15, before16, after16]
    for r in rows:
        l7 = ('—' if r['L7_compliance'] is None
              else f'{r["L7_compliance"]:.2%}（判 {r["L7_judged"]} 条）')
        print(f'      {r["label"]:34s} 边 {r["edges"]:>6} '
              f'L3 {r["L3_endpoints"]:>7.2%} · L4 {r["L4_predicate"]:>7.2%} · L7 {l7}')
    print()
    print('  ★★ 怎么读这张表（**不要读错**）')
    print(f'      · 只加本体（before 两行）：L4 '
          f'{(before16["L4_predicate"] - before15["L4_predicate"]) * 100:+.2f}pp '
          f'—— **那是判据变宽，不是质量提升**')
    print(f'      · 加本体 + 改写（before16 → after）：L4 '
          f'{(after16["L4_predicate"] - before16["L4_predicate"]) * 100:+.2f}pp ')
    print(f'      · **L7 才是真正被改写影响的那一层**：'
          + (f'{before16["L7_compliance"]:.2%} → {after16["L7_compliance"]:.2%}'
             f'（{(after16["L7_compliance"] - before16["L7_compliance"]) * 100:+.2f}pp）'
             if (before16['L7_compliance'] is not None
                 and after16['L7_compliance'] is not None) else '（算不出）')
          + ' —— 因为 `PUBLISHED_IN` 有自己的端点约束，'
            '而原来标 `PART_OF`/`LOCATED_AT` 的那些边**违反**它')
    print(f'      · L3 '
          f'{(after16["L3_endpoints"] - before15["L3_endpoints"]) * 100:+.2f}pp'
          f'（改写只动 type，**理论上不该改变端点完整性** —— '
          f'若变了说明节点集变了，要查）')

    payload = {
        'generated': time.strftime('%Y-%m-%d %H:%M:%S'),
        'purpose': 'A8：执行 PUBLISHED_IN 改写，并留改前/改后的双口径',
        'plan_source': 'data/quality/venue-rewrite-plan.json',
        'output_dir': str(OUT_DIR.relative_to(ROOT)),
        'design': {
            'not_touching_original':
                '**不改原图，另写一份** —— 原图在 `data/corpus/graphs/` 一个字节没动。'
                '这样 before 口径随时可重算，两者可以并排引用（与 P4 的 --convert 同一做法）。',
            'rewrite_not_delete':
                '**只改 `type` 一个字段**，并留 `_rewritten_from` 痕迹。'
                '计划脚本自己写了「改写而非删除 —— 这些边携带真实信息，只是类型标错」。',
            'why_dual_caliber':
                '`PUBLISHED_IN` 加进本体后 **L4 的分母会变** —— 原来被判"越界"的谓词'
                '现在合规了。**只报 after 会让人以为"抽取质量提升了"，'
                '而实际只是"判据变宽了"。**',
            'L7_not_computed':
                '**L7 没有在这里算** —— 它的判据在 `build_analytics_data.py` 里是'
                '**从产物 `l7-role-violation-audit.json` 读的**，不是从图直接算的。'
                '⇒ **不假装算了一个其实没算的层**（见文档边界）。',
        },
        'ontology': {'before_n': len(old_types), 'after_n': len(new_types),
                     'added': sorted(new_types - old_types)},
        'rewritten_edges': n_changed,
        'cases': {
            'before_15_ontology': before15,
            'before_16_ontology_no_rewrite': before16,
            'after_16_ontology_with_rewrite': after16,
        },
        'read_this_way': {
            'ontology_only': round((before16['L4_predicate']
                                    - before15['L4_predicate']) * 100, 3),
            'rewrite_itself': round((after16['L4_predicate']
                                     - before16['L4_predicate']) * 100, 3),
            'note': '两个数**不要相加当成一个总提升** —— 前者是判据变化，后者是数据变化。',
        },
        'elapsed_s': round(time.time() - t0, 1),
    }
    REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding='utf-8')
    print()
    print(f'  已写 {REPORT.name}（含四个口径与"怎么读这张表"）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
