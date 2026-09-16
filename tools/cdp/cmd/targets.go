package cmd

import (
	"encoding/json"
	"fmt"
	"os"

	"cdp/internal"

	"github.com/spf13/cobra"
)

var targetsCmd = &cobra.Command{
	Use:   "targets",
	Short: "列出所有打开的页面",
	RunE:  runTargets,
}

func init() {
	rootCmd.AddCommand(targetsCmd)
}

func runTargets(cmd *cobra.Command, args []string) error {
	pages, err := internal.ListPageTargets(GetHost(), GetPort(), true)
	if err != nil {
		return fmt.Errorf("failed to list targets: %w", err)
	}

	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	return enc.Encode(pages)
}
