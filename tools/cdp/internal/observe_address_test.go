package internal

import (
	"encoding/json"
	"fmt"
	"sort"
	"strings"
	"testing"
)

// 地址（addressability）：**交出去的每一条选择器都必须真的指到它、而且只指到它**。
//
// 这一套对应的是 2026-09-17 真窗口实测（gowizard 的 MUI 问卷）那一趟的三处缺陷：
//
//	F1 控件进不了模型 —— 三个 `<div role="combobox">` **可见**，可选动作的选择器串里
//	   没有这个角色 → `qsa(SEL)` 一个都取不到（真站 `fields: 0`，actions 里也没有它们）
//	F2 地址不唯一 —— 三个控件同形：按 class 取 **3 命中**，写死 4 跳的兜底路径 **11 命中**
//	F3 没有标签 —— 文本是 `2025` / 空 / 空，而 `label[for]` 指着的那个 id 在**第 6 层祖先**上
//
// 夹具 = testdata/observe_reads.html 的 ④⑤⑥ 三块（逐层复刻真站 DOM 结构，含两个
// 「不做的事」的守卫：纯数字 wrapper id、opacity:0 的 native input）。
//
// ⚠️ 唯一性是**在帧内**判的（选择器按帧施用）。断言这一点的办法是**问浏览器本身**：
// 拿模型给的选择器回去 `__cdpQA` 一遍，看它命中的是不是**模型描述的那一个元素**
// （按观测那一刻的 bbox 纵向位置认）。不在 Go 侧重写一遍判据 —— 那是两份判据，
// 必然漂移（本仓反复踩的坑，见 observe_selector_test.go 顶部）。

// addressFixture 导航到 F1/F2/F3 的夹具页并观测一次。
func addressFixture(t *testing.T) *PageModel {
	t.Helper()
	m := navigateAndObserve(t, serveFixtures(t).URL+"/observe_reads.html")
	// 自证观测有效：这一页的可动作元素是两位数（三个 MUI 控件 + 一堆按钮/选项）。
	// 掉到个位数就是观测退化了，下面按角色/文本取的断言会以 Fatal 说话 ——
	// 但先说清是**观测**的问题，不是页面变了。
	if len(m.Actions) < 10 {
		t.Fatalf("夹具页只观测到 %d 个可动作元素 —— 观测退化了，后面的断言无从落地", len(m.Actions))
	}
	return m
}

// comboboxes 取模型里 role=combobox 的动作，按**文档序**（bbox 的 y）排好。
//
// 为什么要按 y 排：三格长得一样（文本 `2025` / 空 / 空），只有纵向位置分得开它们 ——
// 与真站一致（y=110/190/302 那三个）。按 Text 找是不行的，那正是 F3 要治的病。
func comboboxes(t *testing.T, m *PageModel) []Action {
	t.Helper()
	var out []Action
	for _, a := range m.Actions {
		if a.Role == "combobox" {
			out = append(out, a)
		}
	}
	sort.Slice(out, func(i, j int) bool { return out[i].BBox[1] < out[j].BBox[1] })
	return out
}

// roleHistogram 给「一个 combobox 都没有」那类失败配一份现场（不然只看到一句 want 3）。
func roleHistogram(m *PageModel) map[string]int {
	h := map[string]int{}
	for _, a := range m.Actions {
		h[a.Role]++
	}
	return h
}

// evalInPage 在**当前活动页**上求值一次（带回穿透助手，与 observe 用的是同一套）。
//
// 另开一条连接（与 measureViewport 同一套路）：navigateAndObserve 的那个 client
// 由它自己的 cleanup 管，这里只是想问浏览器一句话。
// 连不上是**缺陷**不是环境缺失（同一个浏览器刚被这条测试观测过）—— 所以 Fatal。
func evalInPage(t *testing.T, js string) string {
	t.Helper()
	host, port := shadowTestEndpoint()
	c, err := NewClient(host, port)
	if err != nil {
		t.Fatalf("复核地址：连不上刚才还在用的那个 Chrome: %v", err)
	}
	defer c.Disconnect()
	var out string
	if err := c.EvalInFrame("", withPierce(js), &out); err != nil {
		t.Fatalf("复核地址：求值失败: %v", err)
	}
	return out
}

