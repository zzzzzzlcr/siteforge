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
