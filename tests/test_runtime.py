"""R-15/R-16：siteforge 自己拥有 py 运行时的那一份（`forms/common.py`）。

用户 2026-09-16 定：新系统**不再引用** `/opt/skills/auto-farm-skill/forms/common.py`
（那是生产 57 个站点脚本共用的那份），改为自己拥有一份 —— 但产物仍写
`from common import CDPHelper, setup_logger, report_url`（那一行**逐字不能改**，
Task 3 的测试断言它），改的只是「从哪解析」。所以布局必须镜像生产：
`<root>/forms/common.py` + `<root>/forms/sites/<site>.py`，产物那行路径算术
`sys.path.insert(0, dirname(dirname(abspath(__file__))))` 才原样成立。

这里钉住三条**不能破**的：

1. **默认值与生产等价** —— 部署后这 57 个脚本共用它，改默认值 = 悄悄改生产行为。
   `CDP_PATH` 默认仍是 `/opt/skills/auto-farm-skill/cdp`，两个 API URL 默认仍指
   `fmr.3tkj.cn`（规格第 784 行只许「加环境变量覆盖」，没让改默认值）。
2. **成功路径一个字节都不变** —— `screenshot()` 仍然返回 `stdout.strip()`；
   R-16 那处失败归因（`last_screenshot_error`）是**加法式**的。
3. **出身与分歧看得见**（不靠记忆）—— 文件头写出身（抄自哪份、哪个 commit、
   哪天、md5），`agent/runtime.py` 按需比对。memory 里 `two-json-executors`
   那个坑（生产 `form_executor/` vs 实验 `src/lanuage/`，改错地方等于白改）
   就是这么来的：两份真源并存，而「哪份是真的」只活在人脑子里。
"""

from __future__ import annotations

import logging
import os
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import runtime  # noqa: E402

ENV_OVERRIDES = ("SITEFORGE_CDP_BIN", "CDP_PATH", "SCREENSHOT_API_URL", "FORM_API_URL")
WS = "ws://127.0.0.1:9222/devtools/browser/abc"


def _load(path=None):
    """加载运行时那一份，**不占 `sys.modules["common"]`**。

    `tests/test_template.py` 的 common 替身用的就是那个名字；占了它，那个文件整片会红
    （而红的原因看着毫不相干）。顺带把 `logging.basicConfig` 的副作用收回去 ——
    运行时那份是被 57 个脚本 import 的模块级代码，不该顺手改测试进程的日志设置。
    """
    root = logging.getLogger()
    level, handlers = root.level, root.handlers[:]
    try:
        return runtime.load(path)
    finally:
        root.setLevel(level)
        root.handlers[:] = handlers


def _stub_run(monkeypatch, rc=0, stdout="", stderr=""):
    """替掉 `screenshot()` 底下那个 `subprocess.run`，并把它收到的命令记下来。"""
    seen = {}

    def fake(cmd, **kw):
        seen["cmd"] = list(cmd)
        seen["kw"] = kw
        return subprocess.CompletedProcess(cmd, rc, stdout, stderr)

    monkeypatch.setattr(subprocess, "run", fake)
    return seen


# ── 它在哪：产物那行路径算术指向的就是它 ────────────────────────────

def test_the_artifact_resolves_this_very_file():
    """产物落盘在 `<root>/forms/sites/<site>.py` —— 它 import 到的必须是这一份。"""
    artifact = runtime.RUNTIME_PATH.parent / "sites" / "example.py"
    resolved = pathlib.Path(
        os.path.dirname(os.path.dirname(os.path.abspath(str(artifact))))) / "common.py"
    assert resolved == runtime.RUNTIME_PATH
    assert runtime.RUNTIME_PATH.is_file()


def _gitignored_dirs() -> list:
    return [ln.strip() for ln in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
            if ln.strip().endswith("/") and not ln.startswith("#")]


