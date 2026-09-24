"""`agent/llm.py` 的预算契约（**C1**）—— 不跑模型也要钉住的那一半。

**为什么单独一个文件**：spike 那套测试（`tests/test_tool_loop.py`）整份被
`RUN_LLM=1` 挡着（打真模型、打真站），CI 里跑不到「预算」这条常量上；
而 C1 恰恰是**不跑模型就该钉住**的东西 —— 预算给少了最终答案是**空**的，
而空答案看起来跟「模型说没有」**一模一样**（spike 报告 §2 条件 1）。

三条钉子：
1. 默认 ≥12000（C1）；
2. 能被 `SPIKE_MAX_TOKENS` 覆盖，且**真的递到了模型**（常量放着不传等于没有）；
3. spike 那个 runner（`tests/test_tool_loop.py::_run`）也把预算递下去 ——
   报告里 `mt4000-*` / `mt12000-*` 那组矩阵就是它跑出来的，命令写在报告里。
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import llm  # noqa: E402


@pytest.fixture(autouse=True)
def _restore_module():
    """预算常量是**模块级**读的：每个用例跑完把模块恢复成「当前环境」的样子。"""
    yield
    importlib.reload(llm)


def test_default_budget_is_at_least_12000():
    """C1：**计划一的补偿证据**（4000 → 1/8 空答案；12000 → 5/5 正常）。"""
    assert llm.DEFAULT_MAX_TOKENS >= 12000, llm.DEFAULT_MAX_TOKENS


def test_budget_can_be_overridden_by_env(monkeypatch):
    """矩阵（mt4000 / mt12000）要能从仓库里跑出来 —— 不然那份补偿证据留不下来。"""
    monkeypatch.setenv("SPIKE_MAX_TOKENS", "4000")
    assert importlib.reload(llm).DEFAULT_MAX_TOKENS == 4000
    monkeypatch.delenv("SPIKE_MAX_TOKENS")
    assert importlib.reload(llm).DEFAULT_MAX_TOKENS >= 12000


def test_bad_env_value_keeps_the_safe_default(monkeypatch):
    """环境变量写错时**往安全的方向**退（默认 ≥12000），不静默压小预算、也不崩进程。"""
    for bad in ("八万", "0", "-1", " "):
        monkeypatch.setenv("SPIKE_MAX_TOKENS", bad)
        assert importlib.reload(llm).DEFAULT_MAX_TOKENS >= 12000, bad


def test_no_gateway_is_ever_guessed_at(monkeypatch):
    """★★ 2026-09-23（用户点名：「想法是都要走我 test.sh 那个配置的」）：
    **没给 `OPENAI_BASE_URL` 就不许建客户端** —— 一个请求都不发出去。

    为什么这一条值一条测试：这一格原先兜底 `https://api.deepseek.com` ⇒ 它把
    「env 没带上」变成「**静默打到别处**」，而屏幕上与「模型答不好」长得一模一样 ——
    那件事没人看得出来，而它花的是别人的钱、走的是别人的线路。

    两半都要量：没给 ⇒ 抛，而且那句话得**点名 `OPENAI_BASE_URL`**（不然读的人不知道
    该去配哪个变量）；给了 ⇒ **照常建得起来**（不然判据就退化成「一概不让建」）。
    """
    monkeypatch.setenv("OPENAI_API_KEY", "dummy")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    fresh = importlib.reload(llm)
    assert fresh.DEFAULT_BASE_URL == "", "又给这一格兜了一个地址？"
    with pytest.raises(RuntimeError) as e:
        fresh.client()
    said = str(e.value)
    assert "OPENAI_BASE_URL" in said, said
    assert "没设" in said, said
    #: 还得说清「这一格**故意**没有默认值」—— 不然下一个人会顺手加一个回来。
    assert "默认" in said or "兜" in said, said
    #: 正控：给了就建得起来（判据是「不许猜」，不是「不许建」）。
    monkeypatch.setenv("OPENAI_BASE_URL", "https://gw.example.test/v1")
    built = importlib.reload(fresh).client()
    assert str(built.base_url).rstrip("/") == "https://gw.example.test/v1"


# ── 「递到了模型」这一半：常量放着不传等于没有 ────────────────────────

class _Msg:
    def __init__(self, content="", tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _Resp:
    def __init__(self, content="", tool_calls=None, finish_reason="stop"):
        self.choices = [types.SimpleNamespace(message=_Msg(content, tool_calls),
                                              finish_reason=finish_reason)]
        self.usage = None


def _spy_client():
    """一个只记账的 OpenAI 客户端替身（`run_tool_loop` 认 `_client=`）。"""
    seen: dict = {}

    def create(**kwargs):
        seen.update(kwargs)
        return _Resp("看过了，没什么问题")

    chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=create))
    return types.SimpleNamespace(chat=chat), seen


def test_run_tool_loop_sends_the_budget(monkeypatch):
    cli, seen = _spy_client()
    rounds = llm.run_tool_loop("sys", "usr", [], lambda name, args: None,
                               max_rounds=1, _client=cli)
    assert seen["max_tokens"] >= 12000, seen
    assert rounds and rounds[0]["content"], "循环本身应该照常走完一轮"


def test_run_tool_loop_honours_the_env_budget(monkeypatch):
    monkeypatch.setenv("SPIKE_MAX_TOKENS", "9000")
    importlib.reload(llm)                       # 模块级读 env（与 SPIKE_MODEL 同一套约定）
    cli, seen = _spy_client()
    llm.run_tool_loop("sys", "usr", [], lambda name, args: None, max_rounds=1, _client=cli)
    assert seen["max_tokens"] == 9000, seen


# ── spike 的 runner（矩阵就是它跑出来的）─────────────────────────────

def _load_spike_tests():
    """按文件 import `tests/test_tool_loop.py`（**不 collect**：它整份在 RUN_LLM 门后）。"""
    path = ROOT / "tests" / "test_tool_loop.py"
    spec = importlib.util.spec_from_file_location("siteforge_spike_runner", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_spike_runner_passes_the_env_budget(monkeypatch, tmp_path):
    """`_run()` 把预算递给 `run_tool_loop`，并把它写进 dump。

    这条顺带**在门开着的情况下**摸了一遍 runner 的模块级引用
    （`tools.make_spike_dispatch` / `tools.spike_specs`）—— 那些引用一旦过期，
    默认 skip 的那份测试跑不出来，而这条会红。
    """
    monkeypatch.setenv("SPIKE_MAX_TOKENS", "12345")      # ≠ 默认值：不然「没接上」也看不出来
    monkeypatch.setenv("SPIKE_DUMP_DIR", str(tmp_path))
    spike = _load_spike_tests()
    assert spike.MAX_TOKENS == 12345, "预算要能被环境变量改 —— 矩阵靠它"

    seen: dict = {}

    def fake_loop(*args, **kwargs):
        seen.update(kwargs)
        return []

    monkeypatch.setattr(spike.llm, "run_tool_loop", fake_loop)
    res = spike._run("budget-rep-test", spike.Q1, max_rounds=1)

    assert seen["max_tokens"] == 12345, seen
    assert res["max_tokens"] == 12345, res
    dumped = json.loads((tmp_path / "budget-rep-test.json").read_text(encoding="utf-8"))
    assert dumped["max_tokens"] == 12345, dumped         # 每份证据自带预算
