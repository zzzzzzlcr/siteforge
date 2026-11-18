# Anthropic 原生协议 + 主备模型自动切换 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `agent/llm.py` 里加一条 Anthropic 原生 `/v1/messages` 传输路，并让主模型在可重试失败后自动切到备用模型；`SPIKE_FALLBACK_MODEL=""` 时回到今天逐字节相同的单路行为。

**Architecture:** 新增 `agent/anthropic_transport.py` 做协议适配（消息/工具转换、SSE 解析、httpx 传输）。`llm.client()` 返回一个按模型路由的门面，`browser_agent._Gate` 仍包在门面外。故障切换、熔断、空流/坏参数判定都留在 `run_tool_loop`，因为它看得见「这一次调用」的边界。

**Tech Stack:** Python 3 / httpx 0.28（已有）/ openai SDK（已有）/ pytest。不引入 `anthropic` SDK。

**Spec:** `docs/superpowers/specs/2026-11-07-anthropic-native-dual-model-design.md`

## Global Constraints

- `run_tool_loop(...)` 对外签名不变：`system, user, tool_specs, dispatch, max_rounds=8, *, model=None, max_tokens=DEFAULT_MAX_TOKENS, steer=None, _client=None`。
- `records` 每条原有字段不变；只新增 `model` 与 `failover`。
- `usage` 保持 `prompt_tokens` / `completion_tokens` / `total_tokens`；Anthropic 不造假 `reasoning_tokens`。
- `finish_reason` 映射：`tool_use→tool_calls`，`end_turn`/`stop_sequence→stop`，`max_tokens→length`，其余原样。
- `SPIKE_PROTOCOL` 显式值只影响 primary；fallback 永远按模型名判。
- `SPIKE_FALLBACK_MODEL` 默认 `claude-sonnet-4-6`；空串 = 关闭切换，并一并关闭空流/坏参数判据。
- `SITEFORGE_LLM_FAILOVER_FAILS` 默认 3，`SITEFORGE_LLM_FAILOVER_COOLDOWN` 默认 300 秒。
- `RETRYABLE_STATUS` 补 529。
- Anthropic 请求每次显式 `timeout=timeout_seconds()`，不许吃 httpx 默认 5 秒。
- 不许打印 API key / 消息正文。
- OpenAI 通路的 HTTP 请求形状不变；默认路径不额外传私有 kwarg。
- 测试先写、先红、再实现；验证用 8100，不碰 8099。

## Review Focus

1. **SSE 流在 `message_delta` 后干净断掉、没有 `message_stop`** —— 用户期望这是失败，不是「模型讲完了」。测试：Task 2 的 `test_a_stream_without_message_stop_is_not_a_complete_reply`。
2. **`content_block_start` 的 `tool_use` 自带非空 `input`，随后仍来 `input_json_delta`** —— 用户期望以初值为准，不许把两段 JSON 拼坏。测试：Task 2 的 `test_initial_tool_input_wins_over_later_fragments`。
3. **连续多条 tool 结果里夹着 `image_url`** —— 用户期望合并成一个 user 消息且图片仍是 image 块（截图能力不许在 Anthropic 路丢）。测试：Task 1 的 `test_consecutive_tool_results_merge_and_keep_images`。
4. **`SPIKE_PROTOCOL=anthropic` 时 fallback 仍是 deepseek** —— 用户期望显式协议不许泄漏到备用模型上。测试：Task 3 的 `test_explicit_protocol_does_not_leak_to_fallback`。
5. **坏工具参数但 failover 被关掉** —— 用户期望回到今天的行为：记 `arguments 解析失败`、不切换、该轮照常返回。测试：Task 5 的 `test_bad_arguments_still_recorded_when_failover_is_off`。

---

### Task 1: Anthropic 协议转换（纯函数）

**Files:**
- Create: `agent/anthropic_transport.py`
- Test: `tests/test_llm_anthropic.py`

**Interfaces:**
- Consumes: 无。
- Produces:
  - `ANTHROPIC_VERSION = "2023-06-01"`
  - `to_anthropic_tools(tool_specs: list[dict]) -> list[dict]`
  - `to_anthropic(messages: list[dict]) -> tuple[str | None, list[dict]]`

