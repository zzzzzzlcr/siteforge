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
    """三个名字、一个上限、一张词表、三个方法 —— 跨任务接口（计划 §「跨任务接口」1）就靠它们。"""
    assert events.WHO == ("agent", "system", "you")
    assert events.MAX_EVENTS == 2000
    assert isinstance(events.KINDS, tuple) and events.KINDS, "kind 的词表（第 4 条规矩）"
    tl = events.Timeline()
    assert tl.dropped() == 0
    assert tl.all() == []


def test_the_kind_vocabulary_covers_the_catalog_and_nothing_else():
    """`kind` 是一张**封闭**的词表（目录表那九行的产出 + 这一版补的两个）。

    为什么这条要紧（复审 2026-09-18）：`datewhirl` 的病就是「**脚本自起的名字，
    后台照着当真话读**」。一个自称「事件词汇表」的模块，要是谁递什么名字都收，
    它就只是一个字符串字段 —— 那扇门今天开着，只是没人走。
    """
    # 目录表九行的全部产出，一个都不能少（少一个就是「这一行没地方记」）
    for kind in ("window_died", "window_reopened", "queued", "submitted", "running",
                 "failed", "done", "cap_hit", "recovered", "human_said", "shot_missing"):
        assert kind in events.KINDS, "目录表里的 %r 不在词表里" % kind
    # 判断词一个都不在词表里（`done` 是**状态机**的词，不是判据 —— 见 `service.py:83`）
    for word in ("success", "ok", "passed", "changed"):
        assert word not in events.KINDS
    tl = events.Timeline()
    for bad in ("success", "ok", "passed", "changed", "step-ish", "窗口没了"):
        with pytest.raises(ValueError) as caught:
            tl.add(bad, "一句话")
        assert "KINDS" in str(caught.value), "报错要说清词表在哪：%s" % caught.value
    assert tl.all() == [], "抛了的那条不许留在时间线上"
    # 词表里的词照收
    assert tl.add("step", "第 1 步：看了一眼页面")["kind"] == "step"


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
    assert events.Timeline().add("step", "一句话", data={"receipt": {"raw": "ok"}})["data"] == {
        "receipt": {"raw": "ok"}}


def test_n_is_monotonic_and_the_timestamp_is_second_resolution():
    """`n` **单调递增**，排序用它、不用时间戳（同一秒里可能来两三条）。

    时间戳是**本地 ISO 秒级**的，所以同一秒的两条 `at` 可能**一模一样** ——
    这正是「排序用 `n`」的理由；把 `at` 当排序键的话，同一秒里的先后就随机了。
    """
    tl = events.Timeline()
    first = tl.add("step", "第一句")
    second = tl.add("step", "第二句")
    assert (first["n"], second["n"]) == (1, 2), "n 从 1 起、一次一条"
    assert first["at"] == second["at"] or first["n"] < second["n"]
    assert "." not in first["at"], "秒级时间戳里不该有小数（%r）" % first["at"]
    assert [e["n"] for e in tl.all()] == [1, 2], "读出来的顺序 = 追加顺序"


def test_the_shape_is_the_five_keys_plus_data():
    """事件形状就是 `{n, at, kind, who, say, data}` —— 多一个键少一个键都是漂了。"""
    event = events.Timeline().add("step", "一句话")
    assert set(event) == {"n", "at", "kind", "who", "say", "data"}
    assert event["who"] == "system", "默认那一方是系统（服务自己说的话）"
    assert event["data"] == {}, "没给 data 是个**空字典**（不是 None —— 读的人不用判空）"


def test_who_only_knows_three_values():
    """`who` 只认三个值：页面靠它决定气泡长相。别的值**直接抛**（不许静默归成 system）。

    ⚠️ 每一方说的 `kind` 不一样（`VOICE_KINDS`，见下一条用例）：这里各用各的嗓子。
    """
    tl = events.Timeline()
    for who, kind in (("agent", "step"), ("system", "running"), ("you", "human_said")):
        assert tl.add(kind, "一句话", who=who)["who"] == who
    with pytest.raises(ValueError) as caught:
        tl.add("step", "一句话", who="机器")
    assert "agent" in str(caught.value), "报错要说清有哪三个值：%s" % caught.value
    assert len(tl.all()) == 3, "抛了的那条不许留在时间线上"


