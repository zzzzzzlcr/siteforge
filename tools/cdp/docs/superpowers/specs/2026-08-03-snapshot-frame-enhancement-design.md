# Spec: snapshot frame 能力增强

## 背景

`cdp snapshot` 目前只能 dump 所有 frame，缺乏三个关键能力：
1. 无法指定单个 frame（eval/click 都有 `--frame-id`，snapshot 没有）
2. 动态加载的 iframe（如 Stripe）不出现在 `GetFrameTree()` 中
3. 跨域 frame 因 CSP 限制导致 `createIsolatedWorld` 失败，body 为空

## 目标

`cdp snapshot` 支持 `--frame-id` 精准抓单个 frame，自动监听动态 iframe，
跨域 frame 优雅降级读取内容。

## 设计

### 1. `--frame-id` flag

在 `cmd/snapshot.go` 的 `init()` 中注册 `--frame-id` flag。

行为：
- 不传 `--frame-id`：现有行为不变，输出完整 FrameTree
- 传了 `--frame-id`：只输出目标 frame 的单个 `FrameSnapshot` JSON（无 tree wrapper，无根节点）

实现：
- `runSnapshot` 检查 `--frame-id` → 有值时跳过 `GetFrameTree`，直接调 `captureFrame(client, frameID, ...)`
- `captureFrame` 是现有的单 frame 抓取逻辑（从 `buildFrameTreeOutput` 抽出）

### 2. 动态 iframe 监听：`GetFrameTreeWithEvents`

在 `internal/client.go` 新增方法：

```go
func (c *Client) GetFrameTreeWithEvents(wait time.Duration) (*page.FrameTree, error)
```

流程：
1. 调用 `page.GetFrameTree()` 获取当前 frame 树（基线）
2. 启动 `chromedp.ListenTarget` 监听 `page.EventFrameAttached`，收集新增 frame
3. 等待 `wait` 时长（由调用方传入，snapshot 默认 3s）
4. 将监听到的新 frame 合并到基线 frame 树的 `ChildFrames` 中
5. 返回合并后的完整 frame 树

`snapshot` 调用时使用 `GetFrameTreeWithEvents(3 * time.Second)` 替代原有的 `GetFrameTree()`。

### 3. 跨域 frame 降级读取

修改 `buildFrameTreeOutput` / `captureFrame` 中的内容读取逻辑：

```
try: RunIsolatedWorld(frameID, SnapshotJS)  ← 现有方式
  ↓ 失败 (CSP / isolated world blocked)
try: Accessibility.GetFullAXTree(frameID)   ← 无障碍树，有文本
  ↓ 失败
try: DOM.getDocument(frameID)               ← DOM 结构，无交互性
  ↓ 失败
result: error "all content methods failed: ..."
```

新增辅助方法：

```go
func (c *Client) GetFrameContent(frameID cdp.FrameID) (body interface{}, method string, err error)
```

- 返回 `body`（内容）、`method`（用了哪种方式）、`err`（全部失败时的错误）
- `FrameSnapshot` 新增 `Method` 字段记录内容来源

### FrameSnapshot 结构变更

```go
type FrameSnapshot struct {
    FrameID  string          `json:"frameId"`
    ParentID string          `json:"parentId,omitempty"`
    Name     string          `json:"name,omitempty"`
    URL      string          `json:"url"`
    Title    string          `json:"title,omitempty"`
    Body     json.RawMessage `json:"body,omitempty"`
    Method   string          `json:"method,omitempty"`   // 新增："isolatedWorld" | "axTree" | "domDocument"
    Error    string          `json:"error,omitempty"`
}
```

### 涉及文件

| 文件 | 改动 |
|------|------|
| `cmd/snapshot.go` | 加 `--frame-id` flag；`runSnapshot` 分支逻辑；抽出 `captureFrame` |
| `internal/client.go` | 新增 `GetFrameTreeWithEvents`、`GetFrameContent` |
| `internal/client_test.go` | 新增单元测试（`GetFrameTreeWithEvents` 可用 mock event） |
| `internal/snapshot_integration_test.go` | 新建：动态 iframe + 跨域测试页面 |

### 测试策略

**单元测试**：
- `TestBuildFrameTreeOutput_WithMethod` — 验证 Method 字段正确输出

**集成测试**（需要 Chrome + 测试页面）：
- `TestSnapshot_WithFrameID`：带 `--frame-id` 只返回单个 frame
- `TestSnapshot_DynamicFrames`：页面延迟插入 iframe，3s 内被捕获
- `TestSnapshot_CrossOriginFallback`：跨域 iframe 降级到 axTree/domDocument

### 不涉及

- 不修改 eval/click/form 的 frame 处理逻辑
- 不支持 Web Worker 或 Service Worker 的 snapshot
- 不引入新的 CLI 子命令