- [ ] **Step 1: Write the failing tests**

`tests/test_llm_anthropic.py`:

```python
from agent import anthropic_transport as at


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_llm_anthropic.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'agent.anthropic_transport'`.

- [ ] **Step 3: Implement the module**

`agent/anthropic_transport.py`:

- `_image_block(url)`：把 `data:<media>;base64,<data>` 拆成 `{"type":"image","source":{"type":"base64","media_type":media,"data":data}}`；不是 data URL 的原样 `{"type":"image","source":{"type":"url","url":url}}`。
- `_blocks(content)`：字符串 → `[{"type":"text","text":content}]`；list 里 `text` / `image_url` 逐块转换。
- `to_anthropic_tools`：`{name, description, input_schema=parameters or {"type":"object","properties":{}}}`。
- `to_anthropic`：先收集 `system`（多条用 `"\n\n"` 拼）；顺序遍历非 system 消息，`tool` 连续段合并成一条 user 消息；assistant 只有在它是**最后一条 assistant**时才用 `_anthropic_blocks` 原样作 content，否则用 `content` 文本 + `tool_calls` 重建 `text`/`tool_use`；`arguments` 解析失败按 `{}`。

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_llm_anthropic.py -q`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add agent/anthropic_transport.py tests/test_llm_anthropic.py
git commit -m "feat: convert OpenAI-shaped history to Anthropic messages"
```

---

### Task 2: Anthropic SSE 解析 + httpx 传输

**Files:**
- Modify: `agent/anthropic_transport.py`
- Test: `tests/test_llm_anthropic.py`

**Interfaces:**
- Consumes: `ANTHROPIC_VERSION`、`to_anthropic*`（Task 1）。
- Produces:
  - `AnthropicStreamError(Exception)`，带 `.status_code: int`
  - `anthropic_stream(lines) -> Iterator[dict]`（OpenAI 形状 chunk；最后一片带 `_anthropic_blocks`）
  - `AnthropicTransport(http, *, base_url, api_key, timeout, max_retries, retryable_status)`，暴露 `chat.completions.create(**kwargs)`

- [ ] **Step 1: Write the failing tests**

追加到 `tests/test_llm_anthropic.py`：

```python
import json
import httpx
import pytest
from agent import llm


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_llm_anthropic.py -q`
Expected: FAIL with `AttributeError: module ... has no attribute 'anthropic_stream'` / `AnthropicTransport`.

- [ ] **Step 3: Implement `anthropic_stream` and `AnthropicTransport`**

`agent/anthropic_transport.py`:

- `AnthropicStreamError(message, *, status_code=500)`；`error` 事件按 `error.type` 映射：`overloaded_error→529`、`rate_limit_error→429`、`authentication_error→401`、`permission_error→403`、`not_found_error→404`、`invalid_request_error→400`，其余 `500`。
- `anthropic_stream(lines)`：按 SSE 空行切事件；`ping` 忽略；维护 `blocks[index]` 状态；`message_start` 记 `input_tokens`；`content_block_start` 建块（tool_use 记 `id/name/initial_input`，thinking 记空串）；`content_block_delta` 按类型分发；`content_block_stop` 收尾；`message_delta` 发 finish chunk；`message_stop` 发收尾 chunk（`choices: []`、usage、`_anthropic_blocks`）；没有 `message_stop` 就抛 `AnthropicStreamError`。
- tool_use 的 `input`：`initial_input` 非空 → `json.dumps(initial_input)` 并忽略后续 fragments；否则拼 `partial_json` 后 `json.loads`（失败保留原文，交给 `run_tool_loop` 判坏参数）。
- `AnthropicTransport.chat.completions.create(**kwargs)`：`base_url.rstrip("/") + "/messages"`；头 `x-api-key` / `anthropic-version` / `content-type: application/json`；体 `model` / `max_tokens` / `stream: True` / `messages`（`to_anthropic`）+ `system`（非空时）+ `tools`（非空时）；每次 `http.stream(..., timeout=self._timeout)`。
- 传输层重试：只在**还没吐出任何 chunk** 时，对连接类异常 + `status_code in retryable_status` 立即重试 `max_retries` 次；已吐过 chunk 的断流直接抛，交给外层长退避。非 `Exception` 原样抛。

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_llm_anthropic.py -q`
Expected: 13 passed.

- [ ] **Step 5: Commit**

```bash
git add agent/anthropic_transport.py tests/test_llm_anthropic.py
git commit -m "feat: add Anthropic SSE transport and parser"
```

---

### Task 3: `client()` 门面 + `read_stream` 旁路 + 重试判定

**Files:**
- Modify: `agent/llm.py`
- Test: `tests/test_llm_anthropic.py`

**Interfaces:**
- Consumes: `AnthropicTransport`（Task 2）。
- Produces:
  - `RETRYABLE_STATUS` 含 529
  - `auto_protocol(model: str) -> str`
  - `call_protocol(model: str, *, primary: bool) -> str`
  - `_Router`，`client()` 返回它；未知识属性转给底层 OpenAI client
  - `read_stream(...)["_anthropic_blocks"]`

- [ ] **Step 1: Write the failing tests**

追加到 `tests/test_llm_anthropic.py`：

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_llm_anthropic.py -q`
Expected: FAIL with `AttributeError: module 'agent.llm' has no attribute '_Router'` etc.

