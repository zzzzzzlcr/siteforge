package internal

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	mathrand "math/rand"
	"net"
	"os"
	"strconv"
	"strings"
	"sync/atomic"
	"time"

	"github.com/chromedp/cdproto/accessibility"
	"github.com/chromedp/cdproto/cdp"
	"github.com/chromedp/cdproto/dom"
	"github.com/chromedp/cdproto/input"
	"github.com/chromedp/cdproto/page"
	"github.com/chromedp/cdproto/runtime"
	"github.com/chromedp/cdproto/target"
	"github.com/chromedp/chromedp"
	"github.com/gobwas/ws"
	"github.com/gobwas/ws/wsutil"
)

type Client struct {
	ctx         context.Context
	cancel      context.CancelFunc
	allocCancel context.CancelFunc
	// cached frame execution contexts
	frameCtxs map[cdp.FrameID]runtime.ExecutionContextID
	// browser WebSocket URL for direct CDP access (iframe AX trees)
	browserWSURL string
	// targetDiags 是「这次连接挑中的页面是猜的」那条诊断（NewClient 按
	// ChooseActivePageTarget 的 Fallback 决定一次，见 targetDiagsFor）。
	//
	// ⚠️ 发它的**位置**是刻意的：Observe（单帧）与 ObserveAll（整页）各发**一次**。
	// ObserveAll 是逐帧调 observeFrame 的，若把这条塞进逐帧那条路，N 帧就是 N 条 ——
	// 而这件事跟帧数无关（错的是整个 tab）。所以逐帧的 observeFrame 不发它，
	// 由两个**入口**各自补一条。
	targetDiags []Diagnostic
	// landingDiags 是落点判据（G1）发过的诊断：判据**没能跑成**的那些
	// （跨站子帧盲区 / 取不到证）—— 见 LandingDiags 与 dispatchMouseClick。
	//
	// ⚠️ 为什么攒一份而不是只走返回值：`form` 那条路（FillText / CheckElement /
	// SelectOption）不返回结构化回执，但它的点击**同样会扣下抬起**。
	// 复审实测：`cdp form` 上「不静默」是 **0 字节** —— 与 targetDiags 同一套办法补上。
	landingDiags []Diagnostic
}

// FrameSnapshot captures the full state of a single frame.
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

// CaptureFrame reads the content and metadata for a single frame.
// For the main frame, also collects AX tree text from OOPIF iframes
// using a direct WebSocket connection (like Puppeteer's includeIframes).
func (c *Client) CaptureFrame(frame *cdp.Frame) (FrameSnapshot, string, error) {
	body, method, bodyErr := c.GetFrameContent(frame.ID)

	var title string
	c.EvalInFrame(string(frame.ID), "document.title", &title)

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
	} else if frame.ParentID == "" {
		// Main frame: also collect AX tree from OOPIF iframes via raw CDP
		axTexts := c.collectIframeAXTrees()
		if len(axTexts) > 0 {
			wrapped := map[string]interface{}{
				"dom":    body,
				"axText": axTexts,
			}
			bodyBytes, _ := json.Marshal(wrapped)
			fs.Body = bodyBytes
		} else {
			bodyBytes, _ := json.Marshal(body)
			fs.Body = bodyBytes
		}
	} else {
		bodyBytes, _ := json.Marshal(body)
		fs.Body = bodyBytes
	}

	return fs, method, bodyErr
}

func NewClient(host string, port int) (*Client, error) {
	wsURL, err := getWSDebugURL(host, port)
	if err != nil {
		return nil, err
	}

	// Find the active page target —— 拿的是**完整**的选择结果，不是裸 ID：
	// 「一个报 visible 的都没有、这是退回第一个猜的」这件事必须能传到模型上
	// （见 targetDiagsFor / DiagKindTargetAmbiguous）。
	choice, err := ChooseActivePageTarget(host, port)
	if err != nil {
		return nil, err
	}

	allocCtx2, allocCancel2 := chromedp.NewRemoteAllocator(context.Background(), wsURL)
	ctx2, cancel2 := chromedp.NewContext(allocCtx2, chromedp.WithTargetID(target.ID(choice.ID)))

	return &Client{
		ctx:          ctx2,
		cancel:       cancel2,
		allocCancel:  allocCancel2,
		frameCtxs:    make(map[cdp.FrameID]runtime.ExecutionContextID),
		browserWSURL: wsURL,
		targetDiags:  targetDiagsFor(choice),
	}, nil
}

// -- Raw CDP over WebSocket (gobwas/ws) for OOPIF iframe AX trees --

var cdpCmdID atomic.Int64

// cdpCmd is a minimal CDP command for gobwas/ws.
type cdpCmd struct {
	ID        int64           `json:"id"`
	Method    string          `json:"method"`
	Params    json.RawMessage `json:"params,omitempty"`
	SessionID string          `json:"sessionId,omitempty"`
}

// collectIframeAXTrees connects to the browser WebSocket and gets AX tree
// text from all OOPIF iframe targets using session-based CDP commands.
// This is equivalent to Puppeteer's includeIframes approach.
func (c *Client) collectIframeAXTrees() []string {
	// Use a short timeout to avoid blocking if browser is unreachable
	dialCtx, dialCancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer dialCancel()
	conn, _, _, err := ws.Dial(dialCtx, c.browserWSURL)
	if err != nil {
		return nil
	}
	defer conn.Close()

	// Step 1: get all targets
	result, err := c.cdpSend(conn, "Target.getTargets", "", "")
	if err != nil {
		return nil
	}
	var targetsResp struct {
		TargetInfos []struct {
			TargetID string `json:"targetId"`
			Type     string `json:"type"`
			URL      string `json:"url"`
		} `json:"targetInfos"`
	}
	if err := json.Unmarshal(result, &targetsResp); err != nil {
		return nil
	}

	// Step 2: for each iframe, attach and get AX tree
	var allTexts []string
	for _, ti := range targetsResp.TargetInfos {
		if ti.Type != "iframe" || ti.URL == "about:blank" || ti.URL == "" {
			continue
		}
		// Attach to iframe target
		attachParams := fmt.Sprintf(`{"targetId":"%s","flatten":true}`, ti.TargetID)
		attachResult, err := c.cdpSend(conn, "Target.attachToTarget", attachParams, "")
		if err != nil {
			continue
		}
		var attachResp struct {
			SessionID string `json:"sessionId"`
		}
		if err := json.Unmarshal(attachResult, &attachResp); err != nil || attachResp.SessionID == "" {
			continue
		}
		sessionID := attachResp.SessionID

		// Get AX tree through the session
		axResult, err := c.cdpSend(conn, "Accessibility.getFullAXTree", `{"depth":-1}`, sessionID)
		if err != nil {
			continue
		}
		var axResp struct {
			Nodes []struct {
				Name *struct {
					Value string `json:"value"`
				} `json:"name,omitempty"`
			} `json:"nodes"`
		}
		if err := json.Unmarshal(axResult, &axResp); err != nil {
			continue
		}
		for _, n := range axResp.Nodes {
			if n.Name != nil && n.Name.Value != "" {
				allTexts = append(allTexts, n.Name.Value)
			}
		}
	}
	return allTexts
}

func (c *Client) Close() {
	if c.allocCancel != nil {
		c.allocCancel()
	}
	if c.cancel != nil {
		c.cancel()
	}
}

// Disconnect gracefully tears down the WebSocket connection without closing
// the Chrome page. It clears TargetID before canceling the chromedp context,
// preventing Target.closeTarget from being sent.
func (c *Client) Disconnect() {
	// Clear TargetID so cancel doesn't trigger Target.closeTarget (closes the page).
	// This mirrors the pattern used in checkPageActive (targets.go).
	if c.cancel != nil {
		if c.ctx != nil {
			if chromedpCtx := chromedp.FromContext(c.ctx); chromedpCtx != nil && chromedpCtx.Target != nil {
				chromedpCtx.Target.TargetID = ""
			}
		}
		c.cancel()
	}
	if c.allocCancel != nil {
		c.allocCancel()
	}
}

// GetFrameTree returns the complete frame tree from the current page.
func (c *Client) GetFrameTree() (*page.FrameTree, error) {
	var frameTree *page.FrameTree
	err := chromedp.Run(c.ctx, chromedp.ActionFunc(func(ctx context.Context) error {
		var err error
		frameTree, err = page.GetFrameTree().Do(ctx)
		return err
	}))
	if err != nil {
		return nil, fmt.Errorf("failed to get frame tree: %w", err)
	}
	return frameTree, nil
}

// GetFrameTreeWithEvents returns the frame tree including iframes discovered
// via DOM inspection (dom.GetDocument with Pierce=true). This catches
// dynamically injected iframes and nested iframes that page.GetFrameTree
// may not report — common with cross-origin content like Stripe.
func (c *Client) GetFrameTreeWithEvents(wait time.Duration) (*page.FrameTree, error) {
	ft, err := c.GetFrameTree()
	if err != nil {
		return nil, err
	}

	time.Sleep(wait)

	// Get full DOM tree with Pierce=true to walk into iframe content documents.
	// This discovers nested iframes (e.g. Stripe's embedded checkout inside
	// a parent iframe) that page.GetFrameTree misses.
	var doc *cdp.Node
	err = chromedp.Run(c.ctx, chromedp.ActionFunc(func(ctx context.Context) error {
		cc := chromedp.FromContext(ctx)
		if cc == nil || cc.Target == nil {
			return fmt.Errorf("invalid context")
		}
		var domErr error
		doc, domErr = dom.GetDocument().WithDepth(-1).WithPierce(true).Do(cdp.WithExecutor(ctx, cc.Target))
		return domErr
	}))
	if err != nil {
		return ft, nil // DOM tree unavailable, return baseline tree
	}

	// Collect frames with proper parent-child hierarchy from the DOM
	domFrames := collectFramesFromDOM(doc, ft.Frame.ID)
	if len(domFrames) == 0 {
		return ft, nil
	}

	// Merge DOM-discovered frames not already in the baseline tree
	existing := collectExistingFrameIDs(ft)
	for _, df := range domFrames {
		if existing[string(df.frame.ID)] {
			continue
		}
		ft = mergeFrameIntoTree(ft, df.parentID, df.frame)
	}

	return ft, nil
}

// collectExistingFrameIDs builds a set of all frame IDs in the given tree.
func collectExistingFrameIDs(ft *page.FrameTree) map[string]bool {
	ids := make(map[string]bool)
	if ft == nil {
		return ids
	}
	ids[string(ft.Frame.ID)] = true
	for _, child := range ft.ChildFrames {
		for k, v := range collectExistingFrameIDs(child) {
			ids[k] = v
		}
	}
	return ids
}

