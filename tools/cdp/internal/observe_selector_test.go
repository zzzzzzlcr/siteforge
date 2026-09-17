package internal

import (
	"os"
	"strings"
	"testing"
)

// 选择器候选与稳定性评级的**行为**测试（Task 5）。
//
// 为什么单独一轮：规格 D3 写着「**选择器是 py 准不准的头号因素**」——
// 而 `candidates()` / `stability()`（observe.go:160 / :174，从 R3 探针逐段移植、
// 0 行差异）此前**零断言**：套件里没有任何一条会因它改坏而变红。
// 将来谁动这两个函数，这里必须说话。
//
// 断言打在**真浏览器里 PageModel 的输出**上，不在 Go 侧重实现一遍判据 ——
// 两份实现必然漂移，而「两份判据」正是本项目反复踩的坑（控制器在本任务里
// 明确禁掉了 Go 侧 `stabilityOf`/`looksRandom` 助手）。
//
// 判据（规格 §4.3；2026-09-17 I3 改成按**位置跳数**分档，见 observe.go 的 stability）：
//
//	地址 = 锚点（#id / [id=…] / [name=…] / [data-*=…]）+ 尾巴（:nth-of-type 链）
//	high   = 有锚点且**一跳位置都没有**（整条地址都是身份）
//	medium = 位置跳数 1–3（不管有没有锚点）；或没有锚点、只有 class
//	low    = 位置跳数 ≥4；或没有锚点也没有 class（只剩裸标签的位置）
//
// 「锚点 + 长位置尾巴」原来是 high（只看首字符），现在按尾巴的跳数掉档 ——
// 真站实测那三个 combobox 拿到锚点 + 9 跳的地址、却全是 high，
// 而消费侧（agent/template.py 的 rank）是按 stability 排回退梯子的。
//
// fixture = testdata/selector.html（Task 5 新增，httptest 自带服务，C30：
// 不依赖 localhost:8080 之类的外部 mock server）。每个元素用**唯一文本**标注，
// 下面所有断言都按文本取元素 —— 按 selector 取会自我指涉（selector 正是被测对象）。
//
// ⚠️ 取不到 fixture 元素时**当场 Fatal**，不让断言空转：
// 「找不到 → 循环体不执行 → 绿」是本项目栽过多次的形态。

// actionByText 按可见文本取一个动作；取不到直接 Fatal（否则断言空转通过）。
func actionByText(t *testing.T, m *PageModel, text string) Action {
	t.Helper()
	for _, a := range m.Actions {
		if a.Text == text {
			return a
		}
	}
	t.Fatalf("fixture 里找不到文本 %q 的可动作元素（actions=%d）—— 断言会空转，先查 selector.html 与本断言是否还对得上",
		text, len(m.Actions))
	return Action{}
}

// fieldByPlaceholder 按 placeholder 取一个字段（input 没有 textContent，不能用文本找）。
func fieldByPlaceholder(t *testing.T, m *PageModel, ph string) Field {
	t.Helper()
	for _, f := range m.Fields {
		if f.Placeholder == ph {
			return f
		}
	}
	t.Fatalf("fixture 里找不到 placeholder 为 %q 的字段（fields=%d）", ph, len(m.Fields))
	return Field{}
}

// selectorFixture 导航到 selector.html 并观测，顺带自证「观测本身取到了东西」。
func selectorFixture(t *testing.T) *PageModel {
	t.Helper()
	m := navigateAndObserve(t, serveFixtures(t).URL+"/selector.html")
	// 自证有效：fixture 现有 19 个按钮 + 6 个 input = 25 个可动作元素（实测 25）。
	// 阈值取 10 是**地板**不是等值：少一两个元素说明不了什么，掉到个位数就是观测
	// 退化了，下面按文本取的断言会以 Fatal 说话，但这里先说清是**观测**的问题。
	if len(m.Actions) < 10 {
		t.Fatalf("selector.html 只观测到 %d 个可动作元素（fixture 有 19 按钮 + 6 输入，实测 25）—— 观测退化了", len(m.Actions))
	}
	// 原始输出留档：报告里的「逐条实测结果」直接取这里。
	for _, a := range m.Actions {
		t.Logf("action text=%-24q selector=%-70s stability=%-6s alternates=%v",
			a.Text, a.Selector, a.Stability, a.Alternates)
	}
	for _, f := range m.Fields {
		t.Logf("field  ph=%-16q selector=%-70s stability=%-6s alternates=%v",
			f.Placeholder, f.Selector, f.Stability, f.Alternates)
	}
	return m
}

// ── 判据 1：稳定标识 → high，且**首选就是它** ───────────────────────────────

