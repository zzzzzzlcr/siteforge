"""Task 2：`agent/plan.py` —— 描述 → 计划清单（纯函数，零模型）。

这份测试钉的是设计注 §2.2 的**三条硬规矩**：

1. **一个字都不改写** —— `Step.text` 是运营的原话，`Plan.raw` 是整份原文。
   改写就是**替人重写意图**，而这个项目栽过的「自信地错」正是这一类。
2. **非编号行一个字都不许吞** —— 真描述里除了步骤还有**约束**
   （`禁止点击: …` / `轮次: 30` / `成功条件URL: …`）。
   只给清单等于把约束丢掉 —— 模型就会去点运营明写的禁区。
3. **「没有计划」要判得出来** —— `steps == []` 且 `source == ""`。
   它与「有计划但这一趟一步没走到」在 Task 3 里**行为不同**
   （自由模式 vs 计划模式），所以**不许退化成空计划**。

⚠️ 关于「33」：`test_homebuddy_fixture_parses_33_numbered_lines` 里的 33 是
**解析断言**（「这份文件里有 33 个编号行」），**不是运行断言**。
homebuddy 那份自己就写着「随机选择一个选项」——选不同就走不同分支，
所以**运行**起来 20 / 33 / 34 都合法（设计注 §2.2）。
本文件里**没有任何一处**拿步数当期望值、上限或差值。
"""

from __future__ import annotations

import dataclasses
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import plan as plan_mod  # noqa: E402

DESCRIPTIONS = ROOT / "fixtures" / "descriptions"
HOMEBUDDY = DESCRIPTIONS / "homebuddy.txt"
BLINKIST = DESCRIPTIONS / "blinkist.txt"


# ── 造样例的小工具 ────────────────────────────────────────────────────
def _numbered(n: int) -> str:
    """造 n 个编号行（`1.第 1 步` … `n.第 n 步`）。"""
    return "\n".join(f"{i}.第 {i} 步" for i in range(1, n + 1))


def _plan(n: int = 5):
    return plan_mod.parse(_numbered(n))


# ── 判据 1：只认编号行 ───────────────────────────────────────────────
def test_three_separator_styles_are_recognized():
    """`1. …` / `1、…` / `1) …`（外加全角句点）都是编号行。"""
    plan = plan_mod.parse("1.点 A\n2、点 B\n3) 点 C\n4．点 D")
    assert [(s.n, s.text) for s in plan.steps] == [
        (1, "点 A"), (2, "点 B"), (3, "点 C"), (4, "点 D")]


def test_indented_numbered_lines_are_recognized():
    plan = plan_mod.parse("操作:\n   1.点 A\n\t2.点 B")
    assert [(s.n, s.text) for s in plan.steps] == [(1, "点 A"), (2, "点 B")]


def test_min_steps_is_two():
    assert plan_mod.MIN_STEPS == 2


# ── 判据 2：一个字都不改写 ───────────────────────────────────────────
def test_step_text_is_the_original_wording_verbatim():
    plan = plan_mod.parse("操作:\n1.填 Enter ZIP Code\n2.随机选择一个选项（别点 Cookie Policy）\n")
    assert plan.steps[0].text == "填 Enter ZIP Code"
    assert plan.steps[1].text == "随机选择一个选项（别点 Cookie Policy）"


def test_text_of_a_step_is_never_summarized_or_reordered():
    text = "操作:\n1.点 Get estimate，等页面刷出来\n2.选 Yes 之后再点 Next\n"
    plan = plan_mod.parse(text)
    # 原话逐字在 text 里（逐字断言，不是「长得像」）
    assert plan.steps[0].text in text
    assert plan.steps[1].text in text


def test_numbers_are_kept_as_written_and_never_renumbered():
    """描述可能从 0 开始、可能跳号 —— 重编就与人的话对不上了。"""
    plan = plan_mod.parse("0.零\n2.二\n7.七")
    assert [s.n for s in plan.steps] == [0, 2, 7]
    assert [s.text for s in plan.steps] == ["零", "二", "七"]


# ── 判据 3：非编号行一个字都不许吞 ───────────────────────────────────
def test_raw_is_the_whole_text_untouched():
    text = "页面URL: https://x\n\n成功条件: Thank you\n\n操作:\n1.点 A\n2.点 B\n"
    plan = plan_mod.parse(text)
    assert plan.raw == text


