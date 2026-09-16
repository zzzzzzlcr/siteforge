package internal

import (
	"encoding/json"
	"slices"
	"strings"
	"testing"
)

// Task 7：diff 回答「刚才那一下有没有推进」—— 它是 py 里**分支与重试的唯一依据**（规格 §4.6）。
//
// 判错任何一边的代价都很实在：
//   误判有进展 → 原地打转直到耗尽轮数（而且**不报任何错**）
//   误判没进展 → 明明走对了却重试
//
// ⚠️ 本文件里**最重要的**是 TestDiffImmuneToSelectorRename 那一组：计划里的实现拿
// **选择器集合**算 Appeared/Disappeared，而 T5 已经证明框架生成的 hash class
// 「一刷就变」（见 internal/testdata/README.md 的 RAND 一节）—— SPA 上什么都没发生、
// 只是重渲染了一遍，选择器就全换 → Appeared/Disappeared 都非空 → Actionable 谎报
// 「有进展」。那正是本项目最忌讳的那类错（静默、不报错、恰好落在核心判据上）。

func mkAction(selector, text, role, region string) Action {
	return Action{Selector: selector, Text: text, Role: role, Region: region}
}

func mkField(selector, label, typ, placeholder string) Field {
	return Field{Selector: selector, Label: label, Type: typ, Placeholder: placeholder}
}

func mkGroup(scope string, options ...string) OptionGroup {
	return OptionGroup{Scope: scope, Role: "option", Options: options}
}

// ---- brief 钉的三条（照抄，不改期望） ----

func TestDiffDetectsProgress(t *testing.T) {
	before := &PageModel{URL: "u1", PageText: "Step 1",
		Actions: []Action{{Selector: "#a", Text: "Next"}}}
	after := &PageModel{URL: "u2", PageText: "Step 2",
		Actions: []Action{{Selector: "#b", Text: "Submit"}}}
	d := DiffModels(before, after)

	if !d.URLChanged || !d.TextChanged || !d.Actionable {
		t.Errorf("明显推进了却判无进展: %+v", d)
	}
	if len(d.Disappeared) != 1 || d.Disappeared[0] != "#a" {
		t.Errorf("消失元素没识别: %q", d.Disappeared)
	}
	if len(d.Appeared) != 1 || d.Appeared[0] != "#b" {
		t.Errorf("新出现元素没识别: %q", d.Appeared)
	}
}

func TestDiffDetectsNoProgress(t *testing.T) {
	same := &PageModel{URL: "u", PageText: "Step 1",
		Actions: []Action{{Selector: "#a", Text: "Next"}}}
	d := DiffModels(same, same)
	if d.Actionable {
		t.Error("页面纹丝不动却判为有推进 —— py 的重试逻辑会因此失效")
	}
}

func TestDiffIgnoresWhitespaceOnlyTextChange(t *testing.T) {
	a := &PageModel{URL: "u", PageText: "Step 1 here"}
	b := &PageModel{URL: "u", PageText: "Step   1   here"}
	if DiffModels(a, b).TextChanged {
		t.Error("纯空白差异不该算页面变化")
	}
}

// ---- 控制器裁定 ②：对「选择器改名」免疫（本任务的核心） ----

