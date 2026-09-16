package cmd

import (
	"encoding/json"
	"fmt"
	"os"

	"cdp/internal"

	"github.com/chromedp/cdproto/page"
	"github.com/spf13/cobra"
)

type naviFrame struct {
	FrameID  string `json:"frameId"`
	ParentID string `json:"parentId,omitempty"`
	Name     string `json:"name,omitempty"`
	URL      string `json:"url"`
}

type naviFrameTree struct {
	Frame       naviFrame       `json:"frame"`
	ChildFrames []naviFrameTree `json:"childFrames,omitempty"`
}

var naviCmd = &cobra.Command{
	Use:   "navi <url>",
	Short: "Navigate to URL",
	Args:  cobra.ExactArgs(1),
	RunE:  runNavi,
}

func init() {
	rootCmd.AddCommand(naviCmd)
	naviCmd.Flags().String("frame-id", "", "Frame ID to navigate (default: main frame)")
}

func runNavi(cmd *cobra.Command, args []string) error {
	url := args[0]
	frameID, _ := cmd.Flags().GetString("frame-id")

	client, err := internal.NewClient(GetHost(), GetPort())
	if err != nil {
		return fmt.Errorf("failed to create client: %w", err)
	}
	defer client.Disconnect()

	frameTree, err := client.Navigate(url, frameID)
	if err != nil {
		return err
	}

	result := buildNaviFrameTree(frameTree)
	enc := json.NewEncoder(os.Stdout)
	return enc.Encode(result)
}

func buildNaviFrameTree(ft *page.FrameTree) naviFrameTree {
	frame := ft.Frame
	output := naviFrameTree{
		Frame: naviFrame{
			FrameID:  string(frame.ID),
			ParentID: string(frame.ParentID),
			Name:     frame.Name,
			URL:      frame.URL,
		},
	}

	if len(ft.ChildFrames) > 0 {
		output.ChildFrames = make([]naviFrameTree, 0, len(ft.ChildFrames))
		for _, child := range ft.ChildFrames {
			if child == nil {
				continue
			}
			output.ChildFrames = append(output.ChildFrames, buildNaviFrameTree(child))
		}
	}

	return output
}