- [ ] **Step 3: Implement in `agent/llm.py`**

- `RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})`。
- `_worth_waiting`：保留现有两判据，新增：
  - `httpx.HTTPStatusError` 且 `exc.response.status_code in RETRYABLE_STATUS`；
  - `httpx.TimeoutException` / `httpx.ConnectError` / `httpx.ReadError` / `httpx.RemoteProtocolError` / `httpx.WriteError`；
  - `AnthropicStreamError` 走它自己的 `.status_code` 判据（已有第一段即可）。
  函数内 `import httpx`，与 `openai` 同一条。
- `read_stream`：循环里记 `blocks = d.get("_anthropic_blocks")`，返回值加 `"_anthropic_blocks": blocks`。
- `auto_protocol(model)`：`anthropic` iff `model.lower().startswith("claude")`。
- `call_protocol(model, *, primary)`：显式 `SPIKE_PROTOCOL`（`openai`/`anthropic`）只在 `primary=True` 时生效；否则走 `auto_protocol`。
- `_Router`：
  ```python
  class _Router:
      def __init__(self, openai_client, anthropic_transport):
          self._openai = openai_client
          self._anthropic = anthropic_transport
          self.chat = _Namespace(completions=_Namespace(create=self._create))

      def __getattr__(self, name):
          return getattr(self._openai, name)

      def _create(self, **kwargs):
          proto = kwargs.pop("_protocol", None)
          model = kwargs.get("model") or DEFAULT_MODEL
          proto = proto or auto_protocol(model)
          if proto == "anthropic":
              return self._anthropic.chat.completions.create(**kwargs)
          out = dict(kwargs)
          if "messages" in out:
              out["messages"] = [
                  {k: v for k, v in m.items() if not str(k).startswith("_")}
                  for m in out["messages"]
              ]
          return self._openai.chat.completions.create(**out)
  ```
  显式协议由 `run_tool_loop` 在「显式值 ≠ 自动值」时通过 `_protocol=` 传入；默认路径不传，门面按模型名路由。
- `client()`：保留 key/base 两条报错与 OpenAI 构造，追加 `anthropic = AnthropicTransport(_http_client(), base_url=base, api_key=key, timeout=timeout_seconds(), max_retries=max_retries(), retryable_status=RETRYABLE_STATUS)`，返回 `_Router(inner, anthropic)`。
- 需要一个本地 `_Namespace`（`types.SimpleNamespace` 即可），或 `from types import SimpleNamespace`。

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_llm_anthropic.py tests/test_llm.py -q`
Expected: 全部通过（`test_llm.py` 的 `base_url` 回归也绿）。

- [ ] **Step 5: Commit**

```bash
git add agent/llm.py tests/test_llm_anthropic.py
git commit -m "feat: route model calls through a protocol facade"
```

---

### Task 4: 主备切换 + 熔断 + 观测字段

**Files:**
- Modify: `agent/llm.py`
- Test: `tests/test_llm_failover.py`（新建）
- Test: `tests/test_llm.py`（只改 `test_giving_up_after_waiting_says_so`）

**Interfaces:**
- Consumes: `call_protocol` / `_Router`（Task 3）。
- Produces:
  - `DEFAULT_FALLBACK_MODEL = "claude-sonnet-4-6"`
  - `fallback_model() -> str`
  - `failover_fails() -> int`、`failover_cooldown() -> float`
  - `_reset_failover_state()`
  - `run_tool_loop` 的 `record["model"]` / `record["failover"]`
  - `summarize()["models"]` / `summarize()["failovers"]`

- [ ] **Step 1: Write the failing tests**

`tests/test_llm_failover.py`：

```python
import importlib
import json
import types