// nodeGetAttr extracts an attribute value from a flat [key1,val1,key2,val2] slice.
func nodeGetAttr(attrs []string, name string) string {
	for i := 0; i+1 < len(attrs); i += 2 {
		if attrs[i] == name {
			return attrs[i+1]
		}
	}
	return ""
}

// domFrame is a frame discovered in the DOM with its parent frameId.
type domFrame struct {
	parentID cdp.FrameID
	frame    *cdp.Frame
}

// collectFramesFromDOM walks a DOM node tree (with Pierce=true) and collects
// all IFRAME elements with their frameId and parent-frame relationship.
// Recurses into ContentDocument to find nested iframes (e.g. Stripe's embedded
// checkout inside a parent Stripe iframe).
func collectFramesFromDOM(node *cdp.Node, ownerFrameID cdp.FrameID) []domFrame {
	var frames []domFrame
	if node == nil {
		return frames
	}
	if node.NodeName == "IFRAME" && node.FrameID != "" {
		frames = append(frames, domFrame{
			parentID: ownerFrameID,
			frame: &cdp.Frame{
				ID:   node.FrameID,
				URL:  nodeGetAttr(node.Attributes, "src"),
				Name: nodeGetAttr(node.Attributes, "name"),
			},
		})
		// Walk into the iframe's content document to find nested iframes
		if node.ContentDocument != nil {
			frames = append(frames, collectFramesFromDOM(node.ContentDocument, node.FrameID)...)
		}
	}
	for _, child := range node.Children {
		frames = append(frames, collectFramesFromDOM(child, ownerFrameID)...)
	}
	for _, shadow := range node.ShadowRoots {
		frames = append(frames, collectFramesFromDOM(shadow, ownerFrameID)...)
	}
	if node.TemplateContent != nil {
		frames = append(frames, collectFramesFromDOM(node.TemplateContent, ownerFrameID)...)
	}
	return frames
}

