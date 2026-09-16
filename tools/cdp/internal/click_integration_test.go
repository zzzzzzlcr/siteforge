package internal

import (
	"encoding/json"
	"os"
	"strings"
	"testing"

	"github.com/chromedp/cdproto/input"
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

// ── G1：按下之后落点被新元素覆盖 → 这一次抬起不该交给新落点 ──────────────────
//
// 夹具复刻**实测**到的形态（2026-09-16 真窗口 + 真 MUI 页，见 fix-click-timing-report）：
//
//	#g1-combo 在 mousedown 里展开菜单并铺一层盖住整个视口的 backdrop
//	（Portal 到 body —— 现代组件库的常态）。**实测**：紧接着的 mouseup 落在
//	刚出现的 backdrop 上（`mouseup:<div#backdrop>`），而 click 落在两者的
//	**公共祖先**（`click:BODY`）—— 所以「库在 click 上关掉菜单」那个说法不成立，
//	真正递到覆盖者手里的是 **mouseup**。夹具据此把「关掉菜单」挂在 backdrop 的
//	mouseup 上：那才是实测里会发生的那条路（挂在 click 上等于造一个不存在的机制）。
//
// 三个阶段，缺一不可：
//
//	① 老行为（moved → pressed → released 连着发）**当众复现缺陷**：覆盖者收到 mouseup、
//	   菜单被自己关掉。没有这一阶段，下面那条断言可能是夹具根本没生效才绿的。
//	② 新行为（dispatchMouseClick）：抬起被扣下、覆盖者一个事件都没收到、菜单还开着。
//	③ 正向对照：普通按钮照常点得动，**菜单项也照常点得中** —— 那一下按下与抬起之间
//	   没有东西盖上来，必须走正常路径（`cdp form --select` 的第二半正是它）。
func TestDispatchMouseClick_WithholdsReleaseWhenPointGetsCovered(t *testing.T) {
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

	// 菜单（z-index 6000）盖住 backdrop（5000），但**不盖住** combobox 自己
	// （menu 从 y=150 起，combo 是 y=100..150）—— 于是按下之后那个点上站着的
	// 是 backdrop：这正是实测里量到的形状。
	setupJS := `(function(){
		var c = document.createElement('div');
		c.id = 'g1-wrap';
		c.innerHTML =
			'<div id="g1-combo" style="position:fixed;left:100px;top:100px;width:200px;height:50px;background:#ccc;z-index:1">combo</div>' +
			'<div id="g1-other" style="position:fixed;left:100px;top:300px;width:200px;height:50px;background:#ddd;z-index:1">other</div>' +
			'<ul id="g1-menu" role="listbox" style="display:none;position:fixed;left:100px;top:150px;width:200px;margin:0;padding:0;list-style:none;background:#fff;z-index:6000">' +
			'<li role="option" data-v="a" style="height:40px;line-height:40px">Option A</li>' +
			'<li role="option" data-v="b" style="height:40px;line-height:40px">Option B</li></ul>';
		document.body.innerHTML = '';
		document.body.appendChild(c);

		window.__g1 = { menuOpen:false, backdropMouseUp:0, backdropClick:0, optionClick:0, otherClick:0, gaps:0 };
		window.__g1Close = function(){
			window.__g1.menuOpen = false;
			document.getElementById('g1-menu').style.display = 'none';
			var b = document.getElementById('g1-backdrop');
			if (b) b.parentNode.removeChild(b);
		};
		window.__g1Open = function(){
			if (window.__g1.menuOpen) return;
			window.__g1.menuOpen = true;
			document.getElementById('g1-menu').style.display = 'block';
			var b = document.createElement('div');
			b.id = 'g1-backdrop';
			b.style.cssText = 'position:fixed;left:0;top:0;right:0;bottom:0;z-index:5000;background:rgba(0,0,0,0.2)';
			// **mouseup**（实测递到覆盖者手里的那一条）：覆盖者收到它就当作「点在外面」。
			b.addEventListener('mouseup', function(){ window.__g1.backdropMouseUp++; window.__g1Close(); });
			b.addEventListener('click', function(){ window.__g1.backdropClick++; });
			document.body.appendChild(b);
		};
		document.getElementById('g1-combo').addEventListener('mousedown', window.__g1Open);
		document.getElementById('g1-other').addEventListener('click', function(){ window.__g1.otherClick++; });
		var lis = document.querySelectorAll('#g1-menu li');
		for (var i = 0; i < lis.length; i++) {
			lis[i].addEventListener('click', function(){ window.__g1.optionClick++; window.__g1Close(); });
		}
		return 'ok';
	})()`

	reset := func() {
		t.Helper()
		var r string
		if err := client.EvalInFrame("", setupJS, &r); err != nil {
			t.Fatalf("夹具注入失败: %v", err)
		}
	}
	state := func() (open bool, backdropMouseUp, backdropClick, optionClick, otherClick int) {
		t.Helper()
		var raw string
		if err := client.EvalInFrame("", `JSON.stringify(window.__g1)`, &raw); err != nil {
			t.Fatalf("读状态失败: %v", err)
		}
		var m struct {
			MenuOpen       bool `json:"menuOpen"`
			BackdropMouseU int  `json:"backdropMouseUp"`
			BackdropClick  int  `json:"backdropClick"`
			OptionClick    int  `json:"optionClick"`
			OtherClick     int  `json:"otherClick"`
		}
		if err := json.Unmarshal([]byte(raw), &m); err != nil {
			t.Fatalf("状态解不开: %v (%s)", err, raw)
		}
		return m.MenuOpen, m.BackdropMouseU, m.BackdropClick, m.OptionClick, m.OtherClick
	}
	center := func(sel string) (float64, float64) {
		t.Helper()
		rect, err := client.GetElementCenter(sel, "")
		if err != nil {
			t.Fatalf("取 %q 坐标失败: %v", sel, err)
		}
		return rect["centerX"], rect["centerY"]
	}

	// ── ① 老行为：当众复现缺陷 ──
	reset()
	cx, cy := center("#g1-combo")
	for _, typ := range []input.MouseType{input.MouseMoved, input.MousePressed, input.MouseReleased} {
		if err := client.dispatchMouseEvent(typ, cx, cy); err != nil {
			t.Fatalf("① 老行为发事件失败: %v", err)
		}
	}
	open, backdropMouseUp, _, _, _ := state()
	if backdropMouseUp == 0 {
		t.Fatalf("① 夹具没有复现出缺陷：覆盖者一个 mouseup 都没收到 —— " +
			"后面那条「新行为下收不到」的断言就在证明一件不存在的事（先查夹具几何）")
	}
	if open {
		t.Errorf("① 老行为下菜单居然还开着 —— 夹具的「覆盖者收到 mouseup 就关掉」没生效")
	}
	t.Logf("① 老行为：覆盖者收到 %d 个 mouseup，菜单被关掉（menuOpen=%v）—— 缺陷复现", backdropMouseUp, open)

	// ── ② 新行为：抬起被扣下，覆盖者什么都收不到 ──
	reset()
	cx, cy = center("#g1-combo")
	outcome, err := client.dispatchMouseClick(cx, cy)
	if err != nil {
		t.Fatalf("② dispatchMouseClick 失败: %v", err)
	}
	if !outcome.ReleaseWithheld {
		t.Errorf("② 按下之后 %s 盖了上来，抬起却**没有**被扣下（outcome=%+v）—— "+
			"落点变了这件事必须当场判出来", "backdrop", outcome)
	}
	if !strings.Contains(outcome.LandedAfter, "g1-backdrop") {
		t.Errorf("② 「覆盖者是谁」报的是 %q，应为 #g1-backdrop —— 判据必须是**落点变了**，"+
			"而报告要能回答「变成了谁」", outcome.LandedAfter)
	}
	if !strings.Contains(outcome.LandedBefore, "g1-combo") {
		t.Errorf("② 「按下前是谁」报的是 %q，应为 #g1-combo", outcome.LandedBefore)
	}
	var backdropClick int
	open, backdropMouseUp, backdropClick, _, _ = state()
	if backdropMouseUp != 0 || backdropClick != 0 {
		t.Errorf("② 抬起被扣下了，覆盖者却仍收到事件（mouseup=%d click=%d）—— "+
			"扣下抬起的意义就是**一个鼠标事件都不交给它**", backdropMouseUp, backdropClick)
	}
	if !open {
		t.Errorf("② 菜单没开着 —— mousedown 展开的菜单被我们自己关掉了（这正是缺陷本身）")
	}
	// 公开入口同样成立（生产脚本/agent 走的是它）。
	if err := client.DispatchMouseClick(cx, cy); err != nil {
		t.Fatalf("② DispatchMouseClick 失败: %v", err)
	}
	t.Logf("② 新行为：抬起被扣下（%s → %s），覆盖者收到 0 个事件，菜单仍开着", outcome.LandedBefore, outcome.LandedAfter)

	// ── ③ 正向对照：正常路径一条都没坏 ──
	reset()
	ox, oy := center("#g1-other")
	if err := client.DispatchMouseClick(ox, oy); err != nil {
		t.Fatalf("③ 点普通按钮失败: %v", err)
	}
	if _, _, _, _, otherClick := state(); otherClick != 1 {
		t.Errorf("③ 普通按钮的 onclick 触发 %d 次，应为 1 —— 扣下抬起只该发生在「落点换了人」那一下，"+
			"不许把正常点击一起扣掉", otherClick)
	}

	// 菜单项：按下与抬起之间没有东西盖上来 → 必须走正常路径、click 照常落地。
	reset()
	cx, cy = center("#g1-combo")
	if _, err := client.dispatchMouseClick(cx, cy); err != nil {
		t.Fatalf("③ 打开菜单失败: %v", err)
	}
	ix, iy := center("#g1-menu li")
	outcome, err = client.dispatchMouseClick(ix, iy)
	if err != nil {
		t.Fatalf("③ 点选项失败: %v", err)
	}
	if outcome.ReleaseWithheld {
		t.Errorf("③ 点菜单项那一下抬起也被扣下了（%+v）—— 那一下落点没变，"+
			"扣掉它等于把「选一个选项」也一起废掉（cdp form --select 靠的就是它）", outcome)
	}
	if _, _, _, optionClick, _ := state(); optionClick != 1 {
		t.Errorf("③ 菜单项的 click 触发 %d 次，应为 1 —— 落点没变的那一下必须照常点击", optionClick)
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
