"""Task 3：服务侧的**闸拍** + 图片端点 + 两块接线信息（设计注 §5.5 / §8.1 / §十）。

三件事，每一件都有一条「错了会怎样」：

1. **跑到闸口时拍一张**（`pause-<n>.png`）—— 那张图是**证据**：它得真落在盘上，
   `GET /job/{id}/shot/{name}` 得原样吐回来（`image/png`、字节一致）。
   `n` 是**第几轮**（Task 6 的配对规则：第 n 道闸上拍的那张叫 `pause-<n>`），
   所以拍不成的那一轮**也占号** —— `shot_notes` 里 `name` 为 `None` + 一句 `why`。
2. **拍不成绝不许把闸弄丢**（本任务最关键的一条）。抓拍是**旁路**：`capture` 抛异常、
   卡住、或者压根没有窗口，job **仍然停在闸上等人**，只是多一句人话。
   **闸没了 = 人的交互点被吃掉** —— 比少一张图坏得多。
3. **两块接线信息**（`window` / `shots_note`）**不编话**：`how` / `when` 两句写死在 py 里
   （页面原样显示）；降级 B 那句话与 `explore` 读**同一个**开关 —— 两处各解析一次，
   就会出现「图没了、一个字没解释」，那正是设计注 §5.5 明令禁止的**静默降级**。

全用桩：`TestClient` + 桩图（一张**真** PNG）+ 桩窗口 + 一个**假 cdp 二进制**
（`/bin/sh` 脚本：记下被叫时的 `--host/--port`，把那张真 PNG 写到 `--out`）。
于是走的是**生产那条路**（服务 → `shots.capture_via_cli` → 子进程），
而「有没有退回 `127.0.0.1:9222`」这件事才**看得见**。**不开浏览器窗口、不碰宿主 :1080。**
"""

from __future__ import annotations

import pathlib
import re
import struct
import sys
import threading
import time
import zlib

import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import browser_agent, graph, service, shots  # noqa: E402
from test_agent_shots import _Shooter  # noqa: E402  —— 步拍那个桩（它本身就是命令计数器）
from test_browser_agent import PAGE_LANDING, _run  # noqa: E402
from test_service import FakeGraph, _Snap, _gate, _wait  # noqa: E402  —— 桩图与「等到它停下」

URL = "https://example-funnel.test/quiz"
GOAL = "走到「Thank you」那一页，把报价拿到"
SUCCESS = "Thank you"
SITE = "example-funnel"
#: 载荷里那个窗口 **不是** 本机 —— 「有没有偷偷退回 `127.0.0.1:9222`」才验得出来。
WS = "ws://192.168.1.197:55555/devtools/page/ABC"


# ─────────────────────────────── 桩 ───────────────────────────────


def _png() -> bytes:
    """一张**真** PNG（1×1）。桩图也得是真的：`capture_via_cli` 会验 magic 与 `IEND`。"""
    def chunk(kind: bytes, body: bytes) -> bytes:
        raw = kind + body
        return struct.pack(">I", len(body)) + raw + struct.pack(">I", zlib.crc32(raw))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00"))
            + chunk(b"IEND", b""))


def _brief(tmp_path, **over) -> dict:
    """一份开场白。`ws_url=None` 表示**这一趟还没开窗口**（那份键就不给）。"""
    brief = {"url": URL, "goal": GOAL, "success_text": SUCCESS, "site": SITE,
             "ws_url": WS, "form_file": str(tmp_path / "form.json"),
             "out_dir": str(tmp_path / "forms" / "sites")}
    brief.update(over)
    return {k: v for k, v in brief.items() if v is not None}


def _fake_cdp(tmp_path, blob: bytes):
    """一个**假 cdp 二进制**：记下被叫时的 `--host/--port`，把 `blob` 写到 `--out`。

    为什么要这个东西而不是纯 Python 桩：这样走的是**生产那条路**
    （服务 → `shots.capture_via_cli` → 子进程），而「没窗口时**一个进程都不该起**」
    与「没退回本机 9222」这两件事才有东西可断（脚本把它看到的参数记在一份日志里）。
    """
    src = tmp_path / "captured.png"
    src.write_bytes(blob)
    log = tmp_path / "cdp-args.log"
    script = tmp_path / "cdp"
    script.write_text(
        "#!/bin/sh\n"
        'host=""; port=""; out=""\n'
        'while [ $# -gt 0 ]; do\n'
        '  case "$1" in\n'
        '    --host) host="$2"; shift 2;;\n'
        '    --port) port="$2"; shift 2;;\n'
        '    --out)  out="$2";  shift 2;;\n'
        '    *) shift;;\n'
        '  esac\n'
        'done\n'
        'echo "$host $port" >> "%s"\n'
        'cp "%s" "$out"\n' % (log, src),
        encoding="utf-8")
    script.chmod(0o755)
    return script, log


