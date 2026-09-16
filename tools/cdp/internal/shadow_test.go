package internal

import (
	"strings"
	"testing"
)

// 2026-09-15 georgiapower 实测（Salesforce Lightning 表单页）:
// 页面自己报 document.querySelectorAll('button').length === 0、
// input === 0 —— 表单全在 shadow root 里。于是 cdp form / cdp click /
// cdp scroll 一律报 "element not found"。
//
// 同页 A/B 已证实不是调用姿势的问题:
//   注入一个 **light DOM** 输入框 → cdp form 填得进去（值 = HELLO）
//   同一个 cdp form 打 shadow DOM 里的框 → element not found
//
// shadow DOM 不影响 getBoundingClientRect()，所以「滚动→算中心点→拟人点击」
// 这套逻辑一行都不用改 —— 坏的只有**解析**这一步。下面这些测试钉住解析。
//
// 覆盖的 8 个 JS 生成器是 form.go 里的全部选择器入口；
// client.go 的 GetElementCenter / IsElementVisible / ScrollIntoView
// 走的是同一条解析路径，由 shadow_integration_test.go 行为验证。

func TestSelectorBuildersPierceShadowRoots(t *testing.T) {
	cases := []struct {
		name string
		js   string
	}{
		{"isDatePickerJS", isDatePickerJS("#field")},
		{"buildClearJS", buildClearJS("#field")},
		{"buildBlurJS", buildBlurJS("#field")},
		{"buildCheckStateJS", buildCheckStateJS("#field")},
		{"buildSelectOptionJS", buildSelectOptionJS("#field", "opt")},
		{"isNativeSelectJS", isNativeSelectJS("#field")},
		{"findControlJS", findControlJS("#wrapper")},
		{"findCustomOptionJS", findCustomOptionJS("#wrapper", "opt")},
	}

	for _, c := range cases {
		// 顶层解析必须走穿透助手；裸 document.querySelector 在 shadow 页上恒为 null
		if strings.Contains(c.js, "document.querySelector(") {
			t.Errorf("%s 仍在用裸 document.querySelector —— 穿不透 shadow root", c.name)
		}
		if !strings.Contains(c.js, "__cdpQ") {
			t.Errorf("%s 没有调用穿透解析器 __cdpQ", c.name)
		}
	}
}

// findControlJS / findCustomOptionJS 是在**已解析到的 wrapper 内部**再找子元素。
// 子元素自己也可能在更深的 shadow root 里，所以容器内查找同样要穿透。
func TestWrapperLocalLookupsAlsoPierce(t *testing.T) {
	for _, c := range []struct{ name, js string }{
		{"findControlJS", findControlJS("#wrapper")},
		{"findCustomOptionJS", findCustomOptionJS("#wrapper", "opt")},
	} {
		if !strings.Contains(c.js, "__cdpQAIn") && !strings.Contains(c.js, "__cdpQIn") {
			t.Errorf("%s 的容器内查找没穿透 —— 孙级 shadow root 里的选项会找不到", c.name)
		}
	}
}

// 助手本身要定义齐全，且必须递归（shadow root 可以嵌套；
// georgiapower 那页实测有 76 个，是嵌套的）。
func TestPierceHelperIsRecursiveAndComplete(t *testing.T) {
	js := withPierce("(function(){ return 1; })()")
	for _, want := range []string{"__cdpQ", "__cdpQA", "__cdpQIn", "__cdpQAIn", "shadowRoot"} {
		if !strings.Contains(js, want) {
			t.Errorf("穿透助手缺少 %s", want)
		}
	}
	// 原始脚本要原样保留在助手之后
	if !strings.HasSuffix(js, "(function(){ return 1; })()") {
		t.Error("withPierce 没有把原脚本接在助手后面")
	}
}
