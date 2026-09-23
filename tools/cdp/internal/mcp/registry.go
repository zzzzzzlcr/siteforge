// Package mcp 是 MCP 那道门的**工具表与参数校验**层（计划二 Task 2、规格 §4.1/§4.2）。
//
// 为什么单独一层（计划二 File Structure）：**工具注册与参数校验**和**传输**（stdio）
// 是两件事。分开之后，「哪些工具存在、参数怎么校验」可以单测 —— 不用起 MCP 连接，
// 也不用起浏览器。cmd/mcp 只负责把这一层挂到传输上。
//
// ⚠️ 这里**不 shell out 去调 `cdp` CLI**。同内核两个门（§4.1）的意义就在这里：
// 包一层壳去 subprocess CLI 会重新引入 Go 日志噪声（stdio 上那是致命的）、
// 双重转义（CLI 的 JSON → 字符串 → 再套一层 JSON）和每条命令一次进程开销+超时。
// 所以 Handler 收的是一个 Browser —— 由 cmd/mcp 传 `*internal.Client` 进来。
package mcp

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"cdp/internal"

	"github.com/chromedp/cdproto/page"
)

// Browser 是内核里**这道门用得到的那一面**（internal.Client 的一个子集）。
//
// 收成 interface 只有一个目的：让「工具 → 内核调用」的映射能在**没有浏览器**的
// 情况下测。真跑一遍是测不出映射错的 —— check=false 走成 true、scroll 忘了解析
// iframe，页面还是那个页面，只是什么都没发生。
type Browser interface {
	ObserveAll() (*internal.PageModel, error)
	Observe(frameID string) (*internal.PageModel, error)
	Screenshot() (*internal.Shot, error)
	// ClickElementStrict 而不是 ClickElement：这道门要的是**严格**那一版。
	//
	// 为什么在这道门上换成严格（2026-09-17 真站实测，规格 §4.1）：
	// agent 是那个**必须被逼着说准**的调用方 —— 它的宽松选择器会静默点到 Back
	// （漏斗倒退）、它的禁用目标会静默空点（回 exit 0 + 像样的坐标）。
	// 生产脚本继续走 CLI 的宽松默认路径，两边各要各的，互不影响。
	ClickElementStrict(selector, frameID string, track bool) (*internal.ClickResult, error)
	FillText(selector, text, frameID string, track bool) error
	CheckElement(selector string, checked bool, frameID string, track bool) error
	SelectOption(selector, option, frameID string, track bool) error
	ScrollToElement(selector string, track bool) error
	ScrollIntoView(selector, frameID string) error
	ResolveIframeSelector(frameID string) (string, error)
	Navigate(url, frameID string) (*page.FrameTree, error)
	// EvalInFrame 在一帧里跑一段 JS，结果解成 JSON 递回来（空 frameID = 主帧）。
	//
	// ★ 2026-09-23：这一手是**给系统自己读正文用的**（`browser_agent._final_success_check`
	// 拿它读整页正文）。在这之前它只有 CLI 有（`cmd/eval.go`）、MCP 这张表里没有 ⇒
	// agent 每一次调用都被那侧的 `except` 悄悄吞掉，判据实际只看到 `observe` 那一段
	// （真站上「明明成功却报没成功」的一半原因就在这儿）。**「接上了但不响」比没接更坏。**
	EvalInFrame(frameID string, js string, result any) error
	// LandingDiags 是**落点判据**（click 的 G1）攒下的诊断：抬起被扣下、
	// 或者判据在这一点上根本跑不了（跨站子帧）。
	//
	// ⚠️ 为什么这道门非有不可（2026-09-17 复审实测）：`click` 走的是返回的
	// `*ClickResult`（那几个字段本来就在里面），但 `form` 这条路上原先
	// **一个字节都传不出来** —— agent 拿到的是「填好了」，看不到「这一次点击
	// 只发出了按下的那一半」。**agent 用的就是这道门，不是 CLI。**
	LandingDiags() []internal.Diagnostic
}

// Handler 执行一次工具调用。b 是**这次调用**要打的那个浏览器（由 cmd/mcp 现连现给，
// 不烘进工具表里）—— 工具表本身因此不带任何连接状态。
type Handler func(ctx context.Context, b Browser, args map[string]any) (any, error)

// Param 声明一个参数。它同时是**校验依据**与 **JSON Schema 的来源**：
// 两者由同一份声明生成，就不存在「说明书说能传、校验器却拒」这种不一致。
type Param struct {
	Name        string
	Type        string // "string" / "boolean" / "number" / "object" / "array"
	Description string
	Required    bool
}

