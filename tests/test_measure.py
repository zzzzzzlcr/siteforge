"""Task 1：基线测量的账（`agent/measure.py`）—— **先量，再决定**。

## 这一页为什么存在

计划四的其余部分（重放前缀 / journal / 续跑）建在一个判断上：
**「一次探路装不进一个窗口」**。而支撑它的证据只有**一次观察**（7m42s）。
所以 Task 1 是测量，不是实现。

## 判据（设计注 §3.3，**2026-09-17 改过** —— 这一页照新版钉）

```
max(M2) > M1               → 照做 A（存在**一条**路径装不进一个窗口，一条就够）
抽 ≥3 次且 max(M2) < 0.5×M1 → A 降级（缩成「窗口死因 + 停因区分」，先做 B）
0.8 ~ 1.5 倍               → **undecided：这个数判不出来**，不许挑方向（保守：做 A）
```

⚠️ **为什么不是「`M2 ≤ M1` → 降级」**：这个漏斗是**分叉**的（§2.2）—— 选 Yes 会多出三道题、
选 No 就跳过，同一条描述 20 / 33 / 34 步都合法。**一次跑只是一次抽样**，
拿它去判这条边界，下一次跑就能推翻它。

## 这一页钉住的三条规矩（比数字本身重要）

1. **算不出来就给 `None` 并说明为什么，绝不填 0** —— 0 会被读成「量到了，是零」，
   而那正是「基线」与「猜」的分界。
2. **`alive()` 问不出来（`None`）记成 `unknown`，不许记成死** ——
   同 `agent/service.py` 那条三态规矩：不知道不等于死。
3. **一次样本 != 一个数**：`single_sample` 要标出来（拿单样本判边界是错的）；
   `M9`（这一趟走了哪条路）/ `M10`（N 次抽样的步数跨度）就是把「抽样」变成数的两个量。
"""

from __future__ import annotations

import datetime
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import measure  # noqa: E402

DESCRIPTIONS = ROOT / "fixtures" / "descriptions"


# ─────────────────────────── Step 0：描述原文进 git ───────────────────────────


def test_the_two_acceptance_descriptions_are_in_the_repo_not_only_in_tmp():
    """`/tmp` 不是持久位置（人 2026-09-17 点出来的）—— 这两份是**输入数据**，要进 git。

    拷的时候**一个字都不许改**：改了，基线与后面的验收就不可比。
    """
    hb = (DESCRIPTIONS / "homebuddy.txt").read_text(encoding="utf-8")
    bl = (DESCRIPTIONS / "blinkist.txt").read_text(encoding="utf-8")
    assert hb.startswith("页面URL: https://www.homebuddy.com/walk-in-showers/cr640")
    assert "成功条件: Thank you" in hb and "操作:" in hb
    assert bl.startswith("页面URL: https://www.blinkist.com/magazine/posts/the-5-hour-rule")
    assert "引导: 1.滚动到底部" in bl, "原话要逐字在（序号形式 `1.滚动到底部` 也照抄）"
    assert "https://www.homebuddy.com" not in bl and "blinkist.com" not in hb


# ─────────────────────────── 窗口时间线 ───────────────────────────


def test_a_changed_pid_is_a_new_window_not_the_same_one():
    """`/browser/pids/alive` 的 PID 换了 = **换了一个窗口**（E2：一次跑里至少换过 3 次）。

    这是「一个 run 重开了几次」唯一的硬证据 —— `alive()` 今天把 PID 扔了（G3），
    所以它只能答「有没有一个窗口」，答不了「还是不是刚才那个」。
    """
    t = "2026-09-17T10:00:00+08:00"
    first = measure.window_row({"alive": True, "pid": 4772}, at=t)
    assert first["alive"] == "alive"
    assert first["pid"] == 4772
    assert first["window"] == 1
    assert first["new_window"] is True, "第一行记的是「这是第 1 个窗口」"

    same = measure.window_row({"alive": True, "pid": 4772}, at=t, prev=first)
    assert same["window"] == 1
    assert same["new_window"] is False, "同一个 PID 就是同一个窗口"

    other = measure.window_row({"alive": True, "pid": 2788}, at=t, prev=same)
    assert other["window"] == 2, "PID 变了 = 第 2 个窗口"
    assert other["new_window"] is True


