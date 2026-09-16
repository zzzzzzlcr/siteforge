package mcp

import (
	"encoding/json"
	"errors"
	"reflect"
	"strings"
	"testing"

	"cdp/internal"

	"github.com/chromedp/cdproto/cdp"
	"github.com/chromedp/cdproto/page"
)

// 这一组测的是「工具 → 内核调用」的映射。它**不需要浏览器**：
// 桩替掉的是 interface（内核那一侧），映射写错（check=false 走成 true、
// scroll 忘了解析 iframe、form 三选一分派错）在真浏览器上多半看不出来 ——
// 页面还是那个页面，只是什么都没发生。

func TestObserveHandlerObservesWholePageByDefault(t *testing.T) {
	b := &stubBrowser{model: &internal.PageModel{URL: "https://x.example/"}}

	out, err := runHandler(t, "observe", `{}`, b)
	if err != nil {
		t.Fatalf("observe 失败: %v", err)
	}
	if got := b.lastCall(); got != "ObserveAll()" {
		t.Errorf("默认调的是 %s，期望 ObserveAll()（整页含子帧）", got)
	}
	m, ok := out.(*internal.PageModel)
	if !ok {
		t.Fatalf("observe 返回了 %T，期望 *internal.PageModel（契约就是这个类型）", out)
	}
	if m.URL != "https://x.example/" {
		t.Errorf("模型被换掉了: %+v", m)
	}
}

func TestObserveHandlerUsesSingleFrameWhenAsked(t *testing.T) {
	b := &stubBrowser{model: &internal.PageModel{URL: "https://x.example/"}}

	if _, err := runHandler(t, "observe", `{"frame_id":"FRAME1"}`, b); err != nil {
		t.Fatalf("observe 失败: %v", err)
	}
	if got := b.lastCall(); got != "Observe(FRAME1)" {
		t.Errorf("传了 frame_id 却调的是 %s", got)
	}
}

// TestObserveHandlerEnforcesExpectURL 是 CLI 那条主动闸的 MCP 版：拿错页时
// **不要返回模型**，直接报错。返回一份「合法但说的是另一个页」的模型，
// agent 会照着它往下推理 —— 那是计划一真站上踩过的坑，不是假想。
func TestObserveHandlerEnforcesExpectURL(t *testing.T) {
	b := &stubBrowser{model: &internal.PageModel{URL: "https://other.example/"}}

	_, err := runHandler(t, "observe", `{"expect_url":"wanted.example"}`, b)
	if err == nil {
		t.Fatal("页面不是预期的那个，却照样返回了模型")
	}
	if !strings.Contains(err.Error(), "other.example") {
		t.Errorf("报错里要带上**实际**拿到的 URL（否则无从判断是拿错了页还是根本没过去）: %v", err)
	}

	b2 := &stubBrowser{model: &internal.PageModel{URL: "https://wanted.example/step2"}}
	if _, err := runHandler(t, "observe", `{"expect_url":"wanted.example"}`, b2); err != nil {
		t.Errorf("URL 命中预期时被拒了: %v", err)
	}
}

func TestDiffHandlerComparesBeforeAgainstNow(t *testing.T) {
	before := &internal.PageModel{URL: "https://x.example/", PageText: "Step 1"}
	raw, _ := json.Marshal(before)
	b := &stubBrowser{model: &internal.PageModel{URL: "https://x.example/step2", PageText: "Step 2"}}

	out, err := runHandler(t, "diff", `{"before":`+string(raw)+`}`, b)
	if err != nil {
		t.Fatalf("diff 失败: %v", err)
	}
	if got := b.lastCall(); got != "ObserveAll()" {
		t.Errorf("diff 的「动作后」调的是 %s，期望 ObserveAll()", got)
	}
	d, ok := out.(internal.Diff)
	if !ok {
		t.Fatalf("diff 返回了 %T，期望 internal.Diff", out)
	}
	if !d.Actionable || !d.URLChanged {
		t.Errorf("URL 变了却没判成有推进: %+v", d)
	}
}

