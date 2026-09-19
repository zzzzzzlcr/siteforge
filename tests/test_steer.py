"""Task 9：插话进探路（`steer` 通道）—— 三层各钉各的。

| 层 | 它承诺什么 | 用例 |
|---|---|---|
| `llm.run_tool_loop(steer=…)` | 每一次模型调用**之前**问一句；非空就作为一条 `user` 消息插进去；那一轮的 record 记 `steered` | `…goes_into_the_round_that_comes_next` / `…asked_before_every_model_call` / `…saying_nothing…` / `…no_steer_at_all…` |
| `explore(steer=…)` | 透传；**插话之后那一轮就收尾**时往 `journey.notes` 追一句人话（设计注 §3.4 的 R11） | `…hands_the_line_down…` / `…wrapping_up_right_after…`（+ 负例） |
| 服务 | 把 `Job.inbox` 里**还在等**的那些交出去；**那一轮真的发出去了**才算送到（翻 `delivered` + 时间线「已经交给它了」）；交不出去时结尾要说 | `…hands_over_the_waiting_words…` / `…until_the_round_really_goes_out` / `…handed_over_again…` / `…stays_shut…` / `…does_not_promise…` / `…real_chain…` / `…reported_missed…`（G/H）/ `…takes_neither…`（F2）/ `…at_a_gate…`（反例） |

⚠️ `/say` 那一侧（探路里说一句 ⇒ 回 `delivered`）**不在这儿** —— 那是 Task 8 那个端点的语义，
用例在 `tests/test_service_input.py`（它替掉了那里原先那条 503 的占位用例）。

⚠️ 这一份**不开浏览器、不打真模型**（Global Constraints）：探路那两层走
桩 MCP 服务 + 桩模型；服务那一层走 `graph_factory` 桩图 + 服务自己拼的
`deps.explore` 那根**真**线（真 `explore`、真 `McpSession`、真 `Popen`）。
⚠️ `agent/console.html` 一个字节都不动：页面那一半归 Task 10。
"""

from __future__ import annotations

import copy
import json
import pathlib
import sys
import threading

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import browser_agent, graph, llm, service  # noqa: E402
from test_browser_agent import PAGE_LANDING, _reply, _stub  # noqa: E402
from test_service_input import (GOAL, SITE, URL, FakeGraph, StubWindow,  # noqa: E402
                                _Snap, _brief, _client, _factory, _gate, _live, _settle)

#: 人插的那句话（**逐字**用在两处：插进模型的那条消息里、时间线上）。
SAID = "不是那个按钮，是下面那个"
#: H 那一幕专用的一句开场白（独一无二 ⇒ 桩的「卡住」不会被别的 job 抢走）
_H_GOAL = "H 那一幕：话是在最后一轮**之后**才说的"
#: 设计注 §3.4 写死的那句提醒（**原话**）—— 这一片唯一的「别把它当成收尾」。
REMINDER = "这是人插的话，接着探，别把它当成收尾"
#: 设计注 §3.4 写死的那句人话（插话之后那一轮就收尾时，进 `journey.notes`）。
WRAPPED_UP_NOTE = "你插话之后它就收尾了 —— 看一眼它的结论对不对"


@pytest.fixture(autouse=True)
def _runtime_goes_to_tmp(tmp_path, monkeypatch):
    """运行产物一律落 `tmp_path`（计划 Global Constraints：测试不写 `runtime/`）。

    ⚠️ 这一条**必须在建 app 之前**生效：`Service.__init__` 把根**构造时定死**
    （之后再看环境就是第二个真相源）。
    """
    monkeypatch.setattr(service.measure, "DEFAULT_ROOT", tmp_path / "runtime" / "explore")


# ─────────────────────────── 桩 ───────────────────────────


def _dispatch(calls: list):
    def run(name, args):
        calls.append(name)
        return {"seen": name}
    return run


class _StubLLM:
    """桩模型：**每一次调用都把当时那一片 `messages` 抄一份**。

    ⚠️ 为什么不直接用 `test_browser_agent.FakeLLM`：它存的是**活引用**
    （那个 list 被循环一路追加），跑完之后 `calls[0]["messages"]` 拿出来的是
    **最后一轮的样子** —— 「第 1 轮它看到了什么」当场消失。而这一片的判据全在那一格上。

    两个可选的花样（都是给「那一轮没发出去」那一族用的，**不靠抢时序**）：
      - `fail_on=n`：第 n 次 `create` **抛**（那一次调用没成）；
      - `block_first=(ready, go)`：第 1 次 `create` 里**卡住**（`ready` 置位 = 它已经卡住了），
        好让测试在「`steer()` 已经问过、这一轮还没回来」的时刻插一句话（实例 H）。
    """

    def __init__(self, turns: list, *, fail_on: int | None = None, block_first=None,
                 block_if: str | None = None):
        self.turns = list(turns)
        self.calls: list[dict] = []
        self.fail_on = fail_on
        self.block_first = block_first
        #: 只有**这一趟**的任务里带着这句话时才卡（⚠️ 防串味：同一会话里别的 job 也会来取
        #: 这个桩（`llm.client` 被本用例接管），不挑的话它会把「卡住」这件事先做掉，
        #: 于是本用例以为「我这一轮卡住了」，其实卡的是别人 —— 复审的 H 脚本就这么串过味）。
        self.block_if = block_if

    @property
    def chat(self):
        return self

    @property
    def completions(self):
        return self

    def create(self, **kwargs):
        self.calls.append({**kwargs, "messages": copy.deepcopy(kwargs.get("messages"))})
        n = len(self.calls)
        if self.fail_on == n:
            raise RuntimeError("模型这一次调用没成（这一条就是来把它弄挂的）")
        if n == 1 and self.block_first is not None:
            mine = self.block_if is None or self.block_if in json.dumps(
                kwargs.get("messages") or [], ensure_ascii=False)
            if mine:
                ready, go = self.block_first
                ready.set()
                assert go.wait(10), "测试没放走这一轮"
        turn = self.turns[min(n - 1, len(self.turns) - 1)]
        return _reply(turn.get("content", ""), turn.get("calls"))


