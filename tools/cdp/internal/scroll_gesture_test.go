package internal

import (
	"math"
	"testing"
)

// 滚动那一族的两件事：方向别被 0×0 的隐藏件带反、可见比例别落进 NaN。
//
// 现场（2026-09-29 afrotech 26238706 / 26238922）：页脚订阅框在**第 21 屏**，
// 而页面上还有一个同名 [hidden] 里的 0×0 重复件排在文档序第一位。老实现里
// 那个 0×0 的 centerY 恒是 0 ⇒ 老判据 centerY < vh/2 ⇒ 每一轮都判成「在视口
// 上方」⇒ 每轮手势把页面往**远处**拖 ⇒ 20 轮结构上不可能收敛（实测 cdp scroll
// 46.9 秒、scrollY 从 17348 被拖到 8041）。下面两条钉住「不猜方向」。

func TestScrollGestureFor_RefusesZeroBox(t *testing.T) {
	// centerY=0 正是那个隐藏件的形状。要么拒，要么把页面拖反 —— 没有第三种。
	if _, err := scrollGestureFor(0, 375, 812, 0, 0); err == nil {
		t.Fatal("0×0 的元素必须拒掉方向，不许猜（afrotech 那个隐藏重复件就是这么把页面拖反的）")
	}
}

func TestScrollGestureFor_DirectionFollowsElement(t *testing.T) {
	vw, vh := 375.0, 812.0

	// 元素在视口**下方**（页脚在第 21 屏，centerY=17319）⇒ 上滑手势
	// （起点在下、终点在上），页面才会往下滚、元素才靠得近。
	below, err := scrollGestureFor(17319, vw, vh, 42, 42)
	if err != nil {
		t.Fatalf("有盒子的元素不该被拒：%v", err)
	}
	if len(below) != 15 {
		t.Fatalf("手势点应是指纹的一部分（15 个采样），实得 %d", len(below))
	}
	if !(below[0][1] > below[len(below)-1][1]) {
		t.Fatalf("元素在视口下方时手势方向反了：起点 y=%.0f 终点 y=%.0f（要上滑）",
			below[0][1], below[len(below)-1][1])
	}

	// 元素在视口**上方** ⇒ 下滑手势（起点在上、终点在下）。
	above, err := scrollGestureFor(100, vw, vh, 42, 42)
	if err != nil {
		t.Fatalf("有盒子的元素不该被拒：%v", err)
	}
	if !(above[len(above)-1][1] > above[0][1]) {
		t.Fatalf("元素在视口上方时手势方向反了：起点 y=%.0f 终点 y=%.0f（要下滑）",
			above[0][1], above[len(above)-1][1])
	}
}

func TestScrollRoundsForScreens_FollowsDistance(t *testing.T) {
	// 固定 20 轮正是「第 21 屏刚好差一点」撞超时的成因；轮数要跟距离走，
	// 但仍要有个下限（近的也至少磨几轮，别一上来就精确那一下）。
	if got := scrollRoundsForScreens(21); got != 20 {
		t.Errorf("21 屏应封顶 20 轮，实得 %d", got)
	}
	if got := scrollRoundsForScreens(0.2); got != 4 {
		t.Errorf("0.2 屏应取下限 4 轮，实得 %d", got)
	}
	if got := scrollRoundsForScreens(5); got != 7 {
		t.Errorf("5 屏应是 ceil(5)+2=7 轮，实得 %d", got)
	}
}

// visibleRatio 是 IsElementVisible 的新算式（统一两条路）。
// 老 JS 算式 totalArea = width*height，元素 0×0 时 0/0 = NaN，而 `NaN >= 0.8`
// 恒假 —— 「不可见」这个结论是碰巧成立的，且算式对视口外的矩形不 clamp 会算负数。
func TestVisibleRatio_ZeroSizeIsZeroNotNaN(t *testing.T) {
	got := visibleRatio(map[string]float64{"x": 0, "y": 0, "width": 0, "height": 0}, 375, 812)
	if math.IsNaN(got) || got != 0 {
		t.Fatalf("0×0 的可见比例 = %v（要 0，不许 NaN）", got)
	}
	if got >= 0.8 {
		t.Fatalf("0×0 不该被判可见：%v", got)
	}
}

func TestVisibleRatio_ClampsOutOfViewportRect(t *testing.T) {
	// 完全在视口下方（afrotech 那个真身在第 21 屏）。
	got := visibleRatio(map[string]float64{"x": 42, "y": 17319, "width": 42, "height": 42}, 375, 812)
	if got < 0 || got > 1 {
		t.Fatalf("视口外的可见比例 = %v（要落在 [0,1]；负数是算错了，不是「更不可见」）", got)
	}
	if got != 0 {
		t.Fatalf("完全在视口下方应露出 0，实得 %v", got)
	}
}

func TestVisibleRatio_FullyAndPartlyVisible(t *testing.T) {
	if got := visibleRatio(map[string]float64{"x": 10, "y": 10, "width": 100, "height": 100}, 375, 812); got != 1 {
		t.Fatalf("完整可见应是 1，实得 %v", got)
	}
	// 上半在视口里（一半露出）。
	if got := visibleRatio(map[string]float64{"x": 10, "y": -50, "width": 100, "height": 100}, 375, 812); got != 0.5 {
		t.Fatalf("露出一半应是 0.5，实得 %v", got)
	}
}
