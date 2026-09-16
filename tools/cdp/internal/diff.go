package internal

import (
	"sort"
	"strings"
)

// Diff 回答一个问题：**「刚才那一下有没有推进」**（规格 §4.2 的工具清单、§4.6 的判据）。
//
// 它是 py 里**分支与重试的唯一依据**：Actionable 就继续往下走，不 Actionable 就换策略。
// 两个方向判错的代价都很实在，而且都**不报错**：
//
//	误判有进展 → 原地打转直到耗尽轮数（日志上一切正常，只是永远走不到终点）
//	误判没进展 → 明明这一步走对了却重试（重复提交是最坏的一种）
//
// 字段名是契约（计划 Task 7 钉的）：py 侧按 JSON 键取值，改键名等于改契约。
type Diff struct {
	URLChanged  bool `json:"url_changed"`
	TextChanged bool `json:"text_changed"`
	// Appeared / Disappeared 装的是**选择器**（给 py 当抓手/当线索用），
	// 但**判断身份用的不是它** —— 见下面的 identityKey 说明。
	//
	// ⚠️ 空列表一律是 `[]`，不是 `null`（与 PageModel 同一条约定：py 侧
	// `for s in diff["appeared"]` 撞上 null 就是 TypeError）。
	//
	// ⚠️ 同一个选择器**可能同时出现在两边**，这不是 bug：身份变了而选择器没变
	// （最典型：按钮只是被改了文案 —— Continue → Confirm and continue，位置选择器
	// `#go` 两边一样，但身份键不一样）。此时 `appeared=["#go"] disappeared=["#go"]`
	// 说的是「叫 #go 的那个『Continue』没了，叫 #go 的那个『Confirm and continue』
	// 来了」—— 事实如此。别为了让列表好看而把两边都删掉：选择器是**位置**，
	// 位置选择器完全可能被新元素复用，抹掉那一条就等于抹掉一次真实的替换。
	Appeared    []string `json:"appeared"`
	Disappeared []string `json:"disappeared"`
	// Actionable = 刚才那一下有没有推进。
	//
	// ⚠️ **能力边界（别拿它判「填/选/勾」）**：PageModel 里**没有字段值**
	// （Field 只有 Label/Type/Placeholder），所以「值填进去了没有 / 勾上了没有 /
	// 只是高亮了一下」这类步骤，diff 原理上判不了 —— 它判的是**导航与组成变化**
	// （URL、可见正文、元素集合），不是**控件状态**。
	// 在那类步骤上 Actionable 会是 false（页面组成没变）→ py 会重试。
	// 真正的修法是给模型加字段值（契约变更，记为 R20，归计划二）。
	Actionable bool `json:"actionable"`
	// DiagnosticsBefore / DiagnosticsAfter 是两次观测各自的诊断条数
	// （「这一次观测本身不完整」—— 某帧没取到、帧枚举可能退化，见 Diagnostic）。
	//
	// ⚠️ 为什么要有这两个数：**观测不全与页面变空在模型里长得一模一样**。
	// 某一帧加载失败 → 那一帧的元素从模型里消失 → appeared/disappeared 非空
	// → Actionable=true —— 一次「我没看清」被读成一次「有进展」，正是本文件
	// 最忌讳的那类错。数字在这儿，调用方才能自己判断要不要信这一次差分。
	//
	// ⚠️ 它们**不参与** Actionable 的计算，这是有意的（与 observe 那条
	// 「diagnostics 非空仍然是退出码 0」同一个道理：别让工具替调用方下结论）：
	// 广告/追踪帧失败在真站上是**常态**，把它算成「没推进」会让那些页面
	// **永远**报不出进展 —— 那是另一个方向的坑，而且更常见。
	DiagnosticsBefore int `json:"diagnostics_before"`
	DiagnosticsAfter  int `json:"diagnostics_after"`
}

// normText 把一段文本归一成「看内容」的形态：Unicode 空白（含 NBSP）压成单空格、两端剪掉。
//
// ⚠️ 用 strings.Fields 而不是 Go 正则的 `\s`：Go 的 `\s` **只认 ASCII 空白**，
// 抓不到 NBSP(U+00A0)。而 `&nbsp;` 在真页面里遍地都是 —— 拿 `\s` 归一的话，
// 「Submit」与「Submit&nbsp;」会被判成正文变化，于是**每一轮都误报「有推进」**。
// （observeJS 侧用的是 JS 的 `\s`，那个**含** NBSP，所以真模型上这条很少现形；
// 但差分的输入不止 observeJS 一家 —— 快照文件、测试构造的模型都会进来。）
//
// 注意这里**只归一空白**，不做「忽略数字/忽略大小写」这类聪明事：
// 「Step 1 of 3」→「Step 2 of 3」恰恰是最典型的一次推进，抹掉数字等于把真进展抹掉。
func normText(s string) string { return strings.Join(strings.Fields(s), " ") }

