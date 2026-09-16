# snapshot frame 增强实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans.

**Goal:** `cdp snapshot` 支持 `--frame-id`、动态 iframe 监听、跨域 frame 降级读取，BDD 测试覆盖。

**Architecture:** `GetFrameContent` 降级链（isolatedWorld→axTree→domDocument）→ `GetFrameTreeWithEvents` 事件监听合并 → cmd 层加 `--frame-id` 分支 + `captureFrame` 抽取。BDD 测试用 ginkgo/gomega。

**Tech Stack:** Go 1.26.2, chromedp, cdproto, ginkgo v2, gomega

## Global Constraints

- 不修改 eval/click/form 的 frame 处理逻辑
- 不引入新的 CLI 子命令
- BDD 测试仅覆盖 snapshot frame 增强功能，现有 `_test.go` 不动
- 所有 JS 生成函数保持 `build*JS` 命名惯例

---

## 文件结构

| 文件 | 职责 | 操作 |
|------|------|------|
| `internal/client.go` | `GetFrameTreeWithEvents`、`GetFrameContent` | 修改 |
| `cmd/snapshot.go` | `--frame-id` flag、`captureFrame`、`runSnapshot` 分支 | 修改 |
| `cmd/snapshot_test.go` | `runSnapshot` 单元测试 | 新建 |
| `internal/snapshot_bdd_test.go` | BDD 集成测试（ginkgo） | 新建 |

### Task 1: 添加 ginkgo/gomega 依赖 + BDD 脚手架

**Files:**
- Modify: `go.mod`
- Create: `internal/snapshot_bdd_test.go`

**Interfaces:**
- Produces: ginkgo v2 + gomega 可用，BDD suite 骨架就绪

- [ ] **Step 1: 添加依赖**

```bash
cd /company/cdpcli
export PATH=$PATH:/usr/local/go/bin
export HTTP_PROXY=http://192.168.1.50:7890
export HTTPS_PROXY=http://192.168.1.50:7890
go get github.com/onsi/ginkgo/v2
go get github.com/onsi/gomega
go mod tidy
```

- [ ] **Step 2: 写 BDD 脚手架**

创建 `internal/snapshot_bdd_test.go`：

```go
package internal

import (
    "testing"

    . "github.com/onsi/ginkgo/v2"
    . "github.com/onsi/gomega"
)

func TestSnapshotBDD(t *testing.T) {
    RegisterFailHandler(Fail)
    RunSpecs(t, "Snapshot Frame Enhancement Suite")
}

var _ = Describe("snapshot frame enhancement", func() {
    // 具体 It() 在后续 Task 中实现
})
```

- [ ] **Step 3: 运行验证**

```bash
go test ./internal/ -run TestSnapshotBDD -v -count=1
```
期望：PASS（0 个 Spec，suite 通过）

- [ ] **Step 4: 提交**

```bash
git add go.mod go.sum internal/snapshot_bdd_test.go
git commit -m "chore: add ginkgo/gomega BDD scaffolding for snapshot tests"
```

---

### Task 2: `GetFrameContent` — 跨域 frame 降级读取链

**Files:**
- Modify: `internal/client.go`（在 `RunIsolatedWorld` 之后）
- Modify: `internal/snapshot_bdd_test.go`

**Interfaces:**
- Produces: `func (c *Client) GetFrameContent(frameID cdp.FrameID) (body interface{}, method string, err error)`
  - `method` 取值：`"isolatedWorld"` | `"axTree"` | `"domDocument"`
- Consumes: 现有 `RunIsolatedWorld`

- [ ] **Step 1: 写 BDD 测试**

在 `internal/snapshot_bdd_test.go` 的 `Describe` 块中追加：

