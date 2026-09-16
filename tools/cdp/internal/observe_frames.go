package internal

import (
	"fmt"
	"time"

	"github.com/chromedp/cdproto/page"
)

// mainFramePath 是主帧在 FramePath 里的标记。
//
// ⚠️ 它是**给人/给 agent 读的标记**，不是可以回传给 CDP 的 frameID：
// 本包对主帧的约定一律是**空串**（`EvalInFrame` / `ClickElement` / `Observe`
// 的 frameID == "" 即主帧），子帧才用真实 frameID。agent 拿帧路径回头调工具时，
// 主帧那一段要翻成空串。
const mainFramePath = "main"

// 诊断 kind。**导出**：它跨 JSON 边界到 CLI（Task 6）和 agent，改字符串等于改契约。
const (
	// DiagKindFrameError —— 某一帧观测失败（广告/追踪帧本来就取不到）。
	// 这一帧的内容**没进**模型，不是「它本来就空」。
	DiagKindFrameError = "frame-error"

	// DiagKindFrameBlind —— 帧枚举可能退化了：主帧 DOM 里的 iframe 元素数与
	// 枚举到的子帧数对不上（典型成因：DOM 穿透失败时**跨源子帧会整个消失**，
	// 而且没有任何别的声音）。取值的地方见 checkFrameCoverage。
	DiagKindFrameBlind = "frame-blind"

	// DiagKindTargetAmbiguous —— 比上面两条更重：连**页**都可能选错了。
	// 没有任何页面目标报 visible，要观测的那个 tab 是「退回第一个」猜出来的
	// （判据见 targets.go 的 pickActivePage）。
	//
	// 那两条说的是「某一帧没看清，其余部分是可信的」；这一条说的是「**整份模型
	// 可能说的是另一个页**」—— 2026-09-16 真站实测，target=_blank 点开的新标签页
	// 在可见性检查超时后就是这个状态，而 observe 照样返回一份完全合法、字段齐全、
	// 退出码 0 的**另一个页**的模型（连 diagnostics 都是空的）。
	DiagKindTargetAmbiguous = "target-ambiguous"
)

// targetDiagsFor 把「挑页面目标」的结果翻成诊断：**只有退回时才说话**。
//
// 「只在 Fallback 时说话」这个判据就是本次修正的全部内容，所以单独成一个函数：
// 两个分支都能被直测（见 targets_test.go 的 TestTargetDiagsFor*），不必为了测它
// 去伪造一个「所有页都不报 visible」的浏览器 —— 那个状态只能靠页面把主线程堵死、
// 让 checkPageActive 超时来造（cmd 的 e2e 走的就是这条路，见 observe_e2e_test.go）。
//
// ⚠️ 「不说话」那半边同样是契约：模型里的 diagnostics 每一条都该指向**真问题**，
// 常驻的警告等于没有警告（正常挑中页面时这里必须是 0 条）。
func targetDiagsFor(ch TargetChoice) []Diagnostic {
	if !ch.Fallback {
		return nil
	}
	return []Diagnostic{{
		Kind: DiagKindTargetAmbiguous,
		// detail 要**可行动**：几个候选、为什么没挑出来、挑中的是哪个（ID + URL ——
		// 只报 ID 的话人没法判断挑错没有）。
		Detail: fmt.Sprintf("页面目标共 %d 个候选，没有一个报 visible（可能都是后台页，"+
			"也可能可见性检查本身就失败了）—— 退回选中第一个：%s (%s)。"+
			"这次观测的页面可能不是你要的那一个，用 cdp targets / cdp active <id> 确认并切换",
			ch.Candidates, ch.ID, ch.URL),
		// 这是「整个 tab 选错了」，不是「某一帧没取到」：帧路径按**主帧**记 ——
		// 主帧就是 tab 的身份。⚠️ FramePath 不能留 nil：那是 JSON 里的 null，
		// 而这一格是 []string（契约上各条诊断一律是数组，见 normalizeNilLists）。
		FramePath: []string{mainFramePath},
	}}
}

