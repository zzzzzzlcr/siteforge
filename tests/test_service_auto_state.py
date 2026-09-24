"""T6 服务侧：`GET /auto` —— 面板那一栏要的那几格（只读，页面只搬字）。

验的判据：什么都没写过 ⇒ **关** + 「从来没跳过」；开着且心跳新鲜 ⇒ 不报警；
心跳老了 ⇒ `stale`；账本数得出来 ⇒ 报数、**坏了就如实说**（不装作今天没开过）；
`needs_human` 只摆那些真的需要人的 job（连「为什么」一起）。
"""
import json
import pathlib
import sys
import time

from starlette.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import auto, service  # noqa: E402


class FakeJob:
    """假 job —— 只备 `_view` 那一栏会碰的几格。"""

    def __init__(self, job_id="j-1", site="a.com/x", auto_state="", auto_why=""):
        self.job_id = job_id
        self.brief = {"site": site}
        self.auto_state = auto_state
        self.auto_why = auto_why
        self.say = "停在闸上等人"


def _svc(tmp_path):
    app = TestClient(service.create_app(json_backup_dir=str(tmp_path / "backups")),
                     raise_server_exceptions=False)
    svc = app.app.state.service
    svc._auto_root = str(tmp_path)              # 值班那几格落在用例的 tmp 里（绝不碰真的）
    return app, svc


def _flags(root, **over):
    d = pathlib.Path(root) / auto.STATE_DIR
    d.mkdir(parents=True, exist_ok=True)
    body = {"on": False, "note": ""}
    body.update(over)
    (d / "flags.json").write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")


def test_nothing_written_means_off_and_never_beaten(tmp_path):
    """默认：关着 + **从来没跳过**心跳（`never_beat` 与 `stale` 都要是 True，但话不一样）。"""
    app, _ = _svc(tmp_path)
    got = app.get(service.AUTO_PATH).json()
    assert got["on"] is False and got["never_beat"] is True and got["stale"] is True
    assert got["opened_today"] == 0 and got["day_cap"] == 10 and got["heartbeat_minutes"] == 15
    assert got["needs_human"] == [] and got["ledger_say"] == ""


def test_on_with_a_fresh_heartbeat_is_not_an_alarm(tmp_path):
    app, _ = _svc(tmp_path)
    _flags(tmp_path, on=True, note="用户按的")
    auto.heartbeat(tmp_path)
    got = app.get(service.AUTO_PATH).json()
    assert got["on"] is True and got["note"] == "用户按的"
    assert got["never_beat"] is False and got["stale"] is False, got
    assert got["heartbeat_age"] is not None and got["heartbeat_age"] < 60


def test_a_stale_heartbeat_is_an_alarm(tmp_path):
    """超过 `HEARTBEAT_MINUTES` 没跳 ⇒ 报警（无人值守最坏的形状：它死了没人知道）。"""
    app, _ = _svc(tmp_path)
    d = pathlib.Path(tmp_path) / auto.STATE_DIR
    d.mkdir(parents=True, exist_ok=True)
    (d / "heartbeat.txt").write_text("%.0f" % (time.time() - 20 * 60), encoding="utf-8")
    got = app.get(service.AUTO_PATH).json()
    assert got["never_beat"] is False and got["stale"] is True
    assert got["heartbeat_age"] > auto.HEARTBEAT_MINUTES * 60


def test_today_s_book_count_shows_up(tmp_path):
    app, _ = _svc(tmp_path)
    today = time.strftime("%Y-%m-%d")
    auto.ledger_at(tmp_path).note("a.com/x", today=today, at="09:00")
    auto.ledger_at(tmp_path).note("b.com/y", today=today, at="09:01")
    got = app.get(service.AUTO_PATH).json()
    assert got["opened_today"] == 2 and got["ledger_say"] == ""


def test_a_broken_book_is_said_not_hidden(tmp_path):
    """账本坏了 ⇒ **如实说**（`opened_today` 给 None）—— 装作「今天没开过」是最坏的读法。"""
    app, _ = _svc(tmp_path)
    path = pathlib.Path(tmp_path) / auto.STATE_DIR / "ledger.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ 这不是 JSON", encoding="utf-8")
    got = app.get(service.AUTO_PATH).json()
    assert got["opened_today"] is None and "账本" in got["ledger_say"], got


def test_only_the_jobs_that_need_a_human_are_listed(tmp_path):
    app, svc = _svc(tmp_path)
    svc._jobs["j-1"] = FakeJob(auto_state="needs_human", auto_why="两遍都没过")
    svc._jobs["j-2"] = FakeJob(job_id="j-2", site="b.com/y")          # 正常的，不该出现在这儿
    got = app.get(service.AUTO_PATH).json()
    assert got["needs_human"] == [{"job_id": "j-1", "site": "a.com/x",
                                  "why": "两遍都没过", "say": "停在闸上等人"}], got["needs_human"]


# ── ★ 话在服务这一侧拼好（页面一个判断都不许有）─────────────────────────────


def test_the_words_are_composed_here_so_the_page_judges_nothing(tmp_path):
    """开关、心跳、今天几个 —— 全在 `say` 里（页面 `innerHTML` 一摆就完，不做判断）。"""
    app, _ = _svc(tmp_path)
    assert "**关着**" in app.get(service.AUTO_PATH).json()["say"]
    _flags(tmp_path, on=True, note="用户按的")
    auto.heartbeat(tmp_path)
    got = app.get(service.AUTO_PATH).json()
    assert "**开着**" in got["say"] and "用户按的" in got["say"]
    assert "心跳正常" in got["say"] and "今天自动开了 0 个（上限 10）" in got["say"], got["say"]


def test_never_beat_and_stale_are_two_different_sentences(tmp_path):
    """★ 两句**不同**的话：从没跳过心跳 ≠ 超时没跳（页面上不许自己去分这件事）。"""
    app, _ = _svc(tmp_path)
    assert "从来没跳过心跳" in app.get(service.AUTO_PATH).json()["say"]
    d = pathlib.Path(tmp_path) / auto.STATE_DIR
    d.mkdir(parents=True, exist_ok=True)
    (d / "heartbeat.txt").write_text("%.0f" % (time.time() - 20 * 60), encoding="utf-8")
    got = app.get(service.AUTO_PATH).json()
    assert "超过 15 分钟没有心跳" in got["say"] and "从来没跳过" not in got["say"], got["say"]


def test_a_needs_human_row_gets_a_whole_sentence(tmp_path):
    """「需要人」那几件也把**整句**交下来（页面只摆，不拼）。"""
    app, svc = _svc(tmp_path)
    svc._jobs["j-1"] = FakeJob(auto_state="needs_human", auto_why="两遍都没过")
    got = app.get(service.AUTO_PATH).json()
    assert got["needs"] == [{"job_id": "j-1", "site": "a.com/x",
                             "say": "**需要人**（两遍都没过）：停在闸上等人"}], got["needs"]
