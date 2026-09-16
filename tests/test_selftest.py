"""Task 6：扰动自测（`agent/selftest.py`）的契约测试（规格 §10）。

**为什么是「扰动序列」而不是「同环境跑三遍」**（规格 §10 原话）：
同一环境连跑三遍容易**假通过** —— 它证明的是「同一个条件下能重复」，
而生产失败几乎都来自条件变化。所以五遍各有各的扰动，**每遍淘汰的是不同类失败**。

**这里最承重的一条是诚实**：任一遍挂 → `passed=False`，且说得清**是哪一遍、卡在第几步**。
计划 Task 6 Step 1 的 ⚠️ 原话是「不许把『某遍挂』吞成『部分通过』」。
所以本文件里的断言都对着**报告本身**（不是对着日志），报告里那句人话也一起钉住
（D16：给非技术人员看的是人话）。变异验证见报告：把判据换成「跑过多遍就算部分通过」
（`any(r.ok)`）之后，下面这些测试必须红。

**桩 subprocess**（计划 Step 1 的要求）：不起真浏览器、不起真 cdp。
桩替 `subprocess.run` 应答两类命令 —— 起产物（`python3 <py> …`）与 cdp 命令；
产物那一路按剧本把 **trace（JSON Lines）真写出来**，因为「卡在第几步」是从 trace 里读的
（`ok == false` 的第一行），不写出来就等于那条路径没验到。
"""

from __future__ import annotations

import http.server
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import selftest, template  # noqa: E402

REFERENCE = ROOT / "fixtures" / "reference_site.py"

WS = "ws://127.0.0.1:9222/devtools/browser/abc"
SITE = "example-funnel"
ENTRY = "https://example.test/start"

#: 五遍的名字与顺序（报告里认的就是这五个稳定键）
RUN_NAMES = ("baseline", "rerun", "delay", "viewport", "country")


# ── 桩 subprocess ──────────────────────────────────────────────────

def _flag(cmd, name):
    """取 `--flag value` 的 value（没有就是 None）。"""
    for i, arg in enumerate(cmd):
        if arg == name and i + 1 < len(cmd):
            return cmd[i + 1]
    return None


def _trace_lines(oks, notes=None):
    """产物 trace 的一行一步（键与 `agent/template.py` 的 `_trace` 对齐）。"""
    return [
        {
            "step": i,
            "action": "click",
            "target": "点「Go」",
            "selector_used": "#go",
            "fallback_level": 0,
            "ok": ok,
            "progress": None,
            "progress_why": "这个 cdp 不会 diff",
            "url": "https://example.test/step-%d" % i,
            "page_sig": "…",
            "shot_before": None,
            "shot_after": None,
            "shots_why": None,
            "note": (notes or {}).get(i, "第 %d 步：点「Go」" % i),
        }
        for i, ok in enumerate(oks, start=1)
    ]


class _Script:
    """一遍产物的剧本。默认：跑通（退出码 0 + 全 ok 的 trace）。"""

    def __init__(self, rc=0, oks=(True, True, True), notes=None, trace=True,
                 timeout=False, stderr="", stdout=""):
        self.rc = rc
        self.oks = tuple(oks)
        self.notes = notes or {}
        self.trace = trace
        self.timeout = timeout
        self.stderr = stderr
        self.stdout = stdout

    def __call__(self, cmd):
        if self.timeout:
            raise subprocess.TimeoutExpired(cmd, 600)
        path = _flag(cmd, "--trace")
        if self.trace and path:
            with open(path, "a", encoding="utf-8") as fp:
                for line in _trace_lines(self.oks, self.notes):
                    fp.write(json.dumps(line, ensure_ascii=False) + "\n")
        return subprocess.CompletedProcess(cmd, self.rc, self.stdout, self.stderr)


class _Stub:
    """`subprocess.run` 的替身。

    - `python3 <py> …` = 起产物：**按顺序**领一份剧本（剧本领完了就报错 ——
      「多跑了一遍」或「少跑了一遍」都要现形，不能被静默兜住）；
    - `<cdp> …` = cdp 命令（`navi` 那类）：默认成功；`navi_fails` 可以让它失败；
    - 别的命令一律报错：桩不该替别的调用方做主。
    """

    def __init__(self, py_path, cdp_bin, scripts, navi_fails=False):
        self.py = str(py_path)
        self.cdp = str(cdp_bin)
        self.scripts = list(scripts)
        self.navi_fails = navi_fails
        self.calls = []          # [(kind, cmd, env)]

    @property
    def artifact_calls(self):
        return [cmd for kind, cmd, _ in self.calls if kind == "artifact"]

    @property
    def cdp_calls(self):
        return [cmd for kind, cmd, _ in self.calls if kind == "cdp"]

    @property
    def artifact_envs(self):
        return [env for kind, _, env in self.calls if kind == "artifact"]

    def __call__(self, cmd, **kw):
        cmd = [str(c) for c in cmd]
        env = kw.get("env")
        if cmd[:1] == ["python3"] and cmd[1:2] == [self.py]:
            if not self.scripts:
                raise AssertionError("产物被多跑了一遍：剧本已经领完了（%r）" % (cmd,))
            self.calls.append(("artifact", cmd, env))
            return self.scripts.pop(0)(cmd)
        if cmd[:1] == [self.cdp]:
            self.calls.append(("cdp", cmd, env))
            rc = 1 if (self.navi_fails and cmd[1:2] == ["navi"]) else 0
            return subprocess.CompletedProcess(cmd, rc, "{}", "" if rc == 0 else "boom")
        raise AssertionError("桩 subprocess 收到没人认领的命令：%r" % (cmd,))


