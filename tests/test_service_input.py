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
    """

    def __init__(self, *, steps=None, hold=None):
        self.steps = list(steps or [])
        self.hold = hold or (lambda n: None)
        self.invokes: list = []
        self.state = _Snap(values={}, next=(), interrupts=())

    def invoke(self, payload, config):
        self.invokes.append(payload)
        self.hold(len(self.invokes))
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


def _client(*, graph_factory, window=None, **kw):
    kw.setdefault("checkpointer", InMemorySaver().with_allowlist(graph.MSGPACK_ALLOWLIST))
    kw.setdefault("capture", lambda ws_url, dest, *, timeout=None: (pathlib.Path(str(dest)).name, ""))
    return TestClient(service.create_app(graph_factory=graph_factory,
                                         window=window or StubWindow(), **kw))


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


def test_a_steer_switch_without_the_channel_refuses_instead_of_pretending(tmp_path, monkeypatch):
    """开关打开了、通道却没接上 → **503 + 一句实话**，绝不咽进队列还说「送到了」。

    ⚠️ 这一条今天**走不到生产**（`STEER_WIRED` 写死 False）—— 它钉的是**将来那半扇门**：
    Task 9 会把那个实参翻成 True，而翻的时候忘了接通道，就是「说了没送到」那件事
    （Global Constraints 明令：那比不做更坏）。所以要有一条用例证明：
    那时候服务**响**，而不是静默降级。
    """
    monkeypatch.setattr(service, "STEER_WIRED", True)
    g = FakeGraph(steps=[_Snap(values={"site": SITE, "visits": ["intake"]})])
    client = _client(graph_factory=_factory(g))
    _running_job(client.app.state.service, stage="explore")

    r = client.post("/job/job-running/say", json={"text": "先点 cookie 那个同意"})
    assert r.status_code == 503, r.text
    assert "没接上" in r.json()["detail"], r.json()
    live = _live(client, "job-running")
    assert live["input"]["queued"] == [], "送到没送到都说不清的时候，**不许**悄悄排进队列"
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
    assert len(g.invokes) == 2, "被推了两次：那一次「继续」+ 别的什么（这一句不该自己发）"


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


def test_the_live_view_fills_the_input_and_the_stop_the_page_reads(tmp_path):
    """`/live` 那两格的**形状**（brief 钉的）：`input` 三个键、`stop` 四个键。"""
    g = FakeGraph(steps=[_Snap(values={"site": SITE, "ws_url": WS_URL, "visits": ["intake"]},
                               interrupts=(_gate("intake"),))])
    client = _client(graph_factory=_factory(g))
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    _wait(client, job_id)

    live = _live(client, job_id)
    assert set(live["input"]) == {"mode", "draft_note", "queued"}, live["input"]
    assert set(live["stop"]) == {"requested", "where", "will_stop_at", "say"}, live["stop"]
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
    assert stop["say"] == stop["will_stop_at"], "一句话两处引用 —— 不许漂成两句"
    assert len(g.invokes) == 0, "「停」**绝不许**替人往前走一步（它只阻止，不推进）"
    assert len(_events(client, "job-running")) == before + 1, "按了「停」也是一次人的回话：要有事件"

    live = _live(client, "job-running")
    assert live["stop"]["requested"] is True
    assert live["stop"]["will_stop_at"] == stop["will_stop_at"], "/live 说的与刚才那一句要是同一句"
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

    view = _wait(client, job_id)
    assert view["status"] == "done", view
    assert view["result"]["end_reason"] == "human_stop", view["result"]
    assert view["result"]["visits"] == [], "「停」在闸上：这一步**一步都没有做**"
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
        assert "正在跑" in live["note"] or "跑" in live["note"], live["note"]
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
    new_id = r.json()["job_id"]
    payload = g.invokes[-1]
    assert isinstance(payload, dict), "重来是一趟**新跑**（payload 是开场白，不是 resume）：%r" % (payload,)
    assert payload["hints"] == ["先点 cookie 同意"]
    assert payload["url"] == URL
    assert json.dumps(payload, ensure_ascii=False)  # 交下去的必须是说得清的 JSON
    assert svc._jobs[new_id].brief["url"] == URL
