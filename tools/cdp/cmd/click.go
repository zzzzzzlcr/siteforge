package cmd

import (
	"encoding/json"
	"fmt"
	"os"

	"cdp/internal"

	"github.com/spf13/cobra"
)

var clickCmd = &cobra.Command{
	Use:   "click [selector]",
	Short: "Click element with human-like gesture",
	Args:  cobra.MaximumNArgs(1),
	RunE:  runClick,
}

func init() {
	rootCmd.AddCommand(clickCmd)
	clickCmd.Flags().String("selector", "", "CSS selector")
	clickCmd.Flags().String("frame-id", "", "Frame ID (optional)")
	clickCmd.Flags().Bool("track", false, "Enable scroll track visualization")
	// --strict 默认 **false**（生产脚本的行为一字不改），MCP 那道门默认走严格那一版。
	clickCmd.Flags().Bool("strict", false,
		"歧义选择器（命中多个）或禁用的目标**当场失败**，而不是静默点第一个 / 静默空点。"+
			"agent 那道门默认是开的；生产脚本的默认路径不开")
}

func resolveSelector(cmd *cobra.Command, args []string) (string, error) {
	selector, _ := cmd.Flags().GetString("selector")
	if selector == "" {
		if len(args) == 0 {
			return "", fmt.Errorf("selector is required")
		}
		selector = args[0]
	}
	return selector, nil
}

func runClick(cmd *cobra.Command, args []string) error {
	selector, err := resolveSelector(cmd, args)
	if err != nil {
		return err
	}
	frameID, _ := cmd.Flags().GetString("frame-id")
	track, _ := cmd.Flags().GetBool("track")
	strict, _ := cmd.Flags().GetBool("strict")

	client, err := internal.NewClient(GetHost(), GetPort())
	if err != nil {
		return fmt.Errorf("failed to create client: %w", err)
	}
	defer client.Disconnect()

	var result *internal.ClickResult
	if strict {
		result, err = client.ClickElementStrict(selector, frameID, track)
	} else {
		result, err = client.ClickElement(selector, frameID, track)
	}
	if err != nil {
		return err
	}

	// 人话那一行走 **stderr**，stdout 留给 JSON。
	//
	// ⚠️ 为什么必须分家：stdout 是**契约**（py 侧 json.loads 它、agent 读它），
	// 往里面混一句人话就是往协议里混日志。而这一行又非有不可 ——
	// 「命中 5 个、取的是第 1 个、它禁用」这三件事原来一个都不说，
	// 于是「点到了 Back」和「点了个死按钮」都长得像成功。
	//
	// 每一次都印（不是只在出问题时印）：问的三个问题里，「命中 1 个、可点」
	// 同样是答案。生产脚本那边 stderr 只被拼进日志/工具回执，不参与判定。
	if chosen, ok := result.Probe.Chosen(); ok {
		state := "可点"
		if chosen.Disabled {
			state = "**禁用** —— 点了不会有反应"
		}
		fmt.Fprintf(os.Stderr, "cdp click：选择器 %q 命中 %d 个元素，点的是第 %d 个 —— %s（%s）\n",
			selector, result.Probe.MatchCount, result.Probe.MatchIndex+1, chosen.Describe(), state)
	}

	// 抬起被扣下时说清楚（JSON 里也有 release_withheld）—— 这一次点击只发出了
	// 按下的那一半，页面收到的鼠标事件比从前少一个。那是刻意的（按下之后有东西盖上来，
	// 见 client.go 的 dispatchMouseClick），但**不能说成一次普通点击**。
	if result.ReleaseWithheld {
		fmt.Fprintf(os.Stderr, "cdp click：**抬起已扣下** —— 按下之后落点换成了 %s，"+
			"这一次 mouseup 没有发给它（那正是「下拉点开又自己关掉」的成因；"+
			"菜单若由 mousedown 展开，此刻已经是开着的）\n", result.CoveredBy)
	}
	// 判据没跑成（跨站子帧盲区 / 取证不全）或判成了「没东西盖上来」——
	// 两种都要说：前者是**保护不存在**，后者是**这次没扣**（免得与实际不符）。
	//
	// ⚠️ 扣下那一格**不在这儿重复印**：上面那条 `release_withheld` 的行说的就是它
	// （同一件事印两遍 = 噪音，而噪音会让真话变得不值钱）。
	if result.LandingNote != "" && !result.ReleaseWithheld {
		fmt.Fprintf(os.Stderr, "cdp click：落点判据 —— %s\n", result.LandingNote)
	}

	enc := json.NewEncoder(os.Stdout)
	return enc.Encode(result)
}
