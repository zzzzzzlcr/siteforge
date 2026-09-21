"""B 线 ③（乙）：**老写法**的 py 也能修 —— 认形状、补丁过闸、自测那条线。

用户 2026-09-21 拍板走「乙」：**不改线上脚本、后端一个字不动**，让修站那条路认线上
现成的那一族（`class XxxFiller`：走 `common.report_url`、认生产那四个开关、
`sys.exit(0 if … else 1)`）—— 它们**没有** `STATES/FILLS` 那张数据表。

量过的背景（见 `docs/交接-2026-09-21-B线-3.md` §3）：
后端那一份与机器上 `forms/sites/*.py` 逐字节同一份 ⇒ 不是接口没同步；
线上 66 份 py 里 0 份带 siteforge 的模板标记 ⇒ 模板那条路**没有对象可修**。

这份文件钉三类东西：
  ① `agent/fix.py`：认形状（`shape_of`）与补丁过闸（`check_patch`）—— **纯函数**；
  ② `agent/graph.py`：intake 那条分岔、draft 那条支、少接一根线时**停下并点名**；
  ③ `agent/selftest.py` + `forms/common.py`：老产物的那句**证据**从运行时进
     （argv 不给 `--trace`/`--no-report`/`--delay` —— 它们的 `main()` 里没有那三个开关）。
"""

from __future__ import annotations

import json
import pathlib
import shutil
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from agent import fix, graph, runtime, selftest  # noqa: E402
from test_graph import SITE, SUCCESS, URL, _brief, _build, _deps, _drive  # noqa: E402

#: 一份**老写法**的产物（照着线上那一族的记号写：`from common import … report_url`、
#: 生产那四个开关、`sys.exit(0 if … else 1)`，进度走 `report_url`）。
#: ⚠️ 类名是 `ExampleFiller` —— 但**它一个字都不是判据**（`shape_of` 只看记号）。
LEGACY_PY = '''#!/usr/bin/env python3
"""Site script for example.test survey.

Success: reaches example.test/thank-you
"""

import argparse, json, os, sys, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import CDPHelper, setup_logger, report_url

MAX_STEPS = 40
SUCCESS_URL_MARKERS = ("thank-you", "thankyou")


class ExampleFiller:
    def __init__(self, ws_url, form_file, cid, tid=""):
        self.ws_url, self.form_file, self.cid, self.tid = ws_url, form_file, cid, tid
        self.cdp = None
        self.log = setup_logger("example")

    def run(self, max_steps=MAX_STEPS):
        report_url(self.cdp, self.tid, "started", self.log)
        return False

    def is_success(self, url):
        return any(m in (url or "").lower() for m in SUCCESS_URL_MARKERS)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--ws-url", required=True)
    p.add_argument("--form-file", required=True)
    p.add_argument("--correlation-id", required=True)
    p.add_argument("--log-level", default="INFO")
    p.add_argument("--task-id", default="")
    a = p.parse_args()
    f = ExampleFiller(a.ws_url, a.form_file, a.correlation_id, a.task_id)
    sys.exit(0 if f.run() else 1)


if __name__ == "__main__":
    main()
'''


# ── ① 认形状：靠量，不靠猜 ────────────────────────────────────────

def test_shape_of_tells_the_template_family_from_the_live_one():
    """`STATES/FILLS` 在 ⇒ 模板形；线上那一族的记号在 ⇒ 老写法。**先判模板形**。"""
    template = (ROOT / "fixtures" / "reference_site.py").read_text(encoding="utf-8")
    assert fix.shape_of(template)["kind"] == "template"
    got = fix.shape_of(LEGACY_PY)
    assert got["kind"] == "legacy", got
    assert got["why"] == [], got


def test_shape_of_says_in_plain_words_what_is_missing():
    """都不是时**逐条**说缺什么 —— 那句话是给运营看的，不是给编译器看的。"""
    # ① 少了进度上报（自测那一族的证据靠它）
    no_report = LEGACY_PY.replace("from common import CDPHelper, setup_logger, report_url",
                                  "from common import CDPHelper, setup_logger")
    got = fix.shape_of(no_report)
    assert got["kind"] == "unknown"
    assert any("report_url" in w for w in got["why"]), got
    # ② 少了生产要的开关
    got = fix.shape_of(LEGACY_PY.replace('p.add_argument("--log-level"', 'p.add_argument("--level"'))
    assert got["kind"] == "unknown"
    assert any("--log-level" in w for w in got["why"]), got
    # ③ 收尾那个成功判据被改掉了
    got = fix.shape_of(LEGACY_PY.replace("sys.exit(0 if f.run() else 1)", "sys.exit(0)"))
    assert got["kind"] == "unknown"
    assert any("sys.exit" in w for w in got["why"]), got
    # ④ 压根不是 py
    assert fix.shape_of("def f(:\n")["kind"] == "unknown"


def test_importing_report_url_without_calling_it_is_not_a_legacy_script():
    """**import 了不等于调了**：一处都不调的那种脚本，跑到哪儿外面看不见（自测没有证据）。"""
    silent = LEGACY_PY.replace('report_url(self.cdp, self.tid, "started", self.log)', "pass")
    got = fix.shape_of(silent)
    assert got["kind"] == "unknown", got
    assert any("一处都没调" in w for w in got["why"]), got


# ── ① 补丁过闸：每一条闸对应一种真会出事的坏法 ─────────────────────

def test_a_patch_that_changes_nothing_is_not_a_fix():
    """★ 「一处都没改」的稿**不是修** —— 它会长得跟修好了一模一样（lint 过、自测过）。

    【我量的·2026-09-21】真跑里出过两次：一次把一行注释少了个空格、一次逐字一样。
    """
    bad = fix.check_patch(LEGACY_PY, LEGACY_PY)
    assert bad and "逐字一样" in bad[0], bad
    #: 正控：真改了东西就过（否则这条闸会把好稿也拦下）
    assert fix.check_patch(LEGACY_PY, LEGACY_PY.replace("MAX_STEPS = 40", "MAX_STEPS = 60")) == []