def _said_in(messages: list, needle: str) -> int:
    """这片 messages 里有几条 `user` 消息含这句话（**数**出来的，不是问真假）。"""
    return sum(1 for m in messages
               if isinstance(m, dict) and m.get("role") == "user"
               and needle in str(m.get("content") or ""))


def _once(text: str):
    """一个**只说一次**的 `steer`（第二次起说「没有」）。"""
    left = [text]
    return lambda: left.pop(0) if left else None


def _explore(tmp_path, turns, **kw):
    """一次标准的桩跑法（桩 MCP + 桩模型 + 一条探路目标）。"""
    session, _log = _stub(tmp_path, {"observe": [{"structured": PAGE_LANDING}]})
    fake = _StubLLM(turns)
    try:
        journey = browser_agent.explore(URL, GOAL, session=session, client=fake, **kw)
    finally:
        session.close()
    return journey, fake


# ═══════════════ 一、`llm.run_tool_loop(steer=…)`（计划 Task 9 Step 1 那四条）═══════════════


def test_the_words_go_into_the_round_that_comes_next():
    """`steer()` 第一次返回一句话 ⇒ **那一轮**的 messages 里有它（`role="user"`）且 record 记了。

    三样一起钉：① 它在**这一次调用之前**就进了 messages（`messages[-1]` 就是它 ——
    插晚了这一轮就看不到）；② 那句提醒**在**里头（**子串**断言 —— 判据是「带」，不是「只有它」）；
    ③ record 的 `steered` 是人那句话。
    """
    stub = _StubLLM([{"calls": [("observe", {})]}, {"content": "讲完了"}])
    rounds = llm.run_tool_loop("sys", "usr", [], _dispatch([]), max_rounds=5,
                               _client=stub, steer=_once(SAID))

    first = stub.calls[0]["messages"]
    assert first[-1]["role"] == "user", "插进去的那条必须是最后一条（协议：它得跟在工具结果后面）"
    assert SAID in first[-1]["content"], first[-1]
    assert REMINDER in first[-1]["content"], (
        "插进去的话**必须带那句提醒** —— 那道门上没有 `done()`，「不调工具」=它宣布讲完了（R11）")
    # ⚠️ 子串断言管不到「多一个字」——那句话是**设计注 §3.4 的原话**（引号里没有句号），
    #    所以另钉一条**等式**（回归 2 / NEW-2：上一轮我自报改了，代码里其实没改）。
    assert llm.STEER_REMINDER == REMINDER, llm.STEER_REMINDER
    assert rounds[0]["steered"] == SAID, rounds[0]
    # 说了就那一次：下一轮里它**还在对话里**（1 条），但没有被再插一遍
    assert _said_in(stub.calls[1]["messages"], SAID) == 1, "同一条话插了两遍"


def test_it_is_asked_before_every_model_call():
    """「每一次模型调用之前」—— 不是「第一次之前」。

    一个「只在第一轮问一次」的实现能过上面那一条（`_once` 第二次本来就说「没有」）——
    这里每一轮都有一句新话，于是「问了几次」当场量得出来：3 次调用 = 3 句各进各的轮。
    """
    stub = _StubLLM([{"calls": [("observe", {})]}, {"calls": [("observe", {})]},
                     {"content": "讲完了"}])
    said = iter(["第一句", "第二句", "第三句"])
    rounds = llm.run_tool_loop("sys", "usr", [], _dispatch([]), max_rounds=9,
                               _client=stub, steer=lambda: next(said))

    assert len(stub.calls) == 3, stub.calls
    for k, text in enumerate(["第一句", "第二句", "第三句"]):
        last = stub.calls[k]["messages"][-1]
        assert last["role"] == "user" and text in last["content"], \
            "第 %d 轮：那句话得在**这一次调用之前**插进去（插在之后这一轮就看不到）" % (k + 1)
    assert [r["steered"] for r in rounds] == ["第一句", "第二句", "第三句"]


def test_saying_nothing_inserts_nothing():
    """`steer()` 返回 `None` ⇒ **一个消息都不插**（默认行为一个字节不变）。

    判据是**逐字比对两次运行里模型看到的东西**（不是「消息条数没变」那种松的写法）：
    接了一根「永远说没有」的线，与根本没接这根线，必须**一模一样**。
    """
    quiet = _StubLLM([{"calls": [("observe", {})]}, {"content": "讲完了"}])
    llm.run_tool_loop("sys", "usr", [], _dispatch([]), max_rounds=5,
                      _client=quiet, steer=lambda: None)
    plain = _StubLLM([{"calls": [("observe", {})]}, {"content": "讲完了"}])
    llm.run_tool_loop("sys", "usr", [], _dispatch([]), max_rounds=5, _client=plain)

    assert quiet.calls == plain.calls, "steer 说「没有」时模型看到的东西变了"


class _SpyLine:
    """一根只记账的线：`__call__` 说「没有」，两个结果方法各记一笔。"""

    def __init__(self):
        self.asked = 0
        self.results: list = []

    def __call__(self):
        self.asked += 1
        return None

    def went_out(self):
        self.results.append("went_out")

    def missed(self):
        self.results.append("missed")


def test_a_round_with_nothing_to_hand_over_is_not_confirmed():
    """没人插话的那一轮**不许回填结果**（回填 = 那根线会翻 `delivered`、说「已经交给它了」）。

    `run_tool_loop` 只在**这一轮真的插过话**时才回填 —— 少了那个判据，一节空轮次也会让
    时间线长出「已经交给它了」（而那一句说的是**人插的话**，没人说话时它无从谈起）。
    """
    stub = _StubLLM([{"calls": [("observe", {})]}, {"content": "讲完了"}])
    line = _SpyLine()
    llm.run_tool_loop("sys", "usr", [], _dispatch([]), max_rounds=5, _client=stub, steer=line)

    assert line.asked == 2, "每一轮都得问一次（这一条只钉「回填」）"
    assert line.results == [], "没人插话的轮次回填了：%r" % line.results


def test_no_steer_at_all_is_the_default():
    """`steer=None`（默认）⇒ 不插；那一格也只有「没人插过话」这一种值。"""
    stub = _StubLLM([{"calls": [("observe", {})]}, {"content": "讲完了"}])
    rounds = llm.run_tool_loop("sys", "usr", [], _dispatch([]), max_rounds=5, _client=stub)

    assert [r["steered"] for r in rounds] == [None, None], rounds
    assert [m["role"] for m in stub.calls[0]["messages"]] == ["system", "user"], \
        "不接 steer 的循环里，第一轮只该有开场白那一条 user"


