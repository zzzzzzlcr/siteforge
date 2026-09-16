package internal

import (
	"os"
	"testing"
	"time"
)

// TestBatchCommands_NoHang simulates rapid consecutive client creation/disconnection
// to verify Chrome DevTools doesn't accumulate stale sessions.
//
// Requires: Chrome with --remote-debugging-port=9222 and at least one open page.
func TestBatchCommands_NoHang(t *testing.T) {
	host := os.Getenv("CDP_HOST")
	if host == "" {
		host = "127.0.0.1"
	}
	port := 9222

	// Verify Chrome is available
	pages, err := ListPageTargets(host, port, false)
	if err != nil || len(pages) == 0 {
		t.Skipf("Chrome not available: %v", err)
	}

	// Run 15 rapid iterations — enough to expose connection exhaustion if Disconnect
	// is not called. Each iteration creates a client, does a simple eval, and disconnects.
	for i := 0; i < 15; i++ {
		client, err := NewClient(host, port)
		if err != nil {
			t.Fatalf("iteration %d: NewClient failed: %v", i, err)
		}

		// Simple eval to verify the connection works
		var result string
		err = client.EvalInFrame("", "'hello'", &result)
		if err != nil {
			client.Disconnect()
			t.Fatalf("iteration %d: EvalInFrame failed: %v", i, err)
		}

		client.Disconnect()

		// Brief delay between iterations (simulates shell script pacing)
		time.Sleep(50 * time.Millisecond)
	}
}
