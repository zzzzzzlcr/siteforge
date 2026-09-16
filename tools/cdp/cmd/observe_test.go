package cmd

import (
	"bytes"
	"strings"
	"testing"

	"cdp/internal"
)

// observe 子命令的 CLI 契约（Task 6 Step 1）。
//
// ⚠️ 这一条只是**字符串断言级别**的弱测试：它证明 flag 挂上去了，证明不了
// observe 真的能跑通、输出真的是 PageModel JSON。真正的闸门是
// observe_e2e_test.go（真构建二进制 + 真打页面）。这条留着是因为它便宜：
// 一个 flag 被误删时它先红，不必等 e2e。
func TestObserveCommandHasContractFlags(t *testing.T) {
	cmd := observeCmd
	if cmd == nil {
		t.Fatal("observeCmd 未注册")
	}
	for _, f := range []string{"json", "frame-id", "expect-url"} {
		if cmd.Flags().Lookup(f) == nil {
			t.Errorf("observe 子命令缺 --%s", f)
		}
	}
	if cmd.Short == "" {
		t.Error("observe 子命令缺 Short 描述")
	}
	if cmd.Use != "observe" {
		t.Errorf("Use = %q, want %q", cmd.Use, "observe")
	}
	// --json 默认 true（brief 钉的值）：契约是「默认就吐 JSON」。
	// 光看 flag 存不存在不够 —— 默认 false 时用户不加 flag 就什么都拿不到。
	if f := cmd.Flags().Lookup("json"); f != nil && f.DefValue != "true" {
		t.Errorf("--json 默认值 = %q, want \"true\"", f.DefValue)
	}
}

// 注册必须真的挂在 rootCmd 上：brief 的包级变量检查（observeCmd != nil）
// 过不了这条 —— 变量存在但没 AddCommand 的 CLI，用户敲 observe 会得到
// `unknown command`。
func TestObserveCommandRegisteredOnRoot(t *testing.T) {
	found := false
	for _, sub := range rootCmd.Commands() {
		if sub.Name() == "observe" {
			found = true
			if sub == observeCmd {
				continue
			}
		}
	}
	if !found {
		t.Error("observe 未注册到 rootCmd")
	}
}

// TestRenderHumanSurfacesTargetAmbiguous —— 人话输出必须把 target-ambiguous 说出来，
// 而且 detail 里的 target ID 不许被截掉（ID 就是人判断「是不是看错页了」的依据）。
//
// 为什么不放 e2e：那个状态是**一次性**的 —— 它靠把页面主线程堵死来造，而第一次观测
// 的求值会排队等到主线程放开才返回；等它回来页面已经不堵了，第二次观测就恢复正常
// （实测：在 e2e 里写这条时，第二遍 --json=false 拿到的是「诊断 0 条」，断言红得
// 毫无意义）。这一行要证的是「印得对不对」，不需要真浏览器。
func TestRenderHumanSurfacesTargetAmbiguous(t *testing.T) {
	const pickedID = "A916E5F1A20E1885467D6FE3AAD587ED"
	m := &internal.PageModel{
		URL:   "http://127.0.0.1:9/__busy.html",
		Title: "R3 busy",
		Diagnostics: []internal.Diagnostic{{
			Kind: internal.DiagKindTargetAmbiguous,
			// 与 targetDiagsFor 产出的形状一致（含挑中的 ID 与 URL）
			Detail: "页面目标共 2 个候选，没有一个报 visible（可能都是后台页，也可能可见性检查本身就失败了）" +
				"—— 退回选中第一个：" + pickedID + " (http://127.0.0.1:9/__busy.html)",
			FramePath: []string{"main"},
		}},
	}

	var buf bytes.Buffer
	if err := renderHuman(&buf, m); err != nil {
		t.Fatalf("renderHuman 失败: %v", err)
	}
	out := buf.String()

	// 「诊断 N 条」那一行要数上它（运营扫一眼就知道有没有事）
	if !strings.Contains(out, "诊断 1 条") {
		t.Errorf("摘要里的诊断条数没算上它:\n%s", out)
	}
	// kind 与 detail 的两个要件都要露出来（target ID 被截掉就等于没这条诊断）
	for _, want := range []string{
		internal.DiagKindTargetAmbiguous,
		pickedID,
		"2 个候选",
		"没有一个报 visible",
	} {
		if !strings.Contains(out, want) {
			t.Errorf("人话输出里没有 %q —— 人看不到「可能看错页了、猜的是哪个」:\n%s", want, out)
		}
	}
	// 它走的是「诊断」那一块，不是「遮挡物」—— 两个通道混了会让读者去点一个不存在的按钮
	if i, j := strings.Index(out, "诊断（"), strings.Index(out, internal.DiagKindTargetAmbiguous); i < 0 || j < i {
		t.Errorf("target-ambiguous 没出现在诊断块里:\n%s", out)
	}
	t.Logf("人话输出:\n%s", out)
}

