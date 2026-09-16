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
	"slices"
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

	e.srv = newFixtureServer()
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

// ---- fixture 服务 ----

// newFixtureServer 提供两样东西：仓库里的 internal/testdata（Task 3/4/5 用的同一批）
// **外加**一张本测试自己造的**空页**（`/__empty.html`）。
//
// 为什么要那张空页：`null` vs `[]` 这个毛病**只在空列表上现形** ——
// base.html / outer_same.html 里五类列表几乎都有元素，拿它们测等于空转。
// 也不借 `outer.html`（iframe 指向死端口 8892）那张：它依赖「此刻没人监听 8892」，
// 是个会随时间变质的夹具。这张空页是本测试自己的资产，与别人无关。
//
// 服务由 testEnv 持有、TestMain 关闭 —— **不能**挂 t.Cleanup：三条测试共用一个服务，
// 第一条测完就关掉的话，后面两条会连不上。
func newFixtureServer() *httptest.Server {
	mux := http.NewServeMux()
	mux.HandleFunc("/__empty.html", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		io.WriteString(w, `<!doctype html><html><head><meta charset="utf-8"><title>R3 empty</title></head>`+
			`<body><p>这一页没有可动作元素、表单字段、选项组，也没有遮挡物</p></body></html>`)
	})
	// __busy.html 是 C 的复现夹具：一个把主线程堵住十几秒的页。
	// 为什么需要它 —— 见 TestObserveCommandReportsAmbiguousTarget 顶部。
	mux.HandleFunc("/__busy.html", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		io.WriteString(w, fmt.Sprintf(`<!doctype html><html><head><meta charset="utf-8"><title>R3 busy</title></head>`+
			`<body><p>这一页把主线程堵住 %d 毫秒</p><script>var t0=Date.now(); while(Date.now()-t0<%d){}</script></body></html>`,
			busyHoldMS, busyHoldMS))
	})
	mux.Handle("/", http.FileServer(http.Dir(filepath.Join("..", "internal", "testdata"))))
	return httptest.NewServer(mux)
}

// busyHoldMS 是 __busy.html 把主线程堵住的时长（毫秒）。
//
// 为什么必须**显著大于** checkPageActive 那 5 秒求值超时：这条测试要的就是那次检查
// **失败**（真站实测：两个页面目标都返回 Active == nil，于是挑页面时退回了 pages[0]）。
// 页面堵着的时候，Runtime.evaluate 排不到队 → 5 秒超时 → Active 保持 nil。
//
// 10 秒给了「检查开始得晚一点 / 机器慢一点」约 5 秒余量，代价是这条测试本身要跑
// 十几秒 —— 换的是一次**确定性**的复现，不是碰运气等一个偶发的超时。
const busyHoldMS = 10000

// busyProbeDelay 是「导航起好、脚本开始堵」到「去 observe」之间的等待。
//
// 不能太短：/json/new 开出来的标签页一开始是 about:blank，导航 commit + 脚本开始执行
// 需要一点时间；抢在它前面去 observe 的话，检查会落在那个还活着的 about:blank 上
// （实测过：报 visible=true，于是这条测试假绿）。1.2 秒对本地 fixture 绰绰有余。
const busyProbeDelay = 1200 * time.Millisecond

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
		// 过滤条件与 internal.filterPageTargets **同一套**（type=page、URL 非空、
		// 非 devtools://）—— C 的 e2e 要拿这个数去对诊断里那句「共 N 个候选」，
		// 两边稍有出入就会让断言以「数字对不上」的形式红，而不是悄悄放过。
		if e.Type == "page" && e.URL != "" && !strings.HasPrefix(e.URL, "devtools://") {
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

// ---- 页面管理的辅助（C 的 e2e 要自己造第二个页面目标） ----

// devtoolsClient 是给 /json/* 那些**改状态**的调用用的（开页面、关页面）：
// 比探测用的 probeClient 宽松，但仍有超时 —— 关一个主线程堵住的页面要等渲染进程死掉。
var devtoolsClient = &http.Client{Timeout: 10 * time.Second}

func (e *testEnv) devtoolsURL(path string) string {
	return fmt.Sprintf("http://127.0.0.1:%d%s", e.chromePort, path)
}

// createTab 让浏览器**新开一个标签页**，返回它的 target ID。
//
// 为什么必须另开一个、而不是复用测试环境里那个页：C 要复现的是「有两个候选人，
// 但一个报 visible 的都没有」—— 单个页面目标时那条路也走得到，但那不是真站那次
// 的形态（真站是两个目标都 nil）。多开一个才是同构的复现。
//
// 为什么不用 /json/new?url=...：本机 Chrome 150 实测**那个参数会被忽略**
// （开出来仍是 about:blank）。正好 —— 导航必须另起一步，而且那一步不能等 load
// （见 startNav）。
func (e *testEnv) createTab(t *testing.T) string {
	t.Helper()
	req, err := http.NewRequest("PUT", e.devtoolsURL("/json/new"), nil)
	if err != nil {
		t.Fatalf("造 /json/new 请求失败: %v", err)
	}
	resp, err := devtoolsClient.Do(req)
	if err != nil {
		t.Fatalf("开新标签页失败（PUT /json/new）: %v", err)
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		t.Fatalf("读 /json/new 响应失败: %v", err)
	}
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("开新标签页失败: HTTP %d: %.200s", resp.StatusCode, body)
	}
	var info struct {
		ID  string `json:"id"`
		URL string `json:"url"`
	}
	if err := json.Unmarshal(body, &info); err != nil {
		t.Fatalf("解析 /json/new 响应失败: %v\n%.200s", err, body)
	}
	if info.ID == "" {
		t.Fatalf("/json/new 没回 target ID: %.200s", body)
	}
	return info.ID
}

