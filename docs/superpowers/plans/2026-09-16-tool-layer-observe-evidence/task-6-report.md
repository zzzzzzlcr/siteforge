# Task 6 报告：`cdp observe` 子命令

**日期**：2026-09-16　**分支**：master
**提交**：`44b433a`（本体）→ **`80b7cd8`（修复轮 1）** → **`f501c3f`（修复轮 2）**
**状态**：**DONE** —— 三轮裁定全部落地，无未决契约缺口。
CLI 全绿（`go test ./...`：**PASS 80 / SKIP 0 / FAIL 0**；`./cmd/` 21 绿）。
三轮修掉 3 条：空数组编码 `null`（§8）、`--json=false` 静默不做事（§8）、
单帧 `frame_path` 写死 `['main']`（§9）。仍留 ledger 的只有一条负面结论
（`frame-error` 无确定性测法，§8.7 ⑤ —— 控制器已确认不必再花时间）。

---

## 1. 交付物

| 文件 | 说明 |
|---|---|
| `tools/cdp/cmd/observe.go` | **新增**：子命令实现（78 行） |
| `tools/cdp/cmd/observe_test.go` | **新增**：flags 契约（弱测试，52 行） |
| `tools/cdp/cmd/observe_e2e_test.go` | **新增**：真二进制的端到端闸门（620 行，含自备浏览器） |
| `tools/cdp/cmd/root.go` | **未改** —— 注册走 `observe.go` 的 `init()`，与 brief Step 3 给的代码一致（brief 的「Modify root.go」与它自己的实现代码不符，以实现代码为准） |

`internal/` 一个字没动（`git status` 干净）。

---

## 2. CLI 的形态

```
cdp [--host H] [--port P] observe [--json[=true]] [--frame-id <CDP frameID>]
```

| 项 | 值 |
|---|---|
| `--json` | 默认 **true**（brief 钉的值）。**当前恒为 JSON**，开关只是占位（见 7-②） |
| `--frame-id` | 默认 `""`。非空 → `Observe(frameID)` 单帧；空 → `ObserveAll()` 整页含子帧 |
| 输出 | `PageModel` 的 JSON 到 **stdout**，缩进两格（`SetIndent("", "  ")`），一次 `Encode` |
| 退出码 | **0** = 拿到了模型（**含** `diagnostics` 非空的情况）；**1** = 没拿到模型（连不上 Chrome / 主帧 eval 失败 / frame-id 不存在） |
| 副作用 | `defer client.Disconnect()`（不是 `Close()`，遵守 CLAUDE.md 那条「Close 会关页面」）—— 不关页面、不改页面 |

诊断通道那两类**没有混**：`obstructions` = 页面上的遮挡物（带 `dismiss_selector`），
`diagnostics` = 观测者自己的问题（某帧没取到）—— 语义在 `observe.go` 顶部注释里写死，
免得 py 侧拿 frameId 当选择器去点。

---

## 3. 手工跑（实测，不是转述）

```
$ export PATH=/usr/local/go/bin:$PATH && go build -o /tmp/cdp-observe main.go
构建 OK: 12828698 bytes

$ /tmp/cdp-observe --host 127.0.0.1 --port 9222 navi http://127.0.0.1:8123/base.html
{"frame":{"frameId":"E5DDABB7C915EBF68A2AFBF0F4D7AA67","url":"http://127.0.0.1:8123/base.html"}}

$ time /tmp/cdp-observe --host 127.0.0.1 --port 9222 observe | head -30
{
  "url": "http://127.0.0.1:8123/base.html",
  "title": "R3 base",
  "page_text": "We use cookies. Accept All Learn More Save on your energy bill Schedule a free in-home audit today. Schedule Now Get your free quote ZIP Code Continue Learn More",
  "shadow_roots": 0,
  "actions": [
    {
      "selector": "#onetrust-accept-btn-handler",
      "alternates": [],
      "stability": "high",
      "text": "Accept All",
      "role": "button",
      "tag": "BUTTON",
      "type": "submit",
      "visible": true,
      "occluded_by": null,
      "shadow_depth": 0,
      "frame_path": [
        "main"
      ],
      "bbox": [
        138,
        357,
        99,
        35
      ],
      "region": "body",
      "above_fold": true,
      "relative_size": 1,
      "peer_count": 1,

real	0m0.540s
$ /tmp/cdp-observe --host 127.0.0.1 --port 9222 observe >/dev/null; echo $?
0
```