```go
var _ = Describe("snapshot frame enhancement", func() {
    var client *Client
    var host string
    var port int

    BeforeEach(func() {
        host = os.Getenv("CDP_HOST")
        if host == "" { host = "127.0.0.1" }
        port = 9222
    })

    Describe("GetFrameContent", func() {
        Context("main frame (no CSP)", func() {
            It("reads content via isolatedWorld", func() {
                pages, err := ListPageTargets(host, port, false)
                if err != nil || len(pages) == 0 { Skip("Chrome not available") }

                client, err = NewClient(host, port)
                Expect(err).NotTo(HaveOccurred())
                defer client.Disconnect()

                client.Navigate("http://localhost:8080/mui-datepicker", "")
                ft, err := client.GetFrameTree()
                Expect(err).NotTo(HaveOccurred())

                body, method, err := client.GetFrameContent(ft.Frame.ID)
                Expect(err).NotTo(HaveOccurred())
                Expect(method).To(Equal("isolatedWorld"))
                Expect(body).NotTo(BeNil())
            })
        })
    })
```

- [ ] **Step 2: 运行测试验证失败**

```bash
go test ./internal/ -run TestSnapshotBDD -v -count=1
```
期望：FAIL — `client.GetFrameContent` 未定义。

- [ ] **Step 3: 实现 `GetFrameContent`**

在 `internal/client.go` 的 `RunIsolatedWorld` 之后追加：

```go
// GetFrameContent reads frame content with a fallback chain:
// isolatedWorld → Accessibility.getFullAXTree → DOM.getDocument.
// Returns body, method used, and error if all methods fail.
func (c *Client) GetFrameContent(frameID cdp.FrameID) (interface{}, string, error) {
	// Method 1: isolated world (existing, fastest, full DOM+JS)
	body, err := c.RunIsolatedWorld(frameID, SnapshotJS)
	if err == nil {
		return body, "isolatedWorld", nil
	}

	// Method 2: Accessibility tree (works through CSP, has text)
	var axNodes []*page.AXNode
	err = chromedp.Run(c.ctx, chromedp.ActionFunc(func(ctx context.Context) error {
		cc := chromedp.FromContext(ctx)
		if cc == nil || cc.Target == nil {
			return fmt.Errorf("invalid context")
		}
		var getErr error
		axNodes, getErr = page.GetFullAXTree().Do(cdp.WithExecutor(ctx, cc.Target))
		return getErr
	}))
	if err == nil && len(axNodes) > 0 {
		return axNodes, "axTree", nil
	}

	// Method 3: DOM snapshot (minimal, works almost always)
	var domNode *page.Node
	err = chromedp.Run(c.ctx, chromedp.ActionFunc(func(ctx context.Context) error {
		cc := chromedp.FromContext(ctx)
		if cc == nil || cc.Target == nil {
			return fmt.Errorf("invalid context")
		}
		var getErr error
		domNode, getErr = page.GetDocument().Do(cdp.WithExecutor(ctx, cc.Target))
		return getErr
	}))
	if err == nil && domNode != nil {
		return domNode, "domDocument", nil
	}

	return nil, "", fmt.Errorf("all content methods failed: isolatedWorld, axTree, domDocument")
}
```

- [ ] **Step 4: 运行测试验证通过**

```bash
go test ./internal/ -run TestSnapshotBDD -v -count=1
```
期望：PASS（GetFrameContent spec 通过）

- [ ] **Step 5: 提交**

```bash
git add internal/client.go internal/snapshot_bdd_test.go
git commit -m "feat: add GetFrameContent with isolatedWorld→axTree→domDocument fallback"
```

---

### Task 3: `GetFrameTreeWithEvents` — 动态 iframe 监听

**Files:**
- Modify: `internal/client.go`（在 `GetFrameTree` 之后）
- Modify: `internal/snapshot_bdd_test.go`

**Interfaces:**
- Produces: `func (c *Client) GetFrameTreeWithEvents(wait time.Duration) (*page.FrameTree, error)`
- Consumes: 现有 `page.GetFrameTree`、`chromedp.ListenTarget`

- [ ] **Step 1: 写 BDD 测试**

在 `internal/snapshot_bdd_test.go` 的 `Describe("GetFrameContent", ...)` 之后追加：

