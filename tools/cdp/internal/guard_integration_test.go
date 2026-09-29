package internal

import (
	"fmt"
	"os"
	"strings"
	"testing"
)

// 这几条要一个**真浏览器**（默认 127.0.0.1:9222，可用 CDP_HOST/CDP_PORT 指别的）。
// 没浏览器时按本仓的约定 t.Skipf —— 跑不了就如实说跑不了，不许改成「跳过当通过」。

func guardClient(t *testing.T) *Client {
	t.Helper()
	host := os.Getenv("CDP_HOST")
	if host == "" {
		host = "127.0.0.1"
	}
	port := 9222
	pages, err := ListPageTargets(host, port, false)
	if err != nil || len(pages) == 0 {
		t.Skipf("Chrome not available: %v", err)
	}
	client, err := NewClient(host, port)
	if err != nil {
		t.Fatalf("NewClient failed: %v", err)
	}
	t.Cleanup(client.Disconnect)
	return client
}

func setBody(t *testing.T, c *Client, html string) {
	t.Helper()
	js := fmt.Sprintf(`(function(){ document.body.innerHTML = %q; document.body.style.margin='0'; return 'ok'; })()`, html)
	var out string
	if err := c.EvalInFrame("", js, &out); err != nil {
		t.Fatalf("注入夹具失败: %v", err)
	}
}

// ① 动手之前先问落点：目标中心被另一层压着 ⇒ **拒点**，且理由里说清那一点上是谁。
// 现场（docs/交接-2026-09-29 §7）：georgiapower 首屏 loading 时点 CTA = 点了个空气，
// 而 cdp 回执一切正常（match_count/坐标都像样）。
func TestClickElement_RefusesWhenCentreIsCovered(t *testing.T) {
	c := guardClient(t)
	setBody(t, c, `<div style="position:fixed;top:150px;left:40px;width:240px;height:80px">`+
		`<button id="go" style="width:100%;height:100%">GO</button></div>`+
		`<div id="cover" style="position:fixed;top:150px;left:40px;width:240px;height:80px;z-index:99;background:#fff"></div>`)

	_, err := c.ClickElement("#go", "", false)
	if err == nil {
		t.Fatal("目标中心被另一层压着时必须拒点，不许把这一击发出去")
	}
	for _, want := range []string{"拒绝点击", "#go", "cover"} {
		if !strings.Contains(err.Error(), want) {
			t.Errorf("拒点理由里要有 %q（人得看得出挡路的是谁）：%s", want, err)
		}
	}

	// 正向对照：撤掉覆盖层，同一次点击必须成 —— 证明拒点不是「一律拒」。
	if err := c.EvalInFrame("", "document.getElementById('cover').remove();", nil); err != nil {
		t.Fatalf("撤覆盖层失败: %v", err)
	}
	res, err := c.ClickElement("#go", "", false)
	if err != nil {
		t.Fatalf("没有遮挡时应当点得成（拒点必须有边界）：%v", err)
	}
	if res.X < 41 || res.X > 279 || res.Y < 151 || res.Y > 229 {
		t.Errorf("落点跑到元素外面了: (%.1f, %.1f)", res.X, res.Y)
	}
}

// ② 同名元素里第一个是 0×0 隐藏件 ⇒ 坐标解析要挑**可见的**那一个
// （afrotech 26238922：真正要点的按钮是文档序第二个、在页脚）。
func TestGetElementCenter_SkipsHiddenDuplicate(t *testing.T) {
	c := guardClient(t)
	setBody(t, c, `<button aria-label="Subscribe" style="display:none">A</button>`+
		`<button aria-label="Subscribe" style="position:fixed;top:300px;left:50px;width:42px;height:42px">B</button>`)

	rect, err := c.GetElementCenter(`button[aria-label="Subscribe"]`, "")
	if err != nil {
		t.Fatalf("GetElementCenter 失败: %v", err)
	}
	if !(rect["width"] > 0) || !(rect["height"] > 0) {
		t.Fatalf("选中了 0×0 的隐藏重复件（宽高 %.0f×%.0f）", rect["width"], rect["height"])
	}
	if rect["y"] < 100 {
		t.Fatalf("选中的不是那个可见件（y=%.0f，应约 300）", rect["y"])
	}
}

// ② 0×0 的可见性要**明说不可见**，不许落进 0/0=NaN。
func TestIsElementVisible_ZeroSizeIsFalseNotNaN(t *testing.T) {
	c := guardClient(t)
	setBody(t, c, `<button id="only" style="display:none">x</button>`)
	vis, err := c.IsElementVisible("#only")
	if err != nil {
		t.Fatalf("IsElementVisible 失败: %v", err)
	}
	if vis {
		t.Fatal("0×0 必须判不可见")
	}
}

// ② 只有隐藏件时，滚动方向量不出来 ⇒ 拒滚并说清（老行为是每轮朝反方向拖，20 轮不可能收敛）。
func TestScrollToElement_RefusesHiddenOnly(t *testing.T) {
	c := guardClient(t)
	setBody(t, c, `<div id="only" style="display:none">x</div>`)
	err := c.ScrollToElement("#only", false)
	if err == nil {
		t.Fatal("0×0 必须拒滚，不许猜方向")
	}
	if !strings.Contains(err.Error(), "没有可见盒子") {
		t.Errorf("拒滚的理由要说清是「没有可见盒子」：%s", err)
	}
}

// ② 远的元素（数屏之外）也要滚得到：按距离分流后一次精确 scrollIntoView 就该到位。
func TestScrollToElement_ReachesDeepElement(t *testing.T) {
	c := guardClient(t)
	setBody(t, c, `<div style="height:5000px"></div><button id="deep" style="width:40px;height:40px">d</button>`)
	if err := c.ScrollToElement("#deep", false); err != nil {
		t.Fatalf("滚动失败: %v", err)
	}
	vis, err := c.IsElementVisible("#deep")
	if err != nil {
		t.Fatalf("IsElementVisible 失败: %v", err)
	}
	if !vis {
		t.Fatal("数屏之外的元素滚完还不可见（按距离分流没生效）")
	}
}

// ③ <textarea> 能填上值（2026-09-28 georgiapower：老代码把它当 wrapper 找 input，
// 必然报 no input found in wrapper，连填值的路都没走到）。
func TestFillText_FillsTextarea(t *testing.T) {
	c := guardClient(t)
	setBody(t, c, `<textarea id="street" style="position:fixed;top:120px;left:40px;width:320px;height:90px"></textarea>`)

	if err := c.FillText("#street", "12 Main St", "", false); err != nil {
		t.Fatalf("填 <textarea> 失败: %v", err)
	}
	var got string
	if err := c.EvalInFrame("", `document.getElementById('street').value`, &got); err != nil {
		t.Fatalf("读回失败: %v", err)
	}
	if got != "12 Main St" {
		t.Fatalf("textarea 的值 = %q（要 12 Main St）", got)
	}
}
