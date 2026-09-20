"""Task 12 的服务那一半：**把「窗口」这件事从人手里拿走**。

## 病（用户 2026-09-20 在真面板上量的）

> 「**我的意思是希望面板上运营就能操作。。**」

⇒ 验收口径是「**运营全程不碰命令行**」。而今天有**两下**是命令行的：

| | 今天怎么做 | 不做的后果 |
|---|---|---|
| ① 派之前核「窗口已关」 | `curl …/browser/pids/alive -d '{"ids":[…]}'` | 拿到**正在死掉的 ws url** ⇒ 任务 20 秒内报连接超时 |
| ② 跑完关窗 | `bash bit.sh close …` | 窗口泄漏（**是硬规矩**） |

外加一条同一族的：窗口没了之后的出路（`/reopen` 要一串**新** `ws_url`）**也是命令行**
（`bit.sh open`），而窗口只活几分钟 ⇒ 运营每隔一会儿就会撞上它一次。

## 这一份钉的三件事（都是**服务侧**的判据）

1. **派之前不用人核**：这个部署有窗口层时，`POST /run` **不带 `ws_url`** 也开得了工 ——
   窗口由服务自己开（`fresh_open`）；没接窗口层时它**照旧**什么都不假装（图停在 `intake` 点名）。
2. **跑完/叫停之后，人点一下就能关**：`POST /job/{id}/window/close`。
   ⚠️ **不是无条件自动关**（本仓明文设计：停在闸上是唯一适合人接管的时刻，人可能正看着那个窗口）
   ⇒ 只有 `running`/`queued` 两档**不给关**，其余由人按。
3. **面板看得见状态**：`/live` 的 `window` 那一格里多了 `state` / `state_say` /
   `can_close` / `can_reopen` —— **读数只有一份**（那条已经在跑的探针时间线 `window.jsonl`），
   页面照抄，不自己推。

## 这一份**不**测什么（照实说）

- **页面 JS**：那在 `tests/test_console_js.py`（执行夹具）里。
- **真窗口**：一次都不开（桩窗口层；`tests/conftest.py` 的兜底也把 cdp 指到不存在的地方）。
- **判决**：这一份量的是「服务说了什么、做了什么」，不是「它做得对不对」。
"""

from __future__ import annotations

import json
import pathlib
import sys
import threading
import time

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
#: 调用方**自己**给的那个窗口（老办法：人先在宿主上 `bit.sh open`）。
CALLER_WS = "ws://192.168.1.197:55555/devtools/page/CALLER"
#: 服务**自己**开出来的那个窗口（`fresh_open` 吐的）。
FRESH_WS = "ws://192.168.1.197:55555/devtools/page/FRESH"


# ─────────────────────────────── 桩 ───────────────────────────────


class FakeGraph:
    """照着服务要的协议做的假图（与 `test_console_e2e.py` 那份同一形状，只留要用的那几个）。

    `hold(第几次 invoke)` 让测试**停在这一次 invoke 里面**（不靠抢时序）——
    「它正在跑」那一档必须是这样造出来的：手动改 `job.status` 会与工作线程抢
    （本文件实测：那条用例因此**时红时绿**，红的形状是「它明明在跑，服务却给关了」）。
    """

    def __init__(self, *, snap, hold=None):
        self.snap = snap
        self.hold = hold or (lambda n: None)
        self.invokes: list = []

    def invoke(self, payload, config):
        self.invokes.append(payload)
        self.hold(len(self.invokes))
        return {"__interrupt__": list(self.snap.interrupts)} if self.snap.interrupts else {}

    def get_state(self, config):
        return self.snap

    def update_state(self, config, values, as_node=None):
        self.snap = _Snap(values={**self.snap.values, **values}, next=(as_node,), interrupts=())
        return {}


class _Snap:
    def __init__(self, values=None, next=(), interrupts=()):
        self.values = dict(values or {})
        self.next = next
        self.interrupts = interrupts


