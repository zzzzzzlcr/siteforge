# 能力差距对照 — blinkist（2026-09-11 那次「人 + Claude 聊出来」的实测）

> **为什么有这一片**：人的判断是——**「这事能做，因为我们上次就是聊出来的」**。
> 这片把那句话变成**可核对的**：那次到底靠了什么，今天这个 agent（七个工具的 MCP 门）有没有。
>
> 只回答一件事：**把 blinkist 那次重放一遍，今天差哪几件。**
> 不实现、不改代码、不动 ledger。
>
> 产物本体：`/opt/skills/auto-farm-skill/forms/sites/blinkist.py`（374 行，2026-09-11 16:12）。
> 对照物：`tools/cdp/internal/mcp/registry.go`（七工具）、`agent/template.py`（SKELETON）。
> 证据：`/home/dev/.claude/projects/-opt-skills-auto-farm-skill/0a348f6a-*.jsonl`
> 的 `2026-09-11` 段（行 1333–3915）、`/tmp/bk_test*.log`、`/tmp/blinkist_*`。

---

## 零、先把话说准

### 0.1 一个必须先拆掉的误会

`/tmp/blinkist_r*.json`、`/tmp/blinkist_*_replay*.json`、`/tmp/blinkist_generated.json`
**不是**这次的证据 —— 它们是 **2026-08-17~08-20** 那轮的 JSON 规则折叠产物（`site:www.blinkist.com`、
`guide:[scroll→bottom, click Featured Titles, click Verify]`、`quiz_mode:recorded`）。
那次的下场写在文件尺寸里：

| 事实 | 数 |
|---|---|
| 那轮的轮次/回放产物总数 | **52** |
| 其中 **0 字节**（跑了个空） | **26**（正好一半） |
| 非空且像样的 | ≤ 8 |

`blinkist_research_desc.txt`（8/20）是那轮的运营描述（`轮次:30 浏览:2`），**不是**规格。
所以：**blinkist 有两个时代** —— 8 月的 JSON 折叠（死路，见 `blind-generation-deadlock`），
和 9/11 的 py 手写（活了）。**这片只分析后者。**

### 0.2 那次的产物设计要点（脚本自己写的）

`blinkist.py` 头部第 13–19 行，原文：

```
设计要点(可靠性优先):
  - 通用步进循环: 不硬编码步骤清单, 每轮按 DOM 判断该做什么(防 A/B 变体 / 步骤增减)
  - 选项按钮: 文字型 or aria-label 图标型(Disagree/Not sure/Agree) 都支持
  - 排除 Back(每步都有, 误点会倒退) / cookie
  - Continue 仅在非 disabled 时点; 选中自动跳转的步骤无需 Continue
  - 每步以 URL(pathname) 变化为推进判据, 超时重试
```

**这五行就是本片的核对清单** —— 尤其第一行：它明说可靠性来自「不硬编码步骤清单」。
`agent/template.py` 的 SKELETON 是**固定步骤表**。这是本片最重要的一条，见 §3。

---

## 一、这次是怎么做出来的

### 1.1 时间线（半天，一次会话，`0a348f6a`）

全部时间为 UTC；容器日志时间为 UTC+8，故 `/tmp/bk_test.log` 里是 14:35 起。

