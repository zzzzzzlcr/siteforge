"""Task 10 Step 1：端到端 —— 「看 → 说 → 停 → 再看」走完一遍（计划里那串请求逐条对上）。

## 这一条测的是什么

一条**不用手工拼 cdp、不碰真浏览器、不碰真模型**的路：`TestClient` + 桩图 + 桩窗口 +
**桩图**（一张真 PNG，打进桩抓拍里）。它要证的不是某一格，是**四样同时在位**：

| 哪一样 | 从哪儿看 |
|---|---|
| **时间线** | `/live.events`（`submitted` / `running` / `human_said` / `done` 逐条） |
| **轮次** | `/live.rounds`（第 1 轮 `done=None`；第 2 轮 `done.shots` 那一对） |
| **图** | `/live.rounds[].now` 的文件名 + `GET /job/{id}/shot/{name}` **真取到字节** |
| **人的话** | `/say` 那句进了 `input.queued` / `draft_note`，`human_said` 事件 `who="you"` |

## 「不碰真浏览器 / 不碰真模型」是**量出来的**

第二条用例（`…runs_without_a_browser_or_a_model`）把两条路口封死再走同一串请求：
`subprocess.Popen` **计数**（真起了进程 ⇒ 计数非 0 ⇒ 红）+ `llm.client` 一叫就炸
（真打了模型 ⇒ 炸 ⇒ 红）。`tests/conftest.py` 的兜底只是把 cdp 二进制**指到一个不存在的
地方**（那是「想用也用不了」）；这里量的是**压根没人想去用它** —— 两件事不一样。

## 这一份**不**测什么（照实说）

- **页面 JS**：`agent/console.html` 里的渲染函数不在这一份里（那是
  `tests/test_console_js.py` 那套执行夹具的事）。这里是 HTTP 层：**服务给出去的那几格**。
- **真站**：一次真窗口都不开（Task 10 的 Step 3 手工验收与 Task 9 的真站演练是**人**那一趟的事）。
- **写 `runtime/`**：截图一律落 `tmp_path`（`shots_dir=` 显式给）。
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
import struct
import subprocess
import sys
import threading
import time
import zlib

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Interrupt

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import graph, llm, service  # noqa: E402

SITE = "example-funnel"
URL = "https://example-funnel.test/quiz"
GOAL = "走到「Thank you」那一页，把报价拿到"
SUCCESS = "Thank you"
#: 载荷里那个窗口**不是**本机（「有没有偷偷退回 `127.0.0.1:9222`」在同一片里已有用例钉着；
#: 这里只要一个**看起来像真窗口**的串就够 —— 桩抓拍根本不看它）。
WS = "ws://192.168.1.197:55555/devtools/page/ABC"
#: 人在闸上说的那句话（第 2 轮要看见它**原样**躺在输入框里）。
MY_WORDS = "那个 cookie 横幅先点掉再往下走"


# ─────────────────────────────── 桩 ───────────────────────────────


@dataclasses.dataclass
class _Snap:
    """`get_state()` 返回的那个东西里，服务只准看这三个字段（与别处的桩同一形状）。"""

    values: dict
    next: tuple = ()
    interrupts: tuple = ()


def _gate(step: str, say: str = "准备开工：…"):
    return Interrupt(value={"step": step, "say": say, "facts": {}, "can": ["让它继续"]},
                     id="i-%s" % step)


class FakeGraph:
    """照着服务要的协议做的假图：`invoke` / `get_state` / `update_state`。

    `hold(第几次 invoke)` 让测试**停在那一次 invoke 里面**（不靠抢时序）——
    这一份要用它两次：第 1 次（好让「status=在跑」那一刻是**确定的**）、
    第 2 次（好让 `/reply` 的响应**确定**是 `running` 而不是「还在等人」）。
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
    """窗口层的替身：`/live` 的「这个窗口」那一块要问它三样身份 + `alive`。"""

    def __init__(self, worker="192.168.1.222", bit_id="8f2c1a90", port=54345):
        self.worker_ip, self.bit_id, self.port = worker, bit_id, port
        self.alive_ = True

    def set_viewport(self, width, height):
        pass

    def alive(self):
        return self.alive_


def _png() -> bytes:
    """一张**真** PNG（1×1）—— 桩图也得是真的（`/shot/` 那条路会按 `image/png` 回它）。"""
    def chunk(kind: bytes, body: bytes) -> bytes:
        raw = kind + body
        return struct.pack(">I", len(body)) + raw + struct.pack(">I", zlib.crc32(raw))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00"))
            + chunk(b"IEND", b""))