// mergeFrameIntoTree recursively searches for parentFrameID in the tree
// and appends the new frame as a child. Returns the modified tree.
func mergeFrameIntoTree(ft *page.FrameTree, parentID cdp.FrameID, newFrame *cdp.Frame) *page.FrameTree {
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

// : 导航之后最多等多久（等 load 事件的预算）。
// : ⚠️ 到点**不等于失败** —— 见 `Navigate` 里那段说明。
// : 这个数**必须小于产物那边的 `subprocess` 超时（60 秒）**，否则产物会先把它掐掉，
// : 而那种掐法产出的是一句「没能让 cdp 重新看这一页」，把真因盖住。
// :
// : 45 → 15（2026-09-18 真站量出来的）：`cdp navi` 那一步实耗 **61 秒** ≈ 45（等满）
// : + 5（取树上限）+ 开销 ⇒ **gowizard 那一页的 load 事件 45 秒都没来**
// : （它有广告/埋点，`load` 要等全部子资源）。而**等到等不到都已经不改判**了，
// : 所以这 45 秒是**纯等**。产物那边自己还有 `_wait_ready`（10 秒）看 `readyState`。
// : 15 秒够接住「本来就快」的那些页，又不会在「永远不来」的页上白耗。
const naviWaitTotal = 15 * time.Second

// : 取 frame tree 的截止时间。**这个数必须有** ——
// : `c.ctx` 是 `context.Background()` 下来的（见 `NewClient`），
// : **它没有截止时间**：浏览器不回，任何一次 CDP 往返都会**永远等下去**。
// :
// : 2026-09-18 实测（gowizard 真站）：`Navigate` 在「没等到 load 事件」这条路上
// : 第一次调到 `GetFrameTree`，整条 `cdp navi` 就**挂住不返回**。
// : 原先够不着是因为老代码在那条路上直接 `return err`、**根本不调它**。
// : ⇒ 挂住的不是 `GetFrameTree` 这个名字，是「这个客户端没有一处 CDP 往返有上限」。
const naviTreeTimeout = 5 * time.Second

// getFrameTreeBounded 与 `GetFrameTree` 同，但**自带截止时间**。
// 超时返回错误 —— 调用方**不许**把它读成「导航失败」（见 `Navigate`）。
func (c *Client) getFrameTreeBounded(limit time.Duration) (*page.FrameTree, error) {
	ctx, cancel := context.WithTimeout(c.ctx, limit)
	defer cancel()
	var frameTree *page.FrameTree
	err := chromedp.Run(ctx, chromedp.ActionFunc(func(ctx context.Context) error {
		var err error
		frameTree, err = page.GetFrameTree().Do(ctx)
		return err
	}))
	if err != nil {
		return nil, err
	}
	return frameTree, nil
}

// Navigate navigates the current page (or a specific frame) to the given URL,
// waits for the load event, then returns the updated frame tree.
//
// ⚠️ **「导航发出去」与「页面加载完」是两件事，判据只能是前一件**
// （2026-09-18 真站实测：`nav.Do` 成功了、紧接着 `cdp eval 'location.href'`
// 回的正是那个网址，可这条命令报 `timeout waiting for page load (30s)`、
// 退出码 1 —— 产物把它读成「打不开 <url>」，而页面明明打开了）。
func (c *Client) Navigate(url, frameID string) (*page.FrameTree, error) {
	// Listen for LoadEventFired before starting navigation
	loadCh := make(chan struct{}, 1)
	listenCtx, listenCancel := context.WithCancel(c.ctx)
	chromedp.ListenTarget(listenCtx, func(ev interface{}) {
		if _, ok := ev.(*page.EventLoadEventFired); ok {
			select {
			case loadCh <- struct{}{}:
			default:
			}
		}
	})
	defer listenCancel()

	// 「load 事件到没到」只用来决定**说不说那句话**，不用来决定成没成（见下）
	waited := true
	err := chromedp.Run(c.ctx, chromedp.ActionFunc(func(ctx context.Context) error {
		nav := page.Navigate(url)
		if frameID != "" {
			nav = nav.WithFrameID(cdp.FrameID(frameID))
		}
		_, _, errorText, _, err := nav.Do(ctx)
		if err != nil {
			return err
		}
		if errorText != "" {
			return fmt.Errorf("navigate error: %s", errorText)
		}
		// ⚠️ **等 load 必须【在 Run 里面】等** —— `chromedp.ListenTarget` 的回调
		// 只有 `chromedp.Run` 正在跑的时候才会被派发。原码把 `select` 放在 `Run` **外面**：
		// `Run` 发完导航就退出，**事件随后到了也没人接**，于是硬等满 30 秒报超时。
		// （2026-09-18 真站实测：`dcl≈12.8s`、`load≈20.3s` —— 事件**确实到了**，
		// 只是没人接。所以这不是「页面慢」，是「我们自己没在听」。）
		select {
		case <-loadCh:
		case <-time.After(naviWaitTotal):
			waited = false
		}
		return nil
	}))
	if err != nil {
		return nil, fmt.Errorf("failed to navigate to %s: %w", url, err)
	}

	// ── 从这里往下**只加菜，不改判**：导航成不成，上面 `nav.Do` 已经定了 ──
	if !waited {
		fmt.Fprintf(os.Stderr, "cdp navi: 没等到 load 事件（等了 %s）—— "+
			"「没等到」不等于「没打开」，页面在不在由调用方自己看\n", naviWaitTotal)
	}
	ft, terr := c.getFrameTreeBounded(naviTreeTimeout)
	if terr != nil {
		// ⚠️ **取不到树不是导航失败** —— 这正是那条假失败的形状：
		// 页面明明打开了，可整条命令挂住 / 报错，被读成「打不开」。
		fmt.Fprintf(os.Stderr, "cdp navi: 导航已发出（%s），但没能在 %s 内取到 frame tree：%v "+
			"—— 这**不是**导航失败\n", url, naviTreeTimeout, terr)
		return nil, nil
	}
	return ft, nil
}

// GetFrameOrCreateContext gets or creates a cached execution context for the given frame.
func (c *Client) GetFrameOrCreateContext(ctx context.Context, frameID cdp.FrameID) (runtime.ExecutionContextID, error) {
	if cached, ok := c.frameCtxs[frameID]; ok {
		return cached, nil
	}

	worldName := fmt.Sprintf("click-world-%s", frameID)
	var execCtxID runtime.ExecutionContextID
	err := chromedp.Run(c.ctx, chromedp.ActionFunc(func(ctx context.Context) error {
		c := chromedp.FromContext(ctx)
		if c == nil || c.Target == nil {
			return fmt.Errorf("invalid context or target")
		}

		execCtxIDPtr, err := page.CreateIsolatedWorld(frameID).WithWorldName(worldName).Do(cdp.WithExecutor(ctx, c.Target))
		if err != nil {
			return err
		}
		execCtxID = execCtxIDPtr
		return nil
	}))
	if err != nil {
		return 0, fmt.Errorf("failed to create isolated world for frame %s: %w", frameID, err)
	}

	c.frameCtxs[frameID] = execCtxID
	return execCtxID, nil
}

// EvalInFrame executes JavaScript in the specified frame (or main frame if frameID is empty).
// Returns the result unmarshaled into the provided value.
// Falls back to OOPIF (Target.attachToTarget + Runtime.evaluate) for cross-origin iframes.
func (c *Client) EvalInFrame(frameID string, js string, result interface{}) error {
	err := chromedp.Run(c.ctx, chromedp.ActionFunc(func(ctx context.Context) error {
		cc := chromedp.FromContext(ctx)
		if cc == nil || cc.Target == nil {
			return fmt.Errorf("invalid chromedp context")
		}

		exec := cc.Target
		var execCtxID runtime.ExecutionContextID

		if frameID != "" {
			var err error
			execCtxID, err = c.GetFrameOrCreateContext(ctx, cdp.FrameID(frameID))
			if err != nil {
				return fmt.Errorf("failed to get frame context: %w", err)
			}
		}

		action := runtime.Evaluate(js)
		if frameID != "" {
			action = action.WithContextID(execCtxID)
		}

		remoteObj, excDetails, err := action.Do(cdp.WithExecutor(ctx, exec))
		if err != nil {
			return err
		}
		if excDetails != nil {
			if excDetails.Exception != nil {
				return fmt.Errorf("JS exception: %s", excDetails.Exception.Description)
			}
			return fmt.Errorf("JS exception")
		}
		if remoteObj != nil && remoteObj.Value != nil && result != nil {
			return json.Unmarshal(remoteObj.Value, result)
		}
		return nil
	}))
	// If chromedp path failed, try OOPIF fallback via Target.attachToTarget
	if err != nil && frameID != "" {
		return c.evalInOOPIFFrame(frameID, js, result)
	}
	return err
}

// evalInOOPIFFrame executes JS in a cross-origin iframe by attaching to its
// CDP target directly and sending Runtime.evaluate through the session.
func (c *Client) evalInOOPIFFrame(targetID string, js string, result interface{}) error {
	conn, _, _, err := ws.Dial(context.Background(), c.browserWSURL)
	if err != nil {
		return fmt.Errorf("OOPIF eval: dial failed: %w", err)
	}
	defer conn.Close()

	// Attach to the OOPIF target
	attachParams := fmt.Sprintf(`{"targetId":"%s","flatten":true}`, targetID)
	sessionID, err := c.cdpSend(conn, "Target.attachToTarget", attachParams, "")
	if err != nil {
		return fmt.Errorf("OOPIF eval: attach failed: %w", err)
	}
	var attachResp struct {
		SessionID string `json:"sessionId"`
	}
	if err := json.Unmarshal(sessionID, &attachResp); err != nil || attachResp.SessionID == "" {
		return fmt.Errorf("OOPIF eval: no sessionId in response")
	}

	// Evaluate JS through the session
	expr := fmt.Sprintf(`{"expression":%q,"returnByValue":true}`, js)
	evalResult, err := c.cdpSend(conn, "Runtime.evaluate", expr, attachResp.SessionID)
	if err != nil {
		return fmt.Errorf("OOPIF eval: Runtime.evaluate failed: %w", err)
	}

	// Parse result
	var evalResp struct {
		Result struct {
			Value json.RawMessage `json:"value"`
		} `json:"result"`
		ExceptionDetails *struct {
			Text string `json:"text"`
		} `json:"exceptionDetails,omitempty"`
	}
	if err := json.Unmarshal(evalResult, &evalResp); err != nil {
		return fmt.Errorf("OOPIF eval: parse failed: %w", err)
	}
	if evalResp.ExceptionDetails != nil {
		return fmt.Errorf("OOPIF eval: JS exception: %s", evalResp.ExceptionDetails.Text)
	}
	if result != nil && evalResp.Result.Value != nil {
		return json.Unmarshal(evalResp.Result.Value, result)
	}
	return nil
}

// cdpSend sends a CDP command over a gobwas/ws connection and returns the result.
func (c *Client) cdpSend(conn net.Conn, method, params, sessionID string) (json.RawMessage, error) {
	id := cdpCmdID.Add(1)
	cmd := cdpCmd{ID: id, Method: method, SessionID: sessionID}
	if params != "" {
		cmd.Params = json.RawMessage(params)
	}
	body, _ := json.Marshal(cmd)
	if err := wsutil.WriteClientMessage(conn, ws.OpText, body); err != nil {
		return nil, err
	}
	for {
		msgs, err := wsutil.ReadServerMessage(conn, nil)
		if err != nil {
			return nil, err
		}
		for _, msg := range msgs {
			var resp struct {
				ID     int64           `json:"id"`
				Result json.RawMessage `json:"result,omitempty"`
				Error  *struct {
					Message string `json:"message"`
				} `json:"error,omitempty"`
			}
			if err := json.Unmarshal(msg.Payload, &resp); err != nil {
				continue
			}
			if resp.ID == 0 {
				continue
			}
			if resp.Error != nil {
				return nil, fmt.Errorf("CDP error: %s", resp.Error.Message)
			}
			return resp.Result, nil
		}
	}
}

// EvalBool executes JavaScript in the specified frame and returns a bool result.
func (c *Client) EvalBool(frameID string, js string) (bool, error) {
	var result bool
	err := c.EvalInFrame(frameID, js, &result)
	return result, err
}

// TouchPoint represents a single touch point
type TouchPoint struct {
	X     float64
	Y     float64
	Force float64
}

// DefaultTouchPoint creates a touch point with default force
func DefaultTouchPoint(x, y float64) TouchPoint {
	return TouchPoint{X: x, Y: y, Force: 1.0}
}

// DispatchTouchEvent dispatches touch event via CDP Input.dispatchTouchEvent
func (c *Client) DispatchTouchEvent(touchType string, points []TouchPoint) error {
	// Convert []TouchPoint to []*input.TouchPoint
	cdpPoints := make([]*input.TouchPoint, len(points))
	for i := range points {
		cdpPoints[i] = &input.TouchPoint{
			X:     points[i].X,
			Y:     points[i].Y,
			Force: points[i].Force,
		}
	}

	var touchTypeEnum input.TouchType
	switch touchType {
	case "touchStart":
		touchTypeEnum = input.TouchStart
	case "touchMove":
		touchTypeEnum = input.TouchMove
	case "touchEnd":
		touchTypeEnum = input.TouchEnd
	case "touchCancel":
		touchTypeEnum = input.TouchCancel
	}

	return chromedp.Run(c.ctx, chromedp.ActionFunc(func(ctx context.Context) error {
		cc := chromedp.FromContext(ctx)
		if cc == nil || cc.Target == nil {
			return fmt.Errorf("invalid context")
		}
		exec := cdp.WithExecutor(ctx, cc.Target)
		return input.DispatchTouchEvent(touchTypeEnum, cdpPoints).Do(exec)
	}))
}

// quadraticBezier calculates point on quadratic bezier curve at t
func quadraticBezier(t float64, p0, p1, p2 []float64) []float64 {
	mt := 1 - t
	mt2 := mt * mt
	t2 := t * t
	return []float64{
		mt2*p0[0] + 2*mt*t*p1[0] + t2*p2[0],
		mt2*p0[1] + 2*mt*t*p1[1] + t2*p2[1],
	}
}

// sampleBezier samples n points from quadratic bezier curve
func sampleBezier(p0, p1, p2 []float64, n int) [][]float64 {
	points := make([][]float64, n)
	for i := 0; i < n; i++ {
		t := float64(i) / float64(n-1)
		pt := quadraticBezier(t, p0, p1, p2)
		points[i] = pt
	}
	return points
}

func (c *Client) RunIsolatedWorld(frameID cdp.FrameID, js string) (interface{}, error) {
	execCtxID, err := c.GetFrameOrCreateContext(c.ctx, frameID)
	if err != nil {
		return nil, fmt.Errorf("failed to create isolated world for frame %s: %w", frameID, err)
	}

	var result *runtime.RemoteObject
	var exceptionDetails *runtime.ExceptionDetails
	err = chromedp.Run(c.ctx, chromedp.ActionFunc(func(ctx context.Context) error {
		cc := chromedp.FromContext(ctx)
		if cc == nil || cc.Target == nil {
			return fmt.Errorf("invalid context or target")
		}

		var err error
		result, exceptionDetails, err = runtime.Evaluate(js).WithContextID(execCtxID).Do(cdp.WithExecutor(ctx, cc.Target))
		return err
	}))
	if err != nil {
		return nil, fmt.Errorf("failed to run JS in frame %s: %w", frameID, err)
	}

	if exceptionDetails != nil {
		return nil, fmt.Errorf("JS execution error in frame %s: %s", frameID, exceptionDetails.Exception.Description)
	}

	// First decode to string (jsontext.Value -> string)
	var resultStr string
	if err := json.Unmarshal(result.Value, &resultStr); err != nil {
		// If not a string, try to decode as JSON object directly
		var body interface{}
		if decodeErr := json.Unmarshal(result.Value, &body); decodeErr == nil {
			return body, nil
		}
		return nil, fmt.Errorf("failed to parse result: %w (value: %s)", err, string(result.Value))
	}
	// Then decode string as JSON
	var body interface{}
	if err := json.Unmarshal([]byte(resultStr), &body); err != nil {
		return nil, fmt.Errorf("failed to parse body: %w", err)
	}
	return body, nil
}

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
	var axNodes []*accessibility.Node
	err = chromedp.Run(c.ctx, chromedp.ActionFunc(func(ctx context.Context) error {
		cc := chromedp.FromContext(ctx)
		if cc == nil || cc.Target == nil {
			return fmt.Errorf("invalid context")
		}
		var getErr error
		axNodes, getErr = accessibility.GetFullAXTree().Do(cdp.WithExecutor(ctx, cc.Target))
		return getErr
	}))
	if err == nil && len(axNodes) >= 10 {
		return axNodes, "axTree", nil
	}

	// Method 3: DOM snapshot (minimal, works almost always)
	var domNode *cdp.Node
	err = chromedp.Run(c.ctx, chromedp.ActionFunc(func(ctx context.Context) error {
		cc := chromedp.FromContext(ctx)
		if cc == nil || cc.Target == nil {
			return fmt.Errorf("invalid context")
		}
		var getErr error
		domNode, getErr = dom.GetDocument().WithPierce(true).Do(cdp.WithExecutor(ctx, cc.Target))
		return getErr
	}))
	if err == nil && domNode != nil {
		return domNode, "domDocument", nil
	}

	return nil, "", fmt.Errorf("all content methods failed: isolatedWorld, axTree, domDocument")
}

// iframeNodePrefix 是「这个选择器指的是某个帧的 owner <iframe> 元素，按 backendNodeId 认」
// 的暗号（由 ResolveIframeSelector 产出，只在本文件里消费）。
//
// ⚠️ 它替代了原先的 `__iframe:<下标>`。那一版是**结构性错的**（2026-09-17 真窗口实测，
// 跨源 iframe 里点击坐标没加偏移，点到了 iframe **上方**的主页面上）：
//
//	① 基线帧树**不含跨站子帧**（OOPIF 不进父页会话的帧树）—— 实测一个「0×0 about:blank
//	   + 要点的跨站帧 + 0×0 about:blank」的页面上，`GetFrameTree` 只列出两个 about:blank，
//	   真正要点的那个**一个都没有**；`GetFrameTreeWithEvents` 会把 DOM 里发现的帧
//	   **追加在末尾**，于是那个下标是「合并顺序」；
//	② 那个下标被拿去索引 **DOM 里 `querySelectorAll('iframe')` 的顺序** —— 两个顺序
//	   根本不是一回事（DOM 里还站着 0×0 的、about:blank 的、广告的）。
//	   实测：DOM 顺序 [0]0×0 [1]要点的(top=199) [2]0×0，帧树顺序 [0]0×0 [1]0×0 [2]要点的
//	   → 下标 2 索引到的是**另一个 0×0**（top=14）→ 偏移算成 (0,14) 而不是 (0,199)。
//
// 静默性：落点在按下前后都是那个错的地方，**没有变化** → 判据无话可说 → 三个 landing_*
// 键一个都不出现，回执一切正常。所以这一条只能靠「坐标对不对」的断言来钉。
const iframeNodePrefix = "__iframeNode:"