// closeTab 关掉一个标签页。**失败不 Fatal**：它在 t.Cleanup 里跑，那时测试已经
// 结束（可能已经以别的原因失败了），再往上抛一个错误只会把真正的死因盖掉。
func (e *testEnv) closeTab(t *testing.T, id string) {
	t.Helper()
	resp, err := devtoolsClient.Get(e.devtoolsURL("/json/close/" + id))
	if err != nil {
		t.Logf("关标签页 %s 失败: %v", id, err)
		return
	}
	resp.Body.Close()
}

// waitTabGone 轮询到那个标签页真的从目标列表里消失为止。
//
// 为什么要等：/json/close 返回之后浏览器内部还要拆页面。紧接着去 observe 的话，
// 关掉的页可能还在候选列表里 —— 那正是这条测试的负例阶段最怕的假失败
// （以为已经「有页报 visible 了」，其实只是那个堵住的页还没消失）。
func (e *testEnv) waitTabGone(t *testing.T, id string, timeout time.Duration) {
	t.Helper()
	deadline := time.Now().Add(timeout)
	for {
		body, err := e.targetListJSON()
		if err == nil && !strings.Contains(body, id) {
			return
		}
		if time.Now().After(deadline) {
			t.Fatalf("%s 内标签页 %s 还没从目标列表里消失（最后错误: %v）", timeout, id, err)
		}
		time.Sleep(100 * time.Millisecond)
	}
}

func (e *testEnv) targetListJSON() (string, error) {
	resp, err := devtoolsClient.Get(e.devtoolsURL("/json/list"))
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(resp.Body)
	return string(body), err
}

// startNav 起一个**不等它结束**的 navi 进程，把当前标签页导航到 url。
//
// 为什么不能等：__busy.html 的脚本会把主线程堵住十几秒，而 load 事件要等脚本跑完
// 才来 —— 等它就是把这条测试变成「等 15 秒，然后页面已经空了」。要观测的恰恰是
// **它堵着的时候**。返回的进程由调用方负责杀（它是真进程）。
func (e *testEnv) startNav(t *testing.T, url string) *exec.Cmd {
	t.Helper()
	full := []string{"--host", "127.0.0.1", "--port", strconv.Itoa(e.chromePort), "navi", url}
	cmd := exec.Command(e.bin, full...)
	var log bytes.Buffer
	cmd.Stdout, cmd.Stderr = &log, &log
	if err := cmd.Start(); err != nil {
		t.Fatalf("起 navi 进程失败: %v", err)
	}
	t.Cleanup(func() {
		if cmd.Process != nil {
			_ = cmd.Process.Kill()
			_, _ = cmd.Process.Wait()
		}
		if t.Failed() {
			// 只在失败时把 navi 的输出贴出来：它此刻是「页面到底导航过去没有」的
			// 唯一旁证，而这条测试的失败多半就出在那一步。
			t.Logf("navi 输出: %s", log.String())
		}
	})
	return cmd
}

