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
	//: 树没取到时的说明（正常情况为空）。**有它 = 导航成功、但树缺失。**
	//: 有这一格是为了**不许**把「没取到树」读成「没打开」（2026-09-18 那条假失败）。
	Note string `json:"note,omitempty"`
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

	if frameTree == nil {
		// 导航成功了，只是没取到 frame tree（见 `internal.Navigate` 里那段）。
		// **不许**把它读成失败：退出码仍是 0，而且把这件事**明说**出来（`note`）——
		// 不编一棵假树冒充拿到了。
		return json.NewEncoder(os.Stdout).Encode(naviFrameTree{
			Frame: naviFrame{FrameID: frameID, URL: url},
			Note:  "导航已发出，但没取到 frame tree（页面可能还在加载）—— 这不代表没打开",
		})
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
