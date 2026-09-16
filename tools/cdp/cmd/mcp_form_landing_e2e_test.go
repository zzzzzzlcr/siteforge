package cmd

import (
	"encoding/json"
	"strconv"
	"strings"
	"testing"
)

// evalJSON 在私有浏览器上求值一次并把结果解成 map。
//
// ⚠️ 要**两层**解：cdp eval 的 stdout 是「JSON 值的 JSON 文本」
// （第一层解出来是一个字符串，第二层才是那个对象）—— 只解一层的话断言会去
// 匹配被转义过的引号（`\"listbox\":0`），看着永远不成立（实测踩过）。
func evalJSON(t *testing.T, cliBin string, port int, js string) map[string]any {
	t.Helper()
	var inner string
	if err := json.Unmarshal([]byte(evalOn(t, cliBin, port, js)), &inner); err != nil {
		t.Fatalf("eval 输出不是 JSON 字符串: %v", err)
	}
	var m map[string]any
	if err := json.Unmarshal([]byte(inner), &m); err != nil {
		t.Fatalf("求值结果解不开: %v（原文 %q）", err, inner)
	}
	return m
}

// N-1（2026-09-17 复审）：**落点判据说的话必须从这道门出来**。
//
// 复审实测：CLI 那条路 ✅（stderr 出那行、选项照常选中），而**走 MCP 门时 agent
// 拿到的是「填好了「X」」** —— 一个字节都没有。**agent 用的就是这道门，不是 CLI**，
// 所以「扣下抬起不静默」如果只在 CLI 上成立，对真正的消费方等于没做。
//
// 这条 e2e 走**真的门 + 真的浏览器**（不是桩）：桩只能证明 handler 把
// `LandingDiags()` 塞进了回执，证明不了真跑一遍时那份诊断**真的被攒下来** ——
// 而「攒下来了没有」正是这条缺陷的全部内容。
//
// 夹具（internal/testdata/mcp_form_landing.html）复刻真站 MUI 的形态：
// 控件在 wrapper 里、选项 Portal 到 body、mousedown 铺上来那层 backdrop
// **收到 mouseup 就把菜单关掉**。
func TestMCPFormSurfacesLandingFacts(t *testing.T) {
	e := env(t)
	b := newMCPBrowser(t)
	naviWithCLI(t, e.bin, b.port, e.fixture+"/mcp_form_landing.html")

	door := startMCPDoor(t, mcpBinary(t),
		[]string{"--host", "127.0.0.1", "--port", strconv.Itoa(b.port)}, nil)
	door.handshake()

	state := func() map[string]any {
		return evalJSON(t, e.bin, b.port,
			`JSON.stringify({sel:window.__sel,ups:window.__backdropMouseUps,listbox:document.querySelectorAll('[role=listbox]').length})`)
	}

	// ① 起点：菜单没开、覆盖者没收到过东西
	if s := state(); s["listbox"].(float64) != 0 || s["ups"].(float64) != 0 {
		t.Fatalf("起点不干净: %v", s)
	}

	// ② 走门上的 form --select：回执里必须带出「抬起被扣下」
	text, isErr := door.callTool("form", map[string]any{"selector": "#mcpf-wrap", "select": "BMW"})
	if isErr {
		t.Fatalf("form --select 在门上失败了: %s", text)
	}
	if !strings.Contains(text, "扣下") {
		t.Errorf("门上 form 的回执里没有「抬起被扣下」这件事 —— agent 会把它读成普通的填值，"+
			"而这一次点击只发出了按下的那一半（回执：%s）", text)
	}

	// ③ 覆盖者一个事件都没收到；选项照常选得中
	s := state()
	if s["sel"] != "BMW" {
		t.Errorf("选项没选上（sel=%v）—— 落点判据把「选一个选项」那一下也一起废掉了？（状态 %v）", s["sel"], s)
	}
	if s["ups"].(float64) != 0 {
		t.Errorf("覆盖者收到了 %v 个 mouseup —— 菜单会被我们自己关掉（G1 要治的正是它）", s["ups"])
	}

	// ④ 反面：判据没话可说时**不许**出现落点那几句（常驻的提示等于没有提示）
	text2, isErr := door.callTool("form", map[string]any{"selector": "#mcpf-input", "value": "hello"})
	if isErr {
		t.Fatalf("form --value 在门上失败了: %s", text2)
	}
	if strings.Contains(text2, "落点") || strings.Contains(text2, "扣下") {
		t.Errorf("普通填值（判据没话可说）的回执里带着落点的话: %s", text2)
	}
	if v := evalOn(t, e.bin, b.port, `document.getElementById('mcpf-input').value`); !strings.Contains(v, "hello") {
		t.Errorf("普通填值没落地: %q", v)
	}
	t.Logf("门上 form：扣下那一句到得了 agent；普通填值的回执里一个字都不多")
}