```go
	Describe("GetFrameTreeWithEvents", func() {
		It("captures dynamically added iframes", func() {
			pages, err := ListPageTargets(host, port, false)
			if err != nil || len(pages) == 0 { Skip("Chrome not available") }

			client, err = NewClient(host, port)
			Expect(err).NotTo(HaveOccurred())
			defer client.Disconnect()

			// Inject a delayed iframe via JS
			client.EvalInFrame("", `(function(){
				var iframe = document.createElement('iframe');
				iframe.id = 'dyn-frame';
				iframe.src = 'about:blank';
				iframe.style.cssText = 'width:100px;height:100px;';
				document.body.appendChild(iframe);
				return 'added';
			})()`, nil)

			ft, err := client.GetFrameTreeWithEvents(3 * time.Second)
			Expect(err).NotTo(HaveOccurred())

			// The dynamic iframe should appear in child frames
			found := false
			for _, child := range ft.ChildFrames {
				if child != nil && child.Frame.Name == "dyn-frame" {
					found = true
					break
				}
			}
			Expect(found).To(BeTrue(), "dynamic iframe should be captured")
		})
	})
```

- [ ] **Step 2: 运行测试验证失败**

```bash
go test ./internal/ -run TestSnapshotBDD -v -count=1
```
期望：FAIL — `client.GetFrameTreeWithEvents` 未定义。

- [ ] **Step 3: 实现 `GetFrameTreeWithEvents`**

在 `internal/client.go` 的 `GetFrameTree` 之后追加：

```go
// GetFrameTreeWithEvents returns the frame tree, including frames that load
// dynamically after the initial page load. Listens for Page.frameAttached
// events for the specified duration, then merges them into the tree.
func (c *Client) GetFrameTreeWithEvents(wait time.Duration) (*page.FrameTree, error) {
	ft, err := c.GetFrameTree()
	if err != nil {
		return nil, err
	}

	// Collect dynamically attached frames
	type attachedFrame struct {
		parentFrameID cdp.FrameID
		frame         *page.Frame
	}
	var attached []attachedFrame
	var mu sync.Mutex

	listenCtx, listenCancel := context.WithCancel(c.ctx)
	chromedp.ListenTarget(listenCtx, func(ev interface{}) {
		if e, ok := ev.(*page.EventFrameAttached); ok {
			mu.Lock()
			attached = append(attached, attachedFrame{
				parentFrameID: e.ParentFrameID,
				frame:         &page.Frame{ID: e.FrameID},
			})
			mu.Unlock()
		}
	})

	time.Sleep(wait)
	listenCancel()

	// Merge attached frames into the tree
	// Each attached frame is added as a child of its parent frame
	for _, af := range attached {
		ft = mergeFrameIntoTree(ft, af.parentFrameID, af.frame)
	}

	return ft, nil
}

// mergeFrameIntoTree recursively searches for parentFrameID in the tree
// and appends the new frame as a child. Returns the modified tree.
func mergeFrameIntoTree(ft *page.FrameTree, parentID cdp.FrameID, newFrame *page.Frame) *page.FrameTree {
	if ft == nil {
		return &page.FrameTree{Frame: newFrame}
	}
	if ft.Frame.ID == parentID {
		ft.ChildFrames = append(ft.ChildFrames, &page.FrameTree{Frame: newFrame})
		return ft
	}
	for i, child := range ft.ChildFrames {
		if child != nil {
			ft.ChildFrames[i] = mergeFrameIntoTree(child, parentID, newFrame)
		}
	}
	return ft
}
```

需要在 import 中加 `"sync"`。

- [ ] **Step 4: 运行测试验证通过**

```bash
go test ./internal/ -run TestSnapshotBDD -v -count=1
```
期望：PASS。

- [ ] **Step 5: 提交**

```bash
git add internal/client.go internal/snapshot_bdd_test.go
git commit -m "feat: add GetFrameTreeWithEvents for dynamic iframe capture"
```

---

### Task 4: `--frame-id` flag + `captureFrame` + snapshot BDD

**Files:**
- Modify: `cmd/snapshot.go`
- Modify: `internal/snapshot_bdd_test.go`

