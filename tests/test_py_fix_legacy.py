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

from agent import fix, runtime, selftest  # noqa: E402
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

def _legacy_fix(tmp_path, *, patch=None, **over):
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
        return "```python\n%s\n```" % src

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


def test_without_the_patch_hand_the_run_stops_and_names_the_missing_wire(tmp_path):
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
