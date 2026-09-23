package mcp

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"cdp/internal"

	"github.com/chromedp/cdproto/page"
)

// ---- 工具表 ----

// TestToolsCoverTheSpecList 钉住**工具清单本身**（规格 §4.2 / 计划 Task 2 Step 1）。
//
// 为什么用具名列表而不是 `len(Tools()) == 8`：数量对不上时数量会告诉你
// 「少了/多了」，但不会告诉你少的是哪个；而这个清单是 agent 的能力边界 ——
// 少一个工具，agent 就有一整类动作做不了，而它**不会报错**，只会绕路或瞎猜。
func TestToolsCoverTheSpecList(t *testing.T) {
	want := []string{"observe", "diff", "screenshot", "click", "form", "scroll", "goto", "eval"}

	got := make([]string, 0, len(Tools()))
	for _, tool := range Tools() {
		got = append(got, tool.Name)
	}
	if strings.Join(got, ",") != strings.Join(want, ",") {
		t.Fatalf("工具清单 = %q，期望 %q", got, want)
	}
}

// TestLookupUnknownToolErrors 钉住「未知工具名必须**报错**，不许静默成功」。
//
// 这条不是洁癖：静默成功在 MCP 这层长得像「调用成功了，但什么都没发生」——
// agent 会把它读成「这一步做完了」，然后基于一个从没发生过的动作往下推理。
// 这台机器的既有毛病里，「不报错的错」比「报错的错」贵得多。
func TestLookupUnknownToolErrors(t *testing.T) {
	if _, err := Lookup("obesrve"); err == nil {
		t.Fatal("拼错的工具名没有报错 —— 静默成功会把没发生的动作读成做完了")
	}
	if _, err := Lookup(""); err == nil {
		t.Fatal("空工具名没有报错")
	}
	// 报错要说清楚「有哪些」，否则 agent 只能瞎试（或者放弃）。
	_, err := Lookup("nope")
	if err == nil {
		t.Fatal("unreachable")
	}
	for _, name := range []string{"observe", "diff", "screenshot", "click", "form", "scroll", "goto", "eval"} {
		if !strings.Contains(err.Error(), name) {
			t.Errorf("未知工具的报错里没列出 %q，agent 无从纠正：%v", name, err)
		}
	}
}

// TestObserveSchemaCarriesNoSemanticParameters 是规格 D11 的**机器可打回**形式：
// perceive ≠ cognition —— `observe` 只给感知，语义（`intent` / `importance`
// 这类「这是主 CTA」的判断）由 agent 自己从原始信号推。
//
// 用具名白名单而不是黑名单：黑名单只能挡住今天想到的那几个词，而这条决策的
// 失效方式恰恰是「以后顺手加一个看起来无害的语义字段」（`_trace_to_json` 就是这么长出来的）。
// 加参数时这条测试会红 —— 那是**故意的**，它逼一次显式决定。
func TestObserveSchemaCarriesNoSemanticParameters(t *testing.T) {
	tool, err := Lookup("observe")
	if err != nil {
		t.Fatalf("没有 observe 工具: %v", err)
	}

	// ① 白名单：observe 的参数只能是对准/校验观测的，不能是「你怎么看这一页」。
	want := []string{"expect_url", "frame_id"}
	if got := propNames(t, tool.Schema); !sameSet(got, want) {
		t.Errorf("observe 的参数 = %q，期望恰好 %q\n"+
			"（多出来的那些若是语义参数，就是 D11 禁止的「observe 输出认知」）", got, want)
	}

	// ② 黑名单兜底：连**描述文字**里都不许出现这几个词 —— 一个叫 frame_id
	// 却写着「which frame is the primary CTA」的参数，语义照样漏进来了。
	schema := string(tool.Schema)
	for _, banned := range []string{"intent", "importance", "primary", "semantic", "cognition", "主 CTA", "语义"} {
		if strings.Contains(strings.ToLower(schema), strings.ToLower(banned)) {
			t.Errorf("observe 的 schema 里出现了 %q —— D11：observe 给感知、不给判断", banned)
		}
	}
}

// TestObserveDescriptionSaysPerceptionOnly 钉住「描述里必须写明只给感知」。
//
// 描述是**给模型看的唯一说明书**：schema 只是形状，模型靠这句知道
// 「拿到了这些东西之后，判断得我自己做」。它要是写成了「返回页面上的主要 CTA」，
// 模型就会照着一个并不存在的判断往下走。
func TestObserveDescriptionSaysPerceptionOnly(t *testing.T) {
	tool, err := Lookup("observe")
	if err != nil {
		t.Fatalf("没有 observe 工具: %v", err)
	}
	desc := tool.Description
	if desc == "" {
		t.Fatal("observe 没有描述 —— 模型读不到「这个工具给的是什么」")
	}
	for _, want := range []string{"感知", "不给判断"} {
		if !strings.Contains(desc, want) {
			t.Errorf("observe 的描述里没有 %q（描述 = %q）", want, desc)
		}
	}
}