// TestRenderHumanSurfacesHoneypots —— 人话输出**必须**把蜜罐陷阱说出来
// （Task 1 spike 实测，2026-09-17 计划 §4.2）。
//
// 缺陷是什么：同一次观测，JSON 里有 `honeypots`，而 `--json=false` 的摘要
// （`可动作元素 10 个 表单字段 0 个 …`）**只字未提** —— 给人看的那一路把
// 「有一个字段是陷阱，AI 没碰它」静默吞了。规格 D16 要的恰恰是**非技术**的人
// 能看见这件事（honeypots 的注释自己写着「丢干净之后消费者分不清『本来就没有』
// 和『被判成陷阱丢了』」）。
//
// 断言的四件事，缺一条这一节就没达到目的：
//
//	① 有这一节（否则运营根本不知道发生了什么）
//	② 认得出来是**哪个字段**（hint —— 只给选择器等于没说，那是给机器的）
//	③ 说的是**人话**（判据名 off-document-left 翻成人能懂的方位）
//	④ 明确「AI 没碰它」（那正是运营要的安心话）
//
// 为什么不放 e2e：打印形状不需要真浏览器；真页面那半在
// TestObserveCommandHumanFormatShowsHoneypots（那边用 JSON 那一路做前置对照，
// 证明真有陷阱、人话里却没有 —— 那才是端到端的那条）。
func TestRenderHumanSurfacesHoneypots(t *testing.T) {
	m := &internal.PageModel{
		URL:     "https://check.example/funnel",
		Title:   "Funnel",
		Actions: []internal.Action{{Selector: "#submit", Stability: "high", Text: "Get My Quote"}},
		Fields:  []internal.Field{{Selector: "input[name=email]"}},
		Honeypots: []internal.Honeypot{
			// 真站实测那一条的形状（2026-09-16，compareinsulation 漏斗页）
			{Selector: `input[name="company_url"]`, Hint: "company_url", Why: "off-document-left"},
			// hint 空（元素没有 name / id / placeholder）：那一格不能塌成空白
			{Selector: `input[type="text"]:nth-of-type(3)`, Why: "off-document-top"},
			// 认不出来的判据：原样带出来，不许静默吞掉（Why 是契约值，以后会加）
			{Selector: "#trap3", Hint: "zip_confirm", Why: "off-document-future-axis"},
		},
	}

	var buf bytes.Buffer
	if err := renderHuman(&buf, m); err != nil {
		t.Fatalf("renderHuman 失败: %v", err)
	}
	out := buf.String()

	for _, want := range []string{
		"蜜罐陷阱",                     // ① 有这一节
		"共 3 个",                    // 条数（三个陷阱都数上了）
		"company_url",              // ② 认得出是哪个字段（hint）
		"zip_confirm",              // 同②
		"页面左边之外",                   // ③ 判据翻成人话（不是 off-document-left 这种）
		"页面顶部之外",                   // 同③，另一条轴
		"off-document-future-axis", // ③' 不认识的判据原样带出来
		"没有把它们当成可填字段",              // ④ 「AI 没碰它」
		"（这个字段没有 name / id / placeholder）", // ②' 空 hint 的兜底（那一格不能是空白）
		`input[name="company_url"]`,        // 技术抓手（排查时对得回 JSON）
	} {
		if !strings.Contains(out, want) {
			t.Errorf("人话输出里没有 %q —— 运营看不到「有个字段是陷阱、AI 没碰它」:\n%s", want, out)
		}
	}
	t.Logf("人话输出:\n%s", out)
}