// iframeNodeBackendID 认这个暗号，取出 backendNodeId。
func iframeNodeBackendID(selector string) (cdp.BackendNodeID, bool) {
	if !strings.HasPrefix(selector, iframeNodePrefix) {
		return 0, false
	}
	n, err := strconv.Atoi(strings.TrimPrefix(selector, iframeNodePrefix))
	if err != nil || n <= 0 {
		return 0, false
	}
	return cdp.BackendNodeID(n), true
}

// ResolveIframeSelector 给某个帧的 owner <iframe> 元素造一个「选择器」。
//
// 走 `DOM.getFrameOwner`：**由帧 id 直接给出 owner 元素**，与帧树顺序、DOM 顺序、
// OOPIF 在不在帧树里**都无关**（那三条正是旧实现栽的地方，见 iframeNodePrefix 的注释）。
func (c *Client) ResolveIframeSelector(frameID string) (string, error) {
	if frameID == "" {
		return "", fmt.Errorf("frameID 是空的（主帧不需要解析 iframe）")
	}
	bID, err := c.frameOwnerBackendID(frameID)
	if err != nil {
		return "", fmt.Errorf("找不到帧 %s 的 owner 元素: %w", frameID, err)
	}
	return fmt.Sprintf("%s%d", iframeNodePrefix, bID), nil
}

// frameOwnerBackendID 问 CDP：这个帧的 owner 元素（那个 <iframe>）是谁。
func (c *Client) frameOwnerBackendID(frameID string) (cdp.BackendNodeID, error) {
	var bID cdp.BackendNodeID
	err := chromedp.Run(c.ctx, chromedp.ActionFunc(func(ctx context.Context) error {
		cc := chromedp.FromContext(ctx)
		if cc == nil || cc.Target == nil {
			return fmt.Errorf("invalid context")
		}
		got, _, err := dom.GetFrameOwner(cdp.FrameID(frameID)).Do(cdp.WithExecutor(ctx, cc.Target))
		if err != nil {
			return err
		}
		bID = got
		return nil
	}))
	if err != nil {
		return 0, err
	}
	return bID, nil
}

// frameOrigin 取某个帧的 owner <iframe> 在**主帧视口**里的左上角（CSS px）。
// 主帧（frameID 为空）就是 (0,0)。
//
// 它是「帧内坐标 → 主帧坐标」那一步（`主帧 = 帧内 + 这个原点`）。
// ⚠️ 谁需要它：任何**在帧里求值拿到坐标、再拿去发鼠标事件**的地方 ——
// 鼠标事件收的永远是**主帧视口坐标**（`Input.dispatchMouseEvent` 那一层不知道帧的存在）。
// 2026-09-17 实测：漏了这一步的两条路是 `SelectOption` 的自定义下拉（点控件 / 点选项）
// 与 `fillDatePicker`（点日历按钮 / 点那一天）—— 都是「命令返回成功、页面纹丝不动」。
func (c *Client) frameOrigin(frameID string) (float64, float64, error) {
	if frameID == "" {
		return 0, 0, nil
	}
	sel, err := c.ResolveIframeSelector(frameID)
	if err != nil {
		return 0, 0, err
	}
	rect, err := c.GetElementCenter(sel, "")
	if err != nil {
		return 0, 0, err
	}
	return rect["x"], rect["y"], nil
}

// frameClickCoords 把**帧内**坐标翻成主帧视口坐标。
func (c *Client) frameClickCoords(frameID string, x, y float64) (float64, float64, error) {
	ox, oy, err := c.frameOrigin(frameID)
	if err != nil {
		return 0, 0, err
	}
	return x + ox, y + oy, nil
}

// elementRectByBackendID 取一个节点在**它所在文档的视口**里的矩形（CSS px）。
//
// 为什么不用选择器：跨站子帧的 owner 元素就在主文档里，但**没有选择器能唯一指认它**
// （DOM 顺序与帧 id 无关）—— 只能拿 backendNodeId 名对名地取。
//
// ⚠️ 已知限制（与旧实现同一档，本轮不动）：帧**套帧**时这里给的是内层 iframe 在
// **它父亲那一帧**视口里的坐标，外层偏移没有累加 —— 修它要给整条祖先链求和，
// 是另一件事。
func (c *Client) elementRectByBackendID(bID cdp.BackendNodeID) (map[string]float64, error) {
	var out map[string]float64
	err := chromedp.Run(c.ctx, chromedp.ActionFunc(func(ctx context.Context) error {
		cc := chromedp.FromContext(ctx)
		if cc == nil || cc.Target == nil {
			return fmt.Errorf("invalid context")
		}
		exec := cdp.WithExecutor(ctx, cc.Target)
		obj, err := dom.ResolveNode().WithBackendNodeID(bID).Do(exec)
		if err != nil {
			return err
		}
		if obj == nil || obj.ObjectID == "" {
			return fmt.Errorf("resolveNode 没给对象（backendNodeId=%d）", bID)
		}
		res, _, err := runtime.CallFunctionOn(`function(){
			var r = this.getBoundingClientRect();
			return {x: r.x, y: r.y, width: r.width, height: r.height,
			        centerX: r.x + r.width/2, centerY: r.y + r.height/2};
		}`).WithObjectID(obj.ObjectID).WithReturnByValue(true).Do(exec)
		if err != nil {
			return err
		}
		return json.Unmarshal(res.Value, &out)
	}))
	if err != nil {
		return nil, err
	}
	if out == nil {
		return nil, fmt.Errorf("取不到矩形（backendNodeId=%d）", bID)
	}
	return out, nil
}

// GetElementCenter returns element center coordinates
//
// ⚠️ `__iframeNode:<backendNodeId>` 那一路**不走 JS**（见 iframeNodePrefix 的注释：
// 「帧树下标 → DOM iframe 下标」是结构性错的，实测点到了 iframe 上方的主页面上）。
func (c *Client) GetElementCenter(selector, frameId string) (map[string]float64, error) {
	if bID, ok := iframeNodeBackendID(selector); ok {
		return c.elementRectByBackendID(bID)
	}
	js := withPierce(fmt.Sprintf(`(function(){
		var el;
		el = __cdpQ('%[1]s');
		if (!el) return JSON.stringify({error: 'element not found'});
		var rect = el.getBoundingClientRect();
		if (!rect) return null;
		return JSON.stringify({x: rect.x, y: rect.y, centerX: rect.x + rect.width/2, centerY: rect.y + rect.height/2, width: rect.width, height: rect.height});
	})()`, selector))
	var resultStr string
	err := c.EvalInFrame(frameId, js, &resultStr)
	if err != nil {
		return nil, err
	}
	var result map[string]interface{}
	if err := json.Unmarshal([]byte(resultStr), &result); err != nil {
		return nil, fmt.Errorf("failed to parse element center: %w", err)
	}
	if _, ok := result["error"]; ok {
		return nil, fmt.Errorf("%v", result["error"])
	}
	return map[string]float64{
		"x":       result["x"].(float64),
		"y":       result["y"].(float64),
		"centerX": result["centerX"].(float64),
		"centerY": result["centerY"].(float64),
		"width":   result["width"].(float64),
		"height":  result["height"].(float64),
	}, nil
}

// IsTouchDevice returns true if user agent indicates mobile/tablet
func (c *Client) IsTouchDevice() (bool, error) {
	return c.EvalBool("", TouchDetectionJS)
}

// IsElementVisible checks if element visible area >= 80%
func (c *Client) IsElementVisible(selector string) (bool, error) {
	if bID, ok := iframeNodeBackendID(selector); ok {
		rect, err := c.elementRectByBackendID(bID)
		if err != nil {
			return false, err
		}
		vw, vh, err := c.getViewportDimensions()
		if err != nil {
			return false, err
		}
		w, h := rect["width"], rect["height"]
		if w <= 0 || h <= 0 {
			return false, nil // 0×0 的 iframe：不可见（也点不到）
		}
		visible := (math.Min(rect["x"]+w, vw) - math.Max(rect["x"], 0)) *
			(math.Min(rect["y"]+h, vh) - math.Max(rect["y"], 0))
		return visible/(w*h) >= 0.8, nil
	}
	js := withPierce(fmt.Sprintf(`(function(){
		var el;
		el = __cdpQ('%[1]s');
		if (!el) return false;
		const rect = el.getBoundingClientRect();
		const visibleArea = (Math.min(rect.right, window.innerWidth) - Math.max(rect.left, 0)) *
						   (Math.min(rect.bottom, window.innerHeight) - Math.max(rect.top, 0));
		const totalArea = rect.width * rect.height;
		return visibleArea / totalArea >= 0.8;
	})()`, selector))
	var result bool
	err := c.EvalInFrame("", js, &result)
	return result, err
}

// ScrollIntoView executes scrollIntoView on element
func (c *Client) ScrollIntoView(selector, frameID string) error {
	js := withPierce(fmt.Sprintf(`(function(){
		var el = __cdpQ('%s');
		if (!el) return {error: 'element not found'};
		el.scrollIntoView({ block: "center" });
		return {success: true};
	})()`, selector))
	var result map[string]interface{}
	return c.EvalInFrame(frameID, js, &result)
}

// WaitForPositionStable polls element position until two consecutive reads
// (100ms apart) return the same coordinates, indicating the page has settled.
func (c *Client) WaitForPositionStable(selector, frameID string) error {
	var prevX, prevY float64
	for i := 0; i < 30; i++ {
		rect, err := c.GetElementCenter(selector, frameID)
		if err != nil {
			return err
		}
		if i > 0 && rect["centerX"] == prevX && rect["centerY"] == prevY {
			return nil
		}
		prevX = rect["centerX"]
		prevY = rect["centerY"]
		time.Sleep(100 * time.Millisecond)
	}
	return fmt.Errorf("position did not stabilize after 3s")
}

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
	return c.EvalInFrame("", TrackRenderJS, nil)
}

// getViewportDimensions returns viewport width and height
func (c *Client) getViewportDimensions() (width, height float64, _ error) {
	js := `(function(){return JSON.stringify({width: window.innerWidth, height: window.innerHeight});})()`
	var vpStr string
	if err := c.EvalInFrame("", js, &vpStr); err != nil {
		return 0, 0, err
	}
	var vp map[string]float64
	if err := json.Unmarshal([]byte(vpStr), &vp); err != nil {
		return 0, 0, fmt.Errorf("failed to parse viewport: %w", err)
	}
	return vp["width"], vp["height"], nil
}

