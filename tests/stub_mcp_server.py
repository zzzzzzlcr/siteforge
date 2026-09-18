#!/usr/bin/env python3
"""桩 MCP 服务 —— 测试用的最小 `cdp-mcp`（**不打真浏览器**）。

它为什么存在（计划二 Task 5 Step 1「用桩 MCP，不打真浏览器」）：
agent 的工具循环要测的东西大多与浏览器无关 —— 预算到顶会不会停、人喊停会不会
在下一步之前退出、模型一轮里丢过来三个 `tool_calls` 会不会都执行、工具报错记在哪。
拿真浏览器测这些，测出来的红绿都要先排除「窗口挂了 / 页面慢 / 选择器改了」，
而那些**恰恰不是这条循环的判据**。

⚠️ 它**不是**「假客户端」：它是**真的 MCP 服务**（stdio 上按行分隔的 JSON-RPC 2.0，
真的 `initialize` 握手、真的 `tools/list` / `tools/call`）。所以它同时钉住了
`agent/tools.py` 里那个客户端 —— 握手、按行分帧、id 配对、isError 的转译，
全都真的走一遍。一个假客户端（直接给几个 python 对象）这些**一条都测不到**。

用法（测试里由 `tools.McpSession` 起）：

    python3 tests/stub_mcp_server.py <program.json>

program.json 的形状：

    {
      "log": "/tmp/xxx.calls.jsonl",      # 可选：每次 tools/call 追一行 {name, args}
      "tools": [...],                     # 可选：这道门 `tools/list` 回什么（不给用下面的 TOOLS）
      "responses": {                      # 按工具名给一串回答，**顺序消费，最后一个重复**
        "observe": [{"structured": {...页面模型...}}, ...],
        "click":   [{"error": "没有找到选择器 #ghost"}]
      }
    }

`tools` 那一项是给「工具声明来自门」那道钉子用的：只有在**测试自己决定门口有什么**
的时候，「本地另抄一份表」才会露馅（照抄默认表那种写法两下都是绿的）。
"""

from __future__ import annotations

import json
import sys

# 与 tools/cdp/internal/mcp/registry.go 的工具表同名同参（**只有这七个**）。
# 名字写错的话，`agent/tools.py` 的工具声明就会与生产那张表分家 —— 那正是这个桩要防的。
TOOLS = [
    {
        "name": "observe",
        "description": "观察页面，返回结构化页面模型。",
        "inputSchema": {
            "type": "object",
            "properties": {"frame_id": {"type": "string"}, "expect_url": {"type": "string"}},
        },
    },
    {
        "name": "diff",
        "description": "把上一次 observe 的结果与现在的页面比一遍。",
        "inputSchema": {
            "type": "object",
            "properties": {"before": {"type": "object"}, "frame_id": {"type": "string"}},
            "required": ["before"],
        },
    },
    {
        "name": "screenshot",
        "description": "截当前视口。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "click",
        "description": "拟人点击一个元素。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string"},
                "frame_id": {"type": "string"},
                "track": {"type": "boolean"},
            },
            "required": ["selector"],
        },
    },
    {
        "name": "form",
        "description": "填值 / 勾选 / 选下拉。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string"},
                "value": {"type": "string"},
                "check": {"type": "boolean"},
                "select": {"type": "string"},
                "frame_id": {"type": "string"},
                "track": {"type": "boolean"},
            },
            "required": ["selector"],
        },
    },
    {
        "name": "scroll",
        "description": "把元素滚进视口。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string"},
                "frame_id": {"type": "string"},
                "track": {"type": "boolean"},
            },
            "required": ["selector"],
        },
    },
    {
        "name": "goto",
        "description": "导航到指定 URL。",
        "inputSchema": {
            "type": "object",
            "properties": {"url": {"type": "string"}, "frame_id": {"type": "string"}},
            "required": ["url"],
        },
    },
]

# 客户端报的协议版本。写死一个 SDK 支持的版本（server 会照自己的支持列表回一个）。
PROTOCOL_VERSION = "2025-06-18"


def _send(obj) -> None:
    # ⚠️ `ensure_ascii=True`：JSON 的转义序列（`"\ud800"`）是**纯 ASCII**，
    # 所以「线上写不出去的码位」在这个管道上**过得去**（不转义的话这一句自己就会
    # `UnicodeEncodeError`，桩当场死掉 —— 那测到的就不是被测物了）。见 `_to_result` 的注释。
    sys.stdout.write(json.dumps(obj, ensure_ascii=True) + "\n")
    sys.stdout.flush()


def _to_result(item: dict) -> dict:
    """program 里一条回答 → MCP 的 CallToolResult。

    ⚠️ `ensure_ascii=True`（2026-09-18）：**故意**的，而且只有这一处与真 server 不同。
    JSON 的转义序列（`"\\ud800"`）是一串**纯 ASCII** —— 于是「孤立代理对」这种
    线上写不出去的形状**能过这条管道**，Python 侧 `json.loads` 解出来就是那个代理对。
    真 cdp-mcp（Go）吐不出这个形状（`encoding/json` 会把非 UTF-8 写成 `�`），
    所以它是**桩才有**的一条路 —— 而缺了它，「回执是任意字节」那条判据就永远测不到
    （线上一撞就是 `/live` 500、整条时间线一条都读不出来）。
    """
    if item.get("error"):
        # isError 那一路：与真 server 的 toolError 同形（文本 + IsError）。
        return {"isError": True, "content": [{"type": "text", "text": str(item["error"])}]}
    payload = item.get("structured")
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=True)}],
        "structuredContent": payload,
    }


def main() -> int:
    program = {}
    if len(sys.argv) > 1:
        with open(sys.argv[1], encoding="utf-8") as fp:
            program = json.load(fp)
    responses = program.get("responses") or {}
    tools = program.get("tools") or TOOLS
    log_path = program.get("log")
    used: dict[str, int] = {}

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        method, mid = msg.get("method"), msg.get("id")
        if mid is None:
            continue  # 通知（notifications/initialized）—— 没有回话
        if method == "initialize":
            _send({
                "jsonrpc": "2.0", "id": mid,
                "result": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "stub-cdp", "version": "0.0.1"},
                },
            })
        elif method == "tools/list":
            _send({"jsonrpc": "2.0", "id": mid, "result": {"tools": tools}})
        elif method == "tools/call":
            params = msg.get("params") or {}
            name = params.get("name") or ""
            args = params.get("arguments") or {}
            if log_path:
                with open(log_path, "a", encoding="utf-8") as fp:
                    fp.write(json.dumps({"name": name, "args": args}, ensure_ascii=False) + "\n")
            seq = responses.get(name) or [{"structured": {"ok": True}}]
            index = used.get(name, 0)
            used[name] = index + 1
            _send({"jsonrpc": "2.0", "id": mid, "result": _to_result(seq[min(index, len(seq) - 1)])})
        else:
            _send({
                "jsonrpc": "2.0", "id": mid,
                "error": {"code": -32601, "message": f"桩不认识这个方法: {method}"},
            })
    return 0


if __name__ == "__main__":
    sys.exit(main())