// TestRenderHumanOmitsHoneypotSectionWhenNone —— 没有陷阱的页面**不许**印这一段
// （也不许印一行空的/让人看不懂的东西）。
//
// 为什么这个方向也是契约：这一段的存在意义只是「AI 识别出了陷阱、并且没碰它」那句
// 安心话。绝大多数页面没有陷阱 —— 常驻一行「蜜罐陷阱：无」就是噪音，还会让运营以为
// 这是个要盯着的常态栏目（与「诊断：无」「遮挡物：无」不同：那两样说的都是页面上
// **本该有**的东西）。
func TestRenderHumanOmitsHoneypotSectionWhenNone(t *testing.T) {
	m := &internal.PageModel{
		URL:     "https://check.example/plain",
		Title:   "Plain",
		Actions: []internal.Action{{Selector: "#go", Stability: "high", Text: "Continue"}},
		// 空切片（不是 nil）：两条路（JS 来的 / Go 侧构造的）都得测到同一个行为
		Honeypots: []internal.Honeypot{},
	}

	var buf bytes.Buffer
	if err := renderHuman(&buf, m); err != nil {
		t.Fatalf("renderHuman 失败: %v", err)
	}
	out := buf.String()

	if strings.Contains(out, "蜜罐") || strings.Contains(out, "陷阱") {
		t.Errorf("没有陷阱的页面上印了蜜罐那一节 —— 「这一页没有陷阱」是常态，"+
			"常驻一行是噪音（运营会以为这是个要盯的栏目）:\n%s", out)
	}
	// 反向对照：不是「整段输出都没了」——其余各节照旧（否则上面那条会因为
	// 「什么都没印」而假绿）。
	for _, want := range []string{"可动作元素", "#go", "遮挡物：无", "诊断：无"} {
		if !strings.Contains(out, want) {
			t.Errorf("人话输出里少了 %q —— 摘要是整体坏掉了，不是「只是没印蜜罐那一节」:\n%s", want, out)
		}
	}
	t.Logf("人话输出:\n%s", out)
}

