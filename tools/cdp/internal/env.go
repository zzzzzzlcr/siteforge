package internal

import (
	"fmt"
	"strconv"
	"strings"
)

// EnvPort 解析 CDP_PORT 的值 —— **两道门唯一的那一份**（cmd/root.go 与
// internal/mcp/target.go 都调它）。
//
// 为什么必须只有一份：两道门对同一个环境变量必须给出**同一个答复**。原先这段
// （条件、TrimSpace、Atoi，连那句中文报错）在两处逐字复制，两边注释还都写着
// 「与另一道门逐字相同」—— 可两边的测试各自只断言 strings.Contains(err, "CDP_PORT")，
// 于是改掉其中一句话，两道门从此说的不是同一句，而两套测试全绿。
// 那道裁决现在只活在这里；cmd/port_parity_test.go 拿两道门**逐字**对过。
//
// 三条规矩（都是既有行为，13e8361 + ffac82b 定下的，一条没改）：
//
//	空串     = 没设（set=false）—— 环境里那行 `CDP_PORT=` 不该把默认值顶掉
//	能解析   = 收下，前后空白不算数（" 9999 " 就是 9999）
//	解析不了 = **拒绝**（err），不静默回落
//	（全是空白不算「没设」：它过得了「非空」这一关，TrimSpace 之后是空串，
//	  于是和别的坏值一样被拒绝 —— 两道门一贯如此。）
//
// ⚠️ 第三条是这套规矩里最要紧的一条：静默回落 = 「我设了 CDP_PORT，但它没生效」——
// 操作者以为命令指着 9999，命令连的却是 9222，那是**另一个浏览器**，而且全程没有
// 一句错。MCP 那道门后面站着 agent：它看不到 shell 里的上下文，只能靠一句明确的
// 报错（这也是 P6 那条风险的另一面 —— 窗口没了要一句能照着行动的错，端口写错了同理）。
// 运维后果是明摆着的：环境里留着一个坏的 CDP_PORT，每条 cdp 命令都会非零退出，
// 除非显式给 --port（只有 --help / 裸跑那类不跑钩子的用法例外）。
//
// ⚠️ 调用方只在**没有显式给 --port** 时才该调它：显式 flag 一旦给了，环境里那个值
// 根本不会被读 —— 坏的也一样，既不覆盖 flag，也不把命令拦下来。
//
// 返回值是三个而不是两个：`port=0` 分不出「没设」与「用户就写了 0」，
// 而这两件事在调用方那里走不同的路（没设 → 保持默认值；0 → 就听用户的）。
func EnvPort(raw string) (port int, set bool, err error) {
	if raw == "" {
		return 0, false, nil
	}
	n, err := strconv.Atoi(strings.TrimSpace(raw))
	if err != nil {
		// 报的是**原值**（不是 TrimSpace 之后的）：操作者得能在自己那行环境里
		// 一眼认出它，前后有空白恰恰是最常见的成因。
		return 0, false, fmt.Errorf("CDP_PORT=%q 不是端口号（要么改成数字，要么去掉它，要么显式给 --port）", raw)
	}
	return n, true, nil
}