// TestDiffImmuneToSelectorRename 是整个 Task 7 存在的理由。
//
// 场景（照 T5 的真发现构造）：同一个 SPA 页面，**内容一个字都没变**，只是框架重渲染
// 了一遍 —— 每个元素的 hash class 都换了名字，于是观察出来的 selector 全变了。
//
//	改名前: #schedule-now   →  改名后: button.css-9x8y7z6
//
// 期望：Actionable == false（py 该重试/换策略，而不是以为推进了继续往下走）。
//
// ⚠️ 计划里的实现（选择器集合差）在**这一条上必红** —— 它会报 Appeared/Disappeared
// 各一堆、Actionable=true。这就是为什么判据换成了「身份多重集」。
func TestDiffImmuneToSelectorRename(t *testing.T) {
	before := &PageModel{
		URL:      "https://x.example/quote",
		PageText: "Get your free quote ZIP Code Continue",
		Actions: []Action{
			mkAction("#schedule-now", "Schedule Now", "button", "hero"),
			mkAction("#go", "Continue", "button", "main"),
			mkAction("#nav-learn", "Learn More", "a", "header"),
			mkAction("#foot-learn", "Learn More", "a", "footer"),
		},
	}
	after := &PageModel{
		URL:      "https://x.example/quote",
		PageText: "Get your free quote ZIP Code Continue",
		Actions: []Action{
			// 同一批元素，**同名同区同角色**，只有选择器换了名字（顺序也打乱了）。
			mkAction("button.css-9x8y7z6", "Schedule Now", "button", "hero"),
			mkAction("body:nth-of-type(1) > main:nth-of-type(1) > button:nth-of-type(2)", "Continue", "button", "main"),
			mkAction("header:nth-of-type(1) > a:nth-of-type(1)", "Learn More", "a", "header"),
			mkAction("footer:nth-of-type(1) > a:nth-of-type(1)", "Learn More", "a", "footer"),
		},
	}
	d := DiffModels(before, after)

	if d.Actionable {
		t.Errorf("重渲染（选择器全改名、内容没变）被判成「有推进」—— py 会原地打转直到耗尽轮数。"+
			"\nappeared=%q\ndisappeared=%q", d.Appeared, d.Disappeared)
	}
	if len(d.Appeared) != 0 || len(d.Disappeared) != 0 {
		t.Errorf("改名后的元素被当成了「新出现/消失」—— Appeared 只能是零值。"+
			"\nappeared=%q\ndisappeared=%q", d.Appeared, d.Disappeared)
	}
	if d.URLChanged || d.TextChanged {
		t.Errorf("URL/正文没变却报了变化: %+v", d)
	}
}

// TestDiffImmuneToSelectorRenameAcrossChannels 把上面那条铺到**另外两条通道**：
// 表单字段的 selector、选项组的 scope 也都是观察出来的选择器，同样一刷就变。
//
// ⚠️ 表单字段的 Field.Hint **不是**内容：observeJS 把它填成 `el.name || el.id`，
// 是选择器级的东西（改名就变），所以它**不在**身份键里。这条测试守着这一点。
func TestDiffImmuneToSelectorRenameAcrossChannels(t *testing.T) {
	before := &PageModel{
		URL:      "https://x.example/quote",
		PageText: "ZIP Code Pick your plan",
		Fields: []Field{
			{Selector: "#zip", Label: "ZIP Code", Type: "text", Placeholder: "ZIP Code", Hint: "zip"},
		},
		OptionGroups: []OptionGroup{
			mkGroup("#plan-opts", "Basic", "Premium"),
		},
	}
	after := &PageModel{
		URL:      "https://x.example/quote",
		PageText: "ZIP Code Pick your plan",
		Fields: []Field{
			// 同一个字段：选择器与 hint（name/id）全换，label/placeholder/type 不变。
			{Selector: "input.css-a1b2c3d4", Label: "ZIP Code", Type: "text",
				Placeholder: "ZIP Code", Hint: "field_7f3a"},
		},
		OptionGroups: []OptionGroup{
			mkGroup(".css-e5f6a7b8", "Premium", "Basic"), // scope 变了，选项还那两项（顺序不同）
		},
	}
	d := DiffModels(before, after)

	if d.Actionable {
		t.Errorf("字段/选项组只是选择器改名，却被判成有推进。"+
			"\nappeared=%q\ndisappeared=%q", d.Appeared, d.Disappeared)
	}
	if len(d.Appeared) != 0 || len(d.Disappeared) != 0 {
		t.Errorf("改名被当成新出现/消失: appeared=%q disappeared=%q", d.Appeared, d.Disappeared)
	}
}