def test_the_runtime_is_not_somewhere_git_would_silently_ignore():
    """真源不进 git 等于没有真源。`runtime/` 是被 ignore 的（`.gitignore`），
    所以那份**不许**放在那儿 —— 而 `.gitignore` 里正好有个 `runtime/`，
    这份文件又恰好像运行时的家：一个 wc -l 就掉进去的坑。"""
    assert runtime.RUNTIME_PATH == ROOT / "forms" / "common.py"
    relative = runtime.RUNTIME_PATH.relative_to(ROOT).as_posix()
    for pattern in _gitignored_dirs():
        assert not relative.startswith(pattern), "%s 会被 .gitignore 的 %s 吃掉" % (relative, pattern)


def test_loading_it_does_not_pollute_sys_modules():
    """按别的名字加载：**不许**塞进 `sys.modules`，尤其不许占 `common` 这个名字。"""
    before = set(sys.modules)
    module = _load()
    assert set(sys.modules) - before == set()
    assert module.__name__ != "common"


# ── 默认值：与生产等价 ────────────────────────────────────────────

def test_the_defaults_stay_production_equivalent(monkeypatch):
    """部署后 57 个既有站点脚本共用这份 —— 默认值一个字都不许动。"""
    for name in ENV_OVERRIDES:
        monkeypatch.delenv(name, raising=False)
    common = _load()
    assert common.CDP_PATH == "/opt/skills/auto-farm-skill/cdp"
    assert common.SCREENSHOT_API_URL == "https://fmr.3tkj.cn/api/quest/screenshot"
    assert common.FORM_API_URL == "https://fmr.3tkj.cn/api/quest/formMessage"


def test_the_cdp_binary_can_be_pointed_elsewhere(monkeypatch):
    """规格第 784 行要的就是这条：默认不变，但能指到别处（容器 / 新版 cdp）。

    两个名字都收：`SITEFORGE_CDP_BIN` 是 siteforge 这一侧的旋钮（产物自己也认它，
    一个旋钮指一件事），`CDP_PATH` 是容器里既有的那个（Dockerfile:85）。
    """
    for name in ENV_OVERRIDES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CDP_PATH", "/usr/local/bin/cdp")
    assert _load().CDP_PATH == "/usr/local/bin/cdp"

    monkeypatch.setenv("SITEFORGE_CDP_BIN", "/company/siteforge/tools/cdp/cdp")
    assert _load().CDP_PATH == "/company/siteforge/tools/cdp/cdp", "更专的那个名字优先"


def test_the_env_override_reaches_the_command_line(monkeypatch):
    """指到别处要**真的**指到别处：`screenshot()` 起的命令第一段就是它。"""
    for name in ENV_OVERRIDES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SITEFORGE_CDP_BIN", "/tmp/siteforge-cdp/cdp")
    seen = _stub_run(monkeypatch, rc=0, stdout="AAAA\n")
    common = _load()
    common.CDPHelper(WS).screenshot()
    assert seen["cmd"][:2] == ["/tmp/siteforge-cdp/cdp", "screenshot"]
    assert "--host" in seen["cmd"] and "--port" in seen["cmd"]


# ── screenshot()：成功路径不变，失败可归因（R-16）─────────────────

def test_screenshot_success_path_is_byte_identical(monkeypatch):
    """硬约束：成功路径的返回值**一个字节都不能变**（57 个脚本共用这份）。"""
    for name in ENV_OVERRIDES:
        monkeypatch.delenv(name, raising=False)
    _stub_run(monkeypatch, rc=0, stdout="iVBORw0KGgo=\n\n")
    helper = _load().CDPHelper(WS)
    assert helper.screenshot() == "iVBORw0KGgo=", "就是 stdout.strip()，与生产那份逐字相同"
    assert helper.last_screenshot_error is None


def test_screenshot_failure_is_attributable(monkeypatch, caplog):
    """原先命令没成 = 空字符串，跟「这一页是白的」长得一模一样（stderr 被丢了）。

    现在：返回值**照样**是那个空字符串（不许变），但失败说得清为什么、往哪看。
    """
    for name in ENV_OVERRIDES:
        monkeypatch.delenv(name, raising=False)
    said = 'Error: unknown command "screenshot" for "cdp"'
    _stub_run(monkeypatch, rc=1, stdout="", stderr=said)
    helper = _load().CDPHelper(WS)

    with caplog.at_level(logging.WARNING):
        assert helper.screenshot() == "", "返回值不许跟着变（加法式）"

    assert said in helper.last_screenshot_error, helper.last_screenshot_error
    assert "/opt/skills/auto-farm-skill/cdp" in helper.last_screenshot_error, "要说清是哪个 cdp"
    assert any(said in record.getMessage() for record in caplog.records), caplog.text


