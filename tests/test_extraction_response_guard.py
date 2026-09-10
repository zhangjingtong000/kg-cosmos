"""抽取响应的结构校验回归测试。

背景（2026-09-10 实测）：云端网关在限流/预算耗尽时会返回 HTTP 200，但
choices[0].message.content 为空（或干脆只给 {"error": ...}）。旧代码把空正文
当作"抽取结果为空"继续向下游传播，于是 111 个 chunk 全部产出 0 实体、写出空
快照并被记为完成，同时静默烧掉配额。

本文件把每一种退化形态钉成显式失败，防止回归。
"""

import pytest

from kg_extractor import (
    DS_MAX_TOKENS,
    EmptyExtractionResponse,
    _extract_message_content,
    _parse_extra_headers,
)


def test_normal_content_is_returned():
    body = {
        "choices": [
            {"finish_reason": "stop", "message": {"content": '{"entities": []}'}}
        ]
    }
    assert _extract_message_content(body) == '{"entities": []}'


def test_truncated_content_is_still_returned_for_repair():
    """finish_reason=length 且正文非空：交给最长平衡前缀修复，不能丢结果。"""
    body = {
        "choices": [
            {
                "finish_reason": "length",
                "message": {"content": '{"entities": [{"name": "Trans'},
            }
        ]
    }
    assert _extract_message_content(body).startswith('{"entities"')


def test_gateway_error_body_with_http_200_raises():
    """限流降级：200 + {"error": ...}，没有任何 choices。"""
    body = {"error": {"message": "You've reached your weekly usage limit",
                      "type": "rate_limit_error"}}
    with pytest.raises(EmptyExtractionResponse, match="error 字段"):
        _extract_message_content(body)


def test_missing_choices_raises():
    with pytest.raises(EmptyExtractionResponse, match="choices"):
        _extract_message_content({"id": "x", "object": "chat.completion"})


def test_empty_choices_list_raises():
    with pytest.raises(EmptyExtractionResponse, match="choices"):
        _extract_message_content({"choices": []})


def test_missing_message_raises():
    with pytest.raises(EmptyExtractionResponse, match="message"):
        _extract_message_content({"choices": [{"finish_reason": "stop"}]})


def test_reasoning_only_response_raises_with_budget_hint():
    """真实故障：V4-Flash 把整个输出预算烧在思维链上，正文为空。"""
    body = {
        "choices": [
            {
                "finish_reason": "length",
                "message": {"content": "", "reasoning_content": "We need JSON only..."},
            }
        ]
    }
    with pytest.raises(EmptyExtractionResponse, match="思维链占满预算"):
        _extract_message_content(body)


def test_content_and_reasoning_both_empty_raises():
    body = {"choices": [{"finish_reason": "stop", "message": {"content": "   "}}]}
    with pytest.raises(EmptyExtractionResponse, match="均为空"):
        _extract_message_content(body)


def test_null_content_raises():
    body = {"choices": [{"finish_reason": "stop", "message": {"content": None}}]}
    with pytest.raises(EmptyExtractionResponse):
        _extract_message_content(body)


def test_non_dict_body_raises():
    with pytest.raises(EmptyExtractionResponse, match="JSON 对象"):
        _extract_message_content(["not", "a", "dict"])


def test_budget_is_large_enough_for_reasoning_models():
    """回归护栏：预算必须显著高于旧值 2048，否则推理模型会交不出正文。"""
    assert DS_MAX_TOKENS >= 4096


def test_extra_headers_parsing():
    parsed = _parse_extra_headers("x-opencode-session:ses_abc, Authorization : Bearer x")
    assert parsed == {"x-opencode-session": "ses_abc", "Authorization": "Bearer x"}


def test_extra_headers_tolerates_garbage():
    assert _parse_extra_headers("") == {}
    assert _parse_extra_headers("no-colon-here") == {}
    assert _parse_extra_headers(":empty-key") == {}
