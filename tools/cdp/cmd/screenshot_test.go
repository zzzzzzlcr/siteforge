package cmd

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"image"
	"image/png"
	"math"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"
)

// 截图命令的端到端：**真构建二进制、真跑、真起浏览器、真解码 PNG 取像素**。
//
// 为什么非要在像素上验一次（而不是只比几个数字）：这条链是
//
//	observe 的 bbox（CSS 像素、相对视口）→ × dpr → 截图上的像素
//
// 数字可以对得整整齐齐而图是错的（例如「图是设备像素、却按 CSS 像素报尺寸」——
// 三个数自洽，但比例整个错掉）。**只有回到像素本身**才能把这条链钉死，
// 而这正是「运营在截图上点元素」那条交互依赖的东西：人点的是图上的一个像素，
// py 拿到的必须是页面上那个坐标。
//
// 夹具 = internal/testdata/screenshot.html：一个 200×100、纯 rgb(255,0,255)、
// 无文字、绝对定位在 (40,20) 的 <button>（为什么是这些数，见夹具里的注释）。
//
// ⚠️ 为什么自备浏览器（照 observe_e2e_test.go 的先例，另起一套的原因见下）：
//   - 9222 那个是共享的，`go test ./...` 会并行跑 internal 那边的集成测试，
//     它们往里注入 DOM —— 观测到的是别人的页面
//   - 那一套的 launchChrome 不带 --window-size，而**视口尺寸随 DPR 变**：
//     实测 headless=new 默认窗口在 DPR=2 时只有 500×137，夹具的元素在 y=20..120，
//     只剩 17px 余量（换个机器/版本就会掉出视口 → 假红）。这里显式给
//     --window-size=1200,800（CSS 像素，三档 DPR 下实测都是 1200×657）。

// ---- 自己的浏览器（私有端口 + 私有 profile + 进程组） ----

type shotChrome struct {
	port int
	bin  string
}

// startShotChrome 起一个这条测试**独享**的 headless Chrome：端口、profile、
// 窗口尺寸都显式给。extra 是附加的启动开关（DPR 那几档用它逼出来）。
func startShotChrome(t *testing.T, extra ...string) *shotChrome {
	t.Helper()
	e := env(t) // 借它的二进制与夹具服务（浏览器我们另起）

	chromeBin, err := chromeBinary()
	if err != nil {
		t.Fatalf("找不到 Chrome：%v", err)
	}
	port, err := freePort()
	if err != nil {
		t.Fatalf("找空闲端口失败: %v", err)
	}
	profile := t.TempDir()
	logPath := filepath.Join(profile, "chrome.log")
	logFile, err := os.Create(logPath)
	if err != nil {
		t.Fatalf("建日志文件失败: %v", err)
	}

	args := []string{
		"--headless=new",
		fmt.Sprintf("--remote-debugging-port=%d", port),
		"--remote-debugging-address=127.0.0.1",
		"--no-first-run",
		"--no-default-browser-check",
		"--disable-gpu",
		"--no-sandbox",
		"--disable-extensions",
		"--disable-crash-reporter",
		// 视口尺寸的**唯一**来源：不给它，视口就随 DPR 与机器变（见文件头）
		"--window-size=1200,800",
	}
	args = append(args, extra...)
	args = append(args, "--user-data-dir="+profile, "about:blank")

	cmd := exec.Command(chromeBin, args...)
	cmd.Stdout, cmd.Stderr = logFile, logFile
	// 自己一个进程组：Chrome 会拉起 zygote / renderer / crashpad 一串子进程，
	// 只杀父进程会留孤儿（本仓库栽过：孤儿窗口 → 内存 95%）。
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	if err := cmd.Start(); err != nil {
		t.Fatalf("起 Chrome 失败: %v", err)
	}
	t.Cleanup(func() {
		pid := cmd.Process.Pid
		_ = syscall.Kill(-pid, syscall.SIGKILL)
		_ = syscall.Kill(pid, syscall.SIGKILL)
		_ = cmd.Wait() // 收尸（也免得 pid 被复用后误杀别人的进程组）
		logFile.Close()
	})

	if err := waitForPageTarget(port, logPath, 20*time.Second); err != nil {
		t.Fatalf("等私有浏览器起来失败: %v", err)
	}
	return &shotChrome{port: port, bin: e.bin}
}

