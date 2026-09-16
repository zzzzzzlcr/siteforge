package internal

import (
	"encoding/json"
	"fmt"
)

// PageModel 是规格 §4.3 的 observe 契约。
// 只装**感知**：原始事实。语义判断（intent / importance）由 agent 做（规格 D11）。
type PageModel struct {
	URL         string `json:"url"`
	Title       string `json:"title"`
	PageText    string `json:"page_text"`
	ShadowRoots int    `json:"shadow_roots"`
	// ViewportCssPx 是**观测那一刻的视口**，CSS 像素 —— `bbox` 就在这个空间里。
	//
	// 为什么要有它（Task 1 spike 实测，2026-09-17 计划 §4.1）：模型问「那个折叠线
	// 下面的元素」时**需要视口高**，模型里没有 → **白烧一轮**去找（又一次 observe，
	// 打的是真浏览器真页面），没找到，只能用 `above_fold` 反推出 `528 < vh < 592`
	// 的区间并把「这是推的」说出来。视口尺寸本身就是**感知**（规格 D11），却被漏了。
	//
	// ⚠️ 名字与坐标**必须**与 cmd/screenshot.go 的 `--json` 一致：那边报
	// `viewport_css_px`（CSS 像素）+ `image_px`（设备像素）+ `dpr`，关系是
	// `image_px = viewport_css_px × dpr`。同一个量在一个工具里有两个名字，正是本项目
	// 反复栽的那一类（DPR 那次就是字段名写错被静默忽略）。
	//
	// ⚠️ 取值**必须**是 `window.innerWidth / innerHeight`，**不是** `documentElement`
	// 的 clientWidth / clientHeight —— 后者不含滚动条。screenshot.go 文件头实测过：
	// Chrome 把滚动条画进截图，而 innerWidth 含滚动条。用 clientWidth 会让 observe
	// 比 screenshot 少一个滚动条宽，**静默错开**（症状是「叠不准」，不是报错）。
	//
	// ⚠️ 合并模式（ObserveAll）报的是**主帧**的视口（见 mergeFrameModel）——「这个 tab
	// 现在多大」与 URL / Title 同一个道理，既不求和也不让子帧盖掉；单帧 Observe 报的是
	// **那一帧**的视口，因为它的 bbox / above_fold 就是拿那一帧的 window 算的。
	//
	// 0×0 是**如实的感知**（隐藏/未渲染的帧就是这样），不当错误处理：D11 只给原始事实。
	ViewportCssPx PixelSize     `json:"viewport_css_px"`
	Actions       []Action      `json:"actions"`
	Fields        []Field       `json:"fields"`
	OptionGroups  []OptionGroup `json:"option_groups"`
	Obstructions  []Obstruction `json:"obstructions"`
	// Honeypots 是**已被排除**的元素：它们**不在** actions / fields 里，
	// 单列在这里（规格 R19b，判据见 Honeypot）。
	//
	// ⚠️ 为什么要单独记一笔，而不是静静地丢掉：丢干净之后，消费者**分不清**
	// 「这一页本来就没有这些字段」和「有字段，但被判成陷阱丢掉了」——
	// 而这两件事的下一步动作完全相反（前者要换策略，后者照常往下走）。
	// 本项目对「静默丢东西」有成套的先例（帧取不到记 diagnostic、空列表一律编 `[]`
	// 而不是 `null`），这条是同一族。
	Honeypots []Honeypot `json:"honeypots"`
	// Diagnostics 是**观测者自己的问题**，与「页面上的东西」分开：
	//   - Obstructions = 页面上真有个 cookie 横幅挡着（有 selector / dismiss_selector 语义）
	//   - Diagnostics  = 这一次观测本身不完整（某帧没取到、帧枚举可能退化了）
	// 混用会让消费者拿错东西（把 frameId 当选择器去点）。kind 用导出的常量。
	Diagnostics []Diagnostic `json:"diagnostics"`
}

// normalizeNilLists 把**空列表**从 `null` 掰成 `[]`，并返回同一个模型。
//
// 为什么必须统一（2026-09-16 Task 6 修复轮 1，控制器裁定）：
// 同一份 PageModel 里，「空列表」原本有**两种形状** ——
//
//	JS 来的（actions/fields/option_groups/obstructions 解出来是 []) + alternates /
//	nearby_text / frame_path   → `[]`
//	Go 侧构造的（diagnostics，以及合并后没有元素的那些切片）→ nil → **`null`**
//
// py 侧 `for d in model["diagnostics"]` 会 TypeError —— 而这是**运行阶段唯一的接口**，
// 且正好在最需要它的时候炸：selector 全挂 → 重新 observe → 读 diagnostics。
//
// 两条路都要走一遍：Observe（单帧）与 ObserveAll（合并；空列表在那里**恰恰是常态** ——
// `append(nil)` 一个元素都没加就还是 nil，实测 base.html 的 option_groups 就是这样变 null 的）。
func normalizeNilLists(m *PageModel) *PageModel {
	if m == nil {
		return nil
	}
	if m.Actions == nil {
		m.Actions = []Action{}
	}
	if m.Fields == nil {
		m.Fields = []Field{}
	}
	if m.OptionGroups == nil {
		m.OptionGroups = []OptionGroup{}
	}
	if m.Obstructions == nil {
		m.Obstructions = []Obstruction{}
	}
	if m.Honeypots == nil {
		m.Honeypots = []Honeypot{}
	}
	if m.Diagnostics == nil {
		m.Diagnostics = []Diagnostic{}
	}
	return m
}

// framePathFor 是**单帧**观测的 frame_path 取值（规格 §4.3，2026-09-16 修复轮 2 定的契约）：
//
//	Observe(frameID)  →  [frameID]        （frameID 为空 → ["main"]）
//	ObserveAll()      →  ["main", <childId>, …]   从根到叶（由 mergeFrameModel 写）
//
// 为什么单帧给的不是完整路径：**单帧不知道祖先链** —— 它手上只有一个 frameID。
// 而消费者要的恰恰是「这条动作该发给哪一帧」，`[frameID]` 就是那个答案。
// 需要完整路径的人用 ObserveAll。两种模式语义不同，但都自洽、都够消费者用。
//
// ⚠️ 不要去改 observeJS 里那个写死的 `frame_path: ['main']`：那是 **JS 侧的默认值**，
// 合并那条路（mergeFrameModel 会覆盖 FramePath）依赖它保持现状；动 JS 会连带改合并的行为。
// 这里在 Go 侧覆盖它，影响面只有「单帧」这一条路。
func framePathFor(frameID string) []string {
	if frameID == "" {
		return []string{mainFramePath}
	}
	return []string{frameID}
}

// normalizeFramePaths 把单帧模型的每条动作/字段盖成 framePathFor(frameID)。
//
// 覆盖的是 observeJS 写死的 `['main']` —— 不覆盖的话，**子帧**里观测出来的元素
// 会被标成主帧（py 照 frame_path 选帧就会把点击发到主帧：静默点错，比报错更难查）。
func normalizeFramePaths(m *PageModel, frameID string) *PageModel {
	if m == nil {
		return nil
	}
	path := framePathFor(frameID)
	for i := range m.Actions {
		m.Actions[i].FramePath = path
	}
	for i := range m.Fields {
		m.Fields[i].FramePath = path
	}
	return m
}

// Diagnostic 是一条「这里我没看清」的记录。
//
// FramePath 指向出问题的那一帧（主帧是 ["main"]），agent/Console 顺着能查是哪一帧。
type Diagnostic struct {
	Kind      string   `json:"kind"`
	Detail    string   `json:"detail"`
	FramePath []string `json:"frame_path"`
}

