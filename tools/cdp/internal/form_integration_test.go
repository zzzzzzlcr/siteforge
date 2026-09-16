package internal

import (
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/chromedp/cdproto/input"
)

func TestSelectOption_CustomDropdown_Single(t *testing.T) {
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
	defer client.Disconnect()

	// Navigate to react-select test page
	client.Navigate("http://localhost:8080/react-select", "")

	// Select "Canada" via custom dropdown wrapper
	err = client.SelectOption("#country-wrapper", "Canada", "", false)
	if err != nil {
		t.Fatalf("SelectOption failed: %v", err)
	}

	// Verify hidden input has the selected value
	var val string
	if err := client.EvalInFrame("", "document.getElementById('country-input').value", &val); err != nil {
		t.Fatalf("check input value failed: %v", err)
	}
	if val != "Canada" {
		t.Errorf("country-input value = %q, want %q", val, "Canada")
	}
}

func TestSelectOption_CustomDropdown_NotFound(t *testing.T) {
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
	defer client.Disconnect()

	client.Navigate("http://localhost:8080/react-select", "")

	err = client.SelectOption("#country-wrapper", "Mars", "", false)
	if err == nil {
		t.Error("expected error for non-existent option, got nil")
	}
	if err != nil && !strings.Contains(err.Error(), "option not found") {
		t.Errorf("error should mention 'option not found', got: %v", err)
	}
}

func TestSelectOption_NativeSelect_StillWorks(t *testing.T) {
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
	defer client.Disconnect()

	client.Navigate("http://localhost:8080/carwarranty", "")

	err = client.SelectOption("#car-year", "2025", "", false)
	if err != nil {
		t.Fatalf("native SelectOption failed: %v", err)
	}

	var val string
	if err := client.EvalInFrame("", "document.getElementById('car-year').value", &val); err != nil {
		t.Fatalf("check value failed: %v", err)
	}
	if val != "2025" {
		t.Errorf("car-year value = %q, want %q", val, "2025")
	}
}