**Interfaces:**
- Consumes: `GetFrameTreeWithEvents` (Task 3)
- Produces: `--frame-id` flag、`captureFrame` 函数

- [ ] **Step 1: 写 BDD 测试**

在 `internal/snapshot_bdd_test.go` 的 `Describe` 块中追加：

```go
	Describe("snapshot with --frame-id", func() {
		var testHost string
		var testPort int

		BeforeEach(func() {
			testHost = os.Getenv("CDP_HOST")
			if testHost == "" { testHost = "127.0.0.1" }
			testPort = 9222
		})

		It("returns single frame without tree wrapper", func() {
			pages, err := ListPageTargets(testHost, testPort, false)
			if err != nil || len(pages) == 0 { Skip("Chrome not available") }

			client, err := NewClient(testHost, testPort)
			Expect(err).NotTo(HaveOccurred())
			defer client.Disconnect()

			client.Navigate("http://localhost:8080/mui-datepicker", "")
			ft, err := client.GetFrameTree()
			Expect(err).NotTo(HaveOccurred())

			// captureFrame on the main frame
			snap, method, err := captureFrame(client, ft.Frame)
			Expect(err).NotTo(HaveOccurred())
			Expect(snap.FrameID).To(Equal(string(ft.Frame.ID)))
			Expect(method).NotTo(BeEmpty())
			Expect(snap.Title).NotTo(BeEmpty())
		})

		It("reports method field for content source", func() {
			pages, err := ListPageTargets(testHost, testPort, false)
			if err != nil || len(pages) == 0 { Skip("Chrome not available") }

			client, err := NewClient(testHost, testPort)
			Expect(err).NotTo(HaveOccurred())
			defer client.Disconnect()

			client.Navigate("http://localhost:8080/mui-datepicker", "")
			ft, err := client.GetFrameTree()
			Expect(err).NotTo(HaveOccurred())

			snap, method, _ := captureFrame(client, ft.Frame)
			Expect(method).To(Equal("isolatedWorld"))
			Expect(snap.Method).To(Equal("isolatedWorld"))
			Expect(snap.Error).To(BeEmpty())
		})
	})
```

- [ ] **Step 2: 运行测试验证失败**

```bash
go test ./internal/ -run TestSnapshotBDD -v -count=1
```
期望：FAIL — `captureFrame` 未定义，或 `FrameSnapshot.Method` 字段不存在。

- [ ] **Step 3: 实现 `Method` 字段 + `captureFrame`**

修改 `cmd/snapshot.go`：

3a. `FrameSnapshot` 加 `Method` 字段：

```go
type FrameSnapshot struct {
    FrameID  string          `json:"frameId"`
    ParentID string          `json:"parentId,omitempty"`
    Name     string          `json:"name,omitempty"`
    URL      string          `json:"url"`
    Title    string          `json:"title,omitempty"`
    Body     json.RawMessage `json:"body,omitempty"`
    Method   string          `json:"method,omitempty"`
    Error    string          `json:"error,omitempty"`
}
```

3b. 抽出 `captureFrame` 函数：

```go
func captureFrame(client *internal.Client, frame *page.Frame) (FrameSnapshot, string, error) {
    body, method, bodyErr := client.GetFrameContent(frame.ID)

    var title string
    client.EvalInFrame(string(frame.ID), "document.title", &title)

    fs := FrameSnapshot{
        FrameID:  string(frame.ID),
        ParentID: string(frame.ParentID),
        Name:     frame.Name,
        URL:      frame.URL,
        Title:    title,
        Method:   method,
    }

    if bodyErr != nil {
        fs.Error = bodyErr.Error()
    } else {
        bodyBytes, _ := json.Marshal(body)
        fs.Body = bodyBytes
    }

    return fs, method, bodyErr
}
```

3c. 修改 `runSnapshot` 支持 `--frame-id`：

