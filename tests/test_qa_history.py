# -*- coding: utf-8 -*-
r"""K7 多轮追问：问题重写 + 端点行为。

## 这个功能解决什么

`retrieve()` 是**字面种子匹配**。而追问常常没有主语：

    「那它的缺点呢？」   ← 「它」指谁？匹配不到任何实体，检索 0 种子空转

**修法不是"把历史塞进检索"**（那会让检索被第 1 轮的主题主导，历史越长越糟），
而是**先把追问补全成可独立检索的一句话，再走同一条检索链**。
检索算法本身一行未改。

## 为什么值得回归

**症状是"看起来很安静"**：追问后界面正常显示、结论也生成了，
只是**检索到了空子集 ⇒ 结论没有证据**。不数种子数就发现不了。

实测对照（同一个问题）：
    带历史   → 8 个种子
    不带历史 → **0 个种子**
"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))

os.environ.setdefault('KG_BACKEND', 'memory')

from multi_agent_qa import rewrite_question  # noqa: E402


# ── ① 纯规则的问题重写 ────────────────────────────────────────────────────
#    (问题, 历史, 是否应当补全)
CASES = [
    ('那它的缺点呢？', [{'question': 'TransE 是什么'}], True),
    ('这个方法的局限是什么？', [{'question': 'TransE 是什么'}], True),
    ('它呢？', [{'question': 'TransE 是什么'}, {'question': 'RotatE 呢'}], True),
    # 长问题通常自带主语 —— 不该拼（误拼比不拼更糟）
    ('TransE 和 RotatE 有什么区别？', [{'question': 'TransE 是什么'}], False),
    # 没有指代词
    ('知识图谱的质量怎么评估？', [{'question': 'TransE 是什么'}], False),
    # 没有历史
    ('那它的缺点呢？', [], False),
    # 历史里只有一条空问题（不该拼一个空串）
    ('它的缺点呢？', [{'question': ''}], False),
]


@pytest.mark.parametrize('q,hist,should', CASES)
def test_rewrite_question(q, hist, should):
    out, src = rewrite_question(q, hist)
    got = src is not None
    assert got == should, (
        f'{q!r} 带历史 {hist} ⇒ 期望{"补全" if should else "原样"}，'
        f'实际 {out!r}（来源 {src!r}）')
    if should:
        # 补全后必须**能反查到用了哪条历史** —— 界面要据此告诉用户
        assert src in out, f'补全结果 {out!r} 里看不到来源 {src!r}'
    else:
        assert out == q.strip(), f'不该补全，却改成了 {out!r}'


def test_rewrite_picks_latest_relevant():
    """两轮历史时，应当用**最近一轮**，而不是最早那条。"""
    hist = [{'question': '最早的这个问题是什么'}, {'question': 'RotatE 呢'}]
    out, src = rewrite_question('它呢？', hist)
    assert src == 'RotatE 呢', f'应取最近一轮，实际取了 {src!r}'


# ── ② 端点：带历史 vs 不带（这是"真的有用"的证据）────────────────────────
@pytest.fixture(scope='module')
def client():
    from fastapi.testclient import TestClient
    import app as appmod
    return TestClient(appmod.app, raise_server_exceptions=False)


GID = 'ad690dfb'          # 磁盘上有快照的那篇（与 /chat 的测试同一个）
FOLLOWUP = '那它的缺点呢？'


def _seeds(client, question, history=None):
    body = {'graph_id': GID, 'question': question, 'step': 'retrieve'}
    if history:
        body['history'] = history
    r = client.post('/api/qa/step', json=body)
    assert r.status_code == 200, f'端点返回 {r.status_code}'
    return r.json()['retrieval']


def test_followup_without_history_has_no_seed(client):
    """不带历史时，无主语的追问**匹配不到种子** —— 这就是要修的问题本身。"""
    ri = _seeds(client, FOLLOWUP)
    assert ri['seeds'] == [], (
        f'预期"无主语追问"检索不到种子，实际有 {len(ri["seeds"])} 个：{ri["seeds"][:4]}')
    assert not ri.get('rewritten_from')


def test_followup_with_history_recovers_seeds(client):
    """带上历史后，追问能拿到与"完整问题"相当数量的种子。"""
    hist = [{'question': 'TransE 是什么', 'answer': '（略）'}]
    ri = _seeds(client, FOLLOWUP, history=hist)
    assert ri.get('rewritten_from') == 'TransE 是什么'
    assert ri['question_used'].startswith('TransE 是什么')
    assert len(ri['seeds']) > 0, '带历史后仍检索不到种子 —— 补全没生效'


def test_complete_question_is_untouched(client):
    """本身完整的问题不该被历史改动（否则会污染检索）。"""
    hist = [{'question': '完全无关的另一轮问题'}]
    ri = _seeds(client, 'TransE 和 RotatE 有什么区别？', history=hist)
    assert not ri.get('rewritten_from'), '完整问题被错误地补全了'
    assert ri['question_used'] == 'TransE 和 RotatE 有什么区别？'
