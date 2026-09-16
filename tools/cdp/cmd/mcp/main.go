// Command cdp-mcp 是 MCP 那道门 —— 与 `cdp` CLI **同一个内核**的第二个出口
// （规格 §4.1、计划二 Task 2）。
//
//	┌──────────── cdp/internal（Go 内核）────────────┐
//	│  observe · diff · 拟人手势 · 表单 · 帧 · 截图    │
//	└──────┬────────────────────────────┬────────────┘
//	       │                            │
//	 cmd/cdp（CLI，文本）          cmd/mcp（MCP，结构化）
//	       │                            │
//	 生产 py 脚本                 agent / Claude Code
//
// ⚠️ **不 shell out 去调 CLI**。包一层壳 subprocess `cdp ...` 会重新引入三样东西：
// Go 的日志噪声（stdout 是 JSON-RPC 通道，混一个字符就毁掉整条流）、双重转义
// （CLI 的 JSON → 字符串 → 再套一层 JSON）、以及每条命令一次进程开销+超时。
// 所以这里的 Handler 直接拿 `*internal.Client`（见 internal/mcp 的 Browser）。
//
// ⚠️ **它连哪个浏览器**：生产里不是本机 9222，是一个带代理与指纹的 Bit 窗口。
// 两种给法都收（internal/mcp/target.go 里有完整链路）：
//
//	--ws-url "ws://<worker_ip>:<port>/devtools/browser/<uuid>"   # bit.sh open 吐出来的那串，原样给
//	--host <worker_ip> --port <port>                            # 或者自己拆好
//
// 环境变量 CDP_HOST / CDP_PORT 是**兜底**（显式 flag 永远赢 —— C81 那条）。
// 连不上会在每次工具调用里当场报错并点名 host:port：Bit 窗口只活几分钟，
// 调用方需要一句明确的错才能去重开。
//
// ⚠️ **stdout 是协议通道，一个字都不能多说**。所以：
//   - 用标准库 flag 而不是 cobra（cobra 出错/--help 时把 usage 写到 stdout）
//   - 所有报错都走 stderr；工具的失败走 JSON-RPC 的 result（IsError），不走协议错误
//     —— 后者模型看不见，而「看不见的失败」在 agent 那边就是「做不到」
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"syscall"

	"cdp/internal/mcp"

	sdkmcp "github.com/modelcontextprotocol/go-sdk/mcp"
)

// version 是报给客户端的实现版本（MCP 的 serverInfo）。
// 与 CLI 一样跟着工具层走，不单独发版。
const version = "0.1.0"

func main() {
	os.Exit(run(os.Args[1:]))
}

// run 把「解析参数 → 定目标 → 起服务」整条路走完，返回退出码。
// 单独一个函数是为了让 main 只有一行 —— 也为了让 defer 真的生效（os.Exit 不跑 defer）。
func run(args []string) int {
	// ⚠️ 用标准库 flag，不用 cobra（CLI 那边用的是 cobra）：
	// 这里 stdout 是 JSON-RPC 通道，而 cobra 在出错/--help 时会把 usage 写到 stdout。
	// SetOutput(os.Stderr) 是显式的：ExitOnError 下连 usage 也只能走 stderr。
	fs := flag.NewFlagSet("cdp-mcp", flag.ExitOnError)
	fs.SetOutput(os.Stderr)
	fs.Usage = func() {
		fmt.Fprintf(os.Stderr, "cdp-mcp —— 与 cdp CLI 同内核的 MCP 服务（stdio）\n\n")
		fmt.Fprintf(os.Stderr, "用法: cdp-mcp [--ws-url <url> | --host <h> --port <p>]\n\n")
		fs.PrintDefaults()
	}

	host := fs.String("host", "127.0.0.1", "浏览器 host（与 CLI 同一个 flag；也可用 CDP_HOST）")
	port := fs.Int("port", 9222, "浏览器调试端口（与 CLI 同一个 flag；也可用 CDP_PORT）")
	wsURL := fs.String("ws-url", "", "bit.sh open 吐出来的 WebSocket URL，原样给（含 host 与 port，二选一）")
	if err := fs.Parse(args); err != nil {
		return 2
	}
	if fs.NArg() > 0 {
		fmt.Fprintf(os.Stderr, "cdp-mcp: 不认识的位置参数 %q\n", fs.Args())
		fs.Usage()
		return 2
	}

	// 「显式给了没有」要靠 Visit 问 flag set —— 只看值分不出「--port 9222」与
	// 「默认 9222」，而这两者在优先级里不是一回事（C81：环境变量盖掉显式 flag）。
	given := map[string]bool{}
	fs.Visit(func(f *flag.Flag) { given[f.Name] = true })

	target, err := mcp.ResolveTarget(mcp.Options{
		WSURL:   *wsURL,
		Host:    *host,
		Port:    *port,
		HostSet: given["host"],
		PortSet: given["port"],
		EnvHost: os.Getenv("CDP_HOST"),
		EnvPort: os.Getenv("CDP_PORT"),
	})
	if err != nil {
		// 目标是**启动时**就得定下来的（每一次工具调用都用它），所以这里直接退 ——
		// 起一个「连哪个窗口都还没定」的服务没有意义，而它会在第一次调用时才暴露。
		fmt.Fprintf(os.Stderr, "cdp-mcp: %v\n", err)
		return 2
	}

	srv := newServer(mcp.Connector{Target: target})

	// Ctrl-C / SIGTERM 也当成「收工」：stdio 服务在容器里由 dumb-init 转信号，
	// 不接的话默认动作是直接死（这里没别的清理，但至少退出码是干净的）。
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	if err := srv.Run(ctx, &sdkmcp.StdioTransport{}); err != nil {
		fmt.Fprintf(os.Stderr, "cdp-mcp: 服务退出: %v\n", err)
		return 1
	}
	return 0
}