def _gate(step: str, say: str = "准备开工：…"):
    return Interrupt(value={"step": step, "say": say, "facts": {}, "can": ["让它继续"]},
                     id="i-%s" % step)


def _unfinished_explore() -> _Snap:
    """「探路没走完、账本还在」那一档（`/reopen` **接得住**的形状之一，Task 6 / §1.9）。"""
    return _Snap(values={"site": SITE, "ws_url": "ws://old/DEAD",
                         "visits": ["intake", "explore"],
                         "end_reason": "explore_unfinished",
                         "end_note": "探路没走完（预算到顶）—— 账本在，接着走不用从头再探。"})


def _waiting_snap(**values) -> _Snap:
    base = {"site": SITE, "ws_url": FRESH_WS, "visits": ["intake"]}
    base.update(values)
    return _Snap(values=base, next=("intake",), interrupts=(_gate("intake"),))


class StubWindow:
    """窗口层的替身：把**每一次**被要求做的事记下来（这一份量的几乎都是「服务有没有真去问它」）。

    `probe_answers`：每一次探活按顺序取一个（取完重复最后一个）——
    `/browser/pids/alive` 的真实形状是 `{"alive": bool|None, "pid": …}`。
    `close_raises`：关窗时抛（演「关窗口没被确认」那一档）。
    """

    def __init__(self, *, alive=True, close_raises=None, ws=FRESH_WS):
        self.worker_ip, self.bit_id, self.port = "192.168.1.222", "8f2c1a90", 54345
        self.alive_ = alive
        self.close_raises = close_raises
        self.ws = ws
        self.calls: list = []

    def set_viewport(self, width, height):
        self.calls.append(("set_viewport", width, height))

    def probe(self):
        self.calls.append(("probe",))
        return {"alive": self.alive_, "pid": 4772 if self.alive_ else None}

    def alive(self):
        self.calls.append(("alive",))
        return self.alive_

    def close(self):
        self.calls.append(("close",))
        if self.close_raises:
            raise RuntimeError(self.close_raises)
        self.alive_ = False

    def fresh_open(self):
        self.calls.append(("fresh_open",))
        self.alive_ = True
        return self.ws

    #: 这一份里「服务问过窗口几次」全靠它（别拿调用方的记忆当读数）
    def asked(self, what: str) -> int:
        return len([c for c in self.calls if c[0] == what])


def _client(tmp_path, *, window, snap=None, hold=None, raise_server_exceptions=True) -> TestClient:
    holder = {"g": FakeGraph(snap=snap or _waiting_snap(), hold=hold)}
    kw = {} if raise_server_exceptions else {"raise_server_exceptions": False}
    return TestClient(service.create_app(graph_factory=lambda brief, deps: holder["g"],
                                         window=window, shots_dir=str(tmp_path / "shots"),
                                         explore_dir=str(tmp_path / "explore"), **kw))


def _brief(**over) -> dict:
    brief = {"url": URL, "goal": GOAL, "success_text": SUCCESS, "site": SITE,
             "out_dir": "forms/sites"}
    brief.update(over)
    return brief


def _wait(client, job_id, *, until=("waiting", "done", "failed"), timeout=20.0):
    """等这一趟停到某一档。

    ⚠️ **必须等**：`POST /run` 是 202（活交下去了），响应那一刻它多半还是 `queued`/`running`
    —— 不等就往下按「关窗口」，量到的是「它还在跑，不给关」那条 409（而不是要量的那一条）。
    本文件实测栽过一次：十一条用例里有九条是这么红的。
    """
    end = time.time() + timeout
    last = None
    while time.time() < end:
        last = client.get("/job/%s" % job_id).json()
        if last["status"] in until:
            return last
        time.sleep(0.02)
    raise AssertionError("这一趟没有在 %.0fs 内走到 %s：%r" % (timeout, until, last))