| 时刻 | 谁 | 干了什么 |
|---|---|---|
| 06:26:20 | 人 | 贴出 blinkist 文章页 URL，问「我们之前 py 脚本尝试过然后没通？」 |
| 06:26–06:28 | Claude | **考古**：git log、FMR 配置、`json_exec.log`、历史 py/JSON 尝试、8/7 拉黑 commit `9496e58` |
| 06:28:00 | 人 | **「这个占比很大，希望能借用 mcp-cdp 来尝试写一个相当可靠的脚本可以吗」** |
| **06:28:06–06:34:35** | Claude | **MCP 探索期（6.5 分钟）** —— 详见 §1.2 |
| 06:35:00 | Claude | `Write blinkist.py`（v1，11134 字节） |
| 06:35–06:40 | Claude | **bk-test-01**：45 步全走完（3 分钟）→ 终点填邮箱提交**未见跳转** |
| 06:39–06:40 | Claude | `cdp eval` 诊断：`errorShown` / `inputValue` / 出口 IP |
| 06:41:26 | Claude | Edit：邮箱兜底改为真实风格（`testuser.*` 被风控拒） |
| 06:41:19–31 | Claude | 改 `scripts/ad-task.py` 路由（两处）+ 同步进容器 |
| 06:41:03 | 人 | **「那把他黑名单放开我放几单进来看看？」**（只有人能做的生产动作） |
| 07:07:36 | 人 | 「好像一直没进来 blinkist 广告所以也不知道啥情况」 |
| 07:08:02 | Claude | **bk-test-02** → 走到 S3 后 **URL 变空 → 脚本误判「离开 onboarding = 成功」** |
| **07:09:00** | **人** | **「等会你先停一下，莫名其妙会打开一个空白的页好像」** ← 关键介入 |
| 07:10:00 | Claude | bk-test-03 → 卡在 S2/S3 `mentions` |
| 07:10:49–07:12:07 | Claude | `cdp eval` 诊断空白页（`readyState`/`bodyLen`/`totalEls`）+ **curl 直查 `/json/list`** |
| 07:11:25/31/35 | Claude | 3 次 Edit → 加 `_clean_pages()`（`cdp active` pin + `cdp close --all`） |
| 07:12:23 | Claude | bk-test-04 → 走到 S8 被 `timeout` 截断 |
| 07:13:07 | 人 | 「OK 停掉吧，等任务吧」 |
| **07:23:05** | **人** | **「看日志好像成功了」** ← 生产任务 `25900548` 真邮箱一次成功（3 分 34 秒） |
| 07:24–07:47 | 人+Claude | 部署决策、当日连续失败计数特性（3 单停机）、提交/合并 |
| 07:13 / 16:07 | Claude | 落盘记忆 `memory/blinkist-adaptation.md` |

**总时长：06:26 → 07:47 ≈ 81 分钟。**

### 1.2 MCP 探索期（6.5 分钟）到底做了什么 —— 本片的核心证据

这一段是「MCP 交互摸清」字面的意思。**25 次工具调用**：

| 工具 | 次数 | 备注 |
|---|---|---|
| `mcp__chrome-devtools__evaluate_script` | **17** | 全部有效信息的来源 |
| `mcp__chrome-devtools__navigate_page` | 2 | 文章页 → `/en/onboarding/matrix` |
| `mcp__chrome-devtools__list_pages` | 1 | |
| `mcp__chrome-devtools__new_page` | 1 | 开一个带标记的页面来**反查 MCP 连的是哪个浏览器** |
| `mcp__chrome-devtools__take_screenshot` | 1 | 06:29:16，看页面全貌认 CTA |
| `mcp__chrome-devtools__take_snapshot` | **0** | 整个 blinkist 段一次没用 |
| `mcp__chrome-devtools__click` | **0** | 同上 |
| `mcp__chrome-devtools__form` | **0**（该 MCP 无此工具） | 同上 |

> **读法**：那次「摸清流程」**不是靠 a11y 快照看出来的，是靠 17 次 JS 求值探出来的**。
> 截图只用了一次（认 CTA 位置），快照和拟人点击在 blinkist 上**一次没用**。

**其中 4 次是整段式的「页内探索机器人」** —— 一次调用里跑一个带 `sleep` / 读 DOM / 判断 / 点击 /
判导航的循环，回一个 trace 数组：

| 时刻 | 循环上限 | 结果 |
|---|---|---|
| 06:30:07 | 22 轮 | 卡在 `/matrix/growth-areas` —— **多选步骤要先选选项再 Continue**，走器只点了 Continue |
| 06:31:12 | 24 轮 | 卡在 `/matrix/certain` —— 走器找不到选项按钮 |
| 06:32:31 | 26 轮 | **通了，30+ 步全摸清** —— 选项是 **aria-label 图标按钮**（Disagree/Not sure/Agree），Continue `disabled:true` |
| 06:34:27 | — | 终点探针：React 兼容填值（`HTMLInputElement.prototype` value setter + input/change 事件） |

06:32:31 那一次的 JS 与最终脚本的 `_click_option()` **是同一段逻辑的移植**：

```js
// 06:32:31（MCP 探索期，页内）
const optBtns = btns.filter(b => {
  const t = label(b);
  return t && !primary.includes(t) && !/cookie/i.test(t);
});
const c = optBtns[Math.floor(Math.random() * optBtns.length)];
```

```python
# blinkist.py:150-175（产物，生产期）
#   排除 NON_OPTION（back/continue/cookie…）→ 过滤可见/未禁用 → 随机取一个
#   → 打 data-bk<rand> 标记 → cdp click 那个标记
```

**结论**：产物的选择启发式**是从页内机器人直接抄下来的**。探索机器人和产物不是两件事。

