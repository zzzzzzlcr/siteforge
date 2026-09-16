# 产出闭环与调试契约 — 实施计划（计划二）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** siteforge 能**产出一条真能跑的 py**，且那条 py 跑起来**能被看见**（`--trace` / `--stop-at`）。

**Architecture:** `tools/cdp`（计划一交付）出 MCP 门给 agent 调；agent 在 LangGraph 图里用真浏览器探索、按**固定骨架**起草 py、过 lint、过**扰动自测**；产物是 cdp-first 的 py，能写 trace、能在第 N 步停下。

**Tech Stack:** Go 1.26（`cmd/mcp`，与 CLI 同内核）· Python 3（LangGraph / FastAPI，容器里已有）· MCP（stdio）· 真浏览器（Bit 窗口）

**Spec:** `docs/superpowers/specs/2026-09-16-siteforge-design.md`
（本计划 implements：§4.1 同内核双出口 · §5.1 骨架固定 · §5.1c 调试契约 · §5.2 契约检查器 · §6 图 · §10 扰动测试）

## Global Constraints

以下每条都来自规格，**每个任务的要求都隐含包含本节**：

- **动作一律走 cdp 命令，不手拼 JS**（D8/§5.2）。读可以用 JS，写不行。
- **产物必须是 cdp-first**：`self.cdp.form(...)` / `self.cdp.click(...)` / `self.cdp.scroll(...)`；`eval` **只准用来读**。
- **`observe` 给感知不给判断**（D11）：原始事实，不是语义结论。
- **人每一步都在**（§6.2/D16）：agent 的每一步都要**可被暂停**、**可被人纠正**。
  ⚠️ 这不是「以后加 UI」——它是架构约束：**任何一步都不许设计成「不可打断、跑完才汇报」**。
- **给非技术人员看的文字必须是人话**（D16）：不是错误码、不是选择器。
- **agent 用自己的 Bit 窗口**（D6）+ **自己的 gost 端口**（D9），绝不与 worker 共用。
- **开工前提层**（§4.6）顺序不能反：拉链 → `POST /browser/update` → `bit.sh open`。
  **不要用 `bit.sh update`**（它是残缺包装，收 10 个参数只下发 4 个）。
- **DPR 字段名是 `devicePixelRatio`** —— 写 `dpr` 会被静默忽略（本项目已被坑过一次，点击偏移 3 倍）。
- 测试必须进 git；**不得新增对 `localhost:8080` 的依赖**（自带 fixture）。

### ⚠️ Task 1 spike 实测出的三条（2026-09-16，**不是推测，是量出来的**）

**C0 —— 模型锁定 `deepseek-v4-flash`**（用户 2026-09-16 定）。不要换成 `pro`；
spike 的所有实测都是在 flash 上量的，换模型等于换掉那些结论的前提。

**C1 —— `max_tokens` 必须 ≥ 12000。** `deepseek-v4-*` 的**思考 token 算进 `max_tokens`**：
24 次运行里 **2 次最终答案不完整**（1 次全空、1 次截半句），`reasoning_tokens` 吃满 4000。
**这正是 2026-09-16 那个坑**（`v4-pro` 800 预算返回空），换 `flash` **仍复现（1/8）**；
提到 12000 后 **5/5 正常**。
> 别把这条当调参 —— 它会让**最终答案静默变空**，而空答案看起来和「模型说没有」一模一样。

**C2 —— 工具循环必须处理「一轮里多个 `tool_calls`」。** 模型会把 3 次 `observe` **并进同一轮**；
只处理 `tool_calls[0]` 的实现会**静默丢掉观测**。

**C3 —— 终止条件要认「没有 `tool_calls`」**，不能只认 `done()` ——
spike 里 `done()` 24 次只被调了 6 次。

---

## ⚠️ 开工前必须先验的一件事（Task 1）

**本计划整个建立在「LLM 能可靠地做工具调用」上，而这一点本项目从未验证过。**

2026-09-16 只验过两件：
- `deepseek-v4-flash` **能看图**（数矩形 → `2`、认颜色 → `红色`）✓
- `deepseek-v4-pro` 在 800 token 预算下看图返回**空**（reasoning 吃满）✗

