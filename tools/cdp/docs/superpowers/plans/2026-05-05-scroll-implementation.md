# scroll 子命令实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 `cdp scroll [--selector] <selector> [--frame-id <frame-id>] [--track]` 子命令

**Architecture:**
- `cmd/scroll.go`：cobra 子命令，参数解析，流程编排
- `internal/client.go`：新增 `DispatchTouchEvent()`、`DispatchMouseScrollEvent()`、`EvalInIsolatedWorld()` 方法
- `internal/js.go`：新增 `GetElementCenterJS`、`ScrollIntoViewJS`、`TrackRenderJS`

**Tech Stack:** chromedp, cobra, CDP input 协议

---

## 文件结构

```
cmd/scroll.go          # 新建：scroll 子命令
internal/client.go     # 修改：新增 Touch/MouseWheel/frame eval 方法
internal/js.go         # 修改：新增 JS 常量
```

---

### Task 1: 新建 `cmd/scroll.go`

**Files:**
- Create: `cmd/scroll.go`

```go
package cmd

import (
    "fmt"
    "cdp/internal"
    "github.com/spf13/cobra"
)

var scrollCmd = &cobra.Command{
    Use:   "scroll",
    Short: "Scroll element into view with human-like gesture",
    RunE:  runScroll,
}

func init() {
    rootCmd.AddCommand(scrollCmd)
    scrollCmd.Flags().String("selector", "", "CSS selector (required)")
    scrollCmd.Flags().String("frame-id", "", "Frame ID (optional)")
    scrollCmd.Flags().Bool("track", false, "Enable track visualization")
    scrollCmd.MarkFlagRequired("selector")
}

func runScroll(cmd *cobra.Command, args []string) error {
    selector, _ := cmd.Flags().GetString("selector")
    frameID, _ := cmd.Flags().GetString("frame-id")
    track, _ := cmd.Flags().GetBool("track")

    client, err := internal.NewClient(GetHost(), GetPort())
    if err != nil {
        return fmt.Errorf("failed to create client: %w", err)
    }
    defer client.Close()

    // 1. 获取元素中心坐标
    center, err := client.GetElementCenter(selector, frameID)
    if err != nil {
        return fmt.Errorf("failed to get element center: %w", err)
    }

    // 2. 判断设备类型
    isTouch, err := client.IsTouchDevice(frameID)
    if err != nil {
        return fmt.Errorf("failed to detect device type: %w", err)
    }

    // 3. 执行滑动
    var trackPoints [][]float64
    var trackColor string
    for i := 0; i < 20; i++ {
        if isTouch {
            points, color, err := client.ScrollTouch(selector, frameID, track)
            if err != nil {
                return fmt.Errorf("scroll touch failed: %w", err)
            }
            if track {
                trackPoints = append(trackPoints, points...)
                trackColor = color
            }
        } else {
            err := client.ScrollMouseWheel(selector, frameID)
            if err != nil {
                return fmt.Errorf("scroll mouse wheel failed: %w", err)
            }
        }

        // 4. 检查元素是否可见
        visible, err := client.IsElementVisible(selector, frameID)
        if err != nil {
            return fmt.Errorf("failed to check visibility: %w", err)
        }
        if visible {
            break
        }
    }

    // 5. 执行 scrollIntoView
    err = client.ScrollIntoView(selector, frameID)
    if err != nil {
        return fmt.Errorf("scrollIntoView failed: %w", err)
    }

    // 6. 绘制轨迹
    if track && len(trackPoints) > 0 {
        err = client.RenderTrack(trackPoints, trackColor)
        if err != nil {
            return fmt.Errorf("render track failed: %w", err)
        }
    }

    return nil
}
```

- [ ] **Step 2: 添加占位实现到 client.go（让编译通过）**

在 `internal/client.go` 末尾添加：