// frameEnumerationWait 是 GetFrameTreeWithEvents 的等待参数。
//
// ⚠️ 说准它等在哪一步（这个函数是**先取基线、再睡、再穿透 DOM**，见 client.go
// 的 GetFrameTreeWithEvents）：所以这半时间给的是**穿透那一趟** —— 等运行时注入的
// iframe（追踪器、Stripe 那类内嵌收银台）落到 DOM 上。基线帧树那半在等待**之前**
// 就取完了，晚出现的同源子帧只能靠 DOM 穿透补进来。
// 取值是权衡：越小越跟手，越大越不容易漏帧。
const frameEnumerationWait = 500 * time.Millisecond

// ObserveAll 枚举帧树、逐帧取页面模型再合并。
//
// 为什么必须逐帧：同源策略决定**单次 eval 看不见跨源帧的内容**（R3 探针实测）。
// observeJS 的穿透助手 __cdpRoots(document) 只在**当前帧**的 document 上递归
// （它跟的是 .shadowRoot，**从不进 contentDocument**），跨源帧的 DOM 根本不在
// 它的可达范围内（父帧 JS 连 iframe.contentDocument 都是 null）。所以唯一的路子是：
// 枚举帧 → 逐帧 Observe → 按 frame_path 合并，把帧路径写进每条动作/字段的
// FramePath，agent 才知道动作该发给哪一帧。
//
// ⚠️ **顺带记牢**：主帧的单帧观测看不见**任何** iframe 的内容 —— 同源也一样
// （穿透不进 contentDocument）。所以「主帧看不见子帧东西」**不是**跨源判据，
// 别拿它当跨源报警器（Task 4 修复轮 1 修掉的就是这个）。
//
// ⚠️ 枚举走的是 GetFrameTreeWithEvents，**不是** GetFrameTree。2026-09-16 实测
// （原始协议 JSON 与三次交叉验证见 testdata/README.md「帧枚举」一节）本机 Chrome 150：
//
//	page.getFrameTree      → 同源子帧报，**跨源子帧（OOPIF）一个都不报**
//	GetFrameTreeWithEvents → 两种都报（DOM 穿透带出 OOPIF 的 contentDocument）
//
// 裸 GetFrameTree 会让 ObserveAll 对**所有**跨源 iframe 视而不见 —— 而「看得见
// 跨源 iframe」正是这个函数存在的全部理由。**用现有助手**（HEAD 里已有的
// GetFrameTreeWithEvents）这是唯一解；想直接枚举 OOPIF 也行（Target.getTargets
// 能列出 type=iframe 的 target，且 targetId == frameId —— 探针实测过），但那要
// 另起 browser WS 会话，而且同源子帧不在那份列表里，**照样得并集**。
//
// 单帧失败的处理（见 observeInto）：主帧失败 → 整体报错；子帧失败 → 不整体失败，
// 但在 diagnostics 里留一条痕迹。
func (c *Client) ObserveAll() (*PageModel, error) {
	tree, err := c.GetFrameTreeWithEvents(frameEnumerationWait)
	if err != nil {
		return nil, fmt.Errorf("取帧树失败: %w", err)
	}

	merged := &PageModel{}
	// 同一个帧只观测一次。帧树 ∪ DOM 穿透这条并集在 client.go 里去过一次重
	// （collectExistingFrameIDs），但那只覆盖「合并那一刻的两路来源」：观测这一层
	// 自己不设防 —— 任何漏网的重复都会让这一帧的 actions / fields / ShadowRoots /
	// PageText 全部**翻倍**。
	// （2026-09-16 实测：两级嵌套夹具下**没有**观测到重复，所以这道防线是**防御性**的，
	//  不是已经复现过的缺陷 —— 审查意见 ④ 点名要它。）
	seen := map[string]bool{}
	if err := c.observeInto(merged, tree, []string{mainFramePath}, true, seen); err != nil {
		return nil, err
	}
	// 「挑中的页是猜的」这条在**合并之后补一条**，而且只补一条：它说的是「整个
	// tab 可能选错了」，跟这次观测有几帧无关。逐帧那条路（observeFrame）刻意不发它
	// —— 在这条链上 N 帧就是 N 条，消费者会以为有 N 个问题（见 Client.targetDiags）。
	merged.Diagnostics = append(merged.Diagnostics, c.targetDiags...)
	// 合并这条路**尤其**要归一化：merged 是 `&PageModel{}` 起的（五个切片全是 nil），
	// 某一类一个元素都没并进来时 append 不改变 nil —— 实测 base.html 的
	// option_groups / diagnostics 就是这样编成 null 的（见 normalizeNilLists）。
	return normalizeNilLists(merged), nil
}

