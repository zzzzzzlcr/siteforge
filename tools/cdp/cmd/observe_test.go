package cmd

import (
	"bytes"
	"strings"
	"testing"

	"cdp/internal"
)

// observe 子命令的 CLI 契约（Task 6 Step 1）。
//
// ⚠️ 这一条只是**字符串断言级别**的弱测试：它证明 flag 挂上去了，证明不了
// observe 真的能跑通、输出真的是 PageModel JSON。真正的闸门是
// observe_e2e_test.go（真构建二进制 + 真打页面）。这条留着是因为它便宜：
// 一个 flag 被误删时它先红，不必等 e2e。
func TestObserveCommandHasContractFlags(t *testing.T) {
	cmd := observeCmd
	if cmd == nil {
		t.Fatal("observeCmd 未注册")
	}
	for _, f := range []string{"json", "frame-id", "expect-url"} {
		if cmd.Flags().Lookup(f) == nil {
			t.Errorf("observe 子命令缺 --%s", f)
		}
	}
	if cmd.Short == "" {
		t.Error("observe 子命令缺 Short 描述")
	}
	if cmd.Use != "observe" {
		t.Errorf("Use = %q, want %q", cmd.Use, "observe")
	}
	// --json 默认 true（brief 钉的值）：契约是「默认就吐 JSON」。
	// 光看 flag 存不存在不够 —— 默认 false 时用户不加 flag 就什么都拿不到。
	if f := cmd.Flags().Lookup("json"); f != nil && f.DefValue != "true" {
		t.Errorf("--json 默认值 = %q, want \"true\"", f.DefValue)
	}
}

// 注册必须真的挂在 rootCmd 上：brief 的包级变量检查（observeCmd != nil）
// 过不了这条 —— 变量存在但没 AddCommand 的 CLI，用户敲 observe 会得到
// `unknown command`。
func TestObserveCommandRegisteredOnRoot(t *testing.T) {
	found := false
	for _, sub := range rootCmd.Commands() {
		if sub.Name() == "observe" {
			found = true
			if sub == observeCmd {
				continue
			}
		}
	}
	if !found {
		t.Error("observe 未注册到 rootCmd")
	}
}

// TestRenderHumanSurfacesTargetAmbiguous —— 人话输出必须把 target-ambiguous 说出来，
// 而且 detail 里的 target ID 不许被截掉（ID 就是人判断「是不是看错页了」的依据）。
//
// 为什么不放 e2e：那个状态是**一次性**的 —— 它靠把页面主线程堵死来造，而第一次观测
// 的求值会排队等到主线程放开才返回；等它回来页面已经不堵了，第二次观测就恢复正常
// （实测：在 e2e 里写这条时，第二遍 --json=false 拿到的是「诊断 0 条」，断言红得
// 毫无意义）。这一行要证的是「印得对不对」，不需要真浏览器。
func TestRenderHumanSurfacesTargetAmbiguous(t *testing.T) {
	const pickedID = "A916E5F1A20E1885467D6FE3AAD587ED"
	m := &internal.PageModel{
		URL:   "http://127.0.0.1:9/__busy.html",
		Title: "R3 busy",
		Diagnostics: []internal.Diagnostic{{
			Kind: internal.DiagKindTargetAmbiguous,
			// 与 targetDiagsFor 产出的形状一致（含挑中的 ID 与 URL）
			Detail: "页面目标共 2 个候选，没有一个报 visible（可能都是后台页，也可能可见性检查本身就失败了）" +
				"—— 退回选中第一个：" + pickedID + " (http://127.0.0.1:9/__busy.html)",
			FramePath: []string{"main"},
		}},
	}

	var buf bytes.Buffer
	if err := renderHuman(&buf, m); err != nil {
		t.Fatalf("renderHuman 失败: %v", err)
	}
	out := buf.String()

	// 「诊断 N 条」那一行要数上它（运营扫一眼就知道有没有事）
	if !strings.Contains(out, "诊断 1 条") {
		t.Errorf("摘要里的诊断条数没算上它:\n%s", out)
	}
	// kind 与 detail 的两个要件都要露出来（target ID 被截掉就等于没这条诊断）
	for _, want := range []string{
		internal.DiagKindTargetAmbiguous,
		pickedID,
		"2 个候选",
		"没有一个报 visible",
	} {
		if !strings.Contains(out, want) {
			t.Errorf("人话输出里没有 %q —— 人看不到「可能看错页了、猜的是哪个」:\n%s", want, out)
		}
	}
	// 它走的是「诊断」那一块，不是「遮挡物」—— 两个通道混了会让读者去点一个不存在的按钮
	if i, j := strings.Index(out, "诊断（"), strings.Index(out, internal.DiagKindTargetAmbiguous); i < 0 || j < i {
		t.Errorf("target-ambiguous 没出现在诊断块里:\n%s", out)
	}
	t.Logf("人话输出:\n%s", out)
}
