package internal

import (
	"strings"
	"testing"
)

// 视口（Defect 1）：`PageModel` 原来**没有视口尺寸**，只有每个元素的 `above_fold`。
//
// 为什么会有这两条（Task 1 spike 实测，2026-09-17 计划 §4.1）：模型被问「折叠线下面的
// 那个元素」时需要视口高，模型里找不到 → **白烧一轮**去找（又一次 observe，打的是真
// 浏览器、真页面），还是没找到，只能拿 above_fold 反推出 `528 < vh < 592` 的区间，
// 并把「这是推出来的」说出来。视口尺寸本身就是**感知**（规格 D11），却被漏了。
//
// 这一层钉的是**不依赖浏览器**的那半：
//
//   - JS 必须从 `innerWidth / innerHeight` 取（**不是** clientWidth/clientHeight ——
//     后者不含滚动条，与 cmd/screenshot.go 那条 `image_px = viewport_css_px × dpr`
//     差一个滚动条宽，静默叠不准）
//   - 合并（ObserveAll）只能拿**主帧**的视口：不求和、不让子帧盖掉、也不拿子帧的值
//     去填一个空的主帧
//
// 真浏览器那半在 cmd/observe_e2e_test.go（报出来的数**等于**浏览器说的数、
// 且**不等于** clientWidth，且合并模式拿的确实是主帧的那个）—— 那两条才是能失败的那两条。

// viewportKey 是契约字段名。**必须**与 cmd/screenshot.go 的 `--json` 一致
// （那边也是 viewport_css_px）——同一个量在一个工具里有两个名字，正是本项目反复
// 栽的那一类（窗口指纹那次把 `devicePixelRatio` 写成 `dpr`，被浏览器静默忽略，
// 结果是三倍偏移打在空气上）。这里只钉一次，改名字会同时红。
const viewportKey = "viewport_css_px"

// TestObserveJSMustEmitViewportFromInnerWidth 钉 JS 侧的**取值来源**。
//
// 为什么取值来源值得单独一条：`innerWidth` 与 `clientWidth` 在**不溢出的页面上
// 恒等**（实测：本机 headless 默认视口 780×437，两个值都是它）—— 于是「用错了哪个」
// 在大多数测试页面上根本看不出来，只在页面溢出时差一个滚动条宽，而那时症状是
// 「截图上的框叠不准」，不是报错。所以这里直接钉住来源，e2e 再在有滚动条的页面上钉行为。
func TestObserveJSMustEmitViewportFromInnerWidth(t *testing.T) {
	// observeBody 已剥掉注释：注释里为了说明「别用 clientWidth」写着这个词，
	// 不剥的话下面那条禁令会被**注释**满足（observe_traps_test.go 顶部记过这类空转）。
	body := observeBody(t)

	if !strings.Contains(body, viewportKey) {
		t.Fatalf("observeJS 返回的对象里没有 `%s` —— Go 侧那个键会静默拿到零值"+
			"（而零值 = 0×0 视口，与「这一帧没渲染」长得一样）", viewportKey)
	}
	for _, want := range []string{"window.innerWidth", "window.innerHeight"} {
		if !strings.Contains(body, want) {
			t.Errorf("脚本里没有 %s —— 视口必须从**它**取（含滚动条，与 screenshot 同一套坐标）", want)
		}
	}
	for _, banned := range []string{"clientWidth", "clientHeight"} {
		if strings.Contains(body, banned) {
			t.Errorf("脚本里出现了 %s —— 它**不含滚动条**：页面一溢出，报出来的视口就比 "+
				"cmd/screenshot.go 的 viewport_css_px 少一个滚动条宽（那张图把滚动条画进去了），"+
				"两边静默错开，叠 bbox 时症状是「叠不准」而不是报错", banned)
		}
	}
}

// TestMergeFrameModelViewportFromMainFrame 钉 ObserveAll 的合并语义：视口**只认主帧**。
//
// 三种错法各钉一次（只说「不对」说不清错在哪，而这三种在合并代码里都写得出）：
//
//	① 求和  —— 子帧的 520×520 被加进主帧，报出「视口 1600×1107」这种页面上不存在的尺寸
//	② 后到者赢 —— 帧树先序遍历里子帧排在主帧后面，于是**子帧**的尺寸冒名顶替了 tab 的
//	③ 第一个非零者赢 —— 主帧如实是 0×0 时，被某个子帧的值填上（掩盖「这一帧没渲染」）
func TestMergeFrameModelViewportFromMainFrame(t *testing.T) {
	main := &PageModel{URL: "https://a/", Title: "A", ViewportCssPx: PixelSize{Width: 780, Height: 437}}
	childA := &PageModel{ViewportCssPx: PixelSize{Width: 520, Height: 520}}
	// 两级嵌套：第二个子帧的尺寸故意再变一次，让「后到者赢」一定露头。
	childB := &PageModel{ViewportCssPx: PixelSize{Width: 300, Height: 150}}

	merged := &PageModel{}
	mergeFrameModel(merged, main, []string{mainFramePath}, true)
	mergeFrameModel(merged, childA, []string{mainFramePath, "F1"}, false)
	mergeFrameModel(merged, childB, []string{mainFramePath, "F1", "F2"}, false)

	if want := (PixelSize{Width: 780, Height: 437}); merged.ViewportCssPx != want {
		t.Errorf("合并后的视口 = %+v，want %+v（主帧的）", merged.ViewportCssPx, want)
	}
	if sum := (PixelSize{Width: 1600, Height: 1107}); merged.ViewportCssPx == sum {
		t.Errorf("视口是**加出来的**（%+v = 780+520+300 × 437+520+150）—— 页面上不存在这个尺寸，"+
			"而它看起来完全正常：消费者会拿它去判折叠、去算覆盖比例", merged.ViewportCssPx)
	}
	if merged.ViewportCssPx == childB.ViewportCssPx {
		t.Errorf("视口被**最后一个子帧**盖掉了（%+v）—— 「这个 tab 现在多大」问的不是 iframe", merged.ViewportCssPx)
	}

	// ③ 主帧如实 0×0（未渲染/隐藏的帧就是这样）：不许拿子帧的值去填。
	// 这条与上面两条不同 —— 它守的是「第一个非零者赢」那种写法，而那正是
	// 「隐藏帧」与「正常帧」在模型里长得一样的那类静默。
	hidden := &PageModel{}
	mergeFrameModel(hidden, &PageModel{}, []string{mainFramePath}, true)
	mergeFrameModel(hidden, childA, []string{mainFramePath, "F1"}, false)
	if hidden.ViewportCssPx != (PixelSize{}) {
		t.Errorf("主帧报 0×0 时合并结果是 %+v —— 被一个子帧的尺寸冒名顶替了；"+
			"0×0 是如实的感知（D11），不该被别的帧的值填上", hidden.ViewportCssPx)
	}
}
