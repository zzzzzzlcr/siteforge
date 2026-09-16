package cmd

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"strings"
	"unicode"

	"cdp/internal"

	"github.com/spf13/cobra"
)

// diffCmd 回答一个问题：**「刚才那一下有没有推进」**（规格 §4.2、§4.6）。
//
// 它是 py 里**分支与重试的唯一依据**：判有推进就继续，判没推进就换策略。
// 用法（py 里就是这三行）：
//
//	cdp observe > /tmp/before.json      # 动作前
//	<做动作>
//	cdp diff --before /tmp/before.json  # 动作后：刚才那一下推进了吗
//
// ⚠️ 退出码约定（与 observe 同一套原则：**判断是数据，不是退出码**）：
//
//	0 = 差分算出来了（★ 「actionable=false」仍然是 0 —— 那是一个**正常答案**，
//	    不是错误。py 该去读 JSON 里的 actionable，别拿退出码当判据：
//	    把「没推进」编成非 0，任何 `cdp diff ... || 收尾` 的写法都会静默走错分支）
//	1 = 差分算不出来（--before 读不到 / 不是合法 PageModel JSON / 连不上 Chrome /
//	    观测失败）—— 这种时候 stdout 上**没有** JSON，py 必须当成硬错误，
//	    而不是当成「没推进」（那会把一次观测故障读成一次策略选择）
//
// 输出格式与 observe 同形：默认 `--json`（true）—— py 侧**只该用这一种**；
// `--json=false` 是给人看的一段摘要（见 renderDiffHuman），别拿去解析。
var diffCmd = &cobra.Command{
	Use:   "diff",
	Short: "差分：回答「刚才那一下有没有推进」（py 里分支与重试的依据）",
	Long: `diff 把「动作前的快照」与「现在的页面」比一遍，回答一个问题：刚才那一下有没有推进。

判据（三条都不长在选择器上 —— 框架生成的 hash class 一刷就变，拿选择器算会
把一次重渲染谎报成「有进展」）：
  · URL 变了（SPA 的 pushState 也算）
  · 可见正文变了（归一空白后）
  · 身份上真有元素出现/消失（正文 + 角色 + 区域的多重集，不是选择器集合）

⚠️ 能力边界 —— 它判的是**导航与组成变化**，**不判填写与选择**：
  它的判据里没有**字段值**这一项（比的是 URL / 可见正文 / 元素身份）——
  所以「值填进去了没有 / 勾上了没有 / 只是高亮了一下」这些步骤，
  diff 原理上答不了：值变了而页面组成没变，actionable 照样是 false。
  ⚠️ 别把 actionable=false 读成「这一步没成功」（那是这条边界最容易读错的方向）。

  填/选类步骤该怎么办（**不要**拿 diff 当这一步的判据）：
    · 首选**动作命令自己的退出码**：cdp form --select 在选项不存在时当场报错
      （option not found），--value / --check 在元素找不到时报错 —— 非 0 就是没做成；
    · 要断言「值真写进去了」：observe 现在**回读**它 —— 看字段/动作的 value
      （R20 已落地。⚠️ 三态：null = 不是值控件，"" = 是值控件但现在是空的）；
    · 要断言「这个选项选上了」：看 selected（true / false / **null = 看不出**，
      别把 null 读成没选 —— 那正是「答过了还反复重答」的成因）。

用法：
  cdp observe > before.json && <做动作> && cdp diff --before before.json

退出码：0 = 算出差分（actionable 为 false 也是 0，那是正常答案）；
        1 = 算不出来（读不到快照 / 观测失败）。`,
	RunE: runDiff,
}