def test_check_patch_blocks_each_way_a_patch_could_break_production():
    """五条闸逐条量一遍：**过不了就是过不了**，一条都不许软。"""
    good = LEGACY_PY.replace("MAX_STEPS = 40", "MAX_STEPS = 60")
    assert fix.check_patch(LEGACY_PY, good) == []

    cases = {
        "空的": ("", "空的"),
        "坏语法": ("def f(:\n", "ast.parse"),
        "丢开关": (LEGACY_PY.replace('"--log-level", default="INFO"', '"--level", default="INFO"'),
                 "--log-level"),
        "改收尾": (LEGACY_PY.replace("sys.exit(0 if f.run() else 1)", "sys.exit(0)"), "sys.exit"),
        "断上报": (LEGACY_PY.replace("report_url", "nowhere_url"), "report_url"),
        "上报不调": (LEGACY_PY.replace('report_url(self.cdp, self.tid, "started", self.log)',
                                    "pass"), "一处都没调"),
    }
    for label, (src, want) in cases.items():
        bad = fix.check_patch(LEGACY_PY, src)
        assert bad, "「%s」这一版居然过了闸" % label
        assert any(want in b for b in bad), (label, bad)


def _hunk_diff(old: str, *, needle: str, new_line: str, context: int = 2) -> str:
    """造一段**行号正确**的 unified diff（把 `needle` 那一行换成 `new_line`）。

    ⚠️ 行号是**算出来的**（`index`），不是手数的 —— 手数那一次正好把这一层的严格校验
    验了一遍（它当场指出「它说原文是 X，实际是 Y」）。
    """
    lines = old.splitlines()
    at = next(i for i, ln in enumerate(lines) if needle in ln)
    lo = max(0, at - context)
    hi = min(len(lines), at + context + 1)
    body = ["--- a/x.py", "+++ b/x.py",
            "@@ -%d,%d +%d,%d @@" % (lo + 1, hi - lo, lo + 1, hi - lo)]
    for i in range(lo, hi):
        if i == at:
            body += ["-" + lines[i], "+" + new_line]
        else:
            body.append(" " + lines[i])
    return "\n".join(body) + "\n"


def test_a_diff_reply_is_applied_strictly_and_a_wrong_one_is_refused():
    """★★ 甲那条路：模型只交 diff，**套用由我们机械做**（严格：对不上就一个字都不改）。

    为什么要这个形状（2026-09-21 量的）：「整份源码进出」对 21916 字节 / 459 行那份
    不成立 —— 三档预算全被思考吃满、`content` 空。
    """
    good = _hunk_diff(LEGACY_PY, needle="MAX_STEPS = 40", new_line="MAX_STEPS = 70")
    applied = fix.source_from_reply(LEGACY_PY, "改好了：\n```diff\n%s```\n" % good)
    assert "MAX_STEPS = 70" in applied
    assert "MAX_STEPS = 40" not in applied
    #: 别的行**一字未动**（套用只动那一处）
    assert applied.replace("MAX_STEPS = 70", "MAX_STEPS = 40") == LEGACY_PY

    #: 删一行 / 加一行也认（同一段 hunk 里）
    lines = LEGACY_PY.splitlines()
    at = next(i for i, ln in enumerate(lines) if "MAX_STEPS = 40" in ln)
    lo, hi = at - 1, at + 2
    drop = ["--- a/x.py", "+++ b/x.py",
            "@@ -%d,%d +%d,%d @@" % (lo + 1, hi - lo, lo + 1, hi - lo - 1)]
    for i in range(lo, hi):
        drop.append(("-" if i == at else " ") + lines[i])
    dropped = fix.apply_diff(LEGACY_PY, "\n".join(drop) + "\n")
    assert "MAX_STEPS = 40" not in dropped, "该删的那一行还在"
    assert len(dropped.splitlines()) == len(lines) - 1, "行数不对：只该少一行"

    #: **对不上** ⇒ 抛（不许模糊贴上 —— 贴错地方不报错才是最贵的坏法）
    wrong = good.replace("-MAX_STEPS = 40", "-MAX_STEPS = 999")
    with pytest.raises(ValueError) as caught:
        fix.apply_diff(LEGACY_PY, wrong)
    assert "对不上" in str(caught.value), str(caught.value)

    #: 一处 diff 都没有 / 一段散文 ⇒ 都不许当补丁
    with pytest.raises(ValueError):
        fix.apply_diff(LEGACY_PY, "我把这一行改了。\n")
    with pytest.raises(ValueError) as caught:
        fix.source_from_reply(LEGACY_PY, "问题多半在等待那一步，你再看看。")
    assert "不是 diff" in str(caught.value), str(caught.value)

    #: 整份源码照收（模型偶尔直接交源码）—— 但**必须**是一份读得通的 py
    assert fix.source_from_reply(LEGACY_PY, "```python\n%s```" % LEGACY_PY) == LEGACY_PY


def test_the_feedback_both_shapes_are_read_the_same_way():
    """★ 回灌的两种形状都要认：`lint` 给字典、我们自己重试给人话字符串。

    （真跑撞出来的：只按字典读 ⇒ 重试那一次当场 `AttributeError` —— 而那是
    **第二次**才炸的形状，桩不看 feedback 就一路没现形。）
    """
    got = fix.violations_for_prompt([{"line": 12, "message": "手拼 JS"},
                                     "这段 diff 对不上：…一个字都没改。", "", None])
    assert got == ["第 12 行 —— 手拼 JS", "这段 diff 对不上：…一个字都没改。"], got
    assert fix.violations_for_prompt(None) == []


def test_an_empty_model_reply_says_why_instead_of_just_being_empty():
    """★ 空回话**不是**「补丁是空的」—— 要把那一次调用的账摆出来。

    【我量的·2026-09-21】真跑一趟修站时撞到的：`finish_reason="length"`、
    `reasoning_tokens = 12000 = max_tokens`、`content` 空（思考把预算吃满）。
    「预算不够」与「模型没给东西」是两件事，处置相反（加预算 vs 换模型/换提示词）。
    """
    empty = [{"content": "", "finish_reason": "length",
              "usage": {"completion_tokens": 12000,
                        "completion_tokens_details": {"reasoning_tokens": 12000}}}]
    with pytest.raises(ValueError) as caught:
        fix.patch_from_rounds(empty, max_tokens=32000)
    said = str(caught.value)
    for want in ("length", "12000", "32000"):
        assert want in said, said
    #: 有回话就原样交出去（围栏那一圈由 `extract_source` 管，不在这儿动）
    assert fix.patch_from_rounds([{"content": "print(1)"}]) == "print(1)"
    #: 一轮都没有（模型那边连一条记录都没留下）也不许静默
    with pytest.raises(ValueError):
        fix.patch_from_rounds([])