def _client(tmp_path, *, capture=None, shot_timeout=None, window=None,
            graph_factory=None, shots_dir=None, **kw) -> TestClient:
    """一个装好桩的 TestClient —— 运行产物（截图）一律落 `tmp_path`（**不写 `runtime/`**）。"""
    kw.setdefault("checkpointer", InMemorySaver().with_allowlist(graph.MSGPACK_ALLOWLIST))
    return TestClient(service.create_app(
        graph_factory=graph_factory, window=window, capture=capture,
        shot_timeout=shot_timeout,
        shots_dir=str(shots_dir if shots_dir is not None else tmp_path / "shots"), **kw))


def _one_gate() -> FakeGraph:
    """桩图：跑一次就停在第一道闸（`intake`）上。"""
    return FakeGraph(steps=[_Snap(values={"visits": ["intake"]}, next=("intake",),
                                  interrupts=(_gate(),))])


def _to_the_first_gate(tmp_path, monkeypatch, *, ws_url=WS, blob=None, **kw):
    """把一个 job 跑到第一道闸：返回 `(client, job_id, 那张图的字节, cdp 的日志)`。"""
    png = _png() if blob is None else blob
    script, log = _fake_cdp(tmp_path, png)
    monkeypatch.setenv("SITEFORGE_CDP_BIN", str(script))
    client = _client(tmp_path, graph_factory=lambda b, d: _one_gate(), **kw)
    r = client.post("/run", json=_brief(tmp_path, ws_url=ws_url))
    assert r.status_code == 202, r.text
    return client, r.json()["job_id"], png, log


# ───────────────────── 1. 闸口上那张图：落盘 + 端点 ─────────────────────


def test_the_first_gate_leaves_pause_1_on_disk(tmp_path, monkeypatch):
    """跑到第一道闸 → 盘上有 `pause-1.png`、`pauses == 1`，拍的是**载荷里那个窗口**。"""
    client, job_id, png, log = _to_the_first_gate(tmp_path, monkeypatch)
    view = _wait(client, job_id)
    assert view["status"] == "waiting", view
    assert view["gate"]["step"] == "intake", view["gate"]

    job = client.app.state.service._jobs[job_id]
    assert job.pauses == 1, job.shot_notes
    assert job.shot_notes == [{"n": 1, "name": "pause-1.png", "why": ""}], job.shot_notes
    where = tmp_path / "shots" / job_id / "pause-1.png"
    assert where.read_bytes() == png, "盘上那张不是拍回来的那张"
    assert log.read_text(encoding="utf-8").strip() == "192.168.1.197 55555"


def test_the_shot_endpoint_serves_the_bytes_it_promised(tmp_path, monkeypatch):
    """`GET /job/{id}/shot/{name}` → 200 + `image/png` + **字节一致**。"""
    client, job_id, png, _ = _to_the_first_gate(tmp_path, monkeypatch)
    _wait(client, job_id)
    r = client.get("/job/%s/shot/pause-1.png" % job_id)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/png", r.headers
    assert r.content == png


def test_the_second_gate_gets_its_own_file_and_the_first_one_stays(tmp_path, monkeypatch):
    """`pause-<n>` 里的 n 是**第几轮**（Task 6 的配对规则）—— 一轮一张，互不顶掉。"""
    script, _ = _fake_cdp(tmp_path, _png())
    monkeypatch.setenv("SITEFORGE_CDP_BIN", str(script))
    png = _png()
    fg = FakeGraph(steps=[
        _Snap(values={"visits": ["intake"]}, next=("intake",), interrupts=(_gate("intake"),)),
        _Snap(values={"visits": ["intake", "draft"]}, next=("draft",),
              interrupts=(_gate("draft"),))])
    client = _client(tmp_path, graph_factory=lambda b, d: fg)
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    assert _wait(client, job_id)["gate"]["step"] == "intake"

    r = client.post("/job/%s/reply" % job_id, json={"action": "continue"})
    assert r.status_code == 200, r.text
    view = _wait(client, job_id)
    assert view["status"] == "waiting" and view["gate"]["step"] == "draft", view

    job = client.app.state.service._jobs[job_id]
    assert job.pauses == 2, job.shot_notes
    assert [n["name"] for n in job.shot_notes] == ["pause-1.png", "pause-2.png"], job.shot_notes
    where = tmp_path / "shots" / job_id
    assert (where / "pause-1.png").read_bytes() == png, "第二轮把第一轮那张顶掉了"
    assert (where / "pause-2.png").read_bytes() == png


# ───────────────────────── 2. 拍不成：闸还在 ─────────────────────────