def _scripts(**by_run):
    """五遍的剧本表；`by_run` 里点名的那几遍换成别的剧本。"""
    out = []
    for name in RUN_NAMES:
        out.append(by_run.get(name, _Script()))
    return out


@pytest.fixture
def env(tmp_path, monkeypatch):
    """一个自足的现场：产物（真参考产物的一份拷贝）+ form 文件 + 一个假 cdp 二进制。"""
    py = tmp_path / "forms" / "sites" / ("%s.py" % SITE)
    py.parent.mkdir(parents=True)
    py.write_text(REFERENCE.read_text(encoding="utf-8"), encoding="utf-8")
    form = tmp_path / "form.json"
    form.write_text(json.dumps({"email": "sam@example.test"}), encoding="utf-8")
    cdp = tmp_path / "cdp"
    cdp.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    cdp.chmod(0o755)

    holder = {}

    def install(scripts, navi_fails=False):
        stub = _Stub(py, cdp, scripts, navi_fails=navi_fails)
        monkeypatch.setattr(subprocess, "run", stub)
        holder["stub"] = stub
        return stub

    holder.update(py=py, form=str(form), cdp=str(cdp), install=install, dir=tmp_path / "traces")
    return holder


def _run(env, scripts, **kw):
    env["install"](scripts, navi_fails=kw.pop("navi_fails", False))
    kw.setdefault("run_dir", env["dir"])
    kw.setdefault("cdp_bin", env["cdp"])
    return selftest.run(str(env["py"]), WS, env["form"], SITE, **kw)


# ── 一遍都不能挂：passed 的判据 ────────────────────────────────────

def test_all_five_runs_pass_only_when_both_callbacks_are_given(env):
    """五遍都过 → `passed=True`；五遍的名字、顺序、trace 都落在报告里。

    第 4/5 遍要调用方在窗口/代理那一层动手（R-5），所以「五遍都过」这个前提
    只有在两个回调都给了的时候才成立 —— 这也是下面几条测试的对照面。
    """
    seen = {"viewport": [], "country": []}
    report = _run(
        env, _scripts(),
        set_viewport=lambda w, h: seen["viewport"].append((w, h)),
        set_country=lambda c: seen["country"].append(c), country="us",
    )
    assert report.passed is True
    assert [r.name for r in report.runs] == list(RUN_NAMES)
    assert all(r.status == "passed" and r.ok is True for r in report.runs), report
    assert all(r.failed_step is None for r in report.runs)
    # 换 viewport / 换国家真的发生了（否则「第 4/5 遍」是空转的）
    assert seen["viewport"] == [(1024, 768)], seen
    assert seen["country"] == ["us"], seen
    # 每遍一份自己的 trace（同一份会被下一遍冲掉，「卡在第几步」就读不出来了）
    traces = [r.trace_path for r in report.runs]
    assert len(set(traces)) == 5 and all(os.path.exists(p) for p in traces), traces
    assert env["stub"].scripts == [], "还有剧本没领 = 有一遍没跑"


def test_a_failing_run_is_not_swallowed_into_a_partial_pass(env):
    """**本任务最承重的一条**：第 2 遍挂 → 整体没过，且说清是哪一遍、卡在第几步。

    计划 Task 6 的 ⚠️：「不许把『某遍挂』吞成『部分通过』—— 那是本项目最忌讳的那类谎」。
    """
    report = _run(
        env,
        _scripts(rerun=_Script(rc=1, oks=(True, True, True, False, False),
                               notes={4: "第 4 步：点「Get Started」没点到"})),
        set_viewport=lambda w, h: None, set_country=lambda c: None, country="us",
    )
    assert report.passed is False
    assert [r.name for r in report.runs if r.status == "failed"] == ["rerun"]
    bad = report.runs[1]
    assert bad.ok is False
    assert bad.failed_step == 4
    # 人话里也要指出「哪一遍 + 第几步」（D16：不是错误码、不是选择器）
    said = report.summary()
    assert "第 2 遍" in said, said
    assert "第 4 步" in said, said
    assert "点「Get Started」没点到" in said, "trace 里那句人话要带上来"


