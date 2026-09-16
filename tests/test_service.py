"""Task 8：agent 服务（`agent/service.py`）—— 把图变成一个**能提交、能看进展、能答话**的服务。

这份测试钉住四件事（每一件都对应 Task 7 评审里已经吃过一次亏的地方）：

1. **提交那一刻就把缺的输入拦下**（免费）—— 缺 `success_text`、`allow_skips` 里有不认识的
   名字（P14）、显式空列表（P15）、给了 `viewport` 但这个部署没有窗口层。
   这四样都不许拖到**烧掉一次探路**之后才失败（真窗口 + 一次模型跑）。
   图自己的 intake 闸是**兜底**，不是唯一防线。
2. **`status` 与 `delivered` 是两回事**：跑挂的 job **绝不许**被读成跑成的。
   这条不是靠断言「字段等于什么」钉的，是靠**变异**（见 test_a_job_whose_runner_raised…）。
3. **R-31：服务是窗口层旋钮的供给方** —— 给了就透传；给不了就**明说**，不许默默丢掉；
   没人给也**不许**替图发明默认值（图会停在 `missing_knob` 并点名，那是**对的**）。
4. **P6：窗口没了 → 重开 + 从断点续跑** —— 不是整轮重来（`explore` 只许跑一次）。

这里没有浏览器、没有模型、没有 cdp 二进制、没有网。两条路都测：
**假图**（钉状态机：跑挂、未知 id、重启后还读得到）与**真图 + 桩依赖**
（钉接线：R-31 的旋钮真的到了 `selftest.run` 手里、产物真的落盘）。
"""

from __future__ import annotations

import copy
import json
import pathlib
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

from agent import browser_agent, graph, selftest, service  # noqa: E402

SITE = "example-funnel"
URL = "https://example-funnel.test/quiz"
GOAL = "走到「Thank you」那一页，把报价拿到"
SUCCESS = "Thank you"
WS_URL = "ws://127.0.0.1:9222/devtools/page/ABC"
NEW_WS_URL = "ws://192.168.1.197:55555/devtools/page/NEW"


# ─────────────────────────────── 桩：真图 + 桩依赖 ───────────────────────────────


def _journey(url=URL, *, stop_reason="model_done"):
    """一份**真** Journey（手写账本）—— `states()` / `fills()` / `render()` 全走真代码。"""
    steps = [
        {"state": "landing", "action": "click", "note": "点了「Get Started」",
         "target": {"text": "Get Started", "role": "button", "near": None,
                    "selectors": ["#get-started"], "above_fold_only": False},
         "result": {"ok": True, "selector": "#get-started"}},
        {"state": "landing", "action": "form", "note": "填好了「Postcode」",
         "target": {"text": None, "label": "Postcode", "role": None, "near": None,
                    "selectors": ["input#postcode"], "above_fold_only": False},
         "result": {"ok": True, "selector": "input#postcode",
                    "fill": {"name": "postcode", "source": "postcode", "kind": "value",
                             "label": "Postcode", "value": "SW1A 1AA",
                             "fallback": [{"random": "postcode"}]}}},
    ]
    return browser_agent.Journey(
        steps=steps, notes=["页面变了：现在是「Get Started」那一页", "走完了：点一次、填一个邮编"],
        stop_reason=stop_reason, final_answer="成功时页面上会出现「Thank you」",
        pages=[{"name": "landing",
                "when": {"url_contains": "example.test", "text_contains": ["Get Started"]},
                "url": url, "title": "Get Started"}])


def _pass_report(site=SITE):
    """一份「四遍真跑、第五遍按默认允许跳过」的自测报告（照 Task 6 的判据）。"""
    runs = tuple(
        selftest.Run(name=n, label="第 %d 遍" % (i + 1), status="passed", ok=True,
                     failed_step=None, trace_path="/tmp/%s.trace.jsonl" % n, note="跑通了")
        for i, n in enumerate(("baseline", "rerun", "delay", "viewport")))
    return selftest.Report(runs=runs, passed=True, allowed_skips=("country",),
                           cdp_bin="/usr/local/bin/cdp", site=site, py_path="/tmp/%s.py" % site)


class Rec:
    """桩把「谁被调了、拿到了什么」记在这儿 —— 断言看它，不看实现。"""

    def __init__(self):
        self.explore: list = []
        self.selftest: list = []


def _deps(rec: Rec, tmp_path, *, inherit=None):
    """桩依赖。`inherit` 给的是**服务拼好的**那份 `Deps` —— 把它那根窗口层的线接着用。

    为什么要接：载荷里的 `set_viewport` 是**服务**该给的东西（R-31）。桩要是自己造一根
    假的，这条测试就只证明了「桩给自己接了一根线」，证明不了服务那根线通到了窗口层。
    """
    def explore(url, goal, budget=None, should_pause=None):
        rec.explore.append({"url": url, "goal": goal})
        return _journey(url)

    def selftest_stub(py_path, ws_url, form_file, site, **kw):
        rec.selftest.append({"py_path": str(py_path), "ws_url": ws_url,
                             "form_file": form_file, "site": site, **kw})
        return _pass_report(site)

    return graph.Deps(explore=explore, selftest=selftest_stub,
                      set_viewport=(inherit.set_viewport if inherit is not None else None))


def _brief(tmp_path, **over):
    brief = {"url": URL, "goal": GOAL, "success_text": SUCCESS, "site": SITE,
             "ws_url": WS_URL, "form_file": str(tmp_path / "form.json"),
             "out_dir": str(tmp_path / "forms" / "sites")}
    brief.update(over)
    return brief


# ─────────────────────────────── 桩：假图（钉状态机）───────────────────────────────


@dataclass
class _Snap:
    """`get_state()` 返回的那个东西里，服务只准看这三个字段（其余是实现细节）。"""

    values: dict
    next: tuple = ()
    interrupts: tuple = ()


def _gate(step="intake", say="准备开工：…"):
    return Interrupt(value={"step": step, "say": say, "facts": {}, "can": ["让它继续"]}, id="i-1")


class FakeGraph:
    """一个**照着服务要的协议**做的假图：`invoke` / `get_state` / `update_state`。

    它的用处是钉**服务自己的状态机**（跑挂、未知 id、重启后从 checkpoint 读回来）——
    这些用真图很难造（真图不会自己抛异常）。
    """

    def __init__(self, *, steps=None, raise_on=None, hold=None):
        #: 每次 `invoke` 依次返回的东西；用完了就返回最后一个
        self.steps = list(steps or [])
        self.raise_on = raise_on or []
        #: `hold(第几次 invoke)` —— 让测试能**停在**一次 invoke 中间看状态（不靠抢时序）
        self.hold = hold or (lambda n: None)
        self.invokes: list = []
        self.updates: list = []
        self.state = _Snap(values={}, next=(), interrupts=())

    def invoke(self, payload, config):
        self.invokes.append(payload)
        self.hold(len(self.invokes))
        if len(self.invokes) in self.raise_on:
            raise RuntimeError("窗口连不上了：connect to 192.168.1.197:55555 failed")
        out = self.steps[min(len(self.invokes) - 1, len(self.steps) - 1)] if self.steps else {}
        if isinstance(out, _Snap):
            self.state = out
            return {"__interrupt__": list(out.interrupts)} if out.interrupts else {}
        self.state = _Snap(values=dict(out), next=(), interrupts=())
        return out

    def get_state(self, config):
        return self.state

    def update_state(self, config, values, as_node=None):
        self.updates.append({"values": dict(values), "as_node": as_node})
        self.state = _Snap(values={**self.state.values, **values}, next=(as_node,), interrupts=())
        return {"configurable": dict(config["configurable"])}


