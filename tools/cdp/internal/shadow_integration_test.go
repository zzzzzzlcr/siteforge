package internal

import (
	"os"
	"strconv"
	"testing"
	"time"
)

// 行为验证：选择器解析必须穿得进 shadow DOM。
//
// 单元测试（shadow_test.go）只钉住「生成的 JS 用的是穿透助手」；这里用真浏览器
// 证明端到端可用 —— 建一个**嵌套两层** shadow root 的 fixture，然后让
// cdp form 那套（ScrollIntoView → GetElementCenter → 拟人点击 → 输入）跑一遍。
//
// 为什么是两层：shadow root 可以嵌套，georgiapower 那页实测 76 个。
// 只穿一层的实现能过单层测试，但会在真站上挂。
//
// 没浏览器时 Skip，与 form_integration_test.go 一致。
// 可用 CDP_HOST / CDP_PORT 指向别的浏览器。

func shadowTestEndpoint() (string, int) {
	host := os.Getenv("CDP_HOST")
	if host == "" {
		host = "127.0.0.1"
	}
	port := 9222
	if p := os.Getenv("CDP_PORT"); p != "" {
		if n, err := strconv.Atoi(p); err == nil {
			port = n
		}
	}
	return host, port
}

// nestedShadowFixtureJS 建一个两层嵌套的 shadow DOM，里面放一个输入框和一个按钮。
// 宿主固定在视口内并有真实尺寸 —— 后面要走 scrollIntoView + 坐标点击。
const nestedShadowFixtureJS = `(function(){
	var host = document.createElement('div');
	host.id = '__shadow_host';
	host.style.cssText = 'position:fixed;left:20px;top:20px;width:300px;height:200px;z-index:99999;background:#fff;';
	document.body.appendChild(host);

	var outer = host.attachShadow({mode: 'open'});
	var mid = document.createElement('div');
	mid.id = '__shadow_mid';
	outer.appendChild(mid);

	var inner = mid.attachShadow({mode: 'open'});
	var inp = document.createElement('input');
	inp.id = 'shadow-input';
	inp.type = 'text';
	inp.name = 'shadowInput';
	inp.style.cssText = 'width:200px;height:30px;display:block;margin:10px;';
	inner.appendChild(inp);

	var btn = document.createElement('button');
	btn.id = 'shadow-btn';
	btn.type = 'button';
	btn.textContent = 'ShadowGo';
	btn.style.cssText = 'width:120px;height:30px;display:block;margin:10px;';
	btn.addEventListener('click', function(){ btn.setAttribute('data-clicked', 'yes'); });
	inner.appendChild(btn);

	return JSON.stringify({
		hosts: 2,
		plainInput: document.querySelectorAll('input').length,   // 应为 0
		plainButton: document.querySelectorAll('button').length  // 应为 0
	});
})()`

func withShadowFixture(t *testing.T, host string, port int) (*Client, func()) {
	t.Helper()

	pages, err := ListPageTargets(host, port, false)
	if err != nil || len(pages) == 0 {
		t.Skipf("Chrome 不可用: %v", err)
	}
	client, err := NewClient(host, port)
	if err != nil {
		t.Skipf("NewClient 失败: %v", err)
	}
	if _, err := client.Navigate("about:blank", ""); err != nil {
		client.Disconnect()
		t.Skipf("导航失败: %v", err)
	}
	time.Sleep(300 * time.Millisecond)

	var res string
	if err := client.EvalInFrame("", nestedShadowFixtureJS, &res); err != nil {
		client.Disconnect()
		t.Fatalf("注入 shadow fixture 失败: %v", err)
	}
	return client, func() {
		client.EvalInFrame("", "(function(){var e=document.getElementById('__shadow_host');if(e)e.remove();})()", nil)
		client.Disconnect()
	}
}

