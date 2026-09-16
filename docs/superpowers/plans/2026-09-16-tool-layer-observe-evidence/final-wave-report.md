# 终审修复波 —— 逐条对账

范围：终审（`3bd4329..597ffd9`）列的 7 条。**一行行为代码都没动**（除第 2 项 Dockerfile 的
chmod 编排 —— 那正是终审复现的缺陷本身）。没有动规格（§4.3/§4.4 的修订由控制器完成）。
没有碰 `testdata/` 的任何 fixture。没有派 subagent。

浏览器：**私有 headless Chrome**（`/tmp/finalwave/prof`，端口随机，`--headless=new`，
开关照抄 `cmd/observe_e2e_test.go` 的 `launchChrome`），全程没碰共享的 9222。
`cmd/` 的 e2e 本来就自备浏览器。

---

## 1. 文档补齐 —— DONE

### `tools/cdp/CLAUDE.md`

| 补了什么 | 位置 |
|---|---|
| `observe` / `diff` 两条命令 + `targets`/`active`/`close`/`form` 的一行示例 | `## Commands` 代码块 |
| 子命令 flags 从 5 条补成 **11 条**（含 `observe --json/--frame-id`、`diff --before/--json/--frame-id`） | `**子命令 Flags:**` |
| 功能说明补 observe（§4.3 契约、跨源逐帧合并、frame_path）与 diff（三条判据） | `**功能说明:**` |
| 架构树：`cmd/` 补 `observe.go`/`diff.go`/`form.go`/`targets.go`/`active.go`/`close.go`；`internal/` 补 `observe.go`/`observe_frames.go`/`diff.go`/`shadow.go`/`form.go`/`targets.go`；新增 `internal/testdata/` | `## Architecture` |
| **改正架构树的代码位置**：原先的树只说 `internal/js.go` 放 JS 常量，暗示所有 JS 都在那儿 —— 实际 observeJS 在 `internal/observe.go`、穿透内核在 `internal/shadow.go`，`js.go` 只有触控/snapshot/轨迹三份。树下面补了一条 ⚠️ 明说这件事 | 树末尾 |
| **改掉两处 `<code>internal/client.go:141</code>`**（项目约定 + Gotchas 各一处）：那是个**陈旧行号**，141 行现在落在一个无关的 `return nil` 上，与「30 秒导航超时」毫无关系。正确位置是 **`internal/client.go:401`**（`Navigate` 内的 `time.After(30 * time.Second)`；函数定义在 367 行）—— 两处都改成 `internal/client.go:401` 并带上函数名 `Navigate`（行号会漂，函数名不会） | — |
| Gotchas 补两条：单帧 `--frame-id` 收的是 CDP frameID（不是 `"main"`）；集成测试默认打 9222、cmd 的 e2e 自备私有浏览器，两个测试二进制别抢同一页面 | — |

### `tools/cdp/README.md`

- 顶部一句话补「页面模型观测（observe）与差分（diff）」。
- 新增 **`### observe`** 与 **`### diff`** 两节（照现有 `### <cmd> - <中文>` + 用法 + `Flags:` 的格式）：
  observe 一节写了 PageModel 是什么、`--json`/`--frame-id`、以及 diagnostics 与 obstructions
  **语义不同别混用**；diff 一节写了三条判据、`--before` 必填、`--json`、`--frame-id` 的陷阱、
  **退出码约定（`actionable=false` 也是 0）**、以及 R20 的能力边界。

### `/company/siteforge/README.md`

- 第 54 行的状态行（「工具层尚未迁入，Dockerfile 依赖 tools/cdp/ 就位后才能构建」——
  **被本分支证伪**）改成当前真相：计划一已交付、observe/diff 在四档页面验过、
  计划二/三未开工；并写明**镜像可构建但暂不可运行**（等计划二的 `agent/`）。
- 目录块里 `cmd/cdp = CLI` 一行也改成真的（入口是**模块根**的 `main.go`，cobra 根命令在
  `cmd/` 下 —— 与 Dockerfile 第 28 行的既有警告同口径），`cmd/mcp` 标注「计划二交付，尚无此目录」。

---

## 2. Dockerfile 的 entrypoint 永远不可执行 —— DONE（复现 + 修复 + 实测）

```dockerfile
# 原样（缺陷）
RUN chmod +x /usr/local/bin/cdp /usr/local/bin/cdp-mcp \
    && chmod +x /opt/siteforge/entrypoint.sh 2>/dev/null || true
```

**本机在 /tmp 用真 shell 复现**（`chmodsim/`，摆出镜像里那份状态：`cdp` 存在、`cdp-mcp` 缺失、
`entrypoint.sh` 是 git 里的 `100644`）：

