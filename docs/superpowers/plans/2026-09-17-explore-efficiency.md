# 探索效率（探索可续 + 描述当计划）— 实施计划（计划四）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把「生成」从**没有上界**变成**有上界**：窗口半路死掉不再把探路扔掉（A），
人写在描述里的步骤不再让模型重新发现一遍（B）。

**Architecture:** `agent/browser_agent.py` 的探路多两件本事 —— **重放一段已探明的路（0 模型调用）**
与 **照人给的步骤清单走**；每一步**发生的那一刻**追加一行到 `runtime/explore/<job_id>/`（journal），
窗口死了 `reopen` 读它重放、接着探。**图的节点形状不变**（`NODES` 一个字不改）。

**Tech Stack:** Python 3（LangGraph / FastAPI，容器里已有）· MCP（stdio，`cdp-mcp`）· 真浏览器（Bit 窗口）

**Spec:** `docs/superpowers/specs/2026-09-17-explore-efficiency-design.md`（本计划的**唯一**设计依据；
规格 `2026-09-16-siteforge-design.md` 的 §4.6 / §6.1 / §6.2 / §6.3 / §10 / 十三 是它的上游）

## Global Constraints

以下每条都来自上游规格，**每个任务的要求都隐含包含本节**：

- **动作一律走 cdp 命令，不手拼 JS**（D8/§5.2）。读可以用 JS，写不行。
- **`observe` 给感知不给判断**（D11）：本片**一个语义字段都不加**，工具清单不变。
- **人每一步都在**（§6.2/D16）：本片**不加**绕过闸口的路径；新产生的结论一律进**既有**那些闸的 `facts`。
- **给非技术人员看的文字必须是人话**（D16）：不是错误码、不是选择器。
- **不许静默跳过**（R-35 的教训）：一条判据不成立就**停下说话**，不许「跳过它接着走」。
- **状态住在 saver 里**（R-19）：journal **不是状态**，它是**一次失败尝试的证据**。
  **把 journal 删掉，图必须照常跑完**（只是窗口一死就得从头来）—— 每个碰它的任务都要有这条测试。
- ⚠️ **步数不是结构**（人 2026-09-17 指出，设计注 §2.2）：问卷/评测这类漏斗**是分支的** ——
  同一条描述，这一趟 20 步、下一趟 34 步都合法。**描述是检查点清单，不是脚本。**
  因此**任何判据、断言、上限、产物里都不许出现「期望 33 步」**这种假设；
  **步数变长变短本身不构成偏离**（那是「绕开」，见设计注 §2.3.1）。
  这条约束**每个任务都隐含包含**。
- **测试必须进 git**；不得新增对 `localhost:8080` 的依赖（自带 fixture）；
  跑外部东西的测试**默认 skip**（照 `tests/test_tool_loop.py` 的 `RUN_LLM=1` 惯例）。
- **本片不碰 `tools/cdp`（Go 侧）**、**不碰 `forms/`**、**不碰生产重放路径**。
- agent 用自己的 Bit 窗口与 :1081 的 gost，**绝不碰** :1080 与生产 worker。

### ⚠️ 两条上游实测结论（当作前提，别再验一遍）

- **窗口自己死，约 8 分钟一次**；一次实测寿命 **7m42s**；一次跑里进程 id 至少换过 3 次；
  **worker 内存/CPU 无异常**（不是资源压力）。
- **生产重放不受影响**：33 步表单 1~3 分钟跑完。**问题只在生成侧。**

---

## ⚠️ 开工前必须先做的一件事（Task 1）

**本计划的其余部分建在「一次探路装不进一个窗口」这个判断上，而这个判断目前只有一次观察
（7m42s）撑着。** 所以 Task 1 是**测量**，不是实现 —— 量完可能发现 A 该降级（设计注 §3.3 写了
反向判据，**照它执行**）。

**Task 1 不顺，后面几个任务先别派。**

---

## File Structure

```
agent/
  measure.py          ← 新建：一次运行/一次探路/窗口时间线的账（纯函数 + 落盘）
  plan.py             ← 新建：描述 → 计划清单（纯函数，零模型）
  journal.py          ← 新建：attempt journal 的追加与读回（原子追加，损坏行不吞）
  browser_agent.py    ← 修改：`Journey` 加 origin / plan / rounds；重放前缀与重放；计划模式
  service.py          ← 修改：接 on_step（落 journal）、窗口时间线、resume 注入、停因
  state.py            ← 修改：`explore_spent` / `END_WINDOW_GONE`；`Caps` 语义改「job 级累计」
runtime/explore/<job_id>/
  window.jsonl        ← 窗口时间线（Python 探针写；`runtime/` 不进 git）
  attempt-<n>.jsonl   ← 每一步一行（journal）
  baseline.json       ← 汇总（Task 1 / Task 7 的产物）
tests/
  test_measure.py test_plan.py test_journal.py      ← 新建（纯函数，桩）
  test_browser_agent.py test_service.py test_graph.py  ← 加用例（**别新建同名文件**）
fixtures/descriptions/                               ← 新建：验收用的**运营描述原文**（Task 1 Step 0）
  homebuddy.txt                                      ← 单路站（A 的验收）
  blinkist.txt                                       ← 分支站（B 的验收）
docs/superpowers/specs/2026-09-17-explore-efficiency-design.md  ← 基线表填数（Task 1/7）
```