// hitsOf 问浏览器：这条选择器在这一帧里命中几个。
func hitsOf(t *testing.T, sel string) int {
	t.Helper()
	js := fmt.Sprintf(`(function(){
	  try { return String(__cdpQA('%s').length); }
	  catch (e) { return 'THROW:' + e.name; }
	})()`, escapeJS(sel))
	got := evalInPage(t, js)
	if strings.HasPrefix(got, "THROW") {
		t.Errorf("选择器 %q 在浏览器里**语法就过不去**（%s）—— 交出去的地址必须跑得通："+
			"非法的选择器会被 __cdpQA 的 try/catch 吞成 0 命中，表现是「元素凭空消失」，"+
			"而页面上明明有那个元素（数字开头的 id 就是这种：必须写成 [id=\"…\"]）", sel, got)
		return -1
	}
	var n int
	if _, err := fmt.Sscanf(got, "%d", &n); err != nil {
		t.Fatalf("命中数解不开: %q", got)
	}
	return n
}

// requireUniqueAddress 在浏览器里独立复核一条地址：**命中恰好 1 个，而且就是它**。
//
// 两条都要（与 observeJS 的 uniq 同一套判据）：
//
//	命中数 == 1        唯一 —— 3 命中的地址点下去是抽签
//	命中的是同一个元素  指向性 —— 只判唯一会放过「唯一命中的是别人」那条
//
// 「是不是同一个元素」按 bbox 的纵向位置认：模型里那个数就是观测那一刻浏览器说的
// 渲染事实，地址若指向别的元素，顶边几乎不可能恰好相等。
func requireUniqueAddress(t *testing.T, a Action, what string) {
	t.Helper()
	js := fmt.Sprintf(`(function(){
	  var els; try { els = __cdpQA('%s'); } catch (e) { return 'THROW:' + e.name; }
	  if (!els.length) return 'hits=0';
	  return 'hits=' + els.length + ' top=' + Math.round(els[0].getBoundingClientRect().top);
	})()`, escapeJS(a.Selector))
	got := evalInPage(t, js)
	want := fmt.Sprintf("hits=1 top=%d", a.BBox[1])
	if got != want {
		t.Errorf("%s：地址 %q 实测 %s，want %s —— c[0] 必须是**验证过唯一**（命中恰好 1 个）"+
			"且指向它自己的选择器；不唯一的地址交给 agent，它就只能在候选里猜"+
			"（真站实测：那一趟猜了 25 步）", what, a.Selector, got, want)
	}
}

// ── F1 + F2 + F3：MUI 那三个「点开再选」的控件 ──────────────────────────────

