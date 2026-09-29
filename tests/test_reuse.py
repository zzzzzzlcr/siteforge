"""跨站复用比对（`agent/reuse.py`）的契约测试。

**为什么要这一层**：用户 2026-09-28 的原话「很多不同站点怀疑脚本一样，想加个验证」——
换站之后最贵的失败是**静默**的：判据钉着旧域名 ⇒ 整组跳过、跑完不报错。所以这里的钉子
盯的是「**哪几处算挡路**」，不是相似度分数：

  · 只有站名不同 ⇒ `能直接搬`（站名只进日志）；
  · 判据/到达判据钉着**别的域名** ⇒ `同族，但要改 N 处`，且那一条必须在 `diffs` 里；
  · **同一趟流程自己会跳到的自家域名**（实测：文章页 → 落地页）⇒ **不算挡路**，
    只说明（这是拿真产物试出来的假阳性，钉在 `test_a_sibling_domain…`）；
  · 形状不同（template vs loop）⇒ `不同族`，一条就够，不许硬凑；
  · `legacy` 只能比词表 ⇒ 结论里**必须自己说这是估的**（不许冒充逐条判的骨架）。

⚠️ 负例比正例重要（`tests/test_lint.py` 那条同源的道理）：一个「什么都判不同族」的比对
和有它没它一样坏 —— 所以第 1 条与「同域不报」那两条钉的就是「该说能搬就得说能搬」。
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import reuse  # noqa: E402


def _template(site: str = "alpha", host: str = "alpha.example", text: str = "Thank you",
              success_url: str = "") -> str:
    """一份 template 形状的产物（够 `fix._literals` 读出 STATES/FILLS 字面量）。"""
    urls = "[%r]" % success_url if success_url else "[]"
    return """
SITE = %r
SUCCESS_TEXTS = [%r]
SUCCESS_URLS = %s
STATES = [
  { 'name': 'landing',
    'when': { 'url_contains': 'https://%s/', 'text_contains': ['Welcome']},
    'steps': [ { 'action': 'click', 'note': '点了「Join Now」',
                 'target': {'text': 'Join Now', 'role': 'button',
                            'selectors': ['#hero-join', 'button.join']}}]},
  { 'name': 'form',
    'when': { 'url_contains': 'https://%s/form', 'text_contains': ['Your details']},
    'steps': [ { 'action': 'form', 'fill': 'email', 'note': '填好了「邮箱」',
                 'target': {'text': None, 'role': None,
                            'selectors': ['#email', 'input[name=email]']}}]}]
FILLS = { 'email': { 'name': 'email', 'kind': 'email',
                     'target': {'selectors': ['#email']}}}
""" % (site, text, urls, host, host)


def _loop(site: str = "alpha", arrive: str = "https://qualify.alpha.example/") -> str:
    """一份 loop 形状的产物（没有 STATES，有 FILLS + PROVENANCE）。"""
    return """
SITE = %r
SUCCESS_URL_MARKERS = ["thank-you"]
SUCCESS_TEXTS = ["Thank you"]
PROVENANCE = { 'entry_url': '', 'arrive_url': %r,
               'pre_steps': [ { 'action': 'click',
                                'target': {'text': 'Accept All', 'role': 'button',
                                           'selectors': ['#accept']}}]}
FILLS = { 'postcode': { 'name': 'postcode', 'kind': 'value',
                        'target': {'selectors': ['input#postcode']}}}
READ_PAGE_JS = "(function(){return '';})()"
""" % (site, arrive)


def _legacy(selectors: str, texts: str) -> str:
    return """
import argparse
SITE = 'old'
def main():
    p = argparse.ArgumentParser()
    p.add_argument("--ws-url")
    sel = %s
    say = %s
    return sel, say