**为什么 `plan.py` / `journal.py` / `measure.py` 各自一个文件**：三件都是**纯的、可穷举单测的**东西
（解析 / 落盘 / 记账），与「怎么开浏览器」是两件事。混进 `browser_agent.py`（已经 40KB）
会让它们变成必须起 MCP 会话才能测。

---

## Task 1: 基线测量 —— **先量，再决定**

**这一条不改产物的行为**，只把「今天到底多慢」变成数字。缺的三样先补上（都是**加字段**，不改语义）：
模型轮数没落账（`_wrap_up` 用完 `rounds` 就丢）、`alive()` 把 PID 扔了、`on_step` 没人接。

**Files:**
- Create: `agent/measure.py`, `tests/test_measure.py`
- Create: `fixtures/descriptions/homebuddy.txt`、`fixtures/descriptions/blinkist.txt`（**见 Step 0**）
- Modify: `agent/browser_agent.py`（`_wrap_up` 给 `journey` 加 `rounds` / `usage` 两个字段）
- Modify: `agent/service.py`（`BitWindow.probe()` 返回 `{alive, pid}`；`Service` 起一条窗口时间线探针；
  `_explore_for` 接 `on_step` 落 journal —— journal 的读回在 Task 4/5，本任务只**写**）
- Create: `runtime/explore/…`（运行时产物，不进 git）
- Modify: `docs/superpowers/specs/2026-09-17-explore-efficiency-design.md`（填 §3.2 基线表）

**Interfaces:**
- Produces:
  ```python
  # agent/measure.py（纯函数 + 一个写文件的口子）
  record_attempt(path, *, started_at, ended_at, rounds, steps, stop_reason, notes,
                 path_shape=None) -> dict     # path_shape 见下（**带默认值 = 对在飞实现是加法**）
  window_row(probe: dict, *, at: str, note: str = "") -> dict   # probe = {"alive":bool|None,"pid":int|None}
  baseline(path, *, window_lifetimes: list[float], attempts: list[dict], end: dict) -> dict
  ```
- `path_shape`（M9 的载体）：`{"steps": int, "marks": [int], "states": [str], "jumped_over": [int]}`
- `Journey` 新增：`rounds: int`、`usage: dict`（来自 `llm.summarize(rounds)`，`agent/llm.py:205` 现成）

> ⚠️ **给正在实现本任务的 agent 的话**：`path_shape` 与 `Journey` 那两个字段是**加**上去的
> （关键字参数带默认值 / 新字段带默认值），**不改既有签名、不改既有返回形状**。
> 若你已经写成了别的形状，**以「不破坏既有调用方」为准**，把这份计划里的这一步对齐过去。

- [ ] **Step 0: 先把两份描述存进 git（**在 /tmp 里的那份随时会没**）**

```bash
mkdir -p fixtures/descriptions
cp /tmp/blinkist_research_desc.txt fixtures/descriptions/blinkist.txt
cp /tmp/desc_hb.txt                fixtures/descriptions/homebuddy.txt
```

⚠️ 这两份是**当前**在机器上的实测描述（`/tmp` 不是持久位置 —— 人 2026-09-17 点出来的）。
拷完**不要改内容**：它们是**输入数据**，改了基线就与后面的验收不可比。
（`fixtures/` 本来就是这个用途：**自带 fixture，不依赖外部**。）
若 `cp` 时文件已经不在了 —— **在报告里说一声**，别凭记忆重写一份。

- [ ] **Step 1: 写失败测试（纯函数，不碰浏览器）**

覆盖：
- `baseline()` 算得出 M1~M10 里**可算的那几个**（寿命中位/最短、探路墙钟**样本序列**、模型轮数、
  重启次数、**路径形状**、**步数跨度**）；**算不出来的给 `None` 并说明为什么**
  （不许填 0 —— 0 会被读成「量到了，是零」）
- **一次样本 != 一个数**：`baseline(attempts=[a])` → 样本序列长度为 1 且**标着 `single_sample: True`**
  （否则后面有人会拿单样本去判 §3.3 那条边界）
- `baseline()` 对 `max(M2)`/`M1` 落**在 0.8~1.5 倍这个区间**时，给出 `verdict: "undecided"`
  —— **不许**挑一个方向（设计注 §3.3）
- `window_row()` 拿到 `pid` 变了 → 记成**新窗口**（不是「还是那个」）
- `alive()` 返回 `None`（问不出来）→ 记 `unknown`，**不许记成死**（`agent/service.py:915` 的同一条规矩）
- `_wrap_up` 之后 `journey.rounds == len(rounds)`；**被人打断那条路**（`_Stop`）也要有数

- [ ] **Step 2: 跑测试确认红** → `ModuleNotFoundError: agent.measure`

- [ ] **Step 3: 实现 `measure.py` + 三处加字段**

⚠️ `alive()` 今天的**返回类型不许变**（`tests/test_service.py` 钉着它）——
新加 `probe()`，`alive()` 改成调它。**别把 `alive()` 的返回值改成 dict**。

- [ ] **Step 4: 跑测试确认绿**

- [ ] **Step 5: 真站抽样，量基线**