def test_extract_source_prefers_the_fence_and_falls_back_to_the_whole_reply():
    """先认围栏；一处都没有才退回整段原话 —— 退回之后照样要过闸（不猜）。"""
    wrapped = "这是改好的：\n```python\nprint(1)\n```\n以上。"
    assert fix.extract_source(wrapped) == "print(1)\n"
    assert fix.extract_source("print(2)\n") == "print(2)\n"
    assert fix.extract_source("") == ""


def test_the_patch_prompt_carries_the_page_evidence_as_its_own_section():
    """★ 那一份「真跑一遍」的证据要单独一段进提示词（它是【我量的】里最值钱的那块）。"""
    said = fix.patch_user(LEGACY_PY, evidence="失败那一行",
                          page_evidence="step 1: nothing actionable (1) []\n屏幕 7s 没变",
                          success_text="thank-you")
    assert "真跑了一遍旧脚本" in said and "nothing actionable" in said, said[-300:]
    #: 没给就不许留一段空标题（空的东西看起来像「给了」）
    assert "真跑了一遍旧脚本" not in fix.patch_user(LEGACY_PY, evidence="x")


def test_the_evidence_say_only_puts_what_was_measured():
    """`evidence_say` **只摆量到的**：退出码 / 上报过哪些步 / 最后停在哪 / 日志尾巴。**纯函数**。"""
    said = selftest.evidence_say({
        "rc": 1, "timed_out": False, "bad_lines": 0, "timeout": 600,
        "trace": [{"step": "started", "url": "https://x/a.html", "ok": None},
                  {"step": "form_filled", "url": "https://x/b.html", "ok": None}],
        "tail": "step 6: nothing actionable (6) []\n屏幕 7s 没变"})
    for want in ("退出码", "1", "form_filled", "https://x/b.html", "nothing actionable", "2 步"):
        assert want in said, (want, said)
    #: 超时 / 起不来 / 一步都没上报 —— 三种都各有说法，不许长成一个样
    assert "超时" in selftest.evidence_say({"timed_out": True, "timeout": 60})
    assert "没起来" in selftest.evidence_say({"rc": None})
    assert "一步都没上报" in selftest.evidence_say({"rc": 0, "trace": []})


def test_the_evidence_carries_what_the_tool_saw_including_what_blocks_the_page():
    """★ 工具自己那份读数（`cdp observe`）要进证据 —— 尤其「**挡着的东西 + 点掉它的地址**」。

    为什么这条承重（2026-09-21 真跑）：那一站的 cookie 横幅盖着整页，脚本**读到了它、没点它**
    （它自己那串写死的选择器一个都不匹配，还被 `except: pass` 吞掉），于是后面每一步的点击
    都被浮层吃掉 —— 而当时那段证据里**一个字都没提「有东西挡着」**。
    """
    said = selftest.evidence_say({
        "rc": 1, "timed_out": False, "bad_lines": 0, "timeout": 600, "trace": [],
        "dom": {"page": {
            "obstructions": [{"kind": "consent-overlay", "text": "We value your privacy",
                              "selector": "div#onetrust",
                              "dismiss_selector": "button#onetrust-accept-btn-handler",
                              "dismiss_selector_unique": True}],
            "actions": [{"selector": "body > main > button:nth-of-type(1)",
                         "text": "Get Results", "stability": "stable"}],
            "fields": [], "honeypots": [], "diagnostics": []}}})
    assert "挡着" in said, said
    assert "button#onetrust-accept-btn-handler" in said, said
    assert "body > main > button:nth-of-type(1)" in said, said
    #: ⚠️ 没拿工具看过（老版 cdp / 拉不到）⇒ **不许**提「挡着」或「没挡着」：
    #: 「读不到」与「没有」是两件事，在这一层混起来，人就会拿「页面没问题」去解释一次失败。
    quiet = selftest.evidence_say({"rc": 1, "trace": [], "dom": {}})
    assert "挡着" not in quiet and "obstructions" not in quiet, quiet


def test_a_blank_tool_reading_is_not_painted_as_nothing_blocking_the_page():
    """★ 空页 ≠ 「页面上没有东西挡着」（2026-09-21 真跑换来的）。

    实测：证据那趟跑完**那一刻**去读，`cdp observe` 读到的是「title 空、`page_text` 0 字、
    `actions` 0 个」（三个读法全空 —— 页面正在跳转/白屏）。把那读成「页面上干净」，
    就等于拿一句看着有底气的话把「不知道」盖掉 —— 这正是这一整条线最贵的那类形状。
    """
    blank = {"title": "", "page_text": "", "actions": [], "fields": [],
             "obstructions": [], "honeypots": []}
    said = selftest.evidence_say({"rc": 1, "trace": [], "dom": {"page": blank}})
    assert "读到的是一张空页" in said, said
    #: ⚠️ 卡的是**那句正常读数的话没出现**（不是卡「没有东西挡着」这几个字：那一句本身
    #:   就是「这**不是**『页面上没有东西挡着』」—— 拿它当判据会把自己卡死，实测踩到）。
    assert "工具自己看那一页（`cdp observe`）：可动作元素" not in said, said
    assert "页面上有东西挡着" not in said, said

    #: 非空页（有正文/有元素）照旧按真读数说
    ok = {"title": "x", "page_text": "hello", "actions": [], "fields": [], "obstructions": [],
          "honeypots": []}
    said2 = selftest.evidence_say({"rc": 1, "trace": [], "dom": {"page": ok}})
    assert "空页" not in said2 and "工具自己看那一页" in said2, said2

    #: `_blank_page` 的三条判据：标题 / 正文 / 元素，任一条有东西就不是空页
    assert selftest._blank_page(blank) is True
    assert selftest._blank_page({"title": "t", "page_text": "", "actions": []}) is False
    assert selftest._blank_page({"title": "", "page_text": "文字", "actions": []}) is False
    assert selftest._blank_page({"title": "", "page_text": "", "actions": [{"selector": "a"}]}) is False
    assert selftest._blank_page(None) is False


