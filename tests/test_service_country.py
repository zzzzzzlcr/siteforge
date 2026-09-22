"""换代理国家（2026-09-22）：面板上那两下（看现状 / 换过去）+ 自测第 5 遍那根线。

用户原话：「面板上要能直接换」。这一层就是那句话的接线，两件事各一条：
  ① `GET /country` / `POST /country` —— 面板按的那两下；
  ② `Deps.set_country` / `Deps.country` —— 运营按过之后，自测第 5 遍（换链 + 再跑一遍产物）
     才接得上。**没按过就一个字节都不接**（那是这些用例里承重的一条）。

⚠️ 这一层**不算**「换成功没成功」的第二套判据：判据只有 `tools/set-country.py` 那一个
（它经 :1081 问到要的国家才回 0）—— 所以这里的桩只需给「成/不成」两态。
"""
from __future__ import annotations

import pathlib
import sys

from starlette.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import graph, service  # noqa: E402


class Runner:
    """替掉那条真路（真那条会去写 :1081 的链文件，用例里绝不许动真 gost）。"""

    def __init__(self, *, ok: bool = True, said: str = "US", raises: Exception = None):
        self.ok, self.said, self.raises = ok, said, raises
        self.calls: list = []

    def __call__(self, *, country: str = "", url: str = "") -> tuple:
        self.calls.append({"country": country, "url": url})
        if self.raises is not None:
            raise self.raises
        return self.ok, self.said


def _app(tmp_path, runner: Runner = None):
    app = TestClient(service.create_app(
        json_backup_dir=str(tmp_path / "backups"),
        country_runner=runner), raise_server_exceptions=False)
    return app, app.app.state.service


# ── ① 面板按的那两下 ────────────────────────────────────────────────────────


def test_pressing_the_button_switches_and_says_it_was_checked(tmp_path):
    """按「换成 US」：核到了 ⇒ 200 + 那句话说的是**核过**，且它记下了这个国家。"""
    runner = Runner(ok=True, said="[country] 核到了 ✓ 出口国家 = US")
    app, svc = _app(tmp_path, runner)
    got = app.post("/country", json={"country": "us", "url": "https://x.example/p"})
    assert got.status_code == 200, got.text
    body = got.json()
    assert body["ok"] is True and body["country"] == "US", body
    assert "核过" in body["say"], body["say"]
    assert runner.calls == [{"country": "US", "url": "https://x.example/p"}], runner.calls
    assert svc._country == "US"


def test_not_verified_is_not_reported_as_switched(tmp_path):
    """**没核到就不许说换好了**（502 + 原话），而且**不许**把它记成按过的那个国家。"""
    runner = Runner(ok=False, said="[country] ✗ 没核到：要的是 CA，读到的是 US")
    app, svc = _app(tmp_path, runner)
    got = app.post("/country", json={"country": "CA"})
    assert got.status_code == 502, got.text
    assert "没换成" in got.json()["detail"], got.json()
    assert svc._country == "", "没核到却记下了 ⇒ 自测第 5 遍会拿一个没成立的出口去跑"
    assert svc._country_cb() is None


def test_a_country_code_that_is_not_two_letters_is_refused_before_anything_runs(tmp_path):
    """国家码不成形状：400，而且**一个字节都没动**（没去起那个脚本）。"""
    runner = Runner()
    app, svc = _app(tmp_path, runner)
    for bad in ("USA", "u", "", "  "):
        got = app.post("/country", json={"country": bad})
        assert got.status_code == 400, (bad, got.text)
        assert "两个字母" in got.json()["detail"]
    assert runner.calls == []
    assert svc._country == ""


def test_a_running_job_blocks_the_switch_before_it_happens(tmp_path):
    """有活在跑：409 **拦在动作之前** —— 换链会把那一趟正用着的连接掐断。"""
    runner = Runner()
    app, svc = _app(tmp_path, runner)
    svc._jobs["job-busy"] = service.Job(job_id="job-busy", brief={},
                                        status=service.RUNNING, say="")
    got = app.post("/country", json={"country": "US"})
    assert got.status_code == 409, got.text
    assert "job-busy" in got.json()["detail"], got.json()
    assert runner.calls == [], "拦是拦了，可链还是换了 —— 那一趟照样被掐断"


def test_a_job_waiting_at_a_gate_does_not_block_it(tmp_path):
    """停在闸上的那一趟**不碰浏览器** —— 它不该拦这一下（判据不是「有没有 job」）。"""
    runner = Runner(ok=True, said="US")
    app, svc = _app(tmp_path, runner)
    svc._jobs["job-parked"] = service.Job(job_id="job-parked", brief={},
                                          status=service.WAITING, say="")
    got = app.post("/country", json={"country": "US"})
    assert got.status_code == 200, got.text


