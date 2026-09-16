package internal

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	mathrand "math/rand"
	"net"
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

// Navigate navigates the current page (or a specific frame) to the given URL,
// waits for the load event, then returns the updated frame tree.
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
		return nil
	}))
	if err != nil {
		return nil, fmt.Errorf("failed to navigate to %s: %w", url, err)
	}

	select {
	case <-loadCh:
	case <-time.After(30 * time.Second):
		return nil, fmt.Errorf("timeout waiting for page load (30s)")
	}

	return c.GetFrameTree()
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

// ResolveIframeSelector finds a selector for an iframe by frameID.
// Returns "__iframe:N" where N is the index in frame tree's ChildFrames.
// JS methods detect this prefix and use querySelectorAll('iframe')[N] for reliable selection.
func (c *Client) ResolveIframeSelector(frameID string) (string, error) {
	ft, err := c.GetFrameTreeWithEvents(3 * time.Second)
	if err != nil {
		return "", fmt.Errorf("failed to get frame tree: %w", err)
	}
	for i, child := range ft.ChildFrames {
		if child == nil {
			continue
		}
		if string(child.Frame.ID) == frameID {
			return fmt.Sprintf("__iframe:%d", i), nil
		}
	}
	return "", fmt.Errorf("failed to find iframe %s", frameID)
}

// GetElementCenter returns element center coordinates
func (c *Client) GetElementCenter(selector, frameId string) (map[string]float64, error) {
	js := withPierce(fmt.Sprintf(`(function(){
		var el;
		if ('%[1]s'.startsWith('__iframe:')) {
			var idx = parseInt('%[1]s'.split(':')[1]);
			el = __cdpQA('iframe')[idx];
		} else {
			el = __cdpQ('%[1]s');
		}
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
	js := withPierce(fmt.Sprintf(`(function(){
		var el;
		if ('%[1]s'.startsWith('__iframe:')) {
			var idx = parseInt('%[1]s'.split(':')[1]);
			el = __cdpQA('iframe')[idx];
		} else {
			el = __cdpQ('%[1]s');
		}
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
//	① 按下**之前**问一次：这个坐标上是哪个节点（frame + backendNodeId）
//	② 发 MouseMoved + MousePressed（mousedown 的处理器在这里跑完，DOM 已经变了）
//	③ 按下**之后**再问一次
//	④ 两次是同一个节点 → 照常发 MouseReleased（正常点击，57 个生产脚本的路径）
//	   换了人 → **扣下这次 released**：既不发给新落点，也不让浏览器把这次按下-抬起
//	   合成为一个落在新元素上的 click（真站实测：那一下的 mouseup 落在新出现的
//	   菜单项/backdrop 上，click 落在两者的公共祖先上 —— 两条都是「交给新落点」）
//
// 为什么用 `DOM.getNodeForLocation` 而不是自写 JS 命中测试：
// 它就是**真实输入走的那条代码路**（同一套 HitTestResult），并且**穿 shadow DOM**
// （`document.elementFromPoint` 返回的是 host，shadow 里的覆盖物它看不见 ——
// cdp 全部力气都花在穿透上，这里不能瞎）。
//
// ⚠️ 判不出来时（点不在视口里、协议报错）**按老行为走**（不扣）：扣下抬起是
// 「不把点击交给一个可疑的新落点」的保护，判不出来时凭据不足，不额外拿走一次点击。
// 这条路径**不静默**：返回值里带着前后两次的落点描述（见 MouseClickOutcome）。
type domHit struct {
	FrameID   cdp.FrameID
	BackendID cdp.BackendNodeID
}

// Empty 说这次命中测试**没拿到东西**（而不是「拿到一个空节点」）。
func (h domHit) Empty() bool { return h.BackendID == 0 && h.FrameID == "" }

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

// describeHit 把一次命中印成一行人话（`<div class="MuiBackdrop-root …">`）。
//
// 只在与「按下前」不同时才调（见 dispatchMouseClick）—— 那是罕见路径，
// 多一次往返换「覆盖者是谁」这个**能查下去**的事实，值。
// 取不到描述不算失败：交空串，落点身份本身已经在 MouseClickOutcome 里。
func (c *Client) describeHit(h domHit) string {
	var out string
	_ = chromedp.Run(c.ctx, chromedp.ActionFunc(func(ctx context.Context) error {
		cc := chromedp.FromContext(ctx)
		if cc == nil || cc.Target == nil {
			return fmt.Errorf("invalid context")
		}
		exec := cdp.WithExecutor(ctx, cc.Target)
		n, err := dom.DescribeNode().WithBackendNodeID(h.BackendID).Do(exec)
		if err != nil || n == nil {
			return err
		}
		var b strings.Builder
		b.WriteString("<")
		b.WriteString(strings.ToLower(n.NodeName))
		for i := 0; i+1 < len(n.Attributes); i += 2 {
			k, v := n.Attributes[i], n.Attributes[i+1]
			if k != "id" && k != "class" && k != "role" {
				continue
			}
			fmt.Fprintf(&b, " %s=%q", k, capRunes(v, 40))
		}
		b.WriteString(">")
		out = b.String()
		return nil
	}))
	return out
}

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
}

// DispatchMouseClick dispatches mouse move, press, and release at (x, y) —
// 但**按下与抬起之间落点换了人时，抬起不发**（理由见 domHit 上面那一大段）。
//
// 回执（落点前后是谁、扣没扣）走 dispatchMouseClick；这个入口只报错，
// 因为 57 个生产脚本与 form 侧的动作全都只关心「成没成」。
func (c *Client) DispatchMouseClick(x, y float64) error {
	_, err := c.dispatchMouseClick(x, y)
	return err
}

// dispatchMouseClick 是 DispatchMouseClick 的实现，额外把落点判定的事实交出来。
func (c *Client) dispatchMouseClick(x, y float64) (MouseClickOutcome, error) {
	var out MouseClickOutcome

	before, beforeOK := c.hitTestAt(x, y)

	if err := c.dispatchMouseEvent(input.MouseMoved, x, y); err != nil {
		return out, err
	}
	if err := c.dispatchMouseEvent(input.MousePressed, x, y); err != nil {
		return out, err
	}

	after, afterOK := c.hitTestAt(x, y)
	if beforeOK && afterOK && before != after {
		out.ReleaseWithheld = true
		out.LandedBefore = c.describeHit(before)
		out.LandedAfter = c.describeHit(after)
		return out, nil
	}
	return out, c.dispatchMouseEvent(input.MouseReleased, x, y)
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
