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

	client, err := internal.NewClient(GetHost(), GetPort())
	if err != nil {
		return fmt.Errorf("failed to create client: %w", err)
	}
	defer client.Disconnect()

	result, err := client.ClickElement(selector, frameID, track)
	if err != nil {
		return err
	}

	enc := json.NewEncoder(os.Stdout)
	return enc.Encode(result)
}
