package internal

import (
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/chromedp/cdproto/page"
)

// 行为验证：observe 在真页面上的四档覆盖（Task 3）。
//
// Task 2 的 observe_traps_test.go 全是**字符串断言**（脚本里出现了什么子串）——
// 它能钉住「实现特征」，但证明不了行为对不对。这一档跑真浏览器 + 真页面：
// fixture 来自 R3 探针（docs/probes/2026-09-16-observe-r3/fixtures/）。
//
// 三条陷阱各有一条对应的行为测试：
//
//	① ShadowRoot 没有 innerText      → TestObserveShadowPageTextIsNotEmpty
//	② elementsFromPoint 不穿透 shadow → TestObserveShadowElementsNotFalselyOccluded
//	③ parentElement 出不了 shadow 边界 → TestObserveShadowRegionNotAllBody
//
// ⚠️ 陷阱③那条要求 fixture 里**有 landmark**，且 landmark 必须在 shadow **外面**
// （testdata/shadow.html 的 `<main>` 就是为它加的）。2026-09-16 实测：没有 landmark
// 时「走合成树」与「只走 parentElement」两种实现**都**返回 body —— 断言对两种实现
// 一样红，空转。加了之后：正确实现 main / 陷阱③实现 body，断言才咬得住。
// 这也是 testdata/shadow.html 相对探针副本的**唯一**差异（其余 5 个 fixture 逐字节相同）。
//
// 没浏览器时 Skip（沿用 shadow_integration_test.go 的 shadowTestEndpoint）。
//
// fixture 由 httptest 自带服务（C30：不得依赖外部 mock-server，如 localhost:8080）。
// 跨源 iframe 那一档（ObserveAll 合并）按 C1 从 Task 3 移到 Task 4 ——
// 见文件末尾 TestObserveCrossOriginFrameMerge：它引用 ObserveAll，Task 3 期间
// 留着会让整个 internal 包编不过。
func serveFixtures(t *testing.T) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.FileServer(http.Dir("testdata")))
	t.Cleanup(srv.Close)
	return srv
}

func navigateAndObserve(t *testing.T, url string) *PageModel {
	t.Helper()
	host, port := shadowTestEndpoint()
	c, err := NewClient(host, port)
	if err != nil {
		t.Skipf("Chrome 不可用: %v", err) // 环境缺失 —— 全文件**只有这一处**允许 skip
	}
	t.Cleanup(c.Disconnect)
	// 导航失败**不能** skip：url 是本测试**自己刚起**的 fixture server（httptest），
	// 连不上它不是环境缺失，是缺陷。而 skip 会把整个闸门悄悄变成四条绿 skip ——
	// 跳过和通过长得一样，这条我们已经栽过。
	if _, err := c.Navigate(url, ""); err != nil {
		t.Fatalf("导航到自带 fixture server 失败（不是环境缺失，server 是本测试刚起的）: %v", err)
	}
	time.Sleep(1500 * time.Millisecond)
	m, err := c.Observe("")
	if err != nil {
		t.Fatalf("observe 失败: %v", err)
	}
	return m
}

// requireShadowActions 是「不该出现某种东西」那两条断言的前置守卫。
//
// 它们天生可以**空转通过**：actions 取到 0 个 → bad 为空、regions 为空 → 绿。
// 所以每条都得自证有效性：动作数够，且确实来自**两层 shadow 里**（shadow_depth >= 2）。
func requireShadowActions(t *testing.T, m *PageModel) {
	t.Helper()
	if len(m.Actions) < 5 {
		t.Fatalf("shadow 页只取到 %d 个可动作元素 —— 后面的断言会空转通过", len(m.Actions))
	}
	deep := 0
	for _, a := range m.Actions {
		if a.ShadowDepth >= 2 {
			deep++
		}
	}
	if deep < 5 {
		t.Fatalf("只有 %d/%d 个动作来自两层 shadow 里（shadow_depth>=2）—— 断言的不是 shadow 元素，空转",
			deep, len(m.Actions))
	}
}

