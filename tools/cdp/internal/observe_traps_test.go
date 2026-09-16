package internal

import (
	"encoding/json"
	"strings"
	"testing"
)

// 三条陷阱用 fixture 页面在真浏览器上验（见 observe_integration_test.go）。
// 这里钉的是**脚本本身必须具备的特征** —— 少一条就必然静默出错。
func TestObserveJSMustHandleShadowText(t *testing.T) {
	js := observeJS()
	// ShadowRoot 没有 innerText（那是 HTMLElement 的属性）。
	// 正确做法：shadow root 取子元素的 innerText。
	if strings.Contains(js, "r.innerText") || strings.Contains(js, "root.innerText") {
		t.Error("直接对 shadow root 取 innerText —— ShadowRoot 上没有这个属性，会得到 undefined")
	}
	if !strings.Contains(js, ".children") {
		t.Error("没有遍历 shadow root 的子元素来收文本 —— page_text 在 shadow 页上会几乎为空")
	}
}

func TestObserveJSMustNotTrustElementsFromPointAlone(t *testing.T) {
	js := observeJS()
	// elementsFromPoint 不穿透 shadow（返回 host）。只判断「元素在不在命中栈里」
	// 会把所有 shadow 元素误判为被遮挡（实测 5/5 假阳性）。
	if !strings.Contains(js, "composedAncestors") {
		t.Error("遮挡判定没有走合成树祖先链 —— shadow 元素会被整片误判为被遮挡")
	}
}

func TestObserveJSMustClimbThroughShadowHosts(t *testing.T) {
	js := observeJS()
	// parentElement 出不了 shadow 边界 → region 全部退化成 body
	if !strings.Contains(js, "getRootNode") || !strings.Contains(js, ".host") {
		t.Error("层级遍历没有经 getRootNode().host —— region/祖先链在 shadow 页上会断掉")
	}
}

func TestObserveJSMustUsePierceHelper(t *testing.T) {
	js := observeJS()
	// 全局约束：穿透只用内核的助手，不各自重写一套
	if !strings.Contains(js, "__cdpQA") {
		t.Error("没有用内核的 __cdpQA 穿透查询助手（规格 §4.1：不要各自重写一套）")
	}
}

func TestObserveJSMustNotEmitSemanticJudgements(t *testing.T) {
	js := observeJS()
	// 规格 D11：observe 给感知不给判断
	for _, banned := range []string{"primary CTA", "importance", `"intent"`} {
		if strings.Contains(js, banned) {
			t.Errorf("observe 脚本里出现了语义判断 %q —— 那是 cognition，不是 perception", banned)
		}
	}
}

func TestPageModelJSONShape(t *testing.T) {
	raw := `{"url":"u","title":"t","page_text":"p","shadow_roots":2,
	         "actions":[{"selector":"#a","alternates":[],"stability":"high","text":"Go",
	                     "role":"button","tag":"BUTTON","type":null,"visible":true,
	                     "occluded_by":null,"shadow_depth":2,"frame_path":["main"],
	                     "bbox":[1,2,3,4],"region":"hero","above_fold":true,
	                     "relative_size":1.8,"peer_count":3,"z_index":"auto",
	                     "contrast":"high","nearby_text":["x"]}],
	         "fields":[],"option_groups":[],"obstructions":[]}`
	var m PageModel
	if err := json.Unmarshal([]byte(raw), &m); err != nil {
		t.Fatalf("契约 JSON 解析失败: %v", err)
	}
	if len(m.Actions) != 1 || m.Actions[0].Selector != "#a" || m.Actions[0].ShadowDepth != 2 {
		t.Fatalf("字段没对上: %+v", m.Actions)
	}
}
