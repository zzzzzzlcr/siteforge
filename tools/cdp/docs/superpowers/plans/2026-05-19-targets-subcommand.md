# targets 子命令实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `cdp targets` 子命令，列出 Chrome 中所有打开的页面

**Architecture:** 将过滤逻辑提取为纯函数 `filterPageTargets`（可脱离 Chrome 测试），`ListPageTargets` 负责连接 + 调用过滤。TDD 驱动：先写测试 → 确认失败 → 实现 → 确认通过

**Tech Stack:** Go 1.26, chromedp, cdproto/target, cobra

---

## 文件结构

```
internal/targets.go             # 新: filterPageTargets + ListPageTargets
internal/targets_test.go        # 新: filterPageTargets 单元测试
internal/targets_integration_test.go  # 新: ListPageTargets 集成测试 (//go:build integration)
cmd/targets.go                  # 新: targetsCmd
cmd/targets_test.go             # 新: 命令注册测试
```

无现有文件修改。

---

### Task 1: filterPageTargets (TDD)

**Files:**
- Create: `internal/targets_test.go`
- Create: `internal/targets.go`

- [ ] **Step 1: 写失败测试**

```go
package internal

import (
	"testing"

	"github.com/chromedp/cdproto/target"
)

func TestFilterPageTargets(t *testing.T) {
	tests := []struct {
		name     string
		targets  []*target.Info
		expected []PageTarget
	}{
		{
			name:     "empty",
			targets:  []*target.Info{},
			expected: nil,
		},
		{
			name: "single page",
			targets: []*target.Info{
				{TargetID: "AAA", Type: "page", Title: "GitHub", URL: "https://github.com"},
			},
			expected: []PageTarget{
				{ID: "AAA", Title: "GitHub", URL: "https://github.com"},
			},
		},
		{
			name: "filters non-page types",
			targets: []*target.Info{
				{TargetID: "AAA", Type: "page", Title: "GitHub", URL: "https://github.com"},
				{TargetID: "BBB", Type: "service_worker", Title: "SW", URL: "https://example.com/sw.js"},
			},
			expected: []PageTarget{
				{ID: "AAA", Title: "GitHub", URL: "https://github.com"},
			},
		},
		{
			name: "filters devtools URLs",
			targets: []*target.Info{
				{TargetID: "AAA", Type: "page", Title: "GitHub", URL: "https://github.com"},
				{TargetID: "BBB", Type: "page", Title: "DevTools", URL: "devtools://devtools/bundled/inspector.html"},
			},
			expected: []PageTarget{
				{ID: "AAA", Title: "GitHub", URL: "https://github.com"},
			},
		},
		{
			name: "filters empty URL",
			targets: []*target.Info{
				{TargetID: "AAA", Type: "page", Title: "GitHub", URL: "https://github.com"},
				{TargetID: "BBB", Type: "page", Title: "", URL: ""},
			},
			expected: []PageTarget{
				{ID: "AAA", Title: "GitHub", URL: "https://github.com"},
			},
		},
		{
			name: "multiple pages",
			targets: []*target.Info{
				{TargetID: "AAA", Type: "page", Title: "GitHub", URL: "https://github.com"},
				{TargetID: "BBB", Type: "page", Title: "Example", URL: "https://example.com"},
			},
			expected: []PageTarget{
				{ID: "AAA", Title: "GitHub", URL: "https://github.com"},
				{ID: "BBB", Title: "Example", URL: "https://example.com"},
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := filterPageTargets(tt.targets)
			if len(result) != len(tt.expected) {
				t.Fatalf("len = %d, want %d", len(result), len(tt.expected))
			}
			for i := range result {
				if result[i] != tt.expected[i] {
					t.Errorf("[%d] = %+v, want %+v", i, result[i], tt.expected[i])
				}
			}
		})
	}
}
```

- [ ] **Step 2: 运行测试，验证失败**

```bash
go test ./internal/ -run TestFilterPageTargets -v
```

Expected: FAIL — `undefined: filterPageTargets`

- [ ] **Step 3: 实现 filterPageTargets**

```go
package internal

import (
	"strings"

	"github.com/chromedp/cdproto/target"
)

type PageTarget struct {
	ID    string `json:"id"`
	Title string `json:"title"`
	URL   string `json:"url"`
}

// filterPageTargets 过滤出 type=page 的 target，排除 devtools:// 和空 URL
func filterPageTargets(targets []*target.Info) []PageTarget {
	var pages []PageTarget
	for _, t := range targets {
		if t.Type == "page" && t.URL != "" && !strings.HasPrefix(t.URL, "devtools://") {
			pages = append(pages, PageTarget{
				ID:    string(t.TargetID),
				Title: t.Title,
				URL:   t.URL,
			})
		}
	}
	return pages
}
```

只写 `filterPageTargets` 和 `PageTarget` 类型，先不写 `ListPageTargets`。

- [ ] **Step 4: 运行测试，验证通过**

```bash
go test ./internal/ -run TestFilterPageTargets -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add internal/targets.go internal/targets_test.go
git commit -m "feat: 添加 filterPageTargets 纯函数，过滤 page 类型 target"
```

---

### Task 2: ListPageTargets 集成测试 (TDD)

