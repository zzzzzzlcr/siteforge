# targets --active 标记活跃页面 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 `cdp targets` 新增 `--active` flag，通过检测 `document.visibilityState` 标记当前活跃页面。

**Architecture:** 扩展 `internal/targets.go` 的 `ListPageTargets` 函数签名，增加 `detectActive` 参数。当开启时逐页连接 CDP 执行 `document.visibilityState` 检测。`PageTarget` 新增 `Active *bool` 字段，`*bool` + `omitempty` 保证向后兼容。

**Tech Stack:** Go 1.26.2, chromedp, cobra

---

### Task 1: 更新 PageTarget 数据模型与纯函数

**Files:**
- Modify: `internal/targets.go:14-18`
- Modify: `internal/targets_test.go:1-85`（更新测试断言，增加 Active 字段验证）

- [ ] **Step 1: 修改 PageTarget 结构体**

在 `internal/targets.go` 中，为 `PageTarget` 增加 `Active` 字段：

```go
type PageTarget struct {
	ID     string `json:"id"`
	Title  string `json:"title"`
	URL    string `json:"url"`
	Active *bool  `json:"active,omitempty"`
}
```

- [ ] **Step 2: 更新 TestFilterPageTargets 测试断言**

在 `internal/targets_test.go` 中，所有 `expected` 里的 `PageTarget{}` 保持不变（`Active` 为 `nil`，`filterPageTargets` 纯函数不设置 `Active`）。验证测试仍然通过——这是确认 `*bool` + `omitempty` 不影响现有行为的回归测试。

运行测试：

```bash
go test ./internal/ -run TestFilterPageTargets -v
```

预期：PASS（6 个子测试全部通过）

- [ ] **Step 3: 提交**

```bash
git add internal/targets.go internal/targets_test.go
git commit -m "feat: PageTarget 新增 Active *bool 字段（omitempty 保证向后兼容）"
```

---

### Task 2: 实现活跃页检测函数

**Files:**
- Modify: `internal/targets.go`（新增 `isPageActive` 函数）

- [ ] **Step 1: 新增 isPageActive 函数**

在 `internal/targets.go` 的 `filterPageTargets` 之后添加：

```go
// isPageActive connects to a page target and checks if it's the active tab
// by evaluating document.visibilityState.
func isPageActive(wsURL, targetID string) (bool, error) {
	allocCtx, allocCancel := chromedp.NewRemoteAllocator(context.Background(), wsURL)
	defer allocCancel()

	ctx, cancel := chromedp.NewContext(allocCtx, chromedp.WithTargetID(target.ID(targetID)))
	defer cancel()

	var state string
	err := chromedp.Run(ctx, chromedp.Evaluate(`document.visibilityState`, &state))
	if err != nil {
		return false, fmt.Errorf("failed to check visibilityState for %s: %w", targetID, err)
	}
	return state == "visible", nil
}
```

需要在 import 中增加 `"github.com/chromedp/cdproto/target"`（已存在，确认无误）。

- [ ] **Step 2: 编译验证**

```bash
go build -o cdp main.go
```

预期：编译成功，无错误

- [ ] **Step 3: 提交**

```bash
git add internal/targets.go
git commit -m "feat: 新增 isPageActive 函数，通过 visibilityState 检测活跃页面"
```

---

### Task 3: 改造 ListPageTargets 支持 detectActive 参数

**Files:**
- Modify: `internal/targets.go:36-62`（`ListPageTargets` 函数签名和逻辑）

- [ ] **Step 1: 修改 ListPageTargets 签名和逻辑**

将 `ListPageTargets` 函数签名改为 `ListPageTargets(host string, port int, detectActive bool)`，并在函数末尾增加活跃检测逻辑：

```go
func ListPageTargets(host string, port int, detectActive bool) ([]PageTarget, error) {
	resp, err := http.Get(fmt.Sprintf("http://%s:%d/json/version", host, port))
	if err != nil {
		return nil, fmt.Errorf("failed to connect to Chrome: %w", err)
	}
	defer resp.Body.Close()

	var version struct {
		WebSocketDebuggerURL string `json:"webSocketDebuggerUrl"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&version); err != nil {
		return nil, fmt.Errorf("failed to parse version response: %w", err)
	}

	allocCtx, allocCancel := chromedp.NewRemoteAllocator(context.Background(), version.WebSocketDebuggerURL)
	defer allocCancel()

	ctx, cancel := chromedp.NewContext(allocCtx)
	defer cancel()

	targets, err := chromedp.Targets(ctx)
	if err != nil {
		return nil, fmt.Errorf("failed to get targets: %w", err)
	}

	pages := filterPageTargets(targets)

	if detectActive {
		for i := range pages {
			active, err := isPageActive(version.WebSocketDebuggerURL, string(pages[i].ID))
			if err == nil {
				pages[i].Active = &active
			}
		}
	}

	return pages, nil
}
```

- [ ] **Step 2: 编译验证**

```bash
go build -o cdp main.go
```

预期：编译失败，因为 `cmd/targets.go` 调用 `ListPageTargets` 的参数数量不匹配。这是预期的——确认调用方需要更新。

- [ ] **Step 3: 提交**

```bash
git add internal/targets.go
git commit -m "feat: ListPageTargets 增加 detectActive 参数，遍历检测活跃页面"
```

---

### Task 4: 更新 cmd/targets.go 增加 --active flag

**Files:**
- Modify: `cmd/targets.go:1-32`
- Modify: `cmd/targets_test.go:1-24`（增加 flag 注册测试）

- [ ] **Step 1: 修改 cmd/targets.go**

```go
package cmd

