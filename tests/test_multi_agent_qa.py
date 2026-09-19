# -*- coding: utf-8 -*-
r"""N19：`multi_agent_qa` —— **项目主看点**的单元测试。

## 为什么必须先补这个文件

`multi_agent_qa` 是演示时被问得最多的东西（「多 Agent 问答」），
而在此之前它在 85 个 pytest 里**一处都没有被直接测过**（N19），
`/api/qa` 在 31 项检查里也 **0 处命中**（N17）。
⇒ 被问「你怎么保证问答是对的」时，拿不出任何可展示的东西。

## 这个文件怎么做到"不依赖 GPU / 不依赖 Ollama 服务 / 几十秒跑完"

**所有模型调用点都只有一个**：模块级的 `_ollama()`。
测试一律用 `monkeypatch.setattr(M, '_ollama', FakeOllama(...))` 把它换掉 ——
`answer` / `step_agents` / `step_synth` / `bridge_keywords_zh` 都走这一个口子，
所以换掉它就等于"模型永不联网"，而且**调用次数、prompt 内容、温度参数都可断言**。

## 覆盖的四步与三条纪律

  · ① 检索：字面命中 / 2 跳 BFS / 边去重 / seeds 上限 / 跨语言桥接 / 桥接失败如实报告；
  · ②③ 支持与反对：两次**独立**调用，prompt 不同；
  · ④ 综合：只看两方文本，综合失败时回退成两方原文；
  · 降级：模型不可用时 `degraded=True`，**不抛异常、不假装有模型参与**。

## ⚠️ 两处"现状记录"（不是期望行为，见本文件内对应测试的 docstring）

  1. 上层任务描述期望检索有「长名优先、被更长命中包含的短名被丢弃」——
     **`_match_seeds()` 里没有这段逻辑**（既不排序也不做包含去除），
     短名与长名会**同时**进 seeds。本文件如实钉住现状，改动可见。
  2. 桥接只在**首行**里找 ASCII 关键词：模型若把中文解释写在第一行，
     整个桥接直接落空（`bridge_keywords_zh` 返回 None）。
"""
import json

import pytest

import multi_agent_qa as M


# ── 测试用的小图（不读磁盘快照：检索是纯函数，构造数据更可控）──────────
def _edge(s, t, ty='USED_FOR', quotes=()):
    e = {'source': s, 'target': t, 'type': ty}
    if quotes:
        e['evidence'] = [{'source_name': 'paper.pdf', 'source_id': 'sid',
                          'page': p, 'text': q} for p, q in quotes]
    return e


NODES = [
    # ⚠️ 描述里**刻意不含** 'knowledge graph'：桥接测试要能区分
    #    "桥接词命中了哪个实体"，描述串味会让 TransE 也被命中。
    {'name': 'TransE', 'id': 'TransE',
     'description': 'translation based embedding'},
    {'name': 'TransE Model', 'id': 'TransE Model', 'description': 'a variant of transe'},
    {'name': 'RotatE', 'id': 'RotatE', 'description': 'rotation based embedding'},
    {'name': 'Knowledge Graph', 'id': 'Knowledge Graph',
     'description': 'a graph of entities and relations'},
    {'name': '知识图谱', 'id': '知识图谱', 'description': '中文实体'},
]

EDGES = [
    _edge('TransE', 'Knowledge Graph', quotes=[(2, 'TransE maps entities into a vector space.')]),
    _edge('TransE Model', 'Knowledge Graph'),
    _edge('Knowledge Graph', 'RotatE', 'RELATED_TO'),
    _edge('Knowledge Graph', 'RotatE', 'RELATED_TO'),      # 重复边：必须只留一条
]


class FakeOllama:
    """假的 `_ollama`：记录调用，**绝不联网**。

    `reply` 可以是字符串，也可以是 `prompt -> str|None` 的函数
    （要按角色返回不同文本时用后者）。
    """

    def __init__(self, reply=None):
        self.calls = []
        self.reply = reply

    def __call__(self, prompt, timeout=180, temperature=0.2, num_predict=600):
        self.calls.append({'prompt': prompt, 'timeout': timeout,
                           'temperature': temperature, 'num_predict': num_predict})
        if callable(self.reply):
            return self.reply(prompt)
        return self.reply


@pytest.fixture()
def ollama(monkeypatch):
    """`fake = ollama(reply)` ⇒ 把模块里的 `_ollama` 换成假实现并返回它。"""
    def _install(reply=None):
        fake = FakeOllama(reply)
        monkeypatch.setattr(M, '_ollama', fake)
        return fake
    return _install


