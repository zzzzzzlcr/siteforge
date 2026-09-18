"""Task 4 Step 1：时间线的**形状与上限**（`agent/events.py`）—— 纯单测，不开浏览器、不起服务。

这份测试钉的不是「时间线能用」，是**它是什么形状**。三层，一层比一层不显然：

1. **brief 要求的那几条**：`n` 单调递增（同一秒两条也不乱）、`who` 只认三个值、
   空 `say` 直接抛、超上限丢最旧的且 `dropped()` 数得出来、`all(limit)` 给**最后** limit 条。
2. **`docs/执行事实契约-2026-09-18.md` 那七格**（`step_no / action / receipt / sig_before /
   sig_after / expect / verdict`）：逐格能带进 `data`；`None`（「看不见」）是**一等值**；
   `expect = 未声明` 是**一等值**。这一版**不填**它们，但形状不许挡住它们 ——
   「先拼时间线、schema 随手定」会得到「给旧的 `step` 换个名字」，三层就白分了（契约 §三）。
3. **契约的两条硬规矩**：①字段名里不许出现判断词（`success` / `changed` / `done` …——
   今天那个 `step="success"` 就是这么来的）；②**谁填哪一格**：脚本（`who="agent"`）
   只填前五格，第七格（`verdict`，判断）与第六格（`expect`，运营写的）不许它碰。

第 2/3 层的判据来自契约、不是 brief —— Task 4 就是那份契约落地的地方（契约 §三）。
"""

from __future__ import annotations

import inspect
import json
import pathlib
import sys
import threading

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import events  # noqa: E402


# ═══════════════ 第一层：brief 钉的那几条（形状与人话纪律）═══════════════


def test_the_interface_is_the_one_the_brief_pins():
    """三个名字、一个上限、三个方法 —— 跨任务接口（计划 §「跨任务接口」1）就靠它们。"""
    assert events.WHO == ("agent", "system", "you")
    assert events.MAX_EVENTS == 2000
    tl = events.Timeline()
    assert tl.dropped() == 0
    assert tl.all() == []


def test_add_keeps_the_signature_the_brief_pins():
    """`add(kind, say, *, who="system", data=None)` —— **事实走 `data`**（不是一个一个 kwargs）。

    为什么这条也要钉：事实怎么进来是个**接口**（`Service.narrate(**data)` 把七格收成
    一个字典递进来）。让它顺带吃 `**facts` 看起来方便，但那会让「谁填哪一格」这件事
    散进调用点的参数表里 —— 而它正是这一片要盯住的东西。
    """
    signature = inspect.signature(events.Timeline.add)
    assert list(signature.parameters) == ["self", "kind", "say", "who", "data"]
    assert signature.parameters["who"].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["data"].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["data"].default is None
    assert events.Timeline().add("note", "一句话", data={"receipt": {"raw": "ok"}})["data"] == {
        "receipt": {"raw": "ok"}}


def test_n_is_monotonic_and_the_timestamp_is_second_resolution():
    """`n` **单调递增**，排序用它、不用时间戳（同一秒里可能来两三条）。

    时间戳是**本地 ISO 秒级**的，所以同一秒的两条 `at` 可能**一模一样** ——
    这正是「排序用 `n`」的理由；把 `at` 当排序键的话，同一秒里的先后就随机了。
    """
    tl = events.Timeline()
    first = tl.add("note", "第一句")
    second = tl.add("note", "第二句")
    assert (first["n"], second["n"]) == (1, 2), "n 从 1 起、一次一条"
    assert first["at"] == second["at"] or first["n"] < second["n"]
    assert "." not in first["at"], "秒级时间戳里不该有小数（%r）" % first["at"]
    assert [e["n"] for e in tl.all()] == [1, 2], "读出来的顺序 = 追加顺序"


