package internal

import (
	"encoding/json"
	"math"
	"strings"
	"testing"
)

// 这一组是**纯函数**的测试：不碰浏览器，钉的是 internal/screenshot.go 里
// 那条坐标换算与那份 JSON 契约。真·端到端（真截图 + 真解码 + 真取像素）
// 在 cmd/screenshot_test.go。

// TestScreenPxIsTheDocumentedMapping 钉住 screen_px = css_px × **scale**。
//
// ⚠️ 这一条里的 dpr 是**故意说谎**的（自称 1，比例其实是 2）：换算必须只看 scale。
// 为什么非要有这一刀（2026-10-08，job-f9b0090a9472）：反检测浏览器会编
// devicePixelRatio —— 实测那台窗口自称 3、真画出来是 1 倍（连 CSS 的
// `(resolution: 3dppx)` 媒体查询都点头）。换算要是跟着 dpr 走，整体错一个比例，
// 症状是「运营在图上点的元素不是 py 点的那个」，而不是报错。
func TestScreenPxIsTheDocumentedMapping(t *testing.T) {
	s := &Shot{
		ImagePx:       PixelSize{Width: 2400, Height: 1314},
		ViewportCssPx: PixelSize{Width: 1200, Height: 657},
		Scale:         2, // **量出来的**（image_px ÷ viewport_css_px）
		DPR:           1, // 它自称的（谎）—— 不许拿它换算
	}
	for _, c := range []struct {
		cssX, cssY float64
		wantX      int
		wantY      int
		ok         bool
	}{
		// observe 给的就是这些数（fixture screenshot.html 的元素在 CSS (40,20) 200×100）
		{40, 20, 80, 40, true},            // 元素左上角
		{140, 70, 280, 140, true},         // 元素中心
		{0, 0, 0, 0, true},                // 视口原点
		{1199.4, 656.4, 2399, 1313, true}, // 右下角（含小数：先乘后四舍五入）
		{1200, 657, 2400, 1314, false},    // 越界（右下边界外一像素）
		{-1, 10, -2, 20, false},           // 视口左侧之外
	} {
		x, y, ok := s.ScreenPx(c.cssX, c.cssY)
		if x != c.wantX || y != c.wantY || ok != c.ok {
			t.Errorf("ScreenPx(%g,%g) = (%d,%d,%v)，想要 (%d,%d,%v)",
				c.cssX, c.cssY, x, y, ok, c.wantX, c.wantY, c.ok)
		}
	}
}

// TestMeasureScaleIsTheOnlyBridge 钉住「比例是**量**出来的」这条判据。
//
// 它对应两件事：
//   - 本项目被咬过的那次：**比例错了，而没有任何东西说话**（所以不能默认 1）
//   - 2026-10-08 那次：浏览器**自称的** dpr 与真实渲染出来的比例**可以不一样**，
//     而任务本来就要跑在不同机型/不同指纹上 —— 那不是异常，是常态。
//
// 所以这里完全不问 dpr：比例只从图与视口**自己的尺寸**来。
func TestMeasureScaleIsTheOnlyBridge(t *testing.T) {
	// ① 对得上的必须放行，并给回正确的比例
	for _, c := range []struct {
		name string
		img  PixelSize
		css  PixelSize
		want float64
	}{
		// 那台会撒谎的窗口的实测值（job-f9b0090a9472：视口 377×757 → 图 377×757，
		// 而它自称 dpr=3）。老实现就是在这组数上把**每一张**图都拒了。
		{"1 倍（自称 3 的那台窗口）", PixelSize{377, 757}, PixelSize{377, 757}, 1},
		{"2 倍", PixelSize{2400, 1314}, PixelSize{1200, 657}, 2},
		// 实测（Chrome 150 headless，--force-device-scale-factor=1.25）：
		// 1002×1.25 = 1252.5，Chrome 抹的是 ceil → 1253。两个轴各自取整，
		// 允许差一点；差 1 设备像素就拒的话，这条闸门会在完全正常的机器上常红。
		{"1.25 倍（Chrome 抹 ceil）", PixelSize{1253, 699}, PixelSize{1002, 559}, 1.25},
	} {
		got, err := measureScale(c.img, c.css)
		if err != nil {
			t.Errorf("%s：正常的图被拒了: %v", c.name, err)
			continue
		}
		if math.Abs(got-c.want) > 0.01 {
			t.Errorf("%s：量出来的比例是 %g，想要 %g", c.name, got, c.want)
		}
	}

	// ② 对不上的必须**拒** —— 这不是「算个比例」的函数，是「这张图是不是这块视口」的判据
	for _, c := range []struct {
		name string
		img  PixelSize
		css  PixelSize
	}{
		// 整页图：高多出来一大截（Chrome 哪天把 captureBeyondViewport 的默认值改了）
		{"整页图（高多出来一大截）", PixelSize{1200, 5000}, PixelSize{1200, 657}},
		// 拍到了**别的**窗口/别的页：两个轴的比例差着几十个百分点
		{"两个轴不是一个比例", PixelSize{2400, 1314}, PixelSize{377, 757}},
		// 只有一轴对得上（横向对了、纵向被裁）
		{"只有一轴对得上", PixelSize{1200, 700}, PixelSize{1200, 657}},
	} {
		_, err := measureScale(c.img, c.css)
		if err == nil {
			t.Errorf("%s：应当报错，结果放行了（image=%v css=%v）", c.name, c.img, c.css)
			continue
		}
		// 错误信息必须自己把两个数摊开（不然调用方只知道「失败了」，
		// 不知道自己那套假设哪里崩了）
		if !strings.Contains(err.Error(), "image_px") || !strings.Contains(err.Error(), "viewport_css_px") {
			t.Errorf("%s：错误信息里没有把两个数摊开，看不出是什么对不上: %v", c.name, err)
		}
	}
}