func init() {
	rootCmd.AddCommand(diffCmd)
	// --before 必填：缺了它就没有「动作前」，只能报错 —— 不能悄悄退化成
	// 「observe 两次比一比」（那会把用户想比的两个时刻都换掉，且不报任何错）。
	//
	// ⚠️ 用法文字里**不能出现反引号**：pflag 会把第一对反引号里的内容当成
	// **参数名的占位符**（`--before <那里面的文字>`），于是帮助信息被拆坏、
	// 原本想说的那个命令整个消失（2026-09-16 手工跑时看到的就是这个）。
	diffCmd.Flags().String("before", "", "动作前的 PageModel JSON 文件（先用 cdp observe 输出一份，见 Long）")
	_ = diffCmd.MarkFlagRequired("before")
	// ⚠️ 帮助文字要说**现在**的真话（修复轮 1 的 M1）：原先写「保留开关便于以后加
	// 人类可读格式」，可人话格式早就有了（renderDiffHuman）—— 那正是「看起来能切的
	// 开关、实际不说清楚」那一类。
	diffCmd.Flags().Bool("json", true, "默认输出 JSON（py 侧只该用这一种）；--json=false 输出人话摘要（给人看，别解析）")
	// ⚠️ 它**只影响「动作后」那一次观测**（--before 永远是一份完整快照文件）。
	// 拿整页的 --before 配它去比，其它帧的元素会在差分里**整批变成「消失」**
	// → actionable=true —— 一次静默的假进展（页面其实一动没动）。所以它只适合
	// 「--before 也是同一帧的单帧快照」那种调试场景，不是给人日常用的开关。
	diffCmd.Flags().String("frame-id", "", "只观测「动作后」那一帧（--before 不受它影响）；配整页快照用会把其它帧的元素全报成 disappeared 并给出**假** actionable=true —— 非调试别传")
}

func runDiff(cmd *cobra.Command, args []string) error {
	beforePath, _ := cmd.Flags().GetString("before")
	before, err := loadPageModel(beforePath)
	if err != nil {
		return err
	}

	client, err := internal.NewClient(GetHost(), GetPort())
	if err != nil {
		return fmt.Errorf("failed to create client: %w", err)
	}
	defer client.Disconnect()

	frameID, _ := cmd.Flags().GetString("frame-id")
	var after *internal.PageModel
	if frameID != "" {
		after, err = client.Observe(frameID)
	} else {
		after, err = client.ObserveAll()
	}
	if err != nil {
		return err
	}

	d := internal.DiffModels(before, after)
	jsonOut, _ := cmd.Flags().GetBool("json")
	if jsonOut {
		enc := json.NewEncoder(os.Stdout)
		enc.SetIndent("", "  ")
		return enc.Encode(d)
	}
	return renderDiffHuman(os.Stdout, d, before, after)
}

// loadPageModel 读一个快照文件（就是 `cdp observe` 吐出来的那份 JSON）。
//
// 报错要把「文件是什么、该怎么办」说清楚：这条 CLI 的调用方常常是 py 里的一行
// subprocess，或者一个手敲命令的人 —— 一句 `unexpected end of JSON input`
// 帮不了任何人。
func loadPageModel(path string) (*internal.PageModel, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("读不到动作前的快照 %q: %w（先用 `cdp observe > %s` 存一份）", path, err, path)
	}
	var m internal.PageModel
	if err := json.Unmarshal(raw, &m); err != nil {
		return nil, fmt.Errorf("%q 不是一份 PageModel JSON: %w"+
			"（它得是 `cdp observe` 的默认输出；`--json=false` 的人话摘要不能拿来当快照）", path, err)
	}
	// ⚠️ **解析得动 ≠ 是快照**（修复轮 1 的 I1）：`{}`、或者 `cdp navi` 自己的输出
	// （{"frame":{...}}）、或者任何别的 JSON，都能解析成一份**全零**的 PageModel。
	// 拿它对着一页活页面比 —— 每个元素都「新出现」→ Actionable=true、退出码 0
	// —— 一次「文件给错了」被读成一次**有进展**，而且完全不报错。
	//
	// 判据取「URL 为空 且 五类列表都空」：真快照**一定有 URL**（哪怕 about:blank
	// 也报 "about:blank"），有内容的页面至少有一类非空。两者同时成立只可能是
	// 「这压根不是一份 observe 模型」。
	if m.URL == "" && len(m.Actions)+len(m.Fields)+len(m.OptionGroups) == 0 {
		return nil, fmt.Errorf("%q 里没有 URL 也没有任何元素 —— 这不是一份 observe 快照"+
			"（{} / navi 的输出 / 别的 JSON 都能解析成功，但拿来比会把整页元素误报成「新出现」）。"+
			"先用 `cdp observe > %s` 存一份", path, path)
	}
	return &m, nil
}

