package internal

import (
	"encoding/json"
	"fmt"
	"strings"
)

// click 的**目标解析**：这一次点击到底会落在谁身上。
//
// 为什么要有这一层（2026-09-17 真站实测，docs/probes/2026-09-17-observe-blinkist/）：
//
//	缺陷 1（歧义）：模型要的是 Continue，它的 selector 却**匹配 5 个按钮**。
//	  `querySelector` 取的是文档序第一个 = **Back** —— 于是漏斗被倒着走，
//	  而 `cdp click` 既不拒绝、也不说点到了谁，返回一对完全正常的坐标。
//	缺陷 2（禁用）：`observe` 把 disabled 的 Continue 当普通候选递出来，
//	  点下去 exit 0 + 像样的坐标，**什么都没发生** —— 一次静默的空点被报成成功。
//
// 这两条都是「看起来成功了」的失败，是本项目最贵的一类。所以：
//
//	感知（两种模式都有）：匹配几个、用的是第几个、它禁没禁用、它长什么样。
//	        进 JSON（机器读）+ 一行人话（stderr，人读）。
//	严格（--strict / MCP 门默认）：歧义与禁用**当场失败**，且发生在动作**之前**。
//
// ⚠️ 默认路径的行为**一个字都不改**：仍然取第一个匹配、仍然不因为禁用而硬失败。
// 57 个生产脚本靠这条；改它要单独裁定。
//
// ⚠️ 为什么探测放在**滚动之后、算坐标之前**：探测要说的是「这一刻会点到谁」。
// 提前到滚动之前会快一点，但懒加载页面滚一下就换了一批匹配 —— 那样报的数
// 与真正点下去的那一下对不上，而「报的数与事实不符」比不报更坏。

// ClickCandidate 是候选里的一个元素（探测时抓下来的原始事实）。
//
// AriaLabel 是**给点击用的**：真站上那 5 个按钮的 text 全是空串，只有 aria-label
// 分得开（Back / Disagree / Not sure / Agree / Continue）。没有它，「匹配了 5 个」
// 这句报错对调用方毫无用处 —— 它没法判断该把选择器改成什么。
//
// ⚠️ 与 `observe` 的 `Action` 是两回事：那边**没有** aria-label（规格把它记成
// 另一条能力缺口）。这里只服务于「说清这一下点到谁」，不扩 observe 的契约。
type ClickCandidate struct {
	Tag       string `json:"tag"`
	Text      string `json:"text"`
	AriaLabel string `json:"aria_label"`
	Disabled  bool   `json:"disabled"`
	BBox      [4]int `json:"bbox"`
}

// 描述里 text / aria-label 的截断长度。
//
// ⚠️ 必须短：这一行会进 stderr、进报错，而下游（py 的 `_ok()`）是拿
// **子串**判「这条命令报没报错」的。元素的文本是页面作者写的，长文本会把
// 日志冲散；极端的文本（比如按钮上真写着 error:）还会污染那个判据。
// 40 与 observe 的人话渲染同口径。
const clickDescTextCap = 32

// Describe 是给人和模型看的一行：`<button> "Continue" [160,78,24,24] disabled`。
//
// 以 `<tag>` 开头是刻意的：调用方一眼能看出「点到的是什么类型的东西」，
// 而不用去猜哪一段是标签、哪一段是文本。
//
// ⚠️ 截断在这里（Go 侧）**也**做一遍，不只靠探测脚本里的 `.slice`：
// 那个 slice 只保得住「JS 产出的候选」这一条路。描述还会被 Go 侧的构造者、
// 以及以后任何新的产出方渲染 —— 上限只写在一处，换一条路就没了。
// （实测过：把 slice 从脚本里去掉、或者用 Go 直接构造一个 ClickCandidate，
// 这一行会变成一个 400+ 字符的巨物，进 stderr、进报错、进下游的子串判据。）
func (c ClickCandidate) Describe() string {
	var b strings.Builder
	fmt.Fprintf(&b, "<%s>", c.Tag)
	if c.Text != "" {
		fmt.Fprintf(&b, " %q", capRunes(c.Text, clickDescTextCap))
	}
	if c.AriaLabel != "" {
		fmt.Fprintf(&b, " aria-label=%q", capRunes(c.AriaLabel, clickDescTextCap))
	}
	fmt.Fprintf(&b, " [%d,%d,%d,%d]", c.BBox[0], c.BBox[1], c.BBox[2], c.BBox[3])
	if c.Disabled {
		b.WriteString(" disabled")
	}
	return b.String()
}