func TestObserveMuiComboboxesAreAddressable(t *testing.T) {
	m := addressFixture(t)

	// ── F1：三个控件**都在模型里** ──
	// 判据就是 F1 本身：进不了模型的东西，agent 连讨论它的机会都没有。
	// 真站上这三个是 `<div role="combobox" class="MuiSelect-select …">`、opacity 1、319×60
	// —— 看得见、点得到，只是选择器串里没有这个角色，于是模型里一个都没有。
	boxes := comboboxes(t, m)
	if len(boxes) != 3 {
		t.Fatalf("模型里 role=combobox 的动作有 %d 个，want 3 —— F1（控件进不了模型）。"+
			"三个控件在页面上是可见的（319×60、opacity 1），进不来只可能是可动作元素的"+
			"选择器串里没有这个角色。实际 role 分布: %v", len(boxes), roleHistogram(m))
	}

	// 自证：这三个确实是夹具里那三个（按纵向位置 + 可见文本，与真站同一个口径）
	if boxes[0].Text != "2025" {
		t.Errorf("最上面那个 combobox 的 text = %q，want \"2025\" —— 夹具变了的话，"+
			"下面按位置认元素的断言就全不作数了", boxes[0].Text)
	}
	for _, i := range []int{1, 2} {
		if strings.TrimSpace(boxes[i].Text) != "" {
			t.Errorf("第 %d 个 combobox 的 text = %q，want 空 —— 真站上 Make/Model 的显示文本"+
				"就是空的（**这正是 F3 存在的理由**：没有标签就分不出谁是谁）", i, boxes[i].Text)
		}
	}
	if !boxes[2].Disabled {
		t.Errorf("第三个 combobox（Model）没有被报成禁用 —— 夹具里它是 aria-disabled=\"true\"，" +
			"真站上那一格也是禁用的（`Mui-disabled`）。这一条红了说明判据或夹具变了")
	}

	// ── F2：每一条 `c[0]` 都**验证过唯一**，而且指的就是它 ──
	// 夹具里三格同形：只按 class 取是 3 命中，写死 4 跳的兜底路径命中更多。
	for i, a := range boxes {
		requireUniqueAddress(t, a, fmt.Sprintf("combobox #%d（text=%q）", i, a.Text))
	}

	// 那个 3 命中的 class 选择器**不许**出现在任何一条地址里（首选或退路都不许）。
	//
	// 这不是「顺手」：它是真站上实测的那条坏地址，而「退路」这个位置同样是坑 ——
	// 5 个共享 class 的按钮里，文档序第一个是 Back，顺着退路走会把漏斗**倒着**走
	// （docs/probes/2026-09-17-observe-blinkist/）。所以非唯一候选是**丢**，不是降级排后面。
	const sharedClass = "div.MuiSelect-select.MuiSelect-standard"
	for i, a := range boxes {
		if a.Selector == sharedClass {
			t.Errorf("combobox #%d 的首选仍是那条 3 命中的 class 选择器 %q —— "+
				"F2 的全部意义就是不许把它当 c[0] 交出去", i, sharedClass)
		}
		for _, alt := range a.Alternates {
			if alt == sharedClass {
				t.Errorf("combobox #%d 的 alternates 里还有那条 3 命中的 %q —— "+
					"退路踩不准就不是退路，是第二个坑", i, sharedClass)
			}
		}
	}

	// ── F3：人话名字 ──
	// 三个盒子三种取法，各自承重：
	//   年    —— **没有** label 指着它 → null（如实说，不猜）
	//   Make  —— `label[for="173851"]` 指着的那个 id 在**第 6 层祖先**上（hop6），
	//            而且那个 label 摆在**远处的兄弟子树**里 —— 只有「逐层往上找到
	//            id 被 label[for] 指着的那一层」这一条路取得到它。
	//            ⚠️ 任何「找 4 跳就交差」的写法在这里都够不着（真站上更远，在 hop6 之外
	//            还有 hop9 的锚点）。
	//   Model —— 相邻的 LABEL 兄弟（MUI 的 InputLabel 在 `MuiInputBase-root` 那一层）
	if boxes[0].Label != nil {
		t.Errorf("年那一格的 label = %q，want null —— 夹具里没有任何 label 指着它。"+
			"编一个名字出来比空着更坏：消费侧会把编的当成页面事实用", *boxes[0].Label)
	}
	if boxes[1].Label == nil || *boxes[1].Label != "Make" {
		t.Errorf("Make 那一格的 label = %v，want \"Make\" —— 名字在 label[for=\"173851\"] 里，"+
			"而那个 id 在 combobox 的**第 6 层祖先**上（hop6）。取不到它，模型里三个控件"+
			"就长得一模一样（F3）", labelDesc(boxes[1].Label))
	}
	if boxes[2].Label == nil || *boxes[2].Label != "Model" {
		t.Errorf("Model 那一格的 label = %v，want \"Model\" —— 夹具里它走**另一条**取法："+
			"相邻的 LABEL 兄弟（MUI 的 InputLabel 是 `MuiInputBase-root` 那一层的"+
			"previousElementSibling，不是控件自己的兄弟）", labelDesc(boxes[2].Label))
	}
	for i, a := range boxes {
		if a.Label != nil && strings.TrimSpace(*a.Label) == "" {
			t.Errorf("combobox #%d 的 label 是空串 —— 空串与「没有标签」在消费侧分不开，"+
				"取不到名字就交 null（三态，见 Action.Label 的注释）", i)
		}
	}
	for i, a := range boxes {
		t.Logf("combobox #%d：text=%-6q y=%-4d label=%-8s stability=%-6s selector=%s",
			i, a.Text, a.BBox[1], labelDesc(a.Label), a.Stability, a.Selector)
	}
}