def test_a_window_that_came_back_from_dead_is_a_new_window():
    """PID 问不出来时，**「死了又活了」也能判出换了窗口** —— 死掉的窗口不会自己回来。

    这条不是锦上添花：PID 只活在活着的应答里（`data` 是空 dict 时什么都没有），
    所以「窗口死→重开」这一跳经常只有 `dead → alive` 这一个信号。
    """
    t = "2026-09-17T10:00:00+08:00"
    alive = measure.window_row({"alive": True, "pid": None}, at=t)
    dead = measure.window_row({"alive": False, "pid": None}, at=t, prev=alive)
    assert dead["alive"] == "dead"
    assert dead["window"] == 1, "窗口死了还是那一个（死不是「换了一个」）"
    assert dead["new_window"] is False

    back = measure.window_row({"alive": True, "pid": None}, at=t, prev=dead)
    assert back["window"] == 2
    assert back["new_window"] is True


def test_asking_alive_and_getting_nothing_is_unknown_never_dead():
    """`alive()` 给 `None`（问不出来）→ 记 `unknown`。

    **不许记成死**（`agent/service.py` 那条三态规矩的同一条）：把一个问不出来的窗口
    记成死的，会让「窗口死因」那张表整个失去意义 —— 那是把没量到当成量到了。
    位置也不许动：不知道是不是换了窗口，就记「还是刚才那个」而不是编一个新的。
    """
    t = "2026-09-17T10:00:00+08:00"
    alive = measure.window_row({"alive": True, "pid": 4772}, at=t)
    unknown = measure.window_row({"alive": None, "pid": None}, at=t, prev=alive)
    assert unknown["alive"] == "unknown"
    assert unknown["alive"] != "dead"
    assert unknown["pid"] is None, "问不出来就记不知道，不许沿用上一个 PID"
    assert unknown["window"] == alive["window"], "不知道换没换 → 窗口号不动"
    assert unknown["new_window"] is False


def test_window_row_keeps_the_human_note():
    """`note` 是给**人**记一句的那一栏（§3.1：站方弹窗 / 拦截只能人看得出来）。"""
    row = measure.window_row({"alive": True, "pid": 1}, at="t", note="死之前有一段空闲")
    assert row["note"] == "死之前有一段空闲"
    assert measure.window_row({"alive": True, "pid": 1}, at="t")["note"] == ""


def test_window_lifetimes_only_counts_windows_that_died():
    """寿命只算**死过的**窗口；还活着的那个不许拿「到这一刻为止」冒充寿命。

    ⚠️ 精度是**探针间隔**（死的那一刻没人看见，是下一次探活才发现）——
    所以这个数在报告里要连着间隔一起读，M1 的 `how` 会把它写出来。
    """
    rows = [measure.window_row({"alive": True, "pid": 1}, at="2026-09-17T10:00:00+08:00")]
    rows.append(measure.window_row({"alive": True, "pid": 1}, at="2026-09-17T10:05:00+08:00",
                                   prev=rows[-1]))
    rows.append(measure.window_row({"alive": True, "pid": 2}, at="2026-09-17T10:07:42+08:00",
                                   prev=rows[-1]))     # 换了个新窗口 → 上一个到此为止
    rows.append(measure.window_row({"alive": True, "pid": 2}, at="2026-09-17T10:09:00+08:00",
                                   prev=rows[-1]))
    assert measure.window_lifetimes(rows) == [462.0], "第 1 个窗口活了 7m42s；第 2 个还活着"

    dead = rows + [measure.window_row({"alive": False, "pid": None},
                                      at="2026-09-17T10:12:00+08:00", prev=rows[-1])]
    assert measure.window_lifetimes(dead) == [462.0, 258.0], "第 2 个也死了才算它"
    assert measure.window_lifetimes([]) == [], "没开过窗口 = 空账，不是异常"