def test_a_blank_read_is_retried_after_re_navigating_to_the_entry_url(monkeypatch):
    """★ 读到空页 ⇒ **导航回入口再读一遍** —— 要的是**漏斗第一屏**，不是跑完那一刻的白屏。

    ⚠️ 只在**空页**时才导航：跑成了的那一趟，页面本身就是证据 —— 导航走 = 把证据毁了。
    """
    good = {"title": "Get A Free Quote", "page_text": "Compare Competitive Quotes",
            "actions": [{"selector": "body > main > button:nth-of-type(1)", "text": "Get Results"}],
            "fields": [], "obstructions": [], "honeypots": []}
    blank = {"title": "", "page_text": "", "actions": [], "fields": []}
    seen = {"observe": 0, "navi": []}
    seq = [blank, good]

    def fake_observe(*a, **k):
        seen["observe"] += 1
        return seq.pop(0) if seq else None

    monkeypatch.setattr(selftest, "_cdp_observe", fake_observe)
    monkeypatch.setattr(selftest, "_cdp_eval", lambda *a, **k: None)
    monkeypatch.setattr(selftest, "_navigate",
                        lambda bin_, ws, url, env: seen["navi"].append(url) or None)

    view = selftest._dom_view("/nonexistent.py", "cdp", "ws://h:1/x", {}, "https://entry.test/")
    assert seen["observe"] == 2, seen
    assert seen["navi"] == ["https://entry.test/"], seen
    assert view["page"]["title"] == "Get A Free Quote", view["page"]
    assert "空页" in view["page_retried"], view["page_retried"]

    #: 一读就是好的 ⇒ **不许**多导航一次（把跑完的现场留着）
    seen["observe"], seen["navi"], seq[:] = 0, [], [good]
    view2 = selftest._dom_view("/nonexistent.py", "cdp", "ws://h:1/x", {}, "https://entry.test/")
    assert seen["observe"] == 1 and seen["navi"] == [], seen
    assert "page_retried" not in view2, view2


def test_the_patch_prompt_teaches_the_tool_habits_that_already_exist():
    """★ 改稿提示词要把「这套工具本身的用法」摆到模型面前（用户 2026-09-21 指的方向）。

    为什么：那一站改稿时，提示词里只有「证据 / 判据 / 人的话 / 上一版为什么打回 / 旧源码」——
    关于「这一页上怎么等元素、怎么清弹层、怎么做成了才算做成」**一个字都没有**，
    于是模型自己造（写死一串同意弹层选择器 + `except: pass`）。

    ⚠️ 只说**生产那个 cdp 真有的**那几只手：`observe` / `diff` 它没有（`diff` 要 observe 的快照）
    —— 在提示词里承诺它们，等于让模型写一份生产跑不起来的稿。
    """
    said = fix.patch_user(LEGACY_PY)
    for want in ("人插的话", "照他给的那个写", "同意弹层", "cookie|consent|gdpr|privacy",
                 "下一步的关键元素", "最多 3 次", "如实报失败", "scroll", "covered_by",
                 "self.cdp",
                 #: ★ 步骤表那一段（2026-09-21 用户：「假如我固定描述步骤会不会好点」）
                 "步骤表", "照那张表发号施令", "等待区间", "第 N 步", "self._rpt"):
        assert want in said, (want, said[:300])
    assert "cdp observe" not in said and "cdp diff" not in said, "生产那个 cdp 没有这两样"


def test_the_run_uses_the_evidence_it_fetched_and_says_when_there_is_none(tmp_path):
    """★ 出稿之前那一趟拿证据：接上了就走（且那段证据进状态 + 进闸），没接就**说清**。"""
    calls = []

    def fake_evidence(**kw):
        calls.append(kw)
        return {"rc": 1, "timed_out": False, "bad_lines": 0, "timeout": 600,
                "trace": [{"step": "started", "url": "https://x/a", "ok": None}],
                "tail": "step 6: nothing actionable (6) []",
                "dom": {"reader": {"buttons": [], "fields": [], "progress": ""},
                        "clickable": {"url": "https://x/a",
                                      "cands": [{"tag": "A", "txt": "Get A Free Quote",
                                                 "cls": "btn", "href": "https://q/x", "box": [120, 40]}]}}}

    brief, deps, rec, _ = _legacy_fix(tmp_path, evidence_run=fake_evidence)
    deps.evidence_run = fake_evidence
    app, cfg, _ = _build(deps=deps)
    payloads, out = _drive(app, cfg, brief)

    assert out.get("end_reason") == "delivered", out.get("end_note")
    assert calls and calls[0]["site"] == SITE, calls
    assert "nothing actionable" in (out.get("fix_evidence") or ""), out.get("fix_evidence")
    facts = next((p["facts"] for p in payloads if p["step"] == "draft"), {})
    assert "nothing actionable" in str(facts.get("给模型的证据（旧脚本停在哪儿）")), facts.keys()
    #: ★ **现读那一页的原始读数要留在状态里**（面板那一栏就靠它）——
    #: 「页面上有、它却没收到」那一条是运营最需要的那一眼，不许只煮成一段正文。
    page = out.get("fix_page_view") or {}
    assert [c["txt"] for c in (page.get("clickable") or {}).get("cands", [])] == ["Get A Free Quote"], page
    assert (page.get("reader") or {}).get("buttons") == [], page

    #: 没接那根线 ⇒ 状态里留一句**说清**的话（不许让「没有证据」看起来像「有证据」）
    brief2, deps2, _rec2, _ = _legacy_fix(tmp_path / "b")
    deps2.evidence_run = None
    app2, cfg2, _ = _build(deps=deps2)
    _, out2 = _drive(app2, cfg2, brief2)
    assert "没接" in (out2.get("fix_evidence") or ""), out2.get("fix_evidence")