同一档页面的**模型规模**：`actions=6 fields=1 obstructions=1 diagnostics=0`，
遮挡物识别成 `{"kind":"cookie-banner","selector":"#onetrust-banner","dismiss_selector":"#onetrust-accept-btn-handler",...}`。

跨帧那一档（`outer_same.html` → 子帧里两层 shadow + 表单，**6 个 action 全部来自子帧**）：

```jsonc
{ "selector": "#submit", "text": "Get My Quote",
  "frame_path": ["main", "086EC6DB31210F2463CCE195B72409A1"],   // ← py 侧选帧靠这个
  "shadow_depth": 2, "bbox": [10, 130, 99, 21] }
// option_groups: [{"scope":"#shower-opts","role":"option","options":["Tub to walk-in shower","I need help deciding"],"shadow_depth":2}]
```

---

## 4. 端到端测试怎么做的

**机制**：`go build -o <tmp>/cdp-observe main.go` 到临时目录 → **exec 真二进制** →
断言 stdout / stderr / **退出码**。中间不做任何 Go 函数直调
（直调测不到 flag 解析、输出编码、退出码 —— 那三样恰好是 py 侧依赖的全部）。

三条测试的断言：

| 测试 | 断言 |
|---|---|
| `TestObserveCommandEndToEnd` | 退出码 0；stdout `json.Valid`；**原始 JSON 查键**（`url/title/page_text/actions/fields/option_groups/obstructions/diagnostics` 八个都在）；`actions` 是数组；解进 `internal.PageModel` 不报错；`url`/`title` 等于**这条测试自己导航过去的**那一页；`actions` 非空且每条有 `selector` + `frame_path`；`#schedule-now` 在；`fields` 非空；`obstructions` 里有 `cookie-banner` 且 `selector`/`dismiss_selector` 都非空；`diagnostics` 每条有 `kind`。这条测试的 `--host/--port` 放在**子命令后面**，顺带验 PersistentFlags 位置无关 |
| `TestObserveCommandMergesChildFrames` | 同上 + `actions==6 / fields==3 / shadow_roots==2`（外层页一个可动作元素都没有，这 6 个只可能来自子帧）；每条 `frame_path` 形状是 `["main", <真 frameID>]` 且**全指向同一个子帧**，`frame_path[1]` 非空且不是 `"main"`；有 `shadow_depth>0`（穿透没在合并时丢）；`option_groups` 恰好 1 组 2 项 |
| `TestObserveCommandExitCodeOnBadFrame` | `--frame-id NO_SUCH_FRAME_ID` → 退出码 **1**、**stdout 为空**（不许有半截 JSON）、stderr 非空 |

**环境**：fixture 走 `internal/testdata`（httptest 自带服务）—— 与 Task 3/4/5 同一批文件，
没有新增对 `localhost:8080` 的依赖。

### ⚠️ 这里有一个必须记下来的发现：共享浏览器不能用

第一版沿用 127.0.0.1:9222 那个浏览器（Task 3/4/5 的做法）。**`go test ./cmd/` 全绿，
`go test ./...` 必红，4/4 复现**：

```
--- FAIL: TestObserveCommandMergesChildFrames
    observe_e2e_test.go:324: actions = 1, want 6（全部来自子帧；0 = 只观测了主帧）: [#real-btn]
```

`#real-btn` 不是我的夹具 —— 它是 `internal/click_integration_test.go:187` 注入的。
根因：`go test ./...` **并行**跑 `cdp/cmd` 与 `cdp/internal` 两个测试二进制，
而两边都通过 `GetActivePageTargetID` 抓**同一个**页面目标；internal 的点击测试直接
`document.body.innerHTML = ...`，把我的 iframe 连同整页 DOM 一起换掉了
（URL 没变，所以连「页面不是我导航的那个」都看不出来 —— 这条更阴）。

