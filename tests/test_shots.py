"""Task 1：截图**落盘**（`agent/shots.py`）—— 图落在磁盘上，账本里只留文件名。

## 这一页在钉什么（契约，比实现重要）

两个 capture **都不抛**；返回的是**文件名**（永远不是字节）；没成时第二项是**人话** ——
说得出「为什么没拍成」，而不是一个空串或一句 `None`。

这条纪律不是洁癖：本项目一整天在治的就是「出事了但没人说」。一张没拍成的图如果只留下
`""`，页面上就会长出一块**看起来像没事**的空白；而那句人话是要**原样显示给运营**的。

所以这一页的断言几乎都在查同一件事的两面：① 返回值是 `(None, 为什么)`；
② **磁盘上不留半个文件**（解码失败 / 写失败都不许留下 `.part` 或半张图）。

## 为什么全是桩（**一次浏览器都不开**）

MCP 那条用桩 session，CLI 那条用假 `cdp`（写不写文件、退出码几，由 `mode` 定）。
真窗口上的测量是 Step 5，**不许在这里顺手做** —— 此刻另一个实现者可能正用着那个 worker 的
窗口，同一个 worker 上开两个窗口正是本项目踩过的「两任务抢同一窗口 → 窗口僵死」事故。
"""

from __future__ import annotations

import base64
import json
import os
import pathlib
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import selftest, shots  # noqa: E402
from agent.tools import McpError  # noqa: E402

#: 1×1 的真 PNG（70 字节，magic 全在）。用它当「一张真图」，不是因为好看，
#: 是因为它小到能进 git、又过得了 `b64decode(validate=True)` 与 PNG magic 两道关。
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
PNG_1X1_B64 = base64.b64encode(PNG_1X1).decode("ascii")


class _StubSession:
    """桩 session：只实现被 `shots` 用到的那**一个**方法（`McpSession.call_tool`）。

    `answer` 是它这一调的回话：dict / 别的什么 / 或一个**异常实例**（异常实例就照真
    session 的规矩抛出来 —— 真 session 失败一律抛，见 `agent/tools.py` 的 `call_tool`）。
    """

    def __init__(self, answer):
        self.answer = answer
        self.calls: list = []

    def call_tool(self, name, arguments=None):
        self.calls.append((name, arguments))
        if isinstance(self.answer, BaseException):
            raise self.answer
        return self.answer


def _shot_result(**over) -> dict:
    """内核 `screenshot` 那份结果（`tools/cdp/internal/mcp/registry.go` 的契约形状）。"""
    out = {"png_base64": PNG_1X1_B64, "image_px": [1, 1], "viewport_css_px": [1, 1], "dpr": 1}
    out.update(over)
    return out


# ────────────────────────── 假 cdp（CLI 那条路的桩）──────────────────────────

#: 假 `cdp` 的源码。照着**真 cdp 被调用的那一面**做：
#: 全局 flag 在子命令**前面**、`--out` 之外照样往 stdout 吐裸 base64
#: （真 cdp 默认就这么干，所以产物必须把 stdout 丢掉）、退出码与写不写文件由 `mode` 定。
_FAKE_CDP = '''\
#!{python}
"""假 cdp：只认 `screenshot --out <dest>`，把这次调用记进 {log}。"""

import json
import os
import pathlib
import sys
import time

LOG = pathlib.Path({log!r})
MODE = {mode!r}
argv = sys.argv[1:]

# 这次它被怎么调的 + **stdout 去哪了**（「必须丢进 /dev/null」那条断言靠这一个字段）
try:
    stdout_target = os.readlink("/proc/self/fd/1")
except OSError:
    stdout_target = "?"
LOG.write_text(json.dumps({{"argv": argv, "stdout": stdout_target}}, ensure_ascii=False),
               encoding="utf-8")

# ⚠️ 这里**故意不建目录**：目录要由 shots 建（不然「按需建目录」那条测试是假绿）
dest = argv[argv.index("--out") + 1] if "--out" in argv else None

if MODE == "slow":
    time.sleep(5)

if MODE == "ok":
    pathlib.Path(dest).write_bytes(bytes.fromhex("{png_hex}"))
    print("裸 base64 也照吐不误")          # ← 真 cdp 默认就往 stdout 吐这个
    sys.exit(0)

if MODE == "silent":                        # 退出 0 但什么都没写 —— 「一半成功」那种谎
    print("裸 base64 也照吐不误")
    sys.exit(0)

if MODE == "fail":
    sys.stderr.write("连不上 worker 的 CDP（假 cdp 说的）\\n")
    sys.exit(1)

sys.exit(0)
'''


