# 最小可视界面 —— 「一个人盯着一趟运行」的活回路（计划三第一片）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 一个人坐在屏幕前盯着一趟真运行 —— **看得见它在做什么、为什么**（时间线：它的动作 / 它自己的话 / 系统的解释），
**随时说得上话**（input 常开：闸上回话、探路里下一轮直达、别处排队），**随时停得下来**（落点诚实），
而且**没有任何一件系统已经知道的事是「发生了但没人说」**。

**Architecture:** 后端仍然**不需要新的图逻辑**（`interrupt` 已经在每个节点之前停下来、`/reply` 已经能带话）。
要加的是**四根线**：① 一条**持久到进程里的时间线**（`Job.events` + 一个唯一的写入口 `narrate`）；
② **长节点的叙述钩子**（探路的 `on_step` 本来就有、`on_note` 新加；自测加 `on_run`）——
它们是「屏幕不会看起来冻住」的正解；③ **常开输入**（`/say` 排队、探路里直达；`/stop` 一个布尔 + 诚实的落点；
`/again` 同一份开场白重来）；④ 截图与前一轮那套（闸拍 + 可疑步的点前/点后）。
新文件：`shots.py` / `events.py` / `rounds.py` / `console.html`。

**Tech Stack:** Python 3（FastAPI / LangGraph，镜像里已有）· cdp CLI 与 MCP 门（同一 Go 内核，镜像里已有）· 原生 HTML/JS（无框架、无打包、无外部资源）

**Spec:** `docs/superpowers/specs/2026-09-17-console-minimal-design.md`（v2）
—— 边界来自账本 `R-41`，**验收被 2026-09-17 用户看完一次真运行之后重写**（设计注 §零 / §1.3）

## Global Constraints

以下每条都来自规格或本片的边界，**每个任务的要求都隐含包含本节**：

- **给运营看的每一个字段都是人话**（D16）。选择器（`#submit1`）、`observe` 的 JSON、错误码、
  149 个元素的表 —— **一个都不进主视图**（§6.6 的「反面」那一句）。
- **不编话**：时间线上每一句、卡片上每一句，只从它该来的地方来（闸口原话 / `journey` 的 `note` /
  `report.summary()` / `end_note` / 服务自己的 `narrate`）。没有那句话就写「这一步没说它做了什么」。
- **⚠️ 没有静默的路径**（这一条是本片的核心）：任何一个 `except`、任何一个 `skipped`、
  任何一个 `why` 字段，都**必须**有一条对应的时间线事件（目录表：设计注 §3.2）。
  **没有事件的异常 = 这个任务没做完。**
- **服务可以替人「停」，绝不替人「走」**：停 = 阻止工作（安全，可以自动送达）；
  替人回一句「继续」= 一步在没人看着的情况下跑掉（`c998a72` 那次事故）—— **任何路径都不许自动继续**：
  不因为超时、不因为排队、不因为「看起来没人管」。排队的话**只预填，不自动发**。
- **插话只做在探路**（那里有「下一轮」这个插入点）；别的节点说了就排队，并且**明说**它现在到不了。
  一半的插话（说了没送到）比不做更坏 —— 人会以为自己已经纠正过了。
- **时间线是 process-local 的**：它随服务重启消失，`/live` 的 `note` 必须**明说**这一点；
  轮次与截图仍然从 checkpoint 投影得出来（R-19）——「时间线是现场，轮次与截图是档案」。
- **字节不进 JSON**：`/live` 只带文件名。图片走 `/job/{id}/shot/{name}`，名字白名单 + 解析后必须落在
  `<shots_root>/<job_id>/` 之内（路径穿越是这个端点唯一的真风险）。
- **拍图 / 插话 / 停，都不许把图搞挂**：抓拍失败 = 记一句人话 + 一条事件，**绝不**抛到 `_advance` / 探路的路径上。
- **零构建步骤、零外部资源**：不加 npm / 打包 / CDN / 外部字体。运营的浏览器在内网里，出网要过代理。
- **活窗口那块只摆「事实 + 方法」，不编 URL**（设计注 §十）；「**跑的时候不要动手**」那张表**原样上页面**。
- **不动 `/job/{id}` 的既有形状**（别的调用方在读，`tests/test_service.py` 钉着）；**不动账本**；
  **不动 `tools/cdp`**（Go 侧一个字节都不改）。
- **单飞不变**（D6）：新加的动作都不许在两次 `invoke` 之间并发碰窗口 —— 拍图在图停下之后；
  插话只是往内存队列放一个字符串；停只是一个布尔。
- **测试必须进 git**；不依赖 `localhost:8080`（用 `TestClient`）、**不写 `runtime/`**（用 `tmp_path`）。

---

## File Structure

```
agent/
  shots.py           ← 新建：截图落盘（机制）—— 两条路（MCP 工具 / cdp CLI），**不返回字节**
  events.py          ← 新建：时间线的存（append-only + 上限 + 一个写入口），**只管形状与人话纪律**
  rounds.py          ← 新建：checkpoint → 轮次卡片（纯函数，可单测）
  console.html       ← 新建：单页 Console（时间线 + 卡片 + 常开输入 + 停 + 「这个窗口」；零构建）
  browser_agent.py   ← 改：动页面动作拍点前 / 可疑步补点后；`on_note`（它自己说的话接出来）
  selftest.py        ← 改：`on_run`（每一遍跑完当场说一句）
  llm.py             ← 改：`run_tool_loop(steer=…)` —— 人的话在下一次模型调用之前插进去
  service.py         ← 改：时间线的所有触发点 + `/live` `/runs` `/shot` `/say` `/stop` `/again` + `/console`
tests/
  test_shots.py            ← 新建
  test_agent_shots.py      ← 新建（探路抓拍策略；**不动 test_browser_agent.py**）
  test_service_shots.py    ← 新建（闸拍 + 图片端点；**不动 test_service.py**）
  test_events.py           ← 新建（时间线的形状与上限）
  test_service_events.py   ← 新建（**目录表逐条**：每个已知事实都有一条事件；没有静默路径）
  test_service_narration.py← 新建（长节点的钩子接到时间线：`on_step` / `on_note` / `on_run`）
  test_rounds.py           ← 新建
  test_service_input.py    ← 新建（`/say` `/stop` `/again` 的语义与边界）
  test_steer.py            ← 新建（插话进探路）
  test_console_page.py     ← 新建
  test_console_e2e.py      ← 新建（真 HTTP + 桩图：看 → 说 → 停 → 再看）
```

