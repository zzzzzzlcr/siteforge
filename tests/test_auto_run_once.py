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


class _Refused(Exception):
    """一个「后端 404」的替身（`run_once` 只看 `.status` ⇒ 只备那一格）。"""

    status = 404


class FakeFmr:
    def __init__(self, *, rank=RANK_ONE, fails=FAILS_ONE, raises=None, refuse_once=False,
                 fail_raises=None):
        self.rank, self.fails, self.raises = rank, fails, raises
        #: **对账那一步**抛什么（`raises` 是「看榜就抛」—— 两者不是一回事，别混）。
        self.fail_raises = fail_raises
        #: 模拟「默认查询 404、带 `type=debug` 才读得到」那一支（后端默认只看启用那份）。
        self.refuse_once = refuse_once
        self.calls = []
        self.since_seen = []                 #: 每次对账的 `(site, since, debug)`

    def fetch_rank(self, *, date, limit):
        self.calls.append(("rank", date, limit))
        if self.raises is not None:
            raise self.raises
        return self.rank

    def fetch_failures(self, site, since=None, debug=False):
        self.calls.append(("fails", site))
        self.since_seen.append((site, since, debug))
        if self.fail_raises is not None:
            raise self.fail_raises
        if self.refuse_once and not debug:
            raise _Refused("config not found")
        return self.fails.get(site, [])

    def fetch_steps(self, task_id):
        """那一单报过的几步（真那个走 `/formStep`；这里给一份够 `evidence_text` 用的）。"""
        self.calls.append(("steps", task_id))
        return [{"step": "start", "url": "https://x.example/p"},
                {"step": "no_success", "url": "https://x.example/p?done=1"}]



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


def test_a_disabled_row_is_still_a_candidate(tmp_path):
    """★ 停用的行**照样是候选** —— 「**停用 ≠ 没在跑**」是**量过的**（`japansdates` 那天还失败 6 条，
    见 `agent/fmr.py::_disabled_py_source` 的注释）⇒ 我先前那条「停用 ⇒ 不跑 ⇒ 永远好不了 ⇒ 跳过」
    的前提是错的，**撤了**（用户两次质疑都对）。

    ⚠️ 但自动那条路**不拿停用那份当底稿**（`allow_disabled` 默认 False）—— 那是 2026-09-21 的裁定：
    停用行要不要照修，**运营说了算**（服务那边同名那一格管这事）。
    """
    _on(tmp_path)
    fmr = FakeFmr(rank={"data": {"rank": [
        {"site": "a.com/x", "streak": 23, "config_status": "停用"}]}})
    poster = Poster()
    got = _run(tmp_path, fmr, poster)
    assert got["did"] == "开了", got
    assert poster.calls[0][1]["allow_disabled"] is False, poster.calls[0][1]


def test_the_operator_can_ask_for_a_disabled_row_by_name(tmp_path):
    """点名要修（`allow_disabled`）⇒ 照修，而且把这一格**交给服务**（那边同名那一格管这事）。"""
    _on(tmp_path)
    fmr = FakeFmr(rank={"data": {"rank": [
        {"site": "a.com/x", "streak": 23, "config_status": "停用"}]}})
    poster = Poster()
    got = _run(tmp_path, fmr, poster, allow_disabled=True)
    assert got["did"] == "开了", got
    assert poster.calls[0][1]["allow_disabled"] is True, poster.calls[0][1]


def test_an_enabled_row_is_not_touched_by_that_rule(tmp_path):
    """正控：**启用**的行照旧照走（那条规矩只认「停用」两个字，别把好行也拦下）。"""
    _on(tmp_path)
    poster = Poster()
    got = _run(tmp_path, FakeFmr(), poster)
    assert got["did"] == "开了", got
    assert poster.calls[0][1].get("allow_disabled") is False, poster.calls[0][1]


