package internal

import (
	"strings"
	"testing"
)

// click 的**目标解析**：单元那一层（不起浏览器）。
//
// 真浏览器上的行为在 cmd/click_target_e2e_test.go 与 cmd/mcp_click_e2e_test.go
// 里钉（那两条是实测形态的复刻）。这里钉的是两件**跑起来很贵、坏起来很静默**的事：
//
//	① 探测脚本的判据（少一条判据 = 那一类禁用目标重新变成「点了没反应但报成功」）
//	② 严格判据的纯逻辑（判据顺序、消息里该有什么）
//
// ⚠️ ① 的断言落在**函数体**里，不是整个脚本里（见 observe_traps_test.go 的
// jsFuncBody 注释：全脚本级的断言会被另一处的同名调用满足，变异照样绿）。

// probeBody 是探测脚本剥掉穿透助手与注释之后的正文。
func probeBody(t *testing.T, selector string) string {
	t.Helper()
	return stripJSComments(strings.TrimPrefix(probeTargetJS(selector), pierceJS))
}

// TestProbeTargetJSUsesTheSameResolverAsTheClick 钉「报的数与点的那个是同一套解析」。
//
// 探测若自己写一套遍历（比如 document.querySelectorAll），在 shadow 页上它会
// 报一套、点击又按另一套去点 —— 而两边都不报错，只是数字对不上
// （internal/shadow.go 的文件头就是为这件事写的）。
func TestProbeTargetJSUsesTheSameResolverAsTheClick(t *testing.T) {
	body := probeBody(t, "#go")
	if !strings.Contains(body, "__cdpQA(") {
		t.Error("探测没有走内核穿透助手 __cdpQA —— 与 click / GetElementCenter 的解析会分家" +
			"（shadow 页上两边报的数对不上，且都不报错）")
	}
	// ⚠️ 只许用 __cdpQA：__cdpQ 只回一个，回不了「命中几个」。
	if strings.Contains(body, "__cdpQ(") {
		t.Error("探测里出现了 __cdpQ（只回第一个匹配）—— 命中数就报不出来了")
	}
}

// TestProbeTargetJSDisabledJudgementIsBothWays 是缺陷 2 的判据本身。
//
// 两条判据缺一不可，而且必须落在**真正被调用的那个函数体**里：
//   - `disabled` 属性走 IDL（button/input/select/textarea/fieldset）
//   - `aria-disabled="true"` 走 ARIA（IDL 仍是 false，忽略点击是站点 JS 的约定）
//
// 只认一条的后果：另一类目标重新变成「点了没反应，但回执说成功」。
func TestProbeTargetJSDisabledJudgementIsBothWays(t *testing.T) {
	body := jsFuncBody(t, probeBody(t, "#go"), "one")

	if !strings.Contains(body, ".disabled") {
		t.Error("探测里没有读 `disabled` 属性（IDL）—— 真站上 Continue 那一下就是它")
	}
	if !strings.Contains(body, "aria-disabled") {
		t.Error("探测里没有读 `aria-disabled` —— MUI / Bootstrap 那套自定义控件用它，" +
			"漏掉这一类 = 点了没反应但报成功")
	}
}

// TestProbeTargetJSEscapesSelector 钉选择器是**被转义后**嵌进 JS 字符串的。
//
// 不转义的话，带单引号的选择器会把脚本截断 → 探测报「element not found」，
// 而点击那条路（同样转义过了）照样能点 —— 于是「探测说没找到、点击却成功了」，
// 两边对不上还不报错。
func TestProbeTargetJSEscapesSelector(t *testing.T) {
	js := probeTargetJS(`a[href="it's"]`)
	if strings.Contains(js, `'a[href="it's"]'`) {
		t.Error("选择器里的单引号没转义，脚本会被截断")
	}
	if !strings.Contains(js, `it\'s`) {
		t.Errorf("选择器没有按 escapeJS 转义后嵌入: %s", js)
	}
}

// ---- 严格判据（纯逻辑） ----

func probeOf(cands ...ClickCandidate) *ClickProbe {
	return &ClickProbe{MatchCount: len(cands), MatchIndex: 0, Candidates: cands}
}

func TestStrictClickRefusalAmbiguity(t *testing.T) {
	p := probeOf(
		ClickCandidate{Tag: "button", AriaLabel: "Back"},
		ClickCandidate{Tag: "button", AriaLabel: "Disagree"},
		ClickCandidate{Tag: "button", Text: "Continue", Disabled: true},
	)
	err := strictClickRefusal("button.choice", p)
	if err == nil {
		t.Fatal("命中 3 个元素却没拒绝 —— 这正是缺陷 1（静默点到文档序第一个）")
	}
	msg := err.Error()
	// 要**点名几个**：「歧义」不说数量，调用方改不动选择器。
	if !strings.Contains(msg, "3") {
		t.Errorf("报错没说是几个匹配: %s", msg)
	}
	// 要列出候选，且候选要够认得出（那 5 个按钮的 text 全是空，只有 aria-label 分得开）。
	for _, want := range []string{"Back", "Disagree", "Continue"} {
		if !strings.Contains(msg, want) {
			t.Errorf("报错里没有候选 %q —— 调用方无从判断该换哪个选择器:\n%s", want, msg)
		}
	}
}