class StubWindow:
    """窗口层的替身：记下被要求换成了多大，以及「窗口还活着吗」怎么答。"""

    def __init__(self, *, alive=True):
        self.calls: list = []
        self.alive_ = alive
        self.probes = 0

    def set_viewport(self, width, height):
        self.calls.append((width, height))

    def alive(self):
        self.probes += 1
        return self.alive_


def _real_factory(rec: Rec, tmp_path, *, saver=None):
    """「真图 + 桩依赖」的工厂。**saver 只有一份** —— 每次调用都新建的话，
    服务在 reopen 时重拼的那张图会接到一份**空状态**上（那不是续跑，是重来）。

    这条约束是真的（不是测试的怪癖）：R-19 说状态住在 saver 里，所以「换一张图接着跑」
    的前提就是它接在**同一份** saver 上。
    """
    saver = saver or InMemorySaver().with_allowlist(graph.MSGPACK_ALLOWLIST)
    return lambda brief, deps: graph.build(checkpointer=saver,
                                           deps=_deps(rec, tmp_path, inherit=deps))


def _client(*, graph_factory, window=None, **kw):
    """一个装好桩的 TestClient —— **不碰**进程级默认（那会去连 Postgres / 真 Bit 窗口）。"""
    if "checkpointer" not in kw and "checkpointer_url" not in kw:
        kw["checkpointer"] = InMemorySaver().with_allowlist(graph.MSGPACK_ALLOWLIST)
    # 桩窗口默认**真的会变尺寸**：探针报的就是刚被推上去的那个尺寸。
    # （真实现要起 cdp 子进程去量，测试里不必。）要演「量不出来」「推了但没变」的，
    # 各自显式传一个 `viewport_probe`。
    if isinstance(window, StubWindow):
        kw.setdefault("viewport_probe",
                      lambda ws_url, w=window: (w.calls[-1] if w.calls else None))
    app = service.create_app(graph_factory=graph_factory, window=window, **kw)
    return TestClient(app)


def _wait(client, job_id, *, until=("waiting", "done", "failed"), timeout=20.0):
    """等 job 走到一个**不再动**的状态（服务是异步的：`POST /run` 立刻返回 job_id）。"""
    end = time.time() + timeout
    last = None
    while time.time() < end:
        last = client.get("/job/%s" % job_id).json()
        if last["status"] in until:
            return last
        time.sleep(0.02)
    raise AssertionError("job 没有在 %.0fs 内走到 %s：%r" % (timeout, until, last))


def _reply_until_done(client, job_id, *, reply=None, limit=40):
    """一路把人该说的都说了（默认「继续」），直到 job 自己停下。

    `limit` 是急停：图要是停不下来，它在这儿**红**，而不是把整个 pytest 挂死。
    """
    seen = []
    for _ in range(limit):
        view = _wait(client, job_id)
        if view["status"] != "waiting":
            return view
        seen.append(view["gate"]["step"])
        r = client.post("/job/%s/reply" % job_id, json=reply or {"action": "continue"})
        assert r.status_code == 200, r.text
    raise AssertionError("job 一直停在闸口：%r" % seen)


# ───────────────────────── 1. 提交那一刻就拦下（免费）─────────────────────────


def test_post_run_returns_a_job_id_and_parks_at_the_first_gate(tmp_path):
    """`POST /run` 立刻回一个 job_id；job 停在**第一道闸**上等人（§6.2：人每一步都在）。

    顺便钉住响应形状：`status` 是「job 走到哪了」，`delivered` 是「产物落盘了吗」——
    提交成功**不等于**跑成（这两件事在响应里分开摆）。
    """
    rec = Rec()
    fg = FakeGraph(steps=[_Snap(values={"visits": ["intake"]}, next=("intake",),
                                interrupts=(_gate(),))])
    client = _client(graph_factory=lambda brief, deps: fg)

    r = client.post("/run", json=_brief(tmp_path))
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["job_id"]
    assert body["status"] in ("queued", "running", "waiting")
    assert body["delivered"] is False, "刚提交就说交付了？"

    view = _wait(client, body["job_id"])
    assert view["status"] == "waiting"
    assert view["gate"]["step"] == "intake"
    assert view["delivered"] is False
    assert "准备开工" in view["say"], view["say"]          # 人话，不是错误码（D16）
    assert view["gate"]["can"], "闸口上得说清人能做什么（不然人以为只能点继续）"


def test_get_job_with_an_unknown_id_is_404_with_human_words(tmp_path):
    client = _client(graph_factory=lambda brief, deps: FakeGraph(steps=[{}]))
    r = client.get("/job/nope")
    assert r.status_code == 404
    assert "没这个任务" in r.json()["detail"] or "没有" in r.json()["detail"]


def test_missing_success_text_is_refused_before_any_work(tmp_path):
    """没人说「什么算成功」→ **提交那一刻**就拒（§6.1：这条只有人知道，猜不得）。

    为什么必须在这儿拦：图里的 intake 闸**也能**拦（`no_success_text`），但那要等
    一次提交 + 一次调度；而这条输入是免费的。**先贵后像别的问题**正是 Task 7 刚拆掉的形状。
    """
    fg = FakeGraph(steps=[{}])
    client = _client(graph_factory=lambda brief, deps: fg)

    brief = _brief(tmp_path)
    del brief["success_text"]
    r = client.post("/run", json=brief)
    assert r.status_code == 400, r.text
    assert "什么算成功" in r.json()["detail"]
    assert fg.invokes == [], "被拒的提交**不许**碰图（那正是「先贵后失败」）"


def test_an_unknown_allow_skips_name_is_refused_before_any_work(tmp_path):
    """P14：`allow_skips` 里写错名字，在图边界**不校验** —— 写错就烧掉一次探路才炸。

    服务在**提交那一刻**按 `selftest.RUN_NAMES` 校验，并在人话里把认的名字列出来。
    """
    fg = FakeGraph(steps=[{}])
    client = _client(graph_factory=lambda brief, deps: fg)

    r = client.post("/run", json=_brief(tmp_path, allow_skips=["contry"]))
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert "contry" in detail
    for name in selftest.RUN_NAMES:
        assert name in detail, "得把认的那几个名字列出来，人才知道该怎么改：%s" % detail
    assert fg.invokes == []


def test_an_explicit_empty_allow_skips_is_refused_not_silently_reinterpreted(tmp_path):
    """P15：显式 `allow_skips=[]`（「什么也不许跳」）与「没给」在图的边界**不可区分** ——
    两边都走 `state.get("allow_skips") or DEFAULT`，于是调用方明确的约束被**静默丢掉**。

    服务的处置是**明说**：空列表没有意义，要么别给（用默认），要么把要跳的遍列出来。
    「默默按默认办」在这里等于把人的约束吃掉，那是这套系统最忌讳的那类静默。
    """
    fg = FakeGraph(steps=[{}])
    client = _client(graph_factory=lambda brief, deps: fg)

    r = client.post("/run", json=_brief(tmp_path, allow_skips=[]))
    assert r.status_code == 400, r.text
    assert "空" in r.json()["detail"]
    assert fg.invokes == []


def test_a_viewport_we_cannot_honour_is_refused_not_dropped(tmp_path):
    """R-31：服务是窗口层旋钮的**供给方** —— 给不了就得**明说**。

    载荷里要了「换窗口大小」，而这个部署没接窗口层：**不许**默默把它丢掉
    （丢掉的后果是第 4 遍扰动被记成「没跑」，而调用方以为它跑了 —— R-5 禁止的那种谎）。
    要真的放弃第 4 遍，只有一条明路：显式把它写进 `allow_skips`。
    """
    fg = FakeGraph(steps=[{}])
    client = _client(graph_factory=lambda brief, deps: fg, window=None)

    r = client.post("/run", json=_brief(tmp_path, allow_skips=["country"], set_viewport=True))
    assert r.status_code == 400, r.text
    assert "窗口" in r.json()["detail"]
    assert fg.invokes == []


