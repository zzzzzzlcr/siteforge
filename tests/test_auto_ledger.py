"""T5 里与后端形状无关的那半：**记账 / 总开关 / 心跳**（值班的护栏）。

判据在这儿：同一个站一天只开一次、一天最多 10 个、开关**读不懂就是关**、
心跳「没跳过」与「刚跳过」是两回事、**账本读不回来要抛**（不许当作空的接着跑 ——
那等于同一个站今天再开一遍 = 重复烧钱 + 重复真提交）。
"""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import auto  # noqa: E402


# ── 总开关：默认关，读不懂也当关 ──────────────────────────────────────────


def test_the_switch_is_off_when_nothing_was_ever_written(tmp_path):
    assert auto.read_flags(tmp_path) == {"on": False, "note": ""}


@pytest.mark.parametrize("junk", ["", "   ", "not json at all", "[]", '"on"', "null", "3"])
def test_a_switch_we_cannot_read_is_off_never_on(tmp_path, junk):
    """**读不懂 ⇒ 关**（无人值守护栏：宁可今天不干活，也不要没人看着干活）。"""
    flags = tmp_path / auto.STATE_DIR
    flags.mkdir(parents=True)
    (flags / "flags.json").write_text(junk, encoding="utf-8")
    assert auto.read_flags(tmp_path)["on"] is False, junk


def test_the_switch_reads_what_was_written(tmp_path):
    flags = tmp_path / auto.STATE_DIR
    flags.mkdir(parents=True)
    (flags / "flags.json").write_text(
        json.dumps({"on": True, "note": "用户按的"}), encoding="utf-8")
    got = auto.read_flags(tmp_path)
    assert got["on"] is True and got["note"] == "用户按的"


# ── 账本：同串一天一次 + 每天到顶 ─────────────────────────────────────────


def _ledger(tmp_path):
    return auto.Ledger(tmp_path / auto.STATE_DIR / "ledger.json")


def test_a_site_opens_once_a_day_and_the_book_says_why(tmp_path):
    book = _ledger(tmp_path)
    assert book.may_open("a.com/x", today="2026-09-24") == (True, "")
    book.note("a.com/x", today="2026-09-24", at="09:10")
    ok, why = book.may_open("a.com/x", today="2026-09-24")
    assert ok is False and "今天已经开过它" in why and "09:10" in why


def test_yesterday_does_not_block_today(tmp_path):
    book = _ledger(tmp_path)
    book.note("a.com/x", today="2026-09-23", at="09:10")
    assert book.may_open("a.com/x", today="2026-09-24") == (True, "")


def test_the_day_cap_stops_the_eleventh(tmp_path):
    book = _ledger(tmp_path)
    for i in range(auto.DAY_CAP):
        site = "s%d.com/x" % i
        assert book.may_open(site, today="2026-09-24")[0] is True
        book.note(site, today="2026-09-24", at="10:0%d" % (i % 10))
    assert book.count(today="2026-09-24") == auto.DAY_CAP
    ok, why = book.may_open("s99.com/x", today="2026-09-24")
    assert ok is False and "到顶" in why


def test_a_site_without_a_key_is_never_opened(tmp_path):
    """榜单那一格空了：不知道拿谁去记账 ⇒ **不猜**（不开、也不记）。"""
    book = _ledger(tmp_path)
    ok, why = book.may_open("  ", today="2026-09-24")
    assert ok is False and "没有键" in why
    assert book.count(today="2026-09-24") == 0


def test_a_ledger_we_cannot_read_raises_instead_of_starting_over(tmp_path):
    """**读不回来不许当作空的** —— 那等于同一个站今天再开一遍（重复烧钱 + 重复真提交）。"""
    path = tmp_path / auto.STATE_DIR / "ledger.json"
    path.parent.mkdir(parents=True)
    path.write_text("{ 这不是 JSON", encoding="utf-8")
    with pytest.raises(RuntimeError):
        auto.Ledger(path).may_open("a.com/x", today="2026-09-24")


def test_the_book_keeps_only_today(tmp_path):
    """写的时候顺手把昨天那页丢掉（账本只长一天）。"""
    book = _ledger(tmp_path)
    book.note("old.com/x", today="2026-09-23", at="09:00")
    book.note("new.com/x", today="2026-09-24", at="09:00")
    raw = json.loads(book.path.read_text(encoding="utf-8"))
    assert list(raw) == ["2026-09-24"], raw


# ── 心跳：没跳过 ≠ 刚跳过 ────────────────────────────────────────────────


def test_never_having_beat_is_not_the_same_as_just_having_beat(tmp_path):
    assert auto.heartbeat_age(tmp_path, now=1000.0) is None
    auto.heartbeat(tmp_path)
    age = auto.heartbeat_age(tmp_path)
    assert age is not None and age >= 0


def test_a_junk_heartbeat_is_no_heartbeat(tmp_path):
    path = tmp_path / auto.STATE_DIR / "heartbeat.txt"
    path.parent.mkdir(parents=True)
    path.write_text("昨天下午", encoding="utf-8")
    assert auto.heartbeat_age(tmp_path, now=1000.0) is None


def test_the_age_counts_from_the_stamp(tmp_path):
    path = tmp_path / auto.STATE_DIR / "heartbeat.txt"
    path.parent.mkdir(parents=True)
    path.write_text("900", encoding="utf-8")
    assert auto.heartbeat_age(tmp_path, now=1000.0) == 100.0
