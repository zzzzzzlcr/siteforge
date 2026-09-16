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
// 判据（规格 §4.3，见 brief「验收标准」）：
//
//	high   = 有稳定的 id / name / data-*，且**不含随机 hash**
//	medium = 结构路径（nth-of-type 链）≤3 层
//	low    = 依赖随机 class hash / 深结构路径 >3 层
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
	// 自证有效：fixture 现在有 11 个按钮 + 3 个 input = 14 个可动作元素（实测 14）。
	// 阈值取 10 是**地板**不是等值：少一两个元素说明不了什么，掉到个位数就是观测
	// 退化了，下面按文本取的断言会以 Fatal 说话，但这里先说清是**观测**的问题。
	if len(m.Actions) < 10 {
		t.Fatalf("selector.html 只观测到 %d 个可动作元素（fixture 有 11 按钮 + 3 输入，实测 14）—— 观测退化了", len(m.Actions))
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
// （id > name > data-* > class > 结构路径），且去重 ——
// pathSel 对同一个元素会再吐一次 `#full`，那一份必须被滤掉。
func TestObserveSelectorCandidatesOrderedAndDeduped(t *testing.T) {
	m := selectorFixture(t)

	f := fieldByPlaceholder(t, m, "Full")
	want := []string{
		"#full",
		`input[name="full-name"]`,
		`input[data-testid="full-tid"]`,
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

	// G：4 段结构路径（3 个 >），每一层的 nth-of-type 下标都是 1 —— 选择器完全确定。
	g := actionByText(t, m, "G deep path no id")
	const wantG = "section:nth-of-type(1) > article:nth-of-type(1) > div:nth-of-type(1) > button:nth-of-type(1)"
	if g.Selector != wantG {
		t.Fatalf("G 的 selector = %q，应为 %q（pathSel 上限 4 跳）", g.Selector, wantG)
	}
	// 判据：深结构路径 >3 层（= 4 段 = 3 个 >）→ low。代码里的口径是 depth(> 的个数) <= 2 才 medium。
	if g.Stability != "low" {
		t.Errorf("4 段结构路径的 stability = %q，应为 low（判据：深结构路径 >3 层）", g.Stability)
	}

	// H：同样 4 段，只差在**表头是祖先的稳定 id**。
	//
	// ⚠️ 这条是**特征化断言**（characterization），不是在认可当前判据：
	// `stability()` 的第一条分支是 `/^#/` → 只要选择器以 `#` 开头就答 high，
	// 于是 H 拿到 high —— 与同形状、无 id 表头的 G（low）只差一个表头，
	// 而两者尾巴一样脆（3 跳 nth-of-type，插一个 div 就断）。
	// 规格 §4.3 两种读法都不支持 high：按「结构路径 ≤3 层」读 → H 是 4 段；
	// 按「nth-of-type 层数」读 → H 有 3 层。high 只在「选择器里有 #」这一读法下成立。
	// 本任务不动实现（控制器：不做 Go 侧助手、先报发现），所以这里把**现状钉住**
	// 并在报告里提出来 —— 钉住是为了将来改判的人一眼看到差异，不是为了背书。
	h := actionByText(t, m, "H deep under stable id")
	if !strings.HasPrefix(h.Selector, "#deep-wrap > ") {
		t.Fatalf("H 的 selector = %q，应形如 `#deep-wrap > …`（pathSel 第 4 跳撞上祖先 id 就停）", h.Selector)
	}
	if n := strings.Count(h.Selector, ">"); n != 3 {
		t.Errorf("H 的 selector 有 %d 个 '>'（应为 3，与 G 同形状）—— 断言的前提变了，重新判读", n)
	}
	// H 元素**自身**没有任何稳定标识 —— 这是「high 是否成立」的关键事实：
	// 选择器里唯一那个 `#` 是**祖先**的，H 自己不在其中。
	if n := strings.Count(h.Selector, "#"); n != 1 {
		t.Errorf("H 的选择器里 `#` 出现 %d 次（应恰好 1 次 = 祖先 #deep-wrap）—— 断言前提变了: %q", n, h.Selector)
	}
	for _, own := range []string{"[name=", "[data-"} {
		if strings.Contains(h.Selector, own) {
			t.Errorf("H 的选择器里出现了 %q —— H 自身不该有稳定标识，断言前提变了: %q", own, h.Selector)
		}
	}
	t.Logf("H 特征化：selector=%q stability=%q（G 同形状无 id 表头 = %q）", h.Selector, h.Stability, g.Stability)
	if h.Stability != "high" {
		// 若这条红了，说明判据被改过（很可能就是按本条测试的顾虑修的）——
		// 那么请把这条特征化断言改成正经判据断言，并连带更新报告。
		t.Logf("⚠️ H 的 stability 不再是 high（现在 %q）—— 判据可能已被修改，请复核本测试的定位", h.Stability)
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
	// 与 `button.btn.btn-primary`（同一档）混在一起，agent 区分不出谁更可靠。
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

// RAND 的两种形态（observe.go:147）：
//
//	形态① 被 -/_ 或串首尾夹住的 **8 位以上** hex 片段
//	形态② 整个串 = 字母 + **6 位以上**数字
//
// 这一条跑**三种落点里 RAND 咬得住的那几档** —— 它们是现在就能过的部分，
// 在这里钉住，将来谁把 RAND 或 candidates() 的过滤改坏，这里先红。
func TestObserveSelectorRandomTokensNotPreferred(t *testing.T) {
	m := selectorFixture(t)

	cases := []randomTokenCase{
		{
			name: "8 位 hex class（形态①）", elemText: "E hash8", token: "css-1a2b3c4d",
			act:     actionByText(t, m, "E hash8"),
			whyRand: "`-` 后 8 位纯 hex，正好落在形态①的门槛上（≥8 位）",
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

	// name 这一路：元素自身没有 id / data-* / class，首选只可能来自 name。
	// 过滤掉随机 name 之后必须**退化成结构路径**（不是变成别的随机串，
	// 也不是把这条元素整个丢掉）—— 所以这里断言正面的结果，不只是「没有 token」。
	const nameToken = "sid_9f8e7d6c5b4a"
	requireTokenCarriedBy(t, "K random name", nameToken)
	nf := fieldByPlaceholder(t, m, "K random name")
	if strings.Contains(nf.Selector, nameToken) {
		t.Errorf("name 里带随机 token 时首选仍是它（%q，stability=%q）—— candidates() 对 id/data-*/class 都过了 RAND，"+
			"`if (el.name)` 这一行也要过。该 token = `_` 夹住的 12 位 hex（RAND 形态①，RAND 自己是认得的）",
			nf.Selector, nf.Stability)
	}
	if !strings.HasPrefix(nf.Selector, "body:nth-of-type(1) > input:nth-of-type(") {
		t.Errorf("随机 name 被滤掉后应退化成结构路径，实际 %q —— 要么没滤干净，要么把元素丢了", nf.Selector)
	}
	t.Logf("K random name：selector=%q stability=%q（随机 name 被 RAND 滤掉 → 退回结构路径）",
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