// TestEveryToolSchemaIsAConsistentObject 检查生成的 schema 自身站得住：
// 是 JSON、是 object、required 里每个名字都在 properties 里、且关掉了 additionalProperties。
//
// 最后一条不是排版偏好：schema 开着 additionalProperties（默认）而校验器又拒未知参数时，
// 模型看到的说明书与真实行为**不一致** —— 它按说明书传参，然后被拒。
func TestEveryToolSchemaIsAConsistentObject(t *testing.T) {
	for _, tool := range Tools() {
		t.Run(tool.Name, func(t *testing.T) {
			if tool.Name == "" {
				t.Fatal("工具有名字为空")
			}
			if tool.Description == "" {
				t.Fatal("工具没有描述 —— 模型读不到它做什么")
			}
			var schema struct {
				Type                 string                     `json:"type"`
				Properties           map[string]json.RawMessage `json:"properties"`
				Required             []string                   `json:"required"`
				AdditionalProperties *bool                      `json:"additionalProperties"`
			}
			if err := json.Unmarshal(tool.Schema, &schema); err != nil {
				t.Fatalf("schema 不是 JSON: %v", err)
			}
			if schema.Type != "object" {
				t.Errorf("schema.type = %q，期望 object（MCP 的 tools/call 只收对象参数）", schema.Type)
			}
			for _, name := range schema.Required {
				if _, ok := schema.Properties[name]; !ok {
					t.Errorf("required 里的 %q 不在 properties 里 —— 模型永远填不出来这个必填项", name)
				}
			}
			if schema.AdditionalProperties == nil || *schema.AdditionalProperties {
				t.Error("schema 没有关掉 additionalProperties —— 说明书与「拒未知参数」的校验器不一致")
			}
			// 每个参数都得说清楚是什么，否则模型只能靠名字猜（而名字很短）。
			for name, raw := range schema.Properties {
				var prop struct {
					Type        string `json:"type"`
					Description string `json:"description"`
				}
				if err := json.Unmarshal(raw, &prop); err != nil {
					t.Errorf("参数 %s 的 schema 坏了: %v", name, err)
					continue
				}
				if prop.Type == "" {
					t.Errorf("参数 %s 没有 type", name)
				}
				if prop.Description == "" {
					t.Errorf("参数 %s 没有描述 —— 模型只能猜它是什么", name)
				}
			}
		})
	}
}

// ---- 参数校验 ----

func TestPrepareRejectsMissingRequiredArgument(t *testing.T) {
	_, _, err := Prepare("click", json.RawMessage(`{}`))
	if err == nil {
		t.Fatal("缺 selector 的 click 没报错 —— 校验失败会变成一次没做成的动作")
	}
	if !strings.Contains(err.Error(), "selector") {
		t.Errorf("报错里没说是哪个参数缺了: %v", err)
	}
}

func TestPrepareRejectsWrongType(t *testing.T) {
	// frame_id 是字符串，传数字要当场报错：JSON 里 123 与 "123" 看着像，
	// 但拼进选择器/帧 ID 就是两回事，而顺着走完不会有人发现。
	if _, _, err := Prepare("observe", json.RawMessage(`{"frame_id":123}`)); err == nil {
		t.Error("frame_id 传数字没报错")
	}
	if _, _, err := Prepare("click", json.RawMessage(`{"selector":"#a","track":"yes"}`)); err == nil {
		t.Error("track 传字符串没报错")
	}
}

func TestPrepareRejectsUnknownArgument(t *testing.T) {
	// 拼错的参数名必须报错。静默忽略的后果：agent 以为它传了 frame_id，
	// 实际观测的是主帧 —— 一切正常，只是看错了页。
	_, _, err := Prepare("observe", json.RawMessage(`{"fram_id":"X"}`))
	if err == nil {
		t.Fatal("未知参数 fram_id 被静默忽略了")
	}
	if !strings.Contains(err.Error(), "frame_id") && !strings.Contains(err.Error(), "fram_id") {
		t.Errorf("报错要能让人看出是哪个参数（最好列出合法的那些）: %v", err)
	}
}

