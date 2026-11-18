"""约会站分类器接到服务/面板的那一层（`POST /classify` / `POST /classify/link`）。

这一份钉的是**面板按下去会发生什么**，不是分类规则本身（规则在 `test_classify.py`）：

1. **只读口结构上写不了**：`/classify` 没有 `apply` 参数，调用链也不给 client；
2. **关联是显式的一下**：`/classify/link` 缺 `operator` 时**连窗口都不开**；
3. **单飞不抢窗**：有 run 正占着窗口时识别回 409，`fresh_open` 一次都不叫；
4. **识别完一定收摊**：自己开的窗口关掉、窗口占位清掉（成功和失败两条路都要）；
5. **读不到 ≠ unknown**：FMR 量不到时回 502 + 原话，不把它画成「没识别出来」。
"""

from __future__ import annotations

import pathlib
import sys

from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import fmr, graph, service  # noqa: E402
from test_classify import JAPAN, ROWS, _census  # noqa: E402

URL = "https://soulmatemeets.com/land/sp/23d247da/"
KEY = "soulmatemeets.com/land/sp/23d247da"
WS_CLEAN = "ws://1.2.3.4:55555/devtools/page/CLEAN"


class _Win:
    """服务那个窗口层的替身：记下开/关，不碰真 Bit。"""

    def __init__(self):
        self.opened = 0
        self.closed = 0

    def fresh_open(self):
        self.opened += 1
        return WS_CLEAN

    def close(self):
        self.closed += 1


class _Rows:
    """FMR 那份站点清单 + 关联写口的替身（行为照真后端：加性 upsert + 回读）。"""

    def __init__(self, *, update_rows=True, unreachable=False):
        self.rows = [dict(r) for r in ROWS]
        self.update_rows = update_rows
        self.unreachable = unreachable
        self.linked = []

    def form_config_sites(self):
        if self.unreachable:
            raise fmr.FmrUnreachable("连不上后端")
        return list(self.rows)

    def form_config_link(self, site, config_id, *, operator, reason=""):
        self.linked.append((site, config_id, operator, reason))
        if self.update_rows:
            self.rows = [r for r in self.rows if r.get("site") != site] + [
                {"site": site, "config_id": config_id, "status": 1}]
        return {"site": site, "config_id": config_id, "created": True}


def _live(census=None, *, boom=None):
    calls = []

    def fn(url, *, ws_url, cdp_bin):
        calls.append({"url": url, "ws_url": ws_url, "cdp_bin": cdp_bin})
        if boom is not None:
            raise boom
        return {"census": census if census is not None else JAPAN,
                "clicked_start": False, "clicked_text": "", "note": ""}

    return fn, calls


def _client(tmp_path, *, window=None, fmr_client=None, live=None, **kw):
    kw.setdefault("checkpointer", InMemorySaver().with_allowlist(graph.MSGPACK_ALLOWLIST))
    return TestClient(service.create_app(
        window=window, failures_reader=fmr_client, classify_live=live,
        out_dir=str(tmp_path / "forms"), **kw))


def test_classify_reads_the_page_and_returns_the_platform_without_linking(tmp_path):
    win, rows = _Win(), _Rows()
    live, calls = _live(JAPAN)
    c = _client(tmp_path, window=win, fmr_client=rows, live=live)
    r = c.post("/classify", json={"url": URL})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["exit"] == 0, body
    assert body["result"]["platform"] == "japansdates"
    assert body["result"]["config_id"] == 101
    assert body["result"]["site_key"] == KEY
    assert "japansdates" in body["say"]
    assert rows.linked == [], "只读口不许写后端"
    assert calls[0]["url"] == URL and calls[0]["ws_url"] == WS_CLEAN
    assert win.opened == 1 and win.closed == 1, "自己开的窗口要关掉"
    assert c.app.state.service._window_in_use_by() is None, "占位要收掉"