**做法**：这条闸门**自备浏览器** —— 每次 `go test` 起一个私有 headless Chrome
（`freePort()` 随机端口 + `os.MkdirTemp` 私有 profile，开关照抄 9222 那个实例：
`--headless=new --remote-debugging-address=127.0.0.1 --disable-gpu --no-sandbox ...`）。
通过 **CLI 自己的 `navi`** 导航（与 py 运行时同一条路），再 `observe`。
好处有三：不再与任何并行测试抢页面；`--host/--port` 这条链路被真验了（那正是 py 在
worker 容器里的用法，那边 Chrome 不在 9222）；顺带不受「9222 上现在挂着什么页面」影响。

清理：`TestMain` 杀**整个进程组**（Chrome 有 zygote/renderer/crashpad 一串子进程，
只杀父进程会留孤儿 —— 本仓库栽过「孤儿窗口 → 内存 95%」），随后 `Wait()` 收尸、
删临时 profile。实测全量跑完后：`remote-debugging-port` 只剩 9222 一个，
无 chrome 僵尸、无 `/tmp/cdp-observe-*` 残留。

**没有 Chrome 时失败而不是 Skip**：实测（把候选列表临时清空）——
`--- FAIL: TestObserveCommandEndToEnd / 搭端到端环境失败…找不到 Chrome/Chromium`，
不是 SKIP。skip 与 pass 在报告里长得一样，这条闸门宁可红。

---

## 5. 逐条实测结果

> 本节数字是 **本体轮**（commit `44b433a`）的；修复轮 1（`80b7cd8`）之后的数字见 §8.6
> —— 那边 `./cmd/` 20 绿、全量 79 绿（本轮新增两条测试）。

| 命令 | 结果 | 耗时 |
|---|---|---|
| `go test ./cmd/ -v -count=1` | **PASS 18 / SKIP 0 / FAIL 0**，退出码 0 — `ok cdp/cmd` | 2.08s |
| ├ `TestObserveCommandEndToEnd` | PASS | **1.43s** |
| ├ `TestObserveCommandMergesChildFrames` | PASS | **0.60s** |
| ├ `TestObserveCommandExitCodeOnBadFrame` | PASS | 0.03s |
| ├ `TestObserveCommandHasContractFlags` | PASS | 0.00s |
| └ `TestObserveCommandRegisteredOnRoot` | PASS | 0.00s |
| `go test ./... -v -count=1` | **PASS 77 / SKIP 0 / FAIL 0**，退出码 0 — `ok cdp/cmd 2.1s` + `ok cdp/internal 38.4s`（另有 ginkgo `Ran 4 of 4 Specs … 4 Passed \| 0 Skipped`） | **39.1s** |
| `go test ./... -count=1`（非 verbose） | 退出码 **0** | 34.7s |
| `gofmt -l` / `go vet ./...` | 我的三个文件都不在 gofmt 名单里；vet 干净 | — |

首次构建二进制 ~0.5s（三条测试共用一次构建，`sync.Once`）。

---

## 6. 变异验证（4 个变异体，每个都**先红后还原**；`git diff` 事后为空）

| 变异 | 改了什么 | 哪条测试红、红在哪 |
|---|---|---|
| **A** 输出非 JSON | `runObserve` 在 `Encode` 前多打一行 `fmt.Fprintln(os.Stdout, "MUTATION-A")` | `TestObserveCommandEndToEnd` → `stdout 不是合法 JSON`（1.51s 红） |
| **B** 吞掉错误 | `if err != nil { fmt.Fprintln(os.Stderr,…); return nil }`（错误路径 exit 0） | **只有** `TestObserveCommandExitCodeOnBadFrame` 红 → `frame-id 不存在却退出码 0 —— py 侧会把这当成一次成功的观测`（0.88s） |
| **C** 只观测主帧 | `client.ObserveAll()` → `client.Observe("")` | `TestObserveCommandMergesChildFrames` 红 → `actions = 0, want 6`（1.00s）。**注意**：变异体下 `TestObserveCommandEndToEnd` **照绿**（base.html 没有 iframe）—— 这就是为什么要专门加跨帧那条测试 |
| **D** 假装机器上没浏览器 | `chromeBinary()` 的候选列表清空 | `TestObserveCommandEndToEnd` **FAIL**（不是 SKIP）→ `搭端到端环境失败…找不到 Chrome/Chromium` |

A/B/C 过后 `cp` 回原文件并 `diff` 确认逐字节一致；D 同理。