def _fake_cdp(tmp_path, *, mode="ok", log=None, body=PNG_1X1):
    """造一个假 `cdp` 可执行文件。`mode`：`ok` / `silent` / `fail` / `slow`。"""
    path = tmp_path / ("fake-cdp-" + mode)
    path.write_text(_FAKE_CDP.format(python=sys.executable, log=str(log or tmp_path / "call.json"),
                                     mode=mode, png_hex=body.hex()),
                    encoding="utf-8")
    path.chmod(0o755)
    return path


def _called(log) -> dict:
    """假 cdp 记下来的那一笔调用（没被调过时**报错**，别让断言在 `None` 上打滑）。"""
    assert log.exists(), "假 cdp 一次都没被调到"
    return json.loads(log.read_text(encoding="utf-8"))


# ───────────────────────────── 落点：dir_for ─────────────────────────────


def test_the_job_directory_is_made_on_demand(tmp_path):
    """`<root>/<job_id>/`，**按需建**、且幂等（同一趟会问很多次）。"""
    root = tmp_path / "shots"
    where = shots.dir_for("job-20260917-1", root=root)
    assert where == root / "job-20260917-1"
    assert where.is_dir()
    assert shots.dir_for("job-20260917-1", root=root) == where, "再要一次不许炸"


def test_the_root_comes_from_the_env_when_the_caller_does_not_say(tmp_path, monkeypatch):
    """根可被 `SITEFORGE_SHOTS_DIR` 覆盖（运维改环境变量，不用改产物）；显式给的赢过它。"""
    monkeypatch.setenv("SITEFORGE_SHOTS_DIR", str(tmp_path / "from-env"))
    assert shots.dir_for("j") == tmp_path / "from-env" / "j"
    assert shots.dir_for("j", root=tmp_path / "explicit") == tmp_path / "explicit" / "j"


def test_the_default_root_is_runtime_shots_and_runtime_is_not_in_git(monkeypatch):
    """默认落点是仓库里的 `runtime/shots`，而 `runtime/` **不进 git**（与 `runtime/explore/` 同规矩）。

    图是运行产物：它占了地方、又是别人页面的像素，不该进版本库。
    """
    monkeypatch.delenv("SITEFORGE_SHOTS_DIR", raising=False)
    assert shots.DEFAULT_ROOT == ROOT / "runtime" / "shots"
    assert "runtime/" in (ROOT / ".gitignore").read_text(encoding="utf-8")


def test_path_for_only_resolves_and_touches_nothing_on_disk(tmp_path, monkeypatch):
    """**只解析、不建目录**的那个出口（复审 Important-2）。

    读路由（`GET /shot?…` / 列图）是**未鉴权**的：它要是一个 GET 就建一个目录，
    任何人拿一个 job_id 就能在盘上造目录 —— 所以读的那一侧必须有一个**不碰盘**的入口。
    这里钉的就是「它一个字节都没写」。
    """
    monkeypatch.setenv("SITEFORGE_SHOTS_DIR", str(tmp_path / "from-env"))

    where = shots.path_for("job-1")

    assert where == tmp_path / "from-env" / "job-1"
    assert not where.exists(), "只解析的那个出口**不许**建目录"
    assert not where.parent.exists()
    assert shots.path_for("job-1", root=tmp_path / "explicit") == tmp_path / "explicit" / "job-1"
    assert not (tmp_path / "explicit").exists()
    # 与写的那一侧同一条路径（同一个 job_id 不该出现在两个地方）
    assert shots.dir_for("job-1", root=tmp_path / "explicit") == tmp_path / "explicit" / "job-1"


def test_path_for_refuses_the_same_job_ids_as_dir_for(tmp_path):
    """两个出口过**同一份**判据：坏 job_id 在读的那一侧一样要报错（别在别处放它过去）。"""
    for bad in ("", ".", "..", "../x", "a/b", "/etc"):
        with pytest.raises(ValueError):
            shots.path_for(bad, root=tmp_path / "shots")
        with pytest.raises(ValueError):
            shots.dir_for(bad, root=tmp_path / "shots")
    assert not (tmp_path / "shots").exists()


