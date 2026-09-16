//go:build integration

package internal

import (
	"testing"
)

func TestCloseTarget(t *testing.T) {
	host := "127.0.0.1"
	port := 9222

	pagesBefore, err := ListPageTargets(host, port, false)
	if err != nil {
		t.Fatalf("ListPageTargets failed: %v", err)
	}
	if len(pagesBefore) < 2 {
		t.Skip("需要至少 2 个 page target，请打开多个 tab 后重试")
	}

	activeID, err := GetActivePageTargetID(host, port)
	if err != nil {
		t.Fatalf("GetActivePageTargetID failed: %v", err)
	}

	var targetToClose string
	for _, p := range pagesBefore {
		if p.ID != activeID {
			targetToClose = p.ID
			break
		}
	}
	if targetToClose == "" {
		t.Skip("没有非活跃页面可供关闭")
	}

	if err := CloseTarget(host, port, targetToClose); err != nil {
		t.Fatalf("CloseTarget failed: %v", err)
	}

	pagesAfter, err := ListPageTargets(host, port, false)
	if err != nil {
		t.Fatalf("ListPageTargets after close failed: %v", err)
	}
	if len(pagesAfter) >= len(pagesBefore) {
		t.Errorf("关闭后页面数未减少: before=%d, after=%d", len(pagesBefore), len(pagesAfter))
	}
	for _, p := range pagesAfter {
		if p.ID == targetToClose {
			t.Errorf("已关闭的 target %s 仍在页面列表中", targetToClose)
		}
	}
}

func TestCloseNonExistentTarget(t *testing.T) {
	host := "127.0.0.1"
	port := 9222

	err := CloseTarget(host, port, "non-existent-target-id")
	if err == nil {
		t.Error("关闭不存在的 target 应返回错误")
	}
}