// newServer 把 internal/mcp 的工具表挂到 MCP 传输上。
//
// 这一层刻意做得很薄：查名字、解参数、校验、连浏览器、调用 —— 全在 internal/mcp 里
// （那一层可以不起传输、不起浏览器地单测）。这里只剩「把结果包成 MCP 的形状」。
func newServer(conn mcp.Connector) *sdkmcp.Server {
	srv := sdkmcp.NewServer(&sdkmcp.Implementation{
		Name:        "cdp",
		Title:       "cdp 工具层（同一内核的 MCP 门）",
		Description: "看着真页面做事的工具：observe（结构化页面模型）/ diff / screenshot / click / form / scroll / goto。",
		Version:     version,
	}, &sdkmcp.ServerOptions{
		Instructions: "这套工具直接操作一个**真的浏览器**（通常是 Bit 窗口，带代理与指纹）。" +
			"看页面用 observe（它只给感知：有什么、在哪、被谁挡着 —— 判断要自己做）；" +
			"做动作前后各看一眼，用 diff 判断「刚才那一下有没有推进」。" +
			"写类动作（click/form/scroll）返回 ok 只代表「命令下发了」，不代表页面动了。",
	})

	for _, tool := range mcp.Tools() {
		srv.AddTool(&sdkmcp.Tool{
			Name:        tool.Name,
			Description: tool.Description,
			InputSchema: tool.Schema,
		}, toolHandler(conn, tool.Name))
	}
	return srv
}

// toolHandler 把一次 tools/call 变成「准备 → 连浏览器 → 执行 → 包结果」。
//
// name 在闭包里就定死了（不用 req.Params.Name）：SDK 已经按名字分派过，
// 再信一次请求里的名字等于给「名字被换了」留门。
func toolHandler(conn mcp.Connector, name string) sdkmcp.ToolHandler {
	return func(ctx context.Context, req *sdkmcp.CallToolRequest) (*sdkmcp.CallToolResult, error) {
		var rawArgs any
		if req != nil && req.Params != nil {
			rawArgs = req.Params.Arguments
		}

		tool, args, err := mcp.Prepare(name, rawArgs)
		if err != nil {
			return toolError(err), nil
		}

		browser, release, err := conn.Dial()
		if err != nil {
			return toolError(err), nil
		}
		defer release()

		out, err := tool.Handler(ctx, browser, args)
		if err != nil {
			return toolError(err), nil
		}
		return toolResult(out)
	}
}

// toolError 把失败包成**工具级**的错误（IsError + 文本），而不是协议级错误。
//
// 这是 MCP 的规矩，也正是 agent 需要的：协议错误在模型那边看不见，它只会觉得
// 「这一步没反应」；而工具级错误是模型能读到的一段话 —— 于是它能自己改参数、
// 或者告诉人「窗口没了」。吞掉错误或把它升成协议错误，都会把一次可恢复的失败
// 变成一次「agent 卡住了」。
func toolError(err error) *sdkmcp.CallToolResult {
	return &sdkmcp.CallToolResult{
		IsError: true,
		Content: []sdkmcp.Content{&sdkmcp.TextContent{Text: err.Error()}},
	}
}

// toolResult 包一次成功的结果：**同一份 JSON** 同时进 Content（文本，所有客户端都认）
// 与 StructuredContent（结构化，给支持的客户端）。
//
// 两边必须同源：各自编码一次就可能不一致，而「文本说 A、结构说 B」比只有一种更坏。
func toolResult(out any) (*sdkmcp.CallToolResult, error) {
	raw, err := json.Marshal(out)
	if err != nil {
		return nil, fmt.Errorf("工具结果没法编码成 JSON: %w", err)
	}
	var structured any
	if err := json.Unmarshal(raw, &structured); err != nil {
		return nil, fmt.Errorf("工具结果没法解回结构化形态: %w", err)
	}
	return &sdkmcp.CallToolResult{
		Content:           []sdkmcp.Content{&sdkmcp.TextContent{Text: string(raw)}},
		StructuredContent: structured,
	}, nil
}
