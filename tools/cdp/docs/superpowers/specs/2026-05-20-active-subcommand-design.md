# active 子命令 — 激活指定页面

## 概述

新增 `cdp active` 子命令，使用 CDP `Target.activateTarget` 将指定页面标签页切换到前台（激活）。

## 用法

```bash
cdp active <target-id>     # 位置参数
cdp active --target <id>   # flag 形式
```

## 行为

1. 解析 `[target]` 位置参数或 `--target` flag（至少提供一个，与 click/scroll 的 selector 模式一致）
2. 调用 `internal.ActivateTarget(host, port, targetID)` 激活页面
3. 激活成功后调用 `internal.ListPageTargets(host, port, true)`，JSON 输出所有页面及其活跃状态
4. 激活失败返回 error

## 数据模型

无新增类型。复用 `PageTarget`。

## 实现

### internal/targets.go — 新增 ActivateTarget

```go
func ActivateTarget(host string, port int, targetID string) error
```

- HTTP GET `/json/version` 获取 `webSocketDebuggerUrl`
- 创建 browser 级别的 chromedp context（**不加 `WithTargetID`**，cancel 时不会关闭任何页面）
- 调用 `target.ActivateTarget(target.ID(targetID)).Do(ctx)`
- 错误透传

### cmd/active.go — 新增命令

- `Use: "active [target]"`，`Args: cobra.MaximumNArgs(1)`
- `--target` string flag
- 至少提供一个 target ID，否则报错
- 遵循 click/scroll 的参数解析模式

## 输出示例

```json
[
  {"id": "ABC", "title": "GitHub", "url": "https://github.com", "active": false},
  {"id": "DEF", "title": "Example", "url": "https://example.com", "active": true}
]
```

## 验证

```bash
go build -o cdp main.go && ./cdp active --target <从 targets 获取的 id>
```