### 1.3 脚本编写期（06:35–06:42）

| 动作 | 次数 |
|---|---|
| `Write blinkist.py` | 1（v1，11134 字节） |
| `Edit blinkist.py` | **8**（06:35:09 / 06:41:26 / 07:11:25 / 07:11:31 / 07:11:35，共 5 轮） |
| Bash | 14 |
| `Write test_blinkist.py`（自测） | 1 |

**一次写对的部分**：45 步漏斗的步进循环骨架（v1 就跑了 45 步）。
**改出来的部分**：邮箱兜底（风控）、`_clean_pages()`（空白页）、当日失败计数（人的要求）。

### 1.4 验证期：迭代形状（失败模式是重点）

| 跑次 | 时刻 | 走到 | 结局 | 失败模式 |
|---|---|---|---|---|
| bk-test-01 | 06:35 | **S48**（全程） | ✗ | 终点填了邮箱提交，站点回 `couldn't create your account` → **测试邮箱被风控拒**（不是脚本 bug） |
| bk-test-02 | 07:08 | S3 | ✗ **假成功** | URL 变空 → 脚本判「离开 onboarding → 视为成功」→ **空白页目标抢占** |
| bk-test-03 | 07:10 | S2/S3 | ✗ | 卡在 `mentions`，同一步重复，30 秒/步 |
| bk-test-04 | 07:12 | S8 | 截断 | `timeout 400` 到点，人叫停 |
| **生产 25900548** | 07:23 | 全程 | **✓ 3m34s** | 真邮箱一次过 |

**四个本机跑，零个干净成功；最终的成功是生产任务。** 而且人必须先把黑名单解开、放真单进来，
才有那个真邮箱 —— **最后一次验证是人在生产里完成的，不是 agent 在测试环境里完成的。**
（`.claude` 记忆 `blinkist-adaptation.md` 原话：「此前失败确认只是测试邮箱被风控拒」。）

**`cdp eval` 的用途**（06–09 段，Bash 里 24 处 `cdp eval`，其中 blinkist 相关约 11 处）：

| 用途 | 例子 |
|---|---|
| 读状态 | `location.href` / `document.body.innerText` |
| 探控件 | 枚举 button、读 `aria-label`、读 `disabled`、看年龄选项 |
| **量「页面是不是白的」** | `readyState` / `bodyLen` / `buttonCount` / `totalEls` ← 定位空白页 |
| 核代理 | `fetch('https://api.ipify.org')`（2 次） |
| 判失败原因 | `errorShown`（找 `"couldn"`）、`inputValue`（看值有没有填进去） |

### 1.5 人的介入：19 次实质发言，5 次打断

blinkist 段（06:26–07:50，84 分钟）**19 次实质发言**（另有 5 条 `[Request interrupted]`）。
按类分（一人可多类）：

| 类别 | 次数 | 代表原话 |
|---|---|---|
| 只有人能做的生产动作（解黑名单/放单/合并/打回） | 5 | 「那把他黑名单放开我放几单进来看看？」 |
| 策略/设计决策 | 4 | 「要不这样这个 py 脚本特殊处理下呗，搞个本地当天记录就行…机器当天就是连续错 3 单就停掉就行了」 |
| **人肉看屏 / 看日志报异常** | 4 | **「莫名其妙会打开一个空白的页好像」** |
| 质量追问/验收 | 4 | 「应该不会有隐性 bug 吧，比如线上的容器没权限写入啥的导致控不住啥的」 |
| 目标/任务框架 | 2 | 「这个占比很大，希望能借用 mcp-cdp 来尝试写一个相当可靠的脚本可以吗」 |
| 站点/基建知识补给 | 2 | 「cdp targets 无 type 字段」（直接贴了代码） |
| 叫停/节奏 | 2 | 「OK 停掉吧，等任务吧」 |

**最有价值的一条是 `07:09:00` 那句「莫名其妙会打开一个空白的页好像」。**
它不是抱怨 —— 它是**只有人能给的传感器读数**。agent 当时正在跑，看不到自己开的窗口长什么样；
人看见了。这句话之后 agent 才去 `eval` 量 `bodyLen`、`curl /json/list`，才发现
「URL 为空的 page 目标排在 `/json/list` 首位，cdp CLI 误选中它」，
于是有了 `_clean_pages()`。**没有这句，bk-test-02 的假成功会以「成功」进入生产。**

其余几条是**运营/基建知识**，agent 在文件里查不到：站点任务占比、测试邮箱被风控拒、
「我的代码是部署在很多机器上的」（所以本地 amend 没用）、「bitsh close 没用必须 cdp close」。