// Tool 是一个工具。Schema 由 newTool 从 Params 生成，**不要手写**：
// 手写的 schema 迟早与校验器分家，而分家的表现是「模型照说明书传参却被拒」。
type Tool struct {
	Name        string
	Description string
	Schema      json.RawMessage
	Params      []Param
	// Extra 承担「一条参数列表表达不了」的约束（form 的三选一）。
	// 它在按参数校验**之后**跑。
	Extra   func(args map[string]any) error
	Handler Handler
}

// Tools 返回这门上的全部工具（规格 §4.2）。
//
// ⚠️ 返回的是包级切片本身，调用方**只读**。
func Tools() []Tool { return toolTable }

// Lookup 按名字找工具。
//
// 找不到就**报错**，且报错里列出全部合法名字：未知工具名静默成功的话，
// agent 会把一个从没发生过的动作读成「这一步做完了」，然后基于它继续推理 ——
// 这台机器上「不报错的错」比「报错的错」贵得多。
func Lookup(name string) (*Tool, error) {
	for i := range toolTable {
		if toolTable[i].Name == name {
			return &toolTable[i], nil
		}
	}
	names := make([]string, 0, len(toolTable))
	for _, t := range toolTable {
		names = append(names, t.Name)
	}
	return nil, fmt.Errorf("没有 %q 这个工具。这台门上的工具有：%s", name, strings.Join(names, "、"))
}

// Prepare 是 cmd/mcp 唯一的入口：查名字 → 解参数 → 校验。
//
// 三步都在这儿（而不是散在 main 里）是有意的：参数校验的失败路径要能被单测，
// 而 main 里那一坨是测不到的。
func Prepare(name string, rawArgs any) (*Tool, map[string]any, error) {
	tool, err := Lookup(name)
	if err != nil {
		return nil, nil, err
	}
	args, err := decodeArgs(rawArgs)
	if err != nil {
		return nil, nil, fmt.Errorf("%s 的参数解不开：%w", name, err)
	}
	if err := tool.ValidateArgs(args); err != nil {
		return nil, nil, err
	}
	return tool, args, nil
}

// ValidateArgs 按 Params 校验一次调用的参数。
//
// 三条都查：未知参数名、必填缺失、类型不对。**未知参数名也报错**同样是刻意的 ——
// agent 把 frame_id 写成 fram_id，静默忽略的后果是「它以为看的是那一帧，
// 实际看的是主帧」：一次看不出错的观测错误。
func (t *Tool) ValidateArgs(args map[string]any) error {
	known := make(map[string]Param, len(t.Params))
	for _, p := range t.Params {
		known[p.Name] = p
	}

	if len(args) > 0 {
		unknown := make([]string, 0)
		for name := range args {
			if _, ok := known[name]; !ok {
				unknown = append(unknown, name)
			}
		}
		if len(unknown) > 0 {
			sortStrings(unknown)
			return fmt.Errorf("%s 没有 %s 这个参数；它的参数是：%s"+
				"（拼错的参数名不会报「没传」，只会安静地按默认值走）",
				t.Name, quoteList(unknown), strings.Join(paramNames(t.Params), "、"))
		}
	}

	for _, p := range t.Params {
		v, ok := args[p.Name]
		// 显式传 null 等于没传（Python 侧 args.get(x) 回来的 None 序列化就是 null）。
		if !ok || v == nil {
			if p.Required {
				return fmt.Errorf("%s 缺少必填参数 %s（%s）", t.Name, quoteList([]string{p.Name}), p.Description)
			}
			continue
		}
		if err := checkType(t.Name, p, v); err != nil {
			return err
		}
	}

	if t.Extra != nil {
		return t.Extra(args)
	}
	return nil
}

func checkType(tool string, p Param, v any) error {
	bad := func(want string) error {
		return fmt.Errorf("%s 的参数 %s 类型不对：期望 %s，拿到的是 %T（%v）", tool, quoteList([]string{p.Name}), want, v, v)
	}
	switch p.Type {
	case "string":
		if _, ok := v.(string); !ok {
			return bad("字符串")
		}
	case "boolean":
		if _, ok := v.(bool); !ok {
			return bad("布尔值 true/false")
		}
	case "number":
		switch v.(type) {
		case float64, int, int64, json.Number:
		default:
			return bad("数字")
		}
	case "object":
		if _, ok := v.(map[string]any); !ok {
			return bad("对象")
		}
	case "array":
		if _, ok := v.([]any); !ok {
			return bad("数组")
		}
	default:
		return fmt.Errorf("%s 的参数 %s 声明了未知类型 %q（这是工具表自己的 bug）", tool, p.Name, p.Type)
	}
	return nil
}