// run 跑一次被测二进制，打的是**这个**浏览器的端口。
// （testEnv.run 打的是共享环境那个端口，所以这里另开一个。）
func (c *shotChrome) run(args ...string) (stdout, stderr string, code int) {
	full := append([]string{"--host", "127.0.0.1", "--port", strconv.Itoa(c.port)}, args...)
	cmd := exec.Command(c.bin, full...)
	var out, errBuf bytes.Buffer
	cmd.Stdout, cmd.Stderr = &out, &errBuf
	err := cmd.Run()
	if err != nil {
		ee, ok := err.(*exec.ExitError)
		if !ok {
			panic(fmt.Sprintf("跑二进制失败（不是退出码问题）: %v\nstderr: %s", err, errBuf.String()))
		}
		code = ee.ExitCode()
	}
	return out.String(), errBuf.String(), code
}

// ---- JSON 契约（按名字取值，与 internal/screenshot.go 的 tag 对齐） ----

type pxSizeJSON struct {
	Width  int `json:"width"`
	Height int `json:"height"`
}

type shotJSON struct {
	PNGBase64     string     `json:"png_base64"`
	ImagePx       pxSizeJSON `json:"image_px"`
	ViewportCssPx pxSizeJSON `json:"viewport_css_px"`
	DPR           float64    `json:"dpr"`
}

type observeJSON struct {
	URL     string `json:"url"`
	Actions []struct {
		Selector string `json:"selector"`
		Text     string `json:"text"`
		BBox     [4]int `json:"bbox"`
	} `json:"actions"`
}

// ---- 夹具的几何（internal/testdata/screenshot.html 里钉死的那组数） ----

const (
	fixtureMagenta = "#magenta-cta"
	fixtureLeft    = 40
	fixtureTop     = 20
	fixtureWidth   = 200
	fixtureHeight  = 100
)

var (
	rgbMagenta = [3]uint8{255, 0, 255}
	rgbWhite   = [3]uint8{255, 255, 255}
)

// magentaBBox 从 observe 的模型里取夹具元素，并断言它的 bbox 就是夹具钉的几何。
//
// 为什么连 bbox 一起断言：像素断言是「bbox × dpr = 像素」这条链的**下游**，
// 上游（observe 报的 bbox）飘了的话，下游即使算对了也说明不了什么。
func magentaBBox(t *testing.T, modelJSON string) [4]int {
	t.Helper()
	var m observeJSON
	if err := json.Unmarshal([]byte(modelJSON), &m); err != nil {
		t.Fatalf("observe 的输出不是合法 JSON: %v\n前 200 字符: %q", err, firstN(modelJSON, 200))
	}
	if !strings.Contains(m.URL, "screenshot.html") {
		t.Fatalf("observe 拿到的不是夹具页（url=%q）—— 后面的像素断言没有意义", m.URL)
	}
	selectors := make([]string, 0, len(m.Actions))
	for _, a := range m.Actions {
		selectors = append(selectors, a.Selector)
		if a.Selector != fixtureMagenta {
			continue
		}
		want := [4]int{fixtureLeft, fixtureTop, fixtureWidth, fixtureHeight}
		if a.BBox != want {
			t.Fatalf("%s 的 bbox = %v，夹具钉的是 %v —— "+
				"要么夹具被改了，要么 observe 报的盒子不对（%s 的几何是这条测试的尺子）",
				fixtureMagenta, a.BBox, want, "internal/testdata/screenshot.html")
		}
		return a.BBox
	}
	t.Fatalf("observe 的 actions 里没有 %s（拿到的选择器: %q）—— "+
		"它是 <button>，SEL 认得；不在里面说明元素没渲染出来或被判成了蜜罐", fixtureMagenta, selectors)
	return [4]int{}
}