def test_the_shape_is_the_five_keys_plus_data():
    """事件形状就是 `{n, at, kind, who, say, data}` —— 多一个键少一个键都是漂了。"""
    event = events.Timeline().add("note", "一句话")
    assert set(event) == {"n", "at", "kind", "who", "say", "data"}
    assert event["who"] == "system", "默认那一方是系统（服务自己说的话）"
    assert event["data"] == {}, "没给 data 是个**空字典**（不是 None —— 读的人不用判空）"


def test_who_only_knows_three_values():
    """`who` 只认三个值：页面靠它决定气泡长相。别的值**直接抛**（不许静默归成 system）。"""
    tl = events.Timeline()
    for who in events.WHO:
        assert tl.add("note", "一句话", who=who)["who"] == who
    with pytest.raises(ValueError) as caught:
        tl.add("note", "一句话", who="机器")
    assert "agent" in str(caught.value), "报错要说清有哪三个值：%s" % caught.value
    assert len(tl.all()) == 3, "抛了的那条不许留在时间线上"


def test_an_empty_say_is_refused():
    """**空 `say` 直接抛**：人话纪律 —— 写不出人话说明还没想清（设计注 §3.2 第 1 条）。

    空白串也算空（`"  "` 是最常见的「凑一个」）。
    """
    tl = events.Timeline()
    for empty in ("", "   ", "\n", "\t "):
        with pytest.raises(ValueError):
            tl.add("note", empty)
    assert tl.all() == []


def test_the_kind_has_to_be_a_nonempty_string():
    """`kind` 是事件的词汇表，不许空着、不许不是字符串（`None` 会被读成「没这个词」）。"""
    tl = events.Timeline()
    for bad in ("", "   ", None, 7):
        with pytest.raises(ValueError):
            tl.add(bad, "一句话")
    assert tl.all() == []


def test_a_say_that_is_not_a_string_is_refused_too():
    tl = events.Timeline()
    with pytest.raises(ValueError):
        tl.add("note", None)
    with pytest.raises(ValueError):
        tl.add("note", {"say": "这句话装在一个字典里"})
    assert tl.all() == []


def test_the_oldest_events_are_dropped_and_dropped_counts_them(monkeypatch):
    """超上限 → **丢最旧的**，而且 `dropped()` 数得出来（要能说出来，不许悄悄丢）。

    上限读的是 `events.MAX_EVENTS`（模块级那个数），所以这里把它改小 ——
    真上限（2000）由下面那条钉着，不必造 2001 条来验同一件事。
    """
    monkeypatch.setattr(events, "MAX_EVENTS", 5)
    tl = events.Timeline()
    for i in range(8):
        tl.add("note", "第 %d 句" % i)
    kept = [e["n"] for e in tl.all()]
    assert kept == [4, 5, 6, 7, 8], "留下的是**最后** 5 条（最旧的三条丢了）：%r" % kept
    assert tl.dropped() == 3, "丢了几条要数得出来"
    assert [e["say"] for e in tl.all()] == ["第 3 句", "第 4 句", "第 5 句", "第 6 句", "第 7 句"]
    assert tl.all()[0]["n"] == 4, "丢掉的号**不回卷**：n 认的是「第几条事件」，不是下标"


def test_the_real_cap_is_two_thousand():
    """真上限就是 2000（brief 钉的数）—— 顺便把它跑满一次，看住那条边界。"""
    assert events.MAX_EVENTS == 2000
    tl = events.Timeline()
    for i in range(events.MAX_EVENTS + 1):
        tl.add("note", "第 %d 句" % i)
    assert len(tl.all(limit=events.MAX_EVENTS + 10)) == events.MAX_EVENTS
    assert tl.dropped() == 1
    assert tl.all(limit=1)[0]["n"] == 2001


def test_all_limit_returns_the_last_ones_not_the_first():
    """`all(limit)` 给的是**最后** limit 条（页面要的是「刚刚发生了什么」）。"""
    tl = events.Timeline()
    for i in range(10):
        tl.add("note", "第 %d 句" % i)
    assert [e["n"] for e in tl.all(3)] == [8, 9, 10]
    assert [e["n"] for e in tl.all()] == list(range(1, 11))