// observeInto 把 ft 这一帧（及其子树）的模型并进 merged。
//
//   - path   —— 这一帧的帧路径，主帧是 ["main"]，子帧逐层追加 frameID
//   - isMain —— 是不是主帧。主帧失败整体失败，子帧失败只留痕。
//   - seen   —— 已观测过的 frameID，防重复（见 ObserveAll）
func (c *Client) observeInto(merged *PageModel, ft *page.FrameTree, path []string, isMain bool, seen map[string]bool) error {
	if ft == nil || ft.Frame == nil {
		return nil
	}
	id := string(ft.Frame.ID)
	if seen[id] {
		return nil
	}
	seen[id] = true

	frameID := id
	if isMain {
		// 主帧一律传空串（本包的约定，见 mainFramePath）。传真实 frameID 会走
		// CreateIsolatedWorld：在页面里常驻一个 isolated world，并往 c.frameCtxs
		// 塞一条**导航后不会失效**的缓存项 —— 同一个 frameID 跨导航复用，
		// 指向的是上一次的文档。
		frameID = ""
	}

	m, err := c.observeFrame(frameID)
	if err != nil {
		if isMain {
			// 主帧取不到 = 这份观测整个没有意义，必须报错。
			// 不能「返回一个空模型 + nil error」—— 那是本项目的经典假成功
			// （执行器把 deferred 的 form 步骤算成过，实测 13/13）。
			return fmt.Errorf("主帧 observe 失败: %w", err)
		}
		// 子帧失败**不**整体失败：某些帧（广告、追踪、已被移除的）本来就取不到。
		// 但绝不能静默吞掉：少了一帧，和那一帧本来就空，在结果里长得一模一样。
		// 记进 **diagnostics**（不是 obstructions）——后者是「页面上的东西」
		// （cookie 横幅那类，携带 selector/dismiss_selector 语义）；帧取不到是
		// 「观测者自己的问题」。混在一起的话，忽略 kind 的消费者会拿 frameId 去点。
		merged.Diagnostics = append(merged.Diagnostics, Diagnostic{
			Kind:      DiagKindFrameError,
			Detail:    firstRunes(err.Error(), 160),
			FramePath: path,
		})
	} else {
		mergeFrameModel(merged, m, path)
		// 这一帧的 DOM 我看得见了，顺手对一次账：它里面有几个 iframe 元素，
		// 就该有几个子帧（见 checkFrameCoverage）。
		c.checkFrameCoverage(merged, frameID, ft, path)
	}

	for _, ch := range ft.ChildFrames {
		if ch == nil || ch.Frame == nil {
			continue
		}
		// 复制一份再加 —— 直接 append 到共享底层数组会让兄弟帧互相串路径。
		childPath := append(append([]string{}, path...), string(ch.Frame.ID))
		if err := c.observeInto(merged, ch, childPath, false, seen); err != nil {
			return err
		}
	}
	return nil
}

