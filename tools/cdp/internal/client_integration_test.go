package internal

import (
	"os"
	"testing"
)

// TestDisconnect_PageSurvives verifies that Disconnect() gracefully tears down
// the WebSocket connection WITHOUT closing the Chrome page. This is the key
// difference between Disconnect() and Close().
//
// Requires: Chrome with --remote-debugging-port=9222 and at least one open page.
func TestDisconnect_PageSurvives(t *testing.T) {
	host := os.Getenv("CDP_HOST")
	if host == "" {
		host = "127.0.0.1"
	}
	port := 9222

	// Get the active page before connecting
	pagesBefore, err := ListPageTargets(host, port, false)
	if err != nil {
		t.Skipf("Chrome not available: %v", err)
	}
	if len(pagesBefore) == 0 {
		t.Skip("no page targets available")
	}
	activeBefore := pagesBefore[0].ID

	// Create client and immediately disconnect
	client, err := NewClient(host, port)
	if err != nil {
		t.Fatalf("NewClient failed: %v", err)
	}
	client.Disconnect()

	// Verify the page still exists
	pagesAfter, err := ListPageTargets(host, port, false)
	if err != nil {
		t.Fatalf("ListPageTargets after Disconnect failed: %v", err)
	}

	found := false
	for _, p := range pagesAfter {
		if p.ID == activeBefore {
			found = true
			break
		}
	}
	if !found {
		t.Errorf("page %s was closed by Disconnect() — TargetID was not cleared before cancel", activeBefore)
	}
}

// TestDisconnect_ContextCancelled verifies that after Disconnect(), the client's
// context is cancelled, preventing further CDP operations.
func TestDisconnect_ContextCancelled(t *testing.T) {
	host := os.Getenv("CDP_HOST")
	if host == "" {
		host = "127.0.0.1"
	}
	port := 9222

	client, err := NewClient(host, port)
	if err != nil {
		t.Skipf("Chrome not available: %v", err)
	}
	client.Disconnect()

	// After Disconnect, the context should be cancelled
	select {
	case <-client.ctx.Done():
		// Expected: context is cancelled
	default:
		t.Error("context should be cancelled after Disconnect()")
	}
}
