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
    """一个只记账的 OpenAI 客户端替身（`run_tool_loop` 认 `_client=`）。

    ★ 2026-10-14：它回的**是一股流**，不是一个响应 —— `run_tool_loop` 改走流式了
    （见本文件「流式」那一节）。这一格跟着契约改，不改判据。
    """
    seen: dict = {}

    def create(**kwargs):
        seen.update(kwargs)
        return iter([{"choices": [{"delta": {"content": "看过了，没什么问题"},
                                   "finish_reason": "stop"}], "usage": None}])

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


# ── 模型这一跳挂了的**现场**（★ 2026-10-14）───────────────────────────

def _boom_client(exc, attempts=None):
    """一个**一定抛**的客户端替身；`attempts` 模拟 SDK 那几个重试请求。"""
    def create(**kwargs):
        raise exc

    chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=create))
    fake = types.SimpleNamespace(chat=chat)
    if attempts is not None:
        fake._siteforge_attempts = attempts
    return fake


def test_the_clients_timeout_and_retries_are_written_down_not_inherited():
    """★ 2026-10-14：这两个值原先**藏在 SDK 默认里**（`OpenAI(base_url=…, api_key=…)` 裸着建）。

    为什么值一条测试：8 趟 job 被 `nginx 504` 打死，而「跑了多久 / 试了几次」这两格
    **一个都没记** ⇒ `__error__` 里只剩一页 HTML，三个假设（生成久 / 输入大 / SDK 在重试）
    一个都证不了。**默认值可以就是 SDK 那两个**，但它得**写在我们这儿** ——
    不然下一个人（或下一次调参）得先去翻库。
    """
    assert llm.DEFAULT_TIMEOUT_SECONDS == 600.0, llm.DEFAULT_TIMEOUT_SECONDS
    assert llm.DEFAULT_MAX_RETRIES == 2, llm.DEFAULT_MAX_RETRIES


def test_the_clients_timeout_and_retries_can_be_turned_by_env(monkeypatch):
    """要能**不改代码**把它们调下来 —— 那正是「闸按下 → 180 秒 → 504」这条线索的下一步。"""
    monkeypatch.setenv("SITEFORGE_LLM_TIMEOUT", "45")
    monkeypatch.setenv("SITEFORGE_LLM_RETRIES", "0")
    fresh = importlib.reload(llm)
    assert fresh.timeout_seconds() == 45.0
    assert fresh.max_retries() == 0


def test_a_bad_env_value_keeps_the_safe_default_for_llm_knobs(monkeypatch):
    """读不出来 / 不是正数 ⇒ **留默认值**（与 `SPIKE_MAX_TOKENS` 同一条口径）。"""
    monkeypatch.setenv("SITEFORGE_LLM_TIMEOUT", "  ")
    monkeypatch.setenv("SITEFORGE_LLM_RETRIES", "-3")
    fresh = importlib.reload(llm)
    assert fresh.timeout_seconds() == llm.DEFAULT_TIMEOUT_SECONDS
    assert fresh.max_retries() == llm.DEFAULT_MAX_RETRIES
    monkeypatch.setenv("SITEFORGE_LLM_TIMEOUT", "abc")
    fresh = importlib.reload(fresh)
    assert fresh.timeout_seconds() == llm.DEFAULT_TIMEOUT_SECONDS


class _Req:
    """一个假的 httpx 请求（钩子只读这两样）。"""
    def __init__(self, retry_count=None, body=b""):
        self.headers = {} if retry_count is None else {"x-stainless-retry-count": retry_count}
        self.content = body


def test_every_request_the_sdk_sends_is_counted():
    """记账钩子：**SDK 每次重试 = 一次新请求**，而它自己把第几次打在头上
    （`x-stainless-retry-count`）—— 读它比自己数更可信。

    这一格是「180 秒 = 3 × 60」那条线索的唯一量具：没有它，就分不清
    「一次跑了 180 秒」和「三次各跑了 60 秒」。
    """
    box: list = []
    llm._note_attempt(_Req("0", b"x" * 10), box)
    llm._note_attempt(_Req("1", b"x" * 20), box)
    llm._note_attempt(_Req(None, b"x" * 30), box)      # 老 SDK 不打这个头 ⇒ 也得记
    assert [a["retry_count"] for a in box] == ["0", "1", None], box
    assert [a["body_chars"] for a in box] == [10, 20, 30], box