func TestObserveSelectorStableIdentifiersPreferred(t *testing.T) {
	m := selectorFixture(t)

	// 1a. 稳定 id
	if a := actionByText(t, m, "Stable Id"); a.Selector != "#schedule-now" {
		t.Errorf("有稳定 id 时首选应为 #schedule-now，实际 %q（stability=%s）", a.Selector, a.Stability)
	} else if a.Stability != "high" {
		t.Errorf("#schedule-now 的 stability = %q，应为 high（判据：有稳定的 id）", a.Stability)
	}

	// 1c. 只有 name（无 id）→ 首选必须是 [name=]
	f := fieldByPlaceholder(t, m, "B stable name")
	if f.Selector != `input[name="email-addr"]` {
		t.Errorf("只有 name 时首选应为 input[name=\"email-addr\"]，实际 %q（stability=%s）", f.Selector, f.Stability)
	} else if f.Stability != "high" {
		t.Errorf("input[name=] 的 stability = %q，应为 high（判据：有稳定的 name）", f.Stability)
	}

	// 1d. 只有 data-testid（无 id / name）→ 首选必须是 [data-testid=]
	if a := actionByText(t, m, "C data-testid"); a.Selector != `button[data-testid="go-btn"]` {
		t.Errorf("只有 data-testid 时首选应为 button[data-testid=\"go-btn\"]，实际 %q（stability=%s）", a.Selector, a.Stability)
	} else if a.Stability != "high" {
		t.Errorf("button[data-testid=] 的 stability = %q，应为 high（判据：有稳定的 data-*）", a.Stability)
	}

	// 1e. 只有 id（没有别的标识）→ 首选是它，但 **alternates 恒为空**：
	// pathSel 第一跳撞上元素自己的 id 就停、吐回 `#pure-id`，被去重滤掉。
	// 这里只留档、不断言 —— 「纯 id 元素没有兜底候选」是报告里提出来的一条设计顾虑，
	// 钉死它会让将来想补结构兜底的人撞上一条测试，而那未必是错的方向。
	if a := actionByText(t, m, "L pure id"); a.Selector != "#pure-id" {
		t.Errorf("只有 id 时首选应为 #pure-id，实际 %q", a.Selector)
	} else {
		t.Logf("L pure id：selector=%q stability=%q alternates=%v（空 = 没有兜底候选）",
			a.Selector, a.Stability, a.Alternates)
	}
}

// ── 判据 4：alternates 有序、不冗余 ───────────────────────────────────────

// 五种标识俱全的元素上，候选必须按「稳定度」排出来
// （id > name > data-* > placeholder > class > 结构路径），且去重 ——
// pathSel 对同一个元素会再吐一次 `#full`，那一份必须被滤掉。
//
// ⚠️ placeholder 是 **2026-09-17 新加进来的一档**（原来没有），位置在 data-* 之后、
// class 与位置路径之前 —— 理由（真站实测 + 规格 §5.1b「位置路径是最后手段」）
// 写在 observe.go 的 candidates() 那一段。它在这里被钉住顺序。
func TestObserveSelectorCandidatesOrderedAndDeduped(t *testing.T) {
	m := selectorFixture(t)

	f := fieldByPlaceholder(t, m, "Full")
	want := []string{
		"#full",
		`input[name="full-name"]`,
		`input[data-testid="full-tid"]`,
		`input[placeholder="Full"]`,
		"input.form-control",
	}
	if f.Selector != want[0] {
		t.Fatalf("首选应为 %q，实际 %q", want[0], f.Selector)
	}
	// 只断言**顺序**（稳定标识那几条按 id > name > data-* > class 依次出现），
	// 不钉死总条数：结构路径作为兜底候选出现在后面是合理的，将来若补上更多兜底
	// 也不该让这条测试拦路。判据要的是「有序、不冗余」，不是「恰好几条」。
	if len(f.Alternates) < len(want)-1 {
		t.Fatalf("alternates 只有 %d 条，至少应有 %d 条（id 之外的三种稳定标识 + class 候选）: %v",
			len(f.Alternates), len(want)-1, f.Alternates)
	}
	for i, w := range want[1:] {
		if f.Alternates[i] != w {
			t.Errorf("alternates[%d] = %q，应为 %q（顺序：id > name > data-* > class > 结构路径）", i, f.Alternates[i], w)
		}
	}
	// 结构路径**若在**候选里，必须排在所有稳定标识之后（它是最后手段）。
	//
	// 注意这里写的是「若在则末位」，不是「必须在末位」：`#full` 这种**元素自身有稳定
	// id** 的元素，路径根本不会出现在候选里 —— pathSel 第一跳就撞上它自己的 id、
	// 吐回同一个 `#full`，被去重滤掉。实测 alternates = [name, data-testid, class]
	// 三条，没有路径。这是报告里「alternates 的结构性空洞」一节的事实依据，
	// 这里只断言顺序，不把「路径必须在」当成判据（判据只说有序、不冗余）。
	for i, alt := range f.Alternates {
		if strings.Contains(alt, ":nth-of-type(") && i != len(f.Alternates)-1 {
			t.Errorf("结构路径出现在 alternates[%d]/%d（%q）—— 它必须排在所有稳定标识之后: %v",
				i, len(f.Alternates), alt, f.Alternates)
		}
	}
	t.Logf("#full 的候选链：selector=%q alternates=%v（元素自身有稳定 id ⇒ 结构路径不会出现）",
		f.Selector, f.Alternates)

	// 去重：整条候选链（首选 + alternates）内部不得有重复串。
	// 踩坑形态就是 pathSel 把 `#full` 又吐一遍 —— 那样 alternates 里会多一条与首选相同的串。
	all := append([]string{f.Selector}, f.Alternates...)
	seen := map[string]bool{}
	for i, s := range all {
		if s == "" {
			t.Errorf("候选链里第 %d 条是空串", i)
			continue
		}
		if seen[s] {
			t.Errorf("候选链里 %q 重复出现（第 %d 条）—— 去重没生效", s, i)
		}
		seen[s] = true
	}
}

// ── 判据 3：深层结构路径 → low ─────────────────────────────────────────────