# ════════════════════════════════════════════════════════════════════════
# ① 检索 Agent —— 纯规则、零模型
# ════════════════════════════════════════════════════════════════════════
def test_token_hit_returns_seeds_without_any_model_call(ollama):
    fake = ollama(None)
    r = M.retrieve('What is TransE used for?', NODES, EDGES)
    assert 'TransE' in r['seeds'], f'字面命中应进 seeds：{r["seeds"]}'
    assert fake.calls == [], '英文问题且命中时**一次模型都不该调**'
    assert r['bridge'] is None
    assert '纯规则' in r['reason']


def test_question_equal_to_entity_name_is_hit():
    """问题本身就是实体名（归一化后相等）也必须命中 —— 走的是 `q in nn` 那一支。"""
    r = M.retrieve('transe', NODES, EDGES)
    assert 'TransE' in r['seeds'], f'{r["seeds"]}'


def test_when_both_short_and_long_names_match_both_are_seeded():
    """⚠️ **现状记录**：`_match_seeds()` 没有「长名优先 / 丢掉被包含的短名」逻辑。

    上层任务把「长名优先、被更长命中包含的短名被丢弃」列为应覆盖的行为，
    但源码里既没有排序也没有包含去除（只有 `seeds[:12]` 截断）。
    这里如实钉住**当前**行为：短名与长名**同时**进 seeds。
    **若将来真加了包含去除，这条会红 —— 那时请改成断言短名被丢弃。**
    """
    r = M.retrieve('TransE Model', NODES, EDGES)
    assert 'TransE' in r['seeds'] and 'TransE Model' in r['seeds'], (
        f'当前实现不过滤被包含的短名，两个都应在：{r["seeds"]}')


def test_seeds_are_capped_at_twelve():
    nodes = [{'name': f'alpha concept {i}', 'id': f'alpha concept {i}'} for i in range(20)]
    r = M.retrieve('alpha', nodes, [])
    assert len(r['seeds']) == 12, f'seeds 上限是 12，实际 {len(r["seeds"])}'


def test_two_hop_bfs_expands_neighbours_and_dedupes_edges():
    r = M.retrieve('TransE', NODES, EDGES)
    names = {n['name'] for n in r['nodes']}
    assert {'TransE', 'TransE Model', 'Knowledge Graph', 'RotatE'} <= names, (
        f'2 跳应当覆盖到 RotatE：{names}')
    keys = [(e['source'], e['target'], e['type']) for e in r['edges']]
    assert len(keys) == len(set(keys)), f'边必须去重：{keys}'
    assert len(keys) == 3, f'4 条输入边里有 1 条重复 ⇒ 应为 3 条，实际 {keys}'


def test_empty_question_short_circuits():
    r = M.retrieve('', NODES, EDGES)
    assert r['seeds'] == [] and r['nodes'] == [] and r['edges'] == []
    assert r['reason'] == '问题为空'


def test_no_overlap_reports_zero_evidence_honestly(ollama):
    ollama(None)
    r = M.retrieve('zzz qqq unrelated topic', NODES, EDGES)
    assert r['seeds'] == []
    assert '没有词面交集' in r['reason'], (
        f'检索落空必须说清是"没有交集"，而不是让用户以为图谱里没有这个知识：{r["reason"]}')


# ── 跨语言桥接（BRIDGE_PROMPT 那条路）──────────────────────────────────
def test_chinese_question_bridges_to_english_keywords(ollama):
    fake = ollama('knowledge graph')
    r = M.retrieve('这幅图讲了什么', NODES, EDGES)
    assert fake.calls, '纯中文问题规则落空 ⇒ 必须触发一次桥接调用'
    assert r['bridge'] and r['bridge']['query_en'] == 'knowledge graph'
    assert r['bridge']['hit'] is True, f'桥接词应当命中 Knowledge Graph：{r["bridge"]}'
    assert r['seeds'] == ['Knowledge Graph'], f'{r["seeds"]}'
    assert '调用了本地模型' in r['reason'], (
        f'走了桥接就不能再宣称"纯规则、可复现"：{r["reason"]}')


def test_bridge_call_asks_for_few_keywords(ollama):
    """桥接要的是几个词，不是一段判断 —— 温度 0、只生成 48 token。"""
    fake = ollama('knowledge graph')
    M.bridge_keywords_zh('这幅图讲了什么')
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call['temperature'] == 0.0 and call['num_predict'] == 48, (
        f'桥接的参数变了：temperature={call["temperature"]}, num_predict={call["num_predict"]}')
    assert '这幅图讲了什么' in call['prompt'], '中文问题必须进 prompt'
    assert '英文关键词' in call['prompt']


