package cmd

import (
	"strconv"
	"strings"
	"testing"

	"cdp/internal"
)

// 蜜罐（off-document 陷阱）的真闸门 —— 规格 R19b。
//
// 真站实测（2026-09-16，check.compareinsulation.io 的漏斗页）：注册表单里埋着
//
//	<input name="company_url" type="text" style="…left:-9983px…">
//
// 人看不见它，bot 填了就被站点标记成机器人。而 observe 当时把它**同时**列进
// actions 与 fields，两边都评 `stability: high` —— 等于把陷阱当成高置信度的
// 主路径递给 agent。旧系统早有「蜜罐跳过」（auto-farm-skill db792c1），observe 没有。
//
// 这条闸门钉三个方向（缺一个都测不出这个缺陷）：
//
//	① 陷阱**不在** actions / fields 里（排除生效）
//	② 陷阱**在** honeypots 里，三个键都在（排除 ≠ 静默丢：消费者要看得见）
//	③ 正向对照：**滚出视口、但滚得到**的正常元素**仍在** actions / fields 里
//
// ⚠️ ③ 才是这条测试的价值所在，也是它为什么必须**先滚动再观测**：
// 页面没滚动时，文档坐标判据与视口坐标判据给出**完全一样**的答案（陷阱在两边都是
// 负的、正常元素在两边都是正的）—— ③ 就成了白给的，把「判据用的是哪套坐标」这件事
// 整个放过去。滚到 (600,600) 之后两者才分岔：正常元素的**视口**坐标变成负数
// （fixture 里那个按钮 rect.left = 8-600 = -592，宽 120 ⇒ 视口判据会把它当陷阱），
// 而**文档**坐标一个字没变（8 ⇒ 8+120 > 0）。所以「滚动」是前置条件，不是装饰；
// 对照组元素也必须**自证**它此刻真的满足视口判据的陷阱条件（见 requireViewportTrapLike），
// 否则实现换成视口判据时这个对照可能照样绿。
//
// 为什么在 cmd/：这里走的是 py 运行时唯一那条路的同一套接口（cdp observe 的 stdout
// 与退出码），且用**私有** headless Chrome —— internal 那批集成测试会往共享页里注入
// DOM（见 observe_e2e_test.go 顶部：两边抢同一个页面目标，实测 3/3 串页）。
func TestObserveCommandExcludesHoneypots(t *testing.T) {
	e := env(t)
	fixtureURL := e.fixture + "/honeypot.html"
	e.navigate(t, fixtureURL)

	// ── 前置：先滚过去，正向对照才成立 ──
	// 量的是**滚动真的发生了**（而不是「我调过 scrollTo」）：scrollX/scrollY 是唯一凭据。
	e.evalJS(t, "window.scrollTo(600, 600)")
	scrollX := e.evalFloat(t, "window.scrollX")
	scrollY := e.evalFloat(t, "window.scrollY")
	if scrollX < 600 || scrollY < 600 {
		t.Fatalf("页面没滚起来（scrollX=%v scrollY=%v，要都 >= 600）—— "+
			"没滚动时文档坐标判据与视口坐标判据给出同样的答案，③ 会退化成白给的对照", scrollX, scrollY)
	}

	// 两个对照组元素，各自自证「此刻视口坐标下长得**就像**一个陷阱」。
	// 少了这一层，实现换成视口判据时它们可能还好好待在视口里，对照就空转了
	// ——「不该出现 X」那类断言的空转形态，本项目栽过多次。
	reachLabel := "#reachable（正常流里的按钮，滚出视口但滚得到）"
	reachLeft, reachWidth := requireViewportTrapLike(t, e, reachLabel, `document.getElementById('reachable')`)
	emailLabel := `input[name="email"]（正常字段）`
	emailLeft, emailWidth := requireViewportTrapLike(t, e, emailLabel, `document.querySelector('input[name=email]')`)

	// ── 观测 ──
	out, errOut, code := e.observe(t)
	if code != 0 {
		t.Fatalf("observe 退出码 = %d, want 0\nstderr: %s", code, errOut)
	}
	m := decodeModel(t, out)
	if m.URL != fixtureURL {
		t.Fatalf("模型 url = %q，want %q —— 观测的不是这条测试导航过去的页", m.URL, fixtureURL)
	}

	// ── ① 陷阱不在 actions / fields 里 ──
	// 按**名字**找（真站上它就是被名字认出来的那条 input）：选择器里出现这两个 name
	// 就说明陷阱又回到模型里了。两条路分开断言 —— 它们坏起来是独立的，
	// 而这个缺陷**原来的样子**恰恰是「两条路各漏一次」。
	for _, a := range m.Actions {
		if strings.Contains(a.Selector, "company_url") || strings.Contains(a.Selector, "fax_number") {
			t.Errorf("陷阱仍在 actions 里: selector=%q stability=%q —— agent 会照它去填，"+
				"然后被站点标记成机器人（真站实测就是这个形态）", a.Selector, a.Stability)
		}
	}
	for _, f := range m.Fields {
		if strings.Contains(f.Selector, "company_url") || strings.Contains(f.Selector, "fax_number") {
			t.Errorf("陷阱仍在 fields 里: selector=%q stability=%q", f.Selector, f.Stability)
		}
	}

	// ── ② 陷阱在 honeypots 里，三个键都带上 ──
	// 这一半是「排除 ≠ 静默丢」：丢干净的话，消费者分不清「这页本来就没这些字段」
	// 和「有字段、但被判成陷阱丢了」。
	// ⚠️ 这里是 Errorf 不是 Fatalf：判据用错坐标时**这一条与 ③ 会同时红**
	// （视口坐标下对照组那两个正常元素也会被当陷阱记进来），而 ③ 那两条证据
	// 恰恰是「为什么错」的答案 —— Fatalf 会把它俩一键吞掉，只剩一句「条数不对」。
	if len(m.Honeypots) != 2 {
		t.Errorf("honeypots = %d 条，want 2（两条轴各一条）—— 空/少 = 被静默丢了，"+
			"多 = 同一条陷阱记了两次（那正是原来 actions/fields 各报一次的翻版）: %+v",
			len(m.Honeypots), m.Honeypots)
	}
	wantTraps := []struct{ selector, hint, why string }{
		{`input[name="company_url"]`, "company_url", "off-document-left"}, // 真站形态：屏幕外左侧
		{`input[name="fax_number"]`, "fax_number", "off-document-top"},    // 同一判据的另一条轴
	}
	bySelector := map[string]internal.Honeypot{}
	for _, h := range m.Honeypots {
		if _, dup := bySelector[h.Selector]; dup {
			t.Errorf("honeypots 里同一条记了两次: %q —— 两条收集路（actions / fields）都查了它，去重没生效",
				h.Selector)
		}
		bySelector[h.Selector] = h
	}
	for _, want := range wantTraps {
		h, ok := bySelector[want.selector]
		if !ok {
			t.Errorf("honeypots 里没有 %q —— 陷阱从 actions/fields 里消失了，却没有在 honeypots 里报出来"+
				"（消费者会以为这一页本来就没这些字段）: %+v", want.selector, m.Honeypots)
			continue
		}
		// hint 只是**线索**（名字会腐烂）；why 是**判据名**（哪条轴落在文档坐标的负区）。
		if h.Hint != want.hint {
			t.Errorf("%q 的 hint = %q，want %q（取 name||id||placeholder）", want.selector, h.Hint, want.hint)
		}
		if h.Why != want.why {
			t.Errorf("%q 的 why = %q，want %q（判据名：哪条轴落在文档坐标的负区）",
				want.selector, h.Why, want.why)
		}
	}

	// ── ③ 正向对照：滚出视口、但滚得到的元素**必须还在** ──
	// 两个元素此刻都 left+width <= 0（上面已自证）——视口坐标判据会把它们一起丢掉。
	// 这两条红了，说明判据用错了坐标（判的是视口而不是文档）。
	if !hasSelector(m.Actions, "#reachable") {
		t.Errorf("%s 不在 actions 里了 —— 它此刻视口坐标 left=%v width=%v（left+width=%v <= 0），"+
			"但**文档**坐标是正的（滚得到）。判据若用**视口**坐标，就会把它连同一切"+
			"「滚上去看不见」的正常内容一起静默丢掉。实际 actions: %v",
			reachLabel, reachLeft, reachWidth, reachLeft+reachWidth, selectors(m.Actions))
	}
	if !hasFieldSelector(m.Fields, `input[name="email"]`) {
		t.Errorf("%s 不在 fields 里了 —— 它与 %s 一样被滚出了视口（left=%v width=%v），文档坐标却是正的"+
			"（滚得到）。实际 fields: %v", emailLabel, reachLabel, emailLeft, emailWidth, fieldSelectors(m.Fields))
	}

	t.Logf("honeypots=%d %+v；对照组（视口坐标下像陷阱、文档坐标下可达）：%s left=%v width=%v ／ %s left=%v width=%v；scroll=(%v,%v)",
		len(m.Honeypots), m.Honeypots, reachLabel, reachLeft, reachWidth, emailLabel, emailLeft, emailWidth, scrollX, scrollY)
}

