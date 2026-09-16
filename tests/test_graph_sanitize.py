# -*- coding: utf-8 -*-
r"""A4：`graph_sanitize` —— 三个模块接入图这一层。

## 这个文件测什么

A4 的问题是「三个模块**只在 `tools/_probe/` 里被引用**，没进 `pipeline.py`」
且「接之前**先设计"被过滤实体所连的边怎么办"**」。{

⇒ 本文件把**设计决策**钉成断言：

  1. **`lvl=0` 时原样返回**（默认路径的行为与开销都不变）；
  2. **吸收 ⇒ 边重指向，不是删边**；
  3. **重指后与已有边重复 ⇒ 必须合并**（这一步最容易漏）；
  4. **过滤 ⇒ 级联删边，并记账**（不能留下指向不存在节点的边）；
  5. **方向：只有 `"inverted"` 才翻转**；`"consistent"` 不动；判不出不动；
     未知取值**要暴露**（不能静默）。
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))

import graph_sanitize as GS  # noqa: E402


def _edge(s, t, ty='USES', **kw):
    d = {'source': s, 'target': t, 'type': ty}
    d.update(kw)
    return d


def _node(name, **kw):
    d = {'name': name, 'id': name}
    d.update(kw)
    return d


# ── ① 默认关闭：原样返回，连拷贝都不做 ────────────────────────────────
def test_level_0_is_a_noop(monkeypatch):
    monkeypatch.delenv('KG_SANITIZE', raising=False)
    nodes = [_node('A'), _node('B')]
    edges = [_edge('A', 'B')]
    n2, e2, rep = GS.sanitize(nodes, edges)
    assert rep['enabled'] is False and rep['level'] == 0
    # **同一个对象** —— 证明默认路径没有额外开销
    assert n2 is nodes and e2 is edges, 'lvl=0 应当原样返回同一个对象'


def test_level_read_from_env_each_call(monkeypatch):
    """开关**每次调用都读环境变量**，不在模块级缓存。

    ⚠️ 这条有教训：`app.py` 的 `EDIT_PLAN_DIR` 曾在模块级读环境变量，
    于是测试单跑全过、进全量跑就挂（import 只发生一次）。
    """
    monkeypatch.setenv('KG_SANITIZE', '0')
    assert GS.level() == 0
    monkeypatch.setenv('KG_SANITIZE', '2')
    assert GS.level() == 2, '改了环境变量之后必须立刻生效'
    monkeypatch.setenv('KG_SANITIZE', '9')
    assert GS.level() == 3, '超范围要夹到最大档，不该抛'


# ── ② 吸收 ⇒ 边重指向 ────────────────────────────────────────────────
def test_absorb_rewires_edges_not_deletes(monkeypatch):
    """`Y. Ma` 被吸收进 `W.-Y. Ma` ⇒ 边的端点要换成后者，**边还在**。"""
    monkeypatch.setenv('KG_SANITIZE', '1')
    nodes = [_node('Y. Ma', evidence=[{'page': 1}]), _node('W.-Y. Ma'),
             _node('TransE')]
    edges = [_edge('Y. Ma', 'TransE', 'CREATED_BY')]
    n2, e2, rep = GS.sanitize(nodes, edges)
    assert rep['absorbed_pairs'] >= 1
    assert len(e2) == 1, '**吸收不该删边**，只该重指向'
    assert e2[0]['source'] == 'W.-Y. Ma', f'端点没被重指：{e2[0]}'
    assert rep['rewired_edges'] == 1


def test_rewire_dedupes(monkeypatch):
    """重指之后与已有边撞上 ⇒ **必须合并**，否则同一个三元组出现两条。"""
    monkeypatch.setenv('KG_SANITIZE', '1')
    nodes = [_node('Y. Ma', evidence=[{'page': 1}]), _node('W.-Y. Ma'),
             _node('TransE')]
    edges = [_edge('Y. Ma', 'TransE', 'CREATED_BY'),   # 会被重指成 W.-Y. Ma
             _edge('W.-Y. Ma', 'TransE', 'CREATED_BY')]  # 已经存在
    n2, e2, rep = GS.sanitize(nodes, edges)
    assert len(e2) == 1, f'重指后没去重：{e2}'
    assert rep['merged_after_rewire'] >= 1


# ── ③ 过滤 ⇒ **默认软删（标记）**，硬删要显式开 ─────────────────────────
def test_filter_marks_edges_by_default_no_hard_delete(monkeypatch):
    """**默认软删除**：边一条不删，只打 `src_filtered` / `tgt_filtered`。

    ## 为什么改成了软删（这是量完全库之后的设计修正）

    第一版是**级联硬删**。量完全库发现一刀太大：
        过滤 23 个实体 ⇒ **删掉 560 条边**（平均 1 个实体 24 条）
        过滤 15 个实体 ⇒ **删掉 1072 条边**
        全库：过滤 667 个实体 ⇒ 删约 9000 条边 / 全库约 45000 ⇒ **20%**
    根因：被过滤的是"参考文献作者"，而它们在**图里度数极高**。

    ⇒ 改成软删（与 `entity_absorb` 的 `absorbed_into` 同一哲学：
      **模块只"报"，不擅自"改"**）。硬删由 `KG_SANITIZE_DROP_EDGES=1` 显式开启。
    """
    monkeypatch.setenv('KG_SANITIZE', '2')
    monkeypatch.delenv('KG_SANITIZE_DROP_EDGES', raising=False)
    nodes = [_node('TransE'), _node('RotatE'),
             _node('Smith, J.'),          # ← 参考文献作者形态，会被丢
             _node('2019')]               # ← 纯年份，会被丢
    edges = [_edge('TransE', 'RotatE', 'COMPARES_WITH'),
             _edge('Smith, J.', 'TransE', 'CREATED_BY'),
             _edge('2019', 'RotatE', 'PART_OF')]
    n2, e2, rep = GS.sanitize(nodes, edges)
    alive = {n['name'] for n in n2}
    assert 'Smith, J.' not in alive and '2019' not in alive, '这两个该被过滤'
    assert len(e2) == 3, f'**默认不该删边**：{e2}'
    assert rep['dropped_edges_dangling'] == 0
    assert rep['marked_edges'] == 2, f'应当标记 2 条边：{rep}'
    # 打标要能看出"哪一端被过滤了、原始名字是什么"
    marked = [e for e in e2 if e.get('src_filtered') or e.get('tgt_filtered')]
    assert len(marked) == 2
    assert any(e.get('src_filtered') == 'Smith, J.' for e in marked)


def test_hard_drop_is_opt_in_and_removes_dangling(monkeypatch):
    """`KG_SANITIZE_DROP_EDGES=1` 才真删，**且断言不留悬空边**。"""
    monkeypatch.setenv('KG_SANITIZE', '2')
    monkeypatch.setenv('KG_SANITIZE_DROP_EDGES', '1')
    nodes = [_node('TransE'), _node('RotatE'),
             _node('Smith, J.'), _node('2019')]
    edges = [_edge('TransE', 'RotatE', 'COMPARES_WITH'),
             _edge('Smith, J.', 'TransE', 'CREATED_BY'),
             _edge('2019', 'RotatE', 'PART_OF')]
    n2, e2, rep = GS.sanitize(nodes, edges)
    alive = {n['name'] for n in n2}
    assert rep['hard_drop_edges'] is True
    assert len(e2) == 1, f'硬删没生效：{e2}'
    assert rep['dropped_edges_dangling'] == 2
    # ★ **核心断言：硬删之后不许留下端点不存在的边**
    for e in e2:
        assert e['source'] in alive and e['target'] in alive, f'悬空边：{e}'


def test_keep_dropped_keeps_nodes_but_edges_stay_marked(monkeypatch):
    """`keep_dropped=True` 保留节点（标 drop_reason），边仍然**标记不删**。"""
    monkeypatch.setenv('KG_SANITIZE', '2')
    monkeypatch.delenv('KG_SANITIZE_DROP_EDGES', raising=False)
    nodes = [_node('TransE'), _node('Smith, J.')]
    edges = [_edge('Smith, J.', 'TransE', 'CREATED_BY')]
    n2, e2, rep = GS.sanitize(nodes, edges, keep_dropped=True)
    assert any(n['name'] == 'Smith, J.' for n in n2), 'keep_dropped 应保留节点'
    assert len(e2) == 1, '软删模式下边不该消失'
    assert e2[0].get('src_filtered') == 'Smith, J.'


# ── ④ 方向：只有 inverted 才翻转 ──────────────────────────────────────
def test_direction_only_flips_on_inverted(monkeypatch):
    """`desc_verdict` 的取值是 consistent / inverted / unknown —— **没有 reversed**。

    第一版按 `'reversed'` 写，永远不命中；`'inverted'` 又落不进任何分支 ⇒ 静默丢失。

    ⚠️ 第一版**这条测试自己写错了**：我用了
        `witness('RotatE was authored by TransE', 'TransE', 'RotatE')`
    而它返回 **`consistent`** —— 因为 `RotatE` 在句首（order=`T_S`），
    而 `authored by` 是"无助动词的被动"（sem=`('T','S')`），
    `sem == graph` ⇒ **一致**。**那是对的结果，而我的断言没给 `consistent` 留位置。**
    ⇒ 要造出 `inverted`，必须让**施事出现在句首**：
        `'TransE was authored by RotatE'` 配边 `TransE → RotatE`
        ⇒ order=`S_T` 而 sem=`('T','S')` ⇒ **`inverted`**。
    """
    monkeypatch.setenv('KG_SANITIZE', '3')
    nodes = [_node('TransE'), _node('RotatE')]
    edges = [_edge('TransE', 'RotatE', 'CREATED_BY',
                   description='TransE was authored by RotatE')]
    n2, e2, rep = GS.sanitize(nodes, edges)
    assert rep.get('direction_unexpected', 0) == 0, (
        '出现了未知 verdict ⇒ 说明取值约定又变了（或我写错了），'
        '这一条必须暴露而不是静默')
    assert rep['direction_flipped'] == 1, (
        f"这条边应当被判 inverted 并翻转：{rep}")
    assert e2[0]['source'] == 'RotatE' and e2[0]['target'] == 'TransE', (
        f'翻转后端点应当互换：{e2[0]}')


def test_direction_consistent_is_left_alone(monkeypatch):
    """`consistent` ⇒ **什么都不做**（既不翻转，也不计入"判不出"）。"""
    monkeypatch.setenv('KG_SANITIZE', '3')
    nodes = [_node('TransE'), _node('RotatE')]
    edges = [_edge('TransE', 'RotatE', 'CREATED_BY',
                   description='RotatE was authored by TransE')]
    _n, e2, rep = GS.sanitize(nodes, edges)
    assert rep['direction_flipped'] == 0
    assert rep.get('direction_unexpected', 0) == 0
    assert e2[0]['source'] == 'TransE', '一致就不该动'


def test_direction_unknown_is_counted_not_guessed(monkeypatch):
    """判不出方向的边**不动**，但必须计入 `direction_uncertain`。"""
    monkeypatch.setenv('KG_SANITIZE', '3')
    nodes = [_node('A'), _node('B')]
    edges = [_edge('A', 'B', 'RELATED_TO', description='A and B are related')]
    n2, e2, rep = GS.sanitize(nodes, edges)
    assert rep['direction_flipped'] == 0, '判不出就不该动'
    assert rep['direction_uncertain'] >= 1, '判不出也要记账'
    assert e2[0]['source'] == 'A', '边不该被改动'


def test_witness_contract_is_what_we_assume():
    """**直接钉住 `witness` 的返回约定** —— 这是本模块唯一的对外假设。

    如果将来 `desc_verdict` 换了取值，这条会先断，
    而不是等到线上"方向一条都没翻"，那时已经晚了。
    """
    import direction_witness as DW
    got = DW.witness('RotatE was authored by TransE', 'TransE', 'RotatE')
    assert got is None or got.get('verdict') in ('consistent', 'inverted'), (
        f'witness 的 verdict 取值变了：{got}')
    assert DW.desc_verdict('A and B are related', 'A', 'B') in (
        'consistent', 'inverted', 'unknown')


def test_describe_is_readable(monkeypatch):
    """`describe` 要能说出"标记了多少边"，这是审计的入口。"""
    monkeypatch.setenv('KG_SANITIZE', '2')
    monkeypatch.delenv('KG_SANITIZE_DROP_EDGES', raising=False)
    nodes = [_node('TransE'), _node('Smith, J.')]
    edges = [_edge('Smith, J.', 'TransE', 'CREATED_BY')]
    _n, _e, rep = GS.sanitize(nodes, edges)
    txt = GS.describe(rep)
    assert '软删' in txt, f'审计文本必须说明是软删还是硬删：{txt}'
    assert '标记' in txt and '一条未删' in txt, f'应当明说没删边：{txt}'
