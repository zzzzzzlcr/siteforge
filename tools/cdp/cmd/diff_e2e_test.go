package cmd

import (
	"encoding/json"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"testing"

	"cdp/internal"
)

// 端到端：**真构建二进制、真跑、真页面、真改页面**（控制器裁定 ① —— 计划里
// `cmd/diff.go` 那一步是占位，按 observe 同规格做）。
//
// 为什么非要跑二进制而不是直调 DiffModels：进程内直调测不到 flag 解析（cobra 那层）、
// 测不到快照文件的读写、测不到 stdout 上到底吐了什么字节、测不到**退出码**。
// 而这条 CLI 是 py 里分支与重试的**唯一依据** —— 它坏了没有任何东西会说话。
//
// ⚠️ 与 observe_e2e_test.go 共用同一个**私有** headless Chrome（env(t)：私有端口 +
// 私有 profile + TestMain 回收）。**不碰** 127.0.0.1:9222 那个实例：`go test ./...`
// 会并行跑 cmd 与 internal 两个测试二进制，共享 9222 时它们会互相改页面。

// snapshot 跑一次 `cdp observe`，把模型 JSON 存进文件 —— 就是 py 里那一行
// `cdp observe > before.json`。返回存下来的原始 JSON。
func (e *testEnv) snapshot(t *testing.T, name string) (string, string) {
	t.Helper()
	out, errOut, code := e.observe(t)
	if code != 0 {
		t.Fatalf("observe 退出码 = %d，快照存不下来\nstderr: %s", code, errOut)
	}
	path := filepath.Join(t.TempDir(), name)
	if err := os.WriteFile(path, []byte(out), 0o644); err != nil {
		t.Fatalf("写快照失败: %v", err)
	}
	return path, out
}

// eval 用**被测二进制自己的 eval** 改页面 —— 与 py 运行时同一条路。
func (e *testEnv) eval(t *testing.T, js string, extraArgs ...string) {
	t.Helper()
	args := append([]string{"eval"}, extraArgs...)
	args = append(args, js)
	out, errOut, code := e.run(t, args...)
	if code != 0 {
		t.Fatalf("eval 退出码 = %d\njs: %s\nstderr: %s", code, js, errOut)
	}
	_ = out
}

// diffRun 跑一次 `cdp diff --before <path>`，返回 stdout/stderr/退出码。
func (e *testEnv) diffRun(t *testing.T, beforePath string, extraArgs ...string) (string, string, int) {
	t.Helper()
	args := append([]string{"diff", "--before", beforePath}, extraArgs...)
	return e.run(t, args...)
}

// parseDiff 断言 stdout 是合法 JSON + 契约字段齐全（含「空列表是 [] 不是 null」），
// 再解成 internal.Diff。任何日志混进 stdout 都会在这里被杀掉。
func parseDiff(t *testing.T, out string) internal.Diff {
	t.Helper()
	if !json.Valid([]byte(out)) {
		t.Fatalf("stdout 不是合法 JSON:\n%.400s", out)
	}
	var raw map[string]json.RawMessage
	if err := json.Unmarshal([]byte(out), &raw); err != nil {
		t.Fatalf("解析成 map 失败: %v", err)
	}
	for _, k := range []string{"url_changed", "text_changed", "appeared", "disappeared", "actionable"} {
		if _, ok := raw[k]; !ok {
			t.Errorf("diff 输出缺契约字段 %q", k)
		}
	}
	// 空列表必须是 `[]`，**不许**是 `null`（与 PageModel 同一条约定）。
	for _, k := range []string{"appeared", "disappeared"} {
		s := string(raw[k])
		if len(s) == 0 || s[0] != '[' {
			t.Errorf("%s 不是 JSON 数组（空列表必须是 []，不是 null）: %.80s", k, s)
		}
	}
	var d internal.Diff
	if err := json.Unmarshal([]byte(out), &d); err != nil {
		t.Fatalf("按 internal.Diff 解不动: %v", err)
	}
	return d
}