def test_constraint_lines_are_kept_in_raw_even_when_there_is_no_plan():
    """没解析出步骤时，原文照样原样带着（不是空计划，是「没计划 + 原文在」）。"""
    text = "禁止点击: Cookie Policy,Privacy Policy,Terms\n轮次: 30\n"
    plan = plan_mod.parse(text)
    assert plan.raw == text
    assert plan.steps == []


# ── 判据 4：「没有计划」要判得出来 ───────────────────────────────────
def test_a_single_step_is_not_enough_to_be_a_plan():
    plan = plan_mod.parse("操作:\n1.点 A\n")
    assert plan.steps == []
    assert plan.source == ""
    assert plan.actionable() is False


def test_zero_steps_is_no_plan():
    plan = plan_mod.parse("走到报价页就行。")
    assert plan.steps == []
    assert plan.source == ""
    assert plan.raw == "走到报价页就行。"


def test_empty_text_is_no_plan():
    plan = plan_mod.parse("")
    assert plan.steps == [] and plan.source == "" and plan.raw == ""


def test_source_is_cleared_when_there_is_no_plan():
    assert plan_mod.parse("走到报价页就行。", source="goal").source == ""
    assert plan_mod.parse("走到报价页就行。", source="evidence").source == ""


def test_source_is_kept_when_there_is_a_plan():
    assert plan_mod.parse(_numbered(3)).source == "goal"
    assert plan_mod.parse(_numbered(3), source="evidence").source == "evidence"
    assert plan_mod.parse(_numbered(3), source="evidence").actionable() is True


# ── 判据 5：散文里的数字不许被当成步骤（反例）────────────────────────
@pytest.mark.parametrize("prose", [
    "2026 年这个站改过版，注意一下。",
    "页面上有 3 个选项，随便选。",
    "轮次: 30",
    "浏览: 2",
    "成功条件URL: /news-feed,/welcome",
    "第 1 步要填邮编",
    "1) 这种是编号行",          # ← 反面：这个**该**被认出来，下面单独断言
])
def test_prose_numbers_are_not_mistaken_for_steps(prose):
    if prose.startswith("1)"):
        assert [s.n for s in plan_mod.parse(prose + "\n2.点 B").steps] == [1, 2]
        return
    plan = plan_mod.parse(prose)
    assert plan.steps == [], f"这行是散文/约束，不是步骤：{prose!r}"


def test_round_count_line_is_not_step_thirty():
    """`轮次: 30` 是**约束**，不许被当成第 30 步（计划 Task 2 点名的反例）。"""
    plan = plan_mod.parse("轮次: 30\n1.点 A\n2.点 B")
    assert [s.n for s in plan.steps] == [1, 2]
    assert 30 not in [s.n for s in plan.steps]
    assert "轮次: 30" in plan.raw


# ── 判据 6：Plan 里不许有「期望步数」（§2.2：步数不是结构）────────────
def test_plan_has_exactly_three_fields():
    assert {f.name for f in dataclasses.fields(plan_mod.Plan)} == {"raw", "source", "steps"}


@pytest.mark.parametrize("forbidden", ["count", "expected", "total", "n_steps", "step_count"])
def test_plan_carries_no_expected_step_count(forbidden):
    names = {f.name for f in dataclasses.fields(plan_mod.Plan)}
    assert forbidden not in names


def test_step_has_exactly_two_fields():
    assert {f.name for f in dataclasses.fields(plan_mod.Step)} == {"n", "text"}


# ── mark()：位置标记只读事实，不猜 ───────────────────────────────────
def test_mark_reads_the_marker_at_the_head_of_a_round():
    assert plan_mod.mark("【第 3 步】这一页是问卷，我点了 Next") == 3


def test_mark_returns_none_when_the_round_did_not_say():
    assert plan_mod.mark("我点了 Next，页面变了") is None
    assert plan_mod.mark("") is None


def test_mark_returns_none_for_a_number_that_cannot_be_a_position():
    """「第 0 步」不是一个位置 —— 认不出就给 None，**不猜**。"""
    assert plan_mod.mark("【第 0 步】") is None


def test_mark_returns_none_for_a_step_the_plan_does_not_have():
    plan = _plan(5)
    assert plan_mod.mark("【第 99 步】", plan=plan) is None
    assert plan_mod.mark("【第 3 步】", plan=plan) == 3