@pytest.mark.parametrize("broken", RUN_NAMES)
def test_any_single_failing_run_fails_the_whole_selftest(env, broken):
    """任一遍挂都一样：`passed=False`，且只有那遍被记成挂。"""
    report = _run(
        env,
        _scripts(**{broken: _Script(rc=1, oks=(True, False))}),
        set_viewport=lambda w, h: None, set_country=lambda c: None, country="us",
    )
    assert report.passed is False
    assert [r.name for r in report.runs if r.status == "failed"] == [broken]
    assert report.runs[RUN_NAMES.index(broken)].failed_step == 2


# ── 「跳过」不许长得像「过了」─────────────────────────────────────

def test_a_skipped_run_never_counts_as_passed(env):
    """没给 `set_viewport` → 第 4 遍跳过，**默认不算通过**（R-5：不给它算证据）。"""
    report = _run(env, _scripts())          # 一个回调都不给
    assert report.passed is False
    viewport = report.runs[3]
    assert viewport.status == "skipped"
    # skipped 的 ok 必须是 None —— 写成 True 就是「没跑」在冒充「过了」
    assert viewport.ok is None
    assert viewport.trace_path is None and viewport.failed_step is None
    assert "没跑" in viewport.note, viewport.note
    assert "折叠" in viewport.note or "遮挡" in viewport.note, viewport.note


def test_the_country_run_is_the_one_skip_we_allow_by_default(env):
    """第 5 遍（换代理国家，成本高）是计划里唯一**默认允许跳过**的那遍。"""
    report = _run(env, _scripts(), set_viewport=lambda w, h: None)
    assert report.allowed_skips == ("country",)
    assert report.passed is True, report
    country = report.runs[4]
    assert country.status == "skipped" and country.ok is None
    assert "没跑" in country.note, country.note


def test_allowing_the_viewport_skip_is_explicit_and_still_loud(env):
    """明确放弃第 4 遍 → 可以算过，但报告里**照样吵**（`passed` 变了，字不许变软）。"""
    quiet = _run(env, _scripts())                       # 没给回调 → 第 4 遍跳过 → 没过
    assert quiet.passed is False
    loud = _run(env, _scripts(), allow_skips=("country", "viewport"))
    assert loud.passed is True
    assert loud.runs[3].status == "skipped" and loud.runs[3].ok is None
    assert "没验到" in loud.runs[3].note, loud.runs[3].note
    assert loud.runs[3].note == quiet.runs[3].note, "允许跳过只改判据，不改这句话"


def test_an_unknown_allow_skip_is_refused(env):
    """`allow_skips` 写错名字要当场报错 —— 静默忽略会让「以为允许了」变成没过。"""
    with pytest.raises(ValueError) as err:
        _run(env, _scripts(), allow_skips=("viewprt",))
    assert "viewprt" in str(err.value)


def test_a_viewport_callback_that_blows_up_is_a_loud_skip_not_a_pass(env):
    """回调自己炸了 = 这一遍没跑成 = 跳过 + 吵，**不是**通过。"""
    def boom(w, h):
        raise RuntimeError("窗口没打开")

    report = _run(env, _scripts(), set_viewport=boom)
    assert report.passed is False
    viewport = report.runs[3]
    assert viewport.status == "skipped" and viewport.ok is None
    assert "窗口没打开" in viewport.note, viewport.note
    # 只起了 3 遍（基线 / 第 2 遍 / 第 3 遍）—— 第 4 遍没跑成就不该起产物
    assert len(env["stub"].artifact_calls) == 3, env["stub"].artifact_calls


# ── 第 2/3 遍的扰动怎么做（R-4 / R-6）────────────────────────────

def test_run_two_reruns_the_same_page_and_only_navigates_when_asked(env):
    """R-6：不重启浏览器、不重置页面 —— 同一个 ws_url **接着**再跑一遍。

    给了 `entry_url` 就先 `cdp navi` 过去（字面意义上的「刷新」），
    两种都给覆盖：不给时一次 navi 都不许有。
    """
    stub = env["install"](_scripts())
    selftest.run(str(env["py"]), WS, env["form"], SITE, run_dir=env["dir"], cdp_bin=env["cdp"],
                 set_viewport=lambda w, h: None)
    assert stub.cdp_calls == [], "没给 entry_url 就不该动页面（R-6：不重置）"
    ws_urls = [_flag(cmd, "--ws-url") for cmd in stub.artifact_calls]
    assert ws_urls[0] == ws_urls[1] == WS, ws_urls

    stub = env["install"](_scripts())
    selftest.run(str(env["py"]), WS, env["form"], SITE, run_dir=env["dir"], cdp_bin=env["cdp"],
                 entry_url=ENTRY, set_viewport=lambda w, h: None)
    assert len(stub.cdp_calls) == 1, stub.cdp_calls
    navi = stub.cdp_calls[0]
    assert navi[1] == "navi" and navi[2] == ENTRY, navi
    assert "--host" in navi and "--port" in navi, navi
    # 顺序：第 1 遍跑完 → navi 回到入口 → 第 2 遍（「刷新后重跑」的字面意思）
    order = [kind for kind, _, _ in stub.calls]
    assert order == ["artifact", "cdp", "artifact", "artifact", "artifact"], order


