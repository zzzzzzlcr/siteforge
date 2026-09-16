package cmd

import (
	"encoding/json"
	"fmt"
	"os"

	"cdp/internal"

	"github.com/spf13/cobra"
)

var formCmd = &cobra.Command{
	Use:   "form [selector]",
	Short: "Fill form element with human-like gesture",
	Long: `Fill a form element using human-like mouse/keyboard gestures.

Three operation modes (mutually exclusive):
  --value   Type text, or auto-pick date on MUI-style datepickers
  --check   Toggle checkbox/radio state
  --select  Choose an option from native <select> or custom dropdown

Datepicker detection (--value):
  When the target has type=tel + date-pattern placeholder
  (mm-dd-yyyy / yyyy-mm-dd) + a calendar trigger button nearby,
  automatically opens the calendar, navigates to the target month,
  and clicks the day. Supports YMD, MDY, and MD formats.

Custom dropdown detection (--select):
  When the target is not a native <select>, searches for a
  clickable control (aria-label or onclick) to open the menu,
  then finds and clicks the matching option by text.`,
	Args: cobra.MaximumNArgs(1),
	RunE: runForm,
}

func init() {
	rootCmd.AddCommand(formCmd)
	formCmd.Flags().String("selector", "", "CSS selector")
	formCmd.Flags().String("value", "", "Text to type, or date (YYYY-MM-DD / MM-DD-YYYY / MM-DD) for datepickers")
	formCmd.Flags().String("check", "", "Check/uncheck: \"true\" or \"false\"")
	formCmd.Flags().String("select", "", "Option value or text (native <select> or custom dropdown)")
	formCmd.Flags().String("frame-id", "", "Frame ID (optional)")
	formCmd.Flags().Bool("track", false, "Enable track visualization")
	// 消歧闸（默认关：CLI 是 57 个生产脚本的接口，它们的行为一个字节都不能动）——
	// 要严格的那一方是我们自己的产物（agent/template.py 的 _do 会带上这两条）。
	formCmd.Flags().Bool("strict", false,
		"选择器命中多个元素时**拒绝静默挑第一个**：按 --expect-label 消歧，认不出就报错不填")
	formCmd.Flags().String("expect-label", "",
		"这个字段自己的身份（页面上写着的那句名字）—— --strict 认它来消歧")
}

func validateFormFlags(value, check, selectOpt string) error {
	set := 0
	if value != "" {
		set++
	}
	if check != "" {
		set++
	}
	if selectOpt != "" {
		set++
	}
	if set == 0 {
		return fmt.Errorf("one of --value, --check, --select is required")
	}
	if set > 1 {
		return fmt.Errorf("only one of --value, --check, --select can be specified")
	}
	if check != "" && check != "true" && check != "false" {
		return fmt.Errorf("--check must be \"true\" or \"false\", got: %s", check)
	}
	return nil
}

func runForm(cmd *cobra.Command, args []string) error {
	selector, err := resolveSelector(cmd, args)
	if err != nil {
		return err
	}

	frameID, _ := cmd.Flags().GetString("frame-id")
	track, _ := cmd.Flags().GetBool("track")
	value, _ := cmd.Flags().GetString("value")
	check, _ := cmd.Flags().GetString("check")
	selectOpt, _ := cmd.Flags().GetString("select")

	if err := validateFormFlags(value, check, selectOpt); err != nil {
		return err
	}

	client, err := internal.NewClient(GetHost(), GetPort())
	if err != nil {
		return fmt.Errorf("failed to create client: %w", err)
	}
	defer client.Disconnect()

	// `--strict`：命中多个就想办法认准那一个，认不出就**大声失败**（一个框都不填）。
	// 这一步在**填之前**做，而且把选择器**换成唯一的那条** —— 不是拿原来那条再赌一次。
	if strict, _ := cmd.Flags().GetBool("strict"); strict {
		expectLabel, _ := cmd.Flags().GetString("expect-label")
		picked, err := client.StrictPick(selector, frameID, expectLabel)
		if err != nil {
			return err
		}
		if picked != "" {
			selector = picked
		}
	}

	var err2 error
	if value != "" {
		err2 = client.FillText(selector, value, frameID, track)
	} else if check != "" {
		err2 = client.CheckElement(selector, check == "true", frameID, track)
	} else {
		err2 = client.SelectOption(selector, selectOpt, frameID, track)
	}

	// 落点判据（G1）说的话，这条路**也要说**（与 cdp click 同一口径）。
	//
	// 为什么非补不可：`form` 的每个动作内部都在点鼠标（点控件、点选项、点日历按钮……），
	// 那些点击**同样会扣下抬起**、同样可能因为跨站子帧而**判据全瞎** ——
	// 而这条路上原先这些事**一个字节都没有**（复审实测：0 字节）。
	// 「不静默」必须两条路都真，只在一条路上说等于没说。
	//
	// ⚠️ 这些行**只在判据有话要说时才印**（扣下、判不了、判成重建）——
	// 正常点击一个字都不多：常驻的提示等于没有提示。
	sum := internal.SummarizeLanding(client.LandingDiags())
	for _, d := range sum.Diags {
		fmt.Fprintf(os.Stderr, "cdp form：落点判据 —— %s\n", d.Detail)
	}

	// 回执走 **stdout**（与 `cdp click` 同一套契约：stdout 是给机器读的 JSON，
	// 人话走 stderr）。
	//
	// 为什么 form 也要回执（复审 Minor）：下游（产物 py 的 `_say`）**只拿得到 stdout**
	// —— 它把 stderr 丢了。原先 `cdp form` 的 stdout 是空的，于是「这一次点击只发出了
	// 按下的那一半」在下游看不见：日志里那一步与一次普通填值长得一模一样，
	// 而 `_say` 照样说「填好了「X」」。落点那几件事必须在 stdout 上。
	//
	// ⚠️ 失败时不印（与 click 一致）：错误交给 cobra 走 stderr + 非 0 退出，
	// stdout 保持「成功才有回执」，免得下游把一份失败回执当成一次成功。
	if err2 == nil {
		receipt := map[string]any{"ok": true}
		if sum.Note != "" {
			receipt["landing_note"] = sum.Note
			receipt["landing_blind"] = sum.Blind
			receipt["landing_withheld"] = sum.Withheld
			receipt["landing"] = sum.Diags
		}
		if err := json.NewEncoder(os.Stdout).Encode(receipt); err != nil {
			fmt.Fprintf(os.Stderr, "cdp form：回执写不出去：%v\n", err)
		}
	}
	return err2
}