def test_a_failed_call_says_how_long_and_how_many_requests_went_out():
    """★★ 这条是那件事的**第一段**：挂的时候要把现场带出来。

    落进 job 状态的只有 `str(exc)` ⇒ 现场必须**写进消息里**。要四样：
    耗时、**发出去几个 HTTP 请求**（= SDK 有没有在默默重试）、这次客户端的旋钮、原错。
    """
    boom = RuntimeError("<html><head><title>504 Gateway Time-out</title></head>"
                        "<body><center><h1>504 Gateway Time-out</h1></center>"
                        "<hr><center>nginx</center></body></html>")
    cli = _boom_client(boom)
    with pytest.raises(llm.ModelCallFailed) as e:
        llm.run_tool_loop("sys", "usr", [], lambda name, args: None,
                          max_rounds=1, _client=cli)
    said = str(e.value)
    assert "耗时" in said, said
    assert "504 Gateway Time-out" in said, ("原错不许吞掉", said)
    assert isinstance(e.value.__cause__, RuntimeError), "原错挂 __cause__，一个字节没丢"


def test_the_call_context_says_the_attempt_count_and_the_knobs():
    """现场那句话本身：**几次请求 / 旋钮是什么 / 请求多大**，一个都不许少。"""
    box = [{"retry_count": "0", "body_chars": 111},
           {"retry_count": "1", "body_chars": 111},
           {"retry_count": "2", "body_chars": 111}]
    said = llm.format_call_context(box, 181.4, "deepseek-v4-flash",
                                   [{"role": "user", "content": "x" * 50}])
    assert "耗时 181.4 秒" in said, said
    assert "3 个 HTTP 请求" in said, ("要能一眼看出「试了 3 次」—— 那正是 180 秒的来历", said)
    assert "max_retries=2" in said, said
    assert "timeout=600" in said, said
    assert "deepseek-v4-flash" in said, said


def test_the_call_context_says_so_when_no_request_ever_went_out():
    """**一个请求都没发出去**是另一件事（连不上 / 被拦下），不许读成「重试了 0 次」。"""
    said = llm.format_call_context([], 12.0, "m", [{"role": "user", "content": "x"}])
    assert "一个都没发出去" in said, said
    assert "0 个 HTTP 请求" not in said, said


def test_a_stop_from_the_human_is_not_wrapped():
    """`_Stop`（人喊停）是 **BaseException 而不是 Exception** —— 它必须**原样**往上走。

    包装它 = 把「人按了停」变成「模型挂了」，屏幕上两件事长得一样而意思相反。
    """
    class _Stop(BaseException):
        pass

    cli = _boom_client(_Stop("人喊停了"))
    with pytest.raises(_Stop):
        llm.run_tool_loop("sys", "usr", [], lambda name, args: None,
                          max_rounds=1, _client=cli)


# ── 流式（★ 2026-10-14）：治「60 秒 nginx 读超时」─────────────────────
#
# 现场：`llm.3tkj.cn` 前面那层 nginx 读超时 **60 秒**（实测 60.1s → 504、42.8s → 200），
# 而**非流式在生成完成之前一个字节都不吐** ⇒ 只要这一次生成超过 60 秒就被掐；
# SDK 默认重试 2 次 ⇒ 3 × 60 ≈ 180 秒后整个 job 死（8 趟实测，全死在 `draft`）。
# 流式**一秒钟就开始吐字节**（实测首字节 1.3s）⇒ 那条「读」的超时永远不触发。

def _tc(index, cid=None, name=None, args=None):
    """tool_calls 的一片增量（形状照网关实测：`id`/`name` 只在首片，`arguments` 分片给）。"""
    fn = {}
    if name is not None: fn["name"] = name
    if args is not None: fn["arguments"] = args
    piece = {"index": index}
    if cid is not None: piece["id"] = cid
    if fn: piece["function"] = fn
    return piece


def _chunk(content=None, tool_calls=None, finish=None, usage=None, with_choice=True):
    """一片 chunk。`with_choice=False` = 实测里那一片**只带 usage、`choices` 是空数组**的收尾片。"""
    msg = {"choices": [], "usage": usage}
    if with_choice:
        delta = {}
        if content is not None: delta["content"] = content
        if tool_calls is not None: delta["tool_calls"] = tool_calls
        msg["choices"] = [{"delta": delta, "finish_reason": finish}]
    return msg


def _stream_client(chunks):
    """一个**只会流**的客户端替身（`run_tool_loop` 认 `_client=`）；`seen` 记下 kwargs。"""
    seen: dict = {}

    def create(**kwargs):
        seen.update(kwargs)
        return iter(chunks)

    chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=create))
    return types.SimpleNamespace(chat=chat), seen