// ScrollTouch performs touch scroll gesture
// Returns all touch points and color if track=true
func (c *Client) ScrollTouch(selector string, track bool) ([][]float64, string, error) {
	vpWidth, vpHeight, err := c.getViewportDimensions()
	if err != nil {
		return nil, "", err
	}
	center, err := c.GetElementCenter(selector, "")
	if err != nil {
		return nil, "", err
	}
	centerY := center["centerY"]

	// 计算滑动方向：元素在视口中心上方则下滑，否则上滑
	isAbove := centerY < vpHeight/2

	// 随机偏移（热力图随机）
	randOffsetX := (mathrand.Float64()*2 - 1) * 30 // ±30px
	randOffsetY := (mathrand.Float64()*2 - 1) * 20 // ±20px

	var startX, startY, stopX, stopY float64
	if isAbove {
		// 下滑
		startX = vpWidth*0.85 + randOffsetX
		startY = vpHeight/2 + randOffsetY
		stopX = vpWidth*0.75 + randOffsetX - 30
		stopY = vpHeight - randOffsetY
	} else {
		// 上滑
		startX = vpWidth*0.75 + randOffsetX
		startY = vpHeight - randOffsetY
		stopX = vpWidth*0.85 + randOffsetX + 30
		stopY = vpHeight/2 + randOffsetY
	}

	// 二次贝塞尔曲线控制点 - 右手拇指滑动时弧度始终在左侧
	p0 := []float64{startX, startY}
	var p1X, p1Y float64
	if isAbove {
		// 下滑：起点在右侧，终点在底部，控制点在左上方
		p1X = startX - (startX-vpWidth*0.5)*0.3
		p1Y = startY - (startY-vpHeight*0.5)*0.2
	} else {
		// 上滑：起点在底部左侧，终点在右侧中部，控制点在左上方
		p1X = startX - (startX-vpWidth*0.5)*0.3
		p1Y = stopY + (vpHeight*0.5-stopY)*0.2
	}
	p1 := []float64{p1X, p1Y}
	p2 := []float64{stopX, stopY}

	points := sampleBezier(p0, p1, p2, 15)

	// 分 3 次发送 touch 事件
	// 1. touchStart + 1 point
	if err := c.DispatchTouchEvent("touchStart", []TouchPoint{DefaultTouchPoint(points[0][0], points[0][1])}); err != nil {
		return nil, "", fmt.Errorf("touchStart failed: %w", err)
	}
	// 2. touchMove - each point as a separate event (sequential, not multi-touch)
	for i := 1; i < len(points)-1; i++ {
		if err := c.DispatchTouchEvent("touchMove", []TouchPoint{DefaultTouchPoint(points[i][0], points[i][1])}); err != nil {
			return nil, "", fmt.Errorf("touchMove failed: %w", err)
		}
		time.Sleep(time.Duration(8+mathrand.Intn(8)) * time.Millisecond)
	}
	// 3. touchEnd + 1 point
	if err := c.DispatchTouchEvent("touchEnd", []TouchPoint{DefaultTouchPoint(points[len(points)-1][0], points[len(points)-1][1])}); err != nil {
		return nil, "", fmt.Errorf("touchEnd failed: %w", err)
	}

	// 触摸结束后重置状态，防止页面锁定触摸
	c.DispatchTouchEvent("touchCancel", []TouchPoint{DefaultTouchPoint(points[len(points)-1][0], points[len(points)-1][1])})

	// 生成随机颜色
	color := fmt.Sprintf("hsl(%d, 70%%, 50%%)", int(mathrand.Float64()*360))

	return points, color, nil
}

// DispatchMouseScrollEventAt dispatches mouse scroll event at (x, y) via CDP
func (c *Client) DispatchMouseScrollEventAt(x, y, deltaX, deltaY float64) error {
	return chromedp.Run(c.ctx, chromedp.ActionFunc(func(ctx context.Context) error {
		cc := chromedp.FromContext(ctx)
		if cc == nil || cc.Target == nil {
			return fmt.Errorf("invalid context")
		}
		exec := cdp.WithExecutor(ctx, cc.Target)
		return input.DispatchMouseEvent(input.MouseWheel, x, y).WithDeltaX(deltaX).WithDeltaY(deltaY).Do(exec)
	}))
}

// ── 落点（landing）判定：按下与抬起之间，这个坐标上是不是**换了人** ──────────
//
// 为什么要这一层（2026-09-16 真窗口实测，gowizard 的 MUI 问卷）：
//
//	现代组件库的下拉在 **mousedown 就把菜单展开**（MUI 的 Select 在 mousedown 里
//	setOpen(true)，React 同步重渲染、Portal 到 body），于是紧跟着的 `released`
//	落在**刚出现的那一层**上 —— 那一层（backdrop / 菜单本身）常带「点外面就关掉」
//	的处理器，于是**我们自己的抬起把刚打开的菜单又关掉**：点了没反应。
//	实测：只发 mousedown → 菜单开着；moved→pressed→released 连着发 → 菜单被自己关掉。
//
// 判据是「**落点变了**」这件事本身，不是「等久一点希望它别变」：
// 铺一层 backdrop 是**一帧之内**的事，press 与 release 之间 sleep 多久都拦不住 ——
// 抬起的时候那层已经在了，落点仍然是它。所以这里**不靠时间**，靠**重新判落点**。
//
// 机制（按下之后重新做一次**浏览器自己的命中测试**）：
//
//	① 发 MouseMoved（**判据窗口从这一步之后才开始** —— 见下面的 ⚠️）
//	② 问一次：这个坐标上是哪个节点（frame + backendNodeId）
//	③ 发 MousePressed（mousedown 的处理器在这里跑完，DOM 已经变了）
//	④ 再问一次
//	⑤ 同一个节点 → 照常发 MouseReleased（正常点击，57 个生产脚本的路径）
//	   换了人 → **扣下这次 released**：既不发给新落点，也不让浏览器把这次按下-抬起
//	   合成为一个落在新元素上的 click（真站实测：那一下的 mouseup 落在新出现的
//	   菜单项/backdrop 上，click 落在两者的公共祖先上 —— 两条都是「交给新落点」）
//
// ⚠️ **判据窗口 = 按下之前那一刻 → 按下之后**，**不含** MouseMoved 那一段。
// 规矩说的是「按下与抬起之间」，而悬停引起的重渲染发生在**按下之前** ——
// 把它算进来会让「鼠标掠过页面」本身就触发扣下（复审端到端复现过：hover 换节点
// 会把一次正常点击静默吞掉）。所以 MouseMoved 先发，落点在那之后才取。
//
// ⚠️⚠️ 扣下的**必要条件**（2026-09-17 修复轮 1，复审两条 Critical）：
// 「换了节点」**不等于**「有人盖上来」。`backendNodeId` 在**节点重建**时也会变
// （markup 逐字节相同的重建、子元素换标签类型），而重建**没有任何东西盖上来** ——
// 照直扣下就把一次正常点击**静默吞掉**，那比原来那个病更坏。所以扣下还要求：
//
//	① 前后两个节点的**指纹不同**（标签 + 属性规范化后的串）：逐字节重建的那份
//	   指纹一模一样 → 判成「同一个东西被重建」→ 照常发抬起；
//	② 按下**之前**那个节点**还在文档里**（`isConnected`）：目标自己没了
//	   （被替换/被摘除）说明变的是目标自己，不是「有人盖上来」→ 照常发抬起。
//
// 两条都是**正向取证**：取不到证（读不到指纹、问不到连通性）就**不扣**，
// 并把「为什么没扣/为什么判不了」写进 MouseClickOutcome.Note。
// 理由是不对称的：扣错了 = 一次正常点击**静静地**消失（看不见的失败），
// 不扣 = 退回原来的行为（菜单被自己关掉，**看得见**）。
//
// 为什么用 `DOM.getNodeForLocation` 而不是自写 JS 命中测试：
// 它就是**真实输入走的那条代码路**（同一套 HitTestResult），并且**穿 shadow DOM**
// （`document.elementFromPoint` 返回的是 host，shadow 里的覆盖物它看不见 ——
// cdp 全部力气都花在穿透上，这里不能瞎）。
//
// ⚠️ 判不出来时（点不在视口里、协议报错）**按老行为走**（不扣）：扣下抬起是
// 「不把点击交给一个可疑的新落点」的保护，判不出来时凭据不足，不额外拿走一次点击。
// 这条路径**不静默**：返回值里带着前后两次的落点描述（见 MouseClickOutcome）。
//
// ⚠️ **已知后果（2026-09-17 本机复现）**：判据在跨源子帧里瞎，所以**若页面在 release 上
// 关菜单**（有些 MUI 版本会），那一格会退化回「点开又被自己关掉」—— 夹具里复现过
// （帧内 `mousedown:in-control` → `mouseup:in-backdrop` → 菜单关掉）。真站那一版 MUI
// 实测**不**在 mouseup 上关菜单，所以 gowizard 这一格能跑通；换个库就不一定。
// 要真修得在子帧**内部**自己判落点（`__cdpQA` 那一路能进子帧的 document），另裁。
//
// ⚠️ **跨站 iframe 是这套判据的一个已知盲区**（复审实测 + 本机复验）：
// 跨站子帧里 `DOM.getNodeForLocation` **只返回父页的 `<iframe>` 元素**（同 site
// 跨 origin 才下钻），于是前后两次问到的都是同一个 `<iframe>` —— 判据**恒不触发**。
// 实测：`http://127.0.0.1:8731` 里嵌 `https://chameleon-na.www.gowizard.com/...`，
// 主帧坐标 (640,129) 命中 `IFRAME`（frame = 父页）。这个盲区**必须可听见**
// （见 MouseClickOutcome.Blind / Note 与 DiagKindLandingBlind），不许静默空转。
type domHit struct {
	FrameID   cdp.FrameID
	BackendID cdp.BackendNodeID
}

// hitTestAt 问浏览器：这个坐标上现在是谁。第二个返回值为 false = 判不出来。
func (c *Client) hitTestAt(x, y float64) (domHit, bool) {
	var h domHit
	ok := false
	err := chromedp.Run(c.ctx, chromedp.ActionFunc(func(ctx context.Context) error {
		cc := chromedp.FromContext(ctx)
		if cc == nil || cc.Target == nil {
			return fmt.Errorf("invalid context")
		}
		exec := cdp.WithExecutor(ctx, cc.Target)
		bID, fID, _, err := dom.GetNodeForLocation(int64(math.Round(x)), int64(math.Round(y))).Do(exec)
		if err != nil {
			return err
		}
		h, ok = domHit{FrameID: fID, BackendID: bID}, true
		return nil
	}))
	if err != nil {
		return domHit{}, false
	}
	return h, ok
}