// TestObserveKeepsInvisibleNativeInputsOut —— 「点开再选」的控件旁边那三个
// `input.MuiSelect-nativeInput` 是 `opacity: 0` + `aria-hidden="true"`，
// 被 vis() 滤掉是**对的**（它们的值在 combobox 的 text 里有一份同样的）。
//
// 为什么值得单独钉一条：F1 是「往 SEL 里加一个角色」，最省事的走偏方式是**顺手把
// vis() 也放宽**——那样这三个隐形的重复项会一起进模型，agent 会在三个点不到的东西
// 上浪费动作。这条是那道边界的守卫（vis 的 opacity 判据**不动**）。
func TestObserveKeepsInvisibleNativeInputsOut(t *testing.T) {
	m := addressFixture(t)
	for _, f := range m.Fields {
		if strings.Contains(f.Hint, "mui-native") || strings.Contains(f.Selector, "mui-native") {
			t.Errorf("隐形的 native input 进了 fields（%q / %q）—— 它 opacity:0、"+
				"aria-hidden，值在 combobox 的 text 里有一份；收进来只会多三个看不见、"+
				"点不到的重复项", f.Selector, f.Hint)
		}
	}
	for _, a := range m.Actions {
		if strings.Contains(a.Selector, "mui-native") {
			t.Errorf("隐形的 native input 进了 actions（%q）", a.Selector)
		}
	}
}

// TestObserveEscapesIdsThatAreNotCssIdents —— 数字开头的 id **不能**直接写成 `#id`。
//
// 真窗口实测（2026-09-17）：`#173851` 是**非法的 CSS 选择器**（ident 不能以数字开头），
// querySelector 抛 SyntaxError；而 __cdpQA 用 try/catch 吞掉它 —— 于是它的表现不是
// 报错，是**元素凭空消失**：`cdp click --selector '#173851 …'` 报 element not found，
// 而页面上明明有那个元素；写成 `[id="173851"] …` 命中 1 个、点击成功。
//
// 交出去的地址必须**跑得通**。判据两条，缺一不可：形式是 `[id="…"]`，**且真的命中 1 个**。
// （只判形式的话，一条拼错引号的选择器也能通过。）
func TestObserveEscapesIdsThatAreNotCssIdents(t *testing.T) {
	m := addressFixture(t)
	btn := actionByText(t, m, "Numeric id button")

	if strings.HasPrefix(btn.Selector, "#") {
		t.Errorf("id=\"12\" 的首选是 %q —— `#12` 是**语法非法**的选择器（ident 不能以数字开头），"+
			"而它的失败是静默的：__cdpQA 吞掉 SyntaxError → 0 命中 → 表现为「元素凭空消失」",
			btn.Selector)
	}
	if !strings.HasPrefix(btn.Selector, `[id="12"]`) {
		t.Errorf("id=\"12\" 的首选 = %q，want 形如 `[id=\"12\"]` —— 不是合法 CSS ident 的 id "+
			"必须改写成永远合法的 [id=\"…\"] 形式", btn.Selector)
	}
	// 光看形式不够：它得**真的指到那个元素**
	requireUniqueAddress(t, btn, `id="12" 的按钮`)
	// 反向对照：**合法** ident 的 id 仍走 `#id` 那条老路（不然这条测试可以靠
	// 「一律改用 [id=…]」通过 —— 那会让所有选择器都变长、变难读，而它们本来是好的）
	if a := actionByText(t, m, "Continue"); a.Selector != "#read-continue" {
		t.Errorf("合法 ident 的 id 没有走 `#id`（#read-continue 的首选 = %q）—— "+
			"转义只该发生在 id **不是**合法 CSS ident 的时候", a.Selector)
	}
	t.Logf(`id="12" 的按钮：selector=%q stability=%q`, btn.Selector, btn.Stability)
}