def test_run_three_turns_the_delay_knob_through_the_cli(env):
    """R-4：延迟走**产物自带的 `--delay`**，不去改写产物源码（那测的是另一个产物）。"""
    before = env["py"].read_text(encoding="utf-8")
    report = _run(env, _scripts(), delay=2.5, set_viewport=lambda w, h: None)
    assert env["py"].read_text(encoding="utf-8") == before, "产物源码被改写了"
    assert report.passed is True

    delays = [_flag(cmd, "--delay") for cmd in env["stub"].artifact_calls]
    assert delays[0] is None and delays[1] is None, delays       # 基线与第 2 遍不加延迟
    assert float(delays[2]) == 2.5, delays                        # 第 3 遍才加
    assert delays[3] is None, delays


# ── 判据只认证据：退出码与 trace ───────────────────────────────────

def test_the_trace_outranks_a_success_exit_code(env):
    """退出码说成功、trace 里却有没做成的步 → **按没做成算**（产物不许谎报，我们也不）。"""
    report = _run(
        env,
        _scripts(baseline=_Script(rc=0, oks=(True, True, False))),
        set_viewport=lambda w, h: None,
    )
    assert report.passed is False
    first = report.runs[0]
    assert first.status == "failed" and first.failed_step == 3
    assert "trace" in first.note, first.note


def test_a_run_that_walked_everything_but_never_saw_success_is_a_failure(env):
    """每一步都做成了、退出码 1、trace 里没有 `ok=false` 的行 —— 是「没见到成功文案」，
    不是「卡在第几步」。两种说法要分开，别拿 None 冒充一个步号。"""
    report = _run(
        env,
        _scripts(rerun=_Script(rc=1, oks=(True, True, True))),
        set_viewport=lambda w, h: None,
    )
    assert report.passed is False
    bad = report.runs[1]
    assert bad.status == "failed" and bad.failed_step is None
    assert "成功" in bad.note, bad.note


def test_a_run_that_never_wrote_a_trace_is_still_a_failure(env):
    """产物起来就崩（退出码非 0、trace 都没有）→ 挂，并带上它最后说了什么。"""
    report = _run(
        env,
        _scripts(baseline=_Script(rc=1, trace=False, stderr="ModuleNotFoundError: common")),
        set_viewport=lambda w, h: None,
    )
    assert report.passed is False
    bad = report.runs[0]
    assert bad.status == "failed" and bad.failed_step is None
    assert "退出码" in bad.note and "ModuleNotFoundError" in bad.note, bad.note


def test_exit_zero_with_no_trace_at_all_is_not_a_pass(env):
    """退出码 0，但**一行 trace 都没有** —— 没有证据就不许被读成「过了」（R-27）。

    两个「没有」撞在一起时，判据会变成空口白话：产物 trace 写不进去会警告一句然后接着跑
    （`agent/template.py` 的 `_trace`），exit 0 照样返回；而 `_read_trace` 对「文件不在 /
    读不动」交的是空列表。于是「trace 里没有没做成的步」这句话在**一行都没有**时也成立 ——
    而这个模块存在的唯一理由就是当**证据**的闸门（Task 8 要拿它跑真站）。
    """
    report = _run(env, _scripts(baseline=_Script(rc=0, trace=False)),
                  set_viewport=lambda w, h: None)
    assert report.passed is False
    first = report.runs[0]
    assert first.status == "failed" and first.ok is False
    assert first.failed_step is None, "「没有证据」不是「卡在第 N 步」"
    assert "trace" in first.note and "证据" in first.note, first.note
    assert "跑通了" not in report.summary(), report.summary()


def test_a_hung_run_is_a_failure(env):
    """跑不完（超时）也是挂 —— 不能挂在那儿等它。"""
    report = _run(
        env,
        _scripts(delay=_Script(timeout=True, oks=(True,))),
        set_viewport=lambda w, h: None,
    )
    assert report.passed is False
    bad = report.runs[2]
    assert bad.status == "failed"
    assert "超时" in bad.note or "没跑完" in bad.note, bad.note