def _capture(blob: bytes, calls: list):
    """桩抓拍：把那张真 PNG 写到 `dest`，回**文件名**（与 `capture_via_cli` 同一个契约）。"""
    def capture(ws_url, dest, *, timeout=None):
        calls.append((ws_url, str(dest)))
        where = pathlib.Path(str(dest))
        where.parent.mkdir(parents=True, exist_ok=True)
        where.write_bytes(blob)
        return where.name, ""
    return capture


def _brief(tmp_path) -> dict:
    return {"url": URL, "goal": GOAL, "success_text": SUCCESS, "site": SITE,
            "ws_url": WS, "form_file": str(tmp_path / "form.json"),
            "out_dir": str(tmp_path / "forms" / "sites")}


def _client(tmp_path, *, graph_factory, window=None, capture=None, **kw) -> TestClient:
    kw.setdefault("checkpointer", InMemorySaver().with_allowlist(graph.MSGPACK_ALLOWLIST))
    return TestClient(service.create_app(graph_factory=graph_factory,
                                         window=window or StubWindow(),
                                         capture=capture,
                                         shots_dir=str(tmp_path / "shots"), **kw))


def _wait(client, job_id, *, until=("waiting", "done", "failed"), timeout=20.0):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        last = client.get("/job/%s" % job_id).json()
        if last["status"] in until:
            return last
        time.sleep(0.02)
    raise AssertionError("job 没有在 %.0fs 内走到 %s：%r" % (timeout, until, last))


def _live(client, job_id) -> dict:
    r = client.get("/job/%s/live" % job_id)
    assert r.status_code == 200, r.text
    return r.json()


def _kinds(live: dict) -> list:
    return [e["kind"] for e in live["events"]]


def _of_kind(live: dict, kind: str) -> list:
    return [e for e in live["events"] if e["kind"] == kind]


def _three_steps() -> FakeGraph:
    """这一趟的图：`intake` 停一道闸 → `draft` 停一道闸 → 人在闸上按停，图到头。

    ⚠️ 第三张快照**没有中断**（图走到 END）—— 那正是「在闸上按停」的落点：
    设计注 §4.1 矩阵第 3 行「已经在闸上 → 立刻」，**这一步不做**，这一趟就收在那儿。
    """
    values = {"site": SITE, "ws_url": WS}
    return FakeGraph(steps=[
        _Snap(values={**values, "visits": ["intake"]}, next=("intake",),
              interrupts=(_gate("intake", "先看看这一页长什么样。"),)),
        _Snap(values={**values, "visits": ["intake", "draft"]}, next=("draft",),
              interrupts=(_gate("draft", "按上面的，写这一版 py。"),)),
        _Snap(values={**values, "visits": ["intake", "draft"], "end_reason": "human_stop",
                      "end_note": "人在闸上按了停：这一步不做，这一趟收在这儿。"}, next=()),
    ])


# ═══════════════════════ 1. 端到端：看 → 说 → 停 → 再看 ═══════════════════════