---

## 二、能力对照表

判定口径 —— **用到了 ≠ 必需**。只有「拿掉它这一步就做不成」才记「必需」。

| 用到的能力 | 那次怎么用的（证据：次数/例子） | 今天 agent 有吗（七工具 + 模板） | 结论 |
|---|---|---|---|
| **1. 页面内任意 JS 求值（读）** | MCP `evaluate_script` **17 次**；验证期 `cdp eval` 约 11 处。用途含读 url/body、枚举可点元素、读 aria-label、读 disabled | **部分（2026-09-17 实测，原「大体覆盖」的说法要收回）**：url/正文/枚举元素 ✅，但**本行自己列的两个读法恰恰都没有** —— 无 `aria-label`（`Disagree`/`Back` 在模型里 0 次，Back 与选项同样是**无名的一行**）、无 `disabled`；另无 `inputValue`（填值前后模型逐字节相同） | **补进工具面**（不是「两处具体缺口」而是 **3 处**：`aria-label` / `disabled` / `inputValue`；见 §4.1） |
| **2. 一次调用里跑「页内探索机器人」循环** | 06:30/06:31/06:32 三次 `async` 走流程器，22/24/26 轮，含 sleep+读+判断+点击+判导航，回 trace 数组 | **没有**：七工具都是单步；无 eval。要探 30 步就得来回 30 次 | **补进工具面** ← 本片最贵的一件 |
| **3. 结构化 trace 回模型**（不是截图） | 上面三次各回一个 `trace[]`：每轮 url/按钮标签/输入框/是否导航。**「哪一轮卡住」是从这个数组看出来的** | **部分**：`observe` 回页面模型、`diff` 回布尔；都不是「自己定义的、跨多轮的轨迹」 | **补进工具面**（随 2 一起；否则「卡在第几步」只能靠人念日志） |
| **4. 在页内 `element.click()`** | 机器人里 `target.click()`（探索期点击**全走 JS**，没走拟人 click） | 没有（只有拟人 `click`，要 selector） | **无关**：产物改为走 `cdp click`，更好；探索期只要「能推进」 |
| **5. React 兼容填值探针** | 06:34:27：`HTMLInputElement.prototype` value setter + `input`/`change` 事件 | 部分：`cdp form` 就是干这个的（真手势） | **无关**：`form` 已覆盖；探针只是探索期验证机制 |
| **6. 量「页面是不是白的」** | 07:11:06：`readyState` / `bodyLen` / `buttonCount` / `totalEls` → 坐实空白页 | **部分**：`observe` 给 `page_text` + diagnostics，但**没有 `disabled` 字段**、也没有「元素总数/readyState」这类粗指标 —— **2026-09-17 实测证实**（`disabled` 在模型里 0 次；同一按钮 disabled 翻转过而 action 逐字节不变） | **补进工具面**（轻量，但这次它是定位根因的那一下） |
| **7. 目标列表直查（`/json/list`）** | 07:11:06：`curl http://…:61129/json/list` + `cdp targets/active/close`。根因是「空 URL 的 page 目标排在首位，CLI 误选中」 | **没有**：七工具只作用于「当前页」，**没有 target 概念**，拿不到「还有别的 target / 它是空的」 | **补进工具面**（`observe` 的 `expect_url` 能「拿错页就报错」，但不告诉你有几个页、哪个是空的） |
| **8. 出口 IP / 代理核验** | 06:39:56 两次：`fetch('https://api.ipify.org')` + 查 51 gost 链路 | 没有 | **由人来补（控制台）**：属环境/harness，不是页面能力 |
| **9. 开/关位窗口、指定机器** | `bit.sh open 192.168.1.222 <id>`、`bit.sh close`、`cdp close --all`（每次测试都要重开窗口） | 没有（harness 的职责；见 `explore-efficiency` 的 E1/E5：窗口 ~8 分钟自己死） | **由人来补（控制台）** |
| **10. 人肉看屏** | 人的 4 条观察类发言。**「莫名其妙会打开一个空白的页好像」直接改变了走向** | 没有（agent 看不到人看到的那个窗口） | **由人来补（控制台）**：这条正是规格 §6.2「人每一步都在」的实证 |
| **11. 生产侧动作**（解黑名单/放单/合并/部署/打回） | 人的 5 条。**最终成功那次验证就发生在生产里** | 没有 | **由人来补（控制台）** |
| **12. 运营/基建知识** | 「占比很大」、「测试邮箱被风控拒」、**「cdp targets 无 type 字段」（直接贴代码）**、「bitsh close 没用必须 cdp close」 | 部分：git/log/FMR 考古 agent 能做（06:26–06:28 那 5 分钟是 Claude 自己做的）；**头脑里的那部分做不到** | **混合**：可查的自己查；不可查的**由人来补** |
| **13. 通用步进循环 + 每轮决策**（产物本体） | `for step in range(1,70)`：每轮按 DOM 决定「填邮箱+提交 / 点选项 / 点 Continue」，以 pathname 变化判推进 | **没有**：`agent/template.py` 的 STATES 是**固定步骤表**，`for state in STATES: for step in steps`，**只走一遍**，唯一分支是 `when` 不匹配就整组跳过 | **补进工具面** ← 见 §3.1 |
| **14. 以 pathname 变化为推进判据 + 超时重试/升级** | 未推进 2 次 → `_clean_pages()`；5 次 → `scrollBy(0,300)` 再来 | 部分：模板有 `stuck`/`stalled` 计数与 `STUCK_LIMIT` 早停，但**升级动作**（清页、滚动重试）表达不出来 | **补进工具面**（表达力） |
| **15. 从候选里随机取一个**（反检测） | `_click_option` 里 `random.randint(0,999) % len(cands)` | 没有：模板 target 是**声明式回退链**（selectors[0]→[1]→重新 observe），不是「在活 DOM 上过滤出集合再随机取」。**2026-09-17 实测又添一层**：连「寻址到那一个」都不成立 —— `observe` 的 `selector` **不保证唯一**，`cdp click` 对歧义**不报错**、静默取文档序第一个（Continue 的 selector 首个匹配是 **Back**，实测点下去真的退了步）。`blinkist.py` 是打了 `data-bk<rand>` 标记才寻得准的 | **补进工具面**（轻 → 实际不轻：寻址不可靠会**静默改变漏斗状态**） |
| **16. 截图看一眼** | 06:29:16 一次，用于认 CTA（「右上角蓝色 Start your free trial 按钮」） | **有**（`screenshot`） | **无关**：已覆盖，且那次也不是主力 |
| **17. `goto` 兜底导航** | `_enter_funnel()`：找不到 CTA 链接就直接 `window.location.href = MATRIX_URL` | **有**（`goto`） | **无关** |
| **18. `wait` / 拟人停顿** | `_dly(0.6,1.2)` 等随机停顿；robots 里 `await sleep(1200)` | **有**（模板 `wait` + 内置随机停顿） | **无关** |