# ─────────────────────────── 一次探路的账 ───────────────────────────


def test_record_attempt_appends_one_line_and_never_fakes_the_wall_clock(tmp_path):
    """一次尝试 = 一行（追加，不覆盖）。返回的那份**就是**落盘的那份。"""
    p = tmp_path / "explore" / "job-x" / "attempts.jsonl"
    row = measure.record_attempt(
        p, started_at="2026-09-17T10:00:00+08:00", ended_at="2026-09-17T10:02:06+08:00",
        rounds=20, steps=41, stop_reason="budget_rounds", notes=["AI 说：再看看"])
    assert row["seconds"] == 126.0
    assert row["rounds"] == 20 and row["steps"] == 41
    assert row["stop_reason"] == "budget_rounds"
    assert row["notes"] == ["AI 说：再看看"]
    assert p.exists(), "目录不存在要建（parents=True）"
    on_disk = json.loads(p.read_text(encoding="utf-8").strip().splitlines()[0])
    assert on_disk == row, "落盘的那一行必须与返回的那份逐字一样"

    measure.record_attempt(
        p, started_at="2026-09-17T10:05:00+08:00", ended_at="2026-09-17T10:05:30+08:00",
        rounds=3, steps=4, stop_reason="model_done", notes=[])
    assert len(p.read_text(encoding="utf-8").strip().splitlines()) == 2, "第二次是追加"


def test_record_attempt_says_none_instead_of_zero_when_it_cannot_tell(tmp_path):
    """时间戳读不出来 / 轮数没记到 → `None` + 一句为什么。**绝不是 0。**

    0 秒是一个**合法**的量（真的没花时间）；`None` 是「没量到」。混成同一个数，
    基线表上就再也分不出「快」和「没量到」。
    """
    p = tmp_path / "attempts.jsonl"
    row = measure.record_attempt(p, started_at="", ended_at="", rounds=None, steps=None,
                                 stop_reason="paused", notes=["被中断，这一趟没记到轮数"])
    assert row["seconds"] is None and row["seconds"] != 0
    assert row["seconds_why"], "算不出来必须说清为什么"
    assert row["rounds"] is None

    ok = measure.record_attempt(p, started_at="2026-09-17T10:00:00+08:00",
                                ended_at="2026-09-17T10:00:00+08:00", rounds=0, steps=0,
                                stop_reason="no_rounds", notes=[])
    assert ok["seconds"] == 0.0, "真的没花时间就是 0（这一条要与上面那条分得开）"
    assert ok["rounds"] == 0, "一轮都没跑起来是**量到了**的 0，不是「没量到」"


# ─────────────────────────── M9：这一趟走了哪条路 ───────────────────────────