func TestObserveLightDOM(t *testing.T) {
	srv := serveFixtures(t)
	m := navigateAndObserve(t, srv.URL+"/base.html")

	if m.ShadowRoots != 0 {
		t.Errorf("base.html 不该有 shadow root，实际 %d", m.ShadowRoots)
	}
	if len(m.Actions) < 5 {
		t.Errorf("可动作元素太少: %d", len(m.Actions))
	}
	// 同名按钮必须能靠 region 区分 —— 这是 observe 存在的核心理由
	seen := map[string]map[string]bool{}
	for _, a := range m.Actions {
		if a.Text == "Learn More" {
			if seen[a.Text] == nil {
				seen[a.Text] = map[string]bool{}
			}
			seen[a.Text][a.Region] = true
		}
	}
	if len(seen["Learn More"]) < 2 {
		t.Errorf("两个同名 Learn More 没被 region 区分开: %+v", seen)
	}
	if len(m.Obstructions) == 0 {
		t.Error("cookie 横幅没被识别为 obstruction")
	}
}

// 陷阱 ①：page_text 必须包含 shadow 里的文本
func TestObserveShadowPageTextIsNotEmpty(t *testing.T) {
	srv := serveFixtures(t)
	m := navigateAndObserve(t, srv.URL+"/shadow.html")

	if m.ShadowRoots < 2 {
		t.Fatalf("fixture 应有两层嵌套 shadow root，实际 %d", m.ShadowRoots)
	}
	// 踩坑时的实测值：10 个字符（只有 shadow 外面的标题）。
	// 修好后本文件量到 **142**（量的是 Observe 返回的 page_text，按 runes 数）。
	// 探针 README 记的 141 是**另一个口径**：它的 page_text_len_raw 把各 root 的
	// 原文长度直接相加 —— 本机实测 [10, 6, 125] = 141（没 join 分隔空格、也没归一化）；
	// join(' ') 后在 root 之间多出 2 个分隔空格、又折叠掉各段内部空白，净 +1 → 142。
	// 两个数都对，量的是不同的东西（2026-09-16 实测，见 task-3-report.md 修复轮 1）。
	// 阈值取 60 足以区分两种实现（10 vs 142）。
	if len([]rune(m.PageText)) < 60 {
		t.Errorf("page_text 只有 %d 字 —— shadow 里的正文没收到: %q",
			len([]rune(m.PageText)), m.PageText)
	}
	if len(m.Fields) < 3 {
		t.Errorf("表单字段应至少 3 个（阴影里），实际 %d", len(m.Fields))
	}
	for _, f := range m.Fields {
		if f.ShadowDepth < 2 {
			t.Errorf("字段 %s 的 shadow_depth 应为 2，实际 %d", f.Selector, f.ShadowDepth)
		}
	}
}

// 陷阱 ②：shadow 元素不得被误判为「被遮挡」
func TestObserveShadowElementsNotFalselyOccluded(t *testing.T) {
	srv := serveFixtures(t)
	m := navigateAndObserve(t, srv.URL+"/shadow.html")

	requireShadowActions(t, m) // 动作列表一空，下面这条就**空转通过**

	var bad []string
	for _, a := range m.Actions {
		if a.OccludedBy != nil {
			bad = append(bad, a.Text+"←"+*a.OccludedBy)
		}
	}
	// 踩坑时这里是**全假阳性**：探针 README 记 5/5；本机在 shadow.html 上实测 **6/6**
	// （该页 action 就是 6 条 = 3 字段 + 3 按钮），签名清一色 ←div#host1。
	// 断言只看「有没有」，数量口径变了也不影响。
	if len(bad) > 0 {
		t.Errorf("shadow 元素被误判为被遮挡（踩坑时全假阳性，本机实测 6/6）: %v", bad)
	}
}

// 陷阱 ③：region 不得全部退化成 body
func TestObserveShadowRegionNotAllBody(t *testing.T) {
	srv := serveFixtures(t)
	m := navigateAndObserve(t, srv.URL+"/shadow.html")

	requireShadowActions(t, m) // 动作列表一空，regions 也空 → 下面这条**空转通过**

	regions := map[string]bool{}
	for _, a := range m.Actions {
		regions[a.Region] = true
	}
	// 这条依赖 fixture 里 shadow **外面**有 landmark（testdata/shadow.html 的 <main>）：
	// 没有它的话，走合成树的正确实现与只走 parentElement 的错误实现**都**答 body，
	// 断言对两种实现一样红 = 空转。原委见 testdata/README.md。
	if len(regions) == 1 && regions["body"] {
		t.Error("region 全部退化成 body —— parentElement 出不了 shadow 边界（陷阱 ③）")
	}
}

