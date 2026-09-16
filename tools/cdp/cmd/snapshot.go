package cmd

import (
	"encoding/json"
	"fmt"
	"os"
	"time"

	"cdp/internal"

	"github.com/chromedp/cdproto/cdp"
	"github.com/chromedp/cdproto/page"
	"github.com/spf13/cobra"
)

// FrameTreeOutput represents the frame tree with snapshots for navigation.
type FrameTreeOutput struct {
	Frame       internal.FrameSnapshot `json:"frame"`
	ChildFrames []FrameTreeOutput       `json:"childFrames,omitempty"`
}

var snapshotCmd = &cobra.Command{
	Use:   "snapshot",
	Short: "Get snapshot from all frames",
	RunE:  runSnapshot,
}

func init() {
	rootCmd.AddCommand(snapshotCmd)
	snapshotCmd.Flags().String("frame-id", "", "Target a single frame by ID")
}

func runSnapshot(cmd *cobra.Command, args []string) error {
	client, err := internal.NewClient(GetHost(), GetPort())
	if err != nil {
		return fmt.Errorf("failed to create client: %w", err)
	}
	defer client.Disconnect()

	frameID, _ := cmd.Flags().GetString("frame-id")

	if frameID != "" {
		// Single frame mode
		frameTree, err := client.GetFrameTreeWithEvents(3 * time.Second)
		if err != nil {
			return fmt.Errorf("failed to get frame tree: %w", err)
		}
		targetFrame := findFrameByID(frameTree, frameID)
		if targetFrame == nil {
			return fmt.Errorf("frame %s not found", frameID)
		}
		snap, _, err := client.CaptureFrame(targetFrame)
		if err != nil {
			return err
		}
		enc := json.NewEncoder(os.Stdout)
		return enc.Encode(snap)
	}

	// Full tree mode
	frameTree, err := client.GetFrameTreeWithEvents(3 * time.Second)
	if err != nil {
		return fmt.Errorf("failed to get frame tree: %w", err)
	}

	result := buildFrameTreeOutput(client, frameTree)
	enc := json.NewEncoder(os.Stdout)
	return enc.Encode(result)
}

func buildFrameTreeOutput(client *internal.Client, ft *page.FrameTree) FrameTreeOutput {
	snap, _, _ := client.CaptureFrame(ft.Frame)

	output := FrameTreeOutput{
		Frame: snap,
	}

	if len(ft.ChildFrames) > 0 {
		output.ChildFrames = make([]FrameTreeOutput, 0, len(ft.ChildFrames))
		for _, child := range ft.ChildFrames {
			if child == nil {
				continue
			}
			output.ChildFrames = append(output.ChildFrames, buildFrameTreeOutput(client, child))
		}
	}

	return output
}

func findFrameByID(ft *page.FrameTree, targetID string) *cdp.Frame {
	if ft == nil {
		return nil
	}
	if string(ft.Frame.ID) == targetID {
		return ft.Frame
	}
	for _, child := range ft.ChildFrames {
		if child != nil {
			if found := findFrameByID(child, targetID); found != nil {
				return found
			}
		}
	}
	return nil
}
