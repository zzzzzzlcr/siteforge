"""合规闸：一份 JSON 配置**合不合规** —— 判据从执行器**现量**，不硬编码。

## 为什么要有这一层

用户 2026-09-21 定：「修完的配置要遵守规则」。而**今天没有任何东西管这件事** ——
修复那一步还没写，跑得起来也不等于合规。

执行器碰到**不认的动作**时是这么办的（`json_executor.py:3041`）：

    else:
        self.log.warning(f"[JSON] Unknown action: {action}")
        return False

**不报错、不崩**，只在日志里小声警告一句就当这一步失败了，然后接着往下跑。
⇒ 配置里打错一个动作名、或者模型编一个新动作出来，那份配置**照样跑得完** ——
而它在屏幕上的样子与「这一步没效果」**一模一样**（这个仓最贵的那类形状）。

## ⚠️ 这一份钉的头一件事：**表是量的，不是抄的**

那个执行器在 `/opt/skills/auto-farm-skill/`，**别人在改**（2026-09-21 一天改过好几轮）。
把动作表抄成常量，过几天就成了「拿旧规矩卡新执行器」。所以：
判据一律**从源码文本现量**，并且把**指纹**一起带出来（下面第 ② 组用例钉这个）。
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import configcheck  # noqa: E402

#: **cvrefresh.com 那份配置的逐字复制**（2026-09-21 经 B1 的读口取回，实测；
#: 与 `test_jsondiag.py` / `test_service_jsondiff.py` 是同一份）。
#: 用真形状当底稿：自己编一份简单的，量到的只是「我编的那份能被判对」。
CLEAN = {
    "form_type": "magic_link",
    "site": "cvrefresh.com",
    "steps": [
        {"action": "wait", "max": 10, "min": 5},
        {"action": "click", "find": {"text": "Refresh my resume"}},
        {"action": "form",
         "field": {"label": "Email", "placeholder": "your@email.com", "type": "email"},
         "value": "{{random.email}}"},
        {"action": "click", "find": {"text": "Send magic link"}},
        {"action": "wait", "max": 10, "min": 5},
    ],
    "success": {"any": [{"body_contains": ["Check your email"]}]},
}

#: 一份**假**执行器源码：形状与真的那一份一样（16 空格缩进的分派链 + `step.get("k")`），
#: 但动作集不同（多了 `boop`、少了 `click`）。用它钉「这张表是**量**出来的」——
#: 抄成常量的那种实现，在这份源码上会露馅。
FAKE_SRC = '''
class _X:
    def _run(self, step):
        action = step.get("action")
        if action in ("wait", "delay"):
            t = step.get("min", 0.3)
            or_until = step.get("or_until_url")
        elif action == "boop":
            who = step.get("who")
            n = step["times"]
        else:
            self.log.warning(f"Unknown action: {action}")
            return False
'''


def _errors(problems):
    return [p for p in problems if p["level"] == "error"]


def _notes(problems):
    return [p for p in problems if p["level"] == "note"]


# ── ① 判据是**量**出来的 ────────────────────────────────────────────────

def test_the_action_table_is_measured_from_the_source_text_not_hardcoded():
    """★ 拿一份**改过的**源码去量 ⇒ 量出来的表跟着它变。

    抄成常量的那种实现，在这一条上会露馅：它会说 `click` 在表里、`boop` 不在 ——
    而这份源码里正好反过来。
    """
    rules = configcheck.measure_rules(FAKE_SRC)

    assert set(rules["actions"]) == {"wait", "delay", "boop"}, rules["actions"]
    assert "click" not in rules["actions"], "表是抄的（真执行器里才有 click）"
    assert rules["actions"]["boop"] == ["times", "who"], rules["actions"]["boop"]


def test_a_real_shaped_config_passes_with_the_rules_we_measured():
    """真形状那份（cvrefresh）⇒ **一个问题都没有**（连提醒都没有）。

    这一条同时是**误报的闸**：哪天线上一份好好的配置被这道闸拦下来，
    红的就是它。
    """
    #: 这一条走**真**执行器量出来的那份（假源码里没有 click/form，量不出这份配置）。
    rules = configcheck.load_rules()

    assert configcheck.check_config(CLEAN, rules) == []


# ── ② 指纹：这次量的是哪一版 ────────────────────────────────────────────

def test_the_rules_carry_a_fingerprint_so_drift_is_visible():
    """规则要带上**它量的是哪一版**（路径 + sha256 + 文件时间）—— 漂了才看得见。"""
    rules = configcheck.load_rules()

    assert rules["path"].endswith("json_executor.py"), rules["path"]
    assert len(rules["sha256"]) == 64, rules["sha256"]
    assert rules["mtime"], rules  # 非空


def test_the_real_executor_still_has_the_actions_we_wrote_down():
    """★ **漂移警报**：真执行器里那 16 个动作**一个都还在**。

    为什么这条要红而不是静默：那份规则表（`docs/JSON配置-规则-2026-09-21.md`）
    是照这一版量的；执行器**少了一个动作**就说明有人动过它 ——
    这时该做的是**重新量一遍 + 改文档**，不是让闸拿旧规矩去卡新执行器。
    ⚠️ 只查「还在不在」，**不查「有没有新增」**：新增的动作这一层自动就认，
    不该让别人的一次改动把这一份搞红。
    """
    ruled = {
        "wait", "delay", "scroll", "captcha", "eval", "report", "click", "form",
        "wait_for", "if", "goto", "navigate", "select", "quiz_loop", "ai_step",
        "select_option",
    }
    got = set(configcheck.load_rules()["actions"])

    missing = ruled - got
    assert not missing, (
        "执行器里这几个动作没有了：%s —— 说明 `/opt/skills/auto-farm-skill` 被人动过。"
        "**重新量一遍**，并把 `docs/JSON配置-规则-2026-09-21.md` 一起改掉。" % sorted(missing))


# ── ③ 拦什么、不拦什么 ──────────────────────────────────────────────────

def test_an_unknown_action_is_an_error_and_says_why_it_matters():
    """★ 闸的正身：动作不在白名单里 ⇒ **error**，而且要把后果说出来。

    光说「不认识这个动作」不够 —— 读的人得知道**它不会报错、它只是不干活**，
    否则他只会以为这闸太严。
    """
    bad = dict(CLEAN, steps=[{"action": "clik", "find": {"text": "x"}}])

    got = _errors(configcheck.check_config(bad, configcheck.load_rules()))

    assert len(got) == 1, got
    said = got[0]["say"]
    assert "clik" in said, said
    assert "第 1 步" in got[0]["where"], got[0]
    #: 后果要说出来（「照样跑得完、只是那步没干」正是这一闸存在的理由）
    assert "警告" in said or "不报错" in said, said


def test_a_step_without_an_action_is_an_error():
    """连 `action` 那一格都没有 ⇒ error（执行器那一支读的就是它）。"""
    bad = dict(CLEAN, steps=[{"find": {"text": "x"}}])

    got = _errors(configcheck.check_config(bad, configcheck.load_rules()))

    assert len(got) == 1, got
    assert "action" in got[0]["say"], got[0]


def test_a_step_that_gives_none_of_the_keys_its_action_reads_is_an_error():
    """这个动作要读的键**一个都没给** ⇒ error（它一定读不到东西）。

    ⚠️ 只拦「一个都没给」这一档 —— 「给少了其中几个」不在这一层判
    （有的键有默认值，量不出来）。见下一条。
    """
    bad = dict(CLEAN, steps=[{"action": "eval"}])

    got = _errors(configcheck.check_config(bad, configcheck.load_rules()))

    assert len(got) == 1, got
    assert "script" in got[0]["say"], got[0]  #: 它读的是 script


def test_keys_we_did_not_measure_are_only_a_note_never_an_error():
    """★ **没量到 ≠ 就是错的**：多带的键只出 `note`，**不阻断**。

    为什么这一档必须是提醒：动作分支里读的键是**粗量**的（`if` / `quiz_loop`
    那些子分支里的读法没进去量），而且配置里本来就可能带给别处看的元数据。
    把它判成 error，这道闸就会把好好的配置拦下来 —— 那比不设闸更坏。
    """
    odd = dict(CLEAN, steps=[{"action": "wait", "min": 1, "max": 2, "note": "运营写的"}])

    got = configcheck.check_config(odd, configcheck.load_rules())

    assert _errors(got) == [], got
    assert len(_notes(got)) == 1, got
    assert "note" in _notes(got)[0]["say"], got


# ── ④ 子步也要走进去 ────────────────────────────────────────────────────

def test_sub_steps_are_walked_too():
    """★ 子步（`if` / `quiz_loop` 里的 `steps`）**也要逐格看**。

    只扫最外层的话，最贵的那个坏法正好漏掉：修好的主流程 + 子步里一个编出来的动作。
    """
    bad = dict(CLEAN, steps=[
        {"action": "wait", "min": 1, "max": 2},
        {"action": "quiz_loop", "steps": [
            {"action": "wait", "min": 1, "max": 2},
            {"action": "teleport", "find": {"text": "x"}},
        ]},
    ])

    got = _errors(configcheck.check_config(bad, configcheck.load_rules()))

    assert len(got) == 1, got
    assert "teleport" in got[0]["say"], got
    assert "第 2 步" in got[0]["where"] and "子步" in got[0]["where"], got[0]
