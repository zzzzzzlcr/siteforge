# close 子命令 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `cdp close` 子命令，通过 target ID 关闭 Chrome 页面，支持 `--all` 批量关闭。

**Architecture:** 在 `internal/targets.go` 新增 `CloseTarget` 函数（浏览器级连接，调用 CDP `target.CloseTarget`）；在 `cmd/close.go` 新增 `closeCmd`（参数解析、active 保护、调用 CloseTarget、输出剩余列表）。参考 `ActivateTarget` 的浏览器级连接模式和 `active` 命令的 CLI 模式。

**Tech Stack:** Go 1.26.2, chromedp, cobra

---

### Task 1: 新增 `CloseTarget` 函数

**Files:**
- Modify: `internal/targets.go` — 在文件末尾追加

- [ ] **Step 1: 添加 `CloseTarget` 函数**

```go
// CloseTarget closes a page target by its ID using the browser-level connection.
// It connects at browser level (no WithTargetID) so cancel does not accidentally
// close the target being operated on.
func CloseTarget(host string, port int, targetID string) error {
	resp, err := http.Get(fmt.Sprintf("http://%s:%d/json/version", host, port))
	if err != nil {
		return fmt.Errorf("failed to connect to Chrome: %w", err)
	}
	defer resp.Body.Close()

	var version struct {
		WebSocketDebuggerURL string `json:"webSocketDebuggerUrl"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&version); err != nil {
		return fmt.Errorf("failed to parse version response: %w", err)
	}

	allocCtx, allocCancel := chromedp.NewRemoteAllocator(context.Background(), version.WebSocketDebuggerURL)
	defer allocCancel()

	ctx, cancel := chromedp.NewContext(allocCtx)
	defer cancel()

	return chromedp.Run(ctx,
		chromedp.ActionFunc(func(ctx context.Context) error {
			return target.CloseTarget(target.ID(targetID)).Do(ctx)
		}),
	)
}
```

- [ ] **Step 2: 运行现有测试确保没有破坏**

Run: `go test ./...`
Expected: PASS (已有测试不受影响)

- [ ] **Step 3: 验证编译通过**

Run: `go build -o cdp main.go`
Expected: 编译成功

- [ ] **Step 4: Commit**

```bash
git add internal/targets.go
git commit -m "feat: 新增 CloseTarget 函数，通过浏览器级连接关闭 page target"
```

---

### Task 2: 新增 `closeCmd`

**Files:**
- Create: `cmd/close.go`

- [ ] **Step 1: 创建 `cmd/close.go`**

```go
package cmd

import (
	"encoding/json"
	"fmt"
	"os"

	"cdp/internal"

	"github.com/spf13/cobra"
)

var closeCmd = &cobra.Command{
	Use:   "close [target-id...]",
	Short: "关闭指定页面",
	Long:  `通过 target ID 关闭 Chrome 页面。支持 --all 关闭所有非活跃页面。`,
	Args:  cobra.ArbitraryArgs,
	RunE:  runClose,
}

func init() {
	rootCmd.AddCommand(closeCmd)
	closeCmd.Flags().Bool("all", false, "关闭所有非活跃页面")
}

func runClose(cmd *cobra.Command, args []string) error {
	all, _ := cmd.Flags().GetBool("all")

	if !all && len(args) == 0 {
		return fmt.Errorf("需要指定至少一个 target ID 或使用 --all")
	}

	// 收集要关闭的 target ID（去重）
	toClose := make(map[string]struct{})
	for _, id := range args {
		toClose[id] = struct{}{}
	}

	// 获取 active 页面 ID
	activeID, err := internal.GetActivePageTargetID(GetHost(), GetPort())
	if err != nil {
		return fmt.Errorf("failed to get active page: %w", err)
	}

	if all {
		pages, err := internal.ListPageTargets(GetHost(), GetPort(), false)
		if err != nil {
			return fmt.Errorf("failed to list targets: %w", err)
		}
		for _, p := range pages {
			if p.ID == activeID {
				continue // 跳过 active 页面
			}
			toClose[p.ID] = struct{}{}
		}
	}

	// 检查是否尝试关闭 active 页面
	if _, ok := toClose[activeID]; ok {
		return fmt.Errorf("不能关闭活跃页面 %s", activeID)
	}

	// 逐个关闭
	var firstErr error
	for id := range toClose {
		if err := internal.CloseTarget(GetHost(), GetPort(), id); err != nil {
			if firstErr == nil {
				firstErr = err
			}
			fmt.Fprintf(os.Stderr, "关闭 %s 失败: %v\n", id, err)
		}
	}

	// 输出剩余 page targets
	pages, err := internal.ListPageTargets(GetHost(), GetPort(), true)
	if err != nil {
		return fmt.Errorf("failed to list targets: %w", err)
	}

	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	if err := enc.Encode(pages); err != nil {
		return fmt.Errorf("failed to encode output: %w", err)
	}

	return firstErr
}
```

- [ ] **Step 2: 验证编译通过**

Run: `go build -o cdp main.go`
Expected: 编译成功

- [ ] **Step 3: Commit**

```bash
git add cmd/close.go
git commit -m "feat: 新增 close 子命令，支持 --all 和指定 target ID 关闭页面"
```

---

### Task 3: 单元测试 closeCmd 注册和 flag

**Files:**
- Create: `cmd/close_test.go`

- [ ] **Step 1: 创建 `cmd/close_test.go`**

```go
package cmd

