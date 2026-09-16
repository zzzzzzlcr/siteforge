package cmd

import (
	"bytes"
	"encoding/json"
	"os"
	"os/exec"
	"strconv"
	"testing"
)

// cliOut 跑一次 cdp CLI，返回 stdout（stderr 单独收着，失败时报出来）。
func cliOut(t *testing.T, cliBin string, port int, args ...string) (string, string) {
	t.Helper()
	full := append([]string{"--host", "127.0.0.1", "--port", strconv.Itoa(port)}, args...)
	cmd := exec.Command(cliBin, full...)
	cmd.Env = envWithout(os.Environ(), "CDP_HOST", "CDP_PORT")
	var stdout, stderr bytes.Buffer
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	if err := cmd.Run(); err != nil {
		t.Fatalf("cdp %v 失败: %v\nstderr: %s", args, err, stderr.String())
	}
	return stdout.String(), stderr.String()
}

// `cdp form` 的**回执走 stdout**（2026-09-17 复审 Minor 1 的一半）。
//
// 为什么 form 也要回执：下游（产物 py 的 `_say`）**只拿得到 stdout** —— 它把 stderr 丢了
// （common.py 的 CDPHelper 只交 stdout）。原先 form 的 stdout 是空的，于是「这一次点击
// 只发出了按下的那一半」在下游看不见：那一步的日志与一次普通填值长得一模一样，
// 而 `_say` 照样说「填好了「X」」。
//
// 钉三格：
//
//	① 扣下那一格：stdout 上是**能解的 JSON**，`landing_withheld=true`、note 里有「扣下」；
//	② 同时选项照常选上、覆盖者一个事件都没收到（回执不是拿行为换来的）；
//	③ 反面：普通填值的回执里**不许**出现任何 landing_* —— 常驻提示等于噪音。
func TestFormCLIPrintsLandingReceipt(t *testing.T) {
	e := env(t)
	b := newMCPBrowser(t)
	naviWithCLI(t, e.bin, b.port, e.fixture+"/mcp_form_landing.html")

	// ① / ② 自定义下拉：点控件（抬起被扣下）→ 点选项
	out, stderr := cliOut(t, e.bin, b.port, "form", "--selector", "#mcpf-wrap", "--select", "BMW")
	var receipt map[string]any
	if err := json.Unmarshal([]byte(out), &receipt); err != nil {
		t.Fatalf("stdout 不是能解的 JSON 回执: %v\n原文: %q\nstderr: %s", err, out, stderr)
	}
	if withheld, _ := receipt["landing_withheld"].(bool); !withheld {
		t.Errorf("回执里 landing_withheld != true（%v）—— 这一次抬起被扣下了，下游从 stdout "+
			"看不到它，就还会说「填好了「X」」", receipt)
	}
	if note, _ := receipt["landing_note"].(string); note == "" {
		t.Errorf("回执里没有 landing_note —— 机器读 landing_withheld，人/模型读这句话（%v）", receipt)
	}
	s := evalJSON(t, e.bin, b.port, `JSON.stringify({sel:window.__sel,ups:window.__backdropMouseUps})`)
	if s["sel"] != "BMW" {
		t.Errorf("选项没选上（sel=%v）—— 回执不是拿行为换来的（状态 %v）", s["sel"], s)
	}
	if s["ups"].(float64) != 0 {
		t.Errorf("覆盖者收到了 %v 个 mouseup —— 菜单会被我们自己关掉", s["ups"])
	}

	// ③ 反面：普通填值
	out2, _ := cliOut(t, e.bin, b.port, "form", "--selector", "#mcpf-input", "--value", "hello")
	var receipt2 map[string]any
	if err := json.Unmarshal([]byte(out2), &receipt2); err != nil {
		t.Fatalf("普通填值的 stdout 不是能解的 JSON 回执: %v\n原文: %q", err, out2)
	}
	for k := range receipt2 {
		if len(k) >= 8 && k[:8] == "landing_" {
			t.Errorf("普通填值（判据没话可说）的回执里带着 %q —— 常驻的提示等于没有提示（%v）", k, receipt2)
		}
	}
	t.Logf("CLI 回执：扣下那一格 landing_withheld=true 且选项选上了；普通填值一个字都不多")
}
