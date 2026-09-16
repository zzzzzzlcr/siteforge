"""Task 1（console 最小片）：截图**落盘** —— 图进磁盘，账本里只留**文件名**。

## 为什么要有这一层

时间线要显示「它现在在干什么」，靠的是一张张图；而图一旦以 base64 的形态走进账本
（`Journey` / `/live` / 内存），一趟探路就会拖上几 MB 的字符串 —— 页面卡、日志读不动、
`runtime/` 之外的每一处都在为同一张图重复付代价。所以这里立一条边界：

    **字节只许进磁盘，账本里只许有文件名。**

这条纪律在 agent 侧已经有一份（`browser_agent._summarize` 对 `screenshot` 只记长度、
`tools.tool_message_content` 把 base64 换成 `image_url`）；这一层是**服务侧**的那一份。

## 契约（比实现重要）

1. **两个 `capture_*` 都不抛**。拍照是**旁路**：拍不成，探路照常往下走 ——
   抛出去的话，一次截图失败就能把整趟探路搞挂。
2. **返回值是文件名，永远不是字节**：`("pause-1.png", "")`。
3. **没成 → `(None, 人话)`**：说清是**哪个东西**出的问题、**它说了什么**。
   **不许**返回空串、不许返回一句光秃秃的 `None` —— 沉默的失败比失败坏得多，
   而这句话是要**原样显示给运营**的。

## 两条落盘路

| 路 | 谁在用 | 怎么拍 |
|---|---|---|
| `capture_via_session` | 探路中的 agent（MCP 会话已在手） | `session.call_tool("screenshot")` → `png_base64` |
| `capture_via_cli` | 服务 / 复验（只有 `ws_url`，没有会话） | `cdp --host H --port P screenshot --out F` |

CLI 那条**必须把 stdout 丢进 /dev/null**：内核的 `cmd/screenshot.go` 默认往 stdout
吐**一整条裸 base64**（那里的 `--out` 是「另外还写份文件」，不是「只写文件」），
不丢就等于绕开这一层的边界，把几 MB 的字符串顺着管道收回内存里。

## ws_url → host/port：**一份**，不是三份

`Service.live_viewport()` 与 `selftest._host_port()` 各有一份自己的拆法，这里抽成
`host_port()`，**两处都能反过来用它**（`_host_port` 写成 `host_port(...) or ("127.0.0.1","9222")`；
`live_viewport` 直接取那一对值）。⚠️ 与那两处**故意不同**的一格：**认不出来给 `None`**，
**绝不**退回 `127.0.0.1:9222` —— 那是去拍**别的**浏览器（本机的另一个窗口 / 一个 headless），
拍出来的图**看着像证据**，比拍不到坏得多。把那两处改成调用这里的函数，留给动
`service.py` / `selftest.py` 的任务（这一片只许动本文件与 `tests/test_shots.py`）。
"""

from __future__ import annotations

import base64
import os
import pathlib
import re
import subprocess

__all__ = ["DEFAULT_ROOT", "dir_for", "name_ok", "host_port",
           "capture_via_session", "capture_via_cli"]

_REPO = pathlib.Path(__file__).resolve().parents[1]

#: 运行产物落在哪（`runtime/` 不进 git；与 `runtime/explore/`、`runtime/selftest/` 同层不同目录）。
#: 根可被 `SITEFORGE_SHOTS_DIR` 覆盖 —— 运维改环境变量，不用改产物（与 `SITEFORGE_CDP_BIN` 同一套约定）。
DEFAULT_ROOT = _REPO / "runtime" / "shots"

#: 文件名的形状。**唯一**一份判据：落盘时生成的名字与 `/shot?name=` 收的名字都过它。
#: 用 `fullmatch`（不是 `match`）：`$` 会放过结尾那个换行，而换行能出现在文件名里。
NAME_RE = re.compile(r"^[0-9A-Za-z._-]{1,64}\.png$")

#: 一个 job_id 就是一个目录名：单段。`..` / `.` 单独另判（它们过得了字符表，但那是往上跳）。
_JOB_RE = re.compile(r"^[0-9A-Za-z._-]{1,64}$")

#: PNG 的 magic。这一步是「别把一段 HTML 存成 .png」的下限 —— 存下去的形态是一张裂图，
#: 而裂图与「这一屏没什么可看的」在页面上长得一模一样。
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

#: 没拍成的那句话都从这儿开头（日志里一眼捞得出来，页面上也一眼看得出这是**没成**）。
_FAIL = "这张图没拍成："