# ═══════════════════ 二、`explore(steer=…)`：透传 + R11 那句人话 ═══════════════════


def test_explore_hands_the_line_down_to_the_loop(tmp_path):
    """`explore(steer=…)` 必须**透传到循环里** —— 不透传这条通道在生产里是死的。"""
    journey, fake = _explore(tmp_path,
                             [{"calls": [("observe", {})]}, {"content": "看完了"}],
                             steer=_once(SAID))
    assert journey.steps, "这一趟得真的走了一步（不然下面那句断言证明不了什么）"
    assert _said_in(fake.calls[0]["messages"], SAID) == 1
    assert REMINDER in fake.calls[0]["messages"][-1]["content"]


def test_wrapping_up_right_after_your_words_is_written_into_the_ledger(tmp_path):
    """**R11**：插话之后那一轮它就收尾了 ⇒ 账上追一句人话（设计注 §3.4）。

    为什么会这样：那道门上没有 `done()`，「不调工具」= 它宣布讲完了 ⇒
    `stop_reason = model_done` ⇒ 图认成「探路走完了」——而它可能**正是被那句话**逼收的尾。
    所以这句要写在 `notes` 的**最后**（`explore_say` 的尾巴取的就是它，人看得见）。
    """
    journey, fake = _explore(tmp_path, [{"content": "我探完了，结论是……"}], steer=_once(SAID))

    assert SAID in fake.calls[0]["messages"][-1]["content"], "这句话真进了那一轮才有下面这件事"
    assert journey.stop_reason == "model_done"
    assert journey.notes[-1] == WRAPPED_UP_NOTE, journey.notes


def test_the_wrap_up_note_does_not_depend_on_being_the_last_note(tmp_path):
    """**F4**：那句话的可见性**不许押在「它是 `notes[-1]`」上**。

    `graph._journey_say` 取的是 `notes[-1]`，而 `service._explore_for.run` 在 `explore` 返回
    **之后**还会往 `journey.notes` 追两句（账本没记全 / 时间线没记全）—— 旁路坏过一趟，
    那句话就从 `explore_say` 里消失（复审判的正是这个前提）。这里把**那个形状**直接造出来。
    ⚠️ 另一半是**不许说两遍**：它本来就在尾巴上时，那句话只该出现一次。
    """
    journey = browser_agent.Journey()
    journey.stop_reason = "model_done"
    journey.notes.append("AI 说：我探完了")
    journey.notes.append(browser_agent.STEER_WRAPPED_UP_NOTE)
    journey.notes.append("⚠️ 这一步之后的时间线没记全：X —— 探路照常走完（旁路坏掉不许带塌主路）。")

    said = graph._journey_say(journey)
    assert browser_agent.STEER_WRAPPED_UP_NOTE in said, said

    tail = browser_agent.Journey()
    tail.stop_reason = "model_done"
    tail.notes.append(browser_agent.STEER_WRAPPED_UP_NOTE)
    assert graph._journey_say(tail).count(browser_agent.STEER_WRAPPED_UP_NOTE) == 1, \
        "它本来就在尾巴上 —— 不许再说一遍"


def test_a_wrap_up_with_no_words_from_you_says_nothing_about_them(tmp_path):
    """负例（**这条是人话纪律的另一半**）：没人插话而它收尾了 ⇒ 那句人话**一个字都不许出现**。

    少了这一条，「插话之后它就收尾了」就可能是一句**永远都在**的假话
    （读的人会以为它每次收尾都跟自己那句话有关）。
    """
    journey, _fake = _explore(tmp_path, [{"content": "我探完了，结论是……"}])

    assert journey.stop_reason == "model_done"
    assert not any(WRAPPED_UP_NOTE in n for n in journey.notes), journey.notes


# ═══════════════════ 三、服务：喂给谁、什么时候翻、时间线上说什么 ═══════════════════


class _HoldGraph:
    """`invoke` 里**真跑一趟探路**（服务自己拼的 `deps.explore` 那根线），**卡在门口**等人说话。

    为什么要卡住：这一条要的是「人是**在探路跑着的时候**说的」——而工作线程从 `/run`
    到进 `explore` 之间只有几毫秒，靠 sleep 去抢是运气、不是判据（A1 那一族的老毛病）。
    """

    def __init__(self, deps, inside: threading.Event, go: threading.Event):
        self.deps = deps
        self.inside, self.go = inside, go
        self.invokes: list = []
        # ⚠️ `visits` 预置成 `["explore"]`：这是**桩在表达那一幕**——「这一趟正在探路里跑」
        #    （`_where_it_stopped` 的第三档读的就是它）。不预置的话 `running_step` 是空串，
        #    `/say` 会按「不在探路里」回 `queued` —— 那这一条就只证明了「喂下去了」，
        #    证明不了「页面说直达的那一趟，话真的喂下去了」（两句承诺在同一次跑里对上）。
        self.state = _Snap(values={"visits": ["explore"]})

    def invoke(self, payload, config):
        self.invokes.append(payload)
        self.inside.set()
        assert self.go.wait(10), "测试没放走这一趟 invoke"
        # ⚠️ `should_pause` 照**真图**那条路给（`graph._explore` 递的就是 `deps.should_pause`）：
        #    不给的话「人按了停」在这一趟里**一次都不会被问**，实例 G 那一幕就演不出来。
        self.deps.explore(payload.get("url") or URL, payload.get("goal") or GOAL,
                          should_pause=self.deps.should_pause)
        self.state = _Snap(values={"site": SITE, "visits": ["explore"],
                                   "end_reason": "explore_unfinished",
                                   "end_note": "探了一趟就收工（这一条只关心那条通道）。"})
        return dict(self.state.values)

    def get_state(self, config):
        return self.state

    def update_state(self, config, values, as_node=None):
        self.state = _Snap(values={**self.state.values, **values}, next=(as_node,),
                           interrupts=())
        return {}