def test_the_call_asks_for_a_stream():
    """★★ 这一条**就是那个修法本身**：请求必须走流式。

    非流式 = 生成完成前一个字节都不吐 ⇒ 网关前面那层 nginx 的 60 秒读超时必然触发。
    少了 `stream=True`，后面所有拼装都是白搭。
    """
    cli, seen = _stream_client([_chunk("你好", finish="stop")])
    llm.run_tool_loop("sys", "usr", [], lambda name, args: None, max_rounds=1, _client=cli)
    assert seen.get("stream") is True, seen
    assert (seen.get("stream_options") or {}).get("include_usage") is True, (
        "不点名要 usage，流式就**拿不到 token 账** —— 那是预算那条线（C1）的依据", seen)


def test_a_streamed_reply_is_put_back_together():
    """content 是一片一片来的 —— 拼回去必须与一次给全**逐字相同**。"""
    cli, _ = _stream_client([
        _chunk("我看"), _chunk("了一眼"), _chunk("，没什么问题", finish="stop")])
    rounds = llm.run_tool_loop("sys", "usr", [], lambda name, args: None,
                               max_rounds=1, _client=cli)
    assert rounds[0]["content"] == "我看了一眼，没什么问题", rounds[0]["content"]


def test_tool_call_arguments_arriving_in_pieces_are_joined():
    """★ 流式最经典的坑：`arguments` 是一段一段来的，**拼错了模型就调不动工具**。

    `id` 与 `name` 只在**第一片**上有，后面全是 `arguments` 的碎片。
    """
    cli, _ = _stream_client([
        _chunk(tool_calls=[_tc(0, cid="call_1", name="observe", args="")]),
        _chunk(tool_calls=[_tc(0, args='{"wh')]),
        _chunk(tool_calls=[_tc(0, args='y": "看')]),
        _chunk(tool_calls=[_tc(0, args='一眼"}')]),
        _chunk(finish="tool_calls"),
    ])
    seen: list = []

    def dispatch(name, args):
        seen.append((name, args))
        return "ok"

    rounds = llm.run_tool_loop("sys", "usr", [], dispatch, max_rounds=1, _client=cli)
    assert seen == [("observe", {"why": "看一眼"})], seen
    call = rounds[0]["tool_calls"][0]
    assert call["id"] == "call_1", call
    assert call["args_raw"] == '{"why": "看一眼"}', call
    assert call["error"] is None, call


def test_two_tool_calls_in_one_reply_stay_separate():
    """一次回复里调两个工具时，两串碎片是**交错**来的 —— 分开靠 `index`，不是靠顺序。"""
    cli, _ = _stream_client([
        _chunk(tool_calls=[_tc(0, cid="a", name="observe", args='{"p"')]),
        _chunk(tool_calls=[_tc(1, cid="b", name="screenshot", args='{"q"')]),
        _chunk(tool_calls=[_tc(0, args=': 1}')]),
        _chunk(tool_calls=[_tc(1, args=': 2}')]),
        _chunk(finish="tool_calls"),
    ])
    got: list = []
    rounds = llm.run_tool_loop("sys", "usr", [], lambda n, a: got.append((n, a)) or "ok",
                               max_rounds=1, _client=cli)
    assert [c["name"] for c in rounds[0]["tool_calls"]] == ["observe", "screenshot"], rounds[0]
    assert got == [("observe", {"p": 1}), ("screenshot", {"q": 2})], got


def test_the_usage_only_chunk_does_not_break_the_assembly():
    """收尾那一片**只带 usage、`choices` 是空数组**（网关实测就是这样）——
    按 `choices[0]` 硬取的地方会 IndexError。"""
    cli, _ = _stream_client([
        _chunk("好", finish="stop"),
        _chunk(usage={"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9},
               with_choice=False),
    ])
    rounds = llm.run_tool_loop("sys", "usr", [], lambda n, a: None, max_rounds=1, _client=cli)
    assert rounds[0]["usage"]["total_tokens"] == 9, rounds[0]["usage"]
    assert rounds[0]["finish_reason"] == "stop", rounds[0]["finish_reason"]


def test_a_reply_that_calls_no_tool_still_stops_the_loop():
    """「模型直接说话」= 没调工具 ⇒ 停。这条是**要量的那件事**，流式不许把它弄丢。"""
    cli, _ = _stream_client([_chunk("说完了", finish="stop")])
    rounds = llm.run_tool_loop("sys", "usr", [], lambda n, a: None, max_rounds=3, _client=cli)
    assert len(rounds) == 1, rounds


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
