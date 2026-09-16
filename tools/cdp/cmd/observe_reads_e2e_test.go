package cmd

import (
	"encoding/json"
	"slices"
	"strings"
	"testing"
	"time"

	"cdp/internal"
)

// 观测的**三样读法**（value / selected / aria_label）在真页面上的行为闸门。
//
// 为什么是这三样（2026-09-17 真站验收，gowizard 的 auto-warranty 漏斗）：
// agent 卡住不是因为想错，是因为**看不见**。人在旁边看得见、它看不见的三件事：
//
//	① 这一题现在选的是哪个   → `selected`（它答了 Sedan，模型里一个字都没有，
//	                          于是它没法确认自己答过，反复重答 —— 用户看到的「呆呆的」）
//	② 这个框现在装着什么     → `value`（实测：填值前后两份模型**逐字节相同**）
//	③ 这个没有文字的控件叫什么 → `aria_label`（blinkist 上 5 个按钮 text 全是空串，
//	                          只有 aria-label 分得开 Back / Disagree / Not sure / Agree）
//
// 夹具 `internal/testdata/observe_reads.html` 复刻的就是这三种形态（真站 DOM 真值见
// `docs/probes/2026-09-17-observe-blinkist/dom-ground-truth.txt`）。
//
// 浏览器是**私有**的（env(t)：私有端口 + 私有 profile），与 internal 那批集成测试
// 不抢同一个页面 —— 理由见 observe_e2e_test.go 文件头那次实测（3/3 观测到对方的夹具）。

// observeReads 导航到三样读法的夹具页并观测一次，返回**原始 stdout** 与解好的模型。
//
// 两份都要：原始 JSON 用来查「键在不在」（键缺失与值是 null 在消费侧分不开，
// 这正是 disabled 那次栽的形状），模型用来断言语义。
func observeReads(t *testing.T) (string, *internal.PageModel) {
	t.Helper()
	e := env(t)
	fixtureURL := e.fixture + "/observe_reads.html"
	e.navigate(t, fixtureURL)
	// navi 等的是 load；夹具是纯静态 HTML，给一点余量让首帧布局稳定（bbox 依赖它）。
	time.Sleep(300 * time.Millisecond)

	out, errOut, code := e.observe(t)
	if code != 0 {
		t.Fatalf("observe 退出码 = %d, want 0\nstderr: %s", code, errOut)
	}
	if !json.Valid([]byte(out)) {
		t.Fatalf("stdout 不是合法 JSON:\n%.400s", out)
	}
	m := decodeModel(t, out)
	if m.URL != fixtureURL {
		t.Fatalf("模型 url = %q，want %q —— 观测的不是这条测试导航过去的页（后面的断言会空转）", m.URL, fixtureURL)
	}
	return out, m
}

// rawObjects 把模型里某个列表的**原始 JSON 对象**取出来（按 selector 找一条）。
//
// 为什么要绕到 map[string]json.RawMessage：反序列化进 struct 时**缺键是零值**，
// 断言「值为 false/null」会被「键根本没绑上」蒙混过去。
func rawObjects(t *testing.T, out, list string) []map[string]json.RawMessage {
	t.Helper()
	var doc map[string]json.RawMessage
	if err := json.Unmarshal([]byte(out), &doc); err != nil {
		t.Fatalf("模型解不成 JSON 对象: %v\n%.400s", err, out)
	}
	rawList, ok := doc[list]
	if !ok {
		t.Fatalf("模型里没有 %q 这个键（py 与 agent 读的就是它）:\n%.400s", list, out)
	}
	var objs []map[string]json.RawMessage
	if err := json.Unmarshal(rawList, &objs); err != nil {
		t.Fatalf("解析原始 JSON 的 %s 失败: %v\n%.400s", list, err, rawList)
	}
	return objs
}