**一条真窗口 + 真模型 + 真站的完整 run = 一个样本。** 一次不够（设计注 §3.1）：
**一条探路是分支过程的一次抽样**，同一条描述两次跑的步数本来就会不一样。

- **homebuddy（单路站）：抽 ≥3 次**（同一条描述）
- **blinkist（分支站）：抽 ≥1 次**（见 Task 7 —— 它的漏斗里 18 道单选题 + 5 次选书，
  是**分支**最明显的那个站；它本身就很长，1 次就够说明问题）
- **样本不够时**（时间/窗口不够）：**少抽可以，但必须在设计注 §3.2 里写明"这是 N 次抽样"**，
  并且**不许**拿它去判 §3.3 那条边界（判据见下）

每一次跑都要跑完（哪怕窗口死了几次、重启几次 —— **那是要量的东西**）。
把数字（**样本序列**，不是平均值一个数）填进设计注 §3.2 的表，然后**照 §3.3 判**：

- `max(M2) > M1` → **A 有收益，照做**
- 抽了 ≥3 次且 `max(M2)` 仍**远小于** M1（不到一半）→ **A 降级**，Task 4/5/6 缩成
  「窗口死因 + 停因区分」
- **贴着边界（0.8~1.5 倍）→ 结论写「判不出来」**，按保守走（照做 A）

- [ ] **Step 6: 提交**（基线数字**进 git**；`runtime/` 下的原始 jsonl 不进）

---

## Task 2: `agent/plan.py` —— 描述 → 计划清单（纯函数，零模型）

**Files:**
- Create: `agent/plan.py`, `tests/test_plan.py`

**Interfaces:**
- Produces:
  ```python
  MIN_STEPS = 2
  @dataclass Step:  n: int; text: str            # text 是**原话**，一个字都不改
  @dataclass Plan:  raw: str; source: str        # "goal" | "evidence" | ""（没解析出步骤）
                    steps: list[Step]
                    def actionable(self) -> bool  # len(steps) >= MIN_STEPS
  def parse(text: str, *, source: str = "goal") -> Plan
  def mark(content: str) -> Optional[int]        # 从模型那轮的话里读【第 k 步】；读不出给 None
  def ledger(plan: Plan, rounds: list[dict]) -> list[dict]   # 每步一个终态（Task 3 用）
  def from_states(src: str) -> Plan              # 旧 py 的 STATES → Plan（**可选口子**，见设计注 §2.3）
  ```

**判据（写死，逐条可单测）：**
- 只认**编号行**：`^\s*(\d+)\s*[.、)．]\s*(\S.*)$`（`1. …` / `1、…` / `1) …`）
- **一个字都不改写**：`Step.text` 必须是原行去掉序号后的**原样**（断言：描述里那段话逐字在 `text` 里）
- 序号**原样保留**（`Step.n`），**不重编**（描述可能从 0 开始、可能跳号 —— 重编就与人的话对不上了）
- **非编号行一个字都不许吞**（`Plan.raw` 是原文，**原样**带着；简报里**清单 + 原文都给**）。
  凭什么：真描述里除了步骤还有**约束** —— `fixtures/descriptions/blinkist.txt` 里有
  「`禁止点击: Cookie Policy,Privacy Policy,Terms`」、`轮次: 30`、`成功条件URL: /news-feed,/welcome`。
  只给清单等于**把这些丢掉**，而它们是**运营写的约束**，不是废话
- 少于 `MIN_STEPS` 行 → `steps == []`、`source == ""`（**"没有计划"要能被判出来**，不是空计划）
- 散文里出现过数字（`2026 年`、`3 个选项`）**不许**被当成步骤
- **`Plan` 里不许有「期望步数」这种字段**（§2.2：步数不是结构）——
  断言 `Plan` 的字段集合里**没有** `count` / `expected` / `total` 这类东西
- `mark()` 认不出 / 认出个不存在的号 → `None`（**不猜**）
- `ledger()` 的终态只能是 `{done, jumped_over, contradicted, not_reached}` 四选一；
  **`jumped_over` 不进 `deviations`**（设计注 §2.3.1）；**没被提到过的步骤是 `not_reached`**
- `from_states()` 解析不了 → `Plan(steps=[], source="")`，**抛异常是不行的**（修站那条路可能给的是坏 py）

- [ ] **Step 1: 写失败测试**（上面每条一个用例 + **反例**：散文不解析、0 步、坏 py、
  描述里的 `轮次: 30` **不许**被当成第 30 步）

- [ ] **Step 2: 跑测试确认红** → **Step 3: 实现** → **Step 4: 确认绿**

- [ ] **Step 5: 拿**两份真描述**各跑一次**（**真数据，别只用造的样例**）

| 文件 | 断言 |
|---|---|
| `fixtures/descriptions/homebuddy.txt` | 解析出 **33 步**（`操作:` 下面那串）、`1.填 Enter ZIP Code` 的原话逐字对得上；`成功条件: Thank you` **不在**步骤里 |
| `fixtures/descriptions/blinkist.txt` | 解析出 **4 步**（`引导:` 那 4 条）；`禁止点击: …`、`轮次: 30`、`浏览: 2` **不是**步骤，但**都在 `raw` 里** |

