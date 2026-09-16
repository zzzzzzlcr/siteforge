"""Task 7：LangGraph 图 —— 把 Task 1/3/4/5/6 串起来跑，**人每一步都在**（§6.2）。

```
START → intake → explore → draft → lint → selftest → deliver → END
                     ↑                  │       │
                     │  带违规行回灌 ←───┘       │
                     └────── 带证据回灌 ←─ diagnose ←┘
```

## 三处必须写对的（brief 点名的）

### 1. 人不是最后一道关，是**每一步都在**（§6.2）

每个节点**开工之前**都 `interrupt()` 一次：第一个节点之前、`deliver` 之前，一视同仁。
不是「以后加个 UI」—— 它是架构约束：**任何一步都不许设计成「不可打断、跑完才汇报」**。
所以这道闸写在 `_enter()` 里，每个节点第一件事就是过它，没有例外。

中断之后**真能接着跑**，靠的是 checkpointer（R-19）：`build(checkpointer=…)` 是**必需**参数，
不许默认 `None` —— 「没有 saver」正是「中断之后恢复不了」的根因：
没有 saver 时 `interrupt()` 不报错，图安安静静地停在那儿，而**谁也没法让它再动**
（langgraph 不会拦你，它只是永远返回同一个中断）。存哪儿是**接线**（Task 8 用 Postgres），
但**必须有**。

### 2. 两条回灌都有硬上限（§6.5）

- `lint` 不通过 → 回 `draft`，**带着违规行**（`{line, code, message, snippet}`）——「你手拼 JS 了」
  它找不到地方，得指着行说
- `selftest` 挂了 → `diagnose`（说清哪一遍、卡在第几步）→ 回 `draft`，**带着证据**
- 两条各自的上限在 `state.Caps`。到顶就**停**，并把「为什么停」说成人话

上限要防的是**「跑不完也不会停」的图**，不是省钱（P5）—— 所以别拿砍轮数当优化。

### 3. `deliver` 写出的 py 带 `PROVENANCE`（§5.3）

产物要把**自己的自测结果**写进 `PROVENANCE`，而自测结果只有跑完才知道 ——
所以顺序是：`draft` 先渲一版（`selftest: None`）→ 自测跑**这一版** → `deliver` 用同一个 spec
再渲一版（带上自测结果）落盘。两版之间**只差 `PROVENANCE` 那一块**，
`tests/test_graph.py::test_the_delivered_bytes_differ_from_the_tested_bytes_only_in_provenance`
拿 `ast` 钉着这件事；`deliver` 落盘前还会**再 lint 一次要落的字节**（那块里有自由文本）。

## 谁填 `PROVENANCE` 的哪些键 —— 图**只填它真知道的**

| 键 | 谁填 |
|---|---|
| `generated_at` / `generator` | 图 |
| `env`（代理国家/DPR/UA/视口） | **§4.6 前提层**（Task 8 接线：拉链 → `POST /browser/update` → `bit.sh open`）。图只搬运：有人告诉它就带上，没有就是 `None` |
| `platform` | **不在本计划**（平台分类）；图同样只搬运 |
| `selftest` | 图（跑完才知道） |
| `source.kind` / `source.evidence` | 图（人给的意图 / 失败证据，原样带上） |
| `source.runtime` | `agent.runtime.provenance()`（R-15：这份 py 跑起来用的是**哪一份** `common.py`）|

**没人告诉图的事一律 `None`** —— 缺的键补 `None`，不编内容（§5.3 的同一条）。

## 有一件事这个文件**故意**不做

`deps.write`（「按账本写 py」那一步）默认是**恒等**的确定性翻译（`Journey.states()` /
`journey.fills()` → `template.render()`）：**它改不了自己写出来的东西**。
所以默认接线里，lint / selftest 打回只会走到上限就停 —— 那是**诚实的**（图不假装修好了），
不是藏着缺陷：**能改产物的那个角色（模型 / Console 里的人）从这里注入**
（`write=(spec, feedback) -> spec`，feedback 里就是违规行 / 诊断证据 / 人说的话）。

没有这个缝，「回灌」就只能测到「loop 转了几圈」，测不到「证据真的到了能改它的那双手里」。
"""

from __future__ import annotations

import datetime
import pathlib
import re
from dataclasses import dataclass
from typing import Callable, Optional

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from agent import browser_agent, lint as lint_mod, runtime
from agent import selftest as selftest_mod
from agent import template
from agent.state import (
    END_DELIVERED, END_DELIVER_LINT, END_DRAFT_FAILED, END_EXPLORE_UNFINISHED,
    END_HUMAN_STOP, END_LINT_CAP, END_NO_BRIEF, END_NO_WINDOW, END_PAUSED, END_SELFTEST_CAP,
    FINISHED_EXPLORATION, GENERATOR, MODE_BUILD, STOP, Caps, SiteState, human_reply,
)

