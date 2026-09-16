package cmd

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"strings"
	"text/tabwriter"

	"cdp/internal"

	"github.com/spf13/cobra"
)

// observeCmd 是 agent 的主视角，也是**运行阶段唯一的回退接口**（规格 D2）。
//
// 系统有两个阶段：
//   - 生成阶段：agent 走 MCP observe（计划二）
//   - 运行阶段：产出的 py 跑在 worker 容器里，**只能调 CLI**
//
// 「selector 全挂 → 重新 observe」这条运行时回退，只有这条 CLI 走得通：
// MCP 那条门在 worker 里根本不存在。所以这个子命令的输出格式是**契约**，
// 不是好看的打印 —— py 侧按 `PageModel` 的 JSON 字段名取值（actions / fields /
// option_groups / obstructions / diagnostics），改字段名等于改契约。
//
// ⚠️ diagnostics 与 obstructions 语义不同，别在消费侧混：
//   - obstructions = 页面上真有个东西挡着（cookie 横幅），带 selector /
//     dismiss_selector，是**可以点掉的东西**
//   - diagnostics  = 这一次观测本身不完整（某帧没取到、帧枚举可能退化）
//     混用会让 py 拿 frameId 当选择器去点。
//
// 退出码约定（py 侧靠它判成败）：
//
//	0 = 拿到了模型（★ 「模型里 diagnostics 非空」仍然是 0 —— 观测跑完了，
//	    只是不完整；是否可用由调用方按 diagnostics 判断，别让 CLI 替它决定）
//	1 = 没拿到模型（连不上 Chrome / 主帧 eval 失败 / --frame-id 指了不存在的帧）
//
// 输出格式：默认 `--json`（true）—— py 侧**只该用这一种**；`--json=false`
// 是给人看的一页摘要（见 renderHuman），别拿去解析。
var observeCmd = &cobra.Command{
	Use:   "observe",
	Short: "观察页面：结构化页面模型（可动作元素 / 表单字段 / 选项组 / 遮挡物）",
	Long: `observe 是 agent 的主视角。它只给**感知**（位置、尺寸、区域、遮挡、
选择器候选与稳定性），不给判断 —— 语义由调用方推理。

跨源 iframe 会自动逐帧取再合并，每条动作带 frame_path。

输出是 PageModel 的 JSON（默认即 JSON）。diagnostics 是观测者自己的问题，
obstructions 是页面上的遮挡物，两者语义不同。`,
	RunE: runObserve,
}

func init() {
	rootCmd.AddCommand(observeCmd)
	// ⚠️ 帮助文字说**现在**的真话（Task 7 修复轮 1 的 M1 同一类问题）：人话格式
	// 早就有了（renderHuman，Task 6 修复轮 1），原先那句「便于以后加」是句空头支票，
	// 也正是「看起来能切格式、实际不说清楚」那一类。
	observeCmd.Flags().Bool("json", true, "默认输出 JSON（py 侧只该用这一种）；--json=false 输出人话摘要（给人看，别解析）")
	observeCmd.Flags().String("frame-id", "", "只观察指定帧（默认整页含子帧）")
}

func runObserve(cmd *cobra.Command, args []string) error {
	client, err := internal.NewClient(GetHost(), GetPort())
	if err != nil {
		return fmt.Errorf("failed to create client: %w", err)
	}
	defer client.Disconnect()

	frameID, _ := cmd.Flags().GetString("frame-id")
	var m *internal.PageModel
	if frameID != "" {
		// 单帧：调试用。传的必须是 **CDP frameID**，不是 frame_path 那串给人读的
		// 标记（主帧在 frame_path 里是 "main"，不是 frameID）。
		// ⚠️ 空串在这里被当成「没传」→ 走整页模式，所以**没有**「只观察主帧」
		// 这个用法；模型里每条动作的 frame_path 才是选帧的依据。
		m, err = client.Observe(frameID)
	} else {
		m, err = client.ObserveAll()
	}
	if err != nil {
		return err
	}
	jsonOut, _ := cmd.Flags().GetBool("json")
	if jsonOut {
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		return enc.Encode(m)
	}
	return renderHuman(os.Stdout, m)
}