def test_path_shape_records_the_steps_states_marks_and_choices():
    """M9 的载体：实际步数 / 页面状态序列 / 位置标记序列 / 点了哪些东西。

    「点了哪些东西」是**人 2026-09-17 要的**：这个漏斗分叉 —— 那个 Yes/No 选了什么，
    决定了后面 20 步还是 34 步。只报一个总步数，这个样本就**不可复现**。

    ⚠️ 标记是 Task 3 才有的约定；今天**一行都不带** → `marks is None` + 为什么，
    **不许给 `[]`**（空列表会被读成「量到了，一个标记都没报」）。
    """
    rows = [
        {"state": "start", "action": "goto", "target": {"url": "https://x.test"},
         "result": {"ok": True}, "note": "打开了 https://x.test"},
        {"state": "start", "action": "click", "target": {"text": "Yes"},
         "result": {"ok": True}, "note": "点了「Yes」"},
        {"state": "start", "action": "observe", "target": {}, "result": {"ok": True},
         "note": "看了一眼页面"},
        {"state": "q2", "action": "click", "target": {"text": "No", "selectors": ["#no"]},
         "result": {"ok": True}, "note": "点了「No」"},
    ]
    shape = measure.path_shape(rows)
    assert shape["steps"] == 4
    assert shape["states"] == ["start", "q2"], "状态序列（连续重复只记一次）"
    assert [c["note"] for c in shape["choices"]] == ["点了「Yes」", "点了「No」"]
    assert shape["choices"][0]["target"]["text"] == "Yes"
    assert shape["marks"] is None and shape["marks_why"], "没标记 = 没量到，不是「0 个」"
    assert shape["jumped_over"] is None and shape["jumped_over_why"]

    # 有计划的那天（Task 3）：标记序列与跳号
    marked = [dict(r) for r in rows]
    marked[0]["mark"] = 1
    marked[1]["mark"] = 2
    marked[3]["mark"] = 4
    m = measure.path_shape(marked)
    assert m["marks"] == [1, 2, 4]
    assert m["jumped_over"] == [3], "从 2 跳到 4 → 3 被跳过（§2.2 的分支）"


def test_baseline_can_read_the_attempts_it_wrote_itself(tmp_path):
    """从盘上读回来 —— 服务是在 job 结束时汇总的，那时账只在文件里。"""
    d = tmp_path / "explore" / "job-x"
    measure.record_attempt(d / "attempts.jsonl",
                           started_at="2026-09-17T10:00:00+08:00",
                           ended_at="2026-09-17T10:02:00+08:00",
                           rounds=7, steps=9, stop_reason="model_done", notes=[])
    attempts = measure.read_attempts(d / "attempts.jsonl")
    assert len(attempts) == 1 and attempts[0]["rounds"] == 7
    assert measure.read_attempts(d / "没有这个文件.jsonl") == [], "没跑过就是空账，不是异常"


# ─────────────────────────── 汇总：M1~M10 + 判据 ───────────────────────────


def _secs(start, end):
    return (datetime.datetime.fromisoformat(end) - datetime.datetime.fromisoformat(start)).total_seconds()


def _attempt(start, end, rounds, steps, stop_reason="budget_rounds", shape=None):
    return {"started_at": start, "ended_at": end, "seconds": _secs(start, end),
            "rounds": rounds, "steps": steps, "stop_reason": stop_reason,
            "notes": [], "seconds_why": "", "path_shape": shape}


def _shape(steps, states=None, marks=None):
    return {"steps": steps, "states": states or ["start"], "choices": [],
            "marks": marks, "jumped_over": None}


def test_baseline_computes_the_ones_it_can(tmp_path):
    """M1 寿命中位/最短、M2/M3 的**样本序列**、M4 重启次数、M5 死一次多花的轮数、
    M9 路径形状、M10 步数跨度 —— 有数据就得算出来。"""
    att = [_attempt("2026-09-17T10:00:00+08:00", "2026-09-17T10:02:00+08:00", 20, 41,
                    shape=_shape(41, ["start", "q2"])),
           _attempt("2026-09-17T10:02:10+08:00", "2026-09-17T10:03:40+08:00", 20, 33,
                    shape=_shape(33))]
    p = tmp_path / "baseline.json"
    b = measure.baseline(p, window_lifetimes=[462.0, 300.0], attempts=att,
                         end={"end_reason": "explore_unfinished", "delivered": False,
                              "windows_opened": 3})

    assert b["M1"]["value"] == 381.0, "M1 = 寿命中位（462 与 300 的中位）"
    assert b["M1"]["min"] == 300.0
    assert b["M1"]["n"] == 2
    assert b["M2"]["value"] == 105.0, "M2 = 墙钟样本序列的中位（120 与 90）"
    assert (b["M2"]["min"], b["M2"]["max"]) == (90.0, 120.0)
    assert b["M2"]["samples"] == [120.0, 90.0], "样本序列本身要留着（判据看 max，也要看分散）"
    assert b["M3"]["value"] == 20 and b["M3"]["max"] == 20, "M3 = 模型轮数"
    assert b["M4"]["value"] == 2, "M4 = 重启次数（开过 3 个窗口 = 重启 2 次）"
    assert b["M5"]["value"] == 20, "M5 = 死一次多花的轮数（第二次尝试整趟白探的那 20 轮）"
    assert b["M9"]["value"] == [a["path_shape"] for a in att], "M9 = 每趟一个路径形状"
    assert b["M10"]["value"] == {"min": 33, "max": 41, "span": 8}, "M10 = 步数跨度"
    assert b["end"]["end_reason"] == "explore_unfinished"
    assert json.loads(p.read_text(encoding="utf-8")) == b, "汇总要落盘"


