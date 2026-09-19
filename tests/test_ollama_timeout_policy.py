# -*- coding: utf-8 -*-
"""本地 Ollama 的超时/重试策略回归测试。

背景（2026-09-19）：`call_ollama` / `call_ollama_json` 原来写死
`timeout=120` **且不重试**，而 `jsjkx2025-kg-rag-qa.pdf` 的 35 个 chunk 里
**14 个 ReadTimeout(120s)** —— 40% 的正文从未进入模型，图谱因此残缺
（95 节点/90 边，英文同类 500+）。

本文件把"对齐 `call_openai_compat` 已有策略"这件事钉成断言：
  · 默认 `timeout=300 / retries=2`（**不发明新数字**）；
  · `ReadTimeout` / `ConnectionError` 会退避重试（10s、20s…）；
  · 非瞬时 HTTP 错误（400）**立即抛**，重试只用于 429/5xx；
  · 重试耗尽仍抛出 —— 失败必须可见，不能被吞成"这段没实体"。
"""
import requests

from kg_extractor import (
    OLLAMA_RETRIES,
    OLLAMA_TIMEOUT,
    _ollama_generate,
    call_ollama_json,
)


class FakeResponse:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = {"response": "ok"} if payload is None else payload

    def raise_for_status(self):
        if self.status_code >= 400:
            err = requests.HTTPError(f"HTTP {self.status_code}")
            err.response = self
            raise err

    def json(self):
        return self._payload


def test_default_policy_matches_openai_compat():
    """300 / 2 —— 与本文件 `call_openai_compat` 已有策略一致。"""
    assert (OLLAMA_TIMEOUT, OLLAMA_RETRIES) == (300, 2)


def test_read_timeout_is_retried_then_succeeds(monkeypatch):
    calls, sleeps = [], []

    def fake_post(url, json=None, timeout=None):
        calls.append(timeout)
        if len(calls) < 3:
            raise requests.exceptions.ReadTimeout("slow chunk")
        return FakeResponse(payload={"response": "answer"})

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))

    assert _ollama_generate({"model": "m"}) == {"response": "answer"}
    assert calls == [300, 300, 300], "每次重试都必须带上 300s 超时"
    assert sleeps == [10, 20], "退避与 call_openai_compat 一致（10 * attempt）"


def test_retries_exhausted_raises(monkeypatch):
    def always_timeout(url, json=None, timeout=None):
        raise requests.exceptions.ReadTimeout("still slow")

    monkeypatch.setattr(requests, "post", always_timeout)
    monkeypatch.setattr("time.sleep", lambda s: None)

    try:
        _ollama_generate({"model": "m"})
    except requests.exceptions.ReadTimeout:
        return
    raise AssertionError("重试耗尽必须抛出，不能静默返回")


def test_connection_error_is_retried(monkeypatch):
    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append(1)
        if len(calls) == 1:
            raise requests.exceptions.ConnectionError("refused")
        return FakeResponse()

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr("time.sleep", lambda s: None)
    assert _ollama_generate({"model": "m"})["response"] == "ok"
    assert len(calls) == 2


def test_http_5xx_is_retried_but_4xx_is_not(monkeypatch):
    calls = []

    def server_error_once(url, json=None, timeout=None):
        calls.append(1)
        if len(calls) == 1:
            return FakeResponse(status=503)
        return FakeResponse()

    monkeypatch.setattr(requests, "post", server_error_once)
    monkeypatch.setattr("time.sleep", lambda s: None)
    assert _ollama_generate({"model": "m"})["response"] == "ok"
    assert len(calls) == 2

    def bad_request(url, json=None, timeout=None):
        calls.append(1)
        return FakeResponse(status=400)

    calls.clear()
    monkeypatch.setattr(requests, "post", bad_request)
    try:
        _ollama_generate({"model": "m"})
    except requests.HTTPError:
        pass
    else:
        raise AssertionError("400 不该重试，必须立即抛出")
    assert len(calls) == 1, "4xx 只允许请求一次"


def test_call_ollama_json_uses_the_same_timeout(monkeypatch):
    seen = {}

    def fake_post(url, json=None, timeout=None):
        seen['timeout'] = timeout
        return FakeResponse(payload={"response": '{"entities": []}'})

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr("time.sleep", lambda s: None)
    out = call_ollama_json("m", "prompt")
    assert seen['timeout'] == 300
    assert out == {"entities": []}


def test_empty_structured_response_still_raises(monkeypatch):
    """超时放宽**不许**顺手放宽"空响应"的判据（那是另一种静默失败）。"""
    monkeypatch.setattr(requests, "post",
                        lambda url, json=None, timeout=None: FakeResponse(
                            payload={"response": "   "}))
    try:
        call_ollama_json("m", "prompt")
    except ValueError as exc:
        assert "no structured response" in str(exc)
    else:
        raise AssertionError("空响应必须抛 ValueError")