// ---- ① 真推进：判对「有进展」 ----

// TestDiffCommandEndToEndDetectsProgress：动作前存快照 → 真改页面（把报价卡片换成第二步）
// → diff 必须报 Actionable=true，并且指名道姓说出谁出现了、谁消失了。
//
// ⚠️ 这条特意用**同一个 URL**（页面内改 DOM，不导航）—— 于是 URLChanged 是 false，
// 「有推进」只能由正文/多重集两条腿判出来。py 面对的正是这种 SPA：URL 一动不动，
// 页面已经翻了好几步。
func TestDiffCommandEndToEndDetectsProgress(t *testing.T) {
	e := env(t)
	e.navigate(t, e.fixture+"/base.html")
	beforePath, beforeOut := e.snapshot(t, "before.json")

	var before internal.PageModel
	if err := json.Unmarshal([]byte(beforeOut), &before); err != nil {
		t.Fatalf("快照解不动: %v", err)
	}
	if !hasSelector(before.Actions, "#go") || !hasSelector(before.Actions, "#schedule-now") {
		t.Fatalf("动作前的快照里没有 #go / #schedule-now —— 这条测试会空转通过: %q", selectors(before.Actions))
	}

	// 一次真实的「推进」：报价卡片整块换成第二步（新正文、新按钮、新字段；URL 不变）。
	e.eval(t, `(function(){var c=document.getElementById('quote-card');`+
		`c.innerHTML='<h3>Step 2 of 3 &mdash; pick a plan</h3>'`+
		`+'<label for="plan">Plan</label><select id="plan" name="plan"><option>Basic</option><option>Premium</option></select>'`+
		`+'<button id="confirm">Confirm and continue</button>';return 'ok';})()`)

	out, errOut, code := e.diffRun(t, beforePath)
	if code != 0 {
		t.Fatalf("diff 退出码 = %d, want 0\nstderr: %s", code, errOut)
	}
	d := parseDiff(t, out)

	if !d.Actionable {
		t.Errorf("页面真的翻了第二步，却判成没推进: %+v", d)
	}
	if d.URLChanged {
		t.Errorf("URL 不该变（这条正是「SPA 靠内容判推进」的场景）: %+v", d)
	}
	if !d.TextChanged {
		t.Errorf("正文变了（报价卡片换成了第二步），text_changed 却是 false: %+v", d)
	}
	// 旧卡片里的两件东西没了（#zip 输入框、#go 按钮），新卡片里的两件东西出现了
	// （#plan 下拉、#confirm 按钮）。其余元素（标题按钮、cookie 横幅、两个 Learn More）
	// 一个都没动，**不许**出现在这两个列表里 —— 那正是「匹配到身份就不报」的意思。
	//
	// ⚠️ #zip 与 #plan 都既是「可动作元素」又是「表单字段」（observeJS 两条通道都收
	// input/select），所以每条各被收集两次；报出来的是去重后的抓手。
	if want := []string{"#zip", "#go"}; !slices.Equal(d.Disappeared, want) {
		t.Errorf("disappeared = %q, want %q", d.Disappeared, want)
	}
	if want := []string{"#plan", "#confirm"}; !slices.Equal(d.Appeared, want) {
		t.Errorf("appeared = %q, want %q", d.Appeared, want)
	}
	t.Logf("退出码=0；actionable=%t appeared=%q disappeared=%q", d.Actionable, d.Appeared, d.Disappeared)
}