import httpx
import openai
import pytest

from agent import llm


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    # `off` = 每个模型只打一次；退避重试本身由 test_llm.py 那几条老用例钉着。
    monkeypatch.setenv("SITEFORGE_LLM_BACKOFF", "off")
    monkeypatch.setenv("SPIKE_FALLBACK_MODEL", "claude-sonnet-4-6")
    monkeypatch.setenv("SITEFORGE_LLM_FAILOVER_FAILS", "3")
    monkeypatch.setenv("SITEFORGE_LLM_FAILOVER_COOLDOWN", "300")
    fresh = importlib.reload(llm)
    fresh._reset_failover_state()
    yield fresh
    importlib.reload(llm)


def _busy():
    req = httpx.Request("POST", "https://gw.example.test/v1/chat/completions")
    return openai.InternalServerError("busy", response=httpx.Response(503, request=req), body=None)


class _Script:
    """按脚本回：("raise", exc) / ("text", 文本) / ("tools", [(name, args)])。"""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        action = self.script.pop(0)
        if action[0] == "raise":
            raise action[1]
        chunks = []
        if action[0] == "text":
            chunks.append({"choices": [{"delta": {"content": action[1]}, "finish_reason": "stop"}]})
        else:
            for i, (name, args) in enumerate(action[1]):
                raw = json.dumps(args)
                chunks.append({"choices": [{"delta": {"tool_calls": [
                    {"index": i, "id": f"c{i}", "function": {"name": name, "arguments": raw}}]},
                    "finish_reason": None}]})
            chunks.append({"choices": [{"delta": {}, "finish_reason": "tool_calls"}]})
        return iter(chunks)


def _client(script):
    client = _Script(script)
    chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=client.create))
    return types.SimpleNamespace(chat=chat), client


def test_a_retryable_failure_switches_to_the_fallback_model(fresh):
    cli, script = _client([("raise", _busy()), ("text", "好了")])
    rounds = fresh.run_tool_loop("sys", "usr", [], lambda n, a: None,
                                 max_rounds=1, _client=cli)
    assert [c["model"] for c in script.calls] == [
        "deepseek-v4-flash", "claude-sonnet-4-6"]
    assert rounds[0]["content"] == "好了"
    assert rounds[0]["model"] == "claude-sonnet-4-6"
    assert rounds[0]["failover"] == {"from": "deepseek-v4-flash",
                                     "to": "claude-sonnet-4-6", "reason": "error"}


def test_a_non_retryable_error_does_not_switch(fresh):
    req = httpx.Request("POST", "https://gw.example.test/v1/chat/completions")
    bad = openai.BadRequestError("bad", response=httpx.Response(400, request=req), body=None)
    cli, script = _client([("raise", bad)] * 10)
    with pytest.raises(fresh.ModelCallFailed):
        fresh.run_tool_loop("sys", "usr", [], lambda n, a: None, max_rounds=1, _client=cli)
    assert len(script.calls) == 1


def test_empty_fallback_env_restores_the_single_path(fresh, monkeypatch):
    monkeypatch.setenv("SPIKE_FALLBACK_MODEL", "")
    cli, script = _client([("raise", _busy())] * 10)
    with pytest.raises(fresh.ModelCallFailed):
        fresh.run_tool_loop("sys", "usr", [], lambda n, a: None, max_rounds=1, _client=cli)
    assert len(script.calls) == 1          # 只试 primary，没有 fallback