```go
// GetElementCenter returns element center coordinates
func (c *Client) GetElementCenter(selector, frameID string) (map[string]float64, error) {
    js := fmt.Sprintf(`(function(){
        var el = document.querySelector('%s');
        if (!el) return JSON.stringify({error: 'element not found'});
        var rect = el.getBoundingClientRect();
        return JSON.stringify({x: rect.x, y: rect.y, centerX: rect.x + rect.width/2, centerY: rect.y + rect.height/2});
    })()`, selector)
    var result map[string]interface{}
    err := c.EvalInFrame(frameID, js, &result)
    if err != nil {
        return nil, err
    }
    if _, ok := result["error"]; ok {
        return nil, fmt.Errorf("%v", result["error"])
    }
    return map[string]float64{
        "x":       result["x"].(float64),
        "y":       result["y"].(float64),
        "centerX": result["centerX"].(float64),
        "centerY": result["centerY"].(float64),
    }, nil
}

// IsTouchDevice returns true if user agent indicates mobile/tablet
func (c *Client) IsTouchDevice(frameID string) (bool, error) {
    var result bool
    err := c.EvalInFrame(frameID, internal.TouchDetectionJS, &result)
    return result, err
}

// IsElementVisible checks if element visible area >= 80%
func (c *Client) IsElementVisible(selector, frameID string) (bool, error) {
    js := fmt.Sprintf(internal.ElementVisibilityJS, selector)
    var result bool
    err := c.EvalInFrame(frameID, js, &result)
    return result, err
}

// ScrollIntoView executes scrollIntoView on element
func (c *Client) ScrollIntoView(selector, frameID string) error {
    js := fmt.Sprintf(`(function(){
        var el = document.querySelector('%s');
        if (!el) return {error: 'element not found'};
        el.scrollIntoView();
        return {success: true};
    })()`, selector)
    var result map[string]interface{}
    return c.EvalInFrame(frameID, js, &result)
}

// RenderTrack renders track points on main frame
func (c *Client) RenderTrack(points [][]float64, color string) error {
    js := internal.TrackRenderJS
    // 通过 window 变量传递 points 和 color
    pointsJSON, _ := json.Marshal(points)
    setPointsJS := fmt.Sprintf(`window.__trackPath = %s; window.__trackColor = '%s';`, string(pointsJSON), color)
    err := c.EvalInFrame("", setPointsJS, nil)
    if err != nil {
        return err
    }
    return c.EvalInFrame("", js, nil)
}
```

- [ ] **Step 3: 添加占位实现到 js.go**

在 `internal/js.go` 末尾添加：

```go
// ScrollIntoViewJS scrolls element into view
const ScrollIntoViewJS = `...` // 见 spec

// GetElementCenterJS gets element center coordinates
const GetElementCenterJS = `(function(){
    var el = document.querySelector('%s');
    if (!el) return null;
    var rect = el.getBoundingClientRect();
    return JSON.stringify({x: rect.x, y: rect.y, centerX: rect.x + rect.width/2, centerY: rect.y + rect.height/2});
})()`
```

- [ ] **Step 4: 运行 `go build -o cdp main.go` 验证编译**

- [ ] **Step 5: Commit**

```bash
git add cmd/scroll.go internal/client.go internal/js.go
git commit -m "feat: 实现 scroll 子命令骨架

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 2: 实现 `ScrollTouch()` 方法

**Files:**
- Modify: `internal/client.go`

- [ ] **Step 1: 实现 `DispatchTouchEvent()` CDP 调用**

```go
// TouchPoint represents a single touch point
type TouchPoint struct {
    X           float64
    Y           float64
    Pressure    float64
    RadiusX     float64
    RadiusY     float64
    RotationAngle float64
    Force       float64
    TiltX       float64
    TiltY       float64
}