def test_a_capture_that_raises_keeps_the_gate_and_says_why(tmp_path, monkeypatch):
    """**最关键的一条**：`capture` 抛异常 → job **仍然停在闸上**，只是多一句人话。

    闸没了 = 人的交互点被吃掉（运营再也没法回那一句话），比少一张图坏得多。
    """
    calls: list = []

    def boom(ws_url, dest, *, timeout=None):
        calls.append((ws_url, str(dest)))
        raise RuntimeError("窗口连不上了（桩）")

    client, job_id, _, _ = _to_the_first_gate(tmp_path, monkeypatch, capture=boom)
    view = _wait(client, job_id)
    assert view["status"] == "waiting", view
    assert view["gate"]["step"] == "intake", view["gate"]

    job = client.app.state.service._jobs[job_id]
    assert job.pauses == 1, job.shot_notes
    note = job.shot_notes[0]
    assert note["n"] == 1 and note["name"] is None, note
    assert "RuntimeError" in note["why"] and "窗口连不上了（桩）" in note["why"], note["why"]
    assert len(calls) == 1 and calls[0][0] == WS, calls
    assert not (tmp_path / "shots" / job_id / "pause-1.png").exists()


def test_a_capture_that_wrote_something_rotten_is_recorded_not_swallowed(tmp_path,
                                                                        monkeypatch):
    """`capture` **回一句「拍不成」**（生产里最常见的那条：写下来的不是一张完整的图）。

    这里走真 `capture_via_cli` + 假 cdp：假 cdp 把一段垃圾写到 `--out`。
    要断的是**那一层的人话原样传到了人眼前**（不是一句光秃秃的「没有图」）。
    """
    client, job_id, _, _ = _to_the_first_gate(tmp_path, monkeypatch,
                                              blob=b"this is not a png")
    view = _wait(client, job_id)
    assert view["status"] == "waiting", view

    job = client.app.state.service._jobs[job_id]
    note = job.shot_notes[0]
    assert note["name"] is None, note
    assert "不是一张完整的 PNG" in note["why"], note["why"]
    assert not (tmp_path / "shots" / job_id / "pause-1.png").exists()
    # 连**半张**都不许留在盘上（`<dest>.part` 也要清掉）
    assert list((tmp_path / "shots" / job_id).glob("*")) == [], \
        list((tmp_path / "shots" / job_id).glob("*"))


def test_no_ws_url_means_no_process_at_all_and_never_localhost(tmp_path, monkeypatch):
    """没窗口（`ws_url` 空）→ **一个进程都不起**，而且**绝不**退回 `127.0.0.1:9222`。

    那一格为什么最危险（`shots.host_port` 的注释）：本机那个 9222 是**另一个**浏览器，
    拍出来的图**看着像证据** —— 比拍不到坏得多。
    """
    client, job_id, _, log = _to_the_first_gate(tmp_path, monkeypatch, ws_url=None)
    view = _wait(client, job_id)
    assert view["status"] == "waiting", view

    # ⚠️ 先断「一个进程都没起」：这一条才是这条用例**声称**的那颗钉子
    #    （退回本机那一版会先在这里红，而不是在下面某条旁证上）。
    assert not log.exists(), "没窗口却去起 cdp 了：%r" % (
        log.read_text(encoding="utf-8") if log.exists() else "")
    assert not (tmp_path / "shots" / job_id / "pause-1.png").exists()
    job = client.app.state.service._jobs[job_id]
    note = job.shot_notes[0]
    assert note["name"] is None, note
    assert "还没开浏览器" in note["why"], note["why"]      # 人话要说清是**哪件事**


def test_a_capture_that_hangs_does_not_hold_the_worker_hostage(tmp_path, monkeypatch):
    """**硬超时**（默认 20s）：`capture` 卡住时闸照旧在，并说出来它卡住了。

    为什么要有这一条：单飞的工作线程是**唯一**推图的地方（D6）—— 拍照卡住 = 整个服务停摆。
    """
    def hang(ws_url, dest, *, timeout=None):
        time.sleep(3.0)
        return "pause-1.png", ""

    client, job_id, _, _ = _to_the_first_gate(tmp_path, monkeypatch, capture=hang,
                                              shot_timeout=0.2)
    t0 = time.time()
    view = _wait(client, job_id, timeout=10.0)
    took = time.time() - t0
    assert view["status"] == "waiting", view
    assert took < 2.0, "硬超时没生效：等了 %.1f 秒" % took
    note = client.app.state.service._jobs[job_id].shot_notes[0]
    assert note["name"] is None and "超时" in note["why"], note


