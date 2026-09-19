"""Task 8（人在回路）：`/say` / `/stop` / `/again` 三条线 —— 这一份钉的是**语义**。

三件事，一条一条对着计划与设计注：

| 端点 | 它承诺什么 | 这里的用例 |
|---|---|---|
| `/say` | 一句话**去哪了**（排队 / 直达）—— 而且**永远不替人往前走一步** | `…queues_it_and_never_sends_it` / `…still_only_prefilled_at_the_next_gate` / `…while_it_runs…` |
| `/stop` | 设计注 §四.1 那三行矩阵 + R2 的第四行（还没开跑）**各自落在哪** | `…another_node…` / `…while_it_explores…` / `…at_a_gate…` / `…queued_job…` |
| `/again` | 新 job、同一份开场白、**人说过的话带过去**（R5：两个来源都带） | `…starts_a_new_job…` |

⚠️ 两条**这一片特有**的钉子，别的单测钉不住：

- **A1 那个陷阱**（`…do_not_wait_for_a_running_step`）：跑着那一档，`/say` `/stop` `/live`
  **一个字节的 checkpoint 都不读**。写成 `_where_it_stopped(job_id)` 一行，桩图跑得飞快、
  所有别的用例**照样全绿** —— 到真站上就是「按了停，浏览器转圈转到这一步跑完」。
  所以那一条**让 invoke 真的卡住**，再量三个端点的往返。
- **A2 那根采样线**（同一条）：跑着的同一趟里 `/live.stage` 与 `/stop` 的 `where`
  必须是**同一个词**（`Job.running_step`，不是第二次读 checkpoint）。

桩与 `tests/test_service_events.py` / `tests/test_service.py` 里那套**同一形状**
（`FakeGraph` / `_Snap` / `_gate` / `StubWindow`）—— 这一片所有测试新建文件，
不去动那两个正在被别的任务改的文件（计划 P3）。
⚠️ 这一份**不测页面**（`agent/console.html` 一个字节都不动）：那一半归 Task 10。
"""

from __future__ import annotations

import ast
import json
import pathlib
import re
import sys
import threading
import time
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Interrupt

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import graph, service  # noqa: E402

SITE = "example-funnel"
URL = "https://example-funnel.test/quiz"
GOAL = "走到「Thank you」那一页，把报价拿到"
SUCCESS = "Thank you"
WS_URL = "ws://127.0.0.1:9222/devtools/page/ABC"


# ─────────────────────────────── 桩：假图 ───────────────────────────────


@dataclass
class _Snap:
    """`get_state()` 返回的那个东西里，服务只准看这三个字段。"""

    values: dict
    next: tuple = ()
    interrupts: tuple = ()


def _gate(step="intake", say="准备开工：…"):
    return Interrupt(value={"step": step, "say": say, "facts": {}, "can": ["让它继续"]}, id="i-1")


class FakeGraph:
    """照着服务要的协议做的假图：`invoke` / `get_state` / `update_state`。

    `hold(第几次 invoke)` 让测试能**停在那一次 invoke 里面**（不靠抢时序）——
    A1 那条用例要的就是这个：一次真占着写锁的 invoke。
    `raise_on` 让某一次 invoke **抛**（跑挂那一支：跑挂 ≠ 跑成，快照可能还留着上一趟的结论）。
    """

    def __init__(self, *, steps=None, hold=None, raise_on=None):
        self.steps = list(steps or [])
        self.hold = hold or (lambda n: None)
        self.raise_on = list(raise_on or [])
        self.invokes: list = []
        self.state = _Snap(values={}, next=(), interrupts=())

    def invoke(self, payload, config):
        self.invokes.append(payload)
        self.hold(len(self.invokes))
        if len(self.invokes) in self.raise_on:
            raise RuntimeError("窗口连不上了（这一条就是来把这一趟弄挂的）")
        out = (self.steps[min(len(self.invokes) - 1, len(self.steps) - 1)]
               if self.steps else _Snap(values={}))
        self.state = out if isinstance(out, _Snap) else _Snap(values=dict(out))
        return {"__interrupt__": list(self.state.interrupts)} if self.state.interrupts else {}

    def get_state(self, config):
        return self.state

    def update_state(self, config, values, as_node=None):
        self.state = _Snap(values={**self.state.values, **values}, next=(as_node,), interrupts=())
        return {}


class StubWindow:
    """窗口层的替身（`/live` 的「这个窗口」那一块要问它两句）。"""

    def __init__(self, *, alive=True):
        self.alive_ = alive

    def set_viewport(self, width, height):
        pass

    def alive(self):
        return self.alive_


def _brief(tmp_path, **over):
    brief = {"url": URL, "goal": GOAL, "success_text": SUCCESS, "site": SITE,
             "ws_url": WS_URL, "form_file": str(tmp_path / "form.json"),
             "out_dir": str(tmp_path / "forms" / "sites")}
    brief.update(over)
    return brief


@pytest.fixture(autouse=True)
def _runtime_goes_to_tmp(tmp_path, monkeypatch):
    """运行产物一律落 `tmp_path`（计划 Global Constraints：测试不写 `runtime/`）。"""
    monkeypatch.setattr(service.measure, "DEFAULT_ROOT", tmp_path / "runtime" / "explore")


def _client(*, graph_factory, window=None, raise_server_exceptions=True, **kw):
    kw.setdefault("checkpointer", InMemorySaver().with_allowlist(graph.MSGPACK_ALLOWLIST))
    kw.setdefault("capture", lambda ws_url, dest, *, timeout=None: (pathlib.Path(str(dest)).name, ""))
    return TestClient(service.create_app(graph_factory=graph_factory,
                                         window=window or StubWindow(), **kw),
                      raise_server_exceptions=raise_server_exceptions)


def _factory(g):
    return lambda brief, deps: g


def _wait(client, job_id, *, until=("waiting", "done", "failed"), timeout=20.0):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        last = client.get("/job/%s" % job_id).json()
        if last["status"] in until:
            return last
        time.sleep(0.02)
    raise AssertionError("job 没有在 %.0fs 内走到 %s：%r" % (timeout, until, last))


def _live(client, job_id):
    r = client.get("/job/%s/live" % job_id)
    assert r.status_code == 200, r.text
    return r.json()


def _settle(client) -> None:
    """等**队列**空下来（工作线程把它手上那一件做完了）—— 把「靠时序」的断言变成确定性的。

    为什么要这一句（复审 M-3）：`len(g.invokes) == 1` 这类断言要的是「工作线程**此刻**
    手上没有活」，而 `_wait(...)` 只看**状态**、不看队列 —— `say()` / `again()` 把活交下去
    到工作线程接手之间有一条缝，缝里读到的 `invokes` 少一条，断言就**看运气**。
    `_queue.join()` 就是那条缝的正身（`worker` 在 `finally` 里 `task_done`）。
    ⚠️ 只在**确定空得了**的地方用：队列里压着别人时（比如某个 job 正卡在 invoke 里）它会一直等。
    """
    client.app.state.service._queue.join()


def _events(client, job_id):
    return _live(client, job_id)["events"]


def _of_kind(client, job_id, kind):
    return [e for e in _events(client, job_id) if e["kind"] == kind]


def _running_job(svc, *, job_id="job-running", stage="selftest", **over):
    """登记表里放一个**正在跑**的 job（`_view` 那一档不读 checkpoint，所以这就够了）。

    `running_step` 就是 A2 说的那个**采样**：服务自己在进程里记的「这一步在跑哪个节点」。
    """
    job = service.Job(job_id=job_id, brief={"site": SITE, "url": URL, "goal": GOAL,
                                            "success_text": SUCCESS},
                      status=service.RUNNING, created_at="2026-09-19T00:00:00+08:00", **over)
    job.running_step = stage
    job.graph = FakeGraph(steps=[_Snap(values={"site": SITE, "visits": ["intake"]})])
    svc._jobs[job_id] = job
    return job


# ══════════════ 判据是纯函数（R1：两个分支都测，实参才是会变的那个）══════════════


def test_the_route_decision_and_the_input_mode_are_pure_functions_with_both_branches():
    """一句话怎么送到它手上：一个**纯函数**决定（R1）—— 两个分支都在这里过一遍。

    为什么要有这个函数而不是直接写在端点里（R1 的原话）：Task 9 落地时改的是**实参**
    （「直达那条通道接上了没有」），不是判据 —— 判据今天就得是对的。
    """
    # 直达：只可能在「它正在探路里跑」+「那条通道接上了」这两件事同时成立时
    assert service.say_route(service.RUNNING, "explore", steer=True) == "delivered"
    # 探路里跑，但通道没接上（**今天就是这一支**）⇒ 排队，不是直达
    assert service.say_route(service.RUNNING, "explore", steer=False) == "queued"
    # 别的节点里跑：没有「下一轮」这回事 ⇒ 排队
    assert service.say_route(service.RUNNING, "selftest", steer=True) == "queued"
    # 闸上：这一句进输入框（页面自己那一行走 `/reply`）—— 不是直达
    assert service.say_route(service.WAITING, "intake", steer=True) == "queued"
    # 还没开跑 / 到头了：排队（到头了那两档由端点先拦掉，函数本身说的是「不是直达」）
    assert service.say_route(service.QUEUED, "", steer=True) == "queued"
    assert service.say_route(service.DONE, "deliver", steer=True) == "queued"

    # 输入框的语义（页面的三档）：`gate` **当且仅当**在等人（A4）
    assert service.input_mode(service.WAITING, "intake", steer=True) == "gate"
    assert service.input_mode(service.WAITING, "intake", steer=False) == "gate"
    assert service.input_mode(service.RUNNING, "explore", steer=True) == "steer"
    assert service.input_mode(service.RUNNING, "explore", steer=False) == "queue"
    assert service.input_mode(service.RUNNING, "selftest", steer=True) == "queue"
    assert service.input_mode(service.QUEUED, "", steer=True) == "queue"
    assert service.input_mode(service.DONE, "deliver", steer=True) == "queue"
    assert service.input_mode(service.FAILED, "draft", steer=True) == "queue"


def test_the_steer_channel_is_not_wired_yet_so_no_path_returns_delivered(tmp_path):
    """R1：**今天任何路径都不许回 `delivered`** —— 「一半的插话比不做更坏」。

    这一条钉的是那个**实参**（Task 9 把它翻成 True 时要连通道一起接上；它翻了这条就该改）。
    """
    assert service.STEER_WIRED is False
    g = FakeGraph(steps=[_Snap(values={"site": SITE, "visits": ["intake"]})])
    client = _client(graph_factory=_factory(g))
    svc = client.app.state.service
    _running_job(svc, stage="explore")          # 正在探路里跑 —— 那正是 steer 唯一有用的一档

    r = client.post("/job/job-running/say", json={"text": "不是那个按钮，是下面那个"})
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["delivered"] is False, body
    assert body["queued"] is True, body


def test_say_while_it_explores_goes_straight_in_instead_of_queuing(tmp_path, monkeypatch):
    """探路里跑着的时候说一句 → 回 `delivered`（计划 Task 8 Step 1 里「之后回 delivered」那半）。

    ⚠️ 这一条**换掉了** Task 8 立的 `test_a_steer_switch_without_the_channel_refuses_instead_of_pretending`
    （那条钉的是「开关开了、通道没接上 ⇒ 503」）。Task 9 把通道接上了 ⇒ **那个状态从这天起
    不存在**：开关与通道是同一件事（`STEER_WIRED` 一翻，`_explore_for` 就把那根线交下去、
    `_steer_cb` 就在每一次模型调用之前把它喂给它）。计划正文写死了这一条的两个时态
    （Task 8 Step 1：「`running` 且 `stage=="explore"` 时 `/say` → **Task 9 之前回 `queued`、
    之后回 `delivered`**」）—— 前一种在 `test_the_steer_channel_is_not_wired_yet_…` 里。

    ⚠️ 判据**一个字没放宽**：`delivered` 说的是**它会走哪条路**，不是「它已经看到了」——
    这句话此刻只是排在队里等着下一轮交出去（`input.queued` 里看得到它、它自己的
    `delivered` 还是 False，时间线上那条「已经交给它了」**还没有**）。
    真正的兑现是 `steer_landed`（Task 9 的用例在 `tests/test_steer.py`）。
    """
    monkeypatch.setattr(service, "STEER_WIRED", True)
    g = FakeGraph(steps=[_Snap(values={"site": SITE, "visits": ["intake"]})])
    client = _client(graph_factory=_factory(g))
    _running_job(client.app.state.service, stage="explore")

    r = client.post("/job/job-running/say", json={"text": "先点 cookie 那个同意"})
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["delivered"] is True and body["queued"] is False, body
    assert "直达" in body["say"], body["say"]
    assert "不会自动发" not in body["say"], (
        "「按一下才送」说的是排队那条路 —— 摆在直达这条路上就是假话：%r" % body["say"])
    live = _live(client, "job-running")
    # ① 它进了队（等着下一轮交出去）—— 而**还没**到它手上
    assert [x["text"] for x in live["input"]["queued"]] == ["先点 cookie 那个同意"]
    assert live["input"]["queued"][0]["delivered"] is False, "还没交出去 ⇒ 不许说送到了"
    # ② 人自己那句话 + 它会怎么到它手上，都在时间线上（`who="you"`）
    said = [e for e in live["events"] if e["kind"] == "human_said"]
    assert said and said[-1]["who"] == "you", said
    assert said[-1]["data"]["route"] == "delivered", said[-1]
    # ③ **此刻还没有**「已经交给它了」：人刚说完、那一轮还没来（R6 的那个时刻）
    assert not [e for e in live["events"] if e["kind"] == "steer_landed"], live["events"]
    assert live["input"]["mode"] == "steer", "开关说通道接上了 ⇒ 页面那一行也该是「直达」"