def test_breaker_open_skips_primary_and_says_breaker(fresh, monkeypatch):
    monkeypatch.setenv("SITEFORGE_LLM_FAILOVER_FAILS", "1")
    cli, script = _client([
        ("raise", _busy()),                 # round 1 primary
        ("tools", [("observe", {})]),       # round 1 fallback → 继续
        ("text", "好了"),                    # round 2 fallback（熔断直接走它）
    ])
    rounds = fresh.run_tool_loop("sys", "usr", [], lambda n, a: "ok",
                                 max_rounds=2, _client=cli)
    assert [c["model"] for c in script.calls] == [
        "deepseek-v4-flash", "claude-sonnet-4-6", "claude-sonnet-4-6"]
    assert rounds[0]["failover"]["reason"] == "error"
    assert rounds[1]["failover"]["reason"] == "breaker"


def test_half_open_tries_primary_once_and_re_demotes_on_failure(fresh):
    import time
    state = fresh._breaker_state("deepseek-v4-flash")
    state.update({"open": True, "demoted_until": time.time() - 1, "failures": 0})
    cli, script = _client([("raise", _busy()), ("text", "好了")])
    fresh.run_tool_loop("sys", "usr", [], lambda n, a: None, max_rounds=1, _client=cli)
    assert [c["model"] for c in script.calls] == [
        "deepseek-v4-flash", "claude-sonnet-4-6"]
    assert state["open"] is True and state["demoted_until"] > time.time()


def test_fails_zero_always_tries_primary(fresh, monkeypatch):
    monkeypatch.setenv("SITEFORGE_LLM_FAILOVER_FAILS", "0")
    cli, script = _client([
        ("raise", _busy()), ("text", "好了"),
        ("raise", _busy()), ("text", "又好了"),
    ])
    fresh.run_tool_loop("sys", "usr", [], lambda n, a: None, max_rounds=1, _client=cli)
    fresh.run_tool_loop("sys", "usr", [], lambda n, a: None, max_rounds=1, _client=cli)
    assert [c["model"] for c in script.calls] == [
        "deepseek-v4-flash", "claude-sonnet-4-6",
        "deepseek-v4-flash", "claude-sonnet-4-6"]


def test_fallback_failure_does_not_add_to_primary_breaker(fresh):
    state = fresh._breaker_state("deepseek-v4-flash")
    cli, script = _client([("raise", _busy())] * 6)
    with pytest.raises(fresh.ModelCallFailed):
        fresh.run_tool_loop("sys", "usr", [], lambda n, a: None, max_rounds=1, _client=cli)
    assert state["failures"] == 1


def test_every_model_attempt_still_goes_through_the_gate(fresh):
    gate_calls = {"n": 0}

    class _Gate:
        def __init__(self, inner):
            self._inner = inner
            self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self._create))

        def _create(self, **kw):
            gate_calls["n"] += 1
            return self._inner.chat.completions.create(**kw)

    inner, _ = _client([("raise", _busy()), ("text", "好了")])
    fresh.run_tool_loop("sys", "usr", [], lambda n, a: None,
                        max_rounds=1, _client=_Gate(inner))
    assert gate_calls["n"] == 2


def test_summarize_counts_models_and_failovers(fresh):
    summary = fresh.summarize([
        {"model": "a", "failover": None, "tool_calls": [], "usage": {}, "elapsed_ms": 1},
        {"model": "b", "failover": {"from": "a", "to": "b", "reason": "error"},
         "tool_calls": [], "usage": {}, "elapsed_ms": 1},
    ])
    assert summary["models"] == {"a": 1, "b": 1}
    assert summary["failovers"] == 1
```

修改 `tests/test_llm.py::test_giving_up_after_waiting_says_so`：在函数开头加
`monkeypatch.setenv("SPIKE_FALLBACK_MODEL", "")`，其余断言不动（`calls["n"] == 3`）。

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_llm_failover.py tests/test_llm.py -q`
Expected: 新用例 FAIL（没有 `fallback_model` / `_breaker_state` 等）；`test_giving_up_after_waiting_says_so` 在未改前会因 6 次调用红。

- [ ] **Step 3: Implement failover in `agent/llm.py`**

- env 读取：`fallback_model()`（未设 → 默认，空串 → `""`）、`failover_fails()`、`failover_cooldown()`。
- 熔断状态：
  ```python
  _BREAKER_LOCK = threading.Lock()
  _BREAKERS: dict[str, dict] = {}

  def _breaker_state(model):
      with _BREAKER_LOCK:
          return _BREAKERS.setdefault(model, {"failures": 0, "demoted_until": 0.0, "open": False})
  ```
  `_reset_failover_state()` 清空字典（测试用）。