def test_the_shot_is_taken_outside_both_locks(tmp_path, monkeypatch):
    """拍照**不许在锁里**（简报点名的那条）：那 0.2 秒里不该按着整个服务。

    用 `Event` 把 `capture` **停在半路**再问锁 —— 不靠抢时序。
    `job.lock` 被攥着的话 `GET /job/{id}` 就读不动；`_check.lock`（写锁）被攥着的话
    整个工作线程都在等它。
    """
    entered, release = threading.Event(), threading.Event()

    def slow(ws_url, dest, *, timeout=None):
        entered.set()
        assert release.wait(10), "测试没放手"
        pathlib.Path(dest).parent.mkdir(parents=True, exist_ok=True)
        pathlib.Path(dest).write_bytes(_png())
        return pathlib.Path(dest).name, ""

    client, job_id, _, _ = _to_the_first_gate(tmp_path, monkeypatch, capture=slow)
    assert entered.wait(10), "capture 没被叫到"
    svc = client.app.state.service
    job = svc._jobs[job_id]
    try:
        assert job.lock.acquire(timeout=1.0), \
            "拍照时 `job.lock` 还被攥着 —— 这段时间 /job 读不动"
        job.lock.release()
        assert svc._check.lock.acquire(timeout=1.0), "拍照还在写锁里（会按着整个工作线程）"
        svc._check.lock.release()
    finally:
        release.set()
    assert _wait(client, job_id)["status"] == "waiting"
    assert job.shot_notes[-1]["name"] == "pause-1.png", job.shot_notes


def test_a_capture_that_returns_junk_is_recorded_too(tmp_path, monkeypatch):
    """`capture` 回一个不像话的东西（既不是 `(名, 话)` 也不是 `None`）→ 人话，不抛。

    这一格是给「以后谁换了一个 capture 实现」准备的：故障的形态可以变，
    规矩只有一条 —— **旁路的任何毛病都只变成一句人话，闸照旧在**。
    """
    def junk(ws_url, dest, *, timeout=None):
        return "我觉得成了"

    client, job_id, _, _ = _to_the_first_gate(tmp_path, monkeypatch, capture=junk)
    view = _wait(client, job_id)
    assert view["status"] == "waiting", view
    note = client.app.state.service._jobs[job_id].shot_notes[0]
    assert note["name"] is None and note["why"], note


def test_a_job_id_that_cannot_be_a_directory_is_recorded_not_raised(tmp_path, monkeypatch):
    """落点都算不出来（job_id 不像话）时也**不抛** —— 记一句人话（讲清是哪个 id）。

    生产上 job_id 是服务端生成的，这一格不可达；但「不抛」是这个文件的纪律，
    规矩不该有例外（与 `journal` 那条 M-3 同一个形状）。
    """
    client, job_id, _, _ = _to_the_first_gate(tmp_path, monkeypatch)
    svc = client.app.state.service
    job = service.Job(job_id="../etc", brief={"ws_url": WS})
    svc._capture_pause(job)                       # 不炸
    assert job.pauses == 1
    assert job.shot_notes[0]["name"] is None, job.shot_notes
    assert "../etc" in job.shot_notes[0]["why"], job.shot_notes


def test_a_run_that_died_mid_flight_still_leaves_a_shot(tmp_path, monkeypatch):
    """**跑挂了那一屏更该看得见**：`_advance` 的 except 那条路也拍（同一个出口、同一条纪律）。

    这一条是**判断**不是文档：简报只说「`_advance` 末尾调一次」——
    我按「末尾 = 两条路的那一头都算」办。若裁定只要成功那条路，删的是 `_advance` 里那一行。
    """
    png = _png()
    script, _ = _fake_cdp(tmp_path, png)
    monkeypatch.setenv("SITEFORGE_CDP_BIN", str(script))
    fg = FakeGraph(steps=[{}], raise_on=[1])
    client = _client(tmp_path, graph_factory=lambda b, d: fg)
    job_id = client.post("/run", json=_brief(tmp_path)).json()["job_id"]
    view = _wait(client, job_id)
    assert view["status"] == "failed", view
    job = client.app.state.service._jobs[job_id]
    # ⚠️ 这条路上**状态先落**（人该立刻看见它挂了），图是随后才到的 —— 所以这里**等一句 note**，
    #    不是读一次。那不是要修的竞态：状态是给眼睛的，图是旁路（设计注 §5.5 的代价那一栏）。
    deadline = time.time() + 10
    while not job.shot_notes and time.time() < deadline:
        time.sleep(0.02)
    assert job.pauses == 1, job.shot_notes
    assert job.shot_notes[0]["name"] == "pause-1.png", job.shot_notes
    assert (tmp_path / "shots" / job_id / "pause-1.png").read_bytes() == png