---

## 三、结论：要做到同样的事，今天差哪几件

### 3.1 (a) 应该补进工具面的

按「不做就做不成」排序，**前两条是硬缺口**：

1. **「一次调用跑一段探索循环」的能力**（表 2 + 3）。
   那次能在 6.5 分钟里摸清 30+ 步，靠的是把循环放进页内。
   今天没有 —— 探 30 步就要 30 个来回，还要把「上一轮看到什么」靠上下文续着。
   **这是探索效率的主要成本项**（与 `explore-efficiency` 的 E6 是同一件事的两面）。
   形态未定：可以是「受限的 eval」，也可以是「`observe` 加一个 `repeat_until` 的原语」；
   **但「一次一个来回」这个形态，是这次能成功的必要条件。**
2. **模板得能表达「通用步进循环」**（表 13 + 14 + 15）。
   `blinkist.py` 的可靠性**明写在设计要点第一行**：「不硬编码步骤清单，每轮按 DOM 判断该做什么」。
   `agent/template.py` 的 STATES 是固定步骤表、只走一遍 ——
   **今天这个模板产不出 blinkist.py 的设计**，哪怕 agent 把页面看得清清楚楚。
   这条**印证了已知限制**：模板表达力（click/form/scroll/goto/wait + 固定步骤表）
   装不下「循环 + 每轮决策 + 推进判据」。
   补法未定，但至少要有：**循环**、**每轮的动作选择**、**以 URL 变化为推进判据**、
   **未推进时的升级动作**。
