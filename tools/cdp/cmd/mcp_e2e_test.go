package cmd

// cmd/mcp（MCP 那道门）的端到端闸门。
//
// ⚠️ 为什么这个文件在 package cmd，而不是在 cmd/mcp 里：
// 它要的夹具 —— 私有 headless Chrome（私有端口 + 私有 profile + 杀进程组）、
// fixture 服务、go 工具链定位 —— 全都在 observe_e2e_test.go 里，而**测试辅助函数
// 没法跨包 import**。抄一份的代价是两份会各自漂移（而它们漂移的那天，
// 恰好就是「测试还在绿、真实行为已经变了」）。
//
// 它测的是**两个门之间**的那件事：MCP 门连的是**哪个**浏览器。
// 生产里那个浏览器不是本机 9222，是一个带代理与指纹的 Bit 窗口，窗口只活几分钟
// （规格 §4.6 / 风险 P6）。这中间任何一步搞错（env 盖掉 flag、ws-url 拆错、
// 连不上却挂着），表现都是「agent 对着另一个窗口操作」或者「agent 卡住」——
// 两者都不报错，所以只能在这儿钉死。

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"
)

// ---- 被测二进制 ----

var (
	mcpBinOnce sync.Once
	mcpBin     string
	mcpBinErr  error
)

// mcpBinary 构建并返回 cdp-mcp（一次，全包共用）。
func mcpBinary(t *testing.T) string {
	t.Helper()
	mcpBinOnce.Do(func() { mcpBin, mcpBinErr = buildMCPBinary() })
	if mcpBinErr != nil {
		t.Fatalf("构建 cdp-mcp 失败: %v", mcpBinErr)
	}
	return mcpBin
}

func buildMCPBinary() (string, error) {
	tool, err := goToolchain()
	if err != nil {
		return "", fmt.Errorf("找不到 go 工具链: %w", err)
	}
	root, err := moduleRoot()
	if err != nil {
		return "", err
	}
	dir, err := os.MkdirTemp("", "cdp-mcp-e2e")
	if err != nil {
		return "", err
	}
	bin := filepath.Join(dir, "cdp-mcp")
	// 入口是 ./cmd/mcp（CLI 那个是根目录的 main.go）—— 同内核两个门，
	// 所以这里构建的**只有入口那几十行**，内核是同一份。
	cmd := exec.Command(tool, "build", "-o", bin, "./cmd/mcp")
	cmd.Dir = root
	var stderr bytes.Buffer
	cmd.Stderr = &stderr
	if err := cmd.Run(); err != nil {
		os.RemoveAll(dir)
		return "", fmt.Errorf("go build ./cmd/mcp 失败: %w\n%s", err, stderr.String())
	}
	return bin, nil
}

// ---- 第二个/第三个私有浏览器 ----

// mcpBrowser 是一个**这条测试独享**的 headless Chrome。
//
// 为什么不用 testEnv 里那个（也不要用共享的 9222）：这条测试要**两个**浏览器
// 同时停在不同页上，然后断言 MCP 门连的确实是它被告知的那一个。
// 借别人的浏览器 = 断言的是「此刻别人把它导航到哪了」。
type mcpBrowser struct {
	port    int
	cmd     *exec.Cmd
	profile string
}

func newMCPBrowser(t *testing.T) *mcpBrowser {
	t.Helper()
	port, err := freePort()
	if err != nil {
		t.Fatalf("找空闲端口失败: %v", err)
	}
	profile, err := os.MkdirTemp("", "cdp-mcp-profile")
	if err != nil {
		t.Fatalf("建 profile 目录失败: %v", err)
	}
	logPath := filepath.Join(profile, "chrome.log")
	cmd, err := launchChrome(port, profile, logPath)
	if err != nil {
		os.RemoveAll(profile)
		t.Fatalf("起私有 Chrome 失败: %v", err)
	}
	b := &mcpBrowser{port: port, cmd: cmd, profile: profile}
	t.Cleanup(b.close)
	if err := waitForPageTarget(port, logPath, 20*time.Second); err != nil {
		t.Fatalf("私有 Chrome（端口 %d）没起来: %v", port, err)
	}
	return b
}

