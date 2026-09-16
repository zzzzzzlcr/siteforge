package cmd

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"

	"cdp/internal"
)

// 端到端：**真构建二进制、真跑、真打页面**（Task 6 审查意见 ①）。
//
// 为什么非要跑二进制而不是直接调 runObserve()：
//   - 进程内直调的那条路，测不到 flag 解析（cobra 那一层）、测不到输出编码
//     （stdout 上到底吐了什么字节）、测不到**退出码**
//   - 而这个 CLI 是**运行阶段唯一的回退接口**（规格 D2）：产出的 py 跑在
//     worker 容器里，只能调 CLI 拿页面模型。它坏了，py 侧的
//     「selector 全挂 → 重新 observe」就整条断掉 —— 没有任何东西会说话。
//
// ⚠️ **为什么自己起一个浏览器，而不是用 127.0.0.1:9222 那个**（2026-09-16 实测）：
// `go test ./...` 会**并行**跑 cdp/cmd 与 cdp/internal 两个测试二进制，而
// internal 的集成测试（Task 3/4/5）往里**注入 DOM**（click_integration_test.go:187
// 直接改 document.body.innerHTML）。两边抢同一个页面目标 → 观测到的是对方注入的
// 页面。实测 3/3 复现：`#real-btn`（internal 的夹具）出现在本文件的断言里，
// 而 `go test ./cmd/` 单独跑是全绿。
//
// 所以这条闸门**自备浏览器**：私有端口 + 私有 profile，谁也别想动它的页面。
// 顺带把 --host/--port 这条链路也验了 —— 那正是 py 在 worker 容器里的用法
// （那边的 Chrome 不在 127.0.0.1:9222）。
//
// 没有 Chrome 时**失败**而不是 Skip：skip 与 pass 在报告里长得一样，
// 那正是「0 个页面目标 → 集成测试集体假绿」的成因。这条闸门宁可红。

// ---- 环境：私有 fixture 服务 + 私有浏览器 + 真二进制 ----

var (
	envOnce sync.Once
	envVal  *testEnv
	envErr  error
)

type testEnv struct {
	bin        string // 被测二进制
	srv        *httptest.Server
	fixture    string // fixture 服务基址
	chromePort int
	chromeCmd  *exec.Cmd
	logPath    string
	tempDirs   []string
}

func TestMain(m *testing.M) {
	code := m.Run()
	envVal.close()
	os.Exit(code)
}

// env 建（一次）并返回测试环境。三条测试共用：一个浏览器、一个 fixture 服务、
// 一个二进制；每条测试自己导航到自己的页面。
func env(t *testing.T) *testEnv {
	t.Helper()
	envOnce.Do(func() { envVal, envErr = newTestEnv() })
	if envErr != nil {
		t.Fatalf("搭端到端环境失败（这条闸门必须真跑二进制 + 真浏览器）: %v", envErr)
	}
	return envVal
}

func newTestEnv() (*testEnv, error) {
	e := &testEnv{}

	bin, binDir, err := buildBinary()
	if err != nil {
		return nil, err
	}
	e.bin, e.tempDirs = bin, append(e.tempDirs, binDir)

	e.srv = httptest.NewServer(http.FileServer(http.Dir(filepath.Join("..", "internal", "testdata"))))
	e.fixture = e.srv.URL

	port, err := freePort()
	if err != nil {
		return e, fmt.Errorf("找空闲端口失败: %w", err)
	}
	profileDir, err := os.MkdirTemp("", "cdp-observe-profile")
	if err != nil {
		return e, err
	}
	e.tempDirs = append(e.tempDirs, profileDir)
	e.chromePort = port
	e.logPath = filepath.Join(profileDir, "chrome.log")

	chromeCmd, err := launchChrome(port, profileDir, e.logPath)
	if err != nil {
		return e, err
	}
	e.chromeCmd = chromeCmd

	if err := waitForPageTarget(port, e.logPath, 20*time.Second); err != nil {
		return e, err
	}
	return e, nil
}