@pytest.mark.parametrize('raw,expect', [
    ('Knowledge Graph reasoning', 'Knowledge Graph reasoning'),
    # 重复词、<3 字符的词必须被丢掉（'ab'、'x9'）
    ('Knowledge Graph reasoning knowledge ab x9', 'Knowledge Graph reasoning'),
    # 只取第一行（模型有时会在后面补解释）
    ('Knowledge Graph reasoning\n这是解释', 'Knowledge Graph reasoning'),
    # 一行的第一个词没有 ASCII 词 ⇒ 整个桥接落空（⚠️ 现状：只看首行）
    ('解释：见图', None),
    ('   ', None),
])
def test_bridge_keywords_cleanup(monkeypatch, raw, expect):
    monkeypatch.setattr(M, '_ollama', lambda *a, **k: raw)
    assert M.bridge_keywords_zh('中文问题', timeout=1) == expect


def test_bridge_keywords_at_most_six(monkeypatch):
    monkeypatch.setattr(
        M, '_ollama', lambda *a, **k: 'alpha beta gamma delta epsilon zeta eta theta')
    assert M.bridge_keywords_zh('中文问题') == 'alpha beta gamma delta epsilon zeta'


def test_bridge_returns_none_when_model_unavailable(ollama):
    fake = ollama(None)
    assert M.bridge_keywords_zh('这幅图讲了什么') is None
    assert len(fake.calls) == 1


def test_bridge_failure_is_reported_not_hidden(ollama):
    ollama(None)
    r = M.retrieve('这幅图讲了什么', NODES, EDGES)
    assert r['seeds'] == []
    assert r['bridge'] is not None and r['bridge']['query_en'] is None, (
        f'桥接失败也是结果，必须如实记在 bridge 里：{r["bridge"]}')
    assert r['bridge']['hit'] is False
    assert '本地模型没有返回可用关键词' in r['reason']


def test_rule_hit_suppresses_bridge_for_chinese_question(ollama):
    """中文问题**只要规则能命中**，就不许调模型 —— 这是"桥接不是模型检索"的边界。"""
    fake = ollama('knowledge graph')
    r = M.retrieve('知识图谱的构建方法是什么', NODES, EDGES)
    assert fake.calls == [], '规则已命中却仍调了模型 ⇒ 破坏了"LLM 不做检索"的纪律'
    assert r['bridge'] is None
    assert '知识图谱' in r['seeds'], f'{r["seeds"]}'


# ════════════════════════════════════════════════════════════════════════
# 证据：每条都要指向论文原文
# ════════════════════════════════════════════════════════════════════════
def test_evidence_pack_puts_quoted_items_first():
    sub = {'edges': [_edge('A', 'B', 'X'), _edge('C', 'D', 'X',
                                            quotes=[(3, '原文片段')])]}
    ev = M.evidence_pack(sub)
    assert ev[0]['has_quote'] is True, f'有原文引文的必须排前面：{ev}'
    assert ev[0]['citations'][0]['paper'] == 'paper.pdf'
    assert ev[0]['citations'][0]['page'] == 3
    assert ev[0]['citations'][0]['quote'] == '原文片段'


def test_evidence_pack_caps_items():
    sub = {'edges': [_edge(f'S{i}', f'T{i}') for i in range(30)]}
    assert len(M.evidence_pack(sub, max_items=5)) == 5


def test_citation_quote_is_truncated_to_400():
    ev = {'source_name': 'p.pdf', 'page': 1, 'text': 'x' * 800}
    cits = M.citations_of({'evidence': [ev]})
    assert len(cits[0]['quote']) == 400


def test_citations_limit_counts_raw_entries_not_valid_ones():
    """⚠️ 语义记录：`limit` 截的是**原始 evidence 条数**，不是"有效引文条数"。

    所以 `limit=1` 且第一条是垃圾时，返回的是**空列表**（而不是"再往后找一条有效的"）。
    本项目的抽取产物里 evidence 是脚本生成的 dict，正常不会踩到；
    但把这个语义钉住，免得"少了引文"被误判成"没有原文"。
    """
    two = [{'source_name': 'a.pdf', 'page': 1, 'text': 'x'},
           {'source_name': 'b.pdf', 'page': 2, 'text': 'y'}]
    assert [c['paper'] for c in M.citations_of({'evidence': two}, limit=1)] == ['a.pdf']
    assert [c['paper'] for c in M.citations_of({'evidence': two}, limit=2)] == ['a.pdf', 'b.pdf']
    assert M.citations_of({'evidence': ['junk', two[1]]}, limit=1) == []


