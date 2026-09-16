# cdp CLI

Chrome DevTools Protocol CLI 工具，用于与 Chrome 浏览器交互，支持页面快照、执行 JavaScript、模拟人类手势操作、页面管理，以及**页面模型观测（observe）与差分（diff）**。

## 安装

```bash
go build -o cdp main.go
```

## 前置条件

Chrome 需启用远程调试：

```bash
chrome --remote-debugging-port=9222
```

## 使用方法

### 全局选项

```bash
--host string   Chrome 地址 (默认 "127.0.0.1"，支持 CDP_HOST 环境变量)
--port int      Chrome 端口 (默认 9222，支持 CDP_PORT 环境变量)
```

### snapshot - 获取页面快照

获取当前活跃页面所有 frame 的 DOM 树结构（JSON 格式），包含 frameId、title、URL、DOM 层级。

```bash
cdp snapshot
cdp snapshot --host 192.168.1.100 --port 9222
```

### eval - 执行 JavaScript

在指定 frame 中执行 JavaScript 表达式。

```bash
cdp eval '"hello"'
cdp eval --frame-id <frameId> 'document.title'
cdp eval --file script.js
```

Flags:
- `--frame-id` - 目标 frame（空则为主 frame）
- `--file` - 从文件读取 JS

### click - 模拟人类点击

模拟人类手势点击指定元素（CSS 选择器定位）。

```bash
cdp click '#btn'
cdp click --selector '#btn'
cdp click '#btn' --frame-id <frameId>
cdp click '#btn' --track
cdp click '#ambiguous' --strict      # 歧义/禁用 → 当场失败（agent 那道门默认开）
```

每一次点击都会在 **stderr** 上留一行：命中几个元素、点的是第几个、它禁没禁用
（stdout 保持是纯 JSON）。回执里的机器可读字段是 `match_count` / `match_index` /
`target` / `target_disabled`。

**落点判据（抬起交给谁）**：点击的鼠标序列是 `moved → pressed → released`，
而**按下与抬起之间落点换了人**时，这一次 `released` **不发** —— 现代组件库的下拉在
mousedown 就把菜单展开并铺一层盖住视口的 backdrop，紧接着的抬起落在那一层上、
把刚打开的菜单又关掉（净效果「点了没反应」）。判据是**浏览器自己的命中测试**
（`DOM.getNodeForLocation`，与真实输入同一条代码路、且穿 shadow DOM），
窗口是「按下之前那一刻 → 按下之后」（**不含** MouseMoved 那一段：悬停引起的
重渲染发生在按下之前）。扣下要**正向取证**：只有「原目标还在文档里」且
「新落点与它不是同一个东西（标签+属性不同）」才扣 —— 节点**重建**（markup
逐字节相同）不是「有人盖上来」，照常发出去。回执多三个字段：

| 字段 | 含义 |
|---|---|
| `release_withheld` | 这一次抬起**没发**（页面收到的鼠标事件比从前少一个）。⚠️ 不是失败：真站上那一半恰恰是「把菜单打开」的那一半 |
| `covered_by` | 扣下时：覆盖上来的那个元素（`<div class="MuiBackdrop-root">`） |
| `landing_blind` + `landing_note` | 判据**没能跑**（跨站子帧的 `<iframe>` 上命中栈不下钻，判据在那儿**恒不触发**；或那个坐标取不到节点）以及它说的话。判不了时按老行为走（抬起照发），但**必须能听见** |

⚠️ `form` 那条路（`--select` / `--check` / `--value`）内部的点击**同样**会扣下抬起，
所以它也在 stderr 上印同一批话（判据没话可说时一个字都不多）——
两条路都不静默，只在一条路上说等于没说。

**默认路径的行为没有变**（仍然是文档序第一个匹配、仍然不因为禁用而硬失败）——
57 个生产脚本靠这条。变的是它**不再静默**：选择器命中 5 个、或者点到的是个
禁用控件，原来是完全看不出来的（实测：前者静默点到 Back 把漏斗走回去，后者
exit 0 + 一对像样的坐标而页面纹丝不动）。

Flags:
- `[selector]` - CSS 选择器（位置参数，或 `--selector`）
- `--frame-id` - 目标 frame
- `--track` - 可视化操作轨迹
- `--strict` - 歧义选择器（命中多个）或禁用目标**当场失败**，且拒绝发生在动作
  **之前**。默认 **false**（生产脚本的默认路径）；MCP 门默认走严格那一版