def test_mark_without_a_plan_only_reads_the_number():
    """号在不在计划里要拿 `plan` 才判得了 —— 单独读时只如实读号。

    范围这一层由 `ledger()` 兜住（不在计划里的号**什么都不动**）。
    """
    assert plan_mod.mark("【第 99 步】") == 99


def test_mark_takes_the_first_marker_when_a_round_mentions_two():
    assert plan_mod.mark("【第 2 步】描述说点『有没有浴缸』，页面上是『屋顶类型』") == 2


# ── ledger()：每一步一个终态 ─────────────────────────────────────────
def test_ledger_has_one_entry_per_step_in_plan_order():
    plan = _plan(5)
    led = plan_mod.ledger(plan, [])
    assert len(led) == len(plan.steps) == 5
    assert [e["n"] for e in led] == [1, 2, 3, 4, 5]
    assert [e["text"] for e in led] == [s.text for s in plan.steps]   # 原话照搬


def test_ledger_entry_has_the_four_fields_task_3_reads():
    led = plan_mod.ledger(_plan(2), [{"mark": 1}])
    assert set(led[0]) == {"n", "text", "state", "why"}


def test_every_state_is_one_of_the_four():
    plan = _plan(5)
    rounds = [{"mark": 2}, {"mark": 5}, {"mark": 3, "contradiction": "描述说点 A，页面上是 B"}]
    led = plan_mod.ledger(plan, rounds)
    assert {e["state"] for e in led} <= set(plan_mod.OUTCOMES)
    assert set(plan_mod.OUTCOMES) == {"done", "jumped_over", "contradicted", "not_reached"}


def test_unreported_steps_are_not_reached():
    """没被提到过的步骤是 `not_reached`（"没提到"本身**不算**终态）。"""
    led = plan_mod.ledger(_plan(5), [{"mark": 1}])
    assert [e["state"] for e in led] == [
        "done", "not_reached", "not_reached", "not_reached", "not_reached"]


def test_forward_jump_marks_the_skipped_ones_jumped_over():
    """B7 的正例：`【第 2 步】` → `【第 5 步】`，第 3、4 步是 `jumped_over`。"""
    led = plan_mod.ledger(_plan(5), [{"mark": 2}, {"mark": 5}])
    assert [e["state"] for e in led] == [
        "not_reached", "done", "jumped_over", "jumped_over", "done"]


def test_jumped_over_is_never_recorded_as_a_contradiction():
    """§2.3.1 的硬规矩：绕开（分支）**不是**矛盾。

    Task 3 的 `deviations` 只装 `contradicted` —— 要是「跳过去了」被记成矛盾，
    `deviations` 就会被日常噪音灌满，「同一处反复偏离」那条信号彻底失效。
    """
    led = plan_mod.ledger(_plan(5), [{"mark": 2}, {"mark": 5}])
    assert [e["state"] for e in led if e["n"] in (3, 4)] == ["jumped_over", "jumped_over"]
    assert not any(e["state"] == "contradicted" for e in led)


def test_a_first_mark_at_a_later_step_leaves_the_earlier_ones_not_reached():
    """「没报到」≠「跳过去了」—— 前面没有过标记时，如实记不知道，**不猜**（§2.4）。"""
    led = plan_mod.ledger(_plan(5), [{"mark": 5}])
    assert [e["state"] for e in led] == [
        "not_reached", "not_reached", "not_reached", "not_reached", "done"]


@pytest.mark.parametrize("rounds", [
    [{"mark": 99}],
    [{"mark": None}],
    [{}],
    [{"mark": "三步"}],
    [{"mark": 99}, {"mark": None}],
])
def test_marks_that_are_unknown_or_absent_move_nothing(rounds):
    """`【第 99 步】` / 没标记 → 位置**不动**（Task 3 的同一判据在账本这一层也成立）。"""
    led = plan_mod.ledger(_plan(5), rounds)
    assert [e["state"] for e in led] == ["not_reached"] * 5


def test_a_contradiction_marks_that_step_and_is_kept_verbatim():
    led = plan_mod.ledger(_plan(4), [
        {"mark": 2, "contradiction": "描述说点『有没有浴缸』，这一页上是『屋顶类型』"}])
    assert led[1]["state"] == "contradicted"
    assert led[1]["state"] != "done"
    assert "有没有浴缸" in led[1]["why"]      # 模型的声明原样记着，不改写