# ════════════════════════════════════════════════════════════════════════
# ②③④ 编排：支持 / 反对 / 综合，以及降级
# ════════════════════════════════════════════════════════════════════════
def _by_role(prompt):
    if '你是**支持方**' in prompt:
        return '支持：证据 1 说明 TransE 被广泛使用'
    if '你是**反对方**' in prompt:
        return '未发现反证'
    if '你是**综合**' in prompt:
        return '结论：TransE 被广泛使用。置信度为高。'
    return None


def test_answer_stitches_support_oppose_and_synth(ollama):
    fake = ollama(_by_role)
    res = M.answer('What is TransE used for?', NODES, EDGES)
    assert res['degraded'] is False
    assert [a['role'] for a in res['agents']] == ['支持方', '反对方'], res['agents']
    assert res['agents'][0]['text'].startswith('支持：')
    assert res['agents'][1]['text'] == '未发现反证', (
        '反对 Agent 即使没找到反证也要保留产出（"有反方"不能变摆设）')
    assert res['conclusion'].startswith('结论：')
    assert res['confidence'] == '高', f'从综合文本里必须抽出"高"：{res["confidence"]}'
    assert len(fake.calls) == 3, f'支持/反对/综合各一次，实际 {len(fake.calls)}'


def test_answer_support_and_oppose_see_the_same_evidence(ollama):
    fake = ollama(_by_role)
    M.answer('What is TransE used for?', NODES, EDGES)
    sup_p, opp_p = fake.calls[0]['prompt'], fake.calls[1]['prompt']
    assert 'TransE' in sup_p and 'TransE' in opp_p, '两方都该看到同一批证据'
    assert '你是**支持方**' in sup_p and '你是**反对方**' in opp_p, '两方必须是两次独立调用'
    assert '你是**反对方**' not in sup_p, '支持方的 prompt 里不许出现反对方的角色设定'
    assert '[1]' in sup_p, '证据必须带编号，模型只能引用编号'


def test_answer_synth_sees_both_sides(ollama):
    fake = ollama(_by_role)
    M.answer('What is TransE used for?', NODES, EDGES)
    synth_p = fake.calls[2]['prompt']
    assert '支持：证据 1' in synth_p and '未发现反证' in synth_p, (
        '综合 Agent 只能看到两方交上来的文本')
    assert '[1]' not in synth_p, '综合方不该接触原始证据（编号是证据层的东西）'


def test_answer_falls_back_to_two_sided_text_when_synth_fails(ollama):
    def reply(prompt):
        if '你是**综合**' in prompt:
            return None
        return _by_role(prompt)
    ollama(reply)
    res = M.answer('What is TransE used for?', NODES, EDGES)
    assert res['conclusion'] == '支持方：支持：证据 1 说明 TransE 被广泛使用\n反对方：未发现反证', (
        f'综合失败必须回退成两方原文，不能空结论：{res["conclusion"]!r}')
    assert res['degraded'] is False, '支持/反对有产出，就不算整体降级'


def test_answer_degrades_when_model_unavailable(ollama):
    ollama(None)
    res = M.answer('What is TransE used for?', NODES, EDGES, use_llm=True)
    assert res['degraded'] is True, '模型不可用必须如实标 degraded'
    assert res['agents'] == [], '没有模型就没有 Agent 产出，不许编'
    assert '本地模型不可用' in res['conclusion']
    assert len(res['evidence']) > 0, '降级后**检索结果仍要给出**（不能连证据一起丢）'


def test_answer_without_llm_never_calls_model(ollama):
    fake = ollama('不该被调用')
    res = M.answer('What is TransE used for?', NODES, EDGES, use_llm=False)
    assert fake.calls == [], 'use_llm=False 时一次模型调用都不许有'
    assert res['degraded'] is True
    assert '未启用模型' in res['conclusion']


def test_answer_without_evidence_short_circuits(ollama):
    fake = ollama(_by_role)
    res = M.answer('zzz qqq unrelated', NODES, EDGES)
    assert res['conclusion'] == '图谱里没有与这个问题相关的证据。'
    assert res['confidence'] == '—'
    assert res['agents'] == []
    assert fake.calls == [], '没有证据就不该调模型'


