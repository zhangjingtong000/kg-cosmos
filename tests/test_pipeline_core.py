# -*- coding: utf-8 -*-
r"""N19：`pipeline.py` —— 抽取管线的纯函数与编排。

## 为什么值得测

`pipeline.extract_graph()` 是**上传 PDF 时唯一真正跑的东西**：
分块 → 逐块抽取 → 证据合并 → 去重 → 端点规范化 → 丢悬空边。
在此之前它 0 单测（N19）。而它的失败模式**全部是"静默的"**：

  · 零产出却写出空快照、还记成完成（这就是 `ExtractionFailed` 存在的原因）；
  · 端点对不上的边被丢（实测第一篇 42% 的关系就这么没了）；
  · 重复边不合并 ⇒ 证据被拆散；
  · 进度回调抛异常把整次抽取搞挂。

## 怎么做到不碰模型 / 不碰 GPU

`extract_graph` 只从**模块全局**取 `extract_chunk` / `extract_chunk_once`，
所以 `monkeypatch.setattr(P, 'extract_chunk', fake)` 就能整条替换掉模型调用；
`text=` 入口本身又会跳过 PDF 解析（连 `parse_pdf` 都不会被调）。
⇒ 整个文件是**纯内存计算**，毫秒级。

## ⚠️ 两处"现状记录"（与源码注释矛盾，见对应测试）

  · `deduplicate_entities` 的 docstring 写「Cross-type: keep separate」，
    实际 Phase 1 **只按小写名字合并、完全不看 type**；
  · `chunk_text` 对**无标点的超长段落**会产出超过 `max_chars` 的 chunk
    （分块上限不是硬上限）。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))

import pipeline as P  # noqa: E402
import graph_sanitize as GS  # noqa: E402

TWO_PARA = 'A ' * 250 + '\n\n' + 'B ' * 250          # 两段，各 500 字符
ONE_CHUNK = 'x' * 400                                  # 单块（短文本）


def _res(nodes, edges, e_s=1.0, r_s=2.0):
    return {'nodes': nodes, 'edges': edges,
            'timing': {'entities_s': e_s, 'relations_s': r_s}}


def _nodes(*names):
    return [{'name': n, 'type': 'Method', 'description': 'd'} for n in names]


def _edges(*pairs):
    return [{'source': s, 'target': t, 'type': ty} for s, t, ty in pairs]


@pytest.fixture()
def fake_extract(monkeypatch):
    """把模型抽取整条换成假实现。**绝不联网**。

    两个入口都要换：`KG_EXTRACT_STYLE=combined` 时 pipeline 走的是
    `extract_chunk_once`，只换 `extract_chunk` 会漏掉那条路（测试就真去调模型了）。
    """
    def _install(fn):
        monkeypatch.setattr(P, 'extract_chunk', fn)
        monkeypatch.setattr(P, 'extract_chunk_once', fn)
        return fn
    return _install


# ════════════════════════════════════════════════════════════════════════
# 分块（纯函数）
# ════════════════════════════════════════════════════════════════════════
def test_chunking_splits_on_paragraph_boundaries():
    parsed = {'file': 'f.pdf', 'total_pages': 1,
              'pages': [{'num': 1, 'text': TWO_PARA, 'images': []}]}
    ch = P.chunk_document(parsed, max_chars=500)
    assert len(ch['chunks']) == 2, f'两段各 500 字符、上限 500 ⇒ 必须分两块：{ch["chunks"]}'
    assert [c['chunk_idx'] for c in ch['chunks']] == [1, 2]
    assert all(c['page'] == 1 for c in ch['chunks'])
    assert all(c['char_count'] == len(c['text']) for c in ch['chunks'])
    assert all(c['kind'] == 'text' for c in ch['chunks'])


def test_chunking_short_text_is_one_chunk():
    parsed = {'file': 'f.pdf', 'total_pages': 1,
              'pages': [{'num': 1, 'text': 'short', 'images': []}]}
    assert len(P.chunk_document(parsed, max_chars=500)['chunks']) == 1


def test_blank_text_produces_no_chunk():
    """空页不能变成"一个空 chunk" —— 那会白跑一次模型。"""
    parsed = {'file': 'f.pdf', 'total_pages': 1,
              'pages': [{'num': 1, 'text': '   \n  ', 'images': []}]}
    assert P.chunk_document(parsed, max_chars=500)['chunks'] == []


def test_adjacent_chunks_overlap_by_configured_ratio():
    """相邻块之间必须带重叠 —— 否则跨块的句子会在边界处被切断。"""
    para1, para2 = 'A' * 400, 'B' * 400
    parsed = {'file': 'f.pdf', 'total_pages': 1,
              'pages': [{'num': 1, 'text': para1 + '\n\n' + para2, 'images': []}]}
    chunks = P.chunk_document(parsed, max_chars=500)['chunks']
    assert len(chunks) == 2
    second = chunks[1]['text']
    assert second.startswith('A' * 150), (
        f'第二块应带 150 字符（500*0.3）重叠，实际开头：{second[:40]!r}')
    assert 'B' * 400 in second


def test_unpunctuated_long_paragraph_exceeds_max_chars():
    """⚠️ **现状记录，不是期望行为**。

    `chunk_text` 只在**句子边界**切分；一段没有句点的超长文本会**整段**成为一个 chunk，
    实测 1200 字符的输入产出 **1201 字符**的单块（`max_chars=500`）。
    也就是说 `max_chars` 不是硬上限，prompt 长度因此可能远超预期。
    **修好之后这条会红 —— 那时请把断言改成 `<= max_chars`。**
    """
    chunks = P.chunk_document(
        {'file': 'f.pdf', 'total_pages': 1,
         'pages': [{'num': 1, 'text': 'a' * 1200, 'images': []}]},
        max_chars=500)['chunks']
    assert len(chunks) == 1 and len(chunks[0]['text']) > 500, (
        f'当前行为是"整段不切"，实际：{[len(c["text"]) for c in chunks]}')


# ════════════════════════════════════════════════════════════════════════
# extract_graph 的编排
# ════════════════════════════════════════════════════════════════════════
def test_text_entry_skips_pdf_parsing(monkeypatch, fake_extract):
    """"已解析文本"入口必须**完全不碰 PDF**（外部对照数据集就是走这条路）。"""
    def boom(*a, **k):
        raise AssertionError('text= 入口不该调用 parse_pdf')
    monkeypatch.setattr(P, 'parse_pdf', boom)
    fake_extract(lambda *a, **k: _res(_nodes('TransE', 'RotatE'),
                                      _edges(('TransE', 'RotatE', 'USES'))))
    out = P.extract_graph('', 'wd', 'sid', 'paper.pdf', text='x' * 400)
    assert out['parsed']['file'] == 'paper.pdf'
    assert [n['name'] for n in out['nodes']] == ['TransE', 'RotatE']
    assert out['total_chunks'] == 1


def test_evidence_is_attached_to_every_node_and_edge(fake_extract):
    fake_extract(lambda *a, **k: _res(_nodes('TransE', 'RotatE'),
                                      _edges(('TransE', 'RotatE', 'USES'))))
    out = P.extract_graph('', 'wd', 'sid', 'paper.pdf', text=ONE_CHUNK)
    for item in out['nodes'] + out['edges']:
        ev = item.get('evidence') or []
        assert ev, f'每条产物都必须带证据：{item}'
        assert ev[0]['source_id'] == 'sid'
        assert ev[0]['source_name'] == 'paper.pdf'
        assert ev[0]['page'] == 1
        assert ev[0]['chunk_idx'] == 1
        assert ev[0]['text'] == ONE_CHUNK


def test_duplicate_edges_are_merged_within_one_chunk(fake_extract):
    """同一 (source, target, type) 出现两次 ⇒ **合成一条**（朴素实现会丢证据）。

    同一 chunk 内的证据按 `(page, chunk_idx)` 去重，所以合成后证据仍只有 1 条 ——
    "证据累加"发生在**跨 chunk**（见下一条）。
    """
    fake_extract(lambda *a, **k: _res(
        _nodes('TransE', 'RotatE'),
        _edges(('TransE', 'RotatE', 'USES'), ('TransE', 'RotatE', 'USES'))))
    out = P.extract_graph('', 'wd', 'sid', 'paper.pdf', text=ONE_CHUNK)
    assert len(out['edges']) == 1, f'{out["edges"]}'
    assert len(out['edges'][0]['evidence']) == 1


def test_same_edge_from_two_chunks_accumulates_evidence(fake_extract):
    """两个 chunk 抽出同一条边 ⇒ 一条边、**两份证据**（否则就丢了出处）。"""
    fake_extract(lambda *a, **k: _res(_nodes('TransE', 'RotatE'),
                                      _edges(('TransE', 'RotatE', 'USES'))))
    out = P.extract_graph('', 'wd', 'sid', 'paper.pdf', text=TWO_PARA, max_chars=500)
    assert len(out['edges']) == 1
    idx = sorted(e['chunk_idx'] for e in out['edges'][0]['evidence'])
    assert idx == [1, 2], f'两个 chunk 的证据都要在：{out["edges"][0]["evidence"]}'


def test_dangling_edges_are_dropped_and_counted(fake_extract):
    """端点解析不到实体的边必须丢，**而且要记账**（静默丢是旧版本的坑）。"""
    fake_extract(lambda *a, **k: _res(
        _nodes('TransE', 'RotatE'),
        _edges(('TransE', 'RotatE', 'USES'), ('TransE', 'Ghost', 'USES'))))
    out = P.extract_graph('', 'wd', 'sid', 'paper.pdf', text=ONE_CHUNK)
    assert out['dangling_edges_dropped'] == 1
    assert [(e['source'], e['target']) for e in out['edges']] == [('TransE', 'RotatE')]


def test_edge_endpoints_are_canonicalized_before_the_dangling_check(fake_extract):
    """大小写/连字符变体要先被规范化，否则会被当成悬空边丢掉（旧版丢过 42%）。"""
    fake_extract(lambda *a, **k: _res(
        _nodes('TransE Model', 'RotatE'),
        _edges(('transe  model', 'RotatE', 'USES'))))
    out = P.extract_graph('', 'wd', 'sid', 'paper.pdf', text=ONE_CHUNK)
    assert out['dangling_edges_dropped'] == 0, '变体端点被误判成悬空边'
    assert out['edges'][0]['source'] == 'TransE Model'


def test_all_chunks_empty_raises_extraction_failed(fake_extract):
    """零产出 ≠ 图谱为空 —— 必须冒泡，绝不能静默写出空快照。"""
    fake_extract(lambda *a, **k: _res([], []))
    with pytest.raises(P.ExtractionFailed) as ei:
        P.extract_graph('', 'wd', 'sid', 'paper.pdf', text=ONE_CHUNK)
    assert 'sid' in str(ei.value) and '零产出' in str(ei.value)


def test_partial_chunk_failure_is_tolerated_and_counted(monkeypatch, fake_extract):
    """单个 chunk 抛异常不拖垮全篇，但**必须计数**（不许静默少一段）。"""
    calls = {'n': 0}

    def flaky(*a, **k):
        calls['n'] += 1
        if calls['n'] == 1:
            raise RuntimeError('boom')
        return _res(_nodes('TransE', 'RotatE'), _edges(('TransE', 'RotatE', 'USES')))

    fake_extract(flaky)
    out = P.extract_graph('', 'wd', 'sid', 'paper.pdf', text=TWO_PARA, max_chars=500)
    assert out['total_chunks'] == 2
    assert out['chunk_failures'] == 1
    assert [n['name'] for n in out['nodes']], '另一块成功 ⇒ 不该整体失败'


def test_progress_callback_receives_all_stages(fake_extract):
    fake_extract(lambda *a, **k: _res(_nodes('TransE', 'RotatE'),
                                      _edges(('TransE', 'RotatE', 'USES'))))
    seen = []
    P.extract_graph('', 'wd', 'sid', 'paper.pdf', text=TWO_PARA, max_chars=500,
                    on_progress=lambda stage, pct, detail='': seen.append((stage, pct)))
    stages = [s for s, _ in seen]
    assert stages[0] == 'parse' and 'chunk' in stages and 'norm' in stages
    assert stages.count('extract') >= 2, f'逐块抽取要能报进度：{seen}'
    pcts = [p for _, p in seen]
    assert pcts == sorted(pcts), f'进度必须单调不减：{pcts}'
    assert pcts[-1] <= 100 and all(0 <= p <= 100 for p in pcts)


def test_progress_callback_exception_never_breaks_extraction(fake_extract):
    """进度上报是"锦上添花"，绝不能反过来把抽取搞挂。"""
    fake_extract(lambda *a, **k: _res(_nodes('TransE', 'RotatE'),
                                      _edges(('TransE', 'RotatE', 'USES'))))

    def bad(stage, pct, detail=''):
        raise RuntimeError('progress boom')

    out = P.extract_graph('', 'wd', 'sid', 'paper.pdf', text=ONE_CHUNK, on_progress=bad)
    assert [n['name'] for n in out['nodes']] == ['TransE', 'RotatE']


def test_extraction_time_is_the_sum_of_chunk_timings(fake_extract):
    fake_extract(lambda *a, **k: _res(_nodes('TransE', 'RotatE'),
                                      _edges(('TransE', 'RotatE', 'USES')),
                                      e_s=1.5, r_s=2.25))
    out = P.extract_graph('', 'wd', 'sid', 'paper.pdf', text=TWO_PARA, max_chars=500)
    assert out['extraction_time_s'] == 7.5, f'2 块 ×(1.5+2.25)=7.5，实际 {out["extraction_time_s"]}'


# ════════════════════════════════════════════════════════════════════════
# 归一：去重 + 端点规范化
# ════════════════════════════════════════════════════════════════════════
def test_deduplicate_entities_keeps_the_capitalized_variant():
    ents = [{'name': 'transe', 'type': 'Method', 'description': 'short'},
            {'name': 'TransE', 'type': 'Method', 'description': 'a much longer description'}]
    out = P.deduplicate_entities([dict(e) for e in ents])
    assert len(out) == 1, f'大小写变体必须合并：{out}'
    assert out[0]['name'] == 'TransE', f'保留的是有大写开头的那个：{out[0]["name"]}'
    assert out[0]['description'].startswith('a much longer'), '描述取更长的'


def test_same_name_across_types_is_merged_currently():
    """⚠️ **现状记录**：源码 docstring 写「Cross-type: keep separate
    (e.g. "Transformer" system vs "Transformer" organization)」，
    但 Phase 1 的键只有 `name.lower()`、**完全不看 type** ⇒ 实际会合并成一个。

    这里如实钉住现状（保留先出现的那个 type）。**若将来按 docstring 修好，
    这条会红 —— 那时请改成断言两个都留下。**
    """
    ents = [{'name': 'Transformer', 'type': 'Method', 'description': 'a'},
            {'name': 'Transformer', 'type': 'Organization', 'description': 'b'}]
    out = P.deduplicate_entities([dict(e) for e in ents])
    assert len(out) == 1 and out[0]['type'] == 'Method', (
        f'当前实现跨类型也会合并：{[(e["name"], e["type"]) for e in out]}')


def test_canonicalize_endpoints_maps_case_and_punctuation_variants():
    nodes = [{'name': 'TransE Model', 'type': 'Method'}]
    rels = [{'source': 'transe  model', 'target': 'TransE-Model', 'type': 'X'},
            {'source': 'Unknown Thing', 'target': 'TransE Model', 'type': 'X'}]
    out = P.canonicalize_relation_endpoints([dict(r) for r in rels], nodes)
    assert (out[0]['source'], out[0]['target']) == ('TransE Model', 'TransE Model')
    assert out[1]['source'] == 'Unknown Thing', '匹配不到就保留原名，由上层决定去留'


# ════════════════════════════════════════════════════════════════════════
# A4 接线：默认关闭，开了才动图
# ════════════════════════════════════════════════════════════════════════
def _absorptive_graph():
    """一对可吸收实体（同页、短名是人名的前缀）+ 一对不可吸收的（限定词）。

    ⚠️ 每对的 **type 刻意不同**：`deduplicate_entities` 的 Phase 2 只对同 type
    做模糊合并，而 `balaevic` / `structured data` 恰好会被它判为重复
    （见 `test_same_name_across_types_is_merged_currently`）。
    这里要测的是**净化层**，所以先把 dedup 的干扰排除掉。
    """
    return [
        {'name': 'Balažević', 'type': 'Person', 'evidence': [{'page': 1}]},
        {'name': 'Balažević Et Al.', 'type': 'Author', 'evidence': [{'page': 1}]},
        {'name': 'Structured Data', 'type': 'Concept', 'evidence': [{'page': 2}]},
        {'name': 'Semi-Structured Data', 'type': 'Dataset', 'evidence': [{'page': 2}]},
    ]


def test_sanitize_default_is_off_so_nothing_is_absorbed(monkeypatch, fake_extract):
    monkeypatch.delenv('KG_SANITIZE', raising=False)
    fake_extract(lambda *a, **k: _res(_build(_absorptive_graph()), []))
    out = P.extract_graph('', 'wd', 'sid', 'paper.pdf', text=ONE_CHUNK)
    assert not any('absorbed_into' in n for n in out['nodes']), (
        '默认 KG_SANITIZE=0 ⇒ 不许改动图（既有绿灯与历史产物的口径必须不变）')
    assert len(out['nodes']) == 4, f'{[(n["name"], n["type"]) for n in out["nodes"]]}'


def test_sanitize_level_1_absorbs_person_forms_only(monkeypatch, fake_extract):
    fake_extract(lambda *a, **k: _res(_build(_absorptive_graph()), []))
    monkeypatch.delenv('KG_SANITIZE', raising=False)
    off = P.extract_graph('', 'wd', 'sid', 'paper.pdf', text=ONE_CHUNK)

    monkeypatch.setenv('KG_SANITIZE', '1')
    assert GS.level() == 1
    on = P.extract_graph('', 'wd', 'sid', 'paper.pdf', text=ONE_CHUNK)

    by_name = {n['name']: n for n in on['nodes']}
    assert by_name['Balažević'].get('absorbed_into') == 'Balažević Et Al.', (
        f'人名补齐应当被吸收：{by_name["Balažević"]}')
    assert 'absorbed_into' not in by_name['Structured Data'], (
        '限定词改变所指，**不许**吸收')
    assert len(on['nodes']) == len(off['nodes']), (
        '吸收不删节点（图规模与既有指标口径不变）：'
        f'{len(off["nodes"])} → {len(on["nodes"])}')


def _build(nodes):
    """`_nodes()` 造的是最小节点；这里要把 evidence 原样带上。"""
    return [dict(n) for n in nodes]
