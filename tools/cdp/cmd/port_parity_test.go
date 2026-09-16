package cmd

// CDP_PORT 那道裁决的**跨门一致性**闸门。
//
// 这道裁决原先在两个地方各写了一遍：本包的 root.go（PersistentPreRunE）与
// internal/mcp/target.go（ResolveTarget）—— 从条件、TrimSpace、Atoi 到那句中文报错
// 全是逐字复制的，两边注释里还都写着「与另一道门逐字相同」。可两边的测试**各自只断言
// strings.Contains(err, "CDP_PORT")**：于是改掉其中一句话，两道门就此给出不同的答复，
// 而两套测试全绿 —— 正是这条规矩本身想消灭的那种漂移。
//
// 现在唯一的那一份在 internal.EnvPort（两道门都调它）。这条闸门做三件事：
//
//	① 同一份坏输入，两道门给出**逐字相同**的话（而不是「都提到了 CDP_PORT」）
//	② 那句话就是 internal.EnvPort 那一句 —— 谁把文案重新抄回某一道门里，这里就红
//	③ 真的 cdp-mcp 进程：启动期就拒绝，报错只在 stderr 上，stdout 一个字都没有
//
// 为什么值这么较真：坏掉的 CDP_PORT 被静默忽略 = 「我设了它，但它没生效」——
// 操作者以为命令指着 9999，命令连的是 9222，那是**另一个浏览器**，且全程没有一句错
// （13e8361 定下的规矩：两道门都拒绝，不许回落）。

import (
	"bytes"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"testing"

	"cdp/internal"
	"cdp/internal/mcp"
)

// parityBadPort 是「怎么读都不是端口号」的那个值（两道门都得拒绝它）。
const parityBadPort = "abc"

// TestBothDoorsGiveByteIdenticalVerdictOnBadEnvPort 断言两道门对同一个坏输入
// 给出**同一个字节序列**。
func TestBothDoorsGiveByteIdenticalVerdictOnBadEnvPort(t *testing.T) {
	// 唯一的那一份文案：两道门都得说这一句。
	_, _, canonical := internal.EnvPort(parityBadPort)
	if canonical == nil {
		t.Fatalf("internal.EnvPort(%q) 没报错 —— 「解析不了就拒绝」是 13e8361 定下的规矩"+
			"（静默回落会连到另一个浏览器，且不报错）", parityBadPort)
	}
	want := canonical.Error()

	// ① CLI 那道门：真跑一次根命令（坏 CDP_PORT 必须让它非零退出）。
	resetRootFlags(t)
	t.Cleanup(func() { resetRootFlags(t) })
	t.Setenv("CDP_PORT", parityBadPort)

	cliErr := executeRoot(t, []string{"targets"})
	if cliErr == nil {
		t.Fatalf("CLI 对着 CDP_PORT=%q 正常退出了 —— 它会静默连上默认端口，也就是**另一个浏览器**", parityBadPort)
	}

	// ② MCP 那道门：同一个输入（库这一层；真进程那一层见下面那节）。
	_, mcpErr := mcp.ResolveTarget(mcp.Options{Host: "127.0.0.1", Port: 9222, EnvPort: parityBadPort})
	if mcpErr == nil {
		t.Fatalf("MCP 那道门放过了 CDP_PORT=%q", parityBadPort)
	}

	if got := cliErr.Error(); got != want {
		t.Errorf("CLI 那道门的话与唯一那一份不是逐字相同:\n  CLI  拿到的: %q\n  internal.EnvPort 给的: %q", got, want)
	}
	if got := mcpErr.Error(); got != want {
		t.Errorf("MCP 那道门的话与唯一那一份不是逐字相同:\n  MCP  拿到的: %q\n  internal.EnvPort 给的: %q", got, want)
	}
	if cliErr.Error() != mcpErr.Error() {
		t.Errorf("两道门对同一个输入说出了两句不同的话（那正是这条规矩要消灭的漂移）:\n  CLI: %q\n  MCP: %q",
			cliErr.Error(), mcpErr.Error())
	}

	// ③ 真进程：坏 CDP_PORT 得在**启动期**就被拒绝。
	t.Run("真的 cdp-mcp 进程", func(t *testing.T) {
		bin := mcpBinary(t)
		cmd := exec.Command(bin) // 不给 --port：让它去读环境里的那个坏值
		cmd.Env = append(envWithout(os.Environ(), "CDP_HOST", "CDP_PORT"), "CDP_PORT="+parityBadPort)
		var stdout, stderr bytes.Buffer
		cmd.Stdout, cmd.Stderr = &stdout, &stderr

		if err := cmd.Run(); err == nil {
			t.Fatalf("cdp-mcp 带着 CDP_PORT=%q 正常退出了（stdout=%q）", parityBadPort, stdout.String())
		}
		if stdout.Len() != 0 {
			t.Errorf("启动失败却往 stdout 写了东西（stdio 传输里那是致命的）:\n%q", stdout.String())
		}
		if !strings.Contains(stderr.String(), want) {
			t.Errorf("cdp-mcp 的 stderr 里没有那句裁决 —— 两个门说的不是同一句话:\n  想要: %s\n  实际: %s",
				want, stderr.String())
		}
	})
}

// TestBothDoorsReadAPaddedEnvPortTheSameWay 钉另一头：TrimSpace 也是两边各写了一遍的
// 规矩，所以「收下」的那一侧同样得一致 —— " 9999 " 在两道门上都等于 9999。
//
// 把这条与上面那条一起看才有意义：一道门读成 9999、另一道拒绝，与两道门都拒绝坏值
// 是同一类分歧，坏法一样（操作者拿到的那句话与命令实际连的端口对不上）。
func TestBothDoorsReadAPaddedEnvPortTheSameWay(t *testing.T) {
	const port = 9999
	padded := " " + strconv.Itoa(port) + " "

	resetRootFlags(t)
	t.Cleanup(func() { resetRootFlags(t) })
	t.Setenv("CDP_PORT", padded)

	executeRoot(t, []string{"targets"})
	if got := GetPort(); got != port {
		t.Errorf("CLI：CDP_PORT=%q 应当读成 %d，拿到 %d", padded, port, got)
	}

	got, err := mcp.ResolveTarget(mcp.Options{Host: "127.0.0.1", Port: 9222, EnvPort: padded})
	if err != nil {
		t.Fatalf("MCP：CDP_PORT=%q 被拒了，而 CLI 那道门收下了 —— 两道门不一致: %v", padded, err)
	}
	if got.Port != port {
		t.Errorf("MCP：CDP_PORT=%q 应当读成 %d，拿到 %d", padded, port, got.Port)
	}
}
