"""Task 4 Step 2：**时间线上该有的每一件事都在**（`agent/service.py` 的九个触发点）。

这份测试钉的是计划 Task 4 那张**目录表**（逐条）+ 一条机械断言：跑完一个桩 job 之后
`{"submitted","running","window_reopened","human_said","done"}` 五个 `kind` 都在 ——
**任何一条静默的路径都会让那条测试红**（Global Constraints：「没有静默的路径」）。

逐条对着目录表：

| # | 触发点 | 这里的用例 |
|---|---|---|
| 1 | 窗口从「不是没了」翻成「没了」 | `test_a_dead_window_is_narrated_once_…` |
| 2 | `reopen()` 之后 | `test_reopening_a_window_says_which_of_the_two_things_happened` |
| 3 | `start()` → `_submit` | `test_no_path_is_silent_…` / `test_a_job_that_has_to_wait_…` |
| 4 | `_advance` 拿到 job | 每一条跑起来的用例都在（`running`） |
| 5 | `_advance` 的 except | `test_a_job_that_blew_up_is_narrated_as_failed_…` |
| 6 | `_advance` 返回且快照到终局 | `test_a_terminal_end_…` / `test_a_cap_…` |
| 7 | `_recover()` | `test_a_recovered_job_says_so_…` |
| 8 | `reply()` | `test_a_human_note_lands_…` / `test_a_plain_continue_…` |
| 9 | `_capture_pause` 失败 | `test_a_shot_that_could_not_be_taken_…` |

还有两个**这一版特有**的：
- `/live` 的**骨架形状**（brief 钉的那几个字段：`gate` 恒 null、`input` 恒 queue、`rounds` 恒 []）；
- **读不许写**（`/live` 是 GET：调三次不许往时间线上加东西 —— 加了就是「看的人越多、时间线越长」）。

桩在这里是**必须**的：真图不会自己抛异常、也不会按你要的顺序停在某一跳上。
`FakeGraph` / `_Snap` / `StubWindow` 与 `tests/test_service.py` 里那套是**同一形状**
（计划 P3：本片所有测试新建文件，不去动那两个正在被改的文件）。
"""

from __future__ import annotations

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

from agent import events, graph, service  # noqa: E402

SITE = "example-funnel"
URL = "https://example-funnel.test/quiz"
GOAL = "走到「Thank you」那一页，把报价拿到"
SUCCESS = "Thank you"
WS_URL = "ws://127.0.0.1:9222/devtools/page/ABC"
NEW_WS_URL = "ws://192.168.1.197:55555/devtools/page/NEW"

#: 「服务重启过」那句话（设计注 §3.5）—— `/live` 的 `note` 要**明说**时间线不持久。
RESTART_NOTE_HINT = "服务重启过"
#: `reopen` 那两句人话各自的**记号**（目录表第 2 行：探路重跑 / 从断点接着跑，两句不同）
EXPLORE_RESTART_HINT = "探路从入口重新开始"
RESUME_FROM_BREAK_HINT = "从上次停下的地方接着跑"


# ─────────────────────────────── 桩：假图 ───────────────────────────────


@dataclass
class _Snap:
    """`get_state()` 返回的那个东西里，服务只准看这三个字段（其余是实现细节）。"""

    values: dict
    next: tuple = ()
    interrupts: tuple = ()


def _gate(step="intake", say="准备开工：…"):
    return Interrupt(value={"step": step, "say": say, "facts": {}, "can": ["让它继续"]}, id="i-1")