def test_the_kind_vocabulary_is_split_by_voice():
    """**词表按嗓子分**（`VOICE_KINDS`）—— 与「一格内容豁免」是同一个病的两半（修复轮 2 的 M1/M4）。

    承重的不是「有哪几个词」，是**谁在说**：
    - 脚本（`who="agent"`）只说得出口**它自己干的事**（`step`）；
    - 人（`who="you"`）只说得出**人自己的话**（`human_said`）；
    - 状态机那些词（`done` / `failed` / `running` / `cap_hit` …）**只有服务那一方**能说。

    为什么这条要紧：`datewhirl` 那个病的形状是「**脚本自起的名字，后台照着当真话读**」
    （`step="success"`）。上一轮关上了「新词」那半扇门（`KINDS`），却留着一张能说 `done` 的嘴 ——
    那半扇门等于没关。
    """
    tl = events.Timeline()
    # 脚本这张嘴：说得出口它自己干的事，说不出状态机的话
    assert tl.add("step", "第 1 步：点了一个按钮", who="agent")["kind"] == "step"
    for kind in ("done", "failed", "running", "cap_hit", "submitted", "window_died",
                 "human_said"):
        with pytest.raises(ValueError) as caught:
            tl.add(kind, "一句话", who="agent")
        assert "VOICE_KINDS" in str(caught.value), caught.value
    # 人这张嘴：只说得出人自己的话
    assert tl.add("human_said", "不是那个按钮", who="you")["kind"] == "human_said"
    for kind in ("done", "failed", "step"):
        with pytest.raises(ValueError):
            tl.add(kind, "一句话", who="you")
    # 服务那一方：13 个词**全说得出**（它是唯一能说状态机的嗓子）
    for kind in events.KINDS:
        assert tl.add(kind, "一句话", who="system")["kind"] == kind


def test_an_empty_say_is_refused():
    """**空 `say` 直接抛**：人话纪律 —— 写不出人话说明还没想清（设计注 §3.2 第 1 条）。

    空白串也算空（`"  "` 是最常见的「凑一个」）。
    """
    tl = events.Timeline()
    for empty in ("", "   ", "\n", "\t "):
        with pytest.raises(ValueError):
            tl.add("step", empty)
    assert tl.all() == []


def test_the_kind_has_to_be_a_nonempty_string():
    """`kind` 不许空着、不许不是字符串（`None` 会被读成「没这个词」）。"""
    tl = events.Timeline()
    for bad in ("", "   ", None, 7):
        with pytest.raises(ValueError):
            tl.add(bad, "一句话")
    assert tl.all() == []


def test_a_say_that_is_not_a_string_is_refused_too():
    tl = events.Timeline()
    with pytest.raises(ValueError):
        tl.add("step", None)
    with pytest.raises(ValueError):
        tl.add("step", {"say": "这句话装在一个字典里"})
    assert tl.all() == []


def test_the_oldest_events_are_dropped_and_dropped_counts_them(monkeypatch):
    """超上限 → **丢最旧的**，而且 `dropped()` 数得出来（要能说出来，不许悄悄丢）。

    上限读的是 `events.MAX_EVENTS`（模块级那个数），所以这里把它改小 ——
    真上限（2000）由下面那条钉着，不必造 2001 条来验同一件事。
    """
    monkeypatch.setattr(events, "MAX_EVENTS", 5)
    tl = events.Timeline()
    for i in range(8):
        tl.add("step", "第 %d 句" % i)
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
        tl.add("step", "第 %d 句" % i)
    assert len(tl.all(limit=events.MAX_EVENTS + 10)) == events.MAX_EVENTS
    assert tl.dropped() == 1
    assert tl.all(limit=1)[0]["n"] == 2001