def _registered_job(svc, *, job_id="job-steer", stage="explore", **over):
    """登记表里放一个**正在探路里跑**的 job（`/say` `/live` 那一档不读 checkpoint）。"""
    job = service.Job(job_id=job_id,
                      brief={"site": SITE, "url": URL, "goal": GOAL},
                      status=service.RUNNING, created_at="2026-09-19T00:00:00+08:00", **over)
    job.running_step = stage
    job.graph = FakeGraph(steps=[_Snap(values={"site": SITE, "visits": ["intake"]})])
    svc._jobs[job_id] = job
    return job


def _queued(job, text):
    job.inbox.append({"text": text, "at": "2026-09-19T00:00:00+08:00",
                      "delivered": False, "superseded": False})
    return job.inbox[-1]


def _wired_client(tmp_path, monkeypatch, *, wired=True):
    """一个装好桩的 app + 它的服务；`wired` 就是那个开关（**默认打开**：这一节测的是通道本身）。

    ⚠️ 打开它必须**显式**写在每一处（`monkeypatch.setattr`）—— 生产那一条由
    `test_the_channel_stays_shut_while_the_switch_is_off` 钉着（R1）。
    """
    monkeypatch.setattr(service, "STEER_WIRED", wired)
    client = _client(graph_factory=_factory(FakeGraph(steps=[_Snap(values={"site": SITE})])),
                     window=StubWindow(alive=True))
    return client, client.app.state.service


def test_the_service_hands_over_the_waiting_words(tmp_path, monkeypatch):
    """喂的集合与翻的集合**都不是这里自己判的**（R2/R3）——这一条钉的是那两件事的结果。

    - 还在等的那几句 ⇒ 交给它的下一轮（**一次交完、按说过的先后**）；
    - 那一轮**真的发出去了**之后**那几条**才翻成 `delivered`（翻的是那条**自己**）；
    - 不再摆给他的那条（`superseded`）**既不喂也不翻**（R3）；
    - 同一条话**只交一次**（第二次问它就没有了）。
    """
    _client, svc = _wired_client(tmp_path, monkeypatch)
    job = _registered_job(svc)
    _queued(job, "第一句")
    _queued(job, "第二句")
    job.inbox.append({"text": "算了还是点上面那个", "at": "2026-09-19T00:00:00+08:00",
                      "delivered": False, "superseded": True})

    steer = svc._steer_cb(job.job_id)
    assert steer is not None
    assert steer() == "第一句\n第二句", "还在等的那几句一次交给它（按说过的先后）"
    steer.went_out()                       # ← 那一轮真的发出去了（`_Gate` 那一层回填的）
    assert [x["delivered"] for x in job.inbox] == [True, True, False], \
        "送到的那两条翻；**没送出去的那条一格都不许翻**（没送到就是没送到）"
    assert steer() is None, "同一条话只交一次（它已经到过它手上了）"

    landed = [e for e in job.timeline.all() if e["kind"] == "steer_landed"]
    assert len(landed) == 1, landed
    assert "已经交给它了" in landed[0]["say"], landed[0]
    assert landed[0]["data"]["text"] == "第一句\n第二句", landed[0]
    assert landed[0]["who"] == "system", "这是**服务兑现**那句话（不是人说的，也不是它说的）"


def test_neither_the_flip_nor_the_words_happen_until_the_round_really_goes_out(tmp_path, monkeypatch):
    """翻的**时刻**（R6 + F2③）：**那一轮真的发出去了** —— 不是被调用那一刻，也不是交出去那一刻。

    三个时刻分开量（这一条就是 F2③ 要求的那件事：「放进了消息」≠「发出去了」）：
      ① 什么都没等的时候问它 ⇒ **一格都不许动**（它每轮都会被问）；
      ② 队里有话、把它交出去 ⇒ **还是不翻、也还没有那条时间线**（那一次调用可能发不出去）；
      ③ 那一轮真发出去了 ⇒ 这时才翻、才有「已经交给它了」。
    """
    _client, svc = _wired_client(tmp_path, monkeypatch)
    job = _registered_job(svc)
    steer = svc._steer_cb(job.job_id)

    assert steer() is None, "队里没话 ⇒ 没有可交的"
    assert [e["kind"] for e in job.timeline.all()] == []

    entry = _queued(job, SAID)
    assert entry["delivered"] is False, "进了队**不等于**送到了（这一步只是人说了）"
    assert steer() == SAID
    assert entry["delivered"] is False, "交出去也**还不算**送到（那一轮还没发出去）"
    assert [e["kind"] for e in job.timeline.all()] == [], \
        "那一刻**不许**说「已经交给它了」—— 它可能压根发不出去（F2）"

    steer.went_out()
    assert entry["delivered"] is True
    assert [e["kind"] for e in job.timeline.all()] == ["steer_landed"]


def test_the_words_are_handed_over_again_after_a_round_that_never_went_out(tmp_path, monkeypatch):
    """那一轮**没发出去** ⇒ 一个字都不翻，而且**下一轮照样交给它**（话不会因为一次失败就没了）。"""
    _client, svc = _wired_client(tmp_path, monkeypatch)
    job = _registered_job(svc)
    entry = _queued(job, SAID)
    steer = svc._steer_cb(job.job_id)

    assert steer() == SAID
    steer.missed()                          # ← 那一次调用没成（`_Gate` 那一层回填的）
    assert entry["delivered"] is False, "没发出去就是没送到"
    assert [e["kind"] for e in job.timeline.all()] == []
    assert steer() == SAID, "下一轮还得把它交给它（它上一轮没看到）"


def test_the_pure_route_functions_know_the_one_case_we_already_know():
    """**F1a** 在**纯函数**这一层也要钉住（端点在它上面只读不判，判据不许两处）。

    `held` = 服务**已经知道**这一句交不出去（今天只有「有个还没兑现的停」那一支）。
    ⚠️ 它默认 `False` ⇒ Task 8 写的那几行**逐字不变**（`steer` 那一格的意思也一个字没变：
    那是「通道接上了没有」，不是「这一句交得出去吗」——两个事实两个名字）。
    """
    assert service.say_route(service.RUNNING, "explore", steer=True, held=True) == "queued"
    assert service.input_mode(service.RUNNING, "explore", steer=True, held=True) == "queue"
    assert service.say_route(service.RUNNING, "explore", steer=True) == "delivered"
    assert service.input_mode(service.RUNNING, "explore", steer=True) == "steer"
    # 别的档位它一个都不许动（闸上就是闸上、排队就是排队）
    assert service.input_mode(service.WAITING, "intake", steer=True, held=True) == "gate"
    assert service.say_route(service.QUEUED, "", steer=True, held=True) == "queued"
    assert service.say_route(service.RUNNING, "selftest", steer=True, held=True) == "queued"


