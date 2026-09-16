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

// frameErrorKind 是「某一帧取不到」在 obstructions 里的标记（见 observeInto）。
const frameErrorKind = "frame-error"

// frameEnumerationWait 是枚举帧树前等动态 iframe 出现的时间。
//
// 帧树本身是即时查询，但要给**运行时注入**的 iframe（追踪器、Stripe 那类内嵌
// 收银台）一点时间落到 DOM 上，否则它们连枚举这一步都进不来。取值是权衡：
// 越小越跟手，越大越不容易漏帧。用 GetFrameTreeWithEvents 的参数就是这个语义。
const frameEnumerationWait = 500 * time.Millisecond

// ObserveAll 枚举帧树、逐帧取页面模型再合并。
//
// 为什么必须逐帧：同源策略决定**单次 eval 看不见跨源帧的内容**（R3 探针实测）。
// observeJS 的穿透助手 __cdpRoots(document) 只在**当前帧**的 document 上递归，
// 跨源帧的 DOM 根本不在它的可达范围内（父帧 JS 连 iframe.contentDocument 都是
// null）。所以唯一的路子是：枚举帧 → 逐帧 Observe → 按 frame_path 合并，
// 把帧路径写进每条动作/字段的 FramePath，agent 才知道动作该发给哪一帧。
//
// ⚠️ 枚举走的是 GetFrameTreeWithEvents，**不是** GetFrameTree。2026-09-16 实测
// （原始 JSON 见 .superpowers/sdd/.../task-4-report.md 第 1 节）本机 Chrome 150：
//
//	page.getFrameTree  → 同源子帧报，**跨源子帧（OOPIF）一个都不报**
//	GetFrameTreeWithEvents  → 两种都报（DOM 穿透带出 OOPIF 的 contentDocument）
//
// 也就是说，裸 GetFrameTree 会让 ObserveAll 对**所有**跨源 iframe 视而不见 ——
// 而「看得见跨源 iframe」正是这个函数存在的全部理由。这不是取舍，是唯一解。
//
// 单帧失败的处理（见 observeInto）：主帧失败 → 整体报错；子帧失败 → 不整体失败，
// 但在 obstructions 里留一条痕迹。
func (c *Client) ObserveAll() (*PageModel, error) {
	tree, err := c.GetFrameTreeWithEvents(frameEnumerationWait)
	if err != nil {
		return nil, fmt.Errorf("取帧树失败: %w", err)
	}

	merged := &PageModel{}
	if err := c.observeInto(merged, tree, []string{mainFramePath}, true); err != nil {
		return nil, err
	}
	return merged, nil
}

// observeInto 把 ft 这一帧（及其子树）的模型并进 merged。
//
//   - path  —— 这一帧的帧路径，主帧是 ["main"]，子帧逐层追加 frameID
//   - isMain —— 是不是主帧。主帧失败整体失败，子帧失败只留痕。
func (c *Client) observeInto(merged *PageModel, ft *page.FrameTree, path []string, isMain bool) error {
	if ft == nil || ft.Frame == nil {
		return nil
	}
	frameID := string(ft.Frame.ID)

	m, err := c.Observe(frameID)
	if err != nil {
		if isMain {
			// 主帧取不到 = 这份观测整个没有意义，必须报错。
			// 不能「返回一个空模型 + nil error」—— 那是本项目的经典假成功
			// （执行器把 deferred 的 form 步骤算成过，实测 13/13）。
			return fmt.Errorf("主帧 observe 失败: %w", err)
		}
		// 子帧失败**不**整体失败：某些帧（广告、追踪、已被移除的）本来就取不到。
		// 但绝不能静默吞掉：少了一帧，和那一帧本来就空，在结果里长得一模一样。
		// 记进 obstructions —— PageModel 里唯一能承载「环境异常」的地方，
		// 且不必改契约（OptionGroup / Obstruction 没有 FramePath 字段）。selector
		// 放帧 ID，agent 能顺着去查是哪一帧。
		merged.Obstructions = append(merged.Obstructions, Obstruction{
			Kind:     frameErrorKind,
			Selector: frameID,
			Text:     firstRunes(err.Error(), 160),
		})
	} else {
		mergeFrameModel(merged, m, path)
	}

	for _, ch := range ft.ChildFrames {
		if ch == nil || ch.Frame == nil {
			continue
		}
		// 复制一份再加 —— 直接 append 到共享底层数组会让兄弟帧互相串路径。
		childPath := append(append([]string{}, path...), string(ch.Frame.ID))
		if err := c.observeInto(merged, ch, childPath, false); err != nil {
			return err
		}
	}
	return nil
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
	// OptionGroup / Obstruction 的契约里没有 FramePath（Task 2 定的 PageModel），
	// 只能原样并进来 —— 跨帧时它们的归属是**丢失**的。见 task-4-report.md 顾虑 2。
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