def test_the_old_script_passing_on_its_own_stops_the_trip_without_touching_it(tmp_path):
    """★★ 证据那一趟**旧脚本自己就走通了** ⇒ 就此停住（用户 2026-09-21 的裁断）。

    为什么这条承重：手上的证据只说「它跑通了」，**没有一个可复现的失败条件**。
    拿它去出补丁 = 拿一个跑得通的脚本去赌，而改坏了**没有任何地方会响** ——
    它照样跑，只是偶尔挂。运营 2026-09-21 看到的正是这个形状（`callyourdate` 实测：
    旧脚本自己退出码 0、自己报 `success`，而那个站今天「在失败」）。
    """
    calls = []

    def fake_evidence(**kw):
        calls.append(kw)
        return {"rc": 0, "timed_out": False, "bad_lines": 0, "timeout": 600,
                "trace": [{"step": "success", "url": "https://x/wizard", "ok": True}],
                "tail": "SUCCESS: https://x/wizard?referrer=..."}

    brief, deps, rec, sources = _legacy_fix(tmp_path, evidence_run=fake_evidence)
    deps.evidence_run = fake_evidence
    app, cfg, _ = _build(deps=deps)
    payloads, out = _drive(app, cfg, brief)

    assert out.get("end_reason") == "fix_old_passes", (out.get("end_reason"), out.get("end_note"))
    assert out.get("fix_evidence_ok") is True, out.get("fix_evidence_ok")
    assert "没有可修" in (out.get("end_note") or ""), out.get("end_note")
    assert calls and calls[0]["entry_url"] == URL, calls
    #: **一步都不往下走**：没叫模型、没开自测、没落盘（那个站一个字节都没被动过）
    assert sources == [], "旧脚本自己就走通了，不该再叫模型出补丁"
    assert rec.selftest == [], rec.selftest
    assert [p["step"] for p in payloads] == ["explore"], [p["step"] for p in payloads]
    assert "退出码 **0**" in (out.get("fix_evidence") or ""), out.get("fix_evidence")
    assert "它自己就走通了" in (out.get("end_note") or ""), out.get("end_note")
    assert not (tmp_path / "forms" / "sites" / ("%s.py" % SITE)).exists()

    #: 判据本身（三态）：**导航没成**那一趟的退出码不算数 —— 它是在别的页面上跑的，
    #: 读成「没走通」会去改一个可能根本没坏的脚本。
    for ev, want in (({"rc": 0, "navi": "导航没成（退出码 1）"}, None),
                     ({"rc": 1, "navi": None}, False),
                     ({"rc": 0, "navi": None, "timed_out": True}, False),
                     ({"rc": None}, None),
                     ({"rc": 0}, True)):
        assert graph._old_ran_ok(ev) is want, (ev, graph._old_ran_ok(ev))


def test_a_real_evidence_run_returns_rc_trace_and_the_script_own_log(tmp_path, monkeypatch):
    """`selftest.run_once`：跑一遍产物 → 退出码 + 它上报过的步 + 它自己打的日志尾巴。"""
    py, _ = _live_legacy(tmp_path, monkeypatch)
    monkeypatch.setenv("STUB_RC", "1")
    ev = selftest.run_once(str(py), "ws://127.0.0.1:9222/devtools/browser/x",
                           str(tmp_path / "form.json"), SITE, legacy=True,
                           run_dir=tmp_path / "ev", cdp_bin="/bin/true", timeout=60,
                           entry_url="https://example.test/entry")
    assert ev["rc"] == 1 and ev["timed_out"] is False, ev
    assert [ln["step"] for ln in ev["trace"]] == ["started"], ev["trace"]
    assert "--trace" not in ev["cmd"] and "--no-report" not in ev["cmd"], ev["cmd"]
    assert "退出码" in selftest.evidence_say(ev)
    #: ⚠️ **导航那一步**：不给入口网址的话，脚本是在浏览器自己的控制台页上跑（实测过）——
    #: 所以这一条钉的是「跑了导航」与「没导航时证据里说清」
    assert ev["navi"] is None, ev["navi"]          # `/bin/true` 永远成功
    assert ev["entry_url"] == "https://example.test/entry"
    no_navi = selftest.run_once(str(py), "ws://127.0.0.1:9222/devtools/browser/x",
                                str(tmp_path / "form.json"), SITE, legacy=True,
                                run_dir=tmp_path / "ev2", cdp_bin="/bin/true", timeout=60)
    assert no_navi["navi"], "没给入口网址时**必须**说一句"
    assert "没导航" in selftest.evidence_say(no_navi), selftest.evidence_say(no_navi)


def test_the_patch_prompt_carries_the_evidence_and_the_people_words_verbatim():
    """外部世界给的东西**逐字**进提示词 —— 这一层概括一次，模型就再也看不到原文了。"""
    said = fix.patch_user(LEGACY_PY, evidence="这一趟账上最后一行报的是「max_steps」",
                          success_text="Thank you", violations=["第 12 行 —— 手拼 JS"],
                          hints=["别动成功判据"])
    for want in ("max_steps", "Thank you", "第 12 行 —— 手拼 JS", "别动成功判据",
                 "SUCCESS_URL_MARKERS"):
        assert want in said, want
    #: 系统提示是**另一条**消息（不是在 user 里）—— 它管的是「不许破坏生产契约」那五条。
    for want in ("--ws-url", "--correlation-id", "sys.exit(0 if", "report_url"):
        assert want in fix.PATCH_SYSTEM, want


# ── ② 图：老写法那条路 ───────────────────────────────────────────

def _legacy_fix(tmp_path, *, patch=None, wrap=None, **over):
    """把一份老写法的 py 摆好，返回 `(brief, deps, rec)`。"""
    out = tmp_path / "forms" / "sites"
    out.mkdir(parents=True, exist_ok=True)
    staged = out / ("%s.before.py" % SITE)
    staged.write_text(LEGACY_PY, encoding="utf-8")
    brief = _brief(tmp_path, mode="fix", fix_py=str(staged))
    #: ⚠️ 窗口与表单数据照旧要给：修站**不探索**（不开窗口探路），
    #: 但**自测**那条路要在真窗口上跑产物 —— 两件事，别混。
    assert brief["ws_url"] and brief["form_file"]

    calls = []

    def patch_source(old_src, feedback):
        calls.append({"old": old_src, "feedback": feedback})
        src = patch(calls[-1]) if patch else LEGACY_PY.replace("MAX_STEPS = 40", "MAX_STEPS = 60")
        return wrap(src) if wrap else "```python\n%s\n```" % src

    deps, rec = _deps(patch_source=patch_source, **over)
    deps.patch_source = patch_source
    return brief, deps, rec, calls