⚠️ **homebuddy 那份自己就写着「随机选择一个选项」（第 3、7、11… 步）** ——
**这是"步数不固定"的第一手证据**：运营自己就没钉死选哪个，选不同 → 走的分支不同 →
33 只是**这一次**的长度。→ **断言里不许出现「必须是 33」这种判据**，
只有「这份文件解析出 33 行」这一条（那是**解析**的断言，不是**运行**的断言）。

- [ ] **Step 6: 提交**

---

## Task 3: 计划模式的探路（`browser_agent`）

**Files:**
- Modify: `agent/browser_agent.py`（`_SYSTEM` 加规矩、`_brief` 分支、`explore` 收 `plan`、
  `Journey` 加 `plan_ledger` / `deviations` / `stall_rounds`）
- Test: `tests/test_browser_agent.py`（**加用例，不新建文件**）

**Interfaces:**
- Consumes: Task 2 的 `plan.parse` / `plan.mark` / `plan.ledger`
- Produces:
  - `explore(..., plan=None)` —— `plan` 为 `None` 或 `not plan.actionable()` 时**走今天那条路**
  - `Journey.plan_ledger: list[{n, text, state, why}]`、`Journey.deviations: list[str]`
  - `Budget.stall_limit: int = 6`（**初值，由 Task 1 的轮数分布校准**）

**做法（设计注 §2.2–§2.5）：**
- **简报分两版**：有计划 → **清单（原话）+ 原文（`Plan.raw`，一字不删）** + 三条走法
  （一步一确认 / 用 `【第 k 步】` 标位置 / 与页面不符就说出来）+「描述没说的别点」
  +「**这份清单是一条走法的样子，不是站点的结构**（步数会随分支变）」；
  没计划 → **与今天逐字节相同**
- **规矩进 `_SYSTEM`**（`_SYSTEM` 是稳定层，计划是每轮的事）：加一条「人给了步骤就照着走，
  与页面矛盾时说出来再决定；**这一趟少走或多走几步是正常的**」
- **位置标记只在 `_Gate` 那一层读**（它每轮都在场，`agent/browser_agent.py:437`），
  读出 `【第 k 步】` 就更新 `plan_ledger`；读不出 → 记一句「这一轮没说自己在第几步」，**位置不动**
- **往前跳号 = `jumped_over`**（设计注 §2.3.1）：跳过的那几个记 `jumped_over`，
  **不算偏离、不进 `deviations`**，而且**照样算「前进了」**（停滞计数清零）
- **`deviations` 只装「页面与描述矛盾」**（模型声明的那种），**不装**「没走到某一步」
- **停滞判据（全是感知/声明，没有语义判断）**：位置没前进 **且** 页面状态没变 **且** 没有新成功动作
  → 连续 `stall_limit` 轮 → `_Stop("plan_stalled")`

- [ ] **Step 1: 写失败测试（桩 MCP + 桩模型，不开浏览器）**

覆盖（每条要有**反例**）：
- 有计划 → 简报里出现**原话清单**（逐字断言）、**原文也在**（`禁止点击: …` 那行要在）、
  且**没有**今天那句「要做的事：」
- **没计划 → 简报与今天逐字节相同**（把今天的输出**硬编码**进断言 —— 这条是回归钉子）
- `【第 3 步】` → `plan_ledger[2]` 被标为到过；`【第 99 步】` / 没有标记 → **位置不动** + notes 有那句人话
- **分支（B7）**：`【第 2 步】` → `【第 5 步】` → 第 3、4 步是 `jumped_over`，
  **`deviations` 为空**（负例），且停滞计数**清零**
- **分支（B8）**：同一条描述两次跑，一次 20 步一次 34 步（桩）→ 两条都**不出现** `plan_stalled`、
  `deviations` 为空；**断言里不许出现 "33"**（那是描述的一份样本，不是约束）
- 停滞：连着 `stall_limit` 轮页面没变、位置没动 → `stop_reason == "plan_stalled"`；
  **中间只要有一轮页面变了 → 计数清零**（反例）
- 模型报了**矛盾**（话里带「描述说…页面上是…」）→ `deviations` 里有一条；**位置不前进**
- `plan=None` 的**整条路**（自由模式）与今天的用例**全部照旧绿**（回归）

- [ ] **Step 2: 跑测试确认红** → **Step 3: 实现** → **Step 4: 确认绿 + 全量回归**
- [ ] **Step 5: 提交**

---

## Task 4: `agent/journal.py` —— 每一步落盘（发生的那一刻）

**Files:**
- Create: `agent/journal.py`, `tests/test_journal.py`
- Modify: `agent/service.py`（`_explore_for` 传 `on_step` → 落 journal）
- Modify: `agent/browser_agent.py`（`dispatch` 里给每步记 `origin="model"`）

**Interfaces:**
- Produces:
  ```python
  def dir_for(root, job_id) -> Path                 # <root>/<job_id>/
  def attempt_path(root, job_id, n) -> Path          # attempt-<n>.jsonl
  def append(path, step: dict) -> None               # 追加一行；**原子**（一行一次 write）
  def read(path) -> list[dict]                       # 坏行**不吞**：跳过的行数与原因一起返回
  def attempts(root, job_id) -> list[Path]           # 按 n 排序
  ```