### scroll - 模拟人类滚动

模拟人类手势滚动到指定元素。

```bash
cdp scroll '#content'
cdp scroll --selector '#content'
cdp scroll '#section' --frame-id <frameId>
cdp scroll '#content' --track
```

Flags:
- `[selector]` - CSS 选择器（位置参数，或 `--selector`）
- `--frame-id` - 目标 frame
- `--track` - 可视化操作轨迹

### navi - 页面导航

导航到指定 URL，等待页面加载完成后返回 frame 树。

```bash
cdp navi https://example.com
cdp navi https://example.com --frame-id <frameId>
```

Flags:
- `[url]` - 目标 URL（必填位置参数）
- `--frame-id` - 目标 frame（默认主 frame）

### observe - 观察页面（结构化页面模型）

观察当前页面（默认整页，跨源 iframe 自动逐帧取再合并），输出 PageModel JSON：
可动作元素（选择器候选与稳定性、区域、遮挡、bbox）、表单字段、选项组、遮挡物、诊断。
它是 agent 的主视角，也是**运行阶段唯一的回退接口** —— 选择器全挂时 py 靠它重新看清页面。

```bash
cdp observe
cdp observe --json=false
cdp observe --frame-id <frameId>
```

Flags:
- `--json` - 默认 true 输出 JSON（py 侧只该用这一种）；`--json=false` 输出人话摘要（给人看，别解析）
- `--frame-id` - 只观察指定帧（默认整页含子帧）。传的必须是 **CDP frameID**，不是 `frame_path` 里那个给人读的 `"main"`

⚠️ `diagnostics` 是**观测者自己的问题**（某帧没取到 / 帧枚举可能退化），`obstructions` 是
**页面上的遮挡物**（cookie 横幅，带 `dismiss_selector`）—— 两者语义不同，别混用。

**每条动作/字段还带三样「回读」**（2026-09-17 真站验收补的，三个都是 additive）：

| 字段 | 是什么 | ⚠️ 怎么读 |
|---|---|---|
| `value` | 这个元素**现在装着的值**（input / textarea / select） | 三态：`null` = **不是值控件**（按钮/链接），`""` = 是值控件但**现在是空的**。两个都不能省 |
| `value_truncated` | 上面的值被上限截过（上限 80 字符） | 截了就说 —— 不然消费侧把半个值当成全部 |
| `selected` | 这个控件**现在选着没有** | 三态：`true` / `false` / **`null` = 看不出**（自定义控件没暴露状态）。**不许把 null 读成没选** |
| `aria_label` | 无障碍名（元素的 `aria-label`） | 真站上 Back 与几个图标选项的 `text` 全是空串，只有它分得开 |

为什么是这三样：agent 卡住的那些次，人看得见而它看不见的正是「我答的是哪个」
「值填进去没有」「这个没文字的按钮叫什么」。判据不看 class（`Mui-selected` 那类不算证据，
与 `disabled` 同一条裁定）。

### diff - 差分：刚才那一下有没有推进

把「动作前的快照」与「现在的页面」比一遍，回答一个问题：刚才那一下有没有推进。
判据三条：URL 变了（SPA 的 pushState 也算）/ 可见正文变了 / 身份上真有元素出现或消失
—— 都不长在选择器上，所以框架生成的 hash class 一刷新不会把重渲染谎报成「有进展」。

```bash
cdp observe > before.json
# …做动作…
cdp diff --before before.json
cdp diff --before before.json --json=false
```

Flags:
- `--before` - 动作前的 PageModel JSON 文件（**必填**，先用 `cdp observe` 输出一份）
- `--json` - 默认 true 输出 JSON（py 侧只该用这一种）；`--json=false` 输出人话摘要
- `--frame-id` - 只观察「动作后」那一帧（⚠️ 拿**整页**快照配它，其它帧的元素会整批报成
  disappeared → 假的 `actionable=true`；非调试别传）

退出码：0 = 算出了差分（★ `actionable=false` **也是 0** —— 那是正常答案，请读 JSON 里的
`actionable`，别拿退出码当判据）；1 = 算不出来（快照读不到 / 不是合法 PageModel / 观测失败）。

⚠️ 能力边界：它判**导航与组成变化**，**不判填写与选择** —— 它的判据里没有字段值这一项，
所以「值填进去了没有 / 勾上了没有」这类步骤别拿 diff 的 `actionable` 当判据
（页面组成没变就是 false，那不是「没成功」）。要判它们：`observe` 现在回读
`value` / `selected`（见上表），或者用 `cdp form` 自己的退出码（选项不存在会报错）。