// ── F2 的另一半：交不出唯一选择器时要**如实说** ───────────────────────────────

// TestObserveSaysWhenItCannotAddressUniquely —— 负例：两个逐字节同形的按钮，
// 一个在光 DOM、一个在 shadow root 里。CSS 选择器**跨不过 shadow 边界**
// （__cdpQA 是逐个 root 查的），所以 shadow 里那个爬到头也唯一不了。
//
// 这一格要证明的是：**「唯一不了」必须被说出来**（stability 降成 low），
// 而不是交一条看起来正常的地址出去。
//
// 三条断言缺一不可：
//
//	① 自证这一格**真的**不唯一（拿它的选择器回浏览器数一遍 ≥2）—— 不然「降级」可能是
//	   别的原因造成的，断言就在证明一件不存在的事
//	② 它被降成 low（**那条地址没能验证**的落点）
//	③ 正向对照：同形的孪生兄弟在光 DOM 里、有 `#dup-host` 可以停车 → 唯一 + **不**降级。
//	   没有 ③ 的话，「一律给 low」这种坏实现也是绿的。
func TestObserveSaysWhenItCannotAddressUniquely(t *testing.T) {
	m := addressFixture(t)

	shadow := actionByText(t, m, "Shadow twin")
	light := actionByText(t, m, "Light twin")

	if n := hitsOf(t, shadow.Selector); n < 2 {
		t.Fatalf("shadow 里那个按钮的选择器 %q 只命中 %d 个 —— 这一格**本来就唯一**，"+
			"那它就不是负例（夹具里的孪生结构没生效？）。下面的断言会证明一件不存在的事",
			shadow.Selector, n)
	}
	if shadow.Stability != "low" {
		t.Errorf("交不出唯一选择器的元素 stability = %q，want low —— 它现在只有一条"+
			"「命中 %d 个」的地址，却被当成 %q 交出去；「唯一不了」必须说出来"+
			"（selector=%q）", shadow.Stability, hitsOf(t, shadow.Selector), shadow.Stability, shadow.Selector)
	}

	// 正向对照
	if n := hitsOf(t, light.Selector); n != 1 {
		t.Errorf("光 DOM 里的孪生兄弟（有 #dup-host 可以停车）的选择器 %q 命中 %d 个，"+
			"want 1 —— 同形结构里能唯一的那一个必须照常拿到唯一地址", light.Selector, n)
	}
	if light.Stability == "low" {
		t.Errorf("光 DOM 里的孪生兄弟被降成了 low（%q）—— 它是可唯一寻址的："+
			"降级判据不能是「页面上有同形元素」，得是「这条地址验证不过」",
			light.Selector)
	}
	requireUniqueAddress(t, light, "Light twin")
	t.Logf("负例：shadow twin selector=%q stability=%q（实测 %d 命中）；"+
		"正向对照 light twin selector=%q stability=%q",
		shadow.Selector, shadow.Stability, hitsOf(t, shadow.Selector), light.Selector, light.Stability)
}

// ── label 的三态在 JSON 边界上（不依赖浏览器的那半） ────────────────────────

// TestObserveLabelIsNullWhenThereIsNone —— `label` 的 null 与「一个名字」必须分得开，
// 且在 Go 侧也是这么编的。
//
// 为什么单独一条：JS→Go 的绑定是**按字符串键**的（observeJS 末尾那个对象字面量 ↔
// Go 侧 json tag），键写错就是静默零值 —— 而 `*string` 的零值恰好是 nil，于是
// 「页面真没有标签」与「那个键根本没绑上」在模型里长得一模一样。
func TestObserveLabelIsNullWhenThereIsNone(t *testing.T) {
	b, err := json.Marshal(Action{})
	if err != nil {
		t.Fatalf("编 JSON 失败: %v", err)
	}
	if !strings.Contains(string(b), `"label":null`) {
		t.Errorf("没有标签的动作编出来不是 \"label\":null:\n%s", b)
	}
	name := "Make"
	b2, err := json.Marshal(Action{Label: &name})
	if err != nil {
		t.Fatalf("编 JSON 失败: %v", err)
	}
	if !strings.Contains(string(b2), `"label":"Make"`) {
		t.Errorf("带标签的动作编出来没有 \"label\":\"Make\":\n%s", b2)
	}
}

