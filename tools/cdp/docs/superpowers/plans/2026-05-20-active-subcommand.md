# active 子命令 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `cdp active` 子命令，通过 CDP `Target.activateTarget` 将指定页面切换到前台。

**Architecture:** `cmd/active.go` 负责参数解析和输出，`internal/targets.go` 新增 `ActivateTarget` 函数（browser 级别 context，不绑定任何页面，cancel 不会关闭 target）。激活成功后调用 `ListPageTargets(host, port, true)` 输出结果。

**Tech Stack:** Go 1.26.2, chromedp, cobra

---

### Task 1: 新增 ActivateTarget 函数

**Files:**
- Modify: `internal/targets.go`（新增函数）

- [ ] **Step 1: 在 internal/targets.go 添加 ActivateTarget 函数**

在 `checkPageActive` 函数之后、`ListPageTargets` 函数之前添加：

```go
// ActivateTarget brings the specified page target to the foreground.
// It operates at browser-level (no WithTargetID), so canceling the context
// does NOT close any page target.
func ActivateTarget(host string, port int, targetID string) error {
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
			return target.ActivateTarget(target.ID(targetID)).Do(ctx)
		}),
	)
}
```

- [ ] **Step 2: 编译验证**

```bash
go build -o cdp main.go
```

预期：编译成功

- [ ] **Step 3: 提交**

```bash
git add internal/targets.go
git commit -m "feat: 新增 ActivateTarget 函数，通过 CDP Target.activateTarget 激活指定页面"
```

---

### Task 2: 新增 active 子命令

**Files:**
- Create: `cmd/active.go`
- Create: `cmd/active_test.go`

- [ ] **Step 1: 创建 cmd/active.go**

```go
package cmd

import (
	"encoding/json"
	"fmt"
	"os"

	"cdp/internal"

	"github.com/spf13/cobra"
)

var activeCmd = &cobra.Command{
	Use:   "active [target]",
	Short: "激活指定页面",
	Args:  cobra.MaximumNArgs(1),
	RunE:  runActive,
}

func init() {
	rootCmd.AddCommand(activeCmd)
	activeCmd.Flags().String("target", "", "目标页面 ID")
}

func resolveTarget(cmd *cobra.Command, args []string) (string, error) {
	target, _ := cmd.Flags().GetString("target")
	if target == "" {
		if len(args) == 0 {
			return "", fmt.Errorf("target is required")
		}
		target = args[0]
	}
	return target, nil
}

func runActive(cmd *cobra.Command, args []string) error {
	targetID, err := resolveTarget(cmd, args)
	if err != nil {
		return err
	}

	if err := internal.ActivateTarget(GetHost(), GetPort(), targetID); err != nil {
		return fmt.Errorf("failed to activate target: %w", err)
	}

	pages, err := internal.ListPageTargets(GetHost(), GetPort(), true)
	if err != nil {
		return fmt.Errorf("failed to list targets: %w", err)
	}

	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	return enc.Encode(pages)
}
```

- [ ] **Step 2: 创建 cmd/active_test.go**

```go
package cmd

import (
	"testing"

	"github.com/spf13/cobra"
)

func TestActiveCmdRegistered(t *testing.T) {
	found := false
	for _, sub := range rootCmd.Commands() {
		if sub.Name() == "active" {
			found = true
			if sub.Use != "active [target]" {
				t.Errorf("Use = %q, want %q", sub.Use, "active [target]")
			}
			if sub.Short == "" {
				t.Error("Short is empty")
			}
			break
		}
	}
	if !found {
		t.Error("active command not registered on rootCmd")
	}
}

func TestActiveCmdTargetFlag(t *testing.T) {
	cmd := findActiveCmd()
	if cmd == nil {
		t.Fatal("active command not found")
	}
	flag := cmd.Flags().Lookup("target")
	if flag == nil {
		t.Fatal("--target flag not registered")
	}
	if flag.DefValue != "" {
		t.Errorf("--target default = %q, want %q", flag.DefValue, "")
	}
}

func TestResolveTarget(t *testing.T) {
	tests := []struct {
		name     string
		flagVal  string
		args     []string
		expected string
		wantErr  bool
	}{
		{
			name:     "from flag",
			flagVal:  "ABC123",
			args:     []string{},
			expected: "ABC123",
		},
		{
			name:     "from positional arg",
			flagVal:  "",
			args:     []string{"DEF456"},
			expected: "DEF456",
		},
		{
			name:     "flag takes precedence",
			flagVal:  "ABC123",
			args:     []string{"DEF456"},
			expected: "ABC123",
		},
		{
			name:    "no target",
			flagVal: "",
			args:    []string{},
			wantErr: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			cmd := &cobra.Command{}
			cmd.Flags().String("target", "", "target ID")
			if tt.flagVal != "" {
				cmd.Flags().Set("target", tt.flagVal)
			}
			result, err := resolveTarget(cmd, tt.args)
			if tt.wantErr {
				if err == nil {
					t.Error("expected error, got nil")
				}
				return
			}
			if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			if result != tt.expected {
				t.Errorf("= %q, want %q", result, tt.expected)
			}
		})
	}
}

func findActiveCmd() *cobra.Command {
	for _, sub := range rootCmd.Commands() {
		if sub.Name() == "active" {
			return sub
		}
	}
	return nil
}
```

- [ ] **Step 3: 运行测试**

```bash
go test ./cmd/... -v
```

预期：所有测试 PASS

- [ ] **Step 4: 编译验证**

```bash
go build -o cdp main.go
```

预期：编译成功

- [ ] **Step 5: 提交**

```bash
git add cmd/active.go cmd/active_test.go
git commit -m "feat: 新增 active 子命令，通过 CDP Target.activateTarget 激活指定页面"
```

---

### Task 3: 端到端验证（需要 Chrome 运行）

- [ ] **Step 1: 启动 Chrome 并验证 active 命令**

```bash
# 确保 Chrome 已启动: chrome --remote-debugging-port=9222
# 打开两个以上标签页

# 查看 targets
go build -o cdp main.go && ./cdp targets

# 激活指定 target
./cdp active <target-id-from-above>
```

预期：输出 JSON 列表，目标 target 的 `"active": true`

- [ ] **Step 2: 验证 --target flag 形式**

```bash
./cdp active --target <target-id>
```

预期：同上
```