def test_a_job_id_that_could_write_outside_the_root_is_refused(tmp_path):
    """job_id 只许是**一个**目录名：带斜杠或 `..` 就不是「参数写错了」，是**写到别处去**。

    这里**报错**，不静默改名 —— 静默改名会把图悄悄挪到一个没人找得到的地方。
    """
    root = tmp_path / "shots"
    for bad in ("", ".", "..", "../x", "a/b", "/etc", "j\n", "x" * 65):
        with pytest.raises(ValueError):
            shots.dir_for(bad, root=root)
    assert not (tmp_path / "x").exists(), "不许真的建到 root 外面去"
    assert not root.exists(), "一个都不许建"


# ───────────────────────────── 落点：name_ok ─────────────────────────────


def test_name_ok_takes_the_names_we_generate_and_nothing_else():
    """文件名判据（**唯一**一份）：`^[0-9A-Za-z._-]{1,64}\\.png$`。

    `/shot?name=` 收到的东西一律先过它 —— 它放行什么，就等于允许从哪个路径读文件。
    """
    for good in ("pause-1.png", "step-12-before.png", "a.png", "A_1.2-3.png"):
        assert shots.name_ok(good) is True, good
    for bad in ("../x.png", "a/b.png", "x.txt", "", ".", "..", "x.png/",
                "x.png ", "x.png\n", "x.PNG", "中文.png", "x" * 65 + ".png"):
        assert shots.name_ok(bad) is False, bad
    assert shots.name_ok("a" * 64 + ".png") is True, "64 个字符是上限以内"


# ──────────────────── MCP 那条路：capture_via_session ────────────────────


def test_session_capture_writes_the_bytes_and_returns_only_the_name(tmp_path):
    """成了：PNG 落盘，返回值是**文件名**（账本里永远不进字节）。"""
    dest = shots.dir_for("j", root=tmp_path) / "pause-1.png"
    session = _StubSession(_shot_result())

    name, why = shots.capture_via_session(session, dest)

    assert (name, why) == ("pause-1.png", "")
    assert isinstance(name, str) and not isinstance(name, bytes)
    assert dest.read_bytes() == PNG_1X1, "落盘的必须是解出来的那串字节"
    assert session.calls == [("screenshot", {})], "工具名与参数是这道门上的契约"
    assert sorted(p.name for p in dest.parent.iterdir()) == ["pause-1.png"], "不许留 .part"


def test_session_capture_never_throws_and_says_why(tmp_path):
    """桩 session 抛 `McpError` → `(None, 人话)`，**不抛**，而且人话里带着它说的那句。"""
    dest = shots.dir_for("j", root=tmp_path) / "pause-1.png"
    session = _StubSession(McpError("连不上 worker 的 CDP"))

    name, why = shots.capture_via_session(session, dest)

    assert name is None
    assert "没拍成" in why and "McpError" in why and "连不上 worker 的 CDP" in why, why
    assert list(dest.parent.iterdir()) == [], "没拍成就不许留下文件"


def test_session_capture_says_what_came_back_when_there_is_no_png(tmp_path):
    """回了 `{}` / 空串 / 压根不是一份结果 → 说出来**它到底回了什么**，别只说「失败」。"""
    dest = shots.dir_for("j", root=tmp_path) / "pause-1.png"

    for answer in ({}, {"png_base64": ""}, {"png_base64": None}, {"note": "今天不拍"}):
        name, why = shots.capture_via_session(_StubSession(answer), dest)
        assert name is None, answer
        assert "没拍成" in why and "png_base64" in why, why

    # 不是 dict：`McpSession.call_tool` 在没有结构化内容时回的是**文本**
    name, why = shots.capture_via_session(_StubSession("工具回了句人话"), dest)
    assert name is None and "没拍成" in why and "工具回了句人话" in why, why
    assert list(dest.parent.iterdir()) == []


def test_session_capture_rejects_base64_it_cannot_decode(tmp_path):
    """非法 base64 → `(None, 人话)`，且**不留半个文件**（解码之后就写，写之前先验）。"""
    dest = shots.dir_for("j", root=tmp_path) / "pause-1.png"

    for bad in ("not-base64!!", "aGVsbG8", "这不是 base64!!"):
        name, why = shots.capture_via_session(_StubSession({"png_base64": bad}), dest)
        assert name is None, bad
        assert "没拍成" in why and "base64" in why, why
        assert list(dest.parent.iterdir()) == [], "非法 base64 不许留下文件"