def test_a_job_recovered_from_the_checkpoint_can_be_shot_too(tmp_path, monkeypatch):
    """`_recover` 那条路的 job 也能拍（`pauses` 从 0 起、不炸）。

    服务重启之后 job 是**捡回来的**（没经过 `start()`）—— 闸拍要是只接在 `start()`
    那条路上，重启过的任务就再也不会留图了。
    """
    script, _ = _fake_cdp(tmp_path, _png())
    monkeypatch.setenv("SITEFORGE_CDP_BIN", str(script))
    probe = FakeGraph(steps=[_Snap(values={"url": URL, "goal": GOAL, "success_text": SUCCESS,
                                           "site": SITE, "ws_url": WS})])
    probe.invoke({}, {})                        # 桩图的状态是 invoke 立起来的（get_state 读它）
    svc = service.Service(graph_factory=lambda b, d: _one_gate(),
                          checkpointer=InMemorySaver().with_allowlist(graph.MSGPACK_ALLOWLIST),
                          shots_dir=str(tmp_path / "shots"))
    svc._probe_graph = lambda: probe            # 「checkpoint 里有这个 job」那一半

    job = svc._recover("job-recovered")
    assert job is not None and job.pauses == 0, job
    svc._capture_pause(job)                     # 从 0 起 —— 不炸
    assert job.pauses == 1
    assert [n["name"] for n in job.shot_notes] == ["pause-1.png"], job.shot_notes
    assert (tmp_path / "shots" / "job-recovered" / "pause-1.png").is_file()


# ────────────── 2.5 「不许碰真实世界」那条不变量（修复轮 1）──────────────
#: 复审点名的那个形状：**抓拍跑在工作线程上，它比用例活得久** —— 等到它真去起进程的时候，
#: 「fixture 设的那几个环境变量」早就还回去了。实测（副本里的真 cdp 换成记录脚本）：
#: 子进程里 `SITEFORGE_CDP_BIN` 是空的，回退链落回**仓库里那个真二进制**，
#: 去连的是桩载荷里那个地址（`192.168.1.197:55555`）。
#:
#: 挡住它的**不许是环境变量**（那东西在 teardown 时就没了），必须是
#: 「**`Service` 在构造时就把「图落哪、账落哪、用哪个 cdp」定死了**」这条不变量 ——
#: 而构造发生在用例里面（兜底生效时）。下面三条就是它的判据。


def test_a_shot_that_runs_after_the_fixtures_are_gone_cannot_touch_the_real_world(
        tmp_path, monkeypatch):
    """**「本该漏」那条的形状**：抓拍那一刻，**这一条用例的 fixture 已经收掉了**。

    做法是**当场把那一层撤掉**（三个环境变量删干净，与 teardown 同一效果），
    再看服务会起哪个二进制、往哪写。
    """
    script, log = _fake_cdp(tmp_path, _png())
    monkeypatch.setenv("SITEFORGE_CDP_BIN", str(script))
    client = _client(tmp_path, graph_factory=lambda b, d: _one_gate(),
                     shots_dir=str(tmp_path / "shots"))
    svc = client.app.state.service
    job = service.Job(job_id="job-late", brief={
        "url": URL, "goal": GOAL, "success_text": SUCCESS,
        # 一个**立刻连不上**的地址：万一真去起了真二进制，也是当场的失败（不占 20 秒超时）
        "ws_url": "ws://127.0.0.1:1/devtools/page/X"})

    for name in ("SITEFORGE_CDP_BIN", "SITEFORGE_SHOTS_DIR", "SITEFORGE_EXPLORE_DIR"):
        monkeypatch.delenv(name, raising=False)          # ← 「fixture 收掉了」

    svc._capture_pause(job)                              # 抓拍「发生在 fixture 之后」

    assert job.shot_notes[0]["name"] == "pause-1.png", job.shot_notes
    assert (tmp_path / "shots" / "job-late" / "pause-1.png").is_file()
    # 只有**桩脚本**写得出这张图（仓库里那个真 cdp 一次都没被碰）
    assert log.read_text(encoding="utf-8").strip() == "127.0.0.1 1", log.read_text()
    assert not (ROOT / "runtime" / "shots" / "job-late").exists()
    assert not (ROOT / "runtime" / "explore" / "job-late").exists()


def test_a_job_driven_through_the_service_leaves_its_books_in_tmp_not_in_the_repo(
        tmp_path, monkeypatch):
    """账（`<explore_root>/<job_id>/baseline.json`）**不许落进仓库**。

    修复轮 1 实测漏掉的正是它：一趟全量套件在仓库里建 **22 个** `runtime/explore/job-*`
    —— 全是我这份文件建的（`test_service.py` 有自己的 `_runtime_goes_to_tmp`），
    而它**没人看着**（静默）。驱动走真那条路：`POST /run` → 工作线程 → `_advance`
    → `_write_baseline`，因为漏就是这么漏的。
    """
    client, job_id, _, _ = _to_the_first_gate(tmp_path, monkeypatch)
    view = _wait(client, job_id)
    assert view["status"] == "waiting", view
    assert (tmp_path / "runtime" / "explore" / job_id / "baseline.json").is_file()
    assert not (ROOT / "runtime" / "explore" / job_id).exists(), "账落进仓库了"
    assert not (ROOT / "runtime" / "shots" / job_id).exists(), "图落进仓库了"