| 写法 | entrypoint.sh 的模式 | 退出码 |
|---|---|---|
| 旧（`A && B \|\| C`） | **644 —— 没被执行过** | 0（`\|\| true` 把退出码救了） |
| 新（拆两条） | **755** | 0 |

修法（`Dockerfile:66-76`）：

```dockerfile
RUN chmod +x /usr/local/bin/cdp \
    && chmod +x /opt/siteforge/entrypoint.sh
# cdp-mcp 要到 Task 8 才存在 —— 它单独一条、且允许失败
RUN chmod +x /usr/local/bin/cdp-mcp 2>/dev/null || true
```

- 第一条链里只剩**都该存在**的两个文件：真缺 `entrypoint.sh` 时**响亮地失败**（实测：退出非 0，
  不再被 `|| true` 吞掉），而不是留一个 644 的文件到运行期变成 `Permission denied(126)`。
- 第二条在 JS 里单独成立，失败只影响它自己 —— 并注明**不许**再和别的 chmod 串链。
- **标记文字改正**（原话「缺文件不影响 build: 末尾有 || true 兜住」告诉恢复者的与真相相反）：
  现在写明短路语义、真实后果（cdp-mcp 缺 → entrypoint 的 chmod 从不执行 → 126），
  并引终审已复现该语义。
- **补上那句实话**（`Dockerfile:93-96`）：**镜像可构建，但暂时不可运行** —— ENTRYPOINT 要的
  `agent.service:app` 在计划二交付 `agent/` 之前不存在（起来就是 ModuleNotFoundError）。
  同一句也写进了根 README 的状态节。

---

## 3. D11 感知面零端到端断言 —— DONE（含变异验证）

新增两个**带自证**的断言层，都在 `internal/observe_integration_test.go`：

- `measureViewport(t)`：另开一条连接量当时的**视口 / 文档**尺寸（PageModel 里没有尺寸字段，
  「bbox 落在哪」只有对着当时的尺子才有意义）。⚠️ 实测坑：`[innerWidth, innerHeight]` 这种
  **数组**结果 `EvalInFrame` 解不回（数组是对象，不带 `returnByValue` 拿到的是 nil，`nil` 会让
  边界断言**悄悄失去意义**）—— 所以逐个取数字，并把「解出来是 0/负」当场 Fatal。
- `requireActionInvariants(t, m, vp)`：逐动作不变量（每条的零值/真值可区分性见下表）。

`TestObserveLightDOM` 现在**自带有效性证明**（与 `requireShadowActions` 同族）：
`len(Actions) < 5` 从 `Errorf` 收紧成 `Fatalf`（动作列表一空，后面每条都空转），
找不到 `#schedule-now` / `#nav-learn` 时 `Fatal` 并**列出实际看到的选择器**（`selectorsOf`）。

### 新加的断言各守什么（含变异实测）

| 断言 | 守的是什么 | 变异实测 |
|---|---|---|
| `bbox` 宽高 > 0 | `bbox` 键写错 → 零值 `[0,0,0,0]`。R17（截图上的框要和元素对上）的载重字段 | `bbox:` → `bboxXX:` → **红**（6/6 动作报 0×0） |
| `bbox` 原点非负、落在文档范围内（`x+w<=docW && y+h<=docH`） | 坐标口径错（整体缩放/偏移）。×3 的 DPR 缩放会让脚注的 `y+h=1338` 冲出 492 高的文档 | 见上一行同一变异 |
| `above_fold == (bbox.y < 视口高)` | **把两个字段绑在一起**：`above_fold` 键写错、或 `y` 被整体缩放，都会让两边不一致 | `above_fold:` → `aboveFoldXX:` → **红**（消息直接印出「bbox.y=301 与视口高 437 给出的答案是 true」） |
| hero 按钮（`#schedule-now`）`nearby_text` 非空且**无空白项** | `nearby_text` 键写错 → 空列表 | `nearby_text:` → `nearbyTextXX:` → **红** |
| header 链接（`#nav-learn`）`above_fold` 为真 | 终审点名的那个字段 | 同上（M3 里同时红） |
| `tag`/`role` 恒非空 + **具体值**（hero=`BUTTON`/`button`，链接=`A`/`a`） | 「非空但串位」的绑定错误 —— 非空断言抓不住的那类 | 把 `role:` 与 `tag:` 的取值**互换** → **红**（`tag="button" role="BUTTON"`） |
| `visible` 为真 | 键写错 → 零值 false（JS 侧恒 true；它守的是键，不是 vis() 过滤） | —（同族） |
| `z_index` 非空 | 键写错 → `""`（`getComputedStyle` 恒返回串，`"auto"` 也算） | — |
| `peer_count >= 1` | 键写错 → 0（计数含自己，恒 ≥1） | — |
| `relative_size > 0` | 键写错 → 0（页面有可见元素时中位数面积 > 0） | — |
| `contrast` 形态（空串 或 high/medium/low） | 键写错 → `""`。⚠️ 这条**只做形态检查**：祖先链全透明时 `contrast` **本来就是**空串（`lum()` 对 `rgba(0,0,0,0)` 返回 null）—— 实测 `#nav-learn`/`#foot-learn` 就是 `""`，所以不能断言非空 | — |
| hero 按钮 `contrast` ∈ {high,medium,low} | 上面那条的**真值**那一半：这个按钮自己的背景不透明，必然算得出一档 | — |
| hero 按钮 `region == "hero"`、链接 `region == "header"` | region 的两条路径都还在（landmark 优先 / class 派生兜底） | — |

