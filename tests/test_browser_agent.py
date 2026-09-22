"""Task 5：浏览器 Agent 的工具循环（ReAct over MCP）的契约测试。

## 为什么这些测试长这样

spike（Task 1）量到三件事，每一条都在这条循环里变成一个**具体的**钉子：

- **C1** `max_tokens` ≥ 12000 —— reasoning token 算在里面，给少了最终答案是**空**的，
  而空答案看起来与「模型说没有」一模一样 → `test_max_tokens_is_at_least_12000`
- **C2** 一轮里可能有**多个** `tool_calls`，只处理 `[0]` 的实现会静默丢掉观测
  → `test_all_tool_calls_in_one_turn_reach_the_model`
- **C3** 终止要认「**没有** `tool_calls`」（七个 MCP 工具里**没有** `done()`）
  → `test_no_tool_calls_terminates`

## spike 没验到的那件事（本文件里最重的一条）

`observe` 是只读的，模型有理由把三次 observe 并进一轮 —— spike 测到的「连续 ≥3 轮」
是**同一件事看了三遍**，不是依赖链。`click` 会改页面、天然不能批量。
所以有一条**真浏览器**的端到端：**点一下 → 再 observe → 看到的必须是变了的页面**
（`test_dependency_chain_click_then_observe_changed_page`）。它是这条循环第一次真的走依赖链。

## 桩 MCP 与真浏览器的分工

- **桩**（`tests/stub_mcp_server.py`，真 MCP over stdio，只是不打浏览器）测循环本身：
  预算、暂停、每步入账、工具报错。拿真浏览器测这些，红绿都要先排除「窗口挂了 / 页面慢」。
- **真**（私有 headless Chrome + 真 `cmd/mcp` 二进制 + 本地夹具页）测依赖链。
  ⚠️ **绝不碰共享的 9222** —— 那是别的 agent 与本机其它东西的页面（计划一栽过：
  两个测试二进制抢同一个页面目标，观测到的是对方注入的 DOM）。
"""

from __future__ import annotations

import ast
import http.server
import importlib.util
import json
import os
import pathlib
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import browser_agent, journal, lint, llm, plan, template, tools  # noqa: E402

STUB_SERVER = ROOT / "tests" / "stub_mcp_server.py"


# ─────────────────────────── 桩 LLM ───────────────────────────
#
# 真实的 `llm.run_tool_loop` 要的是一小片 OpenAI 的形状：
# `cli.chat.completions.create(model=, messages=, tools=, max_tokens=)` → 一个带
# `choices[0].message.{content,tool_calls}` 的回复。这里就按那一片造。
#
# ⚠️ 它**把每次调用收到的 messages 原样记下来** —— 这就是 C2 的判据：
# 「三个工具的结果有没有都到模型手里」只能在**下一轮它看到的东西**里验，
# 在 agent 自己的账本里验是自证。


def _reply(content: str = "", calls: list | None = None):
    tool_calls = [
        types.SimpleNamespace(
            id=f"call_{i}",
            function=types.SimpleNamespace(name=name, arguments=json.dumps(args, ensure_ascii=False)),
        )
        for i, (name, args) in enumerate(calls or [])
    ]
    msg = types.SimpleNamespace(content=content, tool_calls=tool_calls or None)
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=msg, finish_reason="tool_calls" if tool_calls else "stop")],
        usage=None,
    )


class FakeLLM:
    """按剧本一轮一轮回的假模型（剧本用完就重复最后一条）。"""

    def __init__(self, turns: list):
        self.turns = list(turns)
        self.calls: list[dict] = []

    @property
    def chat(self):
        return self

    @property
    def completions(self):
        return self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        turn = self.turns[min(len(self.calls) - 1, len(self.turns) - 1)]
        return _reply(turn.get("content", ""), turn.get("calls"))


# ─────────────────────── 桩 MCP（真协议，不打浏览器）───────────────────────

PAGE_LANDING = {
    "url": "https://example.test/funnel",
    "title": "Example 漏斗",
    "page_text": "Get Started 先看看你能省多少",
    "shadow_roots": 0,
    "viewport_css_px": {"width": 1280, "height": 800},
    "actions": [
        {
            "selector": "#get-started", "alternates": ["a.btn-primary"], "stability": "high",
            "text": "Get Started", "role": "button", "tag": "A", "type": "", "visible": True,
            "occluded_by": None, "shadow_depth": 0, "frame_path": ["main"],
            "bbox": [10, 20, 120, 40], "region": "hero", "above_fold": True,
            "relative_size": 0.1, "peer_count": 1, "z_index": "auto", "contrast": "",
            "nearby_text": [],
        }
    ],
    "fields": [],
    "option_groups": [],
    "obstructions": [],
    "honeypots": [],
    "diagnostics": [],
}

PAGE_QUIZ = {
    "url": "https://example.test/funnel?step=2",
    "title": "Example 漏斗 第 2 步",
    "page_text": "你多久用一次？每天 每周 很少",
    "shadow_roots": 0,
    "viewport_css_px": {"width": 1280, "height": 800},
    "actions": [
        {
            "selector": "#opt-daily", "alternates": [], "stability": "high", "text": "每天",
            "role": "option", "tag": "BUTTON", "type": "", "visible": True, "occluded_by": None,
            "shadow_depth": 0, "frame_path": ["main"], "bbox": [10, 300, 200, 60],
            "region": "main", "above_fold": True, "relative_size": 0.2, "peer_count": 3,
            "z_index": "auto", "contrast": "", "nearby_text": [],
        }
    ],
    "fields": [
        {
            "selector": "#postcode", "alternates": ["input[name=zip]"], "stability": "high",
            "label": "Postcode", "hint": "你的邮编", "placeholder": "", "type": "text",
            "required": True, "shadow_depth": 0, "frame_path": ["main"],
        }
    ],
    "option_groups": [],
    "obstructions": [],
    "honeypots": [],
    "diagnostics": [],
}


def _stub(tmp_path: pathlib.Path, responses: dict, tools_table: list | None = None) -> tuple:
    """起一个桩 MCP 会话。返回 (session, 调用流水线路径)。

    `tools_table` 给了就由**测试**决定那道门的 `tools/list` 回什么（不给用桩的默认表）——
    这是「工具声明来自门、不是本地第二份表」唯一验得出来的办法（见
    `test_tool_specs_come_from_the_mcp_server`）。
    """
    program = tmp_path / "program.json"
    log = tmp_path / "calls.jsonl"
    payload: dict = {"log": str(log), "responses": responses}
    if tools_table is not None:
        payload["tools"] = tools_table
    program.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    session = tools.McpSession([sys.executable, str(STUB_SERVER), str(program)])
    return session, log


def _calls(log: pathlib.Path) -> list:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


def _run(tmp_path, responses, turns, tools_table: list | None = None, **kwargs):
    """一次标准的桩跑法：桩 MCP + 假模型 + 一条探路目标。"""
    session, log = _stub(tmp_path, responses, tools_table)
    fake = FakeLLM(turns)
    try:
        journey = browser_agent.explore(
            "https://example.test/funnel", "看看这一页怎么走到报价", session=session, client=fake, **kwargs
        )
    finally:
        session.close()
    return journey, fake, _calls(log)


# ─────────────────────────── 预算 ───────────────────────────


def test_budget_exhaustion_stops(tmp_path):
    """预算到顶会停 —— 而且是**停在下一步之前**，不是「这一步做完顺便停」。"""
    journey, fake, calls = _run(
        tmp_path,
        {"observe": [{"structured": PAGE_LANDING}]},
        [{"calls": [("observe", {})]}],           # 模型每轮都还想再看一眼
        budget=browser_agent.Budget(max_steps=3, max_rounds=50),
    )
    assert len(journey.steps) == 3, f"预算 3 步，实际走了 {len(journey.steps)} 步"
    assert journey.stop_reason == "budget_steps"
    assert len(calls) == 4, (
        f"预算只允许 3 步 + 开跑前那一下站位的 goto，真发出去的却是 {len(calls)} 次")
    assert any("预算" in n for n in journey.notes), f"没告诉人为什么停：{journey.notes}"
    assert len(fake.calls) < 50, "预算到顶之后还在问模型"


def test_budget_counts_the_rounds_too(tmp_path):
    """轮数也要有上限 —— 模型可以一直只说话不调工具（那种情况步数不涨）。"""
    journey, fake, _ = _run(
        tmp_path,
        {"observe": [{"structured": PAGE_LANDING}]},
        [{"calls": [("observe", {})]}],
        budget=browser_agent.Budget(max_steps=99, max_rounds=2),
    )
    assert len(fake.calls) == 2, f"轮数上限 2，实际问了 {len(fake.calls)} 轮"
    assert journey.stop_reason == "budget_rounds"


# ─────────────── 开跑之前先站到目标页上（2026-09-20 真站实测）───────────────


def test_explore_stands_on_the_target_page_before_the_first_round(tmp_path):
    """★ 起点必须是**目标页**，不是 Bit 的工作台页。

    真站实测（2026-09-20，`job-36ab36b56754`）：窗口是 `fresh_open` 新开的 ⇒ 它落在
    **Bit 的工作台页**（`console.bitbrowser.net/…?id=…&port=…`）；而模型的第一个动作是
    `observe` —— 它看到的就是那个「**我们碰巧从那儿开始**」的旁枝，20 步预算全烧在上面，
    目标站一步没探，最后交白卷。

    原先靠「起点那页与后面每页都不同源 ⇒ 撤掉它的 `when`」来**绕**（`_branch_start_states`）——
    那是绕不是修：撤了判据，起点那组步骤在**重放**时就认不出来了。
    生产脚本的第一步也是 `goto`，同一个道理：**先站到起点上，再开始看**。
    """
    journey, fake, calls = _run(
        tmp_path,
        {"observe": [{"structured": PAGE_LANDING}]},
        [{"calls": [("observe", {})]}],
        budget=browser_agent.Budget(max_steps=99, max_rounds=1),
    )
    assert calls, "一次工具调用都没有？"
    first = calls[0]
    assert first["name"] == "goto", (
        "开跑的第一个动作不是 goto 而是 %r —— 起点会落在 Bit 的工作台页上" % first["name"])
    assert first["args"].get("url") == "https://example.test/funnel", first["args"]
    # 它**不算模型要的那一步**：模型的第一轮仍然照自己想的走
    assert [c["name"] for c in calls[1:]] == ["observe"], calls
    # 它**不算一步**（`journey.steps` 里没有它）—— 它是站位，不是探索
    assert [s["action"] for s in journey.steps] == ["observe"], journey.steps
    # 而且**不许静默**：这一下要在账本里说得出话
    assert any("起点是目标页" in n for n in journey.notes), journey.notes


def test_resume_does_not_navigate_first(tmp_path):
    """**重放那一路不补这一下** —— 账本第一步本来就是 goto，再导航一次是白跑一趟。

    ⚠️ 这一条的**正身不在我这儿**：`test_a_resume_walks_the_ledger_back_before_the_model_gets_a_turn`
    断言的是完整的 `["goto", "observe", "click", "observe"]` —— 重放那一路要是被多补一次导航，
    **它当场就红**。所以这里只钉一件事：**重放那一趟，goto 只出现一次**。
    """
    rows = _walk_rows()
    _journey, _fake, calls = _run(
        tmp_path,
        {"observe": _pages_for(rows)},
        [{"content": "我看到了报价页，接着往下走", "calls": []}],
        resume_from=rows,
    )
    assert sum(1 for c in calls if c["name"] == "goto") == 1, (
        "重放那一路被多补了一次导航：%r" % [c["name"] for c in calls])


# ─────────────────────── 人每一步都在（§6.2/D16）───────────────────────


def test_pause_exits_before_the_next_step(tmp_path):
    """`should_pause()` 为真 → **在下一步之前**退出，不是跑完再退。

    ⚠️ 模型把三件事**并进同一轮**（C2 那个形态）—— 于是「停」只能发生在
    **每一步之前**：只在轮边界检查的实现在这里会红（那一轮的三步做完才停）。
    判据落在两处**互相独立**的地方：agent 自己的账本（steps），
    以及桩服务那边的**调用流水线** —— 后者是「那一步真的没发生」的唯一硬证据。
    只在账本上验的话，「跑完了但没记账」也会绿。
    """
    journey, fake, calls = _run(
        tmp_path,
        {"observe": [{"structured": PAGE_LANDING}]},
        [{"calls": [("observe", {}), ("click", {"selector": "#get-started"}), ("observe", {})]}],
        budget=browser_agent.Budget(max_steps=50, max_rounds=50),
        should_pause=lambda j: len(j.steps) >= 1,
    )
    assert len(journey.steps) == 1, f"人喊停之后还走了 {len(journey.steps) - 1} 步"
    assert len(calls) == 2, (
        f"同一轮里剩下的调用照发了（跑了 {len(calls)} 次，跑完才退；头一下是开跑前的 goto）")
    assert journey.stop_reason == "paused"
    assert any("喊停" in n for n in journey.notes), f"没告诉人被谁停了：{journey.notes}"


def test_pause_between_rounds_also_stops(tmp_path):
    """每一轮的**边界**上也问一次 —— 与「每一步之前」是两道闸，缺一个都会漏。"""
    journey, fake, calls = _run(
        tmp_path,
        {"observe": [{"structured": PAGE_LANDING}]},
        [{"calls": [("observe", {})]}],
        budget=browser_agent.Budget(max_steps=50, max_rounds=50),
        should_pause=lambda j: len(j.steps) >= 2,
    )
    assert len(journey.steps) == 2 and len(calls) == 3   # +1 = 开跑前那一下 goto
    assert len(fake.calls) == 2, "人都喊停了，还在问模型"
    assert journey.stop_reason == "paused"


def test_pause_before_anything_runs_nothing(tmp_path):
    """一开始就该停 → 一次工具调用都不许发出去（含「先看一眼」都不看）。"""
    journey, fake, calls = _run(
        tmp_path,
        {"observe": [{"structured": PAGE_LANDING}]},
        [{"calls": [("observe", {})]}],
        should_pause=lambda j: True,
    )
    assert journey.steps == []
    assert calls == []
    assert journey.stop_reason == "paused"
    assert fake.calls == [], "人一上来就喊停，却还是问了模型"


def test_pause_predicate_may_take_no_arguments(tmp_path):
    """注入点两种写法都收：`should_pause(journey)` 与 `should_pause()`。"""
    journey, _, _ = _run(
        tmp_path,
        {"observe": [{"structured": PAGE_LANDING}]},
        [{"calls": [("observe", {})]}],
        should_pause=lambda: True,
    )
    assert journey.stop_reason == "paused" and journey.steps == []


def test_a_pause_gate_that_blows_up_stops_instead_of_failing_a_step(tmp_path):
    """**R-12**：人那道闸自己抛异常时，唯一正确的处置是**停下**，不是「这一步失败了，接着跑」。

    这道闸跑在 `dispatch` 里、在 `try` **外面**（那是对的：闸不该被工具的错误路径吃掉），
    但它抛出来的若是普通 `Exception`，就会落到 `llm.run_tool_loop` 的
    `except Exception` 上、被记成**一次工具失败**然后循环继续 ——
    人的中断**静默降级**成「有个步骤失败了，继续吧」。那是这条路上最坏的一种：
    喊停没停，而且没有任何人看得出来。

    判据：模型第一轮就丢来一个 observe，闸在**第 2 次**被问时炸掉 ——
    于是「正好问过 1 轮模型」是硬证据（`fake.calls`）。
    """
    asked: list = []

    def gate_that_blows_up(journey):
        asked.append(len(journey.steps))
        if len(asked) == 3:                 # ① 开跑前站位 ② 轮边界 ③ 步之前 —— 第 3 次炸
            raise RuntimeError("Console 的队列炸了")
        return False

    journey, fake, calls = _run(
        tmp_path,
        {"observe": [{"structured": PAGE_LANDING}]},
        [{"calls": [("observe", {})]}] * 5,
        budget=browser_agent.Budget(max_steps=50, max_rounds=5),
        should_pause=gate_that_blows_up,
    )
    assert journey.stop_reason == "paused", f"闸炸了却没停下，反而以 {journey.stop_reason} 收场"
    assert len(fake.calls) == 1, f"闸炸了之后还在问模型（问了 {len(fake.calls)} 轮）"
    assert journey.steps == [], f"闸炸了却还是走了 {len(journey.steps)} 步"
    # ⚠️ 开跑前那一下 `goto` 是**合法发出去的**：它自己过了入口那道闸（`_stop_or_raise`），
    # 闸是在它**之后**才炸的。这一条钉的性质没变：**炸了之后**一个都不许多发。
    assert [c["name"] for c in calls] == ["goto"], \
        f"闸炸了之后还是把工具调用发出去了：{calls}"
    assert any("闸" in n and "Console 的队列炸了" in n for n in journey.notes), (
        f"没把「是那道闸自己坏了」说给人听（不然人以为自己喊停了）：{journey.notes}"
    )


def test_paused_journey_still_carries_what_the_model_said(tmp_path):
    """**R-12**：被人打断的那次，**不该**比没被打断的那次知道得更少。

    `journey.notes` 的契约里写着「含模型自己的话」。可 `_Stop` 是 `BaseException`，
    它**穿过** `run_tool_loop` 直接落到 `explore` 的 `except` 里 —— 那些 `rounds`
    记录就此丢掉，于是「收尾时统一记叙述」的写法在暂停这条路上**一个字都不落**。
    人被暂停时正要靠那几句话判断「接下来要不要接着跑」，恰恰最需要它。
    """
    journey, _, _ = _run(
        tmp_path,
        {"observe": [{"structured": PAGE_LANDING}]},
        [{"content": "我先看一眼这一页，再决定要不要点。", "calls": [("observe", {})]},
         {"content": "看清楚了。", "calls": [("observe", {})]}],
        budget=browser_agent.Budget(max_steps=50, max_rounds=50),
        should_pause=lambda j: len(j.steps) >= 1,
    )
    assert journey.stop_reason == "paused" and len(journey.steps) == 1
    assert any("AI 说：" in n and "我先看一眼这一页" in n for n in journey.notes), (
        f"被暂停的这份 Journey 里，模型说的话一句都没留下：{journey.notes}"
    )
    # 但**没有**最终答案 —— 它是被人打断的，不是它讲完了（这两件事不能混）
    assert journey.final_answer == ""


def test_a_broken_on_step_hook_does_not_break_the_walk_but_says_so(tmp_path):
    """**Task 4（R-19 的那条规矩）**：旁路（实时视图 / 账本）坏掉**不许带塌主路**。

    为什么必须挡住：`on_step` 是在 `dispatch` 里调的，而 `dispatch` 抛出去的任何东西
    都会被 `llm.run_tool_loop` 记成**一次工具失败** —— 于是账本写不进去这件事，
    会以「这一步的工具失败了」的身份回到模型面前，模型还可能照着这条假错换一条路走。
    那是把**旁路的故障记到产物头上**（本项目最忌讳的形状）。

    但**不是静默**：事故要进 `journey.notes`（人看得出来这一趟的账本是残的），
    而且**只记一次**（账本坏了通常每步都坏，30 步刷 30 条会把别的 note 淹掉）。
    """
    boom = {"n": 0}

    def broken(step):
        boom["n"] += 1
        raise OSError("No space left on device")

    journey, fake, calls = _run(
        tmp_path,
        {"observe": [{"structured": PAGE_LANDING}], "click": [{"structured": {"ok": True}}]},
        [{"calls": [("observe", {}), ("click", {"selector": "#get-started"})]},
         {"content": "看完了"}],
        on_step=broken,
    )
    assert boom["n"] == 2, "旁路每一步都该被叫到（吞的是它的异常，不是不叫它）"
    # 主路照走：两步都做成了、模型照常收到结果、循环自己收尾
    assert [s["action"] for s in journey.steps] == ["observe", "click"]
    assert [c["name"] for c in calls] == ["goto", "observe", "click"]
    assert journey.stop_reason == "model_done"
    tool_msgs = [m for m in fake.calls[1]["messages"] if m["role"] == "tool"]
    assert len(tool_msgs) == 2 and "No space left" not in tool_msgs[0]["content"], tool_msgs
    # 但要说出来，而且只说一次
    said = [n for n in journey.notes if "旁路" in n]
    assert len(said) == 1, journey.notes
    assert "No space left on device" in said[0] and "OSError" in said[0], said[0]


def test_on_step_hook_sees_every_step_as_it_happens(tmp_path):
    """上层（计划三的 Console）要在**每一步发生的当下**拿到它，不是跑完再拿。"""
    seen: list = []
    journey, _, _ = _run(
        tmp_path,
        {"observe": [{"structured": PAGE_LANDING}]},
        [{"calls": [("observe", {})]}, {"calls": [("observe", {})]}, {"content": "看完了"}],
        on_step=seen.append,
    )
    assert [s["action"] for s in seen] == ["observe", "observe"]
    assert seen == journey.steps


# ───────────────────── 轮数要落账（G2：基线 M3 量不到它）─────────────────────


def test_wrap_up_records_the_rounds_that_were_actually_spent(tmp_path):
    """`_wrap_up` 拿到 `rounds` 之后**用完不许丢**（G2）。

    基线 M3（一次探路花几轮模型）全靠它 —— 今天这个数算出来了却没人接住，
    于是「一次探路多贵」只能靠 `steps` 反推，而 steps 数的是**工具调用**（含 observe），
    与「模型想了几轮」不是一回事（run 4/5 实测：60 步 ≠ 60 轮）。
    """
    journey, fake, _ = _run(
        tmp_path,
        {"observe": [{"structured": PAGE_LANDING}]},
        [{"calls": [("observe", {})]}, {"calls": [("observe", {})]}, {"content": "看完了"}],
    )
    assert journey.stop_reason == "model_done"
    assert journey.rounds == len(fake.calls) == 3, "轮数要与真发出去的模型调用对得上"
    assert journey.usage["rounds"] == 3, "usage 是 llm.summarize 的产物（Task 1 的接口）"
    assert journey.usage["tool_calls"] == 2


def test_a_run_cut_short_still_accounts_for_its_rounds(tmp_path):
    """**被人打断**那条路（`_Stop` 穿过 `run_tool_loop`）也要有数。

    ⚠️ 但那个数**拿不到** —— `rounds` 是 `run_tool_loop` 的局部变量，异常一穿出去就没了。
    所以这一路的 `journey.rounds` 只能是 `0` + **一句人话**说清「没记到」：
    **不许编一个数**（P5），也**不许**让 0 悄悄冒充「这一趟一轮都没花」——
    那条由 `measure.baseline()` 按 `stop_reason` 判（见 `tests/test_measure.py`）。
    """
    journey, _, _ = _run(
        tmp_path,
        {"observe": [{"structured": PAGE_LANDING}]},
        [{"calls": [("observe", {})]}] * 5,
        budget=browser_agent.Budget(max_steps=50, max_rounds=50),
        should_pause=lambda j: len(j.steps) >= 1,
    )
    assert journey.stop_reason == "paused" and len(journey.steps) == 1
    assert journey.rounds == 0
    assert journey.usage == {}
    assert any("轮数" in n for n in journey.notes), (
        f"0 得配一句「没记到」，不然读起来就是「这一趟没花轮数」：{journey.notes}")


# ─────────────────────────── 每步入账 ───────────────────────────


def test_every_step_lands_in_the_journey(tmp_path):
    """每个工具调用都要在 Journey 里留下一步，且带着**人话**。"""
    journey, _, calls = _run(
        tmp_path,
        {
            "observe": [{"structured": PAGE_LANDING}],
            "click": [{"structured": {"ok": True, "note": "动作已下发"}}],
            "scroll": [{"structured": {"ok": True, "note": "动作已下发"}}],
        },
        [{"calls": [("observe", {}), ("click", {"selector": "#get-started"}),
                    ("scroll", {"selector": "#postcode"})]},
         {"content": "走完了"}],
    )
    assert [s["action"] for s in journey.steps] == ["observe", "click", "scroll"]
    assert [c["name"] for c in calls] == ["goto", "observe", "click", "scroll"]
    for step in journey.steps:
        # ⚠️ `origin` 是 Task 4 加的（跨任务接口 §1）：走 dispatch 的每一步都是模型走出来的
        # ⚠️ 后五个键是 **2026-09-18 契约**加的（`docs/执行事实契约-2026-09-18.md` §二
        #    那七格的**前五格**，脚本填的那五格）：`dispatch` 在**回执到手那一刻**填它们。
        #    这条断言今天仍然管着老那件事：**报错那一步与做成了那一步同形**
        #    （见 `test_tool_error_is_recorded_in_that_step`）。
        assert set(step) == {"state", "action", "target", "result", "note", "origin",
                             "step_no", "receipt", "sig_before", "sig_after", "why"}, step
        assert step["origin"] == "model", step

    click = journey.steps[1]
    assert click["target"]["text"] == "Get Started"
    assert click["target"]["role"] == "button"
    assert click["target"]["near"] == "hero"
    assert click["target"]["selectors"][:2] == ["#get-started", "a.btn-primary"]
    assert click["result"]["ok"] is True
    # 人话（D16）：不是选择器、不是错误码
    assert "Get Started" in click["note"] and "#get-started" not in click["note"]

    # 状态归属：第一个 observe 之前还没看过页面（start），点那一下属于它观测出来的那一页
    assert journey.steps[0]["state"] == "start"
    assert click["state"] != "start"
    assert click["state"] == journey.states()[0]["name"]
    assert click["state"] == journey.steps[2]["state"]
    # 但 start 那一组里只有一次 observe（不是重放动作）—— 空组不进 STATES
    assert [s["name"] for s in journey.states()] == ["funnel"]


