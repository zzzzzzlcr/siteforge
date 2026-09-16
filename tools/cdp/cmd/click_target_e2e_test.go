package cmd

import (
	"encoding/json"
	"strings"
	"testing"
)

// 端到端：click 的**目标解析**（缺陷 1 歧义选择器 / 缺陷 2 禁用目标）。
//
// 两个缺陷都是真站上量出来的（docs/probes/2026-09-17-observe-blinkist/）：
//
//	缺陷 1：模型要的是 Continue，它的 selector 却**匹配 5 个按钮**，`querySelector`
//	        取到的是文档序第一个 = **Back**。`cdp click` 既不拒绝也不说点到了谁
//	        —— 于是漏斗被**倒着走**。
//	缺陷 2：`observe` 把 disabled 的 Continue 当普通候选递出来，点了 **exit 0**、
//	        回一对像样的坐标，**什么都没发生** —— 一次静默的空点被报成成功。
//
// 本文件钉的是修完之后的契约：
//
//	默认（生产 57 个脚本走的路）：行为一字不改 —— 仍然取第一个、仍然不硬失败，
//	                              但**必须说出来**（stderr 一行人话 + stdout 的 JSON 字段）
//	--strict（agent 那道门默认开）：两种情况都**当场失败**，且拒绝发生在动作**之前**
//
// 夹具是 internal/testdata/click_target.html（A/B/C 三组，先读它的注释）。
// 浏览器是私有的（env(t)：私有端口 + 私有 profile）—— 不与共享的 9222 抢页面。

// ---- 读结果的小工具（结果一律按**契约字段**断言，不按 Go 类型 —— 这样
//      测试在实现之前就能编译、并在该红的地方红） ----

func jsonField(t *testing.T, res map[string]any, key string) any {
	t.Helper()
	v, ok := res[key]
	if !ok {
		t.Fatalf("click 的输出里没有 %q 这个字段（拿到的是 %v）", key, res)
	}
	return v
}

func jsonString(t *testing.T, res map[string]any, key string) string {
	t.Helper()
	v, ok := jsonField(t, res, key).(string)
	if !ok {
		t.Fatalf("%s 不是字符串: %v", key, res[key])
	}
	return v
}

func jsonNumber(t *testing.T, res map[string]any, key string) float64 {
	t.Helper()
	v, ok := jsonField(t, res, key).(float64)
	if !ok {
		t.Fatalf("%s 不是数字: %v", key, res[key])
	}
	return v
}

func jsonBool(t *testing.T, res map[string]any, key string) bool {
	t.Helper()
	v, ok := jsonField(t, res, key).(bool)
	if !ok {
		t.Fatalf("%s 不是布尔: %v", key, res[key])
	}
	return v
}

// clickJSON 跑一次 `cdp click` 并把 stdout 解成 JSON。
func (e *testEnv) clickJSON(t *testing.T, args ...string) (map[string]any, string, int) {
	t.Helper()
	stdout, stderr, code := e.run(t, append([]string{"click"}, args...)...)
	if code != 0 {
		return nil, stderr, code
	}
	var res map[string]any
	if err := json.Unmarshal([]byte(stdout), &res); err != nil {
		t.Fatalf("click 的 stdout 不是一份 JSON: %v\n原文: %q", err, stdout)
	}
	return res, stderr, code
}

// clicked 读页面上的 window.__clicks —— 「**实际**点到的是哪一个」的唯一证据。
//
// 为什么不看返回的坐标：坐标只能说「点在哪」，说不出「点到了谁」。实测里那一下
// 坐标是 (170.77, 81.86)，看起来毫无问题，而它落在的是 **Back**。
func (e *testEnv) clicked(t *testing.T) []string {
	t.Helper()
	// JS 返回**字符串**（JSON.stringify）：EvalInFrame 走的是 Runtime.evaluate，
	// 对象/数组不带 returnByValue 时拿不到值，字符串才稳定拿得到。
	out, errOut, code := e.run(t, "eval", "JSON.stringify(window.__clicks)")
	if code != 0 {
		t.Fatalf("读 window.__clicks 失败（退出码 %d）: %s", code, errOut)
	}
	var raw string
	if err := json.Unmarshal([]byte(strings.TrimSpace(out)), &raw); err != nil {
		t.Fatalf("eval 的输出不是 JSON 字符串: %v\n原文: %q", err, out)
	}
	var names []string
	if err := json.Unmarshal([]byte(raw), &names); err != nil {
		t.Fatalf("__clicks 不是字符串数组: %v\n原文: %q", err, raw)
	}
	return names
}

