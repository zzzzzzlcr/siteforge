package mcp

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"

	"cdp/internal"
)

// 七个工具的实现。每一个都是 CLI 同名子命令的**同一段内核逻辑**（不是调用 CLI）：
// 参数怎么解释、什么时候报错、返回什么，与 `cdp observe` / `cdp diff` / ... 逐条对齐。
// 两个门行为不一致的话，「CLI 上能跑、MCP 上跑不了」这种差异会变成 agent 的暗礁。

// handleObserve 是 agent 的主视角（规格 D2）。
//
// ⚠️ 它**只给感知**（D11）：原样返回内核的 PageModel，不做任何筛选、排序、打分。
// 在这里加一句「把最像主 CTA 的排前面」，就是把判断烘进观测 —— 那正是 `_trace_to_json`
// 那类病，也正是这套系统要逃出来的坑。
func handleObserve(_ context.Context, b Browser, args map[string]any) (any, error) {
	frameID := strArg(args, "frame_id")

	var (
		m   *internal.PageModel
		err error
	)
	if frameID != "" {
		m, err = b.Observe(frameID)
	} else {
		m, err = b.ObserveAll()
	}
	if err != nil {
		return nil, err
	}

	// 与 CLI 的 --expect-url 同一道主动闸：拿错页时**不返回模型**。
	// 一份「合法但说的是另一个页」的模型比没有模型更坏 —— agent 会照着它推理，
	// 而且每一句话都自洽（计划一真站上踩过）。
	if expect := strArg(args, "expect_url"); expect != "" && !strings.Contains(m.URL, expect) {
		return nil, fmt.Errorf("观察到的页面不是预期的那个：期望 URL 里含 %q，实际是 %q"+
			"（常见成因：target=_blank 开的新标签页、SPA 还没跳转完、或这个窗口停在别的站上）。"+
			"模型已丢弃，没有返回", expect, m.URL)
	}
	return m, nil
}

func handleDiff(_ context.Context, b Browser, args map[string]any) (any, error) {
	before, err := snapshotBefore(args["before"])
	if err != nil {
		return nil, err
	}

	frameID := strArg(args, "frame_id")
	var after *internal.PageModel
	if frameID != "" {
		after, err = b.Observe(frameID)
	} else {
		after, err = b.ObserveAll()
	}
	if err != nil {
		return nil, err
	}

	// 与 CLI 同一段内核：判据只有一份（internal.DiffModels）。
	return internal.DiffModels(before, after), nil
}

// snapshotBefore 把 MCP 传过来的 before（一个 JSON 对象）解成 PageModel，
// 并挡掉「根本不是一份快照」的输入 —— 与 `cdp diff --before` 的 loadPageModel
// 同一条判据（那边多管一件「文件在哪」的事，所以这段没法直接共用；
// 改判据时**两个门一起改**）。
//
// 为什么非挡不可：`{}` / navi 的输出 / 任何别的 JSON 都能解析成一份**全零**
// PageModel，拿它对着一页活页面比 → 每个元素都「新出现」→ actionable=true。
// 「before 给错了」于是被读成一次「有进展」，而且完全不报错 —— 两个方向都错得贵。
func snapshotBefore(raw any) (*internal.PageModel, error) {
	if raw == nil {
		return nil, fmt.Errorf("diff 需要 before（动作前那份 observe 的结果），现在没给")
	}
	b, err := json.Marshal(raw)
	if err != nil {
		return nil, fmt.Errorf("diff 的 before 没法重新编码: %w", err)
	}
	var m internal.PageModel
	if err := json.Unmarshal(b, &m); err != nil {
		return nil, fmt.Errorf("diff 的 before 不是一份 observe 模型: %w"+
			"（要把上一次 observe 的结果**整个对象**原样传回来）", err)
	}
	if m.URL == "" && len(m.Actions)+len(m.Fields)+len(m.OptionGroups) == 0 {
		return nil, fmt.Errorf("diff 的 before 里没有 URL 也没有任何元素 —— 这不是一份 observe 结果"+
			"（{} / 别的 JSON 都能解析成功，但拿来比会把整页元素误报成「新出现」）。"+
			"把上一次 observe 返回的对象整个传回来")
	}
	return &m, nil
}