type Action struct {
	Selector   string   `json:"selector"`
	Alternates []string `json:"alternates"`
	Stability  string   `json:"stability"`
	Text       string   `json:"text"`
	// AriaLabel 是这个控件的**无障碍名**（元素的 aria-label 属性）。
	//
	// 为什么非有不可（2026-09-17 真站实测，gowizard 验收）：漏斗上那几个真按钮的
	// `text` **全是空串** —— Back / Disagree / Not sure / Agree 只有 aria-label 分得开。
	// 只给 text 的模型里，它们是几行**彼此完全同形**的匿名元素（连 Back 也一样），
	// 而认清「哪个是 Back」正是「别把漏斗走回去」的那条判据。
	//
	// ⚠️ 与 `text` 的关系：两个都要给，**不许**拿一个顶替另一个 ——
	// `text` 是页面上印着的字，`aria-label` 是给读屏软件念的名；有文字的按钮照样
	// 可以有 aria-label（那时两者说的往往不是同一句话）。
	//
	// ⚠️ 长度：与 Value 同一个上限（见 observeJS 的 READ_CAP）。上限**不单独报**，
	// 与 text / nearby_text 的历史口径一致（那两个也是静默截断）；值那边有
	// ValueTruncated，是因为「值是不是完整的」影响消费侧判断，名字长度不影响。
	AriaLabel string `json:"aria_label"`
	// Label 是这个控件的**人话名字** —— 页面上某个 `<label>` 元素给它的名字。
	//
	// 为什么非有不可（2026-09-17 真站实测，gowizard 的 MUI 问卷）：三个「点开再选」的
	// combobox 显示文本分别是 `2025` / 空 / 空 —— **没有标签就没有语义**，模型分不出
	// 哪个是 Make。而名字就在页面上：`label[for="173851"]` 的文本是 `Make`，它的
	// `for` 指着的那个 id 是那个 combobox 的**第 6 层祖先**（hop6）。旧实现只找
	// 「自己的 id / closest(label) / label[for=自己的 id]」—— 三条路一条都够不着，
	// 于是三个控件在模型里长得一模一样，agent 只能靠猜。
	//
	// 取法（observeJS 的 labelOf，按优先级）：
	//   ① 自己的 id 被某个 label[for] 指着     → 用那个 label 的文本
	//   ② 向上找祖先（**没有跳数上限**，爬到「有 id 且被 label[for] 指着」的第一个，
	//      或者爬到 frame 根）                 → 用它
	//   ③ 都没有 → 前一个 LABEL 兄弟（也逐层往上找：MUI 的 InputLabel 是
	//      `MuiInputBase-root` 那一层的 previousElementSibling，不是控件自己的兄弟）
	//   ④ 都没有 → **null**
	//
	// ⚠️ **null 与空串**：只会是 null 或一个非空的名字，**不会**是空串 ——
	// 文本为空的 label 不算名字（那与「这个控件有标签」是两件事），一律归 null。
	// 与 Field.Label（string，空串 = 没有）不同口径是有意的：Field 那个是历史契约
	// （diff.go 的身份键、既有测试都吃着它），这里按本文件的三态规矩来 ——
	// 「找不到」要能被机器分辨，而不是给一个看起来像答案的空串。
	//
	// ⚠️ 与 AriaLabel 的关系：两个都给，**不许**拿一个顶替另一个 ——
	// Label 是**页面上印着的**名字（label 元素），AriaLabel 是元素自己声明的
	// 无障碍名（aria-label 属性）。真站上 Back 那类控件只有后者；MUI 那类控件
	// 只有前者。哪个可信、先用哪个，是消费侧的事（D11：这里只给感知）。
	Label   *string `json:"label"`
	Role    string  `json:"role"`
	Tag     string  `json:"tag"`
	Type    string  `json:"type"`
	Visible bool    `json:"visible"`
	// Value 是这个元素**现在装着的值**（input / textarea / select 的 IDL value）。
	//
	// 为什么非有不可：实测过——同一个页面，把值填进去**前**与**后**两份模型
	// **逐字节相同**。于是 agent 无法确认自己的写入生效，只能再填一遍；
	// 与「看不见选中态」是同一类病（2026-09-17 gowizard 验收，用户的原话是「呆呆的」）。
	//
	// ⚠️ 三态，别把它读成「有没有值」：
	//
	//	null   这个元素**不是值控件**（按钮 / 链接 / 自定义控件）—— 它没有值这回事
	//	""     是值控件，而它**现在是空的**（这是一句关于页面的断言：写入没落地）
	//
	// 编一个 "" 给按钮，等于替页面断言「这里是空的」；而消费侧真去读它时，
	// 得到的是一句**看起来像答案的假话**。反过来，把 "" 说成 null 会让
	// 「填了但没进去」与「这根本不是个框」混掉 —— 两边都不能省。
	//
	// ⚠️ 值来自页面作者：可能极长（textarea 几十 KB）。JS 侧封顶（见 ValueTruncated），
	// 不封顶的话一次观测（最多 200 动作 + 100 字段）能被撑成几兆。
	Value *string `json:"value"`
	// ValueTruncated 说 Value 被上限截过（截断**必须**说出来，见 READ_CAP 的注释）。
	//
	// ⚠️ 只是「被截过」这一个事实，不报「截到哪」：上限是观察者的口径，
	// 不是页面的属性 —— 消费者要的是「我看到的不是全部」，不是那个数。
	ValueTruncated bool `json:"value_truncated"`
	// Selected 是这个控件**现在是不是选中/勾起/按下**。
	//
	// 为什么非有不可（2026-09-17 真站实测）：agent 答了「Sedan」（页面上确实选上了，
	// 连 URL 里都带着），而模型里**没有一个字**说这件事 —— 于是它无法确认自己的答案
	// 生效，反复重答。人在旁边一眼就能看见，它看不见。
	//
	// ⚠️ **三态，缺一不可**：
	//
	//	true   选着
	//	false  没选（有明确的「没选」证据）
	//	null   **看不出** —— 这个控件没有暴露任何状态，观察者无从判断
	//
	// false 与 null 必须分得开：把「看不出」编成 false，消费侧就会读成「还没选」，
	// 然后**再答一遍** —— 那正是这个缺陷本来的样子换了个位置。
	//
	// 判据（缺一不可，顺序固定）：
	//
	//	① aria-selected / aria-checked / aria-pressed 取值为 true/false —— 自定义
	//	   控件的状态就写在这儿（`mixed` 这类非布尔取值**不算**答案，继续往下问）
	//	② 原生状态走 IDL —— select 看 selectedIndex >= 0、radio/checkbox 看 checked
	//	③ 都没有 → null
	//
	// ⚠️ 刻意**不**认 class（`Mui-selected` / `is-active` 那一类）：与 Disabled 同一条
	// 裁定 —— class 是站点自己起的名字，认它等于把「什么样算选中」交给页面，
	// 而误判方向是**谎报**（把没选的说成选上了）。宁可给 null。
	//
	// ⚠️ 已知的粗一格：`<select>` 的判据是「有选中的选项」（selectedIndex >= 0），
	// 而占位项（`<option value="">Choose…</option>`）也算「选中」—— 所以「答过了没」
	// 要看 Value（它对占位项是 ""），别只看 Selected。
	Selected *bool `json:"selected"`
	// Disabled 是这个元素**现在能不能点**（2026-09-17 真站实测补的，规格 §4.1 U3）。
	//
	// 为什么非有不可：同一个页面、同一个 URL，DOM 里 Continue 的 disabled 从
	// true 翻成 false，而 observe 给它的整条 Action **逐字节相同** —— 模型里没有
	// 任何字段能分辨这两种状态。后果链实测过：禁用的按钮被当普通候选递出来，
	// 点了 exit 0、回一对像样的坐标、**什么都没发生** —— 一次静默的空点被报成成功，
	// 正是本项目最贵的那类失败（它看起来跟「做成了」一模一样）。
	//
	// 判据两条，缺一不可（与 internal/click.go 的探测脚本同一套）：
	//
	//	disabled 属性  走 IDL —— button / input / select / textarea / fieldset
	//	aria-disabled  ARIA 那一套，IDL 仍是 false，但站点的 JS 会忽略点击
	//	               （MUI / Bootstrap 的自定义控件大量用它）
	//
	// ⚠️ 刻意**不**认 class（`Mui-disabled` / `is-disabled` 那一类）：class 是
	// 站点自己起的名字，认它等于把「什么样算禁用」交给页面 —— 误判的方向是
	// **谎报**（把能点的说成不能点），而这里宁可少报。
	//
	// ⚠️ additive 字段：消费侧（py / agent）不读它时行为一字不变。
	Disabled     bool     `json:"disabled"`
	OccludedBy   *string  `json:"occluded_by"`
	ShadowDepth  int      `json:"shadow_depth"`
	FramePath    []string `json:"frame_path"`
	BBox         [4]int   `json:"bbox"`
	Region       string   `json:"region"`
	AboveFold    bool     `json:"above_fold"`
	RelativeSize float64  `json:"relative_size"`
	PeerCount    int      `json:"peer_count"`
	ZIndex       string   `json:"z_index"`
	Contrast     string   `json:"contrast"`
	NearbyText   []string `json:"nearby_text"`
}

type Field struct {
	Selector   string   `json:"selector"`
	Alternates []string `json:"alternates"`
	Stability  string   `json:"stability"`
	Label      string   `json:"label"`
	// AriaLabel 与 Action 的同名字段**同义同判据**（元素的 aria-label 属性，见那边）。
	//
	// ⚠️ 它与 Label 不是一回事，两个都给：Label 是**标签取法的结果**（aria-label
	// → closest('label') → label[for=id] 三级回落，见 observeJS），可能是包着它的
	// label 文字；AriaLabel 只报元素自己那个属性。要判断「这个框叫什么」用 Label，
	// 要拿无障碍名本身（比如与 click 那行描述对齐）用 AriaLabel。
	AriaLabel string `json:"aria_label"`
	// Value / Selected 与 Action 的同名字段**同义同判据**（三态、上限、为什么
	// 不能猜 class 全写在那边）：字段这一路也要回读，因为「值填进去了没有」
	// 这个问题的**主战场就是表单字段**。
	Value          *string  `json:"value"`
	ValueTruncated bool     `json:"value_truncated"`
	Selected       *bool    `json:"selected"`
	Hint           string   `json:"hint"`
	Placeholder    string   `json:"placeholder"`
	Type           string   `json:"type"`
	Required       bool     `json:"required"`
	ShadowDepth    int      `json:"shadow_depth"`
	FramePath      []string `json:"frame_path"`
}