// clickFixture 把私有浏览器导航到夹具页，并确认「一个都还没点」。
func (e *testEnv) clickFixture(t *testing.T) {
	t.Helper()
	e.navigate(t, e.fixture+"/click_target.html")
	if got := e.clicked(t); len(got) != 0 {
		t.Fatalf("刚导航完就有 %v 条点击记录 —— 起点不干净，后面的断言都不作数", got)
	}
}

// ---- 缺陷 1：歧义选择器 ----

// TestClickAmbiguousSelectorStillFirstMatchButNowSaysSo 钉**默认**那一半：
// 生产脚本的行为一字不改（还是点第一个 = Back，还是 exit 0），但这一次它说话了。
func TestClickAmbiguousSelectorStillFirstMatchButNowSaysSo(t *testing.T) {
	e := env(t)
	e.clickFixture(t)

	res, stderr, code := e.clickJSON(t, "--selector", "button.choice")
	if code != 0 {
		t.Fatalf("默认路径不该失败（57 个生产脚本靠它保持老行为）：退出码 %d\nstderr: %s", code, stderr)
	}

	// ① 老契约不许动：stdout 上仍然是坐标 JSON —— py 侧拿的就是它。
	if _, ok := res["x"]; !ok {
		t.Errorf("落点 x 没了（py 与 agent 都靠它）: %v", res)
	}
	if _, ok := res["y"]; !ok {
		t.Errorf("落点 y 没了: %v", res)
	}

	// ② 新契约：命中几个、用的是第几个、它禁没禁用。
	if got := jsonNumber(t, res, "match_count"); got != 5 {
		t.Errorf("match_count = %v，期望 5（`button.choice` 在夹具里命中 5 个）", got)
	}
	if got := jsonNumber(t, res, "match_index"); got != 0 {
		t.Errorf("match_index = %v，期望 0（默认取文档序第一个）", got)
	}
	if jsonBool(t, res, "target_disabled") {
		t.Error("target_disabled = true，但第一个匹配（Back）是可点的")
	}
	if !strings.Contains(jsonString(t, res, "target"), "Back") {
		t.Errorf("target 里看不出点的是谁（实测那一下点的是 Back）: %q", jsonString(t, res, "target"))
	}

	// ③ 人话那一行在 **stderr** 上（stdout 是给 py 解析的，不许混进人话）。
	for _, want := range []string{"5", "Back"} {
		if !strings.Contains(stderr, want) {
			t.Errorf("stderr 上没有人话那一行（缺 %q）—— 「静默取第一个」正是这个缺陷:\n%s", want, stderr)
		}
	}

	// ④ 复现实测：**真的点了 Back**。这不是我们想要的，但它是默认路径的既有行为，
	//    所以这里如实钉住它 —— 改了它要在这里说话。
	if got := e.clicked(t); len(got) != 1 || got[0] != "Back" {
		t.Errorf("默认路径点的是 %v，期望 [Back]（`querySelector` 取文档序第一个）", got)
	}
}