def test_a_legacy_script_becomes_a_patch_run_instead_of_being_refused(tmp_path):
    """★ 老写法不再在 intake 判死：它走**补丁**那条路，而且**不探索**（不开浏览器）。"""
    brief, deps, rec, calls = _legacy_fix(tmp_path)
    app, cfg, _ = _build(deps=deps)
    payloads, out = _drive(app, cfg, brief)

    assert out.get("end_reason") == "delivered", out.get("end_note")
    assert out["fix_style"] == "legacy", out.get("fix_style")
    assert out["mode"] == "fix"
    # 修站那条路的判据：**不重新探索** ⇒ deps.explore 一次都没被调
    assert rec.explore == [], rec.explore
    assert [p["step"] for p in payloads] == ["explore", "draft", "lint", "selftest", "deliver"], payloads
    # 那一版补丁是**模型那双手**给的，且拿到的是旧源码原文
    assert len(calls) == 1
    assert calls[0]["old"] == LEGACY_PY
    # 交付的那一份就是补丁那一版（不是旧源码）
    delivered = (tmp_path / "forms" / "sites" / ("%s.py" % SITE)).read_text(encoding="utf-8")
    assert "MAX_STEPS = 60" in delivered
    # ⚠️ 自测那一步必须**知道**这是老写法（argv 要换一套调法）
    assert rec.selftest[0].get("legacy") is True, rec.selftest[0]
    #: ⚠️ 自测那一趟**必须先导航到那个站** —— 不导航就是在 Bit 自己的控制台页上跑（实测过）
    assert rec.selftest[0].get("start_url") == URL, rec.selftest[0]


def test_the_gate_says_which_copy_the_draft_came_from(tmp_path):
    """★ 闸上「**底稿是哪一份**」只许照 `state.fix_base` 说（2026-09-21 真闸上印过一句假话）。

    为什么这条承重：停用那些站（`allow_disabled`）拿的是 `?type=debug` 那一份、
    **不是**生产在跑的那一份；而原来那句开头**写死**成「拿的是**线上正在跑的那一份 py**」
    ⇒ 运营在屏幕上读到的是「这份补丁是照线上那份改的」，而它其实是从停用那份改的 ——
    这两件事的后果不一样（一个是改生产，一个是改一份可能早已作废的稿）。

    ⚠️ 反面一起卡住：**算不出来时不许替它猜「线上的那一份」**（那是拿一句看着有底气的话
    把「不知道」盖掉）。判据句是「哪一份：没说」。
    """
    STAMP = ("**停用那一份**（`type=debug` 读回来的，后端 `status: 0`，sha256 deadbeef1234 —— "
             "⚠️ 这一份**可能不是生产在跑的那一份**）")

    brief, deps, rec, _ = _legacy_fix(tmp_path)
    brief = dict(brief, fix_base=STAMP)
    app, cfg, _ = _build(deps=deps)
    payloads, out = _drive(app, cfg, brief)

    gate = payloads[0]
    assert gate["step"] == "explore", gate
    assert "停用那一份" in gate["say"], gate["say"]
    assert "线上正在跑的那一份" not in gate["say"], gate["say"]
    assert gate["facts"].get("底稿来源") == STAMP, gate["facts"].get("底稿来源")
    assert out.get("end_reason") == "delivered", out.get("end_note")

    #: 没说 ⇒ **明说没说**，不许猜成「线上那一份」
    (tmp_path / "b").mkdir(exist_ok=True)
    brief2, deps2, _rec2, _c2 = _legacy_fix(tmp_path / "b")
    app2, cfg2, _ = _build(deps=deps2)
    payloads2, _out2 = _drive(app2, cfg2, brief2)
    assert "哪一份：没说" in payloads2[0]["say"], payloads2[0]["say"]
    assert "线上正在跑的那一份" not in payloads2[0]["say"], payloads2[0]["say"]
    assert payloads2[0]["facts"].get("底稿来源") == "（没说）", payloads2[0]["facts"]


def _replace_block(old: str, *, needle: str, new_lines: list) -> str:
    """造一块 `<<<REPLACE a-b 锚点`（行号与锚点都是从旧源码**取**的，不是抄的）。"""
    lines = old.splitlines()
    at = next(i for i, ln in enumerate(lines) if needle in ln) + 1
    body = "\n".join(new_lines)
    #: ⚠️ 空 `new_lines` = **删掉这一段**（头下一行就是 `>>>`）——
    #: 中间空一行的话，那是一个**空行**（替换成空行），不是删。
    return "<<<REPLACE %d-%d %s\n%s%s>>>\n" % (at, at, lines[at - 1].strip()[:16],
                                               body, "\n" if body else "")


def test_a_line_range_reply_is_applied_by_number_and_checked_by_its_anchor():
    """★ 行号 + 短锚点（用户 2026-09-21 挑的那根杠杆）：**不用模型抄原文**。

    为什么换这个形状：让它抄 diff 的上下文行，它时不时抄错（抄错就整趟白跑）。
    这里只让它说**行号** + 那一段第一行开头的**几个字**，原文我们自己取。
    """
    block = _replace_block(LEGACY_PY, needle="MAX_STEPS = 40", new_lines=["MAX_STEPS = 90"])
    applied = fix.source_from_reply(LEGACY_PY, "改好了：\n```\n%s```" % block)
    assert "MAX_STEPS = 90" in applied
    assert applied.replace("MAX_STEPS = 90", "MAX_STEPS = 40") == LEGACY_PY, "别的行被动了"

    #: 删（把这一段换成空）/ 多块（从后往前套，行号不互相挪）
    drop = _replace_block(LEGACY_PY, needle="MAX_STEPS = 40", new_lines=[])
    assert len(fix.apply_edits(LEGACY_PY, drop).splitlines()) == len(LEGACY_PY.splitlines()) - 1
    two = _replace_block(LEGACY_PY, needle="#!/usr/bin/env python3", new_lines=["#!/usr/bin/env python3", "# 注"]) \
        + block
    got = fix.apply_edits(LEGACY_PY, two)
    assert "# 注" in got and "MAX_STEPS = 90" in got, "多块没套对"

    #: 三条闸逐条：锚点对不上 / 行号越界 / 没给锚点 —— **一个字都不改**
    cases = {
        "锚点对不上": _replace_block(LEGACY_PY, needle="MAX_STEPS = 40", new_lines=["x"]).replace(
            "MAX_STEPS = 40", "别的什么东西", 1),
        "行号越界": "<<<REPLACE 999999-999999 随便\nx\n>>>\n",
        "没给锚点": "<<<REPLACE 1-1\nx\n>>>\n",
    }
    for label, bad in cases.items():
        with pytest.raises(ValueError) as caught:
            fix.apply_edits(LEGACY_PY, bad)
        assert "一个字都没改" in str(caught.value) or "不套" in str(caught.value), (label, caught.value)


