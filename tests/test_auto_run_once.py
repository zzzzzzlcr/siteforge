"""T5：`run_once` 那一圈（看榜 → 对账 → 开修，一次只开一个）。

桩掉两个外面的东西：**后端**（`fetch_rank` / `fetch_failures`）与**发请求那一下**（`post`）——
验的是判据：开关关着不动、空键/缺 streak 不猜、对账读不出 task_id+country 不开、
同一串一天只开一次、**先记再开**、开修**不带 ws_url**（窗口是服务那摊）。
"""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import auto  # noqa: E402

RANK_ONE = {"status": 200, "msg": "success", "data": {"date": "2026-09-24", "failed_total": 9,
            "rank": [{"site": "a.com/x", "fail": 0, "streak": 23, "config_id": 74,
                      "config_status": "启用", "has_script": True}]}}
FAILS_ONE = {"a.com/x": [{"task_id": "26034602", "site": "a.com/x", "country": "US",
                          "status": "failed"}]}


class FakeFmr:
    def __init__(self, *, rank=RANK_ONE, fails=FAILS_ONE, raises=None):
        self.rank, self.fails, self.raises = rank, fails, raises
        self.calls = []

    def fetch_rank(self, *, date, limit):
        self.calls.append(("rank", date, limit))
        if self.raises is not None:
            raise self.raises
        return self.rank

    def fetch_failures(self, site):
        self.calls.append(("fails", site))
        return self.fails.get(site, [])


class Poster:
    def __init__(self, code=202):
        self.code = code
        self.calls = []

    def __call__(self, url, payload):
        self.calls.append((url, payload))
        return self.code, "{}"


def _on(root):
    """把总开关打开（否则 run_once 只跳一下心跳）。"""
    d = pathlib.Path(root) / auto.STATE_DIR
    d.mkdir(parents=True, exist_ok=True)
    (d / "flags.json").write_text(json.dumps({"on": True, "note": "用例"}), encoding="utf-8")


def _run(tmp_path, fmr, poster, **kw):
    return auto.run_once(fmr=fmr, post=poster, root=tmp_path,
                         today="2026-09-24", now=1000.0, **kw)


def test_the_switch_off_means_it_touches_nothing_but_still_beats(tmp_path):
    """关着 ⇒ 榜都不看、一个请求都不发；但**心跳照跳**（「还活着」与「开不开」是两件事）。"""
    fmr, poster = FakeFmr(), Poster()
    got = _run(tmp_path, fmr, poster)
    assert got["did"] == "没动" and "关" in got["why"], got
    assert fmr.calls == [] and poster.calls == [], (fmr.calls, poster.calls)
    assert auto.heartbeat_age(tmp_path, now=1000.0) is not None, "关着就不跳心跳 —— 面板会以为它死了"


def test_it_opens_one_with_the_country_from_the_failure_record(tmp_path):
    """够格的一行 ⇒ 对账读出 task_id + country ⇒ `POST /run`（auto、fix、三格同串、带国家）。"""
    _on(tmp_path)
    fmr, poster = FakeFmr(), Poster()
    got = _run(tmp_path, fmr, poster)
    assert got["did"] == "开了" and "US" in got["why"], got
    assert fmr.calls[1] == ("fails", "a.com/x"), fmr.calls
    url, payload = poster.calls[0]
    assert url.endswith("/run") and payload["auto"] is True and payload["mode"] == "fix"
    assert payload["env"] == {"proxy_country": "US"}, payload
    assert payload["fix_site"] == payload["url"] == payload["fix_site_url"] == "a.com/x", payload
    assert "ws_url" not in payload, "开修不带 ws_url —— 窗口是服务那摊（设计文档 §3）"
    book = auto.Ledger(pathlib.Path(tmp_path) / auto.STATE_DIR / "ledger.json")
    assert book.count(today="2026-09-24") == 1


def test_a_row_without_a_key_is_not_opened_and_never_guessed(tmp_path):
    _on(tmp_path)
    fmr = FakeFmr(rank={"data": {"rank": [{"site": "", "streak": 9}]}})
    poster = Poster()
    got = _run(tmp_path, fmr, poster)
    assert got["did"] == "没动" and "站点键是空的" in got["why"], got
    assert poster.calls == []


def test_a_missing_streak_is_not_a_reason_to_guess(tmp_path):
    """v1 后端没有那一格 ⇒ 不拿 0 顶替、也不猜「大概够久了」。"""
    _on(tmp_path)
    fmr = FakeFmr(rank={"data": {"rank": [{"site": "a.com/x", "fail": 11}]}})
    poster = Poster()
    got = _run(tmp_path, fmr, poster)
    assert got["did"] == "没动" and "连败几次后端没说" in got["why"], got
    assert poster.calls == []


@pytest.mark.parametrize("fails", [{}, {"a.com/x": [{"task_id": "26034602"}]},
                                   {"a.com/x": [{"country": "US"}]}])
def test_an_account_that_does_not_add_up_does_not_open(tmp_path, fails):
    """对账读不出 `task_id` / `country` ⇒ **不开**（不知道切哪条链）。"""
    _on(tmp_path)
    fmr = FakeFmr(fails=fails)
    poster = Poster()
    got = _run(tmp_path, fmr, poster)
    assert got["did"] == "没动", got
    assert poster.calls == [], poster.calls


def test_the_same_station_is_opened_once_a_day(tmp_path):
    _on(tmp_path)
    poster = Poster()
    assert _run(tmp_path, FakeFmr(), poster)["did"] == "开了"
    got = _run(tmp_path, FakeFmr(), poster)
    assert got["did"] == "没动" and "今天已经开过它" in got["why"], got
    assert len(poster.calls) == 1, "同一个串一天开了两次"


def test_an_envelope_we_can_not_read_raises_instead_of_working_from_nothing(tmp_path):
    """「量不到」不许被读成「没有站要修」—— 抛出去，让上面报人。"""
    _on(tmp_path)
    fmr = FakeFmr(raises=RuntimeError("连不上"))
    with pytest.raises(RuntimeError):
        _run(tmp_path, fmr, Poster())
    #: 信封形状不对也一样抛（那是 parse_list 的判）
    with pytest.raises(ValueError):
        auto.run_once(fmr=FakeFmr(rank={"data": []}), post=Poster(), root=tmp_path,
                      today="2026-09-24", now=1000.0)


def test_a_refused_run_is_said_plainly_and_the_book_keeps_the_mark(tmp_path):
    """服务拒了 ⇒ 如实说「开了但被拒」；账本**照旧记着**（先记再开是有意的：宁可少开一次）。"""
    _on(tmp_path)
    poster = Poster(code=409)
    got = _run(tmp_path, FakeFmr(), poster)
    assert got["ok"] is False and got["did"] == "开了但被拒" and "409" in got["why"], got
    book = auto.Ledger(pathlib.Path(tmp_path) / auto.STATE_DIR / "ledger.json")
    assert book.count(today="2026-09-24") == 1