// ─────────────────────────── 跨源 iframe（Task 4 / ObserveAll） ───────────────────────────

// hardcodedFixtureOrigin 是 testdata/outer.html 里 iframe 写死的绝对 URL 前缀
// （8892 = R3 探针那台 `python3 -m http.server` 的端口）。
const hardcodedFixtureOrigin = "http://localhost:8892"

// serveCrossOriginFixtures 起一台 fixture 服务器，并在**服务层**把 outer 页里那个
// 写死的 iframe 端口换成本次 httptest 真正用的端口。
//
// 为什么必须换（C3/C39，两位实现者独立踩过）：httptest 是**随机端口**，照搬 fixture
// 的话子帧会去连一个不存在的 localhost:8892 → 子帧连不上 → 测试以「**没有子帧**」
// 的形式**假失败**（不是报错，最难查）。
//
// 出路选的是「服务层改写 src 的端口」，不是「测试里另写一个 outer 页」：
// 页面结构仍然一字不差地来自 fixture（h1 / iframe id / 尺寸），唯一的合成物是
// origin 里的端口号，而且改写**当场自证** —— 换不上就立刻报错，不进「没有子帧」那条
// 最难查的路径。fixture 将来若改了端口，这里会以一条明确的错误说话，而不是静默退化。
//
// 跨源关系照旧：外层页走 srv.URL（httptest 绑 127.0.0.1），子帧走 localhost。
// 主机名不同 ⇒ 既不同源也不同 site ⇒ 真正的 OOPIF（实测见 task-4-report.md 第 1 节）。
func serveCrossOriginFixtures(t *testing.T) *httptest.Server {
	t.Helper()

	raw, err := os.ReadFile("testdata/outer.html")
	if err != nil {
		t.Fatalf("读 testdata/outer.html 失败: %v", err)
	}
	if !strings.Contains(string(raw), hardcodedFixtureOrigin) {
		t.Fatalf("testdata/outer.html 里找不到 %s —— 端口改写的锚点没了（fixture 被改过？），"+
			"照现在的写法子帧会连不上，而症状是「没有子帧」的假失败", hardcodedFixtureOrigin)
	}

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/outer.html" {
			http.FileServer(http.Dir("testdata")).ServeHTTP(w, r)
			return
		}
		_, port, err := net.SplitHostPort(r.Host)
		if err != nil {
			t.Errorf("从 Host %q 取端口失败: %v", r.Host, err)
			http.Error(w, "bad host", http.StatusInternalServerError)
			return
		}
		want := "http://localhost:" + port
		out := strings.ReplaceAll(string(raw), hardcodedFixtureOrigin, want)
		if !strings.Contains(out, want+"/inner.html") {
			// 静默退化的后果就是「没有子帧」假失败 —— 这里当场喊出来
			t.Errorf("outer 页的 iframe src 没被换成本次端口（想换成 %s）", want+"/inner.html")
			http.Error(w, "rewrite failed", http.StatusInternalServerError)
			return
		}
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		io.WriteString(w, out)
	}))
	t.Cleanup(srv.Close)
	return srv
}

// waitForChildFrame 轮询到子帧出现在帧树里为止；超时就把帧树打出来再 Fatal。
//
// 这是**前置条件**，不是断言：子帧没起来的话，后面每条断言都会以「合并里没有子帧
// 的东西」的形式红 —— 那正是 C3/C39 说的假失败形态。所以这里失败必须**当场说清**
// 是「子帧没起来」，而不同一个看不出所以然的红。
//
// 轮询用 GetFrameTreeWithEvents（和 ObserveAll 同一来源）：裸 GetFrameTree 在
// 跨源（OOPIF）子帧上**永远**返回空（2026-09-16 实测，本机 Chrome 150 的
// page.getFrameTree 不报 OOPIF 子帧），拿它轮询等于必然超时。
func waitForChildFrame(t *testing.T, c *Client, timeout time.Duration) *page.FrameTree {
	t.Helper()
	deadline := time.Now().Add(timeout)
	for {
		ft, err := c.GetFrameTreeWithEvents(200 * time.Millisecond)
		if err != nil {
			t.Fatalf("取帧树失败: %v", err)
		}
		if len(ft.ChildFrames) > 0 {
			return ft
		}
		if time.Now().After(deadline) {
			t.Fatalf("%s 内主帧下一个子帧都没有 —— 子帧没加载起来（最经典的假失败形态）。帧树:\n%s",
				timeout, describeFrameTree(ft))
		}
	}
}

