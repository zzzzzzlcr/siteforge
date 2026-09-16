# CDP CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建一个名为 `cdp` 的 CLI 工具，通过 Chrome DevTools Protocol 连接 Chrome，获取当前活动 target 的所有 frame 信息，并在每个 frame 的隔离世界中执行 snapshot.js，最终收集所有结果以 JSON 数组输出。

**Architecture:** 使用 cobra 构建 CLI 结构，chromedp 库处理 CDP 通信。根命令定义全局 --host/--port 参数，snapshot 子命令实现核心逻辑：连接 Chrome → 获取 FrameTree → 遍历创建隔离世界 → 执行 JS → 收集结果。

**Tech Stack:** Go 1.21+, github.com/chromedp/chromedp, github.com/spf13/cobra

---

### Task 1: 初始化 Go 项目

**Files:**
- Create: `go.mod`
- Create: `main.go`

- [ ] **Step 1: 创建 go.mod**

```bash
cd /home/ansible/project/.claude/worktrees/chromedp
go mod init cdp
```

- [ ] **Step 2: 创建 main.go**

```go
package main

import (
	"cdp/cmd"
	"os"
)

func main() {
	if err := cmd.Execute(); err != nil {
		os.Exit(1)
	}
}
```

- [ ] **Step 3: 提交**

```bash
git add go.mod main.go
git commit -m "feat: initialize cdp CLI project"
```

---

### Task 2: 实现根命令 (cmd/root.go)

**Files:**
- Create: `cmd/root.go`

- [ ] **Step 1: 创建 cmd/root.go**

```go
package cmd

import (
	"fmt"
	"github.com/spf13/cobra"
)

var (
	host string
	port int
)

var rootCmd = &cobra.Command{
	Use:   "cdp",
	Short: "CDP CLI tool for Chrome DevTools Protocol",
	Long:  `A CLI tool to interact with Chrome DevTools Protocol`,
}

func Execute() error {
	return rootCmd.Execute()
}

func init() {
	rootCmd.PersistentFlags().StringVar(&host, "host", "127.0.0.1", "Chrome host address")
	rootCmd.PersistentFlags().IntVar(&port, "port", 9222, "Chrome port")
}

func GetHost() string {
	return host
}

func GetPort() int {
	return port
}
```

- [ ] **Step 2: 提交**

```bash
git add cmd/root.go
git commit -m "feat: add root command with host/port flags"
```

---

### Task 3: 实现内嵌 snapshot.js (internal/snapshot.go)

**Files:**
- Create: `internal/snapshot.go`

- [ ] **Step 1: 创建 internal/snapshot.go**

```go
package internal

// SnapshotJS is the JavaScript code to be executed in each frame's isolated world.
const SnapshotJS = `() => {
	return JSON.stringify({
		title: document.title,
		url: document.URL,
		readyState: document.readyState
	});
}`
```

- [ ] **Step 2: 提交**

```bash
git add internal/snapshot.go
git commit -m "feat: embed snapshot.js"
```

---

### Task 4: 实现 CDP Client 封装 (internal/client.go)

**Files:**
- Create: `internal/client.go`

- [ ] **Step 1: 创建 internal/client.go**

```go
package internal

import (
	"context"
	"fmt"
	"net/url"

	"github.com/chromedp/chromedp"
	"github.com/chromedp/cdproto/page"
)

type Client struct {
	ctx context.Context
}

func NewClient(host string, port int) (*Client, error) {
	u := url.URL{
		Scheme: "http",
		Host:   fmt.Sprintf("%s:%d", host, port),
		Path:   "",
	}

	ctx, cancel := chromedp.NewContext(context.Background())
	if err := chromedp.Run(ctx); err != nil {
		cancel()
		return nil, fmt.Errorf("failed to connect to Chrome: %w", err)
	}

	return &Client{ctx: ctx}, nil
}

func (c *Client) Close() {
	if c.ctx != nil {
		chromedp.Cancel(c.ctx)
	}
}

// GetFrameTree returns all frame IDs from the current page's frame tree.
func (c *Client) GetFrameTree() ([]page.FrameID, error) {
	var tree page.GetFrameTreeResult
	err := chromedp.Run(c.ctx, page.GetFrameTree().Assign(&tree))
	if err != nil {
		return nil, fmt.Errorf("failed to get frame tree: %w", err)
	}

	frameIDs := extractFrameIDs(tree.FrameTree)
	return frameIDs, nil
}

// extractFrameIDs recursively collects all frame IDs from the frame tree.
func extractFrameIDs(ft page.FrameTree) []page.FrameID {
	ids := []page.FrameID{ft.Frame.ID}
	for _, child := range ft.ChildFrames {
		ids = append(ids, extractFrameIDs(child)...)
	}
	return ids
}

// RunIsolatedWorld executes JavaScript in an isolated world for the given frame.
func (c *Client) RunIsolatedWorld(frameID page.FrameID, js string) (string, error) {
	worldName := fmt.Sprintf("snapshot-world-%s", frameID)

	_, err := page.CreateIsolatedWorld(frameID, page.CreateIsolatedWorldWorldName(worldName)).Do(c.ctx)
	if err != nil {
		return "", fmt.Errorf("failed to create isolated world for frame %s: %w", frameID, err)
	}

	var result page.JSEvaluationResult
	err = chromedp.Run(c.ctx, page.RunStringWithWorld(frameID, worldName, js).Assign(&result))
	if err != nil {
		return "", fmt.Errorf("failed to run JS in frame %s: %w", frameID, err)
	}

	return result.Value, nil
}
```

