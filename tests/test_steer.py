"""Task 9：插话进探路（`steer` 通道）—— 三层各钉各的。

| 层 | 它承诺什么 | 用例 |
|---|---|---|
| `llm.run_tool_loop(steer=…)` | 每一次模型调用**之前**问一句；非空就作为一条 `user` 消息插进去；那一轮的 record 记 `steered` | `…goes_into_the_round_that_comes_next` / `…asked_before_every_model_call` / `…saying_nothing…` / `…no_steer_at_all…` |
| `explore(steer=…)` | 透传；**插话之后那一轮就收尾**时往 `journey.notes` 追一句人话（设计注 §3.4 的 R11） | `…hands_the_line_down…` / `…wrapping_up_right_after…`（+ 负例） |
| 服务 | 把 `Job.inbox` 里**还在等**的那些交出去、标 `delivered`、时间线上一条「已经交给它了」 | `…hands_over_the_waiting_words…` / `…only_flips_when…` / `…stays_shut…` / `…pending_stop…` / `…real_chain…` |

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

from agent import browser_agent, llm, service  # noqa: E402
from test_browser_agent import PAGE_LANDING, _reply, _stub  # noqa: E402
from test_service_input import (GOAL, SITE, URL, FakeGraph, StubWindow,  # noqa: E402
                                _Snap, _brief, _client, _factory, _live, _settle)

#: 人插的那句话（**逐字**用在两处：插进模型的那条消息里、时间线上）。
SAID = "不是那个按钮，是下面那个"
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
    """

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
        self.calls.append({**kwargs, "messages": copy.deepcopy(kwargs.get("messages"))})
        turn = self.turns[min(len(self.calls) - 1, len(self.turns) - 1)]
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
    插晚了这一轮就看不到）；② 那句提醒**逐字**在里头；③ record 的 `steered` 是人那句话。
    """
    stub = _StubLLM([{"calls": [("observe", {})]}, {"content": "讲完了"}])
    rounds = llm.run_tool_loop("sys", "usr", [], _dispatch([]), max_rounds=5,
                               _client=stub, steer=_once(SAID))

    first = stub.calls[0]["messages"]
    assert first[-1]["role"] == "user", "插进去的那条必须是最后一条（协议：它得跟在工具结果后面）"
    assert SAID in first[-1]["content"], first[-1]
    assert REMINDER in first[-1]["content"], (
        "插进去的话**必须带那句提醒** —— 那道门上没有 `done()`，「不调工具」=它宣布讲完了（R11）")
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
        self.deps.explore(payload.get("url") or URL, payload.get("goal") or GOAL)
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


def test_the_service_hands_over_the_waiting_words_and_marks_them(tmp_path, monkeypatch):
    """喂的集合与翻的集合**都不是这里自己判的**（R2/R3）——这一条钉的是那两件事的结果。

    - 还在等的那几句 ⇒ 交给它的下一轮（**一次交完、按说过的先后**）；
    - 交出去之后**那几条**翻成 `delivered`（翻的是那条**自己**，不是追加一条新的）；
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
    assert [x["delivered"] for x in job.inbox] == [True, True, False], \
        "送到的那两条翻；**没送出去的那条一格都不许翻**（没送到就是没送到）"
    assert steer() is None, "同一条话只交一次（它已经到过它手上了）"

    landed = [e for e in job.timeline.all() if e["kind"] == "steer_landed"]
    assert len(landed) == 1, landed
    assert "已经交给它了" in landed[0]["say"], landed[0]
    assert landed[0]["data"]["text"] == "第一句\n第二句", landed[0]
    assert landed[0]["who"] == "system", "这是**服务兑现**那句话（不是人说的，也不是它说的）"


def test_the_delivered_flag_only_flips_when_the_words_really_went_out(tmp_path, monkeypatch):
    """`delivered` 翻的**时刻**（R6）：`steer()` 把话交出去的那一刻 —— 不是被调用那一刻。

    这一条把两个时刻分开量：① 什么都没等的时候问它 ⇒ **一格都不许动**（它每轮都会被问）；
    ② 队里有话、把它交出去 ⇒ 那一刻才翻。
    """
    _client, svc = _wired_client(tmp_path, monkeypatch)
    job = _registered_job(svc)
    steer = svc._steer_cb(job.job_id)

    assert steer() is None, "队里没话 ⇒ 没有可交的"
    entry = _queued(job, SAID)
    assert entry["delivered"] is False, "进了队**不等于**送到了（这一步只是人说了）"
    assert steer() == SAID
    assert entry["delivered"] is True
    assert [e["kind"] for e in job.timeline.all()] == ["steer_landed"], \
        "只有**真的交出去**那一次才有一条时间线（被调用不算）"


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


def test_a_pending_stop_holds_the_words_back(tmp_path, monkeypatch):
    """有个还没兑现的「停」⇒ 这一轮**不会发生**（`_Gate` 一上来就抛 `_Stop`）⇒ 一个字都不许交。

    不挡会怎样（**这条是本片自己拍的**，R 里没有）：话被标成「已送出」，可消息根本没发出去
    —— 而且 `/again` 也不会再带上它（`_unsent_texts` 只带**没送出去**的）：
    人的话**两头都没有了**。挡住的代价只是它继续排在队里（页面照旧看得见）。
    """
    _client, svc = _wired_client(tmp_path, monkeypatch)
    job = _registered_job(svc, stop_requested=True)
    _queued(job, SAID)

    assert svc._steer_cb(job.job_id)() is None
    assert job.inbox[0]["delivered"] is False, "没送到就是没送到"
    assert [e["kind"] for e in job.timeline.all()] == []


# ── 真那条链：`/run` → 桩图 → 服务的 `deps.explore` → 真 `explore` → 桩 MCP + 桩模型 ──


def _steer_over_the_service(tmp_path, monkeypatch, *, wired: bool) -> dict:
    """走**真那条链**跑一趟「人在它探路跑着的时候说了一句话」（不靠抢时序，见 `_HoldGraph`）。"""
    monkeypatch.setattr(service, "STEER_WIRED", wired)
    turns = [{"calls": [("observe", {})]}, {"content": "看完了"}]
    made: list = []

    def _new_client():
        stub = _StubLLM(turns)
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
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    assert inside.wait(10), "这一趟没进到 invoke 里"
    r = client.post("/job/%s/say" % job_id, json={"text": SAID})
    assert r.status_code == 202, r.text
    # ⚠️ `/live` 要**在它还卡着的时候**读（放开之后这一趟就跑完了，`mode` 跟着 `status` 变
    #    —— 那时候看到的「不是 steer」说的是另一件事）。
    live_while_held = _live(client, job_id)
    go.set()
    _settle(client)                       # 队列空下来 = 这一次 invoke 真的做完了
    svc = client.app.state.service
    return {"job": svc._jobs[job_id], "stub": made[0], "client": client, "job_id": job_id,
            "say": r.json(), "live": live_while_held}


def steer_over_the_real_chain(tmp_path, monkeypatch) -> set:
    """上面那趟真链跑出来的**时间线 kind 集合**（`tests/test_service_events.py` 的目录表用它）。

    为什么要跨文件借这一个：`steer_landed` 是 Task 9 加的**新词**，而那份目录表的机械判据要求
    「`KINDS` 里每一个有调用点的词，都得有一条**真说得出来**的场景」——
    场景只有一处实现，抄第二份就是两份口径。
    """
    return {e["kind"] for e in _steer_over_the_service(tmp_path, monkeypatch, wired=True)["job"]
            .timeline.all()}


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
    seen = out["stub"].calls[0]["messages"]
    assert any(SAID in str(m.get("content") or "") and REMINDER in str(m.get("content") or "")
               for m in seen if m.get("role") == "user"), seen
    kinds = [e["kind"] for e in out["job"].timeline.all()]
    assert "steer_landed" in kinds, kinds
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
    kinds = [e["kind"] for e in out["job"].timeline.all()]
    assert "steer_landed" not in kinds, kinds
    assert "human_said" in kinds, "人说过这句话这件事**照样要说**（时间线上不能一片安静）"