**没验过的是「多轮工具调用」**：模型能不能稳定地「调 observe → 读结果 → 决定下一步 → 再调」，
而不是编一个答案。**这是本设计的生死判据**（规格 §1.1：规则折叠死掉正是因为它在盲猜页面）。

**所以 Task 1 是一个 spike**：拿真页面跑最小工具循环，量出它能撑几轮、什么时候开始瞎编。
**若 Task 1 失败，后面的任务全部作废** —— 先回来改设计，别再往下做。

---

## File Structure

```
tools/cdp/
  cmd/mcp/main.go              ← 新建：MCP server（同一内核，不包壳调 CLI）
  internal/mcp/                ← 新建：工具注册与参数校验（与传输解耦，便于测）
  internal/mcp/*_test.go       ← 新建
agent/
  service.py                   ← 新建：FastAPI，Debug Console 的后端（本计划只做健康检查 + 任务提交）
  llm.py                       ← 新建：LLM 客户端 + 工具循环（Task 1 spike 的产物）
  tools.py                     ← 新建：MCP 工具的白名单与调用封装
  template.py                  ← 新建：py 产物骨架（固定部分）
  lint.py                      ← 新建：契约检查器（§5.2）
  selftest.py                  ← 新建：扰动自测（§10）
  graph.py                     ← 新建：LangGraph 图
  state.py                     ← 新建：图的状态定义
tests/                         ← 每步的测试（**必须进 git**）
fixtures/                      ← 自带 fixture（**不得依赖 localhost:8080**）
```

**为什么 `internal/mcp` 单独一层**：工具**注册与参数校验**和**传输**（stdio）是两件事。
分开后「哪些工具存在、参数怎么校验」可以单测，不用起 MCP 连接。

---

## Task 1: Spike —— LLM 能不能撑起工具循环（**先做这个，失败就停**）

**Files:**
- Create: `agent/llm.py`, `agent/tools.py`
- Test: `tests/test_tool_loop.py`（打真 LLM，**默认 skip，`RUN_LLM=1` 才跑**）

**Interfaces:**
- Produces: `llm.run_tool_loop(system, user, tools, max_rounds) -> list[dict]` —— 返回每一轮的消息；
  `tools.specs()` 返回 MCP 工具的函数式声明

- [ ] **Step 1: 写一个最小工具循环，接到真模型**

用**已经验过能看图**的 `deepseek-v4-flash`（`OPENAI_BASE_URL=https://api.deepseek.com`，
key 从容器 env 借：`docker exec auto-llm-script printenv OPENAI_API_KEY`）。

工具只给两个，够判定就行：`observe()`（打真页面）与 `done(answer)`。

- [ ] **Step 2: 跑一个真站，量三件事**

```bash
RUN_LLM=1 python3 -m pytest tests/test_tool_loop.py -v
```

拿 `compareinsulation.io` 的漏斗页（计划一真站验过、本地 Chrome 打得开）问它：
「这一页有没有一个叫 `Get Started` 的按钮？用工具确认，别猜。」

量：
1. **它会不会真的调工具**（还是直接编答案）
2. **能连续调几轮**不出错
3. **给它的 observe 输出里有一个「陷阱字段」（`left:-9983px`）时，它会不会去点它**

- [ ] **Step 3: 把结论写进报告；若失败则停下**

**判据**：能连续 ≥3 轮正确调工具、且**明确指出「那个字段在屏幕外」**。
做不到 → 报 `BLOCKED`，**不要再往下做任何任务**。

- [ ] **Step 4: 提交**

---

## Task 2: `cmd/mcp` —— 同内核的 MCP 门

**Files:**
- Create: `tools/cdp/internal/mcp/registry.go`, `tools/cdp/internal/mcp/registry_test.go`
- Create: `tools/cdp/cmd/mcp/main.go`
- Modify: `Dockerfile`（补回 stage-1 的 `cdp-mcp` 构建行 + stage-2 的 COPY + chmod，共 4 处，见 `tools/cdp/CLAUDE.md` 的 Task 8 清单）