// close 幂等：TestMain 兜底调用，失败路径上也调。
func (e *testEnv) close() {
	if e == nil {
		return
	}
	if e.chromeCmd != nil {
		pid := e.chromeCmd.Process.Pid
		// 杀**整个进程组**：Chrome 会拉起 zygote / renderer / crashpad 一串子进程，
		// 只杀父进程会留孤儿（那正是本仓库栽过的坑：孤儿窗口 → 内存 95%）。
		_ = syscall.Kill(-pid, syscall.SIGKILL)
		_ = syscall.Kill(pid, syscall.SIGKILL)
		// 这里才 Wait：既回收僵尸，又不给「pid 被系统复用后误杀一组别人」留窗口
		// （不留 Wait goroutine 是有意的 —— 那样 pid 随时可能已被回收）。
		_ = e.chromeCmd.Wait()
		e.chromeCmd = nil
	}
	if e.srv != nil {
		e.srv.Close()
		e.srv = nil
	}
	for _, d := range e.tempDirs {
		os.RemoveAll(d)
	}
	e.tempDirs = nil
}

// ---- 构建 ----

// goToolchain 找 go 工具链。宿主 PATH 里可能没有 go（本机就是这样），
// 所以退到 runtime.GOROOT()/bin/go —— 那是编译出这个测试的同一个工具链。
func goToolchain() (string, error) {
	if p, err := exec.LookPath("go"); err == nil {
		return p, nil
	}
	if root := runtime.GOROOT(); root != "" {
		p := filepath.Join(root, "bin", "go")
		if _, err := os.Stat(p); err == nil {
			return p, nil
		}
	}
	return "", fmt.Errorf("PATH 与 GOROOT 里都没有 go")
}

// moduleRoot 从当前包目录往上找 go.mod。测试进程的 cwd 是包目录（cmd/），
// 模块根是它的父目录；用找而不是写死 ".."，免得文件挪窝后悄悄指向别处。
func moduleRoot() (string, error) {
	dir, err := filepath.Abs(".")
	if err != nil {
		return "", err
	}
	for {
		if _, err := os.Stat(filepath.Join(dir, "go.mod")); err == nil {
			return dir, nil
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			return "", fmt.Errorf("从 %s 往上没找到 go.mod", dir)
		}
		dir = parent
	}
}

func buildBinary() (string, string, error) {
	tool, err := goToolchain()
	if err != nil {
		return "", "", fmt.Errorf("找不到 go 工具链: %w", err)
	}
	root, err := moduleRoot()
	if err != nil {
		return "", "", err
	}
	dir, err := os.MkdirTemp("", "cdp-observe-e2e")
	if err != nil {
		return "", "", err
	}
	bin := filepath.Join(dir, "cdp-observe")
	cmd := exec.Command(tool, "build", "-o", bin, "main.go")
	cmd.Dir = root
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		os.RemoveAll(dir)
		return "", "", fmt.Errorf("go build 失败: %w\n%s", err, stderr.String())
	}
	return bin, dir, nil
}

// ---- 私有浏览器 ----

func chromeBinary() (string, error) {
	candidates := []string{}
	if v := os.Getenv("CHROME_BIN"); v != "" {
		candidates = append(candidates, v)
	}
	candidates = append(candidates,
		"google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
		"/usr/bin/google-chrome", "/usr/bin/chromium")
	for _, c := range candidates {
		if p, err := exec.LookPath(c); err == nil {
			return p, nil
		}
	}
	return "", fmt.Errorf("找不到 Chrome/Chromium（试过 CHROME_BIN、google-chrome、chromium 及常见绝对路径）")
}

func freePort() (int, error) {
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return 0, err
	}
	defer l.Close()
	return l.Addr().(*net.TCPAddr).Port, nil
}