// rawBySelector 在原始对象里按 selector 找一条，返回它的键值表。
func rawBySelector(t *testing.T, objs []map[string]json.RawMessage, sel string, seen []string) map[string]json.RawMessage {
	t.Helper()
	for _, o := range objs {
		var s string
		if err := json.Unmarshal(o["selector"], &s); err == nil && s == sel {
			return o
		}
		seen = append(seen, string(o["selector"]))
	}
	t.Fatalf("原始 JSON 里没有 selector=%q 的元素（后面的断言会空转）。实际: %v", sel, seen)
	return nil
}

// actionByID / fieldByID 按 id 找一条（`#id` 是 candidates() 的**首选**候选，但允许它
// 落在 alternates 里 —— 断言的是「这个元素在模型里、且带着这三样读法」，不是候选排序）。
func actionByID(t *testing.T, m *internal.PageModel, id string) internal.Action {
	t.Helper()
	for _, a := range m.Actions {
		if a.Selector == "#"+id || slices.Contains(a.Alternates, "#"+id) {
			return a
		}
	}
	t.Fatalf("模型里没有 #%s —— 找不到它，后面的断言会空转。实际 selector: %v", id, selectors(m.Actions))
	return internal.Action{}
}

func fieldByID(t *testing.T, m *internal.PageModel, id string) internal.Field {
	t.Helper()
	for _, f := range m.Fields {
		if f.Selector == "#"+id || slices.Contains(f.Alternates, "#"+id) {
			return f
		}
	}
	got := make([]string, 0, len(m.Fields))
	for _, f := range m.Fields {
		got = append(got, f.Selector)
	}
	t.Fatalf("模型里没有字段 #%s —— 后面的断言会空转。实际: %v", id, got)
	return internal.Field{}
}

// selectedDesc 把三态印成人话（断言失败时要看得出「是 false 还是 null」——
// 这两者分不开正是要根治的那个缺陷）。
func selectedDesc(v *bool) string {
	if v == nil {
		return "null（看不出）"
	}
	if *v {
		return "true"
	}
	return "false"
}

func valueDesc(v *string) string {
	if v == nil {
		return "null（不是值控件）"
	}
	return `"` + *v + `"`
}

// ────────────────────────── ① 键必须在（additive 的第一道闸） ──────────────────────────

// TestObserveCarriesTheThreeReadsOnActionsAndFields 钉住：**每一条**动作与字段都带
// 这三个键（actions 与 fields 是两条独立的收集路，绑定也是各写一遍 —— 只做一条，
// 另一条会静默地什么都没有）。
//
// 判据用**原始 JSON 的键**，不是结构体字段：缺键解进 struct 是零值，
// 「键没绑上」与「值恰好是空」在消费侧长得一模一样（disabled 那次的原话）。
func TestObserveCarriesTheThreeReadsOnActionsAndFields(t *testing.T) {
	out, m := observeReads(t)
	if len(m.Actions) == 0 || len(m.Fields) == 0 {
		t.Fatalf("夹具页取到 actions=%d fields=%d —— 断言会空转", len(m.Actions), len(m.Fields))
	}
	wantKeys := []string{"value", "value_truncated", "selected", "aria_label"}
	for _, list := range []string{"actions", "fields"} {
		objs := rawObjects(t, out, list)
		for i, o := range objs {
			var sel string
			_ = json.Unmarshal(o["selector"], &sel)
			for _, k := range wantKeys {
				if _, ok := o[k]; !ok {
					t.Errorf("%s[%d]（%s）缺 %q 这个键 —— py/agent 侧「键不存在」与「值是空/null」"+
						"分不开，而那正是这三样读法要治的病", list, i, sel, k)
				}
			}
		}
	}
}