def test_no_knob_and_no_skips_is_accepted_and_the_graph_names_what_is_missing(tmp_path):
    """没人给窗口旋钮、也没人点名允许跳过 —— **照收**，让图自己停住点名（R-31）。

    这是**对的**行为，不是要在服务里糊掉的东西：图会在 intake 停下并说清
    「缺的是 `set_viewport`，谁给得了」。服务要是这时候替它塞一个默认，
    等于替人**预授权跳过**（R-5 明令不许）。
    """
    rec = Rec()
    deps = _deps(rec, tmp_path)                               # 真图，但**没人要那根线**
    client = _client(graph_factory=lambda brief, d: graph.build(
        checkpointer=InMemorySaver().with_allowlist(graph.MSGPACK_ALLOWLIST), deps=deps))

    job_id = client.post("/run", json=_brief(tmp_path, ws_url=None)).json()["job_id"]
    view = _reply_until_done(client, job_id)

    assert view["status"] == "done", view
    assert view["delivered"] is False
    assert view["result"]["end_reason"] == "missing_knob", view["result"]
    assert "set_viewport" in view["say"], view["say"]
    assert "换窗口大小" in view["say"], "点名的时候要说人话：%s" % view["say"]
    assert rec.explore == [], "输入不齐就不该开浏览器（这正是 intake 那次检查的意义）"


# ───────────────────────── 2. 人的话与恢复 ─────────────────────────


def test_reply_carries_the_humans_words_through_to_the_graph(tmp_path):
    """人在闸口说的纠正 / 原文不许被丢（§6.2「直接说该点哪」）—— 现场看 state 里在不在。"""
    rec = Rec()
    seen: list = []

    saver = InMemorySaver().with_allowlist(graph.MSGPACK_ALLOWLIST)

    def factory(brief, deps):
        built = graph.build(checkpointer=saver, deps=_deps(rec, tmp_path, inherit=deps))
        real_invoke = built.invoke

        def invoke(payload, config):
            out = real_invoke(payload, config)
            seen.append(dict(built.get_state(config).values))
            return out

        built.invoke = invoke                                # 只想偷看一眼状态
        return built

    client = _client(graph_factory=factory)
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    _wait(client, job_id)
    assert client.post("/job/%s/reply" % job_id,
                       json={"action": "revise", "note": "登录弹窗要先点掉"}).status_code == 200
    _wait(client, job_id)

    assert any("登录弹窗要先点掉" in (v.get("hints") or []) for v in seen), \
        "人在闸口说的话没有进 state：%r" % [v.get("hints") for v in seen]


def test_the_happy_path_delivers_through_the_service(tmp_path):
    """真图 + 桩依赖走完：产物**真的**落在磁盘上，`delivered` 才为真。"""
    rec, win = Rec(), StubWindow()
    client = _client(graph_factory=_real_factory(rec, tmp_path),
        window=win)

    job_id = client.post("/run", json=_brief(tmp_path, set_viewport=True)).json()["job_id"]
    view = _reply_until_done(client, job_id)

    assert view["status"] == "done", view
    assert view["delivered"] is True, view
    py = pathlib.Path(view["result"]["py_path"])
    assert py.is_file(), py
    assert "PROVENANCE" in py.read_text(encoding="utf-8")
    assert "谢谢" not in view["say"]                          # 人话里不该有「成功」这种空话
    # R-31：窗口层那根线真的到了 selftest 手里 —— 而且它**真的通向窗口层那个对象**
    cb = rec.selftest[0].get("set_viewport")
    assert cb is not None, rec.selftest[0]
    cb(1280, 900)
    assert win.calls == [(1280, 900)], "自测拿到的回调不通向窗口层：%r" % (win.calls,)
    assert len(rec.explore) == 1


def test_allow_skips_and_entry_url_reach_the_graph_as_given(tmp_path):
    """透传：载荷里点名放过哪几遍、第 2 遍刷新回哪个 URL —— 原样到 `selftest.run` 手里。"""
    rec, win = Rec(), StubWindow()
    client = _client(graph_factory=_real_factory(rec, tmp_path),
        window=win)

    job_id = client.post("/run", json=_brief(
        tmp_path, allow_skips=["country"], set_viewport=True,
        entry_url=URL)).json()["job_id"]
    _reply_until_done(client, job_id)

    assert rec.selftest, "自测根本没跑"
    assert tuple(rec.selftest[0]["allow_skips"]) == ("country",)
    assert rec.selftest[0]["entry_url"] == URL
    cb = rec.selftest[0].get("set_viewport")
    assert cb is not None, "载荷里要了 set_viewport，那根线就得接上：%r" % (rec.selftest[0],)
    cb(1024, 768)
    assert win.calls == [(1024, 768)], win.calls


# ─────────────────── 3. 跑挂的 job 绝不许被读成跑成的（变异靶子）───────────────────


def test_a_job_whose_runner_raised_is_failed_and_never_readable_as_delivered(tmp_path):
    """跑挂了（窗口连不上 / 图自己抛）→ `failed`，`delivered` 必须是假。

    ⚠️ **这是变异靶子**：把 `_advance()` 里那个 `except` 拿掉、或者让它把异常吞了
    继续往下当成功 —— 这条就红。**跑挂的 job 不许被读成跑成的**，是服务最贵的一条底线：
    它下游接的是「产物可以上生产」这个判断。
    """
    fg = FakeGraph(steps=[{}], raise_on=[1])
    client = _client(graph_factory=lambda brief, deps: fg)

    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    view = _wait(client, job_id, until=("failed", "done", "waiting"))

    assert view["status"] == "failed", view
    assert view["delivered"] is False
    assert view["result"] is None or not view["result"].get("py_path")
    assert "窗口连不上" in view["say"], view["say"]          # 原话要留着，别吞成「出错了」
    # 再读一次也一样（不是「第一次读才失败」）
    assert client.get("/job/%s" % job_id).json()["status"] == "failed"


def test_a_job_that_died_mid_flight_is_not_readable_as_succeeded(tmp_path):
    """中途挂掉（跑到第 3 次唤醒才炸）：`delivered` 依旧必须是假，`end_reason` 不许像成功。"""
    fg = FakeGraph(steps=[_Snap(values={"visits": ["intake"]}, next=("intake",),
                                interrupts=(_gate(),)),
                          _Snap(values={"visits": ["intake", "explore"], "py_path": "/tmp/x.py"},
                                next=("draft",), interrupts=(_gate("draft"),))],
                   raise_on=[3])
    client = _client(graph_factory=lambda brief, deps: fg)

    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    _wait(client, job_id)
    client.post("/job/%s/reply" % job_id, json={"action": "continue"})
    _wait(client, job_id)
    client.post("/job/%s/reply" % job_id, json={"action": "continue"})
    view = _wait(client, job_id, until=("failed", "done"))

    assert view["status"] == "failed", view
    assert view["delivered"] is False
    # 状态里残留的 py_path（上一次真跑留下的）**不许**被当成这次的产物端出来
    assert not (view["result"] or {}).get("py_path"), view["result"]


def test_reply_to_a_job_that_is_not_waiting_is_refused(tmp_path):
    """没在等人（跑完了 / 跑挂了 / 还在跑）→ 不许「回话」，更不许拿它当恢复用。"""
    fg = FakeGraph(steps=[{}])                                 # 一次就结束
    client = _client(graph_factory=lambda brief, deps: fg)
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    _wait(client, job_id, until=("done", "failed"))

    r = client.post("/job/%s/reply" % job_id, json={"action": "continue"})
    assert r.status_code == 409, r.text
    assert "没在等人" in r.json()["detail"] or "不在等" in r.json()["detail"]


# ───────────────────────── 4. P6：窗口没了 → 重开 + 续跑 ─────────────────────────