---

## 7. 自查发现 / 顾虑

① **真发现：空数组被编码成 `null`（不是 `[]`）。** `diagnostics` / `option_groups` /
`obstructions` 是 Go 的 `[]T`，为空时 `encoding/json` 给 `null`：

```jsonc
"diagnostics": null,          // base.html 实测原文（`grep -n '"diagnostics"'` → 217:  "diagnostics": null）
"option_groups": null,
"alternates": [],             // ← 同一个文档里，来自 JS 的数组是 []
"frame_path": ["main"],       // ← 同上
```

规格 §4.3 画的是数组。**py 侧 `for d in model["diagnostics"]` 会 TypeError**——
而这是运行阶段唯一的接口。

> **→ 修复轮 1 已裁定「修实现」，见 §8。** 本节保留原始发现（含当时的判断：
> 「契约面、不在本任务文件清单里，故不动手」）—— 那条判断当时是对的，
> 现在由控制器裁定越权修改 `internal/`，已落地：Observe 与 ObserveAll 两条路
> 都归一化成 `[]`，测试也从「两种形状都放行」**收紧成只放行 `[]`**。

② **`--json=false` 仍然是 JSON**（brief 指定的实现：flag 只是「便于以后加人类可读格式」的占位）。
契约写的是「默认 JSON」，但开关名会让人以为能关。py 侧别用它。

> **→ 修复轮 1 已裁定「实现或删掉」，选择实现，见 §8。**

③ **失败时 cobra 把整段 usage 打到 stderr**（沿用其他子命令的行为）：
`Error: observe eval: OOPIF eval: attach failed: …` + `Usage: cdp observe [flags] …`。
退出码是准的（1），但 py 侧若解析 stderr 要取首行，别假设只有一行。

④ **e2e 对 `diagnostics` 只验了形状、没验内容**：fixture 页本来就该是空的
（主帧取到了、帧数对得上），要造一个非空 diagnostics 得专门设计坏帧夹具 ——
那一档归 `internal` 的集成测试（Task 4 的 `frame-error` / `frame-blind` 用例）。
**已知豁口**：CLI 这条路上「diagnostics 非空时也能正常出口 0」没有被实测覆盖。

⑤ **单帧模式没有「只观察主帧」这个用法**：`--frame-id ""` 被当成「没传」→ 走整页模式。
要单看主帧目前只能用 `internal.Observe("")`（Go 侧）。已在 `observe.go` 注释里写明。

⑥ 本任务**不改** `internal/`，所以 ① 这条发现没有落成补丁 —— 若要修，
建议单独一轮（改 `PageModel` 的编码属于 Task 2/4 的契约面，跨任务决定）。

---

## 8. Task 6 修复轮 1（2026-09-16，控制器裁定）

四条裁定：① 空数组编码**修** + 测试**收紧**；② `--json=false` **实现或删**；③ 非空 diagnostics
e2e **可不做**（记 ledger）；收尾：两种格式各贴实测、变异验证、一个 commit。

### 8.1 ① 空列表归一到 `[]`（改 `internal/`，越权但已获裁定）

新增 `normalizeNilLists(*PageModel) *PageModel`（`internal/observe.go`，紧挨 `PageModel` 定义），
把五个切片里为 nil 的置成空切片；**两条路各调一次**：

| 路径 | 调用点 | 为什么必须在这 |
|---|---|---|
| `Observe(frameID)`（单帧） | `internal/observe.go` 末尾 | JS 不产出 `diagnostics`，不归一化它编出来恒为 `null` |
| `ObserveAll()`（整页合并） | `internal/observe_frames.go` 末尾 | `merged := &PageModel{}` 起手，某类**一个元素都没并进来**时 `append` 不改变 nil → null（base.html 的 `option_groups` 就是这么变 null 的） |

不改 JS、不改 `PageModel` 的类型（`[]T` 不变）—— 只在**出边界前**把「空」统一成一种形状。

### 8.2 ① 测试收紧：只放行 `[]`，退回 `null` 必红