""" % (selectors, texts)


# ── 正例：该说「能搬」就得说能搬 ──────────────────────────────────────────────

def test_only_the_site_name_differs_is_portable():
    got = reuse.compare(_template(site="alpha"), _template(site="beta"))
    assert got["verdict"] == "能直接搬", got
    assert any("站名不同" in d["say"] for d in got["diffs"])
    assert any("步骤序列一致" in one for one in got["same"])


# ── 挡路的那几处 ─────────────────────────────────────────────────────────────

def test_another_domain_in_the_judge_blocks():
    got = reuse.compare(_template(host="alpha.example"), _template(host="beta.example"))
    assert got["verdict"].startswith("同族，但要改")
    blocking = [d for d in got["diffs"] if d.get("blocking")]
    assert blocking and any("域名" in d["say"] for d in blocking)


def test_success_text_difference_is_blocking():
    got = reuse.compare(_template(text="Thank you"), _template(text="You are in"))
    assert got["verdict"].startswith("同族，但要改")
    assert any("成功文案不同" in d["say"] for d in got["diffs"] if d.get("blocking"))


def test_first_diverging_step_is_named():
    a = _template()
    b = _template().replace("'text': 'Join Now'", "'text': 'Get Started'")
    got = reuse.compare(a, b)
    assert got["verdict"].startswith("同族，但要改")
    assert any("第 1 步起不一样" in d["say"] for d in got["diffs"]), got["diffs"]


def test_field_table_difference_is_caught():
    a = _template()
    b = _template().replace("FILLS = { 'email'", "FILLS = { 'phone'")
    got = reuse.compare(a, b)
    assert any("要填的格子不同" in d["say"] for d in got["diffs"]), got["diffs"]


# ── 不同族：一条就说清，不凑分数 ─────────────────────────────────────────────

def test_two_shapes_are_not_the_same_family():
    got = reuse.compare(_template(), _loop())
    assert got["verdict"] == "不同族"
    assert "形状不同" in got["diffs"][0]["say"]


def test_broken_source_is_read_as_unreadable_not_as_a_family():
    got = reuse.compare("def broken(:\n", _template())
    assert got["verdict"] == "不同族"
    assert "读不出骨架" in got["diffs"][0]["say"]


# ── legacy：只能说词表（而且必须自己承认） ────────────────────────────────────

def test_legacy_only_says_wordlist_and_admits_it():
    a = _legacy("['#hero-join', '#accept-cookies', 'button.join']",
                "['Thank you for joining', 'Your details matter']")
    b = _legacy("['#hero-join', '#accept-cookies', 'button.join']",
                "['Thank you for joining', 'Your details matter']")
    got = reuse.compare(a, b)
    assert got["verdict"] == "看着同族（词表，需真跑确认）", got
    assert any("估的不是判的" in one for one in got["say"])


def test_legacy_with_nothing_in_common_is_a_different_family():
    a = _legacy("['#hero-join', '#accept-cookies']", "['Thank you for joining now']")
    b = _legacy("['.zzz-other', '.mmm-else']", "['Completely different words here']")
    got = reuse.compare(a, b)
    assert got["verdict"] == "不同族"
    assert "看着不是同一套漏斗" in got["diffs"][0]["say"]


# ── 拿去新站：挡路的钉子 ─────────────────────────────────────────────────────

def test_loop_arrive_url_on_another_host_blocks_the_new_site():
    got = reuse.portability(_loop(arrive="https://qualify.alpha.example/"),
                            "https://www.beta.example/signup")
    assert got["blocking"] and "alpha.example" in got["blocking"][0]["say"]


def test_same_host_is_not_flagged():
    got = reuse.portability(_loop(arrive="https://qualify.alpha.example/"),
                            "https://qualify.alpha.example/step-2")
    assert got["blocking"] == []
    assert any("没有钉死别的域名" in one for one in got["notes"])


def test_a_sibling_domain_the_flow_visits_is_not_blocking():
    """实测出来的假阳性：同一趟流程自己会跳到自家另一个域名（文章页 → 落地页）——
    只要有一个判据落在新站上，其他那些就不算挡路。"""
    src = _template(host="alpha.example", success_url="https://alpha-go.example/done")
    got = reuse.portability(src, "https://alpha.example/signup")
    assert got["blocking"] == [], got
    assert any("自己会跳过去" in one for one in got["notes"]), got


def test_no_new_url_says_it_cannot_judge_rather_than_ok():
    got = reuse.portability(_loop(), "")
    assert got["blocking"] == []
    assert any("判不了" in one for one in got["notes"])


# ═══════ 跨站同代体检（`reuse.same_generation`）═══════════════════════════════
#
# 这一层治的是《骨架还缺什么》§5 那件真事：funchatt 与同组三家本应同代，可它还挂在
# **文案匹配**的老脚本上，而平台的新版问卷（`data-testid` 那一代）早给别的组迁过了 ——
# 漏在外面十来天，**是靠人开窗手工撞见的**。这一关把那一撞变成机器话。
#
# 夹具两代，形状都从线上真产物量来（#66 那组 14 处 `data-testid`、#93 那组 0 处）：
#   · 新版 `_new_gen`：认 `data-testid`，成功判据钉在网址上；
#   · 老版 `_old_gen`：只读页面正文，没有 testid 词汇。
#
# ⚠️ 分组数据（`/api/quest/formConfig/sites`）**不许编** —— 拿不到就如实说「拿不到」。

def _new_gen(done: str, ids=("continue-button", "age-18-24", "email")) -> str:
    """新版那一代：`data-testid` 元素标记 + 网址成功判据。"""
    body = "\n".join("%s = '[data-testid=\"%s\"]'"
                     % (one.upper().replace("-", "_"), one) for one in ids)
    return ("SUCCESS_URL_MARKERS = (\"%s\",)\n%s\n"
            "def read():\n"
            "    return document.querySelector('[data-testid=\"continue-button\"]')\n"
            % (done, body))


def _old_gen(text: str = "Next") -> str:
    """老版那一代：只读页面正文，一个 testid 都不用。"""
    return ('NEXT = "%s"\n'
            'def _body():\n'
            '    return document.body.innerText\n' % text)


def test_a_group_that_is_all_one_generation_is_not_flagged():
    """① 同组同代 → **不喊**。

    ⚠️ 两家钉的成功网址**字面不同**（`a.example` / `b.example`）—— 同代两站本来就
    各钉各的域名，这一关比的是**形状**，拿字面值比会把健康的一组喊成一锅粥。
    """
    rows = [{"site": "a.example", "config_id": 93},
            {"site": "b.example", "config_id": 93}]
    srcs = {"a.example": _new_gen("https://a.example/wizard"),
            "b.example": _new_gen("https://b.example/wizard")}
    got = reuse.same_generation(rows, srcs)
    assert got["connected"] is True and got["checked"] is True, got
    assert got["verdict"] == "同代", got
    assert got["groups"][0]["same"] is True, got["groups"][0]
    assert "不喊" in "".join(got["say"])


def test_a_member_left_on_the_old_generation_is_named_and_the_gap_located():
    """② 同组混代 → **喊出是哪几家、差在哪几条**（不凑相似度分数）。"""
    rows = [{"site": "asiatalks.example", "config_id": 93},
            {"site": "thisromance.example", "config_id": 93},
            {"site": "funchatt.example", "config_id": 93}]
    srcs = {"asiatalks.example": _new_gen("https://a.example/wizard"),
            "thisromance.example": _new_gen("https://b.example/wizard"),
            "funchatt.example": _old_gen()}
    got = reuse.same_generation(rows, srcs)
    assert got["verdict"] == "有 1 组不同代", got
    group = got["groups"][0]
    assert group["same"] is False, group
    said = "\n".join(group["say"])
    assert "不是同一代" in said and "funchatt.example" in said, said
    whats = {one["what"] for one in group["diffs"]}
    assert "testid 词汇" in whats and "判据形态" in whats, whats
    testid = next(one for one in group["diffs"] if one["what"] == "testid 词汇")
    assert "funchatt.example" in testid["say"] and "一个都不用" in testid["say"], testid


def test_without_the_grouping_data_it_says_not_checked_not_all_clear():
    """③ 分组拿不到 → **如实说「拿不到 / 没检查」**（不是「没问题」）。

    「拿不到」与「检查过、没问题」是**两件事**；混成一件就是本项目最贵的那种谎。
    """
    got = reuse.same_generation(None, {"a.example": _new_gen("https://a.example/wizard")})
    assert got["connected"] is False and got["checked"] is False, got
    assert got["verdict"] == "拿不到分组", got
    said = "".join(got["say"])
    assert "拿不到" in said and "没检查" in said and "不表示" in said, said
    # 空清单走同一条路（「一个站都没有」不是「都没问题」）。
    assert reuse.same_generation([], {})["verdict"] == "拿不到分组"


def test_a_group_member_without_a_source_is_said_not_silently_dropped():
    """④ 组里某一家的源码没接上 → **出声点名**，不许静默当成「它没问题」。"""
    rows = [{"site": "a.example", "config_id": 7},
            {"site": "b.example", "config_id": 7}]
    got = reuse.same_generation(rows, {"a.example": _new_gen("https://a.example/wizard")})
    group = got["groups"][0]
    said = "\n".join(group["say"])
    assert "读不到源码" in said and "b.example" in said, said
    assert group["same"] is None, group          # 比不了 ≠ 同一代
    assert got["checked"] is False and got["verdict"] == "没得比", got


def test_a_site_without_a_config_number_is_said_out_loud_and_left_out():
    """⑤ 一条清单里**没有 config 号**的站 → 出声（分不进组），其余的照比。"""
    rows = [{"site": "a.example", "config_id": 1},
            {"site": "b.example", "config_id": 1},
            {"site": "noconfig.example"}]
    srcs = {"a.example": _new_gen("https://a.example/wizard"),
            "b.example": _new_gen("https://b.example/wizard")}
    got = reuse.same_generation(rows, srcs)
    said = "".join(got["say"])
    assert "noconfig.example" in said and "没有 config 号" in said, said
    assert got["verdict"] == "同代", got        # 另外那两家仍比成了


def test_a_group_that_could_not_be_compared_is_not_read_as_same_generation():
    """⑥ 有组比成了「同代」，**同时**有组比不了 → 后者要单独出声（不许被「同代」盖掉）。

    这两件事在不同组上同时成立时最容易出事：一句「不喊」会把没查的那几组
    一起销掉 —— 那正是「没检查却看着像检查过了」。
    """
    rows = [{"site": "a.example", "config_id": 1},
            {"site": "b.example", "config_id": 1},
            {"site": "lonely.example", "config_id": 2}]
    srcs = {"a.example": _new_gen("https://a.example/wizard"),
            "b.example": _new_gen("https://b.example/wizard")}
    got = reuse.same_generation(rows, srcs)
    assert got["verdict"] == "同代", got           # 比成的那一组确实同代
    said = "".join(got["say"])
    assert "比不了" in said and "没检查" in said and "配置 2" in said, said