def test_a_run_without_a_window_stops_honestly_and_does_not_fake_a_pass(tmp_path):
    """没有窗口 → `no_window` 停住（**不是**跳过自测当通过）。服务只如实转述。"""
    rec = Rec()
    client = _client(graph_factory=_real_factory(rec, tmp_path), window=StubWindow())

    job_id = client.post("/run", json=_brief(tmp_path, ws_url=None, allow_skips=["country",
                                                                                "viewport"])
                         ).json()["job_id"]
    view = _reply_until_done(client, job_id)

    assert view["status"] == "done"
    assert view["delivered"] is False
    assert view["result"]["end_reason"] == "no_window", view["result"]
    assert "窗口" in view["say"], view["say"]
    assert rec.selftest == [], "没窗口还去跑自测？那是在拿一个死窗口烧时间"


def test_reopen_resumes_from_the_checkpoint_and_does_not_re_explore(tmp_path):
    """**P6 的正身**：窗口没了 → 重开一个 → 从断点接着跑，**不是整轮重来**。

    判据看两处：①`explore` 只跑过一次（账本是从 checkpoint 回来的，不是重探的）；
    ②自测这一回拿到的是**新**窗口 —— 然后才真的走到交付。
    """
    rec = Rec()
    client = _client(graph_factory=_real_factory(rec, tmp_path), window=StubWindow())

    job_id = client.post("/run", json=_brief(tmp_path, ws_url=None, allow_skips=["country",
                                                                                "viewport"])
                         ).json()["job_id"]
    stopped = _reply_until_done(client, job_id)
    assert stopped["result"]["end_reason"] == "no_window"
    assert len(rec.explore) == 1

    r = client.post("/job/%s/reopen" % job_id, json={"ws_url": NEW_WS_URL, "set_viewport": True})
    assert r.status_code == 200, r.text

    view = _reply_until_done(client, job_id)
    assert view["status"] == "done", view
    assert view["delivered"] is True, view
    assert len(rec.explore) == 1, "重开窗口把探路又跑了一遍 —— 那不是续跑，是整轮重来"
    assert rec.selftest[-1]["ws_url"] == NEW_WS_URL, "自测用的还是旧窗口"


def test_reopen_on_a_run_that_did_not_stop_for_a_window_is_refused(tmp_path):
    """别的死法不许拿 reopen 糊：那种情况下「重开窗口」解决不了问题，得说实话。"""
    rec = Rec()
    client = _client(graph_factory=_real_factory(rec, tmp_path), window=StubWindow())

    job_id = client.post("/run", json=_brief(tmp_path, allow_skips=["country", "viewport"])
                         ).json()["job_id"]
    view = _reply_until_done(client, job_id)
    assert view["status"] == "done" and view["delivered"] is True

    r = client.post("/job/%s/reopen" % job_id, json={"ws_url": NEW_WS_URL})
    assert r.status_code == 409, r.text
    assert "交付" in r.json()["detail"] or "已经" in r.json()["detail"]


def test_a_dead_window_is_noticed_before_the_next_step_runs(tmp_path):
    """窗口在**等人回话**的那几分钟里死了（§4.6：窗口只活几分钟）→ 回话时先说清。

    不先探一下的后果：自测拿着一个死窗口跑，报告把「连不上」记成产物的问题，
    人看到的是一份**误导性的**失败（R-5 那条「不许把接线问题记成产物不行」的服务侧版本）。
    """
    rec = Rec()
    win = StubWindow(alive=False)
    client = _client(graph_factory=_real_factory(rec, tmp_path), window=win)

    job_id = client.post("/run", json=_brief(tmp_path, set_viewport=True)).json()["job_id"]
    _wait(client, job_id)                                      # 停在 intake 前

    r = client.post("/job/%s/reply" % job_id, json={"action": "continue"})
    assert r.status_code == 409, r.text
    assert "窗口" in r.json()["detail"]
    assert "reopen" in r.json()["detail"] or "重开" in r.json()["detail"]
    assert rec.explore == [], "窗口已经死了还往下走"
    assert win.probes >= 1, "得真去探一下，不能靠猜"


def test_a_window_that_is_alive_does_not_block_the_run(tmp_path):
    """反向钉子：窗口活着时那个探针**不许**拦路（探针错了会拦掉本来能跑的运行）。"""
    rec = Rec()
    win = StubWindow(alive=True)
    client = _client(graph_factory=_real_factory(rec, tmp_path), window=win)

    job_id = client.post("/run", json=_brief(tmp_path, set_viewport=True)).json()["job_id"]
    view = _reply_until_done(client, job_id)
    assert view["status"] == "done", view
    assert view["delivered"] is True, view


def test_a_window_layer_that_cannot_be_asked_is_not_treated_as_dead(tmp_path):
    """探不了（没有窗口层 / 探针给不出答案）→ **不拦**。不知道不等于死。"""
    rec = Rec()
    client = _client(graph_factory=_real_factory(rec, tmp_path), window=None)

    job_id = client.post("/run", json=_brief(
        tmp_path, allow_skips=["country", "viewport"])).json()["job_id"]
    view = _reply_until_done(client, job_id)
    assert view["status"] == "done", view
    assert view["delivered"] is True, view


# ───────────────────────── 5. 状态住在 saver 里，不在进程里（R-19）─────────────────────────


def test_the_job_view_comes_back_from_the_checkpoint_after_a_restart(tmp_path):
    """服务重启（进程里的登记表没了）之后，同一个 job 还**读得到**、还接得下去。

    这条是 R-19 在服务侧的样子：状态住在 saver 里，不在某个进程里的对象里。
    没有它，「中断之后恢复不了」只是从图那一层挪到了服务这一层。
    """
    rec = Rec()
    saver = InMemorySaver().with_allowlist(graph.MSGPACK_ALLOWLIST)
    first = _client(graph_factory=_real_factory(rec, tmp_path, saver=saver),
                    checkpointer=saver)
    job_id = first.post("/run", json=_brief(
        tmp_path, allow_skips=["country", "viewport"])).json()["job_id"]
    _wait(first, job_id)
    assert len(rec.explore) == 0

    # 「重启」：换一个 app（新的登记表），**同一个 saver**
    second = _client(graph_factory=_real_factory(rec, tmp_path, saver=saver),
                     checkpointer=saver)
    view = _wait(second, job_id)
    assert view["status"] == "waiting", view
    assert view["gate"]["step"] == "intake", view["gate"]

    done = _reply_until_done(second, job_id)
    assert done["status"] == "done", done
    assert done["delivered"] is True, done
    assert len(rec.explore) == 1


def test_health_says_which_checkpointer_is_in_use(tmp_path):
    """健康检查要**说清**状态存哪儿了 —— 内存 saver 的「能恢复」只活在进程里。

    读的人要能一眼看出「这次跑的中断是易失的」，不必去翻代码（R-19 的诚实条款）。
    """
    client = _client(graph_factory=lambda brief, deps: FakeGraph(steps=[{}]))
    body = client.get("/health").json()
    assert body["ok"] is True
    assert body["checkpointer"] == "memory", body
    assert "进程" in body["say"] or "重启" in body["say"], body["say"]


def test_a_postgres_checkpointer_is_reported_as_durable():
    """反向钉子：配了 Postgres 时，健康检查不许还说「易失」。

    ⚠️ 这里给的是**连接串**（配置），不是连上的 saver —— 所以这条**不碰网**：
    健康检查只报「配的是哪种」，连接是**第一次真要用**的时候才建。
    """
    app = service.create_app(graph_factory=lambda brief, deps: FakeGraph(steps=[{}]),
                             checkpointer_url="postgresql://u:p@127.0.0.1:1/none")
    body = TestClient(app).get("/health").json()
    assert body["checkpointer"] == "postgres", body
    assert "易失" not in body["say"], body["say"]


