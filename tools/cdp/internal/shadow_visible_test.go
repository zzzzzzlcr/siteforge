package internal

import (
	"strings"
	"testing"
)

// 内核解析助手的两条规矩（2026-09-29 afrotech 26238922 逼出来的）：
// 「第一个匹配」要看**可见性**；落点判据要有**一份**（三处手写实现下沉到这里）。
// 这两条都在注入段里，改坏了不会有人报错，只会「元素凭空变成另一个 / 判据分家」。

func TestPierceResolver_PrefersVisibleMatch(t *testing.T) {
	if !strings.Contains(pierceJS, "__cdpVisible") {
		t.Fatal("注入段里没有 __cdpVisible —— 「第一个可见的匹配」这条规矩没了")
	}
	if !strings.Contains(pierceJS, "if (__cdpVisible(all[i])) return all[i];") {
		t.Error("__cdpQIn 没有先挑可见的匹配（afrotech 上它会选中 [hidden] 里的 0×0 重复件）")
	}
	if !strings.Contains(pierceJS, "return all.length ? all[0] : null;") {
		t.Error("一个可见的都没有时必须退回文档序第一个 —— 否则会从「不可见」变成「找不到元素」")
	}
}

func TestPierceResolver_HasOneLandingVerdict(t *testing.T) {
	// __cdpReachesAt 是「这一点上是不是 el」的**唯一**一份实现。
	for _, want := range []string{"__cdpReachesAt", "__cdpTopAt", "pointerEvents", "__cdpDescribe"} {
		if !strings.Contains(pierceJS, want) {
			t.Errorf("注入段里缺 %q（落点判据要么少了本体，要么没跳过 pointer-events:none 的空遮罩）", want)
		}
	}
}

// 2026-09-28 georgiapower 真站实测：<textarea> 被 isDatePickerJS 当成「wrapper」
// 进去找 input ⇒ 必然报 no input found in wrapper ⇒ FillText 连填值的路都没走到
// （两个不同选择器的 *Street 字段报同一个错）。补丁：把 TEXTAREA 排除出 wrapper 分支。
func TestIsDatePickerJS_DoesNotTreatTextareaAsWrapper(t *testing.T) {
	js := isDatePickerJS("textarea#street")
	if !strings.Contains(js, "el.tagName !== 'INPUT' && el.tagName !== 'TEXTAREA'") {
		t.Fatalf("textarea 还在 wrapper 分支里 —— 填值会在找 input 那一步就报错：%s", js)
	}
}