__all__ = ["Deps", "build", "Caps", "MSGPACK_ALLOWLIST", "allowlisted", "NODES",
           "HUMAN_CAN", "STEP_SAY"]

_REPO = pathlib.Path(__file__).resolve().parents[1]

#: 产物默认落在哪（生产就是 `forms/sites/`，与 63 个手写脚本同一层）。
DEFAULT_OUT_DIR = _REPO / "forms" / "sites"

#: 图里所有的节点，按走的顺序（测试与文档都认这一份）。
NODES = ("intake", "explore", "draft", "lint", "selftest", "deliver", "diagnose")

#: 每一步在**人话**里叫什么（D16：闸口上问的不是错误码、不是选择器）。
STEP_SAY = {
    "intake": "开工前的确认",
    "explore": "打开浏览器探路",
    "draft": "写这一版 py",
    "lint": "检查这一版有没有手拼 JS",
    "selftest": "在真浏览器上按扰动序列自测",
    "deliver": "把它写进站点目录",
    "diagnose": "从自测记录里定位卡在哪",
}

#: 闸口上人**能做什么**（每次都说清楚，免得人以为自己只能点「继续」）。
HUMAN_CAN = ("让它继续（回 continue / 空 / 不回话）",
             "喊停（回 stop）—— 停在这一步之前，这一步不会做",
             "说一句纠正（回一句话或 {action: revise, note: …}）—— 接着走，这句话带进 draft")

#: 存进 checkpoint 的那几个 dataclass。langgraph 的 serde 要**点名允许**它们
#: （不然新版本会拒收：`Deserializing unregistered type … will be blocked in a future version`）。
#: R-19：换 saver 是**接线**（Task 8 的 Postgres）——把 `PostgresSaver(...)` 包一层
#: `allowlisted(...)` 就行，别改这里。
MSGPACK_ALLOWLIST = (("agent.browser_agent", "Journey"),
                     ("agent.selftest", "Report"),
                     ("agent.selftest", "Run"))


def allowlisted(saver):
    """给 saver 加上 `MSGPACK_ALLOWLIST`（拿不到这个能力的 saver 原样返回）。"""
    adder = getattr(saver, "with_allowlist", None)
    return adder(MSGPACK_ALLOWLIST) if callable(adder) else saver


# ─────────────────────────── 依赖（测试从这里注入桩）───────────────────────────


def _writer_from_journey(spec: dict, feedback: dict) -> dict:
    """默认的「写 py」：**确定性的翻译**（账本 → `states` / `fills`），恒等，不改东西。

    ⚠️ 它**修不了**自己被 lint / 自测打回的那一版（看一眼就知道为什么：它没有判断力）。
    这不是缺陷，是**能力的边界**：能改产物的那双手（模型 / 人）从 `Deps.write` 注入，
    `feedback` 就是递给那双手的东西。默认接线里，打回只会走到上限就停 —— 诚实的那种停。
    """
    return spec


@dataclass
class Deps:
    """这张图跟外面世界的每一个接触面（**全部**可注入，测试里全是桩）。

    `should_pause` 为什么在这里而不是在 state 里：状态要进 checkpoint，
    **可调用的东西进不去**。它是 Console 那只「停」按钮伸进浏览器的那根线（§6.2）。
    """

    explore: Callable = browser_agent.explore
    write: Callable = _writer_from_journey
    lint: Callable = lint_mod.check
    selftest: Callable = selftest_mod.run
    provenance: Callable = runtime.provenance
    should_pause: Optional[Callable] = None


# ───────────────────────────── 人的那道闸 ─────────────────────────────


def _visited(state, step: str) -> list:
    return list(state.get("visits") or []) + [step]