// TestDiffCommandEndToEndNoProgress：什么都没做 → Actionable=false，**且退出码仍是 0**
// （「没推进」是一个正常答案，不是错误；把它编成非 0，任何 `diff ... || …`
// 的写法都会静默走错分支）。
func TestDiffCommandEndToEndNoProgress(t *testing.T) {
	e := env(t)
	e.navigate(t, e.fixture+"/base.html")
	beforePath, _ := e.snapshot(t, "before.json")

	out, errOut, code := e.diffRun(t, beforePath)
	if code != 0 {
		t.Fatalf("「没推进」不该是非 0 退出码（那是正常答案）: code=%d\nstderr: %s", code, errOut)
	}
	d := parseDiff(t, out)
	if d.Actionable {
		t.Errorf("页面纹丝不动却判成有推进 —— py 会原地打转直到耗尽轮数: %+v", d)
	}
	if len(d.Appeared) != 0 || len(d.Disappeared) != 0 {
		t.Errorf("没变化却报出了增删: appeared=%q disappeared=%q", d.Appeared, d.Disappeared)
	}
	if d.URLChanged || d.TextChanged {
		t.Errorf("URL/正文都没变: %+v", d)
	}
	t.Logf("退出码=0；actionable=false appeared=%q disappeared=%q", d.Appeared, d.Disappeared)
}

// ---- ② 真重渲染：判对「没进展」（本任务的核心，控制器裁定 ②） ----