import (
	"encoding/json"
	"fmt"
	"os"

	"cdp/internal"

	"github.com/spf13/cobra"
)

var (
	targetsActive bool
)

var targetsCmd = &cobra.Command{
	Use:   "targets",
	Short: "列出所有打开的页面",
	RunE:  runTargets,
}

func init() {
	targetsCmd.Flags().BoolVar(&targetsActive, "active", false, "标记当前活跃页面")
	rootCmd.AddCommand(targetsCmd)
}

func runTargets(cmd *cobra.Command, args []string) error {
	pages, err := internal.ListPageTargets(GetHost(), GetPort(), targetsActive)
	if err != nil {
		return fmt.Errorf("failed to list targets: %w", err)
	}

	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	return enc.Encode(pages)
}
```

- [ ] **Step 2: 更新 cmd/targets_test.go 增加 flag 测试**

```go
package cmd

import (
	"testing"
)

func TestTargetsCmdRegistered(t *testing.T) {
	found := false
	for _, sub := range rootCmd.Commands() {
		if sub.Name() == "targets" {
			found = true
			if sub.Use != "targets" {
				t.Errorf("Use = %q, want %q", sub.Use, "targets")
			}
			if sub.Short == "" {
				t.Error("Short is empty")
			}
			break
		}
	}
	if !found {
		t.Error("targets command not registered on rootCmd")
	}
}

func TestTargetsCmdActiveFlag(t *testing.T) {
	cmd := findTargetsCmd()
	if cmd == nil {
		t.Fatal("targets command not found")
	}
	flag := cmd.Flags().Lookup("active")
	if flag == nil {
		t.Fatal("--active flag not registered")
	}
	if flag.DefValue != "false" {
		t.Errorf("--active default = %q, want %q", flag.DefValue, "false")
	}
}

func findTargetsCmd() *cobra.Command {
	for _, sub := range rootCmd.Commands() {
		if sub.Name() == "targets" {
			return sub
		}
	}
	return nil
}
```

- [ ] **Step 3: 运行所有测试**

```bash
go test ./cmd/... -v
go test ./internal/... -v
```

预期：所有测试 PASS

- [ ] **Step 4: 编译完整二进制**

```bash
go build -o cdp main.go
```

预期：编译成功

- [ ] **Step 5: 提交**

```bash
git add cmd/targets.go cmd/targets_test.go
git commit -m "feat: targets 子命令新增 --active flag，标记当前活跃页面"
```

---

### Task 5: 更新集成测试

**Files:**
- Modify: `internal/targets_integration_test.go:1-27`

- [ ] **Step 1: 更新集成测试以匹配新签名**

```go
//go:build integration

package internal

import (
	"testing"
)

func TestListPageTargets(t *testing.T) {
	pages, err := ListPageTargets("127.0.0.1", 9222, false)
	if err != nil {
		t.Fatalf("ListPageTargets failed: %v", err)
	}
	if len(pages) == 0 {
		t.Error("expected at least one page target")
	}
	for _, p := range pages {
		if p.ID == "" {
			t.Error("page ID is empty")
		}
		if p.URL == "" {
			t.Error("page URL is empty")
		}
		if p.Active != nil {
			t.Error("Active should be nil when detectActive=false")
		}
	}
}

func TestListPageTargetsWithActive(t *testing.T) {
	pages, err := ListPageTargets("127.0.0.1", 9222, true)
	if err != nil {
		t.Fatalf("ListPageTargets with detectActive failed: %v", err)
	}
	if len(pages) == 0 {
		t.Error("expected at least one page target")
	}
	activeCount := 0
	for _, p := range pages {
		if p.ID == "" {
			t.Error("page ID is empty")
		}
		if p.Active == nil {
			t.Error("Active should not be nil when detectActive=true")
		} else if *p.Active {
			activeCount++
		}
	}
	if activeCount == 0 {
		t.Error("expected at least one active page")
	}
}
```

- [ ] **Step 2: 手动验证集成测试（需要 Chrome 运行）**

```bash
go test ./internal/ -run TestListPageTargetsWithActive -tags=integration -v
```

预期：PASS（至少一个页面的 `active` 为 `true`）

- [ ] **Step 3: 端到端验证**

```bash
go build -o cdp main.go && ./cdp targets --active | head -20
```

预期：输出包含 `"active": true` 和 `"active": false` 字段

- [ ] **Step 4: 提交**

```bash
git add internal/targets_integration_test.go
git commit -m "test: 更新集成测试覆盖 detectActive 参数"
```
