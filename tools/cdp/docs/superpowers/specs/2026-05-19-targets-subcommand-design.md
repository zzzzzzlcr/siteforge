# targets 子命令设计

## 概述

新增 `cdp targets` 子命令，列出 Chrome 中所有打开的页面（type=page 的 target），输出 JSON 格式。

## 架构

新增两个文件，零现有代码修改：

```
cmd/targets.go        # CLI 命令注册
internal/targets.go   # 核心逻辑：连接浏览器、获取 target 列表
```

## 数据模型

```go
type PageTarget struct {
    ID    string `json:"id"`
    Title string `json:"title"`
    URL   string `json:"url"`
}
```

## 实现

### internal/targets.go

独立函数 `ListPageTargets(host, port)` 不依赖 `Client`（Client 绑定到单个页面，语义不匹配）：

1. `GET http://host:port/json/version` 获取 `webSocketDebuggerUrl`
2. `chromedp.NewRemoteAllocator` + `chromedp.NewContext` 创建临时浏览器级连接
3. `chromedp.Targets(ctx)` 获取所有 target
4. 过滤 `Type == "page"`，排除 `devtools://` 前缀
5. 返回 `[]PageTarget`

### cmd/targets.go

- `targetsCmd` cobra 命令，`Use: "targets"`, `Short: "列出所有打开的页面"`
- `RunE` 调用 `internal.ListPageTargets`，`json.NewEncoder(os.Stdout).Encode(results)`
- 复用 root.go 的 `--host` / `--port` 全局 flags

## 输出示例

```json
[
  {"id": "ABC123", "title": "GitHub", "url": "https://github.com"},
  {"id": "DEF456", "title": "Example", "url": "https://example.com"}
]
```

空列表时输出 `[]`（或 `null` — 取决于 Go JSON 编码行为，空 slice 为 `[]`）。

## 验证

```bash
go build -o cdp main.go && ./cdp targets
```