@pytest.mark.parametrize('text,expect', [
    ('置信度：高', '高'),
    ('置信度 中', '中'),
    ('置信度为高', '高'),      # 第一次实测模型就是这么写的
    ('置信度是高', '高'),
    ('置信度：低', '低'),
])
def test_confidence_parses_model_phrasings(text, expect):
    assert M._confidence_of(text, None) == expect


@pytest.mark.parametrize('opp,expect', [
    ('未发现反证', '中'),                 # 反方也没找到东西 ⇒ 中
    ('证据 3 削弱了结论，因为……', '低'),   # 有反证 ⇒ 低
    (None, '中'),
])
def test_confidence_falls_back_by_oppose_text(opp, expect):
    assert M._confidence_of('没有任何置信度字样', opp) == expect


# ════════════════════════════════════════════════════════════════════════
# 分步接口（/api/qa/step 的四个 step 直接调它们）
# ════════════════════════════════════════════════════════════════════════
def test_step_retrieve_note_says_pure_rule(ollama):
    fake = ollama(None)
    out = M.step_retrieve('What is TransE used for?', NODES, EDGES)
    assert out['stage'] == 'retrieve' and out['agent'] == '检索 Agent'
    assert '未调用模型' in out['note'], f'{out["note"]}'
    assert fake.calls == []
    assert out['n_evidence'] == len(out['evidence'])


def test_step_retrieve_note_changes_when_bridged(ollama):
    ollama('knowledge graph')
    out = M.step_retrieve('这幅图讲了什么', NODES, EDGES)
    assert '中文问题 → 先转成英文检索词' in out['note'], (
        f'调了模型就不能再宣称"未调用模型"：{out["note"]}')
    assert 'knowledge graph' in out['note']
    assert out['retrieval']['bridge']['hit'] is True


def test_step_agents_support_and_oppose_are_separate_calls(ollama):
    fake = ollama(_by_role)
    sup = M.step_agents('q', M.evidence_pack({'edges': EDGES}), 'support')
    opp = M.step_agents('q', M.evidence_pack({'edges': EDGES}), 'oppose')
    assert sup['role'] == '支持方' and sup['ok'] is True
    assert opp['role'] == '反对方' and opp['ok'] is True
    assert len(fake.calls) == 2, '必须是两次独立调用（一次返回两段就看不出"独立"）'
    assert '你是**支持方**' in fake.calls[0]['prompt']
    assert '你是**反对方**' in fake.calls[1]['prompt']
    assert opp['text'] == '未发现反证'


def test_step_agents_marks_failure_instead_of_raising(ollama):
    ollama(None)
    sup = M.step_agents('q', [{'claim': 'A —[X]→ B'}], 'support')
    assert sup['text'] is None and sup['ok'] is False, (
        f'模型不返回时要如实标 ok=False，而不是抛异常或编一段：{sup}')


def test_step_agents_rejects_unknown_role(ollama):
    ollama('x')
    with pytest.raises(ValueError):
        M.step_agents('q', [], 'nope')


def test_step_synth_falls_back_and_reports_confidence(ollama):
    ollama(None)
    out = M.step_synth('q', '支持方文本', '未发现反证')
    assert out['stage'] == 'synth'
    assert out['text'] == '支持方：支持方文本\n反对方：未发现反证'
    assert out['ok'] is False
    assert out['confidence'] == '中', '反方"未发现反证" ⇒ 兜底置信度是中'


def test_step_retrieve_propagates_history_rewrite():
    """K7 的补全结果必须从分步接口带出来（界面要能说"我把它理解成了什么"）。"""
    out = M.step_retrieve('那它的优点呢？', NODES, EDGES,
                          history=[{'question': 'TransE 是什么'}])
    assert out['retrieval']['rewritten_from'] == 'TransE 是什么'
    assert out['retrieval']['question_used'].startswith('TransE 是什么')
    assert 'TransE' in out['retrieval']['seeds']