// DispatchTouchEvent dispatches touch event via CDP Input.dispatchTouchEvent
func (c *Client) DispatchTouchEvent(touchType string, points []TouchPoint) error {
    return chromedp.Run(c.ctx, chromedp.ActionFunc(func(ctx context.Context) error {
        cc := chromedp.FromContext(ctx)
        if cc == nil || cc.Target == nil {
            return fmt.Errorf("invalid context")
        }
        exec := cdp.WithExecutor(ctx, cc.Target)

        var actions []input.Action
        switch touchType {
        case "touchStart":
            actions = []input.Action{input.DispatchTouchEvent(input.TouchStart).WithTouchPoints(points)}
        case "touchMove":
            actions = []input.Action{input.DispatchTouchEvent(input.TouchMove).WithTouchPoints(points)}
        case "touchEnd":
            actions = []input.Action{input.DispatchTouchEvent(input.TouchEnd).WithTouchPoints([]TouchPoint{})}
        }

        for _, action := range actions {
            if err := action.Do(exec); err != nil {
                return err
            }
        }
        return nil
    }))
}
```

- [ ] **Step 2: 实现 `cubicBezier()` 贝塞尔曲线采样**

```go
import "math"

// cubicBezier calculates point on cubic bezier curve at t
func cubicBezier(t float64, p0, p1, p2, p3 []float64) []float64 {
    t2 := t * t
    t3 := t2 * t
    mt := 1 - t
    mt2 := mt * mt
    mt3 := mt2 * mt
    return []float64{
        mt3*p0[0] + 3*mt2*t*p1[0] + 3*mt*t2*p2[0] + t3*p3[0],
        mt3*p0[1] + 3*mt2*t*p1[1] + 3*mt*t2*p2[1] + t3*p3[1],
    }
}

// sampleBezier samples n points from cubic bezier curve
func sampleBezier(p0, p1, p2, p3 []float64, n int) [][]float64 {
    points := make([][]float64, n)
    for i := 0; i < n; i++ {
        t := float64(i) / float64(n-1)
        pt := cubicBezier(t, p0, p1, p2, p3)
        points[i] = pt
    }
    return points
}
```

- [ ] **Step 3: 实现 `ScrollTouch()` 方法**

```go
// ScrollTouch performs touch scroll gesture
// Returns all touch points and color if track=true
func (c *Client) ScrollTouch(selector, frameID string, track bool) ([][]float64, string, error) {
    // 获取 viewport 尺寸和元素中心
    var vpWidth, vpHeight, centerX, centerY float64
    js := `(function(){
        return JSON.stringify({width: window.innerWidth, height: window.innerHeight});
    })()`
    var vp map[string]float64
    err := c.EvalInFrame(frameID, js, &vp)
    if err != nil {
        return nil, "", err
    }
    vpWidth = vp["width"]
    vpHeight = vp["height"]

    center, err := c.GetElementCenter(selector, frameID)
    if err != nil {
        return nil, "", err
    }
    centerX = center["centerX"]
    centerY = center["centerY"]

    // 计算滑动方向
    isAbove := centerY < vpHeight/2

    // 随机偏移（热力图随机）
    randOffsetX := (mathrand.Float64()*2 - 1) * 30 // ±30px
    randOffsetY := (mathrand.Float64()*2 - 1) * 20 // ±20px，基于拇指长度

    var startX, startY, stopX, stopY float64
    if isAbove {
        // 下滑
        startX = vpWidth/2 + randOffsetX
        startY = vpHeight/2 + randOffsetY
        stopX = startX - randOffsetX
        stopY = vpHeight - randOffsetY
    } else {
        // 上滑
        startX = vpWidth/2 + randOffsetX
        startY = vpHeight - randOffsetY
        stopX = startX + randOffsetX
        stopY = vpHeight/2 + randOffsetY
    }

    // 三次贝塞尔曲线
    distance := math.Abs(stopY - startY)
    p0 := []float64{startX, startY}
    p1 := []float64{startX, startY - distance*0.3 + randOffsetY}
    p2 := []float64{stopX + distance*0.2, stopY + randOffsetY}
    p3 := []float64{stopX, stopY}

    points := sampleBezier(p0, p1, p2, p3, 15)

    // 分 3 次发送 touch 事件
    // 1. touchStart + 1 point
    c.DispatchTouchEvent("touchStart", []TouchPoint{{X: points[0][0], Y: points[0][1]}})
    // 2. touchMove + 13 points (中间部分)
    touchPoints := make([]TouchPoint, len(points)-2)
    for i := 1; i < len(points)-1; i++ {
        touchPoints[i-1] = TouchPoint{X: points[i][0], Y: points[i][1]}
    }
    c.DispatchTouchEvent("touchMove", touchPoints)
    // 3. touchEnd + 1 point
    c.DispatchTouchEvent("touchEnd", []TouchPoint{{X: points[len(points)-1][0], Y: points[len(points)-1][1]}})

    // 生成随机颜色
    color := fmt.Sprintf("hsl(%d, 70%%, 50%%)", int(mathrand.Float64()*360))

    return points, color, nil
}
```

需要添加 `math/rand` 别名（已有 import）并使用 `mathrand` 避免和 `math` 冲突。

- [ ] **Step 4: 运行 `go build -o cdp main.go` 验证编译**

- [ ] **Step 5: Commit**

```bash
git add internal/client.go
git commit -m "feat: 实现 ScrollTouch 和贝塞尔曲线采样

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 3: 实现 `ScrollMouseWheel()` 方法

