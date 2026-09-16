# Click 子命令实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 click 子命令，模拟人类点击操作（自动滚动 + 高斯偏移 + touch/mouse 点击）

**Architecture:** 从 scroll 子命令提取公共滚动逻辑到 `client.ScrollToElement()`，新增 `client.ClickElement()` 封装完整的点击流程（滚动→定位→高斯偏移→点击）。click 和 scroll 子命令共享滚动和 iframe 解析逻辑。

**Tech Stack:** Go, chromedp (CDP), Cobra CLI

---

### Task 1: 扩展 GetElementCenter 返回宽高

**Files:**
- Modify: `internal/client.go:294-320`

GetElementCenter 当前只返回 x/y/centerX/centerY。ClickElement 需要 width/height 来计算高斯偏移的 σ。

- [ ] **Step 1: 修改 GetElementCenter 的 JS 和返回值**

将 `internal/client.go` 中 `GetElementCenter` 方法的 JS 字符串从：

```go
js := fmt.Sprintf(`(function(){
	var el = document.querySelector('%s');
	if (!el) return JSON.stringify({error: 'element not found'});
	var rect = el.getBoundingClientRect();
	if (!rect) return null;
	return JSON.stringify({x: rect.x, y: rect.y, centerX: rect.x + rect.width/2, centerY: rect.y + rect.height/2});
})()`, selector)
```

改为：

```go
js := fmt.Sprintf(`(function(){
	var el = document.querySelector('%s');
	if (!el) return JSON.stringify({error: 'element not found'});
	var rect = el.getBoundingClientRect();
	if (!rect) return null;
	return JSON.stringify({x: rect.x, y: rect.y, centerX: rect.x + rect.width/2, centerY: rect.y + rect.height/2, width: rect.width, height: rect.height});
})()`, selector)
```

返回值从：

```go
	return map[string]float64{
		"x":       result["x"].(float64),
		"y":       result["y"].(float64),
		"centerX": result["centerX"].(float64),
		"centerY": result["centerY"].(float64),
	}, nil
```

改为：

```go
	return map[string]float64{
		"x":       result["x"].(float64),
		"y":       result["y"].(float64),
		"centerX": result["centerX"].(float64),
		"centerY": result["centerY"].(float64),
		"width":   result["width"].(float64),
		"height":  result["height"].(float64),
	}, nil
```

- [ ] **Step 2: 验证构建**

Run: `cd /home/ansible/project/.claude/worktrees/click && go build -o cdp main.go`
Expected: 成功，无报错

---

### Task 2: 新增辅助方法到 client.go

**Files:**
- Modify: `internal/client.go`

新增 4 个方法：`ResolveIframeSelector`、`GaussianOffset`、`DispatchMouseClick`、`DispatchTouchClick`。

- [ ] **Step 1: 添加 ResolveIframeSelector 方法**

在 `internal/client.go` 中 `GetElementCenter` 方法之前添加：

```go
// ResolveIframeSelector finds the CSS selector for an iframe by frameID.
// Returns "iframe:nth-child(N)" based on position in frame tree.
func (c *Client) ResolveIframeSelector(frameID string) (string, error) {
	ft, err := c.GetFrameTree()
	if err != nil {
		return "", fmt.Errorf("failed to get frame tree: %w", err)
	}
	for i, child := range ft.ChildFrames {
		if child == nil {
			continue
		}
		if string(child.Frame.ID) == frameID {
			return fmt.Sprintf("iframe:nth-child(%d)", i), nil
		}
	}
	return "", fmt.Errorf("failed to find iframe %s", frameID)
}
```

- [ ] **Step 2: 添加 GaussianOffset 函数**

在 `internal/client.go` 中（文件末尾或 helper 区域）添加：

```go
// GaussianOffset returns random (dx, dy) offsets using Box-Muller transform.
// sigma controls spread: ~68% within ±sigma, ~95% within ±2*sigma.
func GaussianOffset(sigma float64) (float64, float64) {
	u1 := mathrand.Float64()
	for u1 == 0 {
		u1 = mathrand.Float64()
	}
	u2 := mathrand.Float64()
	mag := sigma * math.Sqrt(-2*math.Log(u1))
	dx := mag * math.Cos(2*math.Pi*u2)
	dy := mag * math.Sin(2*math.Pi*u2)
	return dx, dy
}
```

- [ ] **Step 3: 添加 DispatchMouseClick 方法**

在 `internal/client.go` 的 `DispatchMouseScrollEvent` 之后添加：