func TestObserveSelectorDeepPathRatedLow(t *testing.T) {
	m := selectorFixture(t)

	// G：**爬到 frame 根**的结构路径 —— 6 段（5 个 >），每一层的 nth-of-type 下标都是 1。
	//
	// ⚠️ 这里原来是「4 段」，而那 4 段是 pathSel **写死 4 跳**的特征化（「爬 4 跳就交差」）。
	// 2026-09-17 那轮把它换成了**停止条件**（爬到稳定 id 的祖先、或 frame 根），
	// 于是 G（一路没有 id 可停）自然爬到 body 为止 —— 跳数不再有上限，
	// 「多长算完」由一个能说清的条件决定。判据本身没变：越深的纯结构路径越脆。
	g := actionByText(t, m, "G deep path no id")
	const wantG = "body:nth-of-type(1) > div:nth-of-type(1) > section:nth-of-type(1) > " +
		"article:nth-of-type(1) > div:nth-of-type(1) > button:nth-of-type(1)"
	if g.Selector != wantG {
		t.Fatalf("G 的 selector = %q，应为 %q（无稳定 id 的祖先可停车 → 一路爬到 frame 根；"+
			"路径不再有跳数上限，但每一层都必须是确定的 nth-of-type）", g.Selector, wantG)
	}
	if n := strings.Count(g.Selector, ">"); n != 5 {
		t.Errorf("G 的路径有 %d 个 '>'，应为 5（= 6 段）—— 停止条件变了，重新判读: %q", n, g.Selector)
	}
	// 判据：深结构路径 >3 层 → low。代码里的口径是 depth(> 的个数) <= 2 才 medium。
	if g.Stability != "low" {
		t.Errorf("6 段结构路径的 stability = %q，应为 low（判据：深结构路径 >3 层）", g.Stability)
	}

	// H：4 段（3 个 >），只差在**表头是祖先的稳定 id**。
	//
	// ⚠️ 这一格原来是**特征化断言**（钉住「以 # 开头就 high」的现状，并在报告里
	// 提出异议）。I3（2026-09-17）把那个判据改掉了，于是这里**期望值跟着改**：
	// H 从 high 降成 medium，理由是——
	//
	//   判据改成「地址 = 锚点 + 尾巴，评级看**尾巴里有几跳位置**」之后，H 的锚点
	//   是祖先的 `#deep-wrap`、尾巴是 **3 跳** nth-of-type：tail 1–3 跳 = medium。
	//   这与规格 §4.3「结构路径 ≤3 层 → medium」是同一把尺子（纯路径的跳数 = 段数）。
	//   为什么不是 low：3 跳的位置尾巴与 G（6 跳、无锚点）**不是一个量级**，
	//   把它与 9 跳的真站路径一起打成 low 就等于没有分档（brief 明确禁止）。
	//   为什么不是 high：high 的语义是「整条地址都是身份，没有一处『第几个』」——
	//   H 的每一跳都是「第几个」，插一个同标签的 div 就断，它不配。
	h := actionByText(t, m, "H deep under stable id")
	if !strings.HasPrefix(h.Selector, "#deep-wrap > ") {
		t.Fatalf("H 的 selector = %q，应形如 `#deep-wrap > …`（爬升途中撞上祖先的稳定 id 就停车）", h.Selector)
	}
	if n := strings.Count(h.Selector, ">"); n != 3 {
		t.Errorf("H 的 selector 有 %d 个 '>'（应为 3，与 G 同形状）—— 断言的前提变了，重新判读", n)
	}
	// H 元素**自身**没有任何稳定标识 —— 这是「那一个 `#` 只是祖先的」的关键事实：
	if n := strings.Count(h.Selector, "#"); n != 1 {
		t.Errorf("H 的选择器里 `#` 出现 %d 次（应恰好 1 次 = 祖先 #deep-wrap）—— 断言前提变了: %q", n, h.Selector)
	}
	for _, own := range []string{"[name=", "[data-"} {
		if strings.Contains(h.Selector, own) {
			t.Errorf("H 的选择器里出现了 %q —— H 自身不该有稳定标识，断言前提变了: %q", own, h.Selector)
		}
	}
	t.Logf("H：selector=%q stability=%q（G 同形状无 id 表头 = %q）；"+
		"P = 锚点 + 4 跳（下面那条边界）", h.Selector, h.Stability, g.Stability)
	if h.Stability != "medium" {
		t.Errorf("H（锚点 + **3 跳**位置）的 stability = %q，应为 medium —— "+
			"判据：锚点之后的位置跳数 1–3 跳 = medium（与「没有锚点的纯结构路径 ≤3 段」"+
			"同一把尺子）；0 跳才是 high、≥4 跳是 low", h.Stability)
	}

	// P：锚点 + **4 跳**位置 —— 判据线的**另一侧**（与 H 的 3 跳配对）。
	//
	// 只钉住 H（降级了）不够：把阈值从「≥4 跳」挪到「≥3 跳」同样能让 H 变红，
	// 而那样会把大量「锚点 + 3 跳」的地址一起打进 low（= rank 退化，brief 禁的东西）。
	// 两侧都钉住，阈值才真的是那个数。
	p := actionByText(t, m, "P anchor plus four hops")
	if !strings.HasPrefix(p.Selector, "#far-wrap > ") {
		t.Fatalf("P 的 selector = %q，应形如 `#far-wrap > …`", p.Selector)
	}
	if n := strings.Count(p.Selector, ":nth-of-type"); n != 4 {
		t.Errorf("P 的 selector 里有 %d 跳 nth-of-type（应为 4）—— 断言前提变了: %q", n, p.Selector)
	}
	if p.Stability != "low" {
		t.Errorf("P（锚点 + **4 跳**位置）的 stability = %q，应为 low —— "+
			"真站实测就是这一档：三个 combobox 拿到 `#inputAreaParentContainer > … 9 跳 …`， "+
			"改判前全是 high，会**排在真正稳定的 id 前面先试**（消费侧按 stability 排序）", p.Stability)
	}

	// 正向对照：真稳定 id（0 跳位置）必须仍然是 high —— 降级的判据不许误伤它们。
	for _, want := range []struct{ text, sel string }{
		{"Stable Id", "#schedule-now"},
		{"L pure id", "#pure-id"},
	} {
		if a := actionByText(t, m, want.text); a.Selector != want.sel || a.Stability != "high" {
			t.Errorf("%s：selector=%q stability=%q，want selector=%q stability=high —— "+
				"「一个 :nth-of-type 都没有」的地址就是 high，判据改的是**带位置尾巴的锚点**，不是 id 本身",
				want.text, a.Selector, a.Stability, want.sel)
		}
	}
}