// checkFrameCoverage 是「观测者是不是瞎了」的守卫：**这一帧** DOM 里的 iframe
// **元素**数与枚举到的**子帧**数本该一一对应（一个 iframe 元素对应一帧）。
// 对不上就说明这次枚举很可能漏了帧，且没有任何别的声音。
//
// 为什么这条线索可靠：iframe **元素**在它自己那一帧的文档里，与子帧是什么源无关
// （跨源也一样看得见），所以它**不依赖子帧可达性**，是独立的一路。
//
// ⚠️ 为什么是**逐帧**对账，不是只在主帧上对一次：盲区是**逐父帧**成立的，
// 2026-09-16 实测（探针，见 testdata/README.md「帧枚举」一节）：
//
//	main(127.0.0.1) → OOPIF(localhost, 带一层同源 iframe)
//	GetFrameTreeWithEvents 只报 2 帧 —— 能看见 OOPIF **自己**（它的 <iframe> 元素
//	在父文档里，父文档的 DOM 穿透看得见），但**看不进 OOPIF 里面**（跨进程没有
//	contentDocument），于是 OOPIF 的**子孙帧整个消失**。只在主帧对账正好漏掉这种。
//	（那条子孙帧其实在 OOPIF target 自己的 Page.getFrameTree 里，机制已实测确认 ——
//	 要真收进来得逐 OOPIF target 取树，本任务没做，原始证据见 testdata/README.md。）
//
// 代价：**每帧**多一次 eval。读操作，允许手写 JS —— 但穿透助手要自己套
// （withPierce 注入 __cdpQA），否则 shadow DOM 里的 iframe 会数漏。
func (c *Client) checkFrameCoverage(merged *PageModel, frameID string, ft *page.FrameTree, path []string) {
	if ft == nil || ft.Frame == nil {
		return
	}
	var n int
	if err := c.EvalInFrame(frameID, withPierce(`__cdpQA('iframe').length`), &n); err != nil {
		merged.Diagnostics = append(merged.Diagnostics, Diagnostic{
			Kind:      DiagKindFrameBlind,
			Detail:    "iframe 计数求值失败，无法判断帧枚举完不完整: " + firstRunes(err.Error(), 120),
			FramePath: path,
		})
		return
	}
	if n != len(ft.ChildFrames) {
		merged.Diagnostics = append(merged.Diagnostics, Diagnostic{
			Kind: DiagKindFrameBlind,
			Detail: fmt.Sprintf("这一帧的 DOM 里有 %d 个 iframe 元素，帧树只枚举到 %d 个子帧 —— "+
				"帧枚举可能不完整（DOM 穿透失败、或子帧在跨源帧里面时，子帧会整个消失）",
				n, len(ft.ChildFrames)),
			FramePath: path,
		})
	}
}

// mergeFrameModel 把单帧模型 m 并进 merged，并把 path 盖到每条动作/字段上。
//
// URL / Title 取**第一个**取到的帧（帧树先序遍历里就是主帧）：agent 要的是
// 「这个 tab 现在停在哪」。
func mergeFrameModel(merged *PageModel, m *PageModel, path []string) {
	if merged.URL == "" {
		merged.URL, merged.Title = m.URL, m.Title
	}
	merged.ShadowRoots += m.ShadowRoots
	// 逐帧文本之间补一个空格，不要前导空格（空模型帧也不会污染出 "  xxx"）
	if m.PageText != "" {
		if merged.PageText == "" {
			merged.PageText = m.PageText
		} else {
			merged.PageText += " " + m.PageText
		}
	}
	for _, a := range m.Actions {
		a.FramePath = path // 值拷贝，改的是副本
		merged.Actions = append(merged.Actions, a)
	}
	for _, f := range m.Fields {
		f.FramePath = path
		merged.Fields = append(merged.Fields, f)
	}
	// 单帧自己的 diagnostics 也并进来（目前单帧 Observe 不产出，留着是契约上的并集语义）
	merged.Diagnostics = append(merged.Diagnostics, m.Diagnostics...)
	// OptionGroup / Obstruction 的契约里没有 FramePath（Task 2 定的 PageModel），
	// 只能原样并进来 —— 跨帧时它们的归属是**丢失**的（报告顾虑 2）。
	merged.OptionGroups = append(merged.OptionGroups, m.OptionGroups...)
	merged.Obstructions = append(merged.Obstructions, m.Obstructions...)
}

// firstRunes 截前 n 个字符（按 rune 截，不切碎 UTF-8）。
func firstRunes(s string, n int) string {
	r := []rune(s)
	if len(r) <= n {
		return s
	}
	return string(r[:n]) + "…"
}