def test_the_services_explore_wiring_hands_over_the_job_shots_dir(tmp_path, monkeypatch):
    """**生产那一根线真的接上了**：服务拼的那个探路闭包会把 **job 级**的 shots 目录
    交给 `browser_agent.explore`（Task 2 整片步拍的生产入口）。

    为什么不能靠图那条路验：`test_service.py` 那些「真图」用例把 `deps.explore` 换成了桩
    —— 走图**永远碰不到**这根线（修复轮 1 复审替我验的正是这条：接上它全量 605 绿、
    零回归，代价是零）。所以这里**直接调那个闭包**。
    """
    seen: dict = {}

    def fake_explore(url, goal, budget=None, should_pause=None, ws_url=None, on_step=None,
                     resume_from=None, resume_note="", window_alive=None,
                     shots_dir=None, shooter=None):
        seen.update(shots_dir=shots_dir, ws_url=ws_url)
        return browser_agent.Journey()

    monkeypatch.setattr(browser_agent, "explore", fake_explore)
    svc = service.Service(shots_dir=str(tmp_path / "shots"),
                          checkpointer=InMemorySaver().with_allowlist(graph.MSGPACK_ALLOWLIST))
    run = svc._explore_for({"ws_url": WS}, "job-wired")
    assert run is not None
    run(URL, GOAL)

    assert seen["ws_url"] == WS, seen
    assert seen["shots_dir"] == str(tmp_path / "shots" / "job-wired"), seen
    assert (tmp_path / "shots" / "job-wired").is_dir(), \
        "目录得按需建出来（这是**写**的那一侧）"
    assert not (ROOT / "runtime" / "shots" / "job-wired").exists()


def test_an_explore_with_a_job_id_that_cannot_be_a_directory_still_runs(tmp_path, monkeypatch):
    """`job_id` 不像话时**步拍那条线不接，但探路照跑** —— 这里是图的路径上，不许抛。"""
    seen: dict = {}

    def fake_explore(url, goal, budget=None, should_pause=None, ws_url=None, on_step=None,
                     resume_from=None, resume_note="", window_alive=None,
                     shots_dir=None, shooter=None):
        seen["shots_dir"] = shots_dir
        return browser_agent.Journey()

    monkeypatch.setattr(browser_agent, "explore", fake_explore)
    svc = service.Service(shots_dir=str(tmp_path / "shots"),
                          checkpointer=InMemorySaver().with_allowlist(graph.MSGPACK_ALLOWLIST))
    run = svc._explore_for({"ws_url": WS}, "../etc")
    run(URL, GOAL)                       # 不抛
    assert seen["shots_dir"] is None, seen


# ───────────────────── 3. 图片端点：名字与路径 ─────────────────────


@pytest.mark.parametrize("name", ["..%2F..%2Fetc%2Fpasswd",   # 名字里带 `/`（编码着送出去）
                                  "a.png%2F..%2F..%2Fx.png",
                                  "x.txt"])                    # 名字**不像**一张图
def test_a_name_that_is_not_a_name_is_a_404_with_human_words(tmp_path, monkeypatch, name):
    """`?name=` 是外面来的：名字不像话 → **404 + 人话**，一个字节的文件内容都不给。"""
    client, job_id, _, _ = _to_the_first_gate(tmp_path, monkeypatch)
    _wait(client, job_id)
    r = client.get("/job/%s/shot/%s" % (job_id, name))
    assert r.status_code == 404, (r.status_code, r.text)
    detail = r.json()["detail"]
    assert "不能当图名" in detail, detail
    assert "root:" not in r.text, "把 /etc/passwd 的内容漏出来了"


def test_dot_dots_with_literal_slashes_never_reach_the_server(tmp_path, monkeypatch):
    """简报里那个 `a.png/../../x.png`：**客户端**（浏览器 / curl / httpx）先把它规范化掉，
    服务端根本收不到这种形状 —— 所以这条断的是「404、且一个字节都没漏」，
    不硬要求我们那句人话（那句人话是给**真送到了**服务端的名字准备的）。

    ⚠️ 别把这条读成「服务端挡住了它」：它压根没到。服务端挡的是上面那三条。
    """
    client, job_id, _, _ = _to_the_first_gate(tmp_path, monkeypatch)
    _wait(client, job_id)
    r = client.get("/job/%s/shot/a.png/../../x.png" % job_id)
    assert r.status_code == 404, (r.status_code, r.text)
    assert "root:" not in r.text


def test_a_job_id_that_is_not_a_directory_name_is_refused_too(tmp_path, monkeypatch):
    """`job_id` 也是外面来的：`..` / `.` / 带空格的 id → 404 **人话**（报错，不是静默改名）。

    ⚠️ 用 `%2e%2e` 这种**编码**写法送出去：字面的 `..` 会被客户端自己规范化掉
    （见下一条），那测的就不是服务端了。
    """
    client, _, _, _ = _to_the_first_gate(tmp_path, monkeypatch)
    for bad in ("%2e%2e", "%2e", "a%20b"):
        r = client.get("/job/%s/shot/pause-1.png" % bad)
        assert r.status_code == 404, (bad, r.status_code, r.text)
        assert "不能当" in r.json()["detail"], r.text