type OptionGroup struct {
	Scope string `json:"scope"`
	// ScopeUnique 说 Scope 这条地址**验证过唯一**没有（在这一帧里命中恰好 1 个、且就是它）。
	//
	// 为什么这三个列表（option_groups / obstructions / honeypots）要有它，
	// 而 actions / fields 那边由 `stability` 承载：
	//   actions / fields 有**成套的评级口径**（stability：high/medium/low，判据挂在
	//   observe.go 的 stability() 上），那里「验证过没有」是评级的一部分；
	//   这三个列表**没有** stability —— 可它们的地址同样会进消费者手里：遮挡物的
	//   selector / dismiss_selector 是要**照着去点**的（去关掉那个横幅），选项组的
	//   scope 是要照着去点的。在这里新编一套评级，就是「同一个量两个名字」
	//   （本仓反复栽的那一类：DPR 那次、screenshot 那次），所以只回答那个唯一的问题：
	//   **这条地址验证过没有**。
	//
	// ⚠️ false **不等于**「不要给」：交不出唯一地址时（元素在 shadow root 里、
	// 而光 DOM 里有同形结构 —— CSS 选择器跨不过 shadow 边界）**照样交**，
	// 只是如实标出来。直接丢掉会让消费侧**更瞎**（横幅还在那儿挡着，而模型里
	// 一个字都没有）。消费侧拿 false 当「可能指错」用：点下去会落在文档序第一个身上。
	ScopeUnique bool     `json:"scope_unique"`
	Role        string   `json:"role"`
	Options     []string `json:"options"`
	ShadowDepth int      `json:"shadow_depth"`
}

type Obstruction struct {
	Kind     string `json:"kind"`
	Selector string `json:"selector"`
	// SelectorUnique 与 OptionGroup.ScopeUnique **同义同判据**（见那边的长注释：
	// 为什么这三个列表用布尔、而 actions/fields 用 stability；false 为什么照样要交）。
	SelectorUnique  bool `json:"selector_unique"`
	DismissSelector string `json:"dismiss_selector"`
	// DismissSelectorUnique 说 DismissSelector 那条地址验证过唯一没有。
	//
	// ⚠️ 三态（与 Value / Selected 同一条规矩）：**没有关闭按钮时是 null**，
	// 不是 false —— 「这条地址没验证过」与「根本没有这条地址」是两件事，
	// 编一个 false 等于替页面断言「有个关闭按钮，但它的选择器不唯一」。
	DismissSelectorUnique *bool  `json:"dismiss_selector_unique"`
	Text                  string `json:"text"`
}

// Honeypot 是一条**被判为陷阱、已从 actions / fields 里排除**的元素。
//
// 真站实测（2026-09-16，check.compareinsulation.io 的漏斗页）：注册表单里埋着
//
//	<input name="company_url" type="text" style="…left:-9983px…">
//
// 人看不见它；bot 填了就被站点标记成机器人。而 `observe` 当时把它**同时**列进
// actions 与 fields，两边都评 `stability: high` —— 等于把陷阱当成高置信度的
// 主路径递给 agent。旧系统早有「蜜罐跳过」（auto-farm-skill `db792c1`），
// 这条按**位置**判据把它恢复过来。
//
// 判据（唯一的一条，在 observeJS 的 trapWhy 里）：
//
//	docLeft = rect.left + scrollX ;  docTop = rect.top + scrollY
//	unreachable = (docLeft + rect.width <= 0) || (docTop + rect.height <= 0)
//
// 整个盒子落在**文档坐标的负区** —— 滚也滚不到，人永远碰不着。
// ⚠️ 必须是**文档**坐标而不是视口坐标：rect 是视口相对的，普通元素只要页面滚过
// 就会出现负的 rect.left（实测：滚到 (600,600) 时，文档坐标 120 的按钮给出
// rect.left = -480）。拿视口坐标判 = 把所有「滚上去看不见」的正常内容一起丢掉。
//
// ⚠️ 名字（company_url / website / fax …）**不是**判据：名字会腐烂，而它腐烂的方向
// 是**放行**（陷阱又回到 actions 里，没人会说话）。名字只作为线索记进 Hint。
type Honeypot struct {
	Selector string `json:"selector"`
	// SelectorUnique 与 OptionGroup.ScopeUnique **同义同判据**（见那边的长注释：
	// 为什么这三个列表用布尔、而 actions/fields 用 stability；false 为什么照样要交）。
	// 陷阱的地址不拿去点（它就是**不该碰**的那个东西），但人/agent 会照着它去核对
	// 「被排除的到底是哪一个」—— 指错了同样是在说假话。
	SelectorUnique bool `json:"selector_unique"`
	// Hint 是元素自报的 name / id / placeholder —— **只是线索，不是判据**。
	// 给人和 agent 一眼看出「站点觉得这是个什么字段」，别拿它做分支。
	Hint string `json:"hint"`
	// Why 是判据名：`off-document-left` / `off-document-top`（哪条轴）。
	//
	// 用**判据名**而不是自由文本，理由与 Obstruction.Kind 同族：消费者要能按它分支。
	// ⚠️ 刻意**不**复用 `occluded_by` 的 `"offscreen"` —— 规格 R21 已经裁定那个值
	// 「一个值扛三种含义」（折线下 / 蜜罐 / 视口太矮），别再往那个方向加。
	Why string `json:"why"`
}