// renderDiffHuman 是 `--json=false` 的**人话**输出（与 observe 的 renderHuman 同规矩）。
//
// 为什么要有：这条 CLI 的存在意义之一是**人能直接用**（D14/D15 那两条线上，运营与
// agent 都要能一眼看懂「刚才点那一下到底动没动」）。一个看起来能切格式、实际不切的
// flag，正是本项目反复栽的那类静默不做事的开关。
//
// ⚠️ 它会直接说「有推进 / 没推进」—— 这与 observe 的「只给事实、不给判断」不冲突：
// 那**就是** diff 这个工具的产出（规格 §4.2 把它写成一句判据），不是被藏起来的语义推理。
// 中间那几行仍然只摆事实，让人能自己核对判据。
func renderDiffHuman(w io.Writer, d internal.Diff, before, after *internal.PageModel) error {
	if before == nil {
		before = &internal.PageModel{}
	}
	if after == nil {
		after = &internal.PageModel{}
	}
	const (
		maxListed = 8
		lblWidth  = 6 // 标签列宽（**显示列**，中文算 2 列；见 pad）
	)

	if d.Actionable {
		fmt.Fprintln(w, "刚才那一下：有推进")
	} else {
		fmt.Fprintln(w, "刚才那一下：没推进")
	}
	fmt.Fprintln(w)

	if d.URLChanged {
		fmt.Fprintf(w, "%s变了   %s → %s\n", pad("URL", lblWidth),
			truncRunes(before.URL, 60), truncRunes(after.URL, 60))
	} else {
		fmt.Fprintf(w, "%s未变   %s\n", pad("URL", lblWidth), truncRunes(before.URL, 60))
	}
	if d.TextChanged {
		// ⚠️ 印两段**各自截断**的正文是最容易骗人的写法：变化若在第 80 字之后，
		// 两行会把同一段前缀印两遍、看着一模一样（2026-09-16 手工跑时实测到）。
		// 所以取「第一处差异」附近的窗口，让人一眼看到**到底哪里变了**。
		b, a := textDelta(before.PageText, after.PageText, 88)
		fmt.Fprintf(w, "%s变了   前: %s\n", pad("正文", lblWidth), b)
		fmt.Fprintf(w, "%s     后: %s\n", strings.Repeat(" ", lblWidth), a)
	} else {
		fmt.Fprintf(w, "%s未变   %s\n", pad("正文", lblWidth), truncRunes(before.PageText, 88))
	}
	writeSelList(w, "新出现", d.Appeared, maxListed, lblWidth)
	writeSelList(w, "消失", d.Disappeared, maxListed, lblWidth)

	// 诊断：**观测者自己的问题**，与页面内容分开摆（规格 §4.3）。
	// ⚠️ 它必须显眼：一次帧没取全，在模型里与「那些元素真的没了」长得一模一样 ——
	// 而 Actionable 会照样说「有推进」。人看得见这行，才知道这次的差分要不要信。
	if d.DiagnosticsAfter > 0 || d.DiagnosticsBefore > 0 {
		fmt.Fprintf(w, "\n⚠ 观测不全：动作后 %d 条诊断、动作前 %d 条（某帧没取到 / 帧枚举可能退化）。\n"+
			"  上面的「新出现/消失」里可能有**只是没看见**的元素 —— 别当成页面真的变了。\n",
			d.DiagnosticsAfter, d.DiagnosticsBefore)
	}

	// 末尾把这几个数摆出来，便于人工核对判据（三条都摆，包括没触发的那些）。
	fmt.Fprintf(w, "\nurl_changed=%t  text_changed=%t  appeared=%d  disappeared=%d  actionable=%t\n",
		d.URLChanged, d.TextChanged, len(d.Appeared), len(d.Disappeared), d.Actionable)
	fmt.Fprintf(w, "diagnostics_before=%d  diagnostics_after=%d\n", d.DiagnosticsBefore, d.DiagnosticsAfter)
	return nil
}