// identityKeySep 是身份键各段之间的分隔符。
//
// 用 NUL 而不是 `\x1f`/`|`：DOM 文本里**不可能**出现 NUL（HTML 解析器会把它换成
// U+FFFD），所以它不会和内容撞上。撞上的后果不是崩溃而是**静默判等** —— 正好是
// 本任务最怕的那类错，所以分隔符要挑一个不可能出现在内容里的。
const identityKeySep = "\x00"

// identity 是一条元素的「身份」：**它是什么**，而不是**怎么找到它**。
type identity struct {
	key      string // 内容签名（见 identities）
	selector string // 报给调用方的抓手（选择器）——**不参与** key
}

// identities 把一次观测摊成「身份多重集」。
//
// ★ 这是本文件的核心设计，也是 Task 7 存在的理由 ★
//
// 判据**不能**长在选择器上。T5 已经证明（见 internal/testdata/README.md 的 RAND 一节）：
// 框架生成的 hash class **一刷就变** —— 一个 SPA 页面**什么都没变**、只是重渲染了一遍，
// 每个元素的 selector 就全换了名字：
//
//	#schedule-now        →  button.css-9x8y7z6
//	#go                  →  body:nth-of-type(1) > main:nth-of-type(1) > button:nth-of-type(2)
//
// 拿选择器集合算 Appeared/Disappeared，这一下会报「一堆出现、一堆消失」
// → Actionable=true（**谎报有进展**）→ py 原地打转直到耗尽轮数。
// 这正是本项目最忌讳的那类错：不报错、看起来一切正常、恰好落在核心判据上。
//
// 所以身份取的是**内容与角色**（控制器裁定 ② 点名的三个稳定信号）：
//
//	动作   → 正文 Text + Role + regionIdentity(Region)
//	字段   → Type + Placeholder（⚠️ **不含** Label，也**不含** Hint）
//	选项组 → Role + 选项文本（排序后）（⚠️ **不含** Scope）
//
// 四处 ⚠️ 都是同一个理由：那些字段里有一部分是**派生**出来的，会随重渲染变。
//   - Field.Hint        = observeJS 填的 `el.name || el.id` —— 改名就变
//   - OptionGroup.Scope = `candidates(g)[0]` —— 就是选择器
//   - Field.Label       —— **可能**由 `label[for=id]` 取来（observeJS 的三级取法
//     里它排最后，但仍是其一）：重渲染把 input 重新挂载成新 id，这条关联就断，
//     标签凭空变成空串 → 身份翻转 → 内容没变却报「有进展」。修复轮 1 的 e2e
//     在真浏览器上就是这么红的（打断 for/id 配对，见 TestDiffCommandImmuneToSelectorRename）。
//   - Action.Region     —— 'hero' 是**按祖先 className 判的**（/hero|banner|jumbotron/）。
//     修复轮 1 已让 observeJS 的 region() **优先扫 landmark 标签名**
//     （header/nav/footer/aside/main 与 role=dialog），class 派生只作兜底；
//     但「无 landmark 祖先」的页面上那条兜底仍会命中，所以身份键只取**抗改名的那一半**
//     （见 regionIdentity）。
//
// 把它们放进身份键，等于把「改名敏感」从后门放回来。
//
// 反过来，**不选** BBox / AboveFold / RelativeSize / OccludedBy / Stability /
// Alternates / NearbyText：这些要么是位置（滚动一下就变），要么是选择器的派生
// （改名就变），要么是观测噪声。放进身份键都会变成误报源。
//
// ⚠️ 已知边界（有意接受，别当成完备）：
//   - FramePath **不在**身份键里。理由：帧 ID 在一次重渲染里可能整个换掉
//     （iframe 被重建 → 新 frameID → 所有键都变 → 又回到谎报有进展那条路），
//     而改名免疫是本函数的**头号**要求。代价：同一个「Next」从主帧挪进 iframe
//     （内容与角色都没变）算作没变化 —— 窄，且不改变「页面内容变没变」这个答案。
//   - Obstructions 不在多重集里：遮挡物的身份只能靠 Kind+Text，而它被点掉这件事
//     会改正文（横幅文字从 page_text 里消失）→ 由 TextChanged 兜住。
//   - Honeypots 也不在多重集里，而且**有意**如此：它们是已被排除的陷阱
//     （见 Honeypot），站点往页面里加一条、去掉一条蜜罐都不是「推进」——
//     把它们算进 appeared/disappeared 只会让 Actionable 谎报。⚠️ 别为了
//     「对称」加回来。
//   - **字段没有 Label**：两个「Label/Placeholder 都为空、Type 相同」的输入框
//     （裸 `<input type=text>` 那种）在身份上会**塌成一个** —— 加一个/减一个
//     仍然看得出来（多重集计数变了），但它们之间互换看不出来。
//     这是为抗改名付的代价，且与下面 regionIdentity 是同一笔账：
//     一个**派生**字段宁可不要，也不能让它谎报「有进展」。
func identities(m *PageModel) []identity {
	if m == nil {
		return nil
	}
	out := make([]identity, 0, len(m.Actions)+len(m.Fields)+len(m.OptionGroups))
	key := func(parts ...string) string { return strings.Join(parts, identityKeySep) }

	for _, a := range m.Actions {
		out = append(out, identity{
			key:      key("action", normText(a.Text), a.Role, regionIdentity(a.Region)),
			selector: a.Selector,
		})
	}
	for _, f := range m.Fields {
		out = append(out, identity{
			key:      key("field", f.Type, normText(f.Placeholder)),
			selector: f.Selector,
		})
	}
	for _, g := range m.OptionGroups {
		// 排序：选项**顺序**变了不算内容变了（下拉框重排不是一次推进）。
		opts := make([]string, 0, len(g.Options))
		for _, o := range g.Options {
			opts = append(opts, normText(o))
		}
		sort.Strings(opts)
		// ⚠️ 选项作为**独立的键段**拼进去，不用任何可打印分隔符：
		// 早先用 `strings.Join(opts, "|")`，于是 `["a|b"]`（一个选项，文本里带竖线）
		// 与 `["a","b"]`（两个选项）会**塌成同一个键** → 静默漏报（修复轮 1 的 M2）。
		parts := append([]string{"group", g.Role}, opts...)
		out = append(out, identity{
			key:      key(parts...),
			selector: g.Scope,
		})
	}
	return out
}