# ══════════════════════════ `/say`：一句话去哪了 ══════════════════════════


def test_saying_something_while_it_waits_queues_it_and_never_sends_it(tmp_path):
    """`waiting` 时 `/say` → 排队 + 时间线一条 + `input.queued` 里有它。

    ⚠️ 最要紧的是**它没有替人往前走一步**（Global Constraints：服务可以替人「停」，
    绝不替人「走」）。所以这里的判据**不是**「没有新 reply」这种否定 —— 那是钉不住的
    （R7 的原话）：要钉**闸还在**这个**肯定**，加上**图一次都没有被推**（`invokes` 没长）。
    """
    g = FakeGraph(steps=[_Snap(values={"site": SITE, "ws_url": WS_URL, "visits": ["intake"]},
                               interrupts=(_gate("intake"),))])
    client = _client(graph_factory=_factory(g))
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    _wait(client, job_id)

    r = client.post("/job/%s/say" % job_id, json={"text": "不是那个按钮，是下面那个"})
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["queued"] is True and body["delivered"] is False, body
    assert body["n"] == 1, "队列里现在有几句 —— 这一格要说得出：%r" % body
    assert "不会自动发" in body["say"], body["say"]

    # M-3：**先把队列等空**再读状态与计数 —— 上一步要是偷偷推了一下（这一条要抓的就是它），
    # 那一刻它已经跑完了，下面的断言读的是一个**落定**的世界，不靠运气。
    _settle(client)
    live = _live(client, job_id)
    # ① 肯定的那一半：**闸还在**（状态没变、闸没走、还是同一道）
    assert live["status"] == "waiting", live["status"]
    assert live["gate"] is not None and live["gate"]["step"] == "intake", live["gate"]
    # ② 它排队了，而且**只在队列里**（没送出去）
    assert [x["text"] for x in live["input"]["queued"]] == ["不是那个按钮，是下面那个"]
    assert live["input"]["queued"][0]["delivered"] is False
    assert live["input"]["draft_note"] == "不是那个按钮，是下面那个", \
        "到闸上就进输入框（页面照这一格预填，**不自动发**）"
    assert live["input"]["mode"] == "gate", "停在闸上 ⇒ 这一行就是这道闸的回话（A4）"
    # ③ 图**一次都没有被推**：这一句话没有变成一个动作
    #    ⚠️ 再等一次队列（M-3）：上面那几次 `/live` 要是**自己**偷偷推了一下
    #    （这一条也要抓它），这一步之后它已经落地 —— 计数于是是确定性的，不是看运气。
    _settle(client)
    assert len(g.invokes) == 1, "这一句被自动发出去了（只有人家按的那一下才许推图）"
    # ④ 时间线上有它，`who="you"`（页面靠它决定气泡），而且记的是**留下**的那段
    said = _of_kind(client, job_id, "human_said")
    assert len(said) == 1, said
    assert said[0]["who"] == "you"
    assert said[0]["data"]["text"] == "不是那个按钮，是下面那个"
    assert "不是那个按钮" in said[0]["say"], said[0]["say"]


def test_the_queued_words_are_still_only_prefilled_at_the_next_gate(tmp_path):
    """排队的那些话到**下一道闸**进输入框 —— 而且仍然**不自动发**。

    两件事实一起钉：① 它跟着 job 走（跨过了一次「继续」）；② 到了闸上也只是**预填**。
    """
    g = FakeGraph(steps=[
        _Snap(values={"site": SITE, "ws_url": WS_URL, "visits": ["intake"]},
              interrupts=(_gate("intake"),)),
        _Snap(values={"site": SITE, "ws_url": WS_URL, "visits": ["intake", "explore"]},
              interrupts=(_gate("draft"),)),
    ])
    client = _client(graph_factory=_factory(g))
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    _wait(client, job_id)

    client.post("/job/%s/say" % job_id, json={"text": "表格里的邮编要真能收到信"})
    # 人自己在闸上按了「继续」—— 推图的是**他**，不是那一句话
    client.post("/job/%s/reply" % job_id, json={"action": "continue"})
    _wait(client, job_id)

    live = _live(client, job_id)
    assert live["status"] == "waiting" and live["gate"]["step"] == "draft", live
    assert live["input"]["draft_note"] == "表格里的邮编要真能收到信", \
        "排队的那句话**到下一道闸**要进输入框"
    assert [x["text"] for x in live["input"]["queued"]] == ["表格里的邮编要真能收到信"]
    _settle(client)                                  # M-3：别靠时序
    assert len(g.invokes) == 2, "被推了两次：那一次「继续」+ 别的什么（这一句不该自己发）"


def test_a_word_that_was_sent_at_the_gate_leaves_the_queue(tmp_path):
    """送下去过的话要**离开队列**（修复轮 1 / I-2 —— 设计注 §4.2：送到之后那个标记消失）。

    ⚠️ 这条断言今天是**新的**：原先没有任何用例问过「送出去了它还在不在队里」，
    所以 `delivered` 从来没人翻、`draft_note` 永远预填一句已经送下去的话 —— **连红灯都没有**。
    """
    g = FakeGraph(steps=[
        _Snap(values={"site": SITE, "ws_url": WS_URL, "visits": ["intake"]},
              interrupts=(_gate("intake"),)),
        _Snap(values={"site": SITE, "ws_url": WS_URL, "visits": ["intake", "explore"]},
              interrupts=(_gate("draft"),)),
    ])
    client = _client(graph_factory=_factory(g))
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    _wait(client, job_id)
    text = "不是那个按钮，是下面那个"

    client.post("/job/%s/say" % job_id, json={"text": text})
    assert [x["text"] for x in _live(client, job_id)["input"]["queued"]] == [text], "先说一句：它在队里"
    # 人在闸上按下那个按钮 —— 页面在 `gate` 那一档走的就是这一跳（`note` 是预填进去的那句）
    client.post("/job/%s/reply" % job_id, json={"action": "say", "note": text})
    _wait(client, job_id)

    live = _live(client, job_id)
    assert live["gate"]["step"] == "draft", live
    assert live["input"]["queued"] == [], "送下去了就该离开队列（「还没送到」不许永远挂着）"
    assert live["input"]["draft_note"] == "", \
        "它不许在下一道闸**再预填一次**已经送下去的话（那是在请人再按一次）"


def test_a_word_the_human_replaced_is_kept_but_never_offered_again(tmp_path):
    """人把预填那句**改了**再按 ⇒ 原来那句**不再摆到他面前**，但**一个字都不丢**（修复轮 2 / NEW-1）。

    为什么这一条要紧（复审与控制者的改判）：页面**不渲染** `input.queued`（`grep` = 0），
    所以运营唯一能遇到那句「不再摆给他的那句话」的方式，就是**下一道闸又被预填** ——
    而页面在闸上会把框里的字**原样**当他的话送下去（`console.html:893-901`）。
    那与 I-2 要治的形状是同一个（预填一句不该再摆出来的话 = 请人再按一次），
    只差在 I-2 那句是「已送出」、这句是「不再摆给他」。

    判据拆成两件事（**不是**把 `delivered` 翻掉 —— 那一句一个字都没到过它手上）：
      · `superseded`：**观察到的事实** —— 服务本来要摆给他的就是这一句，而他按下去的是**别的**；
      · 于是 `draft_note` / `input.queued` 都不再列它，而**时间线上要说出来**这件事。
    ⚠️ 说法这一格在修复轮 4 收准了（F3）：不写「摆在他面前过」——**那是假话**
    （页面预填不覆盖正在打字的框，他可能压根没看见过），详见 `agent/service.py` 里
    `HUMAN_SAID_SUPERSEDED_SAY` 上面那段。
    """
    g = FakeGraph(steps=[
        _Snap(values={"site": SITE, "visits": ["intake"]}, interrupts=(_gate("intake"),)),
        _Snap(values={"site": SITE, "visits": ["intake", "explore"]}, interrupts=(_gate("draft"),)),
        _Snap(values={"site": SITE, "visits": ["intake", "explore", "draft"],
                      "hints": ["算了，先点 cookie 同意"],
                      "end_reason": "human_stop", "end_note": "人把这一趟停了。"}),
    ])
    client = _client(graph_factory=_factory(g))
    svc = client.app.state.service
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    _wait(client, job_id)

    client.post("/job/%s/say" % job_id, json={"text": "不是那个按钮"})
    # 人在闸上把它**改了**再按（页面在 `gate` 那一档发的就是这一跳）
    client.post("/job/%s/reply" % job_id, json={"action": "say", "note": "算了，先点 cookie 同意"})
    _wait(client, job_id)

    live = _live(client, job_id)
    assert live["input"]["draft_note"] == "", \
        "不再摆给他的那句不许再进输入框（进了就等着他再按一次）：%r" % live["input"]["draft_note"]
    assert live["input"]["queued"] == [], \
        "它也不在「还在等」那一列里（那一列是给「等着送」的话的）：%r" % live["input"]["queued"]
    # 「没有静默的路径」：这件事必须有一条人说得出的话（在他的那一条气泡里）
    # ⚠️ 说法在修复轮 3 / NEW-R2 收准了：**不说「你改口了」**（服务不知道他屏幕上画的是哪一句）——
    # 只说它真知道的那件事（「我本来要摆给你的是这一句」）。
    said = [e["say"] for e in _of_kind(client, job_id, "human_said")]
    # ⚠️ 认**带那句话的那一条**（「说一句」那条事件里也有人说的原文 —— 只按文字筛会挑错那条）
    hit = [s for s in said if "我本来要摆给你的那句" in s and "不是那个按钮" in s]
    assert hit, "这件事一个字都没说：%r" % said
    assert "不摆了" in hit[0] and "重新来一遍" in hit[0], hit[0]
    assert not any("你改口了" in s for s in said), "整条时间线都不许替他说这个动作：%r" % said

    # 但它**没有被丢掉**（R5 的「你说的话我记着」）：那条仍然是「没送到」，`/again` 照带
    with svc._jobs[job_id].lock:
        entry = [x for x in svc._jobs[job_id].inbox if x["text"] == "不是那个按钮"][0]
    assert entry["delivered"] is False, "它一个字都没到过它手上，不许翻成送到了"
    assert entry["superseded"] is True, entry
    # 让它走到头（重来要在一个终态的 job 上按）
    client.post("/job/%s/reply" % job_id, json={"action": "continue"})
    _wait(client, job_id)
    r = client.post("/job/%s/again" % job_id, json={})
    assert r.status_code == 202, r.text
    _settle(client)
    assert g.invokes[-1]["hints"] == ["算了，先点 cookie 同意", "不是那个按钮"], \
        "不再摆给他 ≠ 这句话就当没说过 —— 重来那一趟要带上它：%r" % (g.invokes[-1].get("hints"),)