def test_a_job_id_with_a_slash_never_reaches_the_server(tmp_path, monkeypatch):
    """id 里带 `/` 的请求**到不了**这个路由（框架先答了 404）—— 也是一条 404，一个字节不泄。

    别把这条读成「服务端挡住了它」（那是上一条在管的事）：它压根没到服务端。
    """
    client, _, _, _ = _to_the_first_gate(tmp_path, monkeypatch)
    for bad in ("a%2Fb", "%2e%2e%2fetc"):
        r = client.get("/job/%s/shot/pause-1.png" % bad)
        assert r.status_code == 404, (bad, r.status_code, r.text)
        assert "root:" not in r.text


def test_a_separator_flip_of_a_valid_name_deserves_no_directory(tmp_path, monkeypatch):
    """名字合法、任务不存在 → 404，而且**不许建目录**。

    `shots.path_for` 的 docstring：这是**读**的那一侧，用 `dir_for` 就等于
    「一个 GET 建一个目录」—— 谁拿一个 job_id 都能在盘上造目录。
    """
    client, _, _, _ = _to_the_first_gate(tmp_path, monkeypatch)
    r = client.get("/job/job-does-not-exist/shot/pause-1.png")
    assert r.status_code == 404, r.text
    assert "没有这张图" in r.json()["detail"], r.text
    assert not (tmp_path / "shots" / "job-does-not-exist").exists(), \
        "一个 GET 在盘上建了一个目录"


def test_a_name_that_is_fine_but_has_no_file_is_a_404_with_human_words(tmp_path, monkeypatch):
    """名字合法、文件不在（比如那一轮没拍成）→ 404，而且**把人话带上**。"""
    client, job_id, _, _ = _to_the_first_gate(tmp_path, monkeypatch)
    _wait(client, job_id)
    r = client.get("/job/%s/shot/pause-9.png" % job_id)      # 第 9 轮？没有这一轮
    assert r.status_code == 404, r.text
    assert "没有这张图" in r.json()["detail"], r.json()["detail"]


def test_a_404_for_a_round_that_could_not_be_shot_says_why(tmp_path, monkeypatch):
    """那一轮**拍过、没拍成** → 404 要带上 `shot_notes` 里那句人话（不许只剩「没有这张图」）。"""
    def boom(ws_url, dest, *, timeout=None):
        raise RuntimeError("窗口连不上了（桩）")

    client, job_id, _, _ = _to_the_first_gate(tmp_path, monkeypatch, capture=boom)
    _wait(client, job_id)
    r = client.get("/job/%s/shot/pause-1.png" % job_id)
    assert r.status_code == 404, r.text
    assert "窗口连不上了（桩）" in r.json()["detail"], r.json()["detail"]


def test_a_symlink_out_of_the_shots_dir_is_not_served(tmp_path, monkeypatch):
    """一个**指向别处**的 `pause-1.png` 也不给读。

    `pause-1.png` 这个名字本身是合法的（一段、以 `.png` 结尾）—— 挡住它的是
    「解析完再确认一次它真的在那个目录里」（设计注：解析后必须落在 `<shots_root>/<job_id>/` 之内）。
    """
    client, job_id, _, _ = _to_the_first_gate(tmp_path, monkeypatch)
    _wait(client, job_id)
    outside = tmp_path / "outside.txt"
    outside.write_text("root:x:0:0:root:/root:/bin/bash\n", encoding="utf-8")
    (tmp_path / "shots" / job_id / "pause-1.png").unlink()      # 换成一个链接
    (tmp_path / "shots" / job_id / "pause-1.png").symlink_to(outside)

    r = client.get("/job/%s/shot/pause-1.png" % job_id)
    assert r.status_code == 404, r.text
    assert "root:" not in r.text, "顺着链接把外面的文件读出来了"


# ───────────────────── 4. 两块接线信息（不编话） ─────────────────────


class _Win:
    """一个**带身份**的窗口层桩（`BitWindow` 那三样：worker_ip / bit_id / port）。"""

    def __init__(self, worker="192.168.1.222", bit_id="8f2c1a90", port=54345):
        self.worker_ip, self.bit_id, self.port = worker, bit_id, port

    def set_viewport(self, width, height):
        pass

    def alive(self):
        return True


def test_window_public_says_where_it_is_and_how_to_look(tmp_path):
    """接了窗口层 → 三样事实都在，`how` / `when` 两句人话是**写死的**（设计注 §十）。"""
    info = service.Service(window=_Win(), shots_dir=str(tmp_path / "shots")).window_public()
    assert set(info) == {"worker", "bit_id", "api_port", "how", "when"}, info
    assert info["worker"] == "192.168.1.222", info
    assert info["bit_id"] == "8f2c1a90", info
    assert info["api_port"] == 54345, info
    assert "那台机器" in info["how"], info["how"]
    assert "不要动手" in info["when"], info["when"]
    # **不编 URL**（设计注 §十：「Bit 有能远程用的控制台 URL」是**猜测**、本次没核实）
    assert "://" not in info["how"] and "://" not in info["when"], info