// TestObserveSelectedIsThreeStateNotNull —— 「看不出」必须与「没选」分开。
//
// 夹具的 #opt-nostate 是一个**没有任何状态标记**的自定义控件（真站上合法：
// 自定义控件的可选态由站点 JS 管，ARIA 标记只是有时才加）。它的 selected 必须是
// **null**，而 #opt-suv 的 false 必须还是 false —— 两者一旦合并，
// agent 就会把「看不出」读成「没选」，然后**再答一遍**（gowizard 那次就是这么卡的）。
func TestObserveSelectedIsThreeStateNotNull(t *testing.T) {
	out, m := observeReads(t)

	// ① JSON 层：null 就是字面量 null（不是 false、也不是缺键）
	objs := rawObjects(t, out, "actions")
	obj := rawBySelector(t, objs, "#opt-nostate", nil)
	if got := strings.TrimSpace(string(obj["selected"])); got != "null" {
		t.Errorf("#opt-nostate 的 selected 在 JSON 里是 %s，want null —— "+
			"「没有标记」被编成了布尔值，消费侧就再也分不出「看不出」与「没选」", got)
	}

	// ② 语义层：三格各是 true / false / null
	on := actionByID(t, m, "opt-sedan")     // aria-checked="true"
	off := actionByID(t, m, "opt-suv")      // aria-checked="false"
	none := actionByID(t, m, "opt-nostate") // 一个标记都没有
	if on.Selected == nil || !*on.Selected {
		t.Errorf("#opt-sedan 的 selected = %s，want true（它带 aria-checked=\"true\" —— "+
			"这正是「我答的是 Sedan」那个答案）", selectedDesc(on.Selected))
	}
	if off.Selected == nil || *off.Selected {
		t.Errorf("#opt-suv 的 selected = %s，want false（aria-checked=\"false\"）", selectedDesc(off.Selected))
	}
	if none.Selected != nil {
		t.Errorf("#opt-nostate 的 selected = %s，want null —— 它没有任何状态标记，"+
			"编一个 false 就是**猜**（本项目已经裁定过：class 派生的标记不算证据，同一套谨慎适用于此）",
			selectedDesc(none.Selected))
	}
	// 三态各自印一遍：这条测试失败时，看的就是它们三个
	t.Logf("三态：#opt-sedan=%s、#opt-suv=%s、#opt-nostate=%s",
		selectedDesc(on.Selected), selectedDesc(off.Selected), selectedDesc(none.Selected))

	// ③ aria-pressed 那一套（按钮式选项，blinkist 用它）
	if got := actionByID(t, m, "press-full"); got.Selected == nil || !*got.Selected {
		t.Errorf("#press-full 的 selected = %s，want true（aria-pressed=\"true\"）", selectedDesc(got.Selected))
	}
	if got := actionByID(t, m, "press-min"); got.Selected == nil || *got.Selected {
		t.Errorf("#press-min 的 selected = %s，want false（aria-pressed=\"false\"）", selectedDesc(got.Selected))
	}
}

// TestObserveSelectedOnNativeControls —— 原生控件的选中态走 IDL，不走 ARIA。
//
// 三格各承重：
//
//	<select> 选好了        → true（selectedIndex >= 0），且 value 是**选中那个**（2020）
//	<select multiple> 无选项 → false（selectedIndex === -1）—— 「一个都没选」是真的 false，
//	                          与「看不出」的 null 不是一回事
//	checkbox / radio        → checked
//	纯文本输入框            → null（它没有「选中」这回事 —— 不许编 false）
func TestObserveSelectedOnNativeControls(t *testing.T) {
	_, m := observeReads(t)
	for _, c := range []struct {
		id   string
		want *bool
		why  string
	}{
		{"read-year", boolPtr(true), `<select> 里有一个 selected 的选项`},
		{"read-none", boolPtr(false), `<select multiple> 没有选项：selectedIndex === -1`},
		{"read-check-on", boolPtr(true), `checkbox 有 checked`},
		{"read-check-off", boolPtr(false), `checkbox 没有 checked`},
		{"read-radio-on", boolPtr(true), `radio 有 checked`},
		{"read-radio-off", boolPtr(false), `radio 没有 checked`},
		{"read-email", nil, `纯文本输入框没有「选中」这回事 —— 只能给 null，不能给 false`},
	} {
		got := actionByID(t, m, c.id)
		if !sameSelected(got.Selected, c.want) {
			t.Errorf("#%s 的 selected = %s，want %s（%s）", c.id, selectedDesc(got.Selected), selectedDesc(c.want), c.why)
		}
	}
}

