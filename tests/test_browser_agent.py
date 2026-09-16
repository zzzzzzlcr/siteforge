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

from agent import browser_agent, lint, llm, template, tools  # noqa: E402

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
    assert len(calls) == 3, f"预算只允许 3 步，真发出去的工具调用却是 {len(calls)} 次"
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
    assert len(calls) == 1, f"同一轮里剩下的调用照发了（跑了 {len(calls)} 次，跑完才退）"
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
    assert len(journey.steps) == 2 and len(calls) == 2
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
        if len(asked) == 2:                 # 第 1 次（轮边界）还好好的，第 2 次（步之前）炸了
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
    assert calls == [], f"闸炸了却还是把工具调用发出去了：{calls}"
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
    assert [c["name"] for c in calls] == ["observe", "click", "scroll"]
    for step in journey.steps:
        assert set(step) == {"state", "action", "target", "result", "note"}, step

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
    for step in journey.steps:
        assert set(step) == {"state", "action", "target", "result", "note"}, step

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
    assert [c["name"] for c in calls] == ["observe", "click", "diff"], "三个调用没按顺序都执行"
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
