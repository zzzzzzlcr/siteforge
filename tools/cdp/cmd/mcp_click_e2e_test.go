package cmd

import (
	"bytes"
	"encoding/json"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"testing"
)

// MCP 那道门上的 click：**严格是默认**（规格裁定 —— additive by default, strict at
// the agent's door）。
//
// 为什么这条必须在**这道门**上再钉一遍，而不是只钉 CLI：
// CLI 的默认是「宽松」（57 个生产脚本靠它保持老行为），MCP 的默认是「严格」——
// 两个默认**不一样**，而它们共用同一个内核方法。只测 CLI 的话，把 MCP 侧接回
// 宽松那一版，全套测试照样绿。这个项目的规矩是「两个门行为不一致会变成 agent 的
// 暗礁」，所以两边各自有自己的闸门。
//
// 浏览器是**这条测试独享**的（newMCPBrowser：私有端口 + 私有 profile）——
// 不与 testEnv 那个、也不与共享的 9222 抢页面。

// evalOn 用 cdp CLI 在**指定的**私有浏览器上求值（MCP 门说的是另一条路）。
func evalOn(t *testing.T, cliBin string, port int, js string) string {
	t.Helper()
	cmd := exec.Command(cliBin,
		"--host", "127.0.0.1", "--port", strconv.Itoa(port), "eval", js)
	cmd.Env = envWithout(os.Environ(), "CDP_HOST", "CDP_PORT")
	var stdout, stderr bytes.Buffer
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	if err := cmd.Run(); err != nil {
		t.Fatalf("在端口 %d 上 eval 失败: %v\nstderr: %s", port, err, stderr.String())
	}
	return strings.TrimSpace(stdout.String())
}

// clickedOn 读某个私有浏览器上的 window.__clicks。
func clickedOn(t *testing.T, cliBin string, port int) []string {
	t.Helper()
	out := evalOn(t, cliBin, port, "JSON.stringify(window.__clicks)")
	var raw string
	if err := json.Unmarshal([]byte(out), &raw); err != nil {
		t.Fatalf("eval 的输出不是 JSON 字符串: %v\n原文: %q", err, out)
	}
	var names []string
	if err := json.Unmarshal([]byte(raw), &names); err != nil {
		t.Fatalf("__clicks 不是字符串数组: %v\n原文: %q", err, raw)
	}
	return names
}

// mcpClickDoor 起一个 MCP 门 + 一个私有浏览器，并把浏览器停在 click 夹具页上。
func mcpClickDoor(t *testing.T) (*mcpDoor, *mcpBrowser, *testEnv) {
	t.Helper()
	e := env(t)
	b := newMCPBrowser(t)
	naviWithCLI(t, e.bin, b.port, e.fixture+"/click_target.html")

	door := startMCPDoor(t, mcpBinary(t),
		[]string{"--host", "127.0.0.1", "--port", strconv.Itoa(b.port)}, nil)
	door.handshake()

	if got := clickedOn(t, e.bin, b.port); len(got) != 0 {
		t.Fatalf("起点不干净：已经有 %v 条点击记录", got)
	}
	return door, b, e
}

// TestMCPClickRefusesAmbiguousSelector：门的默认是**严格** —— 歧义选择器当场拒绝，
// 且拒绝发生在动作之前（页面一个点击记录都不该有）。
func TestMCPClickRefusesAmbiguousSelector(t *testing.T) {
	door, b, e := mcpClickDoor(t)

	text, isErr := door.callTool("click", map[string]any{"selector": "button.choice"})
	if !isErr {
		t.Fatalf("歧义选择器在 MCP 门上被放行了（返回 %q）—— 这道门的默认必须是严格："+
			"agent 会照着它把漏斗倒着走，而它看起来完全成功", text)
	}
	if !strings.Contains(text, "5") {
		t.Errorf("报错没说是几个匹配（agent 改不动选择器）: %s", text)
	}
	// 候选要够认得出 —— 真站上那 5 个按钮 text 全空，只有 aria-label 分得开。
	for _, want := range []string{"Back", "Disagree", "Not sure", "Agree", "Continue"} {
		if !strings.Contains(text, want) {
			t.Errorf("报错里没有候选 %q: %s", want, text)
		}
	}
	if got := clickedOn(t, e.bin, b.port); len(got) != 0 {
		t.Errorf("拒绝了，页面上却留下了点击记录 %v", got)
	}
}

// TestMCPClickRefusesDisabledTarget：门上的默认严格同样覆盖**禁用**目标。
func TestMCPClickRefusesDisabledTarget(t *testing.T) {
	door, b, e := mcpClickDoor(t)

	for _, selector := range []string{"#disabled-go", "#aria-disabled-go"} {
		t.Run(selector, func(t *testing.T) {
			text, isErr := door.callTool("click", map[string]any{"selector": selector})
			if !isErr {
				t.Fatalf("禁用的目标 %s 在 MCP 门上被放行了（返回 %q）—— "+
					"点了什么都不会发生，而 agent 会把它读成「这一步做完了」", selector, text)
			}
			if !strings.Contains(text, "禁用") {
				t.Errorf("报错没说清是「目标被禁用」: %s", text)
			}
			if got := clickedOn(t, e.bin, b.port); len(got) != 0 {
				t.Errorf("拒绝了却还是点了: %v", got)
			}
		})
	}
}

// TestMCPClickStillClicksAUniqueEnabledTarget 是反向对照：严格不等于一律拒绝。
func TestMCPClickStillClicksAUniqueEnabledTarget(t *testing.T) {
	door, b, e := mcpClickDoor(t)

	text, isErr := door.callTool("click", map[string]any{"selector": "#unique-go"})
	if isErr {
		t.Fatalf("唯一且可点的目标被 MCP 门拒了（过度拒绝）: %s", text)
	}

	// agent 拿到的回执里要有那两个**机器可读**的事实（它不用再去猜）。
	var res struct {
		MatchCount     *int  `json:"match_count"`
		MatchIndex     *int  `json:"match_index"`
		TargetDisabled *bool `json:"target_disabled"`
	}
	if err := json.Unmarshal([]byte(text), &res); err != nil {
		t.Fatalf("click 的回执不是 JSON: %v\n原文: %s", err, text)
	}
	if res.MatchCount == nil || *res.MatchCount != 1 {
		t.Errorf("回执里的 match_count = %v，期望 1: %s", res.MatchCount, text)
	}
	if res.MatchIndex == nil || *res.MatchIndex != 0 {
		t.Errorf("回执里的 match_index = %v，期望 0: %s", res.MatchIndex, text)
	}
	if res.TargetDisabled == nil || *res.TargetDisabled {
		t.Errorf("回执里的 target_disabled = %v，期望 false: %s", res.TargetDisabled, text)
	}

	if got := clickedOn(t, e.bin, b.port); len(got) != 1 || got[0] != "UniqueGo" {
		t.Errorf("点的是 %v，期望 [UniqueGo]", got)
	}
}