| 位置 | 之前 | 现在 |
|---|---|---|
| `TestObserveCommandEndToEnd` | 四个列表「数组**或** null 都放行」 | 五个列表**一律** `[` 开头，否则报 `空列表必须是 []，不是 null` |
| `TestObserveCommandEmptyListsAreArrays`（**新增**） | — | 自造空页 `/__empty.html`，断言五个键的**字面量就是 `[]`**，且 Go 侧解出来**非 nil** |
| `TestObserveCommandMergesChildFrames`（加腿） | — | 追加**单帧模式**（`observe --frame-id <子帧ID>`）一档，覆盖 `Observe` 那条路 |

空页是**本测试自己造的**（`newFixtureServer()` 的 mux 里一条 handler），不是借
`outer.html`（那张 iframe 指向死端口 8892，依赖「此刻没人监听 8892」，会随时间变质）。
理由：`null` vs `[]` **只在空列表上现形**，base.html / outer_same.html 里五类几乎都有元素，拿它们测等于空转。

### 8.3 ② `--json=false` 实现成人话摘要（`renderHuman`，`cmd/observe.go`）

按裁定的内容：URL / title、各类计数、前 5 条动作的 `text` + `stability`、`diagnostics` 与
`obstructions` 的条数；另加了两样**人会立刻用到**的：遮挡物的 `dismiss_selector`（下一步该点哪）
与 diagnostics 的 kind + 帧路径。**只做摘要不做判断**（D11）。默认路径（`--json`）一个字节没变。

### 8.4 手工实测（两种格式）

```
$ /tmp/cdp-observe --host 127.0.0.1 --port 9222 observe | head -12      # 默认 JSON
{
  "url": "http://127.0.0.1:8123/base.html",
  "title": "R3 base",
  "page_text": "We use cookies. Accept All Learn More Save on your energy bill …",
  "shadow_roots": 0,
  "actions": [
    { "selector": "#onetrust-accept-btn-handler", "alternates": [], "stability": "high", "text": "Accept All", …
real	0m0.541s

$ /tmp/cdp-observe --host 127.0.0.1 --port 9222 observe --json=false     # 人话，退出码=0
R3 base
http://127.0.0.1:8123/base.html

可动作元素 6 个  表单字段 1 个  选项组 0 组  遮挡物 1 个  诊断 0 条

可动作元素（前 5 / 共 6）：
  1.  [high]  Accept All    #onetrust-accept-btn-handler
  2.  [high]  Learn More    #nav-learn
  3.  [high]  Schedule Now  #schedule-now
  4.  [high]                #zip
  5.  [high]  Continue      #go
  …                         还剩 1 个（用 --json 看全量）

遮挡物（页面上的东西，先关掉再操作）：
  cookie-banner  #onetrust-banner  → 点 #onetrust-accept-btn-handler

诊断：无
```

空列表那一档（同一台浏览器、`outer.html` —— **修复前这五行全是 `null`**）：

```
  "actions": [],
  "fields": [],
  "option_groups": [],
  "obstructions": [],
  "diagnostics": []
```

### 8.5 变异验证（本轮要求的那个：把空列表退回 `null`）

| 变异 | 改了什么 | 结果 |
|---|---|---|
| **E** 还原成 `null` | `normalizeNilLists` 开头插 `if true { return m }` | **三条测试同时红**：`EndToEnd` → `option_groups 不是 JSON 数组…: null` + `diagnostics …: null`；`MergesChildFrames` → `单帧输出 diagnostics 不是 JSON 数组…: null`；`EmptyListsAreArrays` → 五个键逐个 `actions = null，want []`。事后 `diff` 确认 `internal/observe.go` **逐字节还原** |

（A/B/C/D 四个变异体见 §6，仍然有效 —— A/B/C 覆盖 CLI 自己的行为，D 覆盖「无浏览器时 FAIL 而非 SKIP」。）

### 8.6 本轮实测计数

| 命令 | 结果 | 耗时 |
|---|---|---|
| `go test ./cmd/ -v -count=1` | **PASS 20 / SKIP 0 / FAIL 0**（本体 18 + 本轮新增 2） | 3.21s |
| ├ `TestObserveCommandEndToEnd` | PASS | 1.38s |
| ├ `TestObserveCommandMergesChildFrames`（含单帧腿） | PASS | 0.63s |
| ├ `TestObserveCommandEmptyListsAreArrays`（新） | PASS | 0.58s |
| ├ `TestObserveCommandHumanFormat`（新） | PASS | 0.57s |
| └ 其余三条（退出码 / flags / 注册） | PASS | 0.02s / 0.00s / 0.00s |
| `go test ./... -v -count=1` | **PASS 79 / SKIP 0 / FAIL 0**，退出码 0 | **39.3s** |