// hitFacts 是一次命中的「可读事实」——描述给人看，指纹给判据用。
type hitFacts struct {
	// Desc 是给人看的一行（`<div class="MuiBackdrop-root …">`）。
	Desc string
	// Tag 是小写标签名（IFRAME / FRAME 用来识别「命中停在子帧容器上」）。
	Tag string
	// Fingerprint 是**标签 + 属性**规范化后的串：用来回答「前后这两个节点
	// 是不是同一个东西」。节点被**重建**（markup 逐字节相同）时它逐字相同 ——
	// 那正是「没有东西盖上来」却换了 backendNodeId 的那种情况。
	Fingerprint string
}

// hitFactsOf 读一次命中的事实（一次 describeNode 出三条产出）。
//
// ⚠️ 它**不是只在罕见路径上调**（上一版的注释这么写，与调用位不符 —— 复审点名）：
// 「按下前」那一次**每次点击都要读**（要判断命中是不是停在子帧容器上，见
// dispatchMouseClick 的 ②），「按下后」那一次才只在落点变了时读。
// 代价是每次点击多一次往返，换来的是**盲区诊断不会静默** —— 这一次往返省不掉：
// 试过用「这页有没有子帧」当快路的闸，而跨站子帧（OOPIF）根本不进父页的帧树，
// 快路恰好在唯一需要它的场景下失效（详见 dispatchMouseClick ② 的注释）。
// 读不到就返回 err，调用方据此**不扣**。
func (c *Client) hitFactsOf(h domHit) (hitFacts, error) {
	var f hitFacts
	err := chromedp.Run(c.ctx, chromedp.ActionFunc(func(ctx context.Context) error {
		cc := chromedp.FromContext(ctx)
		if cc == nil || cc.Target == nil {
			return fmt.Errorf("invalid context")
		}
		exec := cdp.WithExecutor(ctx, cc.Target)
		n, err := dom.DescribeNode().WithBackendNodeID(h.BackendID).Do(exec)
		if err != nil {
			return err
		}
		if n == nil {
			return fmt.Errorf("describeNode 没给节点")
		}
		f.Tag = strings.ToLower(n.NodeName)
		var b, fp strings.Builder
		b.WriteString("<")
		b.WriteString(f.Tag)
		// 指纹用**全部**属性（规范化：属性名 + 值，按 DOM 给的顺序）——
		// 只比 id/class/role 的话，「同一个类名但 aria-* 变了」会被误判成同一个东西。
		for i := 0; i+1 < len(n.Attributes); i += 2 {
			fp.WriteString(n.Attributes[i])
			fp.WriteString("=")
			fp.WriteString(n.Attributes[i+1])
			fp.WriteString("\x00")
			k, v := n.Attributes[i], n.Attributes[i+1]
			if k != "id" && k != "class" && k != "role" {
				continue
			}
			fmt.Fprintf(&b, " %s=%q", k, capRunes(v, 40))
		}
		b.WriteString(">")
		f.Desc = b.String()
		f.Fingerprint = fp.String()
		return nil
	}))
	if err != nil {
		return hitFacts{}, err
	}
	return f, nil
}

// hitForensics 一次往返问三件事（都在**按下前那个节点**的对象上问）：
//
//	connected   它还在文档里吗（isConnected）—— 「被盖住」与「被重建/摘除」的分水岭
//	descendant  按下后那个节点在**它的子树里**吗（它自己是祖先）
//	ancestor    按下后那个节点是**它的祖先**吗（它自己在子树里）
//
// descendant / ancestor 合起来回答「里面长出来」还是「外面盖上来」——
// 两个方向**分开返回**，因为它们的后果不一样（见 dispatchMouseClick ⑤ 的两句话）：
// 后代那一向事件照样从目标身上过，祖先那一向不会。
//
// ⚠️⚠️ 这里的「祖先/后代」**只走 parentNode 或 shadow host 那一条链**
// （getRootNode().host 那一步是 shadow 边界），**不是完整的合成树**：
// **`<slot>` 分发没有实现** —— 经 slot 被分发进 shadow 树的光 DOM 节点，在这条链上
// 的祖先是它的**光 DOM 父亲**（parentNode），而不是它在 shadow 树里实际渲染的位置。
//
// 已知限制（复审判 Important，**不是** Critical —— 非静默、生产栈上碰不到，但必须写下来）：
//   目标在 shadow 树里、覆盖者经 `<slot>` 被分发进它的子树 → 这一对**认不出来**
//   （两个方向都是 false）→ 判成「外面盖上来」→ **扣下**。复审实测：
//   裸行为里目标自己的 click 处理器跑 **1** 次，被测 **0** 次。
//   要真修得把 slot assignment（`slot.assignedNodes()` / `element.assignedSlot`）那条链
//   补进来 —— 那是另一件事，留给裁定；**这一版没有测试钉它**（别以为它被覆盖了）。
//
// ⚠️ 为什么不干脆用 Node.contains：宿主**不包含** shadow 里的内容
// （`host.contains(shadowChild)` 是 false）—— 拿 contains 判，shadow 里的浮层
// 会被误判成「外面盖上来」，那正是这一轮要治的误扣。走 parentNode/host 这条链
// 至少把 shadow 那一族认对了；缺口只剩 slot 分发这一族。
//
// ⚠️ 另一条已知限制（复审实测，**不是静默的**，留给裁定）：目标在子帧、覆盖者在父帧
// （或反过来）时，`callFunctionOn` 会直接报
// `-32000 Argument should belong to the same JavaScript world` —— 于是这一格走
// 「取证不全 → 不扣 + 报盲区」。方向是「**保护没了**」而不是「点击被吞了」，
// 而且它说出来了（`landing_blind`）。⚠️ 轮 1 的实现在这一格是**扣下**的（复审判定：
// 本轮新引入的退化）。要修得把跨世界的比较换成「各自在自己那一侧取事实，Go 侧拼」。
//
// 问不到就返回 err —— 调用方据此**不扣**（正向取证，见 domHit 上面那段）。
func (c *Client) hitForensics(before, after domHit) (connected, descendant, ancestor bool, err error) {
	err = chromedp.Run(c.ctx, chromedp.ActionFunc(func(ctx context.Context) error {
		cc := chromedp.FromContext(ctx)
		if cc == nil || cc.Target == nil {
			return fmt.Errorf("invalid context")
		}
		exec := cdp.WithExecutor(ctx, cc.Target)
		objBefore, err := dom.ResolveNode().WithBackendNodeID(before.BackendID).Do(exec)
		if err != nil {
			return err
		}
		if objBefore == nil || objBefore.ObjectID == "" {
			return fmt.Errorf("resolveNode(按下前) 没给对象")
		}
		objAfter, err := dom.ResolveNode().WithBackendNodeID(after.BackendID).Do(exec)
		if err != nil {
			return err
		}
		if objAfter == nil || objAfter.ObjectID == "" {
			return fmt.Errorf("resolveNode(按下后) 没给对象")
		}
		res, _, err := runtime.CallFunctionOn(hitForensicsJS).
			WithObjectID(objBefore.ObjectID).
			WithArguments([]*runtime.CallArgument{{ObjectID: objAfter.ObjectID}}).
			WithReturnByValue(true).
			Do(exec)
		if err != nil {
			return err
		}
		var got struct {
			Connected  bool `json:"connected"`
			Descendant bool `json:"descendant"`
			Ancestor   bool `json:"ancestor"`
		}
		// returnByValue 之后 res.Value 就是返回对象的 JSON 文本（直接解）。
		// ⚠️ 别用 JSON.stringify + 手工去引号：那样拿到的是**转义过的字符串**，
		// 里面的 \" 会让 Unmarshal 报「invalid character '\\'」（实测踩过）。
		if err := json.Unmarshal(res.Value, &got); err != nil {
			return fmt.Errorf("取证结果解不开: %w（原始 %s）", err, res.Value)
		}
		connected, descendant, ancestor = got.Connected, got.Descendant, got.Ancestor
		return nil
	}))
	return connected, descendant, ancestor, err
}

// hitForensicsJS 在**按下前那个节点**上跑（this = 它），参数 other = 按下后那个。
//
// inTree 走的是「**parentNode 或 shadow host**」那一条链（host 那一步是 shadow 边界）——
// **不是**完整合成树：`<slot>` 分发没有实现（详见 hitForensics 的已知限制）。
// 它比 Node.contains 强的地方只有一处，但那一处是刚需：
// contains 在 shadow 上会把「宿主包含 shadow 内容」判成 false。
const hitForensicsJS = `function(other){
  function inTree(root, node) {
    var n = node, r;
    while (n) {
      if (n === root) return true;
      r = n.getRootNode ? n.getRootNode() : null;
      n = (r && r.host) ? r.host : (n.parentNode || null);
    }
    return false;
  }
  return {
    connected: !!(this && this.isConnected),
    descendant: !!(other && inTree(this, other)),
    ancestor: !!(other && inTree(other, this))
  };
}`

// MouseClickOutcome 是一次点击**后半段**的事实：这次抬起交给了谁。
//
// 为什么要把「扣下」这件事交出来：扣下抬起 = 页面收到的鼠标事件比从前少一个。
// 那是有意的（见上），但**必须说出来** —— 本项目最贵的一类失败是「看起来成功了」：
// 若回执里一个字都不提，调用方会以为这是一次普通的点击，而它其实只发出了按下的那一半。
type MouseClickOutcome struct {
	// ReleaseWithheld 说这次 MouseReleased **没有发**（落点换了人）。
	ReleaseWithheld bool
	// LandedBefore / LandedAfter 是按下前 / 按下后那个坐标上的节点。
	LandedBefore string
	LandedAfter  string
	// Blind 说**判据这一次没能跑**（跨站子帧 / 判不出落点）：可见的行为与从前一样
	// （抬起照发），但「落点变了就不交给新落点」这条保护**在这一帧不存在**。
	//
	// ⚠️ 它必须能听见：跨站 iframe 里命中测试不下钻，判据**恒不触发** ——
	// 不说的话，这一帧上的点击看起来和别处一样「有保护」，其实一点都没有。
	Blind bool
	// Note 是这一次判据说的话（判不了 / 判成了重建所以没扣 / 取不到证所以没扣）。
	// 空串 = 判据跑成了、也没什么要说的。
	Note string
}

// DispatchMouseClick dispatches mouse move, press, and release at (x, y) —
// 但**按下与抬起之间落点换了人时，抬起不发**（理由见 domHit 上面那一大段）。
//
// 回执（落点前后是谁、扣没扣、判据是不是瞎的）走 dispatchMouseClick；
// 这个入口只报错，因为 57 个生产脚本的动作全都只关心「成没成」。
func (c *Client) DispatchMouseClick(x, y float64) error {
	_, err := c.dispatchMouseClick(x, y)
	return err
}