// TestSelectOption_PortalMenuOutsideWrapper —— 现代组件库（MUI 形态）的「点开再选」：
// 控件在 wrapper 里，**选项在 wrapper 外**（菜单 Portal 到 body）。
//
// 真站实测（2026-09-16，gowizard 的 MUI 问卷）里那一格的选项祖先链是
// `li > ul[role=listbox] > div.MuiPaper > div.MuiPopover-root > body` ——
// 与 wrapper **没有祖先关系**，所以「只在 wrapper 子树里找选项」在它身上结构性地
// 找不到（`liInsideWrap: false`）。这一条把整条链走通，一次覆盖三件事：
//
//	G3 控件判据：combobox 上**没有** inline onclick（React 合成事件），只能靠
//	   `[role=combobox]` 那一趟找到它（夹具刻意不给 onclick）
//	G2 选项搜索：选项在 wrapper 之外，只能靠**已展开的菜单容器**那一趟找到
//	G1 抬起判据：点控件那一下 mousedown 会铺 backdrop 上来 → 抬起该被扣下，
//	   菜单才不会被我们自己关掉；而点选项那一下落点没变 → 必须照常点击
//
// 夹具里 backdrop 的「收到 mouseup 就关掉菜单」不是编的：实测里递到覆盖者手里的
// 正是 mouseup（click 落在公共祖先 BODY 上，见 click_integration_test.go 的 G1 注释）。
func TestSelectOption_PortalMenuOutsideWrapper(t *testing.T) {
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
	defer client.Disconnect()

	setupJS := `(function(){
		document.body.innerHTML =
			'<div id="mui-wrap" style="position:fixed;left:100px;top:100px;width:300px;height:50px;background:#eee;z-index:1">' +
				// ⚠️ 刻意**不给** onclick：这个控件只能靠 [role=combobox] 那一趟找到（G3）
				'<div id="mui-control" role="combobox" aria-haspopup="listbox" aria-expanded="false" tabindex="0" ' +
					'style="width:100%;height:100%;line-height:50px">--</div>' +
			'</div>';
		window.__sel = '';
		window.__close = function(){
			var m = document.getElementById('mui-menu'); if (m) m.parentNode.removeChild(m);
			var b = document.getElementById('mui-backdrop'); if (b) b.parentNode.removeChild(b);
			var c = document.getElementById('mui-control'); if (c) c.setAttribute('aria-expanded', 'false');
		};
		document.getElementById('mui-control').addEventListener('mousedown', function(){
			if (document.getElementById('mui-menu')) return;
			// 菜单 **Portal 到 body**：与 #mui-wrap 没有祖先关系（真站形态）
			var m = document.createElement('ul');
			m.id = 'mui-menu';
			m.setAttribute('role', 'listbox');
			m.style.cssText = 'position:fixed;left:100px;top:150px;width:300px;margin:0;padding:0;list-style:none;background:#fff;z-index:6000';
			['Audi', 'BMW'].forEach(function(v){
				var li = document.createElement('li');
				li.setAttribute('role', 'option');
				li.setAttribute('data-v', v);
				li.style.cssText = 'height:40px;line-height:40px';
				li.textContent = v;
				li.addEventListener('click', function(){ window.__sel = v; window.__close(); });
				m.appendChild(li);
			});
			document.body.appendChild(m);
			var b = document.createElement('div');
			b.id = 'mui-backdrop';
			b.style.cssText = 'position:fixed;left:0;top:0;right:0;bottom:0;z-index:5000;background:rgba(0,0,0,.2)';
			b.addEventListener('mouseup', function(){ window.__g1BackdropMouseUp = (window.__g1BackdropMouseUp||0)+1; window.__close(); });
			document.body.appendChild(b);
			document.getElementById('mui-control').setAttribute('aria-expanded', 'true');
		});
		window.__g1BackdropMouseUp = 0;
		return 'ok';
	})()`

	var setup string
	if err := client.EvalInFrame("", setupJS, &setup); err != nil {
		t.Fatalf("夹具注入失败: %v", err)
	}

	// 自证夹具的形状：把菜单开起来，确认选项确实**不在** wrapper 里 ——
	// 不然这条测试会退化成「wrapper 内找选项」那条老路，而它已经在别处测过了，
	// 这里的断言就会在证明一件不存在的事（本项目反复栽过的形态）。
	rect, err := client.GetElementCenter("#mui-control", "")
	if err != nil {
		t.Fatalf("取控件坐标失败: %v", err)
	}
	if err := client.dispatchMouseEvent(input.MousePressed, rect["centerX"], rect["centerY"]); err != nil {
		t.Fatalf("夹具自检：按下控件失败: %v", err)
	}
	var raw string
	if err := client.EvalInFrame("", `(function(){
		var w = document.getElementById('mui-wrap');
		var m = document.getElementById('mui-menu');
		var li = document.querySelector('#mui-menu [role=option]');
		return JSON.stringify({
			menuExists: !!m,
			menuOutsideWrapper: !!m && !w.contains(m),
			optionOutsideWrapper: !!li && !w.contains(li),
			liInsideWrap: !!li && w.contains(li),
			hasOnclick: !!document.getElementById('mui-control').getAttribute('onclick')
		});
	})()`, &raw); err != nil {
		t.Fatalf("夹具自检失败: %v", err)
	}
	var chk struct {
		MenuExists           bool `json:"menuExists"`
		MenuOutsideWrapper   bool `json:"menuOutsideWrapper"`
		OptionOutsideWrapper bool `json:"optionOutsideWrapper"`
		LiInsideWrap         bool `json:"liInsideWrap"`
		HasOnclick           bool `json:"hasOnclick"`
	}
	if err := json.Unmarshal([]byte(raw), &chk); err != nil {
		t.Fatalf("夹具自检解不开: %v (%s)", err, raw)
	}
	if !chk.MenuExists || !chk.MenuOutsideWrapper || !chk.OptionOutsideWrapper || chk.LiInsideWrap {
		t.Fatalf("夹具形状不对（%s）—— 选项必须在 wrapper **之外**，否则这条测试证明不了 G2", raw)
	}
	if chk.HasOnclick {
		t.Fatalf("夹具形状不对（%s）—— 控件上不该有 inline onclick，否则 [role=combobox] 那一趟（G3）没被考到", raw)
	}
	t.Logf("夹具自检：菜单已开、选项在 wrapper 之外（liInsideWrap=%v），控件无 onclick（G3 那一趟在承重）", chk.LiInsideWrap)

	// 自检改变了页面状态（菜单开着）——重新注入夹具，让被测流程从「关着的下拉」开始。
	if err := client.EvalInFrame("", setupJS, &setup); err != nil {
		t.Fatalf("夹具复位失败: %v", err)
	}

	if err := client.SelectOption("#mui-wrap", "BMW", "", false); err != nil {
		t.Fatalf("SelectOption 失败（菜单在 wrapper 外时选不出选项 = G2 没修好）: %v", err)
	}

	var sel string
	if err := client.EvalInFrame("", "window.__sel", &sel); err != nil {
		t.Fatalf("读选中值失败: %v", err)
	}
	if sel != "BMW" {
		t.Errorf("选中的是 %q，want \"BMW\" —— 选项**找到了**（否则 SelectOption 早报 option not found），"+
			"但点下去没落到它身上：多半是点成了另一个元素", sel)
	}
	var backdropMouseUp int
	if err := client.EvalInFrame("", "window.__g1BackdropMouseUp", &backdropMouseUp); err != nil {
		t.Fatalf("读 backdrop 计数失败: %v", err)
	}
	if backdropMouseUp != 0 {
		t.Errorf("点控件那一下的 mouseup 落到 backdrop 上 %d 次 —— 菜单会被我们自己关掉（G1 要治的正是它）", backdropMouseUp)
	}
	t.Logf("整条链走通：控件靠 [role=combobox] 找到（无 onclick）→ 菜单 Portal 到 body 被找到 → "+
		"点选项落地 sel=%q，backdrop 收到 mouseup %d 次", sel, backdropMouseUp)
}