def test_the_superseded_line_does_not_claim_he_changed_his_mind(tmp_path):
    """那句人话只许说服务**真知道**的事（修复轮 3 / NEW-R2）。

    失效形状（复审探针 B）：他说 A（**服务本来要摆给他的就是 A**），又说 C（他还没看见 —— 页面预填
    **不覆盖正在打字的框**，`console.html:661`），然后他按下去的是**别的**。
    服务这一侧「最后一条等着送的」是 C ⇒ 被标 `superseded` 的是 **C**，
    而时间线上写着「**你改口了**：C」—— **一句他从没做过的动作**（他根本没看见过 C）。

    ⚠️ 机制**不改**（服务不知道页面画了什么，根治要页面报「我看见的是哪一句」= Task 10）：
    这一条钉的是**那句话的说法**必须对任何一条都成立（用服务真知道的那件事说）。
    """
    g = FakeGraph(steps=[
        _Snap(values={"site": SITE, "visits": ["intake"]}, interrupts=(_gate("intake"),)),
        _Snap(values={"site": SITE, "visits": ["intake", "explore"]}, interrupts=(_gate("draft"),)),
    ])
    client = _client(graph_factory=_factory(g))
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    _wait(client, job_id)

    client.post("/job/%s/say" % job_id, json={"text": "A：他看见过的那句"})
    client.post("/job/%s/say" % job_id, json={"text": "C：他没看见过的那句"})
    client.post("/job/%s/reply" % job_id, json={"action": "say", "note": "他打的是别的"})
    _wait(client, job_id)

    said = [e["say"] for e in _of_kind(client, job_id, "human_said")]
    hit = [s for s in said if "我本来要摆给你的那句" in s and "C：他没看见过的那句" in s]
    assert hit, "这件事一个字都没说：%r" % said
    assert not any("你改口了" in s for s in said), \
        "替他说了一个他没做过的动作（他没看见过那一句）：%r" % said
    # 机制这一半也钉住（写下来的是**行为**，不是「应该」）：
    # 被标的是**服务侧最后一条**（C），而 A 留在队里 —— Task 10 若让页面报「我看见的是哪一句」，
    # 这两句会跟着改（那正是这条用例存在的意义：改的时候有人会看见它）。
    assert [x["text"] for x in _live(client, job_id)["input"]["queued"]] == ["A：他看见过的那句"]


def test_the_queue_length_it_reports_is_the_one_that_is_really_waiting(tmp_path):
    """`/say` 的 `n` 与 `/live.input.queued` **同一个口径**（修复轮 3 / NEW-R1）。

    失效形状（复审探针 C）：有一句不再摆给他之后再新说一句 ⇒ 人话说「队列里现在排着 **2** 句」，
    而同一刻 `input.queued` 只有 1 条 —— 那句话对一条**再也不会预填**的话也说「会进输入框」，
    而且同一个名字（`n` / `queued`）指向两个事实（Task 9 拿 `n` 会与页面/`live` 对不上）。
    """
    g = FakeGraph(steps=[
        _Snap(values={"site": SITE, "visits": ["intake"]}, interrupts=(_gate("intake"),)),
        _Snap(values={"site": SITE, "visits": ["intake", "explore"]}, interrupts=(_gate("draft"),)),
    ])
    client = _client(graph_factory=_factory(g))
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    _wait(client, job_id)

    client.post("/job/%s/say" % job_id, json={"text": "A：会被改口的那句"})
    client.post("/job/%s/reply" % job_id, json={"action": "say", "note": "他打的是别的"})
    _wait(client, job_id)
    r = client.post("/job/%s/say" % job_id, json={"text": "D：新说的一句"})
    assert r.status_code == 202, r.text

    live = _live(client, job_id)
    assert r.json()["n"] == len(live["input"]["queued"]) == 1, \
        ("`n`（/say）与 `queued`（/live）对不上：%r vs %r" % (r.json()["n"], live["input"]["queued"]))
    assert "排着 2 句" not in r.json()["say"], r.json()["say"]
    assert [x["text"] for x in live["input"]["queued"]] == ["D：新说的一句"]


def test_the_stop_promise_only_mentions_words_that_are_really_waiting(tmp_path):
    """`/stop` 那句「你说的话排好了」只在**真还有话等着送**时才说（修复轮 3 / NEW-N）。

    两个方向都钉（复审量化过：全仓原先**没有一条**断言碰过 `STOP_WORDS_HELD_SAY`）：
      ① 真有一条等着 ⇒ 那句话里有「已经排好了」；
      ② 那条不再摆给他了 ⇒ 那句话里**没有**（再说一次「会进输入框」就是假话）。
    """
    g = FakeGraph(steps=[_Snap(values={"site": SITE, "visits": ["intake"]})])
    client = _client(graph_factory=_factory(g))
    svc = client.app.state.service
    job = _running_job(svc, stage="selftest")
    with job.lock:
        job.inbox.append({"text": "还等着的那句", "at": "2026-09-19T00:00:00+08:00",
                          "delivered": False, "superseded": False})
    said = client.post("/job/job-running/stop", json={}).json()["stop"]["will_stop_at"]
    assert service.STOP_WORDS_HELD_SAY in said, "真有一条等着，那句话要说出来：%r" % said

    # 它不再摆给他了（他按下去的是别的 —— 服务**观察到的**那件事）⇒ 不许再说「会进输入框」
    with job.lock:
        job.inbox[0]["superseded"] = True
    said = client.post("/job/job-running/stop", json={}).json()["stop"]["will_stop_at"]
    assert service.STOP_WORDS_HELD_SAY not in said, \
        "那句已经不会再进输入框了，说「已经排好了」是假话：%r" % said


def test_the_same_word_said_twice_is_sent_and_carried_once(tmp_path):
    """同一句话说了两遍、按一次 —— **两遍都算送到**，队列不留半条（修复轮 2 / NEW-1 同族）。

    复审量到的那一笔：判据原先只翻**第一条**同文的 ⇒ 剩下那条下一道闸**又被预填**，
    而 `/again` 把「说了一遍」变成 hints 里的两条。送下去的话就一句：同文的那几条一起翻。
    """
    g = FakeGraph(steps=[
        _Snap(values={"site": SITE, "visits": ["intake"]}, interrupts=(_gate("intake"),)),
        _Snap(values={"site": SITE, "visits": ["intake", "explore"], "hints": ["先点 cookie 同意"],
                      "end_reason": "human_stop", "end_note": "人把这一趟停了。"}),
    ])
    client = _client(graph_factory=_factory(g))
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    _wait(client, job_id)

    for _ in range(2):
        client.post("/job/%s/say" % job_id, json={"text": "先点 cookie 同意"})
    assert [x["text"] for x in _live(client, job_id)["input"]["queued"]] == ["先点 cookie 同意"] * 2
    client.post("/job/%s/reply" % job_id, json={"action": "say", "note": "先点 cookie 同意"})
    _wait(client, job_id)

    live = _live(client, job_id)
    assert live["input"]["queued"] == [], "送下去了，两条都该走：%r" % live["input"]["queued"]
    assert live["input"]["draft_note"] == "", live["input"]

    r = client.post("/job/%s/again" % job_id, json={})
    assert r.status_code == 202, r.text
    _settle(client)
    assert g.invokes[-1]["hints"] == ["先点 cookie 同意"], \
        "他说了两遍同一句话，带过去的不许变成两条：%r" % (g.invokes[-1].get("hints"),)


def test_again_does_not_carry_a_word_that_was_already_sent(tmp_path):
    """送下去过的话**不许在重来的那一趟里出现两遍**（修复轮 1 / I-2 的第 2 条后果）。

    R5「两个来源都带」没有错，错的是**其中一个来源是过期的**：话在闸上送下去之后
    既进了 checkpoint 的 `hints`，又还赖在 `Job.inbox` 里 ⇒ `/again` 带两遍，
    而把一个纠正说成两句，正是 `/again` 那个承诺（「你说的话我记着」）最不该出的错。
    """
    text = "不是那个按钮，是下面那个"
    g = FakeGraph(steps=[
        _Snap(values={"site": SITE, "visits": ["intake"]}, interrupts=(_gate("intake"),)),
        # 这一趟到头了：checkpoint 里那句**已经送下去过**的话就在 `hints` 里（真图会这么写）
        _Snap(values={"site": SITE, "visits": ["intake", "explore"], "hints": [text],
                      "end_reason": "human_stop", "end_note": "人把这一趟停了。"}),
    ])
    client = _client(graph_factory=_factory(g))
    svc = client.app.state.service
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    _wait(client, job_id)

    client.post("/job/%s/say" % job_id, json={"text": text})
    client.post("/job/%s/reply" % job_id, json={"action": "say", "note": text})
    _wait(client, job_id)

    r = client.post("/job/%s/again" % job_id, json={})
    assert r.status_code == 202, r.text
    _settle(client)
    assert g.invokes[-1]["hints"] == [text], \
        "同一句话被带了两遍（`inbox` 那份是过期的）：%r" % (g.invokes[-1].get("hints"),)
    assert svc._jobs[r.json()["job_id"]].brief["hints"] == [text]


def test_saying_something_while_it_runs_queues_it_and_the_input_stays_queue_mode(tmp_path):
    """`running` 且 `stage=="explore"` 时 `/say` → **今天回 `queued`**（R1：Task 9 之前）。

    A4 的另一半也在这儿：`input.mode` 今天只能是 `gate` / `queue` 两档 ——
    探路里跑**还没有**「直达」这条通道，所以页面那一行说的是「排队」。
    """
    g = FakeGraph(steps=[_Snap(values={"site": SITE, "visits": ["intake"]})])
    client = _client(graph_factory=_factory(g))
    svc = client.app.state.service
    _running_job(svc, stage="explore")

    r = client.post("/job/job-running/say", json={"text": "先点 cookie 那个同意"})
    assert r.status_code == 202, r.text
    assert r.json()["queued"] is True, r.json()

    live = _live(client, "job-running")
    assert live["input"]["mode"] == "queue", live["input"]
    assert [x["text"] for x in live["input"]["queued"]] == ["先点 cookie 那个同意"]
    assert "不会自动发" in r.json()["say"], r.json()["say"]


def test_the_input_mode_is_gate_exactly_when_it_is_waiting(tmp_path):
    """**A4**：`mode == "gate"` **当且仅当** `status == "waiting"`。

    为什么单钉这一条：页面拿 `mode` 与 `status` 这两格**对不上**就显示一句警告
    （`console.html:680`）—— 而恒 `queue` 的骨架让**每一道闸上**都在显示那句话。
    这一条是那个假警告的正身。
    """
    g = FakeGraph(steps=[_Snap(values={"site": SITE, "ws_url": WS_URL, "visits": ["intake"]},
                               interrupts=(_gate("intake"),))])
    client = _client(graph_factory=_factory(g))
    svc = client.app.state.service
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    _wait(client, job_id)
    assert _live(client, job_id)["input"]["mode"] == "gate", "停在闸上 = 闸上的回话"

    for status, stage in ((service.QUEUED, ""), (service.RUNNING, "explore"),
                          (service.RUNNING, "selftest"), (service.DONE, "deliver"),
                          (service.FAILED, "draft")):
        other = service.Job(job_id="job-%s" % status, brief={"site": SITE},
                            status=status, created_at="2026-09-19T00:00:00+08:00")
        other.running_step = stage
        other.graph = FakeGraph(steps=[_Snap(values={"site": SITE, "visits": ["intake"],
                                                     "end_reason": "human_stop",
                                                     "end_note": "人喊停。"})])
        svc._jobs[other.job_id] = other
        live = _live(client, other.job_id)
        assert live["status"] == status, live
        assert (live["input"]["mode"] == "gate") == (status == service.WAITING), \
            "状态是 %s，输入语义却是 %r（页面会一直报「这两格对不上」）" % (status, live["input"])


def test_a_queued_job_is_not_told_that_it_is_running(tmp_path):
    """**I-1**：排队的那一趟不许被人话那格说成「它现在**正在跑**」。

    失效形状（复审给的那条）：A 在跑（写锁被它占着），B 交上去排在队里 —— B 的 `/live`
    拿不到写锁 ⇒ 进那句人话。**同一屏** `status` 说「排队等窗口」、`note` 说「正在跑」，
    两句对着干，正是页面那句「服务这两格对不上」要防的形状（A4 治的是同一族的病）。
    """
    inside, let_go = threading.Event(), threading.Event()

    def hold(n):
        if n == 1:
            inside.set()
            let_go.wait(10)

    a = FakeGraph(steps=[_Snap(values={"site": SITE, "visits": ["intake"]})], hold=hold)
    client = _client(graph_factory=_factory(a))
    a_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    assert inside.wait(10), "A 没有进到 invoke 里"
    try:
        b_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
        live_b = _live(client, b_id)
        assert live_b["status"] == "queued", live_b["status"]
        assert "排队" in live_b["note"], "排队那一档要说「排队」：%r" % live_b["note"]
        assert "正在跑" not in live_b["note"], \
            "同一屏两格打架（status 说排队、note 说正在跑）：%r" % live_b["note"]
        # NEW-6（修复轮 2）：单飞只保证「前面还有活」，**不保证那活在用浏览器**
        # （前面那趟可能正在 `draft` / `lint` —— 那两个节点一个字节都不碰 cdp）。
        # 说一个自己不知道的**原因**，就是编话。
        assert "浏览器" not in live_b["note"], \
            "排队那句在编一个原因（前面那趟不一定在用浏览器）：%r" % live_b["note"]

        live_a = _live(client, a_id)
        assert live_a["status"] == "running", live_a["status"]
        assert "正在跑" in live_a["note"], "跑着那一档那句话还得在：%r" % live_a["note"]
    finally:
        let_go.set()
    _wait(client, a_id)