// ── 判据 5：真站上最常见的情况 —— 裸 <button>，没有任何标识 ────────────────

func TestObserveSelectorPlainButtonRating(t *testing.T) {
	m := selectorFixture(t)

	a := actionByText(t, m, "I plain button")
	// 没有 id / name / data-* / class → 只剩结构路径，而且是**位置**路径：
	// body 下第 k 个 button。形状确定，这里钉住它。
	if !strings.HasPrefix(a.Selector, "body:nth-of-type(1) > button:nth-of-type(") {
		t.Errorf("裸 button 的 selector = %q，应为 `body:nth-of-type(1) > button:nth-of-type(k)`", a.Selector)
	}
	if len(a.Alternates) != 0 {
		t.Errorf("裸 button 没有别的候选可退，alternates 应为空，实际 %v", a.Alternates)
	}
	// 唯一**判据层面**可断言的一条：它不得评 high。
	// 判据 high 要求「有稳定的 id/name/data-*」或「文本全局唯一」——
	// 裸 button 两者都没有：它只有位置。这条红了才是真缺陷。
	if a.Stability == "high" {
		t.Errorf("裸 button 评了 high（selector=%q）—— 它只有位置、没有任何稳定标识，high 会让 agent 过度信任", a.Selector)
	}
	// 实际取值留档（判据允许 medium：结构路径 ≤3 层）。报告里对这一点有专门一节：
	// `body:nth-of-type(1) > button:nth-of-type(3)` 这种纯位置路径拿 medium，
	// 与 `button.btn.btn-secondary`（同一档）混在一起，agent 区分不出谁更可靠。
	t.Logf("裸 button：selector=%q stability=%q（判据下 medium 是允许的，但它无任何稳定锚点）",
		a.Selector, a.Stability)
}

// ── RAND：随机 token 不得成为首选，也不得评 high ───────────────────────────

// randomTokenCase 是一条「随机 token 不得进候选」的用例。
type randomTokenCase struct {
	name     string
	elemText string // fixture 里标注该元素的那段文本（用来自证 token 真的挂在它身上）
	token    string // 必须**不出现**在任何候选串里的随机片段
	act      Action
	whyRand  string
}

// requireTokenCarriedBy 自证断言不是空转：fixture 里那个元素**真的带**这个 token。
//
// 没有这一层的话，token 里打错一个字符，断言就退化成「候选里没有这个不存在的串」——
// 恒真、永远绿。这正是本项目要求每条「不该出现 X」都自证有效的原因
// （observe_integration_test.go 的 requireShadowActions 是同一套路）。
func requireTokenCarriedBy(t *testing.T, elemText, token string) {
	t.Helper()
	raw, err := os.ReadFile("testdata/selector.html")
	if err != nil {
		t.Fatalf("读 fixture 失败: %v", err)
	}
	for _, line := range strings.Split(string(raw), "\n") {
		if !strings.Contains(line, elemText) {
			continue
		}
		if !strings.Contains(line, token) {
			t.Fatalf("fixture 里 %q 那一行不含 token %q —— 断言会恒真，空转（token 打错了？）\n行: %s",
				elemText, token, strings.TrimSpace(line))
		}
		return
	}
	t.Fatalf("fixture 里找不到含 %q 的那一行 —— 用例与 fixture 脱节了", elemText)
}

// assertNoRandomToken 对一条用例施加判据：首选与 alternates 都不得带该 token。
//
// 判据（规格 §4.3）：high = 有稳定的 id/name/data-*，**且不含随机 hash**；
// low = 依赖随机 class hash。⇒ 选择器里带着随机 token 时，既不该当首选，也不该评 high。
//
// 断言里的 token 是**写死的字面量**（fixture 里那几个元素的值），不是把 RAND
// 用 Go 重写一遍 —— 重写就是两份判据，必然漂移（控制器在本任务里禁掉了这件事）。
// 每个字面量凭什么算「随机」，逐条写在用例里。
func assertNoRandomToken(t *testing.T, c randomTokenCase) {
	t.Helper()
	if c.token == "" {
		t.Fatalf("%s：用例没给 token —— 断言恒真，空转", c.name)
	}
	requireTokenCarriedBy(t, c.elemText, c.token)
	if strings.Contains(c.act.Selector, c.token) {
		t.Errorf("%s：首选选择器里带着随机 token %q —— selector=%q stability=%q。%s。"+
			"判据要求首选不得是随机 token，且 high 必须「不含随机 hash」",
			c.name, c.token, c.act.Selector, c.act.Stability, c.whyRand)
	}
	for i, alt := range c.act.Alternates {
		if strings.Contains(alt, c.token) {
			t.Errorf("%s：alternates[%d] 里带着随机 token %q（%q）—— 换了选择器还是会断", c.name, i, c.token, alt)
		}
	}
	if c.act.Stability == "high" && strings.Contains(c.act.Selector, c.token) {
		t.Errorf("%s：随机 token 上的 stability = high —— 判据明写 high 须「不含随机 hash」", c.name)
	}
}