### form - 表单填充

模拟人类手势填写表单元素，支持文本输入、复选框切换和下拉选择。

```bash
cdp form '#input' --value 'hello'
cdp form '#checkbox' --check true
cdp form '#checkbox' --check false
cdp form '#select' --select 'option-value'
cdp form '#input' --value 'hello' --track
```

Flags:
- `[selector]` - CSS 选择器（位置参数，或 `--selector`）
- `--value` - 文本输入值
- `--check` - 复选框状态（"true" / "false"）
- `--select` - 下拉选项值或文本
- `--frame-id` - 目标 frame
- `--track` - 可视化操作轨迹

三种操作模式互斥，每次只能指定 `--value`、`--check`、`--select` 其中之一。

### targets - 列出页面

列出所有打开的页面，标记当前活跃页面。

```bash
cdp targets
```

输出每个页面的 id、title、url 和 active 状态。

### active - 激活页面

切换到指定页面，后续命令均操作该页面。

```bash
cdp active <target-id>
cdp active --target <target-id>
```

Flags:
- `[target]` - 目标页面 ID（位置参数，或 `--target`）

### close - 关闭页面

通过 target ID 关闭指定页面，支持批量关闭和关闭所有非活跃页面。不能关闭当前活跃页面。

```bash
cdp close <target-id>
cdp close <target-id1> <target-id2>
cdp close --all
```

Flags:
- `[target-id...]` - 要关闭的页面 ID（可指定多个）
- `--all` - 关闭所有非活跃页面

关闭后输出剩余页面列表（JSON 格式）。

## cdp-mcp —— MCP 门（同一个内核的第二个出口）

```bash
go build -o cdp-mcp ./cmd/mcp
```

给 agent 用的 **stdio MCP 服务**。它**不是**包一层壳去调上面的 CLI：两条门直接调
同一份 `internal/`（穿透解析 / 拟人手势 / 表单 / 帧 / 截图都是同一套），所以行为一致。

工具（规格 §4.2）：`observe` / `diff` / `screenshot` / `click` / `form` / `scroll` / `goto`。

⚠️ **两处「默认」是刻意不同的**（2026-09-17 实测裁定，规格 §4.1）：
`click` 在这道门上是**严格**的 —— 选择器命中多个元素、或目标被禁用
（`disabled` / `aria-disabled`）都**报错并拒绝点击**，报错里列出候选与位置。
CLI 那边默认宽松（57 个生产脚本的行为不能动）。agent 是必须被逼着说准的调用方：
它的歧义选择器实测会静默点到 Back（漏斗倒退），禁用目标实测会静默空点
（exit 0 + 像样的坐标，页面纹丝不动）—— 两种都长得像成功。

**连哪个浏览器** —— 生产里不是本机 9222，是一个带代理与指纹的 Bit 窗口：

```bash
cdp-mcp --ws-url "$(bit.sh open <worker_ip> <bit_id> | tail -1)"   # 原样吃 bit.sh 吐出来的那串
cdp-mcp --host <worker_ip> --port <port>                            # 或者自己拆好
```

- `CDP_HOST` / `CDP_PORT` 是**兜底**：显式 flag 永远赢（C81 那条，CLI 上栽过一次）
- `--ws-url` 与显式 `--host/--port` **同时给 = 当场报错**（连哪个是猜的，猜错不报错）
- 拆 `--ws-url` 的逻辑与生产 py 库的 `CDPHelper._parse_ws_url` **逐字对齐**
- 窗口没了会**立刻**报错并点名 `<host:port>`，不挂着 —— 调用方据此重开窗口

⚠️ **stdout 是 JSON-RPC 通道，一个字都不能多说**（日志走 stderr；工具的失败走
JSON-RPC 的 `isError`，不走协议错误）。所以入口用标准库 `flag` 而不是 cobra。

⚠️ 直接发 `tools/list` 会被拒（`method "tools/list" is invalid during session
initialization`）—— MCP 要先 `initialize` + `notifications/initialized`。

## 依赖

- Go 1.26.2
- [chromedp](https://github.com/chromedp/chromedp) - Chrome DevTools Protocol 库
- [cobra](https://github.com/spf13/cobra) - CLI 框架
- [go-sdk](https://github.com/modelcontextprotocol/go-sdk) - MCP 服务端（只有 `cmd/mcp` 用）
