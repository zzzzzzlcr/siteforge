package internal

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

// 跨源 iframe 里的点击坐标**必须带对偏移**（2026-09-17 真窗口实测的缺陷）。
//
// 真站上量到的：帧内 Year 的 `cy=144`，主帧 iframe 的 `top=199`（正确落点 343），
// 而 click **实际发出 y=125.67** —— 约等于帧内坐标、没加偏移，点到了 iframe
// **上方**的主页面上；点后 `listbox=0`。**而且是静默的**：错落点在按下前后没有变化，
// 落点判据无话可说 → 回执里 `release_withheld` / `landing_note` / `landing_blind`
// 一个都不出现。
//
// 根因（我自己核的，与复审给的线索一致、而且比它更完整）：`ResolveIframeSelector`
// 拿**帧树下标**当**DOM iframe 下标**用。实测这一格的三个 iframe：
//
//	DOM  顺序：[0] 0×0 about:blank（y=14） [1] 要点的跨站帧（y=199, 311×400） [2] 0×0
//	帧树 顺序：[0] 0×0   [1] 0×0   [2] 要点的跨站帧
//
// 两处都错：
//   · 基线帧树**不含跨站子帧**（OOPIF 不进父页会话的帧树）——`GetFrameTree` 只列出两个
//     about:blank，要点的那个**一个都没有**；`GetFrameTreeWithEvents` 把 DOM 里发现的帧
//     **追加在末尾**，于是那个下标成了「合并顺序」；
//   · 拿它索引 DOM 顺序 → 下标 2 命中**另一个 0×0**（y=14）→ 偏移算成 (0,14) 而不是 (0,199)。
//
// 这条测试钉三件事（缺一不可）：
//
//	① 自证夹具**真的是**「帧树下标 ≠ DOM 下标」那一格（不然断言在证明一件不存在的事）；
//	② 解析出来的矩形**就是那个 iframe 的**（y=199、311×400 —— 数字级）；
//	③ 端到端：点帧内那个按钮，**帧内的处理器必须跑 1 次**（偏移错了就落在 iframe 外面，
//	   一次都不会跑 —— 这是 ① 的行为级版本，也是「坐标真的带上了偏移」的最终证据）。
func TestFrameClickCoordinatesCarryTheRightIframeOffset(t *testing.T) {
	// 内层页面里放两样东西：一个普通按钮（考 click 那条路），
	// 一个「点开再选」的自定义下拉（考 **form 那条路** —— 它原先同样漏了偏移：
	// findControlJS / findCustomOptionJS 都在帧里求值，给的却是帧内坐标）。
	inner := `<!doctype html><html><body style="margin:0">
	  <div style="height:120px"></div>
	  <button id="in-btn" onclick="window.__inClicks=(window.__inClicks||0)+1"
	          style="display:block;width:300px;height:50px">inner</button>
	  <div id="in-wrap" style="position:absolute;left:20px;top:220px;width:200px;height:40px;background:#eee">
	    <div id="in-control" role="combobox" aria-haspopup="listbox" aria-expanded="false" tabindex="0"
	         style="width:100%;height:100%;line-height:40px">--</div>
	  </div>
	  <script>
	    window.__inSel = '';
	    window.__inClose = function () {
	      var m = document.getElementById('in-menu'); if (m) m.parentNode.removeChild(m);
	      var b = document.getElementById('in-backdrop'); if (b) b.parentNode.removeChild(b);
	      document.getElementById('in-control').setAttribute('aria-expanded', 'false');
	    };
	    document.getElementById('in-control').addEventListener('mousedown', function () {
	      if (document.getElementById('in-menu')) return;
	      var m = document.createElement('ul');
	      m.id = 'in-menu'; m.setAttribute('role', 'listbox');
	      m.style.cssText = 'position:absolute;left:20px;top:260px;width:200px;margin:0;padding:0;list-style:none;background:#fff;z-index:6000';
	      ['Audi', 'BMW'].forEach(function (v) {
	        var li = document.createElement('li');
	        li.setAttribute('role', 'option'); li.textContent = v;
	        li.style.cssText = 'height:36px;line-height:36px';
	        li.addEventListener('click', function () { window.__inSel = v; window.__inClose(); });
	        m.appendChild(li);
	      });
	      document.body.appendChild(m);
	      // ⚠️ 这层 backdrop **不**在 mouseup 上关菜单 —— 这是**实测口径**，不是图省事：
	      //   · 真站（MUI）实测：我们的 release 落在新出现的菜单项/backdrop 上，菜单**留着**；
	      //   · 而**跨源子帧里落点判据本来就瞎**（DOM.getNodeForLocation 不下钻 →
	      //     前后都是同一个 iframe → 恒不触发；诊断会说 landing_blind）。
	      // 所以夹具若在这里关菜单，考的就成了「G1 保护」而不是「坐标偏移」——
	      // 而这一条测试要钉的是**坐标**（偏移错了 mousedown 根本进不了子帧）。
	      var b = document.createElement('div');
	      b.id = 'in-backdrop';
	      b.style.cssText = 'position:absolute;left:0;top:0;right:0;bottom:0;z-index:5000;background:rgba(0,0,0,.15)';
	      document.body.appendChild(b);
	      document.getElementById('in-control').setAttribute('aria-expanded', 'true');
	    });
	  </script>
	</body></html>`
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html")
		if r.URL.Path == "/inner.html" {
			_, _ = w.Write([]byte(inner))
			return
		}
		// 0×0 在前、目标（跨站）在中、0×0 在后 —— 真站上那三个的顺序
		_, _ = w.Write([]byte(`<!doctype html><html><body style="margin:0">
		  <iframe id="f-zero" src="about:blank" style="width:0;height:0;border:0"></iframe>
		  <iframe id="f-target" src="` + r.URL.Query().Get("src") + `"
		          style="position:absolute;left:0;top:199px;width:311px;height:400px;border:0"></iframe>
		  <iframe id="f-zero2" src="about:blank" style="width:0;height:0;border:0"></iframe>
		</body></html>`))
	}))
	defer srv.Close()

	host, port := shadowTestEndpoint()
	client, err := NewClient(host, port)
	if err != nil {
		t.Skipf("Chrome 不可用: %v", err)
	}
	t.Cleanup(client.Disconnect)

	// 跨站：同一个 server、只换 host 名（本机实测这一对确实跨进程/OOPIF，
	// 也就是「基线帧树里看不见它」那一族 —— 这正是缺陷的触发条件）。
	cross := strings.Replace(srv.URL, "127.0.0.1", "localhost", 1) + "/inner.html"
	if _, err := client.Navigate(srv.URL+"/outer.html?src="+cross, ""); err != nil {
		t.Fatalf("导航失败: %v", err)
	}
	time.Sleep(3000 * time.Millisecond)

	// ① 自证夹具
	var raw string
	if err := client.EvalInFrame("", `(function(){
	  var fs = document.querySelectorAll('iframe'), out = [];
	  for (var i = 0; i < fs.length; i++) {
	    var r = fs[i].getBoundingClientRect();
	    out.push({i:i, id:fs[i].id, src:(fs[i].getAttribute('src')||''), y:Math.round(r.top), w:Math.round(r.width)});
	  }
	  return JSON.stringify(out);
	})()`, &raw); err != nil {
		t.Fatalf("读 DOM iframe 失败: %v", err)
	}
	if !strings.Contains(raw, `"i":1,"id":"f-target"`) {
		t.Fatalf("夹具不对：DOM 里要点的那个 iframe 不是第 1 个（%s）—— 这一格就没在考「下标错配」", raw)
	}

	tree, err := client.GetFrameTreeWithEvents(1 * time.Second)
	if err != nil {
		t.Fatalf("取帧树失败: %v", err)
	}
	targetID, treeIdx := "", -1
	for i, ch := range tree.ChildFrames {
		if ch != nil && strings.Contains(ch.Frame.URL, "inner.html") {
			targetID, treeIdx = string(ch.Frame.ID), i
		}
	}
	if targetID == "" {
		t.Fatalf("帧树里找不到跨站那一帧 —— 这一格没复现出「OOPIF 要靠 DOM 穿透才看得见」")
	}
	if treeIdx == 1 {
		t.Fatalf("夹具没复现出下标错配（帧树下标也是 1）—— 这条测试证明不了那个缺陷")
	}
	t.Logf("① 自证：要点的帧在**帧树**里排第 %d、在 **DOM** 里排第 1 —— 两套下标不一致（这就是缺陷的触发条件）", treeIdx)

	// ② 解析出来的矩形必须是**那个 iframe** 的
	sel, err := client.ResolveIframeSelector(targetID)
	if err != nil {
		t.Fatalf("ResolveIframeSelector 失败: %v", err)
	}
	rect, err := client.GetElementCenter(sel, "")
	if err != nil {
		t.Fatalf("取 iframe 矩形失败: %v", err)
	}
	if rect["y"] != 199 || rect["width"] != 311 || rect["height"] != 400 {
		t.Errorf("解析出来的 iframe 矩形 = %v，want y=199 / 311×400（那个 0×0 的是 y=14 / 0×0）—— "+
			"偏移算错就会点到 iframe 上方的主页面上", rect)
	}

	// ③ 端到端：点帧内那个按钮
	if _, err := client.ClickElement("#in-btn", targetID, false); err != nil {
		t.Fatalf("点帧内元素失败: %v", err)
	}
	time.Sleep(200 * time.Millisecond)
	var clicks int
	if err := client.EvalInFrame(targetID, "window.__inClicks || 0", &clicks); err != nil {
		t.Fatalf("读帧内计数失败: %v", err)
	}
	if clicks != 1 {
		t.Errorf("帧内那个按钮被点了 %d 次，want 1 —— 说明坐标**没有**带上那个 iframe 的偏移"+
			"（落点在 iframe 外面，帧内什么都不会发生；真站上这就是「点了没反应」）", clicks)
	}
	t.Logf("②③ 解析=%s rect=%v → 帧内处理器跑 %d 次", sel, rect, clicks)

	// ④ **form 那条路**（`cdp form --select` 的内核）：自定义下拉的两下点击
	// （点控件开菜单、点选项）原先也漏了偏移 —— 而它「返回成功、页面纹丝不动」，
	// 连回执都看不出来。这条断言是那一处的钉子。
	if err := client.SelectOption("#in-wrap", "BMW", targetID, false); err != nil {
		t.Fatalf("跨源 iframe 里选下拉失败（坐标没带偏移 → 点击落在 iframe 外面）: %v", err)
	}
	var picked string
	if err := client.EvalInFrame(targetID, "window.__inSel", &picked); err != nil {
		t.Fatalf("读帧内选中值失败: %v", err)
	}
	if picked != "BMW" {
		t.Errorf("帧内下拉选中的是 %q，want \"BMW\"", picked)
	}

	// ⑤ 跨源子帧里**判据是瞎的**，但这件事必须说出来（不许沉默）：
	// `landing_blind` 的诊断得有一条 —— 否则调用方会以为这一帧也有保护。
	var blind int
	for _, d := range client.LandingDiags() {
		if d.Kind == DiagKindLandingBlind {
			blind++
		}
	}
	if blind == 0 {
		t.Errorf("跨源子帧里的点击一条 landing-blind 诊断都没有 —— 这一帧的点击看起来和别处"+
			"一样「有保护」，其实一点都没有（diags=%+v）", client.LandingDiags())
	}
	t.Logf("④⑤ 帧内下拉选中=%q；landing-blind 诊断 %d 条（跨源子帧里判据不可用，但说出来了）",
		picked, blind)
}
