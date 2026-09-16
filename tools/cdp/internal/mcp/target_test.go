package mcp

import (
	"strings"
	"testing"
)

// ParseWSURL 是**照抄**生产 py 库的 CDPHelper._parse_ws_url
// （/opt/skills/auto-farm-skill/form_executor/common.py）。
//
// 为什么要抄而不是「写个更对的」：这一段的输入是 `bit.sh open` 吐出来的那串
// （`ws://<worker_ip>:<port>/devtools/browser/<uuid>`），两边**必须**从同一个串
// 得到同一个 host:port —— 不然生产 py 与 agent 会连到两个不同的浏览器，
// 而两边都不报错。前四例就是 Python 那四行的逐字对照。
func TestParseWSURLMatchesTheProductionPython(t *testing.T) {
	cases := []struct {
		name string
		raw  string
		host string
		port int
	}{
		{
			name: "bit.sh open 的实际输出",
			raw:  "ws://192.168.1.222:34567/devtools/browser/6b1a4d5e-1c2b-4f3a-9d8e-0a1b2c3d4e5f",
			host: "192.168.1.222", port: 34567,
		},
		{
			name: "只有 host:port，没有路径",
			raw:  "ws://192.168.1.222:34567",
			host: "192.168.1.222", port: 34567,
		},
		{
			name: "没写端口 → 9222（Python 的默认分支）",
			raw:  "ws://192.168.1.222/devtools/browser/abc",
			host: "192.168.1.222", port: 9222,
		},
		{
			name: "空串 → 127.0.0.1:9222（Python 的默认分支）",
			raw:  "",
			host: "127.0.0.1", port: 9222,
		},
		{
			name: "wss 也要认（Python 两个前缀都 replace 掉）",
			raw:  "wss://bit.example:443/devtools/browser/abc",
			host: "bit.example", port: 443,
		},
		{
			name: "主机名而不是 IP",
			raw:  "ws://localhost:9333/devtools/browser/x",
			host: "localhost", port: 9333,
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			host, port, err := ParseWSURL(tc.raw)
			if err != nil {
				t.Fatalf("ParseWSURL(%q) 报错: %v", tc.raw, err)
			}
			if host != tc.host || port != tc.port {
				t.Errorf("ParseWSURL(%q) = %s:%d，期望 %s:%d", tc.raw, host, port, tc.host, tc.port)
			}
		})
	}
}

// TestParseWSURLFailsLoudlyOnWhatPythonWouldCrashOn：Python 在
// `host, port = host_port.split(":")` 上遇到多冒号会 ValueError。
// Go 这边**不许**猜一个出来（猜错了就是静默连错浏览器），要报错。
func TestParseWSURLFailsLoudlyOnWhatPythonWouldCrashOn(t *testing.T) {
	bad := map[string]string{
		"多冒号（IPv6 字面量）": "ws://[::1]:9222/devtools/browser/x",
		"端口不是数字":       "ws://host:notaport/devtools/browser/x",
		"冒号后面是空的":       "ws://host:/devtools/browser/x",
	}
	for name, raw := range bad {
		t.Run(name, func(t *testing.T) {
			if _, _, err := ParseWSURL(raw); err == nil {
				t.Errorf("ParseWSURL(%q) 没报错 —— 猜一个 host:port 出来就是静默连错浏览器", raw)
			}
		})
	}
}

// TestResolvePrefersWSURLAndRefusesToGuessWhenBothAreGiven：
// 窗口只有几分钟，连错浏览器 = 对着一个别的 agent 的窗口操作，且**不报错**。
// 所以「指了两个目标」不是「随便挑一个」，是当场报错。
func TestResolvePrefersWSURLAndRefusesToGuessWhenBothAreGiven(t *testing.T) {
	got, err := ResolveTarget(Options{WSURL: "ws://10.0.0.9:4000/devtools/browser/x"})
	if err != nil {
		t.Fatalf("只给 --ws-url 就失败: %v", err)
	}
	if got.Host != "10.0.0.9" || got.Port != 4000 {
		t.Errorf("ws-url 没被用上: %+v", got)
	}

	_, err = ResolveTarget(Options{
		WSURL:   "ws://10.0.0.9:4000/devtools/browser/x",
		Host:    "127.0.0.1",
		Port:    9222,
		HostSet: true,
	})
	if err == nil {
		t.Fatal("--ws-url 与 --host 同时给了却放过了 —— 连哪个是猜的")
	}

	_, err = ResolveTarget(Options{
		WSURL:   "ws://10.0.0.9:4000/devtools/browser/x",
		Host:    "127.0.0.1",
		Port:    9222,
		PortSet: true,
	})
	if err == nil {
		t.Fatal("--ws-url 与 --port 同时给了却放过了")
	}

	// 但 CDP_HOST/CDP_PORT 只是**兜底**，不是显式指定 ——
	// 环境里有个 CDP_PORT 不该把 --ws-url 卡死（那会让 agent 连启动都启动不了）。
	if _, err := ResolveTarget(Options{
		WSURL:   "ws://10.0.0.9:4000/devtools/browser/x",
		Host:    "127.0.0.1",
		Port:    9222,
		EnvHost: "192.168.1.5",
		EnvPort: "9222",
	}); err != nil {
		t.Errorf("--ws-url 被环境变量里的 CDP_HOST/CDP_PORT 卡住了: %v", err)
	}
}