def test_the_channel_stays_shut_while_the_switch_is_off(tmp_path, monkeypatch):
    """**R1 的正身**：`STEER_WIRED` 是 `False` ⇒ 这条线**根本不存在**（探路走今天那条路）。

    判据是**喂的那根线本身**（`explore` 收到的是 `None`），不是「有没有翻那一格」——
    后者换个坏实现（喂了但忘了翻）也会绿。
    """
    assert service.STEER_WIRED is False, "这一版写死 False（真站演练推迟到 Task 10 那趟窗口）"
    _client, svc = _wired_client(tmp_path, monkeypatch, wired=False)
    job = _registered_job(svc)
    _queued(job, SAID)

    assert svc._steer_cb(job.job_id) is None, "开关没打开 ⇒ 没有那根线（`explore` 收到 `None`）"
    assert job.inbox[0]["delivered"] is False
    assert [e["kind"] for e in job.timeline.all()] == []


def test_say_while_a_stop_is_pending_does_not_promise_direct_delivery(tmp_path, monkeypatch):
    """**F1(a)**：服务**已经知道**交不出去时（有未兑现的「停」），**不许承诺**「直达下一轮」。

    这一条是 F1 实例 G 的那一半（确定性：人按了「停下，我要说一句」，然后说那句话）。
    原先 `/say` 回 `delivered` +「这句话**直达**它的下一轮了」、`/live.input.mode = "steer"`
    （页面：「会进它的下一轮」）—— 而 `_steer_cb` 一个字都不会交（下一轮边界就会被那道闸掐断）。

    判据两半（**替掉 Task 8 那条 503 占位用例里被删掉的那一行守着的那一类**：
    「送到没送到都说不清的时候，不许把话**说成**送到了，也不许把它咽掉」）：
      ① 不许承诺：回 `queued`、页面那一行是「排队」、时间线上没有「已经交给它了」；
      ② 也不许咽掉：这句话**真的在队里**（页面看得见，`/again` 会带上）。
    """
    client, svc = _wired_client(tmp_path, monkeypatch)
    _registered_job(svc, stop_requested=True)

    r = client.post("/job/job-steer/say", json={"text": SAID})
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["delivered"] is False and body["queued"] is True, body
    assert "直达" not in body["say"], body["say"]
    assert "排着" in body["say"], body["say"]

    live = _live(client, "job-steer")
    assert live["input"]["mode"] == "queue", \
        "下一轮不会发生 ⇒ 页面那一行**不许**说「直达」（那是页面照 `mode` 渲染的）"
    assert [x["text"] for x in live["input"]["queued"]] == [SAID], "话也没被咽掉"
    assert not [e for e in live["events"] if e["kind"] == "steer_landed"], live["events"]


# ── 真那条链：`/run` → 桩图 → 服务的 `deps.explore` → 真 `explore` → 桩 MCP + 桩模型 ──


def _chain(tmp_path, monkeypatch, *, wired=True, fail_on=None, block_first=False,
           queued_first=True, turns=None, block_if=None, brief_over=None) -> dict:
    """走**真那条链**起一趟探路，**卡在门口**（`_HoldGraph`），把几只「放行」的手柄交给测试。

    不靠 sleep 抢时序：`invoke` 卡在 `go` 上、桩模型可以卡在 `ready`/`model_go` 上 ——
    于是「人是**在探路跑着的时候**说的」「话是在**那一轮问过 steer 之后**才说的」这两幕
    都是**造出来的**，不是碰上的。

    `queued_first`：话在探路开始**之前**就在队里（第 1 轮问 `steer()` 时就交出去）。
    """
    monkeypatch.setattr(service, "STEER_WIRED", wired)
    turns = turns or [{"calls": [("observe", {})]}, {"content": "看完了"}]
    ready, model_go = (threading.Event(), threading.Event()) if block_first else (None, None)
    made: list = []

    def _new_client():
        stub = _StubLLM(turns, fail_on=fail_on, block_if=block_if,
                        block_first=(ready, model_go) if block_first else None)
        made.append(stub)
        return stub

    monkeypatch.setattr(llm, "client", _new_client)

    program = tmp_path / "steer-program.json"
    program.write_text(json.dumps({"responses": {"observe": [{"structured": PAGE_LANDING}]}}),
                       encoding="utf-8")
    wrapper = tmp_path / "steer-mcp-wrapper"
    wrapper.write_text('#!/bin/sh\nexec %s %s "%s"\n'
                       % (sys.executable, ROOT / "tests" / "stub_mcp_server.py", program),
                       encoding="utf-8")
    wrapper.chmod(0o755)

    inside, go = threading.Event(), threading.Event()
    client = _client(graph_factory=lambda brief, deps: _HoldGraph(deps, inside, go),
                     window=StubWindow(alive=True), mcp_bin=str(wrapper),
                     shots_dir=str(tmp_path / "shots"))
    job_id = client.post("/run", json=_brief(tmp_path, **(brief_over or {}))).json()["job_id"]
    assert inside.wait(10), "这一趟没进到 invoke 里"
    svc = client.app.state.service
    job = svc._jobs[job_id]
    if queued_first:
        with job.lock:
            _queued(job, SAID)
    # ⚠️ 桩模型是**探路真跑起来之后**才建的（这一刻它还卡在门口）—— 所以给的是那个 list，
    #    等跑完再取 `made[0]`（`_steer_over_the_service` 就是这么做的）。
    return {"client": client, "svc": svc, "job": job, "job_id": job_id, "made": made,
            "go": go, "ready": ready, "model_go": model_go}


def _say(out: dict, text: str = SAID):
    r = out["client"].post("/job/%s/say" % out["job_id"], json={"text": text})
    assert r.status_code == 202, r.text
    return r.json()


