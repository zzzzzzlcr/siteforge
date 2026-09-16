package internal

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/chromedp/cdproto/target"
	"github.com/chromedp/chromedp"
)

type PageTarget struct {
	ID     string `json:"id"`
	Title  string `json:"title"`
	URL    string `json:"url"`
	Active *bool  `json:"active,omitempty"`
}

type listEntry struct {
	ID    string `json:"id"`
	Title string `json:"title"`
	URL   string `json:"url"`
	Type  string `json:"type"`
}

var httpClient = &http.Client{Timeout: 10 * time.Second}

// getWSDebugURL fetches the browser-level WebSocket debug URL.
func getWSDebugURL(host string, port int) (string, error) {
	resp, err := httpClient.Get(fmt.Sprintf("http://%s:%d/json/version", host, port))
	if err != nil {
		return "", fmt.Errorf("failed to connect to Chrome: %w", err)
	}
	defer resp.Body.Close()

	var version struct {
		WebSocketDebuggerURL string `json:"webSocketDebuggerUrl"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&version); err != nil {
		return "", fmt.Errorf("failed to parse version response: %w", err)
	}
	return version.WebSocketDebuggerURL, nil
}

// getPageTargets fetches and filters page-type targets from Chrome.
func getPageTargets(host string, port int) ([]PageTarget, error) {
	resp, err := httpClient.Get(fmt.Sprintf("http://%s:%d/json/list", host, port))
	if err != nil {
		return nil, fmt.Errorf("failed to list targets: %w", err)
	}
	defer resp.Body.Close()

	var entries []listEntry
	if err := json.NewDecoder(resp.Body).Decode(&entries); err != nil {
		return nil, fmt.Errorf("failed to parse targets response: %w", err)
	}
	return filterPageTargets(entries), nil
}

// filterPageTargets filters entries to type=page, excluding devtools:// and empty URLs
func filterPageTargets(entries []listEntry) []PageTarget {
	pages := make([]PageTarget, 0, len(entries))
	for _, e := range entries {
		if e.Type == "page" && e.URL != "" && !strings.HasPrefix(e.URL, "devtools://") {
			pages = append(pages, PageTarget{
				ID:    e.ID,
				Title: e.Title,
				URL:   e.URL,
			})
		}
	}
	return pages
}

// checkPageActive connects to a page target and checks if it's the active tab.
// allocCtx should be from a chromedp browser-level allocator, reused across pages.
func checkPageActive(allocCtx context.Context, targetID string) (bool, error) {
	ctx, cancel := chromedp.NewContext(allocCtx, chromedp.WithTargetID(target.ID(targetID)))
	defer func() {
		// Prevent CloseTarget: chromedp's cancel calls Target.closeTarget on any
		// target whose TargetID is set. Clearing it makes cancel only detach.
		if c := chromedp.FromContext(ctx); c != nil && c.Target != nil {
			c.Target.TargetID = ""
		}
		cancel()
	}()

	timeoutCtx, timeoutCancel := context.WithTimeout(ctx, 5*time.Second)
	defer timeoutCancel()

	var state string
	err := chromedp.Run(timeoutCtx, chromedp.Evaluate(`document.visibilityState`, &state))
	if err != nil {
		return false, fmt.Errorf("failed to check visibilityState for %s: %w", targetID, err)
	}
	return state == "visible", nil
}

// ActivateTarget brings the specified page target to the foreground.
func ActivateTarget(host string, port int, targetID string) error {
	wsURL, err := getWSDebugURL(host, port)
	if err != nil {
		return err
	}

	allocCtx, allocCancel := chromedp.NewRemoteAllocator(context.Background(), wsURL)
	defer allocCancel()

	ctx, cancel := chromedp.NewContext(allocCtx)
	defer cancel()

	timeoutCtx, timeoutCancel := context.WithTimeout(ctx, 10*time.Second)
	defer timeoutCancel()

	return chromedp.Run(timeoutCtx,
		chromedp.ActionFunc(func(ctx context.Context) error {
			return target.ActivateTarget(target.ID(targetID)).Do(ctx)
		}),
	)
}

// TargetChoice 是一次「从所有页面目标里挑一个来观察」的结果。
//
// ⚠️ 为什么不能只回一个 ID（2026-09-16 真站实测，这就是本类型存在的理由）：
// 调用方拿到一个裸 ID 时，分不清下面两件事 ——
//
//	① 这个页真的报了 visible（挑中了就是它）
//	② 一个报 visible 的都没有，ID 是「退回第一个」猜出来的
//
// ② 的后果不是报错，而是**静默观测错页**：cdp observe 会返回一份完全合法、
// 字段齐全、退出码 0 的页面模型，只是那模型说的是另一个页（实测：两份 page 目标
// 都返回 Active == nil —— checkPageActive 超时 —— 于是退回了 pages[0]，
// 那是个夹具页而不是待测页）。唯一的线索是 PageModel.url，而没有任何东西
// 强制调用方去看它。target=_blank 点开的新标签页拿不到 visible 时就会踩中。
type TargetChoice struct {
	// ID 是挑中的页面目标（把 ID 当 frameID 用也是对的：顶层 targetId == frameId）。
	ID string
	// URL 是挑中目标此刻的 URL —— 给人看「是不是挑错了」用（诊断里要带上它）。
	URL string
	// Candidates 是候选页面目标数（type=page、URL 非空、非 devtools://）。
	Candidates int
	// Fallback 为 true 表示**没有任何**候选报告 visible，ID 是退回第一个挑出来的：
	// 它多半是对的（见 pickActivePage），但那是猜的，不是看到的事实。
	Fallback bool
}

// ChooseActivePageTarget 挑一个页面目标来观察/操作，并如实汇报这次挑得可不可靠。
//
// 取代了原先那个只回 ID 的 GetActivePageTargetID —— 改名是刻意的：**旧名字会说谎**
// （它回的已经不止是 ID），而改名能让编译器指出每一处调用点，不给「继续无视这次
// 是猜的」留任何静默的位置。
func ChooseActivePageTarget(host string, port int) (TargetChoice, error) {
	pages, err := ListPageTargets(host, port, true)
	if err != nil {
		return TargetChoice{}, err
	}
	return pickActivePage(pages)
}

// pickActivePage 是挑选逻辑本身：不碰浏览器，所以能直接测（见 targets_test.go）。
//
// 判据只有一条：**谁报了 visible 就用谁**。一个都没有，说明这次挑不出来 ——
// 不是「没有页面（那种情况 ListPageTargets 给的是空列表）」，而是「所有候选都没报
// visible」：可能全是后台页，也可能是 checkPageActive 求值失败/超时（页面的主线程
// 被堵住时就是这个下场）。这时退回第一个，但把 Fallback 标出来，让上层把这件事
// 说出去。
//
// ⚠️ 退回的「第一个」不是随便挑的：/json/list 的顺序近似 MRU（最近激活的在前），
// 所以第一个通常就是「刚才那个前台页」—— **多数情况下它是对的**。但那是个猜测，
// 不是观测到的事实；这次修正要传出去的恰恰是这个区别。
func pickActivePage(pages []PageTarget) (TargetChoice, error) {
	if len(pages) == 0 {
		return TargetChoice{}, fmt.Errorf("no page target found")
	}
	for _, p := range pages {
		if p.Active != nil && *p.Active {
			return TargetChoice{ID: p.ID, URL: p.URL, Candidates: len(pages)}, nil
		}
	}
	return TargetChoice{ID: pages[0].ID, URL: pages[0].URL, Candidates: len(pages), Fallback: true}, nil
}

// ListPageTargets returns all page-type targets from Chrome.
// When detectActive is true, each page is tested for active state.
func ListPageTargets(host string, port int, detectActive bool) ([]PageTarget, error) {
	pages, err := getPageTargets(host, port)
	if err != nil {
		return nil, err
	}

	if detectActive {
		wsURL, err := getWSDebugURL(host, port)
		if err != nil {
			return nil, err
		}

		allocCtx, allocCancel := chromedp.NewRemoteAllocator(context.Background(), wsURL)
		defer allocCancel()

		for i := range pages {
			active, err := checkPageActive(allocCtx, pages[i].ID)
			if err == nil {
				pages[i].Active = &active
			}
		}
	}

	return pages, nil
}

// CloseTarget closes a page target by its ID using Chrome's HTTP API.
// Uses GET /json/close/{id} to bypass chromedp's context management, which
// would otherwise refuse to close targets with active sessions.
func CloseTarget(host string, port int, targetID string) error {
	url := fmt.Sprintf("http://%s:%d/json/close/%s", host, port, targetID)
	req, err := http.NewRequest("GET", url, nil)
	if err != nil {
		return fmt.Errorf("failed to create close request: %w", err)
	}

	resp, err := httpClient.Do(req)
	if err != nil {
		return fmt.Errorf("failed to close target %s: %w", targetID, err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		body, readErr := io.ReadAll(resp.Body)
		if readErr != nil {
			return fmt.Errorf("failed to close target %s (status %d, read error: %v)", targetID, resp.StatusCode, readErr)
		}
		return fmt.Errorf("failed to close target %s (status %d): %s", targetID, resp.StatusCode, string(body))
	}

	return nil
}