# ── 环境：cdp 二进制 ───────────────────────────────────────────────

def test_the_artifact_is_pointed_at_the_cdp_we_choose(env):
    """真跑时把 cdp 指到带 observe/diff/screenshot 的那版（R-15 第 5 条）。

    产物自己的 `CDP_BIN` 认 `SITEFORGE_CDP_BIN`；子进程环境由自测下发 ——
    运维不用改产物、也不用改自己的 shell。
    """
    report = _run(env, _scripts(), set_viewport=lambda w, h: None)
    for child_env in env["stub"].artifact_envs:
        assert child_env["SITEFORGE_CDP_BIN"] == env["cdp"], child_env.get("SITEFORGE_CDP_BIN")
        assert child_env["PATH"], "子进程环境要是从当前环境抄来的（别把 PATH 抹掉）"
    assert report.cdp_bin == env["cdp"]
    assert all(cmd[0] == env["cdp"] for cmd in env["stub"].cdp_calls) or not env["stub"].cdp_calls


def test_the_reported_task_id_is_ours_to_choose(env):
    """产物成功时会往上报告接口写一条 URL 记录（那是它自带的行为，关掉就是改产物），
    所以「报的是哪个 id」必须自测说了算：默认是**自测的** id，真任务的 id 要自己传。"""
    stub = env["install"](_scripts())
    selftest.run(str(env["py"]), WS, env["form"], SITE, run_dir=env["dir"], cdp_bin=env["cdp"],
                 set_viewport=lambda w, h: None)
    cid = _flag(stub.artifact_calls[0], "--correlation-id")
    assert cid.startswith("selftest-%s_" % SITE), cid
    assert _flag(stub.artifact_calls[0], "--task-id") == cid.split("_")[0], "别让它冒充某个真任务"

    stub = env["install"](_scripts())
    selftest.run(str(env["py"]), WS, env["form"], SITE, run_dir=env["dir"], cdp_bin=env["cdp"],
                 set_viewport=lambda w, h: None, correlation_id="cid_1", task_id="task_9")
    assert _flag(stub.artifact_calls[0], "--task-id") == "task_9"


# ── 报告本身 ──────────────────────────────────────────────────────

def test_the_report_carries_the_brief_shape_plus_a_status(env):
    """brief 的形状（name/ok/failed_step/trace_path）一个不少，另加 status ——
    「跳过」与「过了」必须分得开，而 `ok` 单独一个布尔分不开。"""
    report = _run(env, _scripts(), set_viewport=lambda w, h: None)
    data = report.as_dict()
    assert json.loads(json.dumps(data, ensure_ascii=False)) == data, "要能进 PROVENANCE"
    assert len(data["runs"]) == 5
    for run in data["runs"]:
        for key in ("name", "ok", "failed_step", "trace_path", "status", "note"):
            assert key in run, run
        assert run["status"] in ("passed", "failed", "skipped"), run
    assert data["passed"] is True


def test_the_summary_always_says_tool_side_passing_is_not_production_passing(env):
    """规格 §10 原话：**工具侧自测通过 ≠ 生产一定过**（代理出口、指纹、时序都不是一套）。
    这句话必须随报告走，不能只活在文档里。"""
    report = _run(env, _scripts(), set_viewport=lambda w, h: None)
    said = report.summary()
    assert selftest.CAVEAT in said, said
    assert "生产" in selftest.CAVEAT and "不是一套" in selftest.CAVEAT, selftest.CAVEAT


def test_a_missing_artifact_is_refused_before_anything_runs(env):
    """产物不在就当场说清（否则五遍各报一次「退出码 2」，人得自己拼）。"""
    env["install"](_scripts())
    with pytest.raises(ValueError) as err:
        selftest.run(str(env["dir"] / "nope.py"), WS, env["form"], SITE, run_dir=env["dir"])
    assert "nope.py" in str(err.value)
    assert env["stub"].calls == [], "产物不在就不该起任何进程"


# ── 自测不许往生产写：断言「有没有发请求」，不是「有没有传那个 flag」────────

#: 真运行时那一份（产物 `from common import …` 解析到的就是它）
RUNTIME_COMMON = ROOT / "forms" / "common.py"

#: 假 cdp：让产物在**真 CDPHelper** 底下走通一次 —— `eval` 交回带成功文案的页面文本，
#: 于是产物会走到「报 URL」那一步。**那一步正是要被堵住的**（走不通就什么也证明不了）。
STUB_CDP_OK = '''#!/usr/bin/env python3
"""假 cdp（测试用）：只够让产物在真 CDPHelper 底下走通一次。"""
import json
import sys

cmd = sys.argv[1] if len(sys.argv) > 1 else ""
if cmd in ("--help", "-h", "help"):
    print("A CLI tool to interact with Chrome DevTools Protocol")
    print("Available Commands:")
    for name in ("active", "click", "close", "eval", "form", "navi", "observe",
                 "diff", "screenshot", "scroll", "snapshot", "targets"):
        print("  %s  stub" % name)
    sys.exit(0)
if cmd == "eval":
    print(json.dumps("Thank you — https://example.test/done"))
    sys.exit(0)
if cmd in ("click", "form", "scroll", "navi"):
    print("done: %s" % cmd)
    sys.exit(0)
sys.exit(0)
'''