// requireViewportTrapLike 量一个对照组元素的**视口**几何，并要求它此刻满足
// 「视口坐标判据」的陷阱条件（left+width <= 0）—— 即：拿视口坐标判的错误实现
// **一定会**把它丢掉。
//
// 不满足就 Fatal：那不叫「测试通过」，叫**对照失效**（元素还好端端待在视口里，
// 换哪种实现都留住它，于是这条对照什么都没证明）。滚动量或 fixture 的几何一变，
// 这里会当场说话，而不是静默退化成一条恒绿的断言 —— 本项目最忌的就是那种
// 「跳过和通过长得一样」的形态。
func requireViewportTrapLike(t *testing.T, e *testEnv, label, expr string) (left, width float64) {
	t.Helper()
	left = e.evalFloat(t, "("+expr+").getBoundingClientRect().left")
	width = e.evalFloat(t, "("+expr+").getBoundingClientRect().width")
	if width <= 0 {
		t.Fatalf("%s 的宽度量出来是 %v —— 几何没量到，下面的对照失去意义", label, width)
	}
	if left+width > 0 {
		t.Fatalf("%s 的视口坐标 left=%v width=%v（left+width=%v > 0）—— 它在视口里还看得见，"+
			"视口坐标的错误实现**也会**留住它，这条对照是空的（滚动量或 fixture 的几何变了？）",
			label, left, width, left+width)
	}
	return left, width
}

