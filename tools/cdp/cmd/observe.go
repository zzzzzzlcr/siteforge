package cmd

import (
	"encoding/json"
	"fmt"
	"os"

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
	observeCmd.Flags().Bool("json", true, "输出 JSON（默认；保留开关便于以后加人类可读格式）")
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
	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	return enc.Encode(m)
}