// ────────────────────────── ② 值：读回来、截断、并说出来 ──────────────────────────

// TestObserveReadsBackWhatIsInTheField —— 「值填进去了没有」的那个答案。
//
// 实测过的缺陷形状：填值**前**与**后**两份模型**逐字节相同** —— 于是 agent 无法
// 确认自己的写入生效，只能再填一遍（与「看不见选中态」是同一类病）。
func TestObserveReadsBackWhatIsInTheField(t *testing.T) {
	_, m := observeReads(t)

	// ① 已填的文本框：值就是页面上的值（actions 与 fields 两条路都要在）
	const wantEmail = "driver@example.com"
	if got := actionByID(t, m, "read-email").Value; got == nil || *got != wantEmail {
		t.Errorf("动作 #read-email 的 value = %s，want %q —— 填进去的值读不回来，"+
			"agent 就无法确认自己的写入生效", valueDesc(got), wantEmail)
	}
	if got := fieldByID(t, m, "read-email").Value; got == nil || *got != wantEmail {
		t.Errorf("字段 #read-email 的 value = %s，want %q —— fields 那条路一个字都没读", valueDesc(got), wantEmail)
	}

	// ② textarea 同理（它最容易很长，也最容易被漏掉）
	const wantNotes = "换车，工作日白天在家"
	if got := actionByID(t, m, "read-notes").Value; got == nil || *got != wantNotes {
		t.Errorf("textarea #read-notes 的 value = %s，want %q", valueDesc(got), wantNotes)
	}

	// ③ <select> 给的是**选中那个**的值（2020），不是第一个（Choose… 的 ""）
	//    —— 这一格与 selected 合起来才是「我答过了、答的是哪个」
	if got := actionByID(t, m, "read-year").Value; got == nil || *got != "2020" {
		t.Errorf("<select> #read-year 的 value = %s，want \"2020\"（选中的那个选项，"+
			"不是文档序第一个占位项）", valueDesc(got))
	}

	// ④ 不是值控件的元素：null。**不是**空串 —— 空串说的是「这个框现在是空的」，
	//    那是个关于页面的断言；按钮上没有值这件事不该被说成「它的值是空的」。
	if got := actionByID(t, m, "read-continue").Value; got != nil {
		t.Errorf("按钮 #read-continue 的 value = %s，want null（它不是值控件）—— "+
			"编一个 \"\" 等于**替页面断言**「这里是空的」", valueDesc(got))
	}
}