def test_a_failed_screenshot_still_returns_what_production_returned(monkeypatch):
    """命令报错但有 stdout 时，生产那份交的还是 stdout —— 这点也不许变。"""
    for name in ENV_OVERRIDES:
        monkeypatch.delenv(name, raising=False)
    _stub_run(monkeypatch, rc=1, stdout="garbage-out\n", stderr="boom")
    helper = _load().CDPHelper(WS)
    assert helper.screenshot() == "garbage-out"
    assert "boom" in helper.last_screenshot_error


# ── 出身与分歧看得见 ──────────────────────────────────────────────

def test_the_provenance_says_where_this_came_from():
    """抄自哪份、哪个 commit、哪天、md5 —— 写进文件里，不靠谁记得。"""
    prov = runtime.provenance()
    assert prov["copied_from"] == "/opt/skills/auto-farm-skill/forms/common.py"
    assert re.fullmatch(r"[0-9a-f]{40}", prov["source_commit"]), prov["source_commit"]
    assert re.fullmatch(r"[0-9a-f]{32}", prov["source_md5"]), prov["source_md5"]
    assert prov["copied_at"] and prov["source_commit_date"]
    assert prov["changes"], "改过哪几处要写下来，否则下次分不清「改的」与「漂的」"

    text = runtime.RUNTIME_PATH.read_text(encoding="utf-8")
    for key in ("copied_from", "source_commit", "source_md5"):
        assert prov[key] in text, "人打开文件时也要看得见 %s" % key


def test_divergence_is_visible_on_demand(tmp_path):
    """「两边漂了」要**看得见**：一条命令比对，还能进脚本（退出码）。"""
    same = tmp_path / "same.py"
    same.write_text(runtime.RUNTIME_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    identical, text = runtime.compare(same)
    assert identical is True and "没有分歧" in text, text
    assert runtime.main(["diff", "--other", str(same)]) == 0

    other = tmp_path / "other.py"
    drifted = runtime.RUNTIME_PATH.read_text(encoding="utf-8").replace(
        "CDP_TIMEOUT = 30", "CDP_TIMEOUT = 99")
    assert drifted != runtime.RUNTIME_PATH.read_text(encoding="utf-8")
    other.write_text(drifted, encoding="utf-8")

    identical, text = runtime.compare(other)
    assert identical is False
    assert "CDP_TIMEOUT = 99" in text, "要指得出是哪一行不一样"
    assert str(other) in text and str(runtime.RUNTIME_PATH) in text
    assert runtime.main(["diff", "--other", str(other)]) == 1
    for change in runtime.known_changes():
        assert change in text, "已知的加法式改动要列出来（好分清「改的」与「漂的」）"


def test_a_missing_other_copy_is_never_read_as_no_divergence(tmp_path):
    """比对的那份不在 = **没法比**，不是「没有分歧」（这两种绝不能混）。"""
    gone = tmp_path / "nope.py"
    identical, text = runtime.compare(gone)
    assert identical is False
    assert "没有分歧：两边逐行逐字相同" not in text, "比不了不许说成没有分歧"
    assert str(gone) in text
    assert runtime.main(["diff", "--other", str(gone)]) == 2


def test_show_says_it_in_human_words(capsys):
    assert runtime.main(["show"]) == 0
    out = capsys.readouterr().out
    assert "抄自" in out and "md5" in out and "改动" in out


def test_the_cli_says_what_it_does_when_asked_nothing(capsys):
    assert runtime.main([]) == 2
    out = capsys.readouterr().out
    assert "show" in out and "diff" in out


def test_the_default_comparison_is_against_production_not_against_itself():
    """默认比对的另一边是**生产那份**（`copied_from`）—— 自己比自己永远「没有分歧」。"""
    assert runtime.PRODUCTION_PATH == runtime.provenance()["copied_from"]
    assert pathlib.Path(runtime.PRODUCTION_PATH) != runtime.RUNTIME_PATH