def test_session_capture_refuses_a_blob_that_is_not_a_png(tmp_path):
    """能解码但**不是 PNG**（比如一段 HTML）→ 说出来，别把一段 HTML 存成 `.png`。

    存下去的后果是「页面上一张裂图」—— 那与「这一屏没东西可看」长得一模一样。
    """
    dest = shots.dir_for("j", root=tmp_path) / "pause-1.png"
    not_png = base64.b64encode("<html>这是一个登录页</html>".encode("utf-8")).decode("ascii")

    name, why = shots.capture_via_session(_StubSession({"png_base64": not_png}), dest)

    assert name is None
    assert "没拍成" in why and "PNG" in why, why
    assert list(dest.parent.iterdir()) == []


def test_the_iend_check_tolerance_is_what_the_docstring_says(tmp_path):
    """把 `IEND` 那道闸的**实测边界**钉住（复审：机制要写准）。

    它是「够用就好」的一道闸，不是「整张解码」：切掉末尾 **1–4 字节**（`IEND` 后面那个
    chunk CRC）**仍放行**，切到 `IEND` 本身（**≥5 字节**）**拒**。
    够用的理由（核过内核）：`cmd/screenshot.go` 写的是 `shot.PNG()`，
    `internal/screenshot.go` 里它就是 CDP `CaptureScreenshot` 原样给的那串字节 ——
    不重编码、不尾随填充，所以那 ≤4 字节只在「写一半断了」时出现。

    ⚠️ 这是一条**表征测试**（先量后钉），不是先红后绿的那一类：它记的是实测事实。
    它存在的理由：这句话上一轮**写错过一次**（写成「截断则一定不在」）——
    改准还不够，得有东西在它**再次变假**时说话。
    """
    assert shots._png_trouble(PNG_1X1) == "", "整张图必须放行"
    for cut in (1, 2, 3, 4):
        assert shots._png_trouble(PNG_1X1[:-cut]) == "", f"切 {cut} 字节只丢尾 CRC，该放行"
    for cut in (5, 8, 40):
        assert "IEND" in shots._png_trouble(PNG_1X1[:-cut]), f"切 {cut} 字节该拒"


def test_session_capture_refuses_a_half_png(tmp_path):
    """能解码、magic 也对，但**没有写完**（没有 IEND 收尾）→ 一样拒绝。

    一张「缺了下半截」的图在页面上与一张完整的图长得几乎一样，而它会被人当证据用 ——
    所以判「完整」这件事得有判据，不能只看头几个字节。
    """
    dest = shots.dir_for("j", root=tmp_path) / "pause-1.png"
    half = base64.b64encode(PNG_1X1[:40]).decode("ascii")

    name, why = shots.capture_via_session(_StubSession({"png_base64": half}), dest)

    assert name is None
    assert "没拍成" in why and "IEND" in why, why
    assert list(dest.parent.iterdir()) == []


def test_session_capture_leaves_nothing_behind_when_the_write_fails(tmp_path):
    """写不进去（落点被一个目录占了）→ `(None, 人话)` + 不留 `.part`，也不许把占位的删掉。"""
    dest = shots.dir_for("j", root=tmp_path) / "pause-1.png"
    dest.mkdir()

    name, why = shots.capture_via_session(_StubSession(_shot_result()), dest)

    assert name is None
    assert "没拍成" in why and "pause-1.png" in why, why
    assert dest.is_dir(), "失败不许把占位的那个东西换掉"
    assert sorted(p.name for p in dest.parent.iterdir()) == ["pause-1.png"], "不许留 .part"


# ───────────────────── CLI 那条路：capture_via_cli ─────────────────────