**为什么新测试一律新建文件**：`agent/service.py` 与 `agent/browser_agent.py` 最近一直在被改
（见「预检扫描」P1/P2），对应的两个测试文件也在动。新建文件 = 冲突面降到零。

---

## Task 1: `agent/shots.py` —— 截图落盘（机制）+ **量一张图多少钱**

**Files:**
- Create: `agent/shots.py`, `tests/test_shots.py`

**Interfaces:**
- Produces:
  ```python
  DEFAULT_ROOT = <repo>/runtime/shots          # 可被 SITEFORGE_SHOTS_DIR 覆盖
  def dir_for(job_id: str, *, root=None) -> pathlib.Path      # <root>/<job_id>/，按需 mkdir
  def name_ok(name: str) -> bool                              # ^[0-9A-Za-z._-]{1,64}\.png$
  def capture_via_session(session, dest) -> tuple[str|None, str]   # (文件名, '') 或 (None, 为什么)
  def capture_via_cli(ws_url, dest, *, cdp_bin=None, timeout=30.0) -> tuple[str|None, str]
  ```
- **契约**：两个 capture **都不抛**；返回**文件名**，**永远不是字节**；没成时第二项是**人话**。

- [ ] **Step 1: 写失败测试（全是桩，不开浏览器）**

覆盖：桩 session 返回 `{"png_base64": <1x1 PNG>}` → 落盘 + 返回文件名；桩 session 抛 `McpError` →
`(None, 人话)` 且**不抛**；返回 `{}` / 非法 base64 → `(None, 人话)` 且**不留半个文件**；
`capture_via_cli` 用假 `cdp`（写文件 + 退出 0 / 退出 1 / 不存在）三条；`ws_url` 认不出来 → `(None, 人话)`
且**不许**退回 `127.0.0.1:9222`（那是去拍**别的**浏览器）；`name_ok` 拒 `../x.png` / `a/b.png` / `x.txt` / 空。

- [ ] **Step 2: 跑测试确认红** → `ModuleNotFoundError: agent.shots`

- [ ] **Step 3: 实现**

- MCP 那条：`tools/cdp/internal/mcp/registry.go:356` 的 `screenshot` 返回
  `{png_base64, image_px, viewport_css_px, dpr}`，用 `base64.b64decode(..., validate=True)` 解。
- CLI 那条：`cdp --host <h> --port <p> screenshot --out <dest>`。
  ⚠️ 它**默认还往 stdout 吐裸 base64** → 起子进程时 `stdout=subprocess.DEVNULL`。
  `ws_url` → host/port 的拆法**照抄** `Service.live_viewport()` 与 `selftest._host_port()`
  （抽成一个共用小函数，别再写第三遍）。

- [ ] **Step 4: 跑测试确认绿**

- [ ] **Step 5: 在真窗口上量一张图多少钱**

对**已经打开的窗口**连拍 20 次，记中位数：

```bash
time cdp --host <worker_ip> --port <port> screenshot --out /tmp/shot.png   # 跑 20 次
```

**再折算成整跑的增量**（设计注 §5.5）：`~25 条命令/次运行`（闸拍 ≤12 + 动页面动作各 1 条 ≈10–15 + 可疑步少量）。

**判据**：中位数 **≤ 1.5s** → 主路（Task 2 按设计注 §5.4 做）；**> 1.5s** → **降级 B**：
把 `SITEFORGE_STEP_SHOTS` 的默认值改成 0（只留闸拍），**实测数与降级这件事要同时进三处**：
设计注 §5.5、交付报告、**页面上的 `shots_note`（一直显示，不许静默）**。

⚠️ 量不出来（没有真窗口 / 连不上）→ **报 BLOCKED 并挂起这一步**，不许拿本机 9222 的 headless 顶替，
也不许凭感觉挑一个数。

- [ ] **Step 6: 提交**

---

## Task 2: 探路时的步拍策略（`browser_agent`）—— 只留「不对劲」的那些步

**Files:**
- Modify: `agent/browser_agent.py`
- Test: Create `tests/test_agent_shots.py`

**Interfaces:**
- Consumes: Task 1 的 `shots.capture_via_session`
- Produces:
  - `explore(..., shots_dir=None, shooter=None)`（**只有给了才拍**；不给 = 今天的行为，一个字节不变）
  - `Journey.steps[i]` 新增 `shot_before` / `shot_after` / `shot_after_deferred`
    （前两个**与产物 trace 逐字同名**，`agent/template.py` 的 `_run_step`）
  - `Journey.shots_why: str`（最近一次没拍成的原因，人话）
  - `MUTATING = tuple(a for a in REPLAY_ACTIONS if a != "wait")`（**从既有常量推出来**）

**策略（设计注 §5.4）:**

```
只对 MUTATING（click / form / scroll / goto）拍
点前：动作**之前**拍一张                                  → 1 条命令（每个动页面动作都有）
点后：只在已经知道不对劲时 ——
        a) ok 为假            → 立刻补拍
        b) 做成了、但**紧接着**那次观测显示页面签没变 → 那一刻补拍（标 shot_after_deferred=True）
              ⚠️ **b) 只对 `click` / `goto` 成立** —— 签是 `body.innerText`，**填框/滚动都不改它**，
              拿它判那两个是**必然假阳性**（2026-09-18 实测：一趟全成功的漏斗因此留了 10 张，
              40 张上限被健康步吃掉）。表同产物侧的 `DIFF_JUDGES`，有防漂移哨兵盯着。
留：只有 a、b 留「点前+点后」；其余情况**把点前那张删掉**
锚点：`_Pages` 的 key（(url, title, page_text[:400])），与 when_holds / _applies 同口径
判据只在**紧接着**的那次观测上生效（click A → click B → observe 不认）
上限 MAX_KEPT_SHOTS = 40 张；到顶就不再拍，并往 journey.notes 写一句人话
```