// decodeModel 把 `cdp observe` 的 stdout 解成 PageModel（解不动就 Fatal ——
// 输出是契约，py 侧解析的就是它）。
func decodeModel(t *testing.T, out string) *internal.PageModel {
	t.Helper()
	var m internal.PageModel
	if err := json.Unmarshal([]byte(out), &m); err != nil {
		t.Fatalf("stdout 按 internal.PageModel 解不动: %v\n%.400s", err, out)
	}
	return &m
}

// countDiagKind 数模型里某一种诊断的条数。
func countDiagKind(m *internal.PageModel, kind string) int {
	n := 0
	for _, d := range m.Diagnostics {
		if d.Kind == kind {
			n++
		}
	}
	return n
}

// waitForVisiblePage 轮询到「浏览器里真有页报 visible」为止（退出条件就是
// `cdp targets` 自己那套可见性检查，不是另一个尺子）。
//
// 负例阶段的前置条件：关掉那个堵住的页之后，必须**先确认**又有页真报 visible 了，
// 否则随后 observe 里出现的 target-ambiguous 说不清是「没恢复」还是「实现错了」。
func (e *testEnv) waitForVisiblePage(t *testing.T, timeout time.Duration) {
	t.Helper()
	deadline := time.Now().Add(timeout)
	var last string
	for {
		out, errOut, code := e.run(t, "targets")
		if code == 0 {
			last = out
			var pages []internal.PageTarget
			if err := json.Unmarshal([]byte(out), &pages); err == nil {
				for _, p := range pages {
					if p.Active != nil && *p.Active {
						return
					}
				}
			}
		} else {
			last = errOut
		}
		if time.Now().After(deadline) {
			t.Fatalf("%s 内没有任何页面目标报 visible —— 负例阶段的前提不成立（targets 输出: %.400s）", timeout, last)
		}
		time.Sleep(200 * time.Millisecond)
	}
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
	for _, k := range []string{"url", "title", "page_text", "actions", "fields", "option_groups", "obstructions", "honeypots", "diagnostics"} {
		if _, ok := raw[k]; !ok {
			t.Errorf("输出缺契约字段 %q", k)
		}
	}
	if s := string(raw["actions"]); len(s) == 0 || s[0] != '[' {
		t.Errorf("actions 不是 JSON 数组: %.80s", s)
	}
	// ③ 类型正确：六个列表**一律**是 JSON 数组。空列表必须是 `[]`，
	// **不许**是 `null`（Task 6 修复轮 1 收紧的：py 侧 `for d in model["diagnostics"]`
	// 撞上 null 就 TypeError，而这是运行阶段唯一的接口）。
	for _, k := range []string{"actions", "fields", "option_groups", "obstructions", "honeypots", "diagnostics"} {
		s := string(raw[k])
		if len(s) == 0 || s[0] != '[' {
			t.Errorf("%s 不是 JSON 数组（空列表必须是 []，不是 null）: %.80s", k, s)
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

	// 单帧模式（--frame-id）走的是**另一条路** —— `Observe` 而不是 `ObserveAll`。
	// 空列表归一化与 frame_path 契约两条都要在这条路上覆盖，所以拿刚取到的子帧 ID 再观测一次。
	//
	// frame_path 契约（规格 §4.3，修复轮 2 定）：单帧 → `[frameID]`，主帧 → `["main"]`。
	// 修之前这里写死的是 `['main']`（observeJS 的默认值，Go 侧没覆盖）——
	// 子帧的元素被标成主帧，py 照它选帧就会**静默把点击发到主帧**。
	// 主帧那条腿在 TestObserveSingleFrameMainFrameFramePath（CLI 上不可达，只能在这一层验）。
	outSingle, errOutSingle, codeSingle := e.observe(t, "--frame-id", childFrameID)
	if codeSingle != 0 {
		t.Fatalf("单帧 observe 退出码 = %d\nstderr: %s", codeSingle, errOutSingle)
	}
	var rawSingle map[string]json.RawMessage
	if err := json.Unmarshal([]byte(outSingle), &rawSingle); err != nil {
		t.Fatalf("单帧输出不是合法 JSON: %v\n%.300s", err, outSingle)
	}
	for _, k := range []string{"actions", "fields", "option_groups", "obstructions", "honeypots", "diagnostics"} {
		if s := string(rawSingle[k]); len(s) == 0 || s[0] != '[' {
			t.Errorf("单帧输出 %s 不是 JSON 数组（空列表必须是 []）: %.80s", k, s)
		}
	}
	var single internal.PageModel
	if err := json.Unmarshal([]byte(outSingle), &single); err != nil {
		t.Fatalf("单帧输出按 internal.PageModel 解不动: %v", err)
	}
	if single.URL != e.fixture+"/inner.html" {
		t.Errorf("单帧 url = %q, want …/inner.html —— --frame-id 指的不是那一帧", single.URL)
	}
	if len(single.Actions) != 6 || len(single.Fields) != 3 {
		t.Errorf("单帧 actions/fields = %d/%d, want 6/3（与整页模式里那一帧的内容一致）",
			len(single.Actions), len(single.Fields))
	}
	if len(single.Diagnostics) != 0 {
		t.Errorf("单帧 diagnostics 应为空（这一帧取得到），实际 %d 条", len(single.Diagnostics))
	}
	// frame_path：[<子帧ID>]，**不是** ["main"]（后者是修之前 JS 写死的错值）。
	wantSinglePath := []string{childFrameID}
	for i, a := range single.Actions {
		if !slices.Equal(a.FramePath, wantSinglePath) {
			t.Errorf("单帧 actions[%d] (%s) 的 frame_path = %q, want %q —— "+
				"单帧只报「这条动作属于哪一帧」，要完整祖先链用整页模式",
				i, a.Selector, a.FramePath, wantSinglePath)
		}
	}
	for i, f := range single.Fields {
		if !slices.Equal(f.FramePath, wantSinglePath) {
			t.Errorf("单帧 fields[%d] (%s) 的 frame_path = %q, want %q", i, f.Selector, f.FramePath, wantSinglePath)
		}
	}

	t.Logf("退出码=0；actions=%d fields=%d shadow_roots=%d 子帧=%s\n单帧：actions=%d fields=%d url=%s\n前 3 行输出:\n%s",
		len(m.Actions), len(m.Fields), m.ShadowRoots, childFrameID,
		len(single.Actions), len(single.Fields), single.URL, firstLines(out, 3))
}

// TestObserveSingleFrameMainFrameFramePath 补 `Observe` frame_path 契约的**另一条腿**：
// frameID 为空（主帧）→ `frame_path == ["main"]`（规格 §4.3）。
//
// ⚠️ 这条**走不了二进制**：CLI 把 `--frame-id ""` 当成「没传」→ 整页模式（见 observe.go
// 顶部注释），所以「单帧观测主帧」在 CLI 上根本不可达 —— 只能在 Go API 这一层验。
// 子帧那条腿（那条**能**走二进制）在 TestObserveCommandMergesChildFrames 里。
//
// 这条也是唯一能证明「主帧不会被盖成空路径/子帧 ID」的地方：单帧归一化用的是同一个
// framePathFor，主帧走的是它的另一个分支。
func TestObserveSingleFrameMainFrameFramePath(t *testing.T) {
	e := env(t)
	fixtureURL := e.fixture + "/base.html"
	e.navigate(t, fixtureURL) // 用二进制导航，确保打的是这一页

	c, err := internal.NewClient("127.0.0.1", e.chromePort)
	if err != nil {
		t.Fatalf("连私有浏览器失败: %v", err)
	}
	defer c.Disconnect()

	m, err := c.Observe("") // 空 frameID = 主帧
	if err != nil {
		t.Fatalf("Observe(\"\") 失败: %v", err)
	}
	if m.URL != fixtureURL {
		t.Errorf("url = %q, want %q", m.URL, fixtureURL)
	}
	if len(m.Actions) == 0 || len(m.Fields) == 0 {
		t.Fatalf("主帧上 actions/fields = %d/%d —— 断言会空转通过", len(m.Actions), len(m.Fields))
	}
	want := []string{"main"}
	for i, a := range m.Actions {
		if !slices.Equal(a.FramePath, want) {
			t.Errorf("主帧 actions[%d] (%s) 的 frame_path = %q, want %q", i, a.Selector, a.FramePath, want)
		}
	}
	for i, f := range m.Fields {
		if !slices.Equal(f.FramePath, want) {
			t.Errorf("主帧 fields[%d] (%s) 的 frame_path = %q, want %q", i, f.Selector, f.FramePath, want)
		}
	}
	t.Logf("Observe(\"\")：actions=%d fields=%d，frame_path 全为 [\"main\"]", len(m.Actions), len(m.Fields))
}

// TestObserveCommandEmptyListsAreArrays 是「空列表必须是 `[]`、不许是 `null`」的守门测试
// （Task 6 修复轮 1：控制器裁定**修实现**并**收紧这条测试**）。
//
// 为什么单开一条、为什么用一张自造的空页：缺陷**只在空列表上现形**。
// 这一页五类列表**全空**，一条断言同时钉住 ObserveAll 的合并路径 ——
// 那里 `merged := &PageModel{}` 起手，某类一个元素都没并进来时 `append` 不改变 nil，
// 于是编成 `null`（实测 base.html 的 option_groups 正是这么变 null 的）。
func TestObserveCommandEmptyListsAreArrays(t *testing.T) {
	e := env(t)
	fixtureURL := e.fixture + "/__empty.html"
	e.navigate(t, fixtureURL)

	out, errOut, code := e.observe(t)
	if code != 0 {
		t.Fatalf("退出码 = %d, want 0\nstderr: %s", code, errOut)
	}
	if !json.Valid([]byte(out)) {
		t.Fatalf("stdout 不是合法 JSON:\n%.400s", out)
	}

	var raw map[string]json.RawMessage
	if err := json.Unmarshal([]byte(out), &raw); err != nil {
		t.Fatalf("解析成 map 失败: %v", err)
	}
	// 字面量断言：**必须是 `[]`**。退回 `null` 这条立刻红 —— 这正是它该守的东西。
	// honeypots 在这一格里的地位与其他五个一样：空页上它必须编成 `[]`，
	// 而不是 nil → `null`（这一条就是 normalizeNilLists 对新字段的守卫）。
	for _, k := range []string{"actions", "fields", "option_groups", "obstructions", "honeypots", "diagnostics"} {
		if got := string(raw[k]); got != "[]" {
			t.Errorf("%s = %s，want []（空列表不许是 null —— py 侧 for x in model[%q] 会 TypeError）", k, got, k)
		}
	}

	// 再从 Go 侧看一遍：解出来必须是「非 nil 的空切片」，而不是 nil。
	var m internal.PageModel
	if err := json.Unmarshal([]byte(out), &m); err != nil {
		t.Fatalf("按 internal.PageModel 解不动: %v", err)
	}
	if m.Actions == nil || m.Fields == nil || m.OptionGroups == nil || m.Obstructions == nil ||
		m.Honeypots == nil || m.Diagnostics == nil {
		t.Errorf("解出来有 nil 切片（= 编码时是 null）: %s %s %s %s %s %s",
			describeSlice("actions", m.Actions == nil, len(m.Actions)),
			describeSlice("fields", m.Fields == nil, len(m.Fields)),
			describeSlice("option_groups", m.OptionGroups == nil, len(m.OptionGroups)),
			describeSlice("obstructions", m.Obstructions == nil, len(m.Obstructions)),
			describeSlice("honeypots", m.Honeypots == nil, len(m.Honeypots)),
			describeSlice("diagnostics", m.Diagnostics == nil, len(m.Diagnostics)))
	}
	if len(m.Actions)+len(m.Fields)+len(m.OptionGroups)+len(m.Obstructions)+len(m.Honeypots)+len(m.Diagnostics) != 0 {
		t.Errorf("空页上不该有内容: %d/%d/%d/%d/%d/%d",
			len(m.Actions), len(m.Fields), len(m.OptionGroups), len(m.Obstructions), len(m.Honeypots), len(m.Diagnostics))
	}
	t.Logf("退出码=0；六个列表都是 []：%s", firstLines(out, 8))
}

// TestObserveCommandHumanFormat 钉住 `--json=false` —— 它必须**真的**给人话
// （Task 6 修复轮 1：一个看起来能切格式、实际不切的 flag 正是本项目反复栽的那类）。
func TestObserveCommandHumanFormat(t *testing.T) {
	e := env(t)
	fixtureURL := e.fixture + "/base.html"
	e.navigate(t, fixtureURL)

	out, errOut, code := e.observe(t, "--json=false")
	if code != 0 {
		t.Fatalf("--json=false 退出码 = %d, want 0\nstderr: %s", code, errOut)
	}
	if json.Valid([]byte(out)) {
		t.Fatalf("--json=false 还是吐了 JSON（flag 静默不做事）:\n%.300s", out)
	}
	if strings.HasPrefix(strings.TrimSpace(out), "{") {
		t.Errorf("--json=false 的输出以 { 开头 —— 像是半截 JSON:\n%.300s", out)
	}
	// 摘要该有的东西：页面身份、动作正文+评级、遮挡物与**可点的**关闭选择器。
	for _, want := range []string{
		fixtureURL,
		"R3 base",
		"可动作元素",
		"Schedule Now",                 // 动作正文
		"high",                         // 稳定性评级
		"cookie-banner",                // 遮挡物
		"#onetrust-accept-btn-handler", // dismiss_selector：人下一步该点的东西
		"#schedule-now",                // 选择器
	} {
		if !strings.Contains(out, want) {
			t.Errorf("人话输出里没有 %q：\n%s", want, out)
		}
	}
	t.Logf("退出码=0；--json=false 输出：\n%s", out)
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

// describeSlice 把「nil 还是空」印清楚：`%v` 对两者都印 `[]`，
// 而调试时**正是这个差别在咬人**（本条断言的由来就是它）。
func describeSlice(name string, isNil bool, n int) string {
	if isNil {
		return name + "=nil(会编成 null)"
	}
	return fmt.Sprintf("%s=[](len=%d)", name, n)
}

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

// ─────────────────────────── B：--expect-url（主动那道闸） ───────────────────────────

// TestObserveCommandExpectURL 钉住 `--expect-url` 的两个方向：
//
//	(i)  子串对得上 → 退出码 0，且**输出与不传这个 flag 时一模一样**（默认行为不许被动）
//	(ii) 子串对不上 → 非 0 退出，消息里同时点名「期望的子串」与「实际的 URL」
//
// 为什么值得一条真闸门：这是拿错页时**唯一会主动说话**的一条路（C 那条是被动的 ——
// 要调用方自己去读 diagnostics）。py 侧在 worker 里能用的只有退出码，所以「对不上
// 就必须非 0」这件事，只能在这条真二进制上验。
func TestObserveCommandExpectURL(t *testing.T) {
	e := env(t)
	fixtureURL := e.fixture + "/base.html"
	e.navigate(t, fixtureURL)

	// ── (i) 对得上：退出码 0，模型照常 ──
	out, errOut, code := e.observe(t, "--expect-url", "base.html")
	if code != 0 {
		t.Fatalf("--expect-url 与页面相符却退出码 %d，want 0\nstderr: %s", code, errOut)
	}
	m := decodeModel(t, out)
	if m.URL != fixtureURL {
		t.Fatalf("url = %q, want %q —— 这次的模型不是这条测试导航过去的页", m.URL, fixtureURL)
	}
	if len(m.Actions) == 0 {
		t.Fatal("actions 为空 —— --expect-url 不该改变观测本身")
	}

	// ── (ii) 对不上：非 0 退出 + 两个串都在消息里 ──
	const wrong = "inner.html"
	outBad, errOutBad, codeBad := e.observe(t, "--expect-url", wrong)
	if codeBad == 0 {
		t.Fatalf("期望子串对不上却退出码 0 —— 调用方会拿这份错页的模型接着跑。stdout: %.200s", outBad)
	}
	if codeBad != 1 {
		t.Errorf("退出码 = %d, want 1（约定见 cmd/observe.go 顶部）", codeBad)
	}
	if outBad != "" {
		// stdout 的约定是「要么一份完整模型，要么什么都没有」：半份/错页的模型
		// 比没有更坏 —— 它长得跟成功一样。
		t.Errorf("失败路径的 stdout 应当为空（模型已丢弃），实际: %.200s", outBad)
	}
	// %q 打出来会带引号，所以这里用 Contains 而不是相等。
	for _, want := range []string{wrong, fixtureURL} {
		if !strings.Contains(errOutBad, want) {
			t.Errorf("错误消息里没有 %q —— 人看不出「要的是哪一页、实际是哪一页」:\n%s", want, errOutBad)
		}
	}
	t.Logf("退出码=0（对得上）；退出码=%d（对不上）stderr 首行: %s", codeBad, firstLines(errOutBad, 1))
}

// TestObserveCommandExpectURLUsesModelURLWithFrameID 钉住 `--frame-id` 时比的是
// **模型顶层的 url**，不是主帧的：单帧观测出来的 url 是**那一帧**的地址，
// 而「我要的是不是这一页」问的正是那个地址。
//
// 为什么单开一条：这条腿的语义和整页那条不同（同一个 flag，两个来源），
// 而写错成「永远比主帧」在下游会是静默的 —— 子帧页上永远判「对不上」，
// 调用方只会看到莫名其妙的失败。
func TestObserveCommandExpectURLUsesModelURLWithFrameID(t *testing.T) {
	e := env(t)
	e.navigate(t, e.fixture+"/outer_same.html")

	// 先拿这一次观测，从里面取出子帧的 frameID（frame_path 第二段就是它）
	out, errOut, code := e.observe(t)
	if code != 0 {
		t.Fatalf("整页 observe 退出码 = %d\nstderr: %s", code, errOut)
	}
	m := decodeModel(t, out)
	childID := ""
	for _, a := range m.Actions {
		if len(a.FramePath) > 1 {
			childID = a.FramePath[1]
			break
		}
	}
	if childID == "" {
		t.Fatalf("动作里没有子帧 frame_path —— 这条测试的前提（有个子帧可观测）不成立: %+v", m.Actions)
	}
	innerURL := e.fixture + "/inner.html"

	// ── 子帧的 url 含 inner.html → 对得上 ──
	_, errOut2, code2 := e.observe(t, "--frame-id", childID, "--expect-url", "inner.html")
	if code2 != 0 {
		t.Fatalf("--frame-id 单帧观测子帧、期望 inner.html 却退出码 %d\nstderr: %s", code2, errOut2)
	}

	// ── 期望主帧的地址 → 对不上（因为模型顶层的 url 是子帧的），且消息里报的是子帧的 URL ──
	outBad, errOutBad, codeBad := e.observe(t, "--frame-id", childID, "--expect-url", "base.html")
	if codeBad == 0 {
		t.Fatalf("子帧观测报的是 %s，期望 base.html 却退出码 0 —— 比错对象了", innerURL)
	}
	if outBad != "" {
		t.Errorf("失败路径的 stdout 应当为空，实际: %.200s", outBad)
	}
	for _, want := range []string{"base.html", innerURL} {
		if !strings.Contains(errOutBad, want) {
			t.Errorf("错误消息里没有 %q —— 单帧那条腿报的应当是子帧的 url:\n%s", want, errOutBad)
		}
	}
	t.Logf("子帧 %s：期望 inner.html → 0；期望 base.html → %d", childID, codeBad)
}

// ───────────────── C：target-ambiguous 诊断（被动） + 真浏览器复现 ─────────────────

// TestObserveCommandReportsAmbiguousTarget 在本机真浏览器上**复现**真站那次测量，
// 钉住 C 的两个方向：
//
//	正例：没有页报 visible（挑页面只能退回第一个）→ 模型里必须有一条 target-ambiguous，
//	      detail 说清「几个候选、挑中的是哪个 target」
//	负例：有页真报 visible 时不说话 —— 常驻的诊断等于没有诊断
//
// ── 怎么复现「没有页报 visible」 ──
// 真站那次的形态是：两份 page 目标都返回 Active == nil（checkPageActive 求值失败/超时），
// 于是挑页面时退回 pages[0] —— 而那是另一个页（夹具页）。要造出同一个状态，就得让
// **可见性检查失败**：__busy.html 把主线程堵住十几秒，Runtime.evaluate 排不到队 →
// 5 秒超时 → Active 保持 nil（页面此时**仍然是可以被观测的**，这正是缺陷的可怕之处：
// 模型完全合法、退出码 0，只是说的是另一个页）。
//
// ⚠️ 这条测试为什么非得这么绕：一条「报告说它是猜的」的诊断，只有在**真的猜了**的时候
// 才该出现 —— 用假造的输入喂进去只能证明函数会拼字符串，证明不了整条链路上真会发生。
// 所以这里真起浏览器、真堵页面、真跑二进制（退出码与 stdout 都当真）。
func TestObserveCommandReportsAmbiguousTarget(t *testing.T) {
	e := env(t)

	// 第二个页面目标：堵住主线程的那个。它同时是「退回」时会挑中的那一个
	// （/json/list 的顺序近似 MRU，新开的标签页在最前 → 退回挑 pages[0] 就是它）。
	busyID := e.createTab(t)
	t.Cleanup(func() { e.closeTab(t, busyID) })
	e.startNav(t, e.fixture+"/__busy.html")
	time.Sleep(busyProbeDelay)

	// 候选数得自己数一份：诊断里那句「共 N 个候选」是**这次**的 N，
	// 而这条测试跑在共享的私有浏览器上（别的测试可能留着自己的页面目标）。
	wantCandidates, err := countPageTargets(e.devtoolsURL("/json/list"))
	if err != nil {
		t.Fatalf("数页面目标失败: %v", err)
	}
	if wantCandidates < 2 {
		t.Fatalf("只有 %d 个页面目标 —— 「多目标里一个报 visible 的都没有」这个复现形态不成立", wantCandidates)
	}

	// ── 正例 ──
	out, errOut, code := e.observe(t)
	if code != 0 {
		t.Fatalf("退出码 = %d, want 0 —— 这一态下观测本身是成功的（这正是缺陷的样子）\nstderr: %s", code, errOut)
	}
	m := decodeModel(t, out)
	if want := e.fixture + "/__busy.html"; m.URL != want {
		t.Fatalf("模型 url = %q, want %q —— 退回挑中的不是这个页，后面的断言没有意义（挑页面顺序变了？）", m.URL, want)
	}
	diag := (*internal.Diagnostic)(nil)
	for i := range m.Diagnostics {
		if m.Diagnostics[i].Kind == internal.DiagKindTargetAmbiguous {
			diag = &m.Diagnostics[i]
		}
	}
	if diag == nil {
		t.Fatalf("没有任何页面报 visible（挑页面只能退回第一个），模型却没有 target-ambiguous 诊断 —— "+
			"agent 会把这份模型当成「就是这个页」照常推理。实际诊断: %+v", m.Diagnostics)
	}
	if n := countDiagKind(m, internal.DiagKindTargetAmbiguous); n != 1 {
		t.Errorf("target-ambiguous = %d 条，want 1", n)
	}
	// detail 要可行动：候选数、挑中的 target（ID + URL）
	for _, want := range []string{busyID, strconv.Itoa(wantCandidates), m.URL} {
		if !strings.Contains(diag.Detail, want) {
			t.Errorf("detail 里没有 %q —— 人没法判断挑错没有:\n%s", want, diag.Detail)
		}
	}
	if len(diag.FramePath) != 1 || diag.FramePath[0] != "main" {
		t.Errorf("frame_path = %q, want [\"main\"]（这是整个 tab 选错了，不是某一帧）", diag.FramePath)
	}
	// 这条诊断走的是 diagnostics 通道，不是 obstructions（混进去的话，
	// 忽略 kind 的消费者会拿 targetID 当选择器去点）
	for _, o := range m.Obstructions {
		if o.Kind == internal.DiagKindTargetAmbiguous {
			t.Errorf("target-ambiguous 混进了 obstructions: %+v", o)
		}
	}

	// ⚠️ 人话输出（--json=false）那一条**故意不在这里**：这个状态是**一次性**的 ——
	// 第一次观测的求值会排队等到主线程放开才返回，等它回来，页面已经不堵了，
	// 第二次观测就恢复成「有页报 visible」（实测：这么写时第二遍拿到的是「诊断 0 条」，
	// 断言红得毫无意义）。人话那一行由 TestRenderHumanSurfacesTargetAmbiguous 用
	// 造好的模型直测（打的就是 renderHuman 本身，不需要真浏览器）。

	// ── 负例：关掉堵住的页 → 又有页真报 visible → 这条诊断必须消失 ──
	e.closeTab(t, busyID)
	e.waitTabGone(t, busyID, 15*time.Second)
	e.waitForVisiblePage(t, 20*time.Second)
	outClean, errOutClean, codeClean := e.observe(t)
	if codeClean != 0 {
		t.Fatalf("恢复后 observe 退出码 = %d\nstderr: %s", codeClean, errOutClean)
	}
	clean := decodeModel(t, outClean)
	if n := countDiagKind(clean, internal.DiagKindTargetAmbiguous); n != 0 {
		t.Errorf("有页面真报了 visible 却仍然报 %s %d 条 —— 常驻的诊断会让消费者学会无视它:\n%+v",
			internal.DiagKindTargetAmbiguous, n, clean.Diagnostics)
	}
	t.Logf("正例：url=%s candidates=%d 诊断=%d；负例（关掉堵住的页后）：诊断=%d",
		m.URL, wantCandidates, len(m.Diagnostics), len(clean.Diagnostics))
}