def test_a_crashed_run_does_not_paste_a_stale_ledger_note(tmp_path):
    """跑挂那一支**不许**贴「账本不完整」（修复轮 1 / M-1）。

    同一个函数上面几行刚因为「快照里可能还留着上一趟那道闸」把 `at_gate` 在跑挂那一支
    强制成 `False`，那就**不许**再照信**同一份快照**的 `end_reason` —— 它同样可能是上一趟
    留下的。**同一份数据两套信任口径**，正是这个仓库的老病之一。

    ⚠️ **射程（别读大）**：这一条钉的是**判据**（那个 `paused` 是**手工摆进** checkpoint 的）。
    复审和我都**没能**用服务自己的路构造出一条真会带着过期 `end_reason` 跑挂的路
    （`reopen` 的 patch 会把它清掉）—— 摆在这儿是因为「同一份快照在同一函数里
    要么都信、要么都不信」这条性质该死，**不是**因为这条场景今天可达。
    """
    clean = _Snap(values={"site": SITE, "visits": ["intake"]})   # 开跑前：干净
    stale = _Snap(values={"site": SITE, "visits": ["intake", "explore"],
                          "end_reason": "paused",             # ← 上一趟留下的结论
                          "end_note": "上一趟：人喊停，探路收住了。"},
                  interrupts=(_gate("deliver"),))             # ← 上一趟那道闸也还在（NEW-2）
    g = FakeGraph(steps=[clean], raise_on=[1],
                  # 这一趟跑着的时候，checkpoint 换成了上一趟那份（跑挂之后服务读到的就是它）
                  hold=lambda n: setattr(g, "state", stale) if n == 1 else None)
    g.state = clean
    client = _client(graph_factory=_factory(g))
    svc = client.app.state.service
    job = _running_job(svc, stage="explore")
    job.graph = g
    assert client.post("/job/job-running/stop", json={}).status_code == 200

    svc._advance(job, None)                   # 这一次 invoke 抛了 —— 跑挂那一支
    assert job.status == service.FAILED, job.status

    landed = _of_kind(client, "job-running", "stop_landed")
    assert len(landed) == 1, landed
    assert "有结果了" in landed[0]["say"], landed[0]["say"]
    assert "账本不完整" not in landed[0]["say"], \
        "跑挂那一支照抄了上一趟的 `end_reason`（同一份快照两套信任口径）：%r" % landed[0]["say"]
    # NEW-2（修复轮 2）：**节点名**也不许从那同一份快照里来 —— 上面刚说过不信它的闸，
    # 紧接着又用它的 `interrupts` 报节点名，就是「同一格两套口径」。
    # 跑挂这一支改用服务自己在安全时刻采的那次样（`running_step`）：那也正是
    # `/stop` 按下去时告诉他的那个词（A2），两处不许打架。
    assert "把它写进站点目录" not in landed[0]["say"], \
        "节点名从**那份快照的闸**里来的（上面刚说过不信它）：%r" % landed[0]["say"]
    assert "开工前的确认" in landed[0]["say"], \
        "跑挂那一支的节点名该是这一趟在跑的那个（`running_step`）：%r" % landed[0]["say"]
    assert landed[0]["data"]["where"] == "intake", landed[0]["data"]


def test_the_queue_only_flips_after_the_sentence_really_went_out(tmp_path, monkeypatch):
    """「已送到」那一翻要在**真的交下去之后**（修复轮 2：这个位置以前只活在注释里）。

    怎么钉：让 `_note_human_said` 那条 narrate 抛（它在 `_submit` **之前**）—— `/reply` 会 500
    （时间线坏了是编程错误，本来就该响），而**队里那一条一个字都不许翻**：
    翻了就是把一句**没送到**的话记成送到了（I-2 要治的正是这种假账）。
    """
    text = "不是那个按钮"
    g = FakeGraph(steps=[_Snap(values={"site": SITE, "visits": ["intake"]},
                               interrupts=(_gate("intake"),))])
    client = _client(graph_factory=_factory(g), raise_server_exceptions=False)
    svc = client.app.state.service
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    _wait(client, job_id)
    client.post("/job/%s/say" % job_id, json={"text": text})

    real = svc.narrate

    def broken(job, kind, say, **kw):
        if kind == "human_said":
            raise RuntimeError("时间线坏了（这一条就是来让它抛的）")
        return real(job, kind, say, **kw)

    monkeypatch.setattr(svc, "narrate", broken)
    r = client.post("/job/%s/reply" % job_id, json={"action": "say", "note": text})
    assert r.status_code == 500, r.text

    with svc._jobs[job_id].lock:
        entry = svc._jobs[job_id].inbox[0]
        assert entry["delivered"] is False, "没送出去却翻了「已送到」"
        assert entry["superseded"] is False, entry
    _settle(client)
    assert len(g.invokes) == 1, "那一句并没有交下去（推图的次数不该涨）"


def test_settle_really_waits_for_the_work_to_be_done(tmp_path):
    """`_settle` 的确定性靠一条性质：**`task_done()` 在工作之后**（修复轮 2 / NEW-5）。

    为什么这条性质要有人钉：`_settle` 一旦不再等「工作真的做完」，那几条
    `len(invokes) == N` 的断言就**悄悄退回靠时序**（M-3 刚治好的那条缝又开了），
    而**不会有任何红灯**。判据是行为：工作还卡在 invoke 里时，`join()` **不许**回来。
    """
    inside, let_go = threading.Event(), threading.Event()

    def hold(n):
        if n == 1:
            inside.set()
            let_go.wait(10)

    client = _client(graph_factory=_factory(
        FakeGraph(steps=[_Snap(values={"site": SITE, "visits": ["intake"]})], hold=hold)))
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    assert inside.wait(10), "这一趟没有进到 invoke 里"

    joined = threading.Event()

    def settle():
        client.app.state.service._queue.join()
        joined.set()

    t = threading.Thread(target=settle, daemon=True)
    t.start()
    try:
        assert not joined.wait(0.5), \
            "工作还卡在 invoke 里，`join()` 就回来了 —— `task_done` 跑到工作前面去了（`_settle` 于是不再确定）"
    finally:
        let_go.set()
    assert joined.wait(10), "放开那一次 invoke 之后 `join()` 该回来"
    _wait(client, job_id)


def test_the_live_view_fills_the_input_and_the_stop_the_page_reads(tmp_path):
    """`/live` 那两格的**形状**（brief 钉的）：`input` 三个键、`stop` 四个键。"""
    g = FakeGraph(steps=[_Snap(values={"site": SITE, "ws_url": WS_URL, "visits": ["intake"]},
                               interrupts=(_gate("intake"),))])
    client = _client(graph_factory=_factory(g))
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    _wait(client, job_id)

    live = _live(client, job_id)
    assert set(live["input"]) == {"mode", "draft_note", "queued"}, live["input"]
    # ⚠️ **三格**（修复轮 1 / N-2）：原先这里还有一格 `say`，它与 `will_stop_at` **一字不差**
    # —— 同一个事实两个名字。`/live` 上 `requested` + `will_stop_at` 已经把那件事说完；
    # 「这一下按到了」那句话只对**响应**有意义，它现在在 `/stop` 的响应里（那一格不是别名）。
    assert set(live["stop"]) == {"requested", "where", "will_stop_at"}, live["stop"]
    assert live["stop"]["requested"] is False, "还没人按过停 —— 说得出就说「没请求过」"
    assert live["input"]["draft_note"] == "", "没人说过话时这一格是空的（不是句废话）"
    assert live["input"]["queued"] == []


def test_an_empty_say_is_refused_and_nothing_is_recorded(tmp_path):
    """空文本 → 400，**并且什么都不留**（没有事件、没有队列条目）。"""
    g = FakeGraph(steps=[_Snap(values={"site": SITE, "ws_url": WS_URL, "visits": ["intake"]},
                               interrupts=(_gate("intake"),))])
    client = _client(graph_factory=_factory(g))
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    _wait(client, job_id)
    before = len(_events(client, job_id))

    r = client.post("/job/%s/say" % job_id, json={"text": "   "})
    assert r.status_code == 400, r.text
    assert "空" in r.json()["detail"], r.json()
    assert len(_events(client, job_id)) == before, "拒掉的那一句不许在时间线上留下痕迹"
    assert _live(client, job_id)["input"]["queued"] == []


def test_a_say_to_a_finished_job_is_refused_in_words(tmp_path):
    """终态 job → 409 + 人话（并且**指给他**出路：重新来一遍）。"""
    g = FakeGraph(steps=[_Snap(values={"site": SITE, "visits": ["intake"],
                                       "end_reason": "human_stop", "end_note": "人喊停。"})])
    client = _client(graph_factory=_factory(g))
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    _wait(client, job_id)

    r = client.post("/job/%s/say" % job_id, json={"text": "再点一次"})
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert "跑完" in detail and "重新来一遍" in detail, detail


def test_a_say_to_a_job_that_does_not_exist_is_a_404(tmp_path):
    client = _client(graph_factory=_factory(FakeGraph()))
    r = client.post("/job/job-nope/say", json={"text": "在吗"})
    assert r.status_code == 404, r.text
    assert "没这个任务" in r.json()["detail"], r.json()


def test_a_too_long_say_is_cut_and_the_cut_is_said_out_loud(tmp_path):
    """超长 → **截断**，而且**说出来**（R4：响应与事件里都说清截了多少、留了多少）。

    ⚠️ 事件里记的必须是**真正留下的那段**，不是原文 —— 记原文 = 时间线上有一句
    它其实没带进去的话（R4 点名的那个坑）。
    """
    g = FakeGraph(steps=[_Snap(values={"site": SITE, "ws_url": WS_URL, "visits": ["intake"]},
                               interrupts=(_gate("intake"),))])
    client = _client(graph_factory=_factory(g))
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    _wait(client, job_id)

    text = "很长的一句话" * 300                       # ≈1800 字，肯定超
    r = client.post("/job/%s/say" % job_id, json={"text": text})
    assert r.status_code == 202, r.text
    body = r.json()
    assert str(len(text)) in body["say"], "要说清**原来**有多长：%r" % body["say"]
    assert str(service.SAY_MAX_CHARS) in body["say"], "还要说清**留下**了多少：%r" % body["say"]

    kept = _live(client, job_id)["input"]["queued"][0]["text"]
    assert len(kept) == service.SAY_MAX_CHARS, len(kept)
    assert text.startswith(kept)
    said = _of_kind(client, job_id, "human_said")[0]
    assert said["data"]["text"] == kept, "事件里记的是**留下的那段**，不是原文"
    assert said["data"]["text"] != text
    assert str(len(text)) in said["say"] and str(service.SAY_MAX_CHARS) in said["say"]


# ══════════════════════ `/stop`：它落在哪儿（§四.1 那张矩阵）══════════════════════


def test_stopping_something_that_runs_in_another_node_promises_the_next_gate(tmp_path):
    """**矩阵第 2 行**：别的节点里跑 → 停不下来，但它**做完这一步一定会停下来问你**。

    三件事一起钉：① 那句人话说了「跑完这一步 / 什么都不用按」；② 时间线有一条；
    ③ **没有任何 reply 被发出去**（图一次都没被推）。
    """
    g = FakeGraph(steps=[_Snap(values={"site": SITE, "visits": ["intake"]})])
    client = _client(graph_factory=_factory(g))
    svc = client.app.state.service
    _running_job(svc, stage="selftest")
    before = len(_events(client, "job-running"))

    r = client.post("/job/job-running/stop", json={})
    assert r.status_code == 200, r.text
    stop = r.json()["stop"]
    assert stop["requested"] is True, stop
    assert stop["where"] == "selftest", "它现在在哪个节点：说要说得准（`running_step` 那一次采样）"
    assert "自测" in stop["will_stop_at"], stop["will_stop_at"]
    assert "跑完这一步" in stop["will_stop_at"] and "不用按" in stop["will_stop_at"], stop["will_stop_at"]
    # N-2（修复轮 1）：`say` 与 `will_stop_at` **不是同一句话** —— 前者说的是「这一下按到了」
    # （那是响应这一侧的事实），后者是落点。两格一字不差就是「同一个事实两个名字」。
    assert stop["say"] != stop["will_stop_at"], stop
    assert "记下了" in stop["say"], stop["say"]
    assert stop["will_stop_at"] in stop["say"], "落点那句要**原样**在里面（不是各写一份）：%r" % stop["say"]
    assert len(g.invokes) == 0, "「停」**绝不许**替人往前走一步（它只阻止，不推进）"
    assert len(_events(client, "job-running")) == before + 1, "按了「停」也是一次人的回话：要有事件"

    live = _live(client, "job-running")
    assert live["stop"]["requested"] is True
    assert live["stop"]["will_stop_at"] == stop["will_stop_at"], "/live 说的与刚才那一句要是同一句"
    # NEW-4（修复轮 2）：**请求过**那一支也必须是三格 —— 只在「没请求过」那支钉的话，
    # 哪天 `say` 那格从这一支长回来，「同一个事实两个名字」会静默地长回去。
    assert set(live["stop"]) == {"requested", "where", "will_stop_at"}, live["stop"]
    # A2：跑着的同一趟里，`/live.stage` 与 `/stop` 的 `where` 是**同一个词**
    assert live["stage"] == stop["where"] == "selftest", (live["stage"], stop["where"])