// RAND 的三种形态（定义与理由见 observe.go 的 RAND 上方注释）：
//
//	形态① 被 -/_ 或串首尾夹住的 **≥8 位 hex** 片段
//	形态② 同上边界内的「可选字母前缀 + **≥6 位数字**」
//	形态③ 同上边界内、**≥5 位**、字母与数字**来回交替 ≥2 次**
//
// 这一条跑**四个落点里 RAND 咬得住的那几档** —— 在这里钉住，将来谁把 RAND 或
// candidates() 的过滤改坏，这里先红。
func TestObserveSelectorRandomTokensNotPreferred(t *testing.T) {
	m := selectorFixture(t)

	cases := []randomTokenCase{
		{
			name: "8 位 hex class（形态①）", elemText: "E hash8", token: "css-1a2b3c4d",
			act:     actionByText(t, m, "E hash8"),
			whyRand: "`-` 后 8 位纯 hex，正好落在形态①的门槛上（≥8 位）",
		},
		{
			// 这一例是**形态①的专用守卫**：`abcdef` 全是字母、`1234` 全是数字，
			// 字母→数字只翻**一次**，所以 ③（要交替 ≥2 次）与 ②（要 ≥6 位数字）都够不着，
			// 只有 ① 认得出。没有它就删得掉①而套件全绿 —— ①会变成没人守的孤儿
			// （修复轮 1 加③之后，①原先独有的用例都被③接管了）。
			name: "只翻转一次的 10 位 hex class（**只有**形态①认得出）", elemText: "N hex10 form1-only", token: "css-abcdef1234",
			act:     actionByText(t, m, "N hex10 form1-only"),
			whyRand: "abcdef 全字母 + 1234 全数字：字母→数字只翻一次，③ 的「交替 ≥2 次」与 ② 的「≥6 位数字」都不成立",
		},
		{
			name: "12 位 hex id（形态①）", elemText: "J random id", token: "a1b2c3d4e5f6",
			act:     actionByText(t, m, "J random id"),
			whyRand: "整个 id 就是 12 位 hex，被串首尾夹住",
		},
		{
			name: "10 位数字 data-testid（形态②）", elemText: "K random tid", token: "x1234567890",
			act:     actionByText(t, m, "K random tid"),
			whyRand: "字母 + 10 位数字，整串匹配形态②",
		},
	}
	for _, c := range cases {
		assertNoRandomToken(t, c)
	}
}