// regionIdentity 把 Action.Region 压成「抗改名的那一半」。
//
// 为什么不能直接用 Region：observeJS 的 region() 有两个来源（修复轮 1 后的顺序）：
//
//	① landmark 标签名 header/nav/footer/aside/main + role=dialog  —— **结构**，重渲染不会换
//	② class 派生 /hero|banner|jumbotron/ 的 'hero'                   —— **生成物**，一刷就变
//	（都没有 → 'body'）
//
// ②在「没有 landmark 祖先」的页面上会命中（fixture base.html 的 section.hero 就是），
// 而它一改名就翻 → 内容没变却报「有进展」。所以身份键**只认①**：
// 取不到①时一律归成一个空桶（'hero' 与 'body' 与任何新出现的派生值同桶）。
//
// 代价（有意接受）：同一个元素在 landmark 与 hero/body 之间搬家看不见。
// 收益：**唯一**冒充得了「有进展」的那条路被掐掉了 —— 两边不对称，取收益那边。
//
// 这张名单必须与 observeJS 的 region() 保持同步：那里新增「派生来源」时，
// 这里要一起收（否则新值会被当成①、静默变回改名敏感）。
func regionIdentity(region string) string {
	switch region {
	case "header", "nav", "footer", "aside", "main", "dialog":
		return region
	default:
		return ""
	}
}

