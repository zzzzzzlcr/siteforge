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

// TestScreenPxIsTheDocumentedMapping 钉住 screen_px = css_px × dpr。
func TestScreenPxIsTheDocumentedMapping(t *testing.T) {
	s := &Shot{
		ImagePx:       PixelSize{Width: 2400, Height: 1314},
		ViewportCssPx: PixelSize{Width: 1200, Height: 657},
		DPR:           2,
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

// TestCheckScaleRejectsContradiction 把「三个数必须自洽」这条判据钉住 ——
// 它对应的正是本项目被咬过的那次：**比例错了，但没有任何东西说话**。
func TestCheckScaleRejectsContradiction(t *testing.T) {
	// ① 实测值（Chrome 150 headless，--force-device-scale-factor=1.25，
	//    视口 1002×559 → 图 1253×699）：小数 DPR 下 Chrome 抹的是 ceil，
	//    1002×1.25 = 1252.5 → 1253。所以差 1 设备像素必须**放行**，否则这条闸门
	//    会在完全正常的机器上常红，然后被人删掉。
	if err := checkScale(PixelSize{1253, 699}, PixelSize{1002, 559}, 1.25); err != nil {
		t.Errorf("小数 DPR 的正常取整被拒了: %v", err)
	}
	// ② 整数 DPR 的精确值
	if err := checkScale(PixelSize{2400, 1314}, PixelSize{1200, 657}, 2); err != nil {
		t.Errorf("1:2 的正常取值被拒了: %v", err)
	}

	for _, c := range []struct {
		name string
		img  PixelSize
		css  PixelSize
		dpr  float64
	}{
		// 谎报比例：图是真·设备像素，却说 DPR=1（「把 2 倍的图当成 1 倍」——
		// 运营在图上点的位置会与 py 真去点的位置差一倍）
		{"图是 2 倍却说 DPR=1", PixelSize{2400, 1314}, PixelSize{1200, 657}, 1},
		// 报成 CSS 像素：图本身就是 2 倍设备像素，却按 CSS 像素报尺寸
		{"image_px 报成 CSS 像素", PixelSize{1200, 657}, PixelSize{1200, 657}, 2},
		// 只有一轴错（横向对了、纵向被裁）
		{"只有高对不上", PixelSize{2400, 700}, PixelSize{1200, 657}, 2},
	} {
		err := checkScale(c.img, c.css, c.dpr)
		if err == nil {
			t.Errorf("%s：应当报错，结果放行了（image=%v css=%v dpr=%g）", c.name, c.img, c.css, c.dpr)
			continue
		}
		// 错误信息必须自己把三个数摊开（不然调用方只知道「失败了」，
		// 不知道自己那套假设哪里崩了）
		if !strings.Contains(err.Error(), "image_px") && !strings.Contains(err.Error(), "viewport_css_px") {
			t.Errorf("%s：错误信息里没有把三个数摊开，看不出是什么对不上: %v", c.name, err)
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
		DPR:           2,
		raw:           []byte{1, 2, 3},
	})
	if err != nil {
		t.Fatalf("marshal 失败: %v", err)
	}
	var got map[string]json.RawMessage
	if err := json.Unmarshal(b, &got); err != nil {
		t.Fatalf("unmarshal 失败: %v", err)
	}

	want := []string{"png_base64", "image_px", "viewport_css_px", "dpr"}
	// 顶层**恰好**这四个：多出来的（比如把解码后的字节也塞进来）会让
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

	var dpr float64
	if err := json.Unmarshal(got["dpr"], &dpr); err != nil {
		t.Errorf("dpr 应当是**数字**（可以带小数，别写成字符串）: %v", err)
	} else if math.Abs(dpr-2) > 1e-9 {
		t.Errorf("dpr = %v，想要 2", dpr)
	}
}

func keysOf(m map[string]json.RawMessage) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	return out
}
