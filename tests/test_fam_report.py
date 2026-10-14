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
