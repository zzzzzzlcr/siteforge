"""T3 后半：**开修之前先切到那一单的国家** —— 核到了才开，核不到就不猜、不开。

直接打那个方法（`service._fix_country_gate`）：那条真路往下走会去读后端的底稿（要真后端），
而这条规矩**只关心「切」这一下** —— 所以桩掉的是那一下（`country_runner`），验的是：
切的方向对（哪国）、错的时候**不开**、**人按那条路一个字节都不动**。
"""
import pathlib
import sys

import pytest
from fastapi import HTTPException
from starlette.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import service  # noqa: E402


class Runner:
    """替掉那条真路（真那条会去写 :1081 的链文件，用例里绝不许动真 gost）。"""

    def __init__(self, *, ok: bool = True, said: str = "US"):
        self.ok, self.said = ok, said
        self.calls: list = []

    def __call__(self, *, country: str = "", url: str = "") -> tuple:
        self.calls.append({"country": country, "url": url})
        return self.ok, self.said


def _svc(tmp_path, runner: Runner):
    app = TestClient(service.create_app(json_backup_dir=str(tmp_path / "backups"),
                                        country_runner=runner),
                     raise_server_exceptions=False)
    return app.app.state.service


def _body(**kw):
    """一份 auto 修站的载荷（只给这条规矩要用的那几格）。"""
    kw.setdefault("mode", "fix")
    kw.setdefault("auto", True)
    kw.setdefault("url", "https://x.example/p")
    return service.RunRequest(**kw)


def test_auto_fix_switches_to_that_runs_country_before_anything_else(tmp_path):
    """核到了：真去换那一条链（国家是那一单的），换成了才记下来。"""
    runner = Runner(ok=True, said="US")
    svc = _svc(tmp_path, runner)
    svc._fix_country_gate(_body(env={"proxy_country": "US"}))
    assert runner.calls == [{"country": "US", "url": "https://x.example/p"}], runner.calls
    assert svc._country == "US"


def test_a_human_run_does_not_touch_the_chain_at_all(tmp_path):
    """**人按的那条路一个字节不变**：它带没带国家都不换（人要换有面板那个按钮）。"""
    runner = Runner()
    svc = _svc(tmp_path, runner)
    svc._fix_country_gate(_body(auto=False, env={"proxy_country": "US"}))
    svc._fix_country_gate(_body(auto=False))
    assert runner.calls == [], "人那条路上把链换了 —— 那是替人做主"
    assert svc._country == ""


@pytest.mark.parametrize("env", [{}, None, {"proxy_country": ""}, {"proxy_country": "usa"},
                                 {"proxy_country": "澳大利亚"}, {"proxy_country": "U1"}])
def test_auto_fix_without_a_usable_country_opens_nothing(tmp_path, env):
    """没给 / 不成形状 ⇒ 400 报人，**链不动、job 也不开**（不猜那条）。"""
    runner = Runner()
    svc = _svc(tmp_path, runner)
    with pytest.raises(HTTPException) as got:
        svc._fix_country_gate(_body(env=env))
    assert got.value.status_code == 400, got.value.detail
    assert runner.calls == [], "码都不成形状，链还是换了"


def test_not_verified_is_an_honest_no_and_nothing_is_opened(tmp_path):
    """**没核到 ≠ 换好了**：502 + 原话，而且**不许**把它记成按过的那个国家。"""
    runner = Runner(ok=False, said="链写下去了，可 :1081 问出来的还是 GB")
    svc = _svc(tmp_path, runner)
    with pytest.raises(HTTPException) as got:
        svc._fix_country_gate(_body(env={"proxy_country": "US"}))
    assert got.value.status_code == 502, got.value.detail
    assert "没换成" in got.value.detail, got.value.detail
    assert svc._country == "", "没核到却记成按过了 —— 自测第 5 遍那根线会照它说话"


def test_a_running_job_blocks_the_switch_so_nothing_is_cut(tmp_path):
    """有活在跑：409 **拦在换链之前**（换链要重启 gost，会把那一趟正用着的连接掐断）。"""
    runner = Runner()
    svc = _svc(tmp_path, runner)
    svc._busy_job = lambda: "j-1（在跑第一步）"
    with pytest.raises(HTTPException) as got:
        svc._fix_country_gate(_body(env={"proxy_country": "US"}))
    assert got.value.status_code == 409, got.value.detail
    assert runner.calls == [], "拦是拦了，可链还是换了 —— 那一趟照样被掐断"
