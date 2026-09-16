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

// GetActivePageTargetID finds the active page target and returns its ID.
// If no active page is found, returns the first available page target.
func GetActivePageTargetID(host string, port int) (string, error) {
	pages, err := ListPageTargets(host, port, true)
	if err != nil {
		return "", err
	}
	if len(pages) == 0 {
		return "", fmt.Errorf("no page target found")
	}

	for _, p := range pages {
		if p.Active != nil && *p.Active {
			return p.ID, nil
		}
	}
	return pages[0].ID, nil
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