func TestStrictClickRefusalDisabled(t *testing.T) {
	for _, tc := range []struct {
		name string
		dis  ClickCandidate
	}{
		{"IDL disabled", ClickCandidate{Tag: "button", Text: "Continue", Disabled: true}},
		{"aria-disabled", ClickCandidate{Tag: "button", Text: "Next", Disabled: true}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			err := strictClickRefusal("#go", probeOf(tc.dis))
			if err == nil {
				t.Fatal("禁用的目标没被拒绝 —— 点了什么都不会发生，而回执看起来像成功")
			}
			if !strings.Contains(err.Error(), "禁用") {
				t.Errorf("报错没说清是「目标被禁用」: %v", err)
			}
		})
	}
}

func TestStrictClickRefusalAllowsUniqueEnabled(t *testing.T) {
	if err := strictClickRefusal("#go", probeOf(ClickCandidate{Tag: "button", Text: "Go"})); err != nil {
		t.Errorf("唯一且可点的目标被拒了（过度拒绝）: %v", err)
	}
	// nil 探测（探测没跑成）不许在这里 panic —— 调用方会自己决定怎么办。
	if err := strictClickRefusal("#go", nil); err != nil {
		t.Errorf("nil 探测应当放过（由调用方决定）: %v", err)
	}
}

// TestStrictClickRefusalChecksAmbiguityFirst：两条都命中时，报的是**歧义**。
//
// 判据顺序不是随便定的：歧义让「寻址」这件事本身不成立 —— 那时目标是不是禁用
// 都无从谈起（第一个匹配可能可点、也可能不可点），先把选择器改对才是下一步。
func TestStrictClickRefusalChecksAmbiguityFirst(t *testing.T) {
	p := probeOf(
		ClickCandidate{Tag: "button", AriaLabel: "Back"},
		ClickCandidate{Tag: "button", AriaLabel: "Continue", Disabled: true},
	)
	err := strictClickRefusal("button.choice", p)
	if err == nil {
		t.Fatal("既歧义又禁用，却没拒绝")
	}
	if !strings.Contains(err.Error(), "匹配 2 个元素") {
		t.Errorf("先报的应当是歧义（寻址不成立），实际: %v", err)
	}
}

// TestClickProbeChosenIsNilSafe 钉 Chosen 的越界/空表行为：
// cmd/click.go 拿它渲染人话那一行，nil 与越界都不许 panic。
func TestClickProbeChosenIsNilSafe(t *testing.T) {
	var nilProbe *ClickProbe
	if _, ok := nilProbe.Chosen(); ok {
		t.Error("nil 探测居然给回了一个候选")
	}
	empty := &ClickProbe{MatchCount: 3, MatchIndex: 0}
	if _, ok := empty.Chosen(); ok {
		t.Error("候选表是空的却给回了候选")
	}
	ok := &ClickProbe{MatchCount: 1, MatchIndex: 0, Candidates: []ClickCandidate{{Tag: "button", Text: "Go"}}}
	c, got := ok.Chosen()
	if !got || c.Text != "Go" {
		t.Errorf("正常情形没取到候选: %+v %v", c, got)
	}
}

// TestClickCandidateDescribeIsShortAndIdentifiable 钉那一行描述的**形状**：
// 以标签开头（看得出是什么东西）、带位置（对得上 observe 的 bbox）、
// 且长度有上限（它要进 stderr、进报错）。
func TestClickCandidateDescribeIsShortAndIdentifiable(t *testing.T) {
	c := ClickCandidate{
		Tag: "button", Text: strings.Repeat("x", 200), AriaLabel: strings.Repeat("y", 200),
		BBox: [4]int{160, 78, 24, 24}, Disabled: true,
	}
	got := c.Describe()
	if !strings.HasPrefix(got, "<button>") {
		t.Errorf("描述不以标签开头: %q", got)
	}
	if !strings.Contains(got, "[160,78,24,24]") {
		t.Errorf("描述里没有位置（对不上 observe 的 bbox）: %q", got)
	}
	if !strings.Contains(got, "disabled") {
		t.Errorf("禁用的候选在描述里看不出来: %q", got)
	}
	if n := len([]rune(got)); n > 120 {
		t.Errorf("描述太长（%d 字符）—— 它要进日志与报错: %q", n, got)
	}
}
