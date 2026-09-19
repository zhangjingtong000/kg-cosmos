# -*- coding: utf-8 -*-
r"""N17：`/api/qa` 与 `/api/qa/step` —— **项目主看点的端点此前 0 处自动化验证**。

## 为什么单独一个文件

N17 记的是：「`api/qa` 在 31 项检查与 85 个 pytest 中 **0 处**命中」。
`test_qa_limits.py` / `test_qa_history.py` 只覆盖了 404 / 超长 / 追问补全，
**正常路径、四个 step 的行为、降级路径**都没有。
⇒ 被问「你怎么保证问答端点是对的」时拿不出东西。

## 两条做法上的取舍

  · **图由测试自己注入**（`monkeypatch.setitem(app.graphs, GID, ...)`），
    不读磁盘快照：`_ensure_graph` 是"内存优先"，注入即可完全控制输入，
    既不受语料变化影响，也不会因为别的测试先跑过而改变结果。
    （磁盘快照那条真路径另有一条单独测试，见文件末尾。）
  · **模型调用一律 mock**：端点内部 `import multi_agent_qa`，
    patch 的是同一个模块对象上的 `_ollama` ⇒ 测试**不依赖 Ollama 是否在跑**。

## ⚠️ 已知但本次不改的 N4

`POST /upload` 默认 `mode="cloud"`，而云端已禁用 —— 本文件不碰它，
所以在测试里也不去调 `/upload`（否则会真发云端请求）。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))

import os  # noqa: E402
os.environ.setdefault('KG_BACKEND', 'memory')

GID = 'n17gid'
DISK_GID = 'ad690dfb'
DISK_GRAPH = ROOT / 'data' / DISK_GID / 'graph.json'

GRAPH = {
    'id': GID,
    'nodes': [{'name': 'TransE', 'id': 'TransE',
               'description': 'translation based embedding'},
              {'name': 'Knowledge Graph', 'id': 'Knowledge Graph',
               'description': 'a graph of entities'}],
    'edges': [{'source': 'TransE', 'target': 'Knowledge Graph', 'type': 'USED_FOR',
               'evidence': [{'source_id': 'sid', 'source_name': 'paper.pdf',
                             'page': 1, 'text': 'TransE maps entities.'}]}],
    'stats': {}, 'created_at': '2026-09-21',
}

QUESTION = 'What is TransE used for?'


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


def _by_role(prompt):
    if '你是**支持方**' in prompt:
        return '支持：证据 1'
    if '你是**反对方**' in prompt:
        return '未发现反证'
    if '你是**综合**' in prompt:
        return '结论：TransE 被广泛使用。置信度为高。'
    return None


# ── ① 错误分流：不存在的图必须 404，而不是静默降级 ───────────────────────
def test_qa_unknown_graph_is_404(client):
    r = client.post('/api/qa', json={'graph_id': 'nosuch_n17', 'question': 'x'})
    assert r.status_code == 404, (
        f'不存在的图必须 404（前端据此不显示演示数据），实际 {r.status_code}')


def test_qa_step_unknown_graph_is_404(client):
    r = client.post('/api/qa/step', json={'graph_id': 'nosuch_n17',
                                          'question': 'x', 'step': 'retrieve'})
    assert r.status_code == 404, f'实际 {r.status_code}: {r.text[:200]}'


# ── ② 输入校验 ──────────────────────────────────────────────────────────
def test_question_over_500_chars_is_422(client):
    r = client.post('/api/qa', json={'graph_id': GID, 'question': 'x' * 501})
    assert r.status_code == 422, f'超长问题必须在校验层被拒：{r.status_code}'


def test_question_at_the_limit_is_accepted(client, ollama):
    ollama(None)
    r = client.post('/api/qa', json={'graph_id': GID, 'question': 'a' * 500})
    assert r.status_code == 200, f'恰好 500 字符要能用：{r.status_code}'


def test_invalid_step_is_400_and_names_the_valid_ones(client):
    r = client.post('/api/qa/step', json={'graph_id': GID, 'question': 'q',
                                          'step': 'bogus'})
    assert r.status_code == 400, f'非法 step 应当是 400，实际 {r.status_code}'
    detail = str(r.json().get('detail', ''))
    for name in ('retrieve', 'support', 'oppose', 'synth'):
        assert name in detail, f'报错要告诉调用方合法取值：{detail}'


# ── ③ 正常路径：四步各自的行为 ───────────────────────────────────────────
def test_step_retrieve_is_pure_rules(client, ollama):
    fake = ollama(None)
    r = client.post('/api/qa/step', json={'graph_id': GID, 'question': QUESTION,
                                          'step': 'retrieve'})
    assert r.status_code == 200, r.text[:200]
    j = r.json()
    assert j['stage'] == 'retrieve' and j['retrieval']['seeds'] == ['TransE']
    assert j['n_evidence'] == 1 and j['n_with_quote'] == 1
    assert '未调用模型' in j['note'], f'{j["note"]}'
    assert fake.calls == [], 'retrieve 这一步不该调模型'


def test_step_support_uses_the_posted_evidence(client, ollama):
    fake = ollama('支持：证据 1 说明……')
    ev = [{'claim': 'TransE —[USED_FOR]→ Knowledge Graph', 'has_quote': True,
           'citations': [{'paper': 'paper.pdf', 'page': 1, 'quote': 'TransE maps entities.'}]}]
    r = client.post('/api/qa/step', json={'graph_id': GID, 'question': QUESTION,
                                          'step': 'support', 'evidence': ev})
    assert r.status_code == 200, r.text[:200]
    assert r.json()['role'] == '支持方' and r.json()['ok'] is True
    assert 'TransE —[USED_FOR]→ Knowledge Graph' in fake.calls[0]['prompt'], (
        '证据必须由前端回传并进 prompt（后端不存 session）')


def test_step_oppose_is_a_separate_call_from_support(client, ollama):
    fake = ollama(_by_role)
    ev = [{'claim': 'TransE —[USED_FOR]→ Knowledge Graph', 'has_quote': True,
           'citations': []}]
    client.post('/api/qa/step', json={'graph_id': GID, 'question': QUESTION,
                                      'step': 'support', 'evidence': ev})
    client.post('/api/qa/step', json={'graph_id': GID, 'question': QUESTION,
                                      'step': 'oppose', 'evidence': ev})
    assert len(fake.calls) == 2, '"独立取证"必须是两次请求、两次调用'
    assert '你是**支持方**' in fake.calls[0]['prompt']
    assert '你是**反对方**' in fake.calls[1]['prompt']


def test_step_synth_only_sees_both_sides(client, ollama):
    fake = ollama('结论：……置信度：中')
    r = client.post('/api/qa/step', json={'graph_id': GID, 'question': QUESTION,
                                          'step': 'synth',
                                          'support': '支持方文本', 'oppose': '未发现反证'})
    assert r.status_code == 200, r.text[:200]
    assert r.json()['confidence'] == '中'
    p = fake.calls[0]['prompt']
    assert '支持方文本' in p and '未发现反证' in p


# ── ④ 一次性问答：正常路径 + 降级 ────────────────────────────────────────
def test_full_qa_returns_agents_and_confidence(client, ollama):
    fake = ollama(_by_role)
    r = client.post('/api/qa', json={'graph_id': GID, 'question': QUESTION})
    assert r.status_code == 200, r.text[:200]
    j = r.json()
    assert j['degraded'] is False, '模型可用时不该标降级'
    assert [a['role'] for a in j['agents']] == ['支持方', '反对方'], j['agents']
    assert j['confidence'] == '高', j['confidence']
    assert j['conclusion'].startswith('结论：')
    assert len(fake.calls) == 3, f'支持/反对/综合各一次：{len(fake.calls)}'


def test_full_qa_degrades_instead_of_failing(client, ollama):
    """模型不可用必须是 **200 + degraded**，不是 500 —— 检索结果仍然有价值。"""
    ollama(None)
    r = client.post('/api/qa', json={'graph_id': GID, 'question': QUESTION})
    assert r.status_code == 200, f'降级路径不该报错：{r.status_code} {r.text[:200]}'
    j = r.json()
    assert j['degraded'] is True and j['agents'] == []
    assert '本地模型不可用' in j['conclusion']
    assert j['evidence'], '降级后检索到的证据仍要返回'


def test_full_qa_without_llm_never_calls_the_model(client, ollama):
    fake = ollama('不该被调用')
    r = client.post('/api/qa', json={'graph_id': GID, 'question': QUESTION,
                                     'use_llm': False})
    assert r.status_code == 200
    assert r.json()['degraded'] is True
    assert fake.calls == [], 'use_llm=False 时一次模型调用都不许有'


# ── ⑤ 磁盘快照这条真路径（后端重启后内存空，靠它兜底）──────────────────
@pytest.mark.skipif(not DISK_GRAPH.exists(), reason=f'缺快照 {DISK_GRAPH}')
def test_step_retrieve_falls_back_to_disk_snapshot(client, ollama):
    ollama(None)
    r = client.post('/api/qa/step', json={'graph_id': DISK_GID, 'question': 'TransE',
                                          'step': 'retrieve'})
    assert r.status_code == 200, f'内存没有时必须回落到 data/<id>/graph.json：{r.text[:200]}'
    j = r.json()
    assert set(j['retrieval']) >= {'seeds', 'reason', 'question_used', 'rewritten_from'}