// TestClickStrictRefusesAmbiguousSelector 钉 **--strict** 那一半（agent 那道门默认走它）。
func TestClickStrictRefusesAmbiguousSelector(t *testing.T) {
	e := env(t)
	e.clickFixture(t)

	stdout, stderr, code := e.run(t, "click", "--selector", "button.choice", "--strict")
	if code == 0 {
		t.Fatalf("歧义选择器在 --strict 下必须失败 —— 它现在会点到 Back（漏斗倒退），"+
			"而 stdout 上说一切正常:\n%s", stdout)
	}

	// 报错要**点名几个**匹配 —— 「歧义」不说数量，调用方改不动。
	if !strings.Contains(stderr, "5") {
		t.Errorf("报错没说是几个匹配: %s", stderr)
	}
	// 还要够它**认得出**这几个候选（否则「报错了」也帮不上忙：
	// 真站上那 5 个按钮的 text 全是空，只有 aria-label 分得开）。
	for _, want := range []string{"Back", "Disagree", "Not sure", "Agree", "Continue"} {
		if !strings.Contains(stderr, want) {
			t.Errorf("报错里没列出候选 %q，调用方无从判断该换哪个选择器:\n%s", want, stderr)
		}
	}

	// 最要紧的一条：拒绝必须发生在**动作之前** —— 页面一个点击记录都不该有。
	if got := e.clicked(t); len(got) != 0 {
		t.Errorf("拒绝了，页面上却留下了点击记录 %v —— 那是「拒绝了个寂寞」", got)
	}
}

// ---- 缺陷 2：禁用目标 ----

// TestClickDisabledTargetSaysSoInsteadOfSilentlySucceeding 钉默认那一半：
// 不硬失败（老行为），但**不许再静默**。
func TestClickDisabledTargetSaysSoInsteadOfSilentlySucceeding(t *testing.T) {
	e := env(t)

	for _, tc := range []struct {
		name     string
		selector string
		why      string
	}{
		{"IDL disabled", "#disabled-go", "`<button disabled>` —— el.disabled === true"},
		{"aria-disabled", "#aria-disabled-go", "`aria-disabled=\"true\"` —— IDL 仍是 false，但站点会忽略点击（MUI 那一套）"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			e.clickFixture(t)

			res, stderr, code := e.clickJSON(t, "--selector", tc.selector)
			if code != 0 {
				t.Fatalf("默认路径不该硬失败（老行为就是 exit 0）: %d\nstderr: %s", code, stderr)
			}
			if !jsonBool(t, res, "target_disabled") {
				t.Errorf("target_disabled = false —— %s 没被认出来。默认路径也**必须报出来**:"+
					"默认不报 = 这正是「静默假成功」那个缺陷", tc.why)
			}
			if got := jsonNumber(t, res, "match_count"); got != 1 {
				t.Errorf("match_count = %v，期望 1（这个选择器是唯一的）", got)
			}
			if !strings.Contains(stderr, "禁用") {
				t.Errorf("stderr 上没说这个目标是禁用的（点了也不会有反应）:\n%s", stderr)
			}
			// 页面没动 —— 这就是那个「静默的空点」，它现在至少是被说出来的。
			if got := e.clicked(t); len(got) != 0 {
				t.Errorf("禁用控件居然被点动了: %v", got)
			}
		})
	}
}

// TestClickStrictRefusesDisabledTarget：--strict 下禁用目标**当场失败**。
func TestClickStrictRefusesDisabledTarget(t *testing.T) {
	e := env(t)

	for _, selector := range []string{"#disabled-go", "#aria-disabled-go"} {
		t.Run(selector, func(t *testing.T) {
			e.clickFixture(t)

			stdout, stderr, code := e.run(t, "click", "--selector", selector, "--strict")
			if code == 0 {
				t.Fatalf("禁用的目标在 --strict 下必须失败（点了什么都不会发生，"+
					"却回 exit 0 + 像样的坐标 —— 这正是最贵的那类失败）:\n%s", stdout)
			}
			if !strings.Contains(stderr, "禁用") {
				t.Errorf("报错没说清是「目标被禁用」: %s", stderr)
			}
			if got := e.clicked(t); len(got) != 0 {
				t.Errorf("拒绝了却还是点了: %v", got)
			}
		})
	}
}

