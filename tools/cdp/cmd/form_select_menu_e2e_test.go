package cmd

import (
	"encoding/json"
	"strings"
	"testing"
)

// 钉住「**谁开的谁关**」：自定义下拉那一下**没成**的时候，我们自己点开的那个菜单必须被关掉。
//
// 真事（2026-10-10 · `job-e7906a6c65d0` · freshstartrelief.org，现场在盘上）：
// 产物第 6 步是 `form #unfiledYears --select "3 years"`（shadcn/Radix 那种下拉）。
// 那一下**值真选上了**（截图里 `✓ 3 years`、按钮上也写着 3 years），可**菜单留在开着** ——
// Radix 打开时把 `document.body` 设成 `pointer-events:none`，于是从那一步起，
// **后面每一次点击都被判「那一点上站着的是 `<html>`」而拒绝**（实测原文：
// `拒绝点击…它中心的整摞里没有它 —— 那一点上站着的是 0:<html>`）。
// 运营看到的就是「选完没收回、盖住了按钮」；自测那一趟 28 步里 17 步被跳过。
//
// 为什么修在 cdp 这一侧、而不是产物那一侧（试过了，不行）：菜单开着的时候**整页只有
// 菜单自己能命中**，产物手上那三件工具（click / form / scroll）**没有一件**能碰到那颗
// 按钮（`cdp click '[role=combobox][aria-expanded="true"]'` 当场被拒）。能关掉它的只有
// Escape —— 而那是开它的这一侧才发得出来的东西。
//
// 判据两条，缺一不可：
//
//	① **页面要还能用** —— 失败之后紧接着换一个真存在的选项，那一下必须成。
//	   没有这一条，测试就退化成「读一个内部状态」：清了没清都能自圆其说；
//	② 那一下的话要说得出来（stderr 里点名「菜单」）—— 不许静默地留下一块盖住整页的东西。
func TestFormSelectClosesTheMenuItOpenedWhenTheOptionIsMissing(t *testing.T) {
	e := env(t)
	e.navigate(t, e.fixture+"/radix_select.html")

	// 控件那一点会把菜单点开；选项「Nope」在页面上不存在 ⇒ 这一步成不了
	_, errOut, code := e.run(t, "form", "--selector", "#rs-trigger", "--select", "Nope")
	if code == 0 {
		t.Fatalf("选项不存在时 `form` 竟然退 0 —— 这一步没做成，退 0 就是谎报成功")
	}
	assertMenuClosedAndUsable(t, e, errOut, "1 year")
}

// 同一个病的另一条失败路径：选项**在**、但那一坐标上够不着它（真站那一下的原话就是
// `Error: option is covered by <html> — 点它会落到那上面，拒绝`）。
// ⚠️ 两条路径在 Go 侧是**同一个 return**（`optionResult["error"]`），分开钉是因为
// 真站踩的是这一条 —— 修的时候容易只顾着「找不到」那一支。
func TestFormSelectClosesTheMenuItOpenedWhenTheOptionCannotBeHit(t *testing.T) {
	e := env(t)
	e.navigate(t, e.fixture+"/radix_select.html")

	_, errOut, code := e.run(t, "form", "--selector", "#rs-trigger", "--select", "2 years")
	if code == 0 {
		t.Fatalf("选项够不着时 `form` 竟然退 0 —— 这一步没做成，退 0 就是谎报成功")
	}
	assertMenuClosedAndUsable(t, e, errOut, "1 year")
}

// assertMenuClosedAndUsable 是上面两条共用的**同一份**判据（两处各写一遍必然漂移）：
//
//	① 菜单关了、body 的 pointer-events 还回去了（这是机制本身）；
//	② 紧接着真选一项，**必须成**（这是行为上的证据 —— 页面真的还能用）；
//	③ 那一下失败时的话里点名了「菜单」（不许静默）。
func assertMenuClosedAndUsable(t *testing.T, e *testEnv, errOut, goodOption string) {
	t.Helper()

	st := rsState(t, e)
	if st["open"] == true {
		t.Errorf("失败之后菜单还开着 —— 下一步的每一次点击都会被判「那一点上站着的是 <html>」"+
			"而拒绝（真站就是这样把一整趟跑死的）。状态：%v", st)
	}
	if pe, _ := st["bodyPE"].(string); pe != "" {
		t.Errorf("失败之后 body 的 pointer-events 还是 %q（没还回去）—— 整页仍然只有菜单能命中。状态：%v",
			pe, st)
	}
	if !strings.Contains(errOut, "菜单") {
		t.Errorf("那一下失败了，可 stderr 里一个字都没说菜单怎么了 —— 不许静默地留下一块"+
			"盖住整页的东西。stderr：%q", errOut)
	}

	// ② 行为上的证据：页面还能用
	out, errOut2, code := e.run(t, "form", "--selector", "#rs-trigger", "--select", goodOption)
	if code != 0 {
		t.Fatalf("紧接着选一个真存在的选项（%q）却没成 —— 页面被上一下留在「点不动」的状态里了\n"+
			"stderr: %s", goodOption, errOut2)
	}
	if !strings.Contains(out, `"ok":true`) {
		t.Errorf("选成了却没有成功回执（stdout=%.200s）", out)
	}
	st = rsState(t, e)
	if st["value"] != goodOption {
		t.Errorf("第二次填的值没进页面：value=%v 想要 %q", st["value"], goodOption)
	}
	if st["open"] == true {
		t.Errorf("选成之后菜单还开着：%v", st)
	}
}

// rsState 读夹具自报的状态（三格：选中的值 / 菜单开没开 / body 的 pointer-events）。
func rsState(t *testing.T, e *testEnv) map[string]any {
	t.Helper()
	var inner string
	if err := json.Unmarshal([]byte(evalOn(t, e.bin, e.chromePort, "window.__rsState()")), &inner); err != nil {
		t.Fatalf("eval 输出不是 JSON 字符串: %v", err)
	}
	var m map[string]any
	if err := json.Unmarshal([]byte(inner), &m); err != nil {
		t.Fatalf("夹具状态解不开: %v（原文 %q）", err, inner)
	}
	return m
}