def test_the_service_wires_the_brief_onto_the_graph_as_is(tmp_path):
    """开场白原样落到 state 里（服务**只搬运**，不加工）—— `env` / `platform` / `mode` 也是。"""
    seen: list = []

    def factory(brief, deps):
        fg = FakeGraph(steps=[_Snap(values={}, next=("intake",), interrupts=(_gate(),))])
        real = fg.invoke

        def invoke(payload, config):
            seen.append(dict(payload))
            return real(payload, config)

        fg.invoke = invoke
        return fg

    client = _client(graph_factory=factory)
    client.post("/run", json=_brief(tmp_path, mode="fix",
                                    env={"proxy_country": "US", "dpr": 1},
                                    platform={"guess": "quiz", "confidence": 0.7}))
    time.sleep(0.1)
    assert seen, "图根本没被调用"
    assert seen[0]["mode"] == "fix"
    assert seen[0]["env"] == {"proxy_country": "US", "dpr": 1}
    assert seen[0]["platform"] == {"guess": "quiz", "confidence": 0.7}
    assert seen[0]["success_text"] == SUCCESS


def test_explore_goes_to_the_window_the_brief_named(tmp_path, monkeypatch):
    """探路必须朝**载荷里那个窗口**去 —— 服务是知道窗口的那一层（R-31 的同一条道理）。

    没有这根线会怎样：`browser_agent.explore()` 自己**不收** `ws_url`（图调它时只给
    url/goal/预算/暂停谓词），窗口是从 `CDP_WS_URL` 或默认值进的 —— 于是它会去开
    **别的**浏览器（本机那个 headless，或者更糟：别的任务的窗口）。
    """
    seen: dict = {}

    def fake_explore(url, goal, budget=None, should_pause=None, **kw):
        seen.update({"url": url, "ws_url": kw.get("ws_url")})
        return _journey(url)

    monkeypatch.setattr(browser_agent, "explore", fake_explore)
    svc = service.Service()
    dep = svc._explore_for({"ws_url": NEW_WS_URL})
    assert dep is not None, "载荷里给了窗口，探路那一步就得朝它去"
    dep("https://example-funnel.test/quiz", "走通")
    assert seen["ws_url"] == NEW_WS_URL, seen

    # 没人给窗口 → 不接线（用默认），图会在自测那一步停下点名 —— 不许编一个窗口出来
    assert svc._explore_for({}) is None
    assert svc._explore_for({"ws_url": "  "}) is None


def test_a_viewport_that_did_not_actually_change_is_refused():
    """**配置改了 ≠ 窗口变了** —— 这是 2026-09-16 在真 worker 上量出来的，不是推测：

    `POST /browser/update` 换尺寸回 `success:true`、`/browser/detail` 回读也已是新值，
    而**活着的那个窗口纹丝不动**（还是 377x757）—— Bit 的窗口尺寸是**启动时**生效的。

    所以这根线写完还得**量活窗口**：量出来没变就抛。抛的后果是那一遍被记成「没跑」，
    而「没跑」不算过（R-5）—— 比「安安静静空转一遍却算过了」好一万倍。
    """
    ws = "ws://192.168.1.197:61129/devtools/browser/xyz"
    win = StubWindow()
    svc = service.Service(window=win, viewport_probe=lambda _ws: (377, 757),
                          checkpointer=InMemorySaver().with_allowlist(graph.MSGPACK_ALLOWLIST))
    with pytest.raises(RuntimeError) as exc:
        svc._viewport_cb(ws)(1024, 768)
    assert "活着的那个窗口" in str(exc.value), str(exc.value)
    assert "377x757" in str(exc.value), "得把量出来的真实尺寸说出来：%s" % exc.value
    assert win.calls == [(1024, 768)], "配置还是得先写下去（那一步本身没错）"

    # 反向钉子：活窗口真的变了 → 不许拦（探针说真话时它不该挡路）
    ok = service.Service(window=StubWindow(), viewport_probe=lambda _ws: (1024, 768),
                         checkpointer=InMemorySaver().with_allowlist(graph.MSGPACK_ALLOWLIST))
    ok._viewport_cb(ws)(1024, 768)          # 不抛就算过


def test_the_viewport_knob_cannot_verify_anything_without_a_window_to_measure():
    """**量不了就是没验到** —— 没有窗口可量时也一样，不许静默当成功。

    （实际上这条分支到不了：没有窗口时 `_selftest` 在更前面就以 `no_window` 停下了。
    但规则保持一致比「这里特殊一次」安全 —— 特殊的那次就是将来漏掉的那次。）
    """
    svc = service.Service(window=StubWindow(), viewport_probe=lambda _ws: (1, 1),
                          checkpointer=InMemorySaver().with_allowlist(graph.MSGPACK_ALLOWLIST))
    with pytest.raises(RuntimeError):
        svc._viewport_cb(None)(1024, 768)


# ───────────────────────── 6. 窗口层：两条线的**真实形状**（真 worker 上量的）─────


def _bit_window(reply):
    """一个把 HTTP 换掉的 `BitWindow`（形状照真 worker 的应答）。"""
    win = service.BitWindow("192.168.1.197", "b" * 32)

    def _post(path, body):
        return reply(path, body)
    win._post = _post
    return win


def test_alive_reads_the_shape_the_real_worker_actually_returns():
    """`/browser/pids/alive` 的 `data` 是**一个 dict**，不是一个 list：

        活着 → {"success":true,"data":{"<bit_id>": 4256}}
        死了 → {"success":true,"data":{}}

    （两种形状都是 2026-09-16 在真 worker 上量的。）
    一开始这里只认 list/bool/str —— 于是**真跑时永远返回 None（「不知道」）**，
    这根线看着接好了，其实永远不响。这种「接上了但不响」比没接更坏：它让 P6 那道前置
    看起来存在。所以这条测试钉死这两种形状。
    """
    bid = "b" * 32
    assert _bit_window(lambda p, b: {"success": True, "data": {bid: 4256}}).alive() is True
    assert _bit_window(lambda p, b: {"success": True, "data": {}}).alive() is False
    # 别把「问不出来」读成「死了」——那会误杀一个本来能跑的运行
    assert _bit_window(lambda p, b: {"success": True, "data": "看不懂"}).alive() is None
    assert _bit_window(lambda p, b: (_ for _ in ()).throw(RuntimeError("连不上"))).alive() is None


def test_set_viewport_writes_the_whole_record_so_the_proxy_is_not_dropped():
    """`POST /browser/update` 是**整条记录更新**，不是补丁。

    实测：只发 `{id, browserFingerPrint}` 会被拒 ——
    `{"success":false,"msg":"请选择代理方式"}`（也就是说代理那几项会被丢掉，
    与 `bit.sh update` 那个残缺包装同一个坑）。所以必须**先读回整条、只改尺寸、原样写回**。
    """
    seen: dict = {}

    def reply(path, body):
        if path == "/browser/detail":
            return {"success": True, "data": {
                "id": "b" * 32, "proxyMethod": 1, "proxyType": "socks5",
                "host": "10.0.0.9", "port": 1081, "syncTabs": False,
                "clearCookiesBeforeLaunch": True,
                "browserFingerPrint": {"coreVersion": "134", "openWidth": 393, "openHeight": 852}}}
        seen.update(body)
        return {"success": True, "data": "操作成功"}

    _bit_window(reply).set_viewport(1024, 768)
    assert seen["browserFingerPrint"]["openWidth"] == 1024
    assert seen["browserFingerPrint"]["openHeight"] == 768
    assert seen["browserFingerPrint"]["coreVersion"] == "134", "原来那几项要原样带回去"
    assert (seen["proxyMethod"], seen["proxyType"], seen["host"], seen["port"]) == \
        (1, "socks5", "10.0.0.9", 1081), "代理那几项一个都不能丢：%r" % seen