```go
func init() {
    rootCmd.AddCommand(snapshotCmd)
    snapshotCmd.Flags().String("frame-id", "", "Target a single frame by ID")
}

func runSnapshot(cmd *cobra.Command, args []string) error {
    client, err := internal.NewClient(GetHost(), GetPort())
    if err != nil {
        return fmt.Errorf("failed to create client: %w", err)
    }
    defer client.Disconnect()

    frameID, _ := cmd.Flags().GetString("frame-id")

    if frameID != "" {
        // Single frame mode
        frameTree, err := client.GetFrameTreeWithEvents(3 * time.Second)
        if err != nil {
            return fmt.Errorf("failed to get frame tree: %w", err)
        }
        targetFrame := findFrameByID(frameTree, frameID)
        if targetFrame == nil {
            return fmt.Errorf("frame %s not found", frameID)
        }
        snap, _, err := captureFrame(client, targetFrame)
        if err != nil {
            return err
        }
        enc := json.NewEncoder(os.Stdout)
        return enc.Encode(snap)
    }

    // Full tree mode (existing behavior)
    frameTree, err := client.GetFrameTreeWithEvents(3 * time.Second)
    if err != nil {
        return fmt.Errorf("failed to get frame tree: %w", err)
    }

    result := buildFrameTreeOutput(client, frameTree)
    enc := json.NewEncoder(os.Stdout)
    return enc.Encode(result)
}

func findFrameByID(ft *page.FrameTree, targetID string) *page.Frame {
    if ft == nil {
        return nil
    }
    if string(ft.Frame.ID) == targetID {
        return ft.Frame
    }
    for _, child := range ft.ChildFrames {
        if child != nil {
            if found := findFrameByID(child, targetID); found != nil {
                return found
            }
        }
    }
    return nil
}
```

3d. 简化 `buildFrameTreeOutput`，改为调 `captureFrame`：

```go
func buildFrameTreeOutput(client *internal.Client, ft *page.FrameTree) FrameTreeOutput {
    snap, _, _ := captureFrame(client, ft.Frame)

    output := FrameTreeOutput{
        Frame: snap,
    }

    if len(ft.ChildFrames) > 0 {
        output.ChildFrames = make([]FrameTreeOutput, 0, len(ft.ChildFrames))
        for _, child := range ft.ChildFrames {
            if child == nil {
                continue
            }
            output.ChildFrames = append(output.ChildFrames, buildFrameTreeOutput(client, child))
        }
    }

    return output
}
```

- [ ] **Step 4: 运行测试验证通过**

```bash
go test ./internal/ -run TestSnapshotBDD -v -count=1
go test ./... -count=1
```
期望：全部 PASS。

- [ ] **Step 5: 构建并端到端验证**

```bash
go build -ldflags="-s -w" -o cdp main.go
# Test: full tree
./cdp --port 9223 snapshot | python3 -c "import sys,json; d=json.load(sys.stdin); print('frames:', 1+len(d.get('childFrames',[])))"
# Test: single frame
./cdp --port 9223 navi http://localhost:8080/mui-datepicker > /dev/null
FRAME_ID=$(./cdp --port 9223 snapshot | python3 -c "import sys,json; print(json.load(sys.stdin)['frame']['frameId'])")
./cdp --port 9223 snapshot --frame-id "$FRAME_ID" | python3 -c "import sys,json; d=json.load(sys.stdin); print('method:',d['method'],'title:',d['title'])"
```
期望：
- 全树模式正常输出 frame 数量
- 单 frame 模式输出 `method: isolatedWorld`

- [ ] **Step 6: 提交**

```bash
git add cmd/snapshot.go internal/snapshot_bdd_test.go
git commit -m "feat: add --frame-id flag, captureFrame, and GetFrameContent fallback to snapshot

- snapshot --frame-id <id>: outputs single FrameSnapshot, no tree wrapper
- GetFrameTreeWithEvents: captures dynamically loaded iframes via Page.frameAttached
- GetFrameContent: isolatedWorld -> Accessibility.getFullAXTree -> DOM.getDocument
- FrameSnapshot.Method field records content source
- captureFrame extracts single-frame logic, reused by full tree and --frame-id"
```
