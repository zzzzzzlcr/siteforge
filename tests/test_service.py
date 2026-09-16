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

import pathlib
import sys
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

    def __init__(self, *, steps=None, raise_on=None):
        #: 每次 `invoke` 依次返回的东西；用完了就返回最后一个
        self.steps = list(steps or [])
        self.raise_on = raise_on or []
        self.invokes: list = []
        self.updates: list = []
        self.state = _Snap(values={}, next=(), interrupts=())

    def invoke(self, payload, config):
        self.invokes.append(payload)
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
    # 「活窗口现在多大」默认量不出来（真实现要起 cdp 子进程）→ 测试里是「不知道」，
    # 「不知道」不许拦路。专门验那根探针的那条测试自己注入一个会说真话的桩。
    kw.setdefault("viewport_probe", lambda ws_url: None)
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


def test_the_viewport_probe_is_skipped_when_we_have_no_window_to_measure():
    """没有 ws_url（没人给窗口）→ 量不了 → **不拦**。不知道不等于没做成。"""
    svc = service.Service(window=StubWindow(), viewport_probe=lambda _ws: (1, 1),
                          checkpointer=InMemorySaver().with_allowlist(graph.MSGPACK_ALLOWLIST))
    svc._viewport_cb(None)(1024, 768)       # 不抛


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