#: 子进程的网络守卫（`sitecustomize` 开机自动 import）：任何出网动作记一笔再抛。
#: 为什么堵在 socket 这一层而不是堵在 `report_url`：**手段会变，网线不会** ——
#: 谁哪天换了个 HTTP 客户端（requests / http.client / 别的），这里照样拦得住。
NET_GUARD = '''"""自测的子进程守卫：这次跑**一个字节都不许发去生产**。"""
import os
import socket
import urllib.request

_LOG = os.environ.get("SITEFORGE_NETLOG")


def _record(what):
    if not _LOG:
        return
    with open(_LOG, "a", encoding="utf-8") as fp:
        fp.write(what + "\\n")


def _no_urlopen(*args, **kwargs):
    target = args[0] if args else kwargs.get("url", "?")
    _record("urlopen %s" % target)
    raise AssertionError("自测不许往生产发请求：%s" % target)


urllib.request.urlopen = _no_urlopen


def _no_connect(self, address):
    _record("connect %r" % (address,))
    raise AssertionError("自测不许连出去：%r" % (address,))


socket.socket.connect = _no_connect
'''


def _reporting_sandbox(tmp_path):
    """真运行时 + 真产物 + 假 cdp：不起浏览器也能让产物**一路跑到「该上报」那一步**。"""
    root = tmp_path / "prodlike"
    (root / "forms" / "sites").mkdir(parents=True)
    shutil.copy(RUNTIME_COMMON, root / "forms" / "common.py")
    states = [{"name": "go", "when": None, "steps": [
        {"action": "click", "note": "点「Go」",
         "target": {"text": "Go", "role": "button", "near": None, "selectors": ["#go"]}}]}]
    py = root / "forms" / "sites" / ("%s.py" % SITE)
    py.write_text(template.render(SITE, "Thank you", states, [], {}), encoding="utf-8")
    form = root / "form.json"
    form.write_text("{}", encoding="utf-8")
    cdp = root / "cdp"
    cdp.write_text(STUB_CDP_OK, encoding="utf-8")
    cdp.chmod(0o755)
    return py, form, cdp


def _install_net_guard(tmp_path, monkeypatch):
    """给**子进程**装上网络守卫，返回它记账的那个文件。"""
    guard = tmp_path / "netguard"
    guard.mkdir()
    (guard / "sitecustomize.py").write_text(NET_GUARD, encoding="utf-8")
    netlog = tmp_path / "net.log"
    monkeypatch.setenv("PYTHONPATH", str(guard))
    monkeypatch.setenv("SITEFORGE_NETLOG", str(netlog))
    return netlog


def test_a_self_test_run_never_touches_the_production_api(tmp_path, monkeypatch):
    """**这一轮最承重的一条**：自测不往生产写。

    为什么不满足于「断言 `--no-report` 传下去了」：flag 只是手段，证据是**网线上没有包**。
    所以这里在子进程里装网络守卫（`sitecustomize` 把 `urllib` 与 `socket` 的出口堵上，
    记一笔再抛），产物真去上报就会留下记录。

    而且先跑**正控**：同一套环境、同一个产物，**不给** `--no-report` 时必须留下记录 ——
    否则「没有记录」可能只是这根网线本来就没通，那这条测试就是永远绿的假钉子。
    """
    py, form, cdp = _reporting_sandbox(tmp_path)
    netlog = _install_net_guard(tmp_path, monkeypatch)

    # 正控：生产那条路（不给 --no-report）真的会去写生产（被守卫挡住，出不了本机）
    done = subprocess.run(
        ["python3", str(py), "--ws-url", WS, "--form-file", str(form),
         "--correlation-id", "cid_1", "--log-level", "INFO"],
        capture_output=True, text=True, timeout=120,
        env={**os.environ, "SITEFORGE_CDP_BIN": str(cdp)})
    assert done.returncode == 0, done.stdout + done.stderr
    assert netlog.exists() and netlog.read_text(encoding="utf-8").strip(), (
        "正控失败：不给 --no-report 时没看到出网动作 —— 那这条测试什么也证明不了")

    netlog.unlink()
    report = selftest.run(str(py), WS, str(form), SITE, run_dir=tmp_path / "traces",
                          cdp_bin=str(cdp), delay=0.01, timeout=120,
                          allow_skips=("country", "viewport"))
    assert report.passed is True, report.summary()   # 真跑通了（不是空转 —— 空转证明不了任何事）
    assert not netlog.exists(), "自测往生产发了请求：\n%s" % (
        netlog.read_text(encoding="utf-8") if netlog.exists() else "")