// TestDiffCommandImmuneToSelectorRename 是本任务的核心闸门。
//
// 场景（T5 的真发现，在**真浏览器**上复现）：页面内容一个字都没变，只是框架重渲染
// 了一遍 —— hash class 全换名字，于是观察出来的 selector 全变了。
// 期望：Actionable=false。**否则** py 会在一个什么都没发生的页面上一直往下走，
// 直到耗尽轮数（不报任何错）。
//
// 这条对计划里的实现（选择器集合差）**必红** —— 它会把整页元素报成「全消失 + 全出现」。
//
// 改名覆盖**三条通道**（都用真浏览器验，不是构造的模型）：
//   - 动作：按钮/链接的 id + data-testid 抹掉、换上 hash class
//   - 字段：#zip 的 **id/name 抹掉且不给 aria-label**（修复轮 1 的扩展：早先注入
//     aria-label 当拐杖，等于**刻意保住**了真实重渲染会断掉的那个关联）
//   - **祖先 class 一起改**（修复轮 1 的扩展）：真实重渲染会把整棵子树的生成 class
//     都换掉 —— 而 region 的 'hero' 正是按**祖先 className** 判的。
//     只改元素自己、不动祖先，就永远测不到 region 那条路（C1 就是这样溜过去的）。
func TestDiffCommandImmuneToSelectorRename(t *testing.T) {
	e := env(t)
	e.navigate(t, e.fixture+"/base.html")
	beforePath, beforeOut := e.snapshot(t, "before.json")

	var before internal.PageModel
	if err := json.Unmarshal([]byte(beforeOut), &before); err != nil {
		t.Fatalf("快照解不动: %v", err)
	}

	// 重渲染：内容一字不动，只把「怎么找到它」与「它长在哪个 class 里」换掉。
	e.eval(t, `(function(){`+
		`var i=0;`+
		// ① 整棵树的 class 全换（含 section.hero、div.card 这些**祖先**）
		`Array.prototype.slice.call(document.querySelectorAll('*')).forEach(function(el){`+
		` if(typeof el.className==='string'&&el.className){el.className='css-9f8e7d6c'+(i++);}});`+
		// ② 可动作元素：id 与 data-testid 抹掉
		`Array.prototype.slice.call(document.querySelectorAll('button,a[href]')).forEach(function(el){`+
		` el.removeAttribute('id'); el.removeAttribute('data-testid');});`+
		// ③ 打断 for=/id 配对：抹掉 input 的 id 与 name，**不**补 aria-label
		//    （label[for=zip] 从此指向不存在的 id —— 这正是重渲染换 id 的后果）
		`var z=document.getElementById('zip');`+
		` if(z){z.removeAttribute('id');z.removeAttribute('name');}`+
		`return 'renamed';})()`)

	// 正控：选择器**必须真的全变了**。否则后面那条「判没进展」是空转通过
	// （页面没变 → 判没进展 → 绿），什么都证明不了。
	outA, errOutA, codeA := e.observe(t)
	if codeA != 0 {
		t.Fatalf("改名后 observe 退出码 = %d\nstderr: %s", codeA, errOutA)
	}
	var after internal.PageModel
	if err := json.Unmarshal([]byte(outA), &after); err != nil {
		t.Fatalf("改名后的模型解不动: %v", err)
	}
	if len(before.Actions) == 0 || len(after.Actions) == 0 {
		t.Fatalf("actions 为空（前 %d / 后 %d）—— 这条测试会空转通过",
			len(before.Actions), len(after.Actions))
	}
	shared := sharedSelectors(before.Actions, after.Actions)
	if len(shared) != 0 {
		t.Fatalf("改名后仍有 %d 个选择器没变: %q\n前: %q\n后: %q\n"+
			"（改名没生效的话，下面那条「判没进展」是空转通过的，必须先修夹具）",
			len(shared), shared, selectors(before.Actions), selectors(after.Actions))
	}
	if before.PageText != after.PageText {
		t.Fatalf("改名把正文也改了（夹具问题，不是被测代码问题）:\n前: %.200s\n后: %.200s",
			before.PageText, after.PageText)
	}

	// 正控②：**那两个「派生字段」必须真的变了** —— 否则这条测试是空转通过，
	// 而 C1 恰恰就是「夹具刻意保住了真实重渲染会断的关联」溜过去的。
	// 这两条断言把「夹具确实打到了 region / label 那两条路」变成可观测事实：
	// 哪天夹具改了、或者 observeJS 的取法改了，这里先红，而不是静默变成空转。
	heroBefore, heroAfter := actionByText(t, before.Actions, "Schedule Now"), actionByText(t, after.Actions, "Schedule Now")
	if heroBefore.Region == heroAfter.Region {
		t.Fatalf("祖先 class 全换之后 region 没变（两边都是 %q）—— 夹具没打到 region 那条路，"+
			"下面「判没进展」是空转通过的", heroBefore.Region)
	}
	if isLandmarkRegion(heroBefore.Region) || isLandmarkRegion(heroAfter.Region) {
		t.Fatalf("region 是 landmark 派生的（%q → %q）—— 那本来就抗改名，"+
			"这条夹具证明不了「class 派生的 region 不再进身份键」", heroBefore.Region, heroAfter.Region)
	}
	zipBefore, zipAfter := fieldByPlaceholder(t, before.Fields, "ZIP Code"), fieldByPlaceholder(t, after.Fields, "ZIP Code")
	if zipBefore.Label == zipAfter.Label {
		t.Fatalf("打断 for=/id 配对之后 Field.Label 没变（两边都是 %q）—— "+
			"夹具没打到 label 那条路，下面「判没进展」是空转通过的", zipBefore.Label)
	}
	if zipBefore.Label == "" {
		t.Fatalf("动作前的 Field.Label 就是空的 —— 这条夹具证明不了「靠 id 找来的标签会断」")
	}

	out, errOut, code := e.diffRun(t, beforePath)
	if code != 0 {
		t.Fatalf("diff 退出码 = %d, want 0\nstderr: %s", code, errOut)
	}
	d := parseDiff(t, out)
	if d.Actionable {
		t.Errorf("只是重渲染（选择器全改名、内容一字未变）却判成**有推进** —— "+
			"py 会在什么都没发生的页面上一直往下走直到耗尽轮数，而且不报任何错。"+
			"\nappeared=%q\ndisappeared=%q", d.Appeared, d.Disappeared)
	}
	if len(d.Appeared) != 0 || len(d.Disappeared) != 0 {
		t.Errorf("改名被当成了「新出现/消失」: appeared=%q disappeared=%q", d.Appeared, d.Disappeared)
	}
	if d.URLChanged || d.TextChanged {
		t.Errorf("URL/正文都没变: %+v", d)
	}
	t.Logf("退出码=0；选择器全换了（%d 个 before / %d 个 after，交集 0）但 actionable=false",
		len(before.Actions), len(after.Actions))
}

