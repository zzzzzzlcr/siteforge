# CLAUDE.md

## 项目约定
- 命令实现在 `cmd/` 下，核心逻辑在 `internal/`，入口在 `main.go`
- 不要使用 `defer client.Close()`，会关闭 Chrome 页面（commit 18cfd23）
- 页面导航有 30 秒超时（`internal/client.go:141`）
- `go build -o cdp main.go` 而非 `go build .`（会包含非 Go 文件）

## Commands

```bash
go build -o cdp main.go  # 构建 CLI（不要用 go build . 会包含非 Go 文件）
go run . snapshot                     # 获取所有 frame 信息
go run . eval '"hello"'               # 执行 JS（用 snapshot 获取 frame-id）
go run . eval --file script.js        # 从文件执行 JS
go run . click '#btn'                 # 点击元素（selector 可作为位置参数）
go run . scroll '#content'            # 滚动元素（selector 可作为位置参数）
go run . navi https://example.com       # 导航到指定 URL
go test ./...                         # 运行所有测试
go mod tidy                           # 整理依赖
```

**子命令 Flags:**
- `eval`: `--frame-id` (目标 frame)、`--file` (JS 文件路径)
- `snapshot`: 无额外 flags
- `click`: `[selector]` (位置参数) 或 `--selector`、`--frame-id`、`--track` (显示鼠标轨迹)
- `scroll`: `[selector]` (位置参数) 或 `--selector`、`--frame-id`、`--track` (显示鼠标轨迹)
- `navi`: `[url]` (必填位置参数)、`--frame-id` (目标 frame，默认主 frame)

**前置条件:** Chrome 需要启用 remote debugging:
```bash
chrome --remote-debugging-port=9222
```

**环境变量:** `CDP_HOST`（默认 127.0.0.1）、`CDP_PORT`（默认 9222），命令行 `--host`/`--port` 优先

**功能说明:** snapshot 命令获取当前打开页面的所有 frame 信息（frameId、title、url、readyState）

## Architecture

```
cdp/
├── cmd/           # CLI 命令实现
│   ├── root.go   # 根命令，全局 flags
│   ├── eval.go      # eval 子命令，执行 JS
│   ├── click.go     # click 子命令，模拟点击
│   ├── scroll.go    # scroll 子命令，模拟滚动
│   ├── navi.go      # navi 子命令，页面导航
│   └── snapshot.go # snapshot 子命令
├── internal/      # 核心库
│   ├── client.go # CDP 客户端，WebSocket 连接管理
│   └── js.go     # JS 常量（触控检测、快照、轨迹渲染）
├── main.go        # 程序入口
└── go.mod         # Go 模块定义
```

## Dependencies

- Go 1.26.2
- `github.com/chromedp/chromedp` - Chrome DevTools Protocol 库
- `github.com/chromedp/cdproto` - CDP 协议类型定义
- `github.com/spf13/cobra` - CLI 框架

## Gotchas

- `defer client.Close()` 会关闭 Chrome 页面而非 WebSocket 连接（commit 18cfd23）
- 页面导航（`Navigate`）有 30 秒超时，超时返回 error（`internal/client.go:141`）