def _one_job(tmp_path, *, window, snap=None, until=("waiting",), **over):
    """交一趟进去、**等它停到那一档**，回 `(client, job_id, service)`。"""
    client = _client(tmp_path, window=window, snap=snap)
    job_id = client.post("/run", json=_brief(**over)).json()["job_id"]
    _wait(client, job_id, until=until)
    return client, job_id, client.app.state.service


def _live(client, job_id) -> dict:
    r = client.get("/job/%s/live" % job_id)
    assert r.status_code == 200, r.text
    return r.json()


# ═══════════ 1. 派之前不用人核：窗口由服务自己开 ═══════════


def test_a_run_can_be_dispatched_without_anyone_checking_the_window_first(tmp_path):
    """★ 正身：`POST /run` **不带 `ws_url`** 也开得了工 —— 窗口由服务自己开出来。

    这一条对着那张表的第一行（`curl …/browser/pids/alive`）：**那一下今天是人做的**，
    因为「调用方得先确认旧窗口已关、再把新窗口那串交进来」。而服务手上本来就有窗口层，
    它自己的 `fresh_open` 本来就是「先收拾掉旧的那个、再开一个干净的」
    —— 所以「有没有旧窗口」不该留给调用方去查。

    量三样：① 交付出去的载荷里那个窗口**是服务开的那一个**（不是调用方没给就空着）；
    ② `fresh_open` **真被叫过**（不是拿别的东西糊的）；③ 调用方**什么都没给**。
    """
    win = StubWindow(ws=FRESH_WS)
    client, job_id, svc = _one_job(tmp_path, window=win)
    sent = svc._jobs[job_id].graph.invokes[0]
    assert sent["ws_url"] == FRESH_WS, sent
    assert win.asked("fresh_open") == 1, win.calls
    assert "ws_url" not in _brief(), "这一条的前提是**调用方没给** ws_url"


def test_without_a_window_layer_the_run_still_stops_honestly(tmp_path):
    """没接窗口层 ⇒ **照旧**什么都不假装：那个窗口它开不出来，也不会编一个。

    ⚠️ 这一条是上面那条的**反面**（「服务自己开」不许变成「服务假装开了一个」）：
    没窗口层时 `ws_url` 不进载荷，图会在 `intake` 停下点名（R-31 要的正是这个）。
    """
    client, job_id, svc = _one_job(tmp_path, window=None)
    sent = svc._jobs[job_id].graph.invokes[0]
    assert "ws_url" not in sent, sent


def test_the_run_records_one_window_reading_right_away(tmp_path):
    """交上去的那一刻**量一眼**那个窗口 —— 面板上「现在什么状态」那一格当场有话说。

    ⚠️ 这一行**不是新的口径**：它就是那条探针时间线的第一行（`window.jsonl`）。
    不量的话，头 15 秒里那一格只能说「服务还没问过」（探针第一眼要等一个间隔）。
    """
    win = StubWindow()
    _, job_id, svc = _one_job(tmp_path, window=win)
    rows = (tmp_path / "explore" / job_id / "window.jsonl").read_text(
        encoding="utf-8").strip().splitlines()
    assert rows, "窗口时间线上一行都没有 —— 交上去那一刻没人量过这一眼"
    first = json.loads(rows[0])
    assert first["alive"] == "alive", first
    assert "刚交上去" in first["note"], first
    assert svc.window_state(job_id)["state"] == "alive"


# ═══════════ 2. 面板看得见状态：`/live` 那几格，读数只有一份 ═══════════


