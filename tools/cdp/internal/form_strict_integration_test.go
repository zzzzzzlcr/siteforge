package internal

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

// `cdp form --strict` 的**行为**判据（真浏览器）：
// 同一个 class 命中两个输入框时，值必须落进**对的那个**；认不出就一个都不填。
//
// 真站实测的那一格（gowizard）：`input.MuiInputBase-input.MuiInputBase-inputAdornedStart`
// 被 zip / full_name / email 三个字段组共用 —— 第一条第选择器一挂，宽松路径就把值
// 填进文档序第一个框。用户在图上看出来的就是「ZIP 框里躺着手机号、页面红字拒收」。
func TestStrictFormPickLandsInTheRightBox(t *testing.T) {
	page := `<!doctype html><html><body style="margin:0">
	  <div><input class="mui" id="a1" name="fullName" placeholder="Full Name:" style="display:block;width:300px;height:40px"></div>
	  <div><input class="mui" id="a2" name="zipCode" placeholder="ZIP code" type="tel" style="display:block;width:300px;height:40px"></div>
	</body></html>`
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html")
		_, _ = w.Write([]byte(page))
	}))
	defer srv.Close()

	host, port := shadowTestEndpoint()
	client, err := NewClient(host, port)
	if err != nil {
		t.Skipf("Chrome 不可用: %v", err)
	}
	t.Cleanup(client.Disconnect)
	if _, err := client.Navigate(srv.URL, ""); err != nil {
		t.Fatalf("导航失败: %v", err)
	}
	time.Sleep(500 * time.Millisecond)

	read := func() (string, string) {
		var raw string
		if err := client.EvalInFrame("", `(function(){
		  var a=document.getElementById('a1'), b=document.getElementById('a2');
		  return JSON.stringify({a1:a?a.value:'?', a2:b?b.value:'?'});
		})()`, &raw); err != nil {
			t.Fatalf("读值失败: %v", err)
		}
		var got struct {
			A1 string `json:"a1"`
			A2 string `json:"a2"`
		}
		_ = json.Unmarshal([]byte(raw), &got)
		return got.A1, got.A2
	}

	// ① 自证夹具：这个 class 的选择器**真的**命中两个（不然这一格没在考消歧）
	var n string
	if err := client.EvalInFrame("", `String(document.getElementsByClassName('mui').length)`, &n); err != nil {
		t.Fatalf("数命中失败: %v", err)
	}
	if strings.TrimSpace(n) != "2" {
		t.Fatalf("夹具不对：.mui 应命中 2 个，实际 %s", n)
	}

	// ② 按身份认出来 → 值落进 zip 那个框，另一个框一个字符都不该有
	picked, err := client.StrictPick(".mui", "", "ZIP code")
	if err != nil {
		t.Fatalf("该认得出的（身份 = ZIP code）：%v", err)
	}
	if picked == "" || !strings.Contains(picked, "a2") {
		t.Fatalf("认错了元素：%q", picked)
	}
	if err := client.FillText(picked, "33139", "", false); err != nil {
		t.Fatalf("填值失败: %v", err)
	}
	a1, a2 := read()
	if a2 != "33139" {
		t.Errorf("ZIP 框里应该是 33139，实际 %q", a2)
	}
	if a1 != "" {
		t.Errorf("另一个框（fullName）**一个字符都不该有**，实际 %q —— 这正是要治的那个洞", a1)
	}

	// ③ 认不出 → **拒绝**，而且一个框都不填（不是「退回第一个」）
	if _, err := client.StrictPick(".mui", "", "phone"); err == nil {
		t.Fatal("身份对不上时必须拒绝")
	}
	if _, err := client.StrictPick(".mui", "", ""); err == nil {
		t.Fatal("没给身份又命中多个时必须拒绝")
	}
	a1, a2 = read()
	if a1 != "" || a2 != "33139" {
		t.Errorf("拒绝之后再动手了：a1=%q a2=%q", a1, a2)
	}

	// ④ 只命中一个时不改任何东西（这一闸不该影响正常的路）
	one, err := client.StrictPick("#a1", "", "")
	if err != nil || !strings.Contains(one, "a1") {
		t.Fatalf("只命中一个时该原样照做：got=%q err=%v", one, err)
	}
}