// ---- ③ 人话输出（--json=false）：真二进制上跑一遍 ----

// TestDiffCommandHumanFormat 钉住 `--json=false` 那条路**真的切了格式**，而且
// 切出来的东西**读起来是对的**。
//
// 由来（2026-09-16 手工跑实测到的一个真错）：第一版把「前/后」两段正文各自从头
// 截断打印，而正文变化常在第 80 字之后（cookie 横幅、页眉都在前面）—— 于是输出成了
// 两行**一模一样**的字，上面还写着「变了」。不报错，但读起来是错的。
// 所以这条的核心断言是：**那两行必须不同**（见 textDelta 的单测）。
func TestDiffCommandHumanFormat(t *testing.T) {
	e := env(t)
	e.navigate(t, e.fixture+"/base.html")
	beforePath, _ := e.snapshot(t, "before.json")

	// 在正文**靠后的位置**改一处：把报价卡片的标题与按钮换掉（前面 80 字不动）。
	e.eval(t, `(function(){`+
		`document.querySelector('#quote-card h3').textContent='Step 2 of 3 - pick a plan';`+
		`document.getElementById('go').textContent='Confirm and continue';`+
		`return 'ok';})()`)

	out, errOut, code := e.diffRun(t, beforePath, "--json=false")
	if code != 0 {
		t.Fatalf("--json=false 退出码 = %d, want 0\nstderr: %s", code, errOut)
	}
	if json.Valid([]byte(out)) {
		t.Fatalf("--json=false 还是吐了 JSON（flag 静默不做事）:\n%.300s", out)
	}
	if strings.HasPrefix(strings.TrimSpace(out), "{") {
		t.Errorf("--json=false 的输出以 { 开头 —— 像是半截 JSON:\n%.300s", out)
	}
	for _, want := range []string{"刚才那一下", "有推进", "新出现", "消失", "actionable=true"} {
		if !strings.Contains(out, want) {
			t.Errorf("人话输出里没有 %q：\n%s", want, out)
		}
	}
	// ★ 前/后两行必须不同 —— 否则人看见的是「变了」+ 两行同样的字。
	before, after := excerptLines(t, out)
	if before == after {
		t.Errorf("「前」「后」两行完全一样（%q）—— 人话输出指着「变了」却给两行一样的字", before)
	}
	if !strings.Contains(before, "Continue") || !strings.Contains(after, "Confirm and continue") {
		t.Errorf("摘录没取到差异点附近:\n前: %s\n后: %s", before, after)
	}
	t.Logf("退出码=0；--json=false 输出：\n%s", out)
}

// excerptLines 取出人话输出里「前: …」「后: …」两行的内容。
func excerptLines(t *testing.T, out string) (string, string) {
	t.Helper()
	var b, a string
	for _, line := range strings.Split(out, "\n") {
		if i := strings.Index(line, "前: "); i >= 0 {
			b = line[i+len("前: "):]
		}
		if i := strings.Index(line, "后: "); i >= 0 {
			a = line[i+len("后: "):]
		}
	}
	if b == "" || a == "" {
		t.Fatalf("人话输出里找不到「前: … / 后: …」两行:\n%s", out)
	}
	return b, a
}

// ---- ④ 快照拿不到 / 用不了：硬错误，不许静默当成「没推进」 ----

