package cmd

import (
	"encoding/json"
	"fmt"
	"os"

	"cdp/internal"

	"github.com/spf13/cobra"
)

// screenshotCmd 截图：给**人的眼睛**用的那条通道。
//
// 为什么工具层需要它（三个设计项都建在它上面，2026-09-16 定）：
//   - Console 主视图 = 「一轮一句话 + **一对截图**」—— 运营（非技术人员）要能亲眼
//     看见「那个 XX 没被点到」，而不是读一段模型 JSON
//   - `vision.inspect`：按需的目视检查
//   - 「**运营在截图上点元素**」这条交互：人点的是图上的一个像素，
//     py 拿到的必须是页面上那个坐标 —— 这条依赖坐标换算（见 internal/screenshot.go）
//
// ⚠️ stdout 的默认形态是**裸 base64**，不是 JSON —— 这与 observe 的默认（JSON）相反，
// 是刻意的：`forms/common.py` 的 `screenshot()` 早就按「stdout 上一串 base64」写好了
// （`subprocess.run([... "screenshot" ...])` 然后 `.stdout.strip()`），而它今天**是死代码**
// （63 个生产站点脚本里 0 个调用它，所以没人发现它一直取不到图）。
// 默认给 base64 = 让那份代码按它原本设想的方式直接活过来；要坐标证据的人显式加 --json。
//
// 退出码：0 = 拿到了图；1 = 没拿到（连不上 Chrome / 读不到 devicePixelRatio /
// 图与视口对不上 —— 见 internal/screenshot.go 的 measureScale）。
// 与 observe 同一个约定：**要么一份完整可用的输出，要么什么都没有**。
var screenshotCmd = &cobra.Command{
	Use:   "screenshot",
	Short: "截图：stdout 出 base64 PNG（--json 附坐标证据，--out 落盘）",
	Long: `截当前视口。默认把 PNG 的 base64 直接写到 stdout（无装饰，即 forms/common.py 读的那种）。

加 --json 会改吐一份带**坐标证据**的对象：png_base64 / image_px / viewport_css_px / dpr / scale。
它们之间的关系是契约：

    scale = image_px ÷ viewport_css_px      （**量出来的**：这张图自己有多大 ÷ 视口有多大）
  ⇒ screen_px = css_px × scale              （observe 的 bbox 就在 css_px 这一侧）

拿它可以把 observe 报告的 bbox（CSS 像素、相对**视口**）叠到这张图上。
比例是**量**的，不是问浏览器的：dpr 那个数是它自称的，反检测浏览器的指纹会编它
（实测一台自称 3、真画出来 1 倍）—— 所以 dpr 只留档，它跟 scale 对不上时
scale_note 里会有一句话。
图与视口对不上（整页图 / 拍到了别的窗口）时命令**直接失败**，
不吐一份看着能叠、其实叠错的 JSON。

⚠️ 图只对**当次**观测有效：bbox 是视口相对的，页面一滚就与图对不上了。
元素在 fold 之下的部分会映射到图外（ScreenPx 会给 ok=false）。

--out 把 PNG 落盘（给人看的那种用法；stdout 上照样有 base64，两边内容一致）。`,
	RunE: runScreenshot,
}

func init() {
	rootCmd.AddCommand(screenshotCmd)
	screenshotCmd.Flags().Bool("json", false, "改吐 JSON（png_base64 + image_px + viewport_css_px + dpr + scale；默认只吐裸 base64）")
	screenshotCmd.Flags().String("out", "", "把 PNG 写到这个文件（stdout 的内容不变）")
}

func runScreenshot(cmd *cobra.Command, args []string) error {
	client, err := internal.NewClient(GetHost(), GetPort())
	if err != nil {
		return fmt.Errorf("failed to create client: %w", err)
	}
	defer client.Disconnect()

	shot, err := client.Screenshot()
	if err != nil {
		return err
	}

	// --out 先写：落盘失败就别再往 stdout 吐 base64 —— 那种「一半成功」的输出
	// 在调用方眼里与成功长得一样（脚本会把 base64 存下来，而人以为文件也写了）。
	if out, _ := cmd.Flags().GetString("out"); out != "" {
		if err := os.WriteFile(out, shot.PNG(), 0o644); err != nil {
			return fmt.Errorf("写 %s 失败: %w", out, err)
		}
	}

	// --json：把坐标证据一起交出去（契约字段名，别改）。
	if jsonOut, _ := cmd.Flags().GetBool("json"); jsonOut {
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		return enc.Encode(shot)
	}

	// 默认：裸 base64（common.py 读的就是这一行；它自己会 strip 掉结尾换行）。
	fmt.Fprintln(os.Stdout, shot.PNGBase64)
	return nil
}