#: ws_url 的协议头（可有可无：Bit 给的串有，人手抄的常常没有）。
_WS_SCHEME_RE = re.compile(r"^wss?://", re.IGNORECASE)

#: host 得真的像一个 host（主机名 / IPv4）。带空格的、中文的、`http:` 都不是。
_HOST_RE = re.compile(r"^[A-Za-z0-9._-]+$")

#: 端口缺省值（CDP 的老默认，与 `selftest._host_port()` 一致）。
_DEFAULT_PORT = "9222"


def _root(root=None) -> pathlib.Path:
    """这次用哪个根：显式给的 > `SITEFORGE_SHOTS_DIR` > 仓库里的 `runtime/shots`。"""
    return pathlib.Path(root or os.environ.get("SITEFORGE_SHOTS_DIR") or DEFAULT_ROOT)


def dir_for(job_id: str, *, root=None) -> pathlib.Path:
    """`<root>/<job_id>/`，**按需**建（同一趟会问很多次，得幂等）。

    job_id 只许是**一个**目录名：带斜杠或 `..` 的 id 能让图落到 `runtime/shots/` 外面去 ——
    那不是「参数写错了」，那是**写到别处去**，所以这里**报错**（`ValueError`），
    不静默改名（改名会把图悄悄挪到一个没人找得到的地方）。
    """
    text = str(job_id or "")
    if not _JOB_RE.fullmatch(text) or text in (".", ".."):
        raise ValueError(
            "job_id 得是单个目录名（字母数字与 . _ -，1–64 字）：%r —— "
            "带斜杠或 .. 的 id 会把图写到 runtime/shots/ 外面去" % (job_id,))
    where = _root(root) / text
    where.mkdir(parents=True, exist_ok=True)
    return where


def name_ok(name: str) -> bool:
    """这个名字能不能当一张图的名字（`/shot?name=` 收到的东西一律先过这里）。"""
    return NAME_RE.fullmatch(str(name or "")) is not None


def host_port(ws_url) -> tuple[str, str] | None:
    """`ws://host:port/...` → `("host", "port")`；**认不出来给 `None`**。

    规则是那两份现成拆法的**并集**：协议头可有可无，端口缺省 `9222`。
    但 host **必须真的像一个 host**：空的、带空格的、`http://` 开头的、端口不是数字的 → `None`。

    ⚠️ 调用方**不许**拿 `None` 去退回 `127.0.0.1:9222`：那是本机的**另一个**浏览器
    （可能是别人的窗口，也可能是个 headless），拍它会得到一张**看着像证据**的图。
    """
    text = str(ws_url or "").strip()
    if not text:
        return None
    head = _WS_SCHEME_RE.sub("", text).split("/", 1)[0]
    host, sep, port = head.partition(":")
    if not _HOST_RE.match(host):
        return None
    if sep:
        return (host, port) if port.isdigit() else None
    return host, _DEFAULT_PORT


def _snippet(value, limit: int = 200) -> str:
    """把「它到底回了什么」压成一句能进人话的短句（**截断了就说截断了**）。"""
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[:limit] + "…"


def _why(exc: BaseException) -> str:
    """一个异常的**人话**：类型 + 它自己那句话（它没说话就别留个孤零零的冒号）。"""
    said = _snippet(exc, 300)
    return f"{type(exc).__name__}：{said}" if said else type(exc).__name__


def _write_png(dest: pathlib.Path, png: bytes) -> str:
    """原子落盘（先写 `.part` 再 rename）。成了给 `""`，没成给**人话**。

    为什么要原子：失败留下半个文件的话，看图的人会拿一张**缺了下半截**的图当证据 ——
    「有一张图」与「有一张完整的图」在页面上长得一样。
    """
    part = dest.with_name(dest.name + ".part")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        part.write_bytes(png)
        os.replace(part, dest)
    except (OSError, ValueError) as exc:           # ValueError：路径本身不可用（NUL 之类）
        try:
            part.unlink()
        except (OSError, ValueError):              # 收尾失败**不许**盖掉上面那条人话
            pass
        return f"{_FAIL}写不进 {dest}（{_why(exc)}）"
    return ""