⚠️ **点前那条命令是下限、省不掉**（动手之前不可能知道会不会出问题）；能省的只有**点后**。
⚠️ **b) 那张是随后补拍的**：签没变 ≠ 像素没变 → 必须标 `shot_after_deferred=True`，页面打一行小字。

- [ ] **Step 1: 写失败测试（桩 session + 桩 shooter，不开浏览器）**

覆盖：`observe`/`diff` 步**一次都不拍**；**跑顺**的 `click`（紧接着 observe 显示变了）→
两张都不留且**磁盘没留文件**；**没变** → 两张都留且 `shot_after_deferred is True`；
`ok is False` → 两张都留且 `shot_after_deferred is False`；`click A → click B → observe(没变)` → 都不留；
四个 MUTATING 动作参数化；到上限 → 不再调 shooter + `journey.notes` 有人话；
**shooter 抛异常 → 探路照常跑完**（最关键的一条）；`shots_dir=None` → shooter 一次都不调。

- [ ] **Step 2: 跑测试确认红** → **Step 3: 实现**（`dispatch` 里加围墙，**别**把字节塞进 `_summarize`）
  → **Step 4: 绿 + `SITEFORGE_STEP_SHOTS=0` 时一次都不拍** → **Step 5: 提交**

---

## Task 3: 服务侧闸拍 + 图片端点 + 两块接线信息

**Files:**
- Modify: `agent/service.py`
- Test: Create `tests/test_service_shots.py`

**Interfaces:**
- Produces:
  - `Service(..., shots_dir=None)` / `create_app(..., shots_dir=None)`（默认 `SITEFORGE_SHOTS_DIR` 或 `runtime/shots`）
  - `Job.pauses: int`、`Job.shot_notes: list[dict]`（`{n, name, why}`）
  - `Service._capture_pause(job)` —— `_advance` 末尾调一次，**不抛**、硬超时（默认 20s）
  - `GET /job/{job_id}/shot/{name}` → `image/png`
  - `Service.window_public()` → `{"worker","bit_id","api_port","how","when"}` 或 `None`
    （`how`/`when` 两句人话**写死在 py 里**，页面不自己编 —— 设计注 §十）
  - `Service.shots_note()` → `""` 或降级 B 的人话（`SITEFORGE_STEP_SHOTS=0` 时那句；
    实测数从 `SITEFORGE_SHOT_SECONDS` 读，没给就不吹具体数字）

- [ ] **Step 1: 写失败测试（TestClient + 桩图 + 桩窗口，`shots_dir=tmp_path`）**

覆盖：跑到第一道闸 → `pause-1.png` 存在、`pauses == 1`；`GET …/shot/pause-1.png` → 200 + `image/png` + 字节一致；
**负例（最关键）**：桩 `capture` 抛异常 → job **仍然 `waiting`**、闸还在、`why` 是人话；
**负例**：名字非法（`..%2F..%2Fetc%2Fpasswd`、`a.png/../../x.png`、`x.txt`）→ **404 + 人话 detail**；
**负例**：名字合法但文件不在 → 404 + 人话；**负例**：`ws_url` 为空 → 不拍、`why` 说清「还没开浏览器」，
**且不去拍 `127.0.0.1:9222`**；`_recover` 那条路的 job 也能拍（`pauses` 从 0 起，不炸）；
`window_public()`：接了窗口层 → 三样都在、`how` 含「那台机器」；`window=None` → `None`；
`shots_note()`：默认 `""`；`SITEFORGE_STEP_SHOTS=0` → 一句人话。

- [ ] **Step 2: 红** → **Step 3: 实现**（`_capture_pause` 放在 `_advance` 的 `invoke` **之外**、
  写锁已经放开的地方）→ **Step 4: 绿** → **Step 5: 提交**

---

## Task 4: 时间线（`agent/events.py`）+ 服务侧的「为什么」句子 + `/job/{id}/live` 骨架

**Files:**
- Create: `agent/events.py`, `tests/test_events.py`, `tests/test_service_events.py`
- Modify: `agent/service.py`

**Interfaces:**
- Produces（`agent/events.py`，**只管形状与上限**）:
  ```python
  WHO = ("agent", "system", "you")
  MAX_EVENTS = 2000
  class Timeline:                       # 线程安全的 append-only
      def add(self, kind: str, say: str, *, who="system", data=None) -> dict
      def all(self, limit=500) -> list[dict]
      def dropped(self) -> int          # 超上限丢掉了几条（要能说出来）
  ```
  事件形状：`{"n", "at", "kind", "who", "say", "data"}`；**`n` 单调递增**（排序用它，不用时间戳）；
  `at` 是本地 ISO 秒级时间戳。
- Produces（服务侧）:
  - `Job.timeline: events.Timeline`、`Service.narrate(job, kind, say, *, who="system", **data)`——
    **时间线的唯一写入口**（谁都别直接碰 `job.timeline`）
  - `Service._advance` 前后与各触发点上的 narration（见 Step 1 的目录表）
  - `GET /job/{job_id}/live` —— **先出骨架**：`job_id / status / say / delivered / stage / note /
    events / gate（先恒 null）/ input（先恒 {mode:"queue", queued:[]}）/ stop / shots_note / window /
    rounds（先恒 []）/ truncated`

**⚠️ 这一节是这一片的核心 —— 目录表逐条落地，一条都不许漏：**

| # | 触发点（代码里的位置） | 事件 `kind` | `say`（人话，写死） |
|---|---|---|---|
| 1 | 每次 `_advance` 返回之后：`_window_is_gone(ws_url)`（**已有这个方法**）从「不是没了」翻成「没了」 | `window_died` | 「窗口没了 —— Bit 的窗口只活几分钟。它停在「%s」之前；接着走之前得先重开一个窗口。」 |
| 2 | `Service.reopen()`（成功之后） | `window_reopened` | **照搬它现有的两句 `say`**（探路重跑 / 从断点接着跑，两句不同） |
| 3 | `start()` → `_submit` | `queued` / `submitted` | 「收到了，排队开跑。」/「排队等窗口（前面还有别的 run 在用）。」 |
| 4 | `_advance` 拿到 job（`status=running`） | `running` | 「在跑：真浏览器 + 模型，做完一步或需要你时就会停下来。」 |
| 5 | `_advance` 的 except | `failed` | 现有那句 + 原始错误进 `data`（**跑挂 ≠ 跑成**） |
| 6 | `_advance` 返回且快照里 `end_reason` 是终态 | `done` / `cap_hit` | 快照里的 `end_note`（**原话**；撞上限时前面加「撞上限了：」） |
| 7 | `_recover()` | `recovered` | 「（服务重启过：这个任务是从 checkpoint 里捡回来的）」 |
| 8 | `reply()` | `human_said` | 人的原话 + 「这句话会一路带进「写这一版 py」」 |
| 9 | `_capture_pause` 失败 | `shot_missing` | 「这一轮没留下图：%s」（why 原文） |