// ── 修复轮 1（复审 I1）：id 撞上 Object.prototype 的名字 ─────────────────────

// TestObserveSurvivesIdsNamedAfterObjectPrototype —— `id="__proto__"` / `id="constructor"`
// 这类名字是**页面上合法的 id**，而它们正好是 Object.prototype 上的键。
//
// 缺陷（复审用 node + Go 双向实测过）：label 的 for→文本表原先是普通对象 `{}`，
// 于是 `LABELS['__proto__']` 取到的是 **Object.prototype**、`LABELS['constructor']`
// 取到的是**函数** —— 都是真值，`labelOf` 就把一个**对象**当名字返回；那个对象串进
// JSON 之后 Go 侧的 `*string` 解不开 → **整条 observe 失败**。
// 页面上的一个 id 就能让整个观测崩掉，而报出来的错跟那行代码看不出关系 ——
// 正是这一轮要消灭的「静默全崩」那一类。
//
// 两格（都在夹具 ⑦）**判别力差得很远**，别把它们当成两个同等证据：
//
//	#__proto__     有 label 指着它，而且那个 label 摆在 `<div class="lbl-wrap">` 里
//	               （**不是**按钮紧挨着的前一个兄弟）→ labelOf 只剩规则①（字典）一条路。
//	               名字必须**真的取到**（不是 null，更不是对象）；把 Object.create(null)
//	               退回 {} 时**这一格必须变红**（写被 `!m[f]` 守卫跳过 → 名字丢了）。
//	#constructor   没有 label → label 如实为 null。**这一格判别力约等于零**：
//	               修复前后都绿，因为机制上就不可能有判别力 ——
//	               `JSON.stringify({label: ({}).constructor})` → `{}`（**函数被 JSON 丢掉**），
//	               Go 侧拿到 nil、如实解成 null，不报错。它的价值只是**对照**。
//
// ⚠️ 「observe 没失败」这件事由 addressFixture 自证：求值出来的模型若带一个对象，
// Go 侧解不开 → navigateAndObserve 当场 Fatal，根本走不到下面。
func TestObserveSurvivesIdsNamedAfterObjectPrototype(t *testing.T) {
	m := addressFixture(t)

	proto := actionByText(t, m, "Proto id button")
	if proto.Label == nil {
		t.Errorf("#__proto__ 的 label = null，want \"Proto named\" —— 页面上有 " +
			"label[for=\"__proto__\"] 指着它，而那个 label **不挨着**按钮，" +
			"所以只剩「for→文本字典」这一条路。拿不到名字说明字典把这一族键当成了" +
			"原型上的东西（读出来是对象，或被 `!m[f]` 守卫把写拦掉了）")
	} else if *proto.Label != "Proto named" {
		t.Errorf("#__proto__ 的 label = %q，want \"Proto named\"", *proto.Label)
	}
	// 顺带钉住 id 那一路：`__proto__` 是合法 CSS ident，应当照常走 `#id`
	if proto.Selector != "#__proto__" {
		t.Errorf("#__proto__ 的首选 = %q，want \"#__proto__\"（它是合法的 CSS ident）", proto.Selector)
	}
	requireUniqueAddress(t, proto, "id=\"__proto__\" 的按钮")

	ctor := actionByText(t, m, "Constructor id button")
	if ctor.Label != nil {
		t.Errorf("#constructor 的 label = %q，want null —— 页面上没有任何 label 指着它。"+
			"这一格是**对照**（判别力约等于零：修不修都绿，因为函数会被 JSON 丢掉、"+
			"Go 侧拿到 nil 不报错），但「没有 label 就如实交 null」这条契约本身要钉住",
			labelDesc(ctor.Label))
	}
	requireUniqueAddress(t, ctor, "id=\"constructor\" 的按钮")

	// H-4：这里**不能**解引用（`*proto.Label`）—— 断言一失败就 SIGSEGV，
	// 一个干净的 FAIL 变成崩溃，该包里排在它后面的测试全部不再运行（复审真实触发过）。
	// labelDesc 就是这个用途（本文件 :501 的助手，三态都印得出来）。
	t.Logf("#__proto__ label=%s selector=%q ；#constructor label=%s selector=%q —— "+
		"两个 Object.prototype 上的名字都活下来了",
		labelDesc(proto.Label), proto.Selector, labelDesc(ctor.Label), ctor.Selector)
}