def test_a_contradicted_step_is_not_downgraded_when_the_walk_goes_on():
    led = plan_mod.ledger(_plan(4), [{"mark": 2, "contradiction": "对不上"}, {"mark": 4}])
    assert [e["state"] for e in led] == ["not_reached", "contradicted", "jumped_over", "done"]


def test_backward_jump_is_recorded_as_a_fact_not_as_a_skip():
    """往回跳也照记（§2.4）；中间那几步是**走到过的**，不许被记成 `jumped_over`。"""
    led = plan_mod.ledger(_plan(4), [{"mark": 3}, {"mark": 2}])
    assert [e["state"] for e in led] == ["not_reached", "done", "done", "not_reached"]


def test_why_is_always_a_sentence():
    """`why` 是写给人看的那句话（账本是给人看的），四选一的每一种都得有。"""
    led = plan_mod.ledger(_plan(5), [
        {"mark": 2}, {"mark": 5}, {"mark": 3, "contradiction": "对不上"}])
    for entry in led:
        assert isinstance(entry["why"], str) and entry["why"]


def test_ledger_of_a_plan_with_no_steps_is_empty():
    assert plan_mod.ledger(plan_mod.parse("走到报价页就行。"), [{"mark": 1}]) == []


# ── from_states()：旧 py 的 STATES → Plan（可选口子，§2.3）────────────
OLD_PY = '''SITE = "example-funnel"
STATES = [
  {'name': 'landing', 'when': {'text_contains': ['Get Started']},
   'steps': [{'action': 'click', 'note': '点「Get Started」进漏斗'},
             {'action': 'scroll', 'pixels': '400', 'note': '往下滚，露出问卷'}]},
  {'name': 'quiz', 'when': None,
   'steps': [{'action': 'click', 'note': '选「Tub to walk-in shower」'}]},
]
'''


def test_from_states_reads_the_notes_in_order():
    plan = plan_mod.from_states(OLD_PY)
    assert [s.text for s in plan.steps] == [
        "点「Get Started」进漏斗", "往下滚，露出问卷", "选「Tub to walk-in shower」"]
    assert [s.n for s in plan.steps] == [1, 2, 3]
    assert plan.actionable() is True


def test_from_states_source_is_evidence():
    """修站那条路（旧 py 的路线）算 `evidence`。"""
    assert plan_mod.from_states(OLD_PY).source == "evidence"


def test_from_states_falls_back_to_the_action_when_a_step_has_no_note():
    """没有 note 的步骤**不丢**（丢了就少一个检查点），退到它自己的动作名。"""
    plan = plan_mod.from_states(
        "STATES = [{'steps': [{'action': 'scroll'}, {'action': 'click', 'note': '点 A'}]}]\n")
    assert [s.text for s in plan.steps] == ["scroll", "点 A"]


@pytest.mark.parametrize("bad", [
    "def broken(:\n  STATES = [",          # 语法就坏
    "$states",                              # 模板占位符没渲染
    "STATES = [{'steps': [",                # 截断的
    "",                                     # 空的
    "STATES = build_states()\n",            # 字面量里算不出来（不 exec）
    "STATES = ",                            # 赋值不完整
])
def test_from_states_parses_bad_py_into_no_plan_not_an_exception(bad):
    """修站那条路可能给的是**坏 py** —— 抛异常是不行的。"""
    plan = plan_mod.from_states(bad)
    assert plan.steps == []
    assert plan.source == ""
    assert plan.actionable() is False


def test_from_states_missing_states_is_no_plan_and_keeps_the_source_text():
    plan = plan_mod.from_states("SITE = 'x'\n")
    assert plan.steps == [] and plan.source == ""
    assert plan.raw == "SITE = 'x'\n"       # 输入不吞


def test_from_states_does_not_execute_the_source(tmp_path):
    """坏 py 也可能**会干坏事** —— 解析走 ast，**不许 exec**。"""
    marker = tmp_path / "boom"
    src = (f"import pathlib\n"
           f"pathlib.Path({str(marker)!r}).write_text('x')\n"
           f"STATES = [{{'steps': [{{'note': '点 A'}}, {{'note': '点 B'}}]}}]\n")
    plan = plan_mod.from_states(src)
    assert not marker.exists(), "from_states 执行了传进来的 py —— 那修站那条路就危险了"
    assert [s.text for s in plan.steps] == ["点 A", "点 B"]