# ═══════════════════ 修复轮 1（评审的 5 条 Important）═══════════════════
#
# 这五条都是「桩测不出来 / 只在那条路上才现形」的那一类。所以每条测试都刻意做成
# **在出厂那版代码上会红**的形状 —— 对 bug 也绿的测试什么也证明不了。


# ── Important 1：状态已经在 Postgres 里了，/health 却翻脸说「内存、重启就没了」──


def test_health_still_says_postgres_after_the_saver_has_actually_been_built(tmp_path):
    """`kind` 原先看的是「**还没建**」这件事：`self._saver is None and self._url`。

    第一次真要用（`get()`）之后 `_saver` 不再是 None —— 于是 `/health` 翻成
    「状态只存在这个进程的内存里：**服务一重启就没了**」，而那个部署的状态明明在 Postgres 里。
    这条把「建完之后再读一次」钉住：错的不是那句话本身，是它**在什么时候**说。
    """
    app = service.create_app(
        graph_factory=lambda brief, deps: FakeGraph(steps=[_Snap(
            values={"visits": ["intake"]}, next=("intake",), interrupts=(_gate(),))]),
        checkpointer_url="postgresql://u:p@127.0.0.1:1/none")
    client = TestClient(app)
    svc = app.state.service
    # 把它换成「建得出来」的替身（这条测的是 kind 的判据，不是 psycopg 连不连得上）
    svc._check._connect = lambda url: InMemorySaver().with_allowlist(graph.MSGPACK_ALLOWLIST)

    assert client.get("/health").json()["checkpointer"] == "postgres"      # 建之前：对
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    _wait(client, job_id)
    svc._check.get()          # 生产里第一次真要用（拼图 / 读状态）就会走到这里

    body = client.get("/health").json()
    assert body["checkpointer"] == "postgres", "建完 saver 之后 /health 翻脸了：%r" % body
    assert "重启就没了" not in body["say"], body["say"]


# ── Important 2：回完话还说「在等人」，第二句回话会落到下一道闸上 ──