```go
// DispatchMouseClick dispatches mouse press and release at (x, y).
func (c *Client) DispatchMouseClick(x, y float64) error {
	return chromedp.Run(c.ctx, chromedp.ActionFunc(func(ctx context.Context) error {
		cc := chromedp.FromContext(ctx)
		if cc == nil || cc.Target == nil {
			return fmt.Errorf("invalid context")
		}
		exec := cdp.WithExecutor(ctx, cc.Target)
		if err := input.DispatchMouseEvent(input.MousePressed, x, y).WithButton(input.Left).WithClickCount(1).Do(exec); err != nil {
			return err
		}
		return input.DispatchMouseEvent(input.MouseReleased, x, y).WithButton(input.Left).WithClickCount(1).Do(exec)
	}))
}
```

- [ ] **Step 4: 添加 DispatchTouchClick 方法**

在 `DispatchMouseClick` 之后添加：

```go
// DispatchTouchClick dispatches touchStart then touchEnd at (x, y).
func (c *Client) DispatchTouchClick(x, y float64) error {
	if err := c.DispatchTouchEvent("touchStart", []TouchPoint{DefaultTouchPoint(x, y)}); err != nil {
		return fmt.Errorf("touchStart failed: %w", err)
	}
	return c.DispatchTouchEvent("touchEnd", []TouchPoint{DefaultTouchPoint(x, y)})
}
```

- [ ] **Step 5: 确认 import 包含所需依赖**

确认 `internal/client.go` 的 import 中有 `"math"` — 已存在。`mathrand` 别名（`mathrand "math/rand"`）也已存在。`math.Pi` 通过 `math` 包可用。无需新增 import。

- [ ] **Step 6: 验证构建**

Run: `cd /home/ansible/project/.claude/worktrees/click && go build -o cdp main.go`
Expected: 成功

---

### Task 3: 提取 ScrollToElement 并重构 scroll.go

**Files:**
- Modify: `internal/client.go` — 新增 `ScrollToElement` 方法
- Modify: `cmd/scroll.go` — 重构为调用 `ScrollToElement`

- [ ] **Step 1: 在 client.go 中添加 ScrollToElement 方法**

在 `internal/client.go` 中添加（放在 `ScrollMouseWheel` 之后）：

```go
// ScrollToElement scrolls the element into viewport using human-like gestures.
// Performs up to 20 iterations of touch/mouse scrolling until element is visible.
func (c *Client) ScrollToElement(selector string, track bool) error {
	isTouch, err := c.IsTouchDevice()
	if err != nil {
		return fmt.Errorf("failed to detect device type: %w", err)
	}

	for i := 0; i < 20; i++ {
		if isTouch {
			points, color, err := c.ScrollTouch(selector, track)
			if err != nil {
				return fmt.Errorf("scroll touch failed: %w", err)
			}
			if track && len(points) > 0 {
				if err := c.RenderTrack(points, color); err != nil {
					return fmt.Errorf("render track failed: %w", err)
				}
			}
		} else {
			if err := c.ScrollMouseWheel(selector); err != nil {
				return fmt.Errorf("scroll mouse wheel failed: %w", err)
			}
		}

		visible, err := c.IsElementVisible(selector)
		if err != nil {
			return fmt.Errorf("failed to check visibility: %w", err)
		}
		if visible {
			break
		}
	}
	return nil
}
```

- [ ] **Step 2: 重构 cmd/scroll.go**

将 `cmd/scroll.go` 的 `runScroll` 函数替换为：

```go
func runScroll(cmd *cobra.Command, args []string) error {
	selector, _ := cmd.Flags().GetString("selector")
	frameID, _ := cmd.Flags().GetString("frame-id")
	track, _ := cmd.Flags().GetBool("track")

	client, err := internal.NewClient(GetHost(), GetPort())
	if err != nil {
		return fmt.Errorf("failed to create client: %w", err)
	}

	scrollSelector := selector
	if frameID != "" {
		iframeSel, err := client.ResolveIframeSelector(frameID)
		if err != nil {
			return err
		}
		scrollSelector = iframeSel
	}

	if err := client.ScrollToElement(scrollSelector, track); err != nil {
		return err
	}

	return client.ScrollIntoView(selector, frameID)
}
```

注意：重构后不再需要 `"log"` import。清理为：

```go
import (
	"fmt"

	"cdp/internal"

	"github.com/spf13/cobra"
)
```

- [ ] **Step 3: 验证构建**

Run: `cd /home/ansible/project/.claude/worktrees/click && go build -o cdp main.go`
Expected: 成功

- [ ] **Step 4: Commit**

```bash
git add cmd/scroll.go internal/client.go
git commit -m "refactor: extract ScrollToElement and add click helper methods"
```

---

### Task 4: 添加 ClickElement 方法

**Files:**
- Modify: `internal/client.go`

- [ ] **Step 1: 在 client.go 中添加 ClickElement 方法**

在 `ScrollToElement` 之后添加：