// TestResolvePrecedenceFlagOverEnvOverDefault 是 C81（commit ffac82b）在
// **MCP 这道门**上的反向钉子：显式 flag > 环境变量 > 默认值。
//
// 这条在本项目栽过一次：环境变量无条件盖掉显式 --host/--port，而
// 「连错了浏览器」不会报错 —— 它会一直对着另一个窗口操作，直到有人发现页面不对。
func TestResolvePrecedenceFlagOverEnvOverDefault(t *testing.T) {
	cases := []struct {
		name string
		o    Options
		want Target
	}{
		{
			name: "显式 flag 赢过环境变量",
			o: Options{
				Host: "10.0.0.7", Port: 4001, HostSet: true, PortSet: true,
				EnvHost: "10.0.0.8", EnvPort: "4002",
			},
			want: Target{Host: "10.0.0.7", Port: 4001},
		},
		{
			name: "没给 flag 时环境变量生效",
			o:    Options{Host: "127.0.0.1", Port: 9222, EnvHost: "10.0.0.8", EnvPort: "4002"},
			want: Target{Host: "10.0.0.8", Port: 4002},
		},
		{
			name: "两个都没有 → 默认值",
			o:    Options{Host: "127.0.0.1", Port: 9222},
			want: Target{Host: "127.0.0.1", Port: 9222},
		},
		{
			name: "只给了 --host，端口仍然走环境变量",
			o: Options{
				Host: "10.0.0.7", Port: 9222, HostSet: true,
				EnvHost: "10.0.0.8", EnvPort: "4002",
			},
			want: Target{Host: "10.0.0.7", Port: 4002},
		},
		{
			name: "空环境变量等于没设（不要让 CDP_PORT= 把默认值顶掉）",
			o:    Options{Host: "127.0.0.1", Port: 9222, EnvHost: "", EnvPort: ""},
			want: Target{Host: "127.0.0.1", Port: 9222},
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got, err := ResolveTarget(tc.o)
			if err != nil {
				t.Fatalf("ResolveTarget 失败: %v", err)
			}
			if got != tc.want {
				t.Errorf("ResolveTarget = %+v，期望 %+v", got, tc.want)
			}
		})
	}
}

// TestResolveRejectsBadEnvPort：CDP_PORT 写错了必须报错。
//
// ⚠️ 这条注释原先写的是「这一条与 CLI **有意不同**（cmd/root.go 会静默忽略掉解析不了的
// CDP_PORT）」—— 那句现在不成立了：13e8361 把那道门也改成拒绝，两道门对同一个输入
// 报同一句话（文案逐字相同），也都先 TrimSpace。所以这条钉的不再是「本门比 CLI 严」，
// 而是「两道门必须给出同一个答复」—— 不一致正是这个缺陷最容易被搬家的形式。
//
// 为什么两道门都得拒绝：静默忽略 = 「我设了 CDP_PORT，但它没生效」，而这种错**不报错** ——
// 它是 C81 那个缺陷的另一半。MCP 这道门是给 agent 用的，它拿不到 shell 里的
// 上下文，只能靠一句明确的报错。
func TestResolveRejectsBadEnvPort(t *testing.T) {
	_, err := ResolveTarget(Options{Host: "127.0.0.1", Port: 9222, EnvPort: "notaport"})
	if err == nil {
		t.Fatal("CDP_PORT=notaport 被静默忽略了 —— 于是它「设了但没生效」，没人知道")
	}
	if !strings.Contains(err.Error(), "CDP_PORT") {
		t.Errorf("报错里没点名是 CDP_PORT 的问题: %v", err)
	}
}

func TestTargetAddress(t *testing.T) {
	// 窗口没了的时候，报错里必须有这个串 —— agent 靠它判断该重开哪个窗口。
	if got := (Target{Host: "10.0.0.9", Port: 4000}).Address(); got != "10.0.0.9:4000" {
		t.Errorf("Address() = %q", got)
	}
}
