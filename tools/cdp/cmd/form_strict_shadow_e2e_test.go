package cmd

import (
	"encoding/json"
	"strings"
	"testing"
)

// 钉住「**`--strict` 不许把影子树里的选择器改写成一条不存在的地址**」。
//
// 真事（2026-10-12 · `job-5b01fdf771e0`/`job-d57366d0cf35` · ic.com 的选国家抽屉）：
// 产物第 9 步要往抽屉里的搜索框填「United Kingdom」，那条命令是
// `cdp form 'input[placeholder="Search..."]' --value … --strict --expect-label "field"`
// —— **本机同一刻实测**：不带那两个闸 rc=0（值进框、列表从 175 项过滤成 1 项），
// 带上就 rc=1 `Error: scroll mouse wheel failed: element not found`。
//
// 根因在 `strictPickJS` 的 `pathOf()`：它沿 `parentElement` 往上走，而**影子边界上
// `parentElement` 是 null** ⇒ 给影子树里的元素造出 `html>input:nth-of-type(1)` 这种
// 根本不存在的地址（有 id 的元素走另一句、不受影响）⇒ CLI 拿这条**改写过的**地址去填
// （`cmd/form.go`：`if picked != "" { selector = picked }`）⇒ 找不到。
//
// 判据是**行为**，两层：
//
//	① 对照：不带 `--strict` 那条命令必须成（证明元素找得到、框填得进 —— 夹具本身没问题）；
//	② 判决：带上 `--strict --expect-label`（**产物走的就是这条**）也必须成，且值真进框。
func TestFormStrictStillFillsAnElementInsideAShadowRoot(t *testing.T) {
	e := env(t)
	e.navigate(t, e.fixture+"/shadow_strict_form.html")

	selector := `input[placeholder="Search..."]`

	st := strictShadowState(t, e)
	if st["plain"] != float64(0) {
		t.Fatalf("夹具没把那个框藏进影子根（裸 querySelector 还看得见 %v 个）—— "+
			"没藏好的话这条用例什么也证明不了", st["plain"])
	}

	// ① 对照（不带那两个闸）
	if _, errOut, code := e.run(t, "form", selector, "--value", "control"); code != 0 {
		t.Fatalf("不带 --strict 都没成（%s）—— 先查夹具/解析，别急着怪严格闸\nstderr: %s",
			selector, errOut)
	}
	if got := strictShadowState(t, e)["value"]; got != "control" {
		t.Fatalf("对照那一遍的值没进框：value=%v", got)
	}

	// ② 判决：产物那条路（--strict + --expect-label）
	out, errOut, code := e.run(t, "form", selector, "--value", "United Kingdom",
		"--strict", "--expect-label", "field")
	if code != 0 {
		t.Fatalf("带 --strict 就填不进去了 —— 产物走的正是这条（真站上这一步的表现就是"+
			"「页面上没找到那个框」，然后掉进「重找」、值落到别的框里）\nstderr: %s", errOut)
	}
	if !strings.Contains(out, `"ok":true`) {
		t.Errorf("成了却没有成功回执（stdout=%.200s）", out)
	}
	if got := strictShadowState(t, e)["value"]; got != "United Kingdom" {
		t.Errorf("值没进那个框：value=%v 想要 %q", got, "United Kingdom")
	}
}

// strictShadowState 读夹具自报的两格：裸查询看得见几个 / 框里装的是什么。
func strictShadowState(t *testing.T, e *testEnv) map[string]any {
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