def test_all_limit_returns_the_last_ones_not_the_first():
    """`all(limit)` 给的是**最后** limit 条（页面要的是「刚刚发生了什么」）。"""
    tl = events.Timeline()
    for i in range(10):
        tl.add("step", "第 %d 句" % i)
    assert [e["n"] for e in tl.all(3)] == [8, 9, 10]
    assert [e["n"] for e in tl.all()] == list(range(1, 11))


def test_a_limit_that_is_not_a_positive_number_returns_nothing():
    """`limit<=0` = 「一条也不要」。**不许**把 `0` 读成「不限」（`[-0:]` 是整个列表 —— 老坑）。"""
    tl = events.Timeline()
    tl.add("step", "一句话")
    assert tl.all(0) == []
    assert tl.all(-1) == []


def test_a_reader_cannot_change_what_is_stored():
    """读出来的是**副本**：改它不许动到时间线里那份（否则一条读就能改历史）。"""
    tl = events.Timeline()
    tl.add("step", "原话", data={"receipt": {"raw": "ok"}})
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
            tl.add("step", "线程 %d 的第 %d 句" % (who, i))

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


def test_anything_that_could_not_be_seen_must_come_with_a_why():
    """**任何一格**写了 `None`（**看不见**）都必须配一句 `why` —— 否则抛（修复轮 2 的 M3）。

    为什么这条要机械挡（契约 §二②）：`None` 与「没变化」在纸上长得一样，
    读的人分不出来。留一句人话是**唯一**能把两者分开的东西 ——
    `goldenagesouls.py` 的 `read()` 读不到返回 `None` 是今天唯一做对的那个。

    ⚠️ 为什么从「签名那两格」扩到**所有格**（复审实测出来的不对称）：
    `nan` 那条路被闸挡住了（写的人当场挨一句报错），而**裸 `None` 那条路一直开着、
    连一句解释都不要** —— 两条路到了线上是**同一个形状**（`null`）。
    闸拿掉响的那条、留下静的那条，理由（「`null` 是『看不见』那个一等值」）在别的格同样成立。
    """
    tl = events.Timeline()
    for cell in ("sig_after", "sig_before", "visible", "progress", "receipt", "action"):
        with pytest.raises(ValueError) as caught:
            tl.add("step", "第 4 步：看了一眼页面", who="system", data={cell: None})
        assert "why" in str(caught.value), "报错要说清该怎么办：%s" % caught.value
    with pytest.raises(ValueError):
        tl.add("step", "第 4 步：看了一眼页面", who="system",
               data={"sig_after": None, "why": "   "})
    ok = tl.add("step", "第 4 步：看了一眼页面", who="system",
                data={"sig_after": None, "why": "动作之后窗口没答，读不到页面"})
    assert ok["data"]["sig_after"] is None


def test_the_why_can_name_which_cell_it_is_about():
    """`why` 也可以**按字段点名**（字典）—— 同一件事里有两格以上看不见时用得着。

    （复审 2026-09-18 第一轮 Q1 附带指出过：一句话的 `why` 挂在两格上，
    读的人分不出它是说哪一格的。一句话仍然合法 —— 那是绝大多数情况；
    但要有一种**说清是哪一格**的写法，而且写歪了就抛。）
    """
    tl = events.Timeline()
    event = tl.add("step", "第 4 步：看了一眼页面", who="system",
                   data={"sig_after": None, "visible": None,
                         "why": {"sig_after": "窗口没答", "visible": "接口没回这个数"}})
    assert event["data"]["sig_after"] is None and event["data"]["visible"] is None
    # 点名点漏了一格 → 抛，并且**指出漏的是哪一格**
    with pytest.raises(ValueError) as caught:
        tl.add("step", "第 5 步：看了一眼页面", who="system",
               data={"sig_after": None, "visible": None, "why": {"sig_after": "窗口没答"}})
    assert "visible" in str(caught.value), caught.value
    with pytest.raises(ValueError):
        tl.add("step", "第 5 步：看了一眼页面", who="system",
               data={"sig_after": None, "why": {"sig_after": "  "}})