def test_a_limit_that_is_not_a_positive_number_returns_nothing():
    """`limit<=0` = 「一条也不要」。**不许**把 `0` 读成「不限」（`[-0:]` 是整个列表 —— 老坑）。"""
    tl = events.Timeline()
    tl.add("note", "一句话")
    assert tl.all(0) == []
    assert tl.all(-1) == []


def test_a_reader_cannot_change_what_is_stored():
    """读出来的是**副本**：改它不许动到时间线里那份（否则一条读就能改历史）。"""
    tl = events.Timeline()
    tl.add("note", "原话", data={"receipt": {"raw": "ok"}})
    got = tl.all()
    got[0]["say"] = "改过的"
    got[0]["data"]["receipt"]["raw"] = "改过的"
    again = tl.all()
    assert again[0]["say"] == "原话"
    assert again[0]["data"]["receipt"]["raw"] == "ok"


def test_n_stays_monotonic_when_callbacks_come_from_many_threads():
    """**线程安全**：写的人不止一个（探路的回调、工作线程、服务的闸口都在写）。

    判据不是「没崩」，是**号不重、不跳、条数对得上** —— 拿锁取号那一步写错就会重号。
    """
    tl = events.Timeline()
    threads = 8
    each = 50
    barrier = threading.Barrier(threads)

    def writer(who: int) -> None:
        barrier.wait()
        for i in range(each):
            tl.add("note", "线程 %d 的第 %d 句" % (who, i))

    pool = [threading.Thread(target=writer, args=(k,)) for k in range(threads)]
    for t in pool:
        t.start()
    for t in pool:
        t.join(10)
    numbers = [e["n"] for e in tl.all(limit=threads * each)]
    assert len(numbers) == threads * each
    assert sorted(numbers) == list(range(1, threads * each + 1)), "号重了或者跳了"


# ═════════════ 第二层：契约那七格在形状里落得下（这一版不填）═════════════


def test_the_seven_cells_are_named_by_the_module():
    """七格的**名字**由模块定下来（契约 §二那张表的顺序）—— 后一个任务照这个名字填。"""
    assert events.CELLS == ("step_no", "action", "receipt", "sig_before", "sig_after",
                            "expect", "verdict")
    assert events.UNDECLARED == "未声明", "「这一步我说不好」是一等值，字面就是这四个字"


def test_every_cell_fits_in_the_event_shape():
    """七格逐格带得进 `data`（这一版不填，但**形状不许挡住**）。

    `verdict=None` 是「还没人判过」—— 与「这一格没人碰过」不是一回事，
    所以它得**带键存在**（`None` 是一等值，见下一条）。这条事件由**服务**那一方发
    （`who="system"`）：第七格不许脚本填（下一条测试钉的是这个）。
    """
    tl = events.Timeline()
    event = tl.add(
        "step", "第 2 步：点了「下一步」",
        who="system",
        data={"step_no": 2,
              "action": {"what": "click", "target": "下一步", "from": "model"},
              "receipt": {"raw": "ok", "from": "cdp"},
              "sig_before": {"url": "https://x.test/a", "text": "a1b2c3", "visible": 12},
              "sig_after": None,
              "why": "动作之后读不到页面（窗口没答）",
              "expect": events.UNDECLARED,
              "verdict": None},
    )
    data = event["data"]
    for cell in events.CELLS:
        assert cell in data, "这一格落不下：%s" % cell
    assert data["step_no"] == 2
    assert data["receipt"]["raw"] == "ok"
    assert data["sig_after"] is None
    assert data["expect"] == events.UNDECLARED
    assert data["verdict"] is None
    assert data["why"] == "动作之后读不到页面（窗口没答）", "「看不见」那句话跟着那一格一起留"


