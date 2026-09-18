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

**两条路都「先写临时名（`<dest>.part`）→ 验过 → 换名」**：目标名只由一次 `os.replace`
产生，于是「一张完整的图」与「旧图原样还在」之间没有第三种状态 —— 不会留下半张图，
失败时也不会把旧图删掉。CLI 那条因此**不再需要信任退出码**：算数的是临时文件里
是不是一张完整的 PNG（非空 + magic + `IEND` 收尾）。

## 降级 B 那个开关：**一份读法**，不是两份（2026-09-18 裁定）

`SITEFORGE_STEP_SHOTS=0` 这条开关**有两个读者**：探路的步拍（`browser_agent.explore`，
它决定「拍不拍」）与页面上的那句话（`service.Service.shots_note()`，它解释「为什么没有图」）。
两处各自解析就是两份口径（一处 strip 一处不 strip），于是会出现
「图没了、一个字没解释」—— 那正是设计注 §5.5 明令禁止的**静默降级**。
所以读法**只有** `step_shots_on()` 这一处，两个读者都走它。

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

__all__ = ["DEFAULT_ROOT", "root_for", "cdp_bin_for", "cdp_bin_with_source", "path_for",
           "dir_for", "name_ok", "host_port", "step_shots_on", "STEP_SHOTS_ENV",
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


def root_for(root=None) -> pathlib.Path:
    """`_root` 的正身（给**别的模块**用）。

    为什么要把它露出来（Task 3）：服务要把根**在构造时**定下来（`Service(shots_dir=...)`
    —— 部署配置不该在服务跑起来之后跟着环境变量变），而不是每次落盘时再解析一遍。
    解析只有一处，规矩与 `step_shots_on()` 一样：**一份读法**。
    """
    return _root(root)


def path_for(job_id: str, *, root=None) -> pathlib.Path:
    """`<root>/<job_id>/` —— **只解析，不碰盘**（一个字节都不写）。

    **读**的那一侧（列图、`GET /shot?…`）要用这个：那些请求是**未鉴权**的，
    用 `dir_for` 就等于「一个 GET 建一个目录」—— 谁拿一个 job_id 都能在盘上造目录。

    job_id 只许是**一个**目录名：带斜杠或 `..` 的 id 能让图落到 `runtime/shots/` 外面去 ——
    那不是「参数写错了」，那是**写到别处去**，所以这里**报错**（`ValueError`），
    不静默改名（改名会把图悄悄挪到一个没人找得到的地方）。⚠️ 这是**响的**失败，
    不是沉默的失败：调用方（Task 3 的路由）该先过一道 / `catch` 成 400，
    而不是把它读成「这一趟没有图」。
    """
    text = str(job_id or "")
    if not _JOB_RE.fullmatch(text) or text in (".", ".."):
        raise ValueError(
            "job_id 得是单个目录名（字母数字与 . _ -，1–64 字）：%r —— "
            "带斜杠或 .. 的 id 会把图写到 runtime/shots/ 外面去" % (job_id,))
    return _root(root) / text


def dir_for(job_id: str, *, root=None) -> pathlib.Path:
    """`path_for()` + **按需建**（同一趟会问很多次，得幂等）—— **写**的那一侧用这个。"""
    where = path_for(job_id, root=root)
    where.mkdir(parents=True, exist_ok=True)
    return where


def name_ok(name: str) -> bool:
    """这个名字能不能当一张图的名字（`/shot?name=` 收到的东西一律先过这里）。"""
    return NAME_RE.fullmatch(str(name or "")) is not None


#: **降级 B 的开关**（设计注 §5.5）：`SITEFORGE_STEP_SHOTS=0` ⇒ 关掉每步抓拍，只留闸拍。
#: 为什么默认**开**：真窗口上量过 —— 一张 **中位 193ms / 110 KB**（17 次真跑实测），
#: 远在 1.5s 那道门槛之下。这个开关是留给「哪天它变贵了」的退路，不是现在的默认。
STEP_SHOTS_ENV = "SITEFORGE_STEP_SHOTS"


def step_shots_on(env=None) -> bool:
    """每步抓拍开着吗 —— **唯一**一份读法（两个读者都走它）。

    为什么要抽出来（上一轮复审点名、2026-09-18 裁定）：这条开关**有两个读者** ——
    探路的步拍（`browser_agent.explore`）与页面上的那句话（`Service.shots_note()`）。
    两处各自解析就是两份口径，而它的后果**不是**「开关偶尔不灵」，是
    **「图没了、一个字没解释」** —— 设计注 §5.5 明令禁止的静默降级。
    判据：**翻这个开关时，「图没了」与「有人解释为什么」必须同时发生或同时不发生。**

    **只有恰好 `"0"` 才算关**（`"1"` / `""` / 没设 / `"0.0"` 都照拍）：判据写宽了
    （比如「非空就关」）会把一个**明确要求开着**的部署反着关掉 —— 比没有这个开关更坏。
    """
    env = os.environ if env is None else env
    return str(env.get(STEP_SHOTS_ENV, "") or "").strip() != "0"


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


def _drop(path: pathlib.Path) -> None:
    """把临时文件清掉。清不掉**不抛** —— 收尾失败不许盖掉上面那条人话。"""
    try:
        path.unlink()
    except (OSError, ValueError):
        pass


def _nameless(dest: pathlib.Path) -> str:
    """落点**连文件名都没有**（`""` / `.` / `/` / `./`）→ 人话；正常 → `""`。

    这一格必须在**动手之前**过：临时名是 `dest.with_name(dest.name + ".part")` 算出来的，
    而 `pathlib` 对这几个输入直接抛 `ValueError: PosixPath('.') has an empty name` ——
    放在 `try` 外面就等于**无条件**破掉「两个 capture 都不抛」那条契约
    （2026-09-17 收尾轮复审抓到的；`fe6f893` 那版这四个输入都是返回人话的）。
    """
    if dest.name:
        return ""
    return (f"{_FAIL}这个落点连文件名都没有（{str(dest)!r}）—— 落点要 "
            "`dir_for(job_id) / \"pause-1.png\"` 那样带文件名的完整路径")


def _png_trouble(blob: bytes) -> str:
    """这串字节像不像一张**完整**的 PNG。像 → `""`，不像 → **人话**（说清缺什么）。

    三道：非空 / PNG magic / 末尾 16 字节里有 `IEND` 收尾。第三道是「半张图」的判据 ——
    写一半断掉的 PNG **头几个字节是对的**，只看 magic 会把一张缺了下半截的图放过去，
    而它在页面上与一张完整的图长得几乎一样，还会被人当证据用。

    ⚠️ 它的**实测**边界（别把它读强了）：切掉末尾 **1–4 字节**仍会放行 —— 那 4 个字节是
    `IEND` 后面那个 chunk CRC，丢了不影响 Decode（`cmd/screenshot.go` 写的是 Chrome 给的
    原始字节，不尾随填充，所以这一格只在「写一半断了」时出现）；切到 `IEND` 本身
    （**≥5 字节**）才拒。要更强就得整张解码，代价不成比例 —— 这里要挡的是
    「缺了下半截的图被当成证据」，不是「末尾 CRC 坏了一位」。
    """
    if not blob:
        return "它是空的（0 字节）"
    if not blob.startswith(_PNG_MAGIC):
        return f"它头几字节是 {blob[:8]!r}，PNG 该是 {_PNG_MAGIC!r}"
    if b"IEND" not in blob[-16:]:
        return "它末尾没有 IEND 收尾 —— 像一张**没写完**的图"
    return ""


def _promote(tmp: pathlib.Path, dest: pathlib.Path) -> str:
    """临时文件 → 正式落点：**先验后换名**。成了给 `""`，没成给**人话**（并清掉临时文件）。

    这是 CLI 那条路**唯一**算数的判据：`cdp` 的退出码只说明它**觉得自己**成了，
    真正算数的是「临时文件里是不是一张完整的图」—— 信文件，不信退出码。

    目标名**只由这一次 `os.replace` 产生**，于是「新的完整图」与「旧图原样还在」之间
    没有第三种状态（不会出现「写了一半的新图把旧图顶掉」）。
    """
    try:
        blob = tmp.read_bytes()
    except (OSError, ValueError) as exc:
        return f"{_FAIL}读不回刚写下的临时文件 {tmp}（{_why(exc)}）—— {dest} 没被动过"
    trouble = _png_trouble(blob)
    if trouble:
        _drop(tmp)
        return f"{_FAIL}刚写下的不是一张完整的 PNG：{trouble}（{dest} 没被动过）"
    try:
        os.replace(tmp, dest)
    except (OSError, ValueError) as exc:
        _drop(tmp)
        return f"{_FAIL}换名成 {dest} 失败（{_why(exc)}）"
    return ""


def _store(dest: pathlib.Path, png: bytes) -> str:
    """字节已经在手：**先写临时名 → 验过 → 换名**。成了给 `""`，没成给**人话**。

    为什么不直接 `dest.write_bytes()`：失败留下半个文件的话，看图的人会拿一张
    **缺了下半截**的图当证据 —— 「有一张图」与「有一张完整的图」在页面上长得一样。
    """
    trouble = _png_trouble(png)
    if trouble:
        return f"{_FAIL}解出来的不是一张完整的 PNG：{trouble}"
    tmp = dest.with_name(dest.name + ".part")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(png)
    except (OSError, ValueError) as exc:           # ValueError：路径本身不可用（NUL 之类）
        _drop(tmp)
        return f"{_FAIL}写不进 {dest}（{_why(exc)}）"
    return _promote(tmp, dest)


def capture_via_session(session, dest) -> tuple[str | None, str]:
    """用**已经在手的** MCP 会话拍一张，落到 `dest`；返回 `(文件名, "")` 或 `(None, 人话)`。

    `dest` 是**完整的落点**（`dir_for(job_id) / "82ece6-step-3-before.png"` 那种 ——
    步拍的名字带**本趟标记**（6 位 hex），形状见 `browser_agent._StepShots` 的类注释），
    返回的只有它的**文件名** —— 账本里存名字，字节只在磁盘上。
    **不抛**：会话是外部世界，它什么都可能抛（见模块头第 1 条纪律）。
    """
    dest = pathlib.Path(dest)
    trouble = _nameless(dest)
    if trouble:                                    # 落点就不对 → 别去拍（那一下是真的开销）
        return None, trouble
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
    trouble = _store(dest, png)
    return (None, trouble) if trouble else (dest.name, "")


def _cdp_bin(cdp_bin=None) -> str:
    """cdp 二进制在哪（与 `service.live_viewport()` 同一套约定：参数 > `SITEFORGE_CDP_BIN`
    > `CDP_PATH` > 仓库里的 `tools/cdp/cdp`）。

    ⚠️ **这里不许有自己的 `or` 链**（修复轮 4 的 G4）：这条链曾经有**两份实现** ——
    这一份与 `cdp_bin_with_source()` 那份，而 `capture_via_cli` 走的是**这一份**、
    `/health` 走的是**那一份**。复审拿 6 组输入比过：今天 0 处不一致，**但那是运气** ——
    它正是这个仓库最老的那条病（同一个判据两份实现，早晚分家）。现在它只是
    `cdp_bin_with_source` 的第 0 个返回值：**一份读法**（与 `step_shots_on` 同一条规矩）。
    """
    return cdp_bin_with_source(cdp_bin)[0]


def cdp_bin_with_source(cdp_bin=None) -> tuple[str, str]:
    """`cdp_bin_for` 的「**哪一跳赢了**」版本：`(路径, 那一跳的名字)`。

    为什么要两个返回值（修复轮 3 的 F5）：`/health` 报的必须是服务**真正会用的那个**
    （上一轮做的），而运维看到它会问的下一个问题是「**为什么**是这个」——
    答案就是这条链上赢的那一跳。一次解析、两个产物；**不许**在 `/health` 那里再解析一次
    （那正是刚关掉的那条「读活环境」的口子换了个形式）。
    名字只有这四个（稳定键，测试与页面都认它们）：
    `"capture_bin"` / `"SITEFORGE_CDP_BIN"` / `"CDP_PATH"` / `"repo-default"`。
    """
    if cdp_bin:
        return str(cdp_bin), "capture_bin"
    env = os.environ.get("SITEFORGE_CDP_BIN")
    if env:
        return str(env), "SITEFORGE_CDP_BIN"
    env = os.environ.get("CDP_PATH")
    if env:
        return str(env), "CDP_PATH"
    return str(_REPO / "tools" / "cdp" / "cdp"), "repo-default"


def cdp_bin_for(cdp_bin=None) -> str:
    """`_cdp_bin` 的正身（给**别的模块**用）。

    为什么要把它露出来（Task 3 修复轮 1）：服务要把用哪个二进制**在构造时**定下来
    （`Service(capture_bin=...)`）—— 而 `capture_via_cli` 是从**环境**读的，
    读的时刻就是调用的时刻。抓拍跑在**工作线程**上，它可能比给它设环境的那段代码活得久
    （实测：全量套件里**每趟 2–4 次**（竞态量：取决于哪几条「发了 job 不等它」的用例的
    worker 活过了它的 fixture）—— 那一刻子进程里 `SITEFORGE_CDP_BIN` 已经是空的，
    于是回退链落回**仓库里那个真二进制**）。
    **定死了就没有「以后再看一眼环境」这回事。**

    它和 `cdp_bin_with_source` 是**同一**条链（后者多报一个「哪一跳赢了」）——
    `_cdp_bin` 只是这条链的旧名字，别再在别处复制第三份。
    """
    return cdp_bin_with_source(cdp_bin)[0]


def capture_via_cli(ws_url, dest, *, cdp_bin=None, timeout: float = 30.0) -> tuple[str | None, str]:
    """没有会话、只有 `ws_url` 时的那条路：`cdp --host H --port P screenshot --out D`。

    ⚠️ **stdout 丢进 /dev/null**：内核的 `screenshot` 默认往 stdout 吐裸 base64
    （`cmd/screenshot.go`），`--out` 不是「只写文件」，是「另外还写份文件」。

    ⚠️ **让 cdp 写临时名**（`<dest>.part`），**验过再换名**（`_promote`）：这样
    「写了一半的新图把旧图顶掉」不可能发生 —— 目标名只由一次 `os.replace` 产生，
    失败时旧图**原样还在**（它只是这一趟没被更新，删掉它就成了「新的没成、旧的也没了」）。

    失败都**看得见**：认不出 ws_url（**绝不**退回本机）、起不来（二进制不在）、
    它自己说没成（退出码 + stderr），以及最容易骗人的两条 —— **退出码 0 但没写出文件**、
    **写出来的不是一张完整的图**。所以这里**信文件，不信退出码**。
    """
    dest = pathlib.Path(dest)
    trouble = _nameless(dest)
    if trouble:                                    # 落点就不对 → 一个进程都不起
        return None, trouble
    pair = host_port(ws_url)
    if pair is None:
        return None, (f"{_FAIL}认不出 ws_url 里的 host/port（{ws_url!r}）—— 这里**不会**"
                      "退回 127.0.0.1:9222：那是去拍本机的**另一个**浏览器，拍出来的图会"
                      "看着像证据。请给 `bit.sh open` 吐出来的那个 ws_url。")
    host, port = pair
    tmp = dest.with_name(dest.name + ".part")
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        _drop(tmp)                                 # 清掉上次残留的临时名；**目标名不动**
    except (OSError, ValueError) as exc:
        return None, f"{_FAIL}动不了落点 {dest}（{_why(exc)}）"

    argv = [_cdp_bin(cdp_bin), "--host", host, "--port", port, "screenshot", "--out", str(tmp)]
    try:
        done = subprocess.run(argv, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        _drop(tmp)
        return None, (f"{_FAIL}cdp screenshot 等了 {timeout} 秒还没回来（超时）—— "
                      "窗口可能卡住了")
    except (OSError, ValueError) as exc:
        _drop(tmp)
        return None, (f"{_FAIL}起不来 cdp（{_cdp_bin(cdp_bin)}）：{_why(exc)} —— "
                      "设 SITEFORGE_CDP_BIN 指到那个二进制")

    if done.returncode != 0:
        _drop(tmp)
        said = _snippet(done.stderr or "它什么都没说", 300)
        return None, f"{_FAIL}cdp screenshot 退出码 {done.returncode}，它说：{said}"
    if not tmp.is_file() or tmp.stat().st_size == 0:
        _drop(tmp)
        return None, (f"{_FAIL}cdp 说成了（退出码 0），但它没写出 {tmp}（或 0 字节）"
                      f"—— 这中间有一步在说谎，别信这张图；{dest} 没被动过")
    trouble = _promote(tmp, dest)
    return (None, trouble) if trouble else (dest.name, "")
