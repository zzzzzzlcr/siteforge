# close 子命令设计

## 需求

新增 `close` 子命令，通过 target ID 关闭 Chrome 页面（tab）。

## 用法

```
cdp close [target-id...]     # 关闭指定 target
cdp close --all               # 关闭所有非活跃页面
```

## 行为

- 接受零或多个 target ID 作为位置参数
- `--all` flag：关闭所有 page targets，但跳过 active（活跃）页面
- 手动指定 active 页面 ID 时拒绝关闭，返回错误
- 位置参数与 `--all` 组合时，重复的 target ID 不重复关闭
- 必须给至少一个参数（ID 或 `--all`），否则报错
- 输出：关闭后列出剩余 page targets（JSON 格式，与 `targets` 输出一致）

## 实现

### `internal/targets.go` — 新增 `CloseTarget` 函数

```go
func CloseTarget(host string, port int, targetID string) error
```

- 通过 HTTP `/json/version` 获取浏览器级 WebSocket URL
- 创建 browser-level allocator（无 `WithTargetID`），确保 cancel 不会误关页面
- 调用 `target.CloseTarget(target.ID(targetID)).Do(ctx)` 关闭目标
- 失败返回 error

### `cmd/close.go` — 新增 `closeCmd`

- 注册到 rootCmd
- `--all` flag
- 位置参数接收 target ID 列表
- 核心逻辑：
  1. 校验至少有一个参数（ID 或 `--all`）
  2. 收集要关闭的 target ID 集合（去重）
  3. 获取 active 页面 ID，如果在集合中则拒绝
  4. 遍历集合并调用 `internal.CloseTarget`
  5. 任一关闭失败则继续处理其余，最终返回第一个错误
  6. 调用 `internal.ListPageTargets` 获取剩余列表并 JSON 输出

### Active 保护

- `--all`：获取 active ID，从关闭列表中排除
- 手动指定 ID：检测是否为 active，是则返回错误并拒绝关闭

## 测试

- 单元测试不适用（依赖外部 Chrome 实例）
- 集成测试：启动 Chrome，打开多个 tab，验证 close 行为