3. **`observe` 补 `disabled`**（表 6）—— **2026-09-17 实测已定，真缺**（§4.1）。
   那次定位 `/matrix/certain` 卡住，靠的就是 `disabled: true` ——
   它告诉走器「别点这个 Continue，去找 aria-label 图标按钮」。
   今天 `Action` 结构体（`tools/cdp/internal/observe.go:143-163`）**没有 disabled 字段**，
   `observe.go` 里也搜不到 disabled；**实测**：同一按钮 `disabled` 翻转，action 逐字节不变。
   **禁用的按钮会以普通候选的身份递给 agent**，点了 `cdp click` 还 **exit 0** ——
   **一次静默空点被报成成功。**

   **同一次实测还挖出两条同源的缺口（原分析没列）**：
   **① `observe` 不给 `aria-label`**（表 1）。DOM 里 Back 与三个选项**都只有
   aria-label、没有文字**，而模型**只给 `text`** → 四者在模型里**同样是无名的一行**；
   `blinkist.py` 的 `_click_option()` 正是靠 aria-label **排除 Back** 的。
   **② `selector` 不唯一而 `click` 不报错**，静默取文档序首个匹配 ——
   Continue 的 selector 首个匹配是 **Back**（实测点下去真的退了步）。
   详见 §4.1。
4. **「页面是不是白的 / 有几个页」的粗指标 + target 概念**（表 6 + 7）。
   那次根因是「URL 为空的 page 目标排在 `/json/list` 首位，CLI 误选中它」。
   七工具里**没有 target**，agent 拿不到「还有别的页、它是空的」。
   连带风险：`blinkist.py` 的 `_clean_pages()` 就是为这个写的 ——
   **如果 agent 产不出这个防护，站点上一出现空白页就是假成功。**

### 3.2 (b) 应该由人在控制台里补的

这几件**不该**塞进工具面，塞了就是把人的职责藏进 agent 里：

1. **人肉看屏**（表 10）。那次最关键的一次介入是「莫名其妙会打开一个空白的页好像」。
   这不是抱怨，是**传感器读数**。规格 §6.2「人每一步都在」在这里有了实证：
   agent 看不见自己开的那个窗口长什么样，人看得见。
   **控制台该做的是让人更容易看见**（实时看屏 / 截图流），不是让 agent 假装看得见。
2. **生产侧动作**（表 11）：解黑名单、放单、合并、部署、打回。
   **本次的最终验证就发生在生产里**（任务 25900548）—— 因为本机测试邮箱被风控拒。
   这一环 agent 不该也做不到替人决定。
3. **运营/基建知识**（表 12）：「blinkist 占比很大」、「测试邮箱被风控拒」、
   「我的代码部署在很多机器上」、「bitsh close 没用必须 cdp close」。
   这些是**人脑子里或生产环境里的**，不在仓库里。
   （能查的那部分 —— git 考古、FMR 配置、历史日志 —— Claude 那次自己做了，今天也该由 agent 做。）
4. **环境**（表 8 + 9）：位窗口开/关、指定机器、代理链路核验。

### 3.3 (c) 不需要的

1. **页内 JS 点击**（表 4）：探索期走 JS 是因为快；产物走 `cdp click` 更对。今天已经有。
2. **React 兼容填值探针**（表 5）：`cdp form` 已经是真手势，比探针强。
3. **截图**（表 16）：有，且那次也只用了 1 次，不是主力。
4. **`goto` / `wait` / 拟人停顿**（表 17 + 18）：都有了。

### 3.4 这片印证了哪些已知限制

| 已知限制 | 本片证据 |
|---|---|
| **模板表达力不够**（固定步骤表 vs 通用循环） | `blinkist.py` 设计要点第一行明说可靠性来自「不硬编码步骤清单」；STATES 做不到 —— §3.1.2 |
| **看不见页面 = 站不住脚**（`blind-generation-deadlock`） | 那次 6.5 分钟里 17 次 JS 求值、0 次快照；产物的选择启发式是从页内机器人**抄**下来的 —— 探索与产物是同一件事 |
| **静默失败最贵**（模板 §4 那条） | bk-test-02 的**假成功**：`URL 变空 → 判「离开 onboarding = 成功」`。今天若不出 `_clean_pages()` 这类防护，同样的假成功会进生产 |
| **人每一步都在**（规格 §6.2） | 19 次实质发言，其中 4 次是人肉看屏；`07:09:00` 那句直接改变了走向 |
| **运营描述是回忆不是规格**（`blind-generation-deadlock`） | `blinkist_research_desc.txt`（8/20 的运营描述，`轮次:30`）与 9/11 实测出的 45 步漏斗**对不上**；描述里没有 onboarding/matrix/aria-label 图标按钮 |

---

## 四、已知不确定

