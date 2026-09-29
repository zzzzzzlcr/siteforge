package internal

import (
	"strings"
	"testing"
)

// 「动手之前」的落点判据（docs/交接-2026-09-29 八条 §3/§7）的**纯函数**那一半。
//
// 为什么值得单钉：这件事原来在三处各写一遍（warthunder 手写 py 的 _check_eula_once /
// json_executor._smart_form / 骨架的 _cover_once），口径还不一致（单点 vs 整摞、
// 要不要跳 pointer-events:none），于是同一张页面在三条线上行为不同。下沉到 cdp 之后
// 三条线自动都有，但「口径一致」这件事只有内核那一份判据能保证 —— 下面这几条就是钉它。

func TestLandedElsewhereError_NamesTheObstacle(t *testing.T) {
	// 2026-09-29 afrotech 26238922：点 (0,0) 那条路上「回执一切正常」，是本项目最贵的
	// 一类失败。拒点可以，但**必须说清那一点上站着谁**，否则调用方无从改起。
	err := landedElsewhereError("#subscribe", `<div class="qc-cmp-cleanslate" id="cover">`)
	if err == nil {
		t.Fatal("落点不是目标时必须拒点，不许默默点下去")
	}
	for _, want := range []string{"#subscribe", "qc-cmp-cleanslate", "拒绝点击"} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("拒点理由里要有 %q（人得看得出那一点上站着谁）：%s", want, err)
		}
	}
}

func TestLandedElsewhereError_HandlesUnknowablePoint(t *testing.T) {
	// 取不到节点时也要有一句人话，不许渲染成空括号。
	err := landedElsewhereError("#x", "")
	if err == nil || !strings.Contains(err.Error(), "取不到节点") {
		t.Fatalf("取不到节点时要说清，而不是留空：%v", err)
	}
}

// 「判据本体只有一份」是这次下沉的全部意义。buildReachProbeJS 必须把候选点喂给
// 内核的 __cdpReachesAt，而不是再自己实现一遍 elementFromPoint + contains。
// （本仓反复踩的坑：两份判据必然漂移，而漂移那天测试还在绿。）
func TestBuildReachProbeJS_DelegatesToKernelHelper(t *testing.T) {
	js := buildReachProbeJS("#go", [][2]float64{{1, 2}, {3, 4}})
	// 只看注入段之后的本体：内核那一份（pierceJS）本来就定义了 elementFromPoint
	// 的回退 —— 要钉的是「这份脚本没有**再实现一遍**判据」。
	body := strings.TrimPrefix(js, pierceJS)
	if !strings.Contains(body, "__cdpReachesAt") {
		t.Errorf("落点探测没有用内核助手 __cdpReachesAt —— 与 click / form 的判据会分家：%s", body)
	}
	if strings.Contains(body, "elementFromPoint") {
		t.Errorf("落点探测自己实现了一遍命中（elementFromPoint）—— 那正是要下沉掉的东西：%s", body)
	}
	for _, want := range []string{"#go", "[[1,2],[3,4]]", "reach"} {
		if !strings.Contains(body, want) {
			t.Errorf("落点探测里缺 %q：%s", want, body)
		}
	}
}

// probeTargetJS 报的「第几个」必须是真正会被点的那一个。__cdpQ 现在先挑**可见的**，
// 所以 match_index 不能还写死 0（afrotech 上那两条同名按钮，可见的是 #2）。
func TestProbeTargetJS_ReportsVisibleIndex(t *testing.T) {
	js := probeTargetJS("button[aria-label=Subscribe]")
	if !strings.Contains(js, "__cdpVisible(els[k])") {
		t.Errorf("探测没有挑可见的那一个 —— 回执里报的 index 会与真正点下去的分家：%s", js)
	}
	if !strings.Contains(js, "match_index: vi") {
		t.Errorf("match_index 还写死/没用可见优先的下标：%s", js)
	}
}
