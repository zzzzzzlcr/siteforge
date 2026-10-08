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
// 页面里有**两套**像素坐标：
//
//	observe 的 bbox   →  **CSS** 像素、相对**视口**
//	                     （getBoundingClientRect 的原样输出，observe.go 只做了 Math.round）
//	截图 PNG 的像素   →  **设备**像素（Page.captureScreenshot 吐的是 surface）
//
// 两者之间只有一个换算，这里把它写成契约：
//
//	scale     = image_px / viewport_css_px   ← **量出来的**（这张图自己有多大 ÷ 视口有多大）
//	screen_px = css_px × scale               ← 逐点形式（ScreenPx 就是这一行）
//
// ⚠️ 比例为什么是**量**出来的，而不是问浏览器的（2026-10-08，job-f9b0090a9472）：
// `window.devicePixelRatio` 是**页面里那个值**，反检测浏览器会编它 —— 实测 8100 上
// 那个窗口的指纹是手机档：dpr 自称 3、连 CSS 的 `(resolution: 3dppx)` 媒体查询都点头，
// 而它真正渲染/截出来的面就是 1 倍（视口 377×757 → 图 377×757）。
// 原来那道闸拿 dpr 当渲染比例（image_px == viewport_css_px × dpr），于是这台窗口上
// **每一张图都被拒**：8100 上 7 次截图 7 次全拒、一张 PNG 都没落下来；而 8099 那边
// 是 dpr=1 的 PC 档，233 张全成 —— 也就是说，坏的不是哪台机器，是那个假设。
// 任务本来就要跑在不同机型/不同指纹上：**自称的 dpr 与真实渲染比例不一致，是常态**。
// 所以：比例只从**图与视口本身**量（谁也没法在这两个数上撒谎），dpr 原样报出来留档，
// 两者对不上时附一句话说清（scaleNote）。
//
// ⚠️ 为什么还值得这么大张旗鼓：本项目**已经被 DPR 咬过一次** —— 窗口指纹里那个字段
// 写成了 `dpr`（浏览器认的是 `devicePixelRatio`），于是它被静默忽略，浏览器继续按
// DPR=3 跑，而点击按 1 倍换算，**三倍偏移打在空气上**。一处静默忽略比例的地方，
// 症状是「点不准」而不是「报错」，所以：
//
//	① 比例只从**图与视口**量（measureScale）；两个轴不是一个比例（整页图、拍到了别的
//	   窗口）**直接报错**，绝不吐一份「看着能叠加、其实叠错」的 JSON
//	② 量出来的比例与浏览器**自称**的 dpr 对不上时**说出来**（scale_note），不假装一致
//	③ 四个数（image_px / viewport_css_px / dpr / scale）**全部**放进 JSON：
//	   调用方不必信我们，自己就能复核这条乘法和那个差值
//	④ `window.devicePixelRatio` 读不到（≤0）仍然**直接报错**：比例虽然不再靠它，
//	   但它是「这台窗口自称是什么」的那一格（探路/复验两边比指纹靠的就是它）。
//	   读不到 = 这不是我们认识的那种浏览器，此时唯一安全的动作是不出图。
//
// ⚠️ bbox 是**视口相对**的（页面一滚它就变），所以这张图只对**当次**观测有效：
// 图与 bbox 必须在同一个滚动位置上取（同一个"动作前/动作后"状态里）。
// 元素在 fold 之下时，映射出来的点会落在**图外** —— ScreenPx 会说 ok=false，
// 而不是给一个越界的坐标让调用方去画到别的元素上。
//
// 实测（2026-09-16，Chrome 150.0.7871.124 headless，见 cmd/screenshot_test.go）：
// 视口 1200×657 @DPR 1 → 图 1200×657；@DPR 2 → 2400×1314；
// @DPR 1.25 视口 1002×559 → 图 1253×699（1002×1.25 = 1252.5，Chrome 抹的是 ceil，
// 所以两个轴允许差一点点，见 measureScale 的容差）。**有滚动条时同样成立**：
// innerWidth 含滚动条，而 Chrome 的截图也把滚动条画进图里 —— 用 clientWidth 反而对不上。

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

	// DPR 是 window.devicePixelRatio 的**原值**（可以是 1.25 这种小数，不是整数）
	// —— 浏览器**自称**的那个数，原样交出去留档。
	//
	// ⚠️ **别拿它换算**：反检测浏览器的指纹会编它（实测：自称 3、真画出来 1 倍）。
	// 换算一律用 Scale；它俩对不上时 ScaleNote 里有一句话。
	DPR float64 `json:"dpr"`

	// Scale 是**量出来的**比例：image_px ÷ viewport_css_px（宽高统一到同一个数，
	// 两个轴对不上时根本不返回 Shot，见 measureScale）。
	//
	// 这是 ScreenPx 用的那个数，也是「图 ↔ bbox」这条链上唯一可信的比例 ——
	// 它是从图**自己的尺寸**量出来的，任何一方都没法在它上面撒谎。
	Scale float64 `json:"scale"`

	// ScaleNote 是「自称的 dpr」与「量出来的 scale」对不上时的那句话（两个数都摆出来）；
	// 一致就是**空串**（不编话 —— 一句话见谁都说的后果是没人再读它）。
	ScaleNote string `json:"scale_note"`

	// raw 是解码后的 PNG 字节（--out 落盘用）。不上线到 JSON：
	// stdout 那条路子（common.py）要的就是 base64。
	raw []byte
}