def _enter(state, step: str, say: str, facts: Optional[dict] = None) -> dict:
    """**每个节点开工之前**过这道闸（§6.2）。返回该写回状态的那部分。

    - `interrupt()` 在这里抛出去：图就停在**这一步之前**，这一步**没有做**
    - 人回来说的话：纠正 → 收进 `hints`（一路带着，进 draft）；喊停 → 只写 `end_reason`
      /`end_note` 回去，调用方那个节点会看到它、直接收摊

    `say` 是给人看的人话，`facts` 是原始事实（D11：给感知不给判断 —— 人要看得到原料）。
    """
    reply = interrupt({"step": step, "say": say, "facts": facts or {}, "can": list(HUMAN_CAN)})
    action, note = human_reply(reply)
    out: dict = {"visits": _visited(state, step)}
    if note:
        out["hints"] = list(state.get("hints") or []) + [note]
    if action == STOP:
        out["end_reason"] = END_HUMAN_STOP
        out["end_note"] = ("人喊停：在「%s」这一步**之前**停下来，这一步没有做，页面与文件都保持原样。"
                           "接着走就再发起一次 —— 已经探到的账本还在（checkpoint 里）。"
                           % STEP_SAY.get(step, step))
        _drop_candidate(state)          # 停下来的这次运行，交付目录里不留东西
    return out


# ─────────────────────────────── 六个节点 ───────────────────────────────


def _intake(state, deps: Deps, caps: Caps) -> dict:
    """收开场白：哪个站、要做什么、成功长什么样。

    §4.6 的前提层（拉链 → 下发指纹 → 开窗口）**不在这里**：那是接线（Task 8），
    产物（`ws_url` / `env`）由调用方放进来。图只负责把它带上。
    """
    url = str(state.get("url") or "").strip()
    goal = str(state.get("goal") or state.get("evidence") or "").strip()
    say = ("准备开工：站点是「%s」，这次要做的是「%s」。" % (url or "（还没说）", goal or "（还没说）")
           + "开工之后每一步之前都会再问你一次，随时可以喊停或纠正。")
    out = _enter(state, "intake", say, facts={"url": url, "goal": goal,
                                              "mode": state.get("mode") or MODE_BUILD,
                                              "要用的窗口": state.get("ws_url")})
    if out.get("end_reason"):
        return out
    if not url or not goal:
        out.update({"end_reason": END_NO_BRIEF,
                    "end_note": ("开不了工：得先说清**哪个站点**（url）和**要做什么**（goal 或失败证据）。"
                                 "没有这两样，探路会去开一个浏览器、然后在空页面上乱走。")})
        return out
    out.update({
        "url": url,
        "goal": goal,
        "mode": state.get("mode") or MODE_BUILD,
        "site": state.get("site") or site_name(url),
        # 人在**开工前**那道闸上说的话也要留着（`_enter` 刚记进 out，不能在这儿盖掉）
        "hints": list(out.get("hints") or state.get("hints") or []),
        "lint_bounces": int(state.get("lint_bounces") or 0),
        "diagnoses": int(state.get("diagnoses") or 0),
        "out_dir": str(state.get("out_dir") or DEFAULT_OUT_DIR),
        "end_reason": "",
        "end_note": "",
    })
    return out


def _explore(state, deps: Deps, caps: Caps) -> dict:
    """在真浏览器里走一遍，拿回账本（Task 5）。"""
    budget = browser_agent.Budget(max_steps=caps.explore_steps, max_rounds=caps.explore_rounds)
    say = ("接下来要打开真浏览器，把「%s」按这个目标走一遍：「%s」。"
           "这一步会动到真页面（点、填、滚），探完把「怎么走」记下来。" % (state["url"], state["goal"]))
    out = _enter(state, "explore", say,
                 facts={"url": state["url"], "goal": state["goal"],
                        "预算": "最多 %d 步 / %d 轮（防跑飞，不是省钱）"
                                % (budget.max_steps, budget.max_rounds)})
    if out.get("end_reason"):
        return out

    # ⚠️ 这里**不接** `_Stop`（它继承 BaseException，就是为了不被吞成工具失败 ——
    #    browser_agent:206）。人喊停的信号必须原样穿出去，不许被降级成「探路失败」。
    journey = deps.explore(state["url"], state["goal"], budget=budget,
                           should_pause=deps.should_pause)
    out["journey"] = journey
    out["explore_say"] = _journey_say(journey)
    stop = str(getattr(journey, "stop_reason", "") or "")
    if stop not in FINISHED_EXPLORATION:
        out["end_reason"] = END_PAUSED if stop == "paused" else END_EXPLORE_UNFINISHED
        out["end_note"] = _unfinished_note(stop, journey)
    return out


