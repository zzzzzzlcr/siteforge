package internal

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"

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

// ── G1 修复轮 1 · Critical-1：**节点被重建**不是「有人盖上来」──────────────────
//
// 复审端到端复现过两个场景：**没东西盖上来，却 `release_withheld=true`、`clicks=0`**，
// 而且回执里 `covered_by` 印出的元素与目标**逐字相同**。根因是身份只比
// `(frameId, backendNodeId)` 一对，而 `backendNodeId` 在**节点重建**时也会变。
//
// ⚠️ 一次正常点击被**静静地**扣掉，比原来那个病更坏（原来的病看得见：菜单被自己关掉）。
//
// 判据的断言设计（**关键**）：每格都在**同一份夹具**上先跑一遍**原始三连**
// （moved → press → release，**绕过我们的门**）当作**浏览器裸行为基线**，
// 再跑一遍被测路径，然后比**两者是否一致**：
//
//	鼠标序列必须与浏览器裸行为**逐项相同** —— 我们不许比裸行为少给任何东西。
//
// 为什么不用「clicks==1」这种写死的期望：mousedown 那一格**浏览器自己**就不合成 click
// （实测：按下时那个节点已被摘掉，Blink 不合成 click —— 原始三连也是 clicks=0，
// mouseups=1）。写死 1 会把「浏览器的合成规则」当成我们的契约，下次 Chrome 改规则
// 就会红；而**与裸行为比对**这件事才是我们真正要保证的（而且它自动跟着浏览器走）。
//
// 两个场景合起来也把**判据窗口**钉住了：窗口是「按下之前 → 按下之后」，
// **不含 MouseMoved 那一段** —— 悬停引起的重渲染发生在按下**之前**，
// 把那一段算进来，鼠标掠过页面本身就触发扣下（场景 A 就是这一条）。
//
// 每格都**自证夹具真的重建了节点**（`rebuilt`），否则「点击发出去了」这句
// 可能在证明一件不存在的事。
func TestDispatchMouseClick_NodeRebuildIsNotACover(t *testing.T) {
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

	// ⚠️ 先换一张白纸：这个 Chrome 页是**长命**的（跨测试、跨整轮 `go test` 都在），
	// 前几轮留下的**文档级监听器**不会随 body.innerHTML 清掉 —— 不清的话
	// 「文档收到几个 mouseup」数的是「这一格 + 前面所有轮」，与裸行为比出来的差
	// 就变成一个常数偏移（看着对，其实什么都没比）。
	if _, err := client.Navigate("about:blank", ""); err != nil {
		t.Fatalf("清场失败: %v", err)
	}
	time.Sleep(500 * time.Millisecond)

	// 夹具外壳：一个按钮 + 挂在**容器**上的事件计数（事件委托 —— 节点被换掉之后
	// 克隆自己是没有监听器的，而浏览器把 click 派发到按下/抬起两个节点的**公共祖先**
	// 上，也就是容器）。每格自己通过 `extra` 挂上它的触发逻辑。
	//
	// 三种触发各考一条判据（见下面的表）：逐字节克隆（考「重建不是覆盖」）、
	// 悬停浮出覆盖物（考「判据窗口不含 MouseMoved 那一段」）。
	setup := func(extra string) string {
		return `(function(){
			document.body.innerHTML =
				'<div id="rb-wrap" style="position:fixed;left:100px;top:100px;width:300px;height:60px;background:#eee">' +
				'<button id="rb-btn" class="rb" style="width:100%;height:100%">rebuild me</button></div>';
			window.__rb = { clicks: 0, mouseups: 0, mousedowns: 0, rebuilt: 0,
			                docMouseUps: 0, docClicks: 0 };
			var wrap = document.getElementById('rb-wrap');
			['click','mouseup','mousedown'].forEach(function(ty){
				wrap.addEventListener(ty, function(){ window.__rb[ty+'s']++; });
			});
			// 文档级（捕获相）计数：**鼠标事件有没有递出去**这件事，容器上的计数看不全
			// （覆盖物是 body 的兄弟，事件不经过容器）—— 这一对才是「抬起照发了没有」的证据。
			//
			// ⚠️ 只挂**一次**（document 上的监听器不随 body.innerHTML 清掉）：
			// 每格注入一次夹具，挂多次的话计数会把前面几格累加进去，与裸行为比不出东西。
			if (!window.__rbDocHooked) {
				window.__rbDocHooked = true;
				document.addEventListener('mouseup', function(){ if (window.__rb) window.__rb.docMouseUps++; }, true);
				document.addEventListener('click', function(){ if (window.__rb) window.__rb.docClicks++; }, true);
			}
			` + extra + `
			return 'ok';
		})()`
	}
	cloneOn := func(trigger string) string {
		return `wrap.addEventListener('` + trigger + `', function(e){
				var b = document.getElementById('rb-btn');
				if (!b || e.target.id !== 'rb-btn') return;
				window.__rb.rebuilt++;
				b.parentNode.replaceChild(b.cloneNode(true), b);   // 逐字节相同
			});`
	}
	reset := func(extra string) {
		t.Helper()
		var r string
		if err := client.EvalInFrame("", setup(extra), &r); err != nil {
			t.Fatalf("夹具注入失败: %v", err)
		}
	}
	type counts struct {
		Clicks, MouseUps, MouseDowns, Rebuilt int
		DocMouseUps, DocClicks                  int
	}
	read := func(who string) counts {
		t.Helper()
		var raw string
		if err := client.EvalInFrame("", `JSON.stringify(window.__rb)`, &raw); err != nil {
			t.Fatalf("%s：读状态失败: %v", who, err)
		}
		var m counts
		if err := json.Unmarshal([]byte(raw), &m); err != nil {
			t.Fatalf("%s：状态解不开: %v (%s)", who, err, raw)
		}
		return m
	}
	// sub 取两次读数之差：文档上的计数是**累计**的（监听器只挂一次），
	// 比累计值等于比「前面几格碰巧对不对」，比**增量**才是这一格到底发生了什么。
	sub := func(a, b counts) counts {
		return counts{
			Clicks: a.Clicks - b.Clicks, MouseUps: a.MouseUps - b.MouseUps,
			MouseDowns: a.MouseDowns - b.MouseDowns, Rebuilt: a.Rebuilt - b.Rebuilt,
			DocMouseUps: a.DocMouseUps - b.DocMouseUps, DocClicks: a.DocClicks - b.DocClicks,
		}
	}
	center := func() (float64, float64) {
		t.Helper()
		rect, err := client.GetElementCenter("#rb-btn", "")
		if err != nil {
			t.Fatalf("取坐标失败: %v", err)
		}
		return rect["centerX"], rect["centerY"]
	}

	for _, c := range []struct{ name, extra, why string }{
		{"悬停换节点", cloneOn("mousemove"),
			"重渲染发生在**按下之前**（MouseMoved 那一段）—— 判据窗口不含它"},
		{"mousedown 触发重渲染", cloneOn("mousedown"),
			"重渲染发生在**按下与抬起之间**，但没有任何东西盖上来"},
		{"悬停浮出一层覆盖物", `wrap.addEventListener('mousemove', function(){
				window.__rb.rebuilt++;
				var o = document.createElement('div');
				o.id = 'rb-cover';
				o.style.cssText = 'position:fixed;left:0;top:0;right:0;bottom:0;z-index:9999;background:rgba(0,0,0,.1)';
				document.body.appendChild(o);
			});`,
			"覆盖物是**悬停**招来的（按下之前就铺好了），不是「按下之后有人盖上来」—— "+
				"判据窗口一旦含 MouseMoved，鼠标掠过页面本身就会把点击扣掉"},
	} {
		// 基线：**绕过我们的门**的原始三连（浏览器裸行为）
		reset(c.extra)
		snap := read(c.name + "（基线前）")
		x, y := center()
		for _, typ := range []input.MouseType{input.MouseMoved, input.MousePressed, input.MouseReleased} {
			if err := client.dispatchMouseEvent(typ, x, y); err != nil {
				t.Fatalf("%s：基线发事件失败: %v", c.name, err)
			}
		}
		time.Sleep(150 * time.Millisecond)
		raw := sub(read(c.name+"（基线）"), snap)
		if raw.Rebuilt == 0 {
			t.Fatalf("%s：夹具**没有**重建节点（rebuilt=0）—— 这条测试什么都没考到（%s）", c.name, c.why)
		}

		// 被测路径
		reset(c.extra)
		snap = read(c.name + "（被测前）")
		result, err := client.ClickElement("#rb-btn", "", false)
		if err != nil {
			t.Fatalf("%s：ClickElement 失败: %v", c.name, err)
		}
		time.Sleep(150 * time.Millisecond)
		got := sub(read(c.name), snap)

		if got.Rebuilt == 0 {
			t.Fatalf("%s：被测那一遍没重建节点（rebuilt=0）—— 断言会空转", c.name)
		}
		if result.ReleaseWithheld {
			t.Errorf("%s：抬起被扣下了（covered_by=%q）—— 但**没有任何东西盖上来**，换掉的只是同一个"+
				"元素的另一个节点（%s）。一次正常点击被静静吞掉，比原来那个病更坏",
				c.name, result.CoveredBy, c.why)
		}
		if got.DocMouseUps != raw.DocMouseUps {
			t.Errorf("%s：文档收到 %d 个 mouseup，浏览器裸行为是 %d 个 —— 我们的门比裸行为**少给了**"+
				"东西（%s）", c.name, got.DocMouseUps, raw.DocMouseUps, c.why)
		}
		if got.DocClicks != raw.DocClicks {
			t.Errorf("%s：文档收到 %d 个 click，浏览器裸行为是 %d 个 —— 门比裸行为少给了东西（%s）",
				c.name, got.DocClicks, raw.DocClicks, c.why)
		}
		if got.MouseUps != raw.MouseUps || got.Clicks != raw.Clicks {
			t.Errorf("%s：容器上的计数也与裸行为不一致（mouseup %d/%d、click %d/%d）",
				c.name, got.MouseUps, raw.MouseUps, got.Clicks, raw.Clicks)
		}
		t.Logf("%s：重建 %d 次；被测（withheld=%v）doc: mouseup=%d click=%d ／ 裸行为 doc: mouseup=%d click=%d（%s）；note=%q",
			c.name, got.Rebuilt, result.ReleaseWithheld, got.DocMouseUps, got.DocClicks,
			raw.DocMouseUps, raw.DocClicks, c.why, result.LandingNote)
	}
}