// 这一条在**修复轮 1 之前是红的** —— 那是本任务报出来的真发现，控制器裁定
// 「修实现，不改测试」，于是它现在是**修好之后的那道闸**：将来谁把 RAND 或
// candidates() 的过滤退回原样，它必须立刻再红（实测见报告「修复轮 1」）。
//
// 原先的两个漏网点（都在 candidates() 里）：
//
//  1. **7 位 base36 class 漏网**：`class="css-1x2y3z4"` 是 emotion / MUI v5 那类
//     CSS-in-JS 的输出形态（murmur2 → toString(36)，通常 6~7 位。⚠️ 这句「6~7 位」
//     是本仓的既有知识，**没在真站上核实过** —— 那是规格 R5 的活；能核实的是另一半：
//     brief 把 `css-1x2y3z4` 当成随机 hash 的字面例子，而 RAND 不认它）。
//     原形态①要求 hex 片段 **≥8 位**，7 位 base36 永远够不着；原形态②要求整串无 `-`，
//     `css-` 前缀又挡一道。后果：首选变成 `button.css-1x2y3z4`、stability=**medium**
//     （走 `/^[a-z]+\.[a-z]/` 那条分量分支），而判据说「依赖随机 class hash」该给 low。
//     D3 写着「选择器是 py 准不准的头号因素」—— agent 会把这个 hash 抄进 py 脚本，
//     站点下次发版就断。**修法**：RAND 加形态③（字母与数字来回交替 ≥2 次的片段）。
//
//  2. **name 那一行没有过 RAND**：`if (el.name) out.push(...)` 是四个落点里唯一
//     没做 `!RAND.test(v)` 的。`name="sid_9f8e7d6c5b4a"` 这种 `_` 夹住的 12 位 hex，
//     RAND 自己是**认得**的（形态①），只是没人问它 —— 首选成了
//     `input[name="sid_9f8e7d6c5b4a"]` 且 stability=high。**修法**：那一行补 `!RAND.test`。
//
// ⚠️ 放宽 RAND 的另一半风险在**反向**：不能开始拒绝正常类名。那半边由
// `TestObserveSelectorLegitTokensNotRejected` 守着 —— 两条是一对，别只留一条。
func TestObserveSelectorRandomHashClassNotPreferred(t *testing.T) {
	m := selectorFixture(t)

	cases := []randomTokenCase{
		{
			name: "7 位 base36 class（emotion / MUI 的实际形态）", elemText: "D hash7", token: "css-1x2y3z4",
			act: actionByText(t, m, "D hash7"),
			whyRand: "emotion 的 `css-` + murmur2→toString(36)，通常 6~7 位；" +
				"形态①要 hex ≥8 位、形态②要整串无 `-`，两道都够不着 —— 靠新增的形态③认出来",
		},
	}
	for _, c := range cases {
		assertNoRandomToken(t, c)
	}

	// name 这一路：元素自身没有 id / data-* / class，首选只可能来自 name 或 placeholder。
	// 过滤掉随机 name 之后必须**改用别的候选**（不是变成别的随机串，
	// 也不是把这条元素整个丢掉）—— 所以这里断言正面的结果，不只是「没有 token」。
	//
	// ⚠️ 2026-09-17：期望从「结构路径」改成「placeholder 候选」—— candidates() 新加了
	// placeholder 落点（排在 name 之后、class 与位置路径之前）。原始意图（随机 token
	// 不得进首选）一字未动；退化目标从 10 跳的位置路径换成了页面上的字面量，是变好不是放宽。
	// 位置路径仍在 alternates（下面钉住它）。
	const nameToken = "sid_9f8e7d6c5b4a"
	requireTokenCarriedBy(t, "K random name", nameToken)
	nf := fieldByPlaceholder(t, m, "K random name")
	if strings.Contains(nf.Selector, nameToken) {
		t.Errorf("name 里带随机 token 时首选仍是它（%q，stability=%q）—— candidates() 对 id/data-*/class 都过了 RAND，"+
			"`if (el.name)` 这一行也要过。该 token = `_` 夹住的 12 位 hex（RAND 形态①，RAND 自己是认得的）",
			nf.Selector, nf.Stability)
	}
	if nf.Selector != `input[placeholder="K random name"]` {
		t.Errorf("随机 name 被滤掉后应改用 placeholder 候选，实际 %q —— 要么没滤干净，要么把元素丢了", nf.Selector)
	}
	hasPath := false
	for _, a := range nf.Alternates {
		if strings.HasPrefix(a, "body:nth-of-type(1) > input:nth-of-type(") {
			hasPath = true
		}
	}
	if !hasPath {
		t.Errorf("alternates 里没有结构路径退路（alternates=%q）—— 「首选字面量、退路结构路径」两档都要在",
			nf.Alternates)
	}
	t.Logf("K random name：selector=%q stability=%q（随机 name 被 RAND 滤掉 → 改用 placeholder 候选）",
		nf.Selector, nf.Stability)
}

// ── 反向边界：**不该抓的别抓** ───────────────────────────────────────────
//
// 控制器（修复轮 1）点名要求的一半，而且**比正向那半更重要**：放宽 RAND 最容易的
// 翻车方式是**静默开始拒绝正常类名**（`btn-primary` / `col-md-6` / `hero-banner` 这类）——
// 那会让 stability 全面变差、直接影响 agent 的选择，而且不会有任何报错。
//
// 覆盖正常类名里数字的三种落法（RAND 形态③的「交替 ≥2 次」正是按这三种设计的）：
//
//	无数字        btn-primary / hero-banner
//	数字自成一段   col-md-6 / icon-24
//	数字在词尾     step1 / section1 / text-2xl
//
// 断言「class 候选还在」= selector 恰好是 `button.<class>`：被误判的话 class 会被
// 丢掉，selector 会变成结构路径（`body:nth-of-type(1) > button:nth-of-type(k)`），
// 这条断言就会红 —— 两种形态差得很远，不会看不出来。
func TestObserveSelectorLegitTokensNotRejected(t *testing.T) {
	m := selectorFixture(t)

	classes := []string{"btn-primary", "hero-banner", "col-md-6", "icon-24", "step1", "section1", "text-2xl"}
	for _, cls := range classes {
		a := actionByText(t, m, "M "+cls)
		if a.Selector != "button."+cls {
			t.Errorf("正常类名 %q 被判成了随机 token（selector=%q，stability=%q）—— "+
				"class 候选被丢掉、退化成结构路径。放宽 RAND 时这是最容易的翻车方式",
				cls, a.Selector, a.Stability)
			continue
		}
		if a.Stability == "low" {
			t.Errorf("正常类名 %q 的 stability = low —— 它不该与「依赖随机 hash」同档", cls)
		}
	}

	// name 落点同样要有反向的一半：修复轮 1 给 name 补了 RAND，
	// 词尾带数字的 name（`step2`）不得被连带判成随机。
	f := fieldByPlaceholder(t, m, "M name step2")
	if f.Selector != `input[name="step2"]` {
		t.Errorf("正常 name %q 被判成了随机 token（selector=%q）—— name 落点补 RAND 时误伤了词尾数字",
			"step2", f.Selector)
	}
	t.Logf("反向边界：7 个正常类名 + 1 个正常 name 全部仍被采用（selector=button.<class> / input[name=] ）")
}