// capRunes 按**字符**（不是字节）截断 —— 中文按钮文字按字节截会切出半个字。
func capRunes(s string, n int) string {
	r := []rune(s)
	if len(r) <= n {
		return s
	}
	return string(r[:n]) + "…"
}

// ClickProbe 是一次目标探测的结果。
type ClickProbe struct {
	// MatchCount 是选择器命中的**总数**（不只是抓下来的那几个）。
	MatchCount int `json:"match_count"`
	// MatchIndex 是「用的是第几个」（文档序，0 起）。默认路径取的是**第一个**：
	// `__cdpQA(sel)[0]` 与 `__cdpQ(sel)` 是同一个元素 —— 两个助手都按
	// `__cdpRoots` 的同一顺序遍历（先各 root 的 querySelectorAll 拼起来，
	// 再取首个），所以「第 0 个」就是坐标与点击真正用的那一个。
	MatchIndex int              `json:"match_index"`
	Candidates []ClickCandidate `json:"candidates"`
}

// Chosen 是实际会被点的那个候选。
func (p *ClickProbe) Chosen() (ClickCandidate, bool) {
	if p == nil || p.MatchIndex < 0 || p.MatchIndex >= len(p.Candidates) {
		return ClickCandidate{}, false
	}
	return p.Candidates[p.MatchIndex], true
}

// candidateCap 是抓下来描述的候选个数上限 —— 报错要**可读**，不是把整页倒出来。
const candidateCap = 6

// probeTargetJS 生成「这个选择器会点到谁」的求值脚本。
//
// 判据一律走内核助手 `__cdpQA`（withPierce 注入），不自实现遍历 ——
// 与 GetElementCenter / click 用的是**同一套**解析（shadow / 顺序都一致）。
// 两边各写一套的话，报出来的数与真正点下去的那一下会静默分家。
func probeTargetJS(selector string) string {
	escaped := escapeJS(selector)
	return withPierce(fmt.Sprintf(`(function(){
  var els = __cdpQA('%s');
  if (!els.length) return JSON.stringify({error: 'element not found'});
  function one(el) {
    var r = el.getBoundingClientRect();
    return {
      tag: el.tagName.toLowerCase(),
      text: (el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, %d),
      aria_label: (el.getAttribute('aria-label') || '').slice(0, %d),
      // 禁用的**两条**判据，缺一不可：
      //   disabled 属性  —— 走 IDL（button/input/select/textarea/fieldset）
      //   aria-disabled  —— ARIA 那一套，IDL 仍是 false，但站点的 JS 会忽略点击
      //                     （MUI / Bootstrap 的自定义控件大量用它）
      // 只认一条的话，另一条就是「点了没反应但报成功」——正是这个缺陷本来的样子。
      disabled: (el.disabled === true || el.getAttribute('aria-disabled') === 'true'),
      bbox: [Math.round(r.left), Math.round(r.top), Math.round(r.width), Math.round(r.height)]
    };
  }
  var cap = Math.min(els.length, %d), out = [];
  for (var i = 0; i < cap; i++) out.push(one(els[i]));
  return JSON.stringify({ match_count: els.length, match_index: 0, candidates: out });
})()`, escaped, clickDescTextCap, clickDescTextCap, candidateCap))
}

// probeClickTarget 在**指定帧**上探测一次。帧与点击用的是同一个 frameID ——
// 跨源 iframe 里的元素必须在那一帧的上下文里解析，否则命中数是主帧的。
func (c *Client) probeClickTarget(selector, frameID string) (*ClickProbe, error) {
	var raw string
	if err := c.EvalInFrame(frameID, probeTargetJS(selector), &raw); err != nil {
		return nil, fmt.Errorf("探测目标失败: %w", err)
	}
	var probe ClickProbe
	if err := json.Unmarshal([]byte(raw), &probe); err != nil {
		return nil, fmt.Errorf("探测结果的契约解不开: %w\n原始: %.200s", err, raw)
	}
	if probe.MatchCount == 0 {
		return nil, fmt.Errorf("element not found")
	}
	return &probe, nil
}

