package cmd

import (
	"encoding/json"
	"fmt"
	"os"

	"cdp/internal"

	"github.com/spf13/cobra"
)

var activeCmd = &cobra.Command{
	Use:   "active [target]",
	Short: "激活指定页面",
	Args:  cobra.MaximumNArgs(1),
	RunE:  runActive,
}

func init() {
	rootCmd.AddCommand(activeCmd)
	activeCmd.Flags().String("target", "", "目标页面 ID")
}

func resolveTarget(cmd *cobra.Command, args []string) (string, error) {
	target, _ := cmd.Flags().GetString("target")
	if target == "" {
		if len(args) == 0 {
			return "", fmt.Errorf("target is required")
		}
		target = args[0]
	}
	return target, nil
}

func runActive(cmd *cobra.Command, args []string) error {
	targetID, err := resolveTarget(cmd, args)
	if err != nil {
		return err
	}

	if err := internal.ActivateTarget(GetHost(), GetPort(), targetID); err != nil {
		return fmt.Errorf("failed to activate target: %w", err)
	}

	pages, err := internal.ListPageTargets(GetHost(), GetPort(), true)
	if err != nil {
		return fmt.Errorf("failed to list targets: %w", err)
	}

	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	return enc.Encode(pages)
}