**Interfaces:**
- Consumes: `internal.Client` 的 `ObserveAll` / `Observe` / 既有的 form/click/scroll（计划一）
- Produces: `mcp.Tools() []Tool`；每个 `Tool{Name, Description, Schema, Handler}`

**关键**：**直接调 `internal` 包**，不 subprocess 调 CLI（§4.1）。工具清单见规格 §4.2。

### ⚠️ 它连**哪个**浏览器 —— 这条不能漏（用户 2026-09-16 指出）

**实战上浏览器不是本机 9222，是 Bit 窗口**（带代理与指纹的那套）。链路：

```
bit.sh open <worker_ip> <bit_id>  →  ws://<worker_ip>:<port>/devtools/browser/<uuid>
        ↓ 取 scheme 后、第一个 "/" 之前那段（生产 py 库 CDPHelper._parse_ws_url 就是这么做的）
   host=<worker_ip>  port=<port>
        ↓
   internal.NewClient(host, port)  →  http://host:port/json/version → webSocketDebuggerUrl
```

**所以 `cdp-mcp` 必须接受浏览器目标，两个参数都要收**：

- `--host` / `--port`（与 CLI 同一套 flag，含 `CDP_HOST`/`CDP_PORT` 环境变量回退）
- **`--ws-url <url>`** —— 直接吃 `bit.sh open` 吐出来的那个串，**省掉调用方自己拆 host/port 这一步**
  （少一步转换 = 少一个搞错的机会；拆分逻辑照 `CDPHelper._parse_ws_url`，逐字对齐）

**并且**：窗口存活只有几分钟（§4.6）。窗口没了的时候要**明确报错**（「连不上 <host:port>」），
**不许挂着** —— agent 拿到明确错误才能重开窗口。

**这一条的测试要有反向钉子**：起两个浏览器（或两个端口），传非默认的那个，
断言它连的**确实是那个** —— 因为本项目刚在 `--host/--port` 上栽过：
env 会静默盖掉显式 flag（C81），而「连错了浏览器」正是那类**不报错的错**。

- [ ] **Step 1: 写失败测试（工具表与参数校验）**

覆盖：七个工具都在（`observe`/`diff`/`screenshot`/`click`/`form`/`scroll`/`goto`）；
`observe` 的 schema 里**没有** `intent`/`importance` 这类语义参数（D11）；
`observe` 的描述里写明「只给感知」；未知工具名报错而不是静默成功。

- [ ] **Step 2: 跑测试确认红** → `undefined: mcp.Tools`

- [ ] **Step 3: 实现 registry + stdio server**