def test_cli_capture_reads_the_ws_url_and_drops_stdout(tmp_path):
    """happy path：`ws_url` → `--host/--port`，`--out` 落盘，**stdout 丢进 /dev/null**。

    ⚠️ 真 cdp 的 `screenshot` **默认还往 stdout 吐一整条裸 base64**（`cmd/screenshot.go`：
    `--out` 不是「只写文件」，是「另外还写份文件」）。不丢，那串 base64 就顺着管道回到内存里 ——
    这一层「字节只进磁盘」的边界等于白立。
    """
    log = tmp_path / "call.json"
    dest = shots.dir_for("j", root=tmp_path) / "pause-1.png"
    cdp = _fake_cdp(tmp_path, mode="ok", log=log)

    name, why = shots.capture_via_cli("ws://10.0.0.9:9333/devtools/browser/abc", dest, cdp_bin=cdp)

    assert (name, why) == ("pause-1.png", "")
    assert dest.read_bytes() == PNG_1X1
    called = _called(log)
    assert called["argv"][:5] == ["--host", "10.0.0.9", "--port", "9333", "screenshot"], called
    assert called["argv"][-2:] == ["--out", str(dest) + ".part"], (
        called, "cdp 该写**临时名**（先验后换名）：目标名只由一次 replace 产生")
    assert called["stdout"] == "/dev/null", (
        "stdout 必须丢进 /dev/null，实际是 %r —— 不丢就等于把裸 base64 收回内存" % called["stdout"])


def test_cli_capture_never_throws_and_carries_what_cdp_said(tmp_path):
    """退出 1 → `(None, 人话)`，人话里有退出码**和它自己那句 stderr**。"""
    log = tmp_path / "call.json"
    dest = shots.dir_for("j", root=tmp_path) / "pause-1.png"
    cdp = _fake_cdp(tmp_path, mode="fail", log=log)

    name, why = shots.capture_via_cli("ws://worker:9333/x", dest, cdp_bin=cdp)

    assert name is None
    assert "没拍成" in why and "1" in why and "连不上 worker 的 CDP（假 cdp 说的）" in why, why
    assert not dest.exists()


def test_cli_capture_says_where_the_binary_was_supposed_to_be(tmp_path):
    """起不来的那条路（二进制不在）也要说清**它在哪**，并告诉运维怎么改。"""
    dest = shots.dir_for("j", root=tmp_path) / "pause-1.png"
    missing = tmp_path / "根本没有这个文件"

    name, why = shots.capture_via_cli("ws://worker:9333/x", dest, cdp_bin=missing)

    assert name is None
    assert "没拍成" in why and str(missing) in why and "SITEFORGE_CDP_BIN" in why, why


def test_cli_capture_refuses_an_unreadable_ws_url_and_never_points_at_localhost(tmp_path):
    """认不出 `ws_url` → `(None, 人话)`，**且一次子进程都不许起**。

    ⚠️ **绝不退回 `127.0.0.1:9222`**：那是本机的**另一个**浏览器（可能是别人的窗口 /
    一个 headless）。拍它会得到一张**看着像证据**的图 —— 比拍不到坏得多，
    所以这里既要「不起进程」，又要在人话里把这条规矩说出来。
    """
    log = tmp_path / "call.json"
    dest = shots.dir_for("j", root=tmp_path) / "pause-1.png"
    cdp = _fake_cdp(tmp_path, mode="ok", log=log)

    for bad in ("", None, "   ", "这不是个地址", "http://127.0.0.1:9222/json/version"):
        name, why = shots.capture_via_cli(bad, dest, cdp_bin=cdp)
        assert name is None, bad
        assert "没拍成" in why and "127.0.0.1:9222" in why, why

    assert not log.exists(), "认不出 ws_url 就不许起子进程 —— 一起就是去拍别的浏览器"
    assert not dest.exists()


def test_cli_capture_does_not_believe_a_zero_exit_that_wrote_nothing(tmp_path):
    """**信文件，不信退出码**：退出 0 但没写文件 = 「一半成功」，跟成功长得一样。

    另一面同等重要（复审 Important-1）：这种情况下**旧图不许被删** —— 它只是这一趟没被
    更新，删掉就成了「新的没成、旧的也没了」。返回 `(None, 人话)` 已经足够：调用方
    不会把这个名字记进这一趟的账本。
    """
    log = tmp_path / "call.json"
    dest = shots.dir_for("j", root=tmp_path) / "pause-1.png"
    cdp = _fake_cdp(tmp_path, mode="silent", log=log)

    name, why = shots.capture_via_cli("ws://worker:9333/x", dest, cdp_bin=cdp)
    assert name is None
    # 人话里要**全路径**：只给一个 `pause-1.png`，运维拿着这句话在盘上找不到那张图
    assert "没拍成" in why and "0" in why and str(dest) in why, why
    assert not dest.exists()

    dest.write_bytes(b"stale png from an earlier run")   # 上一趟留下来的同名文件
    name, why = shots.capture_via_cli("ws://worker:9333/x", dest, cdp_bin=cdp)
    assert name is None, "这一趟没成，就不许报成"
    assert dest.read_bytes() == b"stale png from an earlier run", "失败的这一趟**不许删掉**旧图"
    assert sorted(p.name for p in dest.parent.iterdir()) == ["pause-1.png"], "不许留临时文件"