- `Journey.steps[i]` 新增 `origin`（`"model"` / `"replay"`）—— **这是 Task 5 的接口**

**判据：**
- **一次一步一行**：`append` 里用**单次** `write` 一个完整行（进程被杀也只会留下半行，
  不会串行）—— `read()` 遇半行**跳过并报告**（`(rows, skipped)`），**不抛、不静默**
- 目录不存在 → 建（`parents=True, exist_ok=True`）
- 落盘的东西**只能是 `step` dict**（不许塞原始工具返回 —— 那里面有几百 KB base64，
  `_summarize` 就是为这个存在的）
- **journal 写失败不许把探路带塌**：`append` 抛异常时 `on_step` 侧吞掉并往 `journey.notes` 记一句
  （**与 Console 那片 Task 2 对 `shooter` 的规矩同一条**：旁路坏掉不能影响主路）

- [ ] **Step 1: 写失败测试**（真文件写进 `tmp_path`；**半行**用例：手工写半行 → `read` 报 1 行坏）
- [ ] **Step 2: 跑测试确认红** → **Step 3: 实现** → **Step 4: 确认绿**
- [ ] **Step 5: 服务侧接线 + 一条「journal 删掉图照常跑完」的测试**（R-19 的判据）
- [ ] **Step 6: 提交**

---

## Task 5: 重放前缀与重放（`browser_agent`）

**这一条是本片的核心。** 分两步走：**纯函数先做（Step 1–4）**，重放器后做（Step 5–7）。

**Files:**
- Modify: `agent/browser_agent.py`
- Test: `tests/test_browser_agent.py`（加用例）

**Interfaces:**
- Produces:
  ```python
  def replayable_prefix(steps: list, success_text: str, *,
                        entry_url: str = "", pages: list | None = None
                        ) -> tuple[list, str]
      # 返回 (可重放的步, boundary_reason 人话)。四条判据 R1–R4 见设计注 §1.4.3
  def replay(session, steps, *, on_step=None, alive=None) -> dict
      # 走这段前缀（MCP 调用，**0 模型**）。返回 {done: int, landed: str, why: str}
  ```

- [ ] **Step 1: 写 `replayable_prefix` 的失败测试（纯函数，穷举）**

每条判据**一个正例 + 一个反例**：
- **R1**：`result.ok is False` 的步 → 前缀**停在它前面**
- **R2**：最后那步后面**没有** `observe`/`diff` → 停在它前面（**「提交」就藏在这儿**）
- **R3**：某一步所到的那一页文字里**含 `success_text`** → 停在它前面；
  ⚠️ **反例**：`success_text` 出现在**更早**的页面（描述里的成功文案与页面上的一句普通话撞了）
  → **也不许过**（保守到底，理由说清）
- **R4**：`goto` 到一个**没见过的** URL（带一次性 query）→ 停在它前面；
  `goto` 到入口 / 到见过的页面 → 过
- **前缀是「吃到第一个不满足就停」**，不是「跳过它接着吃」（反例：中间一个坏步，
  后面还有好步 → 结果里**不许**出现后面的步）
- `success_text` 为空 → **抛**（那不是「没有约束」，是**少给了一个输入**，`intake` 本来就该拦住它）

- [ ] **Step 2: 红 → Step 3: 实现 → Step 4: 绿**

- [ ] **Step 5: 写 `replay` 的失败测试（桩 session，不开浏览器）**

覆盖：
- **一次模型都不调**（桩 `client` 上断言 `create` 调用次数 == 0）
- 每一步都 `origin="replay"` 记进 `journey.steps`（**账要对**：产物侧 `states()` 照样能用）
- **逐页核验**：走到账本记着「这一步改了页」的点 → 调一次 `observe` 并用 `when_holds`
  比对；**不成立就停**并给出「我以为会到 X，实际是 Y」（负例）
- ⚠️ 核验用的是**产物那套放宽后的 `when`**（R-37 的稳定前缀 + 文本），**不是逐字节比对** ——
  分支站上「落到另一条分支」与「选错了元素」**系统分不出**，**两条都停**（设计注 §1.6）
- **选择器全部解析不出来** → 停在那一步前 + 人话（负例）；
  解析顺序是 `target.selectors`（`_selectors_of` 给的那份），**第一个能用的胜**
- **`frame_id`**：账本里有就带上；没有就不带（**不许**编一个 `"main"` —— 见 G4）
- **窗口又死了** → `alive()` 说死 → 重放整段重来，**最多 3 次**，到顶返回 `why` 人话（负例）

- [ ] **Step 6: 红 → Step 7: 实现 → Step 8: 绿 + 全量回归** → **Step 9: 提交**

---

## Task 6: 接进续跑（`explore(resume_from=…)` + 服务 + 图）

**Files:**
- Modify: `agent/browser_agent.py`（`explore(..., resume_from=None)`）
- Modify: `agent/state.py`（`END_WINDOW_GONE`；`explore_spent`；`Caps` 语义改 job 级累计）
- Modify: `agent/graph.py`（`_explore` 传累计预算与 resume；闸口 `facts` 带重放摘要）
- Modify: `agent/service.py`（`reopen` 读最新 attempt → 注入 `resume_from`；`Deps.window_alive`）
- Test: `tests/test_graph.py`, `tests/test_service.py`（加用例）

