"""工具层：**真 MCP 客户端**（stdio）+ 工具声明 + dispatch。

## 这个文件现在是干什么的

agent 做动作、看页面，**一律走 `cdp-mcp` 那道门**（计划二 Task 2）：同一个 Go 内核的
第二个出口，工具清单来自它的 `tools/list`（规格 §4.2 的七个：observe / diff /
screenshot / click / form / scroll / goto）。

⚠️ **它不打 CLI**。包一层壳 subprocess `cdp ...` 会把三样东西重新引进来：Go 的日志
噪声（stdout 是 JSON-RPC 通道）、双重转义（CLI 的 JSON → 字符串 → 再套一层 JSON）、
每条命令一次进程开销。CLI 那条路是**产出的 py 在 worker 里**用的回退接口
（规格 D2），两回事。

## 为什么自己写这几十行 JSON-RPC，而不是装 `mcp` 那个包

1. **依赖是钉死的**：运行时只装 `Dockerfile` stage-2 里那一行 pip 列出的东西，
   而它没有 `mcp`（同一个内核的 Go 侧已经有官方 SDK —— 那一侧才是能力所在的地方）；
   为一个「只用三个方法」的客户端动生产镜像，换来的是又一个会漂的版本。
2. **要用到的那片协议很小、很稳**：stdio 上**按行分隔**的 JSON-RPC 2.0
   （go-sdk v1.8.0 的 StdioTransport 就是 `newline-delimited JSON`，见
   `mcp/transport.go` 的 IOTransport 注释），我们只需要
   `initialize` / `notifications/initialized` / `tools/list` / `tools/call` 四个往返。
3. 它是**真协议**，不是桩：握手、id 配对、isError、服务退出/超时都有明确行为，
   `tests/stub_mcp_server.py` 那头是个真 MCP 服务，两边一起把这段钉住。

## spike 的脚手架还在这里（Task 1）

`spike_observe` / `spike_specs` / `make_spike_dispatch` 是 Task 1 量「LLM 肯不肯调工具」
用的**桩**（两个工具、直接 subprocess 打 `cdp observe`）。它们**不再进生产路径**，
但留着 —— `tests/test_tool_loop.py` 靠它们可复现当时那次测量，而「当时的条件是
什么」本身就是证据。生产路径从下面 `McpSession` 起。
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from typing import Any, Callable, Protocol

# ─────────────────────────── 真 MCP 客户端（stdio）───────────────────────────

#: 报给服务端的协议版本。go-sdk v1.8.0 支持 2025-11-25 / 2025-06-18 / 2025-03-26 /
#: 2024-11-05；取中间那个稳的。服务端回什么版本以它回的为准（我们不挑食）。
MCP_PROTOCOL_VERSION = "2025-06-18"

#: 单次请求的等待上限。真站上 observe 可能几秒，工具动作偶尔十几秒；
#: 给 120 秒足够，而**绝不能没有上限** —— Bit 窗口死掉时最坏的形态是「一直挂着」，
#: 那比报错贵得多（调用方拿不到明确的错就没法去重开窗口）。
MCP_TIMEOUT_S = float(os.environ.get("SITEFORGE_MCP_TIMEOUT", "120"))

#: MCP 服务可执行文件。生产容器里是 stage-1 构建出来的 `/out/cdp-mcp`（在 PATH 上）。
MCP_BIN = os.environ.get("CDP_MCP_BIN", "cdp-mcp")


class McpError(RuntimeError):
    """传输/协议层的错：起不来、EOF、超时、JSON-RPC error。"""


class McpToolError(RuntimeError):
    """**工具自己**报的错（`isError: true`）。

    这句话是给模型看的（「连不上 127.0.0.1:9222」「没有找到选择器 #x」）——
    原样带出去，不加工、不套壳：它是模型唯一能据以改主意的信息。
    """


class McpSession:
    """一个 MCP 会话（stdio，按行分隔的 JSON-RPC 2.0）。

    ⚠️ **单线程**用的：一次只发一个请求。工具循环本来就是一步步走的，
    并发只会让「第几步出的错」说不清。
    """

    def __init__(self, argv, *, timeout: float = MCP_TIMEOUT_S, name: str = "cdp-mcp",
                 env: dict | None = None, cwd: str | None = None):
        self.argv = [str(a) for a in argv]
        self.name = name
        self.timeout = timeout
        self._id = 0
        self._lines: queue.Queue = queue.Queue()
        self._stderr: list[str] = []
        try:
            self._proc = subprocess.Popen(
                self.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1,
                env=env if env is not None else os.environ.copy(), cwd=cwd,
            )
        except OSError as exc:
            raise McpError(f"起不了 {name}（{self.argv[0]}）: {exc}") from exc
        threading.Thread(target=self._pump_stdout, daemon=True).start()
        threading.Thread(target=self._pump_stderr, daemon=True).start()
        self.server_info: dict = {}
        self._handshake()

    # ── 命令行 ──────────────────────────────────────────────

    @classmethod
    def open(cls, *, ws_url: str | None = None, host: str | None = None, port: int | None = None,
             binary: str | None = None, timeout: float = MCP_TIMEOUT_S) -> "McpSession":
        """按浏览器目标起一个会话（与 `cdp-mcp` 自己的 flag 同一套）。

        - `ws_url` —— `bit.sh open` 吐出来的那串，**原样**给（省掉调用方自己拆 host/port：
          少一步转换 = 少一个搞错的机会）
        - `host`/`port` —— 自己拆好的情况
        - 两个都不给 → 交给 `cdp-mcp` 自己按 `CDP_HOST`/`CDP_PORT` 与默认值定
        """
        ws_url = ws_url or os.environ.get("CDP_WS_URL") or ""
        argv = [binary or MCP_BIN]
        if ws_url:
            argv += ["--ws-url", ws_url]
        else:
            if host:
                argv += ["--host", str(host)]
            if port:
                argv += ["--port", str(port)]
        return cls(argv, timeout=timeout)

    # ── 传输 ────────────────────────────────────────────────

    def _pump_stdout(self) -> None:
        try:
            for line in self._proc.stdout:
                self._lines.put(line)
        finally:
            self._lines.put(None)      # EOF 的哨兵：读的人不能在这儿永远等下去

    def _pump_stderr(self) -> None:
        """stderr 必须有人读：管道写满时子进程会**卡死**，而卡死没有诊断信息。"""
        for line in self._proc.stderr:
            self._stderr.append(line.rstrip("\n"))
            del self._stderr[:-50]

    def _tail(self, n: int = 5) -> str:
        return " | ".join(self._stderr[-n:])

    def _write(self, msg: dict) -> None:
        try:
            self._proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
            self._proc.stdin.flush()
        except (BrokenPipeError, ValueError) as exc:
            raise McpError(f"{self.name} 已经不在了（写不进去）: {exc}. stderr: {self._tail()}") from exc

    def _read(self, want_id: int) -> dict:
        deadline = time.time() + self.timeout
        while True:
            left = deadline - time.time()
            if left <= 0:
                raise McpError(
                    f"{self.name} 超过 {self.timeout:g} 秒没回 id={want_id}"
                    f"（最可能是浏览器窗口没了 / 连不上）。stderr: {self._tail()}"
                )
            try:
                line = self._lines.get(timeout=left)
            except queue.Empty:
                continue
            if line is None:
                raise McpError(f"{self.name} 退出了（stdout 关了）。stderr: {self._tail()}")
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                # stdout 上出现非 JSON = 那个进程把日志混进了协议通道（stdio 传输里是致命的）
                raise McpError(f"{self.name} 的 stdout 上不是 JSON: {line[:200]!r}") from None
            # 服务端主动发的请求/通知（我们不用它们）：跳过，别让它把 id 配对搞乱
            if msg.get("id") != want_id:
                continue
            if "error" in msg:
                err = msg["error"] or {}
                raise McpError(f"{self.name} 报了协议错 {err.get('code')}: {err.get('message')}")
            return msg.get("result") or {}

    def request(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        mid = self._id
        self._write({"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}})
        return self._read(mid)

    def notify(self, method: str, params: dict | None = None) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def _handshake(self) -> None:
        result = self.request("initialize", {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "siteforge-agent", "version": "0.1.0"},
        })
        self.server_info = result.get("serverInfo") or {}
        self.notify("notifications/initialized")

    # ── 工具 ────────────────────────────────────────────────

    def list_tools(self) -> list[dict]:
        """那道门上的工具（**唯一**一份清单，不在 agent 侧另抄一遍）。"""
        return self.request("tools/list").get("tools") or []

    def call_tool(self, name: str, arguments: dict | None = None) -> Any:
        """调一个工具。

        失败一律**抛**（`McpToolError` 带工具自己那句话 / `McpError` 带传输诊断）——
        由 `llm.run_tool_loop` 记成那条 tool 消息的结果回给模型。
        在这里吞掉错误，模型就会以为「这一步没反应」，而人只看到它开始瞎猜。
        """
        result = self.request("tools/call", {"name": name, "arguments": arguments or {}})
        if result.get("isError"):
            raise McpToolError(_text_of(result) or f"{name} 失败了（服务端没给原因）")
        value = _value_of(result)
        return value if value is not None else _text_of(result)

    # ── 收摊 ────────────────────────────────────────────────

    def close(self) -> None:
        proc = getattr(self, "_proc", None)
        if proc is None or proc.poll() is not None:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001 —— 收摊失败不该把调用方的事盖掉
            try:
                proc.kill()
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                pass

    def __enter__(self) -> "McpSession":
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def _text_of(result: dict) -> str:
    parts = []
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text") or ""))
    return "\n".join(p for p in parts if p)


def _value_of(result: dict) -> Any:
    """结果的值：优先结构化那份，退回文本那份（文本能解成 JSON 就解）。

    两边同源（服务端 `toolResult` 就是同一份 JSON 编两遍），所以这里挑哪个都一样 ——
    挑结构化只是省一次解析。
    """
    if "structuredContent" in result and result["structuredContent"] is not None:
        return result["structuredContent"]
    text = _text_of(result)
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return text


def tool_specs(session: McpSession) -> list[dict]:
    """把那道门的工具表翻成 OpenAI function-calling 形态（**不手写第二份**）。"""
    specs = []
    for tool in session.list_tools():
        specs.append({
            "type": "function",
            "function": {
                "name": tool.get("name") or "",
                "description": tool.get("description") or "",
                "parameters": tool.get("inputSchema") or {"type": "object", "properties": {}},
            },
        })
    return specs


def make_dispatch(session: McpSession, *, on_call: Callable[[str, dict, Any], None] | None = None):
    """建一个 dispatch（`llm.run_tool_loop` 要的那个形状）。

    未知工具名由服务端报错（它的报错里会列出全部合法名字），我们原样抛出去 ——
    静默兜底会让「从没发生过的动作」看起来像做完了。
    """
    def dispatch(name: str, args: dict):
        result = session.call_tool(name, args)
        if on_call is not None:
            on_call(name, args, result)
        return result

    return dispatch


# ─────────────────────── Task 1 spike 的桩（不再进生产路径）───────────────────────

# 真站（2026-09-16 验过国内 IP 打得开）。发现页 → 点 `a.cb` → 漏斗页。
FUNNEL_ENTRY = "https://compareinsulation.io/article-1-c"
FUNNEL_LINK = "a.cb"
FUNNEL_HOST = "check.compareinsulation.io"

DEFAULT_CDP_BIN = os.environ.get("SPIKE_CDP_BIN", "/tmp/cdp-spike")
DEFAULT_CDP_PORT = int(os.environ.get("SPIKE_CDP_PORT", "9333"))
OBSERVE_TIMEOUT_S = 60


class Dispatch(Protocol):
    """工具名 → 可调用对象（`llm.run_tool_loop` 收的就是这个形状）。"""

    def __call__(self, name: str, arguments: dict) -> Any: ...


def spike_observe(port: int = DEFAULT_CDP_PORT, cdp_bin: str = DEFAULT_CDP_BIN) -> dict:
    """调 `cdp observe --json`，返回解析好的 PageModel（spike 路径）。"""
    p = subprocess.run(
        [cdp_bin, "--port", str(port), "observe", "--json"],
        capture_output=True, text=True, timeout=OBSERVE_TIMEOUT_S,
    )
    if p.returncode != 0:
        raise RuntimeError(f"cdp observe 非 0 退出 ({p.returncode}): {p.stderr.strip()[:300]}")
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"cdp observe 的输出不是 JSON: {e}; 开头={p.stdout[:200]!r}") from e


def spike_specs() -> list[dict]:
    """spike 的两个工具（`observe` + `done`）。

    ⚠️ 描述文字**刻意只讲工具本身**（怎么调、返回什么），不讲「你该怎么用」——
    一旦写进「请务必先 observe 再回答」，量的就不再是默认状态下的能力。
    """
    return [
        {
            "type": "function",
            "function": {
                "name": "observe",
                "description": (
                    "观察当前浏览器标签页，返回这一页的结构化页面模型（JSON）。"
                    "包含：url / title / page_text / 可动作元素 actions（带文本、选择器、坐标 bbox、"
                    "是否在首屏 above_fold、遮挡情况 occluded_by）/ 表单字段 fields / "
                    "选项组 option_groups / 遮挡物 obstructions / 被排除的陷阱元素 honeypots / "
                    "观测自身的诊断 diagnostics。无参数。"
                ),
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "done",
                "description": "声明你已经有答案了，结束本次任务。",
                "parameters": {
                    "type": "object",
                    "properties": {"answer": {"type": "string", "description": "你的最终答案"}},
                    "required": ["answer"],
                },
            },
        },
    ]


def make_spike_dispatch(*, port: int = DEFAULT_CDP_PORT, cdp_bin: str = DEFAULT_CDP_BIN):
    """spike 的 dispatch。返回 `(dispatch, log)`：`log` 是每次 observe 的原始 PageModel。"""
    log: list[dict] = []

    def dispatch(name: str, args: dict):
        if name == "observe":
            model = spike_observe(port=port, cdp_bin=cdp_bin)
            log.append(model)
            return model
        if name == "done":
            return {"acknowledged": True, "answer": args.get("answer", "")}
        raise KeyError(f"没有这个工具: {name}")

    return dispatch, log
