package mcp

import (
	"fmt"
	"strconv"
	"strings"

	"cdp/internal"
)

// 这道门连**哪个**浏览器 —— 本文件只干这件事（计划二 Task 2 的那一节）。
//
// 实战上浏览器不是本机 9222，是**一个 Bit 窗口**（带代理与指纹的那套）。
// 链路（两边已经对齐）：
//
//	bit.sh open <worker_ip> <bit_id>  →  ws://<worker_ip>:<port>/devtools/browser/<uuid>
//	        ↓  取 scheme 后、第一个 "/" 之前那段
//	   host=<worker_ip>  port=<port>
//	        ↓
//	   internal.NewClient(host, port)  →  http://host:port/json/version → webSocketDebuggerUrl
//
// ⚠️ 窗口的存活只有几分钟（规格 §4.6）。所以「连不上」要**立刻、明确**地说出来
// （由 Connector.Dial 负责），不许挂着等 —— 调用方拿到明确错误才能重开窗口。
// 这也是 P6 那条风险的落地：窗口没了必须是一种**可失败的前置**，不是一次超时。

// Target 是一个浏览器地址（host + port）。
type Target struct {
	Host string
	Port int
}

// Address 是给人/给日志看的那串，也是「窗口没了」时必须在报错里出现的那串。
func (t Target) Address() string { return fmt.Sprintf("%s:%d", t.Host, t.Port) }

// Options 是这道门的三个输入（已由 cmd/mcp 从 flag/环境变量收齐）。
//
// Host/Port 是**flag 的当前值**（含默认值），HostSet/PortSet 说它俩是不是用户
// 显式给的，EnvHost/EnvPort 是 CDP_HOST/CDP_PORT 的原值（"" = 没设）。
// 把「显式给了没有」带进来是必须的：只看值分不出「--port 9222」与「默认 9222」，
// 而这两者在优先级里不是一回事（C81）。
type Options struct {
	WSURL string

	Host    string
	Port    int
	HostSet bool
	PortSet bool

	EnvHost string
	EnvPort string
}

// ResolveTarget 把人给的东西归一成一个 Target。
//
// 优先级（与 CLI 同一条）：--ws-url > 显式 --host/--port > CDP_HOST/CDP_PORT > 默认值。
//
// ⚠️ --ws-url 与显式 --host/--port **同时给**时直接报错，不挑一个。挑错了不会报错 ——
// 它会一直对着另一个窗口操作，直到有人发现页面不对（「连错了浏览器」正是那类
// 不报错的错，而这个项目刚在 --host/--port 上栽过一次）。
// 环境变量不算「显式给」：它只是兜底，不该把 --ws-url 卡死。
func ResolveTarget(o Options) (Target, error) {
	if o.WSURL != "" {
		if o.HostSet || o.PortSet {
			return Target{}, fmt.Errorf("--ws-url 与 --host/--port 同时给了 —— 这是两个浏览器目标，无法判断该连哪个（"+
				"--ws-url=%q，--host=%q --port=%d）。只给一个："+
				"要么把 bit.sh open 吐出来的那串整个给 --ws-url，要么自己拆成 --host/--port",
				o.WSURL, o.Host, o.Port)
		}
		host, port, err := ParseWSURL(o.WSURL)
		if err != nil {
			return Target{}, err
		}
		return Target{Host: host, Port: port}, nil
	}

	host, port := o.Host, o.Port
	if !o.HostSet && o.EnvHost != "" {
		host = o.EnvHost
	}
	if !o.PortSet {
		// CDP_PORT 怎么读（空 = 没设、前后空白不算数、坏的**拒绝**且说哪句话）
		// 只有一份：internal.EnvPort —— cmd/root.go 那道门调的是同一个函数。
		//
		// ⚠️ 两道门是**同一条规矩**（13e8361）：解析不了就拒绝，不静默回落 ——
		// 静默回落 = 「我设了 CDP_PORT，但它没生效」：操作者以为指着 9999，命令连的
		// 却是 9222，那是**另一个浏览器**，而且全程没有一句错。这道门后面站着 agent，
		// 它看不到 shell 里的上下文，只能靠一句明确的报错。
		//
		// ⚠️ 规矩本身原先在这里又写了一遍（连文案都逐字抄），两边的测试却只各自断言
		// `strings.Contains(err, "CDP_PORT")` —— 改掉一句话，两道门从此答复不一致，
		// 而两套测试全绿。现在文案只有一个来源，且 cmd/port_parity_test.go 拿两道门
		// 逐字对过。
		//
		// ⚠️ 别把这次校验提到 `!o.PortSet` 之外：显式 --port 一旦给了，环境里那个值
		// **根本不会被读** —— 坏的也一样，既不覆盖 flag，也不把命令拦下来。
		// 空串同理：internal.EnvPort 把它当成「没设」（既有行为）。
		//
		// ⚠️ 运维后果：环境里留着一个坏的 CDP_PORT，会让**每一条** cdp 命令都非零
		// 退出（只有 --help / 裸跑那类不跑钩子的用法例外），除非显式给 --port。
		// 生产 py 脚本不受影响 —— 它们每条命令都显式带 --host/--port。
		n, set, err := internal.EnvPort(o.EnvPort)
		if err != nil {
			return Target{}, err
		}
		if set {
			port = n
		}
	}
	return Target{Host: host, Port: port}, nil
}