def _steer_over_the_service(tmp_path, monkeypatch, *, wired: bool) -> dict:
    """**说一句 → 放它跑完**这一趟（端到端那几条用的就是它）。"""
    out = _chain(tmp_path, monkeypatch, wired=wired)
    out["say"] = _say(out)
    # ⚠️ `/live` 要**在它还卡着的时候**读（放开之后这一趟就跑完了，`mode` 跟着 `status` 变
    #    —— 那时候看到的「不是 steer」说的是另一件事）。
    out["live"] = _live(out["client"], out["job_id"])
    out["go"].set()
    _settle(out["client"])                # 队列空下来 = 这一次 invoke 真的做完了
    out["stub"] = out["made"][0]
    return out


def _kinds(out: dict) -> list:
    return [e["kind"] for e in out["job"].timeline.all()]


def steer_over_the_real_chain(tmp_path, monkeypatch) -> set:
    """`steer_landed` 那一条**真说得出来**的场景（`tests/test_service_events.py` 的目录表用它）。

    为什么要跨文件借这一个：它是 Task 9 加的**新词**，而那份目录表的机械判据要求
    「`KINDS` 里每一个有调用点的词，都得有一条**真说得出来**的场景」——
    场景只有一处实现，抄第二份就是两份口径。
    """
    return set(_kinds(_steer_over_the_service(tmp_path, monkeypatch, wired=True)))


def steer_missed_over_the_real_chain(tmp_path, monkeypatch) -> set:
    """`steer_missed` 那一条的场景（同一个目录表用的，理由同上）。

    ⚠️ 走 `/say`（不是预先塞进队里）：**只有被承诺过直达的话**才说得出口「没送到」
    （回归 2 / NEW-1 改准的那一格）—— 预先塞进去的那些没有 `promised`，服务从没答应过。
    """
    out = _chain(tmp_path, monkeypatch, fail_on=1, queued_first=False)
    _say(out)
    out["go"].set()
    _settle(out["client"])
    return set(_kinds(out))


def test_the_words_reach_the_model_over_the_real_chain(tmp_path, monkeypatch):
    """端到端那一格：**服务说「直达」的那一趟，话真的进了模型的眼睛**。

    这一条把**同一趟真运行**里的三件事串起来（分开各测一条的话，「页面说直达」与
    「话真喂下去了」可以各自为真而合起来假）：① `/say` 回 `delivered`（那正是页面上
    那一行「直达」的来源）；② 那句话（连提醒）**在模型收到的那片 messages 里**；
    ③ 时间线上有一条「已经交给它了」、那条话翻成了已送出。
    """
    out = _steer_over_the_service(tmp_path, monkeypatch, wired=True)
    assert out["say"]["delivered"] is True and out["say"]["queued"] is False, out["say"]
    assert out["live"]["input"]["mode"] == "steer", out["live"]["input"]
    # ⚠️ 扫**所有**桩（不是 `made[0]`）：同一会话里若有别的 job 还在跑，`llm.client` 被本测试的
    #    monkeypatch 接管 ⇒ `made` 里会多出**别人的**桩（复审那趟就撞上了：他们自己的 D1
    #    末尾那个 `/again` 起的 job 一直跑到下一个用例里）。
    seen = [m for stub in out["made"] for m in stub.calls[0]["messages"]]
    assert any(SAID in str(m.get("content") or "") and REMINDER in str(m.get("content") or "")
               for m in seen if m.get("role") == "user"), seen
    kinds = [e["kind"] for e in out["job"].timeline.all()]
    assert "steer_landed" in kinds, kinds
    assert "steer_missed" not in kinds, "真送到了的话**不许**再报一次「没送到」"
    assert out["job"].inbox[0]["delivered"] is True, "真交出去了 ⇒ 那一条翻成已送出"


def test_nothing_reaches_the_model_while_the_switch_is_off(tmp_path, monkeypatch):
    """同一趟真链、同一个 `/say` —— 开关没打开时**一个字节都不许变**。

    这一条与上面那条是**同一段代码的两个分支**（只差那个开关），所以它证明的是
    「生产今天走的那条路上，人的话不会以任何形式进到模型上下文里」。
    """
    out = _steer_over_the_service(tmp_path, monkeypatch, wired=False)
    blob = json.dumps(out["stub"].calls, ensure_ascii=False)
    assert SAID not in blob, "开关没打开，那句话却进了模型上下文"
    assert out["say"]["queued"] is True and out["say"]["delivered"] is False, out["say"]
    assert out["live"]["input"]["mode"] == "queue", out["live"]["input"]
    assert out["job"].inbox[0]["delivered"] is False, "没送到就是没送到（`/again` 还要带上它）"
    kinds = _kinds(out)
    assert "steer_landed" not in kinds, kinds
    assert "steer_missed" not in kinds, "这条线根本没接上 ⇒ 没有「没送到」这回事可说"
    assert "human_said" in kinds, "人说过这句话这件事**照样要说**（时间线上不能一片安静）"


# ── 说话时的两个「交不出去」的瞬间（F1 的实例 G / H，都在真链上量）────────────────


def test_say_after_a_stop_is_pressed_end_to_end(tmp_path, monkeypatch):
    """**F1 实例 G**（人按了「停下，我要说一句」之后再说那句话）：既不许假承诺，也不许沉默。

    这是那个按钮**设计出来的用法**（设计注 §4.1 第一行：停下来的那一刻，你说的话已经排好了）。
    原先：`/say` 回 `delivered` +「直达它的下一轮」、页面说「会进它的下一轮」，而 `_steer_cb`
    一个字都不交（下一轮边界就被那道闸掐断）⇒ **时间线上一个字都没说**。
    现在：那一刻服务**已经知道**交不出去 ⇒ 回话与页面都改口成「排队」（F1a）；
    跑完之后**结尾那一笔**把「这几句没送到它手上」说出来（F1b）。
    """
    out = _chain(tmp_path, monkeypatch, queued_first=False)
    client, job_id = out["client"], out["job_id"]
    assert client.post("/job/%s/stop" % job_id, json={}).status_code == 200
    assert out["job"].stop_requested is True, "按了停（还没兑现）"
    assert _live(client, job_id)["input"]["mode"] == "queue", "还没说话，但这一行已经不该说「直达」"

    body = _say(out)
    assert body["delivered"] is False and body["queued"] is True, body
    assert "直达" not in body["say"], body["say"]

    out["go"].set()
    _settle(client)
    kinds = _kinds(out)
    assert "steer_landed" not in kinds, "下一轮根本没发生 ⇒ 不许说「已经交给它了」"
    # ⚠️ 也**不许**报「没送到」（回归 2 / NEW-1）：这一句 `promised` 那一格**没盖上**
    #    （`/say` 当时回的是排队，页面那一行也是「排队」）—— 服务从没答应过直达，
    #    到头了再去替它收回一句没给过的承诺，就是编话（理由句也会指错事）。
    assert "steer_missed" not in kinds, kinds
    assert out["job"].inbox[0]["delivered"] is False
    assert out["job"].inbox[0].get("promised") is False, out["job"].inbox[0]
    assert service.Service._unsent_texts(out["job"].inbox) == [SAID], "话没丢：`/again` 会带上"