// ── name 落点的**双向**边界（协调者审查 ①）────────────────────────────
//
// 为什么单独一条：`name` 是**修复轮 1 改动的那处落点**，而首轮只钉了单向
// （「随机 name 必须丢」）。③ 在 name 上新危及的是 **L→D→L 家族** ——
// `step2a` / `address1a` / `opt2b` 这类「步骤+序号+子项」的人写惯例，
// 那一家被误抓时套件里**原本没有任何东西会说话**。两个方向都钉住（7e-1 / 7e-2）：
//
//	7e-1 `name="a1b2c3"`（随机，③ 形态）→ **必须丢**，改用别的候选
//	7e-2 `name="step2a"`（人写的子字段名）→ **接受被丢**，改用别的候选
//
// 7e-2 不是「红断言」，也不是「假装没事」：控制器裁定**接受 name 也适用 ③**
// （一条规则、一个偏置，不给 name 开特例 —— 按落点分叉会让同一个 token 有不同命运，
// 那正是本仓反复踩的「两份判据」）。取舍写在 observe.go 的 name 那一行旁边。
//
// ⚠️ **2026-09-17 改过期望：从「必须退化成结构路径」改成「必须退化成 placeholder 候选，
// 且结构路径留在 alternates 里」** —— 因为 candidates() 新加了 placeholder 落点
// （排位在 name 之后、class 与位置路径之前，理由见 observe.go 那一段）。
// 这一改是**变好**，不是放宽：
//   - 两个方向的原始意图（**随机 token 不得进首选**）一字未动，断言照旧；
//   - 退化目标从「10 跳的 nth-of-type 位置路径」换成了「页面上的字面量」——
//     真站实测正是这一条把三个共用 class 的字段分开的（`e.g. 06801` / `e.g. California or Texas`）；
//   - 位置路径**没有消失**，它是 alternates —— 所以「一个候选挂了还有退路」这条也还在。
// 谁要是把 placeholder 落点删掉，这条会红，并告诉那个人期望的形状是什么。
func TestObserveSelectorNameLandingBothWays(t *testing.T) {
	m := selectorFixture(t)

	// 方向一：随机 name 必须丢（不是变成别的随机串、也不是把元素丢了）
	const randName = "a1b2c3"
	requireTokenCarriedBy(t, "N random name 3", randName)
	rf := fieldByPlaceholder(t, m, "N random name 3")
	if strings.Contains(rf.Selector, randName) {
		t.Errorf("随机 name %q 仍在首选里（selector=%q，stability=%q）—— name 落点的 RAND 过滤没生效",
			randName, rf.Selector, rf.Stability)
	}
	if rf.Selector != `input[placeholder="N random name 3"]` {
		t.Errorf("随机 name %q 被滤掉后应改用 placeholder 候选（字面量，排在 class 与位置路径之前），实际 %q",
			randName, rf.Selector)
	}

	// 方向二：人写的子字段名 —— ③ 会抓走它。断言「确实被抓走而且退化得干净」，
	// 让代价始终可观测；而不是让它悄悄发生、也没人知道规则边界在哪。
	const subName = "step2a"
	requireTokenCarriedBy(t, "N subfield name", subName)
	sf := fieldByPlaceholder(t, m, "N subfield name")
	if strings.Contains(sf.Selector, subName) {
		t.Errorf("子字段名 %q 没被 ③ 抓走（selector=%q）—— 若这是**有意**收窄了 ③，"+
			"请同步改掉 observe.go 里 name 那一行的取舍注释（「接受 ③ 也适用于 name」），"+
			"并连带更新本测试的期望：别让注释和测试各说一套", subName, sf.Selector)
	}
	if sf.Selector != `input[placeholder="N subfield name"]` {
		t.Errorf("子字段名 %q 被 ③ 抓走后应改用 placeholder 候选，实际 %q", subName, sf.Selector)
	}
	// 位置路径没丢 —— 它是退路。这一条是「退化得干净」的新读法。
	for _, f := range []struct{ name, sel string; alts []string }{
		{randName, rf.Selector, rf.Alternates},
		{subName, sf.Selector, sf.Alternates},
	} {
		hasPath := false
		for _, a := range f.alts {
			if strings.HasPrefix(a, "body:nth-of-type(1) > input:nth-of-type(") {
				hasPath = true
			}
		}
		if !hasPath {
			t.Errorf("%s：alternates 里没有结构路径退路（alternates=%q）—— "+
				"「首选是字面量、退路是结构路径」两档都要在，别把退路一起丢了", f.name, f.alts)
		}
	}
	t.Logf("name 双向：随机 %q → %q ；子字段 %q → %q（首选换成了字面量，结构路径在 alternates）",
		randName, rf.Selector, subName, sf.Selector)
}

// ── Field 的感知字段也要有断言（终审 C77）──────────────────────────────
//
// 为什么要落在这一套里：这一套就是拿 selector.html 的**已知 input** 当真值表，
// 而 `Field` 的字段与选择器一样是**按字符串键**绑上来的（observe.go 的
// fields.map 返回对象 ↔ Field 的 json tag）—— 键写错就是静默零值，
// 而在此之前 type/required/hint 三个键在整套里的断言数是 **0**。
//
// ⚠️ 只钉得住**一半**：selector.html 里没有任何 required 的 input，所以这里
// 只能断言「不该 required 的没被多报」；**正例**（required=true）在
// observe_integration_test.go 的 TestObserveShadowPageTextIsNotEmpty 里
// ——那里的 #fn 模板上真的带 required。零值也是 false，只断言 false 是空转。
func TestObserveSelectorFieldAttributesBind(t *testing.T) {
	m := selectorFixture(t)

	// `type=text`（fixture 里写死的那个属性）—— 零值是空串，两者可区分。
	f := fieldByPlaceholder(t, m, "Full")
	if f.Type != "text" {
		t.Errorf("placeholder=Full 的 input 的 type = %q，应为 text（空串 = 那个键没绑上）", f.Type)
	}
	// 这一半是「别多报」：fixture 这个 input 没有 required 属性。
	if f.Required {
		t.Error("placeholder=Full 的 input 没有 required 属性，却被报成 required（那是反方向的多报）")
	}
	// hint 取 name||id：这个 input 两者都有，空串只可能是键没绑上。
	if f.Hint == "" {
		t.Error("placeholder=Full 的 input 的 hint 为空 —— 它有 name 也有 id，空串=那个键没绑上")
	}
	t.Logf("Field 绑定性：type=%q required=%t hint=%q", f.Type, f.Required, f.Hint)
}