// ---- 判据的各条腿，逐条钉住（每一条都能被一个变异打红） ----

// TestDiffTreatsNBSPAsWhitespace：HTML 里 `&nbsp;` 遍地都是，Go 正则的 `\s` **抓不到它**
// （JS 的 \s 抓得到，所以这条路只在 Go 侧归一化不足时现形）。
// 「Submit」与「Submit&nbsp;」是同一个按钮，不该报成正文变化 —— 那是纯粹的误报，
// 而且会稳定地每轮都报（误判有进展 = py 原地打转）。
func TestDiffTreatsNBSPAsWhitespace(t *testing.T) {
	a := &PageModel{URL: "u", PageText: "Estimate ready  $42/mo"}
	b := &PageModel{URL: "u", PageText: "Estimate ready $42/mo"}
	if DiffModels(a, b).TextChanged {
		t.Error("NBSP(U+00A0) 与空格被当成了内容差异 —— HTML 里 &nbsp; 会让每一轮都误报「有推进」")
	}
}

// TestDiffRegionIsPartOfIdentity：区域是身份的一部分（控制器裁定 ② 点名的三个信号之一）。
// 「header 里那个 Learn More」和「footer 里那个 Learn More」文本完全相同，只有区域能区分。
//
// 变异：把 Region 从身份键里去掉 → 这条红（元素在 landmark 之间移动会被判成「没变化」）。
func TestDiffRegionIsPartOfIdentity(t *testing.T) {
	before := &PageModel{URL: "u", PageText: "Learn More",
		Actions: []Action{mkAction("#nav-learn", "Learn More", "a", "header")}}
	after := &PageModel{URL: "u", PageText: "Learn More",
		Actions: []Action{mkAction("#foot-learn", "Learn More", "a", "footer")}}

	d := DiffModels(before, after)
	if len(d.Disappeared) != 1 || d.Disappeared[0] != "#nav-learn" {
		t.Errorf("header 里的 Learn More 挪到了 footer，却没报「消失」: %q", d.Disappeared)
	}
	if len(d.Appeared) != 1 || d.Appeared[0] != "#foot-learn" {
		t.Errorf("footer 里新出现的 Learn More 没报出来: %q", d.Appeared)
	}
	if !d.Actionable {
		t.Error("元素从 header 挪到了 footer 是真变化，却判成没推进")
	}
	if d.TextChanged || d.URLChanged {
		t.Errorf("正文/URL 没变: %+v", d)
	}
}

// TestDiffNewFieldOrOptionGroupCountsAsProgress 守**误判没进展**那一侧：
// 正文与可动作元素都没变，但这一步真的推进了 —— 常见形态是「填完 ZIP，地址字段与
// 州下拉框被填出来了」。
//
// ⚠️ 为什么光靠 PageText 不够：`<select>` 的 option 文本**不进 innerText**，
// 而 placeholder 也不在 innerText 里 —— 这类变化在正文里根本看不见。
// 所以字段/选项组是独立的信号通道，不是装饰。
func TestDiffNewFieldOrOptionGroupCountsAsProgress(t *testing.T) {
	before := &PageModel{URL: "u", PageText: "Where do you live?",
		Fields: []Field{mkField("#zip", "ZIP Code", "text", "ZIP Code")}}
	after := &PageModel{URL: "u", PageText: "Where do you live?",
		Fields: []Field{
			mkField("#zip", "ZIP Code", "text", "ZIP Code"),
			mkField("#state", "State", "text", "State"),
		},
		OptionGroups: []OptionGroup{mkGroup("#state-opts", "CA", "NY", "TX")}}

	d := DiffModels(before, after)
	if !d.TextChanged && len(d.Appeared) == 0 {
		t.Fatalf("字段/选项组是唯一的信号通道，却什么都没报: %+v", d)
	}
	// 两条通道各报一个：新字段 #state + 新选项组 #state-opts（选项组的抓手是它的 scope）。
	// #zip 没变，**不该**出现在里面 —— 那正是「匹配到身份就不报」的意思。
	want := []string{"#state", "#state-opts"}
	if !slices.Equal(d.Appeared, want) {
		t.Errorf("appeared = %q, want %q（#zip 没变，不该出现在里面）", d.Appeared, want)
	}
	if len(d.Disappeared) != 0 {
		t.Errorf("disappeared = %q, want []", d.Disappeared)
	}
	if !d.Actionable {
		t.Error("页面真的变了（多出一个字段），却判成没推进")
	}
}