// describeFrameTree 把帧树摊成多行文本，失败时贴进错误信息里用。
func describeFrameTree(ft *page.FrameTree) string {
	var b strings.Builder
	var walk func(ft *page.FrameTree, ind string)
	walk = func(ft *page.FrameTree, ind string) {
		if ft == nil || ft.Frame == nil {
			fmt.Fprintf(&b, "%s<nil>\n", ind)
			return
		}
		fmt.Fprintf(&b, "%s%s name=%q url=%s\n", ind, ft.Frame.ID, ft.Frame.Name, ft.Frame.URL)
		for _, ch := range ft.ChildFrames {
			walk(ch, ind+"  ")
		}
	}
	walk(ft, "")
	return b.String()
}

// findAction / findField 按 selector 精确找一条，找不到返回零值 + 报错文案。
func findAction(m *PageModel, sel string) (Action, bool) {
	for _, a := range m.Actions {
		if a.Selector == sel {
			return a, true
		}
	}
	return Action{}, false
}

func findField(m *PageModel, sel string) (Field, bool) {
	for _, f := range m.Fields {
		if f.Selector == sel {
			return f, true
		}
	}
	return Field{}, false
}

// TestObserveCrossOriginFrameMerge —— 跨源 iframe 那一档（Task 4 的交付验收）。
//
// 为什么必须有这一条：同源策略决定**单次 eval 看不见跨源帧的内容**。本测试的证据链
// 就是拿这件事做对照的：
//
//	① 主帧单帧 Observe("") —— **看不见**子帧里的 #fn / #submit（反证）
//	② 帧树里确实有一个**真的是跨源**的子帧（contentDocument === null 自证）
//	③ ObserveAll 合并后 —— 子帧的内容**在**结果里，且每条都带着指向该子帧的 frame_path
//
// 只有 ③ 没有 ①② 的话，一个「同源的 fixture」也能让它绿 —— 那测的就不是跨源。
func TestObserveCrossOriginFrameMerge(t *testing.T) {
	srv := serveCrossOriginFixtures(t)

	host, port := shadowTestEndpoint()
	c, err := NewClient(host, port)
	if err != nil {
		t.Skipf("Chrome 不可用: %v", err) // 环境缺失 —— 全文件**只有这一处**允许 skip
	}
	t.Cleanup(c.Disconnect)

	outerURL := srv.URL + "/outer.html"
	// 导航失败不能 skip：url 是本测试刚起的 fixture server，连不上是缺陷不是环境缺失
	if _, err := c.Navigate(outerURL, ""); err != nil {
		t.Fatalf("导航到自带 fixture server 失败: %v", err)
	}

	// ── 前置条件：子帧真的在帧树里（不是盲睡固定时间）──
	ft := waitForChildFrame(t, c, 10*time.Second)
	childID := string(ft.ChildFrames[0].Frame.ID)
	t.Logf("子帧 frameID=%s url=%s", childID, ft.ChildFrames[0].Frame.URL)

	// ── ② 自证跨源：同源时 contentDocument 拿得到，跨源必然是 null ──
	var probe string
	if err := c.EvalInFrame("", `(function(){var f=document.getElementById('ci');`+
		`if(!f)return 'no-iframe';return f.contentDocument===null?'cross-origin':'same-origin';})()`, &probe); err != nil {
		t.Fatalf("主帧 eval 失败: %v", err)
	}
	if probe != "cross-origin" {
		t.Fatalf("iframe 不是跨源的（contentDocument 探测 = %q）—— 这条测试就测不到跨源合并", probe)
	}

	// ── ① 反证：单帧 Observe 看不见子帧里的东西 —— 这正是 ObserveAll 存在的理由 ──
	main, err := c.Observe("")
	if err != nil {
		t.Fatalf("主帧 observe 失败: %v", err)
	}
	if _, ok := findField(main, "#fn"); ok {
		t.Errorf("主帧单帧 observe 里出现了子帧的字段 #fn —— 那就不需要 ObserveAll 了，先查这条测试是不是在测同源")
	}
	if _, ok := findAction(main, "#submit"); ok {
		t.Errorf("主帧单帧 observe 里出现了子帧的动作 #submit")
	}
	t.Logf("主帧单帧观测：fields=%d actions=%d text=%q（outer.html 只有 h1 + iframe，本就该是空的）",
		len(main.Fields), len(main.Actions), main.PageText)

	// ── ③ 合并 ──
	merged, err := c.ObserveAll()
	if err != nil {
		t.Fatalf("ObserveAll 失败: %v", err)
	}

	// URL 取先序遍历里第一个取到的帧（= 主帧）
	if merged.URL != outerURL {
		t.Errorf("merged.URL = %q，应为外层页 %q（URL 取第一帧）", merged.URL, outerURL)
	}

	wantChildPath := []string{mainFramePath, childID}

	// 子帧的字段：inner.html 的表单在**两层 shadow root** 里，能取到说明
	// 逐帧 Observe 那一趟连穿透一起在子帧里生效了
	fn, ok := findField(merged, "#fn")
	if !ok {
		t.Fatalf("合并结果里没有子帧的字段 #fn —— 跨帧合并没生效。fields=%d", len(merged.Fields))
	}
	if !slices.Equal(fn.FramePath, wantChildPath) {
		t.Errorf("#fn 的 frame_path = %v，应为 %v（agent 靠它决定动作发给哪一帧）", fn.FramePath, wantChildPath)
	}
	if fn.ShadowDepth < 2 {
		t.Errorf("#fn 的 shadow_depth = %d，应 >= 2（inner.html 把表单放在两层 shadow 里）", fn.ShadowDepth)
	}

	// 子帧的动作：同样必须带帧路径
	submit, ok := findAction(merged, "#submit")
	if !ok {
		t.Fatalf("合并结果里没有子帧的动作 #submit。actions=%d", len(merged.Actions))
	}
	if !slices.Equal(submit.FramePath, wantChildPath) {
		t.Errorf("#submit 的 frame_path = %v，应为 %v", submit.FramePath, wantChildPath)
	}

	// 子帧正文（也来自 shadow 里）必须进了 page_text
	if !strings.Contains(merged.PageText, "Get your free quote") {
		t.Errorf("合并后的 page_text 里没有子帧正文: %q", merged.PageText)
	}
	// 主帧正文也要在（合并是并集，不是覆盖）
	if !strings.Contains(merged.PageText, "跨源 iframe 外层") {
		t.Errorf("合并后的 page_text 里没有主帧正文: %q", merged.PageText)
	}

	// 所有带子帧路径的条目，路径必须一致地指向那个子帧（漏盖 / 串路径都会在这里露头）
	for _, a := range merged.Actions {
		if len(a.FramePath) > 1 && !slices.Equal(a.FramePath, wantChildPath) {
			t.Errorf("动作 %s 的 frame_path = %v，应为 %v", a.Selector, a.FramePath, wantChildPath)
		}
	}
	for _, f := range merged.Fields {
		if len(f.FramePath) > 1 && !slices.Equal(f.FramePath, wantChildPath) {
			t.Errorf("字段 %s 的 frame_path = %v，应为 %v", f.Selector, f.FramePath, wantChildPath)
		}
	}

	// 子帧的 shadow root 数并进来了（主帧 0 个，所以 >= 2 只可能来自子帧）
	if merged.ShadowRoots < 2 {
		t.Errorf("merged.ShadowRoots = %d，应 >= 2（inner.html 的两层 shadow root 没并进来）", merged.ShadowRoots)
	}

	// 正常路径上不该有「某帧取不到」的痕迹：有的话说明有帧静默失败了，
	// 而不是「少的那几条本来就没有」
	for _, o := range merged.Obstructions {
		if o.Kind == frameErrorKind {
			t.Errorf("有取不到的帧被记进 obstruction: selector=%s text=%s", o.Selector, o.Text)
		}
	}
	t.Logf("合并后：fields=%d actions=%d shadow_roots=%d page_text=%d字",
		len(merged.Fields), len(merged.Actions), merged.ShadowRoots, len([]rune(merged.PageText)))
}