- `_breaker_start(primary) -> tuple[int, bool]`：open 且未到期 → `(1, False)`；open 但已到期 → `(0, True)`（半开）；否则 `(0, False)`。
- `_breaker_success(primary)`：清零、关闸。
- `_breaker_failure(primary, *, half_open)`：`half_open` 或 `failures + 1 >= threshold`（threshold>0）→ 置 `open=True, demoted_until=now+cooldown, failures=0`；否则 `failures += 1`。threshold==0 时只加计数，不 demote。
- `_log_failover(primary, next_model, exc=None, *, reason, left=None)`：写 stderr 一行，格式按 spec §5.6，不打印正文/key。
- `_call_with_failover(...)`：返回 `(reply, used_model, failover, parsed_calls)`；内部对每个候选做现有退避循环。只有 `pos == 0` 的候选参与熔断记账；`failover_on = bool(chain[1:])`。切换条件：`_worth_waiting(exc)` 且还有下一个候选。`ModelCallFailed` 消息加 `_tried_models_note(tried)`。
- `run_tool_loop`：`primary = model or DEFAULT_MODEL`；`chain = [primary] + ([fb] if fb and fb != primary else [])`；把现有 `while True` 调用替换成 `_call_with_failover`；record 加 `"model": used`、`"failover": failover`；`steer.went_out()` 仍只在成功返回后调。
- `summarize`：`models` 按 `r.get("model")` 计数（缺省不计）；`failovers` 统计 `bool(r.get("failover"))`。
- 显式协议：调用前算 `proto = call_protocol(candidate, primary=(pos == 0))`；仅当 `proto != auto_protocol(candidate)` 时在 kwargs 里加 `_protocol=proto`。默认路径不加任何新 kwarg。

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_llm_failover.py tests/test_llm.py -q`
Expected: 全绿。

- [ ] **Step 5: Commit**

```bash
git add agent/llm.py tests/test_llm.py tests/test_llm_failover.py
git commit -m "feat: fail over to a fallback model with a circuit breaker"
```

---

### Task 5: 空流 / 坏参数硬失败 + `ModelCallFailed` 现场

**Files:**
- Modify: `agent/llm.py`
- Test: `tests/test_llm_failover.py`
- Test: `agent/fix.py`（只加回归测试，不改逻辑）

**Interfaces:**
- Consumes: Task 4 的 `_call_with_failover`。
- Produces:
  - `BadReply(Exception)`，带 `.facts: str`
  - `_parse_arguments(raw: str) -> dict`
  - `ModelCallFailed` 消息含 `finish_reason` / 正文字符数 / `tool_calls` 条数 / `reasoning_tokens` / `max_tokens` / 试过模型

- [ ] **Step 1: Write the failing tests**

追加到 `tests/test_llm_failover.py`：

```python
def _empty():
    return iter([])


def _bad_args():
    return iter([{"choices": [{"delta": {"tool_calls": [
        {"index": 0, "id": "c0", "function": {"name": "observe", "arguments": "{not json"}}]},
        "finish_reason": None}]},
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}])


def test_an_empty_reply_is_a_hard_failure_and_switches(fresh):
    calls = []

    def create(**kw):
        calls.append(kw)
        return _empty() if len(calls) == 1 else iter(
            [{"choices": [{"delta": {"content": "好了"}, "finish_reason": "stop"}]}])

    cli = types.SimpleNamespace(chat=types.SimpleNamespace(
        completions=types.SimpleNamespace(create=create)))
    rounds = fresh.run_tool_loop("sys", "usr", [], lambda n, a: None,
                                 max_rounds=1, _client=cli)
    assert [c["model"] for c in calls] == [
        "deepseek-v4-flash", "claude-sonnet-4-6"]
    assert rounds[0]["content"] == "好了"
    # 空轮整轮作废：fallback 看到的历史仍然只有 system + user
    assert [m["role"] for m in calls[-1]["messages"]] == ["system", "user"]