// 前提校验：fixture 本身确实让裸 querySelector 失明。
// 这条要是挂了，下面的测试就是假绿 —— 元素根本没藏好。
func TestShadowFixtureIsActuallyHidden(t *testing.T) {
	host, port := shadowTestEndpoint()
	client, cleanup := withShadowFixture(t, host, port)
	defer cleanup()

	var plain string
	if err := client.EvalInFrame("",
		"JSON.stringify({i: document.querySelectorAll('input').length, b: document.querySelectorAll('button').length})",
		&plain); err != nil {
		t.Fatalf("eval 失败: %v", err)
	}
	if plain != `{"i":0,"b":0}` {
		t.Fatalf("fixture 没把元素藏进 shadow root，裸查询仍看得见: %s", plain)
	}

	// 穿透后必须看得见。
	// 这里自己写一遍遍历，不用工具内部那套 __cdpQ —— 测试不该依赖实现的内部命名。
	var pierced string
	if err := client.EvalInFrame("",
		"(function(){"+
			"var roots=[document];"+
			"(function walk(r){var all;try{all=r.querySelectorAll('*')}catch(e){return}"+
			" for(var i=0;i<all.length;i++){var sr=all[i].shadowRoot;if(sr){roots.push(sr);walk(sr)}}})(document);"+
			"var count=function(sel){var n=0;for(var i=0;i<roots.length;i++){"+
			" var f;try{f=roots[i].querySelectorAll(sel)}catch(e){continue}n+=f.length}return n};"+
			"return JSON.stringify({i:count('input'),b:count('button')});"+
			"})()", &pierced); err != nil {
		t.Fatalf("eval 失败: %v", err)
	}
	if pierced != `{"i":1,"b":1}` {
		t.Fatalf("穿透后应看到 1 个 input、1 个 button（fixture 里各一个），实际: %s", pierced)
	}
}

func TestGetElementCenterPiercesShadowRoot(t *testing.T) {
	host, port := shadowTestEndpoint()
	client, cleanup := withShadowFixture(t, host, port)
	defer cleanup()

	rect, err := client.GetElementCenter("#shadow-input", "")
	if err != nil {
		t.Fatalf("穿透两层 shadow root 找不到输入框: %v", err)
	}
	if rect["width"] == 0 || rect["height"] == 0 {
		t.Fatalf("输入框尺寸为 0，fixture 没排上版: %+v", rect)
	}
}

func TestFillTextPiercesShadowRoot(t *testing.T) {
	host, port := shadowTestEndpoint()
	client, cleanup := withShadowFixture(t, host, port)
	defer cleanup()

	if err := client.FillText("#shadow-input", "hello-shadow", "", false); err != nil {
		t.Fatalf("FillText 穿不透 shadow DOM: %v", err)
	}

	// 读回同样自己遍历，不依赖内部命名
	var got string
	if err := client.EvalInFrame("",
		"(function(){"+
			"var roots=[document];"+
			"(function walk(r){var all;try{all=r.querySelectorAll('*')}catch(e){return}"+
			" for(var i=0;i<all.length;i++){var sr=all[i].shadowRoot;if(sr){roots.push(sr);walk(sr)}}})(document);"+
			"for(var i=0;i<roots.length;i++){var e;try{e=roots[i].querySelector('#shadow-input')}catch(x){continue}"+
			" if(e)return e.value;}"+
			"return '(找不到)';"+
			"})()", &got); err != nil {
		t.Fatalf("读回失败: %v", err)
	}
	if got != "hello-shadow" {
		t.Fatalf("输入框的值应为 hello-shadow，实际 %q", got)
	}
}

func TestClickPiercesShadowRoot(t *testing.T) {
	host, port := shadowTestEndpoint()
	client, cleanup := withShadowFixture(t, host, port)
	defer cleanup()

	if _, err := client.ClickElement("#shadow-btn", "", false); err != nil {
		t.Fatalf("click 穿不透 shadow DOM: %v", err)
	}

	var clicked string
	if err := client.EvalInFrame("",
		"(function(){"+
			"var roots=[document];"+
			"(function walk(r){var all;try{all=r.querySelectorAll('*')}catch(e){return}"+
			" for(var i=0;i<all.length;i++){var sr=all[i].shadowRoot;if(sr){roots.push(sr);walk(sr)}}})(document);"+
			"for(var i=0;i<roots.length;i++){var e;try{e=roots[i].querySelector('#shadow-btn')}catch(x){continue}"+
			" if(e)return e.getAttribute('data-clicked')||'no';}"+
			"return '(找不到)';"+
			"})()", &clicked); err != nil {
		t.Fatalf("读回失败: %v", err)
	}
	if clicked != "yes" {
		t.Fatalf("按钮应被点中（data-clicked=yes），实际 %q", clicked)
	}
}
