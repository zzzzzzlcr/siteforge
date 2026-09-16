package internal

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"image/png"
	"math"

	"github.com/chromedp/cdproto/page"
	"github.com/chromedp/chromedp"
)

// ─────────────────────────────────────────────────────────────────────────────
// 坐标空间 —— 这份文件存在的**全部理由**
//
// 页面里有**两套**像素坐标，它们差一个 devicePixelRatio：
//
//	observe 的 bbox   →  **CSS** 像素、相对**视口**
//	                     （getBoundingClientRect 的原样输出，observe.go 只做了 Math.round）
//	截图 PNG 的像素   →  **设备**像素（Page.captureScreenshot 吐的是 surface，按 DPR 放大过）
//
// 两者之间只有一个换算，这里把它写成契约：
//
//	image_px = viewport_css_px × dpr        ← 截图的尺寸关系（checkScale 会验它）
//	screen_px = css_px × dpr                ← 同一件事的逐点形式（ScreenPx 就是这一行）
//
// ⚠️ 为什么值得这么大张旗鼓：本项目**已经被 DPR 咬过一次** —— 窗口指纹里那个字段
// 写成了 `dpr`（浏览器认的是 `devicePixelRatio`），于是它被静默忽略，浏览器继续按
// DPR=3 跑，而点击按 1 倍换算，**三倍偏移打在空气上**。一处静默忽略比例的地方，
// 症状是「点不准」而不是「报错」，所以：
//
//	① 比例只从 `window.devicePixelRatio` 取；取不到（≤0）**直接报错**，绝不默认 1
//	② 截完图**验一遍** image_px == viewport_css_px × dpr（checkScale），对不上就报错 ——
//	   不吐一份「看着能叠加、其实叠错」的 JSON（那正是最坏的一种输出）
//	③ 三个数（image_px / viewport_css_px / dpr）**全部**放进 JSON：调用方不必信我们，
//	   自己就能复核这条乘法
//
// ⚠️ bbox 是**视口相对**的（页面一滚它就变），所以这张图只对**当次**观测有效：
// 图与 bbox 必须在同一个滚动位置上取（同一个"动作前/动作后"状态里）。
// 元素在 fold 之下时，映射出来的点会落在**图外** —— ScreenPx 会说 ok=false，
// 而不是给一个越界的坐标让调用方去画到别的元素上。
//
// 实测（2026-09-16，Chrome 150.0.7871.124 headless，见 cmd/screenshot_test.go）：
// 视口 1200×657 @DPR 1 → 图 1200×657；@DPR 2 → 2400×1314；
// @DPR 1.25 视口 1002×559 → 图 1253×699（1002×1.25 = 1252.5，Chrome 抹的是 ceil，
// 所以 checkScale 容忍 1 设备像素）。**有滚动条时同样成立**：innerWidth 含滚动条，
// 而 Chrome 的截图也把滚动条画进图里 —— 用 clientWidth 反而对不上。

// PixelSize 是一组像素尺寸（宽 × 高）。两种用途共用：PNG 的实际像素、视口的 CSS 像素。
type PixelSize struct {
	Width  int `json:"width"`
	Height int `json:"height"`
}

// Shot 是一次截图的**全部坐标证据**（契约）。消费它的是三个东西：Console 主视图
// 的「一对截图」、vision.inspect 的按需目视检查、以及「运营在截图上点元素」那条交互
// —— 最后一个直接依赖 image_px / viewport_css_px / dpr 这三个数。
type Shot struct {
	// PNGBase64 是 PNG 的 base64（**没有** `data:` 前缀、没有换行、没有别的装饰）：
	// forms/common.py 的 screenshot() 就是按「stdout 上那串 base64」读它的。
	PNGBase64 string `json:"png_base64"`

	// ImagePx 是 PNG 的**实际**像素尺寸 —— 解 PNG 头得来的，不是算出来的。
	// （算出来的话，这条链路错了它也跟着错，等于自己给自己作证。）
	ImagePx PixelSize `json:"image_px"`

	// ViewportCssPx 是 window.innerWidth / innerHeight（**含**滚动条，见文件头实测）。
	ViewportCssPx PixelSize `json:"viewport_css_px"`

	// DPR 是 window.devicePixelRatio 的**原值**（可以是 1.25 这种小数，不是整数）。
	DPR float64 `json:"dpr"`

	// raw 是解码后的 PNG 字节（--out 落盘用）。不上线到 JSON：
	// stdout 那条路子（common.py）要的就是 base64。
	raw []byte
}

// PNG 返回解码后的 PNG 字节（--out 落盘 / 需要手写文件时用）。
func (s *Shot) PNG() []byte { return s.raw }

// ScreenPx 把 observe 那套坐标（视口 CSS 像素）映射到**这张图**的像素坐标。
//
// 这就是那条契约的逐点形式：screen_px = css_px × dpr。
// ok=false 表示映射出来的点**不在图里**（元素在 fold 之下、或横向被裁）——
// 调用方此时不该画点什么，该重新取一张。
func (s *Shot) ScreenPx(cssX, cssY float64) (x, y int, ok bool) {
	x = int(math.Round(cssX * s.DPR))
	y = int(math.Round(cssY * s.DPR))
	if x < 0 || y < 0 || x >= s.ImagePx.Width || y >= s.ImagePx.Height {
		return x, y, false
	}
	return x, y, true
}

// viewportProbeJS 一次取回三个数。**必须**是 devicePixelRatio 这个名字
// （写成 `dpr` 会被浏览器静默忽略 —— 本项目真栽过那一次）。
const viewportProbeJS = `(function(){return JSON.stringify({` +
	`width: window.innerWidth, ` +
	`height: window.innerHeight, ` +
	`dpr: window.devicePixelRatio` +
	`});})()`