def test_the_gates_show_what_the_patch_changed(tmp_path):
    """★ 人要在**放它去跑真页面**之前看见它动了什么 —— 那两格的 diff 就是为这件事。

    （2026-09-21 真跑时看见的缺口：老写法那一版的闸上只有「第几版 / 几行」，
    按「继续」是按在一句概括上点的。）
    """
    brief, deps, rec, _ = _legacy_fix(tmp_path)
    app, cfg, _ = _build(deps=deps)
    payloads, out = _drive(app, cfg, brief)

    assert out.get("end_reason") == "delivered", out.get("end_note")
    diff = out.get("src_diff") or ""
    assert "MAX_STEPS" in diff and "-" in diff and "+" in diff, diff[:200]
    for gate in ("selftest", "deliver"):
        facts = next((p["facts"] for p in payloads if p["step"] == gate), {})
        assert facts.get("这一版改了什么") == diff, (gate, facts.keys())
    #: 一处都没改时**也**要说清（不许留白让上一版的 diff 冒充这一版）
    assert graph._patch_diff("a\n", "a\n").startswith("（这一版与旧脚本")
    """★ 甲那条路的**接线**：模型交 diff ⇒ 我们机械套上 ⇒ 图表里那一版就是套好的。

    （`test_a_diff_reply_is_applied_strictly_and_a_wrong_one_is_refused` 量的是套用本身；
    这一条量的是「图上跑的那条路真的用了它」。）
    """
    diff = _hunk_diff(LEGACY_PY, needle="MAX_STEPS = 40", new_line="MAX_STEPS = 70")
    brief, deps, rec, calls = _legacy_fix(tmp_path, patch=lambda c: diff,
                                          wrap=lambda text: "```diff\n%s```" % text)
    app, cfg, _ = _build(deps=deps)
    _, out = _drive(app, cfg, brief)

    assert out.get("end_reason") == "delivered", out.get("end_note")
    delivered = (tmp_path / "forms" / "sites" / ("%s.py" % SITE)).read_text(encoding="utf-8")
    assert "MAX_STEPS = 70" in delivered, delivered[:200]
    assert delivered.replace("MAX_STEPS = 70", "MAX_STEPS = 40") == LEGACY_PY, "除了那一处，别的行被动了"


def test_a_patch_that_did_not_apply_is_retried_once_with_the_reason_fed_back(tmp_path):
    """★ 套不上就**把原因回灌再补一次**（不是停下等人）—— 实测非思考模型答一次只要 4 秒。

    【我量的·2026-09-21】它第一次会把上下文那一行抄错（`括号`/结尾少一截），
    套用那层严格拒掉并说清「它说原文是 X、实际是 Y」；那句话正好是下一轮的输入。
    """
    good = _hunk_diff(LEGACY_PY, needle="MAX_STEPS = 40", new_line="MAX_STEPS = 70")
    bad = good.replace("-MAX_STEPS = 40", "-MAX_STEPS = 999")
    replies = [bad, good]

    brief, deps, rec, calls = _legacy_fix(
        tmp_path, patch=lambda c: replies.pop(0), wrap=lambda t: "```diff\n%s```" % t)
    app, cfg, _ = _build(deps=deps)
    _, out = _drive(app, cfg, brief)

    assert out.get("end_reason") == "delivered", out.get("end_note")
    assert len(calls) == 2, "第一次就套上了？那这一条量不到「回灌再补」"
    #: 第二次那一次，模型**看得到**第一次为什么没成
    fed = calls[1]["feedback"].get("violations") or []
    assert fed and "对不上" in " ".join(fed), fed
    delivered = (tmp_path / "forms" / "sites" / ("%s.py" % SITE)).read_text(encoding="utf-8")
    assert "MAX_STEPS = 70" in delivered


def test_two_bad_patches_stop_and_say_why(tmp_path):
    """两次都套不上/过不了闸 ⇒ 停下（**不许**无限重试，也不许静默）。"""
    bad = _hunk_diff(LEGACY_PY, needle="MAX_STEPS = 40", new_line="MAX_STEPS = 70") \
        .replace("-MAX_STEPS = 40", "-MAX_STEPS = 999")
    brief, deps, rec, calls = _legacy_fix(tmp_path, patch=lambda c: bad,
                                          wrap=lambda t: "```diff\n%s```" % t)
    app, cfg, _ = _build(deps=deps)
    _, out = _drive(app, cfg, brief)

    assert out.get("end_reason") == "draft_failed", out.get("end_note")
    assert "对不上" in (out.get("end_note") or ""), out.get("end_note")
    assert len(calls) == graph.PATCH_ATTEMPTS, "上限就是 PATCH_ATTEMPTS —— 不许一直试"
    assert graph.PATCH_ATTEMPTS >= 2, "至少要能回灌一次（第一次没过就把原因给它）"
    assert rec.selftest == []
    """★ 没接「改稿那双手」⇒ **停下并点名**，不许写成「这份 py 修不了」（那是两件事）。"""
    brief, deps, rec, _ = _legacy_fix(tmp_path)
    deps.patch_source = None
    app, cfg, _ = _build(deps=deps)
    _, out = _drive(app, cfg, brief)

    assert out.get("end_reason") == "draft_failed", out.get("end_note")
    note = out.get("end_note") or ""
    assert "patch_source" in note, note
    assert "没接" in note, note
    assert rec.selftest == [], "没出稿就不该走到自测"


def test_a_patch_that_breaks_the_cli_contract_never_reaches_the_next_step(tmp_path):
    """★ 补丁把生产要的开关弄丢 ⇒ 闸拦住：**不落盘、不往下走**，原因逐条摆出来。"""
    broken = LEGACY_PY.replace('"--log-level", default="INFO"', '"--level", default="INFO"')
    brief, deps, rec, _ = _legacy_fix(tmp_path, patch=lambda c: broken)
    app, cfg, _ = _build(deps=deps)
    _, out = _drive(app, cfg, brief)

    assert out.get("end_reason") == "draft_failed", out.get("end_note")
    assert "--log-level" in (out.get("end_note") or ""), out.get("end_note")
    assert rec.selftest == [], "没过闸的稿子不该进自测"
    assert not (tmp_path / "forms" / "sites" / ("%s.candidate.py" % SITE)).exists()
    assert not (tmp_path / "forms" / "sites" / ("%s.py" % SITE)).exists()