def test_both_empty_keeps_the_budget_diagnostic(fresh):
    def create(**kw):
        return iter([{"choices": [{"delta": {}, "finish_reason": "length"}],
                      "usage": {"completion_tokens": 12000,
                                "completion_tokens_details": {"reasoning_tokens": 12000}}}])

    cli = types.SimpleNamespace(chat=types.SimpleNamespace(
        completions=types.SimpleNamespace(create=create)))
    with pytest.raises(fresh.ModelCallFailed) as e:
        fresh.run_tool_loop("sys", "usr", [], lambda n, a: None,
                            max_rounds=1, max_tokens=12000, _client=cli)
    said = str(e.value)
    assert "finish_reason=length" in said
    assert "reasoning_tokens=12000" in said
    assert "max_tokens=12000" in said
    assert "空" in said


def test_bad_tool_arguments_are_a_hard_failure_and_the_round_is_discarded(fresh):
    calls = []

    def create(**kw):
        calls.append(kw)
        if len(calls) == 1:
            return _bad_args()
        return iter([{"choices": [{"delta": {"content": "好了"}, "finish_reason": "stop"}]}])

    cli = types.SimpleNamespace(chat=types.SimpleNamespace(
        completions=types.SimpleNamespace(create=create)))
    rounds = fresh.run_tool_loop("sys", "usr", [], lambda n, a: None,
                                 max_rounds=1, _client=cli)
    assert rounds[0]["content"] == "好了"
    assert [m["role"] for m in calls[-1]["messages"]] == ["system", "user"]


def test_bad_arguments_still_recorded_when_failover_is_off(fresh, monkeypatch):
    monkeypatch.setenv("SPIKE_FALLBACK_MODEL", "")
    def create(**kw):
        return _bad_args()
    cli = types.SimpleNamespace(chat=types.SimpleNamespace(
        completions=types.SimpleNamespace(create=create)))
    rounds = fresh.run_tool_loop("sys", "usr", [], lambda n, a: None,
                                 max_rounds=1, _client=cli)
    assert rounds[0]["content"] == ""
    assert "arguments 解析失败" in rounds[0]["tool_calls"][0]["error"]


def test_an_empty_reply_still_stops_the_loop_when_failover_is_off(fresh, monkeypatch):
    monkeypatch.setenv("SPIKE_FALLBACK_MODEL", "")
    def create(**kw):
        return _empty()
    cli = types.SimpleNamespace(chat=types.SimpleNamespace(
        completions=types.SimpleNamespace(create=create)))
    rounds = fresh.run_tool_loop("sys", "usr", [], lambda n, a: None,
                                 max_rounds=3, _client=cli)
    assert len(rounds) == 1 and rounds[0]["content"] == ""
```

再在 `tests/test_llm.py` 加一条（或新文件）验证 `fix.py` 的诊断没丢：

```python
def test_the_empty_patch_budget_diagnostic_is_still_reachable(monkeypatch):
    monkeypatch.setenv("SPIKE_FALLBACK_MODEL", "")
    rounds = [{"content": "", "finish_reason": "length",
               "usage": {"completion_tokens": 12000,
                         "completion_tokens_details": {"reasoning_tokens": 12000}}}]
    with pytest.raises(ValueError) as e:
        fix.patch_from_rounds(rounds, max_tokens=12000)
    assert "预算被思考吃满" in str(e.value)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_llm_failover.py -q -k "empty or bad or diagnostic"`
Expected: FAIL（空流仍被当成正常收工；坏参数仍进 dispatch 路径）。

- [ ] **Step 3: Implement hard-failure classification**

- `BadReply(Exception)`：`__init__(message, *, facts)`。
- `_parse_arguments(raw)`：空白 → `{}`；`json.loads`；非 dict → `ValueError("arguments 不是对象，是 %s" % type)`。
- `_parse_calls(raw_calls)`：返回 `[{"args": dict, "error": str|None}]`；错误消息保持 `"arguments 解析失败: ..."` 原格式。
- `_reply_facts_note(reply, max_tokens)`：`finish_reason` / 正文字符 / tool_calls 条数 / `reasoning_tokens` / `max_tokens`。
- `_validate_reply(reply, parsed, *, failover_on)`：
  - `failover_on` 为假 → 直接返回；
  - 空 `content.strip()` 且无 `tool_calls` → `raise BadReply("模型回了空流", facts=...)`；
  - 任何 `parsed[i]["error"]` → `raise BadReply("工具参数不是 JSON", facts=...)`。
- `_worth_waiting` 增加 `isinstance(exc, BadReply) → True`。
- `_call_with_failover` 在 `read_stream` 后、返回前调用 `_parse_calls` + `_validate_reply`；成功时把 `parsed` 一起返回。
- `run_tool_loop` 的工具循环改用预解析结果：`call["args"] = parsed["args"]`，`call["error"] = parsed["error"]`（非 None 时不 dispatch）；不再在循环里 `json.loads`。
- `ModelCallFailed` 消息追加 `getattr(exc, "facts", "")`（BadReply 的现场）和 `_tried_models_note(tried)`。
- `steer`：`_tell_steer(..., went_out=True)` 只在 `_call_with_failover` 返回后执行；失败路径仍是 `went_out=False`。

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_llm_failover.py tests/test_llm.py -q`
Expected: 全绿。

