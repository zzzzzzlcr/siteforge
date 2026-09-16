package cmd

import (
	"testing"
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
	for _, f := range []string{"json", "frame-id"} {
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