def test_words_said_after_the_last_round_are_reported_missed(tmp_path, monkeypatch):
    """**F1 实例 H**（连「停」都不需要）：话是在**最后一次 `steer()` 问过之后**才说的。

    那一轮没有 tool_calls ⇒ 循环结束、不会再来一轮 —— 而 `/say` 那一刻服务**不可能知道**
    （这是这条通道的固有形状，不是漏判）：所以 `delivered` 照旧承诺。**承诺兑现不了，
    就必须在结尾说出来**（F1b 的正身：丢的不是话，是那句承诺）。
    """
    # ⚠️ `block_if`：这句开场白只有**这一趟**有 —— 别的 job（同会话里别的用例留下的）
    #    也会从这个桩上取客户端，不认准的话它会先把「卡住」做掉（复审的 H 脚本就这么串过味：
    #    跑整份文件时 `ready` 是**别人**置的，于是 `/say` 落在探路开始之前，结论整个反过来）。
    out = _chain(tmp_path, monkeypatch, block_first=True, queued_first=False,
                 turns=[{"content": "我探完了"}],
                 block_if=_H_GOAL, brief_over={"goal": _H_GOAL})
    out["go"].set()                                  # 放它进 explore
    assert out["ready"].wait(10), "桩模型没卡在第 1 轮 create 里"
    body = _say(out)                                 # ← 此刻 `steer()` 已经问过了
    assert body["delivered"] is True, "那一刻服务确实不知道后面没有下一轮了：%r" % body
    out["model_go"].set()                            # 放走那一轮 → 它没有 tool_calls → 循环结束
    _settle(out["client"])

    kinds = _kinds(out)
    assert "steer_landed" not in kinds, "那句话从没进过任何一次调用 ⇒ 不许说交给它了"
    assert "steer_missed" in kinds, "承诺过「直达」而没送到 ⇒ 结尾必须说"
    missed = [e for e in out["job"].timeline.all() if e["kind"] == "steer_missed"][0]
    assert service.STEER_MISSED_ENDED_SAY in missed["say"], missed["say"]
    # 判据全落在**这个 job 自己**身上（不扫桩）：`steer_landed` 与 `delivered` 都只在
    # 「那一轮真的发出去了」时才落（F2③），所以「没有那一条 + 还留在队里」= 它确实没见过
    # —— 而上面那条 `steer_missed` 把这件事说了出来。
    assert out["job"].inbox[0]["delivered"] is False
    assert service.Service._unsent_texts(out["job"].inbox) == [SAID]


def test_the_round_that_dies_takes_neither_the_words_nor_the_truth_with_it(tmp_path, monkeypatch):
    """**F2**：话交出去之后**那一次调用失败**（网络/4xx —— 不需要抢时序，任何一次失败都会踩到）。

    原先这条路：条目被标成 `delivered`（`/again` 说「你说过的 **0** 句话」、页面上再也看不到
    那句话），而时间线上留着一条**假话**「已经交给它了 —— 它下一轮就会看到」。
    现在三样都对：① 话还在队里（`/again` 与页面都还看得见）；② 时间线有一条**说清那一轮
    没发出去**；③ 「已经交给它了」**根本没记**（那条消息没发出去，就没什么可「已经」的）。
    """
    out = _chain(tmp_path, monkeypatch, fail_on=1, queued_first=False)
    out["say"] = _say(out)            # ← 走 `/say`：探路里跑着 ⇒ 承诺了直达（`promised`）
    assert out["say"]["delivered"] is True, out["say"]
    out["go"].set()
    _settle(out["client"])
    job = out["job"]

    assert job.status == service.FAILED, job.status
    kinds = _kinds(out)
    assert "steer_landed" not in kinds, "那一次调用失败了 ⇒ 不许说「已经交给它了」（F2③）"
    assert "steer_missed" in kinds, "F2②：那一轮没发出去这件事必须说"
    missed = [e for e in job.timeline.all() if e["kind"] == "steer_missed"][0]
    assert SAID in missed["data"]["text"], missed
    assert service.STEER_MISSED_DIED_SAY in missed["say"], missed["say"]
    # F2①：话没丢 —— `/again` 与页面都还看得见它
    assert job.inbox[0]["delivered"] is False
    assert service.Service._unsent_texts(job.inbox) == [SAID], "`/again` 必须还带得上这句"
    live = _live(out["client"], out["job_id"])
    assert [x["text"] for x in live["input"]["queued"]] == [SAID], live["input"]


def _gate_graph(interrupts=True, end_reason=""):
    """一跳停在闸上（`interrupts=True`）或者一跳就**到头**（`end_reason` 非空 = 撞上限那种）。"""
    return FakeGraph(steps=[_Snap(values={"site": SITE, "visits": ["intake"],
                                          "end_reason": end_reason,
                                          "end_note": "打回 2 次还是同样的地方不过。"},
                                  interrupts=((_gate("draft"),) if interrupts else ()))])