func pixelRGB(img image.Image, x, y int) [3]uint8 {
	r, g, b, _ := img.At(x, y).RGBA()
	return [3]uint8{uint8(r >> 8), uint8(g >> 8), uint8(b >> 8)}
}

func firstN(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "…"
}

// ---- ① 对齐：这条测试的**要点** ----

func TestScreenshotAlignsWithObserveBBox(t *testing.T) {
	e := env(t)
	url := e.fixture + "/screenshot.html"

	// 三档 DPR。DPR=1 是最常见的一档（也最容易**假绿**：比例是 1，
	// 一个「完全忽略比例」的实现在这一档下每一步都对），所以 2 与 1.25 才是主力：
	// 2 逼出整数倍缩放，1.25 逼出小数倍（Chrome 在小数 DPR 下抹的是 ceil）。
	for _, tc := range []struct {
		name        string
		chromeFlags []string
		wantDPR     float64
	}{
		{"dpr1", nil, 1},
		{"dpr2", []string{"--force-device-scale-factor=2"}, 2},
		{"dpr1.25", []string{"--force-device-scale-factor=1.25"}, 1.25},
	} {
		t.Run(tc.name, func(t *testing.T) {
			c := startShotChrome(t, tc.chromeFlags...)

			// 页面用被测二进制自己的 navi 开过去（与 py 运行时同一条路）
			if _, errOut, code := c.run("navi", url); code != 0 {
				t.Fatalf("navi %s 退出码 = %d\nstderr: %s", url, code, errOut)
			}

			modelOut, errOut, code := c.run("observe", "--json")
			if code != 0 {
				t.Fatalf("observe 退出码 = %d\nstderr: %s", code, errOut)
			}
			bbox := magentaBBox(t, modelOut)

			shotOut, errOut, code := c.run("screenshot", "--json")
			if code != 0 {
				t.Fatalf("screenshot --json 退出码 = %d\nstderr: %s", code, errOut)
			}
			var shot shotJSON
			if err := json.Unmarshal([]byte(shotOut), &shot); err != nil {
				t.Fatalf("screenshot 的输出不是合法 JSON: %v\n前 200 字符: %q", err, firstN(shotOut, 200))
			}

			raw, err := base64.StdEncoding.DecodeString(shot.PNGBase64)
			if err != nil {
				t.Fatalf("png_base64 解不开: %v", err)
			}
			img, err := png.Decode(bytes.NewReader(raw))
			if err != nil {
				t.Fatalf("png_base64 不是一张能解码的 PNG: %v", err)
			}

			// ── ① 像素断言 ──
			//
			// 换算**写在测试里**，不调生产的 ScreenPx：调自己的函数等于让实现
			// 给自己作证（函数改了、断言跟着改，永远绿）。这里逐字复刻
			// internal/screenshot.go 文件头那条契约：screen_px = css_px × dpr。
			screen := func(cssX, cssY float64) (int, int) {
				return int(math.Round(cssX * shot.DPR)), int(math.Round(cssY * shot.DPR))
			}
			bx, by := float64(bbox[0]), float64(bbox[1])
			bw, bh := float64(bbox[2]), float64(bbox[3])

			// 两个**取值方向相反**的采样点，缺一个就会漏一整类错：
			//   · 靠原点那一侧（左上内 10px）—— 「比例被当成 1」（本该 ×2 却 ×1）时，
			//     元素在图上的真实位置整体外移，这个点会掉到元素**外面**（背景）
			//   · 正中 —— 「比例被夸大」（本该 ×1 却 ×2）时，这个点会被推到元素外
			// 再加一个元素**外**的采样点：它必须仍是背景色，否则「整张图是洋红」
			// 这种坏法会把上面两条一起骗过去。
			samples := []struct {
				name       string
				cssX, cssY float64
				want       [3]uint8
			}{
				{"元素左上内侧(+10,+10)", bx + 10, by + 10, rgbMagenta},
				{"元素正中", bx + bw/2, by + bh/2, rgbMagenta},
				{"元素外左上(-30,-10)", bx - 30, by - 10, rgbWhite},
			}
			for _, s := range samples {
				x, y := screen(s.cssX, s.cssY)
				if x < 0 || y < 0 || x >= img.Bounds().Dx() || y >= img.Bounds().Dy() {
					t.Errorf("[%s] bbox %v 上的 CSS 点 (%g,%g) × dpr %g → 图上 (%d,%d)，"+
						"落在 %d×%d 的图外 —— 换算把点推出去了",
						tc.name, bbox, s.cssX, s.cssY, shot.DPR, x, y, img.Bounds().Dx(), img.Bounds().Dy())
					continue
				}
				got := pixelRGB(img, x, y)
				if got != s.want {
					t.Errorf("[%s] 对齐错了：bbox 是 %v（CSS 像素，视口相对），"+
						"按契约 screen_px = css_px × dpr 算，CSS 点 (%g,%g) 应落在图上 (%d,%d)、"+
						"颜色 %v；实际是 %v。\n"+
						"（dpr=%g，图 %d×%d —— 「运营在截图上点的那个元素」就是靠这条换算，"+
						"错了就是点不准）",
						tc.name, bbox, s.cssX, s.cssY, x, y, s.want, got,
						shot.DPR, img.Bounds().Dx(), img.Bounds().Dy())
				}
			}

			// ── ② 三个数自洽：image_px == viewport_css_px × dpr ──
			// 容 1 设备像素：小数 DPR 下 Chrome 抹的是 ceil
			// （实测 dpr=1.25、视口 1002×559 → 图 1253×699，1002×1.25 = 1252.5）。
			for _, ax := range []struct {
				name string
				img  int
				css  int
			}{
				{"宽", shot.ImagePx.Width, shot.ViewportCssPx.Width},
				{"高", shot.ImagePx.Height, shot.ViewportCssPx.Height},
			} {
				want := float64(ax.css) * shot.DPR
				if math.Abs(float64(ax.img)-want) > 1 {
					t.Errorf("[%s] %s：image_px=%d 而 viewport_css_px×dpr = %d×%g = %.1f —— 不自洽的"+
						"三个数比没有数更糟（看着能叠 bbox，其实叠错）", tc.name, ax.name, ax.img, ax.css, shot.DPR, want)
				}
			}

			// ③ image_px 必须是 **PNG 实际**的尺寸（不是算出来的）
			if cfgW, cfgH := img.Bounds().Dx(), img.Bounds().Dy(); cfgW != shot.ImagePx.Width || cfgH != shot.ImagePx.Height {
				t.Errorf("[%s] image_px 报的是 %d×%d，但 PNG 实际是 %d×%d —— "+
					"这个字段的名字是「图有多大」，就不能是算出来的",
					tc.name, shot.ImagePx.Width, shot.ImagePx.Height, cfgW, cfgH)
			}

			// ── ④ 这条测试自己的前提：Chrome 真按我们要的 DPR 跑了 ──
			//
			// 放在最后，用 Errorf（不 Fatal）且说清是「前提没成立」：这一档要是
			// 没真跑起来（--force-device-scale-factor 被忽略），上面全部断言都会
			// 在 DPR=1 下跑一遍 —— 那正是**静默降级**（档位名还在，测的东西没了）。
			if math.Abs(shot.DPR-tc.wantDPR) > 1e-9 {
				t.Errorf("[%s] 前提没成立：浏览器报的 dpr 是 %g，我们要的是 %g —— "+
					"这一档没真跑起来，上面那几条断言不能算数（不是截图命令的问题，是起浏览器的方式）",
					tc.name, shot.DPR, tc.wantDPR)
			}
		})
	}
}