def test_reading_the_country_now_reads_it_instead_of_remembering_it(tmp_path):
    """`GET /country`：**实测**（问 :1081），问不出来是 503 + 「问不出来」，不是回一个空国家。"""
    app, svc = _app(tmp_path, Runner(ok=True, said="CA\n"))
    got = app.get("/country")
    assert got.status_code == 200 and got.json()["country"] == "CA", got.text
    assert got.json()["chosen"] == "", "还没按过，`chosen` 该是空的"

    app2, _ = _app(tmp_path, Runner(ok=False, said=""))
    bad = app2.get("/country")
    assert bad.status_code == 503, bad.text
    said = bad.json()["detail"]
    assert "问不出来" in said and "不是" in said, said


# ── ② 自测第 5 遍那根线 ─────────────────────────────────────────────────────


def test_the_fifth_round_line_is_dead_until_somebody_pressed_the_button(tmp_path):
    """**没按过 = 不接**（第 5 遍照旧跳过）—— 这是「不替人决定」那一半。"""
    app, svc = _app(tmp_path, Runner(ok=True, said="US"))
    assert svc._country == "" and svc._country_cb() is None
    deps = _deps(svc)
    assert deps.set_country is None and deps.country == ""
    assert graph._selftest_kwargs({}, deps) == {}


def test_after_the_button_the_fifth_round_really_runs(tmp_path):
    """按过之后：那根线接上，`_selftest_kwargs` 把**两样一起**交下去，回调真去换链。"""
    runner = Runner(ok=True, said="US")
    app, svc = _app(tmp_path, runner)
    assert app.post("/country", json={"country": "us"}).status_code == 200

    deps = _deps(svc)
    assert deps.country == "US" and deps.set_country is not None
    kw = graph._selftest_kwargs({}, deps)
    assert kw["country"] == "US" and callable(kw["set_country"])

    before = len(runner.calls)
    kw["set_country"]("US")                       # 自测那条阶梯就是这么调的（一个位置参数）
    assert len(runner.calls) == before + 1, "接上了却没真去换 —— 那一遍就成了走过场"


def test_the_callback_refuses_when_the_switch_was_not_verified(tmp_path):
    """换不成时回调**抛**：`selftest` 会把那一遍记成 `skipped` 并把理由带上（不是静默）。"""
    runner = Runner(ok=False, said="核不到")
    app, svc = _app(tmp_path, runner)
    svc._country = "US"                            # 先当它按过（接上那根线）
    cb = svc._country_cb()
    try:
        cb("CA")
    except RuntimeError as exc:
        assert "没换成" in str(exc), exc
    else:
        raise AssertionError("没核到却没抛 —— 那一遍会被记成「跑过了」")


def test_the_default_runner_builds_the_argv_the_tool_expects(tmp_path):
    """默认那根线拼的 argv：只读 = `--show`，换 = `--country X --url Y`（脚本那边认这形状）。"""
    record = tmp_path / "argv.txt"
    tool = tmp_path / "fake-tool.py"
    tool.write_text("import sys,pathlib\n"
                    "pathlib.Path(%r).write_text(' '.join(sys.argv[1:]))\n"
                    "print('US')\n" % str(record), encoding="utf-8")

    ok, said = service._run_country_tool(tool=str(tool), timeout=30.0)
    assert ok is True and said.strip() == "US", (ok, said)
    assert record.read_text() == "--show", record.read_text()

    service._run_country_tool(tool=str(tool), timeout=30.0, country="CA",
                              url="https://x.example/p")
    assert record.read_text() == "--country CA --url https://x.example/p", record.read_text()


def test_a_runner_that_blows_up_is_an_honest_no_not_a_crash(tmp_path):
    """外部世界什么都可能抛：抛了 = **没换成**（502），不是 500、更不是「换好了」。"""
    app, svc = _app(tmp_path, Runner(raises=RuntimeError(":1081 没在跑")))
    got = app.post("/country", json={"country": "US"})
    assert got.status_code == 502, got.text
    assert "没换成" in got.json()["detail"], got.json()


def _deps(svc) -> graph.Deps:
    """服务拼给图的那份 `Deps`（走 `_build_graph` 那条真路 —— 别手拼一份假的）。"""
    got: list = []
    svc._graph_factory = lambda brief, deps: got.append(deps)
    svc._build_graph({"set_viewport": False}, "job-x")
    assert got, "`_build_graph` 没把 Deps 交出去"
    return got[0]
