"""Anthropic 原生 `/v1/messages` 的协议适配层。

内部历史始终是 OpenAI 形状（`agent/llm.py` 的 `run_tool_loop` 只维护那一种），
协议差异全部在**发请求前**转换：`to_anthropic*` 是纯函数，`anthropic_stream`
把 SSE 事件还原成 OpenAI 形状的 chunk，让现成的 `read_stream` / `_Gate` 直接消费。

⚠️ 这一层**不碰** `_Gate`：它由 `llm.client()` 的门面包在传输层外面，
停闸 / 叙述 / 停滞判据对 Anthropic 路照样生效。
"""

from __future__ import annotations

import json
import types

import httpx

ANTHROPIC_VERSION = "2023-06-01"

_Namespace = types.SimpleNamespace


def _image_block(url: str) -> dict:
    if isinstance(url, str) and url.startswith("data:") and ";base64," in url:
        head, data = url.split(",", 1)
        media = head[len("data:"):].split(";", 1)[0] or "image/png"
        return {"type": "image",
                "source": {"type": "base64", "media_type": media, "data": data}}
    return {"type": "image", "source": {"type": "url", "url": url}}


def _blocks(content) -> list[dict]:
    """OpenAI content parts → Anthropic content blocks（text / image）。"""
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    out: list[dict] = []
    for part in (content or []):
        part = part or {}
        kind = part.get("type")
        if kind == "text":
            out.append({"type": "text", "text": str(part.get("text") or "")})
        elif kind == "image_url":
            url = ((part.get("image_url") or {}).get("url") or "")
            if url:
                out.append(_image_block(url))
        elif kind == "image":
            out.append(part)                     # 已经是 Anthropic 形状
    return out