// ---- ② stdout 是裸 base64，--out 与它逐字节一致 ----

func TestScreenshotOutMatchesStdout(t *testing.T) {
	e := env(t)
	c := startShotChrome(t)
	if _, errOut, code := c.run("navi", e.fixture+"/screenshot.html"); code != 0 {
		t.Fatalf("navi 退出码 = %d\nstderr: %s", code, errOut)
	}

	outPath := filepath.Join(t.TempDir(), "shot.png")
	stdout, errOut, code := c.run("screenshot", "--out", outPath)
	if code != 0 {
		t.Fatalf("screenshot --out 退出码 = %d\nstderr: %s", code, errOut)
	}

	// stdout 上**只能**是 base64：forms/common.py 的 screenshot() 是
	// `subprocess.run([...]).stdout.strip()` 直接拿去当图片数据用的。
	trimmed := strings.TrimSpace(stdout)
	raw, err := base64.StdEncoding.DecodeString(trimmed)
	if err != nil {
		t.Fatalf("stdout 不是干净的 base64（common.py 会在这里崩掉）: %v\n前 80 字符: %q",
			err, firstN(trimmed, 80))
	}
	if !bytes.HasPrefix(raw, []byte("\x89PNG\r\n\x1a\n")) {
		t.Errorf("stdout 解出来的不是 PNG（头 8 字节不对）: % x", raw[:min(8, len(raw))])
	}

	fileBytes, err := os.ReadFile(outPath)
	if err != nil {
		t.Fatalf("--out 没写出文件: %v", err)
	}
	if !bytes.Equal(fileBytes, raw) {
		t.Errorf("--out 写的文件与 stdout 上的 base64 不是同一份图：文件 %d 字节，stdout %d 字节 —— "+
			"两边内容不一致时，人看的是文件、脚本存的是 stdout，谁也发现不了",
			len(fileBytes), len(raw))
	}

	// 文件本身得是一张能解码的图（不是半截数据）
	cfg, err := png.DecodeConfig(bytes.NewReader(fileBytes))
	if err != nil {
		t.Fatalf("--out 写出来的不是能解码的 PNG: %v", err)
	}
	if cfg.Width <= 0 || cfg.Height <= 0 {
		t.Errorf("--out 写出来的 PNG 尺寸是 %d×%d", cfg.Width, cfg.Height)
	}
}