- [ ] **Step 1: 写 `tests/test_events.py`（形状与上限，纯单测）**

覆盖：`n` 单调递增（同一秒两条也不乱）；`who` 只认三个值（别的值直接抛）；空 `say` → 抛
（**人话纪律**：写不出人话说明还没想清）；超过 `MAX_EVENTS` → 丢最旧的且 `dropped()` 数得出来；
`all(limit)` 返回**最后** limit 条（不是最早）。

- [ ] **Step 2: 写 `tests/test_service_events.py`（目录表逐条 + 没有静默路径）**

逐条：窗口假死（桩窗口 `alive()` 从 True 翻 False）→ 有时间戳的 `window_died`；
`reopen` → `window_reopened` 且两句不同的话各自出现；桩图以 `lint_cap` 结束 → `cap_hit` 且带 `end_note`；
桩图抛异常 → `failed`；`reply` 带 note → `human_said` 含原话；**负例**：桩 `capture` 抛 → `shot_missing`。
**并且**一条机械断言：跑完一个桩 job 之后，`[e["kind"] for e in events]` 里
`{"submitted","running","window_reopened","human_said","done"}` 都在 ——
**任何一条静默的路径都会让这条测试红**。

- [ ] **Step 3: 红** → **Step 4: 实现 `events.py` + 服务侧九个触发点 + `/live` 骨架**

`narrate` 拿 `job.lock` 只包一次 append（回调从工作线程来）。`/live` 走 `_snapshot`（读连接），
**别**排在一次 `invoke` 后面。

- [ ] **Step 5: 测试绿 + 手工 `curl` 看一眼 `/live` 的 JSON** → **Step 6: 提交**

---

## Task 5: 长节点会说话 —— `on_step` / `on_note` / `on_run` 接到时间线

**Files:**
- Modify: `agent/browser_agent.py`（`on_note`）、`agent/selftest.py`（`on_run`）、`agent/service.py`（接线）
- Test: Create `tests/test_service_narration.py`

**Interfaces:**
- Produces:
  - `explore(..., on_note=None)` —— `journey.notes` 每追加一条就回调一次
    （**它自己的话**：`_Gate._note` 每轮记的 `AI 说：…` 从这里出来）
  - `selftest.run(..., on_run=None)` —— 每一遍跑完当场回调一次 `Run`（`status=skipped` 的也要回调）
  - 服务侧接线：`Service._explore_for(brief, job)` 传 `on_step` / `on_note`；
    包一层 `Deps.selftest`（`lambda py, ws, ff, site, **kw: selftest.run(..., on_run=cb, **kw)`）
    —— **不用改 `graph.py`**：`Deps` 本来就是给服务注入的
  - 事件 `kind`：`agent_step`（`who="agent"`，`say` = `step["note"]`）、`agent_said`（`who="agent"`）、
    `selftest_run`（`who="system"`，`say` = `Run.label` + `Run.note` 原文）、
    `shot_missing`（`who="system"`）——**步拍**没成的那条：在一次 `_advance` 返回之后扫一遍
    `journey.steps` 里新出现的 `steps_why`（`step["shots_why"]`，非空且没报过就报一次），
    否则设计注 §3.2 目录表第 6 行只剩闸拍那一半（**A11 会红**）

- [ ] **Step 1: 写失败测试（桩 explore / 桩 selftest 直接调回调）**

覆盖：桩 explore 调 `on_step({"note": "点了「Get Started」"})` → 时间线上有一条 `agent_step`；
调 `on_note("AI 说：先看看这一页")` → `agent_said`；桩 selftest 先回调一遍 `skipped` 的 `Run`
→ 时间线上出现「第 4 遍（换个窗口大小）**没跑**：…」**且带 note 原文**；
桩 journey 的某一步带 `shots_why="截图没成：连不上"` → 下一次 `_advance` 之后时间线上有一条
`shot_missing`，**且同一个 `why` 不会被报第二遍**；
**负例**：回调抛异常 → **探路/自测照常跑完**（同 Task 2 那条铁律）；
`on_step=None`（默认）→ 今天的行为一个字节不变。

- [ ] **Step 2: 红** → **Step 3: 实现（三处钩子 + 接线）**

⚠️ `_Gate._note` 里那一处要小心：它**每轮都在场**（包括被人打断的那条路），
所以 `on_note` 必须在**同一处**触发，别另找地方补 —— `journey.notes` 与时间线**不许有两份不同的真相**。

- [ ] **Step 4: 绿 + 一次真探路的观感检查**（时间线是不是真的在几秒内长出新行）→ **Step 5: 提交**

---

## Task 6: 轮次投影（`agent/rounds.py`）+ `/runs` + `/live` 的 `rounds`

**Files:**
- Create: `agent/rounds.py`, `tests/test_rounds.py`
- Modify: `agent/service.py`

**Interfaces:**
- Produces:
  ```python
  def project(values: dict, gate: dict|None, *, job_id: str, status: str, say: str,
              delivered: bool, pauses: list[dict], window: dict|None = None,
              shots_note: str = "", stage: str = "", max_rounds: int = 50) -> dict
  ```
  **纯函数**：输入是 checkpoint 的 values + `_project` 算好的闸 + 闸拍清单 + 接线信息；
  输出是设计注 §8.2 里 `rounds` 那部分 + `window` / `shots_note`。
  ⚠️ `window` / `shots_note` / `stage` 是**接线信息**，投影**原样带上、一个字不加工**。