def test_a_self_test_run_is_silent_in_every_run(env):
    """五遍**每一遍**都带 `--no-report` —— 只有第一遍静下来不算静。"""
    stub = env["install"](_scripts())
    selftest.run(str(env["py"]), WS, env["form"], SITE, run_dir=env["dir"], cdp_bin=env["cdp"],
                 set_viewport=lambda w, h: None, set_country=lambda c: None, country="us")
    assert stub.artifact_calls, "一次都没跑？"
    for cmd in stub.artifact_calls:
        assert "--no-report" in cmd, cmd


# ── 真浏览器那把尺子（R-23）：桩之外，至少真跑一遍 ────────────────────
#
# 为什么必须有这一节：自测是**诚实性的量具**。如果它自己的诚实只由桩来断言，
# 那这根链子上最弱的一环就是量具本身 —— 桩能证明「trace 读得对」，证明不了
# 「真站上跑得通」。所以这里起一个**私有 headless Chrome**（绝不碰共享的 9222）、
# 一个本地夹具页、真 cdp、真运行时，跑真产物。
#
# 拿不到环境（没 Chrome / 没 go）**宁可红也不 skip** —— 沿用 Task 5 的裁定
# （`tests/test_browser_agent.py` 的评审特意夸了这一点）：skip 与 pass 长得一样。