def test_tool_error_is_recorded_in_that_step(tmp_path):
    """工具报错要**如实记进那一步**，而且模型也要看见（不吞）。

    点/填两条路各来一次：**报错那一步的形状必须和别的一样**（五个键）——
    内部顺手塞进 step 的临时字段一旦漏出来，就是「失败的那步长得与成功的不一样」，
    而下游（Console、states()）是按形状读的。
    """
    journey, fake, _ = _run(
        tmp_path,
        {
            "observe": [{"structured": PAGE_LANDING}, {"structured": PAGE_QUIZ}],
            "click": [{"error": "没有找到选择器 #ghost（这个页面上没有它）"}],
            "form": [{"error": "填不进去：没找到 #missing"}],
        },
        [{"calls": [("click", {"selector": "#ghost"})]},
         {"calls": [("observe", {})]},
         {"calls": [("form", {"selector": "#missing", "value": "x"})]},
         {"content": "点不着也填不进去，换一条路"}],
    )
    clicked, looked, filled = journey.steps
    assert looked["action"] == "observe" and looked["result"]["ok"] is True, looked
    assert clicked["result"]["ok"] is False
    assert "#ghost" in clicked["result"]["error"], clicked["result"]
    assert "没做成" in clicked["note"], f"报错那一步的人话不对：{clicked['note']!r}"
    assert clicked["target"]["selectors"] == ["#ghost"]  # 报错也要能看出「本来想点哪」

    assert filled["result"]["ok"] is False
    assert "填不进去" in filled["result"]["error"], filled["result"]
    assert "没填成" in filled["note"], f"报错那一步的人话不对：{filled['note']!r}"
    assert filled["target"]["selectors"] == ["#missing"]
    for i, step in enumerate(journey.steps):
        # ⚠️ `origin` 是 Task 4 加的（跨任务接口 §1）：走 dispatch 的每一步都是模型走出来的
        # ⚠️ 后五个键是 2026-09-18 契约加的（七格里的前五格）—— 这条断言的**要害**
        #    仍然没变：**报错那一步与做成了那一步的键集合一模一样**。
        assert set(step) == {"state", "action", "target", "result", "note", "origin",
                             "step_no", "receipt", "sig_before", "sig_after", "why"}, step
        assert step["origin"] == "model", step
        assert step["step_no"] == i + 1, step

    # 不吞：下轮模型看到的那条 tool message 里必须带这条错
    tool_msgs = [m for m in fake.calls[1]["messages"] if m["role"] == "tool"]
    assert tool_msgs, "工具报错之后模型什么都没收到"
    assert "没有找到选择器 #ghost" in tool_msgs[0]["content"]
    last_turn = [m for m in fake.calls[3]["messages"] if m["role"] == "tool"]
    assert any("填不进去" in m["content"] for m in last_turn), "第二次报错没送到模型那儿"

    # 报错不掐死循环：它自己换了一条路接着走
    assert [s["action"] for s in journey.steps] == ["click", "observe", "form"]
    # 没做成的字段**不进 FILLS**（重放时不该去填一个当时都没找着的框）
    assert journey.fills() == {}


# ───────────────────── C2：一轮里多个 tool_calls ─────────────────────


def test_all_tool_calls_in_one_turn_reach_the_model(tmp_path):
    """一轮里三个 `tool_calls`：三条结果**都得**到模型手里（C2）。

    判据是**模型下一轮看到的那串 messages** —— 不是 agent 自己的账本。
    只处理 `tool_calls[0]` 的实现在这里必红：它只会送回去一条。
    """
    journey, fake, calls = _run(
        tmp_path,
        {
            "observe": [{"structured": PAGE_LANDING}, {"structured": PAGE_QUIZ}],
            "click": [{"structured": {"ok": True}}],
            "diff": [{"structured": {"actionable": True, "url_changed": False}}],
        },
        [{"calls": [("observe", {}), ("click", {"selector": "#get-started"}), ("diff", {"before": {"url": "x"}})]},
         {"content": "三个都拿到了"}],
    )
    assert [c["name"] for c in calls] == ["goto", "observe", "click", "diff"], \
        "开跑前那一下 + 三个调用没按顺序都执行"
    assert len(journey.steps) == 3

    tool_msgs = [m for m in fake.calls[1]["messages"] if m["role"] == "tool"]
    assert len(tool_msgs) == 3, f"一轮三个 tool_calls，模型只收到 {len(tool_msgs)} 条结果"
    assert [m["tool_call_id"] for m in tool_msgs] == ["call_0", "call_1", "call_2"]
    blob = "\n".join(m["content"] for m in tool_msgs)
    assert "Get Started 先看看你能省多少" in blob, "观测结果没到模型手里"
    assert "actionable" in blob, "diff 的结果没到模型手里"


# ───────────────────── C3：认「没有 tool_calls」 ─────────────────────


def test_no_tool_calls_terminates(tmp_path):
    """模型不调工具直接说话 = 它讲完了（七个工具里**没有** `done()`，这是唯一的正常终止）。"""
    journey, fake, _ = _run(
        tmp_path,
        {"observe": [{"structured": PAGE_LANDING}]},
        [{"calls": [("observe", {})]},
         {"content": "这一页有一个 Get Started 按钮，点它进漏斗。"}],
        budget=browser_agent.Budget(max_steps=50, max_rounds=6),
    )
    assert len(fake.calls) == 2, f"不调工具之后又问了这个模型 {len(fake.calls) - 2} 次"
    assert journey.stop_reason == "model_done"
    assert journey.final_answer == "这一页有一个 Get Started 按钮，点它进漏斗。"
    assert any("Get Started 按钮" in n for n in journey.notes), "模型的话没进 notes"


# ───────────────────── C1：max_tokens 与锁定的模型 ─────────────────────


def test_max_tokens_is_at_least_12000(tmp_path):
    """C1：`deepseek-v4-*` 的思考 token 算进 `max_tokens`，给少了最终答案是**空**的。"""
    _, fake, _ = _run(
        tmp_path,
        {"observe": [{"structured": PAGE_LANDING}]},
        [{"calls": [("observe", {})]}, {"content": "完了"}],
    )
    assert fake.calls, "一次都没问模型"
    for call in fake.calls:
        assert call["max_tokens"] >= 12000, f"max_tokens={call['max_tokens']} 太小，会静默返回空答案"
        assert call["model"] == "deepseek-v4-flash", f"模型被换成了 {call['model']}（C0 锁的是 flash）"


# 测试**自己**推给那道门的工具表：只有三个，其中一个（`stub_extra`）生产那张表里
# **不可能**有。判断依据就落在它身上 —— 见下面那条测试的 docstring。
STUB_ONLY_TOOLS = [
    {"name": "observe",
     "description": "观察页面，返回结构化页面模型。",
     "inputSchema": {"type": "object", "properties": {"frame_id": {"type": "string"}}}},
    {"name": "click",
     "description": "拟人点击一个元素。",
     "inputSchema": {"type": "object", "properties": {"selector": {"type": "string"}},
                     "required": ["selector"]}},
    {"name": "stub_extra",
     "description": "只在桩里存在的工具 —— 用来证明工具表是从这道门读的。",
     "inputSchema": {"type": "object", "properties": {"probe": {"type": "string"}},
                     "required": ["probe"]}},
]


def test_tool_specs_come_from_the_mcp_server(tmp_path):
    """工具声明必须来自那道门的 `tools/list`，不是本地写死的第二份表。

    ⚠️ 这条钉子**早先是空的**（评审指出）：桩默认那张表与「本地手抄一份」同名同参，
    于是把 `tools.tool_specs` 整个换成一张写死的表，它照样绿 ——
    断言拿的是同一份常量，比的是「常量等于常量」。

    修法：**由测试决定那道门回什么**（`STUB_ONLY_TOOLS`），表里带一个生产表里
    不可能有的名字。本地表产出的 specs 里不会有它，这道钉子就真的钉住了。
    """
    _, fake, _ = _run(
        tmp_path,
        {"observe": [{"structured": PAGE_LANDING}]},
        [{"content": "不看了"}],
        tools_table=STUB_ONLY_TOOLS,
    )
    specs = fake.calls[0]["tools"]
    names = [s["function"]["name"] for s in specs]
    # ① 门口给什么，模型就收到什么：多出来的那个名字是**只可能从门里来**的证据
    assert names == ["observe", "click", "stub_extra"], names
    assert all(s["type"] == "function" for s in specs)
    # ② 三个字段都真的搬过来了（名字对而描述/schema 写死也会在这里红）
    extra = next(s for s in specs if s["function"]["name"] == "stub_extra")
    assert extra["function"]["parameters"]["required"] == ["probe"]
    assert "只在桩里存在" in extra["function"]["description"]
    click = next(s for s in specs if s["function"]["name"] == "click")
    assert click["function"]["parameters"]["required"] == ["selector"]


# ──────────────── 跨任务接口：Journey 必须能喂进 template.render ────────────────
#
# `render(site, success_text, states, fills, provenance)` 的形状是 Task 3 定的
# （`agent/template.py` 的模块 docstring + `fixtures/reference_site.py` 是最佳样例）。
# 这条测试就是那个接口的钉子：**不是**另写一套形状，而是把 Journey 渲染成真产物，
# 再让它过 ast / 真 import / lint 三关。


def test_journey_feeds_template_render(tmp_path):
    journey, _, _ = _run(
        tmp_path,
        {
            "observe": [{"structured": PAGE_LANDING}, {"structured": PAGE_QUIZ},
                        {"structured": PAGE_QUIZ}],
            "click": [{"structured": {"ok": True}}],
            "form": [{"structured": {"ok": True}}],
        },
        [{"calls": [("observe", {})]},
         {"calls": [("click", {"selector": "#get-started"})]},
         {"calls": [("observe", {})]},
         {"calls": [("form", {"selector": "#postcode", "value": "SW1A 1AA"})]},
         {"content": "走完了"}],
    )

    states, fills = journey.states(), journey.fills()
    # 两个状态 = 两页（LANDING / QUIZ）；`start` 那组里只有一次 observe，不成状态
    assert [s["name"] for s in states] == ["funnel", "funnel-2"], states
    assert states[0]["when"]["url_contains"] == "https://example.test/funnel"
    assert states[0]["when"]["text_contains"] == ["Get Started 先看看你能省多少"]

    src = template.render("example-funnel", ["Thank you"], states, fills, provenance=None)

    # ① 只写「怎么走」那五种动作（observe/diff 不是重放动作，不许混进去）
    rendered = _literal(src, "STATES")
    assert [step["action"] for state in rendered for step in state["steps"]] == [
        "click", "form"
    ], rendered
    # ② FILLS 的形状与参考产物同一套（name/source/kind/label/target/fallback）
    fill = _literal(src, "FILLS")["postcode"]
    assert fill["kind"] == "value"
    assert fill["label"] == "Postcode"
    assert fill["target"]["selectors"][:2] == ["#postcode", "input[name=zip]"]
    assert fill["fallback"], "没有 fallback 的字段在 form-file 缺键时会填进一个空串"
    # ③ 三关：语法 / 真 import / 契约 lint
    ast.parse(src)
    _import_source(tmp_path, src)
    assert lint.check(src) == []


def test_states_when_holds_on_the_page_it_came_from(tmp_path):
    """生成的 `when` **必须**被它当时那一页满足 —— 否则重放时整组会被静默跳过。"""
    journey, _, _ = _run(
        tmp_path,
        {"observe": [{"structured": PAGE_LANDING}, {"structured": PAGE_QUIZ}],
         "click": [{"structured": {"ok": True}}],
         "form": [{"structured": {"ok": True}}]},
        [{"calls": [("observe", {})]},
         {"calls": [("click", {"selector": "#get-started"})]},
         {"calls": [("observe", {})]},
         {"calls": [("form", {"selector": "#postcode", "value": "SW1A 1AA"})]},
         {"content": "完了"}],
    )
    landing_state, quiz_state = journey.states()
    assert browser_agent.when_holds(landing_state["when"], PAGE_LANDING)
    assert browser_agent.when_holds(quiz_state["when"], PAGE_QUIZ)
    # 反向：一条 when 不该在**别的**页面上也成立 —— 否则状态之间分不开，
    # 「防 A/B 变体、防步骤增减」那条设计就白写了（`_applies` 是整组跳过的判据）
    assert not browser_agent.when_holds(quiz_state["when"], PAGE_LANDING)
    assert not browser_agent.when_holds(landing_state["when"], PAGE_QUIZ)


# ─────── 判据的 URL 侧：钉**稳定前缀**，不钉整条 path（R-35 真站实测 → R-37）───────
#
# 真站（homebuddy）量出来的形状：产物里 `url_contains` 钉的是**整条 path**
# （`…/walk-in-showers/cr640`），重放时站点给的是**同一个页面的轮换变体**
# （`…/gt1791-1`）→ 每条 `when` 都不成立 → 7 步**全被跳过**，扰动自测永远过不了。
# 计划里写 `when` 的理由正是「防 A/B 变体、防步骤增减」—— 钉整条 URL 恰好把这件事做反。
#
# 放松 URL 之所以安全：`when_holds` / 产物的 `_applies` 要求 **URL 与文本两条都命中**，
# 鉴别力在文本那一侧。但「应该没事」正是这个项目被烧过的地方 —— 所以三个方向都钉死：
# ① 轮换用例（回归钉子，先写先红）② 路径前缀真不同的页面**仍然不匹配**
# ③ 没有轮换末段时保持整条 path，绝不退化成主机名。


#: 真站那次的两个地址：同一页，**末段轮换**（`cr640` → `gt1791-1`），前缀一样。
#: `ROTATED_B` 连 query 一起照抄真站（16:34 那次 frame dump 里重放实际落地的窗口地址）
#: ——「query 要剥」与「末段要剥」是同一条判据上必须一起成立的两件事。
ROTATED_A = "https://homebuddy.test/walk-in-showers/cr640"
ROTATED_B = "https://homebuddy.test/walk-in-showers/gt1791-1?_stsgnoredir=1&_configname=landing_page_wis"
#: 另一页：路径前缀**真不同**，而它的末段也长得像 id —— 判别用例里正文故意用同一段。
OTHER_PREFIX = "https://homebuddy.test/bath-fitters/zz991"
#: 这三页的正文（判别用例靠「正文一样」把鉴别力逼到 URL 一条上）
PAGE_TEXT_ONE = "Walk-in showers 五分钟算出报价 免费 不查信用"


def _page(url: str, text: str) -> dict:
    """一份页面模型（借 PAGE_LANDING 的元素，只换地址与正文）。"""
    return dict(PAGE_LANDING, url=url, page_text=text)


def _walk(tmp_path, *pages):
    """一次探路：依次路过这几页，每页点一下。

    每页**必须**有一步可重放动作 —— 没有可重放步骤的状态在 `states()` 里会被整组丢掉。
    """
    turns = []
    for page in pages:
        turns.append({"calls": [("observe", {})]})
        turns.append({"calls": [("click", {"selector": page["actions"][0]["selector"]})]})
    turns.append({"content": "走完了"})
    journey, _, _ = _run(
        tmp_path,
        {"observe": [{"structured": p} for p in pages],
         "click": [{"structured": {"ok": True}} for _ in pages]},
        turns,
    )
    return journey


def _rendered(tmp_path, journey, site="when_url_site") -> str:
    return template.render(site, ["Thank you"], journey.states(), journey.fills(),
                           provenance=None)


def _artifact_says(tmp_path, src, when, url, text, site="when_url_site") -> bool:
    """让**产物自己**回答「这一页算不算这个状态」。

    跑的就是产物里的 `_applies`（真站那次跳掉 7 步的正是它）—— 只把 `_url()` /
    `page_signature()` 换成这两页，别的一个字不改。
    """
    module = _import_source(tmp_path, src, site=site)
    script = object.__new__(module.Filler)
    script._url = lambda: url
    script.page_signature = lambda: text
    return script._applies(when)


def test_when_url_survives_a_rotated_trailing_id(tmp_path):
    """**回归钉子**：末段轮换（`cr640` → `gt1791-1`）不能把整组步骤判掉。

    真站实测的病就是这一条：钉整条 path → 7 步全跳过 → 自测永远过不了。
    """
    journey = _walk(tmp_path, _page(ROTATED_A, PAGE_TEXT_ONE))
    (state,) = journey.states()
    assert state["when"]["url_contains"] == "https://homebuddy.test/walk-in-showers/", \
        state["when"]
    # 重放时站点给的是**同一个页面的轮换变体**：正文一样，只有末段不同
    assert browser_agent.when_holds(state["when"], _page(ROTATED_B, PAGE_TEXT_ONE))
    # 产物自己的判据也得说成立（生成侧与重放侧逐条对齐，评审验过的那条）
    assert _artifact_says(tmp_path, _rendered(tmp_path, journey), state["when"],
                          ROTATED_B, PAGE_TEXT_ONE)


def test_when_url_still_discriminates_a_different_path_prefix(tmp_path):
    """放松 URL **不会**让一个状态匹配到无关页面 —— 这条得证明，不能靠「应该没事」。

    两页的正文**故意一模一样**：这样鉴别力只剩 URL 一条，放松过头会立刻在这里红。
    """
    journey = _walk(tmp_path, _page(ROTATED_A, PAGE_TEXT_ONE), _page(OTHER_PREFIX, PAGE_TEXT_ONE))
    shower, other = journey.states()
    src = _rendered(tmp_path, journey)
    # ① 先钉**鉴别力**（正文一样、末段都像 id，串不串门只看 URL 那一段到底放松到了哪）
    assert not browser_agent.when_holds(shower["when"], _page(OTHER_PREFIX, PAGE_TEXT_ONE))
    assert not browser_agent.when_holds(other["when"], _page(ROTATED_A, PAGE_TEXT_ONE))
    assert not _artifact_says(tmp_path, src, shower["when"], OTHER_PREFIX, PAGE_TEXT_ONE)
    assert not _artifact_says(tmp_path, src, other["when"], ROTATED_A, PAGE_TEXT_ONE)
    # ② 再钉「放松到了哪一段」写死 —— 免得「缩过头」（比如缩到主机名）从别处溜回来
    assert shower["when"]["url_contains"] == "https://homebuddy.test/walk-in-showers/"
    assert other["when"]["url_contains"] == "https://homebuddy.test/bath-fitters/"


def test_same_path_states_are_still_told_apart_by_text(tmp_path):
    """真产物的形状：两个 state **同一个 path**（SPA 每步换正文）—— 真站那次产物里就是
    `cr640` / `cr640-2` 这样两段。放松之后它们的 URL 判据**一模一样**，分开全靠文本 ——
    这条钉的就是「文本那一侧真的扛得住」。
    """
    journey = _walk(tmp_path, _page(ROTATED_A, PAGE_TEXT_ONE),
                    _page(ROTATED_A, "Describe your project: Tub to walk-in shower"))
    first, second = journey.states()
    assert [p["name"] for p in journey.pages][:2] == ["cr640", "cr640-2"]
    assert first["when"]["url_contains"] == second["when"]["url_contains"] == \
        "https://homebuddy.test/walk-in-showers/"
    src = _rendered(tmp_path, journey)
    assert _artifact_says(tmp_path, src, first["when"], ROTATED_B, PAGE_TEXT_ONE)
    assert _artifact_says(tmp_path, src, second["when"], ROTATED_B, second["when"]["text_contains"][0])
    # 换一页：同一个 URL，但正文对不上 —— 各自都不认
    assert not _artifact_says(tmp_path, src, first["when"], ROTATED_B,
                              second["when"]["text_contains"][0])
    assert not _artifact_says(tmp_path, src, second["when"], ROTATED_B, PAGE_TEXT_ONE)


def test_when_url_keeps_the_whole_path_when_nothing_rotates(tmp_path):
    """没有轮换末段时保持**整条 path**（`/checkout`、`/shop/checkout`），也不许缩到只剩主机名。"""
    journey = _walk(tmp_path, _page("https://example.test/checkout", PAGE_TEXT_ONE),
                    _page("https://example.test/cr640", PAGE_TEXT_ONE),
                    _page("https://example.test/shop/checkout", PAGE_TEXT_ONE))
    checkout, single_segment, deep = journey.states()
    assert checkout["when"]["url_contains"] == "https://example.test/checkout"
    # 末段确实是轮换码，但 path 只有这一段 —— 切了就等于拿主机名当判据，绝不
    assert single_segment["when"]["url_contains"] == "https://example.test/cr640"
    # 多段 path、末段不是轮换码：整条留着（这条正是「无脑砍末段」那个变体会踩的地方）
    assert deep["when"]["url_contains"] == "https://example.test/shop/checkout"
    # 地址本来就没有 path 时，判据就是它当时的样子（不是被我们缩出来的）
    assert browser_agent._stable_url("https://example.test/") == "https://example.test/"
    assert browser_agent._stable_url("https://example.test") == "https://example.test"


# ─────────────────────────── 小工具 ───────────────────────────


def _literal(src: str, name: str):
    """把产物里某个常量**真解出来**（不是 grep）——产物是 python，就按 python 读。"""
    module = ast.parse(src)
    for node in module.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == name for t in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError(f"产物里没有 {name}")


#: 真 `common` 在生产仓库（`/opt/skills/auto-farm-skill/forms/common.py`）。
#: 产物 import 的是它，所以这里按它那几个名字摆一个替身 —— **不在模板里塞测试钩子**
#: （落盘形态与 Task 3 的 sandbox 同构：`<root>/forms/sites/<site>.py` + `<root>/forms/common.py`）。
STUB_COMMON = '''"""测试用 common 替身。"""
import logging


class CDPHelper:
    def __init__(self, ws_url):
        self.ws_url = ws_url


def setup_logger(name):
    return logging.getLogger(name)


def report_url(cdp, tid, label, log):
    return None
'''