def test_the_whole_console_loop_from_looking_to_saying_to_stopping(tmp_path):
    """`POST /run` → … → 图上取到字节：一条请求都不手工拼 cdp，而四样都在位。

    ⚠️ **两处时序**用事件卡住（`hold`），不靠跑得快：`/run` 之后那一刻（要看见
    `status=running` + 时间线上的 `running`）、`/reply` 那一刻（响应必须是 `running`，
    不是「还在等人」—— 那一条在 `tests/test_service.py:1276` 有它自己的正身）。
    """
    hold_first, hold_second = threading.Event(), threading.Event()
    capture_calls: list = []
    g = _three_steps()
    g.hold = (lambda n: hold_first.wait(10) if n == 1
              else (hold_second.wait(10) if n == 2 else None))
    blob = _png()
    client = _client(tmp_path, graph_factory=lambda brief, deps: g,
                     capture=_capture(blob, capture_calls))

    # ── ① GET /runs（**跑之前**）：空列表 + 那句「这个列表是这个进程记得的那些」──
    before = client.get("/runs").json()
    assert before["runs"] == [], before
    assert "还没有任何运行" in before["note"] and "这个进程" in before["note"], before["note"]

    # ── ② POST /run → 202 + job_id ──
    r = client.post("/run", json=_brief(tmp_path))
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    assert job_id.startswith("job-"), job_id

    # ── ③ GET /runs → 它在列表里（`note` 那一格照旧在：有行时它是空的）──
    runs = client.get("/runs").json()
    assert [x["job_id"] for x in runs["runs"]] == [job_id], runs
    assert runs["runs"][0]["site"] == SITE, runs["runs"][0]
    assert "note" in runs and runs["note"] == "", runs

    # ── ④ GET /live → status=running，时间线上已有 submitted / running ──
    live = _live(client, job_id)
    assert live["status"] == "running", live["status"]
    assert _kinds(live)[:2] == ["submitted", "running"], _kinds(live)
    assert _of_kind(live, "submitted")[0]["who"] == "system"

    # ── ⑤ 放它跑到第 1 道闸 ──
    hold_first.set()
    view = _wait(client, job_id)
    assert view["status"] == "waiting" and view["gate"]["step"] == "intake", view
    live = _live(client, job_id)
    assert live["status"] == "waiting"
    # 第 1 轮：正要做的就是闸上那一步；第一道闸之前什么都没跑过（`done` 是 None）
    assert [c["n"] for c in live["rounds"]] == [1], live["rounds"]
    first = live["rounds"][0]
    assert first["step"] == "intake", first
    assert first["done"] is None, first
    assert first["now"]["name"] == "pause-1.png", first["now"]
    # 那张图**真能取到**（字节走 `/job/{id}/shot/{name}`，不进 JSON）
    shot = client.get("/job/%s/shot/%s" % (job_id, first["now"]["name"]))
    assert shot.status_code == 200, shot.text
    assert shot.headers["content-type"] == "image/png", shot.headers
    assert shot.content == blob, "端点回的不是桩里那张字节"
    # 顶层接线信息：窗口三样都在；每步抓拍开着 ⇒ `shots_note` 一句话都不说
    #: ⚠️ 这**还是**一条「键集合恰好等于这些」的断言（Task 12 加了后三格：这个 job 的窗口
    #: 现在是死是活、什么时候量的、现在能不能关）。加格 = 改这一行；**不许**改成 `<=`：
    #: 那一条读法是「至少要有这些」，于是别人往里多塞一格这一行**再也不响**。
    assert set(live["window"]) == {"worker", "bit_id", "api_port", "how", "when",
                                   "state", "state_say", "can_close", "can_reopen"}, live["window"]
    #: 这个 job 的窗口**刚交上去就量过一眼**（`start()` 里那一行）—— 桩窗口答「活着」。
    assert live["window"]["state"] == "alive", live["window"]
    assert "打开" in live["window"]["state_say"] or "开着" in live["window"]["state_say"], \
        live["window"]
    assert (live["window"]["worker"], live["window"]["bit_id"],
            live["window"]["api_port"]) == ("192.168.1.222", "8f2c1a90", 54345), live["window"]
    assert live["shots_note"] == "", live["shots_note"]
    assert live["input"]["mode"] == "gate", live["input"]
    # 字节一个都不进 JSON（同一个事实的穷举：整份 `/live` 里没有内联图）
    assert "base64" not in json.dumps(live, ensure_ascii=False)

    # ── ⑥ POST /say → 排队（**它绝不替人往前走一步**）→ 时间线上一条 `who="you"` ──
    r = client.post("/job/%s/say" % job_id, json={"text": MY_WORDS})
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["queued"] is True and body["delivered"] is False, body
    assert body["n"] == 1, body
    assert "不会自动发" in body["say"], body["say"]
    live = _live(client, job_id)
    said = _of_kind(live, "human_said")
    assert len(said) == 1 and said[0]["who"] == "you", said
    assert MY_WORDS in said[0]["say"], said[0]["say"]
    assert [x["text"] for x in live["input"]["queued"]] == [MY_WORDS], live["input"]["queued"]
    assert live["input"]["draft_note"] == MY_WORDS, "到闸上就进输入框（页面照这一格预填）"

    # ── ⑦ POST /reply {"action":"continue"} → 200，**立刻**是 running（不是 waiting）──
    r = client.post("/job/%s/reply" % job_id, json={"action": "continue"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "running", (
        "回完话的响应说「还在等人」—— 调用方会再回一次，而那一句会落到**下一道闸**上：%r"
        % r.json())

    # ── ⑧ 放它跑到第 2 道闸 → 第 2 轮出现，输入框里是**我刚说的那句话** ──
    hold_second.set()
    view = _wait(client, job_id)
    assert view["status"] == "waiting" and view["gate"]["step"] == "draft", view
    live = _live(client, job_id)
    assert [c["n"] for c in live["rounds"]] == [1, 2], live["rounds"]
    assert live["rounds"][1]["step"] == "draft", live["rounds"][1]
    assert live["input"]["draft_note"] == MY_WORDS, live["input"]
    assert live["rounds"][1]["done"]["shots"] == {"before": "pause-1.png",
                                                 "after": "pause-2.png"}, live["rounds"][1]["done"]

    # ── ⑨ POST /stop → 一句人话的落点 + 时间线上一条（「你按了「停」：…」）──
    n_events = len(_live(client, job_id)["events"])
    r = client.post("/job/%s/stop" % job_id)
    assert r.status_code == 200, r.text
    stop = r.json()["stop"]
    assert stop["requested"] is True, stop
    assert stop["will_stop_at"] and "停" in stop["will_stop_at"], stop
    assert "://" not in stop["will_stop_at"], "落点那句话里不许编 URL：%r" % stop
    live = _live(client, job_id)
    assert len(live["events"]) > n_events, "「停」按下去之后时间线上得多一条"
    stopped = [e for e in _of_kind(live, "human_said") if "停" in e["say"]]
    assert stopped and stopped[-1]["who"] == "you", live["events"]

    # ── ⑩ 再看：这一趟收在「人按停」那一支上；两张图**都还取得到** ──
    view = _wait(client, job_id, until=("done", "failed"))
    assert view["status"] == "done", view
    assert _of_kind(_live(client, job_id), "done"), "到头了时间线上没有那条「done」"
    live = _live(client, job_id)
    last = live["rounds"][-1]
    pair = last["done"]["shots"]
    assert pair["before"] and pair["after"], pair
    for name in (pair["before"], pair["after"]):
        got = client.get("/job/%s/shot/%s" % (job_id, name))
        assert got.status_code == 200, (name, got.text)
        assert got.content == blob, name
    # 「这一趟最后那张图」（按停那一刻拍的）挂在最后一张卡上 —— 不许有一张图没人提
    assert last["last_shot"]["name"] == "pause-3.png", last["last_shot"]

    # ── 四样都在位（计划 Step 1 的判据，逐样点名）────────────────────────
    assert [e["n"] for e in live["events"]] == list(range(1, len(live["events"]) + 1)), \
        "时间线的号必须单调递增（页面按它排）"
    assert all(e["say"] for e in live["events"]), "时间线上有一条没有人话的事件"
    assert {e["who"] for e in live["events"]} <= {"agent", "system", "you"}, live["events"]
    assert [c["n"] for c in live["rounds"]] == [1, 2], "轮次：两轮"
    assert capture_calls and len(capture_calls) == 3, capture_calls
    assert g.invokes and len(g.invokes) == 3, (
        "图被推了 %d 次（该推的只有：起跑、人按的「继续」、人按的「停」）" % len(g.invokes))


# ═════════ 2. 「不碰真浏览器、不碰真模型」是量出来的，不是读代码相信的 ═════════


def test_the_whole_loop_runs_without_a_browser_or_a_model(tmp_path, monkeypatch):
    """同一串请求再走一遍，这次把两条路口**封死**：起了进程 / 打了模型 ⇒ 当场红。

    - `subprocess.Popen` 换成**计数**版（`.venv` 里那条 cdp 链、`go build`、任何东西 ——
      只要有人起进程，计数就不是 0）；
    - `llm.client` 换成**一叫就炸**（探路那一步真去打模型的话，服务会把它记成「跑挂」，
      这一条会红在状态上 —— 而不是静静地打了真模型）。

    ⚠️ 射程：这量的是**这一串请求**（桩图那条路）。`tests/conftest.py` 的兜底管的是
    「想用也用不了」；两条一起才是「不碰真浏览器」这句话的全部。
    """
    spawned: list = []

    def counting_popen(*args, **kwargs):
        #: ⚠️ **记下来就抛**（不转手给真的 `Popen`）：这一条不许为了让别的用例过
        #: 而真去起一个进程 —— 那正是它要证明**不会发生**的事。
        spawned.append(args[:1] or kwargs.get("args"))
        raise AssertionError("这一串请求里起了一个子进程：%r" % (spawned[-1],))

    def no_model():
        raise AssertionError("这一串请求里有人去打了真模型（`llm.client` 被调了）")

    monkeypatch.setattr(subprocess, "Popen", counting_popen)
    monkeypatch.setattr(llm, "client", no_model)

    hold_first, hold_second = threading.Event(), threading.Event()
    g = _three_steps()
    g.hold = (lambda n: hold_first.wait(10) if n == 1
              else (hold_second.wait(10) if n == 2 else None))
    calls: list = []
    client = _client(tmp_path, graph_factory=lambda brief, deps: g,
                     capture=_capture(_png(), calls))

    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    hold_first.set()
    _wait(client, job_id)
    client.post("/job/%s/say" % job_id, json={"text": MY_WORDS})
    client.post("/job/%s/reply" % job_id, json={"action": "continue"})
    hold_second.set()
    _wait(client, job_id)
    client.post("/job/%s/stop" % job_id)
    view = _wait(client, job_id, until=("done", "failed"))

    assert view["status"] == "done", view
    assert spawned == [], "这一串请求里起了子进程（那是真浏览器那条路）：%r" % (spawned,)
    assert calls, "桩抓拍一次都没被叫到 —— 那说明这一趟根本没走到拍图那一步"
    assert calls and all(c[0] == WS for c in calls), calls