def _draft(state, deps: Deps, caps: Caps) -> dict:
    """把账本翻译成 py 源码（`template.render`），**带上回灌的证据**。

    回灌的东西一律走 `feedback` 递给 `deps.write`：违规行（指着行说）、诊断证据
    （哪一遍、卡在第几步）、以及人在闸口说过的话。
    """
    was = _feedback(state)
    out = _enter(state, "draft", _draft_say(state, was),
                 facts={"violations": was["violations"], "diagnosis": was["diagnosis"],
                        "第几版": list(state.get("visits") or []).count("draft") + 1})
    if out.get("end_reason"):
        return out
    # 人在**这一道闸**上说的话，属于**这一版**稿（所以 feedback 在闸之后组装）
    feedback = _feedback(state, hints=out.get("hints"))

    journey = state.get("journey")
    if journey is None:
        out.update({"end_reason": END_DRAFT_FAILED,
                    "end_note": "写不了：这次没有探路账本（没有账本就没有「怎么走」）。"})
        return out

    spec = {"site": state["site"], "success_text": state.get("success_text"),
            "states": journey.states(), "fills": journey.fills()}
    spec = deps.write(spec, feedback)
    try:
        src = template.render(spec["site"], spec["success_text"], spec["states"], spec["fills"],
                              provenance=_provenance(state, deps, report=None))
    except ValueError as exc:
        # 最常见的一种：没人说「什么算成功」。**不猜** —— 猜出来的成功判据就是
        # 「跑到底再谎报成功」的入口（template.py 也拒这种产物）。
        out.update({"end_reason": END_DRAFT_FAILED,
                    "end_note": ("先别写 py：%s\n（成功判据只有人知道 —— §6.1：页面能告诉 agent "
                                 "**机制**，只有人能告诉它**意图**。）" % exc)})
        return out

    out.update({"states": spec["states"], "fills": spec["fills"],
                "success_text": spec["success_text"], "src": src, "violations": []})
    return out


def _lint(state, deps: Deps, caps: Caps) -> dict:
    """契约检查（Task 4）：产出的 py 里有没有手拼 JS 这类写模式。"""
    out = _enter(state, "lint", "接下来要把刚写好的这一版逐行过一遍契约检查（重点是手拼 JS）。",
                 facts={"检查的是": "刚写好的那一版（%d 行）" % len((state.get("src") or "").splitlines())})
    if out.get("end_reason"):
        return out

    violations = [_violation_dict(v) for v in (deps.lint(state["src"]) or [])]
    out["violations"] = violations
    if not violations:
        return out

    out["lint_bounces"] = int(state.get("lint_bounces") or 0) + 1
    if out["lint_bounces"] > caps.max_lint_bounces:
        out.update({"end_reason": END_LINT_CAP,
                    "end_note": _lint_cap_note(out["lint_bounces"], violations)})
    return out


def _selftest(state, deps: Deps, caps: Caps) -> dict:
    """扰动自测（Task 6）：在真浏览器上按扰动序列跑，任一遍挂就不算过（§10）。

    没有窗口就**停**，不许「跳过自测当通过」—— 那是把「没验到」说成「过了」。
    """
    out = _enter(state, "selftest",
                 ("接下来要在真浏览器上按扰动序列跑几遍：正常跑一遍、接着再跑一遍、放慢跑一遍、"
                  "再换个窗口大小跑一遍。**自测通过 ≠ 生产一定过** —— 工具侧跑的浏览器与生产 "
                  "worker 的代理出口/指纹/时序不是一套（规格 §10）。"),
                 facts={"窗口": state.get("ws_url"), "表单数据": state.get("form_file")})
    if out.get("end_reason"):
        return out

    if not state.get("ws_url") or not state.get("form_file"):
        out.update({"end_reason": END_NO_WINDOW,
                    "end_note": ("自测跑不了：没有一个可用的浏览器窗口（或没给表单数据）。"
                                 "**不许跳过自测当通过** —— 没验到的东西说成过了，正是这套系统最贵的谎。"
                                 "先把窗口开起来（§4.6 的前提层；窗口本身只活几分钟，P6）再接着走。")})
        return out

    py = _stage_candidate(state)
    report = deps.selftest(str(py), state["ws_url"], state["form_file"], state["site"])
    out.update({"candidate_path": str(py), "report": report})
    if not report.passed:
        out["diagnoses"] = int(state.get("diagnoses") or 0) + 1
        if out["diagnoses"] > caps.max_diagnoses:
            _drop_candidate(state)
            out.update({"end_reason": END_SELFTEST_CAP,
                        "end_note": _selftest_cap_note(out["diagnoses"], report)})
    return out


def _diagnose(state, deps: Deps, caps: Caps) -> dict:
    """从自测报告里定位「哪一遍、卡在第几步、什么错」——**只说报告里真有的东西**。"""
    evidence = _diagnosis(state.get("report"))
    say = ("自测没过。%s 接下来要拿这份记录去定位，定位完回 draft 改一版。" % evidence["say"])
    out = _enter(state, "diagnose", say, facts={"逐遍结果": evidence["per_run"],
                                                "证据": evidence})
    if out.get("end_reason"):
        return out
    out["diagnosis"] = evidence
    return out