def test_cli_capture_refuses_a_half_written_png(tmp_path):
    """**半张图**（写完一半就断了）不许被放到目标名上 —— 这是复审 Important-1 的核心。

    假 cdp 写的是真 PNG 的**前 40 字节**（magic 与 IHDR 都在，就是没有 IEND）并**退出 0**：
    退出码说「成了」，文件也在，只有「它是不是一张完整的图」这一条能戳穿它。
    """
    log = tmp_path / "call.json"
    dest = shots.dir_for("j", root=tmp_path) / "pause-1.png"
    half = PNG_1X1[:40]
    cdp = _fake_cdp(tmp_path, mode="ok", log=log, body=half)

    name, why = shots.capture_via_cli("ws://worker:9333/x", dest, cdp_bin=cdp)

    assert name is None
    assert "没拍成" in why and "IEND" in why, why
    assert not dest.exists(), "半张图不许出现在目标名上"
    assert sorted(p.name for p in dest.parent.iterdir()) == [], "临时文件也要清掉"


def test_a_failed_name_swap_leaves_the_target_alone_and_no_leftovers(tmp_path, monkeypatch):
    """**要么完整地出现在目标名上，要么什么都没有** —— 落盘原子性的那条断言（复审 Minor-4）。

    用故障注入钉住最后一步：把 `os.replace` 弄挂（真实世界里它真会挂：跨设备、权限、
    目标被并发换掉）。目标是**只由这一次 replace 产生**的，所以它必须**仍然不存在**；
    临时文件也必须被清掉。

    ⚠️ 这条测试的判别力在于：把「先写临时名再换名」换成直接 `dest.write_bytes()`（复审的
    M3 变异），目标是会被直接写出来的 —— 于是这条**红**。没有它，原子性只是注释里的一句话。
    """
    dest = shots.dir_for("j", root=tmp_path) / "pause-1.png"
    cdp = _fake_cdp(tmp_path, mode="ok", log=tmp_path / "call.json")

    def boom(*_args, **_kwargs):
        raise OSError(18, "Invalid cross-device link")

    monkeypatch.setattr(os, "replace", boom)

    name, why = shots.capture_via_session(_StubSession(_shot_result()), dest)
    assert name is None and "没拍成" in why, why
    assert not dest.exists(), "换名没成，目标名就不该出现（直接写目标名会留一张半成品）"
    assert sorted(p.name for p in dest.parent.iterdir()) == [], "不许留下临时文件"

    name, why = shots.capture_via_cli("ws://worker:9333/x", dest, cdp_bin=cdp)
    assert name is None and "没拍成" in why, why
    assert not dest.exists()
    assert sorted(p.name for p in dest.parent.iterdir()) == []


def test_cli_capture_gives_up_after_the_timeout(tmp_path):
    """窗口卡住时的最坏形态是「一直挂着」—— 超时必须生效，并说清它卡在哪。"""
    log = tmp_path / "call.json"
    dest = shots.dir_for("j", root=tmp_path) / "pause-1.png"
    cdp = _fake_cdp(tmp_path, mode="slow", log=log)

    started = time.monotonic()
    name, why = shots.capture_via_cli("ws://worker:9333/x", dest, cdp_bin=cdp, timeout=0.5)

    assert name is None
    assert "没拍成" in why and "超时" in why and "0.5" in why, why
    assert time.monotonic() - started < 4, "不许真的等它睡完（假 cdp 睡 5 秒）"


def test_cli_capture_makes_the_destination_directory_on_demand(tmp_path):
    """落点的目录按需建（`dir_for` 建过，但直接给一条深路径也不该失败）。"""
    log = tmp_path / "call.json"
    dest = tmp_path / "runtime" / "shots" / "j" / "step-3-before.png"
    cdp = _fake_cdp(tmp_path, mode="ok", log=log)

    name, why = shots.capture_via_cli("ws://worker:9333/x", dest, cdp_bin=cdp)

    assert (name, why) == ("step-3-before.png", "")
    assert dest.read_bytes() == PNG_1X1