// ParseWSURL 从 `bit.sh open` 吐出来的那个串里取 host 与 port。
//
// 逐字照抄生产 py 库的 CDPHelper._parse_ws_url
// （/opt/skills/auto-farm-skill/form_executor/common.py）：
//
//	去 ws:// / wss:// 前缀 → 取第一个 "/" 之前那段 → 有":"就拆成 host/port，
//	没有就用默认端口 9222；整个串为空 → 127.0.0.1:9222。
//
// **为什么要照抄而不是「写个更对的」**：同一个串在两边必须解成同一个 host:port。
// 生产 py 与 agent 若各自连到不同的浏览器，两边都不报错 —— 只是「agent 看的页面」
// 与「py 跑的页面」从某一刻起就不是同一个了。
//
// ⚠️ 唯一与 Python 不同之处：Python 在 `host, port = host_port.split(":")` 上遇到
// 多冒号会当场 ValueError（崩），Go 这边**报错**（不猜）。猜一个出来就是静默连错。
func ParseWSURL(raw string) (string, int, error) {
	if raw == "" {
		return "127.0.0.1", 9222, nil
	}

	// 与 Python 的 .replace() 同义（它在整个串上替换，不只是前缀）。
	s := strings.ReplaceAll(raw, "ws://", "")
	s = strings.ReplaceAll(s, "wss://", "")

	hostPort := strings.SplitN(s, "/", 2)[0]
	if hostPort == "" {
		return "", 0, fmt.Errorf("--ws-url=%q 里没有主机名（期望 ws://<host>:<port>/devtools/browser/<uuid>）", raw)
	}

	host, portStr, found := strings.Cut(hostPort, ":")
	if !found {
		// Python 的 else 分支：只给了 host。
		return host, 9222, nil
	}
	if strings.Contains(portStr, ":") {
		// Python 在这里 ValueError（IPv6 字面量）。不猜 —— 猜错就是连错浏览器。
		return "", 0, fmt.Errorf("--ws-url=%q 的 %q 里有多个冒号，解析不了（IPv6 字面量请改用 --host/--port）", raw, hostPort)
	}
	port, err := strconv.Atoi(portStr)
	if err != nil {
		return "", 0, fmt.Errorf("--ws-url=%q 里的端口 %q 不是数字", raw, portStr)
	}
	if host == "" {
		return "", 0, fmt.Errorf("--ws-url=%q 里没有主机名", raw)
	}
	return host, port, nil
}