def test_the_verdict_cell_may_be_none_without_an_explanation():
    """`verdict=None` **不要** why：那是那一格定义好的一等值（「**还没人判过**」），
    与「量不出来」不是一回事。

    （这是这条规矩**唯一**的豁免，写在 `NONE_WITHOUT_WHY` 里 —— 一条规矩的边界要能被看见，
    不然下一个人只能靠猜。）
    """
    tl = events.Timeline()
    event = tl.add("step", "第 6 步：走了一步", who="system", data={"verdict": None})
    assert event["data"]["verdict"] is None
    assert events.NONE_WITHOUT_WHY == ("verdict",)


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


def test_a_judgment_word_is_not_allowed_as_a_nested_field_name_either():
    """判断词**哪一层都不许当键**（复审 2026-09-18 实测出的洞）。

    只查最外面那一层 = 没查：`{"step_no": 3, "result": {"ok": true}}` 的顶层键全是干净的，
    判**藏在里面**。而 `result.ok` 正是这个仓库里**已经存在**的那个正身
    （`journey.steps[].result.ok` —— 脚本自己给自己下的判）。
    """
    tl = events.Timeline()
    for bad in ({"result": {"ok": True}},
                {"a": {"b": {"c": {"done": True}}}},
                {"list": [{"passed": True}]},
                {"targets": [{"success": True}]}):
        with pytest.raises(ValueError) as caught:
            tl.add("step", "第 7 步：走了一步", who="system", data=bad)
        assert "判断词" in str(caught.value), caught.value
    assert tl.all() == []
    # 反面：干干净净的收据形状照收
    ok = tl.add("step", "第 7 步：点了一个按钮", who="system",
                data={"receipt": {"verb": "click", "error": "element not found",
                                  "raw": "元素没找到"}})
    assert ok["data"]["receipt"]["error"] == "element not found"


def test_the_judgment_word_rule_follows_who_fills_the_cell_not_which_cell():
    """这条规矩的边界是「**谁填**」，不是「它在不在格子里」（修复轮 2 的 M1）。

    上一轮我把豁免给了「**格子的内容**」（只要进了某一格就整棵子树放行）—— **用宽了**：
    复审实测 `{"action": {"clicked_ok": true}}`、`{"sig_before": {"success": true}}`、
    `{"step_no": {"ok": 1}}` 全收，而**这三格的填写人就是脚本自己**：

    | 格 | 谁填 | 判断词 |
    |---|---|---|
    | `receipt` | cdp（脚本逐字**转抄**） | **必须有** —— 过滤就是伪造笔录 |
    | `verdict` / `expect` | 裁判 / 运营 | **必须有** —— 判断就该待在这两格里 |
    | `step_no` / `action` / `sig_before` / `sig_after` | **脚本** | **一个字都不许** |

    凭据是「装着**别人的**原话」：`receipt` 里那个 `ok` 是 CDP 说的，脚本只是抄；
    而脚本在自己那四格里写的 `ok` 是**脚本自起的名字** —— 正是规矩 ① 那一句
    「脚本自起的名字，后台照着当真话读」换到了一个格子里。
    """
    tl = events.Timeline()
    # ① 脚本自己填的四格：**一个字都不许**（含嵌套、含列表里）
    for bad in ({"action": {"clicked_ok": True}},
                {"action": {"result": {"ok": True}}},
                {"sig_before": {"url": "u", "success": True}},
                {"sig_after": {"changed": True}},
                {"step_no": {"ok": 1}}):
        with pytest.raises(ValueError) as caught:
            tl.add("step", "第 7 步：走了一步", who="system", data=bad)
        assert "判断词" in str(caught.value), caught.value
    # ② 别人填的那三格：**必须有**（转抄 / 判断）
    event = tl.add("step", "第 7 步：核对了一下", who="system",
                   data={"verdict": {"changed": True},
                         "expect": {"screen_changed": True},
                         "receipt": {"verb": "click", "ok": False,
                                     "error": "element not found"}})
    assert event["data"]["verdict"] == {"changed": True}
    assert event["data"]["expect"] == {"screen_changed": True}
    assert event["data"]["receipt"]["ok"] is False