func TestDiffHandlerRejectsBeforeThatIsNotASnapshot(t *testing.T) {
	// 与 `cdp diff --before` 同一道闸：{} / navi 的输出 / 任何别的 JSON 都能
	// 解析成一份**全零** PageModel，拿它比 → 整页元素「新出现」→ actionable=true。
	// 那是「before 给错了」被读成「有进展」，且完全不报错。
	bads := []string{
		`null`,               // before 是 null
		`{}`,                 // 全零对象
		`"x"`,                // 根本不是对象
		`[]`,                 // 也不是对象
		`{"hello":"world"}`,  // 别的 JSON
		`{"url":""}`,         // 有一半形状，但没有 URL 也没有元素
		`{"url":"","actions":null,"fields":null,"option_groups":null}`,
	}
	for _, bad := range bads {
		b := &stubBrowser{model: &internal.PageModel{URL: "https://x.example/"}}
		if _, err := runHandler(t, "diff", `{"before":`+bad+`}`, b); err == nil {
			t.Errorf("diff 的 before=%s 被当成了一份合法快照", bad)
		}
	}

	// 另一半：真快照必须放过（URL 是判据的核心 —— 空白页也有 "about:blank"）。
	good := []string{
		`{"url":"about:blank","actions":[],"fields":[],"option_groups":[]}`,
		`{"url":"https://x.example/","actions":[{"selector":"#go","text":"Continue"}]}`,
	}
	for _, ok := range good {
		b := &stubBrowser{model: &internal.PageModel{URL: "https://x.example/"}}
		if _, err := runHandler(t, "diff", `{"before":`+ok+`}`, b); err != nil {
			t.Errorf("diff 把合法快照 %s 拒了: %v", ok, err)
		}
	}
}

// TestClickHandlerPassesSelectorFrameAndTrack 钉两件事：参数映射，
// 以及**这道门走的是严格那一版**（ClickElementStrict）。
//
// 后者为什么值得单独钉：宽松/严格两个入口都在内核里，把这里接回宽松那一版，
// 「歧义选择器静默点到 Back」会**悄无声息地**回到 agent 手上，而 CLI 那边的
// 测试一条都不会红（两边的默认故意不一样）。
func TestClickHandlerPassesSelectorFrameAndTrack(t *testing.T) {
	b := &stubBrowser{}

	out, err := runHandler(t, "click", `{"selector":"#go","frame_id":"F1","track":true}`, b)
	if err != nil {
		t.Fatalf("click 失败: %v", err)
	}
	if got := b.lastCall(); got != "ClickElementStrict(#go,F1,true)" {
		t.Errorf("click 调的是 %s —— 这道门必须是**严格**那一版"+
			"（宽松那版会对歧义选择器静默取第一个）", got)
	}
	res, ok := out.(*internal.ClickResult)
	if !ok {
		t.Fatalf("click 返回了 %T —— 落点坐标与「实际点到谁」都在这份回执里", out)
	}
	if res.X != 1 || res.Y != 2 {
		t.Errorf("落点坐标被换掉了: %+v —— 它是「到底点在哪」的唯一证据", res)
	}
}

func TestFormHandlerRoutesToTheRightKernelCall(t *testing.T) {
	cases := []struct {
		name, args, want string
	}{
		{"填值", `{"selector":"#z","value":"30301"}`, "FillText(#z,30301,,false)"},
		{"勾上", `{"selector":"#c","check":true}`, "CheckElement(#c,true,,false)"},
		// false 是**传了**（取消勾选），不是没传 —— 这条一旦走错，
		// 「取消勾选」会变成「勾上」，而且页面照样有反应。
		{"取消勾选", `{"selector":"#c","check":false}`, "CheckElement(#c,false,,false)"},
		{"选下拉", `{"selector":"#s","select":"CA"}`, "SelectOption(#s,CA,,false)"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			b := &stubBrowser{}
			if _, err := runHandler(t, "form", tc.args, b); err != nil {
				t.Fatalf("form 失败: %v", err)
			}
			if got := b.lastCall(); got != tc.want {
				t.Errorf("form %s 调的是 %s，期望 %s", tc.args, got, tc.want)
			}
			if len(b.calls) != 1 {
				t.Errorf("一次 form 打了 %d 次内核调用: %v", len(b.calls), b.calls)
			}
		})
	}
}

