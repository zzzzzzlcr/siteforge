"""T5：把后端那张「连续失败」榜读成一行一个站。

判据全用**真东西**：一份是 2026-09-24 线上实测抓下来的响应（下面 `LIVE_20260924`，就是
`/tmp/rank_agent_live.sh` 打出来那三行的来头），一份是交接里写明的 v1/兼容形状。
钉住的四件事：
  ① 信封不认（`data`/`rank` 不是对象/数组）⇒ **抛** —— 那是「这一次没量着」，不是「今天没有失败」；
  ② ★ 缺 `streak` **不算不认**（v1 后端没有那一格）⇒ 不抛；
  ③ ★★ **绝不拿 0 顶替**：`streak=None` 就是「它没给」，0 是合法读数（两者的处置完全不同）；
  ④ ★ **判「有没有在失败」用 `streak`**：实测 `fail==0` 而 `streak==23` 是**常态**（会脱钩）。
"""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import auto  # noqa: E402

#: 2026-09-24 线上实测（`/tmp/rank_agent_live.sh`）：`fail` 与 `streak` 脱钩那一屏，原样抄下来。
LIVE_20260924 = {
    "status": 200, "msg": "success",
    "data": {
        "date": "2026-09-24", "failed_total": 24, "unattributed": 0,
        "min_streak": 5, "days": 7, "settle": 30, "settling": 0, "below_streak": 24,
        "rank": [
            {"site": "", "fail": 0, "streak": 23, "config_id": 74,
             "config_status": "停用", "has_script": True},
            {"site": "", "fail": 0, "streak": 17, "config_id": 43,
             "config_status": "停用", "has_script": True},
            {"site": "", "fail": 0, "streak": 10, "config_id": 29,
             "config_status": "停用", "has_script": True},
        ],
        "configs": [{"config_id": 74}, {"config_id": 43}, {"config_id": 29}],
    },
}


def test_the_live_shape_reads_row_by_row():
    rows = auto.parse_list(LIVE_20260924)
    assert [r["streak"] for r in rows] == [23, 17, 10]
    assert [r["config_id"] for r in rows] == [74, 43, 29]
    assert all(r["config_status"] == "停用" and r["has_script"] for r in rows)


def test_fail_must_not_be_used_to_judge_whether_it_is_failing():
    """★ 实测形状：`fail == 0` 但 `streak == 23` —— 拿 `fail` 当判据就会把这些站漏掉。"""
    rows = auto.parse_list(LIVE_20260924)
    assert rows[0]["fail"] == 0 and rows[0]["streak"] == 23
    #: 值班那边该用的那格是 streak（这一条是**钉子**：谁把判据挪到 fail 上，读这句就知道为什么不行）
    assert all((r["streak"] or 0) >= 5 for r in rows)


@pytest.mark.parametrize("body", [
    None, [], "{}", 0,
    {"status": 200, "msg": "success", "data": []},          # data 是数组（v1 的另一种错法）
    {"status": 200, "msg": "success", "data": "今天没有"},
    {"status": 200, "msg": "success", "data": {"rank": {}}},
    {"status": 200, "msg": "success", "data": {"rank": [None]}},
])
def test_an_envelope_we_do_not_recognize_raises_never_empty(body):
    """**「量不到」与「今天没有失败」是两件事** —— 信封不认就抛，绝不静默成空榜。"""
    with pytest.raises(ValueError):
        auto.parse_list(body)


def test_an_empty_rank_that_was_really_measured_is_an_empty_list():
    """真量了、真没有 ⇒ 空 list（这是唯一能变成空的那一种）。"""
    assert auto.parse_list({"status": 200, "msg": "success",
                            "data": {"date": "2026-09-24", "failed_total": 0, "rank": []}}) == []


def test_a_v1_row_without_streak_is_not_a_shape_we_refuse():
    """★ v1 后端没有 `streak`：**不抛**，而且**绝不拿 0 顶替**（0 与「没给」处置不同）。"""
    rows = auto.parse_list({"status": 200, "msg": "success", "data": {"rank": [
        {"site": "a.com/x", "fail": 11, "config_id": 101, "config_status": "停用",
         "has_script": False}]}})
    assert rows == [{"site": "a.com/x", "fail": 11, "streak": None, "config_id": 101,
                     "config_status": "停用", "has_script": False, "task_id": ""}], rows


@pytest.mark.parametrize("junk", [None, "", "它没给", 23.9, True, [], {}])
def test_a_streak_we_cannot_read_is_None_not_zero(junk):
    """读不出来 ⇒ `None`（**`23.9` 那种也不许截成 23** —— 截出来就成了「连续 23 次」的证据）。"""
    rows = auto.parse_list({"status": 200, "msg": "success",
                            "data": {"rank": [{"site": "a.com/x", "streak": junk}]}})
    assert rows[0]["streak"] is None, junk


#: 数字串是**明确的**（后端偶尔把数当字符串来）⇒ 认它，不许当成「没给」。
def test_a_clean_digit_string_is_read_as_the_number():
    rows = auto.parse_list({"status": 200, "msg": "success",
                            "data": {"rank": [{"site": "a.com/x", "streak": "23"}]}})
    assert rows[0]["streak"] == 23

def test_a_row_without_a_site_is_still_kept():
    """`site` 空的行**照收**（量到了就摆）—— 拒开是值班那边的判（`Ledger.may_open` 说「没有键」）。"""
    rows = auto.parse_list({"status": 200, "msg": "success",
                            "data": {"rank": [{"site": "  ", "streak": 6}]}})
    assert len(rows) == 1 and rows[0]["site"] == ""
