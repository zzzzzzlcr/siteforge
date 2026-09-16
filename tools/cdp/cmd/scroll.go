package cmd

import (
	"fmt"

	"cdp/internal"

	"github.com/spf13/cobra"
)

var scrollCmd = &cobra.Command{
	Use:   "scroll [selector]",
	Short: "Scroll element into view with human-like gesture",
	Args:  cobra.MaximumNArgs(1),
	RunE:  runScroll,
}

func init() {
	rootCmd.AddCommand(scrollCmd)
	scrollCmd.Flags().String("selector", "", "CSS selector")
	scrollCmd.Flags().String("frame-id", "", "Frame ID (optional)")
	scrollCmd.Flags().Bool("track", false, "Enable track visualization")
}

func runScroll(cmd *cobra.Command, args []string) error {
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

	scrollSelector := selector
	if frameID != "" {
		iframeSel, err := client.ResolveIframeSelector(frameID)
		if err != nil {
			return err
		}
		scrollSelector = iframeSel
	}

	if err := client.ScrollToElement(scrollSelector, track); err != nil {
		return err
	}

	return client.ScrollIntoView(selector, frameID)
}