`internal/` 的 Task 3/4/5 用例**全绿未受影响**（它们的断言看的是 Go 值，不比对 JSON 形状）。

### 8.7 本轮新发现（**报出去，本轮不修**）

③ **单帧模式的 `frame_path` 是错的（写死的 `['main']`）**。`observeJS` 对每条动作/字段
都写死 `frame_path: ['main']`（`internal/observe.go:282` 与 `:301`）：

```
$ cdp observe --frame-id <子帧ID>        # 这一帧其实是 iframe 里的 inner.html
{ "selector": "#submit", …, "frame_path": ["main"], "shadow_depth": 2 }
```

合并那条路（`ObserveAll`）会被 `mergeFrameModel` **覆盖**成真实路径，所以没错；
**单帧这条路没有任何东西覆盖它** —— 输出声称「这些元素在主帧」，而它们其实在子帧。
py 侧若照 `frame_path` 选帧，会**把点击发到主帧**（点了空气 / 点错元素）。
这比空列表那个更危险（那个会炸得很响，这个静默点错）。

修法要定契约（是用 `[frameID]` 还是 `["main", frameID]`？主帧又该是什么），
属于 Task 2 的 JS 契约，**我没动**，也刻意**没有在测试里断言它**（免得把错的行为钉死）。
本轮的 e2e 只对单帧模式断言「退出码 0 + 五个列表是数组 + 总量对得上」，没有断言 `frame_path`。

④ **顺带实测到的**（不是缺陷，但会让人误判）：`navi` 失败后，共享页面目标的 URL 变成空串，
于是 `filterPageTargets`（`internal/targets.go:68`）把它滤掉，后续命令一律报
`no page target found` —— 我手工探测时被这个误导过一次（以为浏览器挂了，实际是自己把
fixture 服务关了）。py 侧遇到这个报错时，先查上一步导航成没成。

⑤ **ruling ③ 的负面结论**（省下一次尝试）：想顺手用 `outer.html`（iframe 指向死端口 8892）
逼出非空 `diagnostics`，**实测两次都不行** —— 加载失败的帧会渲染成 Chrome 错误页，
`Observe` 对它照样成功，于是 diagnostics 仍为 `[]`。所以 ③ 留 ledger。

---

## 9. Task 6 修复轮 2（2026-09-16，控制器裁定契约）

针对 8.7 ③（单帧 `frame_path` 写死 `['main']`）—— 控制器把契约定死并同步进规格 §4.3：

```
Observe(frameID)  →  [frameID]                     （frameID 为空 → ["main"]）
ObserveAll()      →  ["main", <childId>, …]        从根到叶
```

理由（照抄裁定）：单帧不知道祖先链，但**消费者要的正是「该把动作发给哪一帧」**，
`[frameID]` 就是这个信息；要完整路径的人用 `ObserveAll`。两种模式语义不同，但都自洽、都够消费者用。

### 9.1 实现（`internal/observe.go`）

| 新增 | 作用 |
|---|---|
| `framePathFor(frameID) []string` | 单帧的取值：空 → `["main"]`，否则 `[frameID]` |
| `normalizeFramePaths(m, frameID)` | 把每条动作/字段的 `FramePath` 盖成上面的值 |

调用点**只有一处**：`Observe` 末尾那一次归一化 —— 与 `normalizeNilLists` 同一行返回：
`return normalizeFramePaths(normalizeNilLists(&m), frameID), nil`。
`ObserveAll` **没动**（它的路径由 `mergeFrameModel` 写，本来就是对的）。

**`observeJS` 一个字没动**（照裁定）：那是 JS 侧的默认值，合并那条路依赖它保持现状；
在 Go 侧覆盖，影响面只有「单帧」这一条。实测确认合并路径输出未变：
整页仍是 `["main", "2CEB94DD3E515564E957F74A77CBE827"]`。

### 9.2 手工实测（修复前 / 修复后）