**Interfaces:**
- Consumes: Task 4 的 `journal.read/attempts`、Task 5 的 `replayable_prefix`/`replay`
- Produces:
  - `explore(..., resume_from: list | None = None, window_alive: Callable | None = None)`
  - `state.END_WINDOW_GONE = "window_gone"`（**进 `state.__all__`**，与既有的十几个并列）
  - `graph.Deps.window_alive: Optional[Callable]`（**可调用不进 checkpoint**，与 `should_pause` 同规矩）
  - `SiteState.explore_spent: {steps, rounds, attempts}`

**四处必须写对的：**
1. **预算改 job 级累计**：`_explore` 把 `state["explore_spent"]` 的累计值从 `Caps` 里减掉再给 `Budget`；
   跑完把本轮的消耗加回去。**重放的步不计 `steps`，计 `attempts`**（设计注 §1.8）
2. **窗口死掉是一等停因**：工具连续失败 2 次 → 问 `window_alive()` → `False` → `_Stop("window_gone")`
   → `END_WINDOW_GONE`。**不许**去匹配工具的错误文字
3. **闸口 facts 带重放摘要**：重放了几步 / 停在哪一步 / **为什么停**（R2/R3/R4 哪一条）。
   ⚠️ 这份摘要**在节点开工前就能算**（只依赖 journal）—— 所以它挂在 `_enter` 的 `facts` 上，
   而不是等重放跑完再说
4. **`reopen` 的话要改**：今天 `:878` 那句「探路要从头再走一遍」**在新形状下是错的**；
   改成说清「重放了几步、停在哪、接着探」。**`END_PAUSED` 一并加进可续跑的集合**
   （`WINDOW_END_REASONS["explore"]`）

- [ ] **Step 1: 写失败测试（桩，不开浏览器）**

覆盖：
- 窗口死 → job 是 `END_WINDOW_GONE`（**不是** `explore_unfinished`）；**预算走完**是另一种（两条反例互斥）
- 第二次进 `explore` 时 `resume_from` 非空 → 桩探路断言**收到了**那段前缀；
  **journal 不存在/读不出 → `resume_from=None`，整条路与今天一样**（负例，R-19 的判据）
- 累计预算：一次探路花 20 步、重开后又给 20 步 → 第二次只剩 `Caps.explore_steps − 20`（**不许重置**）
- `reopen` 不再说「从头再探」（字符串断言，**对着那句旧话反向断言**）
- `END_PAUSED` 现在接得住（**今天这条是红的**）
- 图里 `NODES` 与 `STEP_SAY` **一个字都没变**（断言 —— 防漂）

- [ ] **Step 2: 红 → Step 3: 实现 → Step 4: 绿 + 全量回归** → **Step 5: 提交**

---

## Task 7: 真站端到端验收（**本计划的验收**）

**两个站，各管一件事**（人 2026-09-17 定的）：

| 站 | 描述文件 | 它验什么 |
|---|---|---|
| **homebuddy**（非 shadow、单路） | `fixtures/descriptions/homebuddy.txt` | **A（探索可续）**：路径长、装不进一个窗口 —— 窗口死/重开/重放的主场 |
| **blinkist**（**分支站**） | `fixtures/descriptions/blinkist.txt` | **B（描述当计划）**：步数**不固定**、分支多、有「禁止点击」约束 —— 验「检查点清单」而不是「脚本」 |

> **为什么 blinkist 必须进验收**：规格 §10 拿它当「每轮都不一样的站」的样板（`when` 的存在理由），
> 而 `forms/sites/blinkist.py` 的头注释写着它靠**通用步进循环（不硬编码步骤清单）**活着。
> **分支这件事，只有在这个站上才是常态而不是边角。**

**Files:**
- Modify: `docs/superpowers/specs/2026-09-17-explore-efficiency-design.md`（填验收那一列 + M1–M10 对照）

⚠️ **描述文件是人写的输入，没有任何程序读它**（设计注 §五 G9）：
`POST /run` 的载荷由**人**从文件里抄 —— `url` 抄「页面URL」、`success_text` 抄「成功条件」、
**`goal` 抄整个文件原文**（包括「禁止点击」「轮次」那些约束行，§2.2）。
别去找「读入描述文件」的代码：**它不存在，本片也不建**。

- [ ] **Step 1: homebuddy 跑两次（同一条描述、同一个站、同一条窗口）**

```
① 正常跑一次   → 记 M2 / M3 / M7 / M9（B 的判据要跟基线比）
② 跑一次并**人为把窗口杀掉**（在探路途中 `bit.sh close`，模拟那 8 分钟）
   → 断言：A1（重放的模型轮数 = 0）、A2（重放的步 ⊆ 前缀）、A3（边界说得出为什么）、
           A4（落地页对得上）、A6（轮数不随死亡次数增长）
```

⚠️ **人为杀窗口不算作弊**：窗口本来就会自己死（E1/E2），
人为杀只是把「等 8 分钟」换成「现在就发生」。**但报告里必须写明是人为杀的。**

- [ ] **Step 2: blinkist 跑两次（**同一个站、同一条描述**）**

```
两次都在关键分支上给不同答案（或让它自己选）→ 两次的**实际步数允许不同**（§2.2）
断言：C1（见下）/ B2（每个检查点都有终态）/ B7（绕开不算偏离）/ B8（步数变化不触发异常）
```