// TestObserveValueIsCappedAndSaysSo —— 值有上限，且**截了就要说出来**。
//
// 为什么必须有上限：值由页面作者写（一个 textarea 可以几十 KB），
// 而每条动作/字段都带一个值 —— 不封顶的话一次观测能把模型撑成几兆。
// 为什么必须说出来：截断是**静默丢东西**的经典形态，本项目对它的成套先例是
// 「丢之前先记一笔」（蜜罐进 honeypots、空列表编 [] 而不是 null）。
func TestObserveValueIsCappedAndSaysSo(t *testing.T) {
	out, m := observeReads(t)
	const full = "1HGCM82633A004352-ABCDEFGHIJKLMNOPQRSTUVWXYZ-0123456789-abcdefghijklmnopqrstuvwxyz-ABCDEFGHIJKLMNOPQRSTUVWXYZ-0123456789-tail"

	got := actionByID(t, m, "read-long").Value
	if got == nil {
		t.Fatalf("#read-long 的 value = null —— 一个装了 %d 个字符的输入框读不到值", len(full))
	}
	if len(*got) >= len(full) {
		t.Errorf("长值没有被截断（%d 个字符）—— 上限没生效，一个 textarea 就能把模型撑大", len(*got))
	}
	if !strings.HasPrefix(full, *got) {
		t.Errorf("截断后的值不是原文的前缀: %q —— 截断必须是从尾部砍，不能被改写成别的东西", *got)
	}
	if len(*got) == 0 {
		t.Error("截断后成了空串 —— 那与「这个框是空的」分不开")
	}
	if !actionByID(t, m, "read-long").ValueTruncated {
		t.Errorf("#read-long 的 value_truncated = false，而它的值被截到了 %d 个字符（原文 %d）—— "+
			"截断不说的后果是消费侧把半个值当成全部", len(*got), len(full))
	}
	// 反向对照：没被截的值不许报 true（否则那个布尔字段就是个恒真的噪音）
	if actionByID(t, m, "read-email").ValueTruncated {
		t.Error("#read-email 的值只有 18 个字符，value_truncated 却是 true —— 那个字段会失去意义")
	}
	// 上限那个数在脚本里，只此一份（见 internal/observe_reads_test.go 的读法）
	objs := rawObjects(t, out, "actions")
	if obj := rawBySelector(t, objs, "#read-long", nil); string(obj["value_truncated"]) != "true" {
		t.Errorf("原始 JSON 里 #read-long 的 value_truncated = %s，want true", obj["value_truncated"])
	}
}

// ────────────────────────── ③ 没有文字的控件要有名字 ──────────────────────────

// TestObserveNamesTextlessControlsWithAriaLabel —— 真站上 Back 与三个图标选项
// 的 `text` **全是空串**，只有 aria-label 分得开。模型里没有它，
// 那几个控件就是**彼此完全同形**的匿名行（人的原话：能猜，但不是「看得见」）。
func TestObserveNamesTextlessControlsWithAriaLabel(t *testing.T) {
	_, m := observeReads(t)

	back := actionByID(t, m, "read-back")
	if strings.TrimSpace(back.Text) != "" {
		t.Errorf("#read-back 的 text = %q，want 空串 —— 夹具里它一个字都没有（真站形态）；"+
			"夹具变了的话这条断言就不再测「没有文字」那一格", back.Text)
	}
	if back.AriaLabel != "Back" {
		t.Errorf("#read-back 的 aria_label = %q, want \"Back\" —— 这是这个控件**唯一**的名字，"+
			"没有它模型里就是一行无名的按钮", back.AriaLabel)
	}

	// 反向对照：有文字的按钮不许被 aria-label 顶掉（text 仍是主名字）
	cont := actionByID(t, m, "read-continue")
	if cont.Text != "Continue" {
		t.Errorf("#read-continue 的 text = %q，want \"Continue\"", cont.Text)
	}
	if cont.AriaLabel != "" {
		t.Errorf("#read-continue 的 aria_label = %q，want 空串（它没有 aria-label）", cont.AriaLabel)
	}

	// 字段那条路：没有可显示 <label> 的输入框靠 aria-label 认得出来
	if got := fieldByID(t, m, "read-long").AriaLabel; got != "VIN (17 characters)" {
		t.Errorf("字段 #read-long 的 aria_label = %q，want \"VIN (17 characters)\" —— "+
			"fields 那条路也得把无障碍名交出来", got)
	}
}

// ────────────────────────── 小工具 ──────────────────────────

func boolPtr(v bool) *bool { return &v }

// sameSelected 比三态（nil 与 false 必须分得开 —— 这正是这个字段存在的理由）。
func sameSelected(got, want *bool) bool {
	if got == nil || want == nil {
		return got == nil && want == nil
	}
	return *got == *want
}
