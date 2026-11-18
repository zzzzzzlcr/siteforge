"""Anthropic 原生协议适配层：消息/工具转换、SSE 解析、传输形状。

这一族测试**不打真网关**：转换是纯函数，SSE 用构造事件，传输用
`httpx.MockTransport`。真网关验收留给 8100 上的冒烟。
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import anthropic_transport as at  # noqa: E402


# ── Task 1：协议转换（纯函数）──────────────────────────────────────────


def test_openai_tools_become_anthropic_input_schema():
    specs = [{
        "type": "function",
        "function": {"name": "observe", "description": "看",
                     "parameters": {"type": "object", "properties": {"why": {"type": "string"}}}},
    }]
    assert at.to_anthropic_tools(specs) == [{
        "name": "observe", "description": "看",
        "input_schema": {"type": "object", "properties": {"why": {"type": "string"}}},
    }]


def test_user_text_and_image_parts_become_anthropic_blocks():
    system, msgs = at.to_anthropic([
        {"role": "system", "content": "sys"},
        {"role": "user", "content": [
            {"type": "text", "text": "看"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
        ]},
    ])
    assert system == "sys"
    assert msgs == [{"role": "user", "content": [
        {"type": "text", "text": "看"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAA"}},
    ]}]


def test_consecutive_tool_results_merge_and_keep_images():
    messages = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "a", "type": "function", "function": {"name": "observe", "arguments": "{}"}},
            {"id": "b", "type": "function", "function": {"name": "screenshot", "arguments": '{"x": 1}'}},
        ]},
        {"role": "tool", "tool_call_id": "a", "content": "ra"},
        {"role": "tool", "tool_call_id": "b", "content": [
            {"type": "text", "text": "rb"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,BBB"}},
        ]},
    ]
    _, out = at.to_anthropic(messages)
    assert out[1] == {"role": "assistant", "content": [
        {"type": "tool_use", "id": "a", "name": "observe", "input": {}},
        {"type": "tool_use", "id": "b", "name": "screenshot", "input": {"x": 1}},
    ]}
    assert out[2]["role"] == "user"
    assert [b["type"] for b in out[2]["content"]] == ["tool_result", "tool_result"]
    assert out[2]["content"][1]["content"] == [
        {"type": "text", "text": "rb"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "BBB"}},
    ]


def test_thinking_blocks_pass_back_only_on_the_latest_assistant():
    messages = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "一", "_anthropic_blocks": [
            {"type": "thinking", "thinking": "旧", "signature": "s1"},
            {"type": "text", "text": "一"},
        ]},
        {"role": "user", "content": "再"},
        {"role": "assistant", "content": "二", "_anthropic_blocks": [
            {"type": "thinking", "thinking": "新", "signature": "s2"},
            {"type": "text", "text": "二"},
        ]},
    ]
    _, out = at.to_anthropic(messages)
    assert out[1]["content"] == [{"type": "text", "text": "一"}]
    assert out[3]["content"][0] == {"type": "thinking", "thinking": "新", "signature": "s2"}


# ── Task 2：SSE 解析 + httpx 传输 ──────────────────────────────────────

import json  # noqa: E402
import types  # noqa: E402

import httpx  # noqa: E402
import pytest  # noqa: E402

from agent import llm  # noqa: E402


def _sse(*events):
    out = []
    for event, data in events:
        if event:
            out.append("event: %s" % event)
        out.append("data: %s" % json.dumps(data, ensure_ascii=False))
        out.append("")
    return out


def test_text_stream_maps_content_finish_and_usage():
    chunks = list(at.anthropic_stream(_sse(
        ("message_start", {"type": "message_start", "message": {"usage": {"input_tokens": 7}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0,
                                 "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                 "delta": {"type": "text_delta", "text": "你"}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                 "delta": {"type": "text_delta", "text": "好"}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
                           "usage": {"output_tokens": 2}}),
        ("message_stop", {"type": "message_stop"}),
    )))
    reply = llm.read_stream(chunks)
    assert reply["content"] == "你好"
    assert reply["finish_reason"] == "stop"
    assert reply["usage"] == {"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9}
    assert reply["_anthropic_blocks"] == [{"type": "text", "text": "你好"}]


def test_tool_use_input_json_deltas_are_joined_and_mapped_to_tool_calls():
    chunks = list(at.anthropic_stream(_sse(
        ("message_start", {"type": "message_start", "message": {"usage": {"input_tokens": 3}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0,
                                 "content_block": {"type": "tool_use", "id": "toolu_1",
                                                   "name": "observe", "input": {}}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                 "delta": {"type": "input_json_delta", "partial_json": '{"wh'}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                 "delta": {"type": "input_json_delta", "partial_json": 'y": 1}'}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use"},
                           "usage": {"output_tokens": 4}}),
        ("message_stop", {"type": "message_stop"}),
    )))
    reply = llm.read_stream(chunks)
    assert reply["tool_calls"] == [{"id": "toolu_1", "name": "observe", "arguments": '{"why": 1}'}]
    assert reply["finish_reason"] == "tool_calls"
    assert reply["_anthropic_blocks"][0]["input"] == {"why": 1}


def test_initial_tool_input_wins_over_later_fragments():
    chunks = list(at.anthropic_stream(_sse(
        ("message_start", {"type": "message_start", "message": {"usage": {"input_tokens": 1}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0,
                                 "content_block": {"type": "tool_use", "id": "toolu_1",
                                                   "name": "observe", "input": {"why": "看"}}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                 "delta": {"type": "input_json_delta", "partial_json": '{"bad":'}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use"},
                           "usage": {"output_tokens": 1}}),
        ("message_stop", {"type": "message_stop"}),
    )))
    reply = llm.read_stream(chunks)
    assert json.loads(reply["tool_calls"][0]["arguments"]) == {"why": "看"}


def test_thinking_never_enters_content_but_stays_in_the_sidecar():
    chunks = list(at.anthropic_stream(_sse(
        ("message_start", {"type": "message_start", "message": {"usage": {"input_tokens": 1}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0,
                                 "content_block": {"type": "thinking", "thinking": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                 "delta": {"type": "thinking_delta", "thinking": "想"}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                 "delta": {"type": "signature_delta", "signature": "sig"}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("content_block_start", {"type": "content_block_start", "index": 1,
                                 "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 1,
                                 "delta": {"type": "text_delta", "text": "答"}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 1}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
                           "usage": {"output_tokens": 1}}),
        ("message_stop", {"type": "message_stop"}),
    )))
    reply = llm.read_stream(chunks)
    assert reply["content"] == "答"
    assert reply["_anthropic_blocks"][0] == {"type": "thinking", "thinking": "想", "signature": "sig"}


def test_ping_is_ignored():
    chunks = list(at.anthropic_stream(_sse(
        ("ping", {"type": "ping"}),
        ("message_start", {"type": "message_start", "message": {"usage": {"input_tokens": 1}}}),
        ("message_stop", {"type": "message_stop"}),
    )))
    assert llm.read_stream(chunks)["content"] == ""


def test_error_event_is_a_retryable_failure():
    with pytest.raises(at.AnthropicStreamError) as e:
        list(at.anthropic_stream(_sse(("error", {"type": "error", "error": {
            "type": "overloaded_error", "message": "busy"}}))))
    assert e.value.status_code == 529


def test_a_stream_without_message_stop_is_not_a_complete_reply():
    with pytest.raises(at.AnthropicStreamError) as e:
        list(at.anthropic_stream(_sse(
            ("message_start", {"type": "message_start", "message": {"usage": {"input_tokens": 1}}}),
            ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn"},
                               "usage": {"output_tokens": 1}}),
        )))
    assert "message_stop" in str(e.value)


def _transport(handler, **kw):
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return at.AnthropicTransport(
        http, base_url="https://gw.example.test/v1", api_key="k",
        timeout=42.5, max_retries=kw.pop("max_retries", 0),
        retryable_status=frozenset({503}), **kw)


def test_transport_posts_the_verified_anthropic_shape():
    seen = {}

    def handler(req):
        seen["url"] = str(req.url)
        seen["headers"] = dict(req.headers)
        seen["body"] = json.loads(req.content)
        seen["timeout"] = req.extensions["timeout"]
        return httpx.Response(200, content=b'event: message_stop\ndata: {"type":"message_stop"}\n\n')

    t = _transport(handler)
    list(t.chat.completions.create(
        model="claude-sonnet-4-6", max_tokens=100,
        messages=[{"role": "user", "content": "hi"}]))
    assert seen["url"] == "https://gw.example.test/v1/messages"
    assert seen["headers"]["x-api-key"] == "k"
    assert seen["headers"]["anthropic-version"] == "2023-06-01"
    assert seen["body"]["model"] == "claude-sonnet-4-6"
    assert seen["body"]["max_tokens"] == 100
    assert "temperature" not in seen["body"]
    assert "thinking" not in seen["body"]
    assert seen["timeout"]["read"] == 42.5


def test_transport_retries_a_retryable_status_before_any_chunk():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, json={"error": {"type": "overloaded_error"}})
        return httpx.Response(200, content=b'event: message_stop\ndata: {"type":"message_stop"}\n\n')

    t = _transport(handler, max_retries=1)
    list(t.chat.completions.create(model="claude-sonnet-4-6", max_tokens=10,
                                   messages=[{"role": "user", "content": "hi"}]))
    assert calls["n"] == 2


def test_transport_does_not_retry_a_400():
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(400, json={"error": {"type": "invalid_request_error"}})

    t = _transport(handler, max_retries=1)
    with pytest.raises(httpx.HTTPStatusError):
        list(t.chat.completions.create(model="claude-sonnet-4-6", max_tokens=10,
                                       messages=[{"role": "user", "content": "hi"}]))
    assert calls["n"] == 1


# ── Task 3：门面路由 + read_stream 旁路 + 重试判定 ──────────────────────


def test_worth_waiting_understands_httpx_status_errors():
    def err(code):
        req = httpx.Request("POST", "https://gw.example.test/v1/messages")
        return httpx.HTTPStatusError("x", request=req, response=httpx.Response(code, request=req))

    assert llm._worth_waiting(err(503)) is True
    assert llm._worth_waiting(err(529)) is True
    assert llm._worth_waiting(err(400)) is False
    assert llm._worth_waiting(httpx.ConnectError("no")) is True
    assert llm._worth_waiting(httpx.ReadTimeout("slow")) is True


def test_client_is_a_facade_that_still_exposes_base_url(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "dummy")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://gw.example.test/v1")
    monkeypatch.setattr(llm, "DEFAULT_BASE_URL", "https://gw.example.test/v1")
    built = llm.client()
    assert str(built.base_url).rstrip("/") == "https://gw.example.test/v1"
    assert hasattr(built.chat.completions, "create")


def test_router_sends_anthropic_calls_to_the_transport_and_strips_private_keys():
    sent = {}

    class _OpenAI:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    sent["openai"] = kw
                    return iter([])

    class _Anthropic:
        class chat:
            class completions:
                @staticmethod
                def create(**kw):
                    sent["anthropic"] = kw
                    return iter([])

    router = llm._Router(_OpenAI(), _Anthropic())
    router.chat.completions.create(model="claude-sonnet-4-6", messages=[
        {"role": "assistant", "content": "x", "_anthropic_blocks": [{"type": "text", "text": "x"}]}])
    assert "anthropic" in sent

    router.chat.completions.create(model="deepseek-v4-flash", messages=[
        {"role": "assistant", "content": "x", "_anthropic_blocks": [{"type": "text", "text": "x"}]}],
        _protocol="openai")
    assert sent["openai"]["messages"] == [{"role": "assistant", "content": "x"}]
    assert "_protocol" not in sent["openai"]


def test_explicit_protocol_does_not_leak_to_fallback(monkeypatch):
    monkeypatch.setenv("SPIKE_PROTOCOL", "anthropic")
    assert llm.call_protocol("deepseek-v4-flash", primary=True) == "anthropic"
    assert llm.call_protocol("deepseek-v4-flash", primary=False) == "openai"
    assert llm.call_protocol("claude-sonnet-4-6", primary=False) == "anthropic"


def test_thinking_sidecar_only_rides_on_the_latest_assistant(monkeypatch):
    """§5.4：旁路块只贴**最新一条** assistant；旧的要剥掉，记录里也不许带私有键。"""
    monkeypatch.setenv("SPIKE_FALLBACK_MODEL", "")
    seen = []

    def tool_reply(cid, sidecar):
        return [
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": cid, "function": {"name": "observe", "arguments": "{}"}}]},
                "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}],
             "_anthropic_blocks": sidecar},
        ]

    replies = [
        tool_reply("c1", [{"type": "thinking", "thinking": "想1", "signature": "s1"}]),
        tool_reply("c2", [{"type": "thinking", "thinking": "想2", "signature": "s2"}]),
        [{"choices": [{"delta": {"content": "完"}, "finish_reason": "stop"}]}],
    ]

    def create(**kw):
        seen.append([(m.get("role"), m.get("_anthropic_blocks")) for m in kw["messages"]])
        return iter(replies.pop(0))

    cli = types.SimpleNamespace(chat=types.SimpleNamespace(
        completions=types.SimpleNamespace(create=create)))
    rounds = llm.run_tool_loop("s", "u", [], lambda n, a: "ok",
                               max_rounds=3, _client=cli)

    assistants_second = [b for r, b in seen[1] if r == "assistant"]
    assert assistants_second == [[{"type": "thinking", "thinking": "想1", "signature": "s1"}]]

    assistants_third = [b for r, b in seen[2] if r == "assistant"]
    assert assistants_third == [None, [{"type": "thinking", "thinking": "想2", "signature": "s2"}]]

    assert "_anthropic_blocks" not in rounds[0]["messages"][0]
    assert "_anthropic_blocks" not in rounds[1]["messages"][0]