// launchChrome 起一个**私有** headless Chrome（端口与 profile 都是这条测试独享）。
// 开关照抄仓库里那个 9222 实例的启动方式（同为 headless=new、同机同版本），
// 只换端口与 profile —— 免得多出「本地能过、CI 不能过」的差异。
func launchChrome(port int, profileDir, logPath string) (*exec.Cmd, error) {
	bin, err := chromeBinary()
	if err != nil {
		return nil, err
	}
	logFile, err := os.Create(logPath)
	if err != nil {
		return nil, err
	}
	defer logFile.Close()

	cmd := exec.Command(bin,
		"--headless=new",
		fmt.Sprintf("--remote-debugging-port=%d", port),
		"--remote-debugging-address=127.0.0.1",
		"--no-first-run",
		"--no-default-browser-check",
		"--disable-gpu",
		"--no-sandbox",
		"--disable-extensions",
		"--disable-crash-reporter",
		"--user-data-dir="+profileDir,
		"about:blank",
	)
	cmd.Stdout = logFile
	cmd.Stderr = logFile
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	if err := cmd.Start(); err != nil {
		return nil, fmt.Errorf("起 Chrome 失败: %w", err)
	}
	// ⚠️ 这里**不**起 Wait goroutine：那会把 pid 提前回收掉，后续按 pid 杀进程组
	// 就成了悬空引用（pid 复用后可能误杀别人的进程组）。收尸统一放在 close()。
	return cmd, nil
}

// waitForPageTarget 等 DevTools 起来**且有一个 page 目标** —— CLI 的
// NewClient 要求至少一个 page 目标，否则它自己就报 no page target found。
func waitForPageTarget(port int, logPath string, timeout time.Duration) error {
	deadline := time.Now().Add(timeout)
	url := fmt.Sprintf("http://127.0.0.1:%d/json/list", port)
	var lastErr error
	for time.Now().Before(deadline) {
		n, err := countPageTargets(url)
		if err == nil && n > 0 {
			return nil
		}
		lastErr = err
		time.Sleep(100 * time.Millisecond)
	}
	return fmt.Errorf("等 %s 上的 page 目标超时（最后一个错误: %v）\nchrome 日志尾部:\n%s",
		url, lastErr, tailFile(logPath, 2000))
}

// 等 Chrome 起来时用它轮询：**带超时**，否则一次卡住的请求会把整个
// waitForPageTarget 的 deadline 挂死（那正是「卡到 CI 超时」那一类）。
var probeClient = &http.Client{Timeout: 3 * time.Second}

func countPageTargets(listURL string) (int, error) {
	resp, err := probeClient.Get(listURL)
	if err != nil {
		return 0, err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return 0, err
	}
	var entries []struct {
		Type string `json:"type"`
		URL  string `json:"url"`
	}
	if err := json.Unmarshal(body, &entries); err != nil {
		return 0, err
	}
	n := 0
	for _, e := range entries {
		if e.Type == "page" && e.URL != "" {
			n++
		}
	}
	return n, nil
}

func tailFile(path string, n int64) string {
	f, err := os.Open(path)
	if err != nil {
		return "(读不到日志)"
	}
	defer f.Close()
	st, err := f.Stat()
	if err != nil {
		return "(读不到日志)"
	}
	off := st.Size() - n
	if off < 0 {
		off = 0
	}
	buf := make([]byte, st.Size()-off)
	if _, err := f.ReadAt(buf, off); err != nil {
		return "(读不到日志)"
	}
	return strings.TrimSpace(string(buf))
}

// ---- 跑二进制 ----

// run 跑一次被测二进制：`cdp --host 127.0.0.1 --port <私有端口> <args...>`。
// flags 放在子命令**前面**是刻意的（PersistentFlags 的标准位置）；子命令后面
// 那条位置由 TestObserveCommandEndToEnd 单独覆盖一次。
func (e *testEnv) run(t *testing.T, args ...string) (string, string, int) {
	t.Helper()
	full := append([]string{"--host", "127.0.0.1", "--port", strconv.Itoa(e.chromePort)}, args...)
	cmd := exec.Command(e.bin, full...)
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr
	err := cmd.Run()
	code := 0
	if err != nil {
		ee, ok := err.(*exec.ExitError)
		if !ok {
			t.Fatalf("跑二进制失败（不是退出码问题）: %v\nstderr: %s", err, stderr.String())
		}
		code = ee.ExitCode()
	}
	return stdout.String(), stderr.String(), code
}