# ════════════════════════════════════════════════════════════════════════
# 全库问答（GraphRAG 式 Global Search）—— 2026-09-26
# ════════════════════════════════════════════════════════════════════════
#
# 覆盖四件事，每件对应一条**具体的失败模式**：
#
#   ① 检索**不依赖模型**（可 mock）—— 失败模式是"Ollama 没起 ⇒ 全库模式整个不能用"；
#   ② 找到相关社区 / 找不到时的行为 —— 失败模式是"假装精确检索到了"；
#   ③ 证据带**社区级**标识、且**没有** citations ——
#      失败模式是把"本地模型生成的概括"当成"论文原文证据"（本项目红线）；
#   ④ 社区版 prompt 与图版 prompt **分得开** —— 失败模式是沿用旧 prompt，
#      而旧 prompt 里写着"证据（每条都来自论文原文）"。
#
# 数据形状**刻意与 `load_community_summaries()` 的输出一致**（而不是直接读
# `community-summaries.json` 的原始记录）：这样测试不受 B6 产物格式细节影响，
# 而"读真实文件"另有单独一条测试盯着。

def _comm(cid, name, summary, members, n_nodes=100, n_edges=200):
    return {
        'id': cid, 'name': name, 'summary': summary, 'members': list(members),
        'n_nodes': n_nodes, 'n_edges': n_edges, 'top_types': {},
        'grounding_ok': True, 'says_no_theme': False, 'ok': True,
        'model': 'qwen3.5:9b', 'generated': '2026-09-19 19:39:39',
        'source': 'web/kg-data/community-summaries.json',
    }


COMMS = [
    _comm('comm-0', 'Triple', '这个社区围绕三元组展开。引用实体: Triple',
          ['ComplEx', 'DistMult', 'FB15k'], n_nodes=1200, n_edges=3200),
    _comm('comm-9', 'Embeddings', '这个社区围绕嵌入与逻辑规则展开。引用实体: Embeddings',
          ['TransE', 'RotatE'], n_nodes=613, n_edges=913),
    _comm('comm-13', 'Survey', '这个社区是综述类文献的集合。', ['Survey'], n_nodes=462),
]


def test_global_retrieve_hits_community_name(ollama):
    """社区名被点到 ⇒ 命中该社区。**全程零模型。**"""
    fake = ollama(None)
    out = M.step_retrieve_global('What does the Embeddings community cover?',
                                 communities=COMMS)
    assert out['retrieval']['mode'] == 'global'
    assert out['retrieval']['scope'] == 'focused'
    assert 'Embeddings' in out['retrieval']['seeds']
    assert fake.calls == [], '检索是纯规则，一次模型都不该调'


def test_global_retrieve_hits_member_entity(ollama):
    """成员实体被点到也算命中 —— 问题不必正好是社区名。"""
    ollama(None)
    out = M.step_retrieve_global('TransE 有什么局限？', communities=COMMS)
    assert out['retrieval']['scope'] == 'focused'
    names = [e['community']['name'] for e in out['evidence']]
    assert 'Embeddings' in names, f'TransE 是 Embeddings 的成员 ⇒ 应命中它：{names}'


def test_global_retrieve_does_not_depend_on_model_availability(monkeypatch):
    """**模型不可用不算故障**：把 `_ollama` 换成会抛的实现，检索照样出结果。"""
    def boom(*a, **kw):
        raise AssertionError('全库检索不该调用模型')
    monkeypatch.setattr(M, '_ollama', boom)
    out = M.step_retrieve_global('Embeddings', communities=COMMS)
    assert out['n_evidence'] >= 1


def test_global_retrieve_falls_back_to_all_scope_honestly(ollama):
    """中文全局问题（"这批文献整体在研究什么"）在英文标识词上必然 0 命中 ⇒
    `scope='all'`，并且 **reason 里必须说清这是全库综述、不是精确检索**。"""
    ollama(None)
    out = M.step_retrieve_global('这批文献整体在研究什么？', communities=COMMS)
    assert out['retrieval']['scope'] == 'all'
    assert out['n_evidence'] == len(COMMS)
    reason = out['retrieval']['reason']
    assert '全库综述' in reason and '不是精确检索' in reason, reason
    assert [e['score'] for e in out['evidence']] == [0.0] * len(COMMS)


def test_global_retrieve_respects_top_k(ollama):
    """`top_k` 必须真的生效 —— 否则"取最相关的几个"是一句空话。"""
    ollama(None)
    out = M.step_retrieve_global('Embeddings Triple Survey', communities=COMMS,
                                 top_k=1)
    assert out['n_evidence'] == 1


def test_global_retrieve_without_communities_reports_none(ollama):
    """**没有料就如实说找不到**，不许编。"""
    fake = ollama(None)
    out = M.step_retrieve_global('这批文献整体在研究什么？', communities=[])
    assert out['retrieval']['scope'] == 'none'
    assert out['n_evidence'] == 0 and out['evidence'] == []
    assert '没有可用的社区摘要' in out['retrieval']['reason']
    assert fake.calls == []