def test_none_is_a_first_class_value_and_is_not_dropped_or_rewritten():
    """`None` 是一等值：**不许被丢掉、不许被改写成别的值**。

    契约 §二②：`sig_after = null` + 一句 `why`，**不许**被折算成「没变化」——
    「折算」的第一个形态就是「顺手把空值过滤掉」。
    """
    tl = events.Timeline()
    event = tl.add("step", "第 3 步：填了一个输入框", who="system",
                   data={"sig_before": None, "why": "动作之前也没读到页面",
                         "receipt": None, "action": None})
    data = event["data"]
    assert "sig_before" in data and data["sig_before"] is None
    assert "receipt" in data and data["receipt"] is None
    assert "action" in data and data["action"] is None
    # 存下来那一份也不许被折算
    assert tl.all()[0]["data"] == data


def test_a_signature_that_could_not_be_seen_must_come_with_a_why():
    """`sig_after = None`（**看不见**）必须带一句 `why` —— 否则抛。

    为什么这条要机械挡（契约 §二②）：`None` 与「没变化」在纸上长得一样，
    读的人分不出来。留一句人话是**唯一**能把两者分开的东西 ——
    `goldenagesouls.py` 的 `read()` 读不到返回 `None` 是今天唯一做对的那个。
    """
    tl = events.Timeline()
    with pytest.raises(ValueError) as caught:
        tl.add("step", "第 4 步：看了一眼页面", who="system", data={"sig_after": None})
    assert "why" in str(caught.value), "报错要说清该怎么办：%s" % caught.value
    with pytest.raises(ValueError):
        tl.add("step", "第 4 步：看了一眼页面", who="system",
               data={"sig_after": None, "why": "   "})
    ok = tl.add("step", "第 4 步：看了一眼页面", who="system",
                data={"sig_after": None, "why": "动作之后窗口没答，读不到页面"})
    assert ok["data"]["sig_after"] is None


def test_the_undeclared_expectation_is_a_value_and_not_a_blank():
    """「未声明」要**明写**（`UNDECLARED`），不许留空。

    留空的话，「运营说这一格他说不好」与「这一格没人碰过」在纸上**一模一样** ——
    而这正是整个契约的诚实阀门（契约 §四：明说不知道，而不是拿 Action Truth 冒充）。
    """
    tl = events.Timeline()
    with pytest.raises(ValueError) as caught:
        tl.add("step", "第 5 步：点了一个按钮", who="system", data={"expect": None})
    assert "未声明" in str(caught.value) or "UNDECLARED" in str(caught.value)
    assert tl.add("step", "第 5 步：点了一个按钮", who="system",
                  data={"expect": events.UNDECLARED})["data"]["expect"] == "未声明"


# ═════════════ 第三层：契约的两条硬规矩（字段名 / 谁填哪一格）═════════════


@pytest.mark.parametrize("key", ["success", "is_changed", "changed", "done", "ok", "passed",
                                 "success_text", "result_ok"])
def test_a_judgment_word_is_not_allowed_as_a_field_name(key):
    """契约 §二③：**字段名里不许出现判断词**（`success` / `changed` / `done` …）。

    今天那个 `step="success"` 就是这么来的：脚本自起的名字，后台照着当真话读。
    `success_text` 是**运营**声明的判据，它的落点是 `expect` 那一格，不是 `data` 里的一个键。
    """
    tl = events.Timeline()
    with pytest.raises(ValueError) as caught:
        tl.add("step", "第 6 步：点了一个按钮", who="system", data={key: True})
    said = str(caught.value)
    assert "expect" in said and "sig_after" in said, (
        "报错要告诉人该用哪一格：%s" % said)
    assert tl.all() == [], "抛了的那条不许留在时间线上"


@pytest.mark.parametrize("key", ["step_no", "action", "receipt", "sig_before", "sig_after",
                                 "expect", "verdict", "why", "end_reason", "n"])
def test_the_repo_own_fact_names_are_allowed(key):
    """反面：**事实**的名字一个都不挡（七格 + `why` + 快照里那些键）。"""
    tl = events.Timeline()
    event = tl.add("step", "第 7 步：走了一步", who="system", data={key: 1})
    assert event["data"][key] == 1