**Files:**
- Create: `internal/targets_integration_test.go`
- Modify: `internal/targets.go` — 添加 `ListPageTargets`

- [ ] **Step 1: 写集成测试（带 build tag，需要 Chrome）**

```go
//go:build integration

package internal

import (
	"testing"
)

func TestListPageTargets(t *testing.T) {
	pages, err := ListPageTargets("127.0.0.1", 9222)
	if err != nil {
		t.Fatalf("ListPageTargets failed: %v", err)
	}
	// 至少有一个 page（Chrome 必定至少打开一个空白页）
	if len(pages) == 0 {
		t.Error("expected at least one page target")
	}
	// 验证字段完整
	for _, p := range pages {
		if p.ID == "" {
			t.Error("page ID is empty")
		}
		if p.URL == "" {
			t.Error("page URL is empty")
		}
	}
}
```

- [ ] **Step 2: 运行集成测试，验证失败**

```bash
go test ./internal/ -run TestListPageTargets -tags=integration -v
```

Expected: FAIL — `undefined: ListPageTargets`

- [ ] **Step 3: 在 targets.go 中添加 ListPageTargets**

在已有文件末尾追加：

```go
import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"

	"github.com/chromedp/chromedp"
)

// ListPageTargets 连接浏览器，返回所有 page 类型 target
func ListPageTargets(host string, port int) ([]PageTarget, error) {
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

	return filterPageTargets(targets), nil
}
```

将原有 imports 和新增 imports 合并后，完整文件如下：

```go
package internal

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"strings"

	"github.com/chromedp/cdproto/target"
	"github.com/chromedp/chromedp"
)

type PageTarget struct {
	ID    string `json:"id"`
	Title string `json:"title"`
	URL   string `json:"url"`
}

func filterPageTargets(targets []*target.Info) []PageTarget {
	var pages []PageTarget
	for _, t := range targets {
		if t.Type == "page" && t.URL != "" && !strings.HasPrefix(t.URL, "devtools://") {
			pages = append(pages, PageTarget{
				ID:    string(t.TargetID),
				Title: t.Title,
				URL:   t.URL,
			})
		}
	}
	return pages
}

func ListPageTargets(host string, port int) ([]PageTarget, error) {
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

	return filterPageTargets(targets), nil
}
```

**注意:** 此步骤需要用完整内容覆盖 `internal/targets.go`。

- [ ] **Step 4: 运行单元测试确认未回归**

```bash
go test ./internal/ -run TestFilterPageTargets -v
```

Expected: PASS

- [ ] **Step 5: 编译验证**

```bash
go build -o cdp main.go
```

Expected: 编译成功

- [ ] **Step 6: 运行集成测试（需要 Chrome）**

```bash
go test ./internal/ -run TestListPageTargets -tags=integration -v
```

Expected: PASS（需要 Chrome 在 localhost:9222 运行）

- [ ] **Step 7: Commit**

```bash
git add internal/targets.go internal/targets_integration_test.go
git commit -m "feat: 添加 ListPageTargets 函数，连接浏览器获取 target 列表"
```

---

### Task 3: cmd/targets.go (TDD)

**Files:**
- Create: `cmd/targets_test.go`
- Create: `cmd/targets.go`

- [ ] **Step 1: 写测试**

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
```

- [ ] **Step 2: 运行测试，验证失败**

```bash
go test ./cmd/ -run TestTargetsCmdRegistered -v
```

Expected: FAIL — targets command not registered

- [ ] **Step 3: 实现 cmd/targets.go**

```go
package cmd

import (
	"encoding/json"
	"fmt"
	"os"

	"cdp/internal"

	"github.com/spf13/cobra"
)

var targetsCmd = &cobra.Command{
	Use:   "targets",
	Short: "列出所有打开的页面",
	RunE:  runTargets,
}

func init() {
	rootCmd.AddCommand(targetsCmd)
}

func runTargets(cmd *cobra.Command, args []string) error {
	pages, err := internal.ListPageTargets(GetHost(), GetPort())
	if err != nil {
		return fmt.Errorf("failed to list targets: %w", err)
	}

	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	return enc.Encode(pages)
}
```

- [ ] **Step 4: 运行测试，验证通过**

```bash
go test ./cmd/ -run TestTargetsCmdRegistered -v
```

Expected: PASS

- [ ] **Step 5: 编译验证**

```bash
go build -o cdp main.go
```

Expected: 编译成功

- [ ] **Step 6: Commit**

```bash
git add cmd/targets.go cmd/targets_test.go
git commit -m "feat: 添加 targets 子命令，列出所有打开的页面"
```

---

### Task 4: 端到端验证

- [ ] **Step 1: 帮助信息**

```bash
./cdp targets --help
```

Expected: 显示命令说明，包含 `--host` 和 `--port` flags

- [ ] **Step 2: 实际运行（需要 Chrome）**

```bash
./cdp targets | python3 -m json.tool
```

Expected: 格式化的 JSON 数组，每项含 `id`, `title`, `url`

- [ ] **Step 3: 自定义 host/port**

```bash
./cdp targets --host 127.0.0.1 --port 9222
```

Expected: 与上一步相同结果

- [ ] **Step 4: 运行全部测试**

```bash
go test ./internal/ ./cmd/ -v
```

Expected: 所有测试 PASS（不含集成测试 tag）