def test_m2_and_m3_are_sample_sequences_not_one_number(tmp_path):
    """分支站（§2.2）的两次跑长度不同 —— 只报一个数，读的人会以为那是「一次探路的成本」。

    所以 min/中位/max 与**判据用的那个**（max）都必须在账上。
    """
    att = [_attempt("2026-09-17T10:00:00+08:00", "2026-09-17T10:01:00+08:00", 5, 10),
           _attempt("2026-09-17T10:02:00+08:00", "2026-09-17T10:03:00+08:00", 6, 12),
           _attempt("2026-09-17T10:04:00+08:00", "2026-09-17T10:06:00+08:00", 7, 20)]
    b = measure.baseline(tmp_path / "b.json", window_lifetimes=[600.0], attempts=att,
                         end={"windows_opened": 1})
    assert b["M2"]["n"] == 3 and len(b["M2"]["samples"]) == 3
    assert b["M2"]["value"] == b["M2"]["median"] == 60.0
    assert b["M2"]["max"] == 120.0, "判据看 max（最长的那条路），这个数必须单独摆出来"
    assert b["M10"]["value"] == {"min": 10, "max": 20, "span": 10}


def test_the_verdict_uses_max_m2_and_refuses_to_pick_a_side_on_the_boundary(tmp_path):
    """判据是 **`max(M2) > M1`**，不是「`M2 ≤ M1`」；**贴在 0.8~1.5 倍上必须说判不出来**。

    这一条是 2026-09-17 改过的（设计注 §3.3）：分支站一次抽样判不了这条边界 ——
    一次跑 20 步就下结论「装得进一个窗口」，下一次 34 步就推翻它。
    """
    att = [_attempt("2026-09-17T10:00:00+08:00", "2026-09-17T10:01:00+08:00", 5, 10),
           _attempt("2026-09-17T10:01:10+08:00", "2026-09-17T10:03:00+08:00", 6, 12),
           _attempt("2026-09-17T10:03:10+08:00", "2026-09-17T10:04:00+08:00", 7, 20)]
    # max(M2) = 110s。窗口只活 60s → 1.83 倍，**明显**装不进 → 照做 A
    b = measure.baseline(tmp_path / "b.json", window_lifetimes=[60.0], attempts=att,
                         end={"windows_opened": 1})
    assert b["M2"]["max"] == 110.0 and b["M1"]["value"] == 60.0
    assert b["verdict"]["branch"] == "A", b["verdict"]

    # 贴着边界（0.8~1.5 倍：1.0 与 1.38 两个点）→ **不许挑方向**
    for i, life in enumerate((110.0, 80.0)):
        b2 = measure.baseline(tmp_path / ("b2-%d.json" % i), window_lifetimes=[life],
                              attempts=att, end={"windows_opened": 1})
        assert b2["verdict"]["branch"] == "undecided", (life, b2["verdict"])
        assert "判不出来" in b2["verdict"]["why"]
        assert "抽" in b2["verdict"]["why"], "要说清「再抽一次能定什么」"

    # 抽够 3 次、max(M2) 远小于 M1（不到一半）→ 这才是降级
    b3 = measure.baseline(tmp_path / "b3.json", window_lifetimes=[900.0], attempts=att,
                          end={"windows_opened": 1})
    assert b3["verdict"]["branch"] == "A_downgrade", b3["verdict"]

    # 样本只有 1 次、数也远小于 M1 → **样本不够，不许降级**（判据要 ≥3 次）
    b4 = measure.baseline(tmp_path / "b4.json", window_lifetimes=[900.0], attempts=att[:1],
                          end={"windows_opened": 1})
    assert b4["verdict"]["branch"] == "undecided", b4["verdict"]
    assert b4["single_sample"] is True and b4["sample"]["runs"] == 1