# ── Step 5：两份**真描述** ───────────────────────────────────────────
def test_homebuddy_fixture_parses_33_numbered_lines():
    """⚠️ 这是**解析**断言（这份文件里有 33 个编号行），**不是运行断言**。

    运行起来 20 / 33 / 34 都合法（§2.2：这份描述自己就写着「随机选择一个选项」）。
    拿 33 当期望步数写进任何判据都是错的。
    """
    plan = plan_mod.parse(HOMEBUDDY.read_text(encoding="utf-8"))
    assert len(plan.steps) == 33
    assert plan.actionable() is True


def test_homebuddy_first_step_is_verbatim():
    raw = HOMEBUDDY.read_text(encoding="utf-8")
    plan = plan_mod.parse(raw)
    assert plan.steps[0].n == 1
    assert plan.steps[0].text == "填 Enter ZIP Code"
    assert "1.填 Enter ZIP Code" in raw              # 原话逐字对得上
    assert "1.填 Enter ZIP Code" in plan.raw


def test_homebuddy_keeps_the_random_choice_lines_verbatim():
    """「随机选择一个选项」是**步数不固定**的第一手证据 —— 原话必须原样留着。"""
    plan = plan_mod.parse(HOMEBUDDY.read_text(encoding="utf-8"))
    random_ones = [s for s in plan.steps if s.text == "随机选择一个选项"]
    assert len(random_ones) == 9
    assert [s.n for s in random_ones[:3]] == [3, 7, 9]


def test_homebuddy_success_condition_is_not_a_step():
    plan = plan_mod.parse(HOMEBUDDY.read_text(encoding="utf-8"))
    assert not any("成功条件" in s.text for s in plan.steps)
    assert "成功条件: Thank you" in plan.raw        # 约束行在原文里，没被吞
    assert "页面URL: https://www.homebuddy.com/walk-in-showers/cr640" in plan.raw


def test_homebuddy_raw_is_the_file_untouched():
    raw = HOMEBUDDY.read_text(encoding="utf-8")
    assert plan_mod.parse(raw).raw == raw


def test_blinkist_fixture_raw_keeps_every_constraint_line():
    """⚠️ 简报 Step 5 说这份文件「解析出 4 步（`引导:` 那 4 条）」——
    但**文件里那 4 条不是编号行**，它们挤在 `引导:` 这一行上
    （`引导: 1.滚动到底部 2.点击Featured Titles …`，设计注 §2.2 引的就是这个形状）。

    简报那条「只认编号行」的判据锚在**行首**，这一行以 `引导:` 开头，
    所以按判据它**解析不出步骤**。两条要求互相打架，
    这里**照判据**（可单测的那条）实现，并如实钉住真数据的结果。
    「非编号行一个字都不许吞」这一半照样成立 —— 见下面两条断言。
    """
    raw = BLINKIST.read_text(encoding="utf-8")
    plan = plan_mod.parse(raw)
    assert plan.raw == raw
    for line in ("禁止点击: Cookie Policy,Privacy Policy,Terms", "轮次: 30", "浏览: 2",
                 "引导: 1.滚动到底部 2.点击Featured Titles 3.等待3秒 4.点击Verity",
                 "成功条件URL: /news-feed,/welcome"):
        assert line in plan.raw, f"约束行被吞了：{line!r}"


def test_blinkist_fixture_constraints_are_not_steps():
    """`禁止点击: …` / `轮次: 30` / `浏览: 2` 都不是步骤 —— 但它们都在 raw 里。"""
    plan = plan_mod.parse(BLINKIST.read_text(encoding="utf-8"))
    texts = [s.text for s in plan.steps]
    numbers = [s.n for s in plan.steps]
    assert not any("禁止点击" in t for t in texts)
    assert not any("轮次" in t for t in texts)
    assert not any("浏览" in t for t in texts)
    assert 30 not in numbers and 2 not in numbers


def test_blinkist_fixture_yields_no_plan_under_the_line_anchored_rule():
    """真数据在**行首编号**这条判据下的实际结果：0 步 → 自由模式。

    这一条钉的是**真文件此刻的样子**，不是「blinkist 不该有计划」。
    文件若换成一行一个编号（`1.滚动到底部` 各占一行），结果就该变成 4 步 ——
    那时这条测试要跟着改，`source` 也会变成 `goal`。
    """
    plan = plan_mod.parse(BLINKIST.read_text(encoding="utf-8"))
    assert plan.steps == []
    assert plan.source == ""
    assert plan.actionable() is False
