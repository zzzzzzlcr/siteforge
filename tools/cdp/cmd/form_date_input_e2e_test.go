package cmd

import (
	"encoding/json"
	"strings"
	"testing"
)

// 钉住「**原生 `<input type=date>` 必须真填进去；填不进去就大声失败**」。
//
// 真事（2026-10-12 · `job-9e2250b60c19` · nitrouscompetitions.com/auth 的「Date of birth」）：
// 模型对那根框填了 **7 次、7 种写法**（1990-05-14 / 05/14/1990 / 05141990 / 14051990 …），
// 每次 `cdp form` 的 stdout 都是 `{"ok":true}`，每次回读都是 `dob: ""` ——
// 一趟 45 步的探索里有一大半耗在这根框上，它自己最后记了一句「DOB didn't take」。
//
// 根因（真站 watcher 量到的原文，`runtime-222/_probe_dob_nitrous2.py`）：
// 普填那一路是「清空 → 逐字符 `Input.insertText` → blur」，而 Chromium 的这个框
// **对 insertText 静默丢弃**（只认 `.value` 赋值或按段落的键盘事件）——
// 事件记里只有清空那一下，打字那 10 个字符**一个事件都没发出来**。命令又**不回读**
// ⇒ `{"ok":true}` 是**空枪**。
//
// 判据是**行为**，三层：
//
//	① 判决：日期框填 1990-05-14 ⇒ rc=0 **且值真进去**，React 那边记的值也跟着变；
//	② 对照：同一页的文本框照旧能填（证明问题只在这根框的 type，不在整条路）；
//	③ 反例：框收不下的值 ⇒ **非 0 退出**、说清是哪个值，**且一个字没动**
//	   （宁可拒，不许半做 —— 今天这一版是 rc=0 且静默）。
func TestFormFillsANativeDateInput(t *testing.T) {
	e := env(t)
	e.navigate(t, e.fixture+"/date_input.html")

	const iso = "1990-05-14"
	const sel = "input[type=date]"

	// ① 判决
	out, errOut, code := e.run(t, "form", sel, "--value", iso)
	if code != 0 {
		t.Fatalf("原生日期框填不进去（rc=%d）—— 真站上这一步的表现是"+
			"「日志说填好了、框里还是空的」\nstderr: %s", code, errOut)
	}
	if !strings.Contains(out, `"ok":true`) {
		t.Errorf("成了却没有成功回执（stdout=%.200s）", out)
	}
	st := dateInputState(t, e)
	if st["dob"] != iso {
		t.Fatalf("值没进日期框：dob=%v 想要 %q", st["dob"], iso)
	}
	if st["reactValue"] != iso {
		t.Errorf("DOM 里有了，可 React 那边记的还是 %v —— 受控框下一次重渲染就会把它抹回去"+
			"（要写 HTMLInputElement.prototype 上那个原生 setter，不是 el.value =）", st["reactValue"])
	}

	// ② 对照：同一页的文本框
	if _, errOut, code := e.run(t, "form", "#name", "--value", "Harriet Clarke"); code != 0 {
		t.Fatalf("对照的文本框都没成 —— 先查夹具，别怪日期那条支路\nstderr: %s", errOut)
	}
	if st := dateInputState(t, e); st["name"] != "Harriet Clarke" {
		t.Errorf("对照的文本框没填上：name=%v", st["name"])
	}

	// ③ 反例：框收不下的值
	_, errOut, code = e.run(t, "form", sel, "--value", "not-a-date")
	if code == 0 {
		t.Fatalf("框收不下的值也报成功 —— 那趟 29 步就是这么烧掉的（回执是空枪）")
	}
	if !strings.Contains(errOut, "not-a-date") {
		t.Errorf("拒了，但没说清是哪个值收不下（stderr=%.300s）", errOut)
	}
	if st := dateInputState(t, e); st["dob"] != iso {
		t.Errorf("拒了却动了框：dob=%v（原来那个 %q 没了）—— 宁可拒，不许半做", st["dob"], iso)
	}
}

// dateInputState 读夹具自报的几格（框里的值 / React 记的值 / 对照框 / 事件记）。
func dateInputState(t *testing.T, e *testEnv) map[string]any {
	t.Helper()
	out, errOut, code := e.run(t, "eval", "window.__state()")
	if code != 0 {
		t.Fatalf("读夹具状态失败（退出码 %d）：%s", code, errOut)
	}
	var inner string
	if err := json.Unmarshal([]byte(out), &inner); err != nil {
		t.Fatalf("eval 的输出不是 JSON 字符串: %v\n原文: %q", err, out)
	}
	var m map[string]any
	if err := json.Unmarshal([]byte(inner), &m); err != nil {
		t.Fatalf("夹具状态解不开: %v（原文 %q）", err, inner)
	}
	return m
}