def test_stopping_while_it_explores_says_it_stops_within_seconds(tmp_path):
    """**矩阵第 1 行**：探路里跑 → **几秒内**停，账本不完整，要接着做就按「重新来一遍」。"""
    g = FakeGraph(steps=[_Snap(values={"site": SITE, "visits": ["intake"]})])
    client = _client(graph_factory=_factory(g))
    svc = client.app.state.service
    job = _running_job(svc, stage="explore")

    r = client.post("/job/job-running/stop", json={})
    assert r.status_code == 200, r.text
    said = r.json()["stop"]["will_stop_at"]
    assert "几秒内" in said, said
    assert "重新来一遍" in said, said
    assert job.stop_requested is True, "探路的 `should_pause` 读的就是这一个标志"


def test_the_pause_hook_that_the_explore_node_asks_is_the_stop_request(tmp_path):
    """`Deps.should_pause` 接上了，而且它读的**就是** `stop_requested`（设计注 §四.1 的授权）。

    为什么这条不能省：接线漏了的话，**上面那条用例照样绿**（它只看标志位），
    而真站上「按了停」会一直跑到探路自己收工 —— 那是这一片最贵的一个洞。
    """
    seen = {}

    def factory(brief, deps):
        seen["deps"] = deps
        return FakeGraph(steps=[_Snap(values={"site": SITE, "visits": ["intake"]})])

    client = _client(graph_factory=factory)
    svc = client.app.state.service
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    _wait(client, job_id)

    deps = seen["deps"]
    assert deps.should_pause is not None, "探路那根线没接：按了停它也不会停"
    assert deps.should_pause() is False, "还没人按停 —— 不许一上来就暂停"
    assert svc._jobs[job_id].stop_requested is False
    assert service.Service(graph_factory=factory)._pause_cb(job_id)() is False, \
        "登记表里没有这个 job 时这根线回 False（不是抛）：`/recover` 那条路也会拼图"

    # 让它「正在探路里跑」（`running_step` 就是 A2 那一次采样），然后人按「停」——
    # 这一条**不推图**（矩阵第 1 行说的是「几秒内停」，停靠的是探路自己每一步问一句）
    svc._jobs[job_id].status = service.RUNNING
    svc._jobs[job_id].running_step = "explore"
    assert client.post("/job/%s/stop" % job_id, json={}).status_code == 200
    assert deps.should_pause() is True, "「停」请求没读到 —— 探路会一直跑下去"


def test_stopping_a_queued_job_says_it_has_not_started_and_invents_no_node(tmp_path):
    """**R2**（矩阵里没有的那一行）：还没轮到窗口 → 不许说「几秒内停下」，**不许编节点名**。"""
    client = _client(graph_factory=_factory(FakeGraph(steps=[_Snap(values={})])))
    svc = client.app.state.service
    job = service.Job(job_id="job-waiting-in-line", brief={"site": SITE}, status=service.QUEUED,
                      created_at="2026-09-19T00:00:00+08:00")
    job.graph = FakeGraph(steps=[_Snap(values={})])
    svc._jobs[job.job_id] = job

    r = client.post("/job/job-waiting-in-line/stop", json={})
    assert r.status_code == 200, r.text
    stop = r.json()["stop"]
    assert stop["requested"] is True
    assert stop["where"] == "", "还没开跑 —— 一个节点名都编不出来（R2）"
    assert "还没轮到窗口" in stop["will_stop_at"], stop["will_stop_at"]
    assert "第一道闸" in stop["will_stop_at"], "它一动就会先停在第一道闸上（那本来就等你）"
    assert "几秒内" not in stop["will_stop_at"], "还没开跑说什么「几秒内停下」= 假话"
    assert job.stop_requested is True, "请求要**记下**（R2）"
    assert [e["who"] for e in _of_kind(client, "job-waiting-in-line", "human_said")] == ["you"]


def test_stopping_at_a_gate_stops_it_right_now_and_that_step_is_not_done(tmp_path):
    """**矩阵第 3 行**：已经在闸上 → **立刻**（== 回一句 `{"action":"stop"}`），这一步**不做**。"""
    g = FakeGraph(steps=[
        _Snap(values={"site": SITE, "ws_url": WS_URL, "visits": ["intake"]},
              interrupts=(_gate("intake"),)),
        # 人喊停之后图走到的那一步：`visits` 里**没有** intake（那一步一步都没做）
        _Snap(values={"site": SITE, "ws_url": WS_URL, "visits": [],
                      "end_reason": "human_stop", "end_note": "人喊停：在「开工前的确认」这一步之前停下了。"}),
    ])
    client = _client(graph_factory=_factory(g))
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    _wait(client, job_id)

    r = client.post("/job/%s/stop" % job_id, json={})
    assert r.status_code == 200, r.text
    stop = r.json()["stop"]
    assert stop["requested"] is True and stop["where"] == "intake", stop
    assert stop["will_stop_at"] == service.STOP_AT_GATE_SAY, stop["will_stop_at"]
    assert "这一步没有做" in stop["will_stop_at"], stop["will_stop_at"]
    # N-2 的那条判据**两支都要钉**（闸上这一支与跑着那一支各有一句 `say`）——
    # 只钉一支的话，另一支哪天变回别名，一条红的都不会有。
    assert stop["say"] != stop["will_stop_at"] and "记下了" in stop["say"], stop
    assert stop["will_stop_at"] in stop["say"], stop["say"]

    view = _wait(client, job_id)
    assert view["status"] == "done", view
    assert view["result"]["end_reason"] == "human_stop", view["result"]
    assert view["result"]["visits"] == [], "「停」在闸上：这一步**一步都没有做**"
    # M-2（修复轮 1）：到头之后那个请求**已经兑现并清掉了**（复审量过、我这里钉住）——
    # 「停」不是一条一直挂着的状态，它是**一次**请求（A3 的清在每一种落点上都生效）。
    assert _live(client, job_id)["stop"]["requested"] is False, _live(client, job_id)["stop"]
    # 交下去的必须是「停」本身 —— 不是「继续」（服务绝不替人往前走）
    assert getattr(g.invokes[1], "resume", None) == {"action": "stop", "note": ""}, \
        "resume 的那一句不是「停」：%r" % (g.invokes[1],)
    assert [e["who"] for e in _of_kind(client, job_id, "human_said")] == ["you"]


def test_stopping_a_finished_job_is_refused_with_the_same_shape_as_say(tmp_path):
    """**R3**：终态 job 上 `/stop` → 409 + 人话（与 `/say` 同一种答复）。"""
    g = FakeGraph(steps=[_Snap(values={"site": SITE, "visits": ["intake"],
                                       "end_reason": "human_stop", "end_note": "人喊停。"})])
    client = _client(graph_factory=_factory(g))
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    _wait(client, job_id)

    r = client.post("/job/%s/stop" % job_id, json={})
    assert r.status_code == 409, r.text
    assert "跑完" in r.json()["detail"], r.json()


def test_stopping_a_job_that_does_not_exist_is_a_404(tmp_path):
    client = _client(graph_factory=_factory(FakeGraph()))
    r = client.post("/job/job-nope/stop", json={})
    assert r.status_code == 404, r.text
    assert "没这个任务" in r.json()["detail"], r.json()


def test_the_stop_request_is_cleared_when_it_lands_so_the_button_comes_back(tmp_path):
    """**A3**：停的请求在**到闸上那一刻**清掉（+ 一条时间线），而且**只清一次**。

    不清的后果是**按钮永久按不动**（`console.html:696`：`requested` 为真就禁用）。
    清的动作必须**说出来**（不许静默清）—— 落点是 `stop_landed` 那一条事件。
    """
    g = FakeGraph(steps=[_Snap(values={"site": SITE, "visits": ["intake", "selftest"]},
                               interrupts=(_gate("draft"),))])
    client = _client(graph_factory=_factory(g))
    svc = client.app.state.service
    job = _running_job(svc, stage="selftest")
    job.graph = g

    assert client.post("/job/job-running/stop", json={}).status_code == 200
    assert job.stop_requested is True
    # 这一步跑完了：图停在下一道闸上（draft）—— 「停」到这儿就兑现了
    svc._advance(job, None)
    _wait(client, "job-running")

    assert job.stop_requested is False, "到闸上了还不清 → 那个按钮就再也按不动了"
    live = _live(client, "job-running")
    assert live["status"] == "waiting" and live["gate"]["step"] == "draft", live
    assert live["stop"]["requested"] is False, live["stop"]
    assert live["stop"]["will_stop_at"] == "", "清了就不许再摆着上一次那句承诺"
    landed = _of_kind(client, "job-running", "stop_landed")
    assert len(landed) == 1, landed
    assert "写这一版 py" in landed[0]["say"], "要说清它**落在哪**：%r" % landed[0]["say"]

    # 再推一次：**同一件事不许记第二遍**（照 `window_dead` 那三条例矩）
    g.steps.append(_Snap(values={"site": SITE, "visits": ["intake", "selftest", "draft"]},
                         interrupts=(_gate("lint"),)))
    svc._advance(job, None)
    _wait(client, "job-running")
    assert len(_of_kind(client, "job-running", "stop_landed")) == 1, "同一件事记了第二遍"


def test_the_stop_is_also_honoured_when_the_run_ends_instead_of_landing_on_a_gate(tmp_path):
    """探路那一趟被「停」收住了（`paused`）—— 也是**兑现**（A3 的另一支）。

    这一支漏掉的后果有两层，都在暗处：① 那个按钮在被停住的 run 上**永久按不动**；
    ② `reopen`（`paused` 这一支接得住）会一上来就自己停住 —— 人根本没再按过第二次。
    """
    g = FakeGraph(steps=[_Snap(values={"site": SITE, "visits": ["intake", "explore"],
                                       "end_reason": "paused",
                                       "end_note": "人喊停：探路在几步之内收住了。"})])
    client = _client(graph_factory=_factory(g))
    svc = client.app.state.service
    job = _running_job(svc, stage="explore")
    job.graph = g
    assert client.post("/job/job-running/stop", json={}).status_code == 200
    assert job.stop_requested is True

    svc._advance(job, None)               # 这一趟以 paused 收尾（探路被 should_pause 收住了）
    assert job.stop_requested is False, "这一趟停下来了 —— 那个请求也兑现了，也要还回去"
    landed = _of_kind(client, "job-running", "stop_landed")
    assert len(landed) == 1, landed
    assert "没有接着往下走" in landed[0]["say"], landed[0]["say"]
    assert "账本不完整" in landed[0]["say"], \
        "探路停下的那一支要说清「要接着做就按重新来一遍」：%r" % landed[0]["say"]
    assert _live(client, "job-running")["stop"]["requested"] is False