class FakeGraph:
    """照着服务要的协议做的假图：`invoke` / `get_state` / `update_state`。

    服务自己的状态机（跑挂、停在闸上、重开窗口接着跑）用真图很难造 ——
    真图不会按你要的顺序抛异常、也不会老老实实停在某一步上。
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


class BlindGraph(FakeGraph):
    """读状态读不上来的图（saver 抖了）—— 「没有静默的路径」在**读**这一侧的样子。

    ⚠️ 只有**第一次** `invoke` 之后才瞎：否则 `/run` 那一刻就瞎，
    测的就不是「跑完这一步之后读不回来」了。
    """

    def get_state(self, config):
        if not self.invokes:
            return self.state
        raise RuntimeError("saver 连不上了")


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


def _shot_ok(ws_url, dest, *, timeout=None):
    return pathlib.Path(str(dest)).name, ""


def _shot_boom(ws_url, dest, *, timeout=None):
    raise RuntimeError("cdp 子进程没了")


def _brief(tmp_path, **over):
    brief = {"url": URL, "goal": GOAL, "success_text": SUCCESS, "site": SITE,
             "ws_url": WS_URL, "form_file": str(tmp_path / "form.json"),
             "out_dir": str(tmp_path / "forms" / "sites")}
    brief.update(over)
    return brief


@pytest.fixture(autouse=True)
def _runtime_goes_to_tmp(tmp_path, monkeypatch):
    """运行产物（`runtime/explore/<job_id>/`）在测试里一律落 `tmp_path`。

    `Service` 的默认根是**仓库里**那个 `runtime/`（生产就该是那儿），
    而套件会跑几十个 job —— 那是把仓库当垃圾场（计划 Global Constraints：测试不写 `runtime/`）。
    """
    monkeypatch.setattr(service.measure, "DEFAULT_ROOT", tmp_path / "runtime" / "explore")


def _client(*, graph_factory, window=None, capture=_shot_ok, **kw):
    """装好桩的 TestClient —— **不碰**进程级默认（那会去连 Postgres / 真 Bit 窗口）。"""
    kw.setdefault("checkpointer", InMemorySaver().with_allowlist(graph.MSGPACK_ALLOWLIST))
    kw.setdefault("capture", capture)
    if isinstance(window, StubWindow):
        kw.setdefault("viewport_probe", lambda ws_url, w=window: (w.calls[-1] if w.calls else None))
    return TestClient(service.create_app(graph_factory=graph_factory, window=window, **kw))


def _factory(g: FakeGraph):
    return lambda brief, deps: g


def _never_used_deps() -> graph.Deps:
    """真图的桩依赖 —— 这些用例**只停在 intake 那一道闸上**，一个都不该被叫到。

    真叫到了就说明这一条跑出了它该在的地方（探路会开真浏览器、自测会起真窗口），
    所以这里**抛**而不是给个假结果。
    """
    def boom(*a, **kw):
        raise AssertionError("这一条用例只停在 intake —— 不该走到会开浏览器的那一步")

    return graph.Deps(explore=boom, selftest=boom)


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


def _live(client, job_id):
    r = client.get("/job/%s/live" % job_id)
    assert r.status_code == 200, r.text
    return r.json()


def _kinds(client, job_id):
    return [e["kind"] for e in _live(client, job_id)["events"]]


def _says(client, job_id, kind):
    return [e["say"] for e in _live(client, job_id)["events"] if e["kind"] == kind]


# ══════════════════ `/live` 的骨架（brief 钉的那几个字段）══════════════════


def test_the_live_view_has_every_field_the_brief_pins(tmp_path):
    """`/live` 是**页面的唯一数据源**（设计注 §8.3 第 1 条）—— 字段一个都不能少。"""
    g = FakeGraph(steps=[_Snap(values={"site": SITE, "ws_url": WS_URL, "visits": ["intake"]},
                               interrupts=(_gate(step="intake"),))])
    client = _client(graph_factory=_factory(g), window=StubWindow(alive=True))
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    _wait(client, job_id)

    live = _live(client, job_id)
    assert set(live) == {"job_id", "status", "say", "delivered", "stage", "stage_say", "note",
                         "events", "gate", "input", "stop", "shots_note", "window", "rounds",
                         "truncated"}
    assert live["job_id"] == job_id
    assert live["status"] == "waiting"
    assert live["delivered"] is False, "「停下来了」不是「交付了」—— 两件事分开摆"
    assert live["say"], "永远有一句人话（不编话那一条）"
    # 骨架：这三样这一版**故意**是空的/恒定的（Task 6/8 填它们）——
    # 钉在这儿，免得被读成「已经在工作了」。
    assert live["gate"] is None, "骨架：闸口投影是 Task 8 的事（页面别据此显示按钮）"
    assert live["input"]["mode"] == "queue"
    assert live["input"]["queued"] == []
    assert live["rounds"] == []
    assert live["stop"]["requested"] is False
    assert live["truncated"] is False
    assert live["stage"], "它现在/刚要做的那个节点：说不出来也得说「不知道」，不许空着"
    assert isinstance(live["shots_note"], str)


def test_the_live_view_is_the_pages_only_source_and_reading_never_writes(tmp_path):
    """`/live` 是 **GET** —— 读三次不许往时间线上加东西。

    加了的话时间线会「看的人越多、越长」，而且页面每 2–3 秒轮询一次就长三条
    （设计注 §8.2：页面每 2–3 秒轮询它一个）。顺序也是**旧 → 新**（`n` 递增）。
    """
    g = FakeGraph(steps=[_Snap(values={"site": SITE, "ws_url": WS_URL, "visits": ["intake"]},
                               interrupts=(_gate(step="intake"),))])
    client = _client(graph_factory=_factory(g), window=StubWindow(alive=True))
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    _wait(client, job_id)

    first = _live(client, job_id)
    for _ in range(3):
        again = _live(client, job_id)
    assert again["events"] == first["events"], "读三次，时间线一个字节都不该变"
    numbers = [e["n"] for e in again["events"]]
    assert numbers == sorted(numbers), "时间线是**旧 → 新**（排序用 n）"
    assert len(set(numbers)) == len(numbers)


def test_live_is_404_for_a_job_nobody_knows(tmp_path):
    client = _client(graph_factory=_factory(FakeGraph()), window=None)
    r = client.get("/job/job-nope/live")
    assert r.status_code == 404
    assert "没这个任务" in r.json()["detail"]


def test_the_seven_cells_survive_the_trip_to_the_live_json(tmp_path):
    """契约那七格要**一路走得到页面**（`/live` 的 `events`）—— 不是只在 `events.py` 里落得下。

    这一版**没人填**它们（那是后面的任务）；这里用唯一的写入口 `Service.narrate`
    记一条**带满七格**的，看它原样出现在 `/live` 里。
    """
    g = FakeGraph(steps=[_Snap(values={"site": SITE, "ws_url": WS_URL, "visits": ["intake"]},
                               interrupts=(_gate(step="intake"),))])
    client = _client(graph_factory=_factory(g), window=StubWindow(alive=True))
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    _wait(client, job_id)
    job = client.app.state.service._jobs[job_id]
    client.app.state.service.narrate(
        job, "step", "第 2 步：点了「下一步」", who="agent",
        step_no=2, action={"what": "click", "target": "下一步"},
        receipt={"raw": "ok", "from": "cdp"},
        sig_before={"url": URL, "text": "a1b2c3", "visible": 12},
        sig_after=None, why="动作之后窗口没答，读不到页面")

    last = _live(client, job_id)["events"][-1]
    assert set(events.CELLS) - {"expect", "verdict"} <= set(last["data"])
    assert last["data"]["receipt"]["raw"] == "ok"
    assert last["data"]["sig_after"] is None, "「看不见」是一等值，一路带到页面都一样"
    assert last["who"] == "agent" and last["say"].startswith("第 2 步")


def test_truncated_is_true_and_explained_when_events_are_dropped(tmp_path, monkeypatch):
    """超上限时 `truncated` 为真，而且 `note` 里**说清**丢了什么（设计注 §8.2：「并说明」）。"""
    g = FakeGraph(steps=[_Snap(values={"site": SITE, "ws_url": WS_URL, "visits": ["intake"]},
                               interrupts=(_gate(step="intake"),))])
    client = _client(graph_factory=_factory(g), window=StubWindow(alive=True))
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    _wait(client, job_id)

    monkeypatch.setattr(events, "MAX_EVENTS", 3)
    svc = client.app.state.service
    job = svc._jobs[job_id]
    for i in range(5):
        svc.narrate(job, "note", "第 %d 句" % i)

    live = _live(client, job_id)
    assert live["truncated"] is True
    assert len(live["events"]) == 3, "留下的是最后三条"
    assert "丢" in live["note"], "丢了东西要**说出来**：%r" % live["note"]


# ═════════════════ 目录表第 3/4/6 行：跑起来就有话说 ═════════════════


def test_no_path_is_silent_from_submission_to_delivery(tmp_path):
    """**这一条是这一片的核心断言**（brief Step 2 点名的那条机械断言）。

    一个桩 job 走完它的一生：提交 → 停在闸上 → 人说一句 → 探路没走完（窗口那一支）
    → 重开窗口 → 交付。走完之后这五个 `kind` 一个都不能少：

        submitted / running / window_reopened / human_said / done

    **任何一条静默的路径都会让这条测试红** —— 比如 `_advance` 里少接一个 narrate、
    或者 `reopen` 只写 `job.say` 不写时间线（P9：`say` 是一次性的，下一次 `_advance` 就冲掉了）。
    """
    py_path = tmp_path / "forms" / "sites" / "example-funnel.py"
    py_path.parent.mkdir(parents=True, exist_ok=True)
    py_path.write_text("# 落盘的那一版\n", encoding="utf-8")
    g = FakeGraph(steps=[
        _Snap(values={"site": SITE, "ws_url": WS_URL, "visits": ["intake"]},
              interrupts=(_gate(step="intake", say="准备开工：…"),)),
        _Snap(values={"site": SITE, "ws_url": WS_URL, "visits": ["explore"],
                      "end_reason": "explore_unfinished",
                      "end_note": "探路没走完（预算到顶）—— 账本在，接着走不用从头再探。"}),
        _Snap(values={"site": SITE, "ws_url": NEW_WS_URL, "visits": ["selftest"],
                      "end_reason": "delivered", "py_path": str(py_path),
                      "end_note": "这一版 py 落盘了：%s" % py_path}),
    ])
    client = _client(graph_factory=_factory(g), window=StubWindow(alive=True))
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    _wait(client, job_id)
    assert client.post("/job/%s/reply" % job_id,
                       json={"action": "continue", "note": "不是那个按钮，是下面那个"}).status_code == 200
    _wait(client, job_id)
    r = client.post("/job/%s/reopen" % job_id,
                    json={"ws_url": NEW_WS_URL, "entry_url": URL})
    assert r.status_code == 200, r.text
    _wait(client, job_id)

    kinds = _kinds(client, job_id)
    for kind in ("submitted", "running", "window_reopened", "human_said", "done"):
        assert kind in kinds, "这一条路是**静默**的：%r 里没有 %r" % (kinds, kind)
    assert kinds[0] == "submitted", "时间线从头读起：先「收到了」"
    assert _live(client, job_id)["delivered"] is True, "落盘的那一版在盘上"
    assert _live(client, job_id)["status"] == "done"


def test_a_job_that_has_to_wait_behind_another_gets_a_queued_event(tmp_path):
    """目录表第 3 行那一半：「排队等窗口（前面还有别的 run 在用）。」

    单飞（D6）下这一句说的是**常有**的那种情况：前一个 run 正占着工作线程，这一个得等。
    判据是**这一刻**的两样事实（队列里压着活 / 有人正在跑）—— 说不出「前面有东西」时
    **宁可不说**（漏说只是少一句话，说反了就是编话）。

    ⚠️ 「正占着」用 `hold` 桩**停住**第一次 invoke 来造（不靠抢时序）：
    停在闸上的 job **不算**前面有人 —— 那一刻工作线程是空的，第二个 job 不必等。
    """
    inside = threading.Event()
    release = threading.Event()

    def hold(n):
        if n == 1:
            inside.set()
            assert release.wait(5), "第一个 job 没被放走"

    first_graph = FakeGraph(steps=[_Snap(values={"site": SITE, "ws_url": WS_URL,
                                                 "visits": ["intake"]},
                                         interrupts=(_gate(step="intake"),))],
                            hold=hold)
    client = _client(graph_factory=_factory(first_graph), window=StubWindow(alive=True))
    first = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    assert inside.wait(5), "第一个 job 没进到 invoke 里"
    second = client.post("/run", json=_brief(tmp_path)).json()["job_id"]   # 这一刻它得排队
    release.set()
    _wait(client, first)
    _wait(client, second)

    kinds = _kinds(client, second)
    assert kinds[0] == "submitted"
    assert "queued" in kinds, "前面有 run 在用窗口，这一条必须说出来：%r" % kinds
    says = _says(client, second, "queued")
    assert any("排队等窗口" in s for s in says), says


def test_running_says_what_is_happening(tmp_path):
    """目录表第 4 行：`_advance` 拿到 job 就说「在跑：真浏览器 + 模型…」。

    ⚠️ 它与 `job.say` 是**同一句**（`/job/{id}` 上那句）—— 两处各写一份就是两份口径。
    """
    g = FakeGraph(steps=[_Snap(values={"site": SITE, "ws_url": WS_URL, "visits": ["intake"]},
                               interrupts=(_gate(step="intake"),))])
    client = _client(graph_factory=_factory(g), window=StubWindow(alive=True))
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    _wait(client, job_id)
    says = _says(client, job_id, "running")
    assert says == ["在跑：真浏览器 + 模型，做完一步或需要你时就会停下来。"], says


# ═════════════════ 目录表第 6 行：终局（done / cap_hit）═════════════════


def test_a_terminal_end_is_narrated_with_the_end_note_verbatim(tmp_path):
    """快照里的 `end_note` **原话**照搬（Global Constraints：不编话）。"""
    note = "这一版 py 落盘了：/tmp/example-funnel.py"
    g = FakeGraph(steps=[_Snap(values={"site": SITE, "ws_url": WS_URL, "visits": ["deliver"],
                                      "end_reason": "delivered", "end_note": note})])
    client = _client(graph_factory=_factory(g), window=StubWindow(alive=True))
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    _wait(client, job_id)
    assert _says(client, job_id, "done") == [note]
    assert "cap_hit" not in _kinds(client, job_id)


def test_hitting_a_cap_is_narrated_with_the_prefix_and_the_note_verbatim(tmp_path):
    """目录表第 6 行另一半：撞上限（`lint_cap` / `selftest_cap` / `revision_cap`）。

    人话 = 「撞上限了：」 + `end_note` **原话**（设计注 §3.2 第 4 行）。
    """
    note = "这一版 py 被打回 2 次还是同样的地方不过 —— 停下，别再来第 3 次。"
    g = FakeGraph(steps=[_Snap(values={"site": SITE, "ws_url": WS_URL, "visits": ["lint"],
                                      "end_reason": "lint_cap", "end_note": note})])
    client = _client(graph_factory=_factory(g), window=StubWindow(alive=True))
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    _wait(client, job_id)
    got = _says(client, job_id, "cap_hit")
    assert got == ["撞上限了：" + note], got
    assert "done" not in _kinds(client, job_id), "撞上限是它自己的那个 kind"
    raw = [e for e in _live(client, job_id)["events"] if e["kind"] == "cap_hit"][0]
    assert raw["data"]["end_reason"] == "lint_cap"
    assert raw["data"]["end_note"] == note


def test_a_terminal_end_without_a_note_is_not_silent(tmp_path):
    """到头了但快照里**没留人话** —— 那就明说「它没说清」，**不许**记一条空话或者不记。

    （`Timeline.add` 连空 `say` 都不收：写不出人话说明还没想清。）
    """
    g = FakeGraph(steps=[_Snap(values={"site": SITE, "ws_url": WS_URL, "visits": ["deliver"],
                                      "end_reason": "delivered", "end_note": ""})])
    client = _client(graph_factory=_factory(g), window=StubWindow(alive=True))
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    _wait(client, job_id)
    says = _says(client, job_id, "done")
    assert len(says) == 1 and says[0].strip(), says
    assert "没说清" in says[0] or "没留" in says[0], says[0]


# ═════════════════ 目录表第 5 行：跑挂了（跑挂 ≠ 跑成）═════════════════


def test_a_job_that_blew_up_is_narrated_as_failed_with_the_raw_error(tmp_path):
    """`_advance` 的 except：现有那句人话 + **原始错误进 `data`**（不是被人话盖掉）。"""
    g = FakeGraph(raise_on=[1])
    client = _client(graph_factory=_factory(g), window=StubWindow(alive=True))
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    view = _wait(client, job_id, until=("failed",))
    assert view["status"] == "failed"

    live = _live(client, job_id)
    failed = [e for e in live["events"] if e["kind"] == "failed"]
    assert len(failed) == 1, [e["kind"] for e in live["events"]]
    assert "没跑成" in failed[0]["say"], failed[0]["say"]
    assert "窗口连不上了" in failed[0]["data"]["error"], failed[0]["data"]
    assert "RuntimeError" in failed[0]["data"]["error"], "异常类型也在（原始错误，不是转述）"
    assert "done" not in [e["kind"] for e in live["events"]], "跑挂**绝不许**被读成跑成"


# ═════════════════ 目录表第 1 行：窗口没了 ═════════════════


def test_a_dead_window_is_narrated_once_and_names_the_step_it_stopped_before(tmp_path):
    """每次 `_advance` 返回之后问一次窗口层；**从「不是没了」翻成「没了」**的那一刻记一条。

    判据（四条一起）：
    - 翻转前**不记**（问不出来/还活着都不是「没了」）；得**真去问**窗口层，不能靠猜；
    - 翻转后记**一条**，且**带时间戳**；
    - 往后每次 `_advance` 都去问，但**不再重复记**（同一件事记十遍不是信息）；
    - 那句话里的人话步骤名是**从快照里读出来的**（停在「写这一版 py」之前），不是编的。

    ⚠️ 这一条**直接调 `_advance`**（不经过 `/reply`）：窗口死掉的那一刻，`reply()` 会先
    拦下来（P6：那是它存在的理由）。所以「窗口死了之后还有一跳要跑」这个局面 ——
    探路途中窗口没了，就是它 —— 只能这么造。
    """
    g = FakeGraph(steps=[_Snap(values={"site": SITE, "ws_url": WS_URL, "visits": ["intake"]},
                               interrupts=(_gate(step="draft", say="写这一版 py？"),))])
    win = StubWindow(alive=True)
    client = _client(graph_factory=_factory(g), window=win)
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    _wait(client, job_id)
    assert "window_died" not in _kinds(client, job_id), "窗口还活着，这一条不该出现"
    assert win.probes >= 1, "得**真去问**一次窗口层，不能靠猜"

    win.alive_ = False                      # 假死：下一次 `_advance` 返回之后就翻成「没了」
    svc = client.app.state.service
    job = svc._jobs[job_id]
    svc._advance(job, None)
    died = [e for e in job.timeline.all() if e["kind"] == "window_died"]
    assert len(died) == 1, died
    assert "窗口没了" in died[0]["say"] and "Bit 的窗口只活几分钟" in died[0]["say"]
    assert "写这一版 py" in died[0]["say"], "停在**哪一步**之前要说出来：%r" % died[0]["say"]
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", died[0]["at"]), died[0]["at"]
    assert died[0]["data"]["where"] == "draft", died[0]["data"]

    # 再走一步：窗口**还是**死的，但不许再记一条（记的是「新事实」）
    svc._advance(job, None)
    assert [e["kind"] for e in job.timeline.all()].count("window_died") == 1


def test_a_window_that_died_before_the_human_answered_says_so_on_the_timeline(tmp_path):
    """窗口死在**人回话之前**：`reply()` 会拒绝（P6），但这件事**也要上时间线**。

    ⚠️ 这条是「没有静默的路径」的另一处落点：这个事实是在 `reply()` 里被发现的
    （人回了话服务才去查），那儿要是只抛一个 409，坐在页面前面的人只看到「回话被拒」——
    而「窗口没了、它停在哪一步之前、接下来要做什么」这几件事**一句都没说**
    （409 的正文说给了 HTTP 调用方，时间线是给别人看的）。
    """
    g = FakeGraph(steps=[_Snap(values={"site": SITE, "ws_url": WS_URL, "visits": ["intake"]},
                               interrupts=(_gate(step="draft", say="写这一版 py？"),))])
    win = StubWindow(alive=True)
    client = _client(graph_factory=_factory(g), window=win)
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    _wait(client, job_id)

    win.alive_ = False
    r = client.post("/job/%s/reply" % job_id, json={"action": "continue"})
    assert r.status_code == 409, r.text
    assert "已经不在了" in r.json()["detail"], "拒绝还是要拒（P6 的理由没变）"

    died = [e for e in _live(client, job_id)["events"] if e["kind"] == "window_died"]
    assert len(died) == 1, [e["kind"] for e in _live(client, job_id)["events"]]
    assert "写这一版 py" in died[0]["say"], died[0]["say"]
    assert "重开一个窗口" in died[0]["say"], died[0]["say"]


def test_a_window_layer_that_cannot_be_asked_is_not_narrated_as_dead(tmp_path):
    """反面：问不出来（`alive()` 回 `None`）**不是**「死了」—— 不许误杀（`_window_is_gone` 的规矩）。"""
    g = FakeGraph(steps=[_Snap(values={"site": SITE, "ws_url": WS_URL, "visits": ["intake"]},
                               interrupts=(_gate(step="intake"),))])
    win = StubWindow(alive=True)
    win.alive_ = None
    client = _client(graph_factory=_factory(g), window=win)
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    _wait(client, job_id)
    assert "window_died" not in _kinds(client, job_id)


# ═════════════════ 目录表第 2 行：窗口重开 ═════════════════


def test_reopening_a_window_says_which_of_the_two_things_happened(tmp_path):
    """`reopen` 之后那一句：**探路重跑** 与 **从断点接着跑** 是两句不同的话（照搬现有的，别合并）。

    两句说的是**代价不一样**的两件事（探路从头 vs 从账本接着走）——
    合并成一句就等于把「要再花一整趟」还是「几步就到」含糊过去。
    """
    def _run(end_reason, visit):
        g = FakeGraph(steps=[_Snap(values={"site": SITE, "ws_url": WS_URL, "visits": [visit],
                                           "end_reason": end_reason,
                                           "end_note": "停在这儿了。"})])
        client = _client(graph_factory=_factory(g), window=StubWindow(alive=True))
        job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
        _wait(client, job_id)
        r = client.post("/job/%s/reopen" % job_id,
                        json={"ws_url": NEW_WS_URL, "entry_url": URL})
        assert r.status_code == 200, r.text
        _wait(client, job_id)
        said = _says(client, job_id, "window_reopened")
        assert len(said) == 1, said
        return said[0]

    restart = _run("explore_unfinished", "explore")
    resume = _run("no_window", "selftest")
    assert EXPLORE_RESTART_HINT in restart, restart
    assert RESUME_FROM_BREAK_HINT in resume, resume
    assert restart != resume, "两句不同的话，别合并成一句"


# ═════════════════ 目录表第 7 行：服务重启过 ═════════════════


def test_a_recovered_job_says_so_on_the_timeline_and_in_the_note(tmp_path):
    """`_recover()`：从 checkpoint 捡回来的 job 要**说**「我是捡回来的」，
    而且 `/live` 的 `note` 要**明说**「这之前的时间线没有了」（设计注 §3.5）。

    时间线是 **process-local** 的：新的服务实例上它是**空的**（那不是 bug，是这一版的边界）——
    所以「空」这件事必须由 `note` 说出来，不许让人以为「它什么都没干」。

    ⚠️ 这一条要用**真图**（桩依赖）：假图的状态活在它自己身上，**checkpoint 里什么都没有** ——
    那样测的是「捡一个不存在的东西」。「重启之后还读得到」靠的正是状态真的在 saver 里（R-19）。
    """
    saver = InMemorySaver().with_allowlist(graph.MSGPACK_ALLOWLIST)
    factory = lambda brief, deps: graph.build(checkpointer=saver, deps=_never_used_deps())  # noqa: E731
    client = _client(graph_factory=factory, window=None, checkpointer=saver)
    job_id = client.post("/run", json=_brief(
        tmp_path, ws_url=None, allow_skips=["country", "viewport"])).json()["job_id"]
    view = _wait(client, job_id)
    assert view["status"] == "waiting" and view["gate"]["step"] == "intake", view

    # 「服务重启了」：另一个服务实例，接在**同一份** saver 上（状态在 saver 里，R-19）
    client2 = _client(graph_factory=factory, window=None, checkpointer=saver)
    before = _live(client2, job_id)
    assert before["events"] == [], "时间线**不持久**：重启之后它是空的（§3.5）"
    assert RESTART_NOTE_HINT in before["note"], before["note"]

    job = client2.app.state.service._recover(job_id)          # 捡回来（`reply`/`reopen` 走的就是它）
    assert job is not None
    assert [e["kind"] for e in job.timeline.all()] == ["recovered"]
    assert "checkpoint 里捡回来" in job.timeline.all()[0]["say"]
    after = _live(client2, job_id)
    assert [e["kind"] for e in after["events"]] == ["recovered"]
    assert RESTART_NOTE_HINT in after["note"]
    assert after["status"] == "waiting", "时间线没了不等于任务没了：它还在闸上等人"


# ═════════════════ 目录表第 8 行：人说的话 ═════════════════


def test_a_human_note_lands_with_their_own_words(tmp_path):
    """`reply()` 带 note：**人的原话** + 这句话去哪了（说了就要让人知道它去哪了）。"""
    g = FakeGraph(steps=[_Snap(values={"site": SITE, "ws_url": WS_URL, "visits": ["intake"]},
                               interrupts=(_gate(step="intake"),))])
    client = _client(graph_factory=_factory(g), window=StubWindow(alive=True))
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    _wait(client, job_id)
    mine = "不是那个按钮，是下面那个"
    client.post("/job/%s/reply" % job_id, json={"action": "revise", "note": mine})
    _wait(client, job_id)

    said = [e for e in _live(client, job_id)["events"] if e["kind"] == "human_said"]
    assert len(said) == 1, said
    assert mine in said[0]["say"], said[0]["say"]
    assert "写这一版 py" in said[0]["say"], "这句话去哪了也要说：%r" % said[0]["say"]
    assert said[0]["who"] == "you", "人的话就是 `who=you`（页面靠它决定气泡长相）"


def test_a_plain_continue_is_narrated_too(tmp_path):
    """**没有 note** 的「继续」也得有一条 —— 否则最常见的那次交互在时间线上是**空白**。

    只是它不能说「你的话带进去了」（没有话）—— 那一格照旧如实说。
    """
    g = FakeGraph(steps=[_Snap(values={"site": SITE, "ws_url": WS_URL, "visits": ["intake"]},
                               interrupts=(_gate(step="intake"),))])
    client = _client(graph_factory=_factory(g), window=StubWindow(alive=True))
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    _wait(client, job_id)
    client.post("/job/%s/reply" % job_id, json={"action": "continue"})
    _wait(client, job_id)

    said = [e for e in _live(client, job_id)["events"] if e["kind"] == "human_said"]
    assert len(said) == 1, said
    assert said[0]["say"].strip(), said[0]
    assert "写这一版 py" not in said[0]["say"], "没有话带进去，就不许说「这句话带进去了」"


# ═════════════════ 目录表第 9 行：这一轮没有图 ═════════════════


def test_a_shot_that_could_not_be_taken_is_narrated_with_its_why(tmp_path):
    """负例：抓拍抛异常 → `shot_missing` + `why` 原文。

    旁路纪律（Global Constraints）：拍不成**绝不**抛到 `_advance` 上 ——
    闸照旧在（人还能回话），只是这一轮少一张图，而**为什么少**要留一句人话。
    """
    g = FakeGraph(steps=[_Snap(values={"site": SITE, "ws_url": WS_URL, "visits": ["intake"]},
                               interrupts=(_gate(step="intake"),))])
    client = _client(graph_factory=_factory(g), window=StubWindow(alive=True), capture=_shot_boom)
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    view = _wait(client, job_id)
    assert view["status"] == "waiting", "图没拍成，闸照旧在（人还能回话）"

    live = _live(client, job_id)
    missing = [e for e in live["events"] if e["kind"] == "shot_missing"]
    assert len(missing) == 1, [e["kind"] for e in live["events"]]
    assert "这一轮没留下图" in missing[0]["say"], missing[0]["say"]
    assert "cdp 子进程没了" in missing[0]["say"], "why 原文照抄：%r" % missing[0]["say"]
    assert "cdp 子进程没了" in missing[0]["data"]["why"]


def test_a_shot_that_did_work_is_not_narrated_as_missing(tmp_path):
    """反面：拍成了就**没有** `shot_missing`（不许把成功也记成缺图）。"""
    g = FakeGraph(steps=[_Snap(values={"site": SITE, "ws_url": WS_URL, "visits": ["intake"]},
                               interrupts=(_gate(step="intake"),))])
    client = _client(graph_factory=_factory(g), window=StubWindow(alive=True), capture=_shot_ok)
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    view = _wait(client, job_id)
    assert view["status"] == "waiting"
    assert "shot_missing" not in _kinds(client, job_id)
    assert client.app.state.service._jobs[job_id].shot_notes[0]["name"] == "pause-1.png"


# ═════════════ 「没有静默的路径」在读的那一侧 ═════════════


def test_state_that_cannot_be_read_is_not_silent(tmp_path):
    """读状态读不上来时**也要有一条事件** —— 这是「没有静默的路径」在**读**这一侧的样子。

    （这条不是目录表里的九条之一：九条讲的是「已知的那些事实」，
    而这一条讲的是**新出现的一条会静默的路** —— 它要是只 `traceback.print_exc()`，
    「窗口还在不在」「这一趟是不是到头了」这两件事就**没人说过**。）

    ⚠️ 这里**不走 HTTP**：状态读不回来的时候 `/job/{id}` 自己也会 500（它读的是同一个快照），
    所以直着调 `_advance` 才测得到时间线这一侧。
    ⚠️ job 得**登记在册**（照 `start()` 的样子）：`_snapshot` 是先看登记表里那个 `job.graph` 的
    （那正是「服务读不回**它自己这张图**的状态」这条路的正身）。
    """
    g = BlindGraph(steps=[_Snap(values={"site": SITE, "ws_url": WS_URL, "visits": ["intake"]},
                                interrupts=(_gate(step="intake"),))])
    svc = service.Service(graph_factory=_factory(g), capture=_shot_ok,
                          checkpointer=InMemorySaver().with_allowlist(graph.MSGPACK_ALLOWLIST))
    job = service.Job(job_id="job-blind", brief=_brief(tmp_path), status=service.RUNNING)
    job.graph = g
    svc._jobs["job-blind"] = job
    svc.narrate(job, "submitted", service.SUBMITTED_SAY)      # 假装它是正常提交的
    svc._advance(job, None)                                   # 图跑完了，服务读不回状态

    timeline = job.timeline.all()
    kinds = [e["kind"] for e in timeline]
    assert kinds == ["submitted", "running", "state_unreadable"], kinds
    bad = timeline[-1]
    assert "saver 连不上了" in bad["say"], bad["say"]
    assert "saver 连不上了" in bad["data"]["error"]
    assert "checkpoint 里" in bad["say"], "任务本身还在（读不回来的只是服务这一步）：%s" % bad["say"]