// close 杀**整个进程组**（Chrome 会拉起 zygote/renderer 一串子进程，
// 只杀父进程会留孤儿 —— 本仓库栽过：孤儿窗口 → 内存 95%）。
func (b *mcpBrowser) close() {
	if b.cmd == nil {
		return
	}
	pid := b.cmd.Process.Pid
	_ = syscall.Kill(-pid, syscall.SIGKILL)
	_ = syscall.Kill(pid, syscall.SIGKILL)
	_ = b.cmd.Wait()
	b.cmd = nil
	os.RemoveAll(b.profile)
}

// wsURL 是这个浏览器的 WebSocket 调试地址 —— 形状与 `bit.sh open` 吐出来的
// 一模一样（`ws://host:port/devtools/browser/<uuid>`），所以可以直接拿来喂 --ws-url。
func (b *mcpBrowser) wsURL(t *testing.T) string {
	t.Helper()
	url := fmt.Sprintf("http://127.0.0.1:%d/json/version", b.port)
	resp, err := probeClient.Get(url)
	if err != nil {
		t.Fatalf("取 %s 失败: %v", url, err)
	}
	defer resp.Body.Close()
	var v struct {
		WebSocketDebuggerURL string `json:"webSocketDebuggerUrl"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&v); err != nil {
		t.Fatalf("解析 %s 失败: %v", url, err)
	}
	if v.WebSocketDebuggerURL == "" {
		t.Fatalf("%s 没给 webSocketDebuggerUrl", url)
	}
	return v.WebSocketDebuggerURL
}

// naviWithCLI 用**另一道门**（cdp CLI）把某个私有浏览器导航到 url。
//
// 为什么必须绕开被测的那道门：如果用它自己 `goto`，那么「连错了浏览器」时
// 被导航的正是错的那个 —— 于是 observe 看到的 URL 与预期一致，测试**假绿**。
// 先把两个浏览器各自停到不同的页上（用别的门），再让 MCP 门去 observe，
// 才问得出「你到底连的哪个」。
func naviWithCLI(t *testing.T, cliBin string, port int, url string) {
	t.Helper()
	cmd := exec.Command(cliBin,
		"--host", "127.0.0.1", "--port", strconv.Itoa(port), "navi", url)
	cmd.Env = envWithout(os.Environ(), "CDP_HOST", "CDP_PORT")
	var stdout, stderr bytes.Buffer
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	if err := cmd.Run(); err != nil {
		t.Fatalf("用 CLI 把端口 %d 导航到 %s 失败: %v\nstderr: %s", port, url, err, stderr.String())
	}
	if !strings.Contains(stdout.String(), url) {
		t.Fatalf("CLI 的输出里没有 %s（页面没真过去）:\n%s", url, stdout.String())
	}
}

// ---- 一个跑起来的 MCP 门 + 一条裸的 JSON-RPC 通道 ----

// lockedBuffer 收子进程的 stderr（写它的与读它的是两个 goroutine）。
type lockedBuffer struct {
	mu  sync.Mutex
	buf bytes.Buffer
}

func (b *lockedBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.Write(p)
}

func (b *lockedBuffer) String() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.String()
}

// mcpDoor 是 `cdp-mcp` 的一个进程，用**裸 JSON-RPC** 说话（不借 SDK 的客户端）。
//
// 为什么不借：这条闸门要证明的正是**线上那一份字节**——「stdout 全是合法 JSON-RPC」
// 这件事，用一个帮你解析的客户端去测等于让被测的东西自己给自己作证。
type mcpDoor struct {
	t      *testing.T
	cmd    *exec.Cmd
	stdin  io.WriteCloser
	lines  chan string
	stderr *lockedBuffer

	mu       sync.Mutex
	raw      []string
	badLines []string
	nextID   int
}

// startMCPDoor 起一个 cdp-mcp。extraEnv 会**盖掉**同名环境变量（CDP_PORT 那条钉子要用）。
func startMCPDoor(t *testing.T, bin string, args []string, extraEnv map[string]string) *mcpDoor {
	t.Helper()
	cmd := exec.Command(bin, args...)
	env := os.Environ()
	for k, v := range extraEnv {
		env = append(envWithout(env, k), k+"="+v)
	}
	cmd.Env = env
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}

	stdin, err := cmd.StdinPipe()
	if err != nil {
		t.Fatalf("拿 stdin 管道失败: %v", err)
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		t.Fatalf("拿 stdout 管道失败: %v", err)
	}
	d := &mcpDoor{
		t:      t,
		cmd:    cmd,
		stdin:  stdin,
		lines:  make(chan string, 64),
		stderr: &lockedBuffer{},
	}
	cmd.Stderr = d.stderr

	if err := cmd.Start(); err != nil {
		t.Fatalf("起 cdp-mcp 失败: %v", err)
	}
	go d.pump(stdout)
	t.Cleanup(d.stop)
	return d
}

// pump 把 stdout 的每一行原文收下来，并**当场**判一次合法性。
//
// ⚠️ 这个 goroutine 是「stdout 必须干净」那条契约的唯一见证者：stdout 是 stdio
// 传输的协议通道，混进去一行 Go 日志（或一句 usage、一个 fmt.Println）就会毁掉
// 整条流 —— 而那种坏的形态很隐蔽：客户端报的是 JSON 解析错，不是「你的工具打了日志」。
func (d *mcpDoor) pump(stdout io.Reader) {
	defer close(d.lines)
	sc := bufio.NewScanner(stdout)
	// 截图那条路会把 base64 的 PNG 整个放进一行，默认 64KB 不够。
	sc.Buffer(make([]byte, 0, 64*1024), 64<<20)
	for sc.Scan() {
		line := sc.Text()
		d.mu.Lock()
		d.raw = append(d.raw, line)
		if !json.Valid([]byte(line)) {
			d.badLines = append(d.badLines, line)
		}
		d.mu.Unlock()
		d.lines <- line
	}
}

func (d *mcpDoor) stop() {
	if d.cmd == nil || d.cmd.Process == nil {
		return
	}
	_ = d.stdin.Close()
	// 先礼后兵：给它一点点时间自己收工（stdin 关了，Run 会返回）。
	done := make(chan struct{})
	go func() { _ = d.cmd.Wait(); close(done) }()
	select {
	case <-done:
	case <-time.After(3 * time.Second):
		pid := d.cmd.Process.Pid
		_ = syscall.Kill(-pid, syscall.SIGKILL)
		_ = syscall.Kill(pid, syscall.SIGKILL)
		<-done
	}
	d.cmd = nil
}

func (d *mcpDoor) writeLine(v any) {
	d.t.Helper()
	b, err := json.Marshal(v)
	if err != nil {
		d.t.Fatalf("编码请求失败: %v", err)
	}
	if _, err := d.stdin.Write(append(b, '\n')); err != nil {
		d.t.Fatalf("往 cdp-mcp 的 stdin 写失败: %v\nstderr: %s", err, d.stderr.String())
	}
}

// rpc 发一条请求，读到**对应 id** 的响应为止。
//
// 每一步都有超时：一个挂住的工具调用在 agent 那边呈现为「模型没反应」，
// 而窗口没了本该是「一句明确的错」—— 所以这里宁可红，也不许挂。
func (d *mcpDoor) rpc(method string, params any, timeout time.Duration) map[string]any {
	d.t.Helper()
	d.mu.Lock()
	d.nextID++
	id := d.nextID
	d.mu.Unlock()

	req := map[string]any{"jsonrpc": "2.0", "id": id, "method": method}
	if params != nil {
		req["params"] = params
	}
	d.writeLine(req)

	deadline := time.After(timeout)
	for {
		select {
		case line, ok := <-d.lines:
			if !ok {
				d.t.Fatalf("cdp-mcp 的 stdout 关了（等 %s 的响应时）\nstderr: %s", method, d.stderr.String())
			}
			if bad := d.badJSON(); len(bad) > 0 {
				d.t.Fatalf("stdout 上出现了不是 JSON 的一行 —— stdio 传输里那是致命的:\n%q", bad[0])
			}
			var msg map[string]any
			if err := json.Unmarshal([]byte(line), &msg); err != nil {
				d.t.Fatalf("解析响应失败: %v\n原文: %s", err, line)
			}
			if got, ok := msg["id"].(float64); ok && int(got) == id {
				return msg
			}
			// 不是这条请求的响应（通知、别的响应）—— 继续读。
		case <-deadline:
			d.t.Fatalf("等 %s（id=%d）的响应超时 %v —— 门挂住了\nstderr: %s",
				method, id, timeout, d.stderr.String())
		}
	}
}

// call 发一条请求并返回 result；协议错误（JSON-RPC error）直接判失败。
func (d *mcpDoor) call(method string, params any) json.RawMessage {
	d.t.Helper()
	msg := d.rpc(method, params, 60*time.Second)
	if e, ok := msg["error"]; ok {
		d.t.Fatalf("%s 回了协议错误: %v", method, e)
	}
	raw, err := json.Marshal(msg["result"])
	if err != nil {
		d.t.Fatalf("result 没法重编码: %v", err)
	}
	return raw
}

func (d *mcpDoor) notify(method string, params any) {
	d.t.Helper()
	req := map[string]any{"jsonrpc": "2.0", "method": method}
	if params != nil {
		req["params"] = params
	}
	d.writeLine(req)
}

// handshake 走一次真正的 MCP 初始化。
//
// ⚠️ 不能省 —— 直接发 tools/list 会得到
// `method "tools/list" is invalid during session initialization`
// （计划二 Task 2 Step 4 那条 `echo '{"jsonrpc":...}' | ./cdp-mcp` 就是这个下场：
// 输出**很干净**，但没有工具列表。干净是它对的那一半，另一半得靠握手）。
func (d *mcpDoor) handshake() {
	d.t.Helper()
	d.rpc("initialize", map[string]any{
		"protocolVersion": "2025-06-18",
		"capabilities":    map[string]any{},
		"clientInfo":      map[string]any{"name": "cdp-mcp-e2e", "version": "1"},
	}, 30*time.Second)
	d.notify("notifications/initialized", nil)
}

// callTool 调一次工具，返回 (content 文本, isError)。
func (d *mcpDoor) callTool(name string, args map[string]any) (string, bool) {
	d.t.Helper()
	if args == nil {
		args = map[string]any{}
	}
	raw := d.call("tools/call", map[string]any{"name": name, "arguments": args})

	var res struct {
		IsError bool `json:"isError"`
		Content []struct {
			Type string `json:"type"`
			Text string `json:"text"`
		} `json:"content"`
	}
	if err := json.Unmarshal(raw, &res); err != nil {
		d.t.Fatalf("tools/call 的结果解不开: %v\n原文: %s", err, raw)
	}
	if len(res.Content) == 0 {
		return "", res.IsError
	}
	return res.Content[0].Text, res.IsError
}

// observedURL 调 observe 并取出模型的 url。
func (d *mcpDoor) observedURL() string {
	d.t.Helper()
	text, isErr := d.callTool("observe", nil)
	if isErr {
		d.t.Fatalf("observe 失败: %s", text)
	}
	var m struct {
		URL string `json:"url"`
	}
	if err := json.Unmarshal([]byte(text), &m); err != nil {
		d.t.Fatalf("observe 的返回不是一份页面模型: %v\n%.400s", err, text)
	}
	if m.URL == "" {
		d.t.Fatalf("observe 的模型里没有 url:\n%.400s", text)
	}
	return m.URL
}

func (d *mcpDoor) badJSON() []string {
	d.mu.Lock()
	defer d.mu.Unlock()
	return append([]string(nil), d.badLines...)
}

func (d *mcpDoor) stdoutLines() []string {
	d.mu.Lock()
	defer d.mu.Unlock()
	return append([]string(nil), d.raw...)
}

func envWithout(env []string, keys ...string) []string {
	drop := map[string]bool{}
	for _, k := range keys {
		drop[k] = true
	}
	out := make([]string, 0, len(env))
	for _, kv := range env {
		name, _, _ := strings.Cut(kv, "=")
		if drop[name] {
			continue
		}
		out = append(out, kv)
	}
	return out
}

// ---- ① 它连的到底是哪一个浏览器（反向钉子） ----

// TestMCPDoorTalksToTheBrowserItWasTold 是这条任务里最要紧的一条。
//
// 起**两个**浏览器，各自停在不同页上，然后告诉 MCP 门去连**非默认的**那一个，
// 断言它真的连的是那一个 —— 而不是「参数看起来传对了」。
//
// 为什么非要这么钉（计划二 Task 2 那一节）：本项目刚在 --host/--port 上栽过一次
// （C81 / commit ffac82b：环境变量静默盖掉了显式 flag），而「连错了浏览器」
// 正是那类**不报错的错** —— 它会一直对着另一个窗口操作，每一步都成功，
// 直到有人发现页面根本不对。所以两种给法各钉一遍：
//
//	① 显式 --port 指向 B，环境里 CDP_PORT 指向 A   → 必须连 B
//	② --ws-url 指向 A，环境里 CDP_PORT 指向 B       → 必须连 A（bit.sh open 那条路）
func TestMCPDoorTalksToTheBrowserItWasTold(t *testing.T) {
	e := env(t)
	bin := mcpBinary(t)

	// 两个私有浏览器，各自停在自己的页上。
	a := newMCPBrowser(t)
	b := newMCPBrowser(t)
	pageA := e.fixture + "/base.html"
	pageB := e.fixture + "/form.html"
	naviWithCLI(t, e.bin, a.port, pageA)
	naviWithCLI(t, e.bin, b.port, pageB)

	// 前提：两个浏览器**真的**停在不同页上。不先确认这件事，
	// 下面的断言即便通过也说明不了什么（两边一样的话，「连对了」与「连错了」同形）。
	t.Run("前提：两个浏览器停在不同页上", func(t *testing.T) {
		if got := urlOfCLIObserve(t, e.bin, a.port); !strings.Contains(got, "/base.html") {
			t.Fatalf("浏览器 A（端口 %d）应该在 %s，实际 %q", a.port, pageA, got)
		}
		if got := urlOfCLIObserve(t, e.bin, b.port); !strings.Contains(got, "/form.html") {
			t.Fatalf("浏览器 B（端口 %d）应该在 %s，实际 %q", b.port, pageB, got)
		}
	})

	t.Run("① 显式 --port 赢过环境里的 CDP_PORT", func(t *testing.T) {
		door := startMCPDoor(t, bin,
			[]string{"--host", "127.0.0.1", "--port", strconv.Itoa(b.port)},
			map[string]string{"CDP_PORT": strconv.Itoa(a.port), "CDP_HOST": "127.0.0.1"},
		)
		door.handshake()

		got := door.observedURL()
		if !strings.Contains(got, "/form.html") {
			t.Errorf("告诉它连 B（--port %d），它看到的却是 %q\n"+
				"—— 环境里的 CDP_PORT=%d 盖掉了显式 flag（C81 那一类：连错了浏览器**不报错**）",
				b.port, got, a.port)
		}
	})

	t.Run("② --ws-url（bit.sh open 那一串）赢过环境变量", func(t *testing.T) {
		door := startMCPDoor(t, bin,
			[]string{"--ws-url", a.wsURL(t)},
			map[string]string{"CDP_PORT": strconv.Itoa(b.port), "CDP_HOST": "127.0.0.1"},
		)
		door.handshake()

		got := door.observedURL()
		if !strings.Contains(got, "/base.html") {
			t.Errorf("--ws-url 指向 A，它看到的却是 %q（期望 /base.html）", got)
		}
	})

	t.Run("③ 两个目标同时给：当场报错，且 stdout 上一个字都没有", func(t *testing.T) {
		cmd := exec.Command(bin,
			"--ws-url", a.wsURL(t),
			"--host", "127.0.0.1", "--port", strconv.Itoa(b.port))
		cmd.Env = envWithout(os.Environ(), "CDP_HOST", "CDP_PORT")
		var stdout, stderr bytes.Buffer
		cmd.Stdout, cmd.Stderr = &stdout, &stderr
		err := cmd.Run()

		if err == nil {
			t.Fatal("--ws-url 与 --host/--port 同时给了却正常退出 —— 它挑了哪个是猜的")
		}
		if stdout.Len() != 0 {
			t.Errorf("启动失败却往 stdout 写了东西（那是协议通道）:\n%q", stdout.String())
		}
		if !strings.Contains(stderr.String(), "ws-url") {
			t.Errorf("stderr 没说是 ws-url 的冲突:\n%s", stderr.String())
		}
	})
}

// urlOfCLIObserve 用 CLI 那道门看一眼某个浏览器现在停在哪一页。
func urlOfCLIObserve(t *testing.T, cliBin string, port int) string {
	t.Helper()
	cmd := exec.Command(cliBin, "--host", "127.0.0.1", "--port", strconv.Itoa(port), "observe")
	cmd.Env = envWithout(os.Environ(), "CDP_HOST", "CDP_PORT")
	var stdout, stderr bytes.Buffer
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	if err := cmd.Run(); err != nil {
		t.Fatalf("用 CLI observe 端口 %d 失败: %v\nstderr: %s", port, err, stderr.String())
	}
	var m struct {
		URL string `json:"url"`
	}
	if err := json.Unmarshal(stdout.Bytes(), &m); err != nil {
		t.Fatalf("CLI observe 的输出不是页面模型: %v\n%.300s", err, stdout.String())
	}
	return m.URL
}

// ---- ② stdout 必须干净 ----

// TestMCPDoorKeepsStdoutClean 钉住 stdio 传输那条铁律：
// **stdout 上只能有 JSON-RPC**，一行日志就能毁掉整条流。
//
// 而它坏起来很隐蔽：客户端报的是「JSON 解析失败」，不是「你的工具打了日志」——
// 排查方向从第一步就是错的。所以这里不看日志内容，直接看**每一行的字节**。
func TestMCPDoorKeepsStdoutClean(t *testing.T) {
	bin := mcpBinary(t)
	// 指一个没人监听的端口：这条测试只走握手与工具清单，本来不连浏览器；
	// 万一哪天真去连了，它连的也必须是我自己的死端口，而不是共享的 9222。
	dead := deadPort(t)
	door := startMCPDoor(t, bin, []string{"--port", strconv.Itoa(dead)}, nil)
	door.handshake()

	raw := door.call("tools/list", nil)
	var listed struct {
		Tools []struct {
			Name        string          `json:"name"`
			Description string          `json:"description"`
			InputSchema json.RawMessage `json:"inputSchema"`
		} `json:"tools"`
	}
	if err := json.Unmarshal(raw, &listed); err != nil {
		t.Fatalf("tools/list 解不开: %v\n%.300s", err, raw)
	}

	names := make([]string, 0, len(listed.Tools))
	for _, tool := range listed.Tools {
		names = append(names, tool.Name)
	}
	for _, want := range []string{"observe", "diff", "screenshot", "click", "form", "scroll", "goto"} {
		if !contains(names, want) {
			t.Errorf("工具清单里没有 %q（拿到的是 %v）", want, names)
		}
	}

	// observe 的描述必须写明它只给感知（D11）—— 描述是模型唯一的说明书。
	for _, tool := range listed.Tools {
		if tool.Name != "observe" {
			continue
		}
		if !strings.Contains(tool.Description, "不给判断") {
			t.Errorf("observe 的描述没有「不给判断」: %s", tool.Description)
		}
		// 语义参数一个都不许有（D11）——连**描述文字**里都不许出现。
		schema := string(tool.InputSchema)
		for _, banned := range []string{"intent", "importance", "primary", "semantic"} {
			if strings.Contains(strings.ToLower(schema), banned) {
				t.Errorf("observe 的 schema 里出现了 %q —— observe 给感知、不给判断", banned)
			}
		}
	}

	// 收尾再看一遍**所有**收到过的行（rpc 只检查到它读到的那一条为止）。
	if bad := door.badJSON(); len(bad) > 0 {
		t.Errorf("stdout 上有 %d 行不是 JSON:\n%q", len(bad), bad)
	}
	lines := door.stdoutLines()
	if len(lines) < 2 {
		t.Fatalf("只收到 %d 行 stdout（握手 + tools/list 至少两条）", len(lines))
	}
	for i, line := range lines {
		var msg map[string]any
		if err := json.Unmarshal([]byte(line), &msg); err != nil {
			t.Errorf("第 %d 行不是 JSON: %q", i+1, line)
			continue
		}
		if msg["jsonrpc"] != "2.0" {
			t.Errorf("第 %d 行不是 JSON-RPC 消息: %q", i+1, line)
		}
	}
}

// ---- ③ 窗口没了：立刻、明确地报错，不许挂着 ----

// TestMCPDoorFailsLoudlyWhenTheWindowIsGone 钉住 P6 那条风险。
//
// Bit 窗口只活几分钟，而一次 explore 可能跑很久。窗口没了的时候，agent 需要的是
// **一句能照着行动的错**（连不上 <host:port>，去重开），不是一次超时 ——
// 超时在 agent 那边呈现为「模型卡住了」，它既不会去重开窗口，也不会告诉人。
func TestMCPDoorFailsLoudlyWhenTheWindowIsGone(t *testing.T) {
	bin := mcpBinary(t)
	dead := deadPort(t) // 刚释放的端口：连它必然被拒
	addr := fmt.Sprintf("127.0.0.1:%d", dead)

	door := startMCPDoor(t, bin,
		[]string{"--host", "127.0.0.1", "--port", strconv.Itoa(dead)},
		nil)
	door.handshake()

	start := time.Now()
	text, isErr := door.callTool("observe", nil)
	elapsed := time.Since(start)

	if !isErr {
		t.Fatalf("浏览器不在了，observe 却报了成功（返回 %q）—— 一次没做成的观测被读成做成了", text)
	}
	if !strings.Contains(text, addr) {
		t.Errorf("报错里没点名 %s —— agent 只有拿到这个串才能判断该重开哪个窗口:\n%s", addr, text)
	}
	if elapsed > 20*time.Second {
		t.Errorf("报错花了 %v —— 窗口没了必须立刻说，不许挂着", elapsed)
	}

	// 报错之后门还得活着：窗口重开之后要能接着用（P6：从断点继续，而不是整轮重来）。
	if _, isErr := door.callTool("observe", nil); !isErr {
		t.Error("第二次调用居然成功了（没人监听那个端口）")
	}
}

// ---- 工具级错误 vs 协议级错误 ----

// TestMCPDoorReportsToolErrorsInBand：工具失败要**在 result 里**报（isError=true），
// 不能升成协议错误。
//
// MCP 的规矩，也正是 agent 需要的：协议错误在模型那边看不见，它只会觉得「没反应」；
// 而 isError 里的那段话模型读得到 —— 于是它能自己改参数，或者告诉人「窗口没了」。
func TestMCPDoorReportsToolErrorsInBand(t *testing.T) {
	bin := mcpBinary(t)
	dead := deadPort(t)
	door := startMCPDoor(t, bin, []string{"--port", strconv.Itoa(dead)}, nil)
	door.handshake()

	// 未知工具名走的是**协议**错误（SDK 那一层就挡了），这里确认它不是「静默成功」。
	msg := door.rpc("tools/call", map[string]any{"name": "obesrve", "arguments": map[string]any{}}, 30*time.Second)
	if _, ok := msg["error"]; !ok {
		t.Errorf("拼错的工具名没报错：%v", msg)
	}

	// 参数校验失败走**工具级**错误（模型看得见才能自己改）。
	text, isErr := door.callTool("click", map[string]any{})
	if !isErr {
		t.Fatalf("click 少了必填的 selector 却成功了: %q", text)
	}
	if !strings.Contains(text, "selector") {
		t.Errorf("报错里没说是哪个参数的问题（模型改不动）: %s", text)
	}
}

func contains(list []string, want string) bool {
	for _, s := range list {
		if s == want {
			return true
		}
	}
	return false
}

// deadPort 返回一个**刚刚**没人监听的端口（起一个 listener 拿到系统分配的端口再关掉）。
// 比写死一个数字可靠：写死的那个迟早被别的进程占上，于是测试变成「有时连得上」。
func deadPort(t *testing.T) int {
	t.Helper()
	port, err := freePort()
	if err != nil {
		t.Fatalf("找一个空闲端口失败: %v", err)
	}
	return port
}