| # | 不确定 | 什么能定它 |
|---|---|---|
| U1 | **「一次跑一段探索循环」到底值多少轮**。我数得出那次是 3 次机器人调用顶了 ~30 步，但**没有对照组**（不知道一步步来回要多久） | 拿今天这套在同一个站上真跑一次探索，数总轮数与墙钟 |
| U2 | ~~**`observe` 的页面模型够不够替代那 17 次 eval**~~ **✅ 已定（2026-09-17 实测）：不能** —— 逐条比对后缺 **3** 种读法（`aria-label` / `disabled` / `inputValue`）。见 §4.1 | 已跑：`docs/probes/2026-09-17-observe-blinkist/` |
| U3 | ~~**`disabled` 是不是真缺**~~ **✅ 已定（2026-09-17 实测）：真缺**，且「被别的方式过滤掉」这个反假设**被证伪**（禁用的 Continue 就在 `actions` 里，以普通候选身份）。见 §4.1 | 已跑：同上 |
| U4 | **生产任务的最终成功有多少是脚本的功劳**。生产 25900548 一次过（3m34s），但本机 4 次都没干净成功；差异里混了「真邮箱」「生产窗口环境」「站点当时的 A/B 变体」三个变量 | 用真邮箱在本机再跑 2~3 次；或翻生产 `logs/blinkist.log` 看后续任务的成败率 |
| U5 | **`/tmp/blinkist_*` 里那些 45 字节的 `research_result*.json`**（内容形如 `Remote end closed connection without response`）我没有逐个读，**不能确定** 8 月那轮的死因是「规则折叠到尽头」还是「环境不通」 | 读 `/tmp/blinkist_research_result*.json` 全部 + 搜 8/17–8/20 会话。**注意**：那是另一个 project 的会话（`89e8f49e` / `5095a4b1`），不在本次核对范围 |
| U6 | **那 19 次发言里「只有人能做的生产动作」我按关键词归的类**（5 次），可能有 ±1 的误差 | 逐条读 §1.5 引用的时间戳原文（已列在 §1.1 时间线里） |

### 4.1 U2 / U3 结论（2026-09-17 实测，跑出来的不是推出来的）

**证据**：`docs/probes/2026-09-17-observe-blinkist/`（原文 JSON + 人话渲染 + DOM 真值 + 复跑步骤）。

**产地**：私有 Chrome（Xvfb 上的 **headed**，独立 profile，独立端口 9411），
**不是**生产指纹、**不在**美国代理后面。`observe` 的**字段集**是纯页内计算的，
与指纹/geo 无关，所以两个问题照样能答；页面本身没有被 geo 拦，`/matrix/certain`
与 `/matrix/growth-areas` 都**直达可达**。但**不能**由此推断生产的 blinkist 就是这样。

#### U2：不能替代那 17 次 eval —— 缺 3 种读法

逐条比对（17 次 eval 的用途见 §1.2 / §1.4）：

| eval 问了什么 | `observe` | 证据 |
|---|---|---|
| `location.href` | ✅ `url` | — |
| `document.body.innerText` | ✅ `page_text` | — |
| 枚举可点元素 | ✅ `actions` | — |
| 「页面是不是白的」 | ⚠️ 可推（`page_text` / `actions` 为空），**无 `readyState`、无元素总数** | `diagnostics: []` |
| `errorShown` | ✅ `page_text` 里有就能看到 | — |
| **`aria-label`（选项与 Back 的名字）** | ❌ **没有** | 全 JSON 里 `Disagree`/`Not sure`/`Agree`/`"Back"` 各 **0** 次 |
| **`disabled`** | ❌ **没有** | 全 JSON `disabled` **0** 次（见 U3） |
| **`inputValue`** | ❌ **没有** | 填值前后两份模型**逐字节相同** |

**本次实测新发现的一条（原分析没列）**：**`aria-label` 不在模型里。**
DOM 真值：三个选项是 `text:""` + `aria-label: Disagree / Not sure / Agree`，
Back 是 `text:""` + `aria-label:"Back"`。`observe` **只给 `text`**，
于是模型里 Back 与三个选项**同样是无名的一行**，只能靠几何猜
（Back = `region:"header"` + `24×24`）。
而 `blinkist.py` 的 `_click_option()` 正是靠 `label(b)`（含 aria-label）
**排除 Back** 的 —— 06:31 那轮卡住、06:32 那轮通了，靠的就是这个读法。
**规格问的那句「能不能报出选项按钮无文字」：能（`text:""` 如实报了）；
「Continue 禁用」：不能。**

