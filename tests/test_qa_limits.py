# -*- coding: utf-8 -*-
r"""G3 + G4：问答的输入边界与错误分流。

## G4 · 超长问题

此前 `QARequest.question` **没有任何长度上限** —— 一个几万字符的问题
会一路拼进 prompt，轻则变慢、重则撑爆上下文。
现在后端 `Field(max_length=500)`、前端在 `askReal` 内部截断。

**为什么两端都要**：只做后端 ⇒ 用户先看到"请求被拒绝"，
而那个问题明明可以问（只是太长）；只做前端 ⇒ 绕过界面直接调 API 仍能打进来。

## G3 · 错误分流（**这条比 G4 重要**）

原实现把所有异常都归成"后端不可用"，然后 `run(QA.transe)` ——
于是「**这张图不存在**」（HTTP 404）也显示**内置的 TransE 演示问答**。
**用户看到的是"我这个问题答出来了"，而实际是别人的数据。**

前端据此分三支：404 明确报错且**不显示演示数据** / 400 说请求的问题 /
网络类才降级。
**这里测的是后端给出的"可分流信号"**：404 必须是 404、422 必须是 422 ——
**如果后端把 404 吞成 200 空结果，前端的任何分流都无从做起。**
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))

os.environ.setdefault('KG_BACKEND', 'memory')

GID = 'ad690dfb'          # 磁盘上有快照的那篇


@pytest.fixture(scope='module')
def client():
    from fastapi.testclient import TestClient
    import app as appmod
    return TestClient(appmod.app, raise_server_exceptions=False)


def test_long_question_is_rejected_with_422(client):
    """G4：超长问题必须被**校验层**拒绝（422），而不是一路带进检索。"""
    r = client.post('/api/qa/step', json={
        'graph_id': GID, 'question': 'x' * 600, 'step': 'retrieve'})
    assert r.status_code == 422, (
        f'超长问题应被 pydantic 拒（422），实际 {r.status_code}：{r.text[:200]}')


def test_question_at_the_limit_is_accepted(client):
    """边界值本身要能用（500 字符）—— 否则限制就成了"用不了"而不是"别太长"。"""
    r = client.post('/api/qa/step', json={
        'graph_id': GID, 'question': 'a' * 500, 'step': 'retrieve'})
    assert r.status_code == 200, f'恰好 500 字符应被接受，实际 {r.status_code}'


def test_normal_question_still_works(client):
    r = client.post('/api/qa/step', json={
        'graph_id': GID, 'question': 'TransE 是什么', 'step': 'retrieve'})
    assert r.status_code == 200
    assert r.json()['retrieval']['seeds'], '正常问题应当检索到种子'


def test_unknown_graph_id_is_404_not_silent_empty(client):
    """G3：**不存在的 gid 必须报 404**。

    如果后端返回 200 + 空结果，前端就无从区分"图不存在"与"图里没这个实体"，
    也就没法做"404 时不显示演示数据"这件事 ——
    **前端的错误分流依赖后端给出可分流的信号。**
    """
    r = client.post('/api/qa/step', json={
        'graph_id': 'nosuchgid_zzz', 'question': 'TransE', 'step': 'retrieve'})
    assert r.status_code == 404, (
        f'不存在的 gid 应返回 404（前端据此不显示演示数据），'
        f'实际 {r.status_code}：{r.text[:200]}')


def test_unknown_graph_id_on_full_qa_is_404(client):
    """`/api/qa` 走的是同一个 `_ensure_graph`，行为必须一致。"""
    r = client.post('/api/qa', json={'graph_id': 'nosuchgid_zzz', 'question': 'TransE',
                                     'use_llm': False})
    assert r.status_code == 404, (
        f'/api/qa 对不存在的 gid 也应 404，实际 {r.status_code}')
