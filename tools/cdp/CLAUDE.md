# CLAUDE.md

## 项目约定
- 命令实现在 `cmd/` 下，核心逻辑在 `internal/`，入口在 `main.go`
- 不要使用 `defer client.Close()`，会关闭 Chrome 页面（commit 18cfd23）
- 页面导航有 30 秒超时（`internal/client.go:401`，函数 `Navigate`）
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
go run . form '#zip' --value 30301    # 填表单（--value / --check / --select 三选一）
go run . observe                      # 观察页面：结构化页面模型（agent 的主视角）
go run . observe --json=false         # 同一份模型的人话摘要（给人扫一眼，别解析）
go run . observe > before.json        # 存一份「动作前」的快照
go run . diff --before before.json    # 差分：刚才那一下有没有推进（py 的分支依据）
go build -o cdp-mcp ./cmd/mcp         # 构建 MCP 门（agent 用；同样的内核）
./cdp-mcp --ws-url "ws://10.0.0.9:4000/devtools/browser/<uuid>"  # 直接吃 bit.sh open 那串
go run . targets                      # 列出所有页面
go run . active <target-id>           # 切换活跃页面
go run . close --all                  # 关闭所有非活跃页面
go test ./...                         # 运行所有测试
go mod tidy                           # 整理依赖
```

**子命令 Flags:**
- `eval`: `--frame-id` (目标 frame)、`--file` (JS 文件路径)
- `snapshot`: 无额外 flags
- `click`: `[selector]` (位置参数) 或 `--selector`、`--frame-id`、`--track` (显示鼠标轨迹)、`--strict` (歧义选择器 / 禁用目标**当场失败**；默认 **false**，MCP 门默认走严格那一版)。每次点击在 **stderr** 上留一行「命中几个 / 点的第几个 / 禁没禁用」，stdout 仍是纯 JSON（回执多了 `match_count` / `match_index` / `target` / `target_disabled`）
- `scroll`: `[selector]` (位置参数) 或 `--selector`、`--frame-id`、`--track` (显示鼠标轨迹)
- `navi`: `[url]` (必填位置参数)、`--frame-id` (目标 frame，默认主 frame)
- `form`: `[selector]` (位置参数) 或 `--selector`、`--value` / `--check` / `--select` (三选一)、`--frame-id`、`--track`
- `observe`: `--json` (默认 true；`--json=false` 出人话摘要)、`--frame-id` (只观察指定帧 —— 传的必须是 **CDP frameID**，不是 frame_path 里给人读的 `"main"`)、`--expect-url` (预期 URL 子串；模型的 `url` 不含它就**非 0 退出**且不输出模型 —— 拿错页时的主动闸门)
- `diff`: `--before` (**必填**，动作前那份 `observe` 快照的路径)、`--json` (默认 true)、`--frame-id` (⚠️ **只影响「动作后」那一次观测**；拿整页快照配它会把其它帧的元素全报成 disappeared → 假的 `actionable=true`，非调试别传)
- `targets`: 无额外 flags
- `active`: `[target]` (位置参数) 或 `--target`
- `close`: `[target-id...]` (可多个) 或 `--all`

**前置条件:** Chrome 需要启用 remote debugging:
```bash
chrome --remote-debugging-port=9222
```

**环境变量:** `CDP_HOST`（默认 127.0.0.1）、`CDP_PORT`（默认 9222）。
**优先级：显式 flag > 环境变量 > 默认值**（2026-09-16 修正）。

> 修前是反的 —— 环境变量会盖掉显式 `--host`/`--port`。原因：`PersistentPreRun` 拿到的是**子命令**，
> 而 `--host/--port` 声明在 rootCmd 上；`cmd.PersistentFlags()` 在子命令上返回**它自己的空 flagset**，
> 于是 `Changed()` 恒为 false、守卫恒成立。改用 `cmd.Flags()`（cobra 在 `ParseFlags` 时把 root 的
> persistent flags 并了进来）。实测：`CDP_PORT=<死端口> cdp --port <活端口> targets`
> 修前打**死端口**、修后打**活端口**。回归测试在 `cmd/root_test.go`。

⚠️ **上游 `/company/cdpcli/cmd/root.go` 有同样的缺陷且未修** —— 两个工具此刻行为不一致。

**功能说明:** snapshot 命令获取当前打开页面的所有 frame 信息（frameId、title、url、readyState）；
observe 命令给出页面模型（规格 §4.3 契约：actions / fields / option_groups / obstructions /
diagnostics，跨源 iframe 自动逐帧合并、每条带 `frame_path`），是 py 与 agent 的主视角；
⚠️ 没有页面目标报 visible 时，挑「活动页」会**退回第一个**（`internal/targets.go` 的
`pickActivePage`）—— 那是个猜测，所以模型里会带一条 `target-ambiguous` 诊断（整个 tab
可能选错了，不是某一帧没取到；真站实测过：返回一份完全合法、说的是**另一个页**的模型）；
diff 命令比「动作前快照」与「现在的页面」，回答「刚才那一下有没有推进」。

## Architecture

```
cdp/
├── cmd/            # CLI 命令实现（每个子命令一个文件 + 同名 _test.go）
│   ├── root.go     # 根命令，全局 flags（--host/--port）
│   ├── eval.go     # eval 子命令，执行 JS
│   ├── click.go    # click 子命令，模拟点击
│   ├── scroll.go   # scroll 子命令，模拟滚动
│   ├── navi.go     # navi 子命令，页面导航
│   ├── form.go     # form 子命令，表单填写（--value/--check/--select）
│   ├── snapshot.go # snapshot 子命令
│   ├── targets.go / active.go / close.go  # 页面管理
│   ├── observe.go  # observe 子命令 + renderHuman（--json=false 的人话摘要）
│   ├── diff.go     # diff 子命令 + renderDiffHuman + loadPageModel
│   ├── mcp/main.go # **MCP 门**（cdp-mcp）：stdio 服务，与 CLI 同内核
│   └── mcp_e2e_test.go # MCP 门的 e2e（放这儿是为了复用私有 Chrome 夹具）
├── internal/       # 核心库（CLI 与 MCP 两个门共用同一个内核）
│   ├── mcp/             # MCP 的**工具表 + 参数校验 + 浏览器目标**（与传输解耦，可单测）
│   │   ├── registry.go  # Tool/Param/Tools()/Lookup/Prepare（schema 由参数声明生成）
│   │   ├── handlers.go  # 七个工具 → 内核调用的映射
│   │   ├── target.go    # --ws-url/--host/--port/CDP_* 的优先级与拆分（照抄 py 的 _parse_ws_url）
│   │   └── conn.go      # 每条工具调用现连现断；连不上点名 host:port
│   ├── client.go        # CDP 客户端，WebSocket 连接管理、逐帧 eval（含 OOPIF 回退）
│   ├── click.go         # click 的目标解析：命中几个 / 点的是第几个 / 禁没禁用 + 严格判据
│   ├── observe.go       # PageModel 契约 + observeJS（单帧页面模型）
│   ├── observe_frames.go# 跨帧枚举与合并（ObserveAll）、帧覆盖守卫
│   ├── diff.go          # DiffModels：前进判据（对选择器改名免疫）+ 人话渲染用的数据
│   ├── shadow.go        # 穿透内核：withPierce / __cdpQA / __cdpRoots
│   ├── form.go          # 拟人手势填表（文本 / 复选 / 下拉 / 日期选择器）
│   ├── targets.go       # 页面目标列举与激活
│   └── js.go            # JS 常量（触控检测、快照、轨迹渲染）
├── internal/testdata/   # 集成测试的 fixture 页面（先读它的 README）
├── main.go         # 程序入口
└── go.mod          # Go 模块定义
```

⚠️ observe / diff 的 JS 不在 `internal/js.go` 里：observeJS 在 `internal/observe.go`，
穿透内核在 `internal/shadow.go`，`js.go` 只放触控检测 / snapshot / 轨迹渲染那三份（历史遗留位置）。

## Dependencies

- Go 1.26.2
- `github.com/chromedp/chromedp` - Chrome DevTools Protocol 库
- `github.com/chromedp/cdproto` - CDP 协议类型定义
- `github.com/spf13/cobra` - CLI 框架（**只有 CLI 用**；MCP 那个入口用标准库 `flag`）
- `github.com/modelcontextprotocol/go-sdk` - MCP 服务端（**只有 `cmd/mcp` 用**）

## Gotchas

- **`cdp-mcp` 的 stdout 是 JSON-RPC 通道**：一个 Go 日志行就能毁掉整条流（客户端报的是
  「JSON 解析失败」，不是「你的工具打了日志」）。所以入口用标准库 `flag` 而不是 cobra
  （cobra 出错/`--help` 时把 usage 写 stdout），日志一律走 stderr。`cmd/mcp_e2e_test.go`
  逐行验这件事。
- **MCP 要先握手**：直接发 `tools/list` 得到的是
  `method "tools/list" is invalid during session initialization` —— 先 `initialize`
  再 `notifications/initialized`。另外 stdin 一次性灌完就关（`echo ... | cdp-mcp`）时，
  响应可能还没写出来进程就收工了 —— 用交互式的客户端（测试里的 `mcpDoor`）。
- `defer client.Close()` 会关闭 Chrome 页面而非 WebSocket 连接（commit 18cfd23）
- 页面导航（`Navigate`）有 30 秒超时，超时返回 error（`internal/client.go:401`）
- 单帧 `observe` 的 `--frame-id` 收的是 **CDP frameID**，不是 `frame_path` 里那个给人读的 `"main"`
- 集成测试默认打 `127.0.0.1:9222`（可用 `CDP_HOST`/`CDP_PORT` 指向别的浏览器）；
  `cmd/` 的 e2e 则自己起私有 headless Chrome —— 两个测试二进制并行时别让它们抢同一个页面