**分组也没表达**：`option_groups` 在 `/matrix/certain`（3 个图标选项）与
`/matrix/growth-areas`（7 个有文字选项）**都是 `[]`**。组能**推**出来
（同 `selector` + 同尺寸 + 相邻 bbox），但没有字段这么说；而且 `peer_count: 8`
把 Continue 也算进同一组，**当不了分组信号**。

#### U3：`disabled` 真缺，且后果是「静默假成功」

最硬的一条：**同一 URL、同一页面，DOM 里 Continue 的 `disabled` 从 `true`
翻成 `false`，而 `observe` 给它的整条 action 逐字节相同。**
两份模型的差异只有 `page_text` 和选中项的一个 `bbox` —— **没有任何字段能分辨这两种状态。**

反假设「禁用的按钮被别的方式过滤掉了」**被证伪**：禁用的 Continue **就在
`actions` 里**，`visible: true`、`occluded_by: null`，是一副完全可点的样子。
`stability` 也不代替它（Continue 是 `medium`，Back 同样是 `medium`）。

**后果链（每一步都实测过）**：Continue `disabled:true` → `observe` 当普通候选递出 →
用它唯一的 positional 选择器 `cdp click`，**exit 0** 且回了像样的坐标
`{"x":642.87,"y":682.38}` → **什么都没发生**，URL 不变、页面不变、**无任何报错**。
**一次静默的空点被报成成功** —— 正是本项目最贵的那类失败。

#### 顺带挖到的一条（比 U3 更险）：`selector` 可能**指向 Back**

模型给 Continue 的 `selector`（`button.cursor-pointer.items-center`）在 DOM 里
**匹配 5 个按钮**，文档序是 `[0] Back [1] Disagree [2] Not sure [3] Agree [4] Continue`
—— **`querySelector` 取到的是 Back**。实测点下去，坐标 `(170.77, 81.86)`
落在 Back 的 bbox `[160,78,24,24]` 里，URL 从 `/matrix/certain`
变成 `/matrix/checkpoint-focus-type`。**点的是 Back，不是 Continue** ——
正是 `blinkist.py` 设计要点里「排除 Back(每步都有, **误点会倒退**)」要防的那一下。
`cdp click` **对歧义选择器不报错**，静默取第一个匹配。

模型**有**唯一候选（`alternates[0]` 是 positional 的，实测唯一），所以正确的
消费者能自救 —— 但**必须自己知道要去验唯一性**，而 MCP 的工具说明还写着
「优先 stability=high 的」（Continue 是 `medium`，这句帮不上忙）。

#### 因此，要补的是（按行动价值排序）

1. **`disabled` 进 `Action`** —— 不做则「点了个死按钮」永远是静默假成功（U3）。
2. **让 `selector` 的唯一性可见**，或**让 `click` 对歧义选择器报错** ——
   现在它会静默点到 Back（比 U3 更险，因为它**改变了漏斗状态**）。
3. **`aria-label` 进 `Action`**（哪怕只作为一个 `label` 字段）——
   那一轮真正解开卡点的读法就是它。
4. 组的表达（`option_groups` 现在只认表单控件式的组，按钮式选项组认不出）。

---

## 五、一句话回答

**做不成 —— 差三件，且第一件是硬的。**

那次「聊出来」的东西，**不是「一个脚本」，是「一段在页内跑、每轮看 DOM 再决定」的探索过程被固化了**：
6.5 分钟里 17 次 JS 求值（其中 3 次是整段循环机器人）+ 81 分钟里 19 次人的介入，
最后产物的选择启发式**就是从页内机器人抄下来的**。

今天这个 agent 有 `observe`（比 a11y 快照强得多），但：
1. **不能在页内跑循环** → 探索只能一步一步来回；
2. **模板装不下「通用步进循环」** → 就算看得一清二楚，也产不出 `blinkist.py` 那个设计
   （它的可靠性明写在「不硬编码步骤清单」上）；
3. **没有 target 概念、`observe` 没有 `disabled`** → 那次的两个关键发现
   （空白页抢占、Continue 禁用）今天看不见。
   （**2026-09-17 实测补一刀**：后一个已坐实，且还多缺一个 `aria-label` ——
   而它正是 06:32 那轮「通了」所靠的读法；另有 `selector` 不唯一、
   `click` 静默点到 Back 这条更险的。见 §4.1。）

另外**有一件不该补**：人肉看屏。`07:09:00`「莫名其妙会打开一个空白的页好像」
是本片里单条价值最高的一次介入，而它**恰好是 agent 结构上看不到的东西**。
控制台该让人更容易看见，而不是让 agent 假装看得见。
