package main

// 这道门（cdp-mcp）自己的闸门：**同一个时刻只允许一次工具调用在跑**。
//
// 为什么这条只在**这道门**上钉得住：SDK 对每一条 tools/call 都起一个独立的
// goroutine 处理（go-sdk v1.8.0 mcp/server.go:2004 的 `jsonrpc2.Async(ctx)`），
// 而每次调用都自己连一次 CDP（internal/mcp/conn.go 的 Connector.Dial）。
// 客户端 pipeline 两条调用 → 同一个 Bit 窗口上同时挂着两个**独立的 CDP 会话**：
// 鼠标/键盘事件交错，observe 可能拍到动作做了一半的页面 —— 而两个结果都报「成功」
// （performed() 只说「命令下发了」）。
//
// CLI 撞不上这件事（一个进程一条命令），所以这个性质不是继承来的，
// 是这道门自己欠下的账。
//
// 测试走的是**真的那台 SDK 服务**（in-memory transport），不是直接调 handler：
// 要证的正是「SDK 并发派发 + 我们的门」这**两件事合起来**的结果。

import (
	"context"
	"sync"
	"testing"
	"time"

	"cdp/internal"
	"cdp/internal/mcp"

	sdkmcp "github.com/modelcontextprotocol/go-sdk/mcp"
)

// probeDialer 是一个假连接器：它不碰浏览器，只记录「此刻有几个调用正握着浏览器」。
//
// 计数**从 Dial 起、到 release 止** —— 也就是「两个 CDP 会话同时开着」这件事本身，
// 而不是每个会话内部的某一段。只锁 execute（或只锁 Dial）都会让这里读到 2。
type probeDialer struct {
	// window 是一次调用「握着浏览器」的时长（睡眠发生在 probeBrowser.Screenshot
	// 里，也就是 dial 之后的那一段）—— 两次调用要有重叠，窗口就得比调度间隔长。
	window time.Duration

	mu       sync.Mutex
	calls    int // Dial 被调到的总次数（防「第二次根本没进来」这种假绿）
	inFlight int
	peak     int

	// entered 在**第一次** Dial 进来时关闭。测试用它把第二次调用放进第一次的窗口里，
	// 于是「有没有重叠」不再取决于调度运气。
	entered chan struct{}
	once    sync.Once
}

func (p *probeDialer) Dial() (mcp.Browser, func(), error) {
	p.mu.Lock()
	p.calls++
	p.inFlight++
	if p.inFlight > p.peak {
		p.peak = p.inFlight
	}
	p.mu.Unlock()
	p.once.Do(func() { close(p.entered) })

	return probeBrowser{p: p}, func() {
		p.mu.Lock()
		p.inFlight--
		p.mu.Unlock()
	}, nil
}

func (p *probeDialer) snapshot() (calls, peak int) {
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.calls, p.peak
}

// probeBrowser 只实现 screenshot 真正会走到的那一个方法。
// 嵌入接口（值为 nil）让其余十个方法保持「没实现」—— 真被调到会当场 panic，
// 而不是安静地返回零值（安静地返回零值是这台机器上最难查的一种错）。
type probeBrowser struct {
	mcp.Browser
	p *probeDialer
}

func (b probeBrowser) Screenshot() (*internal.Shot, error) {
	time.Sleep(b.p.window)
	return &internal.Shot{}, nil
}

// TestToolCallsOnOneDoorNeverOverlap 是这条任务的核心断言：
// 两条并发到达的 tools/call，**不许**同时在跟同一个浏览器说话。
//
// 两次调用都必须是**真跑完**的（callErrors 与 calls==2 一起看）：一个「第二次调用
// 压根没进来」的测试是假绿 —— 它证明不了锁存在，只证明了没有第二次。
func TestToolCallsOnOneDoorNeverOverlap(t *testing.T) {
	const window = 300 * time.Millisecond

	p := &probeDialer{window: window, entered: make(chan struct{})}
	srv := newServer(p)

	ctx := context.Background()
	serverT, clientT := sdkmcp.NewInMemoryTransports()

	ss, err := srv.Connect(ctx, serverT, nil)
	if err != nil {
		t.Fatalf("服务端接上 in-memory transport 失败: %v", err)
	}
	defer ss.Close()

	cs, err := sdkmcp.NewClient(&sdkmcp.Implementation{Name: "overlap-probe", Version: "1"}, nil).
		Connect(ctx, clientT, nil)
	if err != nil {
		t.Fatalf("客户端连上来失败: %v", err)
	}
	defer cs.Close()

	// 两条调用同时发（start 一起放开）。第二条还要**等第一条真的进了窗口**
	// （见 probeDialer.entered）：于是「有没有重叠」与调度快慢无关，
	// 锁没锁住是唯一的变量。
	start := make(chan struct{})
	var wg sync.WaitGroup

	callErrors := make([]error, 2)
	for i := range callErrors {
		wg.Add(1)
		go func() {
			defer wg.Done()
			<-start

			if i > 0 {
				select {
				case <-p.entered:
				case <-time.After(5 * time.Second):
					t.Errorf("第一条调用一直没连上浏览器")
					return
				}
			}
			_, err := cs.CallTool(ctx, &sdkmcp.CallToolParams{Name: "screenshot"})
			callErrors[i] = err
		}()
	}
	close(start)
	wg.Wait()

	for i, err := range callErrors {
		if err != nil {
			t.Errorf("第 %d 条 screenshot 调用失败了: %v", i+1, err)
		}
	}

	calls, peak := p.snapshot()
	if calls != 2 {
		t.Fatalf("只发生了 %d 次连接（期望 2）—— 这条测试没有真的制造并发，绿了也不算数", calls)
	}
	if peak != 1 {
		t.Errorf("同一个浏览器上**同时**挂着 %d 个调用（期望 1）\n"+
			"—— 两条 tools/call 各连了一条 CDP，鼠标/键盘事件会交错，"+
			"observe 可能拍到动作做了一半的页面，而两边都报成功", peak)
	}
}