// multisetDiff 按身份做**多重集**差：after 里多出来的选择器、before 里少掉的选择器。
//
// 多重集（而不是集合）是必要的：页面上两个一模一样的「Add」按钮里多出一个，是变化；
// 拿集合算的话两边都是 {"Add"}，那次变化就消失了。
//
// 输出顺序 = 各自的页面顺序（after 序 / before 序），所以对同一对输入是确定的；
// 重复元素被匹配掉哪一个（第一个还是第二个）是任意的，但同样确定
// —— 反正它们的身份和选择器都不可区分。
//
// ⚠️ 报出来的**选择器列表**去重，但上面的计数**不去重**（两边是不同的东西，别搞混）：
// 同一个 DOM 元素会被观察**两条通道**各收一次 —— observeJS 的可动作元素选择器里
// 含 `input,select,textarea`，而表单字段收集器**也**收这一批。于是「报价卡片换掉」
// 这种改动里，`#zip` 会以「动作」和「字段」两个身份各消失一次。
// 计数上它们确实是两条身份（不该合并，否则一个 input 变成纯字段就会被漏掉），
// 但**报给调用方的抓手**是同一个字符串：`appeared=["#plan","#confirm","#plan"]`
// 看着像 bug、读起来是噪声，而两条身份的区别在这个 `[]string` 里本来就表达不出来。
// 所以：先按多重集算准，再对**输出列表**按首次出现顺序去重。
func multisetDiff(before, after []identity) (appeared, disappeared []string) {
	appeared, disappeared = []string{}, []string{}
	remaining := make(map[string]int, len(before))
	for _, b := range before {
		remaining[b.key]++
	}
	for _, a := range after {
		if remaining[a.key] > 0 {
			remaining[a.key]--
			continue
		}
		appeared = append(appeared, a.selector)
	}
	// 剩下的就是 before 里有、after 里没有的（按 before 的顺序报，带原始选择器）。
	left := make(map[string]int, len(remaining))
	for k, n := range remaining {
		if n > 0 {
			left[k] = n
		}
	}
	for _, b := range before {
		if left[b.key] > 0 {
			left[b.key]--
			disappeared = append(disappeared, b.selector)
		}
	}
	return dedupeKeepOrder(appeared), dedupeKeepOrder(disappeared)
}

// dedupeKeepOrder 去掉重复的选择器，保留首次出现的顺序。
func dedupeKeepOrder(sels []string) []string {
	seen := make(map[string]bool, len(sels))
	out := make([]string, 0, len(sels))
	for _, s := range sels {
		if seen[s] {
			continue
		}
		seen[s] = true
		out = append(out, s)
	}
	return out
}

// DiffModels 比较动作前后两次观测，回答「刚才那一下有没有推进」。
//
// Actionable 的三个信号，**没有一个长在选择器上**：
//
//	URLChanged      —— location.href 变了（SPA 的 pushState 也算）
//	TextChanged     —— 可见正文变了（归一空白后；见 normText）
//	多重集差非空    —— 身份上真有元素出现/消失（见 identities）
//
// 三者取或：任何一条成立就算推进。三条都是内容/位置级的信号，所以
// **重渲染（选择器全改名、内容没变）在三条上全都不成立** → Actionable=false。
//
// ⚠️ 三条已知边界，写在代码里免得被当成「已完备」：
//  1. PageText 在 observeJS 里被截到 600 字符（`page_text: pageText.slice(0, 600)`），
//     所以正文的变化只在前 600 字符内可见。600 字符之后的变化要靠多重集那条腿。
//  2. 观测本身不完整（某帧没取到）时，diff 只能照模型说话 —— 一个「取不到的帧」
//     看起来和「那一帧的内容消失了」一模一样。**所以要报 diagnostics 的条数**
//     （DiagnosticsBefore/After，修复轮 1 的 I3）：这条边界没法在 diff 里消掉，
//     但至少能让调用方看见「这次的 after 观测是不全的」。
//  3. **判不了「填/选/勾」**：模型里没有字段值（见 Diff.Actionable 的说明）。
func DiffModels(before, after *PageModel) Diff {
	appeared, disappeared := multisetDiff(identities(before), identities(after))

	var bURL, aURL, bText, aText string
	d := Diff{
		Appeared:    appeared,
		Disappeared: disappeared,
	}
	if before != nil {
		bURL, bText = before.URL, before.PageText
		d.DiagnosticsBefore = len(before.Diagnostics)
	}
	if after != nil {
		aURL, aText = after.URL, after.PageText
		d.DiagnosticsAfter = len(after.Diagnostics)
	}

	d.URLChanged = strings.TrimSpace(bURL) != strings.TrimSpace(aURL)
	d.TextChanged = normText(bText) != normText(aText)
	d.Actionable = d.URLChanged || d.TextChanged || len(appeared) > 0 || len(disappeared) > 0
	return d
}