def test_global_reason_and_note_have_no_markdown_bold(ollama):
    """`reason`/`note` 会经 `textContent` 直接进 DOM ——
    `**粗体**` 会原样显示成星号（本项目栽过一次，18 处）。"""
    ollama(None)
    for q in ('Embeddings', '这批文献整体在研究什么？'):
        out = M.step_retrieve_global(q, communities=COMMS)
        assert '**' not in out['retrieval']['reason'], out['retrieval']['reason']
        assert '**' not in out['note'], out['note']


def test_global_evidence_is_marked_community_level(ollama):
    """★ 核心纪律：**证据必须自证"我是社区级的"**，且不许带原文引文。"""
    ollama(None)
    out = M.step_retrieve_global('Embeddings', communities=COMMS)
    assert out['n_evidence'] >= 1
    for it in out['evidence']:
        assert it['level'] == 'community'
        assert '社区' in it['level_label'] and '论文原文' in it['level_label']
        assert it['citations'] == [], '社区摘要**没有**原文引文，不许填东西进去'
        assert it['has_quote'] is False
        assert it['community']['summary'], '摘要正文必须在证据里（要能展示与回溯）'
        assert it['community']['members'], '成员实体的清单必须在（那是接地清单）'
    assert out['n_with_quote'] == 0


def test_global_evidence_keeps_summary_and_members_verbatim(ollama):
    """证据里的摘要与成员必须与源数据**逐字一致**（展示层不许二次加工）。"""
    ollama(None)
    out = M.step_retrieve_global('Embeddings', communities=COMMS)
    hit = [it for it in out['evidence'] if it['community']['name'] == 'Embeddings'][0]
    src = [c for c in COMMS if c['name'] == 'Embeddings'][0]
    assert hit['community']['summary'] == src['summary']
    assert hit['community']['members'] == src['members']
    assert hit['community']['model'] == 'qwen3.5:9b'


def test_global_support_and_oppose_use_community_prompts(ollama):
    """社区级证据 ⇒ 支持/反对必须换 prompt。

    旧 prompt 里写着「证据（每条都来自论文原文，已标注出处）」——
    拿它去读社区摘要，就是把**生成的概括**说成**原文证据**。
    """
    fake = ollama(_by_role)
    ev = M.community_evidence_pack(M.retrieve_communities('Embeddings', COMMS))
    sup = M.step_agents('q', ev, 'support')
    opp = M.step_agents('q', ev, 'oppose')
    assert sup['evidence_level'] == 'community'
    assert opp['evidence_level'] == 'community'
    for c in fake.calls:
        assert '社区级摘要' in c['prompt'], c['prompt'][:120]
        assert '每条都来自论文原文' not in c['prompt'], (
            '社区摘要不许沿用"每条都来自论文原文"那句')
    assert '社区' in sup['note'] and '社区' in opp['note']


def test_graph_evidence_keeps_paper_wording(ollama):
    """**单篇模式一字不变**：图证据仍走原 prompt（"每条都来自论文原文"）。"""
    fake = ollama(_by_role)
    ev = M.evidence_pack({'edges': EDGES})
    sup = M.step_agents('q', ev, 'support')
    assert sup['evidence_level'] == 'graph'
    assert '每条都来自论文原文' in fake.calls[0]['prompt']


def test_global_synth_prompt_says_community(ollama):
    """综合方看不到原始证据，但它的结论会写"依据是哪些" ——
    上游是社区级摘要，这一步就得说清，否则结论会顺着"论文原文"的口径写下去。"""
    fake = ollama('结论。置信度：中')
    out = M.step_synth('这批文献整体在研究什么？', '支持方说…', '未发现反证',
                       level='community')
    assert out['evidence_level'] == 'community'
    assert '社区级摘要' in fake.calls[0]['prompt']
    assert '不是论文原文' in fake.calls[0]['prompt']


def test_graph_synth_prompt_unchanged_by_default(ollama):
    """默认 `level='graph'` ⇒ 综合 prompt 里不许出现社区口径（旧行为不变）。"""
    fake = ollama('结论。置信度：中')
    out = M.step_synth('q', 'a', 'b')
    assert out['evidence_level'] == 'graph'
    assert '社区级摘要' not in fake.calls[0]['prompt']