// hasFieldSelector 是 hasSelector 的字段版（同名的那个收的是 []internal.Action）。
func hasFieldSelector(fields []internal.Field, sel string) bool {
	for _, f := range fields {
		if f.Selector == sel {
			return true
		}
	}
	return false
}

// fieldSelectors 把字段列表压成一串选择器 —— 只用在失败消息里（「找不到那个字段」
// 得带上实际看到了什么，否则无从查起）。
func fieldSelectors(fields []internal.Field) []string {
	out := make([]string, 0, len(fields))
	for _, f := range fields {
		out = append(out, f.Selector)
	}
	return out
}

// evalJS 跑一次 `cdp eval <js>`（真二进制），非 0 退出即 Fatal。
//
// 为什么用二进制而不是在 Go 客户端里直调 EvalInFrame：这条测试的其余部分也走二进制
// （observe 的 stdout 与退出码是契约），前置步骤用同一条路，出错时的形态才一致 ——
// 「Go 侧直调能过、二进制跑不动」这类差异在这里没有藏身处。
func (e *testEnv) evalJS(t *testing.T, js string) {
	t.Helper()
	if _, errOut, code := e.run(t, "eval", js); code != 0 {
		t.Fatalf("cdp eval %s 退出码 = %d\nstderr: %s", js, code, errOut)
	}
}

// evalFloat 跑一次 eval 并把输出当**数字**解析 —— 量几何用。
//
// ⚠️ 不能省成「拿字符串比一比」：stdout 上回来的是 JSON 编码的值（600 / 600.0 /
// -592.5 都合法），字符串比较会把 -592 与 -592.0 判成不同，而这里要的正是
// **大小关系**（那就是判据本身）。
func (e *testEnv) evalFloat(t *testing.T, js string) float64 {
	t.Helper()
	out, errOut, code := e.run(t, "eval", js)
	if code != 0 {
		t.Fatalf("cdp eval %s 退出码 = %d\nstderr: %s", js, code, errOut)
	}
	trimmed := strings.TrimSpace(out)
	v, err := strconv.ParseFloat(trimmed, 64)
	if err != nil {
		t.Fatalf("eval %s 的输出 %q 不是数字: %v", js, trimmed, err)
	}
	return v
}