- Produces（端点）：`GET /runs`（`{note, runs:[…]}`）；`/live` 里的 `rounds` / `gate` / `stage` 填上。

**配对规则（写死在 docstring 里）**：第 n 道闸上拍的那张叫 `pause-<n>`：

```
rounds[n-1].step = gate.step          rounds[n-1].now = pause-<n>
rounds[n-1].done.step = values["visits"][-1]
rounds[n-1].done.shots = (pause-<n-1>, pause-<n>)
rounds[0].done = None
```

⚠️ `gate` **只在 `waiting` 时非 null**（跑着/排队时是 `null`）—— 否则页面会显示一个「还能按」的按钮。
⚠️ `now` 与 `done.shots.after` 是同一张文件（一份字节两处引用），页面别显示两遍。

- [ ] **Step 1: 写失败测试（纯函数，喂真 `Journey` / 真 `Report`）**

覆盖：两个闸 → 两轮；`rounds[0].done is None`、`step=="intake"`、`now.name=="pause-1.png"`；
`rounds[1]` 的 `done.step=="intake"`、`done.shots=={"before":"pause-1.png","after":"pause-2.png"}`；
`revisable` 只在 `lint`/`selftest`/`deliver` 为真；`done.steps` 带 `note` + `shot_after_deferred`，
**不许**出现 `target` / 选择器 / 原始 result（拿字符串断言 JSON 里没有 `#` 开头的选择器）；
没有 journey 时 `steps == []` 且 `steps_note` 说清（**不许编**）；超 `max_rounds` → `truncated is True`；
`delivered` 与 `status` 两件事；`window=None` → 是 `null`；`shots_note` 非空时在顶层。

- [ ] **Step 2: 红** → **Step 3: 实现 `rounds.project`** → **Step 4: 绿**
  → **Step 5: 接两个路由（`/runs` + `/live` 填 rounds/gate/stage）+ 测试** → **Step 6: 提交**

---

## Task 7: `agent/console.html` —— 一屏：时间线 + 卡片 + 常开输入 + 停

**Files:**
- Create: `agent/console.html`, `tests/test_console_page.py`
- Modify: `agent/service.py`（`GET /console`；顺手 `GET /` → 302）

```
┌ siteforge 控制台 ─────────────────────────────────────────────────────┐
│ 运行（左）              │ 时间线（右，随时间往下长）                    │
│  ● job-9f3a… example…   │ 14:02:14 它   AI 说：先看看这一页有什么…      │
│    在跑 · 3 轮          │ 14:02:15 它   第 1 步：看了一眼页面            │
│  ○ job-71c2… blinkist   │ 14:02:22 它   第 2 步：点了「Get Started」     │
│    跑完了 · 没交付      │ 14:02:27 你   不是那个按钮，是下面那个         │
│                         │ 14:02:27 系统 你这句话已经交给它了 —— 下一轮… │
│                         │ 14:05:02 系统 窗口没了 —— Bit 的窗口只活几分钟…│
│                         ├──────────────────────────────────────────────┤
│                         │ 第 3 轮 · 打开浏览器探路        [现在这一页]  │
│                         │  它刚要做什么：…                              │
│                         │  ── 刚才那一步（intake）──   [点前] [点后]     │
│                         │  ▸ 探路里它走的每一步（30）                    │
│                         ├──────────────────────────────────────────────┤
│                         │ [写一句它该怎么做…            ] [继续] [说一句]│
│                         │ [ 停下，我要说一句 ]                          │
│                         │ 这个窗口：worker 192.168.1.222 · id 8f2c… · …  │
└────────────────────────────────────────────────────────────────────────┘
```

**页面规则（每条对着一条验收）:**
- **时间线是主视图**：每一条 = 时间 + 谁（它 / 系统 / 你）+ 人话；新的在下面，自动滚到底
  （**除非人往上翻了** —— 那时不抢滚动）。
- 按钮**跟着 `revisable` 改名**：`true` → 「打回带话」，`false` → 「说一句，接着走」（+ 一行小字）。
- input 的语义**跟着 `/live` 的 `input.mode` 走**：`gate`（回话）/ `steer`（下一轮直达）/ `queue`（排队），
  三种 placeholder 三句话 —— **页面不许自己猜**它在哪种模式。
- 「停」按钮：按下 → 禁用 + 显示 `/live` 给的 `will_stop_at` 那句话（**说清落在哪**）。
- 跑的时候：按钮禁用、卡片灰掉但**留在屏幕上**、秒数在走、**时间线继续长**。
- 图并排、点开看大图；缺图时显示 `why` **原话**；`shot_after_deferred` 的那张打「随后确认时补拍」。
- `shots_note` 非空 → 页面顶部一直显示。
- 「这个窗口」那块：三样事实 + 两句写死的方法；`window` 为 `null` 时明说看不到；
  **「跑的时候不要动手」不许在改版里丢掉**。
- `409` → **原样显示 detail** + 重拉 `/live`。
- **JS 做笨**：`job_id` + 一个定时器 + 一个渲染函数；取 JSON → 拼 DOM，**不持有状态机**。

- [ ] **Step 1: 写失败测试**

覆盖：`GET /console` 200 + `text/html` + `<title>`；页面里出现 `/live`、`/reply`、`/say`、`/stop`、`/shot/`、
三个动作的 id 与两种 `revisable` 文案；出现「这个窗口」「worker」「不要动手」三处字样（**A10 的自动化那一半**）；
**没有外部资源**（正则扫 `src="http` / `href="http` / `@import` / `<script src`，**A12**）；
`GET /` → 302 到 `/console`（可选，做了就测）。

- [ ] **Step 2: 红** → **Step 3: 实现页面 + 路由**（读文件返回；读不到 → 500 + 人话，别静默给空白页）
  → **Step 4: 绿 + 浏览器里手工开一次**（布局、图、按钮变灰、时间线滚动）→ **Step 5: 提交**

---

## Task 8: 人在回路 —— `/say`、`/stop`、`/again`

**Files:**
- Modify: `agent/service.py`
- Test: Create `tests/test_service_input.py`