**blinkist 的验收判据（写死，别让它被读成「产物要和手写的一样好」）：**

| # | 判据 | 怎么测 |
|---|---|---|
| **C1** | **两次都走到成功页**（「Subscribe to Blinkist Pro」/ `/news-feed`,`/welcome`） | 人看 / `journey.pages` 的 url + 探路结束时的页面 |
| **C2** | **两次都没点「禁止点击」里那三个**（Cookie Policy / Privacy Policy / Terms） | `journey.steps[*].target.text` **与 `note`** 里都没有它们（两处都查 —— 元素没找到时 `target.text` 是 `None`，`agent/browser_agent.py:509`） |
| **C3** | **步数可以不同，且这不被当成异常** | 两次 `len(steps)` 不同 → `stop_reason` 都不是 `plan_stalled`、`deviations` 为空、没有以 33/34 为期望的判据 |
| **C4** | **每一步都有交代** | `plan_ledger` 覆盖 `plan.steps` 全部（`done` / `jumped_over` / `contradicted` / `not_reached`） |
| **C5** | **产物的质量不在这条判据里** | 只要求：渲染得出来、过 lint、自测结论**如实**。**不要求**它达到 `forms/sites/blinkist.py` 的鲁棒性 —— 那是模板层的缺口（设计注 §0.4，**不是本片的任务**） |

⚠️ **C5 必须原样写进报告**：否则「探索可续 + 描述当计划」做完之后，
很容易被读成「现在能产出 blinkist.py 那种脚本了」—— **那是另一件事**（设计注 §0.4）。

- [ ] **Step 3: 判据（设计注 §四 那张表）逐条打勾，**包括没达成的那几条**

不许只报达成的。**没达成的写在设计注里** —— 那才是这份文档存在的意义。
⚠️ 基线 vs 本片的对比要**按样本比**（各 N≥2），**不许拿一次跑比一次跑**（设计注 §3.1）。

- [ ] **Step 4: 清理核对**（照 `skills/bit-window/SKILL.md` 的自检清单）

- 用的是自己的 worker + bit_id；代理走 :1081，**全程没碰 :1080**
- 关窗口之后**查过 `/browser/pids/alive`**（返回空 `data`）
- 中途重开过窗口的话，**看过 worker 的内存余量**（没盲目重开）

- [ ] **Step 5: 提交**

---

## 跨任务接口（三处，写错一处就是漂）

### 1. `Journey.steps` 的键（Task 3/4 加，Task 5/6 读）

```python
step["origin"]        # "model" | "replay"（Task 4 起）
journey.rounds        # int（Task 1 起）
journey.usage         # dict（`llm.summarize` 的产物，Task 1 起）
journey.plan_ledger   # [{n, text, state, why}]（Task 3 起）
journey.deviations    # [str]（Task 3 起）
```

⚠️ **不许**把原始工具返回塞进 `Journey`（它进 checkpoint：`graph.MSGPACK_ALLOWLIST` 点名允许它）。

### 2. journal 的行形状（Task 4 写，Task 5 读）

**就是 `Journey.steps` 的那一步**，逐字同一个 dict（不多包一层、不改键名）——
否则「账本」与「重放」会各有一套字段名，那正是漂。

### 3. `replayable_prefix()` 的返回（Task 5 定，Task 6 用）

```python
(prefix: list, boundary_reason: str)
```

`boundary_reason` 是**人话**，且**必须**在两种情况下非空：
① 有步被挡下（说清是 R2/R3/R4 哪一条、挡的是哪一步）；
② 前缀为空（说清「一步都重放不了」）。

---

## 预检扫描（2026-09-17，写这份计划时对**在飞的工作**做的一次冲突扫描）