// decodeArgs 把传输层递过来的参数归一成 map[string]any。
//
// 为什么要吃三种形态：ToolHandler 拿到的是 json.RawMessage（SDK 文档明说），
// 但不同客户端/SDK 版本递过来的可能是已经解好的 map，或者是 nil（工具没有参数时
// 客户端常整个不带）。**三种都得走通** —— 在参数形态上挑食的后果是
// 「某些客户端调什么都失败」，而那种失败看起来像工具坏了。
func decodeArgs(raw any) (map[string]any, error) {
	switch v := raw.(type) {
	case nil:
		return map[string]any{}, nil
	case json.RawMessage:
		return decodeArgsBytes(v)
	case []byte:
		return decodeArgsBytes(v)
	case map[string]any:
		if v == nil {
			return map[string]any{}, nil
		}
		return v, nil
	case string:
		return decodeArgsBytes([]byte(v))
	}
	return nil, fmt.Errorf("不认识的参数形态 %T（期望 JSON 对象）", raw)
}

func decodeArgsBytes(b []byte) (map[string]any, error) {
	if len(bytes.TrimSpace(b)) == 0 {
		return map[string]any{}, nil
	}
	var m map[string]any
	if err := json.Unmarshal(b, &m); err != nil {
		return nil, fmt.Errorf("不是 JSON 对象：%w", err)
	}
	if m == nil { // 字面量 "null"
		return map[string]any{}, nil
	}
	return m, nil
}

// newTool 组装一个工具，并把 Schema 从 Params 生成出来。
func newTool(name, description string, params []Param, extra func(map[string]any) error, h Handler) Tool {
	return Tool{
		Name:        name,
		Description: description,
		Schema:      schemaFor(params),
		Params:      params,
		Extra:       extra,
		Handler:     h,
	}
}

// schemaFor 把参数声明编成 JSON Schema（draft 2020-12，MCP 用的那个）。
//
// additionalProperties 显式关掉：校验器本来就拒未知参数（见 ValidateArgs），
// schema 要跟它说同一件事 —— 否则模型照着说明书传参，然后被拒。
func schemaFor(params []Param) json.RawMessage {
	props := map[string]any{}
	required := make([]string, 0, len(params))
	for _, p := range params {
		props[p.Name] = map[string]any{
			"type":        p.Type,
			"description": p.Description,
		}
		if p.Required {
			required = append(required, p.Name)
		}
	}
	s := map[string]any{
		"type":                 "object",
		"properties":           props,
		"additionalProperties": false,
	}
	// required 只有非空时才写：空数组与不写是同一个意思，但空数组会让
	// 「这个工具有必填项吗」这句话在模型眼里多一次误读。
	if len(required) > 0 {
		s["required"] = required
	}
	b, err := json.Marshal(s)
	if err != nil {
		// 参数表是编译期常量，编不出来是工具表自己的 bug，不该等到线上才发现。
		panic(fmt.Sprintf("生成 %v 的 schema 失败: %v", params, err))
	}
	return b
}

func paramNames(params []Param) []string {
	names := make([]string, 0, len(params))
	for _, p := range params {
		names = append(names, p.Name)
	}
	return names
}

// quoteList 用 %q 逐个引起来（中文/空格/下划线在裸 %v 里挤成一片，读不出来）。
func quoteList(names []string) string {
	quoted := make([]string, 0, len(names))
	for _, n := range names {
		quoted = append(quoted, fmt.Sprintf("%q", n))
	}
	return strings.Join(quoted, "、")
}

func sortStrings(s []string) {
	for i := 1; i < len(s); i++ {
		for j := i; j > 0 && s[j] < s[j-1]; j-- {
			s[j], s[j-1] = s[j-1], s[j]
		}
	}
}

// ---- 取值小工具（参数已通过校验，所以这里不做类型判断） ----

func strArg(args map[string]any, name string) string {
	if v, ok := args[name].(string); ok {
		return v
	}
	return ""
}

func boolArg(args map[string]any, name string) bool {
	v, _ := args[name].(bool)
	return v
}

// ---- 工具表（规格 §4.2） ----