def test_the_live_window_cell_carries_the_state_and_the_two_offers(tmp_path):
    """`/live` 的 `window` 那格里：状态（人话 + 机器格）与**服务说了算的**两个「能不能」。

    ⚠️ 「能不能关 / 能不能重开」必须由服务给：页面自己拿状态推一遍，就会出现
    「按钮在、按下去 409」那种**点了没反应**的形状（这一片要治的正是它）。
    """
    win = StubWindow(alive=True)
    client, job_id, _ = _one_job(tmp_path, window=win)
    cell = _live(client, job_id)["window"]
    assert cell["state"] == "alive", cell
    assert "开着" in cell["state_say"], cell["state_say"]
    #: 那一句里带着「这是什么时候问的」—— 一个不带时刻的状态会被人当成「此刻」
    assert "问的" in cell["state_say"], cell["state_say"]
    #: 它停在闸上（没在跑）⇒ **关**给
    assert cell["can_close"] is True, cell
    #: ⚠️ 但**重开**不给：`/reopen` 接得住的是「**图已经停下来**」的两档，
    #: 而「停在闸口等人回话」不是那两档（`_resume_point_from` 一个判据两处用 ——
    #: 按钮在不在与端点收不收**必须是同一句话**，不然就是「按钮在、按下去 409」）。
    assert cell["can_reopen"] is False, cell
    #: 身份那五格照旧（一个字都没动）
    assert (cell["worker"], cell["bit_id"], cell["api_port"]) == ("192.168.1.222", "8f2c1a90", 54345)


def test_nothing_was_ever_measured_says_so_instead_of_guessing(tmp_path):
    """一行读数都没有时**不许**猜一个「开着」—— 明说还没量过（并说清多久量一次）。"""
    win = StubWindow()
    svc = service.Service(window=win, shots_dir=str(tmp_path / "shots"),
                          explore_dir=str(tmp_path / "explore"))
    got = svc.window_state("job-never-seen")
    assert got["state"] == service.WINDOW_UNMEASURED, got
    assert "还没问过" in got["state_say"], got["state_say"]


def test_a_window_that_cannot_be_asked_is_not_called_dead(tmp_path):
    """**问不出来 ≠ 死了**（`measure.UNKNOWN` 那三态里的第三态）—— 那句话必须说清这件事。

    那是这条线上最容易变成谎的一格：`_window_is_gone` 立的就是这条规矩
    （问不出来一律当不知道，不许误杀）。
    """
    win = StubWindow()
    svc = service.Service(window=win, shots_dir=str(tmp_path / "shots"),
                          explore_dir=str(tmp_path / "explore"))
    svc._probe_window_row("job-x")            # 先记一行
    win.alive_ = None                         # 之后窗口服务开始答非所问
    svc._probe_window_row("job-x")
    got = svc.window_state("job-x")
    assert got["state"] == service.measure.UNKNOWN, got
    assert "不等于" in got["state_say"], got["state_say"]
    #: 而且它**不许**被当成死用（`_window_is_gone` 只认「明确说死了」）
    assert svc._window_is_gone("ws://x/y") is False


# ═══════════ 3. 关窗口：由人按，而且只在确认死掉之后才说「关掉了」 ═══════════