**Files:**
- Modify: `internal/client.go`

- [ ] **Step 1: 实现 `DispatchMouseScrollEvent()` CDP 调用**

```go
// DispatchMouseScrollEvent dispatches mouse scroll event via CDP
func (c *Client) DispatchMouseScrollEvent(deltaX, deltaY float64) error {
    return chromedp.Run(c.ctx, chromedp.ActionFunc(func(ctx context.Context) error {
        cc := chromedp.FromContext(ctx)
        if cc == nil || cc.Target == nil {
            return fmt.Errorf("invalid context")
        }
        exec := cdp.WithExecutor(ctx, cc.Target)
        return input.DispatchMouseEvent(input.MouseWheel).WithDeltaX(deltaX).WithDeltaY(deltaY).Do(exec)
    }))
}
```

- [ ] **Step 2: 实现 `ScrollMouseWheel()` 方法**

```go
// ScrollMouseWheel scrolls towards element using mouse wheel
func (c *Client) ScrollMouseWheel(selector, frameID string) error {
    center, err := c.GetElementCenter(selector, frameID)
    if err != nil {
        return err
    }

    var vpWidth, vpHeight float64
    js := `(function(){return JSON.stringify({width: window.innerWidth, height: window.innerHeight});})()`
    var vp map[string]float64
    err = c.EvalInFrame(frameID, js, &vp)
    if err != nil {
        return err
    }
    vpWidth = vp["width"]
    vpHeight = vp["height"]

    centerX := center["centerX"]
    centerY := center["centerY"]

    // 计算元素相对于 viewport 中心的方向
    dx := centerX - vpWidth/2
    dy := centerY - vpHeight/2

    // 滚动步长（带随机偏移模拟人手）
    stepX := 0.0
    stepY := 0.0
    if math.Abs(dx) > 10 {
        stepX = -math.Copysign(50+mathrand.Float64()*6, dx) // 50±3
    }
    if math.Abs(dy) > 10 {
        stepY = -math.Copysign(50+mathrand.Float64()*6, dy) // 50±3
    }

    return c.DispatchMouseScrollEvent(stepX, stepY)
}
```

- [ ] **Step 3: 运行 `go build -o cdp main.go` 验证编译**

- [ ] **Step 4: Commit**

