package cmd

import (
	"encoding/json"
	"testing"
)

// 钉住「**选择器里的撇号不许把命令打断**」。
//
// 真事（2026-10-12 · `job-9e2250b60c19` · nitrouscompetitions.com/auth）：账本里那根
// `form:has(input[placeholder="A friend's code"]) input[type=date]` 被**原样**塞进 JS
// 的单引号串 —— `internal/client.go` 有两处（`GetElementCenter`、`ScrollIntoView`）
// 漏了 `escapeJS`，别人都套了 ⇒ rc=1：
//
//	Error: scroll mouse wheel failed: JS exception: SyntaxError: missing ) after argument list
//
// 那就是那单第 16、18 步的报错原文。代价不止"那两步没做成"：模型因此**弃掉那根好
// 选择器**，改用 `body:nth-of-type(1) > … > label:nth-of-type(4) > input:nth-of-type(1)`
// 这种位置链 —— 后来 lint 点名拦"只有一根随时会漂的地址"，拦的正是那串。
//
// 判据：这条带撇号的选择器，三条路（scroll / click / form）都得成。
func TestAQuoteInTheSelectorDoesNotBreakTheCommand(t *testing.T) {
	e := env(t)
	e.navigate(t, e.fixture+"/quoted_selector.html")

	// 撇号在 `:has(...)` 里，与真站那条一字不差地同形
	sel := `form:has(input[placeholder="A friend's code"]) input[type=text]`

	if st := quotedSelectorState(t, e); st["value"] != "" {
		t.Fatalf("夹具刚打开框里就有东西（%v）—— 后面的断言会被它喂饱", st["value"])
	}

	// ① scroll：真站挂的正是这一条（ScrollToElement → ScrollMouseWheel → GetElementCenter）
	if _, errOut, code := e.run(t, "scroll", sel); code != 0 {
		t.Fatalf("scroll 被撇号打断了（真站原文：SyntaxError: missing ) after argument list）\nstderr: %s", errOut)
	}
	// ② click
	if _, errOut, code := e.run(t, "click", sel); code != 0 {
		t.Fatalf("click 被撇号打断了\nstderr: %s", errOut)
	}
	// ③ form
	if _, errOut, code := e.run(t, "form", sel, "--value", "FRIEND10"); code != 0 {
		t.Fatalf("form 被撇号打断了\nstderr: %s", errOut)
	}
	if st := quotedSelectorState(t, e); st["value"] != "FRIEND10" {
		t.Errorf("值没进框：value=%v", st["value"])
	}
}

// quotedSelectorState 读夹具自报的两格：框里的值 / 那个按钮被点了几下。
func quotedSelectorState(t *testing.T, e *testEnv) map[string]any {
	t.Helper()
	out, errOut, code := e.run(t, "eval", "window.__state()")
	if code != 0 {
		t.Fatalf("读夹具状态失败（退出码 %d）：%s", code, errOut)
	}
	var inner string
	if err := json.Unmarshal([]byte(out), &inner); err != nil {
		t.Fatalf("eval 的输出不是 JSON 字符串: %v\n原文: %q", err, out)
	}
	var m map[string]any
	if err := json.Unmarshal([]byte(inner), &m); err != nil {
		t.Fatalf("夹具状态解不开: %v（原文 %q）", err, inner)
	}
	return m
}