**Interfaces:**
- Produces:
  - `POST /job/{id}/say {"text": "…"}` → `202 {queued|delivered, n, say}`
    （空文本 400；超长截断并**说出来**；`status` 终态 → 409 人话）
  - `POST /job/{id}/stop` → `{stop: {requested, where, will_stop_at, say}}`
  - `POST /job/{id}/again` → `{job_id, say}`（**新 job**：同一份 `job.brief` + 人说过的话）
  - `Job.inbox: list[dict]`、`Job.stop_requested: bool`、`Job.running_step: str`
  - `/live` 的 `input`（`mode` / `draft_note` / `queued`）与 `stop` 填上

**语义（设计注 §四.1，三行矩阵）:**

| 它现在在哪 | 停的落点 | 页面说什么 |
|---|---|---|
| 探路里跑 | **几秒内**（`should_pause` 每步/每轮之前查）→ 这一趟以 `paused` 收尾 | 「停下了 —— 探路的账本不完整，要接着做就得重来一次（`/again` 会带上你说的话）」 |
| 别的节点里跑 | **跑完这一步**，停在下一个闸口（那个闸口**本来就在等人**） | 「现在停不下来：它在「自测」里。**它做完这一步一定会停下来问你 —— 你什么都不用按。**」 |
| 已经在闸上 | **立刻** == `reply {"action":"stop"}`（这一步不做） | 「停下了。这一步没有做，页面与文件都是原样。」 |

⚠️ **不许自动继续**：排队的话**只预填**到下一道闸的输入框，**人按按钮才发**。
⚠️ 探路的 `should_pause` 由 `stop_requested` 驱动（**停是安全的**：它不让任何一步发生）。

- [ ] **Step 1: 写失败测试**

覆盖：`waiting` 时 `/say` → 排队 + 时间线 `human_said` + `input.queued` 里有它；
下一次 `/live` 的 `input.draft_note` 是**那句话**（**但不自动发** —— 断言 state 里没有新的 reply）；
`running` 且 `stage=="explore"` 时 `/say` → **Task 9 之前回 `queued`、之后回 `delivered`**
（两种都要测：`input.mode` 跟着变，页面文案跟着变）；
`running` 且 `stage=="selftest"` 时 `/stop` → `will_stop_at` 说「跑完这一步」+ 时间线一条
+ **不自动发任何 reply**；`waiting` 时 `/stop` → 等价于 `reply stop`（`end_reason == human_stop`）；
`/again` → 新 job_id、`brief` 与原来一致、**人说过的话带过去了**（进 `hints`）、时间线第一句说清这是重来；
**负例**：空文本 / 终态 job / 不存在的 id → 400 / 409 / 404，全部带人话。

- [ ] **Step 2: 红** → **Step 3: 实现** → **Step 4: 绿** → **Step 5: 提交**

---

## Task 9: 插话进探路（`steer` 通道）

**Files:**
- Modify: `agent/llm.py`、`agent/browser_agent.py`、`agent/service.py`
- Test: Create `tests/test_steer.py`

**Interfaces:**
- Produces: `llm.run_tool_loop(..., steer=None)` —— 在**每一次模型调用之前**调一次 `steer()`；
  返回非空就作为一条 `user` 消息插进对话（并在那一轮的 record 里留 `steered` 字段）；
  `explore(..., steer=None)` 透传；服务侧把 `job.inbox` 里没送出去的拿出来喂给它，送出去的标 `delivered`。

- [ ] **Step 1: 写失败测试（桩 LLM 客户端，不打真模型）**

覆盖：`steer()` 第一次返回一句话 → 那一轮的 `messages` 里有它（`role="user"`）且 record 记了；
`steer()` 返回 `None` → **一个消息都不插**（默认行为一个字节不变）；
插进去的那句话**必须带那句提醒**（「这是人插的话，接着探，别把它当成收尾」）—— 字符串断言；
`steer=None`（默认）→ 不调、不插。

- [ ] **Step 2: 红** → **Step 3: 实现（llm + explore 透传 + 服务接线）**
- [ ] **Step 4: 绿 + 一次真探路上的人插话演练（**这一条必须在真站上过一遍**）**

**判据**：插话之后 —— ① 它在下一轮里**看得到**那句话；② 它**没有**因此直接收尾；
③ 万一它收尾了，`journey.notes` 里有那句人话（「你插话之后它就收尾了 —— 看一眼它的结论对不对」）。

⚠️ **失败时的退路（写在计划里，别临时决定）**：如果真站上它频繁收尾（说明这个插入点会破坏循环），
**就把 `input.mode` 一直停在 `queue`**（页面文案跟着 `mode` 走，自动变成「会在它停下来时送到」），
把 steer 降级成「下一个版本再上」。**不许**留着一条「说了但没送到」的路还不告诉人。

- [ ] **Step 5: 提交**

---

## Task 10: 验收（本片的验收就在这儿）

**Files:**
- Create: `tests/test_console_e2e.py`

- [ ] **Step 1: 端到端（TestClient + 桩图 + 桩窗口，走完「看 → 说 → 停 → 再看」）**

```
POST /run                                  → 202 + job_id
GET  /runs                                 → 它在列表里（note 那句话也在）
GET  /job/{id}/live                        → status=running（或 waiting）；events 里已有 submitted/running
GET  /job/{id}/live                        → 第 1 轮：step=intake、done=None、now 那张图取得到；
                                              顶层 window 三样都在、shots_note=""
POST /job/{id}/say {"text":"…"}            → 排队（此刻没在等输入）→ events 里出现 who="you"
POST /job/{id}/reply {"action":"continue"} → 200；status 立刻是 running（**不是** waiting）
GET  /job/{id}/live                        → 第 2 轮出现；input.draft_note 是**我刚说的那句话**
POST /job/{id}/stop                        → will_stop_at 是一句人话；events 里有一条
GET  /job/{id}/live                        → done.shots.before/after 都能在 /job/{id}/shot/<name> 上取到
```

**判据**：一条请求都不用手工拼 cdp、不碰真浏览器、不碰真模型，而**时间线、轮次、图、人的话**四样都到位。

- [ ] **Step 2: 变异检查（这个仓库的文化：测试要能红）**

