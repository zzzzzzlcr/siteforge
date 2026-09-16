package internal

import (
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

// 行为验证：observe 在真页面上的四档覆盖（Task 3）。
//
// Task 2 的 observe_traps_test.go 全是**字符串断言**（脚本里出现了什么子串）——
// 它能钉住「实现特征」，但证明不了行为对不对。这一档跑真浏览器 + 真页面：
// fixture 来自 R3 探针（docs/probes/2026-09-16-observe-r3/fixtures/）。
//
// 三条陷阱各有一条对应的行为测试：
//
//	① ShadowRoot 没有 innerText      → TestObserveShadowPageTextIsNotEmpty
//	② elementsFromPoint 不穿透 shadow → TestObserveShadowElementsNotFalselyOccluded
//	③ parentElement 出不了 shadow 边界 → TestObserveShadowRegionNotAllBody
//
// ⚠️ 陷阱③那条要求 fixture 里**有 landmark**，且 landmark 必须在 shadow **外面**
// （testdata/shadow.html 的 `<main>` 就是为它加的）。2026-09-16 实测：没有 landmark
// 时「走合成树」与「只走 parentElement」两种实现**都**返回 body —— 断言对两种实现
// 一样红，空转。加了之后：正确实现 main / 陷阱③实现 body，断言才咬得住。
// 这也是 testdata/shadow.html 相对探针副本的**唯一**差异（其余 5 个 fixture 逐字节相同）。
//
// 没浏览器时 Skip（沿用 shadow_integration_test.go 的 shadowTestEndpoint）。
//
// fixture 由 httptest 自带服务（C30：不得依赖外部 mock-server，如 localhost:8080）。
// 跨源 iframe 那一档（ObserveAll 合并）按 C1 移到 Task 4 —— 本文件**不得**出现
// ObserveAll，否则引用未定义的方法会让整个 internal 包编不过。

func serveFixtures(t *testing.T) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.FileServer(http.Dir("testdata")))
	t.Cleanup(srv.Close)
	return srv
}

func navigateAndObserve(t *testing.T, url string) *PageModel {
	t.Helper()
	host, port := shadowTestEndpoint()
	c, err := NewClient(host, port)
	if err != nil {
		t.Skipf("Chrome 不可用: %v", err)
	}
	t.Cleanup(c.Disconnect)
	if _, err := c.Navigate(url, ""); err != nil {
		t.Skipf("导航失败: %v", err)
	}
	time.Sleep(1500 * time.Millisecond)
	m, err := c.Observe("")
	if err != nil {
		t.Fatalf("observe 失败: %v", err)
	}
	return m
}

func TestObserveLightDOM(t *testing.T) {
	srv := serveFixtures(t)
	m := navigateAndObserve(t, srv.URL+"/base.html")

	if m.ShadowRoots != 0 {
		t.Errorf("base.html 不该有 shadow root，实际 %d", m.ShadowRoots)
	}
	if len(m.Actions) < 5 {
		t.Errorf("可动作元素太少: %d", len(m.Actions))
	}
	// 同名按钮必须能靠 region 区分 —— 这是 observe 存在的核心理由
	seen := map[string]map[string]bool{}
	for _, a := range m.Actions {
		if a.Text == "Learn More" {
			if seen[a.Text] == nil {
				seen[a.Text] = map[string]bool{}
			}
			seen[a.Text][a.Region] = true
		}
	}
	if len(seen["Learn More"]) < 2 {
		t.Errorf("两个同名 Learn More 没被 region 区分开: %+v", seen)
	}
	if len(m.Obstructions) == 0 {
		t.Error("cookie 横幅没被识别为 obstruction")
	}
}

// 陷阱 ①：page_text 必须包含 shadow 里的文本
func TestObserveShadowPageTextIsNotEmpty(t *testing.T) {
	srv := serveFixtures(t)
	m := navigateAndObserve(t, srv.URL+"/shadow.html")

	if m.ShadowRoots < 2 {
		t.Fatalf("fixture 应有两层嵌套 shadow root，实际 %d", m.ShadowRoots)
	}
	// 踩坑时的实测值：10 个字符（只有 shadow 外面的标题）。
	// 修好后是 141。阈值取 60 足以区分两种实现。
	if len([]rune(m.PageText)) < 60 {
		t.Errorf("page_text 只有 %d 字 —— shadow 里的正文没收到: %q",
			len([]rune(m.PageText)), m.PageText)
	}
	if len(m.Fields) < 3 {
		t.Errorf("表单字段应至少 3 个（阴影里），实际 %d", len(m.Fields))
	}
	for _, f := range m.Fields {
		if f.ShadowDepth < 2 {
			t.Errorf("字段 %s 的 shadow_depth 应为 2，实际 %d", f.Selector, f.ShadowDepth)
		}
	}
}

// 陷阱 ②：shadow 元素不得被误判为「被遮挡」
func TestObserveShadowElementsNotFalselyOccluded(t *testing.T) {
	srv := serveFixtures(t)
	m := navigateAndObserve(t, srv.URL+"/shadow.html")

	var bad []string
	for _, a := range m.Actions {
		if a.OccludedBy != nil {
			bad = append(bad, a.Text+"←"+*a.OccludedBy)
		}
	}
	// 踩坑时这里是 5/5 全假阳性
	if len(bad) > 0 {
		t.Errorf("shadow 元素被误判为被遮挡（踩坑时实测 5/5 假阳性）: %v", bad)
	}
}

// 陷阱 ③：region 不得全部退化成 body
func TestObserveShadowRegionNotAllBody(t *testing.T) {
	srv := serveFixtures(t)
	m := navigateAndObserve(t, srv.URL+"/shadow.html")

	regions := map[string]bool{}
	for _, a := range m.Actions {
		regions[a.Region] = true
	}
	if len(regions) == 1 && regions["body"] {
		t.Error("region 全部退化成 body —— parentElement 出不了 shadow 边界（陷阱 ③）")
	}
}