- [ ] **Step 2: 提交**

```bash
git add internal/client.go
git commit -m "feat: add CDP client wrapper"
```

---

### Task 5: 实现 snapshot 子命令 (cmd/snapshot.go)

**Files:**
- Create: `cmd/snapshot.go`

- [ ] **Step 1: 创建 cmd/snapshot.go**

```go
package cmd

import (
	"encoding/json"
	"fmt"
	"os"

	"cdp/internal"

	"github.com/spf13/cobra"
)

type SnapshotResult struct {
	FrameID string          `json:"frameId"`
	Data    json.RawMessage `json:"data,omitempty"`
	Error   string          `json:"error,omitempty"`
}

var snapshotCmd = &cobra.Command{
	Use:   "snapshot",
	Short: "Get snapshot from all frames",
	RunE:  runSnapshot,
}

func init() {
	rootCmd.AddCommand(snapshotCmd)
}

func runSnapshot(cmd *cobra.Command, args []string) error {
	client, err := internal.NewClient(GetHost(), GetPort())
	if err != nil {
		return fmt.Errorf("failed to create client: %w", err)
	}
	defer client.Close()

	frameIDs, err := client.GetFrameTree()
	if err != nil {
		return fmt.Errorf("failed to get frame tree: %w", err)
	}

	results := make([]SnapshotResult, 0, len(frameIDs))
	for _, frameID := range frameIDs {
		result, err := client.RunIsolatedWorld(frameID, internal.SnapshotJS)
		if err != nil {
			results = append(results, SnapshotResult{
				FrameID: string(frameID),
				Error:   err.Error(),
			})
			continue
		}

		results = append(results, SnapshotResult{
			FrameID: string(frameID),
			Data:    json.RawMessage(result),
		})
	}

	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	return enc.Encode(results)
}
```

- [ ] **Step 2: 提交**

```bash
git add cmd/snapshot.go
git commit -m "feat: add snapshot subcommand"
```

---

### Task 6: 添加依赖并验证构建

**Files:**
- Modify: `go.mod`

- [ ] **Step 1: 添加依赖**

```bash
go mod tidy
```

- [ ] **Step 2: 验证构建**

```bash
go build -o cdp .
./cdp --help
```

Expected output:
```
CDP CLI tool for Chrome DevTools Protocol

Usage:
  cdp [command]

Available Commands:
  help      Help about any command
  snapshot  Get snapshot from all frames

Flags:
      --host string   Chrome host address (default "127.0.0.1")
      --port int      Chrome port (default 9222)

Use "cdp [command] --help" for more information about a command.
```

- [ ] **Step 3: 提交**

```bash
git add go.mod go.sum
git commit -m "chore: add dependencies"
```

---

### Task 7: 添加 README

**Files:**
- Create: `README.md`

- [ ] **Step 1: 创建 README.md**

```markdown
# cdp CLI

Chrome DevTools Protocol CLI tool for capturing frame snapshots.

## Usage

```bash
# Connect to local Chrome (default 127.0.0.1:9222)
cdp snapshot

# Connect to remote Chrome
cdp snapshot --host 192.168.1.100 --port 9222
```

## Requirements

- Chrome running with remote debugging enabled: `chrome --remote-debugging-port=9222`
```

- [ ] **Step 2: 提交**

```bash
git add README.md
git commit -m "docs: add README"
```