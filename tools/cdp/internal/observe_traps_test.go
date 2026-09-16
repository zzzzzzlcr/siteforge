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

// jsFuncBody 从脚本里取出 `function name(…){ … }` 的正文（含花括号）。
//
// 为什么要它：断言「整个脚本里含某个助手名」是**空转的** —— 同一个助手往往被多处
// 使用，把某一处退回错误写法，全脚本照样含这个名字。2026-09-16 变异实测：把
// occludedBy 的合成树判定退回「元素在不在命中栈里」、把 region 退回 parentElement
// 遍历，只查全脚本的两条断言**依然全绿**。所以断言必须落在**调用点所在的函数体**内。
//
// 返回的正文**已剥掉注释**（见 stripJSComments）—— 断言不该被一段注释满足。
// 配平花括号时跳过引号内字符（字符串里的 } 不算）。局限：不认识正则字面量 ——
// 别在正文里写 /}/ 这种。
func jsFuncBody(t *testing.T, js, name string) string {
	t.Helper()
	marker := "function " + name + "("
	i := strings.Index(js, marker)
	if i < 0 {
		t.Fatalf("脚本里找不到 function %s( —— 断言无从落地", name)
	}
	open := strings.Index(js[i:], "{")
	if open < 0 {
		t.Fatalf("function %s( 后面没有 {", name)
	}
	start := i + open
	depth, quote := 0, byte(0)
	for k := start; k < len(js); k++ {
		c := js[k]
		if quote != 0 {
			if c == '\\' {
				k++
			} else if c == quote {
				quote = 0
			}
			continue
		}
		switch c {
		case '\'', '"':
			quote = c
		case '{':
			depth++
		case '}':
			depth--
			if depth == 0 {
				return stripJSComments(js[start : k+1])
			}
		}
	}
	t.Fatalf("function %s 的花括号不配平", name)
	return ""
}

// stripJSComments 去掉 // 行注释与 /* */ 块注释。
//
// 为什么返回的正文要先剥注释：不剥的话，函数体内塞一行 `// composedAncestors`
// 就能满足断言 —— 而**「断言可以被一段注释满足」正是这轮修复要根治的病根**
// （变异 M6 实测：不剥注释时它就是绿的）。
// 断言必须落在代码上，注释不算代码。
//
// 局限：不认识字符串/正则字面量里的 //。本脚本被断言的这两个函数体内没有这种情况。
func stripJSComments(s string) string {
	var b strings.Builder
	for i := 0; i < len(s); {
		if s[i] == '/' && i+1 < len(s) && s[i+1] == '/' {
			for i < len(s) && s[i] != '\n' {
				i++
			}
			continue
		}
		if s[i] == '/' && i+1 < len(s) && s[i+1] == '*' {
			i += 2
			for i+1 < len(s) && !(s[i] == '*' && s[i+1] == '/') {
				i++
			}
			i += 2
			if i > len(s) {
				i = len(s)
			}
			continue
		}
		b.WriteByte(s[i])
		i++
	}
	return b.String()
}

func TestObserveJSMustNotTrustElementsFromPointAlone(t *testing.T) {
	js := observeJS()
	// elementsFromPoint 不穿透 shadow（返回 host）。只判断「元素在不在命中栈里」
	// 会把所有 shadow 元素误判为被遮挡（实测 5/5 假阳性）。
	//
	// 断言落在 occludedBy 的**函数体内**，不是全脚本：region 也在用同一个助手，
	// 只查全脚本的话，把这里退回错误写法照样绿（变异 M2 实测）。
	if body := jsFuncBody(t, js, "occludedBy"); !strings.Contains(body, "composedAncestors") {
		t.Error("occludedBy 没有走合成树祖先链 —— shadow 元素会被整片误判为被遮挡")
	}
}

func TestObserveJSMustClimbThroughShadowHosts(t *testing.T) {
	js := observeJS()
	// parentElement 出不了 shadow 边界 → region 全部退化成 body。
	//
	// 两段断言都要落在函数体内：把 region 退回 parentElement-only 遍历时，全脚本
	// 仍有 getRootNode/.host（shadowDepth 与助手本身在用），只查全脚本是空转的
	// （变异 M3 实测）；而把助手掏空成 return [el]，两处调用点仍然「在用」合成树。
	if climb := jsFuncBody(t, js, "composedAncestors"); !strings.Contains(climb, "getRootNode") || !strings.Contains(climb, ".host") {
		t.Error("合成树祖先链没有经 getRootNode().host —— 层级遍历在 shadow 页上会断掉")
	}
	if reg := jsFuncBody(t, js, "region"); !strings.Contains(reg, "composedAncestors") {
		t.Error("region 没有走合成树祖先链 —— 它的层级遍历会退化成 body")
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
