//go:build integration

package internal

import (
	"testing"
)

func TestListPageTargets(t *testing.T) {
	pages, err := ListPageTargets("127.0.0.1", 9222, false)
	if err != nil {
		t.Fatalf("ListPageTargets failed: %v", err)
	}
	if len(pages) == 0 {
		t.Error("expected at least one page target")
	}
	for _, p := range pages {
		if p.ID == "" {
			t.Error("page ID is empty")
		}
		if p.URL == "" {
			t.Error("page URL is empty")
		}
		if p.Active != nil {
			t.Error("Active should be nil when detectActive=false")
		}
	}
}

func TestListPageTargetsWithActive(t *testing.T) {
	pages, err := ListPageTargets("127.0.0.1", 9222, true)
	if err != nil {
		t.Fatalf("ListPageTargets with detectActive failed: %v", err)
	}
	if len(pages) == 0 {
		t.Error("expected at least one page target")
	}
	activeCount := 0
	for _, p := range pages {
		if p.ID == "" {
			t.Error("page ID is empty")
		}
		if p.Active == nil {
			t.Error("Active should not be nil when detectActive=true")
		} else if *p.Active {
			activeCount++
		}
	}
	if activeCount == 0 {
		t.Error("expected at least one active page")
	}
}