func TestDiffCommandExitCodeOnBadBeforeFile(t *testing.T) {
	e := env(t)
	e.navigate(t, e.fixture+"/base.html")

	bad := filepath.Join(t.TempDir(), "not-a-model.json")
	if err := os.WriteFile(bad, []byte("这不是 JSON\n"), 0o644); err != nil {
		t.Fatalf("写坏文件失败: %v", err)
	}
	// 修复轮 1 的 I1：**解析得动 ≠ 是快照**。`{}` 与 `cdp navi` 的输出都能解析成
	// 一份全零 PageModel → 活页面上每个元素都「新出现」→ actionables=true、退出 0。
	empty := filepath.Join(t.TempDir(), "empty.json")
	if err := os.WriteFile(empty, []byte(`{}`), 0o644); err != nil {
		t.Fatalf("写空对象失败: %v", err)
	}
	navi := filepath.Join(t.TempDir(), "navi.json")
	if err := os.WriteFile(navi, []byte(`{"frame":{"frameId":"ABC","url":"http://x/"}}`), 0o644); err != nil {
		t.Fatalf("写 navi 输出失败: %v", err)
	}

	cases := []struct {
		name string
		path string
	}{
		{"文件不存在", filepath.Join(t.TempDir(), "missing.json")},
		{"文件不是 PageModel JSON", bad},
		{"空对象 {}（全零模型）", empty},
		{"navi 的输出（不是快照）", navi},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			out, errOut, code := e.diffRun(t, c.path)
			if code == 0 {
				t.Fatalf("快照用不了却退出码 0 —— py 会把它当成一次正常的「没推进」"+
					"\nstdout: %s", out)
			}
			if out != "" {
				t.Errorf("失败路径的 stdout 应当为空（不许有半截 JSON），实际: %.200s", out)
			}
			if errOut == "" {
				t.Error("失败路径必须有错误信息，实际 stderr 是空的")
			}
		})
	}
}

// TestDiffCommandMissingBeforeFlag：--before 是必填（见 diff_test.go 的注解断言，
// 这里从**真二进制**这一侧再钉一次：cobra 真的会拦下来并给非 0 退出码）。
func TestDiffCommandMissingBeforeFlag(t *testing.T) {
	e := env(t)
	out, errOut, code := e.run(t, "diff")
	if code == 0 {
		t.Fatalf("没给 --before 却退出码 0\nstdout: %s", out)
	}
	if !strings.Contains(errOut, "before") {
		t.Errorf("stderr 里没提 --before，用户不知道缺什么: %s", errOut)
	}
}

// ---- 小工具 ----

func slicesContains(hay []string, needle string) bool {
	for _, s := range hay {
		if s == needle {
			return true
		}
	}
	return false
}

// actionByText 按正文取动作 —— 改名夹具不动正文，所以文本是这条测试里**唯一**
// 稳定的身份（按选择器取会自我指涉：选择器正是被改掉的那个东西）。
func actionByText(t *testing.T, actions []internal.Action, text string) internal.Action {
	t.Helper()
	for _, a := range actions {
		if a.Text == text {
			return a
		}
	}
	t.Fatalf("动作列表里没有正文为 %q 的元素: %q", text, selectors(actions))
	return internal.Action{}
}

// fieldByPlaceholder 按 placeholder 取字段（同上：placeholder 是内容，改名不动）。
func fieldByPlaceholder(t *testing.T, fields []internal.Field, ph string) internal.Field {
	t.Helper()
	for _, f := range fields {
		if f.Placeholder == ph {
			return f
		}
	}
	t.Fatalf("字段列表里没有 placeholder 为 %q 的字段: %+v", ph, fields)
	return internal.Field{}
}

// isLandmarkRegion 判断区域是不是 landmark 派生的（= 抗改名的那一半，见
// internal/diff.go 的 regionIdentity —— 这张名单必须与它一致）。
func isLandmarkRegion(region string) bool {
	return slicesContains([]string{"header", "nav", "footer", "aside", "main", "dialog"}, region)
}

// sharedSelectors 返回两个动作列表里**同名**的选择器（改名检测用正控）。
// 用 map 去重只是为了报错信息短一点。
func sharedSelectors(a, b []internal.Action) []string {
	inB := map[string]bool{}
	for _, x := range b {
		inB[x.Selector] = true
	}
	seen := map[string]bool{}
	out := []string{}
	for _, x := range a {
		if inB[x.Selector] && !seen[x.Selector] {
			seen[x.Selector] = true
			out = append(out, x.Selector)
		}
	}
	return out
}