def test_a_single_sample_is_labelled_as_one(tmp_path):
    """一次跑 = 一次抽样：`single_sample` 必须标出来（否则后面会有人拿它判 §3.3 那条边界）。"""
    att = [_attempt("2026-09-17T10:00:00+08:00", "2026-09-17T10:02:00+08:00", 20, 41,
                    shape=_shape(41))]
    b = measure.baseline(tmp_path / "b.json", window_lifetimes=[462.0], attempts=att,
                         end={"windows_opened": 1})
    assert b["sample"]["runs"] == 1 and b["sample"]["single_sample"] is True
    assert "分叉" in b["sample"]["why"], "要说清为什么一次抽样不算数"
    assert b["M10"]["value"] is None, "一次抽样**没有跨度**（不许拿它当「跨度是 0」）"
    assert b["M10"]["why"]


def test_baseline_says_none_with_a_reason_never_zero(tmp_path):
    """量不出来的一律 `None` + 为什么 —— 表里**一个 0 都不许**冒充「量到了」。

    这一条是这一页最贵的钉子：`0` 在基线上读起来是「量到了，而且它就是零」，
    于是「这次跑窗口一次都没死」会被读成「窗口寿命是 0 秒」那种反话。
    """
    p = tmp_path / "baseline.json"
    b = measure.baseline(p, window_lifetimes=[], attempts=[], end={})
    for key in ("M1", "M2", "M3", "M4", "M5", "M9", "M10"):
        assert b[key]["value"] is None, "%s 没有数据却给了 %r" % (key, b[key]["value"])
        assert b[key]["value"] != 0, "%s 拿 0 冒充「量到了」" % key
        assert b[key]["why"], "%s 算不出来就得说清为什么" % key

    # M6 死因 / M7 计划标记 / M8 站方反应：这一趟的输入里根本没有它们
    for key in ("M6", "M7", "M8"):
        assert b[key]["value"] is None
        assert b[key]["why"]
    assert b["verdict"]["branch"] == "unknown", "寿命没量到 → 判据也给不出结论，不许猜"
    assert b["verdict"]["why"]
    assert json.loads(p.read_text(encoding="utf-8")) == b


def test_baseline_refuses_to_count_rounds_it_did_not_measure(tmp_path):
    """有一次尝试是「被中断、轮数没记到」→ M3/M5 **不能**把那次算成 0，
    只能整条给 `None` 并说清是**哪一次**没记到（否则 M3 会**偏低**，而偏低看起来像好消息）。
    """
    att = [_attempt("2026-09-17T10:00:00+08:00", "2026-09-17T10:02:00+08:00", 20, 41),
           {"started_at": "", "ended_at": "", "seconds": None, "rounds": None, "steps": None,
            "stop_reason": "paused", "notes": ["被中断，这一趟没记到轮数"],
            "path_shape": None, "seconds_why": "时间戳读不出来"}]
    b = measure.baseline(tmp_path / "b.json", window_lifetimes=[462.0], attempts=att,
                         end={"end_reason": "paused", "delivered": False, "windows_opened": 1})
    assert b["M3"]["value"] is None and b["M3"]["why"]
    assert b["M3"]["value"] != 0
    assert b["M2"]["value"] is None, "有一次的墙钟没量到 → 样本序列不完整 → min/中位/max 都不给"
    assert "第 2" in b["M3"]["why"], "要说清是哪一次没记到"