def test_say_stop_and_live_do_not_wait_for_a_running_step(tmp_path):
    """**A1 那个陷阱**：跑着那一档，`/say` `/stop` `/live` **不许读 checkpoint**。

    为什么非要这一条（裁定 A1 的原话）：把它写成 `_where_it_stopped(job_id)` 一行，
    桩图跑得飞快、**所有别的单测照样绿** —— 到真站上就是「按了停，浏览器转圈转到这一步跑完」。
    所以这里让第 2 次 `invoke` **真的卡住**（那时写锁被它占着），再量三个端点的往返。
    """
    inside = threading.Event()
    let_go = threading.Event()

    def hold(n):
        if n == 2:
            inside.set()
            let_go.wait(10)

    g = FakeGraph(steps=[
        _Snap(values={"site": SITE, "ws_url": WS_URL, "visits": ["intake"]},
              interrupts=(_gate("intake"),)),
        _Snap(values={"site": SITE, "ws_url": WS_URL, "visits": ["intake", "explore"]},
              interrupts=(_gate("draft"),)),
    ], hold=hold)
    client = _client(graph_factory=_factory(g))
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    _wait(client, job_id)
    client.post("/job/%s/reply" % job_id, json={"action": "continue"})
    assert inside.wait(10), "第 2 次 invoke 没有进去 —— 这一条量不到东西"

    try:
        calls = {
            "live": lambda: client.get("/job/%s/live" % job_id),
            "say": lambda: client.post("/job/%s/say" % job_id, json={"text": "先点 cookie 同意"}),
            "stop": lambda: client.post("/job/%s/stop" % job_id, json={}),
        }
        for name, call in calls.items():
            t0 = time.time()
            r = call()
            took = time.time() - t0
            assert took < 1.0, (
                "`/%s` 排在了一次**正在跑**的 invoke 后面（%.1fs）—— 跑着那一档"
                "一个字节的 checkpoint 都不许读（A1）" % (name, took))
            assert r.status_code in (200, 202), (name, r.text)

        live = _live(client, job_id)
        assert live["status"] == "running"
        assert live["stage"] == "intake", "跑着时的 stage 是 `running_step` 那一次采样"
        stopped = client.post("/job/%s/stop" % job_id, json={}).json()["stop"]
        assert stopped["where"] == live["stage"], "同一趟里 `/live.stage` 与 `/stop.where` 是同一个词"
        # ⚠️ 收窄过（修复轮 1 / N-1）：原先写的是 `"正在跑" in note or "跑" in note` ——
        # 那个 `or` 让**任何**含「跑」的句子都能让这条过（看着像钉子、其实没钉住那句原话）。
        assert "正在跑" in live["note"], live["note"]
    finally:
        let_go.set()
    _wait(client, job_id)


# ═════════════════════════ `/again`：重新来一遍 ═════════════════════════


def test_again_starts_a_new_job_with_the_same_brief_and_the_words_the_human_said(tmp_path):
    """`/again` → **新 job**：同一份开场白 + **人说过的话**（R5：两个来源都带）。

    两个来源**都要带**（R5 的代价那栏：少带一样 = 人得重说一遍）：
      ① 旧 job 的 checkpoint `hints`（在闸上真说过的）；
      ② 旧 job 的 `inbox` 里**还没送到**的话。
    """
    g = FakeGraph(steps=[_Snap(values={"site": SITE, "ws_url": WS_URL, "visits": ["intake"],
                                       "hints": ["不是那个按钮，是下面那个"],
                                       "end_reason": "human_stop", "end_note": "人喊停。"})])
    client = _client(graph_factory=_factory(g))
    svc = client.app.state.service
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    _wait(client, job_id)
    old = svc._jobs[job_id]
    with old.lock:                                   # 说了还没送到的那一句（排队那一档）
        old.inbox.append({"text": "表格里的邮编要真能收到信", "at": "2026-09-19T00:00:00+08:00",
                          "delivered": False})

    r = client.post("/job/%s/again" % job_id, json={})
    assert r.status_code == 202, r.text
    body = r.json()
    new_id = body["job_id"]
    assert new_id and new_id != job_id, body
    assert job_id in body["say"], "那句话要说清「这是哪一趟的重来」：%r" % body["say"]

    _settle(client)                      # M-3：`invokes[-1]` 要等队列空（不然读的是时序）
    new = svc._jobs[new_id]
    # ① 同一份开场白
    for key in ("url", "goal", "success_text", "site", "ws_url", "form_file", "out_dir"):
        assert new.brief[key] == old.brief[key], "开场白里的 %s 没带过去" % key
    # ② 人说过的话：两个来源、原顺序（闸上真说过的在前，排队那几句在后）
    assert g.invokes[-1]["hints"] == ["不是那个按钮，是下面那个", "表格里的邮编要真能收到信"], \
        "带过去的话不对：%r" % (g.invokes[-1].get("hints"),)
    # ③ 新时间线的**第一句**就说清这是重来
    first = new.timeline.all()[0]
    assert first["kind"] == "submitted", first
    assert "再来一遍" in first["say"] and job_id in first["say"], first["say"]
    # ④ 旧那一趟的时间线上也留一笔（人按了那个按钮）
    pressed = _of_kind(client, job_id, "human_said")
    assert pressed and new_id in pressed[-1]["say"], pressed[-1]["say"]


def test_again_on_a_job_that_is_still_running_is_refused(tmp_path):
    """还在跑的 job 上按「重新来一遍」→ 409（两趟一起跑会抢同一个窗口）。

    ⚠️ 这一条是**这一片自己拍的**（计划与裁定都没写）：页面上那个按钮只在 `over` 时露头，
    而「重来」这个词对着一趟**正在动**的 run 说不通 —— 先按「停」，再说重来。
    """
    g = FakeGraph(steps=[_Snap(values={"site": SITE, "visits": ["intake"]})])
    client = _client(graph_factory=_factory(g))
    svc = client.app.state.service
    _running_job(svc, stage="selftest")

    r = client.post("/job/job-running/again", json={})
    assert r.status_code == 409, r.text
    assert "停" in r.json()["detail"], r.json()


def test_again_on_a_job_that_does_not_exist_is_a_404(tmp_path):
    client = _client(graph_factory=_factory(FakeGraph()))
    r = client.post("/job/job-nope/again", json={})
    assert r.status_code == 404, r.text
    assert "没这个任务" in r.json()["detail"], r.json()


def test_the_words_a_run_starts_with_really_land_in_its_state(tmp_path):
    """`/again` 那句承诺（「你说的话我记着」）全靠这一跳：载荷里的 `hints` **真的**播进状态。

    为什么这条只有**真图**证得了：桩图的状态活在它自己身上，checkpoint 里什么都没有 ——
    `hints` 到底有没有变成「这一趟手上有的东西」，只有真 saver 答得出（R6/C 那条核实）。
    """
    def _never_used_deps() -> graph.Deps:
        def boom(*a, **kw):
            raise AssertionError("这一条只停在 intake —— 不该走到会开浏览器的那一步")
        return graph.Deps(explore=boom, selftest=boom)

    saver = InMemorySaver().with_allowlist(graph.MSGPACK_ALLOWLIST)
    client = _client(graph_factory=lambda brief, deps: graph.build(checkpointer=saver,
                                                                   deps=_never_used_deps()),
                     checkpointer=saver)
    job_id = client.post("/run", json=_brief(tmp_path, ws_url=None,
                                             allow_skips=["country", "viewport"],
                                             hints=["不是那个按钮，是下面那个"])).json()["job_id"]
    _wait(client, job_id)

    values = dict(client.app.state.service._snapshot(job_id).values or {})
    assert values.get("hints") == ["不是那个按钮，是下面那个"], values.get("hints")


def test_again_on_a_run_that_blew_up_carries_the_words_too(tmp_path):
    """跑挂那一档也能重来（`/again` 是 `paused` / 跑挂 / 到头了共同的出路）。

    这一条顺带钉住「重来的那一趟**不是**从旧 job 的中间接着走」：payload 里没有 `resume`
    那一类的东西 —— 它是一次**新起**，不是续跑。
    """
    g = FakeGraph(steps=[_Snap(values={"site": SITE, "visits": ["intake"],
                                       "hints": ["先点 cookie 同意"]})])
    client = _client(graph_factory=_factory(g))
    svc = client.app.state.service
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    _wait(client, job_id)
    svc._jobs[job_id].status = service.FAILED          # 跑挂（时间线与备注仍在）

    r = client.post("/job/%s/again" % job_id, json={})
    assert r.status_code == 202, r.text
    _settle(client)                      # M-3：同上
    new_id = r.json()["job_id"]
    payload = g.invokes[-1]
    assert isinstance(payload, dict), "重来是一趟**新跑**（payload 是开场白，不是 resume）：%r" % (payload,)
    assert payload["hints"] == ["先点 cookie 同意"]
    assert payload["url"] == URL
    assert json.dumps(payload, ensure_ascii=False)  # 交下去的必须是说得清的 JSON
    assert svc._jobs[new_id].brief["url"] == URL


# ═════════ 修复轮 4：按【类】上守（判据只有一处 / 不许替他编动作）═════════
#
# 前三轮的病是**改窄**：每轮只修被点名的那一处，把那一类留在原地（修复轮 3 修了
# `Job.inbox` 那一个 gloss，同一族的另外三处 docstring 原样过审）。所以这一节的两道守
# **都不是钉字面的**：
#   · `…criteria_live_in_exactly_one_place` 按 **AST** 扫，问的是「还有没有别的地方
#     自己在读那两个键」—— 将来在 `agent/*.py` 的**任何位置**（含模块级 / 类体 / lambda）、
#     用**任何写法**冒出手写的判据都会红；⚠️ 但「任何」到此为止，**四条量过的盲路**照实列在
#     那条用例的 docstring 里（键名不写字面量 / `getattr` / 位置下标 / 别的目录）；
#   · `…intent_…` 三条问的是「这句话是不是在**替他编一个他没做过的动作**」，
#     词表在 `_INTENT_PATTERNS`（加词只改那一处），不是只禁「你改口了」这一个词。

#: 「**替他编一个他没做过的动作**」这一族的话（F3/F4 那一类）—— **正则片段**，不是字面。
#: 服务**不知道**他屏幕上画的是哪一句、也不知道他心里想什么 —— 这些话都不是观察语。
#: ⚠️ 为什么写成模式而不是一串词（这一条是这一轮的教训）：复审 3 的反例 `R2c` 是
#: 「**你不要它**了」—— 一个只列了「不要了」的**字面**词表**抓不到它**（子串对不上）。
#: 这一族里「改主意」那一支的**形状**是「不＋要/想/愿＋<宾语>＋了」，所以按形状找。
#: ⚠️ 两处刻意的排除（都会**假红**，所以写明）：
#:   · `改口` 后面跟 `径` ＝ 这一片自己的行话（「改口径」= 改判据），不是意图语；
#:   · 这一族的「不…了」**必须带那个「了」**，否则「要不要喂」（`Job.inbox` 那段）这种
#:     正当用法会被扫进来 —— 那些是「还没决定」，不是「他改主意了」。
#: ⚠️ 这仍是一张**词/形状表**，不是证明：表外的同义说法（比如换一个我们没列进来的词）
#: 它挡不住 —— 报告 §射程里照实写了。但**表内**每多一个实例、或有人想换一个新词，
#: 都必须先动这里（那正是「有人会看见」的地方）。
_INTENT_PATTERNS = (
    r"改了?口(?!径)",                   # 改口 / 改了口（但不含「改了口径」那个行话）
    r"改主意", r"反悔", r"变卦", r"作废",
    r"不[要想愿][^。，；：]{0,6}了",     # 不要了 / 不要它了 / 不想要了 / 不愿要了 …
    r"摆在他面前", r"上过他的屏", r"别再摆给我看",
    # 修复轮 5 / N1：**猜他心里怎么想**也是这一族（服务观察不到）——「想不想要」原先只写在
    # `service.py` 那段散文的「词表」里、表里没有它（量过：不认）。它正是前两轮从 `Job.inbox`
    # 的 gloss 里拿掉的那个词（「他还想不想要」→「这一句还摆不摆给他」）⇒ 下一个人最可能
    # 以为它被守着。这两处现在是**一处**：散文点名的那七个字由哨兵逐个断言。
    r"想不想要",
)
_INTENT_RE = re.compile("|".join(_INTENT_PATTERNS))


def _glossed_words_in_service():
    """从 `service.py` 的那段散文里**读**出它点名的词 —— **不手抄**（手抄就是又一个「两个名字」）。

    那一行（`HUMAN_SAID_SUPERSEDED_SAY` 上面那一段）自称是这一族的「词表」，形如
    `#: 改口 / 不要了 / … / 别再摆给我看 … [禁语]`。⚠️ **修复轮 5 / N1**：它原先点着
    「想不想要」，而 `_INTENT_PATTERNS` 里**没有**这个词（量过：`_intent_hits("想不想要")` ⇒ `[]`）
    —— 那句「词表」当时是**推**出来的。现在散文点一个、哨兵就问一遍「表里认不认」。
    """
    for line in pathlib.Path(service.__file__).read_text(encoding="utf-8").splitlines():
        if not (line.lstrip().startswith("#:") and _FORBIDDEN_MARK in line and " / " in line):
            continue
        body = line.split("——")[-1].split(_FORBIDDEN_MARK)[0].strip().lstrip("#:").strip()
        words = [w.strip() for w in body.replace("…", " ").split("/") if w.strip()]
        if len(words) >= 3:
            return words
    return []