def _import_source(root: pathlib.Path, src: str, site: str = "rendered_site"):
    """真 import 一次（计划一 py_emitter 的断口 1：`ast.parse` 查不出 `false` 这种）。

    ⚠️ `common` 这个名字**不属于**本测试：产物把它写死在 import 行里，而 Task 3 的
    `tests/test_template.py` 有一个**同名不同内容**的替身。所以这里进出一趟都把它
    从 `sys.modules` 摘干净（用之前摘、用完还原）—— 否则先跑的那一方会把对方的替身
    留在缓存里，症状是「单独跑绿、一起跑红」。
    """
    (root / "forms" / "sites").mkdir(parents=True, exist_ok=True)
    (root / "forms" / "common.py").write_text(STUB_COMMON, encoding="utf-8")
    path = root / "forms" / "sites" / f"{site}.py"
    path.write_text(src, encoding="utf-8")
    saved = sys.modules.pop("common", None)
    try:
        spec = importlib.util.spec_from_file_location(site, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.modules.pop("common", None)
        if saved is not None:
            sys.modules["common"] = saved


# ═════════════════════ 真依赖链：私有 Chrome + 真 cdp-mcp ═════════════════════
#
# 这一节是 Task 5 的**新证据**：spike 只能证明「模型肯调工具」，证明不了
# 「动作改变世界之后，下一次观测看到的是**新的**世界」。`observe` 无副作用，
# 模型完全可以并行看三遍；`click` 不行 —— 它改页面，第二眼的页面**只可能**是
# 第一眼 + 那一下点击的结果。这条链子在本文件里第一次真的被走通。

FIXTURE_START = """<!doctype html><html><head><meta charset="utf-8"><title>R5 start</title></head>
<body><h1>Before you click</h1>
<p>点一下 Go 看看页面会不会变。</p>
<button id="go">Go</button>
<script>
document.getElementById('go').addEventListener('click', function () {
  document.body.innerHTML = '<h1>After the click</h1><p>页面确实变了</p>';
});
</script></body></html>"""


class _FixtureHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 —— http.server 的接口名
        body = FIXTURE_START.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # 别把访问日志混进 pytest 的输出
        pass


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _chrome_binary() -> str:
    for candidate in (os.environ.get("CHROME_BIN"), "google-chrome", "google-chrome-stable",
                      "chromium", "chromium-browser", "/usr/bin/google-chrome", "/usr/bin/chromium"):
        if not candidate:
            continue
        found = shutil.which(candidate) or (candidate if os.path.exists(candidate) else None)
        if found:
            return found
    raise AssertionError("找不到 Chrome/Chromium —— 这条闸门宁可红也不 skip（skip 与 pass 长得一样）")


_BUILT: dict = {}


def _cdp_mcp_binary() -> str:
    """真 `cmd/mcp` 二进制：优先用 `CDP_MCP_BIN`，没有就现构建（与 Go 那边的 e2e 同一条路）。"""
    if "bin" in _BUILT:
        return _BUILT["bin"]
    given = os.environ.get("CDP_MCP_BIN")
    if given:
        _BUILT["bin"] = given
        return given
    go = shutil.which("go") or "/usr/local/go/bin/go"
    if not (shutil.which(go) or os.path.exists(go)):
        raise AssertionError(f"找不到 go 工具链（试过 PATH 与 {go}）")
    env = dict(os.environ)
    env["PATH"] = "/usr/local/go/bin:" + env.get("PATH", "")
    env.setdefault("GOPROXY", "https://goproxy.cn,direct")
    out = pathlib.Path(tempfile.mkdtemp(prefix="siteforge-cdp-mcp-")) / "cdp-mcp"
    done = subprocess.run([go, "build", "-o", str(out), "./cmd/mcp"],
                          cwd=str(ROOT / "tools" / "cdp"), env=env,
                          capture_output=True, text=True, timeout=300)
    if done.returncode != 0:
        raise AssertionError(
            "构建 cdp-mcp 失败（并发期已知折扣：另一个 agent 可能正在改 tools/cdp）:\n"
            + done.stderr[-2000:]
        )
    _BUILT["bin"] = str(out)
    return str(out)


@pytest.fixture(scope="module")
def live_browser():
    """私有 headless Chrome + 私有 profile + 本地夹具站（**绝不碰共享的 9222**）。"""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    port = _free_port()
    profile = tempfile.mkdtemp(prefix="siteforge-r5-profile-")
    log = open(os.path.join(profile, "chrome.log"), "w")  # noqa: SIM115 —— 与子进程同寿
    proc = subprocess.Popen(
        [_chrome_binary(), "--headless=new", f"--remote-debugging-port={port}",
         "--remote-debugging-address=127.0.0.1", "--no-first-run", "--no-default-browser-check",
         "--disable-gpu", "--no-sandbox", "--disable-extensions", "--disable-crash-reporter",
         f"--user-data-dir={profile}", "about:blank"],
        stdout=log, stderr=log, start_new_session=True,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            with urllib_request(f"{base}/json/list") as resp:
                targets = json.loads(resp.read().decode("utf-8"))
            if any(t.get("type") == "page" for t in targets):
                break
        except Exception:  # noqa: BLE001 —— 还没起来
            pass
        time.sleep(0.1)
    else:
        raise AssertionError(f"私有 Chrome 没起来（{base}/json/list 一直没有 page 目标）")

    yield {"port": port, "fixture": f"http://127.0.0.1:{server.server_address[1]}",
           "bin": _cdp_mcp_binary()}

    # 杀**整个进程组**：Chrome 会拉起 zygote / renderer 一串子进程，只杀父进程会留孤儿
    # （本仓库栽过：孤儿窗口 → 内存 95%）
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait(timeout=10)
    log.close()
    server.shutdown()
    server.server_close()
    shutil.rmtree(profile, ignore_errors=True)


def urllib_request(url):
    import urllib.request
    return urllib.request.urlopen(url, timeout=3)  # noqa: S310 —— 本机私有端口


def test_dependency_chain_click_then_observe_changed_page(live_browser, tmp_path):
    """**本任务最重的一条**：点一下 → 再 observe → 看到的必须是**变了的**页面。

    为什么这不是「重复 observe 三次」（spike 那条的形态）：
    - 两次观测之间**夹着一个改页面的动作**，且脚本里第二眼在第一眼之后
      （调用流水线本身就被断言了顺序）
    - 第二眼看到的那段文字（`After the click`）在**第一眼里根本不存在** ——
      所以它不可能是「同一页看了两遍」
    - 页面是**原地变的**（url 一模一样，只有正文变了）：只拿 url 判「页面变了」的实现
      在这里会红 —— 而真站里 SPA 与问卷站恰恰都是这一类
    """
    session = tools.McpSession([live_browser["bin"], "--port", str(live_browser["port"])])
    fake = FakeLLM([
        {"calls": [("goto", {"url": f"{live_browser['fixture']}/start.html"})]},
        {"calls": [("observe", {})]},
        {"calls": [("click", {"selector": "#go"})]},
        {"calls": [("observe", {})]},
        {"content": "点了 Go 之后页面从 Before you click 变成了 After the click。"},
    ])
    try:
        journey = browser_agent.explore(
            f"{live_browser['fixture']}/start.html", "看看点了 Go 之后这一页变成什么样",
            session=session, client=fake,
        )
    finally:
        session.close()

    assert [s["action"] for s in journey.steps] == ["goto", "observe", "click", "observe"], journey.steps
    before, click, after = journey.steps[1], journey.steps[2], journey.steps[3]

    # ⓪ goto 那一步要能说清「打开了哪一页」——「打开了 」这种空地址的人话等于没说
    #    （实测踩过：target 是开步时建的，那时还没落地，一偷懒就空）
    landing = journey.steps[0]
    assert landing["target"]["url"] == f"{live_browser['fixture']}/start.html", landing
    assert "start.html" in landing["note"], f"goto 的人话里没有地址：{landing['note']!r}"

    # ① 点的那一下真的打在页面上的那个按钮上（选择器与文案都是从**观测模型**里认出来的）
    assert click["target"]["text"] == "Go", click["target"]
    assert click["target"]["selectors"][0] == "#go"
    assert click["result"]["ok"] is True

    # ② 前置对照：变的那段文字在第一眼里**不存在**（否则「看到了变化」是空话）
    assert "Before you click" in before["result"]["page_text_head"], before["result"]
    assert "After the click" not in before["result"]["page_text_head"]
    # ③ 第二眼看到了它 —— 而它只可能是那一下点击造成的
    assert "After the click" in after["result"]["page_text_head"], after["result"]
    # ④ 页面是**原地**变的：url 没变（只靠 url 判变化的实现在这里红）
    assert before["result"]["url"] == after["result"]["url"]
    assert before["result"]["page_text_head"] != after["result"]["page_text_head"]

    # ⑤ 状态跟着变了：两次观测被记成两页，各自的判据落在**各自那一页**上
    assert len(journey.pages) >= 2, f"页面变了却没分出两页：{[p['name'] for p in journey.pages]}"
    assert any("Before you click" in t for t in journey.pages[0]["when"]["text_contains"])
    assert any("After the click" in t for t in journey.pages[-1]["when"]["text_contains"])
    assert any("变了" in n for n in journey.notes), f"没把「页面变了」说给人听：{journey.notes}"

    # 而 STATES 里那一步（click）挂的是**点之前**那一页的判据 —— 重放时它必须先在那一页上；
    # 「点之后」那一页没有可重放的动作（只是看了一眼），不成一个状态（空状态等于没写）
    states = journey.states()
    assert states[0]["when"] is None, "还没看过页面那一组（goto）不该编一个 when 出来"
    assert any("Before you click" in t for t in states[1]["when"]["text_contains"])
    assert [s["name"] for s in states] == ["start", journey.pages[0]["name"]], states

    # ⑥ 这条链子产出的 Journey 能直接喂进 render()，且过 lint（跨任务接口，真页面上再验一次）
    src = template.render("r5-fixture", ["After the click"],
                          journey.states(), journey.fills(), provenance=None)
    ast.parse(src)
    _import_source(tmp_path, src, site="r5_fixture")
    assert lint.check(src) == []


RUN_LLM = os.environ.get("RUN_LLM") == "1"


def _judge_a_live_run(journey) -> None:
    """真模型那一跑的**放行判据** —— 单独提出来，是为了让它有一条确定性的红。

    ⚠️ 它早先写成 `if clicks:`（评审指出）：那样一来「模型这一跑压根没点」与
    「依赖链走通了」**都是绿的**，报告里引用的那次完全可能是**什么都没验到**的那次 ——
    正是这个项目要消灭的「绿了但什么也没证明」的形状。

    这条测试存在的唯一理由就是「做一步 → 看结果 → 再决定」这个**依赖链**，
    所以没点 = 这一跑**什么也没验到** = 失败，而且要响：错消息里得写明
    「不是机制坏了，是这一跑没验到东西」，免得下一个人把它当成噪音重跑掉。
    真模型不可控是事实，但不可控的正确处置是**红**，不是把判据降级。
    """
    assert journey.steps, "模型一次工具都没调 —— 这条循环的全部意义就是「先看再动」"
    assert journey.stop_reason in ("model_done", "budget_steps", "budget_rounds"), journey.stop_reason

    clicks = [i for i, s in enumerate(journey.steps) if s["action"] == "click"]
    assert clicks, (
        "模型这一跑**一次都没点** —— 依赖链是这条测试唯一的判据，所以这一跑什么都没验到。"
        "这不是「跳过」，更不能读成绿：请重跑。"
        f"（它实际做的：{[(s['action'], s['note']) for s in journey.steps]}）"
    )
    look = [s for s in journey.steps[clicks[0] + 1:]
            if s["action"] in ("observe", "diff") and s["result"].get("ok")]
    assert look, "点了却不再看一眼 —— 「做一步 → 看结果 → 再决定」这条链子没走起来"
    first = look[0]
    if first["action"] == "observe":
        assert "After the click" in first["result"]["page_text_head"], (
            "点完 Go 之后看到的那一页**没变** —— 要么点空了，要么它看的是旧的快照"
            f"（它看到的是：{first['result']['page_text_head']!r}）"
        )


def _steps(action: str, **result) -> list:
    """造一步（给 `_judge_a_live_run` 的桩测试用）。"""
    return [{"state": "start", "action": action, "target": None,
             "result": result, "note": f"{action} 那一步"}]


def test_the_live_judgement_is_red_when_nothing_was_exercised():
    """放行判据本身要有确定性的红 —— 真模型那条默认 skip，否则它这一半永远没人验。

    三种「看着像绿、其实什么都没验到」的跑法，都**必须**红：
    ① 一次都没点（只 observe）；② 点了却没再看一眼；③ 看了，但页面根本没变。
    """
    never_clicked = browser_agent.Journey(
        steps=_steps("observe", ok=True, page_text_head="Before you click"),
        stop_reason="model_done", final_answer="我看完了。",
    )
    with pytest.raises(AssertionError, match="一次都没点"):
        _judge_a_live_run(never_clicked)

    clicked_but_blind = browser_agent.Journey(
        steps=_steps("click", ok=True), stop_reason="model_done",
    )
    with pytest.raises(AssertionError, match="点了却不再看一眼"):
        _judge_a_live_run(clicked_but_blind)

    clicked_and_looked_at_the_same_page = browser_agent.Journey(
        steps=_steps("click", ok=True) + _steps("observe", ok=True, page_text_head="Before you click"),
        stop_reason="model_done",
    )
    with pytest.raises(AssertionError, match="没变"):
        _judge_a_live_run(clicked_and_looked_at_the_same_page)


@pytest.mark.skipif(
    not RUN_LLM,
    reason="打真 LLM（要 key、要钱、要网络），默认 skip；RUN_LLM=1 才跑 —— 它不能进常规 CI",
)
def test_live_llm_walks_a_real_dependency_chain(live_browser, capsys):
    """真 `deepseek-v4-flash` + 真浏览器 + 真 MCP 门走一遍。

    ⚠️ 判据只钉**机制**，不钉「模型答得对不对」——spike 立的规矩：拿真模型做断言，
    绿的是运气、红的是噪音。所以：

    - 它必须真的调过工具（不许凭常识编）
    - 循环必须自己收住（不能挂着）
    - 它**必须**真的点过 Go，且随后看到的**是变过的那一页** —— 这条是机制：
      依赖链断了 / 它点完不看，都说明这条循环没在真的「做一步看一眼」

    ⚠️ 判据在 `_judge_a_live_run` 里，并且**由一条桩测试守着**（`test_the_live_judgement_
    is_red_when_nothing_was_exercised`）：真模型这一跑不可控，但「没验到东西」不可以
    因此变成绿的 —— 那种绿正是这个项目存在要消灭的东西。

    跑它：

        export OPENAI_API_KEY=$(docker exec auto-llm-script printenv OPENAI_API_KEY)
        RUN_LLM=1 python3 -m pytest tests/test_browser_agent.py -k live -v -s
    """
    session = tools.McpSession([live_browser["bin"], "--port", str(live_browser["port"])])
    try:
        journey = browser_agent.explore(
            f"{live_browser['fixture']}/start.html",
            "点「Go」这个按钮之后，这一页会变成什么样？用工具确认，不要猜。",
            budget=browser_agent.Budget(max_steps=8, max_rounds=6),
            session=session,
        )
    finally:
        session.close()

    with capsys.disabled():      # 原始证据要看得见（报告里的引用就抄这里）
        print("\n" + "=" * 72)
        print(f"stop_reason={journey.stop_reason}  final_answer={journey.final_answer!r}")
        for i, step in enumerate(journey.steps, 1):
            print(f"  {i}. [{step['state']}] {step['action']} ok={step['result'].get('ok')} "
                  f"| {step['note']} | {(step['result'].get('error') or '')[:80]}")
        for note in journey.notes:
            print(f"  note: {note}")
        print("=" * 72)

    # 判据在 `_judge_a_live_run` 里（它自己有一条桩测试守着）—— 这里只把原始证据留在上面
    _judge_a_live_run(journey)


# ───────────── 字段语义的三档推断（2026-09-17 真站实测的误填）─────────────
#
# 用户在真窗口上直接看到的：「zipcode 填成了手机号导致过不去」。
# 根因（账本里对得上）：ZIP 那个框是 `<input type="tel">`（弹数字键盘用），标签又是个
# 不透明的 MUI id（`textField-173838`）—— 原先 `_fallback` 把 `type` 和标签**混在同一个
# blob 里**按同一张表匹配，`tel` 成了唯一命中的词 → 判成 phone：
#   账本那一步 `value='33101'`（邮编）配着 `fallback=[{"random":"phone"}]`
# → 复跑时手机号被打进邮编框。
# 修法：**分三档**——① 字段自己的名字（label/hint/placeholder）→ ② 页面上它周围写着的字
# （`nearby_text`）→ ③ html 的 type（只认不歧义的；tel 留作最后一档）。


def _field(**kw):
    elem = {"selector": "#x", "label": "", "hint": "", "placeholder": "", "type": "",
            "nearby_text": []}
    elem.update(kw)
    return elem


def test_a_zip_field_with_type_tel_is_a_postcode_not_a_phone():
    """**这条就是用户看见的那个 bug**：type=tel 的邮编框必须判成 postcode。

    「邮编框用 type=tel」在真站上很常见（为了手机弹数字键盘）—— 拿 type 当语义判据，
    就会把邮编框判成手机号，然后把手机号打进去。判据是**值语义**，不是「没崩」。
    """
    elem = _field(label="textField-173838", type="tel",
                  nearby_text=["What's your ZIP code?", "Your ZIP code ensures we find local quotes"])
    assert browser_agent._fallback("value", "33101", elem["label"], elem) == [{"random": "postcode"}]


def test_the_fields_own_name_beats_the_pages_words_and_the_html_type():
    """三档的顺序：**字段自己的名字** > 周围写着的字 > html 的 type。

    反例（同级）：placeholder 写着 Phone Number: 的框，哪怕旁边那句问句里出现了
    「ZIP code」（比如同一段文字里两种字段都提到），也必须按**它自己的名字**判。
    """
    elem = _field(placeholder="Phone Number:", type="tel",
                  nearby_text=["Your ZIP code and phone number both help"])
    assert browser_agent._fallback("value", "", elem["label"], elem) == [{"random": "phone"}]


def test_the_html_type_is_only_the_last_resort():
    """什么都没写时才轮到 type；而且它只认**不歧义**的那几个。"""
    assert browser_agent._fallback("value", "", "", _field(type="tel")) == [{"random": "phone"}]
    assert browser_agent._fallback("value", "", "", _field(type="email")) == [{"random": "email"}]
    assert browser_agent._fallback("value", "", "", _field(type="password")) == [{"random": "password"}]
    # 一个字都没有 → 保守的默认（不编内容）
    assert browser_agent._fallback("value", "", "", _field()) == [{"random": "full_name"}]


def test_nearby_text_rescues_a_field_whose_label_is_an_opaque_id():
    """标签是不透明 id、周围写着「Postcode」→ 第二档救回来（同类字段一起受益）。

    「别只修 zip 这一个」：这一档对 postcode / postal / zip 以及其它同类字段是**通用**的。
    """
    for words, want in ((["Enter your postcode"], "postcode"),
                        (["What's your ZIP code?"], "postcode"),
                        (["Your postal code"], "postcode"),
                        (["Date of birth"], "dob"),
                        (["First name"], "first_name")):
        elem = _field(label="textField-0001", type="text", nearby_text=words)
        got = browser_agent._fallback("value", "", elem["label"], elem)
        assert got == [{"random": want}], (words, got)


def test_check_and_select_fills_are_not_touched_by_the_semantics():
    """`check` / `select` 那两种照旧用**账本里记的那个值**（这一改只动「猜」的那条路）。"""
    assert browser_agent._fallback("check", "true", "x", _field(type="tel")) == ["true"]
    assert browser_agent._fallback("select", "Florida", "x", _field(type="tel")) == ["Florida"]
    assert browser_agent._fallback("select", "", "x", _field(type="tel")) == []


def test_an_opaque_labelled_field_is_named_after_what_the_page_says():
    """名字（= `source`）要**对得上运营那份资料的键** —— 不然每次都只能退回随机池。

    用户 2026-09-17 在真窗口上问的就是这件事（「是不是资料没对齐」）：那个 ZIP 框的标签是
    不透明的 MUI id（`textField-173838`），名字就成了 `textfield_173838`，
    运营的 `zip` / `postcode` 键**一个都对不上** → 每次都走随机池。
    现在按 `_field_kind` 取语义名（自己的名字 → 周围写着的字 → html 的 type），
    认不出才退回标签那条老路（**不编名字**）。
    """
    journey = browser_agent.Journey()
    opaque_zip = {"label": "textField-173838", "hint": "", "placeholder": "", "type": "tel",
                  "nearby_text": ["What's your ZIP code?"]}
    assert browser_agent._fill_name("textField-173838", opaque_zip, journey) == "postcode"

    # 反例（同一格）：页面上什么都没说 → **不编**，照旧用标签那条老路
    nothing = {"label": "textField-9999", "hint": "", "placeholder": "", "type": "text",
               "nearby_text": []}
    assert browser_agent._fill_name("textField-9999", nothing, journey) == "textfield_9999"

    # 有正经标签的：名字收敛到**运营那份资料的词汇**（`email`，不是 `email_address`）——
    # 「Full Name:」本来就是 full_name，看不出来；「Email Address:」这一格看得出来。
    assert browser_agent._fill_name("Full Name:", {"label": "Full Name:"}, journey) == "full_name"
    assert browser_agent._fill_name("Email Address:", {"label": "Email Address:"}, journey) == "email"


def test_the_source_of_a_zip_fill_matches_the_operators_form_file():
    """端到端：`source` = `postcode` → 产物能从运营的 form-file 里**取到真数据**。

    这就是「资料对齐」的判据 —— 名字对不上时产物只能填随机值（那正是用户看到的现象之一）。
    """
    args = {"value": "33101"}
    target = {"text": None, "label": "textField-173838", "role": None, "near": None,
              "selectors": ["#zip"], "above_fold_only": False, "frame_id": ""}
    element = {"label": "textField-173838", "type": "tel",
               "nearby_text": ["What's your ZIP code?"]}
    info = browser_agent._fill_info(args, target, element, browser_agent.Journey())
    assert info["name"] == "postcode" and info["source"] == "postcode", info
    # 运营那份 form.json（跑这一趟用的就是它）里有 postcode 键 → 能对上
    import json as _json
    form = _json.load(open("/tmp/gwacc4/form.json")) if __import__("pathlib").Path("/tmp/gwacc4/form.json").exists() else None
    if form is not None:
        assert info["source"] in form, (info["source"], sorted(form))


def test_the_zip_field_is_now_read_from_its_placeholder_alone():
    """真站那一格（gowizard 邮编框）**现在靠 placeholder 自己就认得出来**。

    这是简报里的第 2 层，2026-09-17 在活页面上量过的那一格：
        id="textField-173838" · name 属性**空** · placeholder="e.g. 06801" · type="tel"
        label 空 · nearby_text 空（当时 —— 见 `_field_kind` 那条 nearby_text 的修复）
    修之前：`e.g. 06801` 被当成「只是个例子」丢掉 → 三档全落空 → 按 `type=tel` 判成手机号
    → 复跑时手机号被打进邮编框。修之后：**那个例子值本身就是证据**（5 位数字 = 美国邮编）。

    ⚠️ 这条**原先断言的是相反的事**（`_field_kind` 该判成 phone，然后靠「探索时写进去的值」
    兜回来）。现在它直接判对 —— 兜底那一路仍然在（见下面那条），但**不再需要它来救这一格**。
    """
    bare = {"label": "", "hint": "textField-173838", "placeholder": "e.g. 06801", "type": "tel"}
    assert browser_agent._field_kind("", bare) == "postcode", \
        "placeholder 的例子值（5 位数字）就是邮编的证据 —— 不该再落到 type=tel 那一档"

    fill = browser_agent._fill_info(
        {"value": "90210"}, {"label": "textField-173838", "selectors": ["input.mui"]},
        bare, browser_agent.Journey())
    assert fill["name"] == "postcode" and fill["fallback"] == [{"random": "postcode"}], fill

    # 同一个 placeholder 上，关键词那一档（更靠前）仍然优先：写着 ZIP 就按 ZIP
    worded = {"label": "", "hint": "x", "placeholder": "e.g. 06801", "nearby_text": ["What's your ZIP code?"],
              "type": "tel"}
    assert browser_agent._field_kind("", worded) == "postcode"


def test_a_tel_field_with_no_words_or_shapes_is_decided_by_what_the_explore_typed_there():
    """四档全落空时，用**探索那一趟自己写进这个框的值**当证据（保留的兜底路）。

    真站上确实存在这种格：`label` 空、`hint` 是个不透明 id、placeholder 举的例子
    **没有可认的形状**、`nearby_text` 空、而 `type="tel"`（很多站为了弹数字键盘就这么写）。
    这时按 type 会判成手机号，而账本里躺着现成证据：探索那一趟模型自己往里写过一个
    邮编形状的值（`90210`）—— 那条证据仍然要算数。
    """
    shapeless = {"label": "", "hint": "textField-999999", "placeholder": "Type here", "type": "tel"}
    assert browser_agent._field_kind("", shapeless) == "phone", "前提：这一格本来就判成 phone（歧义）"

    zip_fill = browser_agent._fill_info(
        {"value": "90210"}, {"label": "textField-999999", "selectors": ["input.mui"]},
        shapeless, browser_agent.Journey())
    assert zip_fill["name"] == "postcode" and zip_fill["fallback"] == [{"random": "postcode"}], zip_fill

    # 反例（同一格）：手机形状的值不会被读成邮编
    phone_fill = browser_agent._fill_info(
        {"value": "(512) 494-9400"}, {"label": "Phone Number:", "selectors": ["#p"]},
        {"label": "", "hint": "phoneNumberTextField", "placeholder": "(512) 494-9400", "type": "tel"},
        browser_agent.Journey())
    assert phone_fill["fallback"] == [{"random": "phone"}], phone_fill

    # 反例：不是 tel 的字段不掺这一脚（语义已经判出来了就按语义）
    text_elem = {"label": "Full Name:", "type": "text"}
    got = browser_agent._fill_info({"value": "90210"}, {"label": "Full Name:"}, text_elem,
                                   browser_agent.Journey())
    assert got["fallback"] == [{"random": "full_name"}], got


def test_the_state_field_is_read_from_the_question_text_the_page_shows():
    """「州」那一格：题目正文就写在页面上，靠它判（简报第 3 层，真站量过）。

    2026-09-17 活页面上量的那一格：
        id="textField-173862" · name 空 · placeholder="e.g. California or Texas" · type="text"
        label 空 · **题目正文「What state do you live in?」挂在输入框往上第 3 层的前一个兄弟上**
    修之前：那一格判不出种类 → `_fallback` 给了 `full_name` → **往「州」里填一个人名**。
    修之后：`nearby_text` 把正文收上来（observe 爬祖先），第三档命中 `state`。
    """
    state_elem = {"label": "", "hint": "textField-173862", "placeholder": "e.g. California or Texas",
                  "nearby_text": ["What state do you live in?"], "type": "text"}
    assert browser_agent._field_kind("", state_elem) == "state", \
        "题目正文里写着 state —— 这一格必须判成 state（不是靠州名表，是靠页面上写着的字）"
    fill = browser_agent._fill_info(
        {"value": "Texas"}, {"label": "textField-173862", "selectors": ["input.mui"]},
        state_elem, browser_agent.Journey())
    assert fill["name"] == "state" and fill["fallback"] == [{"random": "state"}], fill

    # 认不出的那一格：**不许**再落到 full_name（往州里填人名那件事）
    unknown = {"label": "", "hint": "textField-000000", "placeholder": "Type here", "type": "text"}
    assert browser_agent._field_kind("", unknown) is None
    assert browser_agent._fallback("value", "", "", unknown) == [{"random": "full_name"}], \
        "认不出时的兜底仍然是 full_name —— 这条**没改**，它背后的规矩见 `_fallback`"



def test_the_when_snippet_does_not_lead_with_decoration():
    """判据里那段正文**不带开头的装饰字符**（真站实测的那一格）。

    某站首页的 page_text 是 `___ The listings featured are compensated and…` ——
    那个 `___` 是广告位的占位符，第二次跑时它没了 → 判据要含「___ The listings…」
    而页面上是「The listings…」→ **整组步骤被跳过**，而两句话**明明是同一句**。
    """
    assert browser_agent._snippet("___ The listings featured are compensated and this") == \
        "The listings featured are compensated and this"
    assert browser_agent._snippet("--- Get your free quote today") == "Get your free quote today"
    # 反例（同一格）：正文里本来就有意义的字一个都不许动
    assert browser_agent._snippet("Progress: 30% What state do you live in?") == \
        "Progress: 30% What state do you live in?"


def test_a_when_that_can_not_be_pinned_says_so():
    """★ 线①：钉不出正文判据时**必须说出来** —— 不许悄悄退化成只有 `url_contains` 一条。

    为什么这是硬要求（2026-09-20 gowizard 线①）：`when.text_contains` 是生成期**从一次
    观测**取的一段子串。站点对同一页给两种免责声明时（实测 72 趟里 3 趟），钉住 A 的那条
    判据在 B 那一趟整组不成立 —— `auto_warranty` 是 `start` 之后的**第一个**状态组，
    它一跳过，「Reject All」「Get Free Quote」两个动作都没做，25 步里 24 步全跳过、
    **0 次真实点击** ⇒ `no_success`。

    而产物上「只有 URL 一条」与「本来就只有 URL」长得**一模一样**（都是
    `{'url_contains': …}`）—— 读的人分不出「查过了，只有 URL 稳」和「没钉出来」。
    这一格把**后者**说出来：判据里带一句人话（`text_why`）。
    """
    # ① 这一眼**根本没读到正文**（真站实测有这一格：`page_text` 读出来是空的）
    bare = {"url": "https://example.test/quiz", "title": "t", "page_text": ""}
    when = browser_agent._when_for(bare)
    assert when["url_contains"] == "https://example.test/quiz", when
    assert "text_contains" not in when, when
    assert "text_why" in when, "钉不出来就得说出来：%r" % (when,)
    assert "只有 URL" in when["text_why"], when["text_why"]

    # ② 正文只有装饰字符（`_snippet` 削完就什么都不剩）—— 同一格
    junk = {"url": "https://example.test/quiz", "title": "t", "page_text": "___ ---  "}
    when2 = browser_agent._when_for(junk)
    assert "text_contains" not in when2 and "text_why" in when2, when2

    # ③ 反例（同一格）：正文钉得出来 → **一个字都不许加**
    #    （正常情况被说成「不稳定」= 每个产物都在喊狼来了）
    ok = browser_agent._when_for(PAGE_LANDING)
    assert ok["text_contains"], ok
    assert "text_why" not in ok, ok

    # ④ 连地址都取不出来（不是 http(s)）→ 仍然是「没有判据」，不许编一句出来
    assert browser_agent._when_for({"url": "", "title": "t", "page_text": ""}) is None


def test_the_unpinned_when_reaches_the_ledger(tmp_path):
    """钉不出来的那句话要**进账本**，而且要**带着状态名** —— 读账本的人才知道是哪一组。"""
    journey, _, _ = _run(
        tmp_path,
        {"observe": [{"structured": dict(PAGE_LANDING, page_text="")},
                     {"structured": PAGE_QUIZ}],
         "click": [{"structured": {"ok": True}}]},
        [{"calls": [("observe", {})]},
         {"calls": [("click", {"selector": "#get-started"})]},
         {"calls": [("observe", {})]},
         {"calls": [("click", {"selector": "#opt-daily"})]},
         {"content": "完了"}],
    )
    states = journey.states()
    assert len(states) == 2, "前提：两页各得留下一步，不然状态会被整组丢掉：%s" % (states,)
    said = [n for n in journey.notes if "只有 URL" in n]
    assert said, "钉不出正文判据这件事必须进账本：%s" % (journey.notes,)
    assert any(states[0]["name"] in n for n in said), (states[0]["name"], said)

    # 判据跟着状态一起走（产物就靠它把这件事说给读日志的人）
    assert states[0]["when"]["text_why"], states[0]["when"]
    assert "text_contains" not in states[0]["when"], states[0]["when"]

    # 反例（同一格）：**钉得出来的那一页不许被记账**（不然账上每页都在喊）
    assert "text_why" not in (states[1]["when"] or {}), states[1]["when"]


def test_an_incidental_start_page_gets_no_when():
    """起点那一页与**后面每一页**都不同源 → 它是旁枝，**不设判据**。

    真站实测（2026-09-17 第十一轮）：探索时浏览器停在 Bit 的**工作台页**
    （`console.bitbrowser.net/…?id=…&port=…`），账本第一个状态的 `when` 于是要的是那一串 ——
    而 `?id=…&port=…` **每开一次窗口都不一样**；自测换的是干净窗口（R-F1）→ 判据不成立 →
    起点那组的 `goto` **一步都没轮到** → 后面全部静默跳过（实测 0 执行 / 29 跳过）。
    """
    j = browser_agent.Journey()
    pages = [
        {"name": "console_bitbrowser", "when": {"url_contains": "https://console.bitbrowser.net/"},
         "url": "https://console.bitbrowser.net/?id=abc&port=54345", "title": "workbench"},
        {"name": "auto_warranty", "when": {"url_contains": "https://www.gowizard.com/auto-warranty/"},
         "url": "https://www.gowizard.com/auto-warranty/", "title": "t"},
    ]
    browser_agent._drop_incidental_start_when(pages, j)
    assert pages[0]["when"] is None, pages[0]
    assert pages[1]["when"] is not None, pages[1]          # 站点自己的页照旧有判据
    assert any("旁枝" in n for n in j.notes), j.notes       # 而且要**说给人听**

    # 反例（同一格）：起点就是站点自己的页（从入口开跑的探索）→ 判据必须留着
    j2 = browser_agent.Journey()
    pages2 = [
        {"name": "landing", "when": {"url_contains": "https://example.test/"},
         "url": "https://example.test/", "title": "t"},
        {"name": "quiz", "when": {"url_contains": "https://example.test/quiz"},
         "url": "https://example.test/quiz", "title": "t"},
    ]
    browser_agent._drop_incidental_start_when(pages2, j2)
    assert pages2[0]["when"] is not None, pages2[0]


# ═══════════ 计划模式：描述当**检查点清单**（Task 3，2026-09-17）═══════════
#
# 设计注 §2.2–§2.5。三条硬规矩在这一节里各是一条断言：
#
# ① **一个字都不改写**：简报里给的是运营的**原话**（清单 + 原文两样），
#    而且**非编号行一个字都不许吞**（`禁止点击: …` 那种约束吞了，模型就会去点禁区）。
# ② **位置只由模型报的标记推进**（`observe` 只给感知，系统**不判**「这一步做完没有」）：
#    没报 / 报了个不存在的号 → 位置**不动**，只如实记一句人话（§2.4：不猜）。
# ③ **绕开（分支）不是偏离**：`【第 2 步】`→`【第 5 步】`，中间那几个记 `jumped_over`，
#    **不进 `deviations`**（§2.3.1：混在一起这条账会被日常噪音灌满，信号就废了）。
#
# ⚠️ 这一节**全部用桩**（桩 MCP + 桩模型）—— **不开浏览器**，也不打真模型。
#    判据全是机制（预算 / 位置 / 停滞 / 记账），与是哪个站点无关。

DESCRIPTIONS = ROOT / "fixtures" / "descriptions"
#: 真描述：多题问卷那类（`引导:` 那四项**挤在一行**上 —— R-E1 裁定认的形状）。
BLINKIST_DESC = (DESCRIPTIONS / "blinkist.txt").read_text(encoding="utf-8")
#: 真描述：编号清单那类（一行一步）。
GW_DESC = (DESCRIPTIONS / "gowizard_auto_warranty.txt").read_text(encoding="utf-8")
#: 造出来的 5 步描述 —— 分支用例要 5 个号才跳得起来（真描述里没有这么短的）。
BRANCH_DESC = "操作:\n1. 点 A\n2. 点 B\n3. 点 C\n4. 点 D\n5. 点 E\n"
#: **跳号**的描述（1、3 两步）—— 专门用来钉「号原样搬、不重编」。
#: 真描述的号都是连续的，重编号的输出与不重编号**逐字节相同**，钉不住（复审 M-1）。
GAPPED_DESC = "操作:\n1. 点 A\n3. 点 C\n"

#: **B4 的回归钉子**：Task 3 开工前那一刻 `_brief()` 的**真实输出**（跑出来的，不是手打的）。
#: 没计划时必须与它**逐字节相同** —— 所以它写死在下面那条用例里，
#: **不许**改成「拿今天这段代码再算一遍」：那样它跟着代码一起变，就什么都没钉住。
#:
#: ⚠️ **2026-09-20 更正过一次：`30 步、20 轮` → `100 步、80 轮`**（预算标定，见
#: `browser_agent.DEFAULT_MAX_ROUNDS` 那段）。这是**有意改的事实**，不是措辞漂了：
#: 那两个数是**从预算推出来**的（`_brief` 里 f-string 插的就是 `budget.*`），
#: 而预算原来那个 20 挡不住一条已知要走完的路（真站实测 `job-62b192d2aca4`）。
#: 换的**只是这两个数**，周围那几句措辞**一个字节没动** —— 这条钉子钉的是措辞，
#: 所以它照旧逐字节写死（没有改成插值：插值会让措辞也跟着代码漂）。
TODAY_BRIEF = (
    "目标站点：https://example.test/funnel\n"
    "要做的事：看看这一页怎么走到报价\n"
    "（你最多走 100 步、80 轮。现在这个浏览器窗口可能停在别的页上，先确认自己在哪。\n"
    "⚠️ **能回答了就直接停下来说**（不调工具就是结束）—— 一直调工具会把预算耗光，"
    "那一次你的结论一个字都留不下来。）"
)


def test_a_plan_reaches_the_model_as_the_operators_own_words_not_a_rewrite():
    """**有计划** → 简报里是**原话清单 + 一字不删的原文**（§2.2 的硬规矩）。

    ⚠️ 「原文」那一半不是摆设：真描述是**键值头 + 编号清单**，头里夹着**约束** ——
    `禁止点击: Cookie Policy,Privacy Policy,Terms`、`轮次: 30`、`成功条件URL: …`。
    只给清单等于把这些吞掉，模型就会去点**运营明写的禁区**。

    ⚠️ 断言要**锚在只可能来自那一处的东西上**（复审 I-1 的教训）：
    「`【第` 与 `步】` 在不在」这种问法**被清单本身满足**（清单每行都是 `【第 k 步】…`），
    所以它钉不住「怎么走第 2 条」——而那条是**位置机制唯一的输入约定**。
    """
    p = plan.parse(BLINKIST_DESC)
    # 前提（**解析**的事，Task 2 的判据）：那四项挤在一行上，逐字是这四条
    assert [s.text for s in p.steps] == ["滚动到底部", "点击Featured Titles", "等待3秒", "点击Verity"], p.steps
    # ⚠️ 这个 goal **故意不取 fixture 里那句**（`意图: 浏览网页并完成表单注册`）：
    #    否则「简报里有 goal」会被**原文**满足 —— 又一颗空钉子（I-1 的同一个病）。
    goal = "看看这一页怎么走到报价"

    text = browser_agent._brief("https://www.blinkist.com/magazine/posts/the-5-hour-rule",
                                goal, browser_agent.Budget(), p)
    assert goal not in p.raw, "前提：这个 goal 不在原文里（在的话下面那条断言就是空的）"
    # ① 清单：每一步的**原话**逐字都在
    for step in p.steps:
        assert f"【第 {step.n} 步】" in text, (step, text)
        assert step.text in text, (step, text)
    # ② 原文**一字不删**：约束那几行也在
    assert "禁止点击: Cookie Policy,Privacy Policy,Terms" in text, text
    assert "轮次: 30" in text and "成功条件URL: /news-feed,/welcome" in text, text
    assert p.raw in text, "`Plan.raw` 是**整份原文**，简报里要一字不删地带着"
    # ③ 这一版**不是**自由模式那一版（标签 + 内容都不许少）
    assert "要做的事：" not in text, text
    assert f"这一趟要摸清的是：{goal}" in text, "意图掉了（修站那条路的 goal 才是意图）"
    # ④ 自由版里那句**预算**必须带着：计划模式**没有别的地方**覆盖它
    #    （收尾那句由 `_SYSTEM` 规矩 6 覆盖，预算这句没有 —— 复审 I-2）
    #    ⚠️ 数换了（30/20 → 100/80，2026-09-20 预算标定），**这一句在不在**才是这条要钉的
    assert "（你最多走 100 步、80 轮。" in text, text
    # ⑤ 三条走法：一步一确认 / **用标记报位置** / 与页面不符就说出来
    #    ⚠️ 第 2 条要**整句**钉（`【第` 那种问法被清单满足 —— 复审 I-1）
    assert "每轮开头用 `【第 k 步】` 说自己在哪一步" in text, text
    assert "位置**只认这个标记**" in text, text
    assert "描述说" in text and "页面上是" in text, text
    # ⑥ 描述没说的别点 + 「这份清单是一条走法的样子，不是站点的结构」（§2.2：步数不是结构）
    assert "没说的别点" in text, text
    assert "不是站点的结构" in text, text


def test_the_checklist_keeps_the_numbers_the_operator_wrote():
    """清单里的号**原样搬**，**不重编**（§2.2 / `plan.py` 的硬规矩）——用**跳号**的描述钉。

    ⚠️ 为什么非要用跳号那份：两份真描述的号都是 1..N 连续的，**重编号的输出逐字节相同**
    → 拿它们钉这条等于什么都没钉（复审 M-1 的变异 `N2` 就是这么全绿的）。
    """
    p = plan.parse(GAPPED_DESC)
    assert [s.n for s in p.steps] == [1, 3], p.steps          # 前提：解析（Task 2 的判据）
    text = browser_agent._brief("https://example.test/funnel", "走通", browser_agent.Budget(), p)
    assert "【第 3 步】点 C" in text, text
    assert "【第 2 步】" not in text, "重编号了 —— 描述里**没有**第 2 步（人的话被改了）"


def test_the_walk_the_checklist_rule_lives_in_the_stable_layer():
    """那条规矩进的是 `_SYSTEM`（**稳定层**）—— 计划本身才是每轮的事（走简报那一版）。

    分成两层是有理由的：规矩**不随这一趟有没有计划变**（没计划那趟也带着它，只是无步骤可照），
    而**这一趟的清单**只在简报里。反过来说：谁要是把这条规矩挪进简报，
    没计划那条路就少了一条规矩 —— 而那条路必须是**与今天逐字节相同**的（B4）。
    """
    assert "人给了步骤清单就照着走" in browser_agent._SYSTEM
    assert "少走几步、多走几步都是正常的" in browser_agent._SYSTEM
    assert "与页面矛盾时说出来再决定" in browser_agent._SYSTEM


def test_without_a_plan_the_brief_is_byte_for_byte_what_it_was_today():
    """**B4 回归钉子**：没有步骤清单 → 简报与今天**逐字节相同**。

    三种「没有计划」都要走这条路：`plan=None`、压根没给、给了但一段话里解析不出步骤
    （`plan.parse("把这一页走通")` → `steps == []`）—— §2.3 的「没有」那一行：
    **不许假装有计划**，也不许退化成「空计划」。
    """
    free = plan.parse("把这一页走通")
    assert free.steps == [] and not free.actionable(), free
    for given in (None, free):
        got = browser_agent._brief("https://example.test/funnel", "看看这一页怎么走到报价",
                                   browser_agent.Budget(), given)
        assert got == TODAY_BRIEF, (given, got)


def test_a_goal_without_steps_runs_the_free_path_untouched(tmp_path):
    """自由模式的**整条路**照旧：简报是今天那版，计划的账一格都不记（**不假装有计划**）。"""
    journey, fake, _ = _run(
        tmp_path,
        {"observe": [{"structured": PAGE_LANDING}]},
        [{"calls": [("observe", {})]}, {"content": "看完了"}],
        plan=plan.parse("把这一页走通"),
    )
    assert journey.stop_reason == "model_done"
    assert journey.plan_ledger == [] and journey.deviations == []
    assert journey.stall_rounds == 0
    assert not any("第几步" in n for n in journey.notes), journey.notes
    # 模型**真收到的那份**就是今天那版（不是只在 `_brief` 那一层对）
    assert fake.calls[0]["messages"][1]["content"] == TODAY_BRIEF


def test_the_model_reporting_a_step_lands_in_the_plan_ledger(tmp_path):
    """§2.4：位置**只由模型报的**标记推进 —— `【第 3 步】` 就记到清单上第 3 步。

    反例（同一条账）：它没报到的那两步照实记「这一趟没报到这一步」——
    **不是**「跳过去了」：前面没有报过的位置可比（§2.4：说不出就是不知道，**不猜**）。
    """
    p = plan.parse(GW_DESC)
    assert [s.n for s in p.steps] == [1, 2, 3], p.steps      # 前提：解析的事（Task 2 的判据）
    journey, _, _ = _run(
        tmp_path,
        {"observe": [{"structured": PAGE_LANDING}]},
        [{"content": "【第 3 步】先看一眼这一页", "calls": [("observe", {})]},
         {"content": "走完了"}],
        plan=p,
    )
    assert len(journey.plan_ledger) == len(p.steps), "清单上每一项都要有交代（B2）"
    assert journey.plan_ledger[2]["state"] == "done", journey.plan_ledger
    assert journey.plan_ledger[2]["text"] == p.steps[2].text, journey.plan_ledger
    assert [e["state"] for e in journey.plan_ledger[:2]] == ["not_reached", "not_reached"], \
        journey.plan_ledger


def test_an_unknown_or_missing_mark_leaves_the_position_where_it_was(tmp_path):
    """§2.4：**没报** / 报了个**清单上没有的号** → 位置**不动**，只如实记一句人话。

    判据读 `stall_rounds`（「位置有没有动」的直接体现：动了就清零）。
    两种情形在这一条上**处置相同** —— 系统不去分辨「它忘了报」还是「它报了个不存在的号」：
    两种都只说明「这一轮它没说清自己在哪儿」。
    """
    p = plan.parse(GW_DESC)                     # 清单上只有 1/2/3，**没有**第 99 步
    journey, _, _ = _run(
        tmp_path,
        {"click": [{"error": "没有找到选择器 #ghost（这个页面上没有它）"}]},
        [{"content": "【第 99 步】我看看", "calls": [("click", {"selector": "#ghost"})]},
         {"content": "先看看再动", "calls": [("click", {"selector": "#ghost"})]},
         {"content": "走完了"}],
        plan=p, budget=browser_agent.Budget(max_steps=50, max_rounds=50, stall_limit=99),
    )
    assert all(e["state"] == "not_reached" for e in journey.plan_ledger), journey.plan_ledger
    assert journey.stall_rounds == 3, journey.stall_rounds      # 三轮都没推进（含收尾那轮）
    assert sum(1 for n in journey.notes if "这一轮没说自己在第几步" in n) == 3, journey.notes


def test_jumping_forward_is_a_branch_not_a_deviation(tmp_path):
    """**B7**：`【第 2 步】` → `【第 5 步】` —— 中间那两个记 `jumped_over`，`deviations` **为空**。

    §2.3.1 的硬规矩：分支站上「没走到某个检查点」**几乎每一趟**都会发生，它是站点本来的形状、
    **不用人看**。跟「矛盾」混在一起，`deviations` 会被日常噪音灌满 ——
    「同一处反复偏离」那条信号彻底失效（信号被噪声淹没，比没有信号更坏）。
    而且**跳号照样算「前进了」**：停滞计数清零（§2.4）。
    """
    p = plan.parse(BRANCH_DESC)
    journey, fake, _ = _run(
        tmp_path,
        {"click": [{"error": "没有找到选择器 #ghost"}]},
        [{"content": "【第 2 步】走第二步", "calls": [("click", {"selector": "#ghost"})]},
         {"content": "【第 2 步】还在这儿", "calls": [("click", {"selector": "#ghost"})]},
         {"content": "【第 5 步】这条分支直接到第五步", "calls": [("click", {"selector": "#ghost"})]},
         {"content": "走完了"}],
        plan=p, budget=browser_agent.Budget(max_steps=50, max_rounds=50, stall_limit=2),
    )
    assert [e["state"] for e in journey.plan_ledger] == [
        "not_reached", "done", "jumped_over", "jumped_over", "done"], journey.plan_ledger
    assert journey.deviations == [], journey.deviations      # ★ 负例：绕开**不是**偏离
    # 跳号**照样算前进了**：不然后面那条停滞判据会在这里响（stall_limit=2）→
    # 判据落在下面两条上（**停没停**），末尾那个 1 是**收尾那一轮**（`finish()` 的口径是
    # 「每一轮都算」：收尾那轮确实也是一轮没推进）
    assert journey.stall_rounds == 1, journey.stall_rounds
    assert journey.stop_reason == "model_done", journey.stop_reason
    assert len(fake.calls) == 4, "第 4 轮（收尾那句）没跑起来 = 中间被判成停滞了"


def test_walking_more_or_fewer_steps_is_not_an_anomaly(tmp_path):
    """**B8**：同一条描述跑两次（一次 20 轮、一次 34 轮）—— 两次都**不是异常**。

    §2.2：问卷 / 评测这类漏斗是**分支**的（选了 A 多出三道题，选了 B 直接跳过它们）。
    **步数变长变短本身不构成偏离**，也不许有任何以「期望几步」为前提的判据 ——
    所以这条用例里**一个期望步数都没有**（写成「多少步才对」就是把分支站钉死）。
    """
    p = plan.parse(GW_DESC)

    def _walk(rounds: int) -> list:
        turns = []
        for i in range(rounds):
            # 位置爬到清单末尾就**停在那儿**（真实形状：最后几个检查点在一条分支上反复走）
            turns.append({"content": f"【第 {min(i + 1, len(p.steps))} 步】接着走",
                          "calls": [("click", {"selector": "#get-started"})]})
        turns.append({"content": "走完了"})
        return turns

    for rounds, sub in ((20, "short"), (34, "long")):
        where = tmp_path / sub
        where.mkdir()
        journey, _, _ = _run(
            where, {"click": [{"structured": {"ok": True}}]}, _walk(rounds),
            plan=p, budget=browser_agent.Budget(max_steps=99, max_rounds=99),
        )
        assert len(journey.steps) == rounds, f"{rounds} 轮那一趟走了 {len(journey.steps)} 步"
        assert journey.stop_reason == "model_done", (rounds, journey.stop_reason)
        assert journey.deviations == [], (rounds, journey.deviations)
        # 1 = **收尾那一轮**（它也是一轮没推进）；要是长的那一趟被判成停滞，这里会是
        # `plan_stalled` 且步数远少于 34 —— 判据在下面那条 `stop_reason` 上
        assert journey.stall_rounds == 1, (rounds, journey.stall_rounds)
        assert len(journey.plan_ledger) == len(p.steps), (rounds, journey.plan_ledger)


def test_a_walk_that_stops_moving_stops_the_run(tmp_path):
    """§2.5：连着 `stall_limit` 轮**位置没前进 + 页面没变 + 没有一步做成** → 停。

    凭什么停：实测里模型「不卡住的错」是靠**乱点**表现的（点出站方弹窗就是一次）。
    **停得早 = 少点几下真页面**，这在真站上是实打实的收益。
    ⚠️ 判据全是**感知 / 声明**（标记、页面状态、`result.ok`），没有一条是语义判断。
    """
    p = plan.parse(GW_DESC)
    journey, fake, _ = _run(
        tmp_path,
        {"click": [{"error": "没有找到选择器 #ghost"}]},
        [{"content": "【第 1 步】点它", "calls": [("click", {"selector": "#ghost"})]}] * 6,
        plan=p, budget=browser_agent.Budget(max_steps=50, max_rounds=50, stall_limit=2),
    )
    # 6 是**设计注给的猜测**（STUCK_LIMIT 的两倍），不是量出来的 —— M3 被预算钉死，校准不了
    assert browser_agent.Budget().stall_limit == 6, "初值 6（设计注的猜测，见 Budget 的注释）"
    assert browser_agent._as_budget({"stall_limit": 2}).stall_limit == 2, \
        "dict 那条路也要接上 —— 不然给的值被**静默丢掉**，跑起来才发现没生效"
    # 字符串是配置里最容易写错的形状（`"6"` 看着就像个数）—— 要在**边界上**说清楚是哪个键，
    # 而不是炸到一半才在比较那一行现形（复审 M-6 的探针 P-B）
    with pytest.raises(TypeError, match="stall_limit.*字符串"):
        browser_agent._as_budget({"stall_limit": "6"})
    assert journey.stop_reason == "plan_stalled", journey.stop_reason
    assert journey.stall_rounds == 2, journey.stall_rounds
    assert len(fake.calls) == 3, f"判成停滞之后还在问模型（问了 {len(fake.calls)} 轮）"
    # 人话里带上**它卡在第几步、卡在哪一句描述上**（§2.5）
    assert any("计划停滞" in n and "第 1 步" in n and p.steps[0].text in n
               for n in journey.notes), journey.notes
    # 账本照旧齐全：停下也要「清单上每一项都有交代」（B2）
    assert len(journey.plan_ledger) == len(p.steps), journey.plan_ledger
    # 轮数没记到那句**不许说成「被人打断」**（这一趟不是人停的）——
    # 读账的人会拿它判断「要不要接着跑」，说错停因比不写还坏。
    assert any("没记到轮数" in n for n in journey.notes), journey.notes
    assert not any("被人打断" in n for n in journey.notes), journey.notes
    # **内部停因的 token 不许进人话**（复审 M-5）：`plan_stalled` 是给代码看的，
    # 这份账的读者是非技术的人（D16）—— 停因本身在 `stop_reason` 里，账上不缺它。
    assert not any("plan_stalled" in n for n in journey.notes), journey.notes
    # ★ 有信息的那一条必须是**最后一条**：`graph._journey_say` 的尾巴取的是 `notes[-1]`
    #   （复审 I-4：反过来写，人最终看到的是「轮数没记到」那句 bookkeeping，
    #   带步骤号与描述原文的句子在 `notes[-2]`，于是 `'第 1 步' in say` 是 False）。
    assert "计划停滞" in journey.notes[-1] and p.steps[0].text in journey.notes[-1], \
        f"`explore_say` 的尾巴取 notes[-1]，这一条得是有信息的那句：{journey.notes[-1]!r}"


def test_just_looking_at_the_same_page_is_not_progress(tmp_path):
    """§2.5 的第三个信号只认**会改页面的动作**：只看一眼**不算**推进（反例）。

    一个反复看同一页的模型：位置没动、页面没变、没有一步做成 —— 那是**停滞**。
    要是不这么写（看一眼也算「成功动作」），停滞判据就**永远响不了**：
    模型只要一直 observe 就永远不会被判成卡住。
    """
    p = plan.parse(GW_DESC)
    journey, fake, _ = _run(
        tmp_path,
        {"observe": [{"structured": PAGE_LANDING}]},
        [{"content": "【第 1 步】看一眼", "calls": [("observe", {})]}] * 6,
        plan=p, budget=browser_agent.Budget(max_steps=50, max_rounds=50, stall_limit=2),
    )
    assert journey.stop_reason == "plan_stalled", journey.stop_reason
    assert len(fake.calls) == 3, f"判成停滞之后还在问模型（问了 {len(fake.calls)} 轮）"


def test_going_back_to_an_earlier_step_still_counts_as_moving(tmp_path):
    """**一处已知读法**（模块 docstring 里记着）：位置**动了**就算前进，**不判方向**。

    §2.4 明写往回跳「照记、**不判它该不该**」—— 停滞判据这边照同一条办：
    只有「位置一动不动」才算没前进。要改成「只有往前才算前进」，
    那是在替人判它该不该退 —— **改这一条要连这里一起改**（不是悄悄改）。
    """
    p = plan.parse(BRANCH_DESC)
    journey, fake, _ = _run(
        tmp_path,
        {"click": [{"error": "没有找到选择器 #ghost"}]},
        [{"content": "【第 3 步】走第三步", "calls": [("click", {"selector": "#ghost"})]},
         {"content": "【第 3 步】还在这儿", "calls": [("click", {"selector": "#ghost"})]},
         {"content": "【第 1 步】退回去看第一步", "calls": [("click", {"selector": "#ghost"})]},
         {"content": "走完了"}],
        plan=p, budget=browser_agent.Budget(max_steps=50, max_rounds=50, stall_limit=2),
    )
    assert journey.stall_rounds == 1, journey.stall_rounds      # 1 = 收尾那一轮
    assert journey.stop_reason == "model_done", journey.stop_reason
    assert len(fake.calls) == 4, "第 4 轮没跑起来 = 往回跳被判成停滞了"


def test_one_page_change_resets_the_stall_count(tmp_path):
    """反例（**同一条判据**）：中间只要有一轮**页面真的变了**，计数就**清零** —— 不许停。

    与上面那条只差一处：第 3 轮看了一眼，而**看到的确实是另一页**。
    → 「动不了」才叫停滞；「动着但没走到清单上」不是（那正是分支，§2.3.1）。
    """
    p = plan.parse(GW_DESC)
    journey, fake, _ = _run(
        tmp_path,
        {"click": [{"error": "没有找到选择器 #ghost"}],
         "observe": [{"structured": PAGE_QUIZ}]},
        [{"content": "【第 1 步】点它", "calls": [("click", {"selector": "#ghost"})]},
         {"content": "【第 1 步】点它", "calls": [("click", {"selector": "#ghost"})]},
         {"content": "【第 1 步】看一眼", "calls": [("observe", {})]},
         {"content": "【第 1 步】点它", "calls": [("click", {"selector": "#ghost"})]},
         {"content": "走完了"}],
        plan=p, budget=browser_agent.Budget(max_steps=50, max_rounds=50, stall_limit=2),
    )
    assert journey.stop_reason == "model_done", journey.stop_reason
    assert journey.stall_rounds == 2, journey.stall_rounds
    assert len(fake.calls) == 5, f"页面变了却还是停了（问了 {len(fake.calls)} 轮）"


def test_a_zero_stall_limit_is_clamped_and_never_says_zero_rounds(tmp_path):
    """复审 I-3：`stall_limit <= 0` **夹到 1**，且人话里**永远不出现「连着 0 轮」**。

    原先阈值判在「刚刚清零」那一支也执行的层上（`0 >= 0` 成立）→ `stall_limit=0` 时
    **每轮都在推进**的探路照样被判成停滞，人话还写着「连着 **0** 轮没有推进」——
    一个**自己说自己没在停滞**的停因（与 P5「不许编一个数」同族）。

    两次跑把处置**钉死**（复审给的两个选项，选了「夹住」）：
      ① 每轮都推进 + `stall_limit=0` → **不许停**（不许出现「连着 0 轮」那句）；
      ② 有一轮没推进 + `stall_limit=0` → **还是要停**，人话是「连着 **1** 轮」——
         这一条证明的是「夹到 1」而不是「静默关掉判据」（关掉的话它会跑完）。
    """
    p = plan.parse(GW_DESC)

    moving = tmp_path / "moving"
    moving.mkdir()
    journey, fake, _ = _run(
        moving,
        {"click": [{"structured": {"ok": True}}]},
        [{"content": "【第 1 步】点它", "calls": [("click", {"selector": "#get-started"})]},
         {"content": "【第 2 步】点它", "calls": [("click", {"selector": "#get-started"})]},
         {"content": "走完了"}],
        plan=p, budget=browser_agent.Budget(max_steps=50, max_rounds=50, stall_limit=0),
    )
    assert journey.stop_reason == "model_done", journey.stop_reason
    assert len(fake.calls) == 3, f"每轮都在推进，却被掐死了（问了 {len(fake.calls)} 轮）"
    assert not any("连着 0 轮" in n for n in journey.notes), journey.notes
    assert any("不是个能成立的阈值" in n for n in journey.notes), \
        f"夹住了就要说出来（不静悄悄）：{journey.notes}"

    stuck = tmp_path / "stuck"
    stuck.mkdir()
    journey, fake, _ = _run(
        stuck,
        {"click": [{"error": "没有找到选择器 #ghost"}]},
        [{"content": "【第 1 步】点它", "calls": [("click", {"selector": "#ghost"})]}] * 4,
        plan=p, budget=browser_agent.Budget(max_steps=50, max_rounds=50, stall_limit=0),
    )
    assert journey.stop_reason == "plan_stalled", journey.stop_reason
    assert len(fake.calls) == 2, f"夹到 1 之后第一轮没推进就该停（问了 {len(fake.calls)} 轮）"
    assert any("连着 1 轮没有推进" in n for n in journey.notes), journey.notes


def test_a_contradiction_is_recorded_verbatim_and_does_not_advance_the_position(tmp_path):
    """**B6 的一半**：模型报「描述说…页面上是…」→ `deviations` 里有一条（**原话**），
    而且**位置停在这一步不前进**（§2.3(b)）。

    矛盾是**模型声明的事实报告**，不是分类：系统**不判**「这是分支还是描述写错了」
    （它判不了，也不该判）—— 定性归账本和人。所以 `deviations` 里存的就是**那句话**。
    """
    p = plan.parse(GW_DESC)
    said = "【第 3 步】描述说点「有没有浴缸」，这一页上没有这句话，页面上是「屋顶类型」"
    journey, _, _ = _run(
        tmp_path,
        {"click": [{"structured": {"ok": True}},
                   {"error": "没有找到选择器 #ghost"}]},
        [{"content": "【第 2 步】点了第一个选项", "calls": [("click", {"selector": "#get-started"})]},
         {"content": said, "calls": [("click", {"selector": "#ghost"})]},
         {"content": "先停一下，问问人"}],
        plan=p, budget=browser_agent.Budget(max_steps=50, max_rounds=50, stall_limit=99),
    )
    assert journey.deviations == [said], journey.deviations
    # 账本那一格也看得出来（模型的话原样记着）
    assert journey.plan_ledger[2]["state"] == "contradicted", journey.plan_ledger
    assert journey.plan_ledger[2]["why"] == said, journey.plan_ledger
    # ★ 位置**不前进**：第 3 步报的是矛盾，位置就停在**第 2 步**
    #   （2 = 第 2 轮那一格 + 收尾那一格；与下面 control 的 1 差的就是**矛盾那一轮**）
    assert journey.stall_rounds == 2, journey.stall_rounds

    # 反例（同一格）：同样的话，**去掉那句矛盾声明** → 位置照常前进（计数清零）
    control_dir = tmp_path / "control"
    control_dir.mkdir()
    control, _, _ = _run(
        control_dir,
        {"click": [{"structured": {"ok": True}}, {"error": "没有找到选择器 #ghost"}]},
        [{"content": "【第 2 步】点了第一个选项", "calls": [("click", {"selector": "#get-started"})]},
         # 近身反例：话里**「描述」和「页面上」都出现了**，但没有那句**约定的说法**
         # （判据是约定好的说法，不是「出现过某两个词就算矛盾」）
         {"content": "【第 3 步】按描述走下去，这一页上没有那一项",
          "calls": [("click", {"selector": "#ghost"})]},
         {"content": "先停一下，问问人"}],
        plan=p, budget=browser_agent.Budget(max_steps=50, max_rounds=50, stall_limit=99),
    )
    assert control.deviations == [], control.deviations
    assert control.stall_rounds == 1, control.stall_rounds


def test_after_a_contradiction_the_position_is_not_frozen_forever(tmp_path):
    """驳一条**替代实现**（复审 M-2）：矛盾之后位置**不是从此钉死**，下一轮报到哪儿都照记。

    模块 docstring 里的读法②写的是「**这一轮**不算推进」，不是「位置从此冻结在第 k 步」——
    后半句今天**没有任何东西承载**，所以这里用**用例**把它钉住（裁定②：改用例、不改代码）。
    反例判据落在**停不停**上（`stall_limit=2`）：冻结的实现会让第 3 轮那次报到算不成推进
    → 第 4 轮的边界上凑够 2 轮 → `plan_stalled`，收尾那句就再也跑不到了。
    """
    p = plan.parse(GW_DESC)
    said = "【第 3 步】描述说点「有没有浴缸」，这一页上没有这句话，页面上是「屋顶类型」"
    journey, fake, _ = _run(
        tmp_path,
        {"click": [{"structured": {"ok": True}}, {"error": "没有找到选择器 #ghost"}]},
        [{"content": "【第 2 步】点了第一个选项", "calls": [("click", {"selector": "#get-started"})]},
         {"content": said, "calls": [("click", {"selector": "#ghost"})]},
         {"content": "【第 3 步】按页面上写的往下走", "calls": [("click", {"selector": "#ghost"})]},
         {"content": "走完了"}],
        plan=p, budget=browser_agent.Budget(max_steps=50, max_rounds=50, stall_limit=2),
    )
    assert journey.stop_reason == "model_done", journey.stop_reason
    assert len(fake.calls) == 4, "第 4 轮没跑起来 —— 位置在矛盾之后被冻住了（不是「这一轮不前进」）"
    assert journey.stall_rounds == 1, journey.stall_rounds
    # 矛盾那件事照旧记着（「报过的矛盾不许被降级」——`plan.ledger` 的守则）
    assert journey.deviations == [said], journey.deviations
    assert journey.plan_ledger[2]["state"] == "contradicted", journey.plan_ledger


# ═════════════════════ Task 5：重放前缀（R1–R4）与重放 ═════════════════════
#
# 窗口死了之后，系统要能照着账本把**安全的那一段**自己走回去（MCP 调用，**0 模型**）。
# 判据全部出自设计注 §1.4.3（四条边界规则）与 §1.6（失败一律停下说话）。
#
# 账本里的每一行**就是** `Journey.steps` 的那一步（`agent/journal.py` 的契约），
# 所以下面造的 `_row` 是**真形状**：`target` 里带 `selectors` 与 `frame_id`，
# `result` 里带 `ok` / `url` / `page_text_head` / `fill`。形状对不上的话，
# 这里钉住的东西在生产里一条都对不上（那正是「用假形状测出假绿」的来路）。

ENTRY = "https://site.test/funnel"
QUOTE = "https://site.test/quote"
TEXT_A = "先看看你能省多少"
TEXT_B = "填一下你的房子信息"
SUCCESS = "你的报价已经准备好了"


def _row(action, state="funnel", *, ok=True, target=None, result=None, note="做了一下"):
    res = {"ok": ok, "elapsed_ms": 1}
    res.update(result or {})
    res["ok"] = ok
    return {"state": state, "action": action, "target": target or {},
            "result": res, "note": note, "origin": "model"}


def _goto(url, *, state="start", ok=True, landing=""):
    """一条 `goto` 行。⚠️ 真实现里 `state` 是**发起那一刻**的状态（goto 不记页）。"""
    return _row("goto", state, ok=ok, target={"url": url},
                result={"url": landing or url}, note=f"打开了 {url}")


def _look(url, text, *, state="start", ok=True):
    """一条 `observe` 行（`_summarize` 给的那几样）。"""
    return _row("observe", state, ok=ok,
                result={"url": url, "title": "页面", "page_text_head": text},
                note="看了一眼页面")


def _click(label, selectors=("#cta",), *, state="funnel", ok=True, frame_id=""):
    """一条 `click` 行。`frame_id=""` 是主帧 —— `_target_of` **总会**写这个键。"""
    return _row("click", state, ok=ok,
                target={"text": label, "role": "button", "near": None,
                        "selectors": list(selectors), "above_fold_only": False,
                        "frame_id": frame_id},
                result={"selector": selectors[0]},
                note=f"点了「{label}」" if ok else f"页面上没找到「{label}」，这一步没做成")


def _fill(label, value, selector="#zip", *, kind="value", state="quote"):
    """一条 `form` 行（`result.fill` 是 `_fill_info` 的产物，**值就在里面**）。"""
    return _row("form", state, target={"label": label, "text": None, "role": None,
                                       "near": None, "selectors": [selector],
                                       "above_fold_only": False, "frame_id": ""},
                result={"selector": selector,
                        "fill": {"name": "postcode", "source": "postcode", "kind": kind,
                                 "label": label, "value": value, "fallback": []}},
                note=f"填好了「{label}」")


def _walk_rows():
    """一趟真形状的走法（状态名是**观测那一刻**的，与 `_describe` 同规矩）。

    两条 `observe` **各自让状态名变了一次**（start→funnel、funnel→quote）——
    这正是 `replay` 认「这一步改了页」的判据，也是它要核验的那两个点。
    """
    return [
        _goto(ENTRY, state="start"),
        _look(ENTRY, TEXT_A, state="start"),
        _click("开始申请", state="funnel"),
        _look(QUOTE, TEXT_B, state="funnel"),
    ]


# ─────────────────────── R1：没做成的动作不重放 ───────────────────────


def test_a_walk_that_was_seen_and_never_crossed_the_line_replays_whole():
    """正例（R1–R4 全过的基线）：走过的、被看过的、没过成功线的，整段都能重放。

    这一条同时是后面每一条反例的**对照**：反例里只改一处，前缀就必须短一截。
    """
    rows = _walk_rows()
    prefix, why = browser_agent.replayable_prefix(
        rows, SUCCESS, entry_url=ENTRY)
    assert prefix == rows, f"这一段本该整段可重放，结果只剩 {len(prefix)} 行"
    assert "没有碰到边界" in why, why


def test_a_step_that_did_not_work_stops_the_prefix_before_it():
    """R1 反例：`result.ok is False` 的步**不重放**（它当年就没做成，重放它干嘛）。"""
    rows = _walk_rows() + [_click("提交申请", ok=False, state="quote"),
                           _look("https://site.test/thanks", "谢谢", state="quote")]
    prefix, why = browser_agent.replayable_prefix(rows, SUCCESS, entry_url=ENTRY)
    assert prefix == rows[:4], f"没做成的那一步不该进前缀：{[r['action'] for r in prefix]}"
    assert "R1" in why and "提交申请" in why, why


# ─────────────────────── R2：没被看见的动作不重放 ───────────────────────


def test_an_action_nobody_looked_at_afterwards_is_never_replayed():
    """R2 反例：**提交就藏在尾部那一段没被观测的动作里** —— 一律不重放。

    这是四条判据里最要命的一条：模型走完就说完了，最后一个动作（常常就是提交）
    后面通常**没有** observe。
    """
    rows = _walk_rows() + [_click("提交申请", state="quote")]     # 后面再没有任何观察
    prefix, why = browser_agent.replayable_prefix(rows, SUCCESS, entry_url=ENTRY)
    assert prefix == rows[:4], f"尾部那一段没被观测的动作进了前缀：{[r['action'] for r in prefix]}"
    assert "R2" in why and "没被看见" in why, why


def test_a_failed_look_is_not_having_been_seen():
    """R2 的**第二半**：观察本身也要**做成**了才算「被看见」。

    只在账上「有一条 observe」就放行的话，一次失败的 observe（工具报错、窗口开始不对劲）
    会把整个尾部重新放回可重放集合 —— 而那正是 R2 存在的理由。
    """
    rows = _walk_rows() + [_click("提交申请", state="quote"),
                           _look("https://site.test/thanks", "谢谢", state="quote", ok=False)]
    prefix, why = browser_agent.replayable_prefix(rows, SUCCESS, entry_url=ENTRY)
    assert prefix == rows[:4], f"一条失败的 observe 被当成了「被看见」：{why}"
    assert "R2" in why, why


# ─────────────────────── R3：过了成功线就不许再过 ───────────────────────


def test_the_page_the_step_landed_on_carries_the_success_text():
    """R3 反例（主形态）：**这一步落到的那一页上已经出现成功文案** → 停在它前面。

    ⚠️ 判据必须看**它落到的那一页**（那一步之后那条 observe 记下的），
    只看「走这一步之前看到过什么」是**看不见**的 —— 而提交正是这么过线的。
    ⚠️ 断言下到**理由那句人话**上：这一支要出现「落到的那一页」，**不许**出现「撞」
    （那是另一种形状那句 —— 两者必须分得开，见下面那条用例）。
    """
    rows = [_goto(ENTRY, state="start"),
            _look(ENTRY, TEXT_A, state="start"),
            _click("提交申请", state="funnel"),
            _look(QUOTE, f"已经收到你的申请。{SUCCESS}", state="funnel")]
    prefix, why = browser_agent.replayable_prefix(rows, SUCCESS, entry_url=ENTRY)
    assert prefix == rows[:2], f"过线的那一步进了前缀：{[r['action'] for r in prefix]}"
    assert "R3" in why and SUCCESS in why, why
    assert "落到的那一页" in why and "撞" not in why, why


def test_a_success_text_that_collided_with_an_earlier_page_also_stops_everything():
    """R3 反例（**撞**）：那句话出现在**我们早先就在的**那一页上 → **也不许过**。

    保守到底的理由：撞了说明**这条判据在这一站上不可靠**，而不可靠的判据在重放里的
    代价是「真页面上多按几下」—— 那时没有模型在场、也未必有人在看。

    ⚠️ 夹具**必须**摆成「**没有动作把页面带到这儿**」的形状（复审裁定② + R-E9 之后收紧）：
    同一页**连看两眼**，那句话在第二眼里才出现 —— 于是被拦下的是**那次观察自己**。
    ⚠️ 这一形**不能有前置动作**：`goto` 之类的动作，它的窗口（R-E9 之后）会一直吃到
    「下一次动作之前」，于是那句话落进它的窗口、它先停下 —— 那是**另一形**（「落点」那句）。
    两种形状的**人话**因此各自准确：窗口盖住的那一形说「它落到的那一页」，
    没有动作盖住的那一形说「撞」。
    （不给 `entry_url`：免得合成那条 goto 又变成「有前置动作」。）
    """
    rows = [_look(ENTRY, TEXT_A, state="start"),                     # 先看一眼：没有那句话
            _look(ENTRY, f"这一页上有一句普通话，里面写着 {SUCCESS}",   # 又看一眼：有了
                  state="funnel"),
            _click("开始申请", state="quote"),
            _look(QUOTE, TEXT_B, state="quote")]
    prefix, why = browser_agent.replayable_prefix(rows, SUCCESS)
    assert prefix == rows[:1], f"撞了那句话之后还有 {len(prefix)} 行进前缀"
    assert "R3" in why and SUCCESS in why, why
    assert "撞" in why, why
    assert "落到的那一页" not in why, why
    assert "不是哪一步把它带过来" not in why, why     # 修复轮 2 那条错的措辞不许回来


# ─────────────────────── R4：goto 只回自己去过的地方 ───────────────────────


def test_a_goto_into_a_place_we_never_saw_stops_the_prefix_before_it():
    """R4 反例：**带一次性 query 的深链**（确认链接 / 令牌链接）不许重放。

    ⚠️ 判据落在**要去的那串地址**（`target.url`）上，**不是**它落在了哪儿 ——
    见 `test_the_landing_url_does_not_get_a_one_time_link_through`。
    """
    rows = [_goto(ENTRY, state="start"),
            _look(ENTRY, TEXT_A, state="start"),
            _goto("https://site.test/claim?token=9f3a", state="funnel",
                  landing="https://site.test/claim"),
            _look("https://site.test/claim", "确认你的邮箱", state="funnel")]
    prefix, why = browser_agent.replayable_prefix(rows, SUCCESS, entry_url=ENTRY)
    assert prefix == rows[:2], f"没见过的深链进了前缀：{[r['action'] for r in prefix]}"
    assert "R4" in why and "token=9f3a" in why, why


def test_the_landing_url_does_not_get_a_one_time_link_through():
    """R4 的**哨兵**：判据不许被「它落在了哪儿」满足。

    设计注 R4 那行括号里写着「（或其落地 URL）」—— 本实现**不采纳**，理由是硬的：
    一条 goto 的落地 URL **按定义**就写在它后面那条 observe 上（`pages` 就是这么攒出来的），
    拿它当判据等于**让那条 goto 自己给自己发通行证** → R4 恒真 → 等于没有这条判据
    （本项目一整天在治的正是「恒真的钉子」）。所以：只看**要去的那串地址**。
    """
    rows = [_goto(ENTRY, state="start"),
            _look(ENTRY, TEXT_A, state="start"),
            # 落地页**是**见过的地址（就在下面那条 observe 里），但要去的那串不是
            _goto("https://site.test/claim?token=9f3a", state="funnel",
                  landing="https://site.test/funnel"),
            _look(ENTRY, TEXT_A, state="funnel")]
    prefix, why = browser_agent.replayable_prefix(rows, SUCCESS, entry_url=ENTRY)
    assert prefix == rows[:2], "落地 URL 给一次性深链发了通行证（R4 成了恒真判据）"
    assert "R4" in why, why


def test_a_goto_back_to_the_entry_or_to_a_page_we_saw_passes():
    """R4 正例：**回入口**与**回见过的页面**都过（这正是重放要用的那两下）。

    ⚠️ 「见过的页面」有两个来源：账上那些 `observe` 行自己的地址，以及 `pages`
    （`journey.pages` 攒出来的那份）。这条用例里的深链**只**出现在 `pages` 里 ——
    这样它才真的钉住「两个来源都算」（只认其中一个的实现会在这里红）。
    """
    deep = "https://site.test/deep-page"
    rows = [_goto(ENTRY, state="start"),                       # 回入口
            _look(ENTRY, TEXT_A, state="start"),
            _goto(deep, state="funnel", landing=ENTRY),        # 回见过的页面（只在 pages 里）
            _look(ENTRY, TEXT_A, state="funnel")]
    pages = [{"name": "deep", "when": None, "url": deep, "title": "深一点的页"},
             {"name": "funnel", "when": {"url_contains": ENTRY}, "url": ENTRY, "title": "页面"}]
    prefix, why = browser_agent.replayable_prefix(
        rows, SUCCESS, entry_url=ENTRY, pages=pages)
    assert prefix == rows, f"回入口 / 回见过的页面被拦下了：{why}"
    assert "没有碰到边界" in why, why


# ─────────────────────── 前缀的形状 ───────────────────────


def test_a_bad_step_in_the_middle_does_not_let_later_good_steps_through():
    """前缀是**吃到第一个不满足就停**，不是「跳过它接着吃」。

    跳过会得到一条**中间少了一节**的路径 —— 重放它会走到别的页面上去，
    而那时候没有任何人看得出来「少了一节」。
    """
    rows = [_goto(ENTRY, state="start"),
            _look(ENTRY, TEXT_A, state="start"),
            _click("坏掉的那一下", ok=False, state="funnel"),      # ← 就坏在这儿
            _look(QUOTE, TEXT_B, state="funnel"),
            _click("开始申请", state="quote"),
            _look("https://site.test/step3", "第三页", state="quote")]
    prefix, why = browser_agent.replayable_prefix(rows, SUCCESS, entry_url=ENTRY)
    assert prefix == rows[:2], f"坏步后面的好步被放进了前缀：{[r['action'] for r in prefix]}"
    assert "R1" in why, why


def test_the_walk_starts_by_opening_the_entry_when_the_ledger_never_did():
    """§1.5：新窗口是一个**干净身份**的浏览器 —— 重放只能是「从入口重走一遍」。

    账本头上没有 `goto` 时（模型一上来就看见了页面），由入场合成了**一条** ——
    合成的那条排在最前面，重放的第一件事就是它。
    反例（同一条判据）：不给入口地址 → **不合成**（那是「少给了一个输入」，
    系统不猜一个 URL 出来）。
    """
    rows = [_look(ENTRY, TEXT_A, state="start"),
            _click("开始申请", state="funnel"),
            _look(QUOTE, TEXT_B, state="funnel")]
    prefix, why = browser_agent.replayable_prefix(rows, SUCCESS, entry_url=ENTRY)
    assert [r["action"] for r in prefix] == ["goto", "observe", "click", "observe"], \
        [r["action"] for r in prefix]
    assert prefix[0]["target"]["url"] == ENTRY, prefix[0]
    plain, _ = browser_agent.replayable_prefix(rows, SUCCESS)
    assert plain == rows, "没给入口地址却凭空合成了一条 goto"


def test_an_empty_success_text_is_a_missing_input_not_a_free_pass():
    """`success_text` 为空**抛** —— 它不是「没有约束」，是**少给了一个输入**。

    这是 R3 唯一的输入，而 R3 管的是「过了成功线之后不许再动真页面」。
    缺了它，「没有约束」与「约束不成立」在代码里长得一模一样 ——
    `intake` 本来就该拦住这种载荷，所以这里也不替它兜。
    """
    rows = _walk_rows()
    for bad in ("", None, "   "):
        with pytest.raises(ValueError):
            browser_agent.replayable_prefix(rows, bad, entry_url=ENTRY)


# ─────────────────────── 重放：走这段前缀（0 模型）───────────────────────


def _replay(tmp_path, rows, responses, **kwargs):
    """桩 MCP 上走一段前缀 —— **不开浏览器、不问模型**。返回 (结果, 调用流水线)。"""
    session, log = _stub(tmp_path, responses)
    try:
        out = browser_agent.replay(session, rows, **kwargs)
    finally:
        session.close()
    return out, _calls(log)


def _pages_for(rows):
    """桩要回的页面模型：按账上那两页的（url + 正文）造的（核验就是比这两样）。"""
    return [{"structured": _live_page(ENTRY, TEXT_A)},
            {"structured": _live_page(QUOTE, TEXT_B)}]


def _live_page(url, text, title="页面"):
    """**重放途中** `observe` 回来的那一份（与上面那个 `_page` 无关 —— 那个是产物侧的）。"""
    return {"url": url, "title": title, "page_text": text, "shadow_roots": 0,
            "viewport_css_px": {"width": 1280, "height": 800},
            "actions": [], "fields": [], "option_groups": [], "obstructions": [],
            "honeypots": [], "diagnostics": []}


def test_replay_never_asks_the_model_anything(tmp_path, monkeypatch):
    """**0 模型调用** —— 判据只能用桩来表达：把模型那条路整个换成会炸的东西。

    在「重放自己的账」里断言「没问模型」是自证；只有**替掉模型那一层**
    （`llm.client` 与 `llm.run_tool_loop`），才谈得上「这条路够不着模型」。
    """
    def boom(*_a, **_k):
        raise AssertionError("重放这条路不许问模型")

    monkeypatch.setattr(browser_agent.llm, "client", boom)
    monkeypatch.setattr(browser_agent.llm, "run_tool_loop", boom)
    out, calls = _replay(tmp_path, _walk_rows(), {"observe": _pages_for(None)})
    assert out["done"] == 2, out
    assert [c["name"] for c in calls] == ["goto", "observe", "click", "observe"], calls


def test_every_replayed_step_is_written_down_as_a_replay_step(tmp_path):
    """账要对：每一步都带 `origin="replay"` 进账，而且**产物侧照样能用**（`states()`）。"""
    rows = _walk_rows()
    steps = []
    out, _ = _replay(tmp_path, rows, {"observe": _pages_for(rows)},
                     on_step=steps.append)
    assert [s["action"] for s in steps] == ["goto", "observe", "click", "observe"], steps
    assert all(s["origin"] == "replay" for s in steps), steps
    # 账本里的行**就是** `Journey.steps` 的那一步 —— 所以产物那侧一个字的改动都不需要
    journey = browser_agent.Journey()
    journey.steps = list(steps)
    assert [g["name"] for g in journey.states()] == ["start", "funnel"], journey.states()
    assert journey.fills() == {}, journey.fills()


def test_one_observe_per_page_the_ledger_says_changed(tmp_path):
    """**逐页核验**，不是每一步都核：4 行里只有那两条「让状态名变了」的 observe 要核。

    判据落在**真发出去几次 observe**（桩的流水线）上 —— 在重放自己的账本里数
    是自证（它想让它是几次就记几次）。
    """
    rows = _walk_rows()
    out, calls = _replay(tmp_path, rows, {"observe": _pages_for(rows)})
    assert [c["name"] for c in calls].count("observe") == 2, calls
    assert out["done"] == 2, out
    assert out["landed"] == QUOTE, out
    assert "都重放了" in out["why"] or "都走完了" in out["why"], out["why"]


def test_a_page_that_is_not_where_the_ledger_said_stops_the_replay(tmp_path):
    """负例：走出这一步之后那一页**不是账上那一页** → 停，并说清「我以为会到 X，实际是 Y」。

    ⚠️ 分支站上「落到另一条分支」与「选错了元素」**系统分不出** —— 两条都停（§1.6）。
    停 = 叫一次人；不停 = 在一次没人看着的重放里继续点真页面。**选停。**
    """
    rows = _walk_rows()
    responses = {"observe": [{"structured": _live_page("https://site.test/other", "别的东西")},
                             {"structured": _live_page(QUOTE, TEXT_B)}]}
    out, calls = _replay(tmp_path, rows, responses)
    assert out["done"] == 1, out          # 只有 goto 走成了（好：停在第 2 行那个核验点上）
    assert "我以为会到" in out["why"] and "实际是" in out["why"], out["why"]
    assert ENTRY in out["why"], out["why"]
    assert "other" in out["why"], out["why"]
    assert [c["name"] for c in calls] == ["goto", "observe"], calls


def test_the_verification_uses_the_relaxed_when_not_a_byte_comparison(tmp_path):
    """核验用的是**产物那套放宽后的 `when`**（稳定前缀 + 正文），不是逐字节比对。

    判据：重放时那一页与账上那一页**只在轮换码 / query 上不同** → **照过**
    （逐字节比对的实现会在这里红，而它红的代价是「同一页的变体被读成失败」）。
    """
    recorded = "https://site.test/funnel/cr640"
    live_url = "https://site.test/funnel/gt1791-1"       # 同一页的轮换变体（真站实测那种）
    rows = [_goto(recorded, state="start"),
            _look(recorded, TEXT_A, state="start"),
            _click("开始申请", state="funnel"),
            _look(QUOTE, TEXT_B, state="funnel")]
    responses = {"observe": [{"structured": _live_page(live_url, TEXT_A)},
                             {"structured": _live_page(QUOTE, TEXT_B)}]}
    out, _ = _replay(tmp_path, rows, responses)
    assert out["done"] == 2, out
    assert "我以为会到" not in out["why"], out["why"]


def test_a_selector_that_resolves_nowhere_stops_before_that_step(tmp_path):
    """负例：`target.selectors` 全试完都没找到 → **停在那一步之前** + 人话。

    「跳过这一步接着走」在这套系统里是被明令禁止的（§1.6）：跳过之后剩下的动作
    会落在一页**它们从没在上面做过**的页面上 —— 而那时没有模型在场。
    """
    rows = _walk_rows()
    rows[2] = _click("开始申请", selectors=("#old", "#older"), state="funnel")
    out, calls = _replay(tmp_path, rows,
                         {"observe": _pages_for(rows),
                          "click": [{"error": "没有找到选择器 #old"},
                                    {"error": "没有找到选择器 #older"}]})
    assert out["done"] == 1, out                     # 只有 goto 走成了
    assert [c["args"]["selector"] for c in calls if c["name"] == "click"] == ["#old", "#older"]
    assert "开始申请" in out["why"], out["why"]
    assert "第 3 行" in out["why"] or "第 3 步" in out["why"], out["why"]


def test_the_first_selector_that_works_wins(tmp_path):
    """选择器**按账上那份顺序**一个个试，**第一个能用的胜**（§1.4.4）。"""
    rows = _walk_rows()
    rows[2] = _click("开始申请", selectors=("#old", "#new"), state="funnel")
    out, calls = _replay(tmp_path, rows,
                         {"observe": _pages_for(rows),
                          "click": [{"error": "没有找到选择器 #old"},
                                    {"structured": {"ok": True}}]})
    assert out["done"] == 2, out
    assert [c["args"]["selector"] for c in calls if c["name"] == "click"] == ["#old", "#new"]


def test_the_frame_rides_along_only_when_the_ledger_wrote_one_down(tmp_path):
    """`frame_id`：账上**有**就带上；**没有就不带** —— 不许编一个 `"main"`（G4）。

    ⚠️ `_target_of` **总会**写这个键，主帧那一支的值是 `""` —— 所以判据是
    「值非空才传」，不是「键在不在」。`"main"` 只是个显示用的名字，
    传进 `--frame-id` 会被当成一个不存在的帧（真站实测过这一类错）。
    """
    rows = _walk_rows()
    rows[2] = _click("子帧里的按钮", state="funnel", frame_id="F1")
    out, calls = _replay(tmp_path, rows, {"observe": _pages_for(rows)})
    assert out["done"] == 2, out
    got = [c["args"].get("frame_id") for c in calls if c["name"] == "click"]
    assert got == ["F1"], got

    plain = _walk_rows()
    main_dir = tmp_path / "main"
    main_dir.mkdir()
    out2, calls2 = _replay(main_dir, plain, {"observe": _pages_for(plain)})
    assert out2["done"] == 2, out2
    assert all("frame_id" not in c["args"] for c in calls2 if c["name"] == "click"), calls2
    assert all(c["args"].get("frame_id") != "main" for c in calls2), calls2


def test_a_fill_replays_the_value_the_ledger_recorded(tmp_path):
    """填表重放的是**账上记的那个值**，不是重新随机一个（§1.4.1 的由头）。

    三种形态（`value` / `check` / `select`）各自要发成对的参数 ——
    `form` 那个工具**恰好收一个**，给错了就是静默没发生（registry 的 `formMode`）。
    """
    rows = [_goto(QUOTE, state="start"),
            _look(QUOTE, TEXT_B, state="start"),
            _fill("ZIP code", "90001", selector="#zip", state="quote"),
            _look("https://site.test/step2", "第二页", state="quote"),
            _fill("屋顶类型", "平顶", selector="#roof", kind="select", state="step2"),
            _look("https://site.test/step3", "第三页", state="step2"),
            _fill("有浴缸吗", "true", selector="#tub", kind="check", state="step3"),
            _look("https://site.test/step4", "第四页", state="step3")]
    out, calls = _replay(tmp_path, rows, {"observe": [
        {"structured": _live_page(QUOTE, TEXT_B)},
        {"structured": _live_page("https://site.test/step2", "第二页")},
        {"structured": _live_page("https://site.test/step3", "第三页")},
        {"structured": _live_page("https://site.test/step4", "第四页")},
    ]})
    assert out["done"] == 4, out
    fills = [c["args"] for c in calls if c["name"] == "form"]
    assert fills[0].get("value") == "90001", fills
    assert fills[1].get("select") == "平顶" and "value" not in fills[1], fills
    assert fills[2].get("check") is True, fills


def test_a_window_that_died_again_makes_the_replay_start_over(tmp_path):
    """§1.6：重放途中窗口又死了 → **整段重来**（它里面没有不可逆动作，重来是安全的）。"""
    rows = _walk_rows()
    # ⚠️ 桩的答复是**按顺序消费**的：每一遍都在第 3 行那次点击上死掉，
    # 所以每一遍只会看一眼（第 2 行那个核验点）—— 给的答复也得是那一页。
    out, calls = _replay(tmp_path, rows,
                         {"observe": [{"structured": _live_page(ENTRY, TEXT_A)}],
                          "click": [{"error": "连不上 127.0.0.1:9222"}]},
                         alive=lambda: False)
    assert out["done"] == 1, out
    assert "窗口又死了" in out["why"], out["why"]
    # **整段**重来：死在中间那一步，第二遍照样从**头**开始走（goto 再发一次）——
    # 不是「从断点接着走」（那会落在一页状态不明的页面上）
    assert [c["name"] for c in calls] == ["goto", "observe", "click"] * 3, calls


def test_a_window_that_stays_dead_gives_up_after_three_whole_attempts(tmp_path):
    """负例：窗口一直起不来 → **最多 3 遍**，到顶停下说人话（不许无限重来）。"""
    rows = _walk_rows()
    out, calls = _replay(tmp_path, rows,
                         {"goto": [{"error": "连不上 127.0.0.1:9222"}]},
                         alive=lambda: False)
    assert [c["name"] for c in calls].count("goto") == 3, calls
    assert out["done"] == 0, out
    assert "3" in out["why"] and "窗口" in out["why"], out["why"]


def test_a_broken_window_probe_is_treated_as_dead_not_as_alive(tmp_path):
    """那道「窗口还活着吗」的探针**自己坏掉**时，按**死了**处理（与闸坏掉那条同一条规矩）。

    反过来（坏掉 = 活着）会是什么样：窗口真死了、探针读不出来 → 重放记成
    「页面上找不到那个按钮」—— 一句**指错方向**的话，读账的人会去查选择器。
    """
    def broken():
        raise RuntimeError("问不出来")

    rows = _walk_rows()
    out, calls = _replay(tmp_path, rows,
                         {"goto": [{"error": "连不上"}]}, alive=broken)
    assert [c["name"] for c in calls].count("goto") == 3, calls
    assert "窗口" in out["why"], out["why"]


def test_a_tool_failure_with_a_live_window_is_a_step_failure_not_a_restart(tmp_path):
    """反例（同一颗钉子）：`alive()` 说**活着** → 这不是窗口的事，是这一步没做成 → 停。

    两条路必须分得开：一条是「窗口没了，整段重来」，另一条是「这一页上找不到它了，
    停下来叫人」。混在一起的话，一个坏掉的选择器会换来三遍真页面的重走。
    """
    rows = _walk_rows()
    responses = {"observe": _pages_for(rows),
                 "click": [{"error": "没有找到选择器 #cta"}]}
    out, calls = _replay(tmp_path, rows, responses, alive=lambda: True)
    assert out["done"] == 1, out
    assert [c["name"] for c in calls].count("goto") == 1, calls
    assert "窗口又死了" not in out["why"], out["why"]
    assert "开始申请" in out["why"], out["why"]


def test_a_broken_on_step_hook_does_not_break_the_replay(tmp_path):
    """旁路坏掉不许带塌主路（与 `explore` 的 `emit()` 同一条规矩）—— 但要**说出来**。"""
    def boom(_step):
        raise RuntimeError("盘满了")

    rows = _walk_rows()
    out, _ = _replay(tmp_path, rows, {"observe": _pages_for(rows)}, on_step=boom)
    assert out["done"] == 2, out
    assert "旁路" in out["why"], out["why"]


def test_replaying_an_empty_prefix_is_not_an_error(tmp_path):
    """空前缀不是异常：没得重放就如实说「没得重放」（读账的人要能分清这个与「坏了」）。"""
    out, calls = _replay(tmp_path, [], {})
    assert out["done"] == 0 and out["landed"] == "", out
    assert calls == [], calls
    assert out["why"], "空前缀也要有一句人话（沉默与「没得重放」在账上分不开）"


def test_a_look_that_did_not_change_the_page_is_not_a_verification_point(tmp_path):
    """§1.6：**一页一次 observe**，不是每一步都核 —— 看一眼没换页的地方**不核**。

    判据落在**真发出去几次 observe**上：三条 observe 行里只有两条让状态名变了
    （第一条把 start 变成 funnel；中间那条看的是同一页）。
    「每条 observe 都核一遍」的实现会发 3 次 —— 它不贵，但**它不是这条判据**，
    而这条判据的意义是「核验点由账本说了算」。
    """
    rows = [_goto(ENTRY, state="start"),
            _look(ENTRY, TEXT_A, state="start"),       # ← 这一眼把状态带到了 funnel
            _look(ENTRY, TEXT_A, state="funnel"),      # ← 又看了一眼，页面没变
            _click("开始申请", state="funnel"),
            _look(QUOTE, TEXT_B, state="funnel")]
    out, calls = _replay(tmp_path, rows, {"observe": _pages_for(rows)})
    assert [c["name"] for c in calls].count("observe") == 2, calls
    assert out["done"] == 2, out


def test_the_landing_page_is_looked_at_when_the_ledger_never_wrote_it_down(tmp_path):
    """前缀**最后一行是动作**时，也得看一眼它落在哪儿（§1.4.5 第 2 步）。

    这种形状是真会出现的：中间有个做不成的步（R1 把前缀截在那儿），
    于是最后那个好动作的落点**在边界之外** —— 账上没记。
    那一页没有可比的判据（账上没写），所以**只如实看一眼**、不判对错；
    但 `landed` 必须靠这一眼才不是一句过时的话。
    """
    rows = [_goto(ENTRY, state="start"),
            _look(ENTRY, TEXT_A, state="start"),
            _click("开始申请", state="funnel"),
            _click("做不成的那一下", state="funnel", ok=False),
            _look(QUOTE, TEXT_B, state="funnel")]
    prefix, _ = browser_agent.replayable_prefix(rows, SUCCESS, entry_url=ENTRY)
    assert prefix == rows[:3], [r["action"] for r in prefix]
    out, calls = _replay(tmp_path, prefix,
                         {"observe": _pages_for(rows)})
    assert [c["name"] for c in calls] == ["goto", "observe", "click", "observe"], calls
    assert out["done"] == 2 and out["landed"] == QUOTE, out


# ═══════════ Task 6：接续跑（`explore(resume_from=…)`）+ 窗口死是一等停因 ═══════════
#
# 两件事（设计注 §1.7 / §1.8），**全用桩**（桩 MCP + 假模型）——这一轮不许开真窗口：
#
#   ① **续跑**：开头先照账本走回去（0 模型调用），走完才轮到模型接着探；
#      并且把「重放了什么、边界在哪、为什么停在那儿」告诉模型（不给它原始工具返回）。
#   ② **窗口死掉是一个可判的停因**：工具连着失败 2 次 → **问**窗口服务 → 它说死了才是死。
#      ⚠️ **不许**去匹配工具的错误文字（「连不上 host:port」那种）——「猜文本」与
#      「问接口」的可靠性差一个量级。


def _asked(fake):
    """这一趟模型收到的**开场白**（user 那条）。

    ⚠️ 不能取 `messages[-1]`：`run_tool_loop` 把**同一个 list** 一路传下去，
    循环跑完之后它已经被追加到尾部了（`calls[0]["messages"]` 是个活引用）——
    取尾巴拿到的是最后那条 assistant 消息，而不是开场白。
    """
    msgs = fake.calls[0]["messages"]
    return next(m["content"] for m in msgs if m["role"] == "user")


def test_a_resume_walks_the_ledger_back_before_the_model_gets_a_turn(tmp_path):
    """续跑的第一件事是**照账本走回去**（0 模型），走完才轮到模型接着探。

    判据落在**真发出去的工具调用**上（桩的流水线）：重放那几下必须在模型那一下之前，
    而且账上那两步（goto + click）一个都不许少。
    """
    rows = _walk_rows()
    journey, fake, calls = _run(
        tmp_path,
        {"observe": _pages_for(rows)},
        [{"content": "我看到了报价页，接着往下走", "calls": []}],
        resume_from=rows,
    )
    assert [c["name"] for c in calls] == ["goto", "observe", "click", "observe"], calls
    assert [s["origin"] for s in journey.steps] == ["replay"] * 4, journey.steps
    assert journey.replay["done"] == 2, journey.replay
    assert journey.replay["landed"] == QUOTE, journey.replay


def test_the_model_is_told_what_was_replayed_and_why_the_ledger_stopped_there(tmp_path):
    """§1.7：**给**已重放的步（一句话一步）+ 边界那一步为什么没重放；**不给**原始工具返回。

    不给的后果很具体：把账上的原始返回整份塞回上下文，等于把省下来的那些轮数
    又用 token 付了一遍；而且模型会把「重放」当成「我做过」——明说是重放，它才会在
    需要时重新确认（`_SYSTEM` 规矩 3：click 返回 ok 只代表命令下发了）。
    """
    rows = _walk_rows() + [_click("提交申请", state="quote", ok=False),
                           _look("https://site.test/thanks", "谢谢", state="quote")]
    prefix, why = browser_agent.replayable_prefix(rows, SUCCESS, entry_url=ENTRY)
    assert prefix == rows[:4], [r["action"] for r in prefix]      # 前提：前缀就是那前四行
    assert "R1" in why, why

    journey, fake, _ = _run(tmp_path, {"observe": _pages_for(rows)},
                            [{"content": "接着探", "calls": []}],
                            resume_from=prefix, resume_note=why)
    asked = _asked(fake)
    assert "点了「开始申请」" in asked, asked                 # 重放过的步：一句话一步
    assert "接着" in asked or "重放" in asked, asked          # 明说是「接着上一趟」
    assert "R1" in asked and "提交申请" in asked, asked       # 边界 + 为什么
    assert '"selectors"' not in asked, "把账上的原始结构整份塞回上下文了：%s" % asked


def test_without_a_resume_the_opening_message_is_the_same_bytes_as_before(tmp_path):
    """没给前缀 → 发出去的**就是 `_brief()` 的产物本身**（续跑那一段不许漏进这条路）。

    ⚠️ 口径说清楚（修复轮 1 自查）：它钉的是「**续跑机制没有污染自由模式那条路**」——
    不是「`_brief()` 今天长什么样」。后者由 `test_without_a_plan_the_brief_is_byte_for_byte_
    what_it_was_today`（Task 3）拿**硬编码的字节**钉着：两条各管一头，那一条管「文案没漂」，
    这一条管「**没多出东西**」（少了它，一个「无条件追加续跑段」的实现两条都躲得过）。
    """
    _, fake, _ = _run(tmp_path, {"observe": [{"structured": PAGE_LANDING}]},
                      [{"content": "讲完了", "calls": []}])
    asked = _asked(fake)
    assert asked == browser_agent._brief("https://example.test/funnel",
                                         "看看这一页怎么走到报价", browser_agent.Budget())


# ─────────────── 窗口死掉：连着失败两次 → 问接口（不是猜错误文字）───────────────


def _dead_click_turns(n=2):
    """模型连着发 n 次**注定失败**的 click（桩回的是一句错误文字）。"""
    turns = [{"calls": [("click", {"selector": "#ghost"})]} for _ in range(n)]
    turns.append({"content": "这条点不通，我把话说清楚"})
    return turns


def test_two_failed_tool_calls_in_a_row_ask_the_window_and_stop(tmp_path):
    """连着两次失败 → 问窗口 → **它说死了** → `window_gone` 收场（一等停因）。

    为什么要「连着」：一次失败在活窗口上再正常不过（选择器不对、元素还没渲染出来）。
    为什么要问：`alive()` 是**接口**给的答案，工具那句错误文字是**猜**的。
    """
    probes = []

    def alive():
        probes.append(1)
        return False

    journey, _, _ = _run(tmp_path, {"click": [{"error": "连不上 127.0.0.1:9222"}]},
                         _dead_click_turns(2), window_alive=alive)
    assert journey.stop_reason == "window_gone", journey.stop_reason
    assert probes == [1], "连着失败两次之后**问一次**（多问是白花，少问是漏判）：%r" % probes
    assert len(journey.steps) == 2, journey.steps
    assert any("窗口" in n for n in journey.notes), journey.notes


def test_a_window_that_is_still_there_is_not_blamed_for_a_selector(tmp_path):
    """反例（同一条判据的另一半）：窗口**活着** → 同样的两次失败**不算**窗口死。

    这一条同时钉住「判据是问出来的结果」：探针被问了（`probes == [1]`），
    所以实现**不是**在匹配那句错误文字（匹配的话，同样两句错误文字会得到同样的停因）。
    """
    probes = []

    def alive():
        probes.append(1)
        return True

    journey, _, _ = _run(tmp_path, {"click": [{"error": "没有找到选择器 #ghost"}]},
                         _dead_click_turns(2), window_alive=alive)
    assert journey.stop_reason == "model_done", journey.stop_reason
    assert probes == [1], "问还是要问的 —— 判据是**问出来的那个答案**：%r" % probes


def test_one_failure_is_not_enough_to_ask_the_window(tmp_path):
    """**一次**失败不问（失败之间夹着一次成功就重新计数）—— 否则活窗口上照样会误判。"""
    probes = []

    def alive():
        probes.append(1)
        return False

    turns = [{"calls": [("click", {"selector": "#ghost"})]},      # 失败 ①
             {"calls": [("observe", {})]},                        # 成功 → 计数清零
             {"calls": [("click", {"selector": "#ghost"})]},      # 失败 ①（重新数）
             {"content": "讲完了"}]
    journey, _, _ = _run(tmp_path, {"observe": [{"structured": PAGE_LANDING}],
                                    "click": [{"error": "没有找到选择器 #ghost"}]},
                         turns, window_alive=alive)
    assert journey.stop_reason == "model_done", journey.stop_reason
    assert probes == [], "连着失败才问 —— 中间成功过就不算连着：%r" % probes


def test_a_window_probe_that_breaks_does_not_invent_a_stop_reason(tmp_path):
    """探针**自己坏掉** → 当作「不知道」，**不**编一个 `window_gone` 出来。

    ⚠️ 与 `replay` 里的 `_probe_dead` 取向**故意相反**（那儿坏掉按死了算）：
    那边问的是「这次失败该不该归到窗口头上」（判错的代价是多走两遍重放，重放里没有
    不可逆动作）；这边问的是「这个 job 的结局叫什么」（判错就是把「选择器没找到」
    记成「窗口死了」——一句指错方向的话，而重开窗口解决不了它）。
    两边都问不出来时，可判的那个答案不是「死」，是「不知道」。
    """
    def broken():
        raise RuntimeError("窗口服务连不上")

    journey, _, _ = _run(tmp_path, {"click": [{"error": "没有找到选择器 #ghost"}]},
                         _dead_click_turns(2), window_alive=broken)
    assert journey.stop_reason == "model_done", journey.stop_reason


def test_the_replayed_steps_do_not_eat_the_step_budget(tmp_path):
    """重放的步**不花预算**（它不花模型的钱）：预算 1 步时，那 4 行照样先走完。"""
    rows = _walk_rows()
    journey, _, calls = _run(
        tmp_path, {"observe": _pages_for(rows)},
        [{"calls": [("observe", {})]}, {"calls": [("observe", {})]}],
        budget=browser_agent.Budget(max_steps=1, max_rounds=10),
        resume_from=rows)
    origins = [s["origin"] for s in journey.steps]
    assert origins.count("replay") == 4, origins
    assert origins.count("model") == 1, origins
    assert journey.stop_reason == "budget_steps", journey.stop_reason


def test_a_resume_that_cannot_walk_the_ledger_still_lets_the_model_continue(tmp_path):
    """前缀走不通（选择器一条都解析不出来）→ 停在那儿说话，**然后照样往下探**。

    重放不是主路：它是**开头**。走不通要说清（人话在账上），但模型还有一整趟可以走。
    """
    rows = _walk_rows()
    journey, _, calls = _run(
        tmp_path, {"observe": _pages_for(rows), "click": [{"error": "没有找到选择器 #cta"}]},
        [{"content": "重放没走通，我自己看一眼", "calls": []}],
        resume_from=rows)
    assert [c["name"] for c in calls] == ["goto", "observe", "click"], calls
    assert any("重放" in n and "#cta" in n for n in journey.notes), journey.notes
    assert journey.stop_reason == "model_done", journey.stop_reason


# ── 裁定 ③：两次重试之间**换会话**（§1.6 的「整段重来」要真有意义）───────────────
#
# 事实先摆出来：`_WindowGone` 的定义就是「探针说窗口死了」，而手里那个会话绑的**正是
# 那串已经没了的 ws_url** ⇒ 在同一个会话上「整段重来 3 遍」是**结构性无用**的。
# 而新窗口只有 `reopen` 给得了（那是人/服务的一步，`replay` 不该自己去开窗）。


def _two_stubs(tmp_path, dead_responses, live_responses):
    """两个桩 MCP（各有自己的流水线）—— 「换了会话」这件事必须落在**两条流水线**上才看得见。"""
    (tmp_path / "dead").mkdir(parents=True, exist_ok=True)
    (tmp_path / "live").mkdir(parents=True, exist_ok=True)
    dead, dead_log = _stub(tmp_path / "dead", dead_responses)
    live, live_log = _stub(tmp_path / "live", live_responses)
    return dead, dead_log, live, live_log


def test_a_replay_that_can_swap_its_session_gets_a_new_one(tmp_path):
    """给了 `fresh` → 第二遍**在新会话上**跑（判据落在两个桩各自的流水线上）。"""
    rows = _walk_rows()
    dead, dead_log, live, live_log = _two_stubs(
        tmp_path,
        {"goto": [{"error": "连不上 127.0.0.1:9222"}]},
        {"observe": _pages_for(rows)})
    asked = []

    def fresh(old):
        asked.append(old)
        return live

    try:
        out = browser_agent.replay(dead, rows, alive=lambda: False, fresh=fresh)
        calls_dead, calls_live = _calls(dead_log), _calls(live_log)
    finally:
        dead.close()
        live.close()

    assert asked == [dead], "换会话只该在**两次尝试之间**发生一次：%r" % asked
    assert out["done"] == 2, out
    # `replay` 要把「试了几遍」报出来（复审 ②：它是「这一趟干不干净」唯一的输入）——
    # 这里走了两遍（第一遍死在窗口、换了会话之后第二遍走通）
    assert out["attempts"] == 2, out
    assert [c["name"] for c in calls_dead] == ["goto"], calls_dead
    assert [c["name"] for c in calls_live] == ["goto", "observe", "click", "observe"], \
        calls_live


def test_a_session_that_cannot_be_swapped_is_not_fatal(tmp_path):
    """换不到（抛）**不是致命错**：沿用旧会话接着重来（老样子 3 遍），但**要说出来**。

    不说出来的后果很具体：读账的人看到「重来 3 遍都没走完」，会以为是窗口的问题 ——
    其实是**没人能给它一个新会话**（那两件事的下一步完全不同）。
    """
    def broken(_old):
        raise RuntimeError("起不了新会话")

    rows = _walk_rows()
    out, calls = _replay(tmp_path, rows,
                         {"goto": [{"error": "连不上 127.0.0.1:9222"}]},
                         alive=lambda: False, fresh=broken)
    assert [c["name"] for c in calls].count("goto") == 3, calls
    assert "换会话" in out["why"] and "起不了新会话" in out["why"], out["why"]


def test_a_resume_whose_window_died_stops_before_asking_the_model(tmp_path):
    """重放中途窗口没了 → **就地停**（`window_gone`），**不去问模型那一轮**。

    模型看到的是一个死窗口；再问它一轮，工具接着失败两次之后我们还是停在同一处 ——
    那一轮是白花的（而这是个真窗口 + 真模型的钱）。
    判据：`fake.calls == []`（**一次模型调用都没发生**）。
    """
    rows = _walk_rows()
    journey, fake, calls = _run(tmp_path, {"goto": [{"error": "连不上 127.0.0.1:9222"}]},
                                [{"content": "接着探", "calls": []}],
                                resume_from=rows, window_alive=lambda: False)
    assert journey.stop_reason == "window_gone", journey.stop_reason
    assert fake.calls == [], "窗口都死了还去问模型：%d 轮" % len(fake.calls)
    assert [c["name"] for c in calls] == ["goto"] * 3, calls


def test_without_a_window_probe_two_failures_do_not_invent_a_stop(tmp_path):
    """没接「窗口还活着吗」那根线 → **不知道**（不是「死了」）：两次失败只是两次失败。

    不接那根线时**不许**编一个停因出来：「没接线」被读成「窗口死了」，
    会让人去重开一个本来好好的窗口（G5 的负例）。
    """
    journey, _, _ = _run(tmp_path, {"click": [{"error": "没有找到选择器 #ghost"}]},
                         _dead_click_turns(2))          # window_alive 不给 = None
    assert journey.stop_reason == "model_done", journey.stop_reason


def test_a_goto_with_no_address_on_the_ledger_is_not_replayed():
    """R4 的一支保守行为（R-E7 ②）：账上**没记下「要去哪」** → **不重放这一行**。

    `_goto_url` **不许**回退到 `result.url` —— 那是从**侧门**把同一个谎放回来：
    `target.url` 一空就静默返回落地地址，判据看着在读「要去的地址」、读的却是别的。
    宁可停在「没记下要去的是哪」，也不拿一个**别处的地址**去导航。
    """
    rows = [_row("goto", "start", target={"url": ""}, result={"url": ENTRY},
                 note="打开了某个地址"),
            _look(ENTRY, TEXT_A, state="start"),
            _click("开始申请", state="funnel"),
            _look(QUOTE, TEXT_B, state="funnel")]
    prefix, why = browser_agent.replayable_prefix(rows, SUCCESS, entry_url=ENTRY)
    assert prefix == [], f"账上没记下要去哪，却还有 {len(prefix)} 行进前缀"
    assert "没记下要去的是哪" in why, why


def test_two_pages_behind_the_same_gate_become_one_state():
    """★ 2026-09-22（生成侧建议第 1 条，真产物里量到的）：**相邻两页 `when` 相同 ⇒ 合并成一个状态**。

    为什么必须合并：`when` 是产物重放时**唯一的门** ⇒ 两个状态同门，就会演出
    「同一页上填做了、点却被跳过」—— 真产物 `gowizard-14/-15` 就是这么**半执行**的 ✗；
    合并之后那两步**同生共死**。

    判据就是建议里那句话：**产物里不许出现两条相邻且 `when` 相等的状态**。
    ⚠️ 造夹具的关键：两页要**同 `when`、不同 key**（`key` 只看去掉 `#` 的 url + title + 正文前 400 字，
    而 `when` 的地址用的是**去 query/fragment 的稳定前缀**）⇒ 用**只有 query 不同**的两页最干净。
    ⚠️ 另外：`when` 为空的两个页面**不算相等** —— 拿它们合并会把两页不相干的步揉进一组。
    """
    pages = browser_agent._Pages(site_url="https://x.test/", journey=None)
    #: ⚠️ 第一次的返回值是「**上一页**的名字」—— 这里还没有上一页 ⇒ 它是 `None`（不是失败）
    assert pages.note_page({"url": "https://x.test/a?step=1", "title": "A 页",
                            "page_text": "第一页的正文，够长就行"}) is None
    first_name = pages.current_name
    #: 第二页：**同 when、不同 key**（只有 query 不同）⇒ 名字复用 ⇒ 两页的步落进**同一组**
    again = pages.note_page({"url": "https://x.test/a?step=2", "title": "A 页",
                             "page_text": "第一页的正文，够长就行"})
    assert again == first_name, "同一个 when 的两页该合成一个状态：%r vs %r" % (again, first_name)
    #: 真正的保证在这里：`states()` 按**名字**分组 ⇒ 两页的步落在**同一个状态**里
    book = browser_agent.Journey()
    book.pages = list(pages.pages)
    #: ⚠️ 夹具要用**可重放的**动作（`click`/`form`/`scroll`/`goto`）—— `observe` 不进产物，
    #: 拿它当步，`states()` 会把它滤掉、这条钉子就量的是空气。
    book.steps = [
        {"state": first_name, "action": "click", "target": {"selectors": ["#fill"]},
         "result": {"ok": True}, "note": "先填"},
        {"state": again, "action": "click", "target": {"selectors": ["#go"]},
         "result": {"ok": True}, "note": "再点"},
    ]
    got = book.states()
    assert len(got) == 1 and len(got[0]["steps"]) == 2, got
    #: 正控：把 when 拉开 ⇒ 必须是**两个**状态（别把这条钉子做成恒真）。
    #: ⚠️ 杠杆要用**换路径** —— `_when_for` 不看 title（它看 url 稳定前缀 + 正文原文），
    #: 我第一次拿 title 当杠杆，结果 when 没变、页面照样合并 ⇒ 那条正控是空转的。
    pages.note_page({"url": "https://x.test/b", "title": "B 页",
                     "page_text": "另一页的正文，够长就行"})
    assert pages.current_name != first_name, "换了一页却还复用同一个状态名：%s" % pages.current_name


def test_no_two_adjacent_states_share_the_same_gate():
    """★ 建议第 1 条那句断言，钉在**产物那一侧**：`states()` 里不许有相邻且 `when` 相等的状态。"""
    pages = browser_agent._Pages(site_url="https://x.test/", journey=None)
    for i in (1, 2, 3):
        pages.note_page({"url": "https://x.test/f?q=%d" % i, "title": "同一页",
                         "page_text": "同一页的正文，够长就行"})
    pages.note_page({"url": "https://x.test/g", "title": "另一页",
                     "page_text": "另一页的正文，够长就行"})
    #: 判据取**产物那一侧**：名字 → when 的映射（`states()` 就是按名字分组的）
    whens = {}
    for p in pages.pages:
        whens[p["name"]] = p.get("when")
    got = [n for n, w in whens.items() if w]
    assert len(got) == 2, "同一页那 3 次该合成一个状态（只剩两组）：%s" % list(whens)
    pairs = [(a, b) for a, b in zip(got, got[1:]) if whens[a] == whens[b]]
    assert pairs == [], "产物里出现了相邻且 when 相等的状态：%s" % pairs
    """★ 2026-09-22 真事（用户原话：「第一次生日填过了，为啥还会消掉填第二次。这个比较关键」）。

    探索期模型为了试出掩码/校验的脾气，把同一格**反复填**（真账本里 `#InputDOB` 出现了
    4 次 `form` + 1 次 `click` + 1 次 `scroll`）；这些步在账上**每一步都是「做成了」**
    （回执说下发成功、页面也变了 ⇒ 掩码确实动了）⇒ `_replay_step` 那道 `ok` 筛**筛不掉** ✗。
    而重放时前面那几次毫无意义，还会把后填的值**覆盖掉** —— 用户看到的正是这个。

    判据四条：同一格只留**最后一次** ✓ / 别的字段与别的动作**一步不删** ✓ / **顺序不动** ✓ /
    ⚠️ **跨状态不去重**（流程里第二次问同一格是另一件事）。
    """
    def _fill(name, value, state="form"):
        return {"state": state, "action": "form",
                "target": {"selectors": ["#%s" % name]},
                "result": {"ok": True, "fill": {"name": name, "label": name,
                                                "value": value, "kind": "value",
                                                "source": name, "fallback": []}},
                "note": "填好了「%s」" % name}

    book = browser_agent.Journey(steps=[
        _fill("dob", "1990-01-15"),                       # 第一次（ISO ✗，掩码会吃掉）
        {"state": "form", "action": "click", "target": {"selectors": ["#ok"]},
         "result": {"ok": True}, "note": "点了「继续」"},
        _fill("dob", "15051990"),                         # 试第二次
        _fill("name", "Dana"),
        _fill("dob", "15/05/1990"),                       # 试出来的那一次 ⇒ 留它
    ])
    got = [s for st in book.states() if st["name"] == "form" for s in st["steps"]]
    assert [s["action"] for s in got] == ["click", "form", "form"], got
    assert [s.get("fill") for s in got if s["action"] == "form"] == ["name", "dob"], got
    assert len(got) == 3, "别的动作被删了：%s" % got

    #: ⚠️ 跨状态**不去重**
    book2 = browser_agent.Journey(steps=[_fill("dob", "1"), _fill("dob", "2", state="again")])
    both = [s for st in book2.states() for s in st["steps"]]
    assert len([s for s in both if s["action"] == "form"]) == 2, both


def test_the_human_words_reach_the_explorer_and_nobody_elses_bytes_move(tmp_path) -> None:
    """★ 2026-09-22 真事：运营把步骤写得**很细**，新站那条路上模型**一个字都没收到**。

    为什么：那条路的稿是「账本 → `template.render`」**算**出来的，模型在那条路上**只有探路
    这一处有判断力**；而人的话原来只喂给「修站出补丁」那条路（`fix.patch_user`）——
    屏幕上于是成了「它完全没按我的来」。
    这条钉三件：① 说话时那段**追加**进开场白、并且是**命令式**（规格，不是背景资料）；
    ② **没人说话时逐字节与从前相同**（B4 那颗钉子就在同一支里，别撞它）；
    ③ `explore` 真把 `hints` 递给了 `_brief`（只测纯函数的话，「接上没接上」照旧可能没接 ✗）。
    """
    class _B:
        max_steps, max_rounds = 12, 3

    plain = browser_agent._brief("https://x.test/", "走通", _B())
    said = browser_agent._brief("https://x.test/", "走通", _B(),
                                hints=["点 #a ｜ 等 2-5 秒 ｜ 出现 #b"])
    assert said.startswith(plain), "追加的东西不许动原来那一段"
    extra = said[len(plain):]
    assert "#a" in extra and "这就是规格" in extra and "照它来" in extra, extra
    #: 空白/空串**不算话**（「全是空格」不该冒出一段空标题）
    assert browser_agent._brief("https://x.test/", "走通", _B(), hints=["", "   "]) == plain

    #: ③ 接线：`explore` 把 hints 递下去了没有 —— 用这一支现成的桩跑法（桩 MCP + 假模型）。
    #: ⚠️ 只量纯函数的话，「接上没接上」照旧可能没接（第一版就是这么写的，一跑就红 ✗）。
    seen = []
    real = browser_agent._brief

    def spy_brief(url, goal, budget, plan=None, hints=None):
        seen.append(list(hints or []))
        return real(url, goal, budget, plan, hints=hints)

    browser_agent._brief = spy_brief
    try:
        #: 桩跑法：桩 MCP + 假模型；模型**不说话也不调工具**（一回合就收）⇒ 只为走到拼开场白那儿。
        _run(tmp_path, {"observe": [{"structured": PAGE_LANDING}]},
             [{"content": "看完了，没有要做的。"}], hints=["人的话"])
    finally:
        browser_agent._brief = real
    assert seen == [["人的话"]], seen


def test_the_ledger_keeps_the_address_we_asked_for(tmp_path):
    """**R-E7 ①的哨兵**：账上 `target.url` 记的是**要去**的那串，落地地址在 `result.url`。

    为什么这条必须单独钉住：`dispatch` 里原先有一行把 `target` **整个盖成落地地址** ——
    于是 R4 拿到的「要去的地址」其实是落地地址，而落地地址那条 observe **正是 R2 已经
    要求必须存在的那一条** ⇒ **R4 恒真**（设计注承诺的「一次性深链不会重放」实际没人执行，
    拦住它的是「账本把请求地址丢了」这个副作用 —— 运气对，不是判据对）。
    那一行在老代码里**一条变异都红不到**（变异只改本轮新写的那 472 行）；这一条用例
    是唯一看得见它的地方。
    """
    journey, _, _ = _run(
        tmp_path,
        {"goto": [{"structured": {"url": "https://example.test/funnel"}}]},   # 落地 ≠ 要去
        [{"calls": [("goto", {"url": "https://example.test/start"})]},
         {"content": "走完了"}],
    )
    step = journey.steps[0]
    assert step["action"] == "goto", step
    assert step["target"]["url"] == "https://example.test/start", \
        "账上「要去的地址」被落地地址盖掉了 —— R4 会因此恒真"
    assert (step["result"] or {}).get("url") == "https://example.test/funnel", step


def test_a_look_that_fails_stops_the_replay_at_the_checkpoint(tmp_path):
    """① 核验点那一眼**工具报错**（窗口还活着）→ **返回 `{done, landed, why}`**，不抛。

    修之前的形状（复审实测复现）：`_ToolFailed` 从 `replay` **穿出去** —— 违反返回值契约，
    而且这一步之前**已经重放掉的步**随异常一起丢，读账的人连「走到哪儿了」都看不到。
    为什么当时没有用例打红它：失败桩**全打在 `click`/`goto` 上**，没有一条 observe 报错的桩。
    """
    rows = _walk_rows()
    out, calls = _replay(tmp_path, rows,
                         {"observe": [{"error": "observe 超时：没等到页面稳定"}]},
                         alive=lambda: True)
    assert isinstance(out, dict), out
    assert out["done"] == 1, out              # goto 走成了；核验点那一眼没看成
    assert "超时" in out["why"] and "第 2 行" in out["why"], out["why"]
    assert [c["name"] for c in calls] == ["goto", "observe"], calls


def test_a_failed_last_look_still_returns_a_result_not_an_exception(tmp_path):
    """① 末尾那一瞥**工具报错** → 同样**返回结果**（负例：另一处 observe 调用点）。

    这一处与上一处是**两个**调用点（复审点名两条都没接）—— 一条用例只钉得住一条。
    """
    rows = [_goto(ENTRY, state="start"),
            _look(ENTRY, TEXT_A, state="start"),
            _click("开始申请", state="funnel"),
            _click("做不成的那一下", state="funnel", ok=False),
            _look(QUOTE, TEXT_B, state="funnel")]
    prefix, _ = browser_agent.replayable_prefix(rows, SUCCESS, entry_url=ENTRY)
    out, calls = _replay(tmp_path, prefix,
                         {"observe": [{"structured": _live_page(ENTRY, TEXT_A)},
                                      {"error": "observe 超时：没等到页面稳定"}]},
                         alive=lambda: True)
    assert isinstance(out, dict), out
    assert out["done"] == 2, out              # 两个动作都走成了，只是末尾那一眼没看成
    assert "超时" in out["why"] and "第 3 行" in out["why"], out["why"]
    assert [c["name"] for c in calls] == ["goto", "observe", "click", "observe"], calls


# ── 修复轮 1（复审 Q1）：会话所有权 / `None` 的取向 ────────────────────


def test_a_swapped_session_is_the_one_the_model_loop_uses(tmp_path, monkeypatch):
    """**`own_session=True` 那条路**（复审 Q1：这条路原先**一条用例都没有**）。

    会话是 `explore` 自己起的 ⇒ 换会话之后**它自己手里那个也必须跟着换**。
    只换 `replay` 内部那个名字的话，工具循环会拿着**已经关掉**的会话跑完整趟 ——
    每一步都是 `McpError: cdp-mcp 已经不在了`，一趟真探路当场变成废账
    （白烧一个真窗口 + 一整趟模型钱，账上留下满篇假的「工具失败」）。

    三件事一起验：①**模型那一步落在新会话上**（判据是两条流水线，不是内部变量）；
    ②旧会话**关了**；③新会话**也关了**（不漏 `cdp-mcp` 子进程）。
    """
    rows = _walk_rows()
    for sub in ("dead", "live"):
        (tmp_path / sub).mkdir(exist_ok=True)
    dead, dead_log = _stub(tmp_path / "dead",
                           {"goto": [{"error": "连不上 127.0.0.1:9222"}]})
    live, live_log = _stub(tmp_path / "live", {"observe": _pages_for(rows)})
    opened = [dead, live]
    monkeypatch.setattr(browser_agent.tools.McpSession, "open",
                        staticmethod(lambda **kw: opened.pop(0)))

    #: 探针**抖一下**：重放那一下说死了，之后都说活着（复审描述的正是这个形状）。
    answers = [False]

    def alive():
        return answers.pop(0) if answers else True

    fake = FakeLLM([{"calls": [("observe", {})]}, {"content": "讲完了"}])
    journey = browser_agent.explore(
        "https://example.test/funnel", "看看这一页怎么走到报价",
        session=None, ws_url="ws://127.0.0.1:9222/devtools/page/ABC",
        client=fake, resume_from=rows, window_alive=alive)

    assert [c["name"] for c in _calls(dead_log)] == ["goto"], _calls(dead_log)
    assert [c["name"] for c in _calls(live_log)] == ["goto", "observe", "click", "observe",
                                                     "observe"], _calls(live_log)
    assert journey.replay["done"] == 2, journey.replay
    assert journey.stop_reason == "model_done", journey.stop_reason
    assert dead._proc is not None and dead._proc.poll() is not None, \
        "旧会话没关（换了之后它就该退休）"
    assert live._proc is not None and live._proc.poll() is not None, \
        "换来的那个会话没人关 —— 漏了一个 cdp-mcp 子进程"


def test_a_window_probe_that_does_not_know_is_not_treated_as_dead(tmp_path):
    """探针答「**不知道**」（`None`）≠ 死 —— 两侧（重放 / 模型那侧）取向必须一致（Q1 ③）。

    一个当死、一个当活的最坏组合是真会发生的：重放这边**主动关掉一个还活着的会话**，
    而模型那边不认「窗口没了」⇒ 整趟在一堆假的「工具失败」里烧到预算类停因，
    白烧一个真窗口 + 一整趟模型钱。
    """
    journey, _, _ = _run(tmp_path, {"click": [{"error": "没有找到选择器 #ghost"}]},
                         _dead_click_turns(2), window_alive=lambda: None)
    assert journey.stop_reason == "model_done", journey.stop_reason


def test_a_replay_whose_probe_does_not_know_stops_instead_of_restarting(tmp_path):
    """同一颗钉子的重放那一侧：探针答「不知道」→ **不整段重来**（那会关掉一个活会话）。"""
    rows = _walk_rows()
    out, calls = _replay(tmp_path, rows, {"goto": [{"error": "连不上 127.0.0.1:9222"}]},
                         alive=lambda: None)
    assert [c["name"] for c in calls] == ["goto"], calls
    assert "停住" in out["why"], out["why"]


def test_a_session_the_caller_gave_is_neither_swapped_nor_closed(tmp_path, monkeypatch):
    """**别人给的会话**：`explore` 既不换它、也不关它（所有权不是它的）。

    换的代价：会凭空起一个会话 —— 而它只能按**默认**去连，也就是**别的**窗口
    （`_explore_for` 那条注释早就立过这条规矩）。关的代价：调用方手里那个当场报废
    （测试里它还要接着用，生产里那是上层的东西）。
    """
    opened = []

    def boom(**kw):
        opened.append(kw)
        raise RuntimeError("不该去换会话")

    monkeypatch.setattr(browser_agent.tools.McpSession, "open", staticmethod(boom))
    rows = _walk_rows()
    session, log = _stub(tmp_path, {"goto": [{"error": "连不上 127.0.0.1:9222"}]})
    fake = FakeLLM([{"content": "接着探"}])
    try:
        journey = browser_agent.explore(
            "https://example.test/funnel", "看看这一页怎么走到报价",
            session=session, ws_url="ws://127.0.0.1:9222/devtools/page/ABC",
            client=fake, resume_from=rows, window_alive=lambda: False)
        assert opened == [], "别人给的会话也去换了一个新的：%r" % opened
        assert [c["name"] for c in _calls(log)] == ["goto"] * 3, _calls(log)
        assert session._proc is not None and session._proc.poll() is None, \
            "调用方给的会话被 explore 关了（它不是它的）"
        assert journey.stop_reason == "window_gone", journey.stop_reason
    finally:
        session.close()


def test_a_zero_budget_never_even_asks_the_model(tmp_path):
    """预算被夹到 0（job 级累计已经花超）→ **一步都不走**，如实以 `budget_steps` 收场。

    这是「预算不许重置」那条链的最后一环：图算出 0 之后，探路这一侧得**真的**一步不动
    （而不是「反正第一轮先问一下模型再说」）。
    """
    journey, fake, calls = _run(tmp_path, {"observe": [{"structured": PAGE_LANDING}]},
                                [{"calls": [("observe", {})]}],
                                budget=browser_agent.Budget(max_steps=0, max_rounds=0))
    assert journey.stop_reason == "budget_steps", journey.stop_reason
    assert calls == [], calls
    assert fake.calls == [], fake.calls
    assert journey.steps == [], journey.steps


def test_a_success_text_that_rendered_late_is_still_stopped():
    """**R-E9 的判据**：那句话**在第二眼才渲染出来**时，**那一步（提交）不许进前缀**。

    形状：点了到新页 → 第一眼那一页上**还没有**那句话 → 又看一眼，它出来了 → 再确认一眼。
    （最后那一眼是**故意**加的：它让「这一步的窗口吃到哪儿为止」这件事**可判别** ——
    只吃第一眼的实现会把提交放行，而窗口吃到「下一次动作之前」的实现把它拦下。）

    裁定用的性质：**只要某次动作之后的任何一眼（在下一次动作之前）看见了成功文案，
    那一步就不许进前缀。** 为什么这条是承重的：那一步常常就是**提交** ——
    放行它 = 重放时**往真实站点再交一次真实表单**（用户原话：「刷太多不太好」）。

    ⚠️ 人话**不许**说成「不是哪一步把它带过来的」（修复轮 2 那条错的措辞）：
    这一形里**正是**某一步把它带过来的，只是那句话当时还没渲染 ——
    它的窗口现在盖住了那一眼，所以人话走的是「**落到的那一页**上已经出现」那一支（准确）。
    """
    rows = [_goto(ENTRY, state="start"),
            _look(ENTRY, TEXT_A, state="start"),
            _click("提交申请", state="funnel"),
            _look(QUOTE, TEXT_B, state="funnel"),                    # 新页，那句话还没渲染出来
            _look(QUOTE, f"{TEXT_B} {SUCCESS}", state="quote"),       # 第二眼：它出来了
            _look(QUOTE, f"{TEXT_B} {SUCCESS} 再确认一眼", state="quote")]
    prefix, why = browser_agent.replayable_prefix(rows, SUCCESS, entry_url=ENTRY)
    assert prefix == rows[:2], \
        f"那次「提交」留在前缀里了（剩 {len(prefix)} 行）—— 重放它会再交一次真实表单"
    assert "提交申请" in why, why                 # 人话要点名拦下的是**哪一步**
    assert "落到的那一页" in why and "撞" not in why, why
    assert "不是哪一步把它带过来" not in why, why


def test_an_action_with_no_look_before_the_next_one_keeps_the_old_reading():
    """**R-E9 的「不越界」那一半**：这一步之后**一眼都没有**（下一个就是动作）时，
    照旧按「后面第一条 observe」算 —— **原来拦下的不许因为这次改动被放行**。

    形状：连着两个动作，成功文案在第二个动作之后才被看见。第一个动作**归不出**任何一眼
    （它的窗口里没有观察），于是退回旧口径（往后找第一条 `observe`，哪怕它跨过了第二个动作）
    → 它照样不许进前缀。没有这一条，「窗口吃到下一次动作之前」很容易被顺手实现成
    「窗口就是那一段」——而那会把这一类**从原来拦着的变成放行**。
    """
    rows = [_goto(ENTRY, state="start"),
            _look(ENTRY, TEXT_A, state="start"),
            _click("第一下", state="funnel"),
            _click("第二下", state="funnel"),
            _look(QUOTE, f"已经收到你的申请。{SUCCESS}", state="funnel")]
    prefix, why = browser_agent.replayable_prefix(rows, SUCCESS, entry_url=ENTRY)
    assert prefix == rows[:2], f"第一下被放行了：{[r['action'] for r in prefix]}"
    assert "落到的那一页" in why, why


def test_a_non_observe_row_carrying_the_success_text_trips_the_premise():
    """**R3 的前提哨兵**：正文（`page_text_head`）只有 `observe` 行写 —— 破了要**当场响**。

    为什么这条不是洁癖（复审原话：**这条前提一破，性质本身就破**）：R3 的两端一边是
    「**最早**看见成功文案的那一行」（逐行累加的 blob），另一边是「这一步的**落点**」——
    而落点**只认 `observe`**。两者能对上，靠的正是「带正文的行 == 落点认的那些行」。
    这条一破，「窗口里看见过」会漏判 ⇒ **提交留在前缀里** ⇒ 重放它 = 往真实站点
    再交一次真实表单（R3 存在的唯一理由就是防这个）。

    形状：把成功文案挂在一条 **`diff`** 行上 —— 它是「看一眼」（R2 认它），但**不是** `observe`
    （R3 的落点不认它）。这正是「哪天有别的工具也写这一键」那一形
    （`_summarize` 的兜底那支 `out.update(raw)` 不设防，一条新工具的结果里带上它就成）。

    判据两半：① **抛**（不是照旧返回一个可能重复提交的前缀）；② 那句话**点出前提与后果**，
    让读账的人知道该去改哪儿 —— 一条「不知道哪儿错了」的异常等于没响。
    """
    rows = [_goto(ENTRY, state="start"),
            _look(ENTRY, TEXT_A, state="start"),
            _click("提交申请", state="funnel"),
            _row("diff", "quote", result={"actionable": True,
                                          "page_text_head": f"{TEXT_B} {SUCCESS}"})]
    # 正对照（先证明**这个形状本身**是危险的）：把它当成 observe，前缀就停在它前面 ——
    # 也就是说，这一行只要被落点认，提交就进不去前缀。差别全在「落点认不认这一行」上。
    same_shape = list(rows[:3]) + [_look(QUOTE, f"{TEXT_B} {SUCCESS}", state="quote")]
    prefix, _why = browser_agent.replayable_prefix(same_shape, SUCCESS, entry_url=ENTRY)
    assert prefix == same_shape[:2], f"正对照就不成立：{[r['action'] for r in prefix]}"

    with pytest.raises(browser_agent._TextPremiseBroken) as exc:
        browser_agent.replayable_prefix(rows, SUCCESS, entry_url=ENTRY)
    msg = str(exc.value)
    assert "第 4 行" in msg and "diff" in msg, msg
    assert "前提" in msg and "真实表单" in msg, msg


def test_a_non_observe_tool_writing_the_page_text_key_trips_the_premise_at_the_source():
    """**同一条前提的写入侧哨兵**：`_summarize` 里这一键**只有 `observe` 那一支**写。

    读取侧那一条见 `test_a_non_observe_row_carrying_the_success_text_trips_the_premise`。
    两侧都要，管的不是一件事：
    - **写入侧**管**前提的产地** —— 哪天的工具结果里带上这一键（兜底那支 `out.update(raw)`
      不设防，一条新工具带上它就成），当场响，**并且报得出是哪个工具**；
    - **读取侧**管**性质的使用地** —— 账本从别的路进来（旧文件、手改、别处导入）时照样拦得住。

    判据是**键在不在**，不是值真不真：一串**空正文**也说明「有别的工具在写它」——
    R3 的整套推导（「窗口里任何一眼看见 ⇔ 最后一眼看见」）是从「只有 observe 写」来的，
    与那一串是不是空的无关。

    ⚠️ **哪几条路真能把这一键带进来**（实测，不是推的）：`goto` / `diff` / `screenshot`
    各有自己的分支、**只挑自己那几个键**，raw 里多出来的一律不进 `out`；真正的口子是
    **兜底那一支**（`out.update(raw)`）—— `click` / `form` / `scroll` 都走它，
    **以及任何一条新工具**（名字不在上面那几个分支里 ⇒ 也走兜底）。
    所以下面两条各自钉一条路。
    """
    # ① **新工具**（名字不在那几个分支里 ⇒ 走兜底）：这一形就是「哪天有别的工具也写它」。
    with pytest.raises(browser_agent._TextPremiseBroken) as exc:
        browser_agent._summarize("read", {}, {"page_text_head": f"… {SUCCESS} …"}, 1, None)
    assert "read" in str(exc.value), str(exc.value)          # 报得出是哪个工具
    # ② 已有的兜底那几条路：**键在、值是空的**照样算破（判的是键在不在）。
    with pytest.raises(browser_agent._TextPremiseBroken):
        browser_agent._summarize("click", {}, {"x": 1, "page_text_head": ""}, 1, None)

    # 正对照两半：①同一串正文走 `observe` 那一支 —— 它**本来就该**由这一支写（不抛）；
    # ②`diff` 的分支**只挑自己那几个键** —— raw 里塞进来的这一键进不去（不是口子）。
    got = browser_agent._summarize("observe", {}, {"page_text": SUCCESS}, 1, None)
    assert got["page_text_head"] == SUCCESS, got
    dropped = browser_agent._summarize("diff", {}, {"actionable": True,
                                                    "page_text_head": SUCCESS}, 1, None)
    assert "page_text_head" not in dropped, dropped


def test_the_product_replays_a_goto_to_the_address_we_asked_for(tmp_path):
    """**R-E8 的哨兵**：产物侧重放 goto 发的是**请求地址**（不是落地地址）。

    为什么这条非有不可：复审实测过 —— 把 `_replay_step` 的优先序**翻过来**
    （改成 `result.url first`），**整套用例全绿**。也就是说那条裁定
    （产物侧重放「当初要开的那串」、不翻）在测试里**根本不可见**：
    谁哪天翻过来，一条都不会响。**裁定的内容必须有哨兵跟着落地。**

    新账本为什么该拿请求地址：落地地址常常带着**会话参数**（`?ref=`、`?sess=`），
    而重放跑在一个**干净身份**的浏览器上（§1.5）—— 那串地址在那边**不可复现**。
    """
    journey, _, _ = _run(
        tmp_path,
        {"goto": [{"structured": {"url": "https://example.test/funnel"}}],   # 落地
         "observe": [{"structured": PAGE_LANDING}],
         "click": [{"structured": {"ok": True}}]},
        [{"calls": [("goto", {"url": "https://example.test/go?src=hero"})]},  # 要去
         {"calls": [("observe", {})]},
         {"calls": [("click", {"selector": "#get-started"})]},
         {"content": "走完了"}],
    )
    assert journey.steps[0]["target"]["url"] == "https://example.test/go?src=hero"
    assert (journey.steps[0]["result"] or {}).get("url") == "https://example.test/funnel"

    src = template.render("example-funnel", ["Thank you"], journey.states(),
                          journey.fills(), provenance=None)
    steps = [s for state in _literal(src, "STATES") for s in state["steps"]]
    gotos = [s for s in steps if s["action"] == "goto"]
    assert gotos, f"goto 没进重放规格：{[s['action'] for s in steps]}"
    assert gotos[0]["url"] == "https://example.test/go?src=hero", \
        "产物重放 goto 发的是落地地址 —— 那串常常带会话参数，在干净身份的浏览器上不可复现"


# ─────────────── 诊断进账本：哪一帧、因为什么（2026-09-18 homebuddy 真站）───────────────

#: homebuddy 真站上**原样**观测到的那一条（`/tmp/hb_observe_page1.json`，2026-09-16 探针）。
#: 两半都在：**哪一帧**（`frame_path` 里那个 frameId）+ **因为什么**（`detail` 那句话）。
#: ⚠️ 这两半正是 `_summarize` 原先丢掉的东西 —— 丢完之后账上只剩 `["frame-blind"]`，
#: 而 `frame-blind` 有**两个分支**（iframe 计数求值失败 / 数量对不上），
#: 光看 kind **分不出是哪一种**，于是「它为什么瞎」这件事事后**根本查不出来**。
REAL_FRAME_BLIND = {
    "kind": "frame-blind",
    "detail": "这一帧的 DOM 里有 1 个 iframe 元素，帧树只枚举到 0 个子帧 —— "
              "帧枚举可能不完整（DOM 穿透失败、或子帧在跨源帧里面时，子帧会整个消失）",
    "frame_path": ["main", "60AFD8844FA4656F93333C1DBBBB92D3"],
}

#: 子帧观测失败那条的**同形**夹具（不是真站原样 —— 真站那份原始返回没落盘，见下）：
#: `Detail` 放的是**真实报错**，Go 侧是 `firstRunes(err.Error(), 160)`
#: （`tools/cdp/internal/observe_frames.go`）—— 所以这一格**上限 160**。
#: ⚠️ 它刻意长过 80：`cmd/observe.go:215` 写着「截到 80 会把 target-ambiguous 那条里的
#: target ID / URL 掐掉」，而**账本这一侧再截一刀**这件事，短夹具瞒得住、这一条瞒不住。
#: （真站上那两条 `frame-error` 的 `Detail` 在账本里已经丢了 —— 丢的正是这一格。）
FRAME_ERROR_160 = {
    "kind": "frame-error",
    "detail": "observe 子帧失败: Runtime.evaluate: Execution context was destroyed, most "
              "likely because of a navigation.（帧 3F1B2C4D5E6A7B8C9D0E1F2A3B4C5D6E 在枚举"
              "之后被销毁 —— 广告/追踪帧常见）",
    "frame_path": ["main", "60AFD8844FA4656F93333C1DBBBB92D3"],
}


def _page_with_diagnostics(*diags):
    """一份带诊断的页面模型 —— `PAGE_LANDING` 一个字段都不动，只挂上 `diagnostics`。"""
    page = dict(PAGE_LANDING)
    page["diagnostics"] = [dict(d) for d in diags]
    return page


def test_a_diagnostic_keeps_which_frame_and_why_when_it_lands_in_the_ledger(tmp_path):
    """**这条钉的性质**：一条带 `detail` 的诊断，**进了账本之后 `detail` 还在**。

    为什么这是承重的（工具层自己写着）：`cmd/observe.go:215` 的注释是
    「⚠️ `detail` 截到 160 而不是别处的 40/80：**诊断的详情就是这一行的全部价值**」——
    工具说「这半句是全部价值」，而消费者（`_summarize`）把它整个扔了。
    实测后果：2026-09-18 那趟 homebuddy 探路的账本里只剩
    `["frame-blind", "frame-error", "frame-error"]` 一串光秃秃的 kind，
    **没有理由、没有帧**，事后查不出它为什么瞎 —— 而那正是当时最要紧的问题。

    ⚠️ 走的是**真那一趟**（`_run` = 桩 MCP + 假模型 + `explore`），不是直接调 `_summarize`：
    要钉的是「**账本里**那一行长什么样」，而账本是 `dispatch` 写进 `Journey.steps` 的。
    """
    # 夹具得**留在射程里**：真报错可以长到 160（Go 侧的上限），比 80 长才对得上
    # 「账本这一侧再截一刀」那条 —— 夹具哪天被改短，这道钉会**静默**失效。
    assert len(FRAME_ERROR_160["detail"]) > 80, len(FRAME_ERROR_160["detail"])
    journey, _, _ = _run(
        tmp_path,
        {"observe": [{"structured": _page_with_diagnostics(REAL_FRAME_BLIND,
                                                           FRAME_ERROR_160)}]},
        [{"calls": [("observe", {})]}, {"content": "看完了"}],
    )
    step = journey.steps[0]
    assert step["action"] == "observe", step
    diags = (step["result"] or {}).get("diagnostics")
    assert isinstance(diags, list) and len(diags) == 2, f"诊断没进账本：{diags!r}"
    # ① kind 仍在（原先就有的那半，别修着修着丢了）
    assert [d["kind"] for d in diags] == ["frame-blind", "frame-error"], diags
    # ② **因为什么**（`detail` 就是那一行的全部价值，**原样**进账 —— 工具已经截过了）
    assert diags[0]["detail"] == REAL_FRAME_BLIND["detail"], diags[0]
    assert diags[1]["detail"] == FRAME_ERROR_160["detail"], diags[1]
    # ③ **哪一帧**（`frame-blind` 的两个分支靠 detail 分、帧靠这里分）
    assert diags[0]["frame_path"] == ["main", "60AFD8844FA4656F93333C1DBBBB92D3"], diags[0]
    assert diags[1]["frame_path"] == FRAME_ERROR_160["frame_path"], diags[1]


def test_the_journal_line_on_disk_keeps_which_frame_and_why(tmp_path):
    """同一条性质的**落盘那一端**：账本文件里那一行读回来，`detail` / `frame_path` 还在。

    为什么要单钉一条（`_summarize` 那条不是已经管了吗）：`journal` 的行是
    **JSON 序列化过**的（`json.dumps` → 读回），而「进账本」对读账的人 =
    **盘上那一行**。窗口死掉之后，人与复审只能靠这行文件 —— 内存里那份早没了。

    ⚠️ 落盘那侧刻意**不做检查**（`journal` 的模块 docstring 写了：那是 `_summarize`
    的职责，两处都写就成了两份判据）—— 所以这条只是把「读回来的那一行」量出来。
    """
    journey, _, _ = _run(
        tmp_path,
        {"observe": [{"structured": _page_with_diagnostics(REAL_FRAME_BLIND,
                                                           FRAME_ERROR_160)}]},
        [{"calls": [("observe", {})]}, {"content": "看完了"}],
    )
    path = tmp_path / "attempt-1.jsonl"
    journal.append(path, journey.steps[0])
    rows, skipped = journal.read(path)
    assert skipped == [] and len(rows) == 1, (rows, skipped)
    diags = rows[0]["result"]["diagnostics"]
    assert diags[0]["detail"] == REAL_FRAME_BLIND["detail"], diags
    assert diags[0]["frame_path"] == REAL_FRAME_BLIND["frame_path"], diags
    # 长的那条（真报错那一档，160 上限）也要原样过一遍盘 —— 半路被截就红在这儿
    assert diags[1]["detail"] == FRAME_ERROR_160["detail"], diags[1]


def test_a_diagnostic_row_does_not_drag_the_rest_of_the_tool_return_in(tmp_path):
    """诊断那一格**只留诊断自己的几个字段** —— 工具返回的别的东西一个都不许跟进来。

    为什么这条非有不可：`Journey.steps` 会进 **checkpoint**（`graph.MSGCPACK_ALLOWLIST`
    点名允许 `Journey`），而原始工具返回里可能有几百 KB 的 base64 截图
    （`_summarize` 的 docstring 就是为这件事写的：「整份抄进账本只会让内存和落盘都变得没法读」）。
    所以修「只留 kind」时**不许顺手改成「整条诊断抄进来」**——
    今天多一个键，明天工具给诊断挂上 `screenshot`，账本就跟着涨。
    """
    fat = dict(REAL_FRAME_BLIND,
               # 真实形状：工具哪天给诊断挂上这些（截图 / 整段 DOM / 元素坐标列表）
               screenshot="data:image/png;base64," + "A" * 20000,
               dom="<html>" + "x" * 20000,
               bbox=list(range(2000)))
    journey, _, _ = _run(
        tmp_path,
        {"observe": [{"structured": _page_with_diagnostics(fat)}]},
        [{"calls": [("observe", {})]}, {"content": "看完了"}],
    )
    diags = journey.steps[0]["result"]["diagnostics"]
    assert len(diags) == 1, diags
    assert set(diags[0]) == {"kind", "frame_path", "detail"}, \
        f"诊断那一格混进了别的东西：{sorted(diags[0])}"
    # 账本那一行整个（一步，含正文）也要守住体量：截图跟着进来的话这里就是 2 万+ 字符。
    line = json.dumps(journey.steps[0], ensure_ascii=False)
    assert len(line) < 2000, f"账本那一步被工具返回撑大了：{len(line)} 字符"


# ══════════ 见到成功文案就收摊（Task 15：真站 job-a4d100addd25 第 66 → 71 步）══════════
#
# **病**（2026-09-20 真站实测）：`runtime/explore/job-a4d100addd25/attempt-1.jsonl` 的
# **第 66 步**是一眼 `observe`，它的 `result.page_text_head` 里**就是**人给的那句成功文案
# （下面 `MATCHED` 那句，逐字相同）；而它**没有停** —— 一路走到第 71 步（中间 `goto` 去了
# 别的页、又点了一下），最后是**人按了停**才收的（`attempts.jsonl`：71 步 / `paused`）。
# 运营的原话：「能成功为啥还要继续？」「我不希望她再刷」。
#
# 这条道理本仓库**自己已经写过** —— `replayable_prefix` 的 R3
# （`agent/browser_agent.py` 那条注释：「成功文案出现 = 这一趟已经成了；过了那条线之后
# 每一个动作都可能是**重复的真实请求**」）—— 只是它**只用在重放上**。
# 这个 section 把它接到**活着的那一趟**上：`_stop_reason` 多一条。

#: 真站那一趟第 66 步的正文里逐字含着的那句 —— 就是人（运营）给的成功判据。
MATCHED = "Good news - We've matched you! Your quote is on the way!"

PAGE_MATCHED = {
    "url": "https://example.test/funnel?matched=1",
    "title": "Example 漏斗 匹配好了",
    "page_text": MATCHED + " Best Match Endurance Direct claims, no middleman",
    "shadow_roots": 0,
    "viewport_css_px": {"width": 1280, "height": 800},
    "actions": [
        {
            "selector": "#see-other-quotes", "alternates": [], "stability": "high",
            "text": "See Other Quotes", "role": "button", "tag": "BUTTON", "type": "",
            "visible": True, "occluded_by": None, "shadow_depth": 0,
            "frame_path": ["main"], "bbox": [10, 300, 200, 60], "region": "main",
            "above_fold": True, "relative_size": 0.2, "peer_count": 1,
            "z_index": "auto", "contrast": "", "nearby_text": [],
        }
    ],
    "fields": [], "option_groups": [], "obstructions": [], "honeypots": [], "diagnostics": [],
}


def test_it_stops_the_moment_the_success_text_shows_up_instead_of_going_on(tmp_path):
    """★ 看见了那句成功文案 ⇒ **当场收摊**，之后一次真请求都不许再发。

    真站那一趟第 66 步看见了，**第 67 步还是一个 `goto`**（真人页面上的真请求）——
    「过了那条线之后的每一个动作都可能是重复的真实请求」说的就是这个。
    所以判据落在**桩服务那边真发出去的调用**上（agent 自己的账本可以自证不了）。
    """
    journey, fake, calls = _run(
        tmp_path,
        # 第 1 眼在还没成的页上，第 2 眼就看见了 —— 与真站那一趟的形状同源
        {"observe": [{"structured": PAGE_LANDING}, {"structured": PAGE_MATCHED}]},
        [{"calls": [("observe", {})]},
         {"calls": [("observe", {})]},
         {"calls": [("click", {"selector": "#see-other-quotes"})]},   # ← 过了线它还想再点
         {"content": "我又点了一下"}],
        budget=browser_agent.Budget(max_steps=50, max_rounds=50),
        success_text=MATCHED,
    )
    assert journey.stop_reason == "reached_success", journey.stop_reason
    assert [c["name"] for c in calls] == ["goto", "observe", "observe"], (
        "过了那条线之后还有真请求发出去：%r"
        % [(c["name"], c["args"]) for c in calls])
    assert len(journey.steps) == 2, [s["action"] for s in journey.steps]
    assert len(fake.calls) == 2, (
        "看见了成功文案之后还问了一轮模型（%d 轮）—— 那一轮让「再点一下」有了机会"
        % len(fake.calls))


def test_a_click_queued_in_the_same_round_after_the_look_never_reaches_the_site(tmp_path):
    """同一轮里排在「看见成功文案那一眼」**后面**的动作也一律不发（C2 那个形状）。

    问的是「停」发生在**每一步之前**还是**每一轮之后**：只在轮边界上检查的实现，
    会把同一轮剩下的动作照发出去 —— 而真站那一趟正是「一轮里好几个动作」的形状。
    """
    journey, _fake, calls = _run(
        tmp_path,
        {"observe": [{"structured": PAGE_MATCHED}]},
        [{"calls": [("observe", {}), ("click", {"selector": "#see-other-quotes"})]}],
        budget=browser_agent.Budget(max_steps=50, max_rounds=50),
        success_text=MATCHED,
    )
    assert [c["name"] for c in calls] == ["goto", "observe"], (
        "同一轮里排在后面那一下也发出去了：%r" % [(c["name"], c["args"]) for c in calls])
    assert journey.stop_reason == "reached_success", journey.stop_reason


def test_the_human_stop_is_still_reported_as_the_human_stop(tmp_path):
    """⚠️ **人喊停那一条的语义一个字没动**：两道闸同时为真时，报的是**人**那一句。

    「停」的**时刻**两条一模一样（都在下一步之前、都不做那一步）；
    变的只是**理由报哪个** —— 而人按下去的那一下永远是优先的那个。
    """
    journey, _fake, calls = _run(
        tmp_path,
        {"observe": [{"structured": PAGE_MATCHED}]},
        [{"calls": [("observe", {})]}],
        budget=browser_agent.Budget(max_steps=50, max_rounds=50),
        success_text=MATCHED,
        should_pause=lambda j: len(j.steps) >= 1,
    )
    assert journey.stop_reason == "paused", journey.stop_reason
    assert [c["name"] for c in calls] == ["goto", "observe"], calls


def test_a_budget_stop_that_never_saw_the_line_is_still_a_budget_stop(tmp_path):
    """⚠️ **预算那一条的语义也一个字没动**：没看见成功文案时，停因还是 `budget_steps`。

    这里传的是**给了判据、但页面上没有**的那一格（`success_text=MATCHED` 非空，
    而 `PAGE_LANDING` 的正文里没有它）—— 「判据非空但它不匹配」照旧不拦。
    ⚠️ **空判据那一格不是这一条**：它由 `an_empty_success_text_does_not_stop_the_walk_early`
    两条参数覆盖（复审 F5：这一条的旧自述把它说成了空判据的对照，名/述与实际断言不符）。
    """
    journey, _fake, _calls = _run(
        tmp_path,
        {"observe": [{"structured": PAGE_LANDING}]},
        [{"calls": [("observe", {})]}],
        budget=browser_agent.Budget(max_steps=3, max_rounds=50),
        success_text=MATCHED,
    )
    assert journey.stop_reason == "budget_steps", journey.stop_reason
    assert len(journey.steps) == 3, len(journey.steps)


@pytest.mark.parametrize("extra", [{}, {"success_text": ""}])
def test_an_empty_success_text_does_not_stop_the_walk_early(tmp_path, extra):
    """判据没给 / 给了个空串 ⇒ **判不了就不停**（空**不是**「什么都算成功」）。

    空串要是被当成通配，这一趟会在**第 1 步**就停下并自称成功 —— 那是把
    「少给了一个输入」翻译成了一句**假话**。所以：**不猜**（与 `graph._explore_reached_success`
    给空的处置同口径：判不了就是判不了），照常跑到预算顶。

    ⚠️ **名字不许再撞上 `:2280` 那条**（`replayable_prefix` 对空**抛** `ValueError`）——
    那条老用例是同名的前身，被这一条遮蔽过一次（复审 F1，`f74b585`），
    而现在这一条覆盖的是**另一件事**（活的探路停不停），两条都得真跑起来。
    """
    journey, _fake, _calls = _run(
        tmp_path,
        {"observe": [{"structured": PAGE_MATCHED}]},     # 页面上**确实**有那句文案
        [{"calls": [("observe", {})]}],
        budget=browser_agent.Budget(max_steps=2, max_rounds=50),
        **extra,
    )
    assert journey.stop_reason == "budget_steps", journey.stop_reason
    assert len(journey.steps) == 2, "空判据把它在第 1 步就拦下了 —— 那是「什么都算成功」"


def test_the_account_says_it_stopped_because_it_succeeded(tmp_path):
    """账上那句话必须是「成了」—— **不许**落进「没走完」那一支（R0 那半份账本的说法）。

    为什么这条是硬的：`_unfinished_note` / R0 一族的说法（「没走完 ⇒ 拿半份账本写不出对的 py」）
    会让下游**因为成功而拒绝写 py**；而这一趟是**探到了**的，账本里有那条通向成功的路。
    """
    journey, _fake, _calls = _run(
        tmp_path,
        {"observe": [{"structured": PAGE_LANDING}, {"structured": PAGE_MATCHED}]},
        [{"calls": [("observe", {})]}, {"calls": [("observe", {})]}, {"content": "说完了"}],
        budget=browser_agent.Budget(max_steps=50, max_rounds=50),
        success_text=MATCHED,
    )
    tail = journey.notes[-1]
    assert "成功文案" in tail, tail
    assert "reached_success" not in tail, (
        "内部停因的 token 进了人话（M-5：这份账的读者是非技术的人）：%s" % tail)
    trailing = "".join(journey.notes[-2:])
    assert "没走完" not in trailing, (
        "「成了就停」被写成了「没走完」—— 读账的人会以为这一趟白跑了：%s" % trailing)


def test_the_line_the_live_run_stops_on_is_the_same_line_the_graph_settles(tmp_path):
    """活的那一趟停下的判据，与图上**事后**结算的 `_explore_reached_success` = **同一把尺子**。

    两边要是不一致：这一趟在 `reached_success` 上停住，而图上把它结算成
    「没在页面上见到成功文案」⇒ 人会读到**两句互相打架的话**，而坏的那句会
    **拒绝往下写 py**（尽管这一趟明明成了）。
    """
    from agent import graph as graph_mod

    journey, _fake, _calls = _run(
        tmp_path,
        {"observe": [{"structured": PAGE_LANDING}, {"structured": PAGE_MATCHED}]},
        [{"calls": [("observe", {})]}, {"calls": [("observe", {})]}, {"content": "说完了"}],
        budget=browser_agent.Budget(max_steps=50, max_rounds=50),
        success_text=MATCHED,
    )
    assert journey.stop_reason == "reached_success", journey.stop_reason
    assert graph_mod._explore_reached_success(journey, MATCHED) is True, (
        "停下来说「见着了」、图上结算说「没见着」—— 同一个事实两个答案")


@pytest.mark.parametrize("raw", ["", "  ", "a\nb", " a\t b ", "a b", "x  y", "甲　乙"])
def test_the_three_norms_are_one_yardstick(raw):
    """**三把**归一化必须是同一把尺子（R1 点名的就是三处，复审 F4：原先只跨校了 2 把）。

    三处判的都是**同一句话在不在页面上**：

    1. `browser_agent._norm` —— 活的探路（`_stop_reason`）与重放前缀 R3
       （`replayable_prefix`）用的就是它；
    2. `graph._norm_text` —— 图上**事后**结算用的（`_explore_reached_success`）；
    3. `template._norm` —— **产物那一侧**（`page_signature()` 脚下那份，`agent/template.py`）。

    前两把要是漂了：活的那一趟停下来说「见着了」、图上说「没见着」⇒ **拒绝写 py**。
    第一把与第三把要是漂了：**探路看见的那一页**与**脚本重放时判的那一页**不是同一页
    （规格 §4.3 那句「与 py 里 page_signature() 同口径」）—— 交付出去的那份 py
    会在真站上认错页。
    ⚠️ 三处各自的注释都写着「必须与别人一致」，但**只有这条用例**会红。
    """
    from agent import graph as graph_mod

    want = browser_agent._norm(raw)
    assert graph_mod._norm_text(raw) == want, "图上那份（`_norm_text`）与探路那份漂了"
    assert _artifact_norm()(raw) == want, (
        "产物那份（`page_signature()` 脚下那个 `_norm`）与探路那份漂了")


def _artifact_norm():
    """产物那一侧的 `_norm` —— **从骨架源码里取出来、真编译一遍**再交给用例。

    ⚠️ 它**不是** `agent/template.py` 的模块级函数：它是**交付出去那份 py** 里的函数
    （`SKELETON.template` 那段源码，`page_signature()` 就调它）。所以这里把那段源码
    切下来真跑一遍 —— 跨校的是**真会跑在真站上的那份**，不是另抄的一份
    （另抄一份 = 又一个「同一个事实两个名字」）。
    """
    import re as _re                        # 只在这一处用
    src = template.SKELETON.template
    lines = src.splitlines(keepends=True)
    start = next(i for i, ln in enumerate(lines) if ln.startswith("def _norm(text):"))
    block = [lines[start]]
    for ln in lines[start + 1:]:
        if ln.strip() and not ln[:1].isspace():     # 下一个顶层语句（含顶层注释）= 到头
            break
        block.append(ln)
    ns: dict = {"re": _re}
    exec(compile("".join(block), "<artifact>", "exec"), ns)
    return ns["_norm"]


def test_only_the_observe_rows_can_carry_the_line():
    """R1 点名的那条守卫：**只认 `observe` 行给出的 `page_text_head`**（复审 F3）。

    今天它是**冗余**的（正文那一键只有 `_summarize` 的 `observe` 支会写，别的工具带了它
    会当场抛 `_TextPremiseBroken`）—— 但「今天冗余」不等于「不用钉」：这条判据的**全部力量**
    就来自那个前提（R3 的整套推导是「窗口里任何一眼看见 ⇔ 最后一眼看见」）。
    把这一行去掉，判据就变成「**任何**一行带着那段文字都算过了线」—— 那是一个**更宽**的判据，
    而它不会有任何东西报错。

    ⚠️ 为什么钉判据本身、不走一遍 `explore()`：**端到端造不出这一形**
    （`_summarize` 那道前提在更早的地方就抛了，见上）—— 与 R3 那边同一个处境，
    所以这里直接喂**手写的账本行**。
    """
    #: 一行**不是** `observe` 却带着正文（今天造不出来，正是要钉的那个「万一」）
    smuggled = _row("click", "funnel",
                    result={"url": ENTRY, "page_text_head": "点了之后这一页上写着 " + MATCHED})
    assert browser_agent._success_hit([smuggled, _look(ENTRY, TEXT_A)], MATCHED) is None, (
        "非 observe 行上的正文被当成了「看见成功文案」—— 判据比 R3 宽了")
    #: 正例（同时也是「最早命中的那一步」的钉子）：第 2 行那一眼才是看见的那一眼
    rows = [_look(ENTRY, TEXT_A), _look(QUOTE, "这一页上写着 " + MATCHED, state="quote")]
    assert browser_agent._success_hit(rows, MATCHED) == 1, (
        "判据不是「最早命中那一步」：%r" % browser_agent._success_hit(rows, MATCHED))

def test_a_replay_that_already_crossed_the_line_does_not_walk_on(tmp_path):
    """续跑那一趟：重放回来的那一眼**已经**含着成功文案 ⇒ 重放完就收，**一轮模型都不问**。

    ⚠️ 生产路径上走不到这一形（`replayable_prefix` 的 R3 会把跨过那条线的那一步切在边界外）；
    这一条钉的是**判据长在哪**：它长在 `journey.steps` 上（重放那几步也在里面）。
    换成「只看模型驱动的那几步」的实现，这一条当场红。
    """
    line = "已经收到你的申请。" + MATCHED
    rows = [_goto(ENTRY, state="start"),
            _look(ENTRY, TEXT_A, state="start"),
            _click("开始申请", state="funnel"),
            _look(QUOTE, line, state="funnel")]
    journey, fake, calls = _run(
        tmp_path,
        {"observe": [{"structured": _live_page(ENTRY, TEXT_A)},
                     {"structured": _live_page(QUOTE, line)}]},
        [{"calls": [("click", {"selector": "#cta"})]}],      # 重放完之后它还想再点
        budget=browser_agent.Budget(max_steps=50, max_rounds=50),
        resume_from=rows,
        success_text=MATCHED,
    )
    assert journey.replay.get("done") == 2, journey.replay
    assert [c["name"] for c in calls] == ["goto", "observe", "click", "observe"], calls
    assert len(fake.calls) == 0, "重放完还问了一轮模型：%d" % len(fake.calls)
    assert journey.stop_reason == "reached_success", journey.stop_reason


def test_the_line_wins_over_the_budget_when_both_are_true(tmp_path):
    """⚠️ 两道闸**同时为真**时报的是**成功**那条（看见文案的那一眼正好是预算的最后一步）。

    报 `budget_steps` 会把这一趟判成「没走完」⇒ 图那边**拒绝写 py** —— 而它明明成了。
    停的**时刻**两条**一模一样**（都在下一步之前、都不做那一步），变的只是理由报哪个。
    ⚠️ 这一条钉的是 `_stop_reason` 里那两条的**先后**：调过来它当场红。
    """
    journey, _fake, calls = _run(
        tmp_path,
        {"observe": [{"structured": PAGE_MATCHED}]},
        [{"calls": [("observe", {})]}],
        budget=browser_agent.Budget(max_steps=1, max_rounds=50),   # 那一眼就是最后一步
        success_text=MATCHED,
    )
    assert len(journey.steps) == 1, [s["action"] for s in journey.steps]
    assert [c["name"] for c in calls] == ["goto", "observe"], calls
    assert journey.stop_reason == "reached_success", (
        "预算与成功线同时为真时报了 %r —— 那一趟会被判成「没走完」，于是不写 py"
        % journey.stop_reason)