// observeJS 返回单帧页面模型的求值脚本。
//
// 穿透走内核助手 __cdpQA / __cdpRoots（由 withPierce 注入），不自实现遍历 ——
// 那正是 shadow.go 注释里说的「不必各自重写一套」。R3 探针里的自实现 RS/qsa
// 换成内核助手，算法其余部分逐段照搬（探针已在四档页面取齐）。
//
// ⚠️ EvalInFrame **不注入穿透**（它直接 runtime.Evaluate），所以这里必须自己套
// withPierce；也正因如此，这个脚本**不能用 `cdp eval` 调试** —— 那条路没有注入。
//
// ⚠️ R3 探针实测的三个**静默出错**的坑（都已在下面钉住，见 observe_traps_test.go）：
//  1. ShadowRoot 上**没有** innerText（那是 HTMLElement 的属性）→ shadow root
//     必须取它子元素的 innerText/textContent，否则 shadow 页 page_text 只剩 10 字符
//  2. elementsFromPoint **不穿透 shadow**（返回 host）→ 判遮挡必须看命中元素在不在
//     合成树祖先链上，否则 shadow 元素被整片误判为被遮挡（实测 5/5 假阳性）
//  3. parentElement **出不了 shadow 边界** → region / 祖先链必须走
//     getRootNode().host（composedAncestors），否则 region 全部退化成 body
func observeJS() string {
	return withPierce(`(function(){
  // ── 穿透：内核助手。__cdpRoots(document) = [document, ...所有 shadow root] ──
  var RS = __cdpRoots(document);
  var qsa = function(sel){ return __cdpQA(sel); };

  function txt(el, n) { return (el && el.textContent ? el.textContent : '').replace(/\s+/g, ' ').trim().slice(0, n || 60); }
  function vis(el) {
    if (!el || !el.getBoundingClientRect) return false;
    var r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    var s = getComputedStyle(el);
    return s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0';
  }
  // ── 蜜罐：整个盒子落在**文档坐标的负区** ──
  // 真站实测（2026-09-16，check.compareinsulation.io 的漏斗页）：input[name="company_url"]
  // 被摆在 left:-9983px —— 人看不见，bot 填了就被站点标记。旧系统早有「蜜罐跳过」
  // （auto-farm-skill db792c1），这里按**位置**判据把它恢复。
  //
  // ⚠️ 判据必须是**文档**坐标，不是视口坐标：
  //     docLeft = rect.left + scrollX ;  docTop = rect.top + scrollY
  //   rect 是**视口相对**的 —— 页面只要滚过，一个完全正常的元素也会给出负的 rect.left
  //   （实测：滚到 (600,600) 时，文档坐标 120 的按钮给出 rect.left = -480）。
  //   拿视口坐标判，等于把所有「滚上去看不见」的正常内容一起丢掉，而且是**静默**丢
  //   —— 正是这个缺陷本来的样子，只是受害者换成了正常元素。文档坐标下那个按钮恒为
  //   120、滚不掉；而真陷阱在文档坐标里就是 ≤0，滚也滚不到：判据分得开两者。
  //
  // ⚠️ 名字（company_url / website / fax …）**不是**判据：名字会腐烂，而它腐烂的方向
  //   是**放行**（陷阱又回到 actions 里）。名字只当线索记进 honeypots[].hint。
  function trapWhy(el) {
    var r = el.getBoundingClientRect();
    var docLeft = r.left + (window.scrollX || window.pageXOffset || 0);
    var docTop = r.top + (window.scrollY || window.pageYOffset || 0);
    if (docLeft + r.width <= 0) return 'off-document-left';
    if (docTop + r.height <= 0) return 'off-document-top';
    return null;
  }
  function shadowDepth(el) {
    var d = 0, n = el;
    while (n) {
      var root = n.getRootNode ? n.getRootNode() : null;
      if (root && root.host) { d++; n = root.host; } else break;
    }
    return d;
  }
  // 合成树祖先链：parentElement 出不了 shadow 边界，必须经 getRootNode().host 跳
  // （内核只管「穿透查询」，合成树遍历不在其中 —— 这里保留自实现）
  function composedAncestors(el) {
    var out = [], n = el;
    while (n) {
      out.push(n);
      var r = n.getRootNode ? n.getRootNode() : null;
      n = (r && r.host) ? r.host : (n.parentElement || null);
    }
    return out;
  }
  // ── 命中测试：探针发现 elementsFromPoint **不穿透 shadow**（返回的是 host），
  //    所以「元素不在命中栈里」不能直接判遮挡。要看命中栈顶是不是它的合成树祖先。
  function occludedBy(el) {
    var r = el.getBoundingClientRect();
    var x = r.left + r.width / 2, y = r.top + r.height / 2;
    if (x < 0 || y < 0 || x > innerWidth || y > innerHeight) return 'offscreen';
    var stack = document.elementsFromPoint(x, y) || [];
    var anc = composedAncestors(el);
    for (var i = 0; i < stack.length; i++) {
      if (anc.indexOf(stack[i]) !== -1) return null;   // 命中的是它自己或它的祖先 → 没被挡
    }
    var t = stack[0];
    return t ? (t.tagName.toLowerCase() + (t.id ? '#' + t.id : '')) : 'unknown';
  }
  // 随机 token 判据（规格 §4.3；真站校准 = R5）。三种形态，任一命中即算随机：
  //   ① 被 -/_ 或串首尾夹住的 ≥8 位 hex 片段             a1b2c3d4e5f6 ／ id_9f8e7d6c5b4a
  //   ② 被 -/_ 或串首尾夹住的「可选字母前缀 + ≥6 位数字」  x1234567890 ／ css-x1234567890
  //   ③ 被 -/_ 或串首尾夹住、≥5 位、字母与数字**来回交替 ≥2 次**
  //                                                        css-1x2y3z4 ／ 1x2y3z4
  // ③ 是这一版补的（任务 5 修复轮 1）：brief 把 css-1x2y3z4 当随机 hash 的字面例子，
  // 而原先两道形态都够不着它 —— ①要 hex ≥8 位（7 位且含 x/y/z 直接出局）、
  // ②要整串无 "-"（css- 前缀挡死）。漏判的后果不是「少一条兜底」而是
  // **hash 直接当上首选选择器**（button.css-1x2y3z4），被 agent 抄进 py，
  // 站点下次发版 hash 重算即断 —— 规格 D3 说这正是 py 准不准的头号因素。
  //
  // ③ 的「交替 ≥2 次」不是为了绣花，是为了**不误伤正常类名**：正常类名的数字
  // 只有三种落法，都不产生「字母→数字→字母」的来回 ——
  //   无数字        btn-primary / hero-banner / form-control
  //   数字自成一段   col-md-6 / icon-24 / p-4
  //   数字在词尾     step1 / section1 / text-2xl（且 text-2xl 的 2xl 只有 3 位，另有长度地板）
  // 双向都被 observe_selector_test.go 钉住（正向「该抓的抓到」+ 反向「不该抓的别抓」）。
  //
  // 已知遗漏（留给 R5 用真站样本校准，别当成"已完备"）：
  //   a) 全字母 hash（styled-components 的 sc-bdVaJa）—— 没有数字可认，三形态都够不着。
  //      要认得引前缀表（css- / sc- / jss …），那是站点知识，应由真站样本得出。
  //   b) **≤4 字符的 hash 段**（css-a1b2）—— ③ 有一个 ≥5 位的长度地板（防 md5x 这类
  //      短词误伤），4 字符的段整个漏网。短 hash 的构建配置（CSS-modules 的 4~5 位 hash）
  //      会落在这一格；要收得先把「短到什么程度还算类名」在真站上量出来。
  //   c) 只翻转一次的 hash（css-abcdefg1）—— 被 ③ 的「交替 ≥2 次」挡在门外（那是
  //      为了不误伤 step1/section1 一家的代价）；其中 hex 且 ≥8 位的形态由①兜住
  //      （fixture 的 N hex10 form1-only = css-abcdef1234 专门守①这一条）。
  //   （注意本文件是 Go 的裸字符串字面量：注释里**不能出现反引号**。）
  var RAND = /(^|[-_])[0-9a-f]{8,}($|[-_])|(^|[-_])[a-z]*\d{6,}($|[-_])|(^|[-_])(?=[a-z0-9]{5,}($|[-_]))[a-z0-9]*([a-z][0-9]+[a-z]|[0-9][a-z]+[0-9])[a-z0-9]*($|[-_])/i;
  // ⚠️ RAND 的**已知误伤**（2026-09-17 真站实测，gowizard）：纯数字 id 「173851」
  // 被形态② 判成了随机 token（「[a-z]*\d{6,}」，6 位以上数字）。它不是随机的 ——
  // 它是页面自己编的**稳定**字段号，正是下面要拿来当锚点的那种东西。
  // **仍然不动它**：收窄这一条的波及面是全部站点（step2a / address1a 那一家的取舍
  // 刚在两轮修复里量过），而地址唯一性（下面的 address）已经从**另一头**把问题
  // 解决了 —— 一条被 RAND 拒掉、退化成结构路径的选择器，照样是**验证过的**唯一
  // 地址，只是没能停在那个锚点上、多爬几跳而已。风险不对称，留着。

  // ── 地址（addressability）：交出去的每一条选择器都必须**真的指到它、而且只指到它** ──
  //
  // 为什么要有这一节（2026-09-17 真站实测，gowizard 的 MUI 问卷）：observe 交给
  // agent 的地址本来就有一半是坏的 ——
  //
  //   三个 combobox 都只有一个 「div.MuiSelect-select.MuiSelect-standard」 → **3 命中**
  //   它的兜底路径（写死 4 跳）→ **11 命中**
  //   「a.decision-link」 同样是 6 个元素共用一个选择器
  //
  // agent 拿这些去点，只能靠猜；那一趟它在一个下拉框上猜了 25 步。地址不唯一
  // **不是**推理问题，是观察者交出去的东西本身立不住。
  //
  // ⚠️ 唯一性在**帧内**判（选择器按帧施用）。observe 会合并多个 frame，但这段脚本
  // 每次只在一帧里求值 —— 所以这里的查询天然就是「该帧内」，不许拿合并后的全局模型去判。

  // rootsQSA 用**已经取好的** root 列表查（与 __cdpQA 同一份 __cdpRoots(document)
  // 快照、同一套顺序），只是省掉「每次重新遍历全页找 shadow root」的开销 ——
  // 地址验证是逐候选跑的热路径（一个元素最多 5 条候选 × 200 动作 + 100 字段），
  // 每次重算 root 列表会把一次观测拖成几十秒。
  //
  // ⚠️ 语法不合法的选择器**会抛**，这里吞掉它是有意的（与内核助手同一个口径）：
  // 命中 0 → 下面的 uniq() 判它不合格 → 改用下一条候选。
  // **绝不能**让一条非法选择器当上首选 —— cdp 那边（__cdpQA）同样吞异常，
  // 于是它的表现不是报错，而是**元素凭空消失**（真站实测见 idSel 的注释）。
  function rootsQSA(sel) {
    var out = [];
    for (var i = 0; i < RS.length; i++) {
      var f;
      try { f = RS[i].querySelectorAll(sel); } catch (e) { continue; }
      for (var j = 0; j < f.length; j++) out.push(f[j]);
    }
    return out;
  }
  // uniq：这条选择器在这一帧里**恰好命中它自己**一个。
  //
  // ⚠️ 两条都要，缺一不可：
  //   length === 1    唯一 —— 3 命中的地址点下去是抽签
  //   hits[0] === el  而且**就是它** —— 只判「唯一」会交出一条「唯一命中的是**别人**」
  //                   的选择器。实测过：只判长度的版本把 G 的路径交给了 H
  //                   （两条链在上一层就撞上了），而那条选择器看起来完全正常。
  function uniq(sel, el) {
    var hits = rootsQSA(sel);
    return hits.length === 1 && hits[0] === el;
  }
  // idSel 把 id 变成一个**语法合法**的选择器。
  //
  // ⚠️ 血泪（2026-09-17 真窗口实测）：「#173851」 是**非法的 CSS 选择器**（ident 不能
  // 以数字开头）—— document.querySelector('#173851') 抛 SyntaxError。而 __cdpQA
  // 与 rootsQSA 都用 try/catch 吞掉这个异常，所以它的表现不是报错，是**元素凭空消失**：
  // 「cdp click --selector '#173851 …'」 报 element not found，而页面上明明有那个元素；
  // 同一件事写成 「[id="173851"]」 命中 1 个、点击成功。
  // 所以：**只要 id 不是合法的 CSS ident，就改用 [id="…"] 形式**，不许把 #id 直接拼出去。
  // （RAND 此刻恰好把纯数字 id 拒掉了，等于误打误撞挡住了这个坑 —— 但那是运气，
  //   而且它只挡 ≥6 位数字那一档：「id="12"」 / 「id="3d-btn"」 照样漏得过来。）
  // 判据取**保守**的一侧：认不出来的一律退回 [id="…"]（那种形式永远合法）。
  function idSel(id) {
    return /^[A-Za-z_][A-Za-z0-9_-]*$/.test(id) ? '#' + id
      : '[id="' + id.replace(/\\/g, '\\\\').replace(/"/g, '\\"') + '"]';
  }
  // pathChain 从元素往上爬，返回结构路径。**没有跳数上限** —— 停止条件只有两个：
  //
  //   stopAtId=true  爬到「有稳定 id 的祖先」就在它上面停车（「#inputAreaParentContainer」
  //                  那类锚点是页面自己的骨架，抗得住重渲染），或者爬到 frame 根
  //   stopAtId=false 忽略 id，一路爬到 frame 根（= 走到 html 为止；html 之上没有可选的层了）
  //
  // ⚠️ 原先这里写死 4 跳（「爬 4 跳就交差」）—— 目标页面上那个 id 锚点在 **hop9**，
  // 4 跳连影子都摸不到，于是交出去的是一条 11 命中的路径。这次修的就是它：
  // 不是把 4 换成 8，是**换成停止条件**。
  function pathChain(el, stopAtId) {
    var parts = [], n = el;
    // 停止条件：爬到 html（frame 的根，它自己不做路径的一段 —— 加进去只是噪音），
    // 或者爬到一个没有 parentElement 的节点（shadow root 里的顶层元素就是这种）。
    // ⚠️ 判据写在**循环条件**上而不是「加之前先看有没有爹」：后者会把 shadow root 里
    // 顶层那一格整个丢掉 —— 元素的 parentElement 是 null（它的爹是 ShadowRoot，
    // 不是元素），于是路径只剩它自己那一跳，比实际能写出来的**更弱**。
    while (n && n.tagName && n.tagName.toLowerCase() !== 'html') {
      if (stopAtId && n.id && !RAND.test(n.id)) { parts.unshift(idSel(n.id)); break; }
      var idx = 1, sib = n;
      while ((sib = sib.previousElementSibling)) if (sib.tagName === n.tagName) idx++;
      parts.unshift(n.tagName.toLowerCase() + ':nth-of-type(' + idx + ')');
      n = n.parentElement;
    }
    return parts.join(' > ');
  }
  // pathSel：给候选链用的结构路径 —— **爬到唯一为止**。
  //
  // 两趟，都是「爬到某个条件」而不是「爬几跳」：
  //   ① 先爬到稳定 id 的祖先（能停就停：锚点比长路径抗改名）
  //   ② ①不合格（不唯一 / 命中的不是它）就再爬一趟，这回不在 id 上停、一路到 frame 根
  //      —— 「唯一性验证失败就继续加长路径」就是这一步。全爬到底仍不唯一的情况真实存在：
  //      元素在 shadow root 里、而光 DOM 里有同形的孪生结构 —— **CSS 选择器跨不过
  //      shadow 边界**（observe_reads.html 里有这个负例）。
  // 两趟都不合格 → 把 ① 交出去；「它不唯一」这件事由调用方按 stability 说出去
  // （见下面 address 与 Go 侧 Action.Stability 的注释）。
  function pathSel(el) {
    var p = pathChain(el, true);
    if (uniq(p, el)) return p;
    var q = pathChain(el, false);
    if (q !== p && uniq(q, el)) return q;
    return p || q;
  }
  function candidates(el) {
    var out = [];
    // ⚠️ 这里吐的是**原始候选**（没验过唯一），交出去之前一律过 address()。
    // id 那一行走 idSel：「#173851」 那种 id 直接拼出去是一条**语法非法**的选择器
    // （见 idSel 的注释），而它的失败是静默的。
    if (el.id && !RAND.test(el.id)) out.push(idSel(el.id));
    // name 也要过 RAND（修复轮 1）：它是四个落点里**唯一**原先没过的一道 ——
    // 随机 name（如 sid_9f8e7d6c5b4a，RAND 形态①本来就认得）会直接当上首选、
    // 且判据里 [name= 落进 high 分支 → 判据明写 high 须「不含随机 hash」。
    //
    // ⚠️ 这里有一个**刻意的取舍**，写在这儿免得下一个人以为是漏了：
    //   RAND 形态③（字母数字交替）对 class 是「捡便宜」—— class 由框架生成，hash 常见；
    //   对 name 却是「收益小、代价稍大」—— name 是人写的，hash 罕见，而人写的子字段名
    //   （step2a / address1a / opt2b 这类「步骤+序号+子项」惯例）正好落进 ③ 的
    //   「字母→数字→字母」形态，会被一起抓走、退化成位置路径（medium）。
    //   为什么不给 name 开特例（比如只让它过①②）：**一条规则、一个偏置**比按落点
    //   分叉更好维护 —— 分叉意味着同一个 token 在不同落点有不同命运，那种「两份判据」
    //   正是本仓反复踩的坑；而且退化的代价是**轻微**的（回退到结构路径，仍能选中元素，
    //   只是不再抗结构变化）。控制器（Task 5 审查）裁定：接受 name 也适用 ③。
    //   代价已量化留档：fixture 的 "N subfield name"(name=step2a) 就是断言它确实退化了。
    if (el.name && !RAND.test(el.name)) out.push(el.tagName.toLowerCase() + '[name="' + el.name + '"]');
    ['data-testid', 'data-test', 'data-id', 'data-value'].forEach(function (a) {
      var v = el.getAttribute && el.getAttribute(a);
      if (v && !RAND.test(v)) out.push(el.tagName.toLowerCase() + '[' + a + '="' + v + '"]');
    });
    var cls = (el.className && typeof el.className === 'string' ? el.className : '')
      .split(/\s+/).filter(function (c) { return c && !RAND.test(c); }).slice(0, 2);
    if (cls.length) out.push(el.tagName.toLowerCase() + '.' + cls.join('.'));
    out.push(pathSel(el));
    return out.filter(function (s, i, a) { return s && a.indexOf(s) === i; });
  }
  // address 是**真正交给模型的那条地址**。与 candidates 的分工：
  // candidates 只管「这个元素能用哪些办法找到」（原始候选，可能会重复命中别人），
  // address 负责**验证**，并决定哪一条当首选（c[0]）、哪些当退路（alternates）。
  //
  // 规矩（c[0] 与 alternates 是**同一条**规矩，不是两条）：
  // 交出去的每一条都必须 uniq —— 命中恰好 1 个、且就是它。
  //   一条 3 命中的选择器当「退路」用，不是退路，是**第二个坑**：真站上那 5 个
  //   共享 class 的按钮，退路正好是文档序第一个 = Back —— 顺着它走会把漏斗**倒着**走。
  //   所以不合格的候选**不进模型**（不是降级排在后面），宁可少给一条。
  //
  // 一条都验不出来时（真站形态：元素在 shadow root 里、而光 DOM 里有同形的孪生结构；
  // CSS 选择器跨不过 shadow 边界）：
  //   交「尽力而为」的那条（先试爬到 frame 根的全程路径，再退而求其次找一条至少
  //   **指向它**的候选），并置 unique=false —— 调用方据此把 stability 降成 low。
  //   **那一步就是「交不出唯一选择器时如实说」的落点**（Go 侧 Action.Stability 里
  //   写着这条契约）。绝不静默：宁可交一条被明确标记为不可信的地址，
  //   也不假装它是好的。
  function address(el) {
    var raw = candidates(el), i, hits, sel = null, alts = [];
    for (i = 0; i < raw.length; i++) {
      if (uniq(raw[i], el)) {
        if (sel === null) sel = raw[i]; else alts.push(raw[i]);
      }
    }
    if (sel !== null) return { sel: sel, alts: alts, unique: true };
    var best = pathChain(el, false);
    hits = rootsQSA(best);
    if (!(hits.length && hits[0] === el)) {
      best = '';
      for (i = 0; i < raw.length; i++) {
        hits = rootsQSA(raw[i]);
        if (hits.length && hits[0] === el) { best = raw[i]; break; }
      }
      if (!best && raw.length) best = raw[raw.length - 1];
    }
    return { sel: best, alts: [], unique: false };
  }
  // ── 人话名字（label）：这个控件在页面上**叫什么** ──
  //
  // 为什么非有不可（2026-09-17 真站实测，gowizard 的 MUI 问卷）：三个「点开再选」的
  // combobox 显示文本分别是 「2025」 / 空 / 空 —— **没有标签就没有语义**，模型分不出
  // 哪个是 Make。而名字就在页面上：label[for="173851"] 的文本是 「Make」，它指的那个
  // id 是那个 combobox 的**第 6 层祖先**（hop6）。旧实现的三条取法
  // （自己的 id / closest(label) / label[for=自己的 id]）一条都够不着 hop6。
  //
  // for→文本 表**只建一次**（qsa('label[for]') 一趟）：逐个元素去查
  // label[for="…"] 是 O(元素数 × 全页)，而且 id 里的引号会把选择器拼坏。
  // 表按 for 的**字面值**键（不经过 getElementById）—— shadow root 里的 id 在
  // 文档级 getElementById 是查不到的，而「祖先的 id 等于这个 for」这件事
  // 在任何一个 root 里都成立。同一个 id 挂多个 label 时**第一个说了算**（文档序）。
  //
  // ⚠️⚠️ 字典**必须是无原型的**（Object.create(null)），读的时候再只认字符串 ——
  // 两道闸都要，不是重复：id 是**页面作者写的任意字符串**，其中一族正好落在
  // Object.prototype 的名字上（__proto__ / constructor / toString / valueOf 那一族）。
  // 用普通对象 {} 存，这一族就当场出两种静默错：
  //   ① 读：LABELS['constructor'] 取到的是**函数**、LABELS['__proto__'] 取到的是
  //      **Object.prototype** —— 两者都是真值，于是 labelOf 把一个**对象**当成名字
  //      返回，串进 JSON 之后 Go 侧的 *string 解不开 → **整条 observe 失败**。
  //      一个页面上的 id 就能让整个观测崩掉，而报出来的错跟这行代码看不出关系。
  //   ② 写：m['__proto__'] = 文本 那一步**根本走不到** —— 上面那条
  //      「if (f && t && !m[f])」的守卫先命中：在普通对象上取 m['__proto__']
  //      拿到的是 Object.prototype（node 实测 !({})['__proto__'] === false，
  //      真值），于是整条写被跳过。净效果仍是「名字丢了、也没人知道」，
  //      但机制是**守卫拦下**，不是「原型 setter 静默忽略」—— 那一步压根没执行
  //      （2026-09-17 实测更正：原先这里写的是 setter，那是猜的，不是量的）。
  // 无原型字典把这一族变成普通键（读不到就是 undefined，写进去就是自己的属性）；
  // labelAt 里那道 typeof 是第二道 —— **任何**非字符串都当「没有名字」，
  // 绝不让一个非字符串漏进 JSON（这一轮要消灭的正是「静默全崩」那一类）。
  var LABELS = (function () {
    var m = Object.create(null), ls = qsa('label[for]');
    for (var i = 0; i < ls.length; i++) {
      var f = ls[i].getAttribute('for'), t = txt(ls[i], 40);
      if (f && t && !m[f]) m[f] = t;
    }
    return m;
  })();
  // labelAt 读字典：只认**字符串**（两道闸的理由见上面 LABELS）。
  // 没有 → 空串（调用方按「没有名字」处理）。
  function labelAt(id) {
    var v = LABELS[id];
    return typeof v === 'string' ? v : '';
  }
  // labelOf：按优先级取人话名字（与 Go 侧 Action.Label 的注释同一套）。
  //   ①② 用 label[for] 指过来的名字（自己 → 逐层祖先）；③ 前一个 LABEL 兄弟；④ null
  //
  // ⚠️ ①②的爬升**没有跳数上限**：爬到「有 id 且被 label[for] 指着」的第一个祖先，
  // 或者爬到 frame 根。真站上那一格在 hop6 —— 任何「找 4 跳就交差」的写法都够不着它。
  // （走合成树 composedAncestors：shadow 里的 parentElement 会断在边界上。）
  // ⚠️ ③ 也**逐层往上**找（不是只看元素自己的兄弟）：MUI 的 InputLabel 是
  // 「MuiInputBase-root」 那一层的 previousElementSibling。就近优先 —— 一找到就返回，
  // 所以更靠上的、理它更远的 label 不会抢在近处的前面。
  // ⚠️ 都找不到就交 null：编一个名字出来比空着更坏（消费侧会把编的当页面事实用）。
  function labelOf(el) {
    var chain = composedAncestors(el), i, n, p, t, v;
    if (el.id) { v = labelAt(el.id); if (v) return v; }
    for (i = 1; i < chain.length; i++) {
      n = chain[i];
      if (n.id) { v = labelAt(n.id); if (v) return v; }
    }
    for (i = 0; i < chain.length; i++) {
      p = chain[i].previousElementSibling;
      // ⚠️ **只认紧挨着的那一个**，不再往前翻着找。翻着找实测出过一次**谎报**：
      // 一路往回扫到 body 的兄弟位置时，会捞到页面上**别处**某个 label（夹具上
      // 那一格是「Street」—— 一个跟控件毫无关系的单选标签），而名字这种东西
      // 报错的后果是消费侧把它当成页面事实用。宁可少报（→ null），不许猜。
      if (p && p.tagName && p.tagName.toLowerCase() === 'label') {
        t = txt(p, 40);
        if (t) return t;
      }
    }
    return null;
  }
  // traps：被排除的蜜罐，**记一笔**再丢（见 Go 侧 Honeypot 的注释）。
  //
  // 为什么要去重：同一个元素会被**两条路**各查一次 —— 可动作元素的选择器里含
  // input/select/textarea，表单字段收集器**也**收这一批。不设防的话同一个陷阱会记
  // 两条；而「同一条陷阱被报两次」正是这个缺陷原来的样子（当时是同时列进
  // actions 与 fields 各一次），别让它换个通道复活。
  var traps = [], trapEls = [];
  function trap(el) {
    var why = trapWhy(el);
    if (!why) return null;
    if (trapEls.indexOf(el) === -1) {
      trapEls.push(el);
      // 与 actions / fields 走**同一条**地址路（address），并带上它有没有验证过 ——
      // 这三个列表没有 stability，唯一性只能自己说（见 Go 侧 Honeypot.SelectorUnique）。
      var tad = address(el);
      traps.push({
        selector: tad.sel,
        selector_unique: tad.unique,
        hint: el.name || el.id || el.placeholder || '',
        why: why
      });
    }
    return why;
  }
  // stability：这条地址**抗不抗得住重渲染 / 改名**。判据一句话 ——
  //
  //   地址 = 锚点（身份）+ 尾巴（位置）；评级看**尾巴里有几跳位置**。
  //
  //     hops = 地址里 :nth-of-type 的个数。每一个都是「兄弟里的第几个」，
  //            多一个就多一层结构依赖 —— 中间插一个同标签的 div 就断。
  //     有锚点（#id / [id=…] / [name=…] / [data-*=…]，页面作者写的身份）：
  //       hops 0 跳 → high     整条地址都是身份，没有一处「第几个」
  //       hops 1–3 → medium
  //       hops ≥4  → low
  //     没有锚点（整条只剩位置或 class）：同一把尺子，但没有身份就没有 high ——
  //       0 跳且是 class → medium（class 是框架生成物，抗改名不如 id）
  //       其余 → 跳数 ≤3 是 medium、≥4 是 low
  //
  // ⚠️ 为什么不再「以 # 开头就 high」（2026-09-17 改的，实测驱动）：
  //   F2 之后**锚点停车成了常规行为**（pathChain 爬到第一个稳定 id 的祖先就停），
  //   于是「锚点 + 长位置尾巴」的地址在真站主帧上满屏都是。实测那一趟（gowizard
  //   的 MUI 问卷，2026-09-17）三个 combobox 拿到的都是
  //   「#inputAreaParentContainer > … 9 跳 …」，旧判据只看首字符，三条**全是 high**。
  //   危害在消费侧：产物 py 的回退梯子按 stability 排序（agent/template.py 的
  //   rank = {high:0, medium:1, low:2}），于是一条 9 跳位置路径**排在真正稳定的
  //   id 前面先试**；而同一轮 alternates 又大幅变少（真站主帧 60 → 3），
  //   回退余地同时变小 —— 两件事叠在一起才是这个缺陷的完整形状。
  //
  // ⚠️ 也不许反过来把长路径**一律**打成 low（那等于 rank 集体退化 = 没有排序，
  //   而且把「锚点 + 1 跳」（几乎不会断）与 9 跳路径混成一档）。分档落在
  //   **锚点之后的位置跳数**上，与「没有锚点的纯结构路径」**同一把尺子**：
  //   规格 §4.3 的「结构路径 ≤3 层 → medium」就是这把尺子，而纯路径的跳数 = 段数，
  //   与旧实现的「> 个数 ≤2」**逐字等价** —— 所以 G / I 那几格的档位一个都没动。
  //
  // ⚠️ 判据的**单位**从「> 的个数」换成了「:nth-of-type 的个数」：后者更准
  //   （div:nth-of-type(1) span:nth-of-type(1) 一个 > 都没有，却同样依赖两次位置）。
  //   pathSel 吐出来的永远是 > 连接的链，两种数法在那条路上相等。
  function stability(el, cands) {
    var c = cands[0] || '';
    var hops = (c.match(/:nth-of-type/g) || []).length;
    // 锚点：「[id=…]」与「#id」是**同一件事**（idSel 把不是合法 CSS ident 的 id 写成
    // 前者的形式）—— 只认「#」会让那类元素的稳定性**凭空掉一档**：
    // 一条独一无二的稳定 id 被评成 low，消费侧会以为它脆（少报也是一种不实）。
    var anchored = /^#/.test(c) || /^\[id=/.test(c) || /\[(name|data-)/.test(c);
    if (anchored && hops === 0) return 'high';
    // 只有 class（框架生成物）：没有身份，但也不是位置 —— 与从前同档。
    if (hops === 0 && /^[a-z]+\.[a-z]/.test(c)) return 'medium';
    return hops <= 3 ? 'medium' : 'low';
  }
  function region(el) {
    // 探针发现：走 parentElement 在 shadow 里会断（到 shadow root 顶就 null），
    // 于是所有 shadow 元素都退化成 'body'。必须走合成树。
    //
    // ⚠️ **两趟扫：landmark 优先，class 派生兜底**（2026-09-16 Task 7 修复轮 1）。
    // 原先是「一趟走到底、谁先命中算谁」—— 于是「某个祖先的 className 里带 hero」
    // 与 landmark 平起平坐。而 className 是**框架生成物**（hash 一刷就变），
    // 让区域退化成改名敏感的字段 —— 而 diff 的身份键正要靠 region 抗改名
    // （见 internal/diff.go 的 regionIdentity：它只认下面这趟 landmark 扫出来的值）。
    // 现在：先扫完整条链找 header/nav/footer/aside/main（**标签名**，重渲染不会换）
    // 与 role=dialog（ARIA 属性，也不是生成物）；都没有，才回落到 class 派的 hero。
    var chain = composedAncestors(el), i;
    for (i = 0; i < chain.length; i++) {
      var n = chain[i];
      if (!n.tagName) continue;
      var t = n.tagName.toLowerCase();
      if (t === 'header' || t === 'nav' || t === 'footer' || t === 'aside' || t === 'main') return t;
      if (n.getAttribute && n.getAttribute('role') === 'dialog') return 'dialog';
    }
    // ⚠️ 残留：**没有 landmark 祖先**的页面上，区域仍可能由 className 派生
    // （fixture base.html 的 section.hero 就是这一格）。消费者要按「这条可能是
    // 生成的 class」来对待它 —— diff 侧就是这么做的。
    for (i = 0; i < chain.length; i++) {
      var m = chain[i];
      if (!m.tagName) continue;
      var cn = (typeof m.className === 'string' ? m.className : '').toLowerCase();
      if (/hero|banner|jumbotron/.test(cn)) return 'hero';
    }
    return 'body';
  }
  function lum(c) {
    var m = /rgba?\(([^)]+)\)/.exec(c || ''); if (!m) return null;
    var p = m[1].split(',').map(function (x) { return parseFloat(x); });
    if (p.length > 3 && p[3] === 0) return null;
    var f = p.slice(0, 3).map(function (v) {
      v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * f[0] + 0.7152 * f[1] + 0.0722 * f[2];
  }
  function contrastBand(el) {
    var s = getComputedStyle(el), fg = lum(s.color), n = el;
    var bg = null;
    while (n && !bg) { bg = lum(getComputedStyle(n).backgroundColor); n = n.parentElement; }
    if (fg === null || bg === null) return null;
    var hi = Math.max(fg, bg), lo = Math.min(fg, bg);
    var ratio = (hi + 0.05) / (lo + 0.05);
    return ratio >= 4.5 ? 'high' : ratio >= 3 ? 'medium' : 'low';
  }
  function nearbyText(el) {
    var out = [], n = el.previousElementSibling, k = 0;
    while (n && k < 2) { var t = txt(n, 40); if (t) { out.push(t); k++; } n = n.previousElementSibling; }
    n = el.nextElementSibling; k = 0;
    while (n && k < 1) { var t2 = txt(n, 40); if (t2) { out.push(t2); k++; } n = n.nextElementSibling; }
    return out;
  }

  // ── 三样读法：值 / 选中态 / 无障碍名（2026-09-17 真站实测补的，规格 §4.1 U2）──
  //
  // 它们回答的是**同一类问题**：agent 自己刚才那一下，到底生效了没有。
  // 真站上它答了「Sedan」却无法确认（模型里没有一个字说这件事）→ 反复重答；
  // 填了值也一样（填前填后两份模型逐字节相同）。三条缺口的原委与证据：
  // docs/probes/2026-09-17-observe-blinkist/。

  // READ_CAP 是**回读**字段的上限（value 与 aria_label 共用这一份）。
  //
  // 为什么是 80：
  //   - 够用：一个真实的值/名字都在这以内（邮箱、地址、车型、选中的年份、
  //     「VIN (17 characters)」这类无障碍名），确认「我写进去的就是这个」只要前
  //     几十个字符就够
  //   - 封得住：元素上限是 200 动作 + 100 字段，每个都带一个值 —— 不封顶时
  //     一个几十 KB 的 textarea 就能把一次观测撑成几兆
  //   - 与 text（50）/ nearby_text（40）同量级，不是新开一档口径；
  //     与 click 的描述（32）不同档是有意的：那一行进 stderr 与报错，必须短
  //   **只此一份**：Go 侧不另写一个常量（同一件事两个真相，改一处漏一处是静默的）。
  //   有测试从这段脚本里读它（internal/observe_reads_test.go）。
  var READ_CAP = 80;

  // valOf：这个元素**现在装着什么**。
  //
  // 只有 input / textarea / select 有「值」这回事；别的元素一律 null ——
  // **不是**空串。空串是一句关于页面的断言（「这个框现在是空的」—— 也就是
  // 「写入没落地」），按钮上编一个空串等于替页面说了一句假话。
  function valOf(el) {
    var t = el.tagName.toLowerCase();
    if (t !== 'input' && t !== 'textarea' && t !== 'select') return null;
    return typeof el.value === 'string' ? el.value : '';
  }

  // selState：这个控件**现在是不是选中/勾起/按下**。三态：true / false / null（看不出）。
  //
  // null 与 false 必须分得开。把「看不出」编成 false，消费侧会读成「还没选」，
  // 然后**再答一遍** —— 那正是这个缺陷本来的样子换了个位置（真站上「呆呆的」就是它）。
  //
  // 判据顺序（缺一不可）：
  //   ① ARIA 那一套：自定义控件的状态就写在这里，且它是**作者显式声明**的
  //      （真站上那三个图标选项只有一个带 aria-checked —— 另外两个就是看不出）
  //   ② 原生状态走 IDL：select 看 selectedIndex、radio/checkbox 看 checked
  //   ③ 都没有 → null
  //
  // ⚠️ 刻意**不**认 class（Mui-selected / is-active 那一类）：class 是站点自己起的
  // 名字，认它等于把「什么样算选中」交给页面 —— 与本文件 Disabled 的那条裁定同一套
  // （Mui-disabled 不算禁用证据）。误判方向是**谎报**，而这里宁可少报。
  function selState(el) {
    var attrs = ['aria-selected', 'aria-checked', 'aria-pressed'];
    for (var i = 0; i < attrs.length; i++) {
      var v = el.getAttribute(attrs[i]);
      if (v === 'true') return true;
      if (v === 'false') return false;
      // "mixed" / 空串 / 别的取值**都不是答案**：它不是 true 也不是 false，
      // 继续往下问原生状态；原生也没有 → null（看不出），而不是编一个 false。
    }
    var tag = el.tagName.toLowerCase(), ty = (el.type || '').toLowerCase();
    if (tag === 'select') return el.selectedIndex >= 0;
    if (tag === 'input' && (ty === 'checkbox' || ty === 'radio')) return !!el.checked;
    return null;
  }

  // ── 可动作元素 ──
  // ⚠️ 蜜罐在**切片之前**滤掉：切片（slice(0,200)）是截断，让陷阱占着名额等于
  // 把页面末尾的真元素挤出去 —— 一个观察者自己制造出来的盲区。
  // ⚠️ 「[role=combobox]」 是 2026-09-17 真站实测补的（gowizard 的 MUI 问卷）：目标页面上
  // 那三个「点开再选」的控件是 「<div role="combobox" class="MuiSelect-select …">」，
  // **全部可见**（opacity 1、319×60），本可以通过 vis()，但这一串里没有这个角色 ——
  // 于是 qsa(SEL) 一个都取不到，模型里 fields: 0、actions 里没有它们。
  // 那是「控件进不了模型」的直接原因：agent 看不见的东西，它当然点不到。
  var SEL = 'a[href],button,input,select,textarea,[role=button],[role=link],[role=option],[role=tab],[role=checkbox],[role=radio],[role=combobox],[onclick]';
  var cands = qsa(SEL).filter(vis).filter(function (el) { return !trap(el); });
  var areas = cands.map(function (e) { var r = e.getBoundingClientRect(); return r.width * r.height; })
    .sort(function (a, b) { return a - b; });
  var med = areas.length ? areas[Math.floor(areas.length / 2)] : 1;

  var actions = cands.slice(0, 200).map(function (el) {
    var r = el.getBoundingClientRect(), ad = address(el);
    var tag = el.tagName.toLowerCase();
    var peers = cands.filter(function (o) { return o.tagName === el.tagName && region(o) === region(el); }).length;
    var val = valOf(el);
    return {
      // selector / alternates：**每一条都验证过唯一**（见 address）。
      // stability：唯一不了就**如实降成 low** —— 那是「这条地址我没能验证」的落点。
      // 不唯一还报 high/medium，正是「猜 25 步」的来源。
      selector: ad.sel, alternates: ad.alts,
      stability: ad.unique ? stability(el, [ad.sel]) : 'low',
      text: txt(el, 50), role: el.getAttribute('role') || tag, tag: el.tagName,
      type: el.type || null, visible: true,
      // 页面上印着的名字（label 元素给的那个）。null = 找不到，**不是**空串 ——
      // 三态与取法见 Go 侧 Action.Label。
      label: labelOf(el),
      // 三样读法（见上面 READ_CAP / valOf / selState 的注释）。
      // ⚠️ value 的 null 与 "" 是两件事：null = 不是值控件，"" = 是值控件但现在是空的。
      // ⚠️ value_truncated 只在**真的截了**的时候是 true（截断要说出来，
      //    否则消费侧把半个值当成全部）。
      aria_label: (el.getAttribute('aria-label') || '').slice(0, READ_CAP),
      value: val === null ? null : val.slice(0, READ_CAP),
      value_truncated: val !== null && val.length > READ_CAP,
      selected: selState(el),
      // 能不能点：两条判据（IDL disabled / aria-disabled），理由见 Go 侧 Action.Disabled。
      // ⚠️ 与 internal/click.go 的探测必须**同判据** —— 一个说能点、一个说不能点，
      // 消费侧就会拿模型去点一个必然空点的目标。
      disabled: (el.disabled === true || el.getAttribute('aria-disabled') === 'true'),
      occluded_by: occludedBy(el),
      shadow_depth: shadowDepth(el), frame_path: ['main'],
      bbox: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)],
      region: region(el), above_fold: r.top < innerHeight,
      relative_size: med ? Math.round((r.width * r.height / med) * 10) / 10 : null,
      peer_count: peers, z_index: getComputedStyle(el).zIndex,
      contrast: contrastBand(el), nearby_text: nearbyText(el)
    };
  });

  // ── 表单字段 ──
  // 这条**也要**过 trap()：input[name=company_url] 原来正是从**这一条**路又漏了
  // 一遍（它在 actions 里被滤掉不等于在 fields 里也被滤掉 —— 两条独立的收集路）。
  var fields = qsa('input,select,textarea').filter(vis).filter(function (el) { return !trap(el); }).slice(0, 100).map(function (el) {
    // 标签取法按**抗改名的程度**排序（2026-09-16 Task 7 修复轮 1）：
    //   1. aria-label —— 元素自己的属性，重渲染不换
    //   2. closest('label') —— **结构**关系，与 id 无关
    //   3. label[for=id] —— 与 **id 耦合**：重渲染把 input 重新挂载一次
    //      （React 的 :r1: 那类新 id）这条关联就断，标签凭空消失。
    // 原先 order 是 1 → 3 → 2，于是「结构上包着它的 label」还排在「靠 id 找的」后面。
    // diff 的身份键因此不敢用 Label（见 internal/diff.go 的字段身份键）。
    var lab = el.getAttribute('aria-label') || '';
    if (!lab) { var pl = el.closest ? el.closest('label') : null; if (pl) lab = txt(pl, 40); }
    if (!lab && el.id) { var l = qsa('label[for="' + el.id + '"]')[0]; if (l) lab = txt(l, 40); }
    // 三样读法与 actions 那条**同一套判据**（valOf / selState / READ_CAP）——
    // 表单字段这一路是「值填进去了没有」的主战场，两条路各写一遍就意味着
    // 其中一条哪天会静默地什么都没有（蜜罐那次就是两条路各漏一次）。
    var fval = valOf(el), fad = address(el);
    return {
      // 与 actions 那条**同一套地址规矩**（address：每条都验证过唯一；唯一不了就降 low）。
      // 顺带：这里原先一共同 candidates() 调了**三次**（首选一次、alternates 一次、
      // stability 一次）—— 地址验证是热路径，现在只算一次（parse 地址那点开销就从这儿省回来）。
      selector: fad.sel, alternates: fad.alts,
      stability: fad.unique ? stability(el, [fad.sel]) : 'low',
      label: lab || '', hint: el.name || el.id || '', placeholder: el.placeholder || '',
      aria_label: (el.getAttribute('aria-label') || '').slice(0, READ_CAP),
      value: fval === null ? null : fval.slice(0, READ_CAP),
      value_truncated: fval !== null && fval.length > READ_CAP,
      selected: selState(el),
      type: el.type || el.tagName.toLowerCase(), required: !!el.required,
      shadow_depth: shadowDepth(el), frame_path: ['main']
    };
  });

  // ── 选项组 ──
  var groups = [];
  qsa('[role=radiogroup],fieldset,.opts,[class*=option],[class*=choice]').forEach(function (g) {
    var opts = Array.prototype.slice.call(g.querySelectorAll('button,[role=radio],[role=option],label,input[type=radio],input[type=checkbox]'))
      .filter(vis).map(function (o) { return txt(o, 40); }).filter(Boolean);
    if (opts.length >= 2) {
      var gad = address(g);
      groups.push({ scope: gad.sel, scope_unique: gad.unique, role: 'option', options: opts.slice(0, 12), shadow_depth: shadowDepth(g) });
    }
  });

  // ── 遮挡物 ──
  var obs = [];
  qsa('div,section,aside').filter(function (el) {
    if (!vis(el)) return false;
    var s = getComputedStyle(el);
    if (s.position !== 'fixed' && s.position !== 'sticky') return false;
    var z = parseInt(s.zIndex, 10); if (!(z > 100)) return false;
    var r = el.getBoundingClientRect();
    return r.width * r.height > innerWidth * innerHeight * 0.08;
  }).slice(0, 6).forEach(function (el) {
    var btn = Array.prototype.slice.call(el.querySelectorAll('button,a')).filter(vis)[0];
    // 两条地址各自过一遍 address，各自带自己的 unique 标记：
    // 横幅那条（挡着什么）与关闭按钮那条（要点哪个）是**两件事**，一个唯一不等于另一个唯一。
    // 没有关闭按钮时 dismiss_selector_unique 交 **null**（不是 false）—— 「没有这条地址」
    // 与「这条地址没验证过」是两件事，见 Go 侧 Obstruction.DismissSelectorUnique。
    var oad = address(el), bad = btn ? address(btn) : null;
    obs.push({ kind: /cookie|consent|gdpr|privacy/i.test(txt(el, 120) + el.id + el.className) ? 'cookie-banner' : 'overlay',
      selector: oad.sel, selector_unique: oad.unique,
      dismiss_selector: bad ? bad.sel : null, dismiss_selector_unique: bad ? bad.unique : null,
      text: txt(el, 60) });
  });

  // ── 正文：探针发现两个坑 ──
  //   ① innerText 不穿 shadow（document.body.innerText 在 shadow 页上几乎为空）
  //   ② ShadowRoot 根本没有 innerText（那是 HTMLElement 的属性）——
  //      第一版直接对 root 取 innerText 拿到 undefined，页面正文只剩 10 个字符
  //   正确做法：逐 root 收集，shadow root 取它**子元素**的 innerText
  var perRoot = RS.map(function (rt) {
    if (rt.body) { try { return rt.body.innerText || ''; } catch (e) { return ''; } }
    var out = [];
    for (var i = 0; i < rt.children.length; i++) {
      var c = rt.children[i];
      try { out.push(c.innerText || c.textContent || ''); } catch (e) { }
    }
    return out.join(' ');
  });
  var pageText = perRoot.join(' ').replace(/\s+/g, ' ').trim();

  return JSON.stringify({
    url: location.href, title: document.title,
    page_text: pageText.slice(0, 600),
    shadow_roots: RS.length - 1,
    // 视口（CSS 像素）。**必须**是 innerWidth/innerHeight：它们**含**滚动条，
    // 与 screenshot 那条契约（image_px = viewport_css_px × dpr，滚动条画进图里）
    // 对齐；clientWidth/clientHeight 不含滚动条，用了就会与截图静默错开一个滚动条宽。
    // Math.round：契约是整数（PixelSize），小数（页面缩放时）会让 Go 侧解不动 int。
    viewport_css_px: { width: Math.round(window.innerWidth), height: Math.round(window.innerHeight) },
    actions: actions, fields: fields, option_groups: groups, obstructions: obs,
    honeypots: traps
  });
})()`)
}