// writeSelList 印一组选择器，超过 maxListed 就截断并指路 --json
// （与 observe 的 renderHuman 一个规矩：摘要不假装是全量）。
func writeSelList(w io.Writer, label string, sels []string, maxListed, lblWidth int) {
	if len(sels) == 0 {
		fmt.Fprintf(w, "%s （无）\n", pad(label, lblWidth))
		return
	}
	shown := len(sels)
	if shown > maxListed {
		shown = maxListed
	}
	// 每个选择器也压一下：一条长结构路径能把整行撑到屏幕外。
	list := make([]string, 0, shown)
	for _, s := range sels[:shown] {
		list = append(list, truncRunes(s, 60))
	}
	fmt.Fprintf(w, "%s %d 个   %s\n", pad(label, lblWidth), len(sels), strings.Join(list, "  "))
	if len(sels) > shown {
		fmt.Fprintf(w, "%s …还剩 %d 个（用 --json 看全量）\n",
			strings.Repeat(" ", lblWidth), len(sels)-shown)
	}
}

// pad 把标签补到 n 个**显示列**。
//
// ⚠️ 不用 tabwriter：它按 **rune 数**对齐，而「正文」(2 rune / 4 显示列) 与
// 「URL」(3 rune / 3 显示列) 在天平上根本不是一回事 —— 中英混排的表格会整体错位。
// 这里只做一件事：汉字的宽度算 2。够本文件这几个标签用，不做通用宽度表。
func pad(s string, n int) string {
	w := displayWidth(s)
	if w >= n {
		return s
	}
	return s + strings.Repeat(" ", n-w)
}

// displayWidth 按**显示列**数长度：汉字算 2 列，其余算 1 列。
// 够本文件这几个标签用（全角标点/emoji 不在此列，真要排版得引宽度表 —— 不值）。
func displayWidth(s string) int {
	w := 0
	for _, r := range s {
		if unicode.Is(unicode.Han, r) {
			w += 2
			continue
		}
		w++
	}
	return w
}

// textDelta 取两段文本「第一处差异」附近的窗口，各印一段短摘录。
//
// 为什么不能各自从头截断：正文变化常常在几十个字符之后（页眉、cookie 横幅都在前面），
// 两段从头发截的摘录会**一模一样**，而上面写着「变了」—— 人看了只会以为工具坏了。
// 摘录都从差异点的同一个窗口取，所以「前/后」两行可以直接逐字对照着看。
func textDelta(before, after string, window int) (string, string) {
	b, a := []rune(before), []rune(after)
	// 找第一处不同；一段是另一段的前缀时，差异点就是短的那段的末尾。
	i := 0
	for i < len(b) && i < len(a) && b[i] == a[i] {
		i++
	}
	start := i - window/4
	if start < 0 {
		start = 0
	}
	return excerpt(b, start, window), excerpt(a, start, window)
}

// excerpt 从 start 起取 window 个字符，两端被截断时补省略号。
func excerpt(r []rune, start, window int) string {
	if start > len(r) {
		start = len(r)
	}
	end := start + window
	if end > len(r) {
		end = len(r)
	}
	s := string(r[start:end])
	if start > 0 {
		s = "…" + s
	}
	if end < len(r) {
		s += "…"
	}
	return s
}