func TestFillText_DatePicker_YMD(t *testing.T) {
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
	defer client.Disconnect()

	client.Navigate("http://localhost:8080/mui-datepicker", "")

	err = client.FillText("#dob-field", "2026-07-04", "", false)
	if err != nil {
		t.Fatalf("FillText failed: %v", err)
	}

	var val string
	if err := client.EvalInFrame("", "document.getElementById('dob-input').value", &val); err != nil {
		t.Fatalf("check value failed: %v", err)
	}
	if val != "07-04-2026" {
		t.Errorf("dob-input value = %q, want %q", val, "07-04-2026")
	}
}

func TestFillText_DatePicker_MDY(t *testing.T) {
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
	defer client.Disconnect()

	client.Navigate("http://localhost:8080/mui-datepicker", "")

	err = client.FillText("#dob-field", "07-04-2026", "", false)
	if err != nil {
		t.Fatalf("FillText failed: %v", err)
	}

	var val string
	if err := client.EvalInFrame("", "document.getElementById('dob-input').value", &val); err != nil {
		t.Fatalf("check value failed: %v", err)
	}
	if val != "07-04-2026" {
		t.Errorf("dob-input value = %q, want %q", val, "07-04-2026")
	}
}

func TestFillText_DatePicker_MD(t *testing.T) {
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
	defer client.Disconnect()

	client.Navigate("http://localhost:8080/mui-datepicker", "")

	err = client.FillText("#dob-field", "07-04", "", false)
	if err != nil {
		t.Fatalf("FillText failed: %v", err)
	}

	now := time.Now()
	want := fmt.Sprintf("07-04-%d", now.Year())

	var val string
	if err := client.EvalInFrame("", "document.getElementById('dob-input').value", &val); err != nil {
		t.Fatalf("check value failed: %v", err)
	}
	if val != want {
		t.Errorf("dob-input value = %q, want %q", val, want)
	}
}

func TestFillText_PlainInput_StillWorks(t *testing.T) {
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
	defer client.Disconnect()

	client.Navigate("http://localhost:8080/mui-datepicker", "")

	// Fill a plain text input — should use existing type-char-by-char flow
	err = client.FillText("#fullname", "John Doe", "", false)
	if err != nil {
		t.Fatalf("FillText plain input failed: %v", err)
	}

	var val string
	if err := client.EvalInFrame("", "document.getElementById('fullname').value", &val); err != nil {
		t.Fatalf("check value failed: %v", err)
	}
	if val != "John Doe" {
		t.Errorf("fullname value = %q, want %q", val, "John Doe")
	}
}