LIVE_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>live</title></head>
<body><h1>Landing</h1>
<button id="go" onclick="document.getElementById('done').textContent='Thank you'">Go</button>
<div id="done"></div></body></html>"""


class _LivePage(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = LIVE_PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _chrome_binary() -> str:
    for candidate in (os.environ.get("CHROME_BIN"), "google-chrome", "google-chrome-stable",
                      "chromium", "chromium-browser",
                      "/usr/bin/google-chrome", "/usr/bin/chromium"):
        if not candidate:
            continue
        found = shutil.which(candidate) or (candidate if os.path.exists(candidate) else None)
        if found:
            return found
    raise AssertionError("找不到 Chrome/Chromium —— 这条闸门宁可红也不 skip")


def _cdp_binary() -> str:
    """真 cdp：环境变量 → 本仓库构建的那个 → 现构建一个（与 Go 侧 e2e 同一条路）。"""
    given = os.environ.get("SITEFORGE_CDP_BIN")
    if given and os.access(given, os.X_OK):
        return given
    built = ROOT / "tools" / "cdp" / "cdp"
    if built.is_file() and os.access(built, os.X_OK):
        return str(built)
    go = shutil.which("go") or "/usr/local/go/bin/go"
    if not os.path.exists(go):
        raise AssertionError("既没有 tools/cdp/cdp，也没有 go 工具链（试过 %s）" % go)
    env = dict(os.environ,
               PATH="/usr/local/go/bin:" + os.environ.get("PATH", ""),
               GOPROXY=os.environ.get("GOPROXY", "https://goproxy.cn,direct"))
    out = pathlib.Path(tempfile.mkdtemp(prefix="siteforge-cdp-")) / "cdp"
    done = subprocess.run([go, "build", "-ldflags", "-s -w", "-o", str(out), "main.go"],
                          cwd=str(ROOT / "tools" / "cdp"), env=env,
                          capture_output=True, text=True, timeout=300)
    if done.returncode != 0:
        raise AssertionError("构建 cdp 失败：\n" + done.stderr[-2000:])
    return str(out)


@pytest.fixture(scope="module")
def live_site():
    """本地夹具页 + 私有 headless Chrome（独立端口 / 独立 profile）+ 真 cdp。"""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _LivePage)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    entry = "http://127.0.0.1:%d/" % server.server_address[1]

    port = _free_port()
    profile = tempfile.mkdtemp(prefix="siteforge-live-profile-")
    log = open(os.path.join(profile, "chrome.log"), "w")  # noqa: SIM115 —— 与子进程同寿
    proc = subprocess.Popen(
        [_chrome_binary(), "--headless=new", "--remote-debugging-port=%d" % port,
         "--remote-debugging-address=127.0.0.1", "--no-first-run", "--no-default-browser-check",
         "--disable-gpu", "--no-sandbox", "--disable-extensions", "--disable-crash-reporter",
         "--user-data-dir=%s" % profile, entry],
        stdout=log, stderr=log, start_new_session=True)
    base = "http://127.0.0.1:%d" % port
    ws_url = ""
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base + "/json/version", timeout=1) as resp:
                ws_url = json.loads(resp.read().decode("utf-8"))["webSocketDebuggerUrl"]
            break
        except Exception:  # noqa: BLE001 —— 还没起来
            time.sleep(0.2)
    if not ws_url:
        raise AssertionError("私有 Chrome 没起来（%s 一直没有 page 目标）" % base)
    try:
        yield {"ws_url": ws_url, "entry": entry, "cdp": _cdp_binary()}
    finally:
        # 杀**整个进程组**：Chrome 会拉起 zygote / renderer 一串子进程，
        # 只杀父进程会留孤儿（本仓库栽过：孤儿窗口 → 内存 95%）
        try:
            os.killpg(os.getpgid(proc.pid), 15)
        except OSError:
            pass
        server.shutdown()
        log.close()


def _live_artifact(root, success_text):
    """真产物（模板渲染）+ 真运行时拷贝，摆在 `<root>/forms/{common.py,sites/}` 里。"""
    (root / "forms" / "sites").mkdir(parents=True)
    shutil.copy(RUNTIME_COMMON, root / "forms" / "common.py")
    states = [{"name": "landing", "when": None, "steps": [
        {"action": "click", "note": "点「Go」",
         "target": {"text": "Go", "role": "button", "near": None, "selectors": ["#go"]}}]}]
    py = root / "forms" / "sites" / ("%s.py" % SITE)
    py.write_text(template.render(SITE, success_text, states, [], {}), encoding="utf-8")
    form = root / "form.json"
    form.write_text("{}", encoding="utf-8")
    return py, form


def test_a_real_run_passes_and_stays_off_production(live_site, tmp_path, monkeypatch):
    """真 Chrome + 真 cdp + 真运行时 + 真产物：跑通了，并且**一个字节都没发去生产**。"""
    py, form = _live_artifact(tmp_path / "good", "Thank you")
    netlog = _install_net_guard(tmp_path, monkeypatch)

    report = selftest.run(str(py), live_site["ws_url"], str(form), SITE,
                          run_dir=tmp_path / "traces", cdp_bin=live_site["cdp"],
                          entry_url=live_site["entry"], delay=0.05, timeout=180,
                          allow_skips=("country", "viewport"))

    assert report.passed is True, report.summary()
    assert [r.status for r in report.runs[:3]] == ["passed"] * 3, report.summary()
    for run in report.runs[:3]:
        assert run.trace_path and os.path.exists(run.trace_path), run
        rows = [json.loads(ln) for ln in pathlib.Path(run.trace_path).read_text(
            encoding="utf-8").splitlines() if ln.strip()]
        assert rows and all(ln.get("ok") is True for ln in rows), rows
        assert rows[0]["step"] == 1 and "note" in rows[0]
    # 第 2 遍真的「刷新后重跑」过（entry_url → cdp navi），所以它的 trace 也是新的
    assert report.runs[1].trace_path != report.runs[0].trace_path
    assert not netlog.exists(), "真跑这一遍往生产发了请求：\n%s" % (
        netlog.read_text(encoding="utf-8") if netlog.exists() else "")


def test_a_real_run_that_never_succeeds_is_not_a_pass(live_site, tmp_path, monkeypatch):
    """成功文案永远等不到时：三遍都挂、都指得出「没走到成功」，而且**不是**「卡在第几步」。"""
    py, form = _live_artifact(tmp_path / "bad", "NEVER-APPEARS")
    _install_net_guard(tmp_path, monkeypatch)

    report = selftest.run(str(py), live_site["ws_url"], str(form), SITE,
                          run_dir=tmp_path / "traces", cdp_bin=live_site["cdp"],
                          delay=0.05, timeout=180, allow_skips=("country", "viewport"))

    assert report.passed is False
    assert [r.name for r in report.failed_runs] == ["baseline", "rerun", "delay"]
    for run in report.failed_runs:
        assert run.failed_step is None, run
        assert "成功" in run.note, run.note


# ── 与模板的契约 ──────────────────────────────────────────────────

def test_selftest_and_the_template_agree_on_the_artifact_cli():
    """自测拼的每个参数都必须真的是产物 CLI 上的 —— 模板一改名，这里先红。

    （自测是**第二个**调产物 CLI 的地方：ad-task.py 是第一个。两处对不上，
    自测会在真站上退化成「每遍都挂」，而那种挂法看着像站点的问题。）
    """
    src = template.render(SITE, "READY", [{"name": "go", "when": None, "steps": [
        {"action": "click", "note": "点一下",
         "target": {"text": "Go", "role": "button", "near": None, "selectors": ["#go"]}}]}],
        [], {})
    for flag in selftest.ARTIFACT_FLAGS:
        assert '"%s"' % flag in src, flag
    assert "--delay" in src, "第 3 遍的旋钮要真的在产物 CLI 上"
