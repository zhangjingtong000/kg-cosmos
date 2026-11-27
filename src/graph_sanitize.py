# -*- coding: utf-8 -*-
r"""A4：把 `entity_filter` / `entity_absorb` / `direction_witness` **接到图这一层**。

## 为什么需要单独一个模块（A4 的原话）

> 三个模块**都只在一次性探针里被引用**，没进 `pipeline.py`。不接就没有实际收益。
> 接之前**先设计"被过滤实体所连的边怎么办"**（悬空边）——这一层**现在没有设计**。

## 先说清一件事：三个模块**都不改结构**，所以问题不在它们里面

读过实现之后：

    entity_filter.filter_entities(nodes)        → (kept, dropped)，**不修改入参**
    entity_absorb.apply_absorptions(nodes, plan) → **不删节点**，只加 `absorbed_into`
    direction_witness.witness(desc, s, t, type)  → 只返回判定

⇒ **三个模块都是"只报不改"**，"悬空边"不会在它们内部产生。
⇒ 它产生在**接入时**：一旦你把节点集换成 `kept`，**原来那些边就有一端指向不存在的节点**。
⇒ 所以"设计"这一步就是本模块的职责：**把节点的取舍与边的取舍放在同一个地方决定，并记账。**

## 三个动作各不相同，理由也不一样

| 模块 | 对节点的动作 | **对边的动作** | 为什么是这个动作 |
|---|---|---|---|
| `entity_filter` | 丢弃 | **级联删除 + 记账** | 被丢的实体（参考文献作者/纯数字/泛指名词）本身就是噪声，**它连的边也是噪声**。**不能"把端点提升到父概念"** —— 那会造出原文没有的关系。 |
| `entity_absorb` | 标 `absorbed_into` | **重指向 + 去重** | `A` 被吸收进 `B` ⇒ `A → X` 应变成 `B → X`。**这不是删边**。而重指之后 `B → X` 可能与已有边重复 ⇒ **必须去重**（这一步最容易漏）。 |
| `direction_witness` | 不动 | **只在高置信时翻转** | 见证通道有"判不出"的返回值。**判不出就不动** —— 改错方向比漏改一个更糟（L5 的 NLI 只能拦住 23.9%，任何"顺手也改了"都会放大噪声）。 |

## 记账（这是本模块存在的一半理由）

`sanitize()` 返回一份 `report`，**每一个被丢掉或改动的东西都有数字**：

    dropped_entities          被过滤掉的实体数（按原因分类）
    dropped_edges_dangling    因为端点被过滤而**级联删除**的边数
    absorbed_pairs            被吸收的实体对数
    rewired_edges             因为吸收而**重指向**的边数
    merged_after_rewire       重指后**与已有边重复**、因而被合并掉的边数
    direction_flipped         被见证通道**翻转**的边数
    direction_uncertain       判不出方向、**保持不动**的边数

**为什么要记得这么细**：这个模块一开就会改动图，
而"改了图却不知道改了多少"正是本项目反复栽过的坑
（`backfill_missing_endpoints.py` 把 L1 从 92.8% 抬到 94.4% 却没人一开始注意到副作用）。

## 默认关闭

`pipeline.py` 里通过环境变量接入，**默认 0**：

    KG_SANITIZE=0   不净化（**默认** —— 保证既有绿灯与所有历史产物口径不变）
    KG_SANITIZE=1   只做吸收（最保守，金标实测 +0.0051）
    KG_SANITIZE=2   吸收 + 过滤（会删实体与边）
    KG_SANITIZE=3   全开（再加方向见证）

**分级而不是布尔**：三件事的风险差很多（吸收是"只加字段"、过滤是"真删"），
用一档开关会把它们绑死，**没法只开风险最低的那个**。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import entity_absorb as EA          # noqa: E402
import entity_filter as EF          # noqa: E402
import direction_witness as DW      # noqa: E402


def level() -> int:
    """读环境变量（**每次调用都读**，不在模块级缓存）。

    ⚠️ 这条是有教训的：`app.py` 里 `EDIT_PLAN_DIR` 曾在**模块级**读环境变量，
    于是 `test_edit_plan_api.py` 单跑 6 个全过、进全量跑就挂 ——
    因为 import 只发生一次，**测试改了环境变量也不生效**。
    ⇒ 凡是"由环境变量控制的开关"，都要在**调用点**读。
    """
    try:
        return max(0, min(3, int(os.environ.get('KG_SANITIZE', '0').strip() or 0)))
    except Exception:
        return 0


def _key(e):
    return (str(e.get('source')), str(e.get('target')), str(e.get('type')))


def sanitize(nodes, edges, lvl=None, *, keep_dropped=False):
    """对**一整张图**做净化，返回 (nodes, edges, report)。

    `nodes` / `edges` 是抽取器的输出格式（`{"name"/"id", ...}` 与
    `{"source","target","type",...}`）。**不修改入参**（内部先浅拷贝）。

    `lvl=None` 时读 `KG_SANITIZE`。`lvl=0` 时**原样返回**（连拷贝都不做，
    保证默认路径的开销与行为都完全不变）。

    `keep_dropped=True` 时被过滤的实体仍留在 nodes 里（标 `drop_reason`），
    **但它们的边照删** —— 用于"想审计被丢了什么"的场景。
    """
    if lvl is None:
        lvl = level()
    if lvl <= 0:
        return nodes, edges, {'level': 0, 'enabled': False}

    nodes = [dict(n) for n in (nodes or [])]
    edges = [dict(e) for e in (edges or [])]
    rep = {'level': lvl, 'enabled': True,
           'dropped_entities': 0, 'dropped_by_reason': {},
           'dropped_edges_dangling': 0,
           'absorbed_pairs': 0, 'rewired_edges': 0, 'merged_after_rewire': 0,
           'direction_flipped': 0, 'direction_uncertain': 0,
           'nodes_before': len(nodes), 'edges_before': len(edges)}

    # ── ① 吸收（最保守，lvl>=1）──────────────────────────────────────
    #   **先做吸收**，因为吸收是"改名"；如果先做过滤，
    #   被吸收掉的短名可能已经被过滤删了，吸收就落空。
    alias = {}
    if lvl >= 1:
        plan = EA.find_absorptions(nodes)
        if plan:
            EA.apply_absorptions(nodes, plan)          # 只加 absorbed_into，不删
            rep['absorbed_pairs'] = len(plan)
            for short, long_, _why in plan:
                alias[str(short)] = str(long_)

    # ── ② 重指向（吸收的直接后果）────────────────────────────────────
    if alias:
        seen, out = set(), []
        for e in edges:
            s, t = str(e.get('source')), str(e.get('target'))
            ns, nt = alias.get(s, s), alias.get(t, t)
            if ns != s or nt != t:
                rep['rewired_edges'] += 1
                e['source'], e['target'] = ns, nt
                # **重指后可能与已有边重复** —— 去重（保留证据更全的那条）
                k = _key(e)
                if k in seen:
                    rep['merged_after_rewire'] += 1
                    continue
                seen.add(k)
            else:
                k = _key(e)
                if k in seen:
                    rep['merged_after_rewire'] += 1
                    continue
                seen.add(k)
            out.append(e)
        edges = out

    # ── ③ 过滤（真删节点，lvl>=2）───────────────────────────────────
    #
    # ★★★ **这里改过一次设计**（2026-09-16，量完全库之后）★★★
    #
    # **第一版**：实体被过滤 ⇒ 它连的边**一律级联硬删**。
    # **量完全库发现这一刀太大**（一次性只读探针）：
    #
    #     过滤 23 个实体  ⇒  **删掉 560 条边**（平均 1 个实体 24 条）
    #     过滤 15 个实体  ⇒  **删掉 1072 条边**
    #     全库：过滤 667 个实体 ⇒ 删掉约 9000 条边，而全库约 45000 条 ⇒ **20%**
    #     ⚠️ 「约 45000」是**粗口径**，不对应任何一个当前产物：
    #        · 17 篇 arXiv 快照 = **43867** 条（**历史读数**；见下）
    #        · 现 `data/corpus/graphs/` 21 篇 = **45708** 条（L3–L6 分母口径）
    #        · 概览层导出上限 = 30000（`nebula.meta.edges`）
    #        结论（~20%）不变，但引用时不要把这个数当成某一口径的精确值。
    #     ⚠️ **2026-09-26 更正**：上面第二行原来标的是「`bipartite.totals.edges`」，
    #        **该锚点已失效** —— `web/kg-data/bipartite.json` 当天重跑后
    #        `totals.edges` 已是 **45708**（与图目录一致，`rel_edges` 7894）。
    #        ⇒ **43867 现在不等于任何产物字段**，引用时按"历史读数"对待。
    #
    # **根因**：被过滤的是"参考文献作者"（`Smith, J.` 这种形态），
    # 而它们在**图里度数极高** —— 连着一堆论文、机构、合作者。
    #
    # **新设计（默认）**：**软删除** —— 边**留着**，但打标
    #     `tgt_filtered` / `src_filtered` = 被过滤端点的原始名字
    # ⇒ 信息不丢，下游（前端筛选、统计、六层评测）可以**按需**排除。
    #   这与 `entity_absorb` 的 `absorbed_into` **同一哲学**：
    #   **模块只"报"，不擅自"改"**；真要改，由调用方显式要求。
    #
    # **硬删改成显式开关** `KG_SANITIZE_DROP_EDGES=1`（默认 0）。
    if lvl >= 2:
        kept, dropped = EF.filter_entities(nodes, strict_generic=True)
        rep['dropped_entities'] = len(dropped)
        rep['dropped_by_reason'] = EF.summary(dropped)
        alive = {str(n.get('name') or n.get('id')) for n in kept}
        hard = os.environ.get('KG_SANITIZE_DROP_EDGES', '0').strip().lower() \
            in ('1', 'true', 'yes')
        rep['hard_drop_edges'] = hard
        if hard:
            before = len(edges)
            edges = [e for e in edges
                     if str(e.get('source')) in alive and str(e.get('target')) in alive]
            rep['dropped_edges_dangling'] = before - len(edges)
        else:
            # 软删除：**只打标**，边一条不删
            for e in edges:
                s, t = str(e.get('source')), str(e.get('target'))
                if s not in alive:
                    e['src_filtered'] = s
                if t not in alive:
                    e['tgt_filtered'] = t
            rep['marked_edges'] = sum(
                1 for e in edges if e.get('src_filtered') or e.get('tgt_filtered'))
        nodes = (kept + dropped) if keep_dropped else kept

    # ── ④ 方向见证（lvl>=3）─────────────────────────────────────────
    #
    # ⚠️ **`desc_verdict` 的取值是 `"consistent"` / `"inverted"` / `"unknown"`** ——
    # 不是 `"reversed"`。第一版我按 `'reversed'` 写，**永远不会命中**，
    # 而 `"inverted"` 又落不进任何一个分支 ⇒ **静默丢失**。
    # （与 A5 时"假设 `tokens()` 会保留点"同类：**先读实现再写判据**。）
    if lvl >= 3:
        for e in edges:
            desc = e.get('description') or e.get('evidence_text') or ''
            if not desc:
                rep['direction_uncertain'] += 1
                continue
            try:
                v = DW.witness(desc, str(e.get('source')), str(e.get('target')),
                               rel_type=e.get('type'))
            except Exception:
                rep['direction_uncertain'] += 1
                continue
            # `witness` 判不出时返回 **None**；否则返回 {'verdict': ...}
            verdict = v.get('verdict') if isinstance(v, dict) else None
            if verdict == 'inverted':
                e['source'], e['target'] = e['target'], e['source']
                # **翻转后要重新去重**：翻转可能让它与另一条边撞上
                rep['direction_flipped'] += 1
            elif verdict in (None, 'unknown', 'uncertain'):
                rep['direction_uncertain'] += 1
            elif verdict == 'consistent':
                pass                      # 与声明一致 ⇒ 什么都不做
            else:
                # 未知取值**要暴露**，不要静默 —— 否则将来 witness 改了约定
                # 这里会无声地少做事情（本项目最忌讳的一类失败）
                rep['direction_unexpected'] = rep.get('direction_unexpected', 0) + 1
        if rep['direction_flipped']:
            seen, out = set(), []
            for e in edges:
                k = _key(e)
                if k in seen:
                    rep['merged_after_rewire'] += 1
                    continue
                seen.add(k)
                out.append(e)
            edges = out

    rep['nodes_after'] = len(nodes)
    rep['edges_after'] = len(edges)
    return nodes, edges, rep


def describe(rep):
    """把 report 变成一行中文，便于写进日志与报告。"""
    if not rep.get('enabled'):
        return '图净化：未启用（KG_SANITIZE=0）'
    parts = [f"级别 {rep['level']}",
             f"实体 {rep['nodes_before']}→{rep['nodes_after']}",
             f"边 {rep['edges_before']}→{rep['edges_after']}"]
    if rep.get('absorbed_pairs'):
        parts.append(f"吸收 {rep['absorbed_pairs']} 对（重指 {rep['rewired_edges']} 条边）")
    if rep.get('dropped_entities'):
        if rep.get('hard_drop_edges'):
            parts.append(f"过滤实体 {rep['dropped_entities']}"
                         f"（**级联硬删边 {rep['dropped_edges_dangling']} 条**）")
        else:
            parts.append(f"过滤实体 {rep['dropped_entities']}"
                         f"（**软删：标记 {rep.get('marked_edges', 0)} 条边，一条未删**）")
    if rep.get('merged_after_rewire'):
        parts.append(f"重指后合并重复边 {rep['merged_after_rewire']} 条")
    if rep.get('direction_flipped') or rep.get('direction_uncertain'):
        parts.append(f"方向翻转 {rep['direction_flipped']}"
                     f"（判不出 {rep['direction_uncertain']}）")
    return '图净化：' + ' · '.join(parts)