# ───────────── ws_url → host/port：**一份**，两处调用点能反过来用 ─────────────


def test_host_port_reads_both_shapes_the_two_existing_call_sites_read():
    """Bit 真给的串长这样（`ws://host:port/devtools/browser/...`），手抄的常常没有协议头。"""
    assert shots.host_port("ws://10.0.0.9:9333/devtools/browser/abc") == ("10.0.0.9", "9333")
    assert shots.host_port("wss://worker-3.local:443/devtools/browser/x") == ("worker-3.local", "443")
    assert shots.host_port("ws://worker:9333") == ("worker", "9333"), "没有路径"
    assert shots.host_port("worker:9333") == ("worker", "9333"), "没有协议头"
    assert shots.host_port("ws://worker/") == ("worker", "9222"), "端口缺省 = CDP 的老默认"


def test_host_port_says_none_instead_of_guessing():
    """认不出来给 `None`，**不猜**（猜出来的那个 host 会去拍另一个浏览器）。"""
    for bad in ("", None, "   ", "这不是个地址", "http://h:9222/json", "ws://:9222/x",
                "ws://h:port/x"):
        assert shots.host_port(bad) is None, bad


def test_neither_capture_throws_on_a_destination_it_cannot_use(tmp_path):
    """契约第一条是「**都不抛**」—— 连落点本身不可用（路径里有 NUL、或者干脆没有文件名）时也一样：

    说人话、给 `None`，**不把异常丢给调用方**（拍照是旁路，它不许把探路搞挂）。

    ⚠️ 「没有文件名」那一格（`""` / `.` / `/` / `./`）是收尾轮的复审抓到的**契约破坏**：
    临时名是 `dest.with_name(dest.name + ".part")` 算出来的，而 `pathlib` 对这几个输入
    直接抛 `ValueError`（`'.' has an empty name`）—— 若这个计算发生在 `try` 之外，
    整条 `capture_via_cli` 就**无条件**破了「不抛」。现在这两条路都在动手之前先过这一格。
    """
    log = tmp_path / "call.json"
    cdp = _fake_cdp(tmp_path, mode="ok", log=log)

    name, why = shots.capture_via_session(_StubSession(_shot_result()), "\0坏路径")
    assert name is None and "没拍成" in why, why

    name, why = shots.capture_via_cli("ws://worker:9333/x", "\0坏路径", cdp_bin=cdp)
    assert name is None and "没拍成" in why, why
    assert not log.exists(), "落点都不对，就不该起进程"

    for nameless in ("", ".", "/", "./"):
        session = _StubSession(_shot_result())
        name, why = shots.capture_via_session(session, nameless)
        assert name is None and "没拍成" in why, (nameless, why)
        assert session.calls == [], "落点都不对，就别去拍（那一下是真的开销）"

        name, why = shots.capture_via_cli("ws://worker:9333/x", nameless, cdp_bin=cdp)
        assert name is None and "没拍成" in why, (nameless, why)
    assert not log.exists(), "落点连文件名都没有，更不该起进程"


def test_the_shared_parser_agrees_with_the_call_site_it_is_meant_to_replace():
    """抽出来的这一份必须**能反过来给那两处用** —— 这条测试就是那句话的证据。

    `selftest._host_port()` 是那两处里能直接调的一个（`Service.live_viewport()` 要真窗口，
    这一页调不动），Bit 真给的串逐个比一遍：**逐字一致**才算能替。

    唯一**故意**不一致的是「认不出来」那一格：`_host_port` 退回本机默认
    （`127.0.0.1:9222`），而截图这条路**必须**给 `None` —— 退回本机是去拍别的浏览器。
    """
    for url in ("ws://10.0.0.9:9333/devtools/browser/abc",
                "wss://worker-3.local:443/devtools/browser/x",
                "ws://worker:9333", "worker:9333", "ws://worker/"):
        assert tuple(shots.host_port(url)) == tuple(selftest._host_port(url)), url
    assert shots.host_port("") is None
    assert selftest._host_port("") == ("127.0.0.1", "9222"), "那一格是它自己的默认值，别动它"