// TestDiffDuplicateTextCountsAsOneAppearance：文本重复的元素用**多重集**比 ——
// 两个一模一样的按钮里多出一个，只报**一个** appeared，不是两个。
func TestDiffDuplicateTextCountsAsOneAppearance(t *testing.T) {
	before := &PageModel{URL: "u", PageText: "Add",
		Actions: []Action{mkAction("#add-1", "Add", "button", "main")}}
	after := &PageModel{URL: "u", PageText: "Add",
		Actions: []Action{
			mkAction("#add-1", "Add", "button", "main"),
			mkAction("#add-2", "Add", "button", "main"),
		}}

	d := DiffModels(before, after)
	if len(d.Appeared) != 1 || d.Appeared[0] != "#add-2" {
		t.Errorf("appeared = %q, want [\"#add-2\"]（多出来的是第二个；第一个被匹配掉了）", d.Appeared)
	}
	if len(d.Disappeared) != 0 {
		t.Errorf("disappeared = %q, want []", d.Disappeared)
	}
}

// TestDiffSameElementOnTwoChannelsReportedOnce：同一个 DOM 元素会被观察**两条通道**
// 各收一次（observeJS 的可动作元素选择器里含 `input,select,textarea`，表单字段收集器
// 也收这一批）—— 于是一个 input 消失时，两条身份各报一次，报出来却是**同一个选择器**。
//
// 计数上两条身份必须各算各的（一个 input 变成纯字段不能被漏掉），但报给调用方的
// 抓手列表要去重：`["#zip","#zip"]` 看着像 bug，而两条身份的区别在这个 `[]string`
// 里本来就表达不出来。
func TestDiffSameElementOnTwoChannelsReportedOnce(t *testing.T) {
	before := &PageModel{
		URL:      "u",
		PageText: "ZIP Code",
		Actions:  []Action{mkAction("#zip", "", "input", "main")},
		Fields:   []Field{mkField("#zip", "ZIP Code", "text", "ZIP Code")},
	}
	after := &PageModel{URL: "u", PageText: "Step 2"}

	d := DiffModels(before, after)
	if len(d.Disappeared) != 1 || d.Disappeared[0] != "#zip" {
		t.Errorf("disappeared = %q, want [\"#zip\"]（同一个元素走了两条通道，只该报一次）", d.Disappeared)
	}
	if !d.Actionable {
		t.Error("卡片整块换掉了却判成没推进")
	}
}