```go
// ClickElement performs a human-like click on the element.
// 1. Scrolls element into viewport
// 2. Calculates center coordinates (with iframe offset if needed)
// 3. Applies Gaussian offset for natural click position
// 4. Dispatches touch or mouse click event
func (c *Client) ClickElement(selector, frameID string, track bool) (map[string]float64, error) {
	// Determine scroll target
	scrollSelector := selector
	if frameID != "" {
		iframeSel, err := c.ResolveIframeSelector(frameID)
		if err != nil {
			return nil, err
		}
		scrollSelector = iframeSel
	}

	// Scroll into view
	if err := c.ScrollToElement(scrollSelector, track); err != nil {
		return nil, err
	}
	if err := c.ScrollIntoView(selector, frameID); err != nil {
		return nil, fmt.Errorf("scrollIntoView failed: %w", err)
	}

	// Calculate click coordinates
	var clickX, clickY, elemWidth, elemHeight float64
	if frameID != "" {
		// Get iframe position in main frame
		iframeRect, err := c.GetElementCenter(scrollSelector, "")
		if err != nil {
			return nil, fmt.Errorf("failed to get iframe position: %w", err)
		}
		// Get element position within iframe
		elemRect, err := c.GetElementCenter(selector, frameID)
		if err != nil {
			return nil, fmt.Errorf("failed to get element position: %w", err)
		}
		clickX = iframeRect["x"] + elemRect["centerX"]
		clickY = iframeRect["y"] + elemRect["centerY"]
		elemWidth = elemRect["width"]
		elemHeight = elemRect["height"]
	} else {
		rect, err := c.GetElementCenter(selector, "")
		if err != nil {
			return nil, fmt.Errorf("failed to get element position: %w", err)
		}
		clickX = rect["centerX"]
		clickY = rect["centerY"]
		elemWidth = rect["width"]
		elemHeight = rect["height"]
	}

	// Apply Gaussian offset
	sigma := math.Min(elemWidth, elemHeight) / 4
	dx, dy := GaussianOffset(sigma)
	clickX += dx
	clickY += dy

	// Detect device and click
	isTouch, err := c.IsTouchDevice()
	if err != nil {
		return nil, fmt.Errorf("failed to detect device type: %w", err)
	}
	if isTouch {
		err = c.DispatchTouchClick(clickX, clickY)
	} else {
		err = c.DispatchMouseClick(clickX, clickY)
	}
	if err != nil {
		return nil, fmt.Errorf("click failed: %w", err)
	}

	return map[string]float64{"x": clickX, "y": clickY}, nil
}
```

- [ ] **Step 2: 验证构建**

Run: `cd /home/ansible/project/.claude/worktrees/click && go build -o cdp main.go`
Expected: 成功

---

### Task 5: 创建 cmd/click.go

**Files:**
- Create: `cmd/click.go`

- [ ] **Step 1: 创建 click 子命令文件**

创建 `cmd/click.go`：

```go
package cmd

import (
	"encoding/json"
	"fmt"
	"os"

	"cdp/internal"

	"github.com/spf13/cobra"
)

var clickCmd = &cobra.Command{
	Use:   "click",
	Short: "Click element with human-like gesture",
	RunE:  runClick,
}

func init() {
	rootCmd.AddCommand(clickCmd)
	clickCmd.Flags().String("selector", "", "CSS selector (required)")
	clickCmd.Flags().String("frame-id", "", "Frame ID (optional)")
	clickCmd.Flags().Bool("track", false, "Enable scroll track visualization")
	clickCmd.MarkFlagRequired("selector")
}

func runClick(cmd *cobra.Command, args []string) error {
	selector, _ := cmd.Flags().GetString("selector")
	frameID, _ := cmd.Flags().GetString("frame-id")
	track, _ := cmd.Flags().GetBool("track")

	client, err := internal.NewClient(GetHost(), GetPort())
	if err != nil {
		return fmt.Errorf("failed to create client: %w", err)
	}

	result, err := client.ClickElement(selector, frameID, track)
	if err != nil {
		return err
	}

	enc := json.NewEncoder(os.Stdout)
	return enc.Encode(result)
}
```

- [ ] **Step 2: 验证构建**

Run: `cd /home/ansible/project/.claude/worktrees/click && go build -o cdp main.go`
Expected: 成功

- [ ] **Step 3: 验证子命令注册**

Run: `cd /home/ansible/project/.claude/worktrees/click && ./cdp --help`
Expected: 输出中包含 `click` 子命令

Run: `cd /home/ansible/project/.claude/worktrees/click && ./cdp click --help`
Expected: 输出 `--selector`、`--frame-id`、`--track` flags

- [ ] **Step 4: Commit**

```bash
git add cmd/click.go internal/client.go
git commit -m "feat: add click subcommand with Gaussian offset and human-like gestures"
```
