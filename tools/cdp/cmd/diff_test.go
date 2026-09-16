package cmd

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	"cdp/internal"

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

// TestLoadPageModelRejectsJSONThatIsNotASnapshot 是修复轮 1 的 I1：
// **解析得动 ≠ 是一份快照**。
//
// `{}`、`cdp navi` 自己的输出 `{"frame":{...}}`、任何别的 JSON，都能解析成一份
// **全零** PageModel。拿它对着一页活页面比 → 每个元素都「新出现」→
// `actionable=true`、退出码 0：一次「文件给错了」被读成一次**有进展**，完全不报错。
func TestLoadPageModelRejectsJSONThatIsNotASnapshot(t *testing.T) {
	dir := t.TempDir()
	write := func(name, body string) string {
		p := filepath.Join(dir, name)
		if err := os.WriteFile(p, []byte(body), 0o644); err != nil {
			t.Fatalf("写 %s 失败: %v", name, err)
		}
		return p
	}

	reject := map[string]string{
		"空对象":         `{}`,
		"navi 的输出":    `{"frame":{"frameId":"ABC","url":"https://x.example/"}}`,
		"只有别的字段":      `{"hello":"world"}`,
		"五类列表都是 null": `{"url":"","actions":null,"fields":null,"option_groups":null}`,
	}
	for name, body := range reject {
		if _, err := loadPageModel(write(name+".json", body)); err == nil {
			t.Errorf("%s（%s）被当成了一份合法快照 —— 它会让整页元素被误报成「新出现」", name, body)
		}
	}

	accept := map[string]string{
		// 真快照一定带 URL，**哪怕页面是空的**（about:blank 也报 "about:blank"）。
		"空白页快照":  `{"url":"about:blank","actions":[],"fields":[],"option_groups":[]}`,
		"有动作的快照": `{"url":"https://x.example/","actions":[{"selector":"#go","text":"Continue"}]}`,
	}
	for name, body := range accept {
		m, err := loadPageModel(write(name+".json", body))
		if err != nil {
			t.Errorf("%s 被拒了（不该）: %v", name, err)
			continue
		}
		if m == nil {
			t.Errorf("%s 解出 nil", name)
		}
	}
}

// TestDiffHumanShowsDiagnostics 是修复轮 1 的 I3（人话那一侧）：
// 观测不全必须在人话里**显眼**，因为一次帧没取全与「那些元素真的没了」在模型里
// 长得一模一样，而 Actionable 会照样说「有推进」。
func TestDiffHumanShowsDiagnostics(t *testing.T) {
	before := &internal.PageModel{URL: "u", PageText: "Step 1"}
	after := &internal.PageModel{URL: "u", PageText: "Step 2",
		Diagnostics: []internal.Diagnostic{{Kind: internal.DiagKindFrameError, Detail: "iframe 没取到"}}}
	d := internal.DiffModels(before, after)

	var buf strings.Builder
	if err := renderDiffHuman(&buf, d, before, after); err != nil {
		t.Fatalf("renderDiffHuman 出错: %v", err)
	}
	out := buf.String()
	if !strings.Contains(out, "观测不全") || !strings.Contains(out, "diagnostics_after=1") {
		t.Errorf("观测不全没在人话输出里显示出来:\n%s", out)
	}

	// 反向：观测完整时不许出现那条警告（否则警告会变成天天都在的背景噪声）。
	clean := &internal.PageModel{URL: "u", PageText: "Step 2"}
	var buf2 strings.Builder
	if err := renderDiffHuman(&buf2, internal.DiffModels(before, clean), before, clean); err != nil {
		t.Fatalf("renderDiffHuman 出错: %v", err)
	}
	if strings.Contains(buf2.String(), "观测不全") {
		t.Errorf("观测完整却报了「观测不全」:\n%s", buf2.String())
	}
}

// TestDiffCommandHelpTellsTheTruth 是修复轮 1 的 M1 + I2：
// 帮助文字与实现**不许**矛盾。
//   - M1：`--json` 写着「保留开关便于以后加人类可读格式」，而人话格式早有了
//     —— 那正是「看起来能切格式、实际不说清楚」那类
//   - I2：`diff` 判不了「填/选/勾」（模型里没有字段值），Long 必须说清能力边界，
//     否则 py 侧会把它当成「这一步成没成功」的判据
func TestDiffCommandHelpTellsTheTruth(t *testing.T) {
	f := diffCmd.Flags().Lookup("json")
	if f == nil {
		t.Fatal("缺 --json")
	}
	if !strings.Contains(f.Usage, "人话") {
		t.Errorf("--json 的帮助文字没说 --json=false 是人话摘要（现在实际有这条路）: %q", f.Usage)
	}
	if strings.Contains(f.Usage, "便于以后") {
		t.Errorf("--json 的帮助文字还是「便于以后加人类可读格式」—— 它早就加了: %q", f.Usage)
	}
	for _, want := range []string{"不判填写", "字段值"} {
		if !strings.Contains(diffCmd.Long, want) {
			t.Errorf("Long 里没说清能力边界（缺 %q）—— py 会拿它判「填写成功没有」:\n%s", want, diffCmd.Long)
		}
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