// ── G1 修复轮 1 · Critical-2：跨站子帧里判据**恒不触发**，这件事必须能听见 ──────
//
// 复审实测的机制（本机复验过）：跨站子帧里 `DOM.getNodeForLocation` **只返回父页的
// `<iframe>` 元素**（同 site 跨 origin 才下钻），于是按下前后问到的都是同一个
// `<iframe>` → 判据**恒不触发**、而且**一声不吭** —— 这一帧上的点击看起来和别处
// 一样「有保护」，其实一点都没有。
//
// 夹具用 `localhost` vs `127.0.0.1` 造出**真的跨站**（同一个 httptest 服务器，
// 只换 host 名）：本地实测这一对**确实**跨进程，命中栈停在 `<iframe>` 上 ——
// 与浏览器真的跨站时同形，而且**不依赖外网**。
//
// 两条断言：
//
//	① 跨站那一格：判据**说出来了**（`landing_blind` + diagnostics 里一条 landing-blind），
//	   并且可见行为与从前一样（抬起照发，不因为「判不了」就动老行为）；
//	② 正向对照（同源 iframe）：命中下钻进去了 → **不许**报盲区。
//	   没有 ② 的话，「任何 iframe 都报盲区」这种坏实现也是绿的。
func TestClickElement_CrossSiteFrameReportsBlindLanding(t *testing.T) {
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

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html")
		if r.URL.Path == "/inner.html" {
			_, _ = w.Write([]byte(`<!doctype html><html><body style="margin:0">` +
				`<button id="inner-btn" onclick="window.__inner=1" ` +
				`style="position:absolute;left:0;top:0;width:400px;height:200px">inner</button></body></html>`))
			return
		}
		src := r.URL.Query().Get("src")
		if src == "" {
			src = "inner.html" // 同源（同一个 host 名）
		}
		_, _ = w.Write([]byte(`<!doctype html><html><body style="margin:0">` +
			`<iframe id="fr" src="` + src + `" ` +
			`style="position:absolute;left:0;top:0;width:600px;height:300px;border:0"></iframe></body></html>`))
	}))
	defer srv.Close()

	crossSite := strings.Replace(srv.URL, "127.0.0.1", "localhost", 1) + "/inner.html"

	// ① 跨站
	if _, err := client.Navigate(srv.URL+"/outer.html?src="+crossSite, ""); err != nil {
		t.Fatalf("导航到跨站夹具失败: %v", err)
	}
	time.Sleep(3000 * time.Millisecond)
	probe, err := client.probeClickTarget("#fr", "")
	if err != nil || probe.MatchCount != 1 {
		t.Fatalf("夹具里的 iframe 取不到（%v）—— 下面的断言会空转", err)
	}
	// 自证这一格**真的是**跨站：命中栈停在 iframe 上（没有下钻）
	h, ok := client.hitTestAt(300, 150)
	facts, ferr := client.hitFactsOf(h)
	if !ok || ferr != nil || facts.Tag != "iframe" {
		t.Fatalf("夹具自检失败：iframe 内部那一点的命中是 %q（err=%v）—— 这一格**没有**复现出"+
			"「跨站不下钻」，下面的断言在证明一件不存在的事（先查 localhost/127.0.0.1 这一对"+
			"在当前 Chrome 上还跨不跨站）", facts.Desc, ferr)
	}
	result, err := client.ClickElement("#fr", "", false)
	if err != nil {
		t.Fatalf("跨站那一格 ClickElement 失败: %v", err)
	}
	if !result.LandingBlind {
		t.Errorf("跨站子帧里的点击**没有报盲区**（landing_blind 缺省）—— 判据在这一帧恒不触发，"+
			"而回执里一个字都不说 = 沉默的空转：调用方会以为这层保护在（result=%+v）", result)
	}
	if !strings.Contains(result.LandingNote, "iframe") || !strings.Contains(result.LandingNote, "恒不触发") {
		t.Errorf("盲区那句话没说清是哪一种盲区：%q —— 它要能回答「判据为什么在这儿不可用」", result.LandingNote)
	}
	if result.ReleaseWithheld {
		t.Errorf("判据说它判不了，却还是把抬起扣下了（covered_by=%q）—— 判不了时按老行为走"+
			"（凭据不足不额外拿走一次点击）", result.CoveredBy)
	}
	var blindDiags int
	for _, d := range client.LandingDiags() {
		if d.Kind == DiagKindLandingBlind {
			blindDiags++
		}
	}
	if blindDiags == 0 {
		t.Errorf("LandingDiags 里一条 landing-blind 都没有 —— `form` 那条路只有这份诊断能说话"+
			"（复审实测它在 form 上是 0 字节）；diags=%+v", client.LandingDiags())
	}
	t.Logf("跨站：命中=%s → landing_blind=%v，note=%q，landing-blind 诊断 %d 条",
		facts.Desc, result.LandingBlind, result.LandingNote, blindDiags)

	// ② 正向对照：同源 iframe 必须下钻进去 → **不许**报盲区
	if _, err := client.Navigate(srv.URL+"/outer.html", ""); err != nil {
		t.Fatalf("导航到同源夹具失败: %v", err)
	}
	time.Sleep(3000 * time.Millisecond)
	h2, ok2 := client.hitTestAt(300, 150)
	facts2, ferr2 := client.hitFactsOf(h2)
	if !ok2 || ferr2 != nil || facts2.Tag != "button" {
		t.Fatalf("正向对照的夹具不对：同源 iframe 内部那一点的命中是 %q（err=%v），应下钻到 <button> —— "+
			"这一格不成立的话，上面那条「跨站报盲区」就没有对照（任何 iframe 都报盲区的坏实现也会绿）",
			facts2.Desc, ferr2)
	}
	result2, err := client.ClickElement("#fr", "", false)
	if err != nil {
		t.Fatalf("同源那一格 ClickElement 失败: %v", err)
	}
	if result2.LandingBlind {
		t.Errorf("同源 iframe 里报了盲区（note=%q）—— 命中栈**下钻进去了**，判据在这儿能用。"+
			"「一律报盲区」会让这条诊断变成常驻噪音（常驻的警告等于没有警告）", result2.LandingNote)
	}
	t.Logf("对照（同源）：命中=%s → landing_blind=%v", facts2.Desc, result2.LandingBlind)
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