def test_a_reply_moves_the_job_out_of_waiting_and_a_second_reply_is_refused(tmp_path):
    """回完话**必须**把 job 从「停住」挪开。

    原先 `reply()` 回完直接返回 `_view()`，而 job 还停在 `_advance` 留下的 `DONE` 上 ——
    `_view` 只短路 `FAILED/QUEUED/RUNNING`，于是它**从 checkpoint 投影**，而 checkpoint 里
    那个中断还在 → 响应说「在等人」（还是旧那道闸）。调用方（Console / 脚本）看到「还在等人」
    就会再回一次话 —— 那第二句 `Command(resume=...)` 会**落到下一道闸上**：
    **一步在没人看着的情况下跑掉了**（§6.2/D16 的核心承诺）。

    这条同时钉两件事：①回话的响应立刻是 `running`；②那段时间里再来一句回话被**拒**。
    """
    release = threading.Event()
    fg = FakeGraph(
        steps=[_Snap(values={"visits": ["intake"]}, next=("intake",), interrupts=(_gate("intake"),)),
               _Snap(values={"visits": ["intake", "explore"]}, next=("explore",),
                     interrupts=(_gate("explore"),))],
        hold=lambda n: release.wait(10) if n == 2 else None)   # 第 2 次 invoke 卡住，好观察
    client = _client(graph_factory=lambda brief, deps: fg)
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    assert _wait(client, job_id)["status"] == "waiting"

    r = client.post("/job/%s/reply" % job_id, json={"action": "continue"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "running", (
        "回完话的响应还说「在等人」—— 调用方会再回一次，而那一句会落到**下一道闸**上：%r"
        % r.json())

    again = client.post("/job/%s/reply" % job_id, json={"action": "continue"})
    assert again.status_code == 409, "第二句回话被收下了 —— 那一步就没人看着了：%r" % again.text

    release.set()
    view = _wait(client, job_id)
    assert view["gate"]["step"] == "explore", view["gate"]     # 下一道闸是人回完第一句之后才到的


def test_a_viewport_round_that_could_not_be_measured_is_not_a_pass():
    """**量不出来 ≠ 做成了。**

    `live_viewport` 在这些情况下返回 `None`：没有 cdp 二进制、子进程失败、输出看不懂、
    或者 `ws_url` 不认识。原先 `None` 被当成「没问题」，回调返回成功 →
    自测把第 4 遍记成**跑了** → `_judge` 可能判过 → 交出去的产物带着
    「折叠 / 遮挡 / 坐标假设**验过了**」，而**一个数都没量到**。

    这正是这段代码自己的 docstring 骂的那件事。抛出去的后果是**对的**那一头：
    `selftest.run` 会把回调抛错记成「这一遍没跑」（`selftest.py:475`）——
    **不是**把整跑杀掉，是那一遍不算过。
    """
    svc = service.Service(window=StubWindow(), viewport_probe=lambda _ws: None,
                          checkpointer=InMemorySaver().with_allowlist(graph.MSGPACK_ALLOWLIST))
    with pytest.raises(RuntimeError) as exc:
        svc._viewport_cb("ws://192.168.1.197:61129/devtools/browser/x")(1024, 768)
    assert "量" in str(exc.value), str(exc.value)
    assert "allow_skips" in str(exc.value) or "放弃" in str(exc.value), \
        "得告诉人两条明路（修好 / 明确放弃），不然他们只会看到「失败了」：%s" % exc.value


def test_a_window_update_that_did_not_say_success_is_not_a_success():
    """`/browser/update` 的应答里没有 `success: true` 就不算成功。

    实测两种应答：成功 `{"success":true,"data":{…}}`；被拒 `{"success":false,"msg":"请选择代理方式"}`。
    原先只看 `success is False`，于是**一个空应答 / 换了形状的应答**会被当成「改好了」。
    """
    for reply in ({}, {"msg": "什么也没说"}, {"data": {"whatever": 1}}):
        win = _bit_window(lambda p, b, r=reply: (
            {"success": True, "data": {"b" * 32: {"browserFingerPrint": {}}}}
            if p == "/browser/detail" else r))
        # `/browser/detail` 的形状不对时它自己就会抛；这里只要求**不许**静默当成功
        with pytest.raises(RuntimeError):
            win.set_viewport(1024, 768)


# ── Important 4：整条记录回写（键一个都不能少）──


def test_set_viewport_writes_back_every_key_of_the_real_record():
    """`/browser/update` 是**整条记录**更新，不是补丁 —— 回写时漏掉的键就是把那个设置弄丢了。

    这条**照着真应答的键集合**比：`tests/fixtures/bit_window_detail.json` 是真从
    Bit worker 的 `POST /browser/detail` 抓的（126 个顶层键 / 112 个指纹键，只把**值**redact 了，
    键一个没删）。照着我们自己以为的那几个键写一条 fixture 去比 —— 那正是这个 bug 能活下来的原因。

    另外实测过：**整条原样回写（只改尺寸）→ `success:true`，回读 126/112 个键一个不少**，
    所以「全都写回去」这条路上没有服务端不认的键。
    """
    fixture = json.loads((ROOT / "tests" / "fixtures" / "bit_window_detail.json")
                         .read_text(encoding="utf-8"))
    real = fixture["response"]["data"]
    assert len(real) > 100 and len(real["browserFingerPrint"]) > 100, "fixture 不像真应答"
    seen: dict = {}

    def reply(path, body):
        if path == "/browser/detail":
            return {"success": True, "data": copy.deepcopy(real)}
        seen.update(body)
        return {"success": True, "data": "操作成功"}

    _bit_window(reply).set_viewport(1024, 768)

    missing = sorted(set(real) - set(seen))
    assert not missing, "回写时漏了这些顶层键（整条替换会把它们弄丢）：%r" % missing[:12]
    fp_missing = sorted(set(real["browserFingerPrint"]) - set(seen["browserFingerPrint"]))
    assert not fp_missing, "回写时漏了这些指纹键：%r" % fp_missing[:12]
    assert seen["browserFingerPrint"]["openWidth"] == 1024
    assert seen["browserFingerPrint"]["openHeight"] == 768
    # 值也要原样带回去（不是被清空）—— 代理那几项 + 一个原来就有的、跟尺寸无关的设置
    assert seen["browserFingerPrint"]["resolution"] == real["browserFingerPrint"]["resolution"]
    assert seen["proxyMethod"] == real["proxyMethod"] and seen["port"] == real["port"]
    assert seen["clearCookiesBeforeLaunch"] == real["clearCookiesBeforeLaunch"]


# ── Important 5：P6 要覆盖的是「探路途中窗口就死了」那一支（而且那是大概率那支）──


def _scripted_deps(rec: Rec, tmp_path, script):
    """`explore` 按 `script` 依次来：返回一个 Journey，或者抛一个异常。"""
    calls = {"n": 0}

    def explore(url, goal, budget=None, should_pause=None):
        rec.explore.append({"url": url})
        item = script[min(calls["n"], len(script) - 1)]
        calls["n"] += 1
        if isinstance(item, Exception):
            raise item
        return item

    def selftest_stub(py_path, ws_url, form_file, site, **kw):
        rec.selftest.append({"py_path": str(py_path), "ws_url": ws_url, "site": site, **kw})
        return _pass_report(site)

    return graph.Deps(explore=explore, selftest=selftest_stub, set_viewport=None)


def _reopenable_client(rec, tmp_path, script, *, saver=None):
    saver = saver or InMemorySaver().with_allowlist(graph.MSGPACK_ALLOWLIST)
    deps = _scripted_deps(rec, tmp_path, script)
    return _client(graph_factory=lambda brief, d: graph.build(checkpointer=saver, deps=deps),
                   window=StubWindow(), checkpointer=saver)


def test_reopen_picks_up_a_run_whose_window_died_during_the_explore(tmp_path):
    """窗口**探路途中**没了 → 图停在 `explore_unfinished`（那一趟白探了一半）。

    原先的 `reopen` 只认 `no_window`@自测，所以这一支直接 409 ——
    唯一的出路变成**重新开一个任务**，也就是**整轮重来**，而 P6 明令不许这个。

    修法：停在**探路那一步**的 run 也接得住 —— 重开窗口之后探路**重跑一遍**
    （页面状态没了，账本必须从头收），但开场白、人说过的话、以及 checkpoint 都还在。
    """
    rec = Rec()
    client = _reopenable_client(rec, tmp_path, [
        _journey(stop_reason="budget_steps"),      # 第一次：探路没走完
        _journey(),                                # 重开之后：走完了
    ])
    job_id = client.post("/run", json=_brief(
        tmp_path, allow_skips=["country", "viewport"])).json()["job_id"]

    view = _wait(client, job_id)
    assert view["status"] == "waiting" and view["gate"]["step"] == "intake"
    client.post("/job/%s/reply" % job_id, json={"action": "revise", "note": "ZIP 要填真的"})
    view = _reply_until_done(client, job_id)

    assert view["status"] == "done", view
    assert view["result"]["end_reason"] == "explore_unfinished", view["result"]
    assert list(view["result"]["visits"])[-1] == "explore", view["result"]["visits"]
    assert len(rec.explore) == 1

    r = client.post("/job/%s/reopen" % job_id, json={"ws_url": NEW_WS_URL})
    assert r.status_code == 200, r.text
    assert "探路" in r.json()["say"] and ("重" in r.json()["say"] or "再走" in r.json()["say"]), \
        "得用**人话**说清「探路要从头再走一遍」：%s" % r.json()["say"]

    view = _reply_until_done(client, job_id)
    assert view["status"] == "done", view
    assert view["delivered"] is True, view
    assert len(rec.explore) == 2, "重开之后的探路没有重跑"
    assert rec.selftest[-1]["ws_url"] == NEW_WS_URL, "自测用的还是旧窗口"

    hints = client.app.state.service._snapshot(job_id).values.get("hints") or []
    assert any("ZIP 要填真的" in h for h in hints), "重开之后人说过的话丢了：%r" % hints


def test_reopen_picks_up_a_job_that_blew_up_during_the_explore(tmp_path):
    """更糟的那一支：探路**抛异常**（窗口的传输断了 / MCP 门起不来）→ `_advance` 把 job 记成 `failed`。

    那时候 `reopen` 与 `reply` **双双 409** —— 出路只剩「重新开一个任务」，也就是整轮重来。
    而窗口只活几分钟、一次探路可能跑更久，所以**这一支是大概率那支，不是稀奇的那支**。
    """
    rec = Rec()
    client = _reopenable_client(rec, tmp_path, [
        RuntimeError("窗口连不上了：connect to 192.168.1.197:61129 failed"),
        _journey(),
    ])
    job_id = client.post("/run", json=_brief(
        tmp_path, allow_skips=["country", "viewport"])).json()["job_id"]
    _wait(client, job_id)                                  # 停在 intake 前
    view = _reply_until_done(client, job_id)               # 回一句 → 探路抛 → failed

    assert view["status"] == "failed", view
    assert "窗口连不上" in str(view["say"]), view["say"]
    # ⚠️ 这里最后**落下**的是 intake，不是 explore —— 探路那一步是在**它的身体里**炸的，
    # 所以它的输出（连同 visits）从来没提交过。`reopen` 的 FAILED 那一支就是照这个形状判的
    # （「接在最后落下的那一步上，炸掉的是它的下一跳」）。
    assert list(client.app.state.service._snapshot(job_id).values.get("visits") or [])[-1] == "intake"

    r = client.post("/job/%s/reopen" % job_id, json={"ws_url": NEW_WS_URL})
    assert r.status_code == 200, "探路炸掉的 job 接不下去 —— 那就只剩「整轮重来」了：%s" % r.text

    view = _reply_until_done(client, job_id)
    assert view["status"] == "done", view
    assert view["delivered"] is True, view
    assert len(rec.explore) == 2


def test_reopen_still_refuses_a_job_that_died_somewhere_else(tmp_path):
    """反向钉子：接得住的是「窗口那一支」，不是「什么都能用重开窗口糊过去」。

    停在 draft 上的、交付过的 —— 一律照旧 409（重开窗口解决不了它们）。
    """
    fg = FakeGraph(steps=[_Snap(values={"visits": ["intake", "explore", "draft"]},
                                next=("draft",), interrupts=(_gate("draft"),))],
                   raise_on=[2])
    client = _client(graph_factory=lambda brief, deps: fg)
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    _wait(client, job_id)
    client.post("/job/%s/reply" % job_id, json={"action": "continue"})
    view = _wait(client, job_id, until=("failed", "done"))
    assert view["status"] == "failed", view

    r = client.post("/job/%s/reopen" % job_id, json={"ws_url": NEW_WS_URL})
    assert r.status_code == 409, r.text
    assert "探路" in r.json()["detail"], r.json()["detail"]


# ═══════════ 修复轮 2（Important 5 的**接线**那一半 + 三条 Minor）═══════════


def _selftest_stub(rec: Rec, tmp_path=None, *, call_viewport=False):
    """自测桩。`call_viewport=True` 时**照 `selftest.run` 的样子真去调那个回调** ——
    桩不调它，「闭包里是哪个窗口」这件事就永远看不见（上一轮就是这么漏掉的）。"""
    def stub(py_path, ws_url, form_file, site, **kw):
        rec.selftest.append({"py_path": str(py_path), "ws_url": ws_url, "site": site, **kw})
        cb = kw.get("set_viewport")
        if call_viewport and cb is not None:
            try:
                cb(*selftest.DEFAULT_VIEWPORT)
            except Exception:
                pass          # 真 `run()` 把回调抛错记成「这一遍没跑」，不往外抛
        return _pass_report(site)
    return stub


def test_reopen_hands_the_resumed_explore_the_new_window(tmp_path, monkeypatch):
    """`reopen` 换了窗口之后，**探路那一步真的拿到新窗口了吗**。

    `Deps.explore` 是拼图时对 `brief["ws_url"]` **闭包**出来的（`_explore_for`），
    而 `reopen` 原先只在 `set_viewport` 新打开时才重拼那张图 —— 于是
    `POST /reopen {"ws_url": NEW}` 回 200、checkpoint 里也写着 NEW，
    而探路手里那根线**还指着旧窗口**：续跑会又一次死在旧窗口上，
    出路只剩「重新开一个任务」= **整轮重来**，正是 P6 明令不许的那件事。

    ⚠️ 这条断言的是**探路实际被交到哪个 ws_url**，不是「reopen 回了 200」——
    上一轮那几条断言（回 200、explore 跑了两次、自测拿到新窗口）对**出厂那版**也是绿的，
    这正是它没被抓到的原因。
    """
    seen: list = []

    def fake_explore(url, goal, budget=None, should_pause=None, **kw):
        seen.append(kw.get("ws_url"))
        if len(seen) == 1:
            raise RuntimeError("窗口连不上了：connect to 192.168.1.197:61129 failed")
        return _journey(url)

    monkeypatch.setattr(browser_agent, "explore", fake_explore)
    rec, saver = Rec(), InMemorySaver().with_allowlist(graph.MSGPACK_ALLOWLIST)

    def factory(brief, deps):
        # ⚠️ 用**服务拼的那份** `deps.explore`（它包着 ws_url），只把贵的自测换成桩。
        # 上一轮那个工厂把服务拼的 deps **整个丢掉**、自己造了一个不认 ws_url 的探路桩 ——
        # 所以它对「闭包里是哪个窗口」这件事**结构上就是瞎的**。
        return graph.build(checkpointer=saver, deps=graph.Deps(
            explore=deps.explore, selftest=_selftest_stub(rec), set_viewport=deps.set_viewport))

    client = _client(graph_factory=factory, window=StubWindow(), checkpointer=saver,
                     viewport_probe=lambda _ws: None)
    job_id = client.post("/run", json=_brief(
        tmp_path, allow_skips=["country", "viewport"])).json()["job_id"]
    _wait(client, job_id)
    view = _reply_until_done(client, job_id)              # 回一句 → 探路抛 → failed
    assert view["status"] == "failed", view
    assert seen == [WS_URL], seen

    assert client.post("/job/%s/reopen" % job_id,
                       json={"ws_url": NEW_WS_URL}).status_code == 200
    _reply_until_done(client, job_id)

    assert seen[-1] == NEW_WS_URL, (
        "续跑的探路还朝**旧窗口**去（reopen 给的是 %s，探路拿到的是 %s）—— "
        "它会再死一次，而出路只剩整轮重来（P6 不许）" % (NEW_WS_URL, seen[-1]))


def test_reopen_rebuilds_the_viewport_probe_onto_the_new_window(tmp_path):
    """同一个闭包的另一半：`_viewport_cb` 也把 ws_url 闭在里头了。

    换窗口之后它还在量**旧窗口** —— 于是第 4 遍要么以一个莫名其妙的理由被判没做成，
    要么（旧窗口刚好是那个尺寸时）**被判成做成了**。两个方向都不能接受。
    """
    probes: list = []
    win = StubWindow()
    rec = Rec()

    def probe(ws_url):
        probes.append(ws_url)
        return win.calls[-1] if win.calls else None

    saver = InMemorySaver().with_allowlist(graph.MSGPACK_ALLOWLIST)
    selftest_stub = _selftest_stub(rec, call_viewport=True)
    explore_stub = _deps(Rec(), tmp_path).explore      # 这条测的是**窗口那根线**，不是探路
    client = _client(graph_factory=lambda brief, deps: graph.build(
        checkpointer=saver, deps=graph.Deps(explore=explore_stub, selftest=selftest_stub,
                                            set_viewport=deps.set_viewport)),
        window=win, checkpointer=saver, viewport_probe=probe)
    job_id = client.post("/run", json=_brief(
        tmp_path, ws_url=None, set_viewport=True)).json()["job_id"]
    stopped = _reply_until_done(client, job_id)
    assert stopped["result"]["end_reason"] == "no_window", stopped["result"]
    assert probes == [], "没窗口可量的时候根本到不了那根线：%r" % probes

    assert client.post("/job/%s/reopen" % job_id,
                       json={"ws_url": NEW_WS_URL, "set_viewport": True}).status_code == 200
    _reply_until_done(client, job_id)

    with_size = [p for p in probes if p]
    assert with_size and with_size[-1] == NEW_WS_URL, (
        "换窗口之后那根线还在量旧窗口（量过：%r）" % with_size)


# ── 同一轮评审的三条 Minor ──


def test_the_refusal_message_names_the_step_a_failed_job_died_on(tmp_path):
    """Minor 1：`FAILED` 的 job，`_view` 给的 `result` 是 `None`（**那是对的** ——
    跑挂的没有结果），所以拒绝的话里想「点名是哪一步」就不能从那儿读：
    它永远渲染成「炸在『不知道哪一步』那一步」—— 一句读了等于没读的话。
    （覆盖那条测试只断言了 `"探路"`，而那三个字来自另一句 `can` 文案，与这里无关。）
    """
    fg = FakeGraph(steps=[_Snap(values={"visits": ["intake", "explore", "draft"]},
                                next=("draft",), interrupts=(_gate("draft"),))],
                   raise_on=[2])
    client = _client(graph_factory=lambda brief, deps: fg)
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    _wait(client, job_id)
    client.post("/job/%s/reply" % job_id, json={"action": "continue"})
    view = _wait(client, job_id, until=("failed", "done"))
    assert view["status"] == "failed", view

    detail = client.post("/job/%s/reopen" % job_id, json={"ws_url": NEW_WS_URL}).json()["detail"]
    assert "不知道哪一步" not in detail, detail
    assert graph.STEP_SAY["draft"] in detail, "得点名它炸在哪一步：%s" % detail


def test_reads_of_a_shared_in_memory_saver_stay_under_the_write_lock():
    """Minor 2：「读不排队」只对**另一条连接**成立。

    （这条是替换掉旧那条 `test_reading_a_job_does_not_queue_behind_another_jobs_run` 的 ——
    旧那条拿**内存** saver 断言「读不排队」，而内存 saver 没有内部锁，那个断言是**错的**。
    「不排队」那一半现在由 `tests/test_pg_checkpointer.py` 里
    `test_reads_go_through_a_second_connection_so_progress_does_not_wait_for_a_run`
    在真 Postgres 上钉着 —— 那才是这条性质成立的场合。）

    `InMemorySaver` **没有内部锁** —— 读绕过写锁就可能撞上
    「dictionary changed size during iteration」→ 500。所以同一条 saver 上，读**该**排队；
    上一条（Postgres 那条）验的才是「另一条连接上的读不排队」。
    """
    svc = service.Service(checkpointer=InMemorySaver().with_allowlist(graph.MSGPACK_ALLOWLIST))
    done = threading.Event()
    threading.Thread(target=lambda: (svc._snapshot("job-nobody"), done.set()), daemon=True).start()
    with svc._check.lock:                        # 写锁按着（模拟一次 invoke）
        assert not done.wait(0.5), "读绕过了写锁 —— 同一条内存 saver 上并发读会炸"
    assert done.wait(5), "松开写锁之后读还是没回来"