// TestRenderHumanSurfacesTheThreeReads —— 人话摘要也要看得见这三样。
//
// 为什么（与 TestRenderHumanSurfacesHoneypots 同一条理由）：`--json=false` 是给
// **非技术**的人看的那一路（D14/D15：运营要能自助看懂「AI 看清了什么」）。
// 三个新字段只在 JSON 里、人话里一个字不提的话，坐在旁边的人依然回答不了
// 「它答的是哪个」「这框填进去没有」—— 而那正是它卡住时人唯一想知道的。
//
// 但**不许变成噪音**：这三样只在**有话说**的时候印。
//
//	名字：有文字用文字，**没有文字**才回落到无障碍名（真站上 Back 与三个图标选项
//	      text 全是空串 —— 不回落到那儿这一列就是四个一模一样的空白行）
//	已选：**只在真的选着时**印。false 与 null（看不出）都不印 ——
//	      印「未选」等于替页面断言，「看不出」印成任何东西都是在猜
//	值：  只在**是值控件**时印（按钮没有值这回事）
func TestRenderHumanSurfacesTheThreeReads(t *testing.T) {
	yes, no := true, false
	chosen := "2020"
	m := &internal.PageModel{
		URL:   "https://quote.example/step-2",
		Title: "问答页",
		Actions: []internal.Action{
			{Selector: "#opt-sedan", Stability: "low", Text: "Sedan", Selected: &yes},
			{Selector: "#opt-suv", Stability: "low", Text: "SUV", Selected: &no},
			// 没有状态标记的自定义控件：selected 是 null（看不出）——
			// 人话里**不许**因此印「未选」之类的东西
			{Selector: "#opt-nostate", Stability: "low", Text: "Truck"},
			// 没有文字、只有无障碍名的那一个（真站上的 Back）
			{Selector: "button.choice", Stability: "medium", AriaLabel: "Back"},
			// 值控件：值要印出来（这一个同时是「已选」—— <select> 选中了 2020）
			{Selector: "#year", Stability: "high", Text: "Year Choose… 2019 2020", Value: &chosen, Selected: &yes},
		},
	}

	var buf bytes.Buffer
	if err := renderHuman(&buf, m); err != nil {
		t.Fatalf("renderHuman 失败: %v", err)
	}
	out := buf.String()

	for _, want := range []string{
		"已选",   // 选着的那个说出来了（不然「答的是哪个」还是没人知道）
		"Back", // 没有文字的控件靠无障碍名认得出来
		"2020", // 值控件里装着的值
		"Sedan",
	} {
		if !strings.Contains(out, want) {
			t.Errorf("人话输出里没有 %q —— 运营看不到这一页选了什么、那个无名按钮叫什么、框里装着什么:\n%s", want, out)
		}
	}
	// 「已选」只该出现**一次**（#opt-sedan 与 #year 都是 true —— 两个都选着，
	// 所以两次；这里数的是不能多到把 false / null 也标上）
	if n := strings.Count(out, "已选"); n != 2 {
		t.Errorf("「已选」印了 %d 次，want 2（只有 selected=true 的两个）—— "+
			"多印出来的那些里必然混着 false 或 null，而那是**猜**:\n%s", n, out)
	}
	// false 与 null 都不许被印成任何东西（这一列只有 true 才说话）
	for _, banned := range []string{"未选", "没选", "看不出", "未知"} {
		if strings.Contains(out, banned) {
			t.Errorf("人话输出里出现了 %q —— false 与 null 都不该被翻译成一个说法"+
				"（「看不出」印成「没选」正是这个缺陷本来的样子）:\n%s", banned, out)
		}
	}
	t.Logf("人话输出:\n%s", out)
}

// TestRenderHumanValueShowsEmptyAndTruncated —— 值的两个边界：**空的**要说出来、
// **截过的**不能看起来像完整的。
//
// 为什么「空」必须有个说法：`value` 是空串时它是**一句关于页面的断言**
// （「这个框现在是空的」= 我刚那次写入没落地）。而它不是值控件时（Value 为 nil）
// 什么也不该印 —— 两件事在输出里长得一样的话，读的人分不出「没填进去」与「这不是个框」。
func TestRenderHumanValueShowsEmptyAndTruncated(t *testing.T) {
	blank := ""
	long := strings.Repeat("1HGCM82633A", 12) // 144 字符，肯定超过回读上限（80）
	m := &internal.PageModel{
		URL: "https://quote.example/form",
		Actions: []internal.Action{
			{Selector: "#email", Stability: "high", Text: "Email", Value: &blank},
			{Selector: "#long", Stability: "high", Text: "VIN", Value: &long, ValueTruncated: true},
			{Selector: "#submit", Stability: "high", Text: "Continue"}, // 不是值控件
		},
	}

	var buf bytes.Buffer
	if err := renderHuman(&buf, m); err != nil {
		t.Fatalf("renderHuman 失败: %v", err)
	}
	out := buf.String()

	if !strings.Contains(out, "（空）") {
		t.Errorf("空的输入框在人话里什么都没说 —— 读的人分不出「没填进去」与「这不是个框」:\n%s", out)
	}
	if !strings.Contains(out, "#long") || strings.Contains(out, long) {
		t.Errorf("超长的值被原样印出来了（%d 个字符）—— 摘要会被一个 textarea 撑散:\n%s", len(long), out)
	}
	// 截过的值必须看得出被截过（尾部那个省略号）
	if !strings.Contains(out, "…") {
		t.Errorf("被截断的值在人话里没有任何「不全」的标记:\n%s", out)
	}
	// 反向对照：不是值控件的元素（按钮）不许被印上一个空值
	if strings.Contains(out, "Continue = ") || strings.Contains(out, "Continue=（空）") {
		t.Errorf("按钮被印上了「值」—— 它没有值这回事，编一个空值等于替页面断言:\n%s", out)
	}
	t.Logf("人话输出:\n%s", out)
}