# ── ③ 自测：老产物的证据从运行时进 ────────────────────────────────

#: 一份**真能跑**的老写法产物：只认生产那四个开关（+ `--task-id`），
#: 进度走 `common.report_url`。自测的 argv 一个字节都不许给它不认的开关。
LIVE_LEGACY = '''#!/usr/bin/env python3
"""Site script for example.test survey."""
import argparse, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import report_url


class FakeCdp:
    def get_page_info(self):
        return {"url": "https://example.test/step-1"}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--ws-url", required=True)
    p.add_argument("--form-file", required=True)
    p.add_argument("--correlation-id", required=True)
    p.add_argument("--log-level", default="INFO")
    p.add_argument("--task-id", default="")
    a = p.parse_args()
    log_path = os.environ.get("SITEFORGE_ARGV_LOG")
    if log_path:
        with open(log_path, "a", encoding="utf-8") as fp:
            fp.write(json.dumps(sys.argv) + "\\n")
    report_url(FakeCdp(), a.task_id, "started", None)
    sys.exit(int(os.environ.get("STUB_RC", "0")))


if __name__ == "__main__":
    main()
'''


def _live_legacy(tmp_path, monkeypatch):
    """把那份能跑的老写法产物摆成 `<root>/forms/sites/x.py`（旁边要有 `common.py`）。"""
    forms = tmp_path / "forms"
    (forms / "sites").mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "forms" / "common.py", forms / "common.py")
    py = forms / "sites" / "example.test.py"
    py.write_text(LIVE_LEGACY, encoding="utf-8")
    argv_log = tmp_path / "argv.jsonl"
    monkeypatch.setenv("SITEFORGE_ARGV_LOG", str(argv_log))
    monkeypatch.setenv("SITEFORGE_CDP_BIN", shutil.which("true") or "/bin/true")
    monkeypatch.delenv("SCREENSHOT_API_URL", raising=False)
    return py, argv_log


def test_a_legacy_artifact_is_run_without_the_debug_switches(tmp_path, monkeypatch):
    """★★ 老产物那一遍：argv **不给** `--trace`/`--no-report`/`--delay`（它们不认，
    硬传就是 argparse 报错），证据由运行时写进 `SITEFORGE_TRACE` —— 判据一个字不改。"""
    py, argv_log = _live_legacy(tmp_path, monkeypatch)
    report = selftest.run(str(py), "ws://127.0.0.1:9222/devtools/browser/x",
                          str(tmp_path / "form.json"), SITE, legacy=True,
                          run_dir=tmp_path / "runs", cdp_bin="/bin/true",
                          timeout=60)

    argv = json.loads(argv_log.read_text(encoding="utf-8").splitlines()[0])
    for flag in ("--trace", "--no-report", "--delay"):
        assert flag not in argv, "给老产物传了它不认的开关（会 argparse 报错）：%s" % argv
    for flag in ("--ws-url", "--form-file", "--correlation-id", "--log-level"):
        assert flag in argv, argv

    baseline = report.runs[0]
    assert baseline.status == "passed", baseline.note
    assert report.passed is True, report.summary()
    # 证据是**运行时**写的那一行（不是产物自己写的 trace）
    lines = [json.loads(ln) for ln in
             pathlib.Path(baseline.trace_path).read_text(encoding="utf-8").splitlines()]
    assert [ln["step"] for ln in lines] == ["started"], lines
    assert lines[0]["ok"] is None, "老写法判不出「这一步成没成」，不许替它写成 True"
    # ⚠️ 弱一档的那句话必须跟着结论上屏
    assert "老写法" in baseline.note, baseline.note
    assert "定位不到卡在第几步" in baseline.note, baseline.note


def test_the_delay_pass_is_not_needed_for_a_legacy_artifact(tmp_path, monkeypatch):
    """放慢那一遍跑不了（产物没有 `--delay`）⇒ 记 `not_needed` 并说清哪一类**没验到**。

    为什么要造一个「第一遍就挂」的产物：R-84 之下第一遍过了后面几遍**根本不到**
    （那是另一条正确性），这里要验的是**老写法**那一支，所以先让它挂。
    """
    py, _ = _live_legacy(tmp_path, monkeypatch)
    monkeypatch.setenv("STUB_RC", "1")
    report = selftest.run(str(py), "ws://127.0.0.1:9222/devtools/browser/x",
                          str(tmp_path / "form.json"), SITE, legacy=True,
                          run_dir=tmp_path / "runs", cdp_bin="/bin/true", timeout=60)

    delay = [r for r in report.runs if r.name == "delay"]
    assert delay and delay[0].status == selftest.STATUS_NOT_NEEDED, [r.as_dict() for r in report.runs]
    assert "--delay" in delay[0].note, delay[0].note
    assert "没验到" in delay[0].note, delay[0].note
    assert report.passed is False


# ── ③ 运行时那两个旋钮（证据线与「不上报」）───────────────────────

def test_the_runtime_writes_the_trace_line_and_can_stop_reporting(tmp_path, monkeypatch):
    """`forms/common.py` 的那两个环境旋钮：**没设 = 生产那条路一个字不变**。"""
    trace = tmp_path / "t.jsonl"
    monkeypatch.setenv("SITEFORGE_TRACE", str(trace))
    monkeypatch.setenv("SITEFORGE_NO_REPORT", "1")
    module = runtime.load()

    class FakeCdp:
        def get_page_info(self):
            return {"url": "https://example.test/a"}

    #: `SITEFORGE_NO_REPORT` 设了 ⇒ **一个字节都不往生产发**，直接回 True
    #: （网线那一侧不许动：这里连 urlopen 都没被碰到，因为 `return` 排在它前面）
    assert module.report_url(FakeCdp(), "t-1", "step-a", None) is True
    rows = [json.loads(ln) for ln in trace.read_text(encoding="utf-8").splitlines()]
    assert rows == [{"step": "step-a", "ok": None,
                     "ok_why": "老写法的 report_url 只说明「这一步上报过」，成没成要退出码说了算",
                     "url": "https://example.test/a", "note": ""}], rows

    #: 没设 `SITEFORGE_TRACE` ⇒ 一行都不许写（生产那条路：这个旋钮等于不存在）
    trace.unlink()
    monkeypatch.delenv("SITEFORGE_TRACE")
    module._trace_step("step-b", "https://example.test/b")
    assert not trace.exists()