var toolTable = []Tool{
	newTool("observe",
		"观察页面，返回结构化页面模型：可动作元素（含选择器候选与稳定性评级）、"+
			"表单字段、选项组、遮挡物、诊断。跨源 iframe 会自动逐帧观测再合并，每条动作带 frame_path。\n"+
			"每条动作/字段还**回读**三样：value（这个框现在装着什么）、selected（这个控件选着没有）、"+
			"aria_label（没有文字的控件叫什么）。**做完动作之后看它们**，就能确认自己那一下生效了没有 —— "+
			"不用靠再点一次试。两个三态字段的读法：value 的 null = 不是值控件（按钮），\"\" = 是值控件但现在是空的；"+
			"selected 的 null = **看不出**（控件没暴露状态），**不等于** false（没选）—— "+
			"把 null 读成没选会让你把同一道题再答一遍。\n"+
			"这是**感知**：它只报页面上有什么、在哪、多大、被谁挡着、选择器可以怎么写、"+
			"控件现在是什么状态，**不给判断** —— 「哪个是主 CTA」「这一步该点哪里」这类语义要你自己从这些原始信号推。"+
			"（把判断烘进观测里，等于又写死一套规则，那正是这套系统要逃出来的坑。）",
		[]Param{
			{
				Name: "frame_id", Type: "string",
				Description: "只观察这一帧。收的是 CDP frameID，不是 frame_path 里给人读的 \"main\"。" +
					"不给就观察整页（含子帧，自动逐帧观测再合并）。",
			},
			{
				Name: "expect_url", Type: "string",
				Description: "预期页面 URL 里含有的子串。模型的 url 不含它就**报错**、不返回模型 —— " +
					"拿错页时别往下推理（新开的标签页、还没跳转完的 SPA 都会这样）。",
			},
		}, nil, handleObserve),

	newTool("diff",
		"差分：把「上一次 observe 的结果」与**现在的**页面比一遍，回答一个问题 —— "+
			"刚才那一下有没有推进。判据是 URL、可见正文、元素身份集合（对选择器改名免疫）。\n"+
			"⚠️ 它不判填写与勾选：它的判据里没有字段值这一项，所以「值填进去了没有」这类步骤，"+
			"diff 原理上答不了（页面组成没变就是 false）。别把它读成「这一步失败了」。"+
			"（要判「我填/选的那一下生效了没有」用 observe：它回读 value / selected。）",
		[]Param{
			{
				Name: "before", Type: "object", Required: true,
				Description: "动作前那一份 observe 的结果，**原样**传回来（整个对象，不要自己裁字段）—— " +
					"只言片语的 {} 也能解析成一份全零模型，那会把整页元素误报成「新出现」。",
			},
			{
				Name: "frame_id", Type: "string",
				Description: "只观测「动作后」那一帧。⚠️ 配整页的 before 用会把其它帧的元素全报成 disappeared " +
					"并给出**假**的 actionable=true，非调试别传。",
			},
		}, nil, handleDiff),

	newTool("screenshot",
		"截当前视口，返回 PNG 的 base64 与**坐标证据**（image_px / viewport_css_px / dpr）。"+
			"三者是契约：image_px = viewport_css_px × dpr，screen_px = css_px × dpr。"+
			"拿它可以把 observe 报的 bbox（视口 CSS 像素）叠到图上。\n"+
			"这是**按需**的目视检查：DOM 分不出时再用（同名按钮、SPA、shadow/iframe 里），"+
			"不是主视角 —— 主视角是 observe。⚠️ 图只对当次观测有效：页面一滚，bbox 就与它对不上了。",
		nil, nil, handleScreenshot),

	newTool("click",
		"拟人点击一个元素（穿透 shadow DOM 与跨源 iframe）。返回点击**落点**的视口 CSS 坐标、"+
			"以及这一下**实际点到的是谁**（match_count / match_index / target / target_disabled）。\n"+
			"⚠️ 落点坐标是「到底点在哪」的唯一证据：元素在屏幕外或被子元素盖住时，"+
			"点击会被浏览器丢在别处，而这里返回的坐标就是那个「别处」。\n"+
			"⚠️ **这道门是严格的**：选择器命中多个元素（歧义）或目标被禁用（disabled / "+
			"aria-disabled），一律**报错并拒绝点击**，不会静默点第一个、也不会静默空点。"+
			"报错里会列出命中的候选，照它把选择器收窄到只命中一个再点（observe 给的 "+
			"alternates 里那条 nth-of-type 路径是唯一的）。",
		[]Param{
			{Name: "selector", Type: "string", Required: true, Description: "CSS 选择器（用 observe 给的候选，优先 stability=high 的）。⚠️ 必须**唯一**命中目标：命中多个会被拒绝。"},
			{Name: "frame_id", Type: "string", Description: "元素所在帧的 CDP frameID。跨源 iframe 里的元素必须给，否则够不着。"},
			{Name: "track", Type: "boolean", Description: "在页面上画出鼠标/滚动轨迹（调试用，默认 false）。"},
		}, nil, handleClick),

	newTool("form",
		"填值 / 勾选 / 选下拉，全部走拟人手势（真鼠标键盘事件，不是合成事件）。"+
			"value 还能自动识别 MUI 式日期选择器（打开日历、翻月、点那一天）；"+
			"select 同时支持原生 <select> 与自定义下拉。\n"+
			"value / check / select **三选一**，恰好给一个。",
		[]Param{
			{Name: "selector", Type: "string", Required: true, Description: "目标控件的 CSS 选择器。"},
			{Name: "value", Type: "string", Description: "要填的文本；日期可给 YYYY-MM-DD / MM-DD-YYYY / MM-DD。"},
			{Name: "check", Type: "boolean", Description: "true = 勾上，false = 取消勾选（false 是**明确取消**，不是没传）。"},
			{Name: "select", Type: "string", Description: "要选的选项（值或可见文本，原生下拉与自定义下拉都认）。"},
			{Name: "frame_id", Type: "string", Description: "控件所在帧的 CDP frameID。"},
			{Name: "track", Type: "boolean", Description: "画出鼠标轨迹（调试用，默认 false）。"},
		}, formMode, handleForm),

	newTool("scroll",
		"把元素滚进视口（拟人滚动，穿透 shadow DOM 与跨源 iframe）。\n"+
			"元素在 fold 之下时，先滚它再点/填 —— 不滚就点，点击会落在屏幕外。",
		[]Param{
			{Name: "selector", Type: "string", Required: true, Description: "要滚进来的元素的 CSS 选择器。"},
			{Name: "frame_id", Type: "string", Description: "元素所在帧的 CDP frameID。跨源 iframe 里的元素必须给。"},
			{Name: "track", Type: "boolean", Description: "画出滚动轨迹（调试用，默认 false）。"},
		}, nil, handleScroll),

	newTool("goto",
		"导航到指定 URL（当前标签页）。返回落地页的 URL 与主帧 ID。\n"+
			"⚠️ 导航完**别猜**页面长什么样：接着调一次 observe 看它到底停在哪一页。"+
			"（很多站会重定向到地区页/语言页，URL 与你给的那个不一样。）",
		[]Param{
			{Name: "url", Type: "string", Required: true, Description: "目标 URL（带 scheme，如 https://example.com/）。"},
			{Name: "frame_id", Type: "string", Description: "要导航的帧；不给就导航主帧（常规用法）。"},
		}, nil, handleGoto),

	newTool("eval",
		"在某一帧里执行一段 JavaScript，把结果原样返回。\n"+
			"**这是给系统自己读正文用的**（收尾那一眼读整页文字），不是主视角 —— 主视角是 observe。\n"+
			"⚠️ 它**不做只读校验**：给什么跑什么。调用方自己保证只拿它读（`document.body.innerText` 那类），\n"+
			"别在这里改页面 —— 改了就没有第二个人知道你改过。",
		[]Param{
			{Name: "code", Type: "string", Required: true, Description: "要执行的 JS 源码。"},
			{Name: "frame_id", Type: "string", Description: "哪一帧（CDP frameID）。不给就主帧。"},
		}, nil, handleEval),
}

// formMode 是 form 的跨参数约束：value / check / select 恰好给一个。
//
// 与 CLI 的 validateFormFlags 同一套判据。为什么要在这里管：三个都给了的时候，
// 内核是按顺序分派的（value 优先），于是「勾选」这个动作**静默地没发生** ——
// 调用方以为勾上了，页面说没有。
func formMode(args map[string]any) error {
	given := make([]string, 0, 3)
	for _, name := range []string{"value", "check", "select"} {
		if v, ok := args[name]; ok && v != nil {
			given = append(given, name)
		}
	}
	switch {
	case len(given) == 0:
		return errors.New("form 要恰好给一个：\"value\"（填值）/ \"check\"（勾选）/ \"select\"（选下拉），现在一个都没给")
	case len(given) > 1:
		return fmt.Errorf("form 的 value / check / select 只能给一个，现在给了 %s —— 多给的那几个会被**静默忽略**（内核按 value > check > select 分派）",
			strings.Join(given, "、"))
	}
	return nil
}