func TestPrepareToleratesAbsentArguments(t *testing.T) {
	// MCP 的 arguments 可以整个不带（工具没有参数时客户端常这样发）。
	for _, raw := range []any{nil, json.RawMessage(``), json.RawMessage(`null`), json.RawMessage(`{}`)} {
		if _, _, err := Prepare("screenshot", raw); err != nil {
			t.Errorf("screenshot 无参数被拒了（raw=%v）: %v", raw, err)
		}
	}
}

func TestPrepareDropsNullOptionals(t *testing.T) {
	// 显式传 null 等于没传（Python 侧 `args.get("frame_id")` 会给出 None，
	// 序列化回来就是 null）。它不该被当成「传了个非字符串」而报错。
	_, args, err := Prepare("observe", json.RawMessage(`{"frame_id":null}`))
	if err != nil {
		t.Fatalf("显式 null 的可选参数被拒了: %v", err)
	}
	if v, ok := args["frame_id"]; ok && v != nil {
		t.Errorf("null 的可选参数没有归一掉: %#v", v)
	}
}

// TestFormRequiresExactlyOneOfValueCheckSelect 是 form 的跨参数约束
// （一条参数列表表达不了它，所以它在 Tool.Extra 里）。
//
// 与 CLI 的 validateFormFlags 同一套判据：三选一，且 --check 只能是布尔。
func TestFormRequiresExactlyOneOfValueCheckSelect(t *testing.T) {
	reject := []string{
		`{"selector":"#a"}`,                                       // 一个都没给
		`{"selector":"#a","value":"x","check":true}`,              // 两个
		`{"selector":"#a","value":"x","select":"y"}`,              // 两个
		`{"selector":"#a","value":"x","check":true,"select":"y"}`, // 三个
	}
	for _, raw := range reject {
		if _, _, err := Prepare("form", json.RawMessage(raw)); err == nil {
			t.Errorf("form %s 被放过了（value/check/select 必须恰好给一个）", raw)
		}
	}
	accept := []string{
		`{"selector":"#a","value":"x"}`,
		`{"selector":"#a","check":true}`,
		`{"selector":"#a","check":false}`, // false 是**传了**（取消勾选），不是没传
		`{"selector":"#a","select":"y"}`,
	}
	for _, raw := range accept {
		if _, _, err := Prepare("form", json.RawMessage(raw)); err != nil {
			t.Errorf("form %s 被误拒了: %v", raw, err)
		}
	}
}

func TestPrepareAcceptsRawMessageAndDecodedMap(t *testing.T) {
	// 传输层给过来的可能是 json.RawMessage，也可能是已经解开的 map
	// （不同客户端/SDK 版本不一样）。两者都该走通。
	if _, args, err := Prepare("click", map[string]any{"selector": "#a"}); err != nil {
		t.Errorf("map 形式的参数被拒了: %v", err)
	} else if args["selector"] != "#a" {
		t.Errorf("map 形式的参数没解出来: %#v", args)
	}
}

// ---- 辅助 ----

func propNames(t *testing.T, schema json.RawMessage) []string {
	t.Helper()
	var s struct {
		Properties map[string]json.RawMessage `json:"properties"`
	}
	if err := json.Unmarshal(schema, &s); err != nil {
		t.Fatalf("schema 不是 JSON: %v", err)
	}
	names := make([]string, 0, len(s.Properties))
	for name := range s.Properties {
		names = append(names, name)
	}
	return names
}

func sameSet(got, want []string) bool {
	if len(got) != len(want) {
		return false
	}
	seen := map[string]int{}
	for _, s := range got {
		seen[s]++
	}
	for _, s := range want {
		seen[s]--
	}
	for _, n := range seen {
		if n != 0 {
			return false
		}
	}
	return true
}

// ---- 桩：一个把调用记下来的浏览器（不起任何连接） ----

// stubBrowser 让「工具 → 内核调用」这一步**不用浏览器**就能测。
// 它替换的是 interface，不是内核 —— 映射写错了（比如 check=false 走成 true、
// scroll 忘了解析 iframe）只有这里抓得住：真跑一遍的话，页面多半还是那个页面，
// 什么都看不出来。
type stubBrowser struct {
	calls []string

	model *internal.PageModel
	shot  *internal.Shot
	tree  *page.FrameTree
	err   error

	iframeSelector string
	// landingDiags 是桩攒下的落点判据诊断（form 那条路只有它能说话）
	landingDiags []internal.Diagnostic
	// clickResult 让用例决定 click 回执长什么样（默认给一个普通的成功回执）
	clickResult *internal.ClickResult
	// evalRaw 是桩替 `EvalInFrame` 写回的那段 JSON（原样，连引号一起）
	evalRaw string
}