def capture_via_session(session, dest) -> tuple[str | None, str]:
    """用**已经在手的** MCP 会话拍一张，落到 `dest`；返回 `(文件名, "")` 或 `(None, 人话)`。

    `dest` 是**完整的落点**（`dir_for(job_id) / "step-3-before.png"` 那种），
    返回的只有它的**文件名** —— 账本里存名字，字节只在磁盘上。
    **不抛**：会话是外部世界，它什么都可能抛（见模块头第 1 条纪律）。
    """
    dest = pathlib.Path(dest)
    try:
        shot = session.call_tool("screenshot", {})
    except Exception as exc:                       # noqa: BLE001 —— 外部世界，什么都可能抛
        return None, f"{_FAIL}调截图工具时它报错了（{_why(exc)}）"

    if not isinstance(shot, dict):
        return None, (f"{_FAIL}截图工具回的不是一份结果（{type(shot).__name__}："
                      f"{_snippet(shot)}）")
    b64 = shot.get("png_base64")
    if not isinstance(b64, str) or not b64.strip():
        return None, (f"{_FAIL}截图工具没回 png_base64（它回的键：{sorted(shot)}）")
    try:
        png = base64.b64decode(b64.strip(), validate=True)
    except ValueError as exc:                      # binascii.Error 也是 ValueError
        return None, (f"{_FAIL}png_base64 不是合法的 base64（{len(b64)} 个字符，{_why(exc)}）")
    if not png.startswith(_PNG_MAGIC):
        return None, (f"{_FAIL}解出来的不是 PNG（头几字节是 {png[:8]!r}，"
                      f"PNG 该是 {_PNG_MAGIC!r}）")
    trouble = _write_png(dest, png)
    return (None, trouble) if trouble else (dest.name, "")


def _cdp_bin(cdp_bin=None) -> str:
    """cdp 二进制在哪（与 `service.live_viewport()` 同一套约定：参数 > `SITEFORGE_CDP_BIN`
    > `CDP_PATH` > 仓库里的 `tools/cdp/cdp`）。"""
    return str(cdp_bin or os.environ.get("SITEFORGE_CDP_BIN") or os.environ.get("CDP_PATH")
               or (_REPO / "tools" / "cdp" / "cdp"))


def capture_via_cli(ws_url, dest, *, cdp_bin=None, timeout: float = 30.0) -> tuple[str | None, str]:
    """没有会话、只有 `ws_url` 时的那条路：`cdp --host H --port P screenshot --out D`。

    ⚠️ **stdout 丢进 /dev/null**：内核的 `screenshot` 默认往 stdout 吐裸 base64
    （`cmd/screenshot.go`），`--out` 不是「只写文件」，是「另外还写份文件」。

    失败都**看得见**：认不出 ws_url（**绝不**退回本机）、起不来（二进制不在）、
    它自己说没成（退出码 + stderr）。外加一条最容易骗人的：**退出码 0 但没写文件** ——
    那是「一半成功」，跟成功长得一样，所以这里**信文件，不信退出码**，
    而且起进程前先把落点清掉（否则上一趟的同名旧图会冒充这一趟的证据）。
    """
    dest = pathlib.Path(dest)
    pair = host_port(ws_url)
    if pair is None:
        return None, (f"{_FAIL}认不出 ws_url 里的 host/port（{ws_url!r}）—— 这里**不会**"
                      "退回 127.0.0.1:9222：那是去拍本机的**另一个**浏览器，拍出来的图会"
                      "看着像证据。请给 `bit.sh open` 吐出来的那个 ws_url。")
    host, port = pair
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.unlink(missing_ok=True)               # 清掉同名旧文件（它不许冒充这一趟）
    except (OSError, ValueError) as exc:
        return None, f"{_FAIL}动不了落点 {dest}（{_why(exc)}）"

    argv = [_cdp_bin(cdp_bin), "--host", host, "--port", port, "screenshot", "--out", str(dest)]
    try:
        done = subprocess.run(argv, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, (f"{_FAIL}cdp screenshot 等了 {timeout} 秒还没回来（超时）—— "
                      "窗口可能卡住了")
    except (OSError, ValueError) as exc:
        return None, (f"{_FAIL}起不来 cdp（{_cdp_bin(cdp_bin)}）：{_why(exc)} —— "
                      "设 SITEFORGE_CDP_BIN 指到那个二进制")

    if done.returncode != 0:
        said = _snippet(done.stderr or "它什么都没说", 300)
        return None, f"{_FAIL}cdp screenshot 退出码 {done.returncode}，它说：{said}"
    if not dest.is_file() or dest.stat().st_size == 0:
        return None, (f"{_FAIL}cdp 说成了（退出码 0），但 {dest} 没有（或 0 字节）—— "
                      "这中间有一步在说谎，别信这张图")
    return dest.name, ""