| 变异 | 必须红在哪 |
|---|---|
| 删掉任意一条 `narrate`（比如 `window_died`） | Task 4 的「没有静默路径」机械断言 |
| 让 `/say` 在闸上**自动**发出去 | Task 8 的「不自动发」断言 |
| 让抓拍异常往上抛 | Task 2 / Task 3 / Task 5 的三条「不影响主流程」负例 |
| 把 base64 塞回 `/live` | Task 6 的「JSON 里只有文件名」 |
| 把图片端点的名字校验删掉 | Task 3 的路径穿越用例 |
| 把 `revisable` 写死 `False` | Task 6 的 `lint/selftest/deliver` 用例 |
| 把「不要动手」那句从页面删掉 | Task 7 的文案断言 |
| 让 `input.mode` 由页面自己猜 | Task 7 的「page 只认 mode」断言 |

- [ ] **Step 3: 手工验收（**由用户/运营本人执行**，结果记进交付报告）**

按设计注 §十二的 **A1–A12** 逐条过，**且这一次必须由人自己回话**（不要 agent 代答 ——
上一次就是代答让整套 HITL 机制隐形了）。
⚠️ A10 需要配了窗口层的部署；没配时它只完成「明说看不到」那一半，**别拿本机 9222 冒名顶替**。

- [ ] **Step 4: 提交 + 交付报告（含 Task 1 量出来的那个数、Task 9 真站演练的结论）**

---

## 跨任务接口（写错一处就是漂）

### 1. 时间线的三个键（Task 4 定，Task 5/6/7 读）

`{"n", "at", "kind", "who", "say"}`；`who ∈ {agent, system, you}`；`n` 单调递增；
**唯一的写入口是 `Service.narrate`**（谁都不许直接 `job.timeline.add`，否则人话纪律会散）。
`say` 不许空 —— `events.py` 直接抛。

### 2. `Journey` 的三个新键（Task 2 定，Task 5/6 读）

`step["shot_before"] / ["shot_after"] / ["shot_after_deferred"]`（前两个与产物 trace 逐字同名）。
⚠️ **不许**把字节放进 `step`（`Journey` 要进 checkpoint；一次探路 30 步 × 500KB 会把它撑爆）。
账本里只有文件名，字节在盘上。

### 3. `/live` 的输入语义（Task 8 定，Task 7 显示，Task 9 扩展）

`input.mode ∈ {gate, steer, queue}` —— **页面只渲染，不推断**。
`gate`：这就是那道闸的回话（按钮可用）；`steer`：下一轮直达；`queue`：排队，到闸上预填（**不自动发**）。
Task 9 落地之前，`mode` 永远不出现 `steer`（退路见 Task 9）。

---

## 预检扫描（2026-09-17，写计划时对**在飞的工作**做的一次冲突扫描）

| # | 发现 | 裁定 |
|---|---|---|
| **P1** | 写计划时 `agent/service.py` 有**未提交的改动**（另一个 agent 的 Task 8 修复轮，随后落成 `c998a72`）。Task 3/4/6/7/8/9 **都改它** | **开工前先 `git status` 确认干净**，动手前重读 `_advance` / `_project` / `reply` / `create_app` 的现状。不要照这份计划里的行号去改 |
| **P2** | `agent/browser_agent.py` 最近一直在被改（R-35/R-37 那一支） | Task 2/5/9 改它：先读 `dispatch` / `_summarize` / `_Gate` / `Journey` 四处再动 |
| **P3** | `tests/test_service.py` 与 `tests/test_browser_agent.py` 可能正在被改 | 本片所有测试**新建文件**（见 File Structure） |
| **P4** | 产物的 trace 里**已经有** `shot_before` / `shot_after` / `shots_why`（`agent/template.py`） | `Journey` 用**同样的键名**与同一条诚实条款 |
| **P5** | `/job/{id}` 已被外部调用方读，`tests/test_service.py` 钉着它的形状 | **一个字段都不改**；新数据走 `/live` |
| **P6** | `graph.Deps.should_pause` 服务侧**没接**（`_build_graph` 只给了 `explore` 与 `set_viewport`） | Task 8 接上（`stop_requested` 驱动）。⚠️ 它是**「停下」信号**不是可恢复中断：探路被打断 = 这一趟 `paused` 收尾（设计注 §4.3） |
| **P7** | `runtime/` 不进 git，且 `runtime/selftest/` 已有别的写入者 | 截图放 `runtime/shots/<job_id>/`（同层不同目录）；测试一律 `tmp_path` |
| **P8** | `llm.run_tool_loop` 把工具返回值**整份**塞进 tool 消息（`agent/llm.py:183`） | 抓拍**一律走旁路**；模型自调 `screenshot` 的上下文账单不在本片（风险表 R2） |
| **P9** | **`Job.say` 是一次性的**：`_advance` 一返回就把它覆盖成 `DONE`/`""`，`reopen`/`_recover` 写的话**下一次就没了** | 这正是 Task 4 存在的理由：**凡是系统已经知道的事，写进 `Job.events`（append-only），别只写 `say`** |
| **P10** | `_Gate._note` 每轮都在场（包括被人打断的那条路） | `on_note` 必须挂**在同一处**（Task 5），别另找地方补 —— `journey.notes` 与时间线不许有两份真相 |

**扫描过、确认没问题的**：
- 本片**不动** `tools/cdp`（Go 侧一个字节不改：`screenshot` 命令与 MCP 工具都在镜像里）
- 本片**不动** `Dockerfile` / `entrypoint.sh`（页面与端点都长在已有的 uvicorn 上）
- 本片**不动**账本 `progress.md`
- **不用改 `graph.py`**：`Deps` 本来就是服务注入的，`selftest` / `explore` 都能在服务侧包一层

---

## 已知风险