def _intent_hits(text: str):
    """这一串里出现了哪几个「意图语」（返回**命中的原文**，不是模式）。"""
    return sorted({m.group(0) for m in _INTENT_RE.finditer(text)})


#: 标了它 = 「这一行是在说**这个词不许用**，不是在用它」。**只豁免散文**（注释 / 文档串）。
_FORBIDDEN_MARK = "[禁语]"

#: 唯一允许**从条目上读**那两个键的几个函数（＝判据的纯核）。别处读 = 又抄了一份判据。
_CRITERIA_HOME = {"_waiting_entries", "_delivered_by", "_unsent_texts"}

#: 三个纯核**各自**该被算到几次（修复轮 5 / 加固①）——「总数 ≥ N」两头都挡不住：
#: 掉**一条**读键（4 → 3）它还绿；往纯核**内部**再塞一份副本反而把计数**推大**（4 → 5），
#: 哨兵被喂饱。逐个对账才两头都挡（两条都量过，见报告 §修复轮 5）。
_CRITERIA_READS = {"_waiting_entries": 2, "_delivered_by": 1, "_unsent_texts": 1}

#: 条目上表示状态的键。
_ENTRY_KEYS = {"delivered", "superseded"}

#: 唯二的两个**同名不同物**的例外：`/job/{id}` 的投影 `view` 与 checkpoint 的 `values`
#: 里也有一个 `delivered`（那是「这一趟的产物送到没有」，**不是** `inbox` 条目的那一格）。
#: ⇒ 守按「接收者名字 **+ 它出现在哪个函数里**」放行这两个（别的名字一律要落在 `_CRITERIA_HOME` 里）。
#: ⚠️ **修复轮 5 / 加固②**：原先**只按接收者名字**放行 ⇒ 任何一条**条目**只要那个变量叫
#: `view` / `values` 就自动免检，而 `view` 正是这个文件里最常见的名字
#: （量过：在别的函数里写 `view.get("delivered")`（`view` 是一条条目）⇒ 旧守**全绿**）。
#: 现在还要**出现在下面点名的那个函数里** —— 名字对、地方不对 ＝ 野的。
_NOT_INBOX_RECEIVERS = {"view": ("live", "runs"), "values": ("_measure_after",)}


def _agent_python_files():
    return sorted(p for p in pathlib.Path(service.__file__).resolve().parent.glob("*.py")
                  if p.is_file())


def _entry_key_read(node):
    """这一处是不是**从条目上读**状态键 —— 返回接收者那串源码，不是就返回 `None`。

    ⚠️ 只认**读**：写（`entry["delivered"] = True`）与造条目（`{"delivered": False, …}`）
    是合法的、也不是判据。所以认的是 `.get(<键>)` 与**取值**下标。
    """
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
            and node.func.attr == "get" and node.args:
        first = node.args[0]
        if isinstance(first, ast.Constant) and first.value in _ENTRY_KEYS:
            return ast.unparse(node.func.value)
    if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load):
        if isinstance(node.slice, ast.Constant) and node.slice.value in _ENTRY_KEYS:
            return ast.unparse(node.value)
    return None


def _criteria_outside_their_home():
    """扫 `agent/` 下那一层 `*.py`：**从条目上读那两个键**的地方有没有跑到那三个纯核外面。

    导出 `(野的清单, 正经判据处数, 每个纯核各被算到几次)`。
    ⚠️ 这一扫**不认写法**：不推导式也认（普通 `if` 也认），所以「换一种筛法」躲不过去
    —— 躲得过去的只有「不读这两个键」。
    """
    bad, seen_home, by_core = [], 0, {}
    for path in _agent_python_files():
        b, n, c = _criteria_in_tree(ast.parse(path.read_text(encoding="utf-8")), path.name)
        bad += b
        seen_home += n
        for name, k in c.items():
            by_core[name] = by_core.get(name, 0) + k
    return bad, seen_home, by_core


def _nearest_named_function(node, parents):
    """从这一处往外找**最近的具名函数**的名 —— 模块级 / 类体 / 类体里的 lambda ⇒ `None`。

    ⚠️ **修复轮 5 / N3**：原先是「只遍历 `FunctionDef`」，所以**不在任何函数里**的三处
    （模块级 / 类体 / 类体里的 lambda）**整个跳过**（量过：三处全绿）。现在按**位置**判。
    """
    cur = parents.get(node)
    while cur is not None:
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return cur.name
        cur = parents.get(cur)
    return None


def _criteria_in_tree(tree, filename):
    """扫**一棵** AST：哪些地方从条目上读了那两个键、而**不在**那三个纯核里。

    导出 `(野的清单, 正经判据处数, 每个纯核各被算到几次)`。
    ⚠️ **位置**也算（修复轮 5 / N3）：最近的具名函数不在 `_CRITERIA_HOME` 里就是野的
    —— 包括**不在任何具名函数里**的（模块级 / 类体 / 类体里的 lambda），也包括**嵌在纯核里的
    另一个函数**（它自己就是最近的具名函数）。
    ⚠️ 够不着的四条（**量过全绿**，不是「挡得住」）：键名不写字面量 / `getattr` / 位置下标 /
    `agent/` 单层以外的一切（子目录、`tests/`、别的目录）。
    """
    bad, seen_home, by_core = [], 0, {}
    parents = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    for node in ast.walk(tree):
        receiver = _entry_key_read(node)
        if receiver is None:
            continue
        home = _nearest_named_function(node, parents)
        if receiver in _NOT_INBOX_RECEIVERS:
            if home in _NOT_INBOX_RECEIVERS[receiver]:
                continue
            bad.append("%s:%d 在 `%s` 里从条目上读了 %s —— 接收者叫 `%s`，但那两个**同名不同物**的"
                       "读键只许出现在 `%s` 里"
                       % (filename, node.lineno, home or "（不在任何函数里）",
                          ast.unparse(node), receiver,
                          " / ".join(_NOT_INBOX_RECEIVERS[receiver])))
        elif home in _CRITERIA_HOME:
            seen_home += 1
            by_core[home] = by_core.get(home, 0) + 1
        else:
            bad.append("%s:%d 在 `%s` 里从条目上读了 %s"
                       % (filename, node.lineno,
                          home or "（不在任何函数里：模块级 / 类体 / lambda）",
                          ast.unparse(node)))
    return bad, seen_home, by_core


def test_the_inbox_criteria_live_in_exactly_one_place():
    """「谁还在等」这条判据**只许有一处**（修复轮 4 / F1、F2）。

    失效形状（复审 3 的 `R1e`）：`_still_waiting` 与 `_inbox_plan` 里**各写一份**判据，
    把其中一份改坏 —— **38 passed，全绿**。而 `_inbox_plan` 那一份是**承重的那一份**
    （它决定谁被标 `superseded`），`_still_waiting` 的 docstring 却写着「这一条判据只有这一处」。

    ⚠️ 为什么守的是**AST**、不是那句 docstring 的字面：这一族的病是「又冒出一个新实例」，
    而新实例可以换写法（换变量名、先存一格再筛、写成 `for` 循环、抄成 `if` 判断）。
    这里问的是**「还有没有别的地方**从条目上读**那两个键」** —— 换写法躲不过
    （量过：`x.get("delivered")` / `x["delivered"] is False` / 先 `_e = job.inbox` 再筛，**都红**，
    见报告 §三 的 `R4a`/`R4c`/`R4c2`）。
    ⚠️ **射程（修复轮 5 / N3 收准 —— 逐条量过，不是推的）**：
      ✅ `agent/` 那一层 `*.py` 里**任何位置**：模块级 / 类体 / 类体里的 lambda / 任何函数名 /
         任何写法（推导式、`if`、先存一格、下标、另一个模块、嵌 `def`、默认参数）；
      ✅ `view` / `values` 的豁免是「**名字 + 它出现在哪个函数里**」——名字对、地方不对就是野的。
      ❌ **仍躲得过去**（四条，全部量过**全绿**）：①键名不写字面量（`_k = "delivered"; x.get(_k)`）；
         ②`getattr(x, "delivered")`；③条目换成位置结构（`x[2]`）；
         ④ `agent/` **单层**以外的一切（子目录、`tests/`、别的目录）。
      合成源码的正/反控在 `…sees_every_position`；真文件里那几条由变异证明（报告 §修复轮 5）。
    ⚠️ 两个**同名不同物**的例外写在 `_NOT_INBOX_RECEIVERS`（`view` / `values` 里那个
    `delivered` 说的是「这一趟的产物送到没有」，与 `inbox` 无关）—— 不豁免这两处，
    这一条会**假红 4 处**（`live` 两处 + `runs` 一处 + `_measure_after` 一处；量过）。
    """
    bad, seen_home, by_core = _criteria_outside_their_home()
    assert not bad, ("队里那两个键被读的地方不止一处 —— 判据又有副本了：\n  %s\n"
                     "（判据只在 `%s` 里；要改口径改那儿，别在调用点再抄一份）"
                     % ("\n  ".join(bad), " / ".join(sorted(_CRITERIA_HOME))))
    # ⚠️ 哨兵：证明这一扫**真的在看东西**（判据被删光时，上面那句「没有野的」会**静默变真**）
    # 修复轮 5 / 加固①：原先 `>= 3` 松一格（今天就是 4 —— 掉 1 条读键还剩 3 ⇒ 哨兵**不响**）。
    assert seen_home >= 4, ("只扫到 %d 处正经判据 —— 那三个纯核不见了？"
                            "（这一扫要是空的，上面那条断言什么都没证明）" % seen_home)
    # ⚠️ 光有总数不够（修复轮 5 / 加固①）：往纯核**内部**再塞一份漂移副本会把计数**推大**
    # （4 → 5），哨兵反而**被喂饱**（量过：那种形状下旧哨兵全绿）。逐个纯核对账，两头都挡：
    # 掉一条读键 → 少一个计数；塞副本 → 多一个计数；纯核被掏空 → 那一格没了。
    assert by_core == _CRITERIA_READS, (
        "三个纯核各自该被算到几次对不上：量到 %r，该是 %r —— 要么某个纯核不再读那两个键了"
        "（判据被掏空/改坏了），要么有人在纯核里又塞了一份副本（两份会**静默分岔**）。"
        "**照实改了就一起改 `_CRITERIA_READS`**，别只把数调过去。" % (by_core, _CRITERIA_READS))


def test_the_criteria_guard_sees_every_position():
    """守看得见**不在任何函数里**的判据、也看得见**接收者名字冒充**的（修复轮 5 / N3）。

    复审 4 量到的四条盲路（当时**都没自认**，我逐条重量过，都**全绿**）：
    ① **模块级**（不在任何函数里）；② **类体**；③ **类体里的 lambda**（lambda 体不是 `FunctionDef`
    —— 原先那一扫只走 `FunctionDef`，这三处整个跳过）；
    ④ **接收者名字**放行：`view` / `values` 只按**变量名**免检 ⇒ 一条**条目**只要那个变量叫
    `view` 就自动过（量过：`view.get("delivered")` **绿**）。⚠️ 复审举的那个例子
    （`[e for e in view if e.get("delivered")]`）**当时就是红的** —— 读键的接收者是 `e`，
    不是 `view`；真正躲得过去的是「**读键的接收者本身**叫 `view`/`values`」那一种。
    这一条拿**合成源码**把这两件事钉住（真文件里那五条由变异证明，见报告 §修复轮 5）。
    ⚠️ 下面那两段**照实钉住它够不着的地方**（不是「挡得住」）。
    """
    for why, src in {
        "模块级": 'X = [x for x in [] if not x.get("delivered")]\n',
        "类体": 'class A:\n    X = [x for x in [] if not x.get("superseded")]\n',
        "类体里的 lambda": 'class A:\n    x = staticmethod('
                           'lambda i: [y for y in i if not y.get("delivered")])\n',
        "函数里的 lambda": 'def f(i):\n    return (lambda j: [y for y in j'
                           ' if not y.get("delivered")])(i)\n',
        "读键的接收者本身叫 view（在别的函数里）": 'def f(view):\n    return view.get("delivered")\n',
        "读键的接收者本身叫 values（在别的函数里）": 'def f(values):\n    return values.get("delivered")\n',
    }.items():
        bad, _seen, _by = _criteria_in_tree(ast.parse(src), "<合成>")
        assert bad, "「%s」里的判据没被扫出来（副本身份就这么混过去了）：%s" % (why, src)
    # 反方向：那两处**同名不同物**的读键、以及三个纯核自己，不许被误伤
    for why, src in {
        "live 里读投影 `view`": 'def live(view):\n    return view.get("delivered")\n',
        "runs 里读投影 `view`": 'def runs(view):\n    return view.get("delivered")\n',
        "_measure_after 里读 `values`": 'def _measure_after(values):\n'
                                        '    return values.get("delivered")\n',
        "三个纯核自己": 'def _waiting_entries(xs):\n'
                        '    return [e for e in xs if not e.get("delivered")]\n',
    }.items():
        bad, seen, by = _criteria_in_tree(ast.parse(src), "<合成>")
        assert bad == [], "「%s」被误伤了：%r" % (why, bad)
    # ⚠️ 它**够不着**的四条（前三条是复审自认的，第四条是豁免机制自己的窄缝；都重量过：绿）
    for why, src in {
        "键名不写字面量": 'def f(xs):\n    k = "delivered"\n    return [e for e in xs if e.get(k)]\n',
        "getattr": 'def f(xs):\n    return [e for e in xs if not getattr(e, "delivered")]\n',
        "位置下标": 'def f(xs):\n    return [e for e in xs if not e[2]]\n',
        # 名字对、**地点也对**（在 `live` 里）⇒ 一律放行 —— 哪怕那儿读的其实是一条**条目**
        # （AST 分不出「投影」与「条目」；加固②之后**仍然**是这么放的，照实钉住）
        "在 live 里读一个叫 view 的东西": 'def live(view):\n    return view.get("superseded")\n',
    }.items():
        bad, _seen, _by = _criteria_in_tree(ast.parse(src), "<合成>")
        assert bad == [], ("「%s」量过是**绿的**（守够不着的路，报告里照实记着）——"
                           "它要是变红了：把这一条改成正向断言，并更新那条用例的 docstring"
                           "与报告 §射程" % why)