// ── 修复轮 1（复审 I2）：另外三个列表也要说清「地址验没验证过」 ──────────────

// TestObserveMarksUnverifiedAddressesOnTheOtherThreeLists —— option_groups / obstructions /
// honeypots 这三条路同样把地址交给消费者（遮挡物那条是要**照着去点**的），
// 但它们**没有 stability** 那个评级口径 —— 唯一性只能自己说（`scope_unique` /
// `selector_unique` / `dismiss_selector_unique`，语义见 Go 侧注释）。
//
// 判据一条，但要求**两个分支都有真值**（夹具 ⑧ 就是为「false 那一支」造的）：
//
//	标记必须与浏览器说的事实**一致**：hits == 1  ⟺  标记为 true
//
// ⚠️ 反过来的错法要一并挡住：**不许**因为「不唯一」就把地址丢掉 ——
// 丢掉了消费侧只会更瞎（横幅还挡着，模型里却一个字都没有）。所以这里同时断言
// 「不唯一的那一条**在**模型里」。
func TestObserveMarksUnverifiedAddressesOnTheOtherThreeLists(t *testing.T) {
	m := addressFixture(t)

	// ── honeypots：两个同形的陷阱，一个在光 DOM、一个在 shadow 里 ──
	var twins []Honeypot
	for _, h := range m.Honeypots {
		if h.Hint == "trap_twin" {
			twins = append(twins, h)
		}
	}
	if len(twins) != 2 {
		t.Fatalf("夹具里两个同形陷阱应各记一条 honeypot（hint=trap_twin），实际 %d 条 —— "+
			"下面的断言会空转。全部 honeypots: %+v", len(twins), m.Honeypots)
	}
	unverified := 0
	for i, h := range twins {
		n := hitsOf(t, h.Selector)
		if want := n == 1; h.SelectorUnique != want {
			t.Errorf("陷阱 #%d（%q）的 selector_unique = %t，而浏览器说这条地址命中 %d 个 —— "+
				"标记必须与事实一致（true ⟺ 命中恰好 1 个）", i, h.Selector, h.SelectorUnique, n)
		}
		if !h.SelectorUnique {
			unverified++
		}
	}
	if unverified != 1 {
		t.Errorf("两个陷阱里「没验证过」的有 %d 个，want **恰好 1** —— shadow 里那个交不出唯一地址"+
			"（CSS 选择器跨不过 shadow 边界），光 DOM 那个能。0 = 那个假分支根本没被测到，"+
			"2 = 判据坏了（把能唯一的也判成不唯一）", unverified)
	}
	// 「如实标注」不是「删掉」：不唯一的那条地址必须**还在**模型里
	for i, h := range twins {
		if !h.SelectorUnique && h.Selector == "" {
			t.Errorf("陷阱 #%d 交不出唯一地址，于是**地址被丢了**（selector 空）—— "+
				"这三个列表要的是如实标注、不是删除：丢掉它，消费侧连「这里有个陷阱、在哪儿」都不知道", i)
		}
	}

	// ── obstructions：两条地址各带各的标记 ──
	//
	// 夹具里**两个分支都要有真值**（H-3 之前这里零覆盖）：
	//   「有 id + 有关闭按钮」  → ⑨ cookie 横幅（两个标记都必然是非 null 的布尔）
	//   「无 id + 无关闭按钮」  → ⑩ 那一对（影子里那份唯一不了 → selector_unique=false；
	//                             里面没有 button/a → dismiss_* 两个字段都是 null）
	// 没有 ⑩，`dismiss_selector_unique` 的 null 分支与 obstruction 的
	// `selector_unique: false` 分支**从不执行** —— 将来有人把 null 写成 false，
	// 一条测试都不会红。
	if len(m.Obstructions) == 0 {
		t.Fatalf("夹具里的遮挡物一个都没被识别出来 —— 下面这些断言会空转")
	}
	obstructionsUnverified, obstructionsWithoutDismiss := 0, 0
	for i, o := range m.Obstructions {
		if n := hitsOf(t, o.Selector); o.SelectorUnique != (n == 1) {
			t.Errorf("遮挡物 #%d（%q）的 selector_unique = %t，而浏览器说它命中 %d 个",
				i, o.Selector, o.SelectorUnique, n)
		}
		if !o.SelectorUnique {
			obstructionsUnverified++
		}
		if o.DismissSelector == "" {
			obstructionsWithoutDismiss++
			if o.DismissSelectorUnique != nil {
				t.Errorf("遮挡物 #%d 没有关闭按钮（dismiss_selector 空），"+
					"dismiss_selector_unique 却是 %t —— 没有这条地址就该交 **null**，"+
					"编一个布尔值等于替页面断言「有个关闭按钮，只是它不唯一」",
					i, *o.DismissSelectorUnique)
			}
			continue
		}
		if o.DismissSelectorUnique == nil {
			t.Errorf("遮挡物 #%d 有关闭按钮 %q，dismiss_selector_unique 却是 null —— "+
				"有地址就必须说清它验没验证过", i, o.DismissSelector)
			continue
		}
		if n := hitsOf(t, o.DismissSelector); *o.DismissSelectorUnique != (n == 1) {
			t.Errorf("遮挡物 #%d 的关闭按钮 %q：dismiss_selector_unique = %t，浏览器说命中 %d 个",
				i, o.DismissSelector, *o.DismissSelectorUnique, n)
		}
	}
	// 覆盖自证（与上面 honeypots 那条同一套写法）：两个分支各自**必须有真值**，
	// 而且「唯一不了」的那个数要**恰好 1** —— 光 DOM 那一份（有 id 可停车）必须
	// 照常拿到唯一地址，否则夹具的正对照没了，断言会退化成「一律不唯一」也绿。
	if obstructionsWithoutDismiss == 0 {
		t.Errorf("没有一条遮挡物是「无关闭按钮」的 —— dismiss_selector_unique 的 **null** "+
			"分支又变成零覆盖了（夹具 ⑩ 是不是被删了？）")
	}
	if obstructionsUnverified != 1 {
		t.Errorf("「唯一不了」的遮挡物有 %d 个，want **恰好 1** —— "+
			"1 = 影子里那份（夹具 ⑩，CSS 选择器跨不过 shadow 边界）；"+
			"0 = selector_unique 的 **false** 分支零覆盖；"+
			"2+ = 判据坏了（把能唯一的也判成不唯一）。全部：%+v",
			obstructionsUnverified, m.Obstructions)
	}

	// ── option_groups：夹具里的自定义问答题（#q-widget 那三个选项）──
	if len(m.OptionGroups) == 0 {
		t.Fatalf("夹具里的 role=radiogroup 没被识别成 option_group —— 下面的断言会空转")
	}
	for i, g := range m.OptionGroups {
		if n := hitsOf(t, g.Scope); g.ScopeUnique != (n == 1) {
			t.Errorf("选项组 #%d 的 scope %q：scope_unique = %t，浏览器说命中 %d 个",
				i, g.Scope, g.ScopeUnique, n)
		}
	}

	t.Logf("三个列表：honeypots=%d（其中未验证 %d）、obstructions=%d、option_groups=%d —— "+
		"每个标记都与浏览器数出来的命中数一致",
		len(m.Honeypots), unverified, len(m.Obstructions), len(m.OptionGroups))
}

// labelDesc 把三态印成人话（断言失败时要看得出「是 null 还是空串」）。
func labelDesc(v *string) string {
	if v == nil {
		return "null（页面上没有 label 给它名字）"
	}
	return fmt.Sprintf("%q", *v)
}