def test_a_stale_row_does_not_block_the_whole_round(tmp_path):
    """★ 存量条目（读不出失败单）**跳过它、接着看下一行** —— 别让一条把整圈卡住。

    2026-09-24 用户点明：榜上那些读不出来的连败，是「失败记录这条线**还没上线**」之前攒下的
    ⇒ 后端根本没有可对账的单 ⇒ 那种行**永远**过不了对账 ✗ ⇒ 它要是排在最前面，
    后面够格的行就永远轮不到 ✗（这条就是为这个写的）。
    """
    _on(tmp_path)
    fmr = FakeFmr(
        rank={"data": {"rank": [
            {"site": "old.com/x", "streak": 23, "config_status": "启用"},   # 存量：读不到单
            {"site": "a.com/x", "streak": 9, "config_status": "启用"}]}},
        fails=FAILS_ONE)                                   # 只有 a.com/x 读得到
    poster = Poster()
    got = _run(tmp_path, fmr, poster)
    assert got["did"] == "开了" and got["site"] == "a.com/x", got
    assert poster.calls[0][1]["fix_site"] == "a.com/x", poster.calls[0][1]


def test_the_reconciliation_window_is_wider_than_today(tmp_path):
    """★ 窗口要够宽（2026-09-24 实测）：不给 `since` 时默认「今天 00:00」，而榜单按**跨天连败**筛
    ⇒ 拿今天去对账一条都读不到（实测两行最新失败是 9-23 / 9-20）⇒ 这里要明显**早于今天**。"""
    _on(tmp_path)
    fmr = FakeFmr()
    assert _run(tmp_path, fmr, Poster())["did"] == "开了"
    site, since, debug = fmr.since_seen[0]
    assert site == "a.com/x" and debug is False, fmr.since_seen
    #: 期望值用 `auto` 里那个 `time`（这份用例没 import 它 —— 只为算一个日期，不值当加一行 import）
    want = auto.time.strftime("%Y-%m-%d", auto.time.localtime(1000.0 - 7 * 86400))
    assert since == want, (since, want)          # 用例里的 now=1000.0 ⇒ 窗口就是往前 7 天


def test_a_404_gets_one_retry_with_type_debug(tmp_path):
    """★ 默认查询 404（停用的站在后端默认只看启用那份）⇒ **带 `type=debug` 再问一次**。"""
    _on(tmp_path)
    fmr = FakeFmr(refuse_once=True)
    poster = Poster()
    got = _run(tmp_path, fmr, poster)
    assert got["did"] == "开了", got
    assert [(c[1], c[2]) for c in fmr.since_seen][-1][1] is True, fmr.since_seen
    assert poster.calls, "第二跳读到了却没开"


def test_anything_that_is_not_a_404_is_not_retried(tmp_path):
    """别的码的原因**不是**「哪一份配置」⇒ 不许拿 `type=debug` 去撞（与 `form_script` 同一条）。"""
    _on(tmp_path)
    fmr = FakeFmr(fail_raises=RuntimeError("connection refused"))   #: 抛在**对账**那一步
    poster = Poster()
    got = _run(tmp_path, fmr, poster)
    assert got["did"] == "没动" and "对账读不到" in got["why"], got
    assert all(c[2] is False for c in fmr.since_seen), fmr.since_seen
    assert poster.calls == []


def test_the_payload_carries_what_this_run_is_about(tmp_path):
    """★ 验收那一趟跑出来的（2026-09-24）：**不带证据，服务在门口就拦** ——
    「还没说这次要做什么：`goal`（或者 fix 模式下的失败证据 `evidence`）」⇒ 一圈白跑。
    所以载荷里必须有那一格（证据从那一单自己身上取：行 + 它报过的几步）。"""
    _on(tmp_path)
    poster = Poster()
    fmr = FakeFmr()
    assert _run(tmp_path, fmr, poster)["did"] == "开了"
    payload = poster.calls[0][1]
    assert payload["evidence"], payload
    assert ("steps", "26034602") in fmr.calls, fmr.calls


def test_steps_that_cannot_be_read_do_not_open_anything(tmp_path):
    """逐步记录读不到 ⇒ **不开**（也**不记账**：记了就是把额度白花在一趟没开成的活上）。"""
    _on(tmp_path)

    class NoSteps(FakeFmr):
        def fetch_steps(self, task_id):
            raise RuntimeError("量不到")

    poster = Poster()
    got = _run(tmp_path, NoSteps(), poster)
    assert got["did"] == "没动" and "逐步记录" in got["why"], got
    assert poster.calls == []
    assert auto.ledger_at(tmp_path).count(today="2026-09-24") == 0
