# -*- coding: utf-8 -*-
r"""全库问答的端点测试：`POST /api/qa/global/step` 与 `GET /api/communities`。

## 为什么单独一个文件

`tests/test_multi_agent_qa.py` 覆盖的是**算法层**（检索、证据形状、prompt 口径）；
这里覆盖**接线层**：端点有没有把模式接对、缺 `graph_id` 会不会误伤单篇模式、
超参与未知 step 的行为、以及"社区摘要的料到底有没有下发"。

## 两条刻意的做法

  · **模型调用一律 mock**（patch `multi_agent_qa._ollama`）⇒ 测试不依赖 Ollama；
  · **同时断言单篇端点没被改动** —— 新增一个模式最容易伤到的就是旧模式，
    而旧模式有 44 项测试盯着，这里再钉一条"响应里不许出现 `mode: global`"。
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))

os.environ.setdefault('KG_BACKEND', 'memory')

GID = 'qaglobalgid'

GRAPH = {
    'id': GID,
    'nodes': [{'name': 'TransE', 'id': 'TransE',
               'description': 'translation based embedding'},
              {'name': 'Knowledge Graph', 'id': 'Knowledge Graph',
               'description': 'a graph of entities'}],
    'edges': [{'source': 'TransE', 'target': 'Knowledge Graph', 'type': 'USED_FOR',
               'evidence': [{'source_id': 'sid', 'source_name': 'paper.pdf',
                             'page': 1, 'text': 'TransE maps entities.'}]}],
    'stats': {}, 'created_at': '2026-09-26',
}

# 一个**必然命中**社区标识词的问题（社区名 `Embeddings` 就在问题里）
GLOBAL_Q = 'What does the Embeddings community cover?'


class FakeOllama:
    """假的 `_ollama`：记录调用，**绝不联网**。"""

    def __init__(self, reply=None):
        self.calls = []
        self.reply = reply

    def __call__(self, prompt, timeout=180, temperature=0.2, num_predict=600):
        self.calls.append({'prompt': prompt, 'timeout': timeout,
                           'temperature': temperature, 'num_predict': num_predict})
        return self.reply(prompt) if callable(self.reply) else self.reply


@pytest.fixture(scope='module')
def appmod():
    import app
    return app


@pytest.fixture()
def client(appmod, monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setitem(appmod.graphs, GID, GRAPH)
    return TestClient(appmod.app, raise_server_exceptions=False)


@pytest.fixture()
def ollama(monkeypatch):
    import multi_agent_qa as M

    def _install(reply=None):
        fake = FakeOllama(reply)
        monkeypatch.setattr(M, '_ollama', fake)
        return fake
    return _install


# ── 全库端点：检索 ───────────────────────────────────────────────────────
def test_global_retrieve_needs_no_graph_id(client, ollama):
    """★ 全库模式**不需要 graph_id** —— 这就是它存在的理由（见 QAGlobalStepRequest）。"""
    ollama(None)
    r = client.post('/api/qa/global/step',
                    json={'question': GLOBAL_Q, 'step': 'retrieve'})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j['stage'] == 'retrieve'
    assert j['retrieval']['mode'] == 'global'
    assert j['n_evidence'] >= 1, '这个问题必然命中 Embeddings 社区'
    assert j['n_with_quote'] == 0
    for it in j['evidence']:
        assert it['level'] == 'community'
        assert it['citations'] == []
        assert it['community']['summary']


def test_global_retrieve_does_not_call_model(client, ollama):
    """检索零模型 —— 端点这一层也要守住（Ollama 没起时全库检索照样可用）。"""
    fake = ollama(None)
    r = client.post('/api/qa/global/step',
                    json={'question': GLOBAL_Q, 'step': 'retrieve'})
    assert r.status_code == 200
    assert fake.calls == []


def test_global_retrieve_survives_model_being_down(appmod, client, monkeypatch):
    """把 `_ollama` 换成会抛的实现：检索**不受影响**（模型只用在后三步）。"""
    import multi_agent_qa as M

    def boom(*a, **kw):
        raise AssertionError('全库检索不该调用模型')
    monkeypatch.setattr(M, '_ollama', boom)
    r = client.post('/api/qa/global/step',
                    json={'question': GLOBAL_Q, 'step': 'retrieve'})
    assert r.status_code == 200 and r.json()['n_evidence'] >= 1


def test_global_question_with_no_community_hit_reports_all_scope(client, ollama):
    """中文全局问题在英文标识词上 0 命中 ⇒ `scope='all'`（全库综述），
    并且**如实写在 reason 里**（不许假装精确检索到了）。"""
    ollama(None)
    r = client.post('/api/qa/global/step',
                    json={'question': '这批文献整体在研究什么？', 'step': 'retrieve'})
    j = r.json()
    assert j['retrieval']['scope'] == 'all'
    assert '全库综述' in j['retrieval']['reason']
    assert j['n_evidence'] >= 1


def test_global_top_k_is_bounded(client):
    """`top_k` 有上限（8）：每条摘要几百字，取太多会把 prompt 撑过本地模型上下文，
    而"超上下文被静默截断"是最危险的失败方式。"""
    assert client.post('/api/qa/global/step',
                       json={'question': GLOBAL_Q, 'top_k': 99}).status_code == 422
    assert client.post('/api/qa/global/step',
                       json={'question': GLOBAL_Q, 'top_k': 0}).status_code == 422
    assert client.post('/api/qa/global/step',
                       json={'question': GLOBAL_Q, 'top_k': 2}).status_code == 200


def test_global_question_length_limit(client):
    r = client.post('/api/qa/global/step',
                    json={'question': 'x' * 501, 'step': 'retrieve'})
    assert r.status_code == 422


def test_global_unknown_step_is_400(client):
    r = client.post('/api/qa/global/step',
                    json={'question': GLOBAL_Q, 'step': 'nope'})
    assert r.status_code == 400
    assert '未知的 step' in r.text


# ── 全库端点：②③④ 三步用社区口径 ────────────────────────────────────────
def test_global_support_and_oppose_use_community_prompts(client, ollama):
    """证据带 `level='community'` ⇒ 端点转发到 `step_agents` 时必须走社区版 prompt。"""
    fake = ollama('支持方的意见。')
    r1 = client.post('/api/qa/global/step',
                     json={'question': GLOBAL_Q, 'step': 'retrieve'})
    ev = r1.json()['evidence']
    r2 = client.post('/api/qa/global/step',
                     json={'question': GLOBAL_Q, 'step': 'support', 'evidence': ev})
    r3 = client.post('/api/qa/global/step',
                     json={'question': GLOBAL_Q, 'step': 'oppose', 'evidence': ev})
    assert r2.status_code == 200 and r3.status_code == 200
    assert r2.json()['evidence_level'] == 'community'
    assert r3.json()['evidence_level'] == 'community'
    for c in fake.calls:
        assert '社区级摘要' in c['prompt']
        assert '每条都来自论文原文' not in c['prompt']


def test_global_synth_uses_community_prompt(client, ollama):
    """综合步在**全库端点**上默认按社区口径（`level='community'`）。"""
    fake = ollama('结论。置信度：中')
    r = client.post('/api/qa/global/step',
                    json={'question': GLOBAL_Q, 'step': 'synth',
                          'support': '支持方', 'oppose': '未发现反证'})
    assert r.status_code == 200
    assert r.json()['evidence_level'] == 'community'
    assert '社区级摘要' in fake.calls[0]['prompt']
    assert r.json()['confidence'] == '中'


# ── 单篇端点：一个字都不许变 ─────────────────────────────────────────────
def test_single_mode_endpoint_still_requires_graph_id(client):
    """★ 旧行为回归：`/api/qa/step` 的 `graph_id` **仍然必填**（缺 ⇒ 422）。

    这一条是"新功能没有把旧模式改成可选"的形式化断言。
    """
    r = client.post('/api/qa/step', json={'question': GLOBAL_Q, 'step': 'retrieve'})
    assert r.status_code == 422


def test_single_mode_retrieve_response_has_no_global_mode(client, ollama):
    """★ 旧行为回归：单篇检索的响应里**不许出现** `mode: global` 之类的模式标记
    —— 那会让前端把它当全库结果渲染。"""
    ollama(None)
    r = client.post('/api/qa/step',
                    json={'graph_id': GID, 'question': 'What is TransE used for?',
                          'step': 'retrieve'})
    assert r.status_code == 200, r.text
    j = r.json()
    assert 'mode' not in j['retrieval']
    assert j['evidence'], '这条问题在图上是命中的'
    assert all(it.get('level') != 'community' for it in j['evidence'])
    assert j['n_with_quote'] >= 1, '单篇证据带论文原文引文'


def test_single_mode_synth_prompt_still_says_paper(client, ollama):
    """单篇端点的综合步仍用图版 prompt（默认 level='graph'）。"""
    fake = ollama('结论。置信度：中')
    r = client.post('/api/qa/step',
                    json={'graph_id': GID, 'question': 'q', 'step': 'synth',
                          'support': 'a', 'oppose': 'b'})
    assert r.status_code == 200
    assert r.json()['evidence_level'] == 'graph'
    assert '社区级摘要' not in fake.calls[0]['prompt']


# ── 社区摘要清单端点 ─────────────────────────────────────────────────────
def test_communities_catalog_endpoint(client):
    """`GET /api/communities`：界面用它说"全库 = N 条社区摘要（谁生成的）"。

    **清单里不带摘要正文**（正文随检索结果按需下发，避免一次多传几十 KB）。"""
    r = client.get('/api/communities')
    assert r.status_code == 200, r.text
    j = r.json()
    assert j['n'] == 18
    assert j['model'] == 'qwen3.5:9b'
    assert j['source'].endswith('community-summaries.json')
    assert len(j['communities']) == 18
    for item in j['communities']:
        assert set(item.keys()) == {'id', 'name', 'n_nodes', 'n_edges'}


def test_community_summaries_raw_file_is_still_served(client):
    """B6 的产物本身仍可从数据面取到（`/api/kg/{name}`）——
    全库模式不是唯一入口，社区星云页那条路不受影响。"""
    r = client.get('/api/kg/community-summaries.json')
    assert r.status_code == 200
    assert r.json()['n_ok'] == 18