### `Field` 的 `type` / `required` / `hint`（同为零断言字段）

- `observe_selector_test.go` 新增 **`TestObserveSelectorFieldAttributesBind`**（selector.html 那套）：
  `placeholder=Full` 的 `type=="text"`（**非零值**，能区分）、`required==false`（**「别多报」那一半**）、
  `hint` 非空。
- 正例放在 `TestObserveShadowPageTextIsNotEmpty`（shadow.html）：`#fn` 在 fixture 模板里**真的带
  `required`** → 断言 `Required==true`（selector.html 里没有任何 required 的 input，在那里断言
  `false` 等于**断言零值 = 空转**）；`#fn.type=="text"`、`#fn.hint` 非空（`name=firstName`）；
  另加 `#ph.type=="tel"`（换个**不同的** type 值：只钉 `text` 的话，「把所有 type 都写成 text」
  那种错照样绿）。
- 变异：`type: … required: …` 一起改名 → **红**（`#fn 的 type = ""，应为 text` +
  `#fn …带 required，却报成 false` + `#ph 的 type = ""`）；`hint:` 单独改名 → **红**。

### ⚠️ 与终审原话的一处**刻意偏离**：「每条 action 的 bbox 在视口内」不成立

终审要的是「bbox 非零且**在视口内**」。实测（私有 headless，视口 **780×437**）：

```
#foot-learn   bbox=[30 446 72 16]   above_fold=false
```

`observe` 收的是**整页**的可见元素，折线以下的元素**本来就合法** —— 脚注链接 y=446 > 437 是
**对的**。硬写「都在视口内」会得到一条对**正确实现**也红的断言（正是本仓反复栽的那种）。
所以换成一组**环境无关**的等价强度断言：**非零 + 非负 + 落在文档范围内 + 与 above_fold 自洽**
（自洽那一条把两个字段绑在同一把尺子上，键错位与坐标缩放在上面两条变异里都实测报了红）。
「header 链接在视口内」这层意思由 `above_fold==true` 这条具体断言守着。

---

## 4. `observe_traps_test.go:192` 的计数用了未剥注释的 js —— DONE

```go
if n := strings.Count(stripJSComments(js), "__cdpRoots = function"); n != 1 {
```

前提与修复都实测过（临时注入、跑完删除）：

| 场景 | 旧写法（未剥注释） | 新写法（剥注释） |
|---|---|---|
| 正文里一句提到该字面量的**注释** | 计数 **2** → 把**正确的**树弄红 | 计数 **1** → 绿 |
| 代码里**真的**出现两次（重复注入） | 2 → 红 | 2 → **仍然红** |

（「0 = withPierce 没注入」那一半不受影响：剥注释只删注释，不动代码。）

---

## 5. `cmd/diff_e2e_test.go` 的契约字段清单 —— DONE

`diagnostics_before` / `diagnostics_after` 进 `parseDiff` 的清单（第 70-75 行），并注明为什么：
这两个键的**存在**就是 I3「观测不全被读成有进展」那一轮的交付物，只靠 struct tag 钉不住
「它在 stdout 上真出现了」。全套 diff e2e（`--before` 好/坏/空对象/navi 输出、人话格式、
退出码）都照旧绿。

---

## 6. `.dockerignore` 漏了 `.superpowers/` —— DONE

根 `.dockerignore` 加 `.superpowers/`（带一句理由：SDD ledger + 十几份 review diff ≈ 2MB，
`COPY . /opt/siteforge/` 会把整份打进镜像，它没有一行是运行期需要的）。
（`tools/cdp/.dockerignore` 是另一个用途的文件，不动。）

---