def test_no_window_layer_means_no_block_at_all(tmp_path):
    """没接窗口层 → `None`（页面据此**明说**「看不到活窗口」，而不是显示一块空表）。"""
    svc = service.Service(window=None, shots_dir=str(tmp_path / "shots"))
    assert svc.window_public() is None


def test_shots_note_is_empty_while_step_shots_are_on(tmp_path):
    """每步抓拍开着 → 一句话都不说（`/live` 的 `shots_note` 是空串）。"""
    assert service.Service(shots_dir=str(tmp_path / "shots")).shots_note() == ""


def test_shots_note_says_so_when_step_shots_are_off(tmp_path, monkeypatch):
    """**降级 B**：关掉每步抓拍时，页面上得**一直**有一句话解释（不许静默降级）。"""
    monkeypatch.setenv("SITEFORGE_STEP_SHOTS", "0")
    note = service.Service(shots_dir=str(tmp_path / "shots")).shots_note()
    assert note and "这一次没留逐步的图" in note, note
    assert "闸" in note, "还得说清「闸口那张还在」（不然人以为一张都没有了）：%r" % note


def test_shots_note_quotes_the_price_only_when_there_is_one(tmp_path, monkeypatch):
    """实测数从 `SITEFORGE_SHOT_SECONDS` 读；**没给就不吹具体数字**。"""
    monkeypatch.setenv("SITEFORGE_STEP_SHOTS", "0")
    svc = service.Service(shots_dir=str(tmp_path / "shots"))
    monkeypatch.setenv("SITEFORGE_SHOT_SECONDS", "1.8")
    assert "1.8" in svc.shots_note(), svc.shots_note()
    monkeypatch.delenv("SITEFORGE_SHOT_SECONDS")
    note = svc.shots_note()
    assert note, "关了抓拍却一个字都不说"
    assert not re.search(r"\d+(\.\d+)?\s*秒", note), "没给实测数却吹了具体数字：%r" % note


def test_the_switch_judgement_is_exactly_zero_after_stripping(tmp_path, monkeypatch):
    """判据只有一条：`strip()` 之后**恰好**是 `"0"` 才算关。

    读法搬进 `agent/shots.py` 之后，这一格是**新的**：原来那两处各自的代码里都带着
    `.strip()`，谁重构时顺手去掉，就会出现「设了 `" 0 "` 却照拍，而页面上那句话**还在**说
    「这一次没留逐步的图」」—— 两边当场打架（这正是「两处解析」那类漂移的另一种长相）。
    """
    for value, on in (("0", False), (" 0 ", False), ("0\n", False),
                      ("", True), ("1", True), ("00", True), ("0.0", True), ("no", True)):
        monkeypatch.setenv(shots.STEP_SHOTS_ENV, value)
        assert shots.step_shots_on() is on, (value, shots.step_shots_on())
        note = service.Service(shots_dir=str(tmp_path / "shots")).shots_note()
        assert (note == "") is on, (value, note)


def test_the_degrade_switch_has_one_reader_for_both_halves(tmp_path, monkeypatch):
    """**公共契约的判据**（裁定那一句）：这个开关一翻，「图没了」与「有人解释为什么」
    **同时发生或同时不发生**。

    两半住在两个模块里：图那一半是 `explore` 的抓拍（`agent/browser_agent.py`），
    解释那一半是 `Service.shots_note()`（`agent/service.py`）。这条**同时**驱动两边 ——
    谁要是自己另解析一次（一处 strip 一处不 strip、一处认 `"0"` 一处认「非空就关」），
    这两个断言当场分家。
    """
    for i, value in enumerate(["0", " 0 ", "1", "", "yes", "00"]):
        monkeypatch.setenv("SITEFORGE_STEP_SHOTS", value)
        note = service.Service(shots_dir=str(tmp_path / "shots")).shots_note()
        shooter = _Shooter(tmp_path / ("step-shots-%d" % i))
        _run(tmp_path,
             {"observe": [{"structured": PAGE_LANDING}]},
             [{"calls": [("observe", {})]},
              {"calls": [("click", {"selector": "#get-started"})]},
              {"content": "看完了"}],
             shots_dir=shooter.root, shooter=shooter,
             budget=browser_agent.Budget(max_steps=10, max_rounds=10))
        on = shots.step_shots_on()
        assert (note == "") is on, \
            "开关=%r：`shots_note()`=%r，而唯一的读法说 on=%r" % (value, note, on)
        assert bool(shooter.dests) is on, \
            "开关=%r：抓拍次数=%d，而唯一的读法说 on=%r" % (value, len(shooter.dests), on)
