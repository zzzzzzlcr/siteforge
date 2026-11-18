"""主备模型切换 + 熔断。

这一族的边界：**只测切换与熔断的接线**。退避本身、流解析本身由
`tests/test_llm.py` / `tests/test_llm_anthropic.py` 钉着。
"""

import importlib
import json
import types

import httpx
import openai
import pytest

from agent import llm


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
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


# ── Task 5：空流 / 坏参数是硬失败 ──────────────────────────────────────


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
        # `messages` 是同一个列表对象 —— 当场快照角色，否则事后看到的是追加后的样子。
        calls.append({"model": kw["model"], "roles": [m["role"] for m in kw["messages"]]})
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
    assert calls[-1]["roles"] == ["system", "user"]


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
        calls.append({"model": kw["model"], "roles": [m["role"] for m in kw["messages"]]})
        if len(calls) == 1:
            return _bad_args()
        return iter([{"choices": [{"delta": {"content": "好了"}, "finish_reason": "stop"}]}])

    cli = types.SimpleNamespace(chat=types.SimpleNamespace(
        completions=types.SimpleNamespace(create=create)))
    rounds = fresh.run_tool_loop("sys", "usr", [], lambda n, a: None,
                                 max_rounds=1, _client=cli)
    assert rounds[0]["content"] == "好了"
    assert calls[-1]["roles"] == ["system", "user"]


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