func (s *stubBrowser) rec(call string) { s.calls = append(s.calls, call) }

func (s *stubBrowser) lastCall() string {
	if len(s.calls) == 0 {
		return "(没有任何调用)"
	}
	return s.calls[len(s.calls)-1]
}

func (s *stubBrowser) ObserveAll() (*internal.PageModel, error) {
	s.rec("ObserveAll()")
	return s.model, s.err
}

func (s *stubBrowser) Observe(frameID string) (*internal.PageModel, error) {
	s.rec("Observe(" + frameID + ")")
	return s.model, s.err
}

func (s *stubBrowser) Screenshot() (*internal.Shot, error) {
	s.rec("Screenshot()")
	return s.shot, s.err
}

func (s *stubBrowser) ClickElementStrict(selector, frameID string, track bool) (*internal.ClickResult, error) {
	s.rec(callf("ClickElementStrict", selector, frameID, track))
	if s.err != nil {
		return nil, s.err
	}
	if s.clickResult != nil {
		return s.clickResult, nil
	}
	return &internal.ClickResult{X: 1, Y: 2, MatchCount: 1, TargetDisabled: false}, nil
}

func (s *stubBrowser) FillText(selector, text, frameID string, track bool) error {
	s.rec(callf("FillText", selector, text, frameID, track))
	return s.err
}

func (s *stubBrowser) CheckElement(selector string, checked bool, frameID string, track bool) error {
	s.rec(callf("CheckElement", selector, checked, frameID, track))
	return s.err
}

func (s *stubBrowser) SelectOption(selector, option, frameID string, track bool) error {
	s.rec(callf("SelectOption", selector, option, frameID, track))
	return s.err
}

func (s *stubBrowser) ScrollToElement(selector string, track bool) error {
	s.rec(callf("ScrollToElement", selector, track))
	return s.err
}

func (s *stubBrowser) ScrollIntoView(selector, frameID string) error {
	s.rec(callf("ScrollIntoView", selector, frameID))
	return s.err
}

// LandingDiags **不进 calls**：它是读一份**回执**，不是一次内核**动作**。
// 上面那条「一次 form 只许打一次内核调用」的断言说的是动作 —— 把读回执混进去
// 会让它变成「一次 form 只许打一次调用 + 一次读」，而那是另一件事
// （放宽断言正是这一轮反复被点名的病）。
func (s *stubBrowser) LandingDiags() []internal.Diagnostic {
	return s.landingDiags
}

func (s *stubBrowser) ResolveIframeSelector(frameID string) (string, error) {
	s.rec(callf("ResolveIframeSelector", frameID))
	return s.iframeSelector, s.err
}

func (s *stubBrowser) Navigate(url, frameID string) (*page.FrameTree, error) {
	s.rec(callf("Navigate", url, frameID))
	return s.tree, s.err
}

// EvalInFrame 是**给系统自己读正文**那一手（2026-09-23 接进 MCP：原先只有 CLI 有，
// agent 的 `dispatch("eval", …)` 一直打在空气上）。桩只把 `evalRaw` 原样写回。
func (s *stubBrowser) EvalInFrame(frameID, js string, result any) error {
	s.rec(callf("EvalInFrame", frameID, js))
	if s.err != nil {
		return s.err
	}
	if p, ok := result.(*json.RawMessage); ok && s.evalRaw != "" {
		*p = json.RawMessage(s.evalRaw)
	}
	return nil
}

func callf(name string, args ...any) string {
	parts := make([]string, 0, len(args))
	for _, a := range args {
		parts = append(parts, toStr(a))
	}
	return name + "(" + strings.Join(parts, ",") + ")"
}

func toStr(v any) string {
	switch x := v.(type) {
	case string:
		return x
	case bool:
		if x {
			return "true"
		}
		return "false"
	}
	return "?"
}

// runHandler 走**和 cmd/mcp 一样的那条路**：先 Prepare（查名字 + 校验参数），
// 再拿桩浏览器执行。测试因此覆盖的是真实入口，而不是绕开校验直接调 handler。
//
// 参数校验失败时**返回那个错误**（不 Fatal）：负例里「参数层就挡掉了」也是一次
// 合格的报错，测试要能把它与「handler 报错」放在一起断言。
func runHandler(t *testing.T, name, rawArgs string, b Browser) (any, error) {
	t.Helper()
	tool, args, err := Prepare(name, json.RawMessage(rawArgs))
	if err != nil {
		return nil, err
	}
	return tool.Handler(context.Background(), b, args)
}