// TestClickStrictStillClicksAUniqueEnabledTarget 是**反向**那一半：
// strict 不许变成「一律拒绝」—— 唯一且可点的目标照点不误。
//
// 没有这一条，「strict 拒绝」与「strict 把所有点击都拒了」在测试里长得一样。
func TestClickStrictStillClicksAUniqueEnabledTarget(t *testing.T) {
	e := env(t)
	e.clickFixture(t)

	res, stderr, code := e.clickJSON(t, "--selector", "#unique-go", "--strict")
	if code != 0 {
		t.Fatalf("唯一且可点的目标被 --strict 拒了（过度拒绝）: %d\nstderr: %s", code, stderr)
	}
	if got := jsonNumber(t, res, "match_count"); got != 1 {
		t.Errorf("match_count = %v，期望 1", got)
	}
	if jsonBool(t, res, "target_disabled") {
		t.Error("可点的按钮被报成禁用")
	}
	if got := e.clicked(t); len(got) != 1 || got[0] != "UniqueGo" {
		t.Errorf("点的是 %v，期望 [UniqueGo]", got)
	}
}

// TestClickDisabledContinueThroughItsOnlyUniqueHandle 是**实测那条链的完整复刻**。
//
// 真站上禁用的是 Continue，而模型给它的 selector 是**歧义的**那个（命中 5 个）。
// 模型里唯一能寻到 Continue 的抓手是 `alternates[0]`（positional 路径，实测唯一）——
// 本测试就顺着 observe 自己给的那条路走：拿 `alternates[0]` 去点。
//
// 它证明的是：**就算调用方规规矩矩用了 observe 给的唯一抓手**，禁用的目标也必须被
// 拦下（而不是回一对坐标、然后什么都没发生）。
func TestClickDisabledContinueThroughItsOnlyUniqueHandle(t *testing.T) {
	e := env(t)
	e.clickFixture(t)

	out, errOut, code := e.observe(t)
	if code != 0 {
		t.Fatalf("observe 失败: %d\n%s", code, errOut)
	}
	var m struct {
		Actions []struct {
			Selector   string   `json:"selector"`
			Alternates []string `json:"alternates"`
			Text       string   `json:"text"`
		} `json:"actions"`
	}
	if err := json.Unmarshal([]byte(out), &m); err != nil {
		t.Fatalf("observe 的输出解不开: %v", err)
	}

	var continueAction *struct {
		Selector   string   `json:"selector"`
		Alternates []string `json:"alternates"`
		Text       string   `json:"text"`
	}
	for i := range m.Actions {
		if m.Actions[i].Text == "Continue" {
			continueAction = &m.Actions[i]
			break
		}
	}
	if continueAction == nil {
		t.Fatalf("observe 的模型里没有 text=\"Continue\" 的动作（夹具坏了？共 %d 个动作）", len(m.Actions))
	}
	if len(continueAction.Alternates) == 0 {
		t.Fatalf("Continue 的 alternates 是空的 —— 模型里就没有唯一的抓手了: %+v", continueAction)
	}
	unique := continueAction.Alternates[0]

	// 用 click --strict 自己来验：它若报「歧义」就说明这个选择器不唯一，
	// 那「唯一抓手」这个前提就不成立（不是本测试要证的事）。
	stdout, stderr, code := e.run(t, "click", "--selector", unique, "--strict")
	if code == 0 {
		// 点成功了 = 唯一 + 可点 —— 那这个夹具就没复现出「唯一的抓手指向禁用目标」。
		t.Fatalf("observe 给的唯一抓手 %q 被点成功了（%s）—— 夹具里 Continue 必须是 disabled 的",
			unique, stdout)
	}
	if strings.Contains(stderr, "匹配") && strings.Contains(stderr, "5") {
		t.Fatalf("observe 给的 alternates[0] = %q 居然是歧义的 —— 「唯一抓手」这个前提不成立", unique)
	}
	if !strings.Contains(stderr, "禁用") {
		t.Errorf("拒绝的理由不是「目标被禁用」而是别的（%q）:\n%s", unique, stderr)
	}
	if got := e.clicked(t); len(got) != 0 {
		t.Errorf("拒绝了却还是点了: %v", got)
	}
}