// renderHuman 是 `--json=false` 的**人话**输出（2026-09-16 Task 6 修复轮 1）。
//
// 为什么要有它（而不是留个空开关）：这条 CLI 的存在意义之一就是**人能直接用**
// （D14/D15 的 Console 那条线上，运营要能自助看懂「AI 看清了什么」）。
// 一个看起来能切格式、实际不切的 flag，正是本项目反复栽的那类静默不做事的开关。
//
// ⚠️ 它**只做摘要，不做判断**（规格 D11）：不给「这是主 CTA」这种结论，
// 只把模型里的原始事实摆出来。要全量字段请用默认的 --json。
func renderHuman(w io.Writer, m *internal.PageModel) error {
	const maxActions = 5
	tw := tabwriter.NewWriter(w, 0, 4, 2, ' ', 0)

	if m.Title != "" {
		fmt.Fprintf(tw, "%s\n", m.Title)
	}
	fmt.Fprintf(tw, "%s\n\n", m.URL)
	fmt.Fprintf(tw, "可动作元素 %d 个\t表单字段 %d 个\t选项组 %d 组\t遮挡物 %d 个\t诊断 %d 条\n\n",
		len(m.Actions), len(m.Fields), len(m.OptionGroups), len(m.Obstructions), len(m.Diagnostics))

	// 可动作元素：正文 + 稳定性评级（判断留给调用方，这里只报事实）。
	shown := len(m.Actions)
	if shown > maxActions {
		shown = maxActions
	}
	if shown == 0 {
		fmt.Fprintf(tw, "可动作元素：（无）\n\n")
	} else {
		fmt.Fprintf(tw, "可动作元素（前 %d / 共 %d）：\n", shown, len(m.Actions))
		for i := 0; i < shown; i++ {
			a := m.Actions[i]
			fmt.Fprintf(tw, "  %d.\t[%s]\t%s\t%s\n", i+1, a.Stability, truncRunes(a.Text, 40), a.Selector)
		}
		if len(m.Actions) > shown {
			fmt.Fprintf(tw, "  …\t\t\t还剩 %d 个（用 --json 看全量）\n", len(m.Actions)-shown)
		}
		fmt.Fprintln(tw)
	}

	// 遮挡物：带 dismiss_selector 才有可操作性 —— 那是人下一步该点的东西。
	if len(m.Obstructions) == 0 {
		fmt.Fprintf(tw, "遮挡物：无\n\n")
	} else {
		fmt.Fprintf(tw, "遮挡物（页面上的东西，先关掉再操作）：\n")
		for _, o := range m.Obstructions {
			if o.DismissSelector != "" {
				fmt.Fprintf(tw, "  %s\t%s\t→ 点 %s\n", o.Kind, o.Selector, o.DismissSelector)
			} else {
				fmt.Fprintf(tw, "  %s\t%s\t(没找到关闭按钮)\n", o.Kind, o.Selector)
			}
		}
		fmt.Fprintln(tw)
	}

	// 诊断：和遮挡物**分开**摆（前者是「我没看清」，后者是「页面上有东西」）。
	if len(m.Diagnostics) == 0 {
		fmt.Fprintf(tw, "诊断：无\n")
	} else {
		fmt.Fprintf(tw, "诊断（观测者自己的问题 —— 不是页面内容）：\n")
		for _, d := range m.Diagnostics {
			fmt.Fprintf(tw, "  %s\t%s\t%s\n", d.Kind, strings.Join(d.FramePath, " > "), truncRunes(d.Detail, 80))
		}
	}
	return tw.Flush()
}

// truncRunes 把一段文本压成单行并截断 —— 摘要是给人扫一眼的，
// 动作正文里带换行会把整块输出撑散。
func truncRunes(s string, n int) string {
	s = strings.Join(strings.Fields(s), " ")
	r := []rune(s)
	if len(r) <= n {
		return s
	}
	return string(r[:n]) + "…"
}