// LandingDiags 交出**这次连接上**落点判据发过的诊断（判据没跑成的那些）。
//
// 为什么单独攒一份：`form` 那条路（FillText / CheckElement / SelectOption）不返回
// 结构化回执，但它的点击**同样会扣下抬起** —— 复审实测那条路上「不静默」是 0 字节。
// 攒在这儿，由调用方（cmd/form.go）印出去。与 `targetDiags` 同一套办法。
func (c *Client) LandingDiags() []Diagnostic {
	return c.landingDiags
}

// LandingSummary 是落点判据对**这一次动作**的一句话总结 —— `cdp form` 的回执与
// MCP 的 form 回执**共用同一份**（两处各拼一遍必然漂移，而「两份判据」是本仓反复
// 踩的坑）。字段名与 MCP 那道门上的键一一对应，直接塞进回执即可。
type LandingSummary struct {
	Note     string       `json:"landing_note,omitempty"`
	Blind    bool         `json:"landing_blind,omitempty"`
	Withheld bool         `json:"landing_withheld,omitempty"`
	Diags    []Diagnostic `json:"landing,omitempty"`
}

// SummarizeLanding 把一次动作攒下的落点诊断拼成给消费方的那一份。
//
// 没有任何诊断时返回零值（`Note == ""`）—— 调用方据此**一个键都不加**：
// 常驻的提示等于没有提示（判据没话可说时回执里一个字都不该多）。
func SummarizeLanding(diags []Diagnostic) LandingSummary {
	if len(diags) == 0 {
		return LandingSummary{}
	}
	var b strings.Builder
	sum := LandingSummary{Diags: diags}
	for i, d := range diags {
		if i > 0 {
			b.WriteString("；")
		}
		b.WriteString(d.Detail)
		switch d.Kind {
		case DiagKindLandingBlind:
			sum.Blind = true
		case DiagKindLandingWithheld:
			// ⚠️ 只认这一个 kind：DiagKindLandingPassed（判据跑了但决定**不扣**）
			// 绝不能算进来 —— 那会让 `landing_withheld` 这个字段说假话。
			sum.Withheld = true
		}
	}
	sum.Note = b.String()
	return sum
}

// noteLanding 记一条落点判据的诊断，并把它写进回执的 Note。
//
// kind 取三选一：DiagKindLandingBlind（判据没能跑）/ DiagKindLandingWithheld（扣下了）/
// DiagKindLandingPassed（判据跑了，但决定**不扣** —— 重建、目标自己被摘除、里面长出来）。
// ⚠️ 「没扣」与「扣了」**必须分得开**：下游（`cdp form` 的回执、MCP 的回执）是按 kind
// 出 `landing_withheld` 这个布尔值的 —— 把「没扣」也记成 withheld，那个字段就在说假话。
// 三条都要记：`form` 那条路只有这份诊断能说话（见 LandingDiags）。
//
// frameID 是这次命中**落在哪一帧**（命中测试给的）。诊断里的 frame_path 按
// `framePathFor` 的同一套口径写：空 → ["main"]，否则就是那一帧的 id ——
// 一律写 ["main"] 在跨帧时不实（复审点名），而位置这种东西写错比不写更坏。
func (c *Client) noteLanding(out *MouseClickOutcome, kind, detail, frameID string) {
	out.Note = detail
	if kind == DiagKindLandingBlind {
		out.Blind = true
	}
	c.landingDiags = append(c.landingDiags, Diagnostic{
		Kind:      kind,
		Detail:    detail,
		FramePath: framePathFor(frameID),
	})
}

// dispatchMouseClick 是 DispatchMouseClick 的实现，额外把落点判定的事实交出来。
func (c *Client) dispatchMouseClick(x, y float64) (MouseClickOutcome, error) {
	var out MouseClickOutcome

	// ⚠️ MouseMoved 先发：判据窗口是「按下之前那一刻 → 按下之后」，
	// 悬停引起的重渲染不算在内（见 domHit 上面那段 ⚠️）。
	if err := c.dispatchMouseEvent(input.MouseMoved, x, y); err != nil {
		return out, err
	}

	before, beforeOK := c.hitTestAt(x, y)

	if err := c.dispatchMouseEvent(input.MousePressed, x, y); err != nil {
		return out, err
	}

	after, afterOK := c.hitTestAt(x, y)

	// ① 判不出落点：不扣 + 说清楚（判据这次是瞎的）。
	if !beforeOK || !afterOK {
		c.noteLanding(&out, DiagKindLandingBlind, "落点判据这次没跑成：那个坐标上取不到节点（点可能在视口外）—— "+
			"按老行为把抬起发出去，这一次没有「落点变了就不交给新落点」这层保护", "")
		return out, c.dispatchMouseEvent(input.MouseReleased, x, y)
	}

	// ② 命中**停在 `<iframe>`/`<frame>` 上**：判据在这一点不可用。
	//
	// 跨站子帧时浏览器不下钻（实测：父页 127.0.0.1 嵌 gowizard 的跨站子帧，
	// 主帧坐标命中 `IFRAME` 且 frame = 父页），于是前后两次问到的都是同一个
	// `<iframe>` —— 判据**恒不触发**。这是沉默的空转，必须说出来。
	//
	// ⚠️ 这次 describeNode 是**每次点击都要付**的（与上面「只在罕见路径上调」不同 ——
	// 复审点名过注释与调用位不符）。
	//
	// ⚠️ 曾经想省掉它：先问一次「这一页有没有子帧」，没有就跳过 —— 判据是
	// `Page.getFrameTree` 的 ChildFrames。**那是错的，而且错在正好要治的那一格上**：
	// 跨站子帧（OOPIF）**不出现在父页会话的帧树里**（实测：主帧 URL 带
	// `?src=http://localhost:37311/inner.html` 的跨站 iframe，`len(ChildFrames) == 0`）——
	// 于是快路在**唯一需要它**的场景下恒判「没有子帧」，盲区诊断一声不吭。
	// 「省一次往返」换来的是「诊断在最该响的地方不响」，不值。这次往返就是
	// 「盲区必须能听见」的价钱。
	if bf, err := c.hitFactsOf(before); err == nil && (bf.Tag == "iframe" || bf.Tag == "frame") {
		c.noteLanding(&out, DiagKindLandingBlind, fmt.Sprintf("落点判据在这一点不可用：命中栈停在 %s 上（没有下钻到子帧内部）。"+
			"跨站子帧就是这种 —— 跨站时 DOM.getNodeForLocation 只返回父页的 iframe 元素，"+
			"判据在子帧内部**恒不触发**（同 site 跨 origin 才会下钻）。这一帧里的点击没有这层保护", bf.Desc), string(before.FrameID))
		return out, c.dispatchMouseEvent(input.MouseReleased, x, y)
	}

	// ③ 落点没变 → 正常点击。
	if before == after {
		return out, c.dispatchMouseEvent(input.MouseReleased, x, y)
	}

	// ④ 落点变了 —— 但「换了节点」≠「有人盖上来」，扣下要**正向取证**。
	beforeFacts, bErr := c.hitFactsOf(before)
	afterFacts, aErr := c.hitFactsOf(after)
	if bErr == nil {
		out.LandedBefore = beforeFacts.Desc
	}
	if aErr == nil {
		out.LandedAfter = afterFacts.Desc
	}
	if bErr != nil || aErr != nil {
		c.noteLanding(&out, DiagKindLandingBlind, "落点判据这次没跑成：换了节点，但读不出前后两个节点的属性"+
			"（取证不全）—— 按老行为把抬起发出去，宁可不扣也不误扣", string(before.FrameID))
		return out, c.dispatchMouseEvent(input.MouseReleased, x, y)
	}

	conn, isDescendant, isAncestor, fErr := c.hitForensics(before, after)
	if fErr != nil {
		c.noteLanding(&out, DiagKindLandingBlind, "落点判据这次没跑成：换了节点，但问不出「原目标还在不在文档里」"+
			"（取证不全）—— 按老行为把抬起发出去，宁可不扣也不误扣", string(before.FrameID))
		return out, c.dispatchMouseEvent(input.MouseReleased, x, y)
	}

	// ⑤ **里面长出来** ≠ **外面盖上来**（2026-09-17 复审实测的那一格）。
	//
	// 浮层长在目标**自己的子树里**时，递出去的事件照样从目标身上过（冒泡），
	// 目标这一侧并没有被绕开 —— 扣下抬起却会把这一次点击**整根拿走**
	// （实测：裸行为 1/1 → 被测 0/0）。所以先问 ancestry：
	// 「覆盖者」是不是目标的祖先/后代（合成树，穿 shadow）。
	if isDescendant || isAncestor {
		// ⚠️ 两个方向的话**分开写**：后代那一向事件照样从目标身上过（冒泡），
		// 祖先那一向**不会**（目标自己在子树里，click 从祖先往上冒，目标收不到）——
		// 上一版把两向写成同一句「事件照样从目标身上过」，祖先那一向是**假话**
		// （复审实测 muTargetPath=0）。
		why := fmt.Sprintf("它**长在原目标自己的子树里**（原目标是它的祖先）：事件照样从目标身上过（冒泡），"+
			"目标那一侧没有被绕开 —— 变了的是目标自己那棵树内部，不是「外面盖上来」（原目标 %s）", beforeFacts.Desc)
		if isAncestor {
			why = fmt.Sprintf("它是原目标的**祖先**（原目标在它的子树里）：这是目标自己缩了/移开了，"+
				"不是「外面盖上来」；⚠️ 这一向事件**不会**从目标身上过（click 从祖先往上冒）—— "+
				"但变的同样是目标自己那棵树（原目标 %s）", beforeFacts.Desc)
		}
		c.noteLanding(&out, DiagKindLandingPassed, fmt.Sprintf("按下之后那个点上换成了 %s —— %s。"+
			"照常把抬起发出去", afterFacts.Desc, why), string(before.FrameID))
		return out, c.dispatchMouseEvent(input.MouseReleased, x, y)
	}

	// ⑥ 原目标自己被替换/摘除：变的是**目标自己**，不是「有人盖上来」。
	//
	// 实测（复审端到端复现 + 本机复验）：节点**重建**时 backendNodeId 会变，
	// 而那一刻**没有任何东西盖上来** —— 照直扣下会把一次正常点击静静吞掉。
	if !conn {
		c.noteLanding(&out, DiagKindLandingPassed, fmt.Sprintf("按下之后落点换了人，但**原目标自己"+
			"从文档里没了**（被替换/被摘除），不是有人盖上来 —— 照常把抬起发出去（%s → %s）",
			beforeFacts.Desc, afterFacts.Desc), string(before.FrameID))
		return out, c.dispatchMouseEvent(input.MouseReleased, x, y)
	}

	// ⑦ 标签与属性**逐字相同**的另一个节点。
	//
	// ⚠️ 这一格**判不出来**：逐字节重建与「一个逐字节相同的覆盖者」在现有证据下
	// 一模一样。按不对称原则倒向「不扣」（扣错 = 一次正常点击静静地消失）。
	// ⚠️ 话必须**这么说**（复审点名）：上一版这里写「是重建不是覆盖」——
	// 那是**在说假话**，因为覆盖者也能长这样。如实说「分不出」。
	if beforeFacts.Fingerprint == afterFacts.Fingerprint {
		c.noteLanding(&out, DiagKindLandingPassed, fmt.Sprintf("按下之后落点换成了 %s —— 它与原目标"+
			"**标签与属性逐字相同**。判据**分不出**这是「同一个东西被重建」还是「一个逐字节相同的"+
			"覆盖者」，按老行为把抬起发出去（宁可漏扣，也不误扣一次正常点击）", afterFacts.Desc), string(before.FrameID))
		return out, c.dispatchMouseEvent(input.MouseReleased, x, y)
	}

	// ⑧ 取证齐了：**原目标还在文档里**（它只是被盖住）、盖上来的是**外面**的东西、
	// 而且它与原目标不是同一个东西 —— 这正是「别人盖上来」。扣下。
	out.ReleaseWithheld = true
	c.noteLanding(&out, DiagKindLandingWithheld, fmt.Sprintf("**抬起已扣下**：按下之后落点换成了 %s"+
		"（原目标 %s 还在文档里、也不是它的重建）—— 这一次 mouseup 没有发出去",
		afterFacts.Desc, beforeFacts.Desc), string(before.FrameID))
	return out, nil
}