def _deliver(state, deps: Deps, caps: Caps) -> dict:
    """写 `forms/sites/<site>.py`（带 `PROVENANCE`），并**再查一遍要落盘的字节**。

    为什么最后还要查：`PROVENANCE` 里有自由文本（人给的意图/证据），而它是 lint 之后
    才写进去的 ——「查过的那份」与「交出去的那份」天生不是同一串字节。脏了就不落盘，
    也不许悄悄改一改糊过去。
    """
    out = _enter(state, "deliver", _deliver_say(state),
                 facts={"自测": _selftest_block(state.get("report"),
                                                datetime.datetime.now().astimezone()
                                                .isoformat(timespec="seconds")),
                        "要写进哪": str(_delivery_path(state))})
    if out.get("end_reason"):
        return out

    # 闸之后**重算一次**：人在闸上可能待了很久，`generated_at` 该是**落盘那一刻**，
    # 不是「他还没说话的那一刻」。
    prov = _provenance(state, deps, report=state.get("report"))
    try:
        src = template.render(state["site"], state.get("success_text"), state.get("states"),
                              state.get("fills"), provenance=prov)
    except ValueError as exc:                                  # spec 里少了东西（不该到这）
        out.update({"end_reason": END_DRAFT_FAILED, "end_note": "交付前重渲失败：%s" % exc})
        return out

    dirty = [_violation_dict(v) for v in (deps.lint(src) or [])]
    if dirty:
        _drop_candidate(state)
        out.update({"end_reason": END_DELIVER_LINT,
                    "end_note": ("**没有落盘**：要写出去的那串字节自己没过契约检查（多半是 "
                                 "PROVENANCE 里的自由文本撞上了写模式）：%s\n"
                                 "一个字节都没写 —— 宁可没有，也不交一份自己都没过检查的产物。"
                                 % "；".join("第 %s 行：%s" % (v["line"], v["message"])
                                             for v in dirty))})
        return out

    path = _delivery_path(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(src, encoding="utf-8")
    _drop_candidate(state)
    out.update({"py_path": str(path), "provenance": prov, "src": src,
                "end_reason": END_DELIVERED, "end_note": _delivered_note(state, path)})
    return out


# ─────────────────────────────── 路由 ───────────────────────────────


def _next(node: str) -> Callable:
    """往前走 —— 除非已经有 `end_reason`（人喊停 / 前面某一步停了）。"""
    def route(state) -> str:
        return END if state.get("end_reason") else node
    return route


def _after_lint(state) -> str:
    if state.get("end_reason"):
        return END
    return "draft" if state.get("violations") else "selftest"


def _after_selftest(state) -> str:
    if state.get("end_reason"):
        return END
    report = state.get("report")
    return "deliver" if (report is not None and report.passed) else "diagnose"


# ─────────────────────────────── 拼图 ───────────────────────────────


def build(*, checkpointer, deps: Optional[Deps] = None, caps: Optional[Caps] = None):
    """编译这张图。

    参数：
        checkpointer  **必需**（R-19）。没有它，`interrupt()` 之后**恢复不了** ——
                      图会安静地停在那儿，而谁也没法让它再动一下。存哪儿是接线的事
                      （Task 8：Postgres；本地跑用 `InMemorySaver`），但必须有。
                      存 dataclass 的 saver 记得 `allowlisted(...)`。
        deps          跟外面世界的接触面（全部可注入；测试里全是桩）
        caps          硬上限（`state.Caps`）

    用法：

        app = build(checkpointer=allowlisted(InMemorySaver()))
        cfg = {"configurable": {"thread_id": "job-1"}}
        app.invoke({"url": …, "goal": …, "success_text": …}, cfg)   # 停在第一道闸前
        app.invoke(Command(resume="continue"), cfg)                 # 人说了「继续」
        # 想看停在哪儿：`app.get_state(cfg).next` / `out["__interrupt__"][0].value`
    """
    if checkpointer is None:
        # 这一条是**故意**报错的：它正是 R-19 那个根因（「没有 saver」=「中断之后恢复不了」）。
        # 报错比「安静地给你一张恢复不了的图」好一万倍。
        raise ValueError(
            "图必须带 checkpointer（R-19）：没有它，`interrupt()` 之后**恢复不了** —— "
            "人喊停、人纠正都会变成「停在那儿再也动不了」。存哪儿是接线的事"
            "（Task 8 用 Postgres），但**必须有**；本地跑测试用 `InMemorySaver`。")

    deps = deps if deps is not None else Deps()
    caps = caps if caps is not None else Caps()

    graph = StateGraph(SiteState)
    graph.add_node("intake", lambda s: _intake(s, deps, caps))
    graph.add_node("explore", lambda s: _explore(s, deps, caps))
    graph.add_node("draft", lambda s: _draft(s, deps, caps))
    graph.add_node("lint", lambda s: _lint(s, deps, caps))
    graph.add_node("selftest", lambda s: _selftest(s, deps, caps))
    graph.add_node("diagnose", lambda s: _diagnose(s, deps, caps))
    graph.add_node("deliver", lambda s: _deliver(s, deps, caps))

    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", _next("explore"), ["explore", END])
    graph.add_conditional_edges("explore", _next("draft"), ["draft", END])
    graph.add_conditional_edges("draft", _next("lint"), ["lint", END])
    graph.add_conditional_edges("lint", _after_lint, ["draft", "selftest", END])
    graph.add_conditional_edges("selftest", _after_selftest, ["deliver", "diagnose", END])
    graph.add_conditional_edges("diagnose", _next("draft"), ["draft", END])
    graph.add_edge("deliver", END)
    return graph.compile(checkpointer=checkpointer)


# ───────────────────────── 小工具：名字、人话、证据 ─────────────────────────

def site_name(url: str) -> str:
    """从 URL 推一个站点短名（产物文件名、logger 名、路由表都用它）。

    规则朴素：取主机名的第一段有意义的部分（去掉 `www.` 这类），非字母数字压成 `-`。
    推出来的名字**只是默认值** —— 调用方给了 `site` 就用它的。
    """
    host = re.sub(r"^[a-z]+://", "", str(url or ""), flags=re.I).split("/")[0]
    host = host.split("@")[-1].split(":")[0]
    parts = [p for p in host.split(".") if p and p.lower() not in ("www", "m", "co", "com",
                                                                   "org", "net", "io", "uk", "cn")]
    stem = parts[0] if parts else host
    return re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-") or "site"


def _journey_say(journey) -> str:
    """人话：探路是怎么结束的（`Journey.notes` 里本来就有这句话）。"""
    stop = str(getattr(journey, "stop_reason", "") or "")
    notes = [str(n) for n in (getattr(journey, "notes", None) or [])]
    tail = notes[-1] if notes else ""
    if stop == "model_done":
        head = "探路走完了：agent 自己说讲完了。"
    elif stop == "paused":
        head = "探路被人喊停了。"
    elif stop.startswith("budget"):
        head = "探路没走完：预算到顶了。"
    else:
        head = "探路停得不明不白（%s）。" % (stop or "没说为什么")
    steps = len(getattr(journey, "steps", None) or [])
    return "%s走了 %d 步。%s" % (head, steps, tail)


def _unfinished_note(stop: str, journey) -> str:
    """探路没走完时停下来的**人话**理由（R0：半份账本写不出对的 py）。"""
    if stop == "paused":
        return ("人喊停：在浏览器里就把这次探路停下了，所以没有往下写 py。"
                "已经探到的那部分在账本里（%s）。要接着走就再发起一次。" % _journey_say(journey))
    return ("这次探路没走完（%s），所以**没有**往下写 py：拿半份账本写出来的 py 会看着挺像、"
            "实际有洞 —— 那正是「自信地错」（R0）最贵的形状。这一回的账本留着（走了 %d 步，%s），"
            "人可以看完之后再决定：加预算重探、或者直接说该怎么做。"
            % (_journey_say(journey), len(getattr(journey, "steps", None) or []),
               "；".join(str(n) for n in (getattr(journey, "notes", None) or [])[-2:]) or "没有别的记录"))


def _feedback(state, hints=None) -> dict:
    """回灌给 draft 的东西（违规行 / 诊断证据 / 人说的话）——一处组装，两处消费。

    `hints` 覆盖时以它为准：人在**这一道闸**上说的话属于**这一版**稿
    （`_enter` 刚把它记下来，还没进 state）。
    """
    return {"violations": [dict(v) for v in (state.get("violations") or [])],
            "diagnosis": dict(state["diagnosis"]) if state.get("diagnosis") else None,
            "hints": list(state.get("hints") or []) if hints is None else list(hints)}


def _draft_say(state, feedback: dict) -> str:
    """draft 那道闸上问的话：**先把它为什么被叫回来**说清楚（人话，不是 code）。"""
    version = list(state.get("visits") or []).count("draft") + 1
    head = "接下来写第 %d 版 py（按账本里的「怎么走」填骨架）。" % version
    if feedback["violations"]:
        lines = "；".join("第 %s 行 —— %s" % (v.get("line"), v.get("message"))
                          for v in feedback["violations"])
        head = ("上一版没过契约检查，要重写一版：%s\n（这些是**写动作**上的问题："
                "动页面一律走 cdp 命令，别手拼 JS。规格 §5.2）" % lines)
    elif feedback["diagnosis"]:
        head = "上一版自测没过，要重写一版：%s" % feedback["diagnosis"].get("say", "")
    if feedback["hints"]:
        head += "\n人说过：%s" % "；".join(feedback["hints"])
    return head


def _violation_dict(v) -> dict:
    """一处违规 → 交给下游的朴素 dict（prompt / JSON / 人看的清单都是它）。"""
    get = (lambda k: v.get(k)) if isinstance(v, dict) else (lambda k: getattr(v, k, None))
    return {"line": get("line"), "code": get("code"),
            "message": get("message"), "snippet": get("snippet")}


def _lint_cap_note(bounces: int, violations: list) -> str:
    lines = "；".join("第 %s 行 —— %s" % (v.get("line"), v.get("message")) for v in violations)
    return ("停：这一版 py 被打回 %d 次还是同样的地方不过（%s）。"
            "**反复打回同一处**本身就是「agent 对页面的理解有问题」的信号（§6.3），"
            "再转下去只是烧时间。人接手：看一眼它写的路线，或者直接说该怎么做。" % (bounces, lines))


def _selftest_cap_note(diagnoses: int, report) -> str:
    return ("停：自测挂了 %d 次、修了 %d 轮还是不过，再修下去只是在猜。\n%s\n"
            "人接手：上面是每一遍的结果（哪遍挂、卡在第几步）。"
            % (diagnoses, diagnoses, _report_say(report)))


def _report_say(report) -> str:
    """自测报告的**人话**版本（Task 6 的 `Report.summary()` 就是为这个写的）。"""
    if report is None:
        return "（没有自测报告）"
    summary = getattr(report, "summary", None)
    return summary() if callable(summary) else "（这份报告不会说人话）"


def _diagnosis(report) -> dict:
    """报告 → 证据。**只说报告里真有的东西**：没说「卡在第几步」就不许编一个出来。"""
    per_run = [{"name": getattr(r, "name", None), "status": getattr(r, "status", None),
                "ok": getattr(r, "ok", None), "failed_step": getattr(r, "failed_step", None),
                "note": getattr(r, "note", "")}
               for r in (getattr(report, "runs", None) or ())]
    if report is None:
        return {"run": None, "failed_step": None, "say": "没有自测报告可看。",
                "note": "", "per_run": per_run}

    blocking = list(getattr(report, "blocking", None) or ())
    first = blocking[0] if blocking else None
    if first is None:
        return {"run": None, "failed_step": None,
                "say": "自测没过，但报告里没有哪一遍说清是为什么（这份报告不完整）。",
                "note": "", "per_run": per_run}

    name, label, note = getattr(first, "name", None), getattr(first, "label", "") or "", \
        getattr(first, "note", "") or ""
    if getattr(first, "status", None) == "skipped":
        # 「跳过」不许被读成「卡在哪一步」—— 这一遍压根没跑，没有步号可指（Task 6 的诚实条款）
        return {"run": name, "failed_step": None,
                "say": "%s 这一遍**没跑**（%s）—— 这一类失败这次没验到，不是「卡在哪一步」。"
                       % (label or name, note), "note": note, "per_run": per_run}
    step = getattr(first, "failed_step", None)
    if step is None:
        say = "%s 这一遍挂了：%s" % (label or name, note)
    else:
        say = "%s 这一遍挂了：卡在第 %s 步 —— %s" % (label or name, step, note)
    return {"run": name, "failed_step": step, "say": say, "note": note, "per_run": per_run}


def _delivery_path(state) -> pathlib.Path:
    """交付点：`<out_dir>/<site>.py`（生产就是 `forms/sites/<site>.py`）。"""
    return pathlib.Path(str(state.get("out_dir") or DEFAULT_OUT_DIR)) / ("%s.py" % state["site"])


def _stage_candidate(state) -> pathlib.Path:
    """把这一版落到 `<out_dir>/<site>.candidate.py` —— 自测跑的就是它。

    为什么是**这个位置**：产物开头那句路径算术
    （`sys.path.insert(0, dirname(dirname(abspath(__file__))))`）解析到的是 `out_dir`
    的上一层，`common.py` 得在那儿 —— 所以候选不能扔进 `runtime/` 之类的深目录，
    它必须与将来交付的那份**同层**。
    为什么不直接写交付路径：自测跑的是**没验过**的东西，别让它先出现在
    `forms/sites/<site>.py` 上（那条路径生产会按名字找；C15 的路由表还没条目是另一回事，
    但「没验过的东西不放在交付点上」这条不该靠别人的疏漏来兜）。
    """
    out_dir = pathlib.Path(str(state.get("out_dir") or DEFAULT_OUT_DIR))
    out_dir.mkdir(parents=True, exist_ok=True)
    py = out_dir / ("%s.candidate.py" % state["site"])
    py.write_text(state["src"], encoding="utf-8")
    return py


def _drop_candidate(state) -> None:
    """收摊：候选产物**任何结局都不留**。

    规则一句话：**交付目录里只该有交付物**。停了/挂了的那一版去哪看？源码在 checkpoint
    的 `src` 里，自测的逐遍 trace 在 `runtime/selftest/` 下 —— 而那一版 py 本身属于
    §6.4 说的「人不读代码」的那一类，留着只会在交付目录里多一份说不清来历的 py。
    """
    candidate = state.get("candidate_path")
    if not candidate:
        return
    try:
        pathlib.Path(candidate).unlink()
    except OSError:
        pass                                  # 已经不在了 / 删不掉：不值得让交付失败


def _provenance(state, deps: Deps, *, report) -> dict:
    """§5.3 的元数据块。**图只填它真知道的**，剩下的 `None`（不编内容）。"""
    now = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    return {
        "generated_at": now,
        "generator": GENERATOR,
        "env": state.get("env") or None,
        "platform": state.get("platform") or None,
        "selftest": _selftest_block(report, now),
        "source": {
            "kind": state.get("mode") or MODE_BUILD,
            "evidence": str(state.get("evidence") or state.get("goal") or ""),
            # R-15：这份 py 跑起来用的是**哪一份** `common.py`（出身 + md5 + 抄自哪个 commit）。
            # 环境漂了而没人知道，就是下一轮「昨天还好今天不行」的排查地狱（§5.3）。
            "runtime": dict(deps.provenance()),
        },
    }


def _selftest_block(report, now: Optional[str]) -> Optional[dict]:
    """`PROVENANCE["selftest"]`（§5.3 的形状：runs / passed / at）。

    `verdict` 是加出来的**判据**：`runs=5 passed=4` 读不出「那是允许跳过的第 5 遍」
    还是「挂了」，而图是**按 `report.passed` 走的** —— 它得跟着产物走，
    不然下一个人只看产物就以为「4/5 差不多过了」。跑过的才算 `runs`（跳过的不是「跑了」）。
    """
    if report is None:
        return None
    runs = list(getattr(report, "runs", None) or ())
    ran = [r for r in runs if getattr(r, "status", None) != "skipped"]
    return {"runs": len(ran),
            "passed": len([r for r in ran if getattr(r, "ok", None) is True]),
            "at": now,
            "verdict": bool(getattr(report, "passed", False))}


def _deliver_say(state) -> str:
    """`deliver` 那道闸上问的话 —— **不吹**：自测过了不等于生产会过（§10）。"""
    out_dir = str(state.get("out_dir") or DEFAULT_OUT_DIR)
    return ("自测过了，接下来把它写进站点目录（%s）。"
            "写下去的是刚才自测那一版的**同一份**，只多一块 `PROVENANCE` 环境指纹"
            "（代理国家 / DPR / UA / 视口 / 平台 / 自测结果 / 出身）—— 同一个 URL 在不同"
            "代理国家是**不同的页面**，指纹得跟着产物走（§5.3）。\n"
            "提醒：**工具侧自测通过 ≠ 生产一定过**（规格 §10）；这份 py 也还**不能被生产"
            "调起来**，路由表条目归计划四（C15）。" % out_dir)


def _delivered_note(state, path: pathlib.Path) -> str:
    """交付成功之后那段**人话**（D16：给人看的一句话，不是错误码）。"""
    runs = (_selftest_block(state.get("report"), None) or {}).get("runs")
    return ("写好了：%s\n"
            "自测跑了 %s 遍，判据过了（自测结果也写进产物里了）。\n"
            "两件还没做的事：① 工具侧自测通过 ≠ 生产一定过 —— 自测的浏览器与生产 worker 的"
            "代理出口/指纹/时序不是一套（规格 §10）；② 这份 py 还不能被生产调起来，"
            "要在路由表里登记（C15，计划四）。" % (path, runs))
