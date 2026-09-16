package cmd

import (
	"encoding/json"
	"fmt"
	"os"

	"cdp/internal"

	"github.com/spf13/cobra"
)

var closeCmd = &cobra.Command{
	Use:   "close [target-id...]",
	Short: "关闭指定页面",
	Long:  `通过 target ID 关闭 Chrome 页面。支持 --all 关闭所有非活跃页面。`,
	Args:  cobra.ArbitraryArgs,
	RunE:  runClose,
}

func init() {
	rootCmd.AddCommand(closeCmd)
	closeCmd.Flags().Bool("all", false, "关闭所有非活跃页面")
}

func runClose(cmd *cobra.Command, args []string) error {
	all, _ := cmd.Flags().GetBool("all")

	if !all && len(args) == 0 {
		return fmt.Errorf("需要指定至少一个 target ID 或使用 --all")
	}

	// 一次性获取所有页面及 active 状态，避免多次 HTTP 往返
	pages, err := internal.ListPageTargets(GetHost(), GetPort(), true)
	if err != nil {
		return fmt.Errorf("failed to list targets: %w", err)
	}

	var activeID string
	for _, p := range pages {
		if p.Active != nil && *p.Active {
			activeID = p.ID
			break
		}
	}
	if activeID == "" && len(pages) > 0 {
		activeID = pages[0].ID
	}

	toClose := make(map[string]struct{})
	for _, id := range args {
		toClose[id] = struct{}{}
	}

	if all {
		for _, p := range pages {
			if p.ID == activeID {
				continue
			}
			toClose[p.ID] = struct{}{}
		}
	}

	if _, ok := toClose[activeID]; ok {
		return fmt.Errorf("不能关闭活跃页面 %s", activeID)
	}

	var firstErr error
	for id := range toClose {
		if err := internal.CloseTarget(GetHost(), GetPort(), id); err != nil {
			if firstErr == nil {
				firstErr = err
			}
			fmt.Fprintf(os.Stderr, "关闭 %s 失败: %v\n", id, err)
		}
	}

	pages, err = internal.ListPageTargets(GetHost(), GetPort(), false)
	if err != nil {
		return fmt.Errorf("failed to list targets: %w", err)
	}

	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	if err := enc.Encode(pages); err != nil {
		return fmt.Errorf("failed to encode output: %w", err)
	}

	return firstErr
}