// PNG 返回解码后的 PNG 字节（--out 落盘 / 需要手写文件时用）。
func (s *Shot) PNG() []byte { return s.raw }

// ScreenPx 把 observe 那套坐标（视口 CSS 像素）映射到**这张图**的像素坐标。
//
// 这就是那条契约的逐点形式：screen_px = css_px × **scale**（量出来的那个）。
// ⚠️ 这里**不许**换成 DPR：那个数是浏览器自称的，会与真实渲染比例差一个倍数
// （文件头写着 2026-10-08 那次实测）。
// ok=false 表示映射出来的点**不在图里**（元素在 fold 之下、或横向被裁）——
// 调用方此时不该画点什么，该重新取一张。
func (s *Shot) ScreenPx(cssX, cssY float64) (x, y int, ok bool) {
	x = int(math.Round(cssX * s.Scale))
	y = int(math.Round(cssY * s.Scale))
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

	// 比例**量**出来（图自己的尺寸 ÷ 视口），不问浏览器 —— 它自称的那个数
	// 只原样报出去、并在对不上时说一句（见文件头）。
	scale, err := measureScale(img, css)
	if err != nil {
		return nil, err
	}

	return &Shot{
		PNGBase64:     base64.StdEncoding.EncodeToString(raw),
		ImagePx:       img,
		ViewportCssPx: css,
		DPR:           vp.DPR,
		Scale:         scale,
		ScaleNote:     scaleNote(vp.DPR, scale),
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
		// 这里是**有意**的硬失败，但理由**不是**「没它就算不出比例」了 ——
		// 比例现在是量出来的（见文件头），读不到它也照样能量。
		// 它仍然是「这台窗口自称是什么」的那一格：探路与复验两边比指纹（viewport/dpr/ua）
		// 靠的就是它，读不到 = 这不是我们认识的那种浏览器（本项目被 DPR 咬过的那一次，
		// 恰恰就是**这个数没读对**而没有任何东西说话）。此时唯一安全的动作是不出图。
		return viewportProbe{}, fmt.Errorf("window.devicePixelRatio 读回来是 %g（≤0）—— "+
			"这是这台窗口「自称是什么」的那一格（比指纹靠它），读不到就说明它不是我们认识的"+
			"那种浏览器，这里不猜默认值、也不出图", vp.DPR)
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

// measureScale 从**图与视口自己**的尺寸量出比例，并要求两个轴是同一个比例 ——
// 这是「这张图就是这块视口」唯一站得住的判据：它不经过任何会撒谎的字段
// （浏览器的 dpr 会撒谎，图的字节数不会）。
//
// 容差是**相对**的（2%），不是「几个像素」：两个轴各自有一个取整（各 ±0.5px），
// 这个误差在很窄很高的视口上会被放大 —— 实测 DPR=1.25、视口 377×1400 时，
// 宽那边 0.75px 的取整误差折算到高就是 2.8px。而真正要拦的两种坏法
// （整页图 / 拍到了别的窗口）差的是**几十个百分点**，2% 隔得远远的。
//
// 为什么要拦：比例错了的症状是「运营在图上点的那个元素」与「py 真去点的那个坐标」
// 悄悄错开 —— 看着没事，点不准。矛盾时唯一安全的动作是**不出图**。
func measureScale(img, css PixelSize) (float64, error) {
	if img.Width <= 0 || img.Height <= 0 {
		return 0, fmt.Errorf("图没有尺寸（image_px = %d×%d）—— 没法从它量比例", img.Width, img.Height)
	}
	if css.Width <= 0 || css.Height <= 0 {
		return 0, fmt.Errorf("视口没有尺寸（viewport_css_px = %d×%d）—— 没法拿它当另一头", css.Width, css.Height)
	}
	sx := float64(img.Width) / float64(css.Width)
	sy := float64(img.Height) / float64(css.Height)

	const tolRel = 0.02
	if diff := math.Abs(sx - sy); diff > tolRel*math.Max(sx, sy) {
		return 0, fmt.Errorf("截图与视口对不上：image_px = %d×%d，viewport_css_px = %d×%d —— "+
			"宽量出来是 %g 倍、高量出来是 %g 倍，两个轴不是一个比例。"+
			"这张图**不是这块视口**（整页图？拍到了别的窗口/别的页？），叠不了 observe 的 bbox"+
			"（bbox 是 CSS 像素、相对视口），所以不输出（宁可没有图，也不要一张坐标不对的图）",
			img.Width, img.Height, css.Width, css.Height, sx, sy)
	}
	return sx, nil
}

// scaleNote 说清「浏览器自称的」与「量出来的」差在哪 —— 一致就是**空串**。
//
// 为什么不默认就有话说：这句话要在人头攒动的输出里被人**读到**才有用，
// 见谁都说的后果是没人再读它。容差与 measureScale 同一套（相对 2%）：两个轴各自
// 取整会把量出来的比例挪千分之几，那不是撒谎。
func scaleNote(dpr, scale float64) string {
	if math.Abs(dpr-scale) <= 0.02*math.Max(dpr, scale) {
		return ""
	}
	return fmt.Sprintf("⚠️ 这台窗口自称 dpr=%g，而图 ÷ 视口量出来是 %g 倍 —— "+
		"换算一律按**量出来的** %g 走（ScreenPx 用的就是它）；dpr 只是它自称的数，"+
		"别拿它换算。反检测浏览器的指纹会编这个数（实测那一台：iPhone 档自称 3、"+
		"真正渲染出来是 1 倍）。", dpr, scale, scale)
}