def test_the_script_cannot_reach_the_three_exempt_cells_anyway():
    """反面里的反面：脚本**够不着**那三格的豁免 —— 它不许填 `verdict` / `expect`，
    `receipt` 是它**转抄**的东西（那三格里的判断词是别人的原话）。

    所以「按谁填分」这条边界，脚本这一侧没有任何缺口：它能填的四格全查。
    """
    tl = events.Timeline()
    for bad in ({"verdict": {"changed": True}}, {"expect": {"screen_changed": True}}):
        with pytest.raises(ValueError) as caught:
            tl.add("step", "第 8 步：走了一步", who="agent", data=bad)
        assert "脚本只填前五格" in str(caught.value) or "verdict" in str(caught.value), caught.value
    # 三格里的判断词**由别人填**时才合法 —— 脚本填不了它们（上面那两条就是证据）
    ok = tl.add("step", "第 8 步：走了一步", who="agent",
                data={"receipt": {"verb": "click", "ok": False}})
    assert ok["data"]["receipt"]["ok"] is False, "转抄位（cdp 的回执原文）脚本照抄就行"


def test_a_cell_name_is_only_allowed_at_the_top_level():
    """七格的名字**只在顶层**：`receipt` 里冒出一个 `verdict`，说明有人把「判」塞进收据里了。

    （收据是**转抄**：契约 §二说脚本只转抄、也留原文 —— 转抄来的东西里不该有判。）
    """
    tl = events.Timeline()
    for bad in ({"receipt": {"verdict": "success"}},
                {"sig_after": {"url": "u", "verdict": True}},
                {"action": {"targets": [{"expect": "x"}]}}):
        with pytest.raises(ValueError) as caught:
            tl.add("step", "第 7 步：走了一步", who="system", data=bad)
        assert "顶层" in str(caught.value), caught.value
    assert tl.all() == []
    # 七格自己当然在顶层；一格的内容里放什么由那一格说了算
    event = tl.add("step", "第 8 步：核对了一下", who="system",
                   data={"verdict": {"changed": True},
                         "expect": {"url_contains": "/wizard"},
                         "receipt": {"verb": "click", "raw": "元素没找到"}})
    assert event["data"]["verdict"] == {"changed": True}
    assert event["data"]["expect"] == {"url_contains": "/wizard"}


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


def test_the_gate_uses_the_same_scale_as_the_wire():
    """写侧那道闸的刻度 = **线上那一层**的刻度（修复轮 2 的 M2c：复审实测出来的第二格刻度）。

    闸原来只判到 `json.dumps` 的**字符串**为止，而线上最后还要 `.encode("utf-8")`：
    一个**孤立代理对**（`"\\ud800"`）过得了闸，到 `/live` 直接 **500**（整条时间线一条都读不出来）。
    量具的刻度比被量的东西低一档 —— 「我量过了」就是假的。

    判据（两个方向都要能红）：
    - **过不了线上的**：字节 / `nan` / `inf` / 孤立代理对 → 闸抛；
    - **过得了线上的**：中日韩、emoji、代理**对**（`"\\U0001F600"` 这种是合法的）→ 闸收，
      而且真的经得起 `.encode("utf-8")`。
    """
    tl = events.Timeline()
    for bad in (b"\x89PNG", float("nan"), float("inf"), "\ud800", "\udfff"):
        with pytest.raises(ValueError):
            tl.add("step", "第 10 步：走了一步", who="system", data={"x": bad})
    good = tl.add("step", "第 10 步：走了一步", who="system",
                  data={"what": "点了「下一步」", "emoji": "\U0001F600", "cjk": "中文"})
    assert json.dumps(good["data"], ensure_ascii=False, allow_nan=False).encode("utf-8")
    assert tl.all()[0]["data"]["emoji"] == "\U0001F600"


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