import (
	"testing"
)

func TestCloseCmdRegistered(t *testing.T) {
	found := false
	for _, sub := range rootCmd.Commands() {
		if sub.Name() == "close" {
			found = true
			if sub.Use != "close [target-id...]" {
				t.Errorf("Use = %q, want %q", sub.Use, "close [target-id...]")
			}
			if sub.Short == "" {
				t.Error("Short is empty")
			}
			break
		}
	}
	if !found {
		t.Error("close command not registered on rootCmd")
	}
}

func TestCloseCmdAllFlag(t *testing.T) {
	for _, sub := range rootCmd.Commands() {
		if sub.Name() == "close" {
			flag := sub.Flags().Lookup("all")
			if flag == nil {
				t.Fatal("--all flag not registered")
			}
			if flag.DefValue != "false" {
				t.Errorf("--all default = %q, want %q", flag.DefValue, "false")
			}
			return
		}
	}
	t.Fatal("close command not found")
}
```

- [ ] **Step 2: 运行单元测试**

Run: `go test ./cmd/ -run "TestCloseCmd" -v`
Expected: 2 tests PASS

- [ ] **Step 3: Commit**

```bash
git add cmd/close_test.go
git commit -m "test: 新增 close 子命令单元测试（注册和 flag）"
```

---

### Task 4: 集成测试 CloseTarget

**Files:**
- Create: `internal/close_integration_test.go`

- [ ] **Step 1: 创建 `internal/close_integration_test.go`**

```go
//go:build integration

package internal

import (
	"testing"
)

func TestCloseTarget(t *testing.T) {
	host := "127.0.0.1"
	port := 9222

	pagesBefore, err := ListPageTargets(host, port, false)
	if err != nil {
		t.Fatalf("ListPageTargets failed: %v", err)
	}
	if len(pagesBefore) < 2 {
		t.Skip("需要至少 2 个 page target，请打开多个 tab 后重试")
	}

	activeID, err := GetActivePageTargetID(host, port)
	if err != nil {
		t.Fatalf("GetActivePageTargetID failed: %v", err)
	}

	var targetToClose string
	for _, p := range pagesBefore {
		if p.ID != activeID {
			targetToClose = p.ID
			break
		}
	}
	if targetToClose == "" {
		t.Skip("没有非活跃页面可供关闭")
	}

	if err := CloseTarget(host, port, targetToClose); err != nil {
		t.Fatalf("CloseTarget failed: %v", err)
	}

	pagesAfter, err := ListPageTargets(host, port, false)
	if err != nil {
		t.Fatalf("ListPageTargets after close failed: %v", err)
	}
	if len(pagesAfter) >= len(pagesBefore) {
		t.Errorf("关闭后页面数未减少: before=%d, after=%d", len(pagesBefore), len(pagesAfter))
	}
	for _, p := range pagesAfter {
		if p.ID == targetToClose {
			t.Errorf("已关闭的 target %s 仍在页面列表中", targetToClose)
		}
	}
}

func TestCloseNonExistentTarget(t *testing.T) {
	host := "127.0.0.1"
	port := 9222

	err := CloseTarget(host, port, "non-existent-target-id")
	if err == nil {
		t.Error("关闭不存在的 target 应返回错误")
	}
}
```

- [ ] **Step 2: 运行集成测试**

Run: `go test -tags=integration ./internal/ -run "TestCloseTarget|TestCloseNonExistentTarget" -v`
Expected: TestCloseTarget 需要 2+ tabs；TestCloseNonExistentTarget PASS（返回错误）

- [ ] **Step 3: Commit**

```bash
git add internal/close_integration_test.go
git commit -m "test: 新增 CloseTarget 集成测试"
```

---

### Task 5: 最终构建和验证

- [ ] **Step 1: 运行所有测试**

Run: `go test ./...`
Expected: 所有单元测试 PASS

Run: `go vet ./...`
Expected: 无错误

- [ ] **Step 2: 构建**

Run: `go build -o cdp main.go`
Expected: 编译成功，生成 `cdp` 二进制

- [ ] **Step 3: 手动验证帮助信息**

Run: `./cdp close --help`
Expected: 显示 close 子命令的帮助信息，包含 `--all` flag