// navigate 用**被测二进制自己的 navi** 把页面开过去 —— 与 py 运行时同一条路
// （它也是先 navi 再 observe）。输出里带回 frame url，正好用来确认页面真到了。
func (e *testEnv) navigate(t *testing.T, url string) {
	t.Helper()
	out, errOut, code := e.run(t, "navi", url)
	if code != 0 {
		t.Fatalf("cdp navi %s 退出码 = %d\nstderr: %s", url, code, errOut)
	}
	if !strings.Contains(out, url) {
		t.Fatalf("navi 的输出里没有目标 URL，页面没真过去:\n%s", out)
	}
}

// observe 跑一次 `cdp observe`。
func (e *testEnv) observe(t *testing.T, extraArgs ...string) (string, string, int) {
	t.Helper()
	return e.run(t, append([]string{"observe"}, extraArgs...)...)
}

// ---- 闸门 ----

// TestObserveCommandEndToEnd 是这条 CLI 最基本的真闸门。
//
// 断言：退出码 0 + stdout 是可解析的 PageModel JSON + 契约字段存在且类型正确
// + 内容真的来自它自己导航过去的那个页面（url/title/具体 selector）。
func TestObserveCommandEndToEnd(t *testing.T) {
	e := env(t)
	fixtureURL := e.fixture + "/base.html"
	e.navigate(t, fixtureURL)

	// 这一次把 --host/--port 放在**子命令后面**：PersistentFlags 位置无关性
	// （`cdp observe --host X` 与 `cdp --host X observe` 都该能用）。
	out, errOut, code := e.run(t, "observe", "--host", "127.0.0.1", "--port", strconv.Itoa(e.chromePort))
	if code != 0 {
		t.Fatalf("退出码 = %d, want 0\nstderr: %s", code, errOut)
	}

	// ① 必须是可解析的 JSON（先验字节，再验结构）—— 任何日志混进 stdout 都会
	// 在这里被杀掉，而不是等下游 py 的 json.loads 在运行时炸掉。
	if !json.Valid([]byte(out)) {
		t.Fatalf("stdout 不是合法 JSON:\n%.400s", out)
	}

	// ② 契约字段存在。用**原始 JSON** 查键：反序列化进 struct 时缺字段是零值，
	// 静默通过 —— 而 py 侧读到的是键本身。
	var raw map[string]json.RawMessage
	if err := json.Unmarshal([]byte(out), &raw); err != nil {
		t.Fatalf("解析成 map 失败: %v", err)
	}
	for _, k := range []string{"url", "title", "page_text", "actions", "fields", "option_groups", "obstructions", "diagnostics"} {
		if _, ok := raw[k]; !ok {
			t.Errorf("输出缺契约字段 %q", k)
		}
	}
	if s := string(raw["actions"]); len(s) == 0 || s[0] != '[' {
		t.Errorf("actions 不是 JSON 数组: %.80s", s)
	}
	// ③ 类型正确：空切片在 Go 里编码成 **null**（不是 []，见报告「自查发现 ①」），
	// 两种都放行 —— 那是 internal 的编码选择，不是 CLI 能决定的；但**不允许**
	// 第三种形状（对象、字符串），那说明字段被换掉了。
	for _, k := range []string{"diagnostics", "obstructions", "fields", "option_groups"} {
		s := string(raw[k])
		if len(s) > 0 && s[0] != '[' && s != "null" {
			t.Errorf("%s 既不是数组也不是 null: %.80s", k, s)
		}
	}
	var m internal.PageModel
	if err := json.Unmarshal([]byte(out), &m); err != nil {
		t.Fatalf("按 internal.PageModel 解不动: %v", err)
	}

	// ④ 内容真的来自我们导航过去的那个页面。
	if m.URL != fixtureURL {
		t.Errorf("url = %q, want %q —— observe 打的不是这条测试导航过去的页面", m.URL, fixtureURL)
	}
	if m.Title != "R3 base" {
		t.Errorf("title = %q, want %q", m.Title, "R3 base")
	}
	if len(m.Actions) == 0 {
		t.Fatal("actions 为空 —— 页面上明明有按钮")
	}
	for i, a := range m.Actions {
		if a.Selector == "" {
			t.Errorf("actions[%d] 缺 selector: %+v", i, a)
		}
		if len(a.FramePath) == 0 {
			t.Errorf("actions[%d] 缺 frame_path —— py 侧无法判断该发给哪一帧: %+v", i, a)
		}
	}
	if !hasSelector(m.Actions, "#schedule-now") {
		t.Errorf("没观察到 #schedule-now；实际 selector: %v", selectors(m.Actions))
	}
	if len(m.Fields) == 0 {
		t.Error("fields 为空 —— base.html 有一个 input#zip")
	}

	// ⑤ 遮挡物走的是 obstructions 通道（不是 diagnostics）：base.html 有
	// onetrust 横幅，且**必须**带 dismiss_selector —— 那正是它存在的意义。
	if len(m.Obstructions) == 0 {
		t.Fatal("obstructions 为空 —— base.html 有个 fixed 的 cookie 横幅")
	}
	var banner *internal.Obstruction
	for i := range m.Obstructions {
		if m.Obstructions[i].Kind == "cookie-banner" {
			banner = &m.Obstructions[i]
		}
	}
	if banner == nil {
		t.Fatalf("没识别出 cookie-banner: %+v", m.Obstructions)
	}
	if banner.Selector == "" || banner.DismissSelector == "" {
		t.Errorf("cookie-banner 缺 selector/dismiss_selector: %+v", *banner)
	}

	// ⑥ diagnostics 的类型正确性：这一页**本来就该是空的**（主帧取到了、帧数
	// 对得上），所以只查形状，不查内容 —— 内容那一档归 internal 的集成测试。
	for i, d := range m.Diagnostics {
		if d.Kind == "" {
			t.Errorf("diagnostics[%d] 缺 kind: %+v", i, d)
		}
	}

	t.Logf("退出码=0；actions=%d fields=%d obstructions=%d diagnostics=%d\n前 3 行输出:\n%s",
		len(m.Actions), len(m.Fields), len(m.Obstructions), len(m.Diagnostics), firstLines(out, 3))
}

