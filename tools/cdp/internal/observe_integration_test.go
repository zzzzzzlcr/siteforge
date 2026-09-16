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
		t.Skipf("Chrome 不可用: %v", err) // 环境缺失 —— 全文件**只有这一处**允许 skip
	}
	t.Cleanup(c.Disconnect)
	// 导航失败**不能** skip：url 是本测试**自己刚起**的 fixture server（httptest），
	// 连不上它不是环境缺失，是缺陷。而 skip 会把整个闸门悄悄变成四条绿 skip ——
	// 跳过和通过长得一样，这条我们已经栽过。
	if _, err := c.Navigate(url, ""); err != nil {
		t.Fatalf("导航到自带 fixture server 失败（不是环境缺失，server 是本测试刚起的）: %v", err)
	}
	time.Sleep(1500 * time.Millisecond)
	m, err := c.Observe("")
	if err != nil {
		t.Fatalf("observe 失败: %v", err)
	}
	return m
}

// requireShadowActions 是「不该出现某种东西」那两条断言的前置守卫。
//
// 它们天生可以**空转通过**：actions 取到 0 个 → bad 为空、regions 为空 → 绿。
// 所以每条都得自证有效性：动作数够，且确实来自**两层 shadow 里**（shadow_depth >= 2）。
func requireShadowActions(t *testing.T, m *PageModel) {
	t.Helper()
	if len(m.Actions) < 5 {
		t.Fatalf("shadow 页只取到 %d 个可动作元素 —— 后面的断言会空转通过", len(m.Actions))
	}
	deep := 0
	for _, a := range m.Actions {
		if a.ShadowDepth >= 2 {
			deep++
		}
	}
	if deep < 5 {
		t.Fatalf("只有 %d/%d 个动作来自两层 shadow 里（shadow_depth>=2）—— 断言的不是 shadow 元素，空转",
			deep, len(m.Actions))
	}
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
	// 修好后本文件量到 **142**（量的是 Observe 返回的 page_text，按 runes 数）。
	// 探针 README 记的 141 是**另一个口径**：它的 page_text_len_raw 把各 root 的
	// 原文长度直接相加 —— 本机实测 [10, 6, 125] = 141（没 join 分隔空格、也没归一化）；
	// join(' ') 后在 root 之间多出 2 个分隔空格、又折叠掉各段内部空白，净 +1 → 142。
	// 两个数都对，量的是不同的东西（2026-09-16 实测，见 task-3-report.md 修复轮 1）。
	// 阈值取 60 足以区分两种实现（10 vs 142）。
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

	requireShadowActions(t, m) // 动作列表一空，下面这条就**空转通过**

	var bad []string
	for _, a := range m.Actions {
		if a.OccludedBy != nil {
			bad = append(bad, a.Text+"←"+*a.OccludedBy)
		}
	}
	// 踩坑时这里是**全假阳性**：探针 README 记 5/5；本机在 shadow.html 上实测 **6/6**
	// （该页 action 就是 6 条 = 3 字段 + 3 按钮），签名清一色 ←div#host1。
	// 断言只看「有没有」，数量口径变了也不影响。
	if len(bad) > 0 {
		t.Errorf("shadow 元素被误判为被遮挡（踩坑时全假阳性，本机实测 6/6）: %v", bad)
	}
}

// 陷阱 ③：region 不得全部退化成 body
func TestObserveShadowRegionNotAllBody(t *testing.T) {
	srv := serveFixtures(t)
	m := navigateAndObserve(t, srv.URL+"/shadow.html")

	requireShadowActions(t, m) // 动作列表一空，regions 也空 → 下面这条**空转通过**

	regions := map[string]bool{}
	for _, a := range m.Actions {
		regions[a.Region] = true
	}
	// 这条依赖 fixture 里 shadow **外面**有 landmark（testdata/shadow.html 的 <main>）：
	// 没有它的话，走合成树的正确实现与只走 parentElement 的错误实现**都**答 body，
	// 断言对两种实现一样红 = 空转。原委见 testdata/README.md。
	if len(regions) == 1 && regions["body"] {
		t.Error("region 全部退化成 body —— parentElement 出不了 shadow 边界（陷阱 ③）")
	}
}
