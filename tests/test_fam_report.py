"""`tools/fam_report.py` 里那一处**判断**的钉子 —— 修到顶的趟，是「原地反复」还是「往前推进」。

## 为什么这一条要单独钉

这份报告里**只有这一格带判断**，其余全是数数（数几趟、数几轮）—— 数错了看得见，
判断错了看不出来。而它错了的代价是把两件处置**相反**的事读成一件：

| 读成 | 现场长什么样 | 该干什么 |
|---|---|---|
| **A 原地反复** | 两轮抱怨的是**同一个东西** | 换证据 / 换人 —— 再改一稿还是撞同一堵墙 |
| **B 往前推进** | 每轮修好一处，抱怨**换了个东西** | 查流程可靠性与状态恢复 —— 单点修复治不了 |

## ⚠️ 步号（`failed_step`）**不是**判据 —— 这是量出来的，不是设计偏好

第一版拿「步号相同」当 A 的判据，被真数据打回来了：**修完会重写整份 py，步号跟着重编号**。

| 真事 | 第 1 轮 | 第 2 轮 | 步号在说什么 |
|---|---|---|---|
| `job-cedaca6eaa52` | 第 **4** 步 · 滚不动「Buy now」 | 第 **3** 步 · 没找到「Buy now」 | 往回走 —— 可抱怨的是**同一个元素** |
| `job-d40bb4d2d2be` | 第 **19** 步 · 滚不动「Next →」 | 第 **16** 步 · 滚不动「Next →」 | 往回走 —— 同一句话 |
| `job-c053e0b049f3` | 第 **9** 步 · trace 没写为什么 | 第 **60** 步 · 同一句 | 挪了 51 步 —— 可抱怨一字没变 |

而 `第 0 步` 满场都是（3 趟）—— 那是**没记到**，不是「第 0 步」。所以判据只用
**抱怨点名了哪个东西**（`「…」`里那几个），步号一个字都不看。

## 还有一档：同句但**没点名**（记账类）

「trace 里那一步没写为什么」这类抱怨**不含任何点名的元素** —— 它是**我们自己没记下来**，
不是页面上有什么东西挡着。把它读成 A 会让人去查一个根本没问题的页面，所以单独一档。

判据与 `agent/state.py` 的 `END_REPAIR_STUCK` 同一句话（「同一处、同一句话连着两次」），
**不另立一把尺子**。真事 `job-c02d5768e535`：两轮都是「Accept All Cookies」，
**整句前后缀不同**（第一轮多了「退出码说成功」那半截）—— 只看整句会把它误判成「分不出」。

⚠️ **判不出来不许默认成任何一类**（只有一轮 / 两轮都没点名 / 点名的换法认不出）。
「拿一个数顶替没量到」这一族在本仓有先例（`formLog` 那格 `fail=0` 不是「没失败」）。
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

_TOOL = ROOT / "tools" / "fam_report.py"


def _load():
    """`tools/` 下的脚本不是包，按路径装进来（与 `test_browser_agent` 装产物同一个法子）。"""
    spec = importlib.util.spec_from_file_location("fam_report", _TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def fam():
    return _load()


def _round(step, note):
    return {"failed_step": step, "note": note}


def test_the_same_named_thing_twice_reads_as_stuck(fam):
    """真事 `job-a6954761e657`：两轮都是「Your postcode」没找到。"""
    rounds = [_round(7, "卡在第 7 步：没找到「Your postcode」这个输入框，没填成；页面没有变化"),
              _round(7, "卡在第 7 步：没找到「Your postcode」这个输入框，没填成；页面没有变化")]
    assert fam.classify_rounds(rounds) == fam.ROUNDS_STUCK


def test_the_same_named_thing_with_different_wording_around_it_still_reads_as_stuck(fam):
    """真事 `job-c02d5768e535`：两轮都点名「Accept All Cookies」，**整句不同** —— 仍是 A。"""
    rounds = [_round(2, "退出码说成功，但 trace 里第 2 步（**承重步** click）没做成"
                        "（页面上没找到「Accept All Cookies」，这一步没做成；页面没有变化）—— 按没做成算"),
              _round(2, "卡在第 2 步：页面上没找到「Accept All Cookies」，这一步没做成；页面没有变化")]
    assert fam.classify_rounds(rounds) == fam.ROUNDS_STUCK


def test_a_step_number_that_walks_backwards_does_not_stop_it_reading_as_stuck(fam):
    """真事 `job-cedaca6eaa52`：第 4 步 → 第 3 步（重写后重编号）—— 点名的还是「Buy now」。"""
    rounds = [_round(4, "卡在第 4 步：滚不动「Buy now」；页面没有变化"),
              _round(3, "卡在第 3 步：页面上没找到「Buy now」，这一步没做成；页面没有变化")]
    assert fam.classify_rounds(rounds) == fam.ROUNDS_STUCK


def test_a_different_named_thing_on_the_second_round_reads_as_advancing(fam):
    """两轮点的是**不同的东西** —— 那才叫「修好一处、冒出下一处」。"""
    rounds = [_round(5, "卡在第 5 步：没找到「Email」这个输入框"),
              _round(9, "卡在第 9 步：没找到「Phone number」这个输入框")]
    assert fam.classify_rounds(rounds) == fam.ROUNDS_ADVANCING


def test_the_same_words_with_nothing_named_is_its_own_thing_not_stuck(fam):
    """真事 `job-c053e0b049f3`：两轮一字不差，可那是「**trace 没写为什么**」—— 我们自己没记下来。

    它和「页面上有个东西挡着」是**两件事**：读成 A 会让去查一个根本没问题的页面。
    """
    rounds = [_round(9, "卡在第 9 步：trace 里那一步没写为什么"),
              _round(60, "卡在第 60 步：trace 里那一步没写为什么")]
    got = fam.classify_rounds(rounds)
    assert got == fam.ROUNDS_UNNAMED
    assert got != fam.ROUNDS_STUCK


def test_a_single_round_is_not_enough_to_tell_the_two_apart(fam):
    """只修了一轮 —— 「有没有推进」这个问题**没量到**，不是「没推进」。"""
    assert fam.classify_rounds([_round(7, "卡在第 7 步：没找到「框」")]) == fam.ROUNDS_UNCLEAR


def test_one_round_naming_something_and_one_naming_nothing_is_not_read_as_either(fam):
    """一轮点了名、一轮没有 —— 换法认不出来，不许硬塞进 A 或 B。"""
    rounds = [_round(7, "卡在第 7 步：没找到「Your postcode」"), _round(7, "这一格是阴影宿主里的，够不着")]
    assert fam.classify_rounds(rounds) == fam.ROUNDS_UNCLEAR


def test_a_round_with_no_step_number_still_reads_by_what_it_names(fam):
    """`failed_step` 为空（「没走到成功」那档）**不影响**判据 —— 步号本来就不参与。"""
    rounds = [_round(None, "每一步都做成了，但没出现「Thank You」"),
              _round(None, "每一步都做成了，但没出现「Thank You」")]
    assert fam.classify_rounds(rounds) == fam.ROUNDS_STUCK


# ── 弹窗族：产物运行期那四格（从自测日志读）────────────────────────────
#
# 为什么口径是「**次运行**」不是「趟」：自测目录名只有 `<站>-<时刻>`，**不带 job id**，
# 没法跟台账 join；而「两道保护有没有用」这件事，每次自测就是一次检验。

def test_the_popup_ledger_separates_exempt_from_died(fam, tmp_path):
    """**豁免**与**判死**必须分开计数 —— 那是「保护有没有用」这件事的两半。

    真事 `runtime/selftest/compareinsulation-20261013-075250`：钩子表命中一次
    （`把「同意条」点掉了`），账本那条「Accept all」被**豁免**（`跳过，不算没做成`），
    可同一份产物里**另一条**「Accept All Cookies」被判死（`第 2 步：页面上没找到…`）。
    把两者混成一个数，就永远答不出「保护到底有没有用」。
    """
    run = tmp_path / "selftest" / "demo-20260101-000000" / "baseline"
    run.mkdir(parents=True)
    (run / "demo.baseline.log").write_text(
        "这一步动手之前之后 把「同意条」点掉了（#si-accept，'Accept All Cookies'）：看起来成了\n"
        "「Accept all」这一步：页面上已经没有同意弹层了（前面那次会话把它点掉过）—— 跳过，不算没做成。\n"
        "第 2 步：页面上没找到「Accept All Cookies」，这一步没做成；页面没有变化\n",
        encoding="utf-8")
    st = fam.selftest_popup_stats(tmp_path)

    assert st["runs"] == 1
    assert st["hits"] == {"同意条": 1}, "① 钩子表命中了几次、命中的是哪一条"
    assert st["exempt"] == 1, "③a 账本那条同意条被豁免（保护有效）"
    assert st["died"] == 1, "③b 另一条被判死（保护漏了）—— 两半分开记"
    assert st["dup"] == 0, "② 同一次运行里同一条被点 ≥2 次才算重复"


def test_a_non_consent_step_that_was_not_found_is_not_counted_as_a_popup(fam, tmp_path):
    """**负控**：`第 N 步：没找到「Submit」` 不是弹窗那一族 —— 别把它算进来。"""
    run = tmp_path / "selftest" / "demo-20260101-000000" / "baseline"
    run.mkdir(parents=True)
    (run / "demo.baseline.log").write_text(
        "第 5 步：页面上没找到「Submit」，这一步没做成；页面没有变化\n", encoding="utf-8")
    st = fam.selftest_popup_stats(tmp_path)

    assert st["died"] == 0, "「Submit」不是同意条 —— 这一格不许把普通步骤算成弹窗"


def test_only_the_names_the_executor_calls_consent_are_counted(fam, tmp_path):
    """③b 的**二次过滤**不可省：同一份日志里混着同意条与普通步骤时，只数同意条那几条。

    用真数据里出现过的三个名字（`Yes` / `See if you qualify for benefits` /
    `没写名字的元素` 都**不是**同意条 —— 前两个是业务按钮，第三个是探路误点的）。
    """
    run = tmp_path / "selftest" / "demo-20260101-000000" / "baseline"
    run.mkdir(parents=True)
    (run / "demo.baseline.log").write_text(
        "第 2 步：页面上没找到「Accept All Cookies」，这一步没做成；页面没有变化\n"
        "第 6 步：页面上没找到「Yes」，这一步没做成；页面没有变化\n"
        "第 9 步：页面上没找到「See if you qualify for benefits」，这一步没做成；页面没有变化\n"
        "第 11 步：页面上没找到「没写名字的元素」，这一步没做成；页面没有变化\n",
        encoding="utf-8")
    st = fam.selftest_popup_stats(tmp_path)

    assert st["died"] == 1, "只该数「Accept All Cookies」那一条 —— 其余三条是普通步骤"


def test_the_same_hook_twice_in_one_run_duplicates_but_across_runs_does_not(fam, tmp_path):
    """`dup` 的口径是「**同一次运行**内同名 ≥2 次」—— **跨运行不许累计**。

    两个运行各点一次同一个钩子，那是**两次正常运行**，不是「重复处理」。
    混起来算会让「重复处理」这个数随运行次数虚涨（是个隐蔽的统计偏差）。
    """
    for tag, hits in (("a", 2), ("b", 1), ("c", 1)):
        run = tmp_path / "selftest" / ("demo-20260101-00000%s" % tag) / "baseline"
        run.mkdir(parents=True)
        (run / ("demo.%s.log" % tag)).write_text(
            "把「同意条」点掉了（#x，'Accept all'）：看起来成了\n" * hits, encoding="utf-8")
    st = fam.selftest_popup_stats(tmp_path)

    assert st["runs"] == 3
    assert st["hits"] == {"同意条": 4}, "命中总数照旧是全部加起来"
    assert st["dup"] == 1, "只有 a 那一次运行算重复；b 与 c 各一次不算"


def test_no_selftest_run_says_unmeasured_not_zero_popup_failures(fam, tmp_path):
    """`runs == 0` 是「**没观测到**」，不是「零弹窗故障」—— 报告上必须这么说。"""
    (tmp_path / "selftest").mkdir()
    st = fam.selftest_popup_stats(tmp_path)
    assert st["runs"] == 0 and st["died"] == 0, "两个都是 0，但意思完全不同"

    text = fam.render({"empty": False, "rows": [], "no_journal": [], "missing": [],
                       "popup": {str(tmp_path): st}})
    assert "没量到" in text, "报告要把它说成「没量到」，不许画成一片空白"
    assert "没有 selftest 目录" in text


# ── 「他有没有点修复」与「没走到终局的趟」 ──────────────────────────────
#
# 起因：运营反馈「今天的都是自测过不去」，而**没人答得出他点没点修复**。
# 账本答得出（`visits` 路线），但报表里没有那一格。

def test_the_report_says_diagnose_to_draft_is_not_a_human_click(fam):
    """★ 2026-10-14 **撤回的一格**：`diagnose` 后面跟 `draft` **不是「他点了修复」**。

    那是图在那道闸上的**默认路径**（`_next("draft")`）—— 第一版拿它当判据，
    218 趟里 25/25 全中，一点信息量都没有。
    真查过：人打回那一档（`revised_at`）**218 趟里一次都没被用过**；人在闸上做过的事，
    账本上有痕迹的只有 `hints`（「说一句纠正」）。

    ⇒ 报告里必须把这条**说死**，免得下一个人又拿默认路径当人的动作读。
    这一条钉的就是那句话还在。
    """
    text = fam.render({"empty": False, "rows": [], "no_journal": [], "missing": [],
                       "popup": {}, "open_jobs": []})
    assert "默认路径" in text, "「diagnose→draft 是默认路径」这句不许被删掉"
    assert "这版不行" in text and "一次都没被用过" in text, "撤回的理由要留在报告里"
    assert "hints" in text, "真正看得见的那一格（人说过的话）要点名"


def test_a_run_that_never_ended_is_listed_and_named(fam):
    """**没走到终局的趟**要单列、要点名 —— 不许被静默吃掉。

    台账里这类有 69 个（10-14 那天 3 个），而先前所有数（族分布、A/B、长度墙）
    **全部跳过了它们** —— 谁读报表都看不见这一块。
    """
    text = fam.render({
        "empty": False, "rows": [], "no_journal": [], "missing": [], "popup": {},
        "open_jobs": [{"job": "job-aaaaaaaaaaaa", "last": "selftest", "repair": True},
                      {"job": "job-bbbbbbbbbbbb", "last": "intake", "repair": None}]})
    assert "没走到终局" in text, "要有一格专门说它们"
    assert "job-aaaaaaaaaaaa" in text and "job-bbbbbbbbbbbb" in text, "要点名"
    assert "2" in text
    assert "正在跑" in text or "不一定是卡住" in text, "「停在 selftest」不等于卡住，得说清"