```bash
git add internal/client.go
git commit -m "feat: 实现 ScrollMouseWheel

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 4: 实现 `RenderTrack()` 轨迹可视化

**Files:**
- Modify: `internal/client.go`
- Modify: `internal/js.go`

- [ ] **Step 1: 添加 `TrackRenderJS` 到 js.go**

```go
// TrackRenderJS renders track points from window.__trackPath variable
const TrackRenderJS = `
(function() {
    var points = window.__trackPath;
    if (!points || points.length < 2) return;
    var color = window.__trackColor || 'hsl(200, 70%, 50%)';

    var container = document.createElement('div');
    container.id = '__track-overlay';
    container.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;pointer-events:none;z-index:99999;overflow:hidden;';

    var svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.style.cssText = 'position:absolute;top:0;left:0;width:100%;height:100%;';

    for (var i = 1; i < points.length; i++) {
        var line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
        line.setAttribute('x1', points[i-1][0]);
        line.setAttribute('y1', points[i-1][1]);
        line.setAttribute('x2', points[i][0]);
        line.setAttribute('y2', points[i][1]);
        line.setAttribute('stroke', color);
        line.setAttribute('stroke-width', '1');
        line.setAttribute('stroke-opacity', '0.7');
        svg.appendChild(line);
    }

    container.appendChild(svg);

    var lastPoint = points[points.length - 1];
    var dot = document.createElement('div');
    dot.style.cssText = 'position:absolute;left:' + (lastPoint[0] - 3) + 'px;top:' + (lastPoint[1] - 3) + 'px;width:6px;height:6px;border-radius:50%;background:' + color + ';box-shadow:0 0 8px ' + color + ';transition:opacity 2s ease-out;';
    container.appendChild(dot);

    document.body.appendChild(container);

    setTimeout(function() {
        dot.style.opacity = '0';
        svg.style.opacity = '0';
    }, 500);

    setTimeout(function() {
        if (container.parentNode) {
            container.parentNode.removeChild(container);
        }
    }, 2500);
})();
`
```

- [ ] **Step 2: 实现 `RenderTrack()` 方法**

```go
// RenderTrack renders track points on main frame
func (c *Client) RenderTrack(points [][]float64, color string) error {
    pointsJSON, err := json.Marshal(points)
    if err != nil {
        return err
    }
    setJS := fmt.Sprintf(`window.__trackPath = %s; window.__trackColor = '%s';`, string(pointsJSON), color)
    err = c.EvalInFrame("", setJS, nil)
    if err != nil {
        return err
    }
    return c.EvalInFrame("", internal.TrackRenderJS, nil)
}
```

- [ ] **Step 3: 运行 `go build -o cdp main.go` 验证编译**

- [ ] **Step 4: Commit**

```bash
git add internal/js.go internal/client.go
git commit -m "feat: 实现轨迹可视化 RenderTrack

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 5: 最终验证

- [ ] **Step 1: 运行 `go build -o cdp main.go` 确保编译通过**

- [ ] **Step 2: 检查 spec 覆盖度**
  - [ ] 命令接口：`--selector` 可选 flag + 位置参数
  - [ ] frame-id：在 childFrames 中查找
  - [ ] Touch 滑动：上滑/下滑、贝塞尔曲线、`Input.dispatchTouchEvent`
  - [ ] MouseWheel：最多 20 次、随机偏移
  - [ ] scrollIntoView：frame-id 对应 iframe 中执行
  - [ ] track 模式：保留 points、随机颜色、DOM 绘制

- [ ] **Step 3: Commit any final changes**

---

## 自审检查

**1. Spec 覆盖度：** 全部 7 个 section 都有对应任务实现

**2. Placeholder 扫描：** 无 TBD/TODO，所有方法实现完整

**3. 类型一致性：** 方法名统一使用 `ScrollTouch`、`ScrollMouseWheel`、`RenderTrack`，签名清晰

---

Plan complete and saved to `docs/superpowers/plans/2026-05-05-scroll-implementation.md`.