// TestScaleNoteSpeaksOnlyWhenTheBrowserLies 钉住那句话的边界：
// 对得上时**一个字都不说**（不编话），对不上时把两个数都摆出来。
func TestScaleNoteSpeaksOnlyWhenTheBrowserLies(t *testing.T) {
	// 一致 → 闭嘴
	if s := scaleNote(3, 3); s != "" {
		t.Errorf("自称的与量出来的一致时不该有话说，实际：%q", s)
	}
	// 1.25 那档：1002×1.25 = 1252.5，图是 1253 → 量出来 1.2505…，
	// 与自称的 1.25 差千分之零点四 —— 这是取整，不是撒谎
	if s := scaleNote(1.25, 1253.0/1002.0); s != "" {
		t.Errorf("取整那点差不是谎，不该有话说，实际：%q", s)
	}

	// 撒谎 → 两个数都要摆出来（3 与 1）
	s := scaleNote(3, 1)
	if s == "" {
		t.Fatalf("自称 dpr=3、量出来 1 倍 —— 这句话必须说出来（不然调用方会以为一致）")
	}
	for _, want := range []string{"3", "1"} {
		if !strings.Contains(s, want) {
			t.Errorf("这句话里没说清 %q 这个数，只有：%q", want, s)
		}
	}
}

// TestShotJSONContractFieldNames 钉字段名 —— Console / vision.inspect / py 侧
// 都是按名字取值的（照 PageModel 的先例：改字段名等于改契约）。
func TestShotJSONContractFieldNames(t *testing.T) {
	b, err := json.Marshal(&Shot{
		PNGBase64:     "AAAA",
		ImagePx:       PixelSize{Width: 2400, Height: 1314},
		ViewportCssPx: PixelSize{Width: 1200, Height: 657},
		DPR:           3,
		Scale:         2,
		ScaleNote:     "自称 3、量出来 2",
		raw:           []byte{1, 2, 3},
	})
	if err != nil {
		t.Fatalf("marshal 失败: %v", err)
	}
	var got map[string]json.RawMessage
	if err := json.Unmarshal(b, &got); err != nil {
		t.Fatalf("unmarshal 失败: %v", err)
	}

	// dpr = 浏览器**自称**的；scale = **量出来的**（换算只看它）；scale_note = 两者
	// 对不上时的那句话（对得上就是空串，但**字段还在** —— 形状固定才好取值）。
	want := []string{"png_base64", "image_px", "viewport_css_px", "dpr", "scale", "scale_note"}
	// 顶层**恰好**这六个：多出来的（比如把解码后的字节也塞进来）会让
	// stdout 上那份 JSON 变成几百 KB 的垃圾，而消费者看不出来。
	if len(got) != len(want) {
		t.Errorf("顶层字段是 %q，应当恰好是 %q", keysOf(got), want)
	}
	for _, k := range want {
		if _, ok := got[k]; !ok {
			t.Errorf("缺字段 %q（py / Console 按名字取值，改名等于改契约）", k)
		}
	}

	for _, sub := range []string{"image_px", "viewport_css_px"} {
		var m map[string]json.RawMessage
		if err := json.Unmarshal(got[sub], &m); err != nil {
			t.Errorf("%s 不是对象: %v", sub, err)
			continue
		}
		if len(m) != 2 || m["width"] == nil || m["height"] == nil {
			t.Errorf("%s 里应当是 width/height 两个数，实际是 %q", sub, keysOf(m))
		}
	}

	for _, num := range []struct {
		key  string
		want float64
	}{{"dpr", 3}, {"scale", 2}} {
		var v float64
		if err := json.Unmarshal(got[num.key], &v); err != nil {
			t.Errorf("%s 应当是**数字**（可以带小数，别写成字符串）: %v", num.key, err)
		} else if math.Abs(v-num.want) > 1e-9 {
			t.Errorf("%s = %v，想要 %v", num.key, v, num.want)
		}
	}

	var note string
	if err := json.Unmarshal(got["scale_note"], &note); err != nil {
		t.Errorf("scale_note 应当是字符串（空串表示「一致，没话说」）: %v", err)
	} else if note != "自称 3、量出来 2" {
		t.Errorf("scale_note = %q，想要 %q", note, "自称 3、量出来 2")
	}
}

func keysOf(m map[string]json.RawMessage) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	return out
}