// TestDiffRegionClassDerivedValuesAreOneBucket：区域**只取抗改名的那一半**。
//
// 由来（修复轮 1 的 C1）：observeJS 的 region() 有两个来源 —— landmark 标签名
// （header/nav/footer/aside/main，结构，抗改名）与**按祖先 className 判的** 'hero'
// （生成物，一刷就变）。身份键若直接用 Region，则「祖先 class 全换」会把这一个元素
// 判成「消失 + 出现」→ 内容没变却报「有进展」。
//
// 所以：landmark 保留（真结构），'hero'/'body'/未知值一律归成同一个空桶。
// ⚠️ 这条测试的「未知值」那一格是有意的：观察者以后新增任何**派生**区域值，
// 都会落进空桶而不是悄悄变成改名敏感。
func TestDiffRegionClassDerivedValuesAreOneBucket(t *testing.T) {
	same := func(r1, r2 string) bool {
		before := &PageModel{URL: "u", PageText: "p",
			Actions: []Action{mkAction("#a", "Schedule Now", "button", r1)}}
		after := &PageModel{URL: "u", PageText: "p",
			Actions: []Action{mkAction("button.css-9x8y7z6", "Schedule Now", "button", r2)}}
		return DiffModels(before, after).Actionable
	}
	// 桶内互换（含以后可能新增的派生值）→ 不算变化
	for _, pair := range [][2]string{{"hero", "body"}, {"body", "hero"}, {"hero", "cta"}, {"", "hero"}} {
		if same(pair[0], pair[1]) {
			t.Errorf("region %q → %q 被判成了变化 —— 派生出来的区域值不该进身份键"+
				"（祖先 class 一换就翻，内容没变却报有进展）", pair[0], pair[1])
		}
	}
	// landmark 之间是真结构，仍然要区分
	for _, pair := range [][2]string{{"header", "footer"}, {"main", "body"}, {"dialog", "body"}} {
		if !same(pair[0], pair[1]) {
			t.Errorf("region %q → %q 没被判成变化 —— landmark 是抗改名的结构信号，不该被一起抹掉",
				pair[0], pair[1])
		}
	}
}

// TestDiffFieldLabelIsNotIdentity：字段的 Label **不在**身份键里。
//
// 由来（修复轮 1 的 C1）：observeJS 取标签有三级 —— aria-label → closest('label')
// → **label[for=id]**，最后那条与 id 耦合：重渲染把 input 重新挂载成新 id，这条
// 关联就断，Label 凭空变成空串 → 身份翻转 → 内容没变却报「有进展」。
// 观察者侧的修法是把它降到最低优先级（保留感知价值），**消费者侧**则干脆不用它。
//
// 代价（有意接受）：两个 Label/Placeholder 都空、Type 相同的输入框会塌成一个身份
// —— 增删仍看得见（多重集计数），互换看不见。为一个**派生**字段付这个价是划算的。
func TestDiffFieldLabelIsNotIdentity(t *testing.T) {
	before := &PageModel{URL: "u", PageText: "ZIP Code",
		Fields: []Field{mkField("#zip", "ZIP Code", "text", "ZIP Code")}}
	// 同一个字段：for=/id 断了 → Label 变空，其余不变。
	after := &PageModel{URL: "u", PageText: "ZIP Code",
		Fields: []Field{mkField("input.css-a1b2c3d4", "", "text", "ZIP Code")}}

	d := DiffModels(before, after)
	if d.Actionable {
		t.Errorf("只断了 label[for=id] 的配对（重渲染换 id 的必然结果）就报「有进展」。"+
			"\nappeared=%q\ndisappeared=%q", d.Appeared, d.Disappeared)
	}
	// 反向：Placeholder/Type 是内容，变了必须报出来（别把整个字段身份键掏空）。
	other := &PageModel{URL: "u", PageText: "ZIP Code",
		Fields: []Field{mkField("#zip", "ZIP Code", "text", "Postal Code")}}
	if !DiffModels(before, other).Actionable {
		t.Error("placeholder 变了却判成没变化 —— 字段身份键被掏空了")
	}
}

// TestDiffOptionGroupsDoNotCollapseOnSeparator 是修复轮 1 的 M2：
// 选项原先用可打印的 `|` 拼成一个字符串，于是
//
//	["a|b"]（一个选项，文本里带竖线）  vs  ["a","b"]（两个选项）
//
// **塌成同一个键** → 静默漏报。改成用身份键分隔符（NUL）分段拼。
func TestDiffOptionGroupsDoNotCollapseOnSeparator(t *testing.T) {
	before := &PageModel{URL: "u", PageText: "plans",
		OptionGroups: []OptionGroup{mkGroup("#opts", "a|b")}}
	after := &PageModel{URL: "u", PageText: "plans",
		OptionGroups: []OptionGroup{mkGroup("#opts", "a", "b")}}

	d := DiffModels(before, after)
	if !d.Actionable {
		t.Error("「一个选项文本为 a|b」与「两个选项 a、b」被当成同一件事 —— 选项键塌了（M2）")
	}
	if len(d.Appeared) != 1 || len(d.Disappeared) != 1 {
		t.Errorf("appeared=%q disappeared=%q, want 各 1 个", d.Appeared, d.Disappeared)
	}
}