def test_classify_unknown_is_a_result_not_a_write(tmp_path):
    rows = _Rows()
    live, _ = _live(_census())
    c = _client(tmp_path, window=_Win(), fmr_client=rows, live=live)
    body = c.post("/classify", json={"url": URL}).json()
    assert body["exit"] == 2, body
    assert body["result"]["platform"] is None
    assert rows.linked == []


def test_classify_link_requires_an_operator_before_opening_a_window(tmp_path):
    win, rows = _Win(), _Rows()
    live, calls = _live(JAPAN)
    c = _client(tmp_path, window=win, fmr_client=rows, live=live)
    r = c.post("/classify/link", json={"url": URL})
    assert r.status_code == 400, r.text
    assert "operator" in r.json()["detail"]
    assert win.opened == 0 and calls == [], "缺 operator 时连窗口都不该开"


def test_classify_link_writes_then_reads_back(tmp_path):
    win, rows = _Win(), _Rows()
    live, _ = _live(JAPAN)
    c = _client(tmp_path, window=win, fmr_client=rows, live=live)
    r = c.post("/classify/link", json={"url": URL, "operator": "alice (console)"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["exit"] == 0, body
    assert rows.linked and rows.linked[0][0] == KEY
    assert rows.linked[0][1] == 101
    assert rows.linked[0][2] == "alice (console)"
    assert body["result"]["linked"] == {"created": True, "config_id": 101}
    assert win.closed == 1
    assert c.app.state.service._window_in_use_by() is None


def test_classify_does_not_steal_the_window_a_run_is_using(tmp_path):
    win, rows = _Win(), _Rows()
    live, calls = _live(JAPAN)
    c = _client(tmp_path, window=win, fmr_client=rows, live=live)
    c.app.state.service._begin_window_use("job-other00001")
    r = c.post("/classify", json={"url": URL})
    assert r.status_code == 409, r.text
    assert "job-other00001" in r.json()["detail"]
    assert win.opened == 0 and calls == [], "别人正在用的窗口一个字节都不许动"


def test_a_run_started_while_classifying_is_refused_not_queued_behind_the_window(tmp_path):
    """识别整段占着那个窗口；这时按「开一趟」要**当场**收到 409（不是干等 60 秒）。"""
    import threading

    started, release = threading.Event(), threading.Event()
    win, rows = _Win(), _Rows()

    def live(url, *, ws_url, cdp_bin):
        started.set()
        release.wait(5)
        return {"census": JAPAN, "clicked_start": False, "clicked_text": "", "note": ""}

    c = _client(tmp_path, window=win, fmr_client=rows, live=live)
    out = {}

    def _classify():
        out["r"] = c.post("/classify", json={"url": URL})

    t = threading.Thread(target=_classify)
    t.start()
    assert started.wait(2), "识别没走到 live 那一步"
    try:
        r = c.post("/run", json={"url": "https://example.test/", "goal": "走到那一步",
                                 "mode": "build", "success_text": "Thank you"})
        assert r.status_code == 409, r.text
        assert "识别" in r.json()["detail"]
    finally:
        release.set()
        t.join(5)
    assert out["r"].status_code == 200, out["r"].text
    assert c.app.state.service._window_in_use_by() is None


def test_classify_says_when_it_had_to_replace_unwritable_bytes(tmp_path):
    """换掉坏字节是**有损**的 —— 响应那句必须说（这一片的全局约束：没有静默的路径）。"""
    live, _ = _live(JAPAN)
    c = _client(tmp_path, window=_Win(), fmr_client=_Rows(), live=live)
    import json as _json
    r = c.request("POST", "/classify",
                  content=_json.dumps({"url": URL + "\ud800"}).encode("ascii"),
                  headers={"content-type": "application/json"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert (service.UNWRITABLE_BYTES_SAY % 1) in body["say"], body["say"]


def test_classify_refuses_while_a_run_is_waiting_at_a_gate(tmp_path):
    """停在闸上的那一趟**还占着那个窗口**（它一接着走就要用）—— 识别不抢。"""
    win, rows = _Win(), _Rows()
    live, calls = _live(JAPAN)
    c = _client(tmp_path, window=win, fmr_client=rows, live=live)
    svc = c.app.state.service
    job = service.Job(job_id="job-waiting0001", brief={}, status=service.WAITING)
    with svc._jobs_lock:
        svc._jobs[job.job_id] = job
    r = c.post("/classify", json={"url": URL})
    assert r.status_code == 409, r.text
    assert "job-waiting0001" in r.json()["detail"]
    assert win.opened == 0 and calls == []


def test_classify_refuses_when_a_run_is_queued(tmp_path):
    """队列里压着活 ⇒ 那个窗口已经归它（开跑时开好了）—— 识别不换。"""
    win, rows = _Win(), _Rows()
    live, calls = _live(JAPAN)
    c = _client(tmp_path, window=win, fmr_client=rows, live=live)
    svc = c.app.state.service
    svc._queue.put(("job-queued0001", {}))
    r = c.post("/classify", json={"url": URL})
    assert r.status_code == 409, r.text
    assert "排队" in r.json()["detail"]
    assert win.opened == 0 and calls == []


def test_classify_says_when_the_window_cannot_be_opened_and_releases_the_slot(tmp_path):
    class _Bad:
        def fresh_open(self):
            raise RuntimeError("Bit 连不上")

        def close(self):
            pass

    live, calls = _live(JAPAN)
    c = _client(tmp_path, window=_Bad(), fmr_client=_Rows(), live=live)
    r = c.post("/classify", json={"url": URL})
    assert r.status_code == 502, r.text
    assert "Bit 连不上" in r.json()["detail"]
    assert calls == [], "窗口没开出来就不该去读页面"
    assert c.app.state.service._window_in_use_by() is None, "开窗失败也要把占位收掉"


def test_classify_without_a_window_layer_says_so(tmp_path):
    c = _client(tmp_path, window=None, fmr_client=_Rows(), live=_live(JAPAN)[0])
    r = c.post("/classify", json={"url": URL})
    assert r.status_code == 400, r.text
    assert "窗口层" in r.json()["detail"]


def test_classify_uses_a_given_ws_url_without_opening_or_closing(tmp_path):
    live, calls = _live(JAPAN)
    c = _client(tmp_path, window=None, fmr_client=_Rows(), live=live)
    r = c.post("/classify", json={"url": URL, "ws_url": "ws://given/page/A"})
    assert r.status_code == 200, r.text
    assert calls[0]["ws_url"] == "ws://given/page/A"


def test_classify_maps_a_live_failure_to_502_and_still_closes_the_window(tmp_path):
    win = _Win()
    live, _ = _live(boom=RuntimeError("导航没成：boom"))
    c = _client(tmp_path, window=win, fmr_client=_Rows(), live=live)
    r = c.post("/classify", json={"url": URL})
    assert r.status_code == 502, r.text
    assert "导航没成" in r.json()["detail"]
    assert win.closed == 1
    assert c.app.state.service._window_in_use_by() is None


def test_classify_says_when_the_site_list_cannot_be_read(tmp_path):
    win, rows = _Win(), _Rows(unreachable=True)
    live, _ = _live(JAPAN)
    c = _client(tmp_path, window=win, fmr_client=rows, live=live)
    r = c.post("/classify", json={"url": URL})
    assert r.status_code == 502, r.text
    assert "连不上后端" in r.json()["detail"]
    assert win.closed == 1


def test_classify_link_readback_mismatch_is_not_success(tmp_path):
    rows = _Rows(update_rows=False)
    live, _ = _live(JAPAN)
    c = _client(tmp_path, window=_Win(), fmr_client=rows, live=live)
    body = c.post("/classify/link",
                  json={"url": URL, "operator": "alice"}).json()
    assert body["exit"] == 4, body
    assert body["result"]["linked"] is None
    assert "回读" in (body["result"]["link_error"] or "")