| # | 风险 | 处置 |
|---|---|---|
| **R1** | **一张图多少钱没量过** —— 「每步拍不拍」的岔路、整跑增量（猜测 ~25 条命令 ≈ 4–16 秒）都架在它上面 | **Task 1 Step 5 先量并折算**；> 1.5s/张 → 降级 B + **页面上的 `shots_note` 一直显示** + 数进设计注与报告。量不出来就 BLOCKED，不许拿本机 headless 顶替 |
| **R2** | 模型自己调 `screenshot` 会把 ~500KB base64 塞进上下文（`llm.py:183`） | 本片不依赖它、也不修它；真跑时先看上下文账单 |
| **R3** | 截图是真站页面、页面还会显示 **worker IP 与 bit_id**，全都由**无鉴权**的 8080 服务 | 内网部署的假设（`/run` 本来也没有鉴权）。**本片不发明半吊子鉴权** |
| **R4** | 页面 JS 没有单测 | JS 做笨（只有 `job_id` + 定时器 + 渲染函数）；HTTP 层断言接线与「无外部资源」；其余走手工验收 |
| **R5** | 「轮」与「步」不是 1:1（一轮里可能 30 步） | 卡片两层 + 时间线；验收必须专门覆盖「一轮里卡在第 25 步」 |
| **R6** | 闸拍/叙述回调在**工作线程**里跑，会占住队列 | 闸拍硬超时 20s；回调只做一次 append（拿锁的时间是微秒级） |
| **R7** | 磁盘增长（一次运行最坏 ~20MB） | 一次运行有上限；清理**不在本片**（`runtime/` 不进 git）—— 记进交付报告 |
| **R8** | 真失败的验收需要有人提交运行（**页面上不能提单**） | 明确写进设计注 §十一；验收时由人用 API/curl 提交。`/again` 只重复**同一份**开场白，不教新意图 |
| **R9** | **人接管窗口**：停闸上动手安全，但他动过的页面就是下一步要跑的页面 —— 自测会把「页面不对」记成产物的问题 | 设计注 §十那张表**原样上页面**（A10）；接管之后请他用「说一句」告诉图 |
| **R10** | 「Bit 有远程控制台 URL」是**猜测** | 页面只写核实过的与人话方法；**不许**编 URL |
| **R11** | **插话可能让模型直接收尾**（`model_done` → 图认成「探路走完了」） | 措辞 + 追一句人话（Task 9 Step 4 的判据 ③）；真站验不过就走**退路**：`mode` 停在 `queue`（页面文案跟着变），不许静默 |
| **R12** | 时间线**不持久**（服务重启就没了） | `/live` 的 `note` 明说；轮次与截图仍在（checkpoint）。要持久就落 trace/表，是另一片 |
| **R13** | `should_pause` 的语义：探路被打断 = **这一趟结束**（`paused`） | 设计注 §4.1 第二/三行把落点写在页面上；**别**把它包装成「暂停一下还能接着跑」 |

---

## 后续（不在本计划内）

| 片 | 范围 | 验收 |
|---|---|---|
| **完整 Console** | 框选元素（截图 + bbox 可点元素列表）· patch(=py diff) → 验证 → 合并 · **能改产物那个角色**（`Deps.write` 的注入）· 中途停别的节点 · 停下再接着走 · 重开窗口按钮 · 页面上提单 · 内嵌活窗口 | 运营对着一次失败，**自己把站修好** |
| **记忆层** | `site_memory` · Correction Event 采集与沉淀（§6.5）· JSON 修复入口 · 路由表/注册（C15） | 同类站复用得上；“教了有用” |

---

## Task 11: 产物交付 —— 把跑出来的 py 交到人手上（**规格 §十五**）

> **这一条是补漏**：账本 `progress.md:34` 在本片开工时就写了「这条要落成计划里的一个任务，
> 派到那里时补上」，而 **Task 3 派工时没带上它** ⇒ 它在计划里从未存在过。
> **验收是由用户亲口提的需求**（规格 §十五 开头：「2026-09-16 用户新增。原话：『就是生成的 py 脚本也能下载啥的』」）。

**Files:**
- Modify: `agent/service.py`（新端点）、`agent/console.html`（时间线上那一步自带下载）
- Test: Create `tests/test_service_artifact.py`；页面那一半加进既有的 `tests/test_console_js.py`

**Interfaces:**
- Produces: `GET /job/{id}/artifact`
  - **有产物 → 200**，body 是那个 py 的**字节**；`Content-Disposition: attachment; filename="<site>.py"`
  - **没产物 → 409 + 一句人话**（说清**为什么**没有：跑到上限 / 窗口死了 / 诚实停下）
- `/live`：**产物那一格**（时间线上「它写下了 py」那一步自带下载；**产物出现之前那个位置不存在** ——
  不是灰按钮，是**还没有**）

**判据（规格 §15.2 / §15.4 / §15.5，原样用）:**
- **交付的是 `deliver` 那一步真正写下的那个文件**，不是「应该会写在哪」；
  **路径必须来自这一趟运行自己的记录**（`state["py_path"]`，已经在 state 里），**不许页面自己拼**；
- **没有产物时页面必须明说没有**，**不许摆一个空按钮或灰按钮了事**；
- 顺带把**它写到哪了**（路径）显示出来，人要对得上账；
- **明确不做**：在线编辑 py、在线 diff（§1.2 / §十一 已划掉，**别顺手加回来**）。

**★ 安全边界（照做，别简化）:**
- **只许服务这一趟运行自己记录的那个路径**。**不接受任何路径参数** ——
  一旦接受，就是一个**任意文件读取**；
- 服务前**核对真实路径落在 `out_dir` 之内**（**解析符号链接之后**），否则拒绝。

- [ ] **Step 1: 写失败测试**

覆盖：有产物 ⇒ 200 + **字节逐字节相等**（不是 JSON、不是空文件）+ `Content-Disposition` 里有文件名；
**没产物 ⇒ 409 + 一句人话**（三种没产出的成因各一条：撞上限 / 窗口死了 / 诚实停下）；
**★ 安全**：把 `py_path` 换成指向 `out_dir` 之外（含**符号链接**指出去）⇒ **拒绝**，
且**不许**因为「反正路径是服务自己记的」就跳过这次核对；
**★ A15 那条最容易悄悄错的**：**跑两趟之后**，第 1 趟的 `/artifact` 与第 2 趟的**不是同一个文件**。

- [ ] **Step 2: 红** → **Step 3: 实现** → **Step 4: 绿** → **Step 5: 提交**

**留给那趟真站窗口的**：**A13 / A14 / A15**（§12 那套手工验收 —— 只有人点得出来）。