def test_step_retrieve_global_shape_matches_single_mode(ollama):
    """形状与 `step_retrieve` 一致（前端复用同一套渲染），
    但 `n_nodes`/`n_edges` 恒为 0 —— 全库模式**没有子图**，不许拿社区数去凑。"""
    ollama(None)
    keys = set(M.step_retrieve('TransE', NODES, EDGES).keys())
    g = M.step_retrieve_global('Embeddings', communities=COMMS)
    assert keys <= set(g.keys()), f'缺字段：{keys - set(g.keys())}'
    assert g['retrieval']['n_nodes'] == 0 and g['retrieval']['n_edges'] == 0
    assert g['retrieval']['mode'] == 'global'
    assert g['stage'] == 'retrieve'
    assert g['n_evidence'] == len(g['evidence'])


def test_global_retrieve_result_is_reproducible(ollama):
    """同一问题两次跑，命中社区与顺序**完全一致**（同分时有稳定次序）。"""
    ollama(None)
    a = M.step_retrieve_global('Embeddings Triple Survey', communities=COMMS)
    b = M.step_retrieve_global('Embeddings Triple Survey', communities=COMMS)
    assert [e['community']['id'] for e in a['evidence']] == \
           [e['community']['id'] for e in b['evidence']]


def test_global_retrieve_propagates_history_rewrite(ollama):
    """K7：全库模式的追问补全与单篇同一条路径（`rewrite_question`）。"""
    ollama(None)
    out = M.step_retrieve_global('那它的优点呢？', communities=COMMS,
                                 history=[{'question': 'Embeddings 是什么'}])
    assert out['retrieval']['rewritten_from'] == 'Embeddings 是什么'


# ── 读真实产物（B6 的 community-summaries.json）──────────────────────────
def test_load_community_summaries_reads_the_real_b6_file():
    """★ 料在哪：`web/kg-data/community-summaries.json`，18 个社区各一段。

    **这条测试盯的是一个具体风险**：料换地方/换格式了，而全库模式"看起来还在跑"
    （因为它会退化成 `scope='none'` 或全部落空），没人会发现。
    """
    comms = M.load_community_summaries()
    assert len(comms) == 18, f'B6 产出应是 18 个社区，实际 {len(comms)}'
    c = comms[0]
    for k in ('id', 'name', 'summary', 'members', 'n_nodes', 'n_edges',
              'model', 'generated', 'source'):
        assert k in c, f'缺字段 {k}'
    assert c['id'].startswith('comm-')
    assert c['summary'] and c['members'], '摘要正文与成员清单都不能为空'
    assert all(x['ok'] for x in comms), '失败的记录不该被当成"这个社区没有内容"'


def test_community_catalog_is_lightweight_and_honest():
    """清单端点给的是**概览**：有 N 与模型名，但**不带摘要正文**（正文按需下发）。"""
    cat = M.community_catalog()
    assert cat['n'] == 18
    assert cat['model'] == 'qwen3.5:9b'
    assert cat['source'].endswith('community-summaries.json')
    assert len(cat['communities']) == 18
    for item in cat['communities']:
        assert set(item.keys()) == {'id', 'name', 'n_nodes', 'n_edges'}


def test_load_community_summaries_missing_file_returns_empty(tmp_path):
    """文件不存在 ⇒ `[]`（**不抛**）。调用方据此如实报"没有可用的社区摘要"。"""
    assert M.load_community_summaries(tmp_path / 'nope.json') == []


def test_load_community_summaries_skips_failed_records(tmp_path):
    """B6 里 `ok: False`（模型空输出）的记录**不许当成内容**喂给模型 ——
    那是"把失败当事实"。"""
    p = tmp_path / 'community-summaries.json'
    p.write_text(json.dumps({
        'model': 'qwen3.5:9b', 'generated': '2026-09-19 19:39:39',
        'communities': [
            {'id': 'comm-0', 'name': 'Triple', 'ok': True, 'summary': '有内容',
             'facts': {'core_ids': ['A'], 'n_nodes': 1, 'n_edges': 1}},
            {'id': 'comm-1', 'name': 'ACL', 'ok': False, 'error': '空 response'},
            {'id': 'comm-2', 'name': 'Empty', 'ok': True, 'summary': '   '},
        ],
    }, ensure_ascii=False), encoding='utf-8')
    out = M.load_community_summaries(p, use_cache=False)
    assert [c['id'] for c in out] == ['comm-0']


def test_community_evidence_pack_is_empty_without_hits():
    assert M.community_evidence_pack({'hits': []}) == []
    assert M.community_evidence_pack(None) == []