// ---- ③ 失败时 stdout 必须**空**（「要么一份完整输出，要么什么都没有」） ----

func TestScreenshotFailsWithoutChrome(t *testing.T) {
	e := env(t)
	port, err := freePort() // freePort 会把监听关掉 → 这个端口上没人
	if err != nil {
		t.Fatalf("找空闲端口失败: %v", err)
	}

	stdout, _, code := runBinaryAt(t, e.bin, port, "screenshot")
	if code == 0 {
		t.Errorf("Chrome 连不上时退出码居然是 0 —— py 侧靠退出码判成败，会拿它当成功")
	}
	if strings.TrimSpace(stdout) != "" {
		t.Errorf("失败时 stdout 上有东西（%q）—— 约定是「要么一份完整输出，要么什么都没有」，"+
			"半份输出会被调用方当成成功", firstN(stdout, 120))
	}

	// --json 那条路同样不能吐半份
	stdout, _, code = runBinaryAt(t, e.bin, port, "screenshot", "--json")
	if code == 0 {
		t.Errorf("--json 模式下同样应当失败")
	}
	if strings.TrimSpace(stdout) != "" {
		t.Errorf("--json 失败时 stdout 上有东西（%q）", firstN(stdout, 120))
	}
}

// runBinaryAt 跑一次被测二进制，指定端口。shotChrome 之外的失败路径测试用它
// （那边连不上任何东西，没有浏览器可借）。
func runBinaryAt(t *testing.T, bin string, port int, args ...string) (stdout, stderr string, code int) {
	t.Helper()
	full := append([]string{"--host", "127.0.0.1", "--port", strconv.Itoa(port)}, args...)
	cmd := exec.Command(bin, full...)
	var out, errBuf bytes.Buffer
	cmd.Stdout, cmd.Stderr = &out, &errBuf
	err := cmd.Run()
	if err != nil {
		ee, ok := err.(*exec.ExitError)
		if !ok {
			t.Fatalf("跑二进制失败（不是退出码问题）: %v\nstderr: %s", err, errBuf.String())
		}
		code = ee.ExitCode()
	}
	return out.String(), errBuf.String(), code
}