| # | 发现 | 裁定 |
|---|---|---|
| **P1** | `docs/superpowers/plans/2026-09-17-console-minimal.md` 的 **Task 2 也改 `agent/browser_agent.py` 的 `dispatch`**，并且**也给 `Journey.steps` 加键**（`shot_before` / `shot_after` / `shot_after_deferred`） | **键名不冲突**（各加各的）；但**同一个函数同一个位置**要改两遍。**开工前 `git status` + 读一遍 `dispatch` 现状**，别照本计划的行号改。两边都改 `Journey` 的字段表 → **谁后改谁 rebase** |
| **P2** | Console 那片 **Task 4** 从 checkpoint 里读 `journey.steps` 做轮次投影；本片把**途中的**步落到 journal | **两件事，别合并也别互相等**。journal 是「失败尝试的证据」，投影是「闸口上给人看的」。本片**不改** `rounds.project()` 的输入（`journey` 仍在 checkpoint 里） |
| **P3** | `agent/service.py` 刚被 Task 8 修复轮改过（`c998a72` / `b4a4788`），且 Console 那片 Task 3/4/5 **也改它** | Task 1/4/6 改的是 `BitWindow`、`_explore_for`、`reopen`、`_resume_point`。**先读现状**；`_resume_point` 的两种判据（DONE vs FAILED）**别顺手重构**，只做加法 |
| **P4** | `tests/test_browser_agent.py` / `test_service.py` 可能正在被改（Console 那片**新建** `test_agent_shots.py` / `test_service_shots.py`） | 本片**加用例**进既有文件（Task 2/3/5/6 的核心断言都在那儿，另开文件会漏回归）；新建的只有 `test_plan.py` / `test_journal.py` / `test_measure.py` |
| **P5** | `_wrap_up` 里 `rounds` 目前只用完就丢 | Task 1 加两个字段；⚠️ **被人打断那条路**（`_Stop` 穿过 `run_tool_loop`）**拿不到 rounds** —— 那一路的 `journey.rounds` 只能是 `0` + 一句「被中断，这一趟没记到轮数」。**不许编一个数** |
| **P6** | `graph.Deps` 加可调用旋钮有先例（`should_pause` / `set_viewport`），且**服务侧没接 `should_pause`** | `window_alive` 照同一条规矩加；本片**不接** `should_pause`（那是 Console 那片的事） |
| **P7** | `runtime/` 不进 git，且 `runtime/selftest/` 已有写入者 | journal 落 `runtime/explore/<job_id>/`（**同层不同目录**，互不覆盖）；测试一律 `tmp_path` |
| **P8** | `alive()` 的返回类型被 `tests/test_service.py` 钉着 | Task 1 **不改** `alive()` 的签名（新加 `probe()`） |
| **P9** | 验收用的两份描述**现在只在 `/tmp`**（`/tmp/blinkist_research_desc.txt`、`/tmp/desc_hb.txt`），而 `/tmp` 会被清 | **Task 1 Step 0 先拷进 `fixtures/descriptions/`**（本来就该自带 fixture）；拷不到就在报告里说，**别凭记忆重写** |
| **P10** | `forms/sites/blinkist.py`（生产，374 行）**不在本仓库**（在 `/opt/skills/auto-farm-skill/`） | 验收里只**读**它当对照（C5 那条判据引它），**不复制、不改、不依赖它跑** —— 本片不碰生产仓库 |

**扫描过、确认没问题的**：
- 本片**不碰** `tools/cdp`（Go 侧一个字节不改）—— B5 那条判据正好是「工具 schema 没变」
- 本片**不碰** `Dockerfile` / `entrypoint.sh` / `forms/`
- 本片**不改** `NODES` / `STEP_SAY` / `REVISABLE`（Task 6 有一条断言钉着）

**并发期的已知折扣**（实测发生）：同一仓库里并发跑 `pytest tests/ -q` 会看到**对方半成品**的代码
→ **「测试绿」在并发期要打折扣，等所有 agent 停下再跑一次才算数。**

---

## 已知风险（本计划内必须记住）

| # | 风险 | 处置 |
|---|---|---|
| **R1** | **基线只有一次观察（7m42s）撑着** —— 整个 A 建在「一次探路装不进一个窗口」上 | **Task 1 先量，量完照设计注 §3.3 的反向判据判**；不成立就把 A 降级 |
| **R2** | **重放的分辨力比产物弱**（MCP 只收裸选择器，没有 text/role/near 那一跳；`frame_id` 也没记） | 已写进设计注 §1.4.4；**逐页核验 + 失败就停**兜住；`frame_id` 是 Task 5 的一步 |
| **R3** | **「不可逆」的判据可能不够**：R2 靠「没被观测过就不重放」，而模型**可以**在提交后立刻 observe（那就被观测了）| R3（没过成功线）是第二道闸；两道都过才重放。**§1.4.5 的闸口摘要 + A2 断言**是第三道（人能看见） |
| **R4** | **重放要重复走一遍真站**（同 IP、新指纹）—— 站方会不会拦，**没量过** | Task 1 的 M8 记「重放那一段有没有出验证码/拦截」；有 → 回到设计（可能要给重放加更保守的步长） |
| **R5** | **计划标记（`【第 k 步】`）模型可能不照做** | 降级不是失败：位置退化成「只有页面变化」，系统照跑（设计注 §2.4）；M7 量合规率 |
| **R6** | **`plan_stalled` 之后没有闭环**（人纠一句、原地接着探要动作级往返，本片不做） | 明说不做（设计注 §2.6）。**本片保证的只有「账本不丢」** |
| **R7** | **预算改成 job 级累计会把「慢慢探」的路堵死**（一次跑的总轮数上限变小了） | 这不是省钱（P5），是**防跑飞**（今天的「每尝试一份新预算」等于没有上限）。若真跑发现不够，**加 `Caps` 的默认值**，不许把累计改回每尝试 |
| **R8** | 并发期（Console 那片）同时在改 `browser_agent.py` / `service.py` | 见预检扫描 P1/P3；**动手前重读现状**，别照行号改 |
| **R9** | **基线是一次抽样，却容易被当成一个数**（分支站：这一趟 20 步、下一趟 34 步都正常） | Task 1 抽 ≥3 次（blinkist ≥1）、记**样本序列**、贴着边界写 `undecided`（设计注 §3.3）。⚠️ **不许拿一次跑比一次跑**（B1 改成**每检查点摊**的轮数，见设计注 §四） |
| **R10** | **blinkist 的产物一定比手写的弱**（模板没有循环，设计注 §0.4） | 验收判据里**明写 C5**：产物的鲁棒性**不在**本片判据内。**报告里必须原样带上这句**，防止误读 |