// TestObserveCommandMergesChildFrames 钉住 CLI 走的是 **ObserveAll（整页含子帧）**
// 而不是只观测主帧 —— 那条区别是 Task 4 存在的全部理由，也是 py 侧 frame_path 的来处。
//
// 为什么这条**必须**有：fixture base.html 没有 iframe，所以「把 ObserveAll 换成
// Observe("")」这种变异在 base.html 上**测不出来**（实测：变异体下 EndToEnd 照绿）。
// outer_same.html 的外层页**一个可动作元素都没有**，6 个 action 全部来自子帧 ——
// 只观测主帧的话 actions 直接是空的。
func TestObserveCommandMergesChildFrames(t *testing.T) {
	e := env(t)
	fixtureURL := e.fixture + "/outer_same.html"
	e.navigate(t, fixtureURL)

	out, errOut, code := e.observe(t)
	if code != 0 {
		t.Fatalf("退出码 = %d, want 0\nstderr: %s", code, errOut)
	}
	var m internal.PageModel
	if err := json.Unmarshal([]byte(out), &m); err != nil {
		t.Fatalf("按 internal.PageModel 解不动: %v\n%.400s", err, out)
	}

	if m.URL != fixtureURL {
		t.Errorf("url = %q, want %q", m.URL, fixtureURL)
	}
	// 外层页只有 h1 + iframe：这些 action 只可能来自子帧。
	if len(m.Actions) != 6 {
		t.Fatalf("actions = %d, want 6（全部来自子帧；0 = 只观测了主帧）: %v",
			len(m.Actions), selectors(m.Actions))
	}
	if len(m.Fields) != 3 {
		t.Errorf("fields = %d, want 3（子帧里三个输入）", len(m.Fields))
	}
	if m.ShadowRoots != 2 {
		t.Errorf("shadow_roots = %d, want 2（子帧里的两层 shadow）", m.ShadowRoots)
	}

	// frame_path 的形状：["main", <真实 frameID>] —— py 侧靠它选帧。
	childFrameID := ""
	for i, a := range m.Actions {
		if len(a.FramePath) != 2 {
			t.Fatalf("actions[%d].frame_path = %v, want [\"main\" <frameID>]", i, a.FramePath)
		}
		if a.FramePath[0] != "main" {
			t.Errorf("actions[%d].frame_path[0] = %q, want \"main\"", i, a.FramePath[0])
		}
		if id := a.FramePath[1]; id == "" || id == "main" {
			t.Errorf("actions[%d].frame_path[1] = %q —— 这一段必须是可回传给 CDP 的真 frameID", i, id)
		} else if childFrameID == "" {
			childFrameID = id
		} else if childFrameID != id {
			t.Errorf("同一次观测里出现了两个子帧 ID: %q vs %q", childFrameID, id)
		}
	}
	// 动作与字段必须指向**同一个**子帧（这里只有一帧）。
	for i, f := range m.Fields {
		if len(f.FramePath) != 2 || f.FramePath[1] != childFrameID {
			t.Errorf("fields[%d].frame_path = %v, want [\"main\" %q]", i, f.FramePath, childFrameID)
		}
	}
	if !hasSelector(m.Actions, "#submit") {
		t.Errorf("没观察到子帧里的 #submit；实际 selector: %v", selectors(m.Actions))
	}
	// 穿透在跨帧合并之后仍然有效（shadow_depth 是子帧内那两层）。
	pierced := false
	for _, a := range m.Actions {
		if a.ShadowDepth > 0 {
			pierced = true
		}
	}
	if !pierced {
		t.Error("所有 action 的 shadow_depth 都是 0 —— 子帧里的 shadow 穿透丢了")
	}
	if len(m.OptionGroups) != 1 {
		t.Errorf("option_groups = %d, want 1（#shower-opts 那组）", len(m.OptionGroups))
	} else if len(m.OptionGroups[0].Options) != 2 {
		t.Errorf("option_groups[0].options = %v, want 2 个选项", m.OptionGroups[0].Options)
	}

	t.Logf("退出码=0；actions=%d fields=%d shadow_roots=%d 子帧=%s\n前 3 行输出:\n%s",
		len(m.Actions), len(m.Fields), m.ShadowRoots, childFrameID, firstLines(out, 3))
}