// ── 站方自己的**纯数字**组件号：必须能当首选地址（2026-09-17 真站实测）──────
//
// 真站形状（gowizard，在活页面上逐字量的）：
//
//	id="173851"          三个「点开再选」的下拉
//	id="textField-173862" 三个文本框（州 / 邮编）
//	**name 属性是空的** —— `form_components[173838]` 只活在站方配置里，不在 DOM 上
//
// 这些号是站点自己发的**整数**组件号，跨趟稳定，还是后端字段名（`urlMapping`）。
// 而 RAND 形态②（可选字母前缀 + ≥6 位数字）把「整串就是数字」也当成了随机 hash →
// 首选退化成 10 跳的 nth-of-type 位置路径 —— 而「位置路径会烂」正是这十二轮反复
// 付代价的那一件事。
//
// 反向那一半同样钉住：**12 位 hex 的真随机 id 照旧必须被拒**（形态①），
// 这条豁免只放行「整串十进制」，不许顺手把随机 id 也放进来。
func TestObserveSelectorPlainNumericIdIsStable(t *testing.T) {
	m := selectorFixture(t)

	// 正向：纯数字 id → 首选就是它，且评级 high（有稳定 id）
	for _, c := range []struct{ ph, want string }{
		// ⚠️ 期望是 `[id="173851"]` 而不是 `#173851`：`#` 后面跟数字是**语法非法**的
		// CSS（idSel 的注释写着这条），交出去会是一条静默失败的地址。
		// `[id="..."]` 是它的合法写法，同样是「指着那个稳定号」，不是退让。
		{"e.g. 2020", `[id="173851"]`},
		// textField-173838 = 真站上的邮编框（页面上那三个文本框之一）
		{"e.g. 06801", "#textField-173838"},
	} {
		f := fieldByPlaceholder(t, m, c.ph)
		if f.Selector != c.want {
			t.Errorf("站方组件号 %s 应当首选（纯十进制是人编的序号，不是随机 hash），实际 %q",
				c.want, f.Selector)
		}
		if f.Stability != "high" {
			t.Errorf("站方组件号 %s 的 stability = %q，应为 high（判据：有稳定的 id）", c.want, f.Stability)
		}
	}

	// 反向：12 位 hex 的真随机 id 照旧被 RAND 形态① 拒掉 → 退化成别的候选
	f := fieldByPlaceholder(t, m, "e.g. random id")
	if strings.Contains(f.Selector, "a1b2c3d4e5f6") {
		t.Errorf("12 位 hex 的随机 id 不该当首选（selector=%q）—— "+
			"「纯十进制」这条豁免不许把随机 id 一起放进来", f.Selector)
	}
	t.Logf("站方组件号：173851 / textField-173838 当了首选；hex 随机 id 仍被拒（→ %q）", f.Selector)
}

// ── 题目正文在**上面第 3 层**的兄弟里：nearbyText 必须爬上去（2026-09-17 真站实测）──
//
// 真站实测（gowizard 的州那一格）：题目正文「What state do you live in?」**就在页面上**，
// 却挂在输入框往上第 3 层的 previousElementSibling 上，而输入框自己的兄弟全是空的
// 装饰容器 —— 旧 nearbyText 只看自己的兄弟，于是 `nearby_text` 是 `[]`，
// 三档语义全落空，那一格被填进一个人名（兜底 `full_name`）。
//
// 这条钉两件：① 爬得到（正文进了 nearby_text）；② 有界且**取到就停**（不会把整页
// 容器的字都收上来 —— 那种「多收」会直接把语义判错，比不收更坏）。
func TestObserveSelectorNearbyTextClimbsToQuestionText(t *testing.T) {
	m := selectorFixture(t)

	f := fieldByPlaceholder(t, m, "e.g. California or Texas")
	if f.Hint != "deepq" {
		t.Fatalf("取到的不是 fixture 里那个深层的 input（hint=%q）—— 用例与 fixture 脱节了", f.Hint)
	}
	joined := strings.Join(f.NearbyText, " | ")
	if !strings.Contains(joined, "What state do you live in?") {
		t.Errorf("题目正文没被收进 nearby_text（nearby_text=%q）—— "+
			"正文在往上第 3 层的 previousElementSibling 上，nearbyText 要爬上去", f.NearbyText)
	}
	// 有界：收上来的条数不许随着「往上爬」无限增长（每层最多收一条，且取到就停）
	if len(f.NearbyText) > 4 {
		t.Errorf("nearby_text 收了 %d 条（%q）—— 爬祖先那段应当「每层最多一条、取到就停」，"+
			"多收会把别的题目的字也带进来，语义直接判错", len(f.NearbyText), f.NearbyText)
	}
	t.Logf("深层 input：nearby_text=%q（正文在第 3 层祖先的前一个兄弟上）", f.NearbyText)
}