## 7. `cmd/diff.go` 的两处帮助文字 —— DONE

- **`--frame-id`**：文字改成「只观测**动作后**那一帧（`--before` 不受它影响）；配整页快照用会把
  其它帧的元素全报成 disappeared 并给出**假** `actionable=true` —— 非调试别传」，
  并在这行 flag 上方的注释里写清机理（`--before` 永远是整页快照文件 → 其它帧的元素整批「消失」
  → 静默假进展）。
- **R20 那句**：补上**填/选类步骤该怎么办**（原先只说了 diff 判不了，没说替代做法）：
  ① 首选**动作命令自己的退出码**（`cdp form --select` 选项不存在时当场报错 `option not found`、
  `--value`/`--check` 元素找不到时报错 —— 都读代码确认过）；
  ② 要真断言「值写进去了」只能另用 `cdp eval` 读回 `el.value`，并注明 **eval 那条路不注入穿透
  助手**（shadow DOM 里的 input 够不着，这是 `observe.go` 顶部注释里已声明的既有事实）；
  ③ 通用判据要等计划二把字段值加进契约（R20）。

`cmd/diff_test.go` 的 `TestDiffCommandHelpTellsTheTruth`（断言 Long 里含「不判填写」「字段值」）
照旧绿；`--frame-id` 的新文案**不含反引号**（pflag 会把反引号当参数名占位符，帮助信息会被拆坏）。

---

## 测试

私有 headless Chrome（`--headless=new`、私有 profile、私有端口）；静态检查 `gofmt` + `go vet` 干净
（我改的每一个文件都过了 `gofmt -l`）。

| 运行 | PASS（含子测试） | SKIP | FAIL |
|---|---|---|---|
| `CDP_PORT=<私有> go test ./internal/ -v -count=1` | 82 | **0** | 0 |
| `go test ./cmd/ -v -count=1`（e2e 自备浏览器） | 43 | **0** | 0 |
| `go test . -v -count=1` | — | 0 | 0 |
| **合计** | **125** | **0** | **0** |

比分支原来的 124 多 1 条：新增 `TestObserveSelectorFieldAttributesBind`（其余新断言都加在既有
测试里，不靠新测试数凑数）。

**为什么分成两条命令而不是一条 `go test ./...`**：见下面「顾虑 ①」。一条 `./...` 里，
`internal` 需要的 `CDP_PORT` 会被 `cmd` 的 e2e 里那个子进程读到并**覆盖**它显式传的 `--port`
（既有缺陷，非本波引入）。

---

## 顾虑（都不在本波范围内，未改）

1. **`cmd/root.go` 的 flag/env 优先级是反的（既有缺陷，本波未修）** —— 实测：

   ```
   CDP_PORT=<死端口> cdp --host 127.0.0.1 --port <活端口> targets
     → dial tcp 127.0.0.1:<死端口>: connect: connection refused
   ```

   `PersistentPreRun` 里判的是 `cmd.PersistentFlags().Changed("port")` —— 那是**子命令**的
   flagset，`--port` 实际解析在**根**上，于是 `Changed()` 恒 false，env 覆盖显式 flag。
   后果：凡是在设了 `CDP_PORT` 的 shell 里跑 `go test ./...`，`cmd/` 的 e2e 会**静默打到另一个
   浏览器**上（我第一轮 baseline 就是这样红的：`TestObserveSingleFrameMainFrameFramePath`
   拿到 `about:blank`，而 `./cmd/` 单独跑全绿）。这与 `CLAUDE.md` 里「命令行 `--host`/`--port` 优先」
   的**意图**相反 —— 我在文档里把**实测真相**写清了（并标注与代码意图相反），
   但**没有改代码**：本波明确「不改行为」。修法很小（改判 `cmd.Flags().Changed` 或改用
   `cmd.Root().PersistentFlags()`），建议作为独立一格。
2. **`gofmt -l` 在 HEAD 上就列出 6 个文件**（`cmd/eval.go`、`cmd/root.go`、`cmd/snapshot.go`、
   `internal/form.go`、`internal/form_test.go`、`internal/snapshot_bdd_test.go`）—— 与 `3f70131`
   那条「修 gofmt 回归」是同一类，本波没碰（不在清单里，且会扩大 diff）。
3. **本波没做的事**（终审判为可随分支发布，照单遵守）：两处截断助手不合并、`loadPageModel`
   的判据不挪进 `internal`、合并后的 `OptionGroups`/`Obstructions` 不带帧归属、任何阈值/断言都没放宽
   （唯一的收紧是 `TestObserveLightDOM` 里的 `Errorf`→`Fatalf`，那只会让空转更早暴露）。