func handleScreenshot(_ context.Context, b Browser, _ map[string]any) (any, error) {
	return b.Screenshot()
}

// handleClick 走的是**严格**那一版内核调用（ClickElementStrict）。
//
// 门与 CLI 的默认在这里**故意不一样**：CLI 的默认必须宽松（57 个生产脚本的
// 行为不能动），而 agent 是那个必须被逼着说准的调用方 —— 它的歧义选择器会静默
// 点到 Back（实测：漏斗倒退），它的禁用目标会静默空点（实测：exit 0 + 像样的坐标，
// 页面纹丝不动）。两种失败都长得像成功，所以这道门选择当场报错。
func handleClick(_ context.Context, b Browser, args map[string]any) (any, error) {
	selector := strArg(args, "selector")
	result, err := b.ClickElementStrict(selector, strArg(args, "frame_id"), boolArg(args, "track"))
	if err != nil {
		return nil, err
	}
	return result, nil
}

func handleForm(_ context.Context, b Browser, args map[string]any) (any, error) {
	selector := strArg(args, "selector")
	frameID := strArg(args, "frame_id")
	track := boolArg(args, "track")

	// 分派与 CLI 的 runForm 同序（value → check → select）；「只给一个」这条
	// 由 formMode（Extra）在**调用之前**就挡掉了，所以这里不会有「多给的那个被
	// 静默忽略」的情况。
	var err error
	switch {
	case args["value"] != nil:
		err = b.FillText(selector, strArg(args, "value"), frameID, track)
	case args["check"] != nil:
		err = b.CheckElement(selector, boolArg(args, "check"), frameID, track)
	default:
		err = b.SelectOption(selector, strArg(args, "select"), frameID, track)
	}
	if err != nil {
		return nil, err
	}
	return performed(), nil
}

// handleScroll 抄的是 CLI 的 runScroll：跨源 iframe 里的元素**够不着**
// （选择器是外层文档的坐标系），得先用 frame_id 换出 iframe 元素的选择器，
// 滚它，再滚里面那个元素。
//
// ⚠️ 漏掉第一步不会报错 —— 滚动只是没动。而「没动」在下一次 observe 里
// 与「这一页本来就不需要滚」长得一模一样。
func handleScroll(_ context.Context, b Browser, args map[string]any) (any, error) {
	selector := strArg(args, "selector")
	frameID := strArg(args, "frame_id")
	track := boolArg(args, "track")

	scrollSelector := selector
	if frameID != "" {
		iframeSel, err := b.ResolveIframeSelector(frameID)
		if err != nil {
			return nil, err
		}
		scrollSelector = iframeSel
	}
	if err := b.ScrollToElement(scrollSelector, track); err != nil {
		return nil, err
	}
	if err := b.ScrollIntoView(selector, frameID); err != nil {
		return nil, err
	}
	return performed(), nil
}

// handleGoto 只回「落到哪了」，不把整棵 frame tree 摆出来（CLI 的 navi 会摆，
// 那是给 py 用的）。agent 要看页面结构有 observe —— 这里塞一棵树只是把
// 同一份信息换个更难读的形状再说一遍。
func handleGoto(_ context.Context, b Browser, args map[string]any) (any, error) {
	url := strArg(args, "url")
	tree, err := b.Navigate(url, strArg(args, "frame_id"))
	if err != nil {
		return nil, err
	}
	res := map[string]any{"url": url, "frame_id": ""}
	if tree != nil && tree.Frame != nil {
		res["url"] = tree.Frame.URL
		res["frame_id"] = string(tree.Frame.ID)
	}
	return res, nil
}

// performed 是「写类」动作（click/form/scroll）的返回。
//
// ⚠️ 它只说**内核调用没报错**，不说「页面推进了」—— 那是 diff 的活。
// 把它读成「这一步成功了」，就是拿一次动作的回执当结果（本项目吃过这个亏）。
func performed() map[string]any {
	return map[string]any{
		"ok":   true,
		"note": "动作已下发（没有报错）。它有没有推进页面，用 diff 比一比才知道 —— 别把这条读成成功。",
	}
}