def test_words_that_were_never_promised_are_not_reported_as_missed(tmp_path, monkeypatch):
    """**防滥报（NEW-1 / O1）**：服务**从没承诺过直达**的话，到头了也**不许**报「没送到」。

    判据是**那句话当初是按哪条路收下的**（条目上那一格 `promised`），不是「队里还有没有话」：
    排队收下的那些，`/say` 当时说的就是「到下一道闸进输入框，按一下才送」——**那本来就是诚实的**
    （R1），到头了再去替它收回一句从没给过的承诺，就是**编话**（而且会把那两句理由句也说错：
    「这一趟探路结束了 / 那一轮没发出去」跟它没发生过的事对不上）。
    ⚠️ 这一跳必须**真的到头**（`end_reason` 非空 —— 停在闸上不算到头），不然什么都没量到。
    """
    client, svc = _wired_client(tmp_path, monkeypatch)
    job = _registered_job(svc, job_id="job-gate", stage="draft")   # ⚠️ 不在探路里 ⇒ `/say` 只排队
    _queued(job, SAID)                                             #（没盖 `promised` 那一格）
    job.graph = _gate_graph(interrupts=False, end_reason="lint_cap")

    svc._advance(job, None)

    assert "cap_hit" in [e["kind"] for e in job.timeline.all()], "这一跳真的到头了（撞上限）"
    assert "steer_missed" not in [e["kind"] for e in job.timeline.all()]
    assert job.inbox[0]["delivered"] is False, "它当然没送到 —— 但**没人承诺过**，所以不报"


def test_a_promise_that_died_at_a_gate_is_taken_back(tmp_path, monkeypatch):
    """**承诺了却停在闸上（NEW-1 / O2）** ⇒ 那一刻就要收回（不是等到「到头」）。

    承诺过「直达下一轮」的话，如果这一趟**在它下一次问模型之前就停在闸上了**，
    那就是「没直达」——两个出口（到头 **或** 停在闸上）都得说，不然承诺就烂在时间线上了。
    ⚠️ 停在闸上时那几句**还有个去处**（页面把它们摆进输入框，人按一下才送）—— 这句要说全。
    """
    client, svc = _wired_client(tmp_path, monkeypatch)
    job = _registered_job(svc, job_id="job-gate")                  # 探路里跑着 ⇒ `/say` 会承诺直达
    client.post("/job/job-gate/say", json={"text": SAID})
    assert job.inbox[0].get("promised") is True, "承诺过的条目要盖那一格：%r" % job.inbox[0]
    job.graph = _gate_graph()

    svc._advance(job, None)                                       # 停在 `draft` 那道闸上

    missed = [e for e in job.timeline.all() if e["kind"] == "steer_missed"]
    assert len(missed) == 1, [e["kind"] for e in job.timeline.all()]
    assert SAID in missed[0]["data"]["text"], missed[0]
    assert "停在闸上" in missed[0]["say"], missed[0]["say"]
    assert job.inbox[0]["delivered"] is False


def test_the_same_sentence_is_not_taken_back_twice(tmp_path, monkeypatch):
    """**同一句话只说一次（C7）**：停在闸上说了一次，后来这一趟到头了**不许**再说一遍。"""
    client, svc = _wired_client(tmp_path, monkeypatch)
    job = _registered_job(svc, job_id="job-gate")
    client.post("/job/job-gate/say", json={"text": SAID})
    job.graph = _gate_graph()
    svc._advance(job, None)                                       # 停在闸上 ⇒ 收回一次

    job.graph = _gate_graph(interrupts=False, end_reason="lint_cap")
    svc._advance(job, None)                                       # 这一跳到头了

    said = [e for e in job.timeline.all() if e["kind"] == "steer_missed"]
    assert len(said) == 1, "同一句话说了两遍：%r" % [e["say"] for e in said]


def test_a_second_promise_of_the_same_words_is_taken_back_too(tmp_path, monkeypatch):
    """**承诺过的每个出口都要报（R3）**：同一句话说了两遍 ⇒ **两条承诺**，两条都要收回。

    这一条是复审判出来的那个残余：`steer_missed_reported` 原先**按文本**去重 ——
    同文说两遍，第二遍那条承诺**每个出口都不会被收回**（一条残余的静默），
    而 docstring 那一轮刚写下「承诺过的每个出口都要报」。
    ⚠️ 形状是**可达**的：页面本来就预填上一句没送出去的话（`draft_note`），
    人再按一次发送就是这个样子。
    """
    client, svc = _wired_client(tmp_path, monkeypatch)
    job = _registered_job(svc, job_id="job-twice")
    client.post("/job/job-twice/say", json={"text": SAID})
    job.graph = _gate_graph()
    svc._advance(job, None)                       # ① 停在闸上 ⇒ 收回第一条承诺

    with job.lock:                                # `/reply` 之后这一趟又跑起来了（同一个形状）
        job.status = service.RUNNING
    job.running_step = "explore"
    assert client.post("/job/job-twice/say", json={"text": SAID}).status_code == 202   # 同文第二遍
    job.graph = FakeGraph(raise_on=[1])           # ② 这一趟跑挂 ⇒ 第二条承诺也要收回
    svc._advance(job, None)

    said = [e for e in job.timeline.all() if e["kind"] == "steer_missed"]
    assert len(said) == 2, "两条承诺只收回了 %d 条：%r" % (len(said), [e["say"] for e in said])
    assert all(SAID in e["data"]["text"] for e in said), said
    assert service.STEER_MISSED_AT_GATE_SAY in said[0]["say"], said[0]["say"]
    assert service.STEER_MISSED_DIED_SAY in said[1]["say"], said[1]["say"]


def test_words_still_queued_at_a_gate_are_not_reported_as_missed(tmp_path, monkeypatch):
    """反面（**防滥报**）：没承诺过的话停在闸上 —— 一个字都不说（那是**正常的排队**）。

    停在闸上的那几句：页面下一屏就把它们摆进输入框，人按一下才送。在那儿说「它没看到」
    是**假话**（它只是还没轮到）—— 「该说的要说」的另外半边。
    """
    client, svc = _wired_client(tmp_path, monkeypatch)
    job = _registered_job(svc, job_id="job-gate", stage="draft")   # 不在探路里 ⇒ 只排队
    _queued(job, SAID)
    job.graph = _gate_graph()

    svc._advance(job, None)

    assert "steer_missed" not in [e["kind"] for e in job.timeline.all()]
    live = _live(client, "job-gate")
    assert [x["text"] for x in live["input"]["queued"]] == [SAID], "它只是排着，下一道闸会摆出来"
    assert live["input"]["draft_note"] == SAID