// strictClickRefusal 是**严格模式**的判据（纯函数，可单测）。
//
// 判据只有两条，且**先判歧义**：歧义让「寻址」这件事本身不成立 —— 那种情况下
// 目标是不是禁用都无所谓了，先把选择器改对才是调用方要做的事。
func strictClickRefusal(selector string, p *ClickProbe) error {
	if p == nil {
		return nil
	}
	if p.MatchCount > 1 {
		chosen, _ := p.Chosen()
		return fmt.Errorf("选择器 %q 匹配 %d 个元素 —— 拒绝点击：不知道你要哪一个。"+
			"（宽松模式会静默取文档序第 1 个，也就是 %s；真站实测里那一下点到了 **Back**，"+
			"把漏斗走回去了。）请换一个**唯一**的选择器：observe 给的 alternates 里那条 "+
			"nth-of-type 路径是唯一的，或者把选择器收窄到只命中目标。\n候选：\n%s",
			selector, p.MatchCount, chosen.Describe(), formatCandidates(p))
	}
	if chosen, ok := p.Chosen(); ok && chosen.Disabled {
		return fmt.Errorf("选择器 %q 指向的元素是**禁用的**（%s）—— 拒绝点击："+
			"点了什么都不会发生，而回执看起来会像成功（真站实测：exit 0 + 一对像样的坐标，"+
			"页面纹丝不动 —— 这正是最难查的那类失败）。"+
			"等它可点（先满足页面上的前置条件）再点，或者换一个目标。", selector, chosen.Describe())
	}
	return nil
}

// formatCandidates 把候选摆成多行，给报错用。
func formatCandidates(p *ClickProbe) string {
	var b strings.Builder
	for i, c := range p.Candidates {
		fmt.Fprintf(&b, "  [%d] %s\n", i, c.Describe())
	}
	if p.MatchCount > len(p.Candidates) {
		fmt.Fprintf(&b, "  …还有 %d 个（用 observe 看全量）\n", p.MatchCount-len(p.Candidates))
	}
	return b.String()
}

// ClickResult 是一次点击的回执。
//
// x / y 是**老契约**（py 与 agent 都靠它），其余字段是这次新加的**机器可读**那一半：
// 消费方不用再靠「点一下试试」去发现「我点到了谁、那个东西能不能点」。
type ClickResult struct {
	X float64 `json:"x"`
	Y float64 `json:"y"`

	// MatchCount 是选择器命中几个元素。**只有探测跑成了才有这个字段** ——
	// 探测没跑成时省略，而不是编一个 0（0 看起来像「一个都没命中」，
	// 而那是一种合法答案：那时候点击本身早就失败了）。
	MatchCount int `json:"match_count,omitempty"`
	// MatchIndex 是实际会点的那个在命中集合里的位置（文档序，0 起）。
	MatchIndex int `json:"match_index"`
	// Target 是实际会点的那个元素的一行描述（`<button> "Continue" [x,y,w,h]`）。
	//
	// 它是「我到底点到了谁」的答案 —— 歧义那条链里，模型以为自己点的是 Continue，
	// 实际点到的是 Back；没有这个字段，那件事在回执里**一个字都没有**。
	//
	// ⚠️ 已知的相互作用（写在这儿，免得下一个人当成没想过）：描述里含**页面作者
	// 写的**文本（截到 32 字符）。下游有拿**子串**判「这条命令报没报错」的消费者
	// （siteforge 的 `fixtures/reference_site.py` `_ok()` 扫 ERR_MARKERS，
	// 其中包含 `not found` / `Error:`）—— 一个文字里带这些字样的按钮被点中时，
	// 那类判据会把它读成失败。代价与收益的取舍：这个字段的价值是「发现自己点错了」，
	// 而误判方向是**响亮**的（那一步被重试），不是静默的。
	// 要彻底消掉这条，只能把这个字段从**成功路径**里拿掉（拒绝那一路不受影响 ——
	// 那时候命令本来就已经失败了）；这是一个一行改动的口子，留待需要时再收。
	Target string `json:"target,omitempty"`
	// TargetDisabled 是它禁没禁用（IDL disabled 或 aria-disabled）。
	TargetDisabled bool `json:"target_disabled"`
	// ProbeError 只在探测**没跑成**时出现：这时上面几个字段都不可信，
	// 宁可不给，也不给一个看起来像答案的零值。
	ProbeError string `json:"probe_error,omitempty"`

	// Probe 是探测的原始结果（含候选全表）。`json:"-"`：JSON 里只放上面那几个
	// 扁平字段（agent 读的是那些），这张全表只给 Go 侧渲染人话那一行用。
	Probe *ClickProbe `json:"-"`
}