def test_the_script_may_not_fill_the_verdict_or_the_expectation():
    """契约 §二①：**脚本只填前五格** —— 第七格永远不是它的，第六格是运营写的。

    判据用 `who`：时间线上脚本的声音就是 `who="agent"`。它替运营声明期望 =
    「执行的那一方在当裁判」换个地方又长出来（契约 §四为什么不让模型提议正好是这个）。
    """
    tl = events.Timeline()
    with pytest.raises(ValueError) as caught:
        tl.add("step", "第 8 步：点了「下一步」", who="agent", data={"verdict": "变了"})
    assert "verdict" in str(caught.value)
    with pytest.raises(ValueError):
        tl.add("step", "第 8 步：点了「下一步」", who="agent",
               data={"expect": {"url_contains": "/wizard"}})
    # 前五格它随便填（那是它的活）
    ok = tl.add("step", "第 8 步：点了「下一步」", who="agent",
                data={"step_no": 8, "action": {"what": "click"}, "receipt": {"raw": "ok"}})
    assert ok["data"]["step_no"] == 8
    # 服务/运营那一侧填这两格是允许的（第七格「收到了上面那些的那一方」在服务之外，
    # 这里只保证形状不挡它）
    other = tl.add("step", "第 8 步（核对）", who="system", data={"verdict": "变了"})
    assert other["data"]["verdict"] == "变了"


def test_bytes_never_get_into_an_event():
    """**字节不进 JSON**（Global Constraints）—— 图走文件名，字节在盘上。

    形状这一层就要挡住：`data` 里放一张 png 的字节，`/live` 那个 JSON 就爆了
    （一次 12 轮、每轮两张图的运行，每次轮询 ~7MB —— 设计注 §8.3 第 2 条）。
    """
    tl = events.Timeline()
    for bad in (b"\x89PNG\r\n", bytearray(b"\x89PNG"), pathlib.Path("/tmp/pause-1.png")):
        with pytest.raises(ValueError) as caught:
            tl.add("step", "第 9 步：看了一眼页面", who="system", data={"shot": bad})
        assert "JSON" in str(caught.value), "报错要说清是 JSON 装不下：%s" % caught.value
    assert tl.all() == []


def test_data_has_to_be_a_mapping():
    """`data` 是一个字典（七格是**有名字的格子**）—— 给列表/字符串不是这个形状。"""
    tl = events.Timeline()
    for bad in ([1, 2], "receipt=ok", 7):
        with pytest.raises(ValueError):
            tl.add("step", "第 10 步：走了一步", data=bad)
    assert tl.all() == []


def test_a_nested_fact_that_json_cannot_hold_is_refused():
    """装不下的东西**嵌套**在里头也要挡住（`json.dumps` 是递归的判据，不是只看一层）。"""
    tl = events.Timeline()
    with pytest.raises(ValueError):
        tl.add("step", "第 11 步：走了一步", who="system",
               data={"sig_after": {"shot_bytes": b"\x89PNG"}})
    assert tl.all() == []


def test_a_stored_event_is_json_round_trippable():
    """存下来的每一条都得是**能进 `/live` 那个 JSON** 的东西 —— 拿真 json 走一遍。"""
    tl = events.Timeline()
    tl.add("step", "第 12 步：点了一个按钮", who="agent",
           data={"step_no": 12, "action": {"what": "click", "target": "下一步"},
                 "receipt": {"raw": "元素没找到", "from": "cdp"},
                 "sig_before": {"url": "https://x.test/a", "text": "a1b2c3", "visible": 12},
                 "sig_after": None, "why": "窗口没答"})
    raw = json.dumps({"events": tl.all()}, ensure_ascii=False)
    back = json.loads(raw)
    assert back["events"][0]["data"]["receipt"]["raw"] == "元素没找到"
    assert back["events"][0]["data"]["sig_after"] is None
