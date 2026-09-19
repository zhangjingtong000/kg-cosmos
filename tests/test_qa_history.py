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

⚠️ **2026-09-19 补记**：上面那行"不带历史 → 0 个种子"只说**规则路径**。
2026-09-18 加了「跨语言桥接」之后，纯中文问题会在规则落空时
**从问题本身**（与历史无关）调一次本地模型转英文检索词 ——
模型可用时同一个「那它的缺点呢？」能桥出种子（实测 12 个）。
⇒ **"没有历史" ≠ "检索不到种子"**，本文件据此改成
"钉补全行为（不依赖模型）+ 规则路径单独钉 0 种子（把模型显式关掉）"。
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


def _fake_ollama(monkeypatch, reply=None):
    """把 `multi_agent_qa._ollama` 换成假实现（**绝不联网**），返回调用记录表。

    与 `test_multi_agent_qa.py` 同一个口子：该模块所有模型调用都走 `_ollama`，
    所以这样才能让这些端点测试**不依赖本机有没有跑 Ollama**。
    """
    import multi_agent_qa as M
    calls = []

    def fake(prompt, timeout=180, temperature=0.2, num_predict=600):
        calls.append(prompt)
        return reply

    monkeypatch.setattr(M, '_ollama', fake)
    return calls


def test_followup_without_history_has_no_seed(client, monkeypatch):
    """不带历史时：追问**不被补全**，且**规则路径**下 0 种子。

    ## 这个测试原本想验证什么（判断见下，比改动本身重要）

    它原来的标题/断言语义是「没有历史 ⇒ 检索不到种子」，但真正要钉的
    其实是 K7 的核心行为：**没有历史，补全就不该发生**（不凭空造上下文）。

    ## 原断言为什么会红

    2026-09-18 加了跨语言桥接：图谱实体名全是英文，纯中文问题在词面匹配上
    必然 0 种子，于是 `retrieve()` 会**从问题本身**（与历史无关）调一次本地模型
    把问题转成英文检索词。模型可用时「那它的缺点呢？」能桥出 12 个种子
    ⇒ `assert ri['seeds'] == []` 报红；模型不可用/超时/慢时又变绿。
    **红不红取决于本机模型服务** ⇒ 不稳定测试；而且它的语义前提
    （没有历史 ⇒ 检索不到东西）在加桥接之后**已经失效**。

    ## 改成了什么

    分两层钉：
      · 补全层（**不依赖模型**）：`rewritten_from is None`、检索输入就是原问题；
      · 规则检索层：把模型显式关掉（桥接落空）后，字面匹配仍是 0 种子。
    """
    _fake_ollama(monkeypatch, reply=None)      # 桥接拿不到英文词 ⇒ 退化为纯规则
    ri = _seeds(client, FOLLOWUP)
    assert not ri.get('rewritten_from'), (
        '没有历史却被"补全"了 —— 这是凭空造上下文，K7 明令禁止')
    assert ri['question_used'] == FOLLOWUP, (
        f'没有历史时检索输入应当就是原问题，实际 {ri["question_used"]!r}')
    assert ri['seeds'] == [], (
        f'规则路径（桥接落空）下"无主语追问"应当 0 种子，'
        f'实际有 {len(ri["seeds"])} 个：{ri["seeds"][:4]}')


def test_followup_without_history_is_never_rewritten_even_if_bridge_hits(
        client, monkeypatch):
    """**桥接命中也不等于"补全"**：种子可以来自问题本身，但绝不会来自历史。

    这条把 2026-09-18 之后的新语义钉住：没有历史时 `rewritten_from` 必须
    仍是 `None`（与有没有模型、桥接命不命中无关）；同时桥接确实能给出种子 ——
    「没有历史 ≠ 检索不到东西」。
    """
    _fake_ollama(monkeypatch, reply='TransE knowledge graph')
    ri = _seeds(client, FOLLOWUP)
    assert not ri.get('rewritten_from'), '桥接不是补全：不该记 rewritten_from'
    assert ri['question_used'] == FOLLOWUP
    assert ri['seeds'], '桥接命中了英文检索词，这里应当有种子（否则该用例失去意义）'


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