// TestDiffCarriesDiagnosticsCounts 是修复轮 1 的 I3：把「观测本身不全」的**条数**
// 带进 Diff，让调用方自己判断要不要信这次差分。
//
// ⚠️ 为什么是「带条数」而不是「让 diagnostics 影响 Actionable」：
// 观测不全与页面变空在模型里长得一样（某帧没取到 → 它的元素「消失」→ 报有进展），
// 但把 diagnostics 折进 Actionable 会更糟 —— 广告/追踪帧失败在真站上是**常态**，
// 那样这些页面会**永远**报不出进展（另一个方向的坑，而且更常见）。
// 所以：事实报出来，结论留给调用方（与 observe「diagnostics 非空仍是退出码 0」同一条原则）。
func TestDiffCarriesDiagnosticsCounts(t *testing.T) {
	before := &PageModel{URL: "u", PageText: "Step 1", Diagnostics: []Diagnostic{{Kind: DiagKindFrameError}}}
	after := &PageModel{URL: "u", PageText: "Step 2",
		Diagnostics: []Diagnostic{{Kind: DiagKindFrameError}, {Kind: DiagKindFrameBlind}}}

	d := DiffModels(before, after)
	if d.DiagnosticsBefore != 1 || d.DiagnosticsAfter != 2 {
		t.Errorf("diagnostics_before/after = %d/%d, want 1/2 —— 观测不全这件事必须能被调用方看见",
			d.DiagnosticsBefore, d.DiagnosticsAfter)
	}
	if !d.Actionable {
		t.Error("正文真的变了，diagnostics 不该把它压成「没推进」")
	}
	// 反向钉住：diagnostics **不参与**判据（一份干净观测 + 一份有诊断的观测，
	// 只要内容一样，判据就该一样）。
	clean := &PageModel{URL: "u", PageText: "Step 2"}
	if d2 := DiffModels(before, clean); d2.Actionable != d.Actionable || d2.TextChanged != d.TextChanged {
		t.Errorf("diagnostics 影响了判据: %+v vs %+v", d2, d)
	}
}

// TestDiffEmptyListsMarshalAsArrays：与 PageModel 同一条约定 ——
// 空列表编成 `[]` 不是 `null`（py 侧 `for s in diff["appeared"]` 撞上 null 就 TypeError）。
func TestDiffEmptyListsMarshalAsArrays(t *testing.T) {
	same := &PageModel{URL: "u", PageText: "Step 1"}
	raw, err := json.Marshal(DiffModels(same, same))
	if err != nil {
		t.Fatalf("编 JSON 失败: %v", err)
	}
	s := string(raw)
	for _, k := range []string{`"appeared":[]`, `"disappeared":[]`} {
		if !strings.Contains(s, k) {
			t.Errorf("输出里没有 %s —— 空列表编成了 null？\n%s", k, s)
		}
	}
}

// TestDiffNilModelsDoNotPanic：nil 当零值模型处理（纯函数、不 panic）。
// CLI 那条路拿不到 nil（观测失败会先返回 error），这里只保证不炸。
func TestDiffNilModelsDoNotPanic(t *testing.T) {
	if d := DiffModels(nil, nil); d.Actionable {
		t.Errorf("两个 nil 不该判成有推进: %+v", d)
	}
	if d := DiffModels(nil, &PageModel{URL: "u", PageText: "x"}); !d.Actionable {
		t.Errorf("从「什么都没有」到有内容，是变化: %+v", d)
	}
	if len(DiffModels(nil, nil).Appeared) != 0 {
		t.Error("Appeared 应为空")
	}
}
