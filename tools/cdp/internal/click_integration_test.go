package internal

import (
	"os"
	"testing"
)

// TestDispatchMouseClick_FiresOnClick verifies that DispatchMouseClick triggers
// an inline onclick handler on the target element.
//
// Requires: Chrome with --remote-debugging-port=9222 and at least one open page.
func TestDispatchMouseClick_FiresOnClick(t *testing.T) {
	host := os.Getenv("CDP_HOST")
	if host == "" {
		host = "127.0.0.1"
	}
	port := 9222

	pages, err := ListPageTargets(host, port, false)
	if err != nil || len(pages) == 0 {
		t.Skipf("Chrome not available: %v", err)
	}

	client, err := NewClient(host, port)
	if err != nil {
		t.Fatalf("NewClient failed: %v", err)
	}
	defer client.Disconnect()

	// Inject test page with inline onclick
	setupJS := `(function(){
		document.body.innerHTML = '<button id="test-btn" onclick="window.__clicked = true" style="position:fixed;top:100px;left:100px;width:200px;height:50px;">Click Me</button>';
		window.__clicked = false;
		return 'ok';
	})()`

	var setupResult string
	if err := client.EvalInFrame("", setupJS, &setupResult); err != nil {
		t.Fatalf("setup failed: %v", err)
	}

	// Get button center
	rect, err := client.GetElementCenter("#test-btn", "")
	if err != nil {
		t.Fatalf("GetElementCenter failed: %v", err)
	}

	// Click the button
	if err := client.DispatchMouseClick(rect["centerX"], rect["centerY"]); err != nil {
		t.Fatalf("DispatchMouseClick failed: %v", err)
	}

	// Verify onclick fired
	var clicked bool
	if err := client.EvalInFrame("", "window.__clicked", &clicked); err != nil {
		t.Fatalf("check onclick result failed: %v", err)
	}
	if !clicked {
		t.Error("DispatchMouseClick did not trigger onclick handler")
	}
}

// TestDispatchMouseClick_SpanOnClick verifies that DispatchMouseClick triggers
// an onclick on a non-interactive element (span), testing the broader click
// synthesis path.
func TestDispatchMouseClick_SpanOnClick(t *testing.T) {
	host := os.Getenv("CDP_HOST")
	if host == "" {
		host = "127.0.0.1"
	}
	port := 9222

	pages, err := ListPageTargets(host, port, false)
	if err != nil || len(pages) == 0 {
		t.Skipf("Chrome not available: %v", err)
	}

	client, err := NewClient(host, port)
	if err != nil {
		t.Fatalf("NewClient failed: %v", err)
	}
	defer client.Disconnect()

	setupJS := `(function(){
		document.body.innerHTML = '<span id="test-span" onclick="window.__spanClicked=true" style="position:fixed;top:100px;left:100px;width:200px;height:50px;display:inline-block;background:#eee;">Span</span>';
		window.__spanClicked = false;
		return 'ok';
	})()`

	var setupResult string
	if err := client.EvalInFrame("", setupJS, &setupResult); err != nil {
		t.Fatalf("setup failed: %v", err)
	}

	rect, err := client.GetElementCenter("#test-span", "")
	if err != nil {
		t.Fatalf("GetElementCenter failed: %v", err)
	}

	if err := client.DispatchMouseClick(rect["centerX"], rect["centerY"]); err != nil {
		t.Fatalf("DispatchMouseClick failed: %v", err)
	}

	var clicked bool
	if err := client.EvalInFrame("", "window.__spanClicked", &clicked); err != nil {
		t.Fatalf("check onclick result failed: %v", err)
	}
	if !clicked {
		t.Error("DispatchMouseClick did not trigger onclick on span element")
	}
}

// TestDispatchMouseClick_LinkOnClick verifies that clicking a link with an
// inline onclick+preventDefault triggers the handler without navigation.
func TestDispatchMouseClick_LinkOnClick(t *testing.T) {
	host := os.Getenv("CDP_HOST")
	if host == "" {
		host = "127.0.0.1"
	}
	port := 9222

	pages, err := ListPageTargets(host, port, false)
	if err != nil || len(pages) == 0 {
		t.Skipf("Chrome not available: %v", err)
	}

	client, err := NewClient(host, port)
	if err != nil {
		t.Fatalf("NewClient failed: %v", err)
	}
	defer client.Disconnect()

	setupJS := `(function(){
		document.body.innerHTML = '<a id="test-a" href="#" onclick="event.preventDefault();window.__aClicked=true" style="position:fixed;top:100px;left:100px;width:200px;height:50px;display:block;">Link</a>';
		window.__aClicked = false;
		return 'ok';
	})()`

	var setupResult string
	if err := client.EvalInFrame("", setupJS, &setupResult); err != nil {
		t.Fatalf("setup failed: %v", err)
	}

	rect, err := client.GetElementCenter("#test-a", "")
	if err != nil {
		t.Fatalf("GetElementCenter failed: %v", err)
	}

	if err := client.DispatchMouseClick(rect["centerX"], rect["centerY"]); err != nil {
		t.Fatalf("DispatchMouseClick failed: %v", err)
	}

	var clicked bool
	if err := client.EvalInFrame("", "window.__aClicked", &clicked); err != nil {
		t.Fatalf("check onclick result failed: %v", err)
	}
	if !clicked {
		t.Error("DispatchMouseClick did not trigger onclick on <a> element")
	}
}

// TestClickElement_SmallButtonOnClick verifies that the full ClickElement pipeline
// (scroll + wait + DispatchMouseClick with Gaussian offset clamped) reliably
// triggers onclick on a realistically-sized button.
func TestClickElement_SmallButtonOnClick(t *testing.T) {
	host := os.Getenv("CDP_HOST")
	if host == "" {
		host = "127.0.0.1"
	}
	port := 9222

	pages, err := ListPageTargets(host, port, false)
	if err != nil || len(pages) == 0 {
		t.Skipf("Chrome not available: %v", err)
	}

	client, err := NewClient(host, port)
	if err != nil {
		t.Fatalf("NewClient failed: %v", err)
	}
	defer client.Disconnect()

	// Simulate a real-world button: auto-sized with padding, long scrollable page.
	// Run 10 times to catch intermittent failures from unclamped Gaussian offset.
	for i := 0; i < 10; i++ {
		setupJS := `(function(){
			document.body.innerHTML = '<div style="height:2000px;"></div><button id="real-btn" onclick="window.__btnClicked=true" style="margin-top:100px;padding:8px 16px;">Next</button><div style="height:2000px;"></div>';
			window.__btnClicked = false;
			return 'ok';
		})()`

		var setupResult string
		if err := client.EvalInFrame("", setupJS, &setupResult); err != nil {
			t.Fatalf("iter %d: setup failed: %v", i, err)
		}

		_, err = client.ClickElement("#real-btn", "", false)
		if err != nil {
			t.Fatalf("iter %d: ClickElement failed: %v", i, err)
		}

		var clicked bool
		if err := client.EvalInFrame("", "window.__btnClicked", &clicked); err != nil {
			t.Fatalf("iter %d: check onclick failed: %v", i, err)
		}
		if !clicked {
			t.Errorf("iter %d: ClickElement did not trigger onclick (Gaussian offset pushed click outside element)", i)
			return // don't spam, one failure is enough
		}
	}
}