- [ ] **Step 5: Commit**

```bash
git add agent/llm.py tests/test_llm.py tests/test_llm_failover.py
git commit -m "feat: treat empty streams and bad tool arguments as hard failures"
```

---

### Task 6: 全量回归 + 8100 真网关验收

**Files:**
- 无新增代码；只跑验证。

**Interfaces:**
- Consumes: Task 1-5 全部。

- [ ] **Step 1: 跑 llm 相关回归**

Run:
```bash
python -m pytest tests/test_llm.py tests/test_llm_anthropic.py tests/test_llm_failover.py \
  tests/test_steer.py tests/test_browser_agent.py -q
```
Expected: 全绿。若有既有用例因 failover 默认开启而红，先判断是不是「空 reply 被当成硬失败」这一类，再决定是修用例还是修实现（不许为了绿而关掉默认行为）。

- [ ] **Step 2: 确认 OpenAI 路请求形状没变**

用 Task 4 的 `test_every_model_attempt_still_goes_through_the_gate` + `test_empty_fallback_env_restores_the_single_path` 两条兜住；另外手工跑：

```bash
python -m pytest tests/test_llm.py -q -k "stream or budget or call_context"
```
Expected: 全绿。

- [ ] **Step 3: 8100 真网关冒烟（只在 8100，不碰 8099）**

在 8100 容器里用现有 env 跑一次 **最小** `run_tool_loop`（一个 observe 风格的桩工具、max_rounds=1），确认：
- `records[0]["model"] == "deepseek-v4-flash"`（或 `SPIKE_MODEL`）；
- `usage` 三格齐全；
- `finish_reason` 正常；
- 把 `SPIKE_MODEL` 改成一个不存在的名字时，日志出现 `[llm] ... → fallback=claude-sonnet-4-6` 且 job 仍完成；
- 恢复 `SPIKE_MODEL` 后下一次回到主模型。

⚠️ 该步骤会真的花钱、且依赖 8100 的出口国家；若网关把 `model_not_found` 包成 503，会先付 85 秒退避再切换（见 spec §7），验收时间要按 170 秒留。不要打印 key。

- [ ] **Step 4: 汇报**

把「单测结果 / 真网关结果 / 还没做的那一步（8099 重建）」三件事写清楚。8099 的重建与上线需用户当次批准，不自动做。

---

## Self-Review

- **Spec coverage:** §5.1 门面/路由 → Task 3；§5.2 转换 → Task 1；§5.3 SSE → Task 2；§5.4 thinking 旁路 → Task 1/2/3；§5.5 切换/熔断/硬失败 → Task 4/5；§5.6 观测 → Task 4；§5.7 配置 → Task 3/4；§8 测试 → 各 Task；§9 builder 决定 → 新模块 `anthropic_transport.py`、全局一把锁、日志格式。无遗漏。
- **类型一致性:** `to_anthropic` 返回 `(system, messages)`；`AnthropicTransport` 暴露 `chat.completions.create`；`_call_with_failover` 返回四元组；`record["failover"]` 是 `None` 或 `{"from","to","reason"}`。前后一致。
- **Review Focus:** 五条都在对应 Task 的测试里钉住。