def _tool_result_content(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return _blocks(content)
    return json.dumps(content, ensure_ascii=False)


def _assistant_content(message: dict) -> list[dict]:
    blocks: list[dict] = []
    text = message.get("content")
    if isinstance(text, str):
        if text:
            blocks.append({"type": "text", "text": text})
    elif isinstance(text, list):
        blocks.extend(_blocks(text))
    for call in (message.get("tool_calls") or []):
        fn = (call or {}).get("function") or {}
        raw = fn.get("arguments")
        try:
            inp = json.loads(raw) if raw and str(raw).strip() else {}
        except Exception:                        # noqa: BLE001 —— 坏参数轮会被整轮作废
            inp = {}
        if not isinstance(inp, dict):
            inp = {}
        blocks.append({"type": "tool_use", "id": call.get("id") or "",
                       "name": fn.get("name") or "", "input": inp})
    return blocks


def to_anthropic_tools(tool_specs: list[dict]) -> list[dict]:
    """OpenAI `type:function` 工具表 → Anthropic `{name, description, input_schema}`。"""
    out: list[dict] = []
    for spec in (tool_specs or []):
        fn = (spec or {}).get("function") or {}
        out.append({
            "name": fn.get("name") or "",
            "description": fn.get("description") or "",
            "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
        })
    return out


def to_anthropic(messages: list[dict]) -> tuple[str | None, list[dict]]:
    """整段 OpenAI 历史 → `(system, anthropic_messages)`。

    - `system` 提升为顶层参数（多条用空行拼）；
    - 连续 `role:"tool"` 合并成**一条** user 消息里的多个 `tool_result` 块；
    - assistant 的 `tool_calls` 重建为 `tool_use`；
    - thinking 只从**最后一条** assistant 的 `_anthropic_blocks` 旁路回传，
      更早的 assistant 一律重建（防止历史 thinking 让上下文线性膨胀）。
    """
    systems: list[str] = []
    last_assistant = -1
    for i, msg in enumerate(messages):
        if (msg or {}).get("role") == "assistant":
            last_assistant = i

    out: list[dict] = []
    i = 0
    while i < len(messages):
        msg = messages[i] or {}
        role = msg.get("role")

        if role == "system":
            text = msg.get("content")
            if text:
                systems.append(str(text))
            i += 1
            continue

        if role == "tool":
            results: list[dict] = []
            while i < len(messages) and (messages[i] or {}).get("role") == "tool":
                tm = messages[i] or {}
                results.append({
                    "type": "tool_result",
                    "tool_use_id": tm.get("tool_call_id") or "",
                    "content": _tool_result_content(tm.get("content")),
                })
                i += 1
            out.append({"role": "user", "content": results})
            continue

        if role == "assistant":
            sidecar = msg.get("_anthropic_blocks") if i == last_assistant else None
            content = sidecar if sidecar else _assistant_content(msg)
            if content:
                out.append({"role": "assistant", "content": content})
            i += 1
            continue

        if role == "user":
            content = msg.get("content")
            if isinstance(content, str):
                out.append({"role": "user", "content": [{"type": "text", "text": content}]})
            else:
                out.append({"role": "user", "content": _blocks(content)})
            i += 1
            continue

        i += 1

    return ("\n\n".join(systems) if systems else None), out


# ── SSE → OpenAI 形状的 chunk ──────────────────────────────────────────

_STOP_REASONS = {
    "tool_use": "tool_calls",
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
}

_ERROR_STATUS = {
    "overloaded_error": 529,
    "rate_limit_error": 429,
    "authentication_error": 401,
    "permission_error": 403,
    "not_found_error": 404,
    "invalid_request_error": 400,
    "timeout_error": 408,
    "api_error": 500,
}


class AnthropicStreamError(Exception):
    """Anthropic 流本身报错 / 没收到终止事件 / SSE 不是 JSON。

    带 `.status_code` 是为了让 `llm._worth_waiting` 用**同一张状态码表**判断
    值不值得重试 —— `overloaded_error` 是 529，`invalid_request_error` 是 400。
    """

    def __init__(self, message: str, *, status_code: int = 500):
        super().__init__(message)
        self.status_code = status_code


def _iter_sse(lines):
    """把 SSE 行流切成 `(event, data)`；空行是事件边界。"""
    event = None
    data: list[str] = []
    for raw in lines:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        line = raw.rstrip("\r")
        if line == "":
            if data:
                yield event, "\n".join(data)
            event, data = None, []
            continue
        if line.startswith(":"):
            continue
        field, sep, value = line.partition(":")
        if not sep:
            continue
        if field == "event":
            event = value.strip()
        elif field == "data":
            data.append(value[1:] if value.startswith(" ") else value)
    if data:
        yield event, "\n".join(data)


def _chunk(content=None, tool_calls=None, finish=None):
    delta = {}
    if content is not None:
        delta["content"] = content
    if tool_calls is not None:
        delta["tool_calls"] = tool_calls
    return {"choices": [{"delta": delta, "finish_reason": finish}]}


def anthropic_stream(lines):
    """Anthropic SSE → OpenAI 形状的 chunk 流。

    thinking / signature 只进旁路（`_anthropic_blocks`），**绝不进** `delta.content`
    —— 否则 `_Gate` 会把思考当叙述记进账本。
    """
    input_tokens = 0
    output_tokens = 0
    finish = None
    seen_stop = False
    blocks: dict[int, dict] = {}
    sidecar: list[dict] = []
    tool_index = 0

    for event, raw in _iter_sse(lines):
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AnthropicStreamError("Anthropic SSE data 不是 JSON: %s" % exc,
                                       status_code=500) from exc
        typ = payload.get("type") or event

        if typ == "ping":
            continue

        if typ == "error":
            err = payload.get("error") or {}
            kind = err.get("type") or "api_error"
            raise AnthropicStreamError(
                "Anthropic 流报错（%s）：%s" % (kind, str(err.get("message") or "")[:300]),
                status_code=_ERROR_STATUS.get(kind, 500))

        if typ == "message_start":
            usage = (payload.get("message") or {}).get("usage") or {}
            input_tokens = int(usage.get("input_tokens") or 0)
            continue

        if typ == "content_block_start":
            idx = int(payload.get("index") or 0)
            block = payload.get("content_block") or {}
            kind = block.get("type")
            if kind == "text":
                state = {"type": "text", "text": block.get("text") or ""}
                if state["text"]:
                    yield _chunk(content=state["text"])
            elif kind == "tool_use":
                initial = block.get("input")
                if not isinstance(initial, dict):
                    initial = None
                state = {"type": "tool_use", "id": block.get("id") or "",
                         "name": block.get("name") or "", "initial": initial,
                         "fragments": [], "emitted": False, "_tool_index": tool_index}
                tool_index += 1
                args = json.dumps(initial, ensure_ascii=False) if initial else ""
                state["emitted"] = bool(initial)
                yield _chunk(tool_calls=[{"index": state["_tool_index"],
                                          "id": state["id"],
                                          "function": {"name": state["name"],
                                                       "arguments": args}}])
            elif kind == "thinking":
                state = {"type": "thinking", "thinking": block.get("thinking") or "",
                         "signature": block.get("signature") or ""}
            elif kind == "redacted_thinking":
                state = {"type": "redacted_thinking", "data": block.get("data")}
            else:
                state = dict(block)
            blocks[idx] = state
            continue

        if typ == "content_block_delta":
            idx = int(payload.get("index") or 0)
            delta = payload.get("delta") or {}
            kind = delta.get("type")
            state = blocks.get(idx)
            if kind == "text_delta":
                text = delta.get("text") or ""
                if state is None:
                    state = {"type": "text", "text": ""}
                    blocks[idx] = state
                state["text"] = (state.get("text") or "") + text
                if text:
                    yield _chunk(content=text)
            elif kind == "thinking_delta":
                if state is not None:
                    state["thinking"] = (state.get("thinking") or "") + (delta.get("thinking") or "")
            elif kind == "signature_delta":
                if state is not None:
                    state["signature"] = (state.get("signature") or "") + (delta.get("signature") or "")
            elif kind == "input_json_delta":
                if state is not None and state.get("type") == "tool_use" and not state.get("initial"):
                    frag = delta.get("partial_json") or ""
                    state["fragments"].append(frag)
                    state["emitted"] = True
                    if frag:
                        yield _chunk(tool_calls=[{"index": state["_tool_index"],
                                                  "function": {"arguments": frag}}])
            continue

        if typ == "content_block_stop":
            idx = int(payload.get("index") or 0)
            state = blocks.pop(idx, None)
            if state is None:
                continue
            kind = state.get("type")
            if kind == "text":
                if state.get("text"):
                    sidecar.append({"type": "text", "text": state["text"]})
            elif kind == "thinking":
                block = {"type": "thinking", "thinking": state.get("thinking") or ""}
                if state.get("signature"):
                    block["signature"] = state["signature"]
                sidecar.append(block)
            elif kind == "redacted_thinking":
                sidecar.append({"type": "redacted_thinking", "data": state.get("data")})
            elif kind == "tool_use":
                if state.get("initial"):
                    inp = state["initial"]
                elif state.get("fragments"):
                    raw_args = "".join(state["fragments"])
                    try:
                        inp = json.loads(raw_args)
                    except Exception:            # noqa: BLE001 —— 坏参数交给 run_tool_loop 判
                        inp = {}
                    if not isinstance(inp, dict):
                        inp = {}
                else:
                    inp = {}
                    if not state.get("emitted"):
                        yield _chunk(tool_calls=[{"index": state["_tool_index"],
                                                  "function": {"arguments": "{}"}}])
                        state["emitted"] = True
                sidecar.append({"type": "tool_use", "id": state.get("id") or "",
                                "name": state.get("name") or "", "input": inp})
            continue

        if typ == "message_delta":
            stop = (payload.get("delta") or {}).get("stop_reason")
            usage = payload.get("usage") or {}
            if usage.get("output_tokens") is not None:
                output_tokens = int(usage.get("output_tokens") or 0)
            if stop:
                finish = _STOP_REASONS.get(stop, stop)
                yield _chunk(finish=finish)
            continue

        if typ == "message_stop":
            seen_stop = True
            yield {"choices": [], "usage": {
                "prompt_tokens": input_tokens,
                "completion_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
            }, "_anthropic_blocks": sidecar}
            continue

    if not seen_stop:
        raise AnthropicStreamError("Anthropic 流提前结束（没收到 message_stop）",
                                   status_code=500)


class AnthropicTransport:
    """最小的传输层：对外只暴露 `chat.completions.create(**kwargs)`。

    调用方（`llm._Router`）负责按模型选路；这一层只把 OpenAI 形状的入参
    翻成 Anthropic 请求，再把 SSE 还原成 OpenAI 形状的 chunk。
    """

    def __init__(self, http, *, base_url: str, api_key: str, timeout: float,
                 max_retries: int = 0, retryable_status=frozenset()):
        self._http = http
        self._url = str(base_url).rstrip("/") + "/messages"
        self._api_key = api_key
        self._timeout = timeout
        self._max_retries = max(0, int(max_retries))
        self._retryable_status = frozenset(retryable_status)
        self.chat = _Namespace(completions=_Namespace(create=self._create))

    def _create(self, **kwargs):
        max_tokens = kwargs.get("max_tokens")
        if not max_tokens:
            raise ValueError("Anthropic 传输层需要 max_tokens")
        system, messages = to_anthropic(kwargs.get("messages") or [])
        body = {"model": kwargs.get("model") or "", "max_tokens": max_tokens,
                "stream": True, "messages": messages}
        if system:
            body["system"] = system
        tools = to_anthropic_tools(kwargs.get("tools") or [])
        if tools:
            body["tools"] = tools
        return self._stream(body)

    def _headers(self) -> dict:
        return {"x-api-key": self._api_key,
                "anthropic-version": ANTHROPIC_VERSION,
                "content-type": "application/json"}

    def _retryable(self, exc) -> bool:
        if isinstance(exc, AnthropicStreamError):
            return exc.status_code in self._retryable_status
        if isinstance(exc, httpx.HTTPStatusError):
            return exc.response.status_code in self._retryable_status
        return isinstance(exc, (httpx.TimeoutException, httpx.ConnectError,
                                httpx.ReadError, httpx.RemoteProtocolError,
                                httpx.WriteError))

    def _stream(self, body):
        """流式 POST。只在**还没吐过 chunk** 时立即重试；已吐过的断流交给外层。"""
        for attempt in range(self._max_retries + 1):
            emitted = False
            try:
                with self._http.stream("POST", self._url, headers=self._headers(),
                                       json=body, timeout=self._timeout) as resp:
                    resp.raise_for_status()
                    for chunk in anthropic_stream(resp.iter_lines()):
                        emitted = True
                        yield chunk
                return
            except BaseException as exc:         # noqa: BLE001 —— `_Stop` 原样穿过
                if not isinstance(exc, Exception):
                    raise
                if emitted or attempt >= self._max_retries or not self._retryable(exc):
                    raise