// Observe 对指定帧求值并解析成 PageModel。frameID 为空表示主帧。
//
// 这是**单帧**那条路的入口（CLI 的 --frame-id、diff 的 after 观测都走它）。
// 整页那条路见 ObserveAll。
func (c *Client) Observe(frameID string) (*PageModel, error) {
	m, err := c.observeFrame(frameID)
	if err != nil {
		return nil, err
	}
	// 「挑中的页是猜的」这条诊断在**入口**发，不在 observeFrame 里发 ——
	// ObserveAll 逐帧调 observeFrame，在那一层发就成了 N 帧 N 条（见 Client.targetDiags）。
	m.Diagnostics = append(m.Diagnostics, c.targetDiags...)
	// 单帧这条路的两处归一化都在这里（合并那条路走 mergeFrameModel + normalizeNilLists）：
	//   - 空列表一律编成 []（JS 不产出 diagnostics，不归一化它编出来就是 null）
	//   - frame_path 按单帧契约盖成 [frameID]（JS 写死的 ['main'] 在子帧上是错的）
	return normalizeFramePaths(normalizeNilLists(m), frameID), nil
}

// observeFrame 只做「观测这一帧」本身：求值 + 解析，**不带**目标歧义诊断。
// 归一化也不在这里（两条路各自的形状不同，见 Observe 与 ObserveAll）。
//
// EvalInFrame 末尾是 json.Unmarshal(remoteObj.Value, result)：JS 返回字符串时
// remoteObj.Value 是**带引号的 JSON 编码串**，解进 Go string 自动去引号，
// 所以这里取 raw string 再解一次是对的。
func (c *Client) observeFrame(frameID string) (*PageModel, error) {
	var raw string
	if err := c.EvalInFrame(frameID, observeJS(), &raw); err != nil {
		return nil, fmt.Errorf("observe eval: %w", err)
	}
	var m PageModel
	if err := json.Unmarshal([]byte(raw), &m); err != nil {
		return nil, fmt.Errorf("observe 契约解析失败: %w\n原始: %.300s", err, raw)
	}
	return &m, nil
}
