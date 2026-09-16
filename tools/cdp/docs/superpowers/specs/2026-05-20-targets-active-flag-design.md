# targets --active 标记活跃页面

## 概述

为 `cdp targets` 新增 `--active` flag，标记用户当前正在查看的页面。

## 行为

- `cdp targets` — 现有行为不变，输出不含 `active` 字段
- `cdp targets --active` — 每个页面输出含 `"active": true` / `"active": false`

## 数据模型

```go
type PageTarget struct {
    ID     string `json:"id"`
    Title  string `json:"title"`
    URL    string `json:"url"`
    Active *bool  `json:"active,omitempty"`
}
```

`*bool` + `omitempty` 保证：不传 `--active` 时字段完全不出现，与现有输出一致。

## 实现

### internal/targets.go

- `ListPageTargets(host, port int, detectActive bool)` — 新增 `detectActive` 参数
- 当 `detectActive=true` 时，对每个页面创建 CDP 连接，执行 `document.visibilityState`，返回 `"visible"` 的标记为活跃
- 新增 `isPageActive(wsURL, targetID string) (bool, error)` — 连接指定页面并检测 visibility
- 检测失败不阻断流程，`Active` 保持 `nil`

### cmd/targets.go

- `targetsCmd` 新增 `--active` bool flag（默认 `false`）
- `runTargets` 将 flag 值传入 `ListPageTargets`

## 输出示例

`cdp targets --active`：

```json
[
  {"id": "ABC", "title": "GitHub", "url": "https://github.com", "active": true},
  {"id": "DEF", "title": "Example", "url": "https://example.com", "active": false}
]
```

空列表时输出 `[]`。

## 验证

```bash
go build -o cdp main.go && ./cdp targets --active
```
