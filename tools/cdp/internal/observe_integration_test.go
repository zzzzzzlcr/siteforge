package internal

import (
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
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

// viewport 是观测当时浏览器与文档的尺寸 —— bbox 的边界/自洽断言要拿它们当尺子。
type viewport struct{ w, h, docW, docH int }

// measureViewport 取**同一个浏览器**此刻的视口与文档尺寸。
//
// 为什么要单独取一次：PageModel 里**没有**尺寸字段，而「bbox 落在哪」只有对着
// 当时的尺寸才有意义。另开一条连接而不是改 navigateAndObserve 的签名 —— 那条
// helper 被本文件与 observe_selector_test.go 共用，为一个断言动它不划算
// （frameID 传空串 = 主帧默认世界求值，不建隔离世界、不改页面，纯读）。
func measureViewport(t *testing.T) viewport {
	t.Helper()
	host, port := shadowTestEndpoint()
	c, err := NewClient(host, port)
	if err != nil {
		// **不是** skip：同一个浏览器刚被这条测试观测过，此刻连不上是缺陷
		t.Fatalf("量尺寸：连不上刚才还在用的那个 Chrome: %v", err)
	}
	defer c.Disconnect()
	// 逐个取**数字**：EvalInFrame 只解按值返回的结果（字符串/数字/布尔），
	// 数组是对象、不带 returnByValue 拿回来是空的 —— 实测过：`[innerWidth, innerHeight]`
	// 解出来是 nil，而 nil 会让边界断言**悄悄失去意义**。
	var vw, vh, dw, dh int
	for _, n := range []struct {
		label, js string
		dst       *int
	}{
		{"视口宽", "innerWidth", &vw},
		{"视口高", "innerHeight", &vh},
		{"文档宽", "Math.max(document.documentElement.scrollWidth, innerWidth)", &dw},
		{"文档高", "Math.max(document.documentElement.scrollHeight, innerHeight)", &dh},
	} {
		var v float64
		if err := c.EvalInFrame("", n.js, &v); err != nil {
			t.Fatalf("取%s失败: %v", n.label, err)
		}
		*n.dst = int(v)
	}
	if vw <= 0 || vh <= 0 || dw < vw || dh < vh {
		t.Fatalf("量出来的尺寸不像话：视口 %d×%d、文档 %d×%d —— 边界断言会失去意义", vw, vh, dw, dh)
	}
	return viewport{w: vw, h: vh, docW: dw, docH: dh}
}

// requireActionInvariants 是 D11 感知面的**逐动作不变量**（2026-09-16 终审 C77 必修）。
//
// 为什么必须有这一层：JS→Go 的绑定**按字符串键**（observeJS 末尾那个对象字面量 ↔
// PageModel 的 json tag）。任何一个键写错（`nearby_text` 拼成 `nearbyText`、
// `above_fold` 漏了），那个字段就**静默变成零值** —— 而在此之前，
// Role/Tag/Visible/BBox/AboveFold/RelativeSize/PeerCount/ZIndex/Contrast/NearbyText
// 这十个字段在**整套 124 条测试里的断言数是 0**：绑定整块断掉也不会有人说话。
//
// 每条断言都按「零值与真值可区分」挑过（断言零值 = 空转，那正是要根治的病）：
//
//	bbox         零值 [0,0,0,0] 是键写错的指纹；负原点/超出文档 = 坐标口径错
//	             （R17 要拿它在**截图**上画框，对不上就没法「框选元素」）
//	above_fold   与 bbox **自洽**：判据就是 `bbox 的 y < 视口高`（JS 侧同一个式子）
//	             —— 这一条把两个字段绑在一起，任一键错位 / y 被整体缩放都会露头
//	tag/role     都是恒非空串（tagName / role||tag）→ 空串只可能是没绑上
//	visible      JS 侧写死 true（收进来的元素都过了 vis() 过滤）—— 它守的是**键**
//	z_index      getComputedStyle 恒返回串（"auto" 也算）→ 空串 = 没绑上
//	peer_count   ≥1 恒成立（计数里包含自己）→ 0 只可能是没绑上
//	relative_size 中位数面积 > 0 → 每个都该是正数
//	contrast     祖先链全透明时它**就是**空串（合法值，见 lum()），所以这里只做
//	             「要么空、要么是三档之一」的**形态**检查；真值那半在调用方
//	             用 hero 按钮单独钉（那里背景不透明，必然算得出来）
//
// ⚠️ **没有**「每条 bbox 都在视口内」这条：它不是正确的不变量。observe 收的是
// **整页**的可见元素，折线以下的元素本来就合法 —— 实测 base.html 的脚注链接
// （headless 默认窗口 780×437）bbox=[30 446 72 16]，y 就在视口外，而它是**对的**。
// 换成上面那组环境无关的断言（自洽 + 文档范围），一样咬得住键错位与坐标口径错。
func requireActionInvariants(t *testing.T, m *PageModel, vp viewport) {
	t.Helper()
	if len(m.Actions) == 0 {
		t.Fatalf("actions 为空 —— 逐动作不变量会**空转通过**")
	}
	bands := map[string]bool{"high": true, "medium": true, "low": true}
	for i, a := range m.Actions {
		where := fmt.Sprintf("actions[%d]（%s）", i, a.Selector)
		x, y, w, h := a.BBox[0], a.BBox[1], a.BBox[2], a.BBox[3]
		if w <= 0 || h <= 0 {
			t.Errorf("%s 的 bbox 宽高为 %d×%d —— 零值或负值只可能是那个键没绑上: %v", where, w, h, a.BBox)
		}
		if x < 0 || y < 0 {
			t.Errorf("%s 的 bbox 原点是负的 %v —— 元素在视口左上角之外（或坐标口径错）", where, a.BBox)
		}
		if x+w > vp.docW || y+h > vp.docH {
			t.Errorf("%s 的 bbox %v 超出了文档范围 %d×%d —— R17 要拿它在截图上画框，"+
				"框对不上元素就等于「框选元素」这条路整个不成立", where, a.BBox, vp.docW, vp.docH)
		}
		// above_fold 与 bbox 必须自洽（同一把尺子：视口高）
		if want := y < vp.h; a.AboveFold != want {
			t.Errorf("%s 的 above_fold=%t，而它的 bbox.y=%d 与视口高 %d 给出的答案是 %t"+
				" —— 两个字段用的是同一个判据，对不上就说明其中一个没绑对",
				where, a.AboveFold, y, vp.h, want)
		}
		if a.Tag == "" || a.Role == "" {
			t.Errorf("%s 的 tag=%q role=%q —— 两个都该恒非空（tagName / role||tag），空串=键没绑上", where, a.Tag, a.Role)
		}
		if !a.Visible {
			t.Errorf("%s 的 visible=false —— JS 收进来的元素都过了 vis() 过滤，这里只可能是键没绑上", where)
		}
		if a.ZIndex == "" {
			t.Errorf("%s 的 z_index 是空串 —— getComputedStyle 恒返回串（\"auto\" 也算），空串=键没绑上", where)
		}
		if a.PeerCount < 1 {
			t.Errorf("%s 的 peer_count=%d —— 计数里包含元素自己，恒 >=1", where, a.PeerCount)
		}
		if a.RelativeSize <= 0 {
			t.Errorf("%s 的 relative_size=%v —— 页面有可见元素时中位数面积 >0，每个都该是正数", where, a.RelativeSize)
		}
		if a.Contrast != "" && !bands[a.Contrast] {
			t.Errorf("%s 的 contrast=%q —— 只该是 high/medium/low 或空串（背景全透明时算不出来）", where, a.Contrast)
		}
	}
}

func TestObserveLightDOM(t *testing.T) {
	srv := serveFixtures(t)
	m := navigateAndObserve(t, srv.URL+"/base.html")

	if m.ShadowRoots != 0 {
		t.Errorf("base.html 不该有 shadow root，实际 %d", m.ShadowRoots)
	}
	if len(m.Actions) < 5 {
		t.Fatalf("可动作元素太少: %d —— 后面的断言会**空转通过**", len(m.Actions))
	}
	// D11 感知面的逐动作不变量：bbox 非零/非负/落在文档内、above_fold 与 bbox
	// 自洽，以及 tag/role/visible/z_index/peer_count/relative_size/contrast
	// 的**绑定性** —— 逐条理由（含为什么没有「都在视口内」）见该 helper 的注释。
	vp := measureViewport(t)
	requireActionInvariants(t, m, vp)

	// 具体元素的**具体值**（上面那一圈只管「非零/形态」；这里能抓住键**串位**
	// 这类「非零但错」的绑定错误 —— 比如 role 与 tag 接反）
	hero, ok := findAction(m, "#schedule-now")
	if !ok {
		t.Fatalf("base.html 里找不到 hero 按钮 #schedule-now —— 下面的断言会空转: %q", selectorsOf(m.Actions))
	}
	if hero.Tag != "BUTTON" || hero.Role != "button" {
		t.Errorf("hero 按钮的 tag=%q role=%q，应为 BUTTON / button（role 缺省回落成小写标签名）", hero.Tag, hero.Role)
	}
	if hero.Region != "hero" {
		t.Errorf("hero 按钮的 region = %q，应为 hero（base.html 的 section.hero 是 class 派生的那一格）", hero.Region)
	}
	// nearby_text：规格 §4.3 的感知字段，零值 = 键没绑上。
	// fixture 里它前一个兄弟是 <p>、再前一个是 <h1>，所以**必然**收得到东西。
	if len(hero.NearbyText) == 0 {
		t.Error("hero 按钮的 nearby_text 为空 —— 它的前后兄弟里有 <p>/<h1> 文本，收不到只可能是那个键没绑上")
	}
	for i, s := range hero.NearbyText {
		if strings.TrimSpace(s) == "" {
			t.Errorf("hero 按钮 nearby_text[%d] 是空白串 —— JS 侧只推非空文本进来", i)
		}
	}
	// contrast 的真值那半放在这里（循环里只能做形态检查）：这个按钮自己的背景
	// 不透明，必然算得出一档 —— 空串在这里就是**键没绑上**。
	if hero.Contrast != "high" && hero.Contrast != "medium" && hero.Contrast != "low" {
		t.Errorf("hero 按钮的 contrast = %q —— 它背景不透明，应算出一档（high/medium/low）", hero.Contrast)
	}

	head, ok := findAction(m, "#nav-learn")
	if !ok {
		t.Fatalf("base.html 里找不到 header 链接 #nav-learn —— 下面的断言会空转: %q", selectorsOf(m.Actions))
	}
	if !head.AboveFold {
		t.Errorf("header 里的链接 above_fold=false —— 它在页面顶端（box=%v，视口高 %d）",
			head.BBox, vp.h)
	}
	if head.Tag != "A" || head.Role != "a" {
		t.Errorf("header 链接的 tag=%q role=%q，应为 A / a", head.Tag, head.Role)
	}
	if head.Region != "header" {
		t.Errorf("header 链接的 region = %q，应为 header（<header> 是 landmark，两趟扫的第一趟就命中）", head.Region)
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

// selectorsOf 把动作列表压成一串选择器 —— 只用在 Fatal 的消息里
// （「找不到那个元素」的话术里得带上实际看到了什么，否则无从查起）。
func selectorsOf(actions []Action) []string {
	out := make([]string, 0, len(actions))
	for _, a := range actions {
		out = append(out, a.Selector)
	}
	return out
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

	// Field 的 type / required / hint —— 这三个键此前在整套里的断言数是 0（终审 C77）。
	// **正例只有放在这里才不是空转**：shadow.html 的模板里 #fn 真的带 required，
	// 而 selector.html 里没有任何 required 的 input —— 只断言 false 等于在断言零值。
	if fn, ok := findField(m, "#fn"); !ok {
		t.Errorf("shadow 页里找不到字段 #fn（模板里写死的那个 input）—— 下面三条会空转")
	} else {
		if fn.Type != "text" {
			t.Errorf("#fn 的 type = %q，应为 text（零值 = 那个键没绑上）", fn.Type)
		}
		if !fn.Required {
			t.Error("#fn 在 fixture 里带 required 属性，却报成 false —— 这个键没绑上（零值也是 false）")
		}
		if fn.Hint == "" {
			t.Error("#fn 的 hint 为空 —— hint 取的是 name||id，这个 input 有 name=firstName，空串=键没绑上")
		}
	}
	if ph, ok := findField(m, "#ph"); !ok {
		t.Errorf("shadow 页里找不到字段 #ph —— 下面这条会空转")
	} else if ph.Type != "tel" {
		// 换一个**不同的** type 值：只钉 text 的话，「把所有 type 都写成 text」
		// 那种错照样绿（tel 不是零值，能真的区分）
		t.Errorf("#ph 的 type = %q，应为 tel（fixture 里写的就是 type=tel）", ph.Type)
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
// 主机名不同 ⇒ 既不同源也不同 site ⇒ 真正的 OOPIF（原始协议证据见 testdata/README.md「帧枚举」一节）。
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
// 为什么必须有这一条：同源策略决定**单次 eval 看不见跨源帧的内容**。判据分四段，
// 前两段是「这条测试有没有测到跨源」的自证，后两段才是合并本身：
//
//	判据 1（主判据）：从**子帧自己**取 location.href，host 必须与主帧不同
//	判据 2：父帧的 JS 够不到子帧（contentDocument === null）
//	判据 3：ObserveAll 合并后子帧的内容**在**结果里，且带着指向该子帧的 frame_path
//	判据 4：退化守卫（checkFrameCoverage）在「帧树被截断」时**必须喊**
//
// ⚠️ 判据 1 是**唯一**能同时担保「跨源」和「子帧文档真的 commit 了」的一条，
// 所以它是主判据：判据 2 单独用会有一个没验的口子（同源帧若尚未 commit 文档，
// contentDocument 也是 null → 同源误配照样绿）；判据 3 单独用则**同源夹具也能过**
// （合并逻辑对同源子帧一样工作）。
//
// ⚠️ **不要**拿「主帧单帧观测看不见子帧里的 #fn」当跨源判据（Task 4 修复轮 1 删掉了
// 那条）：穿透助手只跟 .shadowRoot、**从不进 contentDocument**，所以主帧在任何情况下
// 都看不见 iframe 的内容 —— 同源也一样，它对源的异同完全不敏感。
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

	// ── 判据 1：从子帧自己取 location.href，host 必须与主帧不同 ──
	var mainHref, childHref string
	if err := c.EvalInFrame("", "location.href", &mainHref); err != nil {
		t.Fatalf("从主帧取 location.href 失败: %v", err)
	}
	// 这一行本身就是「跨源子帧能 eval」的证据：它走的是 OOPIF 回退
	// （page 目标上的 CreateIsolatedWorld 对 OOPIF 必然失败，实测 -32602）
	if err := c.EvalInFrame(childID, "location.href", &childHref); err != nil {
		t.Fatalf("从子帧取 location.href 失败: %v", err)
	}
	mainU, err := url.Parse(mainHref)
	if err != nil {
		t.Fatalf("主帧 href 解析失败 %q: %v", mainHref, err)
	}
	childU, err := url.Parse(childHref)
	if err != nil {
		t.Fatalf("子帧 href 解析失败 %q: %v", childHref, err)
	}
	// 子帧文档真的 commit 了、且就是 fixture 那一页（不是 about:blank、不是错误页）
	// —— 顺带堵掉「帧存在 ≠ 文档就绪」那个假失败口子
	if !strings.HasSuffix(childU.Path, "/inner.html") {
		t.Fatalf("子帧 href = %q，不是 fixture 的 inner.html —— 子帧没加载起来或加载失败", childHref)
	}
	if mainU.Host == childU.Host {
		t.Fatalf("子帧与主帧**同源**（host 都是 %q）—— 这条测试测不到跨源合并，"+
			"先查 fixture 的 iframe src 是不是被改成了同源地址", mainU.Host)
	}
	t.Logf("主帧 %s ／ 子帧 %s —— host 不同（真跨源，且子帧文档已 commit）", mainHref, childHref)

	// ── 判据 2：父帧的 JS 确实够不到子帧 —— 同源策略在这一对帧上真的生效 ──
	// （判据 1 已证明子帧文档 commit 了，所以这条不再有「还没 commit 也返回 null」
	//   那个口子；它证明的是另一半：**必须**逐帧 eval 才行）
	var probe string
	if err := c.EvalInFrame("", `(function(){var f=document.getElementById('ci');`+
		`if(!f)return 'no-iframe';return f.contentDocument===null?'cross-origin':'same-origin';})()`, &probe); err != nil {
		t.Fatalf("主帧 eval 失败: %v", err)
	}
	if probe != "cross-origin" {
		t.Fatalf("主帧能碰到子帧文档（contentDocument 探测 = %q）—— 这一对帧不是跨源", probe)
	}

	// ── 判据 3：合并 ──
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

	// 判据 3 附加：正常路径上 diagnostics 必须是**空的**。
	// 有 frame-error = 有帧静默失败了（不是「那几条本来就没有」）；
	// 有 frame-blind = 守卫自己喊了（枚举可能不完整）。两种都得让这条测试红，
	// 否则诊断通道就成了摆设 —— 这正是本项目最忌的那种「写了但没人验证会响」。
	if len(merged.Diagnostics) != 0 {
		t.Errorf("正常路径不该有诊断，实际 %d 条: %+v", len(merged.Diagnostics), merged.Diagnostics)
	}
	// 遮挡物通道要干净：帧的问题**不许**再混进 obstructions
	// （混进去会让忽略 kind 的消费者拿 frameId 当选择器去点）
	for _, o := range merged.Obstructions {
		if o.Kind == DiagKindFrameError {
			t.Errorf("帧取不到被记进了 obstructions（kind=%s）—— 两个通道没分开", o.Kind)
		}
	}
	t.Logf("合并后：fields=%d actions=%d shadow_roots=%d page_text=%d字 diagnostics=%d",
		len(merged.Fields), len(merged.Actions), merged.ShadowRoots, len([]rune(merged.PageText)),
		len(merged.Diagnostics))

	// ── 判据 4：退化守卫必须会喊 ──
	//
	// 把帧树截成「只剩主帧」—— 这正是 DOM 穿透失败时 GetFrameTreeWithEvents 返回的形态
	// （本机实测：跨源页上裸 page.getFrameTree 就是这个输出，见 testdata/README.md）。
	// 此时主帧 DOM 里明明有 iframe 元素，枚举却报 0 个子帧 → 守卫必须开口。
	// 不这么测的话，它就是一段从没被观察到「会响」的代码。
	truncated := &page.FrameTree{Frame: ft.Frame} // 同一次导航的帧树，掐掉所有子帧
	guardModel := &PageModel{}
	c.checkFrameCoverage(guardModel, "", truncated, []string{mainFramePath})
	blind := false
	for _, d := range guardModel.Diagnostics {
		if d.Kind == DiagKindFrameBlind {
			blind = true
		}
	}
	if !blind {
		t.Errorf("帧树被截断成「只剩主帧」之后守卫没喊 —— 跨源子帧整个消失会变成静默的（diagnostics=%+v）",
			guardModel.Diagnostics)
	}
}