def test_the_operator_can_close_the_window_with_one_press(tmp_path):
    """★ 人按一下就能关（对着那张表的第二行：`bash bit.sh close …`）。"""
    win = StubWindow(alive=True)
    client, job_id, svc = _one_job(tmp_path, window=win)
    r = client.post("/job/%s/window/close" % job_id, json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert win.asked("close") == 1, win.calls
    assert body["state"] == "dead", body
    assert "关掉了" in body["say"], body["say"]
    #: 关完之后 `alive()` 说的是「不在了」—— 那句「关掉了」有读数撑着
    assert svc.window_state(job_id)["state"] == "dead"


def test_the_window_is_not_closed_while_it_is_running(tmp_path):
    """**不许在它跑着的时候把窗口抽走** —— 而且这一条要**在窗口层那一侧**也成立。

    ⚠️ 只回一个 409 是不够的：真正要防的是「服务**真去关了**」。
    所以这一条量的是 `close` **一次都没被叫过**（光看响应码，一个先关后拒的实现照样绿）。

    ⚠️「它正在跑」是**卡住第一次 invoke** 造出来的（不是手动改 `job.status` ——
    那样造出来的那一刻会与工作线程抢，本文件实测过：那条写法**时红时绿**）。
    """
    inside, release = threading.Event(), threading.Event()
    win = StubWindow(alive=True)

    def hold(n):
        if n == 1:
            inside.set()
            assert release.wait(10), "没人放它走"

    snap = _Snap(values={"site": SITE, "ws_url": FRESH_WS, "visits": []})
    client = _client(tmp_path, window=win, snap=snap, hold=hold)
    job_id = client.post("/run", json=_brief()).json()["job_id"]
    assert inside.wait(10), "它没有进到 invoke 里"
    try:
        r = client.post("/job/%s/window/close" % job_id, json={})
        assert r.status_code == 409, r.text
        assert win.asked("close") == 0, "它还在跑，服务真去关了：%r" % win.calls
        assert "先别关" in r.json()["detail"], r.json()
        #: 那句话还要说清**怎么办**（等它停下来）—— 不是一句「不行」
        assert "等它停下来" in r.json()["detail"], r.json()["detail"]
    finally:
        release.set()


def test_a_close_that_cannot_be_confirmed_does_not_claim_it_worked(tmp_path):
    """关窗口**没被确认**（探活说它还活着）⇒ 照实说没成，**不许**说「关掉了」。

    这一条是「不许静默、不许编」那条纪律在窗口这一格上的样子：
    `BitWindow.close()` 自己会探活（SKILL：`close` 返回成功 ≠ 窗口真关了），
    它抛出来的那句话本身**就是人话** —— 原样端出去。
    """
    win = StubWindow(alive=True, close_raises="关窗口的请求回了成功，但**探活说它还活着**")
    client, job_id, _ = _one_job(tmp_path, window=win)
    r = client.post("/job/%s/window/close" % job_id, json={})
    assert r.status_code != 200, r.text
    assert "探活说它还活着" in r.json()["detail"], r.json()
    assert "关掉了" not in r.json()["detail"], r.json()


def test_a_deployment_without_a_window_layer_says_so_instead_of_pretending(tmp_path):
    """没接窗口层 ⇒ 明说「关不了」，**不假装关了一下**（也没有按钮可给）。"""
    client, job_id, _ = _one_job(tmp_path, window=None)
    r = client.post("/job/%s/window/close" % job_id, json={})
    assert r.status_code == 400, r.text
    assert "没有接窗口层" in r.json()["detail"], r.json()


def test_closing_leaves_a_line_on_the_timeline(tmp_path):
    """**人按的那一下要留痕**：时间线上一条 `window_closed`（不是 `window_died`）。

    ⚠️ 两个词**不是一回事**（`events.KINDS` 里那条注释）：`window_died` 是服务**撞见**
    「它不在了」（人什么都没做），这一条是服务**执行了人的请求** —— 合成一条，
    读时间线的人就分不出「窗口自己到点了」与「我关的」。
    """
    win = StubWindow(alive=True)
    client, job_id, _ = _one_job(tmp_path, window=win)
    assert client.post("/job/%s/window/close" % job_id, json={}).status_code == 200
    kinds = [e["kind"] for e in _live(client, job_id)["events"]]
    assert kinds.count("window_closed") == 1, kinds
    assert "window_died" not in kinds, "人按的那一下被记成了「窗口自己没了」：%r" % kinds


# ═══════════ 4. 窗口没了之后的出路：服务自己开，不再落回命令行 ═══════════


def test_reopen_opens_the_window_itself_when_nobody_gave_one(tmp_path):
    """★ `POST /reopen` **空手发就行** —— 服务自己去开一个新窗口（原先要 `bit.sh open`）。"""
    win = StubWindow()
    snap = _unfinished_explore()
    client = _client(tmp_path, window=win, snap=snap)
    job_id = client.post("/run", json=_brief()).json()["job_id"]
    _wait(client, job_id, until=("done",))
    #: 面板那一格**要能重开**（同一个判据两处用：按钮在不在与端点收不收必须是同一句话）。
    #: ⚠️ 量在**按之前** —— 按完之后这一趟又跑起来了，那一格本来就会翻回「不给」。
    assert _live(client, job_id)["window"]["can_reopen"] is True, _live(client, job_id)["window"]
    before = win.asked("fresh_open")
    r = client.post("/job/%s/reopen" % job_id, json={})
    assert r.status_code == 200, r.text
    assert win.asked("fresh_open") == before + 1, "服务没有自己去开窗口：%r" % win.calls
    state = client.app.state.service._snapshot(job_id).values
    assert state["ws_url"] == FRESH_WS, state


def test_reopen_still_takes_a_window_the_caller_hands_over(tmp_path):
    """老办法**没有被拿走**：调用方给了 `ws_url` 就用它的（这条路上不许自作主张）。"""
    win = StubWindow()
    client = _client(tmp_path, window=win, snap=_unfinished_explore())
    job_id = client.post("/run", json=_brief()).json()["job_id"]
    _wait(client, job_id, until=("done",))
    before = win.asked("fresh_open")
    r = client.post("/job/%s/reopen" % job_id, json={"ws_url": CALLER_WS})
    assert r.status_code == 200, r.text
    assert win.asked("fresh_open") == before, "调用方给了窗口，服务却自己去开了一个"
    assert client.app.state.service._snapshot(job_id).values["ws_url"] == CALLER_WS


def test_reopen_says_it_cannot_open_one_instead_of_pretending(tmp_path):
    """开不出来就**照实说开不出来**：没窗口层 / 窗口层抖了，两档都明说。

    ⚠️ 而且**绝不拿一个旧窗口顶替** —— 那正是「拿到一个正在死掉的 ws url」那条老路。
    """
    client = _client(tmp_path, window=None, snap=_unfinished_explore())
    job_id = client.post("/run", json=_brief()).json()["job_id"]
    _wait(client, job_id, until=("done",))
    r = client.post("/job/%s/reopen" % job_id, json={})
    assert r.status_code == 400, r.text
    assert "开不了新窗口" in r.json()["detail"], r.json()

    class _Broken(StubWindow):
        def fresh_open(self):
            raise RuntimeError("窗口服务连不上")

    client2 = _client(tmp_path, window=_Broken(), snap=_unfinished_explore())
    job_id2 = client2.post("/run", json=_brief()).json()["job_id"]
    _wait(client2, job_id2, until=("done",))
    r2 = client2.post("/job/%s/reopen" % job_id2, json={})
    assert r2.status_code != 200, r2.text
    assert "窗口服务连不上" in r2.json()["detail"], r2.json()
    assert client2.app.state.service._snapshot(job_id2).values["ws_url"] == "ws://old/DEAD", \
        "开不出来却把状态里的窗口换掉了（换成了什么？）"


# ═══════════ 5. 那条 409：**「怎么办」顶到最前面** ═══════════


def test_the_reply_409_leads_with_what_to_do(tmp_path):
    """★ 现场原话：「**我点继续没任何反应**」—— 真因是那条 409 的「怎么办」**埋在中间**。

    用户的体感：按了「继续」，屏幕上像是什么都没发生（那句话在，只是他一眼看不到那一步）。
    ⇒ 改的是**顺序与醒目程度**：第一行就是那个动作。

    这一条量的是**顺序**（拿两个位置比大小），不是「有没有那几个字」——
    后者在改之前就是绿的（那正是它当初没被发现的原因）。

    ⚠️ 顺带钉住**实质换了的那一处**（它**不是**「顺序动了一下」）：原来那句
    「重开一个窗口，再从这里接着走」**做不到** —— 这条用例自己会先量一遍
    （`/reopen` 在这一档回什么），所以这一条**不会**退化成「字还在就算过」。
    """
    win = StubWindow(alive=False)
    client, job_id, _ = _one_job(tmp_path, window=win)
    win.alive_ = False                        # 窗口在闸上死掉了（`fresh_open` 会把它弄活）
    r = client.post("/job/%s/reply" % job_id, json={"action": "continue"})
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    here, why = detail.index("怎么办"), detail.index("为什么不能")
    #: 「最前面」= 第一行（`**` 是服务那句原话自己的着重号，不算字）
    assert detail.startswith("**怎么办"), "「怎么办」不在最前面（前面还有别的字）：\n%s" % detail
    assert here < why, "「怎么办」还在「为什么」后面（运营还是一眼看不到）：\n%s" % detail
    #: **为什么不能接着走**那半句一个字都不许少（那是「不编话」的另一半）
    assert "已经不在了" in detail and "误导" in detail, detail
    #: ★ 那两步**落在面板的按钮上**（不再是「你去敲一条命令」）——
    assert "按「停下」" in detail and "按「重新来一遍」" in detail, detail
    #: ★★ 而**原先指的那条路**在这一档真的走不通 —— 这条用例**当场量一遍**，
    #:    不是为了证明它错，是为了让「换了实质」这件事有个可复算的凭据：
    blocked = client.post("/job/%s/reopen" % job_id, json={})
    assert blocked.status_code == 409, (
        "实测：停在闸上 + 窗口没了这一档 `/reopen` 是能接住的 —— "
        "那这条 409 就该去指它（而不是指「停下 + 重新来一遍」）：%r" % blocked.text)


def test_the_two_buttons_the_409_names_are_really_on_the_panel(tmp_path):
    """★ 上面那条 409 让人按的两个按钮，**在这一档真的在屏幕上**。

    ⚠️ 这一条是「不许指一个不在那儿的按钮」那条规矩的正身：人话里点名一个按钮，
    而它此刻是 `hidden` 的 —— 那就是「按下去没反应」换了一张脸。
    `「重新来一遍」`那一档还差一步（先「停下」），所以它由**下一步**保证出现。
    """
    #: ⚠️ 这一条用**真图**（不是上面那个桩图）。为什么：「停」要**真的兑现**才行 ——
    #: 桩图的 `invoke` 不会把中断吃掉，停在闸上的 job 会一直停在那儿，
    #: 「跑完了」那一档永远到不了（本文件实测：桩图下这一条会卡在 `waiting` 上超时）。
    #: 真图走到 `intake` 那道闸**不碰浏览器、不打模型**（那道闸在探路之前；
    #: `tests/conftest.py` 也把 cdp 指到一条不存在的路径上）。
    saver = InMemorySaver().with_allowlist(graph.MSGPACK_ALLOWLIST)
    client = TestClient(service.create_app(checkpointer=saver, window=StubWindow(),
                                           shots_dir=str(tmp_path / "shots"),
                                           explore_dir=str(tmp_path / "explore")))
    job_id = client.post("/run", json=_brief()).json()["job_id"]
    _wait(client, job_id, until=("waiting",))
    #: 第 ① 步那个按钮（「停下」）：闸上就是**能按**的（页面按 `stop.requested` / 状态禁用它）
    live = _live(client, job_id)
    assert live["status"] == "waiting", live["status"]
    assert live["stop"]["requested"] is False, live["stop"]
    assert client.post("/job/%s/stop" % job_id, json={}).status_code == 200
    _wait(client, job_id, until=("done", "failed"))
    #: 第 ② 步那个按钮（「重新来一遍」）：到「跑完了」那一档才出现（页面按 `over` 露它）
    after = _live(client, job_id)
    assert after["status"] in ("done", "failed"), after["status"]
    r = client.post("/job/%s/again" % job_id)
    assert r.status_code == 202, r.text
    assert r.json()["job_id"].startswith("job-"), r.json()