// TestScrollHandlerResolvesIframeFirst 抄的是 CLI 的 scroll：跨源 iframe 里
// 的元素**够不着**（选择器是外层文档的坐标系），得先用 frame_id 换出 iframe 元素
// 的选择器，滚它，再滚里面那个元素。漏掉第一步的话，滚动**不报错**、只是没动。
func TestScrollHandlerResolvesIframeFirst(t *testing.T) {
	b := &stubBrowser{iframeSelector: "#the-iframe"}

	if _, err := runHandler(t, "scroll", `{"selector":"#deep","frame_id":"F1"}`, b); err != nil {
		t.Fatalf("scroll 失败: %v", err)
	}
	want := []string{
		"ResolveIframeSelector(F1)",
		"ScrollToElement(#the-iframe,false)",
		"ScrollIntoView(#deep,F1)",
	}
	if !reflect.DeepEqual(b.calls, want) {
		t.Errorf("scroll 的调用序列 = %v，期望 %v", b.calls, want)
	}
}

func TestScrollHandlerWithoutFrameSkipsIframeResolution(t *testing.T) {
	b := &stubBrowser{}

	if _, err := runHandler(t, "scroll", `{"selector":"#deep"}`, b); err != nil {
		t.Fatalf("scroll 失败: %v", err)
	}
	want := []string{"ScrollToElement(#deep,false)", "ScrollIntoView(#deep,)"}
	if !reflect.DeepEqual(b.calls, want) {
		t.Errorf("scroll 的调用序列 = %v，期望 %v", b.calls, want)
	}
}

func TestGotoHandlerNavigates(t *testing.T) {
	b := &stubBrowser{tree: &page.FrameTree{Frame: &cdp.Frame{ID: "MAIN", URL: "https://x.example/"}}}

	out, err := runHandler(t, "goto", `{"url":"https://x.example/"}`, b)
	if err != nil {
		t.Fatalf("goto 失败: %v", err)
	}
	if got := b.lastCall(); got != "Navigate(https://x.example/,)" {
		t.Errorf("goto 调的是 %s", got)
	}
	res, ok := out.(map[string]any)
	if !ok {
		t.Fatalf("goto 返回了 %T，期望一个对象", out)
	}
	if res["url"] != "https://x.example/" {
		t.Errorf("goto 的结果里没有落地 URL: %#v", res)
	}
}

func TestScreenshotHandlerReturnsCoordinatesEvidence(t *testing.T) {
	b := &stubBrowser{shot: &internal.Shot{PNGBase64: "AAA", DPR: 2}}

	out, err := runHandler(t, "screenshot", `{}`, b)
	if err != nil {
		t.Fatalf("screenshot 失败: %v", err)
	}
	shot, ok := out.(*internal.Shot)
	if !ok {
		t.Fatalf("screenshot 返回了 %T，期望 *internal.Shot", out)
	}
	// 坐标证据必须跟着图一起出去：只给图，agent 就无法把 observe 的 bbox
	// 叠到图上（那是 Console 上「运营框选元素」那条交互的地基）。
	if shot.DPR != 2 || shot.PNGBase64 != "AAA" {
		t.Errorf("截图丢了坐标证据: %+v", shot)
	}
}

// TestHandlersSurfaceKernelErrors 钉住「内核报错要**原样带出去**」。
// 吞掉它的后果：工具报告「成功」，而浏览器那边什么都没发生。
func TestHandlersSurfaceKernelErrors(t *testing.T) {
	boom := errors.New("selector not found: #nope")
	for _, name := range []string{"observe", "screenshot", "click", "form", "scroll", "goto", "diff"} {
		args := map[string]string{
			"observe":    `{}`,
			"diff":       `{"before":{"url":"https://x.example/","actions":[]}}`,
			"screenshot": `{}`,
			"click":      `{"selector":"#a"}`,
			"form":       `{"selector":"#a","value":"x"}`,
			"scroll":     `{"selector":"#a"}`,
			"goto":       `{"url":"https://x.example/"}`,
		}[name]
		b := &stubBrowser{err: boom}
		_, err := runHandler(t, name, args, b)
		if err == nil {
			t.Errorf("%s 把内核的错误吞了（内核说 %v，工具说成功）", name, boom)
			continue
		}
		if !errors.Is(err, boom) && !strings.Contains(err.Error(), boom.Error()) {
			t.Errorf("%s 报的错丢了内核的原话: %v", name, err)
		}
	}
}
