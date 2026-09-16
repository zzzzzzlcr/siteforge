package cmd

import (
	"strings"
	"testing"

	"github.com/spf13/cobra"
)

// diff 子命令的 CLI 契约（控制器裁定 ①：计划里那一步是占位，按 observe 同规格做）。
//
// ⚠️ 与 observe_test.go 一样，这一条只是**字符串断言级别**的弱测试：它证明 flag
// 挂上去了，证明不了 diff 真能跑通、输出真的是可解析的 Diff JSON。真正的闸门是
// diff_e2e_test.go（真构建二进制 + 真页面 + 真改页面）。这条留着是因为它便宜：
// 一个 flag 被误删时它先红，不必等 e2e。
func TestDiffCommandHasContractFlags(t *testing.T) {
	cmd := diffCmd
	if cmd == nil {
		t.Fatal("diffCmd 未注册")
	}
	for _, f := range []string{"before", "json", "frame-id"} {
		if cmd.Flags().Lookup(f) == nil {
			t.Errorf("diff 子命令缺 --%s", f)
		}
	}
	if cmd.Short == "" {
		t.Error("diff 子命令缺 Short 描述")
	}
	if cmd.Use != "diff" {
		t.Errorf("Use = %q, want %q", cmd.Use, "diff")
	}
	// --json 默认 true：契约是「默认就吐 JSON」（py 侧只该用这一种）。
	if f := cmd.Flags().Lookup("json"); f != nil && f.DefValue != "true" {
		t.Errorf("--json 默认值 = %q, want \"true\"", f.DefValue)
	}
}

// TestDiffCommandRequiresBefore 钉住 --before 是**必填**。
//
// 为什么这条值得单列（而不是「flag 存在就行」）：缺了 --before 就没有「动作前」，
// 正确的行为是**报错**。悄悄退化成「observe 两次比一比」的话，用户以为在比
// 「动作前 vs 动作后」，实际比的是「现在 vs 现在」—— 每次都会得到
// actionable=false（「没推进」），而 py 拿着它就会无限重试。**不报错的错。**
func TestDiffCommandRequiresBefore(t *testing.T) {
	f := diffCmd.Flags().Lookup("before")
	if f == nil {
		t.Fatal("diff 子命令缺 --before")
	}
	if _, ok := f.Annotations[cobra.BashCompOneRequiredFlag]; !ok {
		t.Error("--before 不是必填 —— 缺了它 diff 没法知道「动作前」是哪一刻，必须报错而不是猜")
	}
}

// TestTextDeltaPinsTheDifference 钉住人话输出里那两行正文摘录的**唯一**职责：
// 让人一眼看到**哪里变了**。
//
// 由来（2026-09-16 手工跑实测）：第一版是「各自从头截断 80 字」，而正文变化常常在
// 第 80 字之后（cookie 横幅、页眉都在前面）—— 于是输出成了
//
//	正文  变了   前: We use cookies. Accept All Learn More Save on your energy bill…
//	             后: We use cookies. Accept All Learn More Save on your energy bill…
//
// 两行一模一样，上面却写着「变了」。**人看了只会以为工具坏了**（那正是本项目
// 忌讳的那类输出：不报错，但读起来是错的）。修法：摘录从**第一处差异**附近取。
func TestTextDeltaPinsTheDifference(t *testing.T) {
	// 两段前 200 字相同的正文，差异在第 200 字之后。
	prefix := strings.Repeat("页眉和 cookie 横幅挡在前面。", 20) // 一段足够长的相同前缀
	before := prefix + "报价卡片：ZIP Code Continue"
	after := prefix + "报价卡片：Step 2 of 3 pick a plan"

	b, a := textDelta(before, after, 88)
	if b == a {
		t.Fatalf("两段摘录完全相同（%q）—— 人话输出会指着「变了」却给你两行一样的字", b)
	}
	for _, want := range []string{"ZIP Code", "Step 2 of 3"} {
		if !strings.Contains(b+a, want) {
			t.Errorf("两段摘录里看不到 %q —— 差异点在窗口外:\n前: %s\n后: %s", want, b, a)
		}
	}
	if len([]rune(b)) > 92 || len([]rune(a)) > 92 {
		t.Errorf("摘录没被限制在窗口内（%d / %d 字符）:\n%s\n%s",
			len([]rune(b)), len([]rune(a)), b, a)
	}
	// 窗口从差异点附近取 → 两段都要带省略号（差异不在开头）。
	if !strings.HasPrefix(b, "…") || !strings.HasPrefix(a, "…") {
		t.Errorf("摘录没标出「前面还有内容」:\n前: %s\n后: %s", b, a)
	}
}

// TestPadAlignsByDisplayWidth：标签列按**显示列**对齐（汉字算 2 列）。
// 用 tabwriter 做不到这件事：它按 rune 数对齐，「正文」(2 rune)与「URL」(3 rune)
// 在它眼里差 1 列，在屏幕上差 2 列 —— 中英混排的表格会整体错位。
func TestPadAlignsByDisplayWidth(t *testing.T) {
	for _, s := range []string{"URL", "正文", "新出现", "消失"} {
		if got := displayWidth(pad(s, 6)); got != 6 {
			t.Errorf("pad(%q, 6) 的显示宽度 = %d, want 6（补出来的标签列对不齐）", s, got)
		}
	}
	if got := pad("超长了不用补", 6); got != "超长了不用补" {
		t.Errorf("pad 不该动已经够宽的标签，实际 %q", got)
	}
}

// 注册必须真的挂在 rootCmd 上：变量存在但没 AddCommand 的 CLI，
// 用户敲 diff 会得到 `unknown command`。
func TestDiffCommandRegisteredOnRoot(t *testing.T) {
	for _, sub := range rootCmd.Commands() {
		if sub.Name() == "diff" {
			return
		}
	}
	t.Error("diff 未注册到 rootCmd")
}