def test_the_two_places_that_ask_who_is_waiting_agree(tmp_path):
    """问「谁还在等」的两处**必须是同一个答案**（修复轮 4 / F1）。

    `_inbox_plan`（决定谁被标 `superseded`）与 `_still_waiting`（决定 `draft_note` / `n`）
    原先各有一份判据 —— 两份今天**逐字一样**，所以这条用例在旧实现上不会红（它是**补牙**，
    不是红→绿；红灯由变异 `R4-b` 给，见报告）。它钉的是**行为上的同一个答案**：
    队里三种条目（还能摆的 / 已送出的 / 不再摆给他的）同时在场，两处各问一次。
    """
    g = FakeGraph(steps=[_Snap(values={"site": SITE, "visits": ["intake"]})])
    client = _client(graph_factory=_factory(g))
    svc = client.app.state.service
    job = _running_job(svc, stage="selftest")
    with job.lock:
        job.inbox.extend([
            {"text": "A：还能摆给他的", "at": "2026-09-19T00:00:00+08:00",
             "delivered": False, "superseded": False},
            {"text": "B：已经送出去的", "at": "2026-09-19T00:00:01+08:00",
             "delivered": True, "superseded": False},
            {"text": "C：不再摆给他的", "at": "2026-09-19T00:00:02+08:00",
             "delivered": False, "superseded": True},
        ])

    still = service.Service._still_waiting(job)
    assert [x["text"] for x in still] == ["A：还能摆给他的"], [x["text"] for x in still]
    # 决定「谁被标 superseded」的那一处，认的必须是**同一条**
    sent, superseded = service.Service._inbox_plan(job, "他打的是别的")
    assert sent == [], sent
    assert [x["text"] for x in superseded] == [x["text"] for x in still][-1:], \
        "标错了那一条（两份判据分岔）：%r" % [x["text"] for x in superseded]
    # 端点上也必须是同一个答案（`/live.input.queued` 与 `/say` 的 `n`）
    live = _live(client, "job-running")
    assert [x["text"] for x in live["input"]["queued"]] == ["A：还能摆给他的"], live["input"]
    r = client.post("/job/job-running/say", json={"text": "D：新说的一句"})
    assert r.status_code == 202, r.text
    assert r.json()["n"] == 2, "还能摆给他的那一条 + 刚说的这一条：%r" % r.json()


def test_the_intent_guard_really_matches_the_variants_it_claims():
    """哨兵：证明上面那两条守**真的在找东西**（词表写坏了会静默全绿）。

    ⚠️ 这一条是**给自己上的钉子**：本轮第一版词表**就是坏的** —— 它列的是字面「不要了」，
    而复审 3 的反例是「你不要**它**了」（中间隔一个字，子串对不上）⇒ 变异 `R4e` **全绿**。
    形状改成 `不[要想愿]…了` 之后才抓住。**没有这一条，那张表坏了没人知道。**
    """
    assert _intent_hits("他改了口"), "「改了口」与「改口」都得认（中间那个「了」是常见写法）"
    assert _intent_hits("你不要它了"), "复审 3 的反例 R2c 必须被认出来（这是本轮修的第一件事）"
    assert _intent_hits("他不想要了"), "同一族的另一个变体也得认"
    assert _intent_hits("这一句摆在他面前过"), "「上过他的屏」那一类假断言也要认"
    assert _intent_hits("他说「别再摆给我看了」"), "替人编一句他没说过的话也要认"
    # ⚠️ 修复轮 5 / N1：`service.py` 那段散文（`HUMAN_SAID_SUPERSEDED_SAY` 上面那一段）
    # 自称是「词表」，它**逐个点名**的那些字就**必须**每个都被认出来 —— 否则那一行是**推**出来的，
    # 不是量出来的（当时「想不想要」写在那一行里、表里没有它；而它正是前两轮从 `Job.inbox` 的
    # gloss 里拿掉的那个词 ⇒ 下一个人**最可能**以为它被守着）。
    # ⚠️ 这份名单**从散文里读**（`_glossed_words_in_service`），**不手抄** —— 手抄就是又一个
    # 「一个事实两个名字」，而这一片的病正是那个。
    _glossed = _glossed_words_in_service()
    assert len(_glossed) >= 3, (
        "`service.py` 那段自称「词表」的散文没读出来（它被改了形？）：%r" % (_glossed,))
    for _w in _glossed:
        assert _intent_hits(_w), (
            "`service.py` 那段散文点名的「%s」必须真的被认出来（认不出就把那一行改准，"
            "或者把它收进 `_INTENT_PATTERNS`）" % _w)
    # 反方向：正当用法不许被扫进来（不然这一条会把好句子判红，久了就没人信它）
    assert _intent_hits("要改口径就改这里") == [], "「改口径」是行话（改判据），不是意图语"
    assert _intent_hits("队里还有没有还等着送的话，要不要提它一句") == [], "「要不要」是没决定，不是改主意"


def test_the_forbidden_intent_words_only_appear_on_marked_lines():
    """散文（注释/文档串）里出现这一族的话**必须**标 `[禁语]`（修复轮 4 / F3）。

    规矩立在 `agent/service.py` 那段（`HUMAN_SAID_SUPERSEDED_SAY` 上面）：
    标了 = 「这一行是在说**这个词不许用**」。没标 = 有人正在用这个词 —— 那正是这一片的病史
    （修复轮 3 把「他改了口」从**一句人话**里拿掉，同一族的**三处 docstring** 原样留着过审）。
    """
    src = pathlib.Path(service.__file__).read_text(encoding="utf-8")
    bad = []
    for line_no, line in enumerate(src.splitlines(), 1):
        hit = _intent_hits(line)
        if hit and _FORBIDDEN_MARK not in line:
            bad.append("service.py:%d %s —— %s" % (line_no, hit, line.strip()[:90]))
    assert not bad, (
        "这一族的话只许出现在标了 %s 的行上（标了 = 这一行在说它**不许用**；"
        "要用它就得先想清楚：服务**观察不到**他在想什么）：\n  %s"
        % (_FORBIDDEN_MARK, "\n  ".join(bad)))


def _string_assembly_text(node):
    """这一处是不是**在同一处表达式里拼字符串** —— 是就返回「各段字面量接起来」的串。

    ⚠️ **过度近似**：接缝里的东西（`str(1)` / `{}` 那些）被丢掉。方向是**保守的**
    （宁可假红，不可漏），因为这一族的形状 `不[要想愿][^。，；：]{0,6}了` 本来就允许 ≤6 字的缝。
    """
    if isinstance(node, ast.JoinedStr):        # f-string
        return "".join(v.value for v in node.values
                       if isinstance(v, ast.Constant) and isinstance(v.value, str))
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        parts, stack = [], [node]
        while stack:
            cur = stack.pop(0)
            if isinstance(cur, ast.BinOp) and isinstance(cur.op, ast.Add):
                stack = [cur.left, cur.right] + stack
            elif isinstance(cur, ast.Constant) and isinstance(cur.value, str):
                parts.append(cur.value)
        return "".join(parts)
    return None


def _human_facing_intent_hits(tree):
    """人话（**给运营看的字符串**）里出现的意图语 —— 逐条 `"service.py:行 命中 —— 原文"`。

    认两种：①字符串**字面量**；②**同一处表达式里拼出来**的串（`+` 链 / f-string）。
    ⚠️ ②是**修复轮 5 / N2** 补的：原先只认字面量 ⇒ `"你不要" + str(1) + "它了"` 这种
    拆开再拼的写法**整体绕过**（成品串画到屏上、谓词也认得出，两道守却全绿 —— 复审 4 量到）。
    ⚠️ **它看不见的**：跨语句拼（先存进变量）、`"".join([...])`、从别处读来的串 ——
    这几条量过**全绿**，见 `…sees_text_assembled_in_one_place` 与报告 §修复轮 5。
    """
    prose = {id(n.value) for n in ast.walk(tree)
             if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
             and isinstance(n.value.value, str)}
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) in prose:
                continue
            text, where = node.value, "字面量"
        else:
            text = _string_assembly_text(node)
            if not text:
                continue
            where = "拼出来的"
        hit = _intent_hits(text)
        if hit:
            bad.append("service.py:%d %s %s —— %s"
                       % (node.lineno, hit, where, text.strip()[:90]))
    return bad


def test_no_human_facing_text_attributes_intent_to_him():
    """**给运营看的话**里一个都不许有（修复轮 4 / F3 的另一半）。

    散文还能靠 `[禁语]` 自证「我在说它不许用」；**人话没有这个豁免** —— 它直接画到运营屏上
    （`narrate` 的句子 / `job.say` / 那些 `*_SAY` 常量全是**字符串字面量**）。
    复审 3 的变异 `R2c`（把「你刚送下去的是别的」换成「你不要它了」，其余一字不动）
    在旧验收上**全绿** —— 那说明旧验收钉的是**那一串字**，不是**那一类**。
    ⚠️ 射程（修复轮 5 / N2 收准）：字面量 + **同一处表达式里拼出来**的串都认；
    **跨语句**拼出来的看不见（见 `…sees_text_assembled_in_one_place` 与报告 §修复轮 5）。
    """
    bad = _human_facing_intent_hits(ast.parse(pathlib.Path(service.__file__).read_text(
        encoding="utf-8")))
    assert not bad, ("人话里不许出现这一族的话（它们都在替他编一个他没做过的动作）：\n  %s"
                     % "\n  ".join(bad))


def test_the_human_facing_guard_sees_text_assembled_in_one_place():
    """守看得见**同一处表达式里拼出来**的人话（修复轮 5 / N2）。

    复审 4 量到的那条缝：守原先**逐字符串常量**判 ⇒ 把一句话拆成几段再拼起来
    （`"你不要" + str(1) + "它了"`）**整体绕过**：成品串画到运营屏上、`_intent_hits(成品)`
    也认得出（=`['不要1它了']`），两道守却 **2 passed**。这一条拿**合成源码**把「拼出来的也算」
    钉住（真文件那条路由变异证明，见报告 §修复轮 5）。
    ⚠️ 下面那半段**照实钉住它看不见的那几种**：它们量过是全绿的，不是「挡得住」。
    """
    for why, src in {
        "同行 `+` 拼": 'SAY = "你不要" + str(1) + "它了"\n',
        "跨行 `+` 链": 'SAY = ("你不要"\n       + str(1)\n       + "它了")\n',
        "f-string（接缝里是一个表达式）": 'SAY = f"你不要{str(1)}它了"\n',
    }.items():
        hits = _human_facing_intent_hits(ast.parse(src))
        assert hits, "守没看见%s —— 那条缝还在：%s" % (why, src)
    for why, src in {
        "跨语句拼（先存进变量）": 'A = "你不要"\nSAY = A + "1" + "它了"\n',
        '`"".join([...])`': 'SAY = "".join(["你不要", "1", "它了"])\n',
    }.items():
        assert _human_facing_intent_hits(ast.parse(src)) == [], (
            "%s 这一条**量过是全绿的**（守看不见的残留缝，报告里照实记着）——"
            "它要是变红了：把这一条改成正向断言，并更新 `service.py` 那段散文与报告" % why)