def test_a_number_that_cannot_survive_the_live_json_is_refused():
    """`nan` / `inf` **进不来** —— 因为它们在 `/live` 那一层会被**悄悄写成 `null`**。

    **因果写对**（修复轮 2 的 M2a：上一版把账记在 starlette 头上，实测是错的）：
    `/live` 的路由标注是 `-> dict`，FastAPI 会把它过一遍 **pydantic 的 JSON-mode 序列化**
    （`ser_json_inf_nan` 默认把 `nan`/`inf` 写成 **`null`**）；starlette 的 `JSONResponse.render`
    用的是 `allow_nan=False`，**那一档是抛**（500）。所以少了写侧这道闸，
    得到的是**一次静默改写**，不是一声响 —— 线上那一层的真身在
    `test_service_events.py::test_the_wire_turns_nan_into_null_and_a_lone_surrogate_into_a_500`。

    选的是「**让它根本进不来**」这条（不是「让它活着穿过去」）：`NaN`/`Infinity`
    **本身就不是合法 JSON**（`json.dumps(..., allow_nan=True)` 吐出来的那串东西
    浏览器 `JSON.parse` 读不了），放它过去就得同时改 `/live` 的渲染器，
    而那会把一个**读不了**的响应体送到页面上。所以闸放在写这一侧：
    要记「量不出来」，明写 `None` 并且**说清为什么**（规矩 2，任何一格都一样）。
    """
    tl = events.Timeline()
    for bad in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError) as caught:
            tl.add("step", "第 9 步：量了一下", who="system", data={"progress": bad})
        said = str(caught.value)
        assert "null" in said and "看不见" in said, "报错要说清它会被写成什么：%s" % said
    assert tl.all() == [], "抛了的那条不许留在时间线上"
    # 「量不出来」的正确写法：明写 None + 一句 why
    ok = tl.add("step", "第 9 步：量了一下", who="system",
                data={"sig_after": None, "why": "探针这一次没量出来"})
    assert ok["data"]["sig_after"] is None


def test_a_stored_event_survives_the_gates_own_scale():
    """存下来的每一条都过得了**闸自己那把尺**（`allow_nan=False` + `.encode("utf-8")`）。

    ⚠️ 名字收窄过（修复轮 2 的 M5）：上一版叫「与线上同一套设置」，而线上**不用**
    `json.dumps` —— 它走的是 pydantic 的 JSON-mode 序列化。两把尺**对齐的是那一档**
    （`nan` 不许活着、编码要过得去），不是同一件东西；说成「同一套设置」是名实不符。

    这条量的是「**闸放行的那一档，确实出得去**」（与线上那一层的行为比，
    另有 `test_service_events.py` 里绕过闸的那条判据）。
    """
    tl = events.Timeline()
    tl.add("step", "第 12 步：点了一个按钮", who="agent",
           data={"step_no": 12, "action": {"what": "click", "target": "下一步"},
                 "receipt": {"raw": "元素没找到", "from": "cdp"},
                 "sig_before": {"url": "https://x.test/a", "text": "a1b2c3", "visible": 12},
                 "sig_after": None, "why": "窗口没答"})
    raw = json.dumps({"events": tl.all()}, ensure_ascii=False, allow_nan=False).encode("utf-8")
    back = json.loads(raw.decode("utf-8"))
    assert back["events"][0]["data"]["receipt"]["raw"] == "元素没找到"
    assert back["events"][0]["data"]["sig_after"] is None
    # 而且「看不见」在这一档就是 `null` 这个字面值（不是缺键、不是别的值）
    assert '"sig_after": null' in raw.decode("utf-8")