type viewportProbe struct {
	Width  float64 `json:"width"`
	Height float64 `json:"height"`
	DPR    float64 `json:"dpr"`
}

// Screenshot 截当前视口，并把它连同**坐标证据**一起返回。
//
// 失败（连不上 / 拿不到比例 / 比例与图对不上）一律返回 error，**不返回半份 Shot**：
// 一个没有比例的截图对「叠 bbox」这件事是没用的，而它看起来又完全正常。
func (c *Client) Screenshot() (*Shot, error) {
	vp, err := c.probeViewport()
	if err != nil {
		return nil, err
	}

	raw, err := c.capturePNG()
	if err != nil {
		return nil, err
	}

	// 图的实际尺寸**从 PNG 自己身上读**（不拿视口×dpr 反推 —— 那样就成了自证）。
	cfg, err := png.DecodeConfig(bytes.NewReader(raw))
	if err != nil {
		return nil, fmt.Errorf("截图不是一张能解码的 PNG（%d 字节）: %w", len(raw), err)
	}
	img := PixelSize{Width: cfg.Width, Height: cfg.Height}
	css := PixelSize{Width: int(math.Round(vp.Width)), Height: int(math.Round(vp.Height))}

	if err := checkScale(img, css, vp.DPR); err != nil {
		return nil, err
	}

	return &Shot{
		PNGBase64:     base64.StdEncoding.EncodeToString(raw),
		ImagePx:       img,
		ViewportCssPx: css,
		DPR:           vp.DPR,
		raw:           raw,
	}, nil
}

// probeViewport 量视口与 DPR。三个数缺一不可，缺了**报错**而不是补默认值。
func (c *Client) probeViewport() (viewportProbe, error) {
	var s string
	if err := c.EvalInFrame("", viewportProbeJS, &s); err != nil {
		return viewportProbe{}, fmt.Errorf("读视口尺寸失败: %w", err)
	}
	var vp viewportProbe
	if err := json.Unmarshal([]byte(s), &vp); err != nil {
		return viewportProbe{}, fmt.Errorf("解析视口尺寸失败（拿到 %q）: %w", s, err)
	}
	if vp.Width <= 0 || vp.Height <= 0 {
		return viewportProbe{}, fmt.Errorf("视口尺寸不合理（%g×%g）—— 截图没法与 observe 的 bbox 对齐", vp.Width, vp.Height)
	}
	if vp.DPR <= 0 {
		// 这里是**有意**的硬失败：比例是这两个坐标空间之间唯一的桥，
		// 猜一个 1 会让所有换算静默错掉（本项目被 DPR 咬过的那一次就是这样）。
		return viewportProbe{}, fmt.Errorf("window.devicePixelRatio 读回来是 %g（≤0）—— "+
			"没有它就换算不出截图与 bbox 的比例，这里不猜默认值（默认 1 会让带缩放屏上的点击整体偏移）", vp.DPR)
	}
	return vp, nil
}

// capturePNG 走 CDP 截当前视口。
//
// fromSurface / captureBeyondViewport 都用默认值（true / false）—— 于是拿到的是
// **视口那么大**的一张图，而不是整页：整页图与 bbox（视口相对）对不上。
func (c *Client) capturePNG() ([]byte, error) {
	var raw []byte
	err := chromedp.Run(c.ctx, chromedp.ActionFunc(func(ctx context.Context) error {
		b, err := page.CaptureScreenshot().WithFormat(page.CaptureScreenshotFormatPng).Do(ctx)
		if err != nil {
			return err
		}
		raw = b
		return nil
	}))
	if err != nil {
		return nil, fmt.Errorf("截图失败: %w", err)
	}
	if len(raw) == 0 {
		return nil, fmt.Errorf("截图返回了 0 字节（Chrome 没给图，也没报错）")
	}
	return raw, nil
}

// checkScale 验的就是文件头那条换算：image_px == round(viewport_css_px × dpr)。
//
// 容忍 1 设备像素：DPR 是小数时 Chrome 抹的是 ceil，四舍五入与它可以差 1
// （实测 DPR=1.25、视口 1002×559 → 图 1253×699，而 1002×1.25 = 1252.5）。
//
// 为什么对不上要**报错**而不是照常输出：这三个数是**同一份证据**，它们互相矛盾时
// 调用方无从取舍（到底信图的大小、还是信乘法？）—— 而任何一个选择都会让
// 「运营在截图上点的那个元素」与「py 真去点的那个坐标」悄悄错开。
// 矛盾说明我们对这套浏览器的假设已经不成立了，此时唯一安全的动作是**不出图**。
func checkScale(img, css PixelSize, dpr float64) error {
	const tolPx = 1.0
	for _, ax := range []struct {
		axis     string
		got, css int
	}{
		{"宽", img.Width, css.Width},
		{"高", img.Height, css.Height},
	} {
		want := float64(ax.css) * dpr
		if math.Abs(float64(ax.got)-want) > tolPx {
			return fmt.Errorf("截图与视口的比例对不上（%s）：图是 %d 设备像素，"+
				"而 viewport_css_px × dpr = %d × %g = %.1f —— "+
				"两者差一个比例，这张图**叠不了** observe 的 bbox（bbox 是 CSS 像素、相对视口），"+
				"所以不输出（宁可没有图，也不要一张坐标不对的图）", ax.axis, ax.got, ax.css, dpr, want)
		}
	}
	return nil
}