// TestObserveCommandExitCodeOnBadFrame 钉住失败约定：拿不到模型 → 非 0 退出码，
// 且 **stdout 上不许有半截 JSON**（py 侧靠退出码判成败，stdout 干净才好解析）。
//
// 这条同时证明二进制真的在传播错误，而不是「打印个错误照样 exit 0」——
// 实测：把 runObserve 的错误吞掉（返回 nil），**只有这条**会红。
func TestObserveCommandExitCodeOnBadFrame(t *testing.T) {
	e := env(t)
	out, errOut, code := e.observe(t, "--frame-id", "NO_SUCH_FRAME_ID")
	if code == 0 {
		t.Fatalf("frame-id 不存在却退出码 0 —— py 侧会把这当成一次成功的观测\nstdout: %s", out)
	}
	if code != 1 {
		t.Errorf("退出码 = %d, want 1（约定见 observe.go 顶部）", code)
	}
	if out != "" {
		t.Errorf("失败路径的 stdout 应当为空，实际: %.200s", out)
	}
	if errOut == "" {
		t.Error("失败路径必须有错误信息，实际 stderr 是空的")
	}
	t.Logf("退出码=%d；stderr 首行: %s", code, firstLines(errOut, 1))
}

// ---- 小工具 ----

func hasSelector(actions []internal.Action, sel string) bool {
	for _, a := range actions {
		if a.Selector == sel {
			return true
		}
	}
	return false
}

func selectors(actions []internal.Action) []string {
	out := make([]string, 0, len(actions))
	for _, a := range actions {
		out = append(out, a.Selector)
	}
	return out
}

func firstLines(s string, n int) string {
	lines := strings.Split(s, "\n")
	if len(lines) > n {
		lines = lines[:n]
	}
	return strings.Join(lines, "\n")
}