// dispatchMouseEvent 发一条鼠标事件（按下/抬起带左键与 clickCount=1）。
func (c *Client) dispatchMouseEvent(typ input.MouseType, x, y float64) error {
	return chromedp.Run(c.ctx, chromedp.ActionFunc(func(ctx context.Context) error {
		cc := chromedp.FromContext(ctx)
		if cc == nil || cc.Target == nil {
			return fmt.Errorf("invalid context")
		}
		exec := cdp.WithExecutor(ctx, cc.Target)
		ev := input.DispatchMouseEvent(typ, x, y)
		if typ != input.MouseMoved {
			ev = ev.WithButton(input.Left).WithClickCount(1)
		}
		return ev.Do(exec)
	}))
}

// DispatchTouchClick dispatches touchStart then touchEnd at (x, y).
func (c *Client) DispatchTouchClick(x, y float64) error {
	if err := c.DispatchTouchEvent("touchStart", []TouchPoint{DefaultTouchPoint(x, y)}); err != nil {
		return fmt.Errorf("touchStart failed: %w", err)
	}
	return c.DispatchTouchEvent("touchEnd", []TouchPoint{DefaultTouchPoint(x, y)})
}

// ScrollMouseWheel scrolls towards element using mouse wheel
func (c *Client) ScrollMouseWheel(selector string) error {
	center, err := c.GetElementCenter(selector, "")
	if err != nil {
		return err
	}

	vpWidth, vpHeight, err := c.getViewportDimensions()
	if err != nil {
		return err
	}

	centerY := center["centerY"]

	// 计算元素相对于 viewport 中心的垂直偏移
	dy := centerY - vpHeight/2

	// 只做垂直滚动，步长 100±6（带随机偏移模拟人手）
	var stepY float64
	if math.Abs(dy) > 10 {
		stepY = math.Copysign(100+mathrand.Float64()*6, dy)
	}

	var mouseX, mouseY float64
	if dy > 0 {
		// 元素在 viewport 中心下面 → 向下滚动，鼠标在上方区域
		mouseX = vpWidth*0.75 + (mathrand.Float64()-0.5)*10
		mouseY = vpHeight*0.3 + (mathrand.Float64()-0.5)*10
	} else {
		// 元素在 viewport 中心上面 → 向上滚动，鼠标在下方区域
		mouseX = vpWidth*0.75 + (mathrand.Float64()-0.5)*10
		mouseY = vpHeight*0.66 + (mathrand.Float64()-0.5)*10
	}
	return c.DispatchMouseScrollEventAt(mouseX, mouseY, 0, stepY)
}

// ScrollToElement scrolls the element into viewport using human-like gestures.
// Performs up to 20 iterations of touch/mouse scrolling until element is visible.
func (c *Client) ScrollToElement(selector string, track bool) error {
	isTouch, err := c.IsTouchDevice()
	if err != nil {
		return fmt.Errorf("failed to detect device type: %w", err)
	}

	for i := 0; i < 20; i++ {
		if i > 0 {
			delay := time.Duration(200+mathrand.Intn(400)) * time.Millisecond
			time.Sleep(delay)
		}

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

// ClickElement 拟人点击一个元素（**宽松**：老行为，一个字节都不改）。
//
// 1. Scrolls element into viewport
// 2. Calculates center coordinates (with iframe offset if needed)
// 3. Applies Gaussian offset for natural click position
// 4. Dispatches touch or mouse click event
//
// 仍然取文档序第一个匹配、仍然不因为目标禁用而硬失败 —— 57 个生产脚本靠这条。
// 它**不再静默**：回执里带上「命中几个 / 点的是第几个 / 它禁没禁用」（见 ClickResult）。
func (c *Client) ClickElement(selector, frameID string, track bool) (*ClickResult, error) {
	return c.clickAndReport(selector, frameID, track, false)
}

// ClickElementStrict 是**严格**那一版：歧义选择器与禁用的目标一律当场失败，
// 且拒绝发生在动作**之前**（页面上不会留下任何点击痕迹）。
//
// MCP 那道门默认走它（agent 是必须被逼着说准的调用方；生产脚本不走这条路）。
// 判据与文案见 internal/click.go 的 strictClickRefusal。
func (c *Client) ClickElementStrict(selector, frameID string, track bool) (*ClickResult, error) {
	return c.clickAndReport(selector, frameID, track, true)
}

// clickAndReport 是两条门共用的那一份实现（strict 只改**判据**，不改流程）。
func (c *Client) clickAndReport(selector, frameID string, track bool, strict bool) (*ClickResult, error) {
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

	// Wait for scrollSelector position to stabilize
	if err := c.WaitForPositionStable(scrollSelector, ""); err != nil {
		return nil, fmt.Errorf("position not stable: %w", err)
	}

	// 目标探测：这一刻会点到谁（命中几个 / 第几个 / 禁没禁用）。
	//
	// ⚠️ 位置是刻意的（滚动之后、算坐标之前，见 internal/click.go 文件头）：
	// 报出来的数必须与**马上要发出去的那一下**对得上。
	result := &ClickResult{}
	probe, probeErr := c.probeClickTarget(selector, frameID)
	switch {
	case probeErr != nil:
		// 探测没跑成时**不拦**宽松路径：能走到这里说明同一套求值刚刚还工作
		// （WaitForPositionStable 一路都在用），真正没探到的后果是回执里少几个
		// 字段 —— 而探测的失败原因会照实记进 ProbeError（不许看起来像「命中 0 个」）。
		// 严格路径则必须拦：这道门的意义就是「看不清就别点」。
		result.ProbeError = probeErr.Error()
		if strict {
			return nil, fmt.Errorf("无法确认选择器 %q 会点到哪个元素（%v）—— 这道门要求先看清目标再动手",
				selector, probeErr)
		}
	default:
		result.Probe = probe
		if chosen, ok := probe.Chosen(); ok {
			result.MatchCount = probe.MatchCount
			result.MatchIndex = probe.MatchIndex
			result.Target = chosen.Describe()
			result.TargetDisabled = chosen.Disabled
		}
		if strict {
			if err := strictClickRefusal(selector, probe); err != nil {
				return nil, err
			}
		}
	}

	// Calculate click coordinates
	var clickX, clickY, elemWidth, elemHeight float64
	var elemLeft, elemTop float64
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
		elemLeft = iframeRect["x"] + elemRect["x"]
		elemTop = iframeRect["y"] + elemRect["y"]
	} else {
		rect, err := c.GetElementCenter(selector, "")
		if err != nil {
			return nil, fmt.Errorf("failed to get element position: %w", err)
		}
		clickX = rect["centerX"]
		clickY = rect["centerY"]
		elemWidth = rect["width"]
		elemHeight = rect["height"]
		elemLeft = rect["x"]
		elemTop = rect["y"]
	}

	// Apply Gaussian offset, then clamp to stay within element bounds.
	// Small buttons can be missed entirely by the offset (e.g. a 35px-tall
	// button with sigma=8.8 can get +20px dy, landing outside the element).
	sigma := math.Min(elemWidth, elemHeight) / 4
	dx, dy := GaussianOffset(sigma)
	clickX += dx
	clickY += dy

	// Clamp: keep 1px margin from edges to avoid border/rounding issues.
	if clickX < elemLeft+1 {
		clickX = elemLeft + 1
	}
	if clickX > elemLeft+elemWidth-1 {
		clickX = elemLeft + elemWidth - 1
	}
	if clickY < elemTop+1 {
		clickY = elemTop + 1
	}
	if clickY > elemTop+elemHeight-1 {
		clickY = elemTop + elemHeight - 1
	}

	// Reject hidden elements (display:none or 0×0). Clicking them wastes time
	// and the hit-test lands at (0,0), triggering nothing useful.
	if elemWidth == 0 || elemHeight == 0 {
		return nil, fmt.Errorf("element %q is not visible (size 0×0)", selector)
	}

	// Always use mouse click - CDP dispatchTouchEvent does not synthesize
	// click events, so touch-only dispatch breaks <a> navigation and most
	// click handlers. Mouse events work on all device types via CDP.
	//
	// ⚠️ 走 dispatchMouseClick（不是 DispatchMouseClick）：这里要拿到「抬起交没交给
	// 新落点」那份事实，填进回执。少了它，「这次点击只发出了按下的那一半」在回执里
	// 一个字都没有 —— 而那正是本项目最贵的一类失败（看起来成功了）。
	outcome, err := c.dispatchMouseClick(clickX, clickY)
	if err != nil {
		return nil, fmt.Errorf("click failed: %w", err)
	}
	result.ReleaseWithheld = outcome.ReleaseWithheld
	if outcome.ReleaseWithheld {
		result.CoveredBy = outcome.LandedAfter
	}
	result.LandingBlind = outcome.Blind
	result.LandingNote = outcome.Note

	result.X = clickX
	result.Y = clickY
	return result, nil
}

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