```
$ cdp observe --frame-id 2CEB94DD3E515564E957F74A77CBE827     # 子帧（inner.html），退出码=0
  "url": "http://127.0.0.1:8123/inner.html",
      "frame_path": [
        "2CEB94DD3E515564E957F74A77CBE827"        ← 修复前这里是 "main"（JS 写死的错值）

$ cdp observe | python -c …                                # 整页模式（合并那条路）没被动到
  整页 actions 的 frame_path 集合: {('main', '2CEB94DD3E515564E957F74A77CBE827')}
```

### 9.3 测试（契约定了，所以现在可以断言了）

| 腿 | 在哪 | 断言 |
|---|---|---|
| **子帧**（走真二进制） | `TestObserveCommandMergesChildFrames` 的单帧段 | 每条 action/field 的 `frame_path == [<childId>]`（共 9 条） |
| **主帧** | `TestObserveSingleFrameMainFrameFramePath`（**新增**） | `Observe("")` 的每条 action/field `frame_path == ["main"]` |

⚠️ 主帧那条腿**走不了二进制**：CLI 把 `--frame-id ""` 当成「没传」→ 整页模式，
所以「单帧观测主帧」在 CLI 上根本不可达 —— 只能在这一层（Go API）验，
用的是同一个私有浏览器（不经 9222，不和别人抢）。这条在测试注释里写明了原因。

顺带把四处 frame_path 断言的格式符从 `%v` 换成 `%q`：**`%v` 会把 `[""]` 印成 `[]`**，
看起来像「空切片」（变异 G 时亲眼见到），换成 `%q` 后是 `[""]` vs `["main"]`，一眼可辨。

### 9.4 变异验证（两个，都先红后还原、`diff` 确认逐字节还原）

| 变异 | 改了什么 | 结果 |
|---|---|---|
| **F** 去掉 frame_path 归一化（`Observe` 只调 `normalizeNilLists`） | — | **子帧那条腿红**：9 条断言逐条报 `单帧 actions[i] (#fn) 的 frame_path = ["main"], want ["7155C70788…"]`。★ **主帧那条腿照绿** —— 因为 JS 的默认值 `['main']` 恰与主帧契约相同。**这条负面结果本身就是设计依据：真正咬住这个缺陷的是子帧那条腿** |
| **G** 去掉主帧特判（`framePathFor` 对空 frameID 返回 `[""]`） | — | **主帧那条腿红**：`主帧 actions[0] (#onetrust-accept-btn-handler) 的 frame_path = [""], want ["main"]`（子帧那条腿照绿）。证明主帧那条腿不是摆设 —— 它守的是「归一化别把主帧写成空串」 |

### 9.5 本轮实测计数

| 命令 | 结果 | 耗时 |
|---|---|---|
| `go test ./cmd/ -v -count=1` | **PASS 21 / SKIP 0 / FAIL 0**（本体 18 + 轮 1 两条 + 轮 2 一条） | 3.33s |
| ├ `TestObserveCommandEndToEnd` | PASS | 1.40s |
| ├ `TestObserveCommandMergesChildFrames`（含单帧子帧腿） | PASS | 0.63s |
| ├ `TestObserveSingleFrameMainFrameFramePath`（新） | PASS | 0.09s |
| ├ `TestObserveCommandEmptyListsAreArrays` / `HumanFormat` | PASS | 0.58s / 0.58s |
| └ `TestObserveCommandExitCodeOnBadFrame` / `HasContractFlags` / `RegisteredOnRoot` | PASS | 0.03s / 0.00s / 0.00s |
| `go test ./... -v -count=1` | **PASS 80 / SKIP 0 / FAIL 0**，退出码 0 | **40.0s** |

### 9.6 状态

- `frame_path` 契约（轮 2）与空列表归一（轮 1）**都已落地并有测试守**：共 8 条 observe 测试，两个变异体各咬住一条腿。
- 提交：`44b433a`（本体）→ `80b7cd8`（轮 1）→ `f501c3f`（轮 2）。
- **没有未决的契约缺口**了：8.7 ③ 是最后一条，已按裁定修完。
- 唯一仍留 ledger 的是 8.7 ⑤（`frame-error` 无确定性测法）—— 控制器已确认不必再花时间。