- [ ] **Step 4: 手工握手验证**

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | ./cdp-mcp | head -5
```

预期：返回工具列表（含 `observe`），**输出干净**（没有 Go 日志混进 stdout —— stdio 传输里那是致命的）。

- [ ] **Step 5: 提交**

---

## Task 3: py 产物骨架（模板固定，agent 只填「怎么走」）

**Files:**
- Create: `agent/template.py`, `tests/test_template.py`
- Create: `fixtures/reference_site.py`（模板渲染出的参考产物，供后续任务消费）

**Interfaces:**
- Produces: `template.render(site, success_text, states, fills, provenance) -> str`（完整 py 源码）

**骨架必须已有的东西**（规格 §5.1/§5.1c）：

- CLI：`--ws-url --form-file --correlation-id --log-level --task-id`（生产契约，逐字对齐 `forms/sites/*.py`）
- `sys.path.insert(0, dirname(dirname(abspath(__file__))))` + `from common import CDPHelper, setup_logger, report_url`
- `sys.exit(0 if f.run() else 1)`
- `PROVENANCE = {...}`（§5.3）
- **`--trace` / `--stop-at`**（§5.1c）

- [ ] **Step 1: 写失败测试**

断言渲染出的源码：`ast.parse` 通过（语法）；含四个 CLI 参数；`from common import` 那行逐字一致；
含 `PROVENANCE`；`--trace` 与 `--stop-at` 在 `add_argument` 里；`sys.exit(0 if ...)`。

⚠️ **并且加一条 Task 0 级断言**：**`import` 得动**。计划一在 `py_emitter` 上发现的三个断口里，
第一个就是「JSON 字面量导致 `import` 就 `NameError`」而 `ast.parse` **查不出来**
（`false` 在语法上合法）。所以：**把渲染产物写进临时文件、真 `import` 一次**
（`importlib.util.spec_from_file_location`），断言不抛异常。

- [ ] **Step 2: 跑测试确认红**

- [ ] **Step 3: 实现模板**

- [ ] **Step 4: 跑测试确认绿 + 渲染参考产物**

- [ ] **Step 5: 提交**

---

## Task 4: 契约检查器（lint）—— 把手拼 JS 变成机器可打回的约束

**Files:**
- Create: `agent/lint.py`, `tests/test_lint.py`

**Interfaces:**
- Produces: `lint.check(src: str) -> list[Violation]`；`Violation{line, code, message, snippet}`

**判据**（规格 §5.2）：产物里出现下列手拼填充/点击 → **打回**：

| 违规 | 为什么 |
|---|---|
| `dispatchEvent(new Event('input'` / `'change'` | 合成事件站点 React 常不认 |
| `Object.getOwnPropertyDescriptor(...'value')` | 手拼 native setter |
| `.click()` 形式的 JS 点击 | 不是拟人手势 |
| `e.value = ` / `.value=` 形式的直接赋值 | 绕过 cdp 的拟人键入手势 |

> ⚠️ **预检修正（S1）**：原表里有一条「**裸 `document.querySelector` 出现在写路径**」——
> **删掉了**。理由：**「读路径 vs 写路径」在静态上不可判定**，而 `querySelector` **只是查找**，
> 读写都用它 —— 拿它当违规会**误伤合法的读**（产物里的 `cdp eval` 读页面本来就要用）。
> 判据要盯**写动作本身**（上面四条），不要盯查找。

- [ ] **Step 1: 写失败测试（每条违规一个用例 + 合规产物不误报）**

**关键**：必须有**负例** —— 一份合法的 cdp-first 产物跑 lint **必须零违规**。
只测「抓到了」会放过「什么都抓」。

- [ ] **Step 2: 跑测试确认红**

- [ ] **Step 3: 实现 lint**

- [ ] **Step 4: 跑测试确认绿 + 对 Task 3 的参考产物跑一次（必须零违规）**

- [ ] **Step 5: 提交**

---

## Task 5: 浏览器 Agent 的工具循环（ReAct over MCP）

**Files:**
- Create: `agent/browser_agent.py`, `tests/test_browser_agent.py`
- Modify: `agent/tools.py`（接真的 MCP 客户端）

**Interfaces:**
- Consumes: Task 1 的 `llm.run_tool_loop`、Task 2 的 MCP server
- Produces: `browser_agent.explore(url, goal, budget) -> Journey`；
  `Journey{steps: [{state, action, target, result, note}], notes: [str]}`

**每一步都要可被打断**（§6.2）：循环的每一轮前后都检查 `should_pause()`，
**不许写成「跑完 30 轮再汇报」**。

⚠️ **spike 没验到的一件事（它自己承认的）**：`observe` 是只读的，模型有理由**并行批量调**。
spike 测到的「连续 ≥3 轮」是**同一件事看了三遍**，**不是依赖链**。
`click` / `form` 会**改页面**、天然不能批量 —— 所以**你的循环是本项目第一次真的走依赖链**。
→ 本条任务的 e2e **必须**包含「点一下 → 再 observe → 看到页面变了」这种**真依赖链**，
不能只重复调 observe。

- [ ] **Step 1: 写失败测试（用桩 MCP，不打真浏览器）**

覆盖：预算到顶会停；`should_pause()` 为真时**在下一轮之前**退出（不是跑完）；
每步都记进 `Journey`；工具报错时它**如实把错误记进那一步**，不吞。

- [ ] **Step 2: 跑测试确认红**

- [ ] **Step 3: 实现**

- [ ] **Step 4: 跑测试确认绿**

- [ ] **Step 5: 提交**

---

## Task 6: 扰动自测（§10）

**Files:**
- Create: `agent/selftest.py`, `tests/test_selftest.py`

**Interfaces:**
- Produces: `selftest.run(py_path, ws_url, form_file, site) -> Report`；
  `Report{runs: [{name, ok, failed_step, trace_path}], passed: bool}`

**扰动序列**（规格 §10，**不是同环境跑 3 遍**）：

| Run | 扰动 | 打的是什么 |
|---|---|---|
| 1 | 正常 | 基线 |
| 2 | 刷新页面后重跑 | 状态残留 / 首次加载假设 |
| 3 | 注入延迟（每步 +2s） | 时序竞争 |
| 4 | 换 viewport | 折叠/遮挡/坐标假设 |
| 5 | 换代理国家 | 地区内容差异（**这条要重新拉链，成本高；先跑 1–4，第 5 遍作为可选**） |

- [ ] **Step 1: 写失败测试（桩 subprocess）**

覆盖：五遍都过 → `passed=True`；**任一遍挂 → `passed=False` 且指出是哪一遍、卡在第几步**
（⚠️ 不许把「某遍挂」吞成「部分通过」—— 那是本项目最忌讳的那类谎）。

- [ ] **Step 2: 跑测试确认红** → **Step 3: 实现** → **Step 4: 确认绿** → **Step 5: 提交**

---

## Task 7: LangGraph 图（把上面几件串起来，含人可暂停）

**Files:**
- Create: `agent/state.py`, `agent/graph.py`, `tests/test_graph.py`
  （⚠️ **预检修正（S2）**：`agent/service.py` 归 Task 8，本任务不建 —— 原先两条都写它）

**Interfaces:**
- Consumes: Task 1/3/4/5/6 的全部
- Produces: `graph.build() -> CompiledGraph`；节点
  `intake → explore → draft → lint → selftest → deliver`，加 `diagnose` 回灌

**三处必须写对**（都是今天实证出来的）：

1. **人不是最后一道关**（§6.2）：图要能停在**任意两个节点之间**等人。
   用 LangGraph 的 `interrupt`，**checkpointer 用 Postgres**（中断后要能恢复）。
2. **`lint` 不通过 → 回 `draft` 带违规行**；`selftest` 挂 → `diagnose` → 回 `draft` 带证据。
   两条回灌都要有**硬上限**，不能无限循环。
3. **`deliver` 写出的 py 必须带 `PROVENANCE`**（§5.3）—— 环境指纹随产物落盘。

- [ ] **Step 1: 写失败测试（桩掉所有外部依赖）**

覆盖：happy path 走完；lint 挂 → 回 draft 且**带上了违规行**；
selftest 挂 → 走 diagnose 且**带上了 failed_step**；预算耗尽 → 停，不无限循环；
`deliver` 的产物含 `PROVENANCE`。

- [ ] **Step 2: 跑测试确认红** → **Step 3: 实现** → **Step 4: 确认绿** → **Step 5: 提交**

---

## Task 8: `agent/service.py` —— 最小可用服务 + 真站端到端

**Files:**
- Create: `agent/service.py`（FastAPI）, `tests/test_service.py`
- Modify: `entrypoint.sh`（若路由有变）

**Interfaces:**
- Produces: `POST /run {url, goal, mode}` → `{job_id}`；`GET /job/{id}` → 状态与结果

> **本计划只做后端**。Console 的页面、框选、patch 是**计划三**。

- [ ] **Step 1: 写失败测试（TestClient，桩掉图）**

- [ ] **Step 2: 跑测试确认红** → **Step 3: 实现** → **Step 4: 确认绿**

- [ ] **Step 5: 真站端到端（**本计划的验收**）**

```
拿 homebuddy（非 shadow 站，描述现成 /tmp/desc_hb.txt）
  → POST /run
  → 等它产出一条 py
  → 手工跑那条 py --trace /tmp/t.json
  → 断言：trace 里每一步都有 step/action/target/ok/note
  → 手工跑 --stop-at 3 → 浏览器停在那一页没被关
```

**判据**：**一条真能跑的 py + 一份能读的 trace**。这是本计划的唯一交付判据。

- [ ] **Step 6: 提交**

---

## 预检扫描（2026-09-17，派发 Task 4–8 之前）

对**还没派的任务**做的一次冲突扫描（计划一那次扫出 9 个、其中一个会直接卡死，所以这是常规动作）。

| # | 发现 | 裁定 |
|---|---|---|
| **S1** | Task 4 的 lint 规则「裸 `document.querySelector` 出现在**写**路径」—— **静态不可判定**；而且 `querySelector` 只是查找，**读写都用**，拿它当违规会**误伤合法的读** | **删掉那条**，判据改盯**写动作本身**（四条写模式）。已改 |
| **S2** | **Task 7 与 Task 8 都创建 `agent/service.py`** | 归 Task 8；Task 7 只建 `state.py` + `graph.py`。已改 |

**扫描过、确认没问题的**：
- 各任务的**文件清单两两不重叠**（除已修的 S2）—— Task 3 的 `fixtures/` 只被 Task 4 读，不被写
- Task 5 修改 Task 1 的 `agent/tools.py` ✓ 是**有意的**（spike 的桩接成真 MCP）
- Task 6 的「换代理国家」那遍扰动需要重拉链，计划已标为**可选**（先跑 1–4）✓
- 没有任务再引用已删除的 `.superpowers/` workspace ✓

**并发期的已知折扣**（实测发生）：同一 Go module 里并发跑 `go test ./...` 会看到**对方半成品**的代码
（一次红是因为另一个 agent 的 `cmd/mcp_e2e_test.go` 正在写）。→ **「测试绿」在并发期要打折扣，
等所有 agent 停下再跑一次才算数。**

## 后续计划（不在本计划内）

| 计划 | 范围 | 验收 |
|---|---|---|
| **计划三：Agent Debug Console** | 运营入口页面（截图对比 + 一轮一句话）· 框选元素 · patch(=py diff) → 验证 → 合并 · HITL 界面 | **运营对着一次失败，自己把站修好** |
| **计划四：记忆层与覆盖度** | `site_memory` · `correction` 采集（`siteforge correct`）· JSON 修复入口 · **路由表/注册（C15 —— 没有它产出的 py 调不起来）** | 同类站复用得上；JSON 挂掉的站也能修 |

**依赖**：计划三依赖本计划交付的 `--trace`；计划四依赖计划三的 correction 出口。

---

## 已知风险（本计划内必须记住）

| # | 风险 | 处置 |
|---|---|---|
| **P1** | **LLM 工具调用的可靠性从未验证** —— 整个计划建在它上面 | **Task 1 先验，失败就停** |
| P2 | agent 要自己的 Bit 窗口与 gost 端口，**这两个都还没指定**（规格 R8/R10） | Task 1 之前要拿到 |
| P3 | 工具层还有两个已知缺口：Tailwind 类 quiz 认不出选项组（R20b）、`occluded_by='offscreen'` 一值三义（R21）| 它们的**消费者是 agent**，会在 explore 阶段现形 —— 遇到就回来补，别绕 |
| P4 | 蜜罐修复 / 截图能力**正在单独落地**，不是本计划的任务 | 开工前确认它们已合并（`git log --oneline \| grep -E "蜜罐\|screenshot"`）|
| P5 | ~~成本~~ → **改为：预算的目的是防跑飞，不是省钱**（用户 2026-09-16 定） | 图里每一环仍要有上限，但**理由是「别无限循环」**，不是「省 token」。**不要为了省钱牺牲能力**（砍轮数、砍上下文、缩小 max_tokens）—— 那会把 Task 1 验出来的能力又削掉 |
| **P6** | **Bit 窗口存活只有几分钟**（§4.6），而一次 explore 可能跑很久 | 图要把「窗口还活着吗」当成**可失败的前置**；窗口没了要能**重开并从断点继续**，而不是整轮重来 |
| **P7** | agent 用的 Bit 窗口 / gost 端口**都还没指定**（R8/R10） | Task 1 之前必须拿到 —— 没有它，Task 1 的 spike 只能用本机 9222，测不到真实链路 |
