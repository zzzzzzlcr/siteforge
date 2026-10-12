package cmd

import (
	"encoding/json"
	"testing"
)

// 钉住「**影子宿主从 body 继承来的 `pointer-events:none` 不是透明层**」。
//
// 真事（2026-10-12 · `job-5b01fdf771e0` · ic.com 注册页的「选国家」那一步，现场与读数在盘上：
// `runtime-222/_probe_ic_country*.js|.out`）：运营看到的是「**AI 只会输入国家名，不会点**，
// 然后一直卡在这里」。量出来的链子是四环：
//
//	① 整个注册 app 在一个**开着的影子根**里（宿主 `div#register.icm-ios`）；
//	   国家选择器是里面第二层影子宿主（`div.icm-ios`）上的一个 Radix 抽屉。
//	② 抽屉一打开，站点给 `body` 写上 `pointer-events:none`（模态锁）⇒ 整页所有东西的
//	   computed pe 都成了 none —— **包括那个影子宿主**（它是**继承**来的，不是自己写的）。
//	③ 抽屉内容那一层**显式写回 auto** ⇒ 真人点得动：实测拿裸鼠标在选项中心按一下，
//	   `#country` 当场变成 `United Kingdom`、抽屉关掉（`_probe_ic_country4.out`）。
//	④ 而 cdp 的落点判据 `__cdpTopAt` 里有一条「**跳过 pointer-events:none 的层**」
//	   （2026-09-28 ukg 那个空遮罩留下的老保护）——它把**影子宿主**也当透明层跳过去了。
//	   可 `document.elementsFromPoint` **不穿 shadow**：那一点上回的就是宿主本身，
//	   宿主被跳过之后整摞只剩 `<html>` ⇒ 判据判「不是它」⇒ **拒点**。
//	   生产 trace 第 34/36/41/48/56 步的原话就是这么一句：
//	   `拒绝点击 "button[role=\"option\"]"：它中心的整摞里没有它 —— 那一点上站着的是 0:<html class="icm-ios">`
//
// 为什么修在 cdp 这一侧：产物手上只有 click / form / scroll，**三件全过同一道
// `__cdpReachesAt`**（`form --select` 的自定义下拉那两条路也一样，见下面的对照断言）。
//
// 判据是**行为**，不是内部状态：
//
//	① 这一下必须**成**（退出码 0），而且按钮自己的 click 真的响了（`__optClicked`）；
//	② 反向对照（`…StillDoesNotBlock`）：一个**真透明**的影子层（连宿主自己都是 none、
//	   子树里一处 auto 都没有）盖着谁，谁照旧点得成 —— 老保护不许被这一刀弄丢。
func TestClickReachesThroughAShadowHostInheritingPointerEventsNone(t *testing.T) {
	e := env(t)
	e.navigate(t, e.fixture+"/shadow_pe_none.html")

	st := shadowPeState(t, e)
	// 夹具守卫：这三格就是那三条事实。少了任何一条，这条用例就退化成恒绿的空转。
	if st["bodyPE"] != "none" || st["hostPE"] != "none" || st["optPE"] != "auto" {
		t.Fatalf("夹具不是那个形状（要看 body=none / 宿主=none / 选项=auto）：%v\n"+
			"夹具被改小了的话，这条用例什么也证明不了", st)
	}

	out, errOut, code := e.run(t, "click", "#opt")
	if code != 0 {
		t.Fatalf("影子树里那一颗按钮点不成 —— 这就是生产那 5 步的原文（判据落在 <html> 上）\n"+
			"stderr: %s", errOut)
	}
	if !shadowPeOptClicked(t, e) {
		t.Errorf("cdp 回了成功，可按钮自己的 click **没响** —— 那就是「看起来成功了、实际点的是别人」\n"+
			"（本项目最贵的一类失败）。回执：%.200s", out)
	}
}

// 反向对照：**真透明**的影子层不许变成遮挡。
//
// 2026-09-28 ukg 实测那个空遮罩（`pointer-events:none`）当年被当成了「挡住落点」，
// 于是每一步都不点 —— 判据里「跳过 pe:none 的层」那一条就是为它写的。
// 这一刀（钻进去问影子树）**不许**把那件事弄回来：宿主自己 none、子树里也没有一处
// 写回 auto 时，钻进去应当**问不出东西**，然后照旧跳过、照旧点得到下面那个元素。
func TestATrulyTransparentShadowLayerStillDoesNotBlockTheClick(t *testing.T) {
	e := env(t)
	e.navigate(t, e.fixture+"/shadow_pe_none.html")

	// 解锁：这一条要的是「透明层不挡」，不是「锁着的时候点得动」（那是另一条用例的事）
	if _, errOut, code := e.run(t, "eval", "window.__lock(false)"); code != 0 {
		t.Fatalf("解锁夹具失败（退出码 %d）：%s", code, errOut)
	}

	st := shadowPeState(t, e)
	if st["veilPE"] != "none" {
		t.Fatalf("夹具的形状不对：那层透明影子层的 pe 是 %v，该是 none", st["veilPE"])
	}
	if st["behindPE"] != "auto" {
		t.Fatalf("夹具的形状不对：被盖住那颗按钮的 pe 是 %v，该是 auto"+
			"（它自己不是 auto 的话，这一条测的就成了「点不动」）", st["behindPE"])
	}

	if _, errOut, code := e.run(t, "click", "#behind"); code != 0 {
		t.Fatalf("透明的影子层把落点判据骗了 —— 它又不算透明了（2026-09-28 ukg 那条老保护丢了）\n"+
			"stderr: %s", errOut)
	}
}

// shadowPeState 读夹具自报的那几格（pe 三连 + 点击证据）。
func shadowPeState(t *testing.T, e *testEnv) map[string]any {
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

// shadowPeOptClicked 直接问页面「那颗按钮自己的 click 响了没有」。
//
// 为什么不看 `window.__clicks`：影子树里来的点击在**文档这一层**会被重定向成宿主
// （影子 DOM 的规矩），于是「点到了宿主」与「点到了里面那颗按钮」在那条记录里长得一样。
func shadowPeOptClicked(t *testing.T, e *testEnv) bool {
	t.Helper()
	st := shadowPeState(t, e)
	got, _ := st["optClicked"].(bool)
	return got
}
