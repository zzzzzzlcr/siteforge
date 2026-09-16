package mcp

import (
	"fmt"
	"net"
	"strings"
	"testing"
	"time"
)

// TestDialNamesTheHostAndPortWhenTheWindowIsGone 钉住那条「窗口没了要**立刻、明确**
// 地报错」的契约（规格 §4.6 / 风险 P6）。
//
// Bit 窗口的存活只有几分钟。窗口没了以后，调用方（agent）只有两件事能做：
// 重开一个窗口，或者报告卡住。**它靠报错里的 host:port 决定重开哪个** ——
// 报错里没有这个串，它就只能瞎猜或者卡住。
//
// ⚠️ 两条都要：名字要对，而且**要快**。一个挂住的工具调用在 agent 那边呈现为
// 「模型没反应」，而不是「窗口没了」—— 最坏的一种失败形态。
func TestDialNamesTheHostAndPortWhenTheWindowIsGone(t *testing.T) {
	port := deadPort(t)
	addr := fmt.Sprintf("127.0.0.1:%d", port)

	start := time.Now()
	_, release, err := Connector{Target: Target{Host: "127.0.0.1", Port: port}}.Dial()
	elapsed := time.Since(start)

	if err == nil {
		release()
		t.Fatalf("对着没人监听的 %s 居然连上了", addr)
	}
	if !strings.Contains(err.Error(), addr) {
		t.Errorf("报错里没有点名 %s —— 调用方无法判断该重开哪个窗口:\n%v", addr, err)
	}
	if release != nil {
		t.Error("连不上却给了个释放函数")
	}
	if elapsed > 15*time.Second {
		t.Errorf("连不上花了 %v —— 窗口没了必须立刻报错，不许挂着", elapsed)
	}
}

// deadPort 返回一个**刚刚**没人监听的端口：起一个 listener 拿到系统分配的端口，
// 立刻关掉。比写死一个数字可靠 —— 写死的那个迟早会被别的进程占上，
// 于是这条测试变成「有时连得上」。
func deadPort(t *testing.T) int {
	t.Helper()
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("起 listener 失败: %v", err)
	}
	port := l.Addr().(*net.TCPAddr).Port
	if err := l.Close(); err != nil {
		t.Fatalf("关 listener 失败: %v", err)
	}
	return port
}