// ---- observe 那一半：disabled 进模型（additive 字段） ----

// TestObserveReportsDisabledOnActions：`observe` 把禁用状态**说出来**，
// agent 就不用靠「点一下试试」去发现它。
//
// 判据是**两条**（IDL disabled / aria-disabled）—— 只认一条的话，另一条就是
// 「点了没反应但报成功」，而那正是这个缺陷本来的样子。
func TestObserveReportsDisabledOnActions(t *testing.T) {
	e := env(t)
	e.navigate(t, e.fixture+"/click_target.html")

	out, errOut, code := e.observe(t)
	if code != 0 {
		t.Fatalf("observe 失败: %d\n%s", code, errOut)
	}
	var m struct {
		Actions []struct {
			Selector string `json:"selector"`
			Text     string `json:"text"`
			Disabled *bool  `json:"disabled"`
		} `json:"actions"`
	}
	if err := json.Unmarshal([]byte(out), &m); err != nil {
		t.Fatalf("observe 的输出解不开: %v", err)
	}

	disabled := map[string]bool{}
	for _, a := range m.Actions {
		if a.Disabled == nil {
			t.Fatalf("动作 %q 的 disabled 字段**不存在** —— 字段缺失与 disabled=false 在消费侧"+
				"分不开，而那正是「模型里看不出这两种状态」这个缺陷", a.Text)
		}
		disabled[a.Text] = *a.Disabled
	}

	for _, want := range []struct {
		text string
		dis  bool
	}{
		{"Continue", true},       // 缺陷 2 的主角：disabled 属性
		{"DisabledGo", true},     // 唯一 + disabled
		{"AriaDisabledGo", true}, // aria-disabled="true"
		{"UniqueGo", false},      // 反向对照：可点的**不许**被报成禁用
	} {
		got, ok := disabled[want.text]
		if !ok {
			t.Errorf("模型里没有 text=%q 的动作（拿到的是 %v）", want.text, disabled)
			continue
		}
		if got != want.dis {
			t.Errorf("%q 的 disabled = %v，期望 %v", want.text, got, want.dis)
		}
	}

	// 人话那一页（给人看的摘要）也不许把「禁用」吞掉 —— 蜜罐那次就是栽在
	// 「JSON 里有、人话里只字未提」（cmd/observe.go 的 renderHuman 注释）。
	human, _, code := e.run(t, "observe", "--json=false")
	if code != 0 {
		t.Fatalf("observe --json=false 失败: %d", code)
	}
	if !strings.Contains(human, "禁用") {
		t.Errorf("人话摘要里看不出有元素是禁用的（运营看不出「AI 正准备点一个死按钮」）:\n%s", human)
	}
}

// TestClickTargetDescriptionIsCapped 是夹具/契约的守卫：描述里不放整段页面文本。
//
// 它挡住的是「把元素的 textContent 原样塞进 stderr」那种实现 —— 那既会把日志冲散，
// 也会让真站上「按钮文字里带 error:」这类内容被下游的 `_ok()` 判成命令报错。
func TestClickTargetDescriptionIsCapped(t *testing.T) {
	e := env(t)
	e.clickFixture(t)

	res, _, code := e.clickJSON(t, "--selector", "button.choice")
	if code != 0 {
		t.Skipf("默认路径就失败了（另有专测）")
	}
	desc := jsonString(t, res, "target")
	if len([]rune(desc)) > 120 {
		t.Errorf("target 描述太长（%d 字符）—— 它要能进日志、进报错，得短: %q", len([]rune(desc)), desc)
	}
	if !strings.HasPrefix(desc, "<") {
		t.Errorf("target 描述不以标签开头，读不出「点的是什么」: %q", desc)
	}
}
