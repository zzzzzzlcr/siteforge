"""Task 5：浏览器 Agent 的工具循环（ReAct over MCP）。

    explore(url, goal, budget) -> Journey
    Journey{steps: [{state, action, target, result, note}], notes: [str]}

## 它为什么是「本项目第一次真的走依赖链」

Task 1 的 spike 证明了模型**肯**调工具（24 跑 0 编造、47 次真 observe），但它自己承认
一件事没验：`observe` 是**只读**的，模型有理由把**三次 observe 并进同一轮** ——
那测到的「连续 ≥3 轮」是**同一件事看了三遍**，不是「做一步 → 看结果 → 再决定」。

`click` / `form` 会**改页面**，天然不能批量：第二眼的页面只可能是「第一眼 + 那一下点击」
的结果。所以这条循环跑起来，模型看到的每一轮都是**上一轮动作造成的**新世界。
`tests/test_browser_agent.py` 里那条真浏览器的端到端钉的就是它。

## spike 量到的三条，在这里各是一个具体的东西（不是调参）

- **C1**（`max_tokens` ≥ 12000）：`MAX_TOKENS` 常量，**显式**传给 `run_tool_loop`。
  给少了最终答案是**空**的，而空答案看起来与「模型说没有」一模一样。
- **C2**（一轮里可能多个 `tool_calls`）：靠 `llm.run_tool_loop` 的逐条派发 + 我们自己的
  dispatch **每条都记账**。只处理 `tool_calls[0]` 的实现会静默丢掉观测 —— 这里丢掉的不只是
  一条结果，还有 Journey 里的**一步**（那一步发生过，账上却没有）。
- **C3**（终止认「没有 `tool_calls`」）：**那道门上的七个工具里没有 `done()`**，
  所以「不调工具」是唯一的正常收尾。`run_tool_loop` 认它，我们据此记 `model_done`。

## 人每一步都在（规格 §6.2/D16 —— 架构约束，不是「以后加 UI」）

`should_pause` 是**注入点**（计划三的 Console 接它）。它在**每一步之前**被问一次，
为真就**在下一步之前**退出：这里有两道闸 ——

1. 每个工具调用**之前**（dispatch 里）
2. 每一轮模型调用**之前**（包装客户端，`_Gate`）

两道都必要：模型可能一轮丢来五个 `click`，只在轮边界问一次 = 那一轮的五步全做完了才停。
停下来的信号是 `_Stop`，它**故意**继承 `BaseException`：`run_tool_loop` 对 dispatch 抛出的
`Exception` 是「记成一次工具失败、接着跑」—— 那对工具自己的错是对的，对「人喊停」是错的。

⚠️ **闸不只那一道人的**（Task 15）：同一个 `_stop_reason` 里还有「预算到顶」与
「**已经在页面上见到了人给的那句成功文案**」（`STOP_REACHED_SUCCESS`）。三条**共用**
上面那两道闸 —— 所以判据加在 `_stop_reason` 里就等于**每一个工具调用之前、每一轮模型调用
之前**都问了一遍，不必另开检查点。三条的**优先级**见 `_stop_reason` 的 docstring。

两条**同源**的诚实要求（R-12）：

- 人那道闸**自己抛异常**时，归一成「暂停」而不是「一次工具失败」（`_stop_or_raise`）。
  不然人的中断会静默降级成「有个步骤失败了，继续吧」—— 喊停没停，还没人看得出来。
- 模型每轮说的话由 `_Gate` **当场**记进 `notes`，不在收尾时统一记。`_Stop` 会穿过
  `run_tool_loop`，`rounds` 就此丢掉 —— 被暂停的那份 Journey 会**比没被暂停的少知道一截**，
  而人正是要靠那几句话决定要不要接着跑。**人被暂停时不该比没暂停时知道得更少。**

## Journey 与 `template.render()` 的接口（跨任务，Task 3 定）

`journey.states()` / `journey.fills()` 产出的就是 `render()` 要的 `states` / `fills`
（形状以 `agent/template.py` 的模块 docstring 与 `fixtures/reference_site.py` 为准）。
两件事因此被明确下来：

- **`Journey.steps` 是「agent 干了什么」**（含 observe / diff / screenshot —— 那些是感知动作）
- **`states()` 是「py 要重放什么」**：只留 click / form / scroll / goto / wait，
  且**只留做成了的**（没做成的动作不该在重放里再来一遍）
- `state` 归属 = 那个动作**发生在哪一页上**；换页（url 或正文变了）就换一个状态，
  状态的 `when` 就是**那一页**的页面判据（`url_contains` + `text_contains`）。
  ⚠️ `when` 生成时会**当场验一遍**（`when_holds`）—— 一条当时就不成立的 `when`
  在重放时会让整组步骤被静默跳过（`_applies` 就是这么写的），那是这类产物最贵的错。

## 「帧」是跟着 target 走的（2026-09-17 补）

第二条题起，漏斗常常整段活在一个**跨源 iframe** 里（实测 gowizard：`chameleon-…` 部件）。
`observe` 早就把每条元素的 `frame_path` 报出来了，但账本里原先**没有帧**这个键 ——
于是产物重放时那个选择器在主帧命中 0、在帧内命中 1，**每一遍都必挂在同一步**。

现在：`_frame_of(args)` 把「这一步在哪一帧里做的」记进 `target["frame_id"]`
（主帧 = `""`，见那个函数的 docstring：以**模型真给的那个参数**为准，因为 cdp 就是拿它去
那一帧里解析选择器的），产物在 click / form 时把它交给 `cdp --frame-id`。

⚠️ 有四处**已知有损**，不藏着：① 字段的 `source`（form-file 的键）与 `fallback` 是按
**标签文字猜的**，猜不准时宁可给保守的随机值，也不编一个假的值；② **嵌了两层以上的子帧**
表达不了（`_frame_id_of_path` 的第三种形状）—— cdp 换坐标只补目标帧 owner 那一层的原点，
中间几层没人补，所以那种 target 会退回主帧（够不着 = 老老实实失败）而不是拿一个会**点偏**
的帧号去试；③ **`goto` 的帧**没进账本（MCP 的 `goto` 也收 `frame_id`，但重放那条路
只导航主帧）；④ `wait` 只有「等一会儿」这一种形状（页面上没有可判断的「加载完成」信号）。

### `scroll` 这一步：两边口径已经对齐（2026-09-17 第二轮改）

**原先这里写着「工具是滚到某个元素、骨架是滚多少像素，重放只能滚一屏」—— 那句话是错的**，
而且正是它让产物发了一条**永远跑不通**的命令：产物拿 `pixels`（`"400"`）当选择器喂给
`cdp scroll`，而 CLI 的位置参数**是选择器**（`scroll [selector]`）——
真窗口实测 `cdp scroll 400` → `Error: scroll mouse wheel failed: element not found`。

现在：账本里这一步就是**它的元素**（`target.selectors` + `target.frame_id`，与 MCP 的
`scroll` 工具同一件事），产物把它交给 `cdp scroll <选择器> [--frame-id <帧>]` ——
**两边说的是同一件事**，不再有「像素」这一层假映射。

## 计划模式：描述当**检查点清单**（2026-09-17，Task 3）

人给的描述里如果有一串编号步骤，这趟就进**计划模式**（`explore(..., plan=Plan)`）：

- **简报分两版**（`_brief`）。有计划 → **清单（原话）+ 原文（`Plan.raw`，一字不删）**
  + 三条走法；没计划 → **与今天逐字节相同**（B4，那颗回归钉子在
  `tests/test_browser_agent.py` 里把今天那串字节**硬编码**着，改措辞会当场红）。
  ⚠️ 「原文」不是陪衬：真描述的键值头里夹着**约束**（`禁止点击: Cookie Policy,…`），
  只给清单等于把它吞掉 —— 吞了模型就会去点**运营明写的禁区**。
- **规矩进 `_SYSTEM`**（那是稳定层），计划本身是**每轮的事**，走简报那一版。

位置 / 偏离 / 停滞由 `_PlanWatch` 记账，它挂在 `_Gate` 上 —— 那是**唯一每轮都在场**的
东西（连被人打断的那条路也在场；与「叙述归 `_Gate`」是同一个道理）。三条硬规矩：

1. **不判「这一步做完没有」**（D11：observe 只给感知）。位置**只由模型报的** `【第 k 步】`
   推进；没报 / 报了个不存在的号 → 位置**不动** + 一句人话（§2.4：说不出就是不猜）。
2. **绕开（分支）不是偏离**：往前跳号 → 中间那几个记 `jumped_over`，**不进 `deviations`**，
   而且**照样算前进**（停滞计数清零）。`deviations` 只装**模型声明**的
   「描述说…页面上是…」（§2.3.1：两者混在一起，这条账会被日常噪音灌满，信号就废了）。
3. **唯一的自动干预是「停」**：连着 `Budget.stall_limit` 轮「位置没动 + 页面没变 +
   没有一步做成了」→ `_Stop("plan_stalled")`。判据全是感知 / 声明，**没有一条语义判断**。

⚠️ **两处已知读法**（复审要看的，照 Task 2 的规矩摆在这里，行为不改）：

- **「位置前进」= 位置动了**，不判方向。往回跳（第 5 步 → 第 3 步）在 §2.4 里明写
  「照记、不判它该不该」，所以这里也只算「动了」；要改成「只有往前才算」，那是在替人判。
- **矛盾那一轮位置不前进**（§2.3(b)「位置标记停在这一步不前进」）。这里取的是
  「**这一轮不算推进**」，**不是**「位置从此钉死在第 k 步、后面永远不许再往前提」——
  后者会让一次矛盾把整趟都判成停滞，而设计注只说过「停在这一步」。
"""

from __future__ import annotations

import hashlib
import inspect
import json
import pathlib
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from . import events, llm, plan as plan_module, shots, tools

#: **C1**：`deepseek-v4-*` 把思考 token 算进 `max_tokens`。给 4000 时最终答案会**静默变空**
#: （spike 实测 1/8，提到 12000 后 5/5 正常）。别往下调 —— 那不是省钱，是把能力削掉。
MAX_TOKENS = 12000

#: 预算的目的是**防跑飞**，不是省钱（规格 §6.5 / P5）。到顶就停，且**明确说出来**。
#:
#: ⚠️ **2026-09-20 放开过一次**（原来 30 / 20）。为什么：真站实测 `job-62b192d2aca4`
#: 那一趟**是正常的** —— 它一路在点问卷的 `Get Free Quote` / `Continue`，最后一句
#: 「Now clicking Continue to advance the quiz.」—— 然后停在 `budget_rounds`（20/20）。
#: 而**同一个站的确定性重放脚本要走 25 步**
#: （`forms/_remote/www.gowizard.com_auto-warranty_.py`：19 个 state 组、`steps` 相加 = 25；
#: 分解是 `goto 1 / click 19 / form 5`）
#: ⇒ **20 < 25：那一趟结构上就走不完**，不是模型不行。
#:
#: **这两个数是按什么推出来的**（可复算，别靠回忆）：
#: 4 趟被轮预算钉死的真站探路（`runtime/explore/job-*/`），每一轮**成功推进**的动作数实测
#: 0.450 / 0.450 / 0.450 / 0.400（动作 = click/form/scroll/goto；⚠️ **只数
#: `result.ok == true` 的那些** —— 「点了没做成」不叫推进。2026-09-20 复审复算过：
#: 我第一版把 `ok == false` 也算进去，四个数会偏成 0.450 / 0.500 / 0.500 / 0.600）。
#: 按**最坏**那个折算：`25 / 0.400 = 62.5 → 63` 轮，再加「收尾那一轮」
#: （模型不调工具、只说话）⇒ **64 轮**（⚠️ 是**估计**不是下界 —— 见下面那段保留）。
#: 取 **80**（1.25 倍）吸收「走错几步 / 多观察几次」。
#: 步数取 **100 > 80**：实测**每一轮正好一次工具调用**（四趟都是 `rounds == steps`），
#: 所以步数必须不先于轮数卡死 —— 否则停因变成 `budget_steps`，而模型**连收尾那一轮都拿不到**。
#: 步数只在「模型一轮里塞好几个调用」（真跑飞了）时才兜底。
#:
#: ⚠️ **那四个折算率来自一个偏的子集 ⇒ 上面那个 64 是「估计」，不是「下界」**
#: （复审 2026-09-20 §5④ 补的那条）：4 条样本**全是同一个站**（gowizard），而且
#: **全是被 20 轮钉死的那一档** —— 能走完的早就走了（走完那几趟只用 2–4 轮）
#: ⇒ 这个子集天然是「动作密度最低」的那一类。**偏在哪一边我没量**（要别的站、别的档的
#: 样本才量得出）⇒ 它是「按手头这几条折出来的估计」，**不是**一条从无偏样本上量出来的下界。
#:
#: ⚠️ **这一放，松开的是【唯一】那根刹车**（下一个人再调它之前必须知道这一条）：
#: `stall_limit`（停滞判据）**在生产路径上一次都不参与** —— `explore` 只有拿到 `plan`
#: 才建那个 `watch`（`browser_agent.py` 里 `if plan is not None and plan.actionable()`），
#: 而生产那条路上**没有任何一处**交得出 `plan`：图那一跳（`graph._explore` →
#: `deps.explore(...)`）不带 `plan=`，服务那一层（`service._explore_for` 里的
#: `run(url, goal, budget=None, …)`）连这个参数都没有，而 `Deps.explore` 的默认值
#: `browser_agent.explore` 的 `plan` 默认就是 `None`。
#: ⇒ 20 → 80 **不是**「两根上限里松开了一根」，是**把那唯一的一根翻了两番**：
#: 一个卡住的模型在一趟里最多能对**真站**做 80 个会改页面的动作（原先 20 个），
#: 墙钟从 ≈50–85 秒涨到 ≈200–340 秒（四趟账本实测 2.5 / 3.35 / 4.0 / 4.25 秒每轮）。
#: 用例：`tests/test_budget_calibration.py::
#: test_the_production_path_never_hands_explore_a_plan_so_the_round_budget_is_the_only_brake`。
#:
#: ⚠️ **「模型自然要用多少轮」这个数今天仍然没量到**（量它要跑一趟真模型，那一轮不许）。
#: 上面 64 是个**按手头那几条（偏的）样本折出来的估计**（见上），不是「模型自然要用多少」。什么时候再校准：
#: **放开之后的第一趟真站探路**，读 `attempts.jsonl` 的 `rounds` 那一格 ——
#: 落在新预算 80% 以上（>64）就说明还是紧的，该再放。
#: ⚠️ 那个 **>64 的门限现在恰好压在 64（估计）上** —— 所以**第一批样本就可能响**：
#: 响了说明还是紧的；**不响也不能说明松**（估计若偏高，真实需求本就在 64 以下 ——
#: 见上：「偏在哪一边我没量」）。⇒ 两种结果都要记下来；但**别把「响」当成误报**。
#: （2026-09-20 修复轮 4 改准：原来写的是「第一批样本就会响**是预期的**」——
#: 那句的前提是「真·下界就是 64」，而那个前提在同一轮里被我删掉了 ⇒ 它不再被蕴含。）
DEFAULT_MAX_STEPS = 100
DEFAULT_MAX_ROUNDS = 80

#: 骨架认的五个重放动作（其余动作出现在 STATES 里会被当成「产物写错了」）。
REPLAY_ACTIONS = ("click", "form", "scroll", "goto", "wait")

#: 停滞判据（§2.5）里的「**动作**」= `_SYSTEM` 规矩 2 里那几个**会改页面**的工具。
#: `observe` / `diff` / `screenshot` 是**感知**，不在里面 —— 一个只在那儿看来看去的模型
#: 本来就不算「在推进」（不然停滞判据永远响不了：看一眼也是成功的）。
#: ⚠️ 与 `REPLAY_ACTIONS` 现在几乎重合，但**不是同一件事**：那个是「产物能重放什么」，
#: 这个是「这一轮算不算做了事」。哪天要分家，改的应当是那一个。
_ACTIONS = ("click", "form", "scroll", "goto")

#: 「**动页面**」的动作 —— 步拍只对它们拍（设计注 §5.4）。
#: ⚠️ 从 `REPLAY_ACTIONS` **推出来**，不是手抄的第二张表：哪天它加了动作，这里跟着变
#: （手抄的表不会 —— 那个形状这个仓库栽过，叫「第二张手写名单」）。
#: `wait` 不在里面：它不动页面，拍它等于给每一步都留一张。
#: 与 `_ACTIONS` **今天同值，但不是同一件事**（那个问「这一轮算不算做了事」）—— 别合并。
MUTATING = tuple(a for a in REPLAY_ACTIONS if a != "wait")

#: 步拍**留在盘上**的上限（张）—— **每趟**（= 每次 `explore()`，一个 `_StepShots` 实例一个），
#: **不是每个目录**。到顶就不再拍，并往 `journey.notes` 写一句人话。
#: ⚠️ 口径读错就在这儿：同一个 job 目录会被**多趟**探路共用（`service.py` 的
#: `shots.dir_for(job_id)`），而生产里一个节点**最多 3 趟**（`graph.EXPLORE_ATTEMPTS`）
#: ⇒ **一个 job 目录的天花板是 3 × 40 = 120 张**（闸拍 `pause-<n>.png` 另算，不计入）。
#: 为什么不做成「每个目录」的：上限只数得出**本趟**写过的那些（收口删的名单也是本趟的，
#: 见 `finish()`）—— 数目录就得 glob，而那会把闸拍算进来、把别人的趟算进来。
#: ⚠️ **判在要落盘的那一刻**（`_take` 里数的是「此刻盘上属于本趟的张数」`_on_disk`），
#: 不是收尾才算 —— 数 `kept` 那种写法在结算钩子抛异常时永远到不了顶（复审实测：
#: 上限 2、坏 `_keep`、5 个 click ⇒ 10 条命令、运行期盘上最多 9 张）。
#: 为什么是 40：一张约 110 KB（17 次真跑实测的中位）⇒ **每趟**到顶约 4.4 MB，可以接受；
#: 而「不对劲的步」在一次典型探路里是**个位数**，40 是给异常情况留的余量。
MAX_KEPT_SHOTS = 40

#: 「**页面变了没有**」这条判据只对这几个动作成立 —— 产物侧同一张表在
#: `template.SKELETON` 的**模板字符串里**（`agent/template.py`，生成的 py **不能** import
#: `agent`，所以那一份必须自带）。**两份必须一致**，有哨兵盯着
#: （`test_the_two_diff_judge_tables_agree`）—— 不一致的话，产物会在
#: `form`/`scroll` 上按「判不了」办、而 agent 侧按「判得了」办，两边对同一步给出不同的留法。
#: 为什么填框/滚动不能拿它判：签是 `body.innerText`，**填框不改它、滚动也不改它** ⇒ 必然假阳性。
DIFF_JUDGES = ("click", "goto")

#: 连着几次工具调用没成才去问一次「窗口还活着吗」（§1.8）。
#: 为什么不是 1：一次失败在活窗口上再正常不过（选择器不对、元素还没渲染出来）。
#: 为什么不是 5：窗口死掉之后每一次失败的代价都是白等（`MCP_TIMEOUT_S` 那个量级）。
FAILURES_BEFORE_DEAD = 2

#: 计划模式里「这一轮没说清自己在第几步」那句人话（§2.4：**如实记「不知道」，不猜**）。
#: 两种情形**同一句**：压根没报、报了个清单上没有的号 —— 系统不去分辨这两件事，
#: 它们都只说明「这一轮它没说清自己在哪儿」。
_NO_MARK_NOTE = "这一轮没说自己在第几步（也可能是报了个清单上没有的号）—— 位置不动，不猜"

#: 模型报「描述与页面**矛盾**」的**说法**（§2.3(b)）：`描述说 … 页面上 …` 两半**同句**出现。
#: 这是**约定**（简报里写死了这个说法）—— 系统**不判**「这算不算矛盾」，
#: 它判不了也不该判（定性归账本和人），只判「它有没有按约定说出来」。
_CONTRA_RE = re.compile(r"描述.{0,80}?(?:说|写).{0,120}?页面上")

#: 一句话的边界（用来把报矛盾的那**一整句原话**摘下来）。
_SENTENCE_RE = re.compile(r"[。！？\n]+")

#: 一个状态的 `when` 里带多少字的页面文字（够认出「是不是这一页」，又不至于一改就失配）。
WHEN_SNIPPET_CHARS = 48

#: 判据里**钉不出正文那半条**时，写在 `when` 上的那句人话（`when["text_why"]`）。
#:
#: ⚠️ **为什么非要留这句话**（2026-09-20，gowizard 线①）：`when.text_contains` 是生成期
#: **从一次观测**取的一段子串 —— 站点对同一页给两种免责声明（实测 72 趟里 3 趟撞上 B）时，
#: 钉住 A 的那条判据在 B 那一趟**整组不成立**；而 `auto_warranty` 是 `start` 之后的
#: **第一个**状态组，它一跳过，「Reject All」「Get Free Quote」两个动作都没做
#: ⇒ 25 步里 24 步全跳过 ⇒ **0 次真实点击** ⇒ `no_success`。
#:
#: 而产物上「只有 URL 一条」与「本来就只有 URL」长得**一模一样**（都是 `{'url_contains': …}`）
#: —— 读的人分不出「查过了，只有 URL 稳」和「没钉出来，只好只钉 URL」。所以要**说出来**：
#: 生成期写进 `when["text_why"]`，产物开跑时照着它喊（`Filler._say_url_only_whens`）。
WHEN_TEXT_DROPPED = ("这个状态的判据**只有 URL**：生成期没能钉出一段稳定的正文（%s）—— "
                     "站点换一版文案，这一组就会**整组被跳过**。")
#: 「没钉出正文」的第一种原因：这一次观测**根本没读到正文**。
WHEN_NO_TEXT_WHY = "这一次观测没读到页面正文（`page_text` 是空的）"
#: 第二种原因：读到了正文，可**照它取出来的那条判据当场就不成立**。
#: ⚠️ 这一支眼下够不着（`_snippet` 取的是那一页正文自己的前缀，必然成立）——
#: 留着是因为它的**反面**才是要命的那件事：判据当场不成立却照样发出去，
#: 重放时会变成整组静默跳过。够不着 ≠ 可以删：删了它，「取出来的判据当场不成立」
#: 就会**静默**地发出去。（别把这句读成「有一条用例钉着它」—— 没有。）
WHEN_TEXT_UNVERIFIED_WHY = "照这一次观测取出来的那段正文，在这一次观测上就不成立"

#: 看着像轮换码的 path 末段长什么样（字母数字，可带 `-_.` 分隔；**还要含数字**才算，
#: 见 `_id_like`）。`cr640` / `gt1791-1` / `a3f9c2` / `12345` 都算，`checkout` 不算。
_ID_LIKE_RE = re.compile(r"[0-9a-z]+(?:[-_.][0-9a-z]+)*")

#: `result["page_text_head"]` 留多少字。
#:
#: ⚠️ **它不只给人看 —— 它是「有没有见到成功文案」这个判据唯一的取材处**
#: （`graph._explore_reached_success` 与 `_success_line` 都只读这一格）。
#: 2026-09-22 真事（`job-9b48f2b513ec`）：运营给的判据是对的，可这一格当时只留 **200 字**，
#: 而那 200 字恰好是**页脚**（`Privacy Policy Cookie Policy … Company registration number`）
#: ⇒ 判据一个字都进不去 ⇒ 两趟都判「没见到成功文案」⇒ **自动重探** ⇒
#: **又真提交一次表单**（运营在面板上看见的就是「明明成功了一直在重复」，最后他手动关窗止住）。
#: 8000 字：够装下一整页正文（含挂在跨源 iframe 里的那句成功文案），落盘还是 KB 级。
PAGE_HEAD_CHARS = 8000

#: `receipt`（契约 §二第 3 格）**逐字转抄的上限**：超过它就不抄了，只记下它有多大。
#:
#: ⚠️ 为什么是「不抄」而不是「截断」：`Journey.steps` 会进 checkpoint，而**已有两条判据**
#: 钉着账本一步的大小（`test_a_diagnostic_row_does_not_drag_the_rest_of_the_tool_return_in`、
#: `test_screenshot_vision.py::test_explore_hands_the_screenshot_to_the_model_and_keeps_the_journal_small`
#: —— 后者直接断言 base64 **不许进账本**）。截一段 base64 进去，两条都会红，而且那段
#: 前缀对读的人**一个字的用都没有**。所以超了就是一句「有多大、没抄」——**有损，但说出来**。
#:
#: ⚠️ 咬得到的只有**感知类**（`observe` / `screenshot` 的返回可以几十上百 KB）；
#: 动作类（click / form / scroll / goto）的回执就是 `{"ok": true, "note": …}` 或那句报错，
#: **远在闸下、永远逐字** —— 而契约 §六 那条验收要的正是动作类的回执。
RECEIPT_MAX_CHARS = 800

#: **单格**里一个字符串超过多少字符就不抄了（换成「有多大、没抄」）。
#: 一个值超过两百字，它就不是「回执」而是「内容」了 —— 而内容有它自己的去处
#: （`_summarize` 的摘要、`_raw_sig` 的指纹）。
#: ⚠️ **别拿 `PAGE_HEAD_CHARS` 当它的参照**（2026-09-22 起两者不再同一个量级）：
#: 这一格管的是**回执**里那些值（长了就是内容），而 `page_text_head` 是**正文摘要**，
#: 它**必须**留得够长 —— 成功判据就是在它里面找的（见那一格自己的注释）。
RECEIPT_STR_CHARS = 200

#: 状态名/字段名的兜底（页面 slug 取不出来时）
FALLBACK_STATE_NAME = "page"
FALLBACK_FILL_NAME = "field"
#: 「还没看过页面」那个状态的名字。它会被**预占**，免得页面 slug 与它撞名。
START_STATE = "start"
#: **插话之后那一轮它就收尾了** —— 往账上追的那句人话（设计注 §3.4 的「已知风险」/ R11，**原话**）。
#:
#: 为什么非记不可：探路那道门上**没有 `done()`**，所以「模型这一轮没调工具」与
#: 「探路走完了」在图上是同一件事（`stop_reason = model_done`）。人插的那句话**可能**
#: 就是它收尾的原因（那句话读起来像「说完了」）—— 而**系统分不出**「它是被那句话逼收的」
#: 还是「它本来就探完了」。分不出就**不许替它下判**：如实记一句，让人自己去看结论。
#: ⚠️ 判据只有一条：**这一轮真有人插的话**（`record["steered"]` 非空）—— 没人插话时
#: 一个字都不许出现（不然每次收尾都在说人那句话，那是编话）。
STEER_WRAPPED_UP_NOTE = "你插话之后它就收尾了 —— 看一眼它的结论对不对"

#: 一条 note 里混进了**线上写不出来**的字节时，接在**那条话自己尾巴上**的那句（`%d` = 几个）。
#:
#: 为什么接在自己身上、不另起一条：`notes` 是个**有顺序的**单子，`graph._journey_say`
#: 的尾巴取的是 `notes[-1]`（那句「有信息的是停因那句」的注释就在 `_walk_back` 上面）——
#: 另起一条报数会把那一句顶掉，于是「换了几个字节」这个**次要**消息压掉了**主要**消息。
#: 这与 `_utf8_safe` 把那句话写进**这一步自己的 `why`** 是同一条规矩：报数跟着**出事的那条
#: 记录**走（不另开一格，也不吞掉）。
#:
#: ⚠️ 措辞与 `service.UNWRITABLE_BYTES_SAY` 那个**同源**（同一件事在两个出口说，
#: 说的就该是同一种话）；只有「从哪来的」那半句不同 —— 那边是**人打的/粘的**，
#: 这边是**页面上的字 / 模型说的话**（`_utf8_safe` 那句里点的也是这两个来源）。
#:
#: ⚠️ 「线上写不出来」说的是**这些字节本身**写不出去，**不是**「请求会 500」
#: （补丁 B 复审打掉的那句，我复算过）：不换的话，`end_note` 进 state 时会被
#: `serde.dumps_typed` **悄悄换成 `?`** —— 端点是 200，坏的是**账**。
#: 所以这句话是**唯一**能让人知道「这里少了几个字节」的地方。
NOTE_UNWRITABLE_SAY = ("（这条里有 %d 个字节**线上写不出来**（孤立代理对，来自页面上的字 / "
                       "模型说的话）—— 已按 `�` 记，不是它本来长这样。）")

_SYSTEM = """你是 siteforge 的探路 agent：在一个**真的浏览器**里把目标站点走一遍，\
把「怎么走」探清楚，后面要照它生成一条能重放的 py 脚本。

规矩（每一条都有理由）：
1. **先看再动**。页面长什么样只能从 observe 拿 —— 不许凭常识猜「这个站大概有个提交按钮」。
2. **改页面一律走工具**：click / form / scroll / goto。工具层没有 eval，手拼 JS 这条路不存在。
3. 做完一个动作，**用 observe（或 diff）确认它到底有没有推进**。click 返回 ok 只代表命令
   下发了，不代表页面动了 —— 这两件事在这套系统里是分开的。
4. 元素在折线（above_fold=false）之下或被挡着（occluded_by 非空）时，先 scroll 再动它。
   对看不见的元素动手不是「没点到」，是**点到了别的东西**。
5. 页面里 honeypots 列出的元素是**陷阱**（人看不见、bot 填了会被标记），别碰。
6. 走通了（或确定走不通）就**别再调工具**，用一段话说明：这条路怎么走、每一步为什么这么做、
   以及**成功时页面上会出现什么文字**。注意：**没有 done 这个工具** —— 你不调工具就是结束。
7. **人给了步骤清单就照着走**（没给就按目标自由探）。清单是**一条走法的样子**，不是站点的
   结构：**这一趟少走几步、多走几步都是正常的**，别为了凑步数去点清单和原文里都没有的东西。
   **与页面矛盾时说出来再决定**，不要闷头按清单点下去。"""


@dataclass
class Budget:
    """跑飞的上限。到顶就停，并如实记下「为什么停」。

    ## 这两个默认值是按什么推出来的（2026-09-20 标定）

    原先 30 步 / 20 轮。**20 轮挡不住一条已知要走完的路**：真站实测 `job-62b192d2aca4`
    一路在正常推进（最后一句「Now clicking Continue to advance the quiz.」），
    停在 `budget_rounds`（20/20），而同一个站的确定性重放脚本要走 **25 步**
    （`forms/_remote/www.gowizard.com_auto-warranty_.py`：19 个 state 组、`steps` 相加 = 25）。

    推法（可复算）：4 趟被轮预算钉死的真站探路（`runtime/explore/job-*/`），实测每轮
    **成功推进**的动作数是 **0.450 / 0.450 / 0.450 / 0.400**（只数 `result.ok == true` 的
    —— 「点了没做成」不叫推进）；按最坏 0.400 折算，`25 / 0.400 = 62.5 → 63` 轮，
    再加收尾那一轮 ⇒ **64 轮**（⚠️ 是**估计**不是下界 —— 见紧接的那段保留）⇒
    取 **80**（**1.25 倍**）。步数取 100（> 80）：
    实测每轮正好一次工具调用，步数不许先于轮数卡死（卡死时模型连收尾那一轮都拿不到）。

    ⚠️ **那四个折算率来自一个偏的子集 ⇒ 上面那个 64 是「估计」，不是「下界」**
    （复审 2026-09-20 §5④ 补的那条）：4 条样本**全是同一个站**（gowizard），而且
    **全是被 20 轮钉死的那一档** —— 能走完的早就走了（走完那几趟只用 2–4 轮）
    ⇒ 这个子集天然是「动作密度最低」的那一类。**偏在哪一边我没量**（要别的站、别的档的
    样本才量得出）⇒ 它是「按手头这几条折出来的估计」，**不是**一条从无偏样本上量出来的下界。

    ⚠️ **80 松开的是【唯一】那根刹车**（下一个人再调它之前必须知道这一条）：
    `stall_limit` 在生产路径上**一次都不参与**（`explore` 只有拿到 `plan` 才建那个
    `watch`，而生产那条路交不出 `plan` —— 逐条见模块顶上那段）。所以这里其实是
    **两个上限、一个在起作用**：实测每轮正好一次工具调用 ⇒ `max_rounds` 永远先到。
    ⇒ 20 → 80 **不是**「两根里松开了一根」（那个口径会低估代价）：墙钟从 ≈50–85 秒
    涨到 ≈200–340 秒，对**真站**的足迹是 4 倍。这条由
    `tests/test_budget_calibration.py::test_the_production_path_never_hands_explore_a_plan_so_the_round_budget_is_the_only_brake`
    钉着。

    ⚠️ **「模型自然要用多少轮」仍然没量到**（量它要跑一趟真模型）—— 80 是**那个估计**
    之上的余量，不是那个自然数。**再校准的时机**：放开之后的第一趟真站探路，
    看 `attempts.jsonl` 的 `rounds` 是否落在新预算的 80% 以上（>64）。
    ⚠️ 那个门限**恰好压在 64 上**（不要再写「下界」：64 是**估计**，不是下界 —— 见上）
    ⇒ **第一批样本就可能响**：响了说明还是紧的；**不响也不能说明松**（见上：偏在哪一边
    没量）。两种都要记下来；但**别把「响」当成误报**。
    （2026-09-20 修复轮 4 改准：原来那句「就会响**是预期的**」的前提是「真·下界就是 64」，
    而那个前提被我删掉了 ⇒ 它不再被蕴含。）
    """

    max_steps: int = DEFAULT_MAX_STEPS
    max_rounds: int = DEFAULT_MAX_ROUNDS
    #: 计划模式下「连着几轮没有推进就停」（设计注 §2.5 的 `STALL_LIMIT`）。
    #: ⚠️ **6 这个数是设计注自己给的猜测，不是量出来的**：它是产物那边确定性重放的
    #: `STUCK_LIMIT = 3` 的两倍（探路有正常的「看几眼才动手」）。
    #: **Task 1 校准不了它** —— M3 实测是 20/20/20，而那个 20 被**预算钉死**
    #: （设计注原话：「这个数被预算钉死 —— 它是『预算允许多少』，不是『模型自然要用多少』」）。
    #:
    #: ⚠️ **到 2026-09-20 为止，这次校准仍然没做完**：那件「另一件事」的**前半截**做了
    #: （预算放开了，见类说明），但「模型自然要用多少轮」这一趟**量不到** ——
    #: 量它得跑一趟真模型，而那一轮不许。所以 6 仍然是**设计注的猜测**；
    #: 它变了的只是：不再被那个 20 钉死。**再校准的时机与上面那条同一个**
    #: （放开后的第一趟真站探路）。
    #: ⚠️ 只有计划模式读它；**`<= 0` 一律当 1**（见 `_PlanWatch` 的注释，复审 I-3）。
    #: ⚠️ **生产路径上一次都不读它**（`plan` 交不进来，见模块顶上那段与类说明）——
    #: 所以调 `max_rounds` 的时候别拿「还有停滞判据兜着」安慰自己：那条线是断的。
    stall_limit: int = 6


@dataclass
class Journey:
    """一次探索的账本。

    - `steps`  每步 `{state, action, target, result, note}`（**每个工具调用一步**）
    - `notes`  给人看的一句话串（D16）—— 含模型自己的话、报错、以及**为什么停下**

    计划里只定了这两个字段；下面几个是**加出来的**，都摆在明面上：
    `stop_reason` / `final_answer` / `pages`（状态清单，`states()` 的 `when` 就在里面）。
    """

    steps: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    stop_reason: str = "running"
    final_answer: str = ""
    #: **现在**坏着吗 —— 最近一次没拍成的原因（人话）。空串 = 这一刻没事。
    #: ⚠️ 与 `notes` **分开**：`notes` 是「这一趟发生了什么」，这个是「拍照那条旁路现在的状态」——
    #: 混进去会让「它为什么没有图」被别的 note 淹掉（旁路坏掉要能单独被看见）。
    #: ⚠️ **它是一格状态，不是一本账**（Task 5 修复轮 2）：拍成了就清空 ——
    #: 所以**「发生又消失」的事在它上面留不下痕迹**，要那种痕迹请看 `shot_failures`。
    shots_why: str = ""
    #: 步拍这条路上**没留下图**的每一次，**当时**记一条（**只增不减**，Task 5 修复轮 2/3）：
    #: `[{"why": 人话原因, "when": "before" | "after"}]`；`when` 缺席 = 这条不是某一步的
    #: （步拍自己的代码抛了那种，`_safe` 记的，它够不着「哪一步」）；
    #: 撞上 `MAX_KEPT_SHOTS` 那一条是 `{"why": …, "capped": True}`（**知道的、不再拍**）。
    #: ⚠️ **上限一生效，这本账（`_take` 那条路）就停涨**：那之后 `_take` 直接返回、
    #: 连 `_fail` 都走不到 ——「从这一步起不再拍」由**那一条 `capped`** 一次说清
    #: （后面每步再记一遍不是信息）。
    #: ⚠️ 但**别的路照样能往账里加行**：`_safe`（步拍自己的代码抛了）到顶之后一样走 `_fail` ——
    #: 那不是漏，是另一件事（这一句不说清就会被读成「到顶之后账不再动」）。
    #:
    #: **为什么非要一本只增的账**（而不是只看 `shots_why`）：`shots_why` 说的是「这条**路
    #: 现在**坏着吗」（拍成了就清，那是对的），而**一张没成、下一张成了**这种事在它上面
    #: 什么痕迹都不留 —— 时间线于是会说「一切顺利」，而那一刻确实出过事。
    #: **残留状态天生会漏掉「发生又消失」的事实**；那一刻的事只有当场记下来才留得住。
    shot_failures: list = field(default_factory=list)
    pages: list = field(default_factory=list)

    # ── 计划模式的账（Task 3 起；没计划时全空 —— **不加计划就不假装有计划**）──

    #: 清单上每一步一个终态，**一步一条**、顺序就是清单顺序（`plan.ledger` 的产物）：
    #: `[{n, text, state, why}]`，`state` 是 `done / jumped_over / contradicted / not_reached`。
    #: ⚠️ 它是**给人看的账**，不是产物的原料 —— 「第 k 步」这个东西**一个字节都不许**
    #: 进到生成出来的 py 里（那等于把分支站钉死在一条路上，§2.3.2）。
    plan_ledger: list = field(default_factory=list)
    #: **模型声明**的「描述说…页面上是…」（**原话**，不改写）。⚠️ 只有**矛盾**进这里：
    #: 绕开 / 分支（这一趟没走到某一步）**不进** —— 那是站点本来的形状，混进来这条账
    #: 会被日常噪音灌满，「同一处反复偏离」那条信号就废了（§2.3.1）。
    deviations: list = field(default_factory=list)
    #: 计划模式里**已经连着几轮没有推进**（§2.5 的停滞判据；推进一步就清零）。
    #: 没计划时恒为 0（那套判据不参与）。它同时也是「位置到底有没有动」的直接体现。
    #: 每一轮都算数：**含收尾那一轮**（最后一轮由 `_PlanWatch.finish()` 补结算，
    #: 它没有下一次边界）—— 所以**一次干净走完的探路也常是 1，它不表示停滞**。
    #: ⚠️ 判「停没停滞」只看 `stop_reason == "plan_stalled"`（那个值只在实际抛停时才写上）；
    #: 这个数是**计数**，不是布尔量 —— 不许当布尔量用。
    stall_rounds: int = 0
    #: ★ 收尾那一眼**照着图**看的结果（2026-09-22，用户要求：「到最后一步执行完之后等待页面
    #: 完毕了就截图分析下」）。
    #: `vision_say` = 模型照着截图的原话（⚠️ **base64 不许进账本** —— 只留它说的话）；
    #: `vision_hit` = 那句话里**照抄到了**成功文案。判据还是**子串**那一把尺子（没放宽），
    #: 只是这一次读的是**图** ⇒ 谁引用它都必须写明**是在图上见到的**（`_journey_say` /
    #: `_attempts_note` 的任务）。
    vision_say: str = ""
    vision_hit: bool = False

    #: 这一趟开头**照账本重放**了的那一段（`explore(resume_from=…)`）——
    #: 记的是**当时真发生的那件事**：`{done, landed, why}`（`replay` 的原样产物），
    #: 加上切前缀那侧给的 `boundary_reason`。没重放 = 空 dict（**不假装重放过**）。
    #: 为什么要有它：重放不经过 `dispatch`，它的结果（走成几步、落在哪、为什么停）
    #: 除了这里没有第二个落脚点。
    #: 读者**四处**（Task 6 修复轮 3 就地改正：原先这里写「就两处」，与报告 §七.8 的
    #: 撤回框**互相矛盾** —— 那里把四处列全了，代码这边漏了两处）：
    #:   1. `explore` → `_with_resume(opening, resume_from, journey.replay)`（说给模型听）；
    #:   2. `graph._resume_say`（人读的那句话）；
    #:   3. `replay_cut_short`（读 `done`：「重放走完没有」—— 决定「就地停」还是接着问模型）；
    #:   4. `replay_went_clean`（读 `attempts`：「一次过吗」—— 决定这一趟**值不值得重探**）。
    #: ⚠️ 别再写成「就两处」：复审 Q2 撤回过一次（原先这句写成「闸口那份摘要也要读它」，
    #: 那是错的 —— 那份摘要在**节点开工之前**就算，那时这个 journey 还不存在，
    #: 它读的是状态里的 `resume_note`），修复轮 2 的 ② 又**新加**了第 4 个读者。
    replay: dict = field(default_factory=dict)

    #: 那一趟的轮数**量到了没有**（P5：`0` 有两种意思）。**只有 `_wrap_up` 会把它置 True**
    #: —— 就是它写下 `rounds` 的那一句旁边；`_Stop` 那一支走不到那儿（`rounds` 随异常丢掉），
    #: 于是天然是 False。⚠️ 别按停因去猜（那要维护一张会漏的名单，Task 4 的 `GROUPS` 栽过）。
    rounds_measured: bool = False
    #: 这一趟**问了几轮模型**（G2：`_wrap_up` 原先用完 `rounds` 就丢，于是基线 M3 量不到）。
    #: ⚠️ `0` 有两种来源（**别按它自己判**，一律看 `rounds_measured`）：
    #:   - **真的 0 轮**（模型一次都没回话）；
    #:   - **没量到**：`_Stop` 一穿出 `run_tool_loop`，`rounds` 那个局部变量就没了
    #:     （与是哪一条停因无关）。
    #: 复审 I-5 点的那处**已经收掉**（Task 6 修复轮 1）：读账的人一律走
    #: `rounds_measured(journey)` → 标记落在 `_wrap_up` 写下 `rounds` 的同一句旁边，
    #: `_Stop` 那一支天然走不到 ⇒ 不再有一张按停因手写的名单要维护，也不会把 0 当真数读。
    rounds: int = 0
    #: `llm.summarize(rounds)` 的产物（几轮 / 几次工具调用 / token / 耗时）。
    #: 被人打断那条路是空的 `{}` —— 与 `rounds == 0` 同一个道理。
    usage: dict = field(default_factory=dict)

    # ── 账本那一句一句的话（**唯一的写入口**，见下）──

    def note(self, text: str) -> None:
        """往 `notes` 记一句人话 —— **`journey.notes` 唯一的写入口**。

        ⚠️ **为什么要收成一个口子**（Task 8 补丁 B，2026-09-19）：`notes` 是**外面来的字**
        进系统的第二个口子（第一个是载荷，补丁 A 治的）—— 页面自报的标题与地址、页面上的
        元素文字、模型每一轮的推理，全都从这儿进来。它们随后被 `graph._unfinished_note` /
        `graph._journey_say` 拼成 `end_note` 进 state。

        ⚠️ **真症状是【静默失真】，不是 500**（补丁 B 复审 2026-09-19 打掉了我原来那句，
        我照它的量法自己复算过 —— 见 `tests/test_notes_intake.py` 的
        `test_the_operator_sees_the_replacement_mark_not_a_silent_question_mark`）：
        走**真栈**（真 `explore` + 真 `graph.build` + 真 allowlisted `InMemorySaver` + 走服务）时
        **两个端点都是 200**，坏码位在 `serde.dumps_typed` 那一步被**悄悄换成 `?`**
        （`b'\xe5\x88\xb0?'`），**没有任何人被告知**——运营那一屏上是「第 2 步?」，
        一个**没人报告过的问号**。500 只出现在「有人拿手工快照绕过 saver」的量具里
        （`FakeGraph.get_state`），**不是线上的形状**。
        ⇒ 这一层的病与补丁 A 那个口子是**同一个主题（外面来的字）**，但**不是同一个症状**：
        那边是响的 500，这边是哑的问号 —— 而「**没有静默的路径**」正是这一片要治的东西。

        ⚠️ **为什么不是「在每个写点记得接一下」**：补丁之前写点有 **22 处**
        （`grep -n "journey.notes.append(" agent/*.py` —— 那串字符现在只剩这一行文档还写着它，
        真调用一处都没有），靠人记得 = 早晚漏一个 —— 这正是 `_utf8_safe` 的注释里那句
        「不能靠写的人小心」。收成一个口子之后，新加的写点**自动**走消毒；
        而「绕开这个口子直接往 list 里塞」由 `tests/test_notes_intake.py` 的机器守当场拦下
        （它走 AST，认的是**真调用**，不会被这行文档骗到；**它挡得住的与挡不住的都在那条用例的
        docstring 里列着**，别把它的射程想大）。

        ⚠️ **换掉是有损的 ⇒ 有损必须说**（Global Constraints：没有静默的路径）：换掉的个数
        接在**这条话自己的尾巴**上（与 `_utf8_safe` 把那句话写进**这一步自己的 `why`** 同一条
        规矩）。**不另起一条 note** —— 另起一条会顶掉 `notes[-1]`，而 `graph._journey_say`
        的尾巴取的正是那一句（见 `_walk_back` 上面那段「顺序有讲究」）。

        ⚠️ 非 `str` 的值照收（`notes` 的契约是 `[str]`，但这一层不替调用方改类型）：
        `events.safe_value` 对它原样返回，于是行为与以前**一个字节都不差**。
        """
        safe, replaced = events.safe_value(text)
        if replaced:
            safe = "%s%s" % (safe, NOTE_UNWRITABLE_SAY % replaced)
        self.notes.append(safe)

    # ── 给 Task 7 的 draft 节点用：直接喂 template.render() ──

    def states(self) -> list:
        """`[{name, when, steps}]` —— 与 `template.render(states=...)` 同形。

        步骤按**状态**分组（同一个状态里的是连续发生的），没有可重放步骤的状态整组丢掉
        （骨架里空 steps 的状态等于没写，留着只会让人以为那儿本来有东西）。

        ★ 2026-09-22（真事）**同一个字段被反复填 ⇒ 只留最后一次**（见 `_last_fill_wins`）：
        用户看到的现象是「第一次生日填过了，为啥还会消掉填第二次」。
        """
        order: list[str] = []
        groups: dict[str, list] = {}
        for step in self.steps:
            name = step.get("state") or START_STATE
            if name not in groups:
                groups[name] = []
                order.append(name)
            replay = _replay_step(step)
            if replay is not None:
                groups[name].append(replay)
        groups = {name: _last_fill_wins(steps) for name, steps in groups.items()}
        whens = {page.get("name"): page.get("when") for page in self.pages}
        return [
            {"name": name, "when": whens.get(name), "steps": groups[name]}
            for name in order if groups[name]
        ]

    def fills(self) -> dict:
        """`{name: {source, kind, label, target, fallback}}` —— 与 `render(fills=...)` 同形。

        只收**真填成功过**的字段：没填成的字段进了 FILLS，重放时就会去填一个
        agent 当时都没找着的东西。
        """
        out: dict = {}
        for step in self.steps:
            info = (step.get("result") or {}).get("fill")
            if not info:
                continue
            out[info["name"]] = {
                "name": info["name"],
                "source": info["source"],
                "kind": info["kind"],
                "label": info["label"],
                "target": step.get("target") or {},
                "fallback": list(info.get("fallback") or []),
            }
        return out


def when_holds(when, model: dict) -> bool:
    """一条 `when` 在**这一份页面模型**上成不成立。

    判据与产物里的 `_applies` **逐条对齐**（url 子串 + 正文里含任一段文字），
    因为「生成时的 `when`」与「重放时的 `when`」不是两件事：一条当时就不成立的 when，
    在重放时会让整组步骤**静默跳过**。
    """
    if not when:
        return True
    if when.get("url_contains") and when["url_contains"] not in (model.get("url") or ""):
        return False
    wants = when.get("text_contains") or []
    if wants:
        text = _norm(model.get("page_text") or "")
        return any(_norm(str(w)) in text for w in wants)
    return True


class _Stop(BaseException):
    """「停下」这个信号（人喊停 / 预算到顶）。

    ⚠️ **故意继承 `BaseException`**：`llm.run_tool_loop` 对 dispatch 抛出的 `Exception`
    是「记成一次工具失败，接着跑」—— 那对工具自己的错是对的（模型该看见它），
    对「人喊停」是错的（那会变成「喊了停还在跑」）。与 KeyboardInterrupt 同一个道理。
    """

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(reason)
        self.reason = reason
        #: 停下来的**细节**（眼下只有一种：人那道闸自己坏了）。空 = 没什么好补充的。
        self.detail = detail


def _enter_target(session, url: str, journey) -> None:
    """开跑之前把这一页导航到目标 url —— **失败不抛**，但要**说出来**。

    为什么失败不抛：导航没成不等于这一趟没救 —— 模型看见「起点不对」之后**可以自己再导航一次**。
    把它抛出去，就把一条「模型自己能救」的路变成了一趟白跑。
    ⚠️ 但**不许静默**：没成就要在账本里留下一句人话（否则读账的人以为起点本来就是目标页）。

    ⚠️ **调用方必须先过暂停闸**（`_stop_or_raise`）—— 它自己不查：
    「停在下一步之前」那条不变量要求这一下也归暂停管。
    """
    try:
        session.call_tool("goto", {"url": url})
    except Exception as exc:                       # noqa: BLE001 —— 见上面「失败不抛」
        journey.note("开跑之前先导航到「%s」**没成**：%s: %s —— 探路照常开始，"
                     "但**起点可能不是目标页**（谁读这份账都要知道这一条）。"
                     % (url, type(exc).__name__, exc))
        return
    journey.note("探路的**起点是目标页**：开跑之前先导航到「%s」。" % url)


def explore(url: str, goal: str, budget: Budget | int | dict | None = None, *,
            plan: "plan_module.Plan | None" = None,
            should_pause: Callable | None = None,
            client=None, session: "tools.McpSession | None" = None,
            ws_url: str | None = None, host: str | None = None, port: int | None = None,
            binary: str | None = None,
            on_step: Callable[[dict], None] | None = None,
            on_note: Callable[[str], None] | None = None,
            resume_from: list | None = None,
            resume_note: str = "",
            window_alive: Callable | None = None,
            shots_dir=None, shooter: Callable | None = None,
            steer: Callable[[], str | None] | None = None,
            success_text: str = "",
            hints=None) -> Journey:
    """在真浏览器里为 `goal` 探 `url` 这条路，返回 `Journey`。

    参数：
      - `url` / `goal`：探哪一页、要摸清什么
      - `hints`：**人另外交代的话**（面板上「开工前先说一句」/「步骤表」那一格，逐字）。
        ★ 2026-09-22 加的（真事）：新站那条路的稿是「账本 → 模板」算出来的，模型在这条路上
        **只有探路这一处有判断力**；人的话原来只进「修站出补丁」那条路 ⇒ 运营把步骤写得再细
        也**一个字都到不了这儿**，屏幕上就成了「它完全没按我的来」。现在进 `_brief` ✓。
      - `success_text`：**人给的成功判据**（「走通之后页面上会出现哪段文字」）。
        给了它，这一趟就多一条停因（`STOP_REACHED_SUCCESS`）：**页面上出现了这句话就
        当场收摊**，不再往下点。为什么非加不可（2026-09-20 真站 `job-a4d100addd25`）：
        第 66 步的正文里**就是**运营给的那句成功文案，而那一趟没有停 —— 一路走到第 71 步
        （中间还 `goto` 去别的页、又点了一下），最后是**人按了停**才收的。
        过线之后的每个动作都可能是**重复的真实请求**（运营的原话：「能成功为啥还要继续」
        「我不希望她再刷」）。这句话本来就是 R3 的判据，Task 15 把它从**重放**接到了
        **活着的那一趟**上。
        ⚠️ **不给 / 给空串 = 判不了**（不据此停，照常跑到别的停因）—— 空不是「没有约束」，
        更不是「什么都算成功」。口径见 `_success_hit`
      - `budget`：`Budget` / 步数（int）/ dict；不给就用默认上限（防跑飞）
      - `plan`：人给的**检查点清单**（`agent/plan.py` 的 `Plan`）。给了、且里面够
        `MIN_STEPS` 步 → 走**计划模式**（简报换成清单那一版，位置 / 偏离 / 停滞开始记账）；
        不给、或一段话里解析不出步骤（`not plan.actionable()`）→ **走今天那条路**
        （自由模式，简报逐字节不变 —— B4）
      - `should_pause`：**人的注入点**。`should_pause(journey)` 或 `should_pause()`，
        真 → 在**下一步之前**退出。每一步之前都会被问一次（见模块 docstring 的两道闸）
      - `session`：MCP 会话（测试用桩；不给就按 `ws_url`/`host`/`port` 起一个真的）
      - `client`：LLM 客户端（测试注入；不给就用 `llm.client()`）
      - `on_step`：每步**发生的当下**回调一次（Console 的实时视图靠它）
      - `on_note`：模型**每一轮**说的话（`AI 说：…`）在记进 `journey.notes` 的**同一处**
        回调一次，拿到的是**逐字那一句**（Console 的实时视图靠它 —— 见 `_Gate._note`）。
        不给 = 今天那条路，一个字节不变
      - `resume_from`：**续跑的开头**（§1.7）。账本里那一段（`replayable_prefix` 切出来的
        那几行）——先照它走回去（**0 模型调用**），再把「重放了什么、边界在哪」告诉模型。
        不给就是从头探（**今天那条路**，一个字节都不差）
      - `resume_note`：那段前缀**为什么停在那儿**（`replayable_prefix` 的第二返回值，人话）。
        只管**说给人听**（账上那句 + 给模型的边界信息），**不参与判断** ——
        它是切前缀的那个人（服务侧）现成就算出来的，不是这里再猜一遍
      - `window_alive`：问一句「窗口还活着吗」（零参数，回三态 True / False / **None＝不知道**）。
        问两处：① 工具**连着失败 `FAILURES_BEFORE_DEAD` 次**时；② 续跑把账本重放完之后
        （重放**没走完**、而它说死了 → 就地停，不去白问模型一轮）。
        **只有「明确说死了」才停** —— 没接这根线（`None`）、探针自己抛异常、
        或探针答「不知道」（`None`）一律**不编**一个停因出来（`_window_is_dead` 的 docstring）。
        ⚠️ **不许**拿工具那句错误文字去猜（§1.8）
      - `steer`（Task 9）：**人的话**的注入点。循环**每一次模型调用之前**问它一次
        （`llm.run_tool_loop(steer=…)` 透传下去），回了非空的一句就插进这一轮的对话里。
        不给（`None`）= 今天那条路，**一个字节都不插**。
        ⚠️ 它**只在那一次调用之前**被问，所以**插进去那一刻起这一轮就带着它**；
        而「插话之后那一轮它就收尾了」这件事**必须写进账**（`STEER_WRAPPED_UP_NOTE`，
        见 `_wrap_up`）—— 那道门上没有 `done()`，收尾与「讲完了」在这里是同一件事（R11）

    出错怎么办：
      - **工具自己报的错**（找不到元素 / 连不上窗口）→ 记进**那一步**，也回给模型，
        循环接着走（模型可能自己换一条路）
      - **门起不来 / 传输断了**（`McpError`）→ **抛**。不吞成一份空 Journey ——
        空的 Journey 与「探完了，什么都没发现」长得一模一样，而这两件事的下一步
        完全相反（一个要去重开窗口，一个要继续往下写 py）
    """
    limits = _as_budget(budget)
    paused = _as_predicate(should_pause)
    journey = Journey()
    pages = _Pages(journey=journey)
    #: 步拍（§5.4）。**只有调用方给了目录才建** —— 不给 = 今天的行为，一个字节不变
    #: （连 `shooter` 都不看一眼）。`shooter` 不给就用 `shots.capture_via_session`。
    #:
    #: **降级 B 的开关**（设计注 §5.5）：`SITEFORGE_STEP_SHOTS=0` ⇒ 关掉每步抓拍，只留闸拍。
    #: ⚠️ 读法**只有一处**（`shots.step_shots_on`）：这条开关还有**第二个读者**
    #: （`Service.shots_note()` —— 页面上那句「为什么这次没有逐步的图」）。
    #: 两处各自解析就是两份口径，后果是设计注禁止的**静默降级**：图没了、一个字没解释。
    if not shots.step_shots_on():
        shots_dir = None
    step_shots = (_StepShots(journey, shots_dir, shooter or shots.capture_via_session)
                  if shots_dir else None)
    own_session = session is None
    if session is None:
        session = tools.McpSession.open(ws_url=ws_url, host=host, port=port, binary=binary)
    taken = 0
    #: 连着几次工具调用没成（成功的调用把它清零）—— 见 `FAILURES_BEFORE_DEAD`。
    fails = 0
    #: 只有**真的有计划**才建这一份：`plan=None` / 解析不出步骤 → `None`，
    #: 位置 / 停滞那套判据一次都不参与（自由模式与今天逐字节相同）。
    watch = None
    if plan is not None and plan.actionable():
        watch = _PlanWatch(plan, journey, limits.stall_limit,
                           page_state=lambda: pages.current_name,
                           steps_taken=lambda: len(journey.steps))
    try:
        specs = tools.tool_specs(session)
        if not specs:
            raise RuntimeError("MCP 门上一个工具都没有 —— 工具循环没法开始")
        inner = client if client is not None else llm.client()
        gate = _Gate(inner, lambda: _stop_or_raise(paused, journey, taken, limits,
                                                   success_text),
                     journey, watch, on_note=on_note)

        #: 旁路的故障**只记一次**（别每步刷一条）—— 见 `emit` 的 docstring。
        side_broken: list = []

        def emit(step: dict) -> None:
            """把这一步交给**旁路**（Console 的实时视图 / journal 的账本）。

            ⚠️ **旁路坏掉不许带塌主路**（与 Console 那片对 `shooter` 的规矩同一条）：
            回调抛异常时**吞掉**，但**不是静默**——往 `journey.notes` 记一句，
            人看得出来「这一趟的实时视图 / 账本没记上」。

            不吞会怎样：它会以**一次工具失败**的身份回到模型面前
            （`llm.run_tool_loop` 的 `except Exception` 把 dispatch 抛的都记成工具错）——
            那等于把旁路的故障记到产物头上，而模型还会照着这条假错换路走。
            **只记一次**：账本坏了通常每一步都坏，30 步刷 30 条会把别的 note 淹掉。

            ⚠️ **交出去之前先过一次 `_utf8_safe`**（2026-09-18）：这一步里混着**外面来的
            字节**（CDP 回执的原文、页面上的 url 与元素文字），而两个读者都是 UTF-8 的
            文本产物（JSONL 账本 + `/live` 的 JSON）—— 一个孤立代理对过得了 `json.dumps`、
            过不了最后那次 `.encode("utf-8")`，后果是**整条时间线一条都读不出来**
            （不是这一步坏掉）。所以**转抄的那一刻**就换掉，并把「换了几个」写进 `why`。
            """
            step = _utf8_safe(step)
            try:
                _emit(on_step, step)
            except Exception as exc:                       # noqa: BLE001
                if side_broken:
                    return
                side_broken.append(True)
                journey.note(
                    "⚠️ 旁路（实时视图 / 账本）在这一步上没记成：%s: %s —— "
                    "探路照常往下走（旁路坏掉不许带塌主路），但这一趟的账本可能是残的。"
                    % (type(exc).__name__, exc))

        def _shots(fn, *a) -> None:
            """步拍是**旁路** —— 一律走总闸 `_StepShots._safe`，坏了只记账、不带塌主路。

            为什么要有这一层而不是直接调：`dispatch` 里抛出去的异常会被 `run_tool_loop`
            记成**一次工具失败**，模型会照着那条假错换路走（实测栽过一次）。
            """
            if step_shots is not None:
                step_shots._safe(fn.__name__, fn, *a)

        def dispatch(name: str, args: dict) -> Any:
            nonlocal taken, fails
            # ← 每一步之前（§6.2）。⚠️ 三条闸（人 / 成功文案 / 预算）都在这一个出口上：
            # 模型一轮里丢了五个动作时，**过线之后的那些一个都不许发**。
            _stop_or_raise(paused, journey, taken, limits, success_text)
            taken += 1
            step, fill = _describe(name, args, pages, journey)
            # ── 契约七格里的**前五格**：脚本只填这五格（契约 §二①）──────────────
            #
            # 第 1 格 `step_no`：第几步（模型这一步是它自己走的，编号按**发出去的顺序**）。
            step["step_no"] = taken
            # 第 4 格 `sig_before`：**动手之前**那一页的原始签 —— 最近一眼算出来的那个。
            # 一眼都还没看过（模型第一手就是 click）时是 `None` + 一句 why：
            # 「看不见」是一等值，不许折算成「没变化」（契约 §二②）。
            # ⚠️ 它取的是 `pages.sig`（**原始观测**算的），不是 `pages.current_key` /
            # `step["state"]` —— 后两个是我们自己写的摘要，不是签（见 `_raw_sig`）。
            step["sig_before"] = pages.sig
            if pages.sig is None:
                step["why"]["sig_before"] = (
                    "动手之前还没看过一眼页面（`observe` 一步都还没跑过）"
                    "—— 这一页长什么样**量不到**。")
            # ── 步拍：**动手之前**那一张（§5.4）────────────────────────────
            # 它是**下限、省不掉** —— 动手之前不可能知道这一步会不会出问题。
            # 能省的只有「点后」那张：只在**已经知道不对劲**时才补拍。
            if step_shots is not None and name in MUTATING:
                _shots(step_shots.before_mutation, step, session, pages.current_key)
            t0 = time.time()
            try:
                raw = session.call_tool(name, args)
            except Exception as exc:                       # noqa: BLE001
                # 第 3 格 `receipt`：**CDP 回执的原文** —— 这一条是工具**没成**那一侧。
                # 为什么带一个 `isError`：那是 **MCP 协议自己**的字段（`tools.call_tool`
                # 就是拿它分支的），不是我们下的判 —— 转抄它 = 把协议的原话留给裁判，
                # 而不是替他先说一句「这一步失败了」。
                # ⚠️ `text` 里那个类名（`McpToolError` / `McpError`）也是**转抄的一部分**，
                # 不是我们加的话：它分得开「工具说它没做成」与「传输断了」—— 而这两件事
                # 的下一步完全相反（换选择器 / 重开窗口）。本文件里工具出错一律这么记。
                step["receipt"] = {"isError": True, "text": "%s: %s" % (type(exc).__name__, exc)}
                step["result"] = {"ok": False, "elapsed_ms": _ms(t0),
                                  "error": f"{type(exc).__name__}: {exc}"}
                step["note"] = _say(name, step["target"], False)
                # 第 5 格 `sig_after`：动作**没发出去**，页面当然也没再看过 ——
                # 这一格是 `None` + 一句 why，**不是**「和 before 一样」（那会是
                # 「页面没变」这个假话：页面没变是因为**这一步没发生**）。
                step["sig_after"] = None
                step["why"]["sig_after"] = ("这一步的工具调用没成，之后也没有再看一眼页面 —— "
                                            "页面变没变**量不到**。")
                if step_shots is not None:
                    # 失败那条路：动页面动作 ⇒ 当场补拍点后那张（两张都留，§5.4 的 a 支）；
                    # **报错的 `observe`**（唯一「本该结算而没结算成」的那个）⇒ 那链作废。
                    # 放在 `emit` **之前**：账本是 emit 那一刻落的，晚一步这一步就没有图了。
                    _shots(step_shots.after_mutation, step, session, name, False)
                journey.steps.append(step)
                emit(step)
                # ── 窗口死掉是一等停因（§1.8）──────────────────────────────
                # 连着失败几次之后**问一次**窗口服务：它说死了就停在这里。
                # ⚠️ 问它、不看 `exc` 里那句话 —— 「连不上 host:port」是**猜**出来的形状，
                # `alive()` 是**接口**给的答案，两件事的可靠性差一个量级。
                # 停之前那一步**照常入账**（它就是「死在哪一下」的证据）。
                fails += 1
                if fails >= FAILURES_BEFORE_DEAD and _window_is_dead(window_alive):
                    raise _Stop("window_gone",
                                detail="连着 %d 次工具调用都没成（最后一次：%s）"
                                       % (fails, step["note"]))
                raise        # 还给 run_tool_loop：模型也必须看见这条错（不吞）
            fails = 0
            # 第 3 格 `receipt`：工具**回来了** —— 转抄它的原文（超长只标注、不过滤）。
            step["receipt"] = _receipt_of(name, raw)
            # 第 5 格 `sig_after`：只有 `observe` 这一步**当场**量得出来 —— 那一眼就是
            # 量它的动作（`_raw_sig(raw)`）。别的动作（click / form / scroll / goto）
            # 动手那一下**手上没有新的一页**：页面变没变要等下一次观测，
            # 所以是 `None` + 一句 why。**不许**拿 `sig_before` 顶上（那是「没变化」的假话）。
            step["sig_after"] = None
            if name != "observe":
                step["why"]["sig_after"] = (
                    "这一步之后还没有再看过一眼页面 —— 页面变没变要等下一次 `observe`"
                    "才知道，**量不到**（不拿动手前那一份顶上）。")
            step["result"] = _summarize(name, args, raw, _ms(t0), fill)
            if step_shots is not None:
                # 做成了 ⇒ 这一步**先挂着**，留不留由**紧接着那次观测**说了算（§5.4 b 支）。
                _shots(step_shots.after_mutation, step, session, name, True)
            # ⚠️ 这里原先有一行 `step["target"] = {"url": raw["url"]}`（把 target 盖成**落地地址**）。
            # **它被删掉了**（R-E7 ①）—— 那两行把「要打开哪」就地销毁，而账上**再无别处**存它
            # （`_summarize` 那次赋值写到的是 `result.url`，两份名字不同、用途也不同）。
            # 后果：R4 拿 `target.url` 比「见过的地址」，而它手上那个是**落地地址** ——
            # 而那条落地 URL 的 observe 正是 R2 已经要求必须存在的那一条 ⇒ **R4 恒真**
            # （设计注承诺的「一次性深链不会重放」实际没人执行，拦住它的是「账本把请求地址丢了」
            # 这个副作用 —— 运气对，不是判据对）。
            # 落地地址没有丢：它一直在 `result.url` 上（`_summarize`），这里只是**不再复制一份**。
            step["note"] = _say(name, step["target"], True)
            journey.steps.append(step)
            if name == "observe":
                moved = pages.note_page(raw)
                # 第 5 格 `sig_after`：**这一眼看到的**那一页（`note_page` 刚把它算进
                # `pages.sig`）。读不出正文时它是 `None` —— 那就补一句 why，
                # 仍然是「看不见」，不是「没变化」。
                step["sig_after"] = pages.sig
                if pages.sig is None:
                    step["why"]["sig_after"] = (
                        "这一眼没读到正文（回执里没有 `page_text`）"
                        "—— 这一页的签**量不到**。")
                if step_shots is not None:
                    # 结算手上那一步 —— **只有「紧接着」的这次观测才算**（§5.4 判据 2）。
                    # 放在 `note_page` **之后**：要比的是「这一眼看过之后」的签。
                    _shots(step_shots.on_observation, session, pages.current_key)
                if moved:
                    journey.note(
                        f"页面变了：现在是「{_title_of(raw)}」（{raw.get('url') or '?'}）")
            if name == "scroll" and not any("滚进视口" in n for n in journey.notes):
                journey.note(
                    f"第 {len(journey.steps)} 步是把「{_label_of(step['target'])}」滚进视口；"
                    "重放时会照做同一件事（把那个元素滚进视口），元素在子帧里时连帧一起带")
            emit(step)
            return raw

        #: 「两次重放尝试之间**换一个会话**」那根线（见 `replay` 的 docstring）。
        #: ⚠️ **只有会话是这一步自己起的**才给：别人给的会话（测试的桩、上层复用）
        #: 我们不知道该怎么再造一个，而**编一个**（比如退回默认的 9222）会连到**别的**
        #: 窗口上 —— 那比不换更坏（`_explore_for` 那条注释早就立过这条规矩）。
        fresh = None
        if own_session and (ws_url or host or port):
            def respawn(old):
                """换一个新会话（同一串 ws_url）—— **换完这一步手里那个也跟着换**。

                ⚠️ 三条一起才成立（少一条就是**半截事**，复审 Q1）：

                ① **所有权只有一个地方说了算**：`session` 是 `explore` 的局部变量，这里用
                   `nonlocal` 把它换掉。只换 `replay` 内部那个名字的话，**工具循环会继续
                   拿着已经关掉的会话跑完整趟** —— 每一步都是 `McpError: cdp-mcp 已经不在了`，
                   一趟真探路当场变成废账（白烧一个真窗口 + 一整趟模型钱，账上留下满篇
                   假的「工具失败」）。
                ② **换来的那个必须有人关**：关它的就是下面 `finally` 里那句
                   `session.close()`（它关的正是换过之后的这个）—— 不换的话漏一个
                   `cdp-mcp` 子进程。
                ③ **先起新的、成了再关旧的**：起不来时旧的那个还能用（换不到就照旧重来）。
                """
                nonlocal session
                new = tools.McpSession.open(ws_url=ws_url, host=host, port=port,
                                            binary=binary)
                try:
                    old.close()
                except Exception:                  # noqa: BLE001 —— 关不掉不是错
                    pass
                session = new
                return new
            fresh = respawn

        #: 预算**一开始就花光了**（job 级累计已经超：图那边把 `max_*=0` 交了下来）——
        #: 一步都不走，连模型都不问（问一轮也是白花：这一趟没有任何步数可走）。
        #: 放在重放之前：重放是为「接着往下探」准备的，而这一趟探不了。
        if limits.max_steps <= 0 or limits.max_rounds <= 0:
            journey.note(
                "这一趟的预算**一开始就是 0**（这个 job 前面几趟已经花掉：给了 %d 步 / %d 轮）"
                "—— 一步都不走，如实停下（`reopen` 也救不了它：那只是再烧一次，该人看一眼）。"
                % (limits.max_steps, limits.max_rounds))
            raise _Stop("budget_steps")

        #: 续跑的开头（§1.7）：先把账本里那一段走回去。**必须在工具循环之前** ——
        #: 模型看到的第一眼就该是「上一趟走到哪儿了」，而不是一个停在入口的空窗口。
        if resume_from:
            _walk_back(journey, resume_from, session, emit, window_alive, resume_note,
                       fresh=fresh)
            # ⚠️ **重放没走完、而窗口服务说它已经死了** → 就地停下，**不去问模型那一轮**：
            # 它看到的是一个死窗口，工具连着失败两次之后我们还是会停在同一处（白问一轮）。
            # 判据两条都要：**没走完**（数出来的，不是读 `why` 那句话）+ **问接口**说死了。
            if replay_cut_short(journey, resume_from) and _window_is_dead(window_alive):
                raise _Stop("window_gone", detail="账本还没重放完，窗口就没了")

        # ⚠️ **开跑之前先站到目标页上**（2026-09-20 真站实测的根因，`job-36ab36b56754`）：
        # 窗口是 `fresh_open` 新开的 ⇒ 它落在 **Bit 的工作台页**
        # （`console.bitbrowser.net/…?id=…&port=…`）；而模型的第一个动作是 `observe` ——
        # 它看到的就是那个「**我们碰巧从那儿开始**」的旁枝。那一趟 20 步预算全烧在旁枝上，
        # 目标站一步没探，最后只能交白卷（它交得对，但**本来不该走到那一步**）。
        #
        # 原先靠 `_branch_start_states`「起点那页与后面每页都不同源 ⇒ 撤掉它的 `when`」**绕** ——
        # 那是绕不是修：判据撤了，起点那组步骤在**重放**时就认不出来了。
        # 生产脚本的第一步也是 `goto`，同一个道理：**先站到起点上，再开始看**。
        #
        # ⚠️ **重放那一路不补**（`resume_from`）：账本第一步本来就是 goto，再导航一次是白跑。
        # ⚠️ **先过暂停闸**（`_stop_or_raise`）：人已经喊停 / 已经暂停时，
        # **这一下也不许发生** —— 「停是安全的，它不让任何一步发生」那条不变量。
        # ⚠️ 它**不走 `dispatch`**（试过，太重）：走 dispatch 就等于把它算成模型的一步，
        # 还会触发步拍、把后面每一步的编号整体挪一格 —— 而它其实是**站位**，不是探索。
        # 代价是它不进 `journey.steps`，所以**必须有一句人话说出来**（见 `_enter_target`）。
        if not resume_from:
            _stop_or_raise(paused, journey, taken, limits, success_text)
            _enter_target(session, url, journey)

        opening = _brief(url, goal, limits, plan, hints=hints, success_text=success_text)
        if journey.replay:
            opening = _with_resume(opening, resume_from, journey.replay)
        rounds = llm.run_tool_loop(
            _SYSTEM, opening, specs, dispatch,
            max_rounds=limits.max_rounds, max_tokens=MAX_TOKENS,
            steer=steer, _client=gate,
        )
        _wrap_up(journey, rounds, limits)
        #: ★ 2026-09-22 真事（用户原话：「**明明成功了但是却不知道，一直没产物空转**」）：
        #: 判据**只认 `observe` 读到的正文** ⇒ 模型提交之后没再看 ⇒ 系统**永远不知道成了**
        #: ⇒ 自动重探 ×3、空转、最后没有产物。⇒ **收摊前系统自己看一眼**（浏览器就在手边）。
        _final_success_check(journey, dispatch, limits, success_text,
                             specs=specs, gate=gate)
    except _Stop as stop:
        journey.stop_reason = stop.reason
        # 被停下来这一路**拿不到轮数**：`rounds` 是 `run_tool_loop` 的局部变量，
        # `_Stop`（BaseException）一穿出去就没了。所以只能是 0 + 一句人话 ——
        # **不许编一个数**（P5）。那个 0 的意思是「没量到」：读账的人一律走
        # `rounds_measured()`（**不是**各自去比 `stop_reason == "paused"` ——
        # 那个写法在本片新增 `window_gone` 之后就已经漏了）。
        journey.rounds = 0
        journey.usage = {}
        # ⚠️ **顺序有讲究**（复审 I-4）：`graph._journey_say` 的尾巴取的是 `notes[-1]`,
        # 而**有信息的是停因那句**（带「卡在第几步、卡在哪一句描述上」）。轮数那句是
        # bookkeeping，先记 —— 反过来写，人最终看到的就是那句 bookkeeping。
        journey.note(_rounds_lost_note(stop.reason))
        journey.note(_stop_note(stop.reason, len(journey.steps), stop.detail))
    finally:
        # 计划模式的账**在 `finally` 里收**：被打断 / 停滞 / 预算到顶那几条路上 `rounds`
        # 一样拿不到，但 `_PlanWatch` **每轮都在场** —— 「怎么停的」不该决定「账还在不在」。
        # （`plan.ledger()` 只读那几轮记录，一步没走到也照样「每一项都有交代」。）
        if watch is not None:
            journey.plan_ledger = plan_module.ledger(plan, watch.rounds)
            watch.finish()              # 最后一轮也要结算（它没有下一次边界）—— **不抛停**
        if step_shots is not None:
            # 步拍也要收尾 —— 一趟**以动页面动作收尾**的探路（模型收工 / 预算到顶 /
            # **人按停**）永远不会再来一次观测，那个没结算的点前图得删掉
            # （「按停」正是这个功能的主交互，这条路上不收就是天天漏）。
            _shots(step_shots.finish)
        # 起点那一页**与后面所有页都不同源**时，撤掉它的 `when`（见 `_drop_incidental_start_when`）。
        _drop_incidental_start_when(pages.pages, journey)
        journey.pages = [{"name": p["name"], "when": p["when"], "url": p["url"],
                          "title": p["title"]} for p in pages.pages]
        if own_session:
            session.close()
    return journey


# ──────────────── 续跑：照账本走回去 + 说给模型听（§1.7）────────────────


def _walk_back(journey: Journey, rows: list, session, emit, alive, boundary: str = "",
               fresh=None) -> None:
    """续跑的开头：**照账本走回去**（0 模型调用），并如实地把这件事记进账。

    三样都记（少一样，读账的人就得靠猜）：

    - **每一步**：`origin="replay"`，与探索时那一步**同形**（产物那侧一个字的改动都不需要）；
    - **一句人话**：重放了几步、停在哪、为什么 —— 这句进 `notes`，也进 `explore_say`；
    - **`journey.replay`**：`replay` 的原样产物，外加 `boundary_reason`
      （那段前缀**为什么停在那儿** —— 它来自切前缀那个人，重放自己不知道）。

    ⚠️ 重放的步**进 `journey.steps`**（它就是「这一趟做过什么」的账），而**不进预算** ——
    预算数的是 `dispatch` 那几下（模型花的钱），重放一下都不花（§1.8）。
    """
    def record(step: dict) -> None:
        journey.steps.append(step)
        emit(step)

    out = replay(session, rows, on_step=record, alive=alive, fresh=fresh)
    journey.replay = dict(out)
    if str(boundary or "").strip():
        journey.replay["boundary_reason"] = str(boundary)
    journey.note(
        "这一趟开头**照账本重放**了 %d 个动作（0 模型调用）：%s%s"
        % (int(out.get("done") or 0), out.get("why") or "",
           (" 边界（它为什么停在这儿）：" + str(boundary)) if str(boundary or "").strip() else ""))


def replay_went_clean(journey, rows: list) -> bool:
    """这一趟的重放**一次过吗**（没被打断、也没因为窗口抖动重来第二遍）。

    ⚠️ **两个条件都要**（复审 ②）：只判「没走完」（`replay_cut_short`）是不够的 ——
    第 3 遍**可以走通**，于是「用满 3 次尝试」并不等于「被打断」，那一形会整个漏掉。
    合起来才等于「这一趟对这条路做了一次**干净**的观察」——`graph._worth_retrying`
    拿它决定要不要再烧一趟（这就是「不许内外两层 3 次叠加」那根结构线的全部）。
    """
    info = dict(getattr(journey, "replay", None) or {})
    if not info:
        return True                                # 压根没重放（不是续跑）—— 没有干不干净这回事
    if int(info.get("attempts") or 1) > 1:
        return False                               # 抖过：重来过第二遍
    return not replay_cut_short(journey, rows)     # 没被打断


def replay_cut_short(journey, rows: list) -> bool:
    """重放**没走完**（该做的动作没做齐）—— 判据是**数出来的**，不是读 `why` 那句话。

    数的是**动作**（`replay_actions`）：账上那些「看一眼」不算动作，`replay` 也不会去动它们。

    ⚠️ **两个直接读者**：`explore`（重放没走完 + 窗口说死了 → 就地停）与 `replay_went_clean`
    （把这一条并进「一次过吗」）；后者又喂 `graph._worth_retrying` —— 重放走得不干净的那一趟
    **不再重探**（「不许内外两层 3 次叠加」靠的就是这一条：重探只会把同一段在真页面上再撞一遍）。

    ⚠️ **「为什么走得不干净」这件事，判据看不出来**（Task 6 修复轮 3 就地改正：原先这里写着
    「被打断 = 窗口抖了」—— 那是**被复审证伪的那个等价**）：判据**只看数**
    （`done < 该做的动作数`），而走到这个数的路有**两条**：① 窗口在重放途中抖了
    （`_WindowGone`）；② 工具报了错**而窗口全程活着**（页面上找不到那一步的元素）。
    所以下游那句人话不许一口咬定是哪一种 —— 见 `_worth_retrying` 与
    `_rounds_lost_note` 旁边那条同一族的改正，以及钉住它的变异 `R2-note-blames-the-window`。
    """
    want = len(replay_actions(rows))
    return int((getattr(journey, "replay", None) or {}).get("done") or 0) < want


def _with_resume(opening: str, rows: list, result: dict) -> str:
    """把「这一趟是接着上一趟走的」说给模型听（设计注 §1.7）。

    **给**：重放过的步（一句话一步，`note` 就是现成的人话）、现在落在哪、
    **边界那一步为什么没被重放**（那是模型最需要知道的边界信息：它决定「从哪儿接着走」）。
    **不给**：那些步的原始工具返回 —— 两个理由都硬：①省下来的轮数不该又用 token 付一遍；
    ②重放里可能有**不该让模型当成「我做过」**的动作，明说是重放，它才会在该确认时重新确认
    （`_SYSTEM` 规矩 3：click 返回 ok 只代表命令下发了）。
    """
    done = replay_actions(rows)
    lines = ["· " + (str((r or {}).get("note") or "").strip() or _step_label(r)) for r in done]
    if not lines:
        lines = ["· （账上这一段没有动作可重放 —— 只有几眼观察）"]
    return "\n\n".join([
        opening,
        "⚠️ 这一趟**接着上一趟走**：下面这些动作是系统照上一趟的账本**重放**过来的"
        "（是它自己走的，不是你做的 —— 别当成自己刚做过；要确认就再 observe 一次）。",
        "重放了 %d 个动作：\n%s" % (len(done), "\n".join(lines)),
        "现在落在：%s" % (result.get("landed") or "（账上没记下地址）"),
        "**再往前就没有重放了**，因为：%s"
        % (result.get("boundary_reason") or result.get("why") or "（没说为什么）"),
        "接着往下探：先 observe 确认自己在哪一页，再从这一页继续。",
    ])


# ─────────────────────── 停止条件（人 / 预算 / 成功线）───────────────────────


#: 停因：**这一趟已经在页面上见到了人给的那句成功文案**（Task 15）。
#:
#: 为什么它是一条**独立的**停因，而不是并进「预算到顶」那一族：它说的是「这一趟**成了**」。
#: 图那边据此照常往下写 py（`state.FINISHED_EXPLORATION`），**不是**按「没走完」处理 ——
#: 「拿半份账本写 py」是 R0 那条禁忌，而这里账本是**全的**（通向成功的那条路就在里面）。
#: 名字与图上事后结算的 `_explore_reached_success` **同一个事实**（同一个词，别再造一个）。
STOP_REACHED_SUCCESS = "reached_success"


#: 收尾那一眼之前**等页面静下来**多久（秒）—— 用户原话：「到最后一步执行完之后**等待页面
#: 完毕**了就截图分析下」。真站上提交之后那一页常常还在换（跳转 / 异步回填），
#: 立刻读会读到一半的页面（`job-46ed69c04b5c` 那类「差一步没认出来」里就有这个影子）。
FINAL_SETTLE_SECONDS = 3.0

#: 收尾那一眼最多问模型几轮（1 = 叫它截图，2 = 它照着图回答）。⚠️ 这是**旁路**，
#: 绝不许多问（它跑在收摊那一刻，人正等着）。
FINAL_LOOK_ROUNDS = 2

#: 收尾取**整页正文**时最多留多少字（`eval` 那一手）。⚠️ 按用户口径（「让**代码**判断」），
#: 判据要能在**足够长**的正文里找 —— cdp 的 `observe` 只给前 600 字，
#: 而真站的成功文案常在 600 字之后（2026-09-22 那趟就是这样：模型在图上看见了，
#: 代码在 600 字里找不到 ⇒ 屏幕上「言行不一致」）。
FINAL_TEXT_CHARS = 20000
#: 取整页正文的那段 JS —— ⚠️ **只读**（不点、不填、不选元素）。
#: 为什么还是得写这一段：`observe` 的正文被它自己截到 600 字（`internal/observe.go`），
#: 而这里要的是**整页**；shadow 那条坑由**同一轮紧挨着的 `observe`** 兜着（两处一起读）。
_FULL_TEXT_JS = ("(function(){try{return (document.body && document.body.innerText || '')"
                 ".replace(/\\s+/g,' ').trim().slice(0, %d);}catch(e){return '';}})()")

#: 收尾「照着图找那串字」那句的 system（**只问这一件事**：别让它顺手改页面）。
_FINAL_LOOK_SYSTEM = (
    "你只做一件事：看一眼当前页面的**截图**，回答「下面这串字有没有出现」。"
    "先调一次 `screenshot`，然后**照抄**你在图上看到的那串字；确实没有就说「没有」。"
    "**不要**点任何东西、不要填任何东西 —— 页面已经走完了，你只看。"
)


def _frames_used(journey) -> list:
    """这一趟**动手用过**的那几个 `frame_id`（去重、保序、最多 4 个）。

    ⚠️ 为什么要它们：真站的表单常挂在**跨源 iframe** 里（`job-9b48f2b513ec` 的模型原话：
    「真正的表单在另一个域的应用里……所以对表单的一切操作必须带 frame_id」）——
    只读主帧的正文，成功文案写在子帧里就永远搜不到。
    """
    out: list = []
    for step in getattr(journey, "steps", None) or []:
        got = str(((step or {}).get("target") or {}).get("frame_id") or "").strip()
        if got and got not in out:
            out.append(got)
    return out[:4]


def _final_success_check(journey, dispatch, limits, success_text: str, *,
                         specs=(), gate=None) -> None:
    """收摊前**系统自己**看一眼那一页 —— 判据用**这一眼**（真读数），而不是「模型看没看」。

    ⚠️ 只在**还没见到成功文案**时才看（真见到了就不多花这一步，别的路一个字节不变）。
    ⚠️ 这一眼是**真的**：走的是与模型同一条 `dispatch("observe", …)` ⇒ 读到的正文原样进账本，
    判据一个字不放宽 —— 它**不是**「补一个假的成功」，只是**不让「没人看」被读成「没成功」**。
    ⚠️ 看一眼不成就**照实不算**（不编），也不许把这一趟带塌。

    ★ 2026-09-22 按用户要求做实（原话：「成功条件应该要允许他搜索更多文本**或者他能理解图片**吗？
    到最后一步执行完之后**等待页面完毕**了就**截图分析**下」）：三件事按顺序来 ——

    ① **等页面静下来**（`FINAL_SETTLE_SECONDS`）：提交之后那一页还在换，立刻读会读到一半；
    ② **搜索更多文本**：主帧 + **这一趟动手用过的每一帧**各看一眼
       （`_frames_used`：真站的表单常挂在跨源 iframe 里，只读主帧永远搜不到子帧里的那串字）；
    ③ 两者都没有 ⇒ **截图 + 让模型照着图找**（`_vision_look`）——
       判据仍是子串那一把，只是这次读的是**图**；见了就记 `journey.vision_hit`，
       并且**必须写明是在图上**（`vision_say` 原样留着，人自己看）。
    """
    if not success_text or _success_hit(journey.steps, success_text) is not None:
        return
    #: ① 等页面静下来（用户原话里的「等待页面完毕」）
    time.sleep(FINAL_SETTLE_SECONDS)
    before = len(journey.steps)
    #: ② 主帧 + 用过的每一帧（正文全记进来 ⇒ 判据能搜到更多文本）
    for frame_id in ["", *_frames_used(journey)]:
        try:
            dispatch("observe", {"frame_id": frame_id} if frame_id else {})
        except BaseException:                  # noqa: BLE001 —— 旁路不许带塌这一趟
            continue
    #: ②b **整页正文**（`eval`，只读）—— ⚠️ **判据要读的正文就在这儿**：
    #: `observe` 那一手自己把正文截到 600 字（`tools/cdp/internal/observe.go` 的
    #: `pageText.slice(0, 600)`），而真站的成功文案常在 600 字之后
    #: ⇒ 2026-09-22 那趟屏幕上「言行不一致」就是这么来的（模型在图上看见了、代码在 600 字里找不着）。
    #: 用户口径：**这一步让代码判断**（图只是给人看的证据）。
    eval_raw = None
    try:
        eval_raw = dispatch("eval", {"code": _FULL_TEXT_JS % FINAL_TEXT_CHARS})
    except BaseException:                      # noqa: BLE001 —— 旁路不许带塌这一趟
        eval_raw = None
    if len(journey.steps) > before and eval_raw is not None:
        got = _norm(eval_raw if isinstance(eval_raw, str)
                    else ((eval_raw or {}).get("result") or (eval_raw or {}).get("value") or ""))
        if got:
            journey.steps[-1]["result"]["page_text_full"] = got[:FINAL_TEXT_CHARS]
            journey.steps[-1]["note"] = ("收摊前**系统自己**取的**整页正文**（只读；"
                                         "`observe` 那份只有前 600 字）：判据就在这份里找")
    if len(journey.steps) == before:
        return                                 # 一步都没记上（被闸拦住等）⇒ 不假装
    for step in journey.steps[before:]:
        #: 它们是**系统**看的，不是模型看的（`origin` 要让读账的人一眼看出来）——
        #: 顺带把来由说清楚（账本里不许有来路不明的一步）。
        step["origin"] = "final_check"
        step.setdefault("note", ("收摊前**系统自己**看了一眼（模型这一趟没看它）："
                                 "拿这一眼的正文判成功文案"))
    if _success_hit(journey.steps, success_text) is not None:
        return                                 # 代码在正文里见着了 ⇒ 收工
    #: ③ 正文里还是没有 ⇒ 照一张图存成**证据**（⚠️ **判据不由它定** —— 用户口径：代码判断）
    _vision_look(journey, dispatch, success_text, specs, gate)


def _vision_look(journey, dispatch, success_text: str, specs, gate) -> None:
    """③ **照着图**再找一遍那串字（用户要求：「截图分析下」）。

    ★ 2026-09-22 口径（用户后一句更明确的话）：「**不然这一步就让代码判断而不是让AI判断**
    我记得有个可以提取文字的」⇒ 这一步**只当证据**：

      · **判据不由它定** —— `graph._explore_reached_success` **不看** `vision_hit`
        （它只认代码读到的正文）；图上见到的消息**摆到人面前**，由人按「继续」决定；
      · 它**同时是「不许再自动重探」的判据**（`graph._retry_wont_help`）：图上都见着了，
        再拿真站试一趟只是又交一次表单（用户原话「不要重复执行」）；
      · **base64 不进账本**（那条硬规矩）：账上只留它说的话（`vision_say`）；
      · 给不了图 / 问不了模型 ⇒ 什么都不做（照实算没见到，**不许**补一个假的成功）。
    """
    shot_specs = [s for s in (specs or []) if _spec_name(s) == "screenshot"]
    if not shot_specs or gate is None:
        return
    ask = ("这一趟走完了。请**调一次 `screenshot`** 把当前页面截下来，然后照着图回答："
           "页面上**有没有出现**下面这串字（原样照抄你看到的；确实没有就说「没有」）：\n"
           "  「%s」" % "」、「".join(wanted_texts(success_text)))
    try:
        rounds = llm.run_tool_loop(_FINAL_LOOK_SYSTEM, ask, shot_specs, dispatch,
                                   max_rounds=FINAL_LOOK_ROUNDS, max_tokens=MAX_TOKENS,
                                   _client=gate)
    except BaseException as exc:               # noqa: BLE001 —— 旁路不许带塌这一趟
        journey.note("收尾照图那一眼**没做成**（%s: %s）—— 正文里没见到成功文案，"
                     "这一条照实算「没见到」。" % (type(exc).__name__, exc))
        return
    said = str((rounds[-1].get("content") or "") if rounds else "").strip()
    journey.vision_say = said
    wants = [w.lower() for w in wanted_texts(success_text)]
    journey.vision_hit = bool(wants) and any(w in _norm(said).lower() for w in wants)
    if journey.vision_hit:
        journey.note("👁 收尾照图那一眼**在图上见到了**那句成功文案 —— 模型照着截图的原话是："
                     "「%s」。（⚠️ 这是**证据**，不是判据：判据只认**代码读到的那份正文**"
                     "（`page_text_head` + `page_text_full`）。图上见着了、正文里没有 ⇒ "
                     "这一趟照实算「没在正文里见到」，**由你定**：按「继续」就往下走，"
                     "改「什么算成功」那一格就重新来一遍。另外它也是一条止损："
                     "图上都见着了，就**不再自动重探**（重探 = 再交一次表单）。）" % said)
    else:
        journey.note("收尾照图那一眼**没在图上找到**那句成功文案 —— 模型的原话是：「%s」"
                     "（正文里也没有 ⇒ 这一趟**没见到**成功文案，照实算。）" % (said or "（它什么都没说）"))


def _spec_name(spec) -> str:
    """一个工具 spec 里那个**名字**（形状由 MCP/OpenAI 那一侧定）。"""
    try:
        return str(((spec or {}).get("function") or {}).get("name") or (spec or {}).get("name") or "")
    except AttributeError:
        return ""


#: 判据那一格写成**说明句**时的开头（「我要它出现」的意思）—— 这些词是**壳**，
#: 不是页面上会有的字。长的排前面（`出现文字` 先于 `出现`）。
DESCRIPTIVE_CRITERION_HEADS = ("出现文字:", "出现文字：", "出现文字", "页面上出现", "页面出现",
                               "出现", "显示", "看到", "页面上", "shows", "show ", "contains")


def strip_criterion_head(word: str) -> str:
    """判据里的一句 → **真要去找的那串字**（剥掉「出现 / 显示 / 页面上…」这种说明壳）。

    ★ 2026-09-22 用户贴出的一趟真事（模型刚说完「有，出现了。图上那行紫色条里的字是：
    `Tailor Your Cover`」，系统下一句就是「**没在页面上见到成功文案**」——
    用户的原话：「为啥会这样言行不一致」）：
    那一格写的是「**出现 Tailor Your Cover**」，而页面上真正的字是 `Tailor Your Cover`
    ⇒ 字面比「出现 Tailor Your Cover」**永远**匹配不上 ⇒ 屏幕上一句真话、模型一句真话，
    两句互相打架。**病根就是没有剥这个壳。**

    ⚠️ 剥壳**更严不更松**：页面上真写着「出现 Tailor Your Cover」时，它也含 `Tailor Your Cover`
    ⇒ 照样算见到（剥了只是不可能**更**容易匹配）。
    ⚠️ 壳剥完什么都不剩（那一格只写了「出现」）⇒ 返回**空串** —— 由调用方去说人话，不编。
    """
    text = _norm(str(word or ""))
    for head in sorted(DESCRIPTIVE_CRITERION_HEADS, key=len, reverse=True):
        if text.lower().startswith(head.lower()):
            return _norm(text[len(head):]).lstrip(" :：,，。;；-—").strip()
    return text


def wanted_texts(success_text) -> list:
    """判据 → **真要去找的那几串字**（剥掉说明壳；空的不留）。一处实现，三处共用。

    谁用它：`_success_hit`（这一趟读过的正文）、`_vision_look`（图里读到的原话）、
    `graph._explore_reached_success`（图上结算）。⚠️ **三处必须是同一把尺子** ——
    活的探路说「见着了」、图上说「没见到」，人读到的就是两句打架的话（这一格出过真事）。
    """
    words = [success_text] if isinstance(success_text, str) else list(success_text or [])
    out: list = []
    for word in words:
        got = strip_criterion_head(word)
        if got and got not in out:
            out.append(got)
    return out


def row_text(row) -> str:
    """一行账**读到的正文**（`observe` 的摘要 或 收尾那次**全文**读）。

    ⚠️ 两处必须是同一个函数：`page_text_head` 是 `observe` 那一手的（cdp 自己**截到 600 字**，
    `tools/cdp/internal/observe.go` 的 `pageText.slice(0, 600)`），`page_text_full` 是收尾
    那次 `eval` 取的**整页正文**（2026-09-22：用户要求「让**代码**判断……有个可以提取文字的」
    —— 600 字装不下很多真实页面，判据就是在这上面栽的）。
    判据读的是**加起来**的那份文字，所以两处都算。
    """
    result = (row or {}).get("result") or {}
    return _norm("%s %s" % (result.get("page_text_head") or "",
                            result.get("page_text_full") or ""))


def is_look(row) -> bool:
    """这一行**算不算「一眼」**（判据只在「看过」的行里找）。

    两条都算：`observe`（模型或系统看的）与**任何带着 `page_text_full` 的行**
    （收尾那次 `eval` 读的整页正文 —— ⚠️ 它读的**也是正文**，只是那一手比 `observe`
    取的多；把它排除在外，就等于判据看不见自己刚读回来的那两万字）。
    ⚠️ `page_text_head` 仍然只有 `observe` 行才写（R3 的前提，`_only_looks_carry_the_text`
    会当场抛）—— 这一条判的是「算不算一眼」，不是「谁写了哪个键」。
    """
    if str((row or {}).get("action") or "") == "observe":
        return True
    return bool((((row or {}).get("result") or {}).get("page_text_full") or "").strip())


def _success_hit(steps: list, success_text: str) -> int | None:
    """那句成功文案**最早**出现在第几步那一眼里（`None` = 没出现过 / 判不了）。

    判据与 `replayable_prefix` 的 **R3** 是**同一把尺子**：同一个 `_norm` + 子串，
    而且只认 `observe` 行的 `page_text_head`（R3 的**前提**：正文只有 `observe` 行才写，
    `_summarize` 是唯一的写点、破了会当场抛 `_TextPremiseBroken`）。
    R3 与这里问的是**同一个问题**（「过了那条线没有」），只是一个问在**重放**上、
    一个问在**活着的那一趟**上 —— 而「过了那条线之后的每一个动作都可能是重复的真实请求」
    这句话，在活着的那一趟上更贵。

    ⚠️ **两边必须一直是同一把尺子**：活的探路停下来说「见着了」、图上事后结算说
    「没见到成功文案」的话，人读到的是两句互相打架的话，而坏的那句会**拒绝写 py**。
    钉住它的用例：`test_the_line_the_live_run_stops_on_is_the_same_line_the_graph_settles`
    与 `test_the_two_norms_are_one_yardstick`。

    ⚠️ `success_text` 为空 ⇒ `None`（**判不了就不猜**）。空**不是**「没有约束」，
    更**不是**「什么都算成功」—— 拿空串当通配的话，第一页就会「匹配上」并自称成功，
    那是把「少给了一个输入」翻译成一句假话。口径与 `graph._explore_reached_success`
    给空的三态（`None`）一致。`replayable_prefix` 对空**抛**，因为它的输入由
    `service.reopen` 一处掌控；而这一格的输入来自**每一次** `explore()` 调用
    （`success_text` 是个可选参数），直接抛会把「调用方没传」变成异常 —— 判据的松紧搞反了。
    """
    wants = [w.lower() for w in wanted_texts(success_text)]
    if not wants:
        return None                       # 没给判据 / 剥完什么也不剩 ⇒ **判不了就不猜**
    for i, row in enumerate(steps or []):
        if not is_look(row):
            continue                      # 没看过页面的一行（动作类）⇒ 判据不该在它身上找
        head = row_text(row).lower()
        if head and any(w in head for w in wants):
            return i
    return None


def _stop_reason(paused, journey, taken: int, budget: Budget,
                 success_text: str = "") -> str | None:
    """该不该停下？返回理由或 None。**只看，不做**（做由调用方决定）。

    三条闸，**顺序就是优先级**（同时为真时报哪个，全看这里）：

    1. `paused` —— **人喊停**。人按下去的那一下永远是优先的那条（与 `_Gate` 那条
       「人 / 预算在前」的注释同源）。
    2. `reached_success` —— **页面上已经出现了人给的那句成功文案**（`_success_hit`）。
       见到就收摊：过了那条线之后**每一个动作都可能是重复的真实请求**
       （这句话是 R3 的判据原文，Task 15 把它从重放接到了活着的那一趟上）。
    3. `budget_steps` —— 预算到顶。

    ⚠️ ②排在③**前面**是**有意的**，不是顺手：两条同时为真时（看见成功文案的那一眼
    正好是预算允许的最后一步），报 `budget_steps` 会把这一趟判成「没走完」⇒ 图那边
    **拒绝写 py** —— 而它明明成了。停的**时刻**两条**一模一样**（都在下一步之前、
    都不做那一步），变的只是**理由报哪个**；`paused` 与 `budget_steps` 各自的语义
    因此一个字没动。钉住这两条的用例：
    `test_the_human_stop_is_still_reported_as_the_human_stop` /
    `test_a_budget_stop_that_never_saw_the_line_is_still_a_budget_stop`。
    """
    if paused is not None and paused(journey):
        return "paused"
    if _success_hit(journey.steps, success_text) is not None:
        return STOP_REACHED_SUCCESS
    if taken >= budget.max_steps:
        return "budget_steps"
    return None


def _stop_or_raise(paused, journey, taken: int, budget: Budget,
                   success_text: str = "") -> None:
    """该停就抛 `_Stop` —— **两道闸共用这一个出口**（每步之前 / 每轮之前）。

    ⚠️ 人那道闸**自己抛异常**时，这里把它**归一成「暂停」**。不这么做的话，闸的错误
    会以普通 `Exception` 的身份落到 `llm.run_tool_loop` 的 `except Exception` 上 ——
    被记成**一次工具失败**、然后**循环继续**：人的中断静默降级成「有个步骤失败了，继续吧」。
    那是这条路上最坏的形状（喊停没停，而且没有任何人看得出来）。
    闸坏了要**停下来**（带上它坏在哪），不能带着一个坏掉的闸往下跑。
    """
    try:
        reason = _stop_reason(paused, journey, taken, budget, success_text)
    except _Stop:
        raise
    except Exception as exc:                           # noqa: BLE001
        raise _Stop("paused", detail=f"那道闸自己抛了 {type(exc).__name__}: {exc}") from exc
    if reason:
        raise _Stop(reason)


def _window_is_dead(alive) -> bool:
    """问一句「窗口还死了没」。**只有明确说死了才算**（三态：True / False / None）。

    ⚠️ **三态里只有「明确说死了」（`False`）算死**：`True` 是活，而「问不出来」（`None`）
    是**不知道** —— 不知道**不是**死。

    这一条与 `replay` 那边的 `_probe_dead` **是同一个取向**（复审 Q1 ③ 要求一致）：
    两处问的虽然是两件事（那边「这次失败该不该归到窗口头上」，这边「这个 job 的结局叫什么」），
    但**面对同一个 `None` 必须给同一个答案**。一个当死一个当活的后果是实打实的：
    重放会去关掉一个还活着的会话，而这边不认「窗口没了」⇒ 整趟烧到预算停因才停。

    `alive=None`（这个部署没有窗口层）同样按「不知道」算：**不编**一个停因出来。
    探针自己抛异常也一样（与「人的那道闸坏掉归一成暂停」那条规矩的取向一致：
    坏掉的探针不许把一次普通失败升级成一个停因）。
    """
    if alive is None:
        return False
    try:
        return alive() is False
    except Exception:                                  # noqa: BLE001
        return False


def _stop_note(reason: str, steps: int, detail: str = "") -> str:
    if reason == "paused":
        if detail:
            # 别骗人：这不是人喊的停，是**人的那道闸坏了**。两件事不能混成一句。
            return (f"人那道闸自己出了问题（{detail}）—— 按「人喊停」处理：在第 {steps + 1} 步之前"
                    "停下来，这一步**没有做**。宁可停下，也不带着一个坏掉的闸往下跑。")
        return (f"人喊停：在第 {steps + 1} 步之前停下来 —— 这一步**没有做**，页面保持原样"
                "（§6.2：人是每一步都在的旁路，不是最后一关）")
    if reason == STOP_REACHED_SUCCESS:
        # ⚠️ 这是**成功的收尾**，不是「没走完」（那两句话下游处置相反：一说「成了、
        # 照常写 py」，一说「半份账本写不出对的 py」）。措辞里**不许**出现「没走完」。
        return ("这一趟**成了**：走到第 %d 步那一看，页面上已经出现了**人给的那句成功文案**"
                " —— 见到就收摊，下一步**没有做**（过了那条线之后每一次点击都可能是"
                "**重复的真实请求**；真站那一趟就是这么又点了几下、最后要人按停）。" % steps)
    if reason == "budget_steps":
        return f"预算到顶：走满 {steps} 步就停下（预算是**防跑飞**，不是省钱）"
    if reason == "budget_rounds":
        return (f"预算到顶：问满 {steps} 轮就停下 —— 模型一直在调工具、没有自己收尾"
                "（预算是防跑飞，不是省钱）")
    if reason == "window_gone":
        # 窗口死掉是**一等停因**（§1.8）：它说得出理由，而且**账本还在** ——
        # 处置与「预算走完」相反（那边续跑只是再烧一次）。
        return ("窗口没了：%s。**账本还留在盘上** —— 重开一个窗口就能从断点接着走"
                "（`POST /job/<id>/reopen`），不用从头再探一遍。"
                % (detail or "连着几次工具调用都没成"))
    if reason == "plan_stalled":
        # §2.5：人话里要带上**它卡在第几步、卡在哪一句描述上**（`detail` 就是那个）。
        return (f"计划停滞：{detail or '连着几轮没有推进'} —— 在这里停下说话，"
                "别接着在真页面上试（继续试只会多点几下真页面，那正是这条计划要少做的事）")
    return f"停下（{reason}）"


def _rounds_lost_note(reason: str) -> str:
    """「这一趟的轮数没记到」那句话（P5：`0` 的意思必须说清，**不许编一个数**）。

    两条纪律（都是复审指出的，必须同时成立）：

    1. **按停因分开**：原先只有「被人打断」一种说法，可停滞与预算到顶都不是人喊的停 ——
       拿那句话去说它们，读账的人会以为是人停的；
    2. **内部停因的 token 不许进人话**（M-5）：`plan_stalled` 这种是给代码看的，
       这份账的读者是非技术的人（D16）—— 停因本身在 `journey.stop_reason` 里，账上不缺它。
    """
    if reason == "paused":
        return ("这一趟被人打断了，**没记到轮数**（打断的信号一穿出工具循环，"
                "那个数就没了）—— 这里的 0 是「没量到」，不是「一轮都没花」。")
    if reason == STOP_REACHED_SUCCESS:
        # ⚠️ 与「人喊停」同一个形状（`_Stop` 一穿出工具循环，`rounds` 就丢了），
        # 但**不是同一件事**：这一趟是**见到成功文案收的尾**。所以那句话
        # （「没走完就停下了」）**不许**用在这儿 —— 它会把一次成功说成一次白跑。
        return ("这一趟是**见到成功文案收的尾**，可轮数一样**没记到**（停止的信号一穿出"
                "工具循环，那个数就没了）—— 这里的 0 是「没量到」，不是「一轮都没花」。")
    return ("这一趟没走完就停下了（**不是人打断的** —— 为什么停，紧挨着的那一条记着），"
            "同样**没记到轮数**（停止的信号一穿出工具循环，那个数就没了）—— "
            "这里的 0 是「没量到」，不是「一轮都没花」。")


def rounds_measured(journey) -> bool:
    """这一趟的轮数**量到了没有**（P5：`0` 的两种意思必须分得开）。

    判据是 `Journey.rounds_measured` 那个**标记**（复审裁定：换掉原先那张按停因列的
    白名单）—— 名单要人维护、会漏；标记**落在事情发生的那一句旁边**：
    `_wrap_up` 写下 `rounds` 的同时把它置 True，而 `_Stop` 那一支**天然**走不到那里
    （它一穿出 `run_tool_loop`，`rounds` 那个局部变量就没了），标记保持 False。
    **没有表要维护，方向也是安全的那一侧**（新停因默认落到「没量到」）。

    ⚠️ 今天的读者只有 `service._note_attempt`（写 `attempts.jsonl` 那一行）。
    `graph._spend_of` **不走它**：那边记的是 `journey.rounds` 那个数本身，
    没量到的趟本来就是 0（加 0 等于没加）—— 这一条写清楚，免得下一个人以为
    「两处都读它」（那句话曾经是错的，复审 Q2）。
    """
    return bool(getattr(journey, "rounds_measured", False))


def _wrap_up(journey: Journey, rounds: list, budget: Budget) -> None:
    """判定它**是怎么结束的**。

    ⚠️ 模型每一轮说的话**不在这里记** —— 归 `_Gate`（它每轮都在场，包括被人打断的
    那条路上；见它的 docstring）。两处都记就会在没被打断时记成两份。

    轮数（G2）与它的汇总账**在这里落**：原先 `rounds` 用完就丢，于是「一次探路问了几轮」
    这个数**算出来了却没人接住**，基线 M3 只能靠 `steps` 反推 —— 而 steps 数的是
    **工具调用**（含 observe），与「模型想了几轮」不是一回事（实测 60 步 ≠ 60 轮）。
    """
    journey.rounds = len(rounds)
    #: ⚠️ **就在这一句旁边**（复审裁定）：这个标记就是「轮数量到了」的判据本身 ——
    #: 它跟 `rounds` 同生共死，所以没有第二张名单要维护，也不会漂。
    journey.rounds_measured = True
    journey.usage = llm.summarize(rounds)
    last = rounds[-1] if rounds else None
    if last is None:
        journey.stop_reason = "no_rounds"
        journey.note("一轮都没跑起来 —— 模型一次都没回话")
    elif not last.get("tool_calls"):
        # **C3**：没有 tool_calls = 它讲完了（那道门上没有 done()）
        journey.stop_reason = "model_done"
        journey.final_answer = (last.get("content") or "").strip()
        if last.get("steered"):
            # **R11**：人刚插了一句话，而**这一轮**它就收尾了（`steered` 与「没有 tool_calls」
            # 同时落在最后那一轮上 —— 循环一遇到没有 tool_calls 的轮就停，所以只可能是它）。
            # 记一句人话给人看：这可能是它**被那句话逼收的尾**（那道门上没有 `done()`，
            # 「不调工具」在图上就是「探路走完了」），人得自己看一眼结论对不对。
            # ⚠️ 只有**真插过话**才记（没插话也记 = 每次收尾都把人那句话搬出来，那是编话）。
            journey.note(STEER_WRAPPED_UP_NOTE)
    elif len(rounds) >= budget.max_rounds:
        journey.stop_reason = "budget_rounds"
        journey.note(_stop_note("budget_rounds", len(rounds)))
    else:
        journey.stop_reason = "ended"
        journey.note("循环停了，但最后一轮既没有 tool_calls 也没到预算上限")


def _as_budget(budget) -> Budget:
    if budget is None:
        return Budget()
    if isinstance(budget, Budget):
        return budget
    if isinstance(budget, int):
        return Budget(max_steps=int(budget))
    if isinstance(budget, dict):
        picked = {k: v for k, v in budget.items()
                  if k in ("max_steps", "max_rounds", "stall_limit")}
        # 字符串是**配置里最容易写错**的形状（`"6"` 看着就像个数），而它今天会炸在很远的地方
        # （`taken >= "6"` → 一句看不出是配置问题的 TypeError，复审 M-6 的探针 P-B）。
        # 在**边界上**说清楚：哪个键、给了什么。别的类型照旧（不新增门槛 —— 它们今天能用）。
        for key, value in picked.items():
            if isinstance(value, str):
                raise TypeError(
                    f"budget 里的 {key} 是字符串 {value!r} —— 要的是数字"
                    "（写错这一处，会炸到一半才在比较那一行现形，看不出是配置问题）")
        return Budget(**picked)
    raise TypeError(f"不认识这种 budget: {budget!r}（给 Budget / 步数 int / dict）")


def _as_predicate(callback) -> Callable[[Journey], bool] | None:
    """把 `should_pause` 收成「收 journey 一个参数」的形状。

    两种写法都收：`should_pause(journey)`（下层能看到进度）与 `should_pause()`（上层只有一个
    flag）。按参数个数判断，**不**靠 try/except TypeError —— 那会把回调里真正的 TypeError
    也读成「它是不带参数的」。
    """
    if callback is None:
        return None
    try:
        params = [p for p in inspect.signature(callback).parameters.values()
                  if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
        arity = len(params)
    except (TypeError, ValueError):        # 内建函数之类拿不到签名
        arity = 1
    if arity >= 1:
        return lambda journey: bool(callback(journey))
    return lambda journey: bool(callback())


class _PlanWatch:
    """计划模式的**位置 / 偏离 / 停滞**（设计注 §2.3–§2.5）。

    它挂在 `_Gate` 上 —— 那是**唯一每轮都在场**的东西（连被人打断的那条路也在场）。
    三样东西全是**感知或声明**，没有一条语义判断：

    - **位置**：模型那一轮的话里报的 `【第 k 步】`（**声明**）——
      系统**不判**「这一步做完没有」（D11：observe 只给感知不给判断）；
    - **页面状态**：`_Pages` 的换页（**感知**，现成的）；
    - **有没有做成动作**：那一步的 `result.ok`（**感知**），只认会改页面的那几种
      （`_ACTIONS`；看一眼不算，不然停滞判据永远响不了）。

    ⚠️ 别拿 `plan.ledger()` 当位置源（复审点名的那条）：账本是**收尾时**按终态算的，
    这里要的是**每一轮当场**的位置 —— 而且它只认「模型报的号」，与终态不是一回事。
    """

    def __init__(self, plan: plan_module.Plan, journey: Journey, stall_limit: int,
                 page_state: Callable[[], str], steps_taken: Callable[[], int]):
        self.plan = plan
        self.journey = journey
        #: ⚠️ `stall_limit <= 0` 一律**当 1**（夹住），不是「关掉判据」。理由：阈值判在
        #: 「刚刚清零」的那个值上也会成立（`0 >= 0`）→ 正常推进的探路会被掐死，
        #: 而人话会写着「连着 **0** 轮没有推进」—— 一个**自己说自己没在停滞**的停因。
        #: 不给「关掉」那个选项：它是条安全机制（卡住的探路会把预算全烧在真页面上），
        #: 夹到最小的有意义的值比静默关掉更稳。夹了会**说出来**（不静悄悄）。
        self.stall_limit = stall_limit if stall_limit > 0 else 1
        if stall_limit <= 0:
            journey.note(
                f"停滞判据的上限给成了 {stall_limit}（不是个能成立的阈值）——这一趟按 1 算；"
                "夹住而不是关掉：关掉之后卡住的探路会把预算全烧在真页面上。")
        #: `() -> str`：当前那一页在 `_Pages` 里的名字（换页 = 换状态）。
        self._page_state = page_state
        #: `() -> int`：已经走了几步（用来切出「这一轮新增的那几步」）。
        self._steps_taken = steps_taken
        #: 给 `plan.ledger()` 的每轮记录，**一轮一条**：`{"mark": int|None, "contradiction": str|None}`。
        self.rounds: list = []
        #: 上一个**确认过的**位置（清单上的号）。一次都没报过 = `None`。
        self.position = None
        self._baseline = None               # 这一轮开头：（页面名, 已经走了几步）
        self._moved = False                 # 这一轮里位置动过没有
        #: 这一轮**还没被结算**（`note_round` 置起、`_settle` 落下）——
        #: 它是「要不要结算」的判据，也是「最后一轮有没有被漏掉」的判据（`finish`）。
        self._open = False

    # ── 每轮两次：边界（＝上一轮做完了）与模型回话之后 ───────────────────

    def round_boundary(self) -> None:
        """一轮的**边界**：先结算上一轮（停滞判据），再给这一轮记基线。

        ⚠️ 「连着 N 轮」这个数只能在这里读 —— 这一刻上一轮的工具**全都做完了**。
        到顶就抛 `_Stop`：于是**下一轮的模型调用和工具调用一次都不会发出去**（§2.5）。
        """
        self._settle(raise_on_stall=True)
        self._baseline = (self._page_state(), self._steps_taken())
        self._moved = False

    def finish(self) -> None:
        """收尾：把**最后一轮**也结算掉 —— 它没有下一次边界（复审 M-4：不结算就少算一轮）。

        ⚠️ 这里**不抛停**：循环已经因为别的原因结束了（模型讲完了 / 人喊停 / 预算到顶），
        事后把 `stop_reason` 改成 `plan_stalled` 是**假话**。这一步只把那本数**算准**。
        （停的那条路不受影响：停永远发生在边界上，那一轮在抛之前就结算过了 ——
        此时 `_open` 已经是 `False`，这里是个空操作。）
        """
        self._settle(raise_on_stall=False)

    def note_round(self, content: str) -> None:
        """模型这一轮的话 → 位置标记 / 矛盾声明（都是**声明**，不是判断）。"""
        self._open = True
        k = plan_module.mark(content, plan=self.plan)
        contradiction = _contradiction_in(content)
        self.rounds.append({"mark": k, "contradiction": contradiction})
        if contradiction is not None:
            # §2.3(b)：模型**必须说出来**，说了就记进 `deviations`（**原话**，不改写 ——
            # 改写就不是事实了）。而且**位置停在这一步不前进**（这一轮不算推进）。
            # ⚠️ 只是**这一轮**不算推进：下一轮再报到哪儿都照记（§2.4：不判它该不该）。
            self.journey.deviations.append(contradiction)
            return
        if k is None:
            # §2.4：没报 / 报了个清单上没有的号 → 位置**不动**，如实记一句「不知道」。
            self.journey.note(_NO_MARK_NOTE)
            return
        if k != self.position:
            self.position = k
            self._moved = True

    # ── 停滞判据（§2.5）───────────────────────────────────────────────

    def _settle(self, *, raise_on_stall: bool) -> None:
        """结算**上一轮**：推进了就清零，没有就加一；到顶（且调用方要求抛）抛 `_Stop`。"""
        if not self._open:                  # 还没有轮 / 这一轮已经结算过了
            return
        self._open = False
        page_changed = self._page_state() != self._baseline[0]
        if self._moved or page_changed or self._acted():
            self.journey.stall_rounds = 0
        else:
            # 阈值**只在这一支里判**（复审 I-3）：原先判在公共的那一层上，于是「刚刚清零」
            # 也参与比较 —— `stall_limit=0` 时 `0 >= 0` 成立，正常推进的探路照样被判成停滞。
            self.journey.stall_rounds += 1
            if raise_on_stall and self.journey.stall_rounds >= self.stall_limit:
                raise _Stop("plan_stalled", detail=self._where_it_stuck())

    def _acted(self) -> bool:
        """上一轮里有没有**做成了一个会改页面的动作**（看一眼不算 —— 见类 docstring）。"""
        fresh = self.journey.steps[self._baseline[1]:]
        return any(step.get("action") in _ACTIONS and (step.get("result") or {}).get("ok")
                   for step in fresh)

    def _where_it_stuck(self) -> str:
        """停滞那句人话的细节：**卡在第几步、卡在哪一句描述上**（§2.5）。"""
        if self.position is None:
            return f"连着 {self.journey.stall_rounds} 轮没有推进（它一次都没报自己在第几步）"
        # 位置只可能是 `plan.mark()` 认下来的号（它保证那个号在清单上），所以这里必然找得到
        text = next(s.text for s in self.plan.steps if s.n == self.position)
        return (f"连着 {self.journey.stall_rounds} 轮没有推进，"
                f"位置停在第 {self.position} 步「{text}」上")


def _contradiction_in(content: str) -> str | None:
    """这一轮的话里有没有「描述说…页面上是…」这种**事实报告**；只判有没有，**不判性质**。

    摘下来的是**那一句原话**（改了就不是事实了）。模型**不许**去判「这是分支还是描述写错了」
    —— 它报告「描述这句话 vs 页面这句话」，定性归账本和人（§2.3）。
    """
    for chunk in _SENTENCE_RE.split(str(content or "")):
        sentence = chunk.strip()
        if sentence and _CONTRA_RE.search(sentence):
            return sentence
    return None


class _Gate:
    """在两个工具轮之间也问一次「该停了吗」——否则模型一轮丢来五个动作时，
    停只能发生在那一轮**做完之后**。

    它同时是**叙述的落点**：模型每一轮说的话，在这里**当场**记进 `journey.notes`。
    为什么不等到循环结束再统一记（那样更省事）：`_Stop` 是 `BaseException`，
    它**穿过** `run_tool_loop` 直接落到 `explore` 的 `except` 里，那些 `rounds`
    记录就此丢掉 —— 于是「被人打断」的那份 Journey 会比没被打断的那份**少掉模型的
    全部叙述**，恰恰在人最需要它的时候（正要靠那几句话决定「要不要接着跑」）。
    这道闸是唯一每轮都在场的东西，所以叙述归它 —— 计划模式的**位置与停滞也归它**
    （`watch`，同一个道理：只有每轮都在场，才记得住「连着几轮没推进」）。

    `on_note`（Task 5）：那一句**当场**也要给 Console 一份（`explore` 的 docstring）。
    ⚠️ 它**必须挂在 `_note` 里**（= 记进 `journey.notes` 的那一处），不许另找地方补：
    两处各触发一次的话，账本与时间线会有**两份不同的真相**（改了前缀、漏了被打断那一轮），
    而坐在屏幕前的人读到的就不一定是它真说过的那句。
    """

    def __init__(self, inner, check: Callable[[], None], journey: Journey, watch=None,
                 on_note: Callable[[str], None] | None = None):
        self._inner = inner
        self._check = check
        self._journey = journey
        self._watch = watch
        self._on_note = on_note
        #: 「旁路没送成」这件事**说过了**没有（别每轮刷一条 —— 与 `emit` 同一条规矩）。
        self._note_broken = False
        self.chat = _Namespace(completions=_Namespace(create=self._create))

    def _create(self, **kwargs):
        # 顺序：**先问该不该停**、再结算上一轮（两者都会「停」，但**理由要报对**：
        # 那三条闸（人 / 成功文案 / 预算）优先于「它自己卡住了」；三条之间的先后
        # 见 `_stop_reason` 的 docstring —— 这里**不是**那张优先级表，它只管这两步的先后）。
        self._check()
        if self._watch is not None:
            self._watch.round_boundary()
        resp = self._inner.chat.completions.create(**kwargs)
        content = _content_of(resp)
        self._note(content)
        if self._watch is not None:
            self._watch.note_round(content)
        return resp

    def _note(self, content: str) -> None:
        if not content:
            return
        said = f"AI 说：{content}"
        self._journey.note(said)
        if self._on_note is None:
            return                                # 没接这根线 = 今天那条路，一个字节不变
        # ⚠️ **旁路坏掉不许带塌主路**（与 `emit` 那条一模一样的规矩）：
        # 回调抛出去的话，它会以**模型这一轮自己炸了**的身份穿过 `run_tool_loop`，
        # 把一趟真探路变成废账 —— 而时间线坏掉不是产物的事。
        # 但也**不许静默**：坏掉这件事记进账本（人看得出来这一趟的时间线少了它的推理）。
        try:
            self._on_note(said)
        except Exception as exc:                   # noqa: BLE001 —— 外部世界，什么都可能抛
            if self._note_broken:
                return
            self._note_broken = True
            self._journey.note(
                "⚠️ 旁路（模型这一轮的话没送到时间线）没记成：%s: %s —— "
                "探路照常往下走（旁路坏掉不许带塌主路），但这一趟的时间线上会少掉它的推理。"
                % (type(exc).__name__, exc))


def _content_of(resp) -> str:
    """模型这一轮说的话（**形状不对就给空串**，不在这一步把循环带塌）。"""
    try:
        return (resp.choices[0].message.content or "").strip()
    except (AttributeError, IndexError, TypeError, KeyError):
        # 真形状由 llm.py 保证，它就在下一步读同一片。这里吞的只是「记不上人话」这件事，
        # 不是任何一条错误。
        return ""


class _Namespace:
    """极简的「只有属性」的壳（用来把 create 挂成 chat.completions.create）。"""

    def __init__(self, **kw):
        self.__dict__.update(kw)


# ─────────────────────── 每一步：描述 / 记账 / 人话 ───────────────────────


def _describe(name: str, args: dict, pages: "_Pages", journey: Journey) -> tuple:
    """开一个「步」的骨架：状态归属 + target（**声明式多元描述**）+ 这一步填什么。

    返回 `(step, fill)`。⚠️ fill 是**跟着 step 一起算出来的，但不塞进 step 里** ——
    step 的形状（`{state, action, target, result, note, origin}`）是要给外面看的，
    多塞一个键就会在「报错那一步」上现形（报错也要能看出「本来想填哪个字段」）。

    `origin`：这一步是**怎么来的**（跨任务接口 §1，Task 4 起）。走 `dispatch` 的每一步
    都是**模型自己走出来的**（`"model"`）；Task 5 的重放那一路才是 `"replay"`——
    两者混不起来，`Journey.steps` 与账本（`journal` 的一行就是这一步）因此能回答
    「这一步是它自己做的，还是我们照着账本重放的」。**默认值不给**：它必须被显式写下来
    （默认值会让「忘了写」的那条路悄悄变回 model）。
    """
    # ⚠️ **形状在**这一处**定死**（`_replay_step` 造的是同一套键）：报错那一步与做成了
    # 那一步的键集合必须**一模一样** —— 下游（Console / `states()` / 账本）是按形状读的，
    # 「失败的那步长得与成功的不一样」是这套系统里最贵的一种便宜。
    #
    # 后五个键是**契约七格里的前五格**（`docs/执行事实契约-2026-09-18.md` §二）：脚本
    # **只填这五格**，第六格是运营写的、第七格**永远不是脚本的**（那两格在服务那一侧，
    # 见 `agent/service.py` 的 `_judge_step`）。它们在这里只是**占位**，值由 `dispatch`
    # 在**回执到手的那一刻**填（`step_no` / `receipt` / `sig_before` / `sig_after`），
    # `why` 是「哪一格看不见、为什么」——契约 §二②：「看不见」是一等值，**必须**配一句话。
    step = {"state": pages.current_name, "action": name, "target": None, "result": None,
            "note": "", "origin": "model",
            "step_no": None, "receipt": None, "sig_before": None, "sig_after": None,
            "why": {}}
    fill = None
    selector = str(args.get("selector") or "")
    if name == "goto":
        # goto 的 target 就是**要去**的那个地址（很多站会重定向，落到哪儿是另一件事 ——
        # 那个记在 `result.url` 上，由 `_summarize` 写）。
        # ⚠️ 它**不再**被落地地址盖掉（R-E7 ①）：盖掉之后账上就没有「要打开哪」了，
        # 而 R4 的判据正是拿它比「这一趟见过的地址」。失败时这句话也还成立
        # （`_say` 报的是「打不开 <url>」—— 打不开的是**要去**的那个）。
        step["target"] = {"url": str(args.get("url") or "")}
    if name in ("click", "scroll", "form"):
        frame_id = _frame_of(args)
        element = _find_element(pages, "field" if name == "form" else "action",
                                selector, frame_id)
        step["target"] = _target_of(element, name, selector, frame_id)
        if name == "form":
            fill = _fill_info(args, step["target"], element, journey)
    return step, fill


#: `observe` 用这个名字表示**主帧**（规格 §4.3：单帧 observe 给 `[frameID]`，`""` → `["main"]`）。
#: ⚠️ 它是**给人看的标记，不是能回传的 frameID** —— `cdp --frame-id main` 会报「没有这一帧」。
FRAME_MAIN = "main"


def _frame_of(args: dict) -> str:
    """这一步**在哪一帧里做的**（主帧 = `""`）—— 从模型真给的那个参数读。

    为什么以 `args` 为准、而不是「上一次观测里那个元素在 `frame_path` 的哪一段」：
    `cdp` 的 click / form / scroll **就是拿 `frame_id` 去那一帧里解析选择器的**
    （`tools/cdp/internal/mcp/handlers.go:110/119`）。所以「参数里给了哪一帧」**就是**
    「这一步真在哪一帧发生的」，是**事实**，不是推测；而元素的 `frame_path` 是「它当时
    在哪儿」的旁证 —— 两者不一致时，能重放的是前者（后者可能只是同一个选择器在主帧与
    子帧里各有一个）。

    没给这个参数 = 那一帧没被点名 = cdp 在主帧里解析的（`""`）。**不编 `"main"`**：
    它只是个显示用的名字，传给 `--frame-id` 会被当成一个不存在的帧。

    模型真写了 `"main"`（工具描述里那个词很容易被照抄）时归一成 `""` —— 不归一的话，
    账本里存的是个**永远解析不出来的帧号**：重放时每一条这样的动作都会失败，
    而原因（「模型把显示名当帧号用了」）在产物那一侧完全看不出来。
    """
    given = str((args or {}).get("frame_id") or "").strip()
    return "" if given.lower() == FRAME_MAIN else given


def _frame_id_of_path(path):
    """`observe` 那条元素的 `frame_path` → 能回传给 cdp 的 frame_id。

    三种形状（规格 §4.3）与各自的答案：

    - `["main"]`（或空 `[]`）      → `""`：主帧
    - `["main", "<frameID>"]`      → 那个 frameID
    - 嵌了**两层以上**（`["main", a, b]`）→ `None`：**说不清**，不猜

    第三种为什么是 `None` 而不是「取最里面那个」：cdp 换坐标时只补**目标帧的 owner
    `<iframe>` 在主帧里**那一个原点（`internal/form.go` 的 `calcClickCoords` +
    `ResolveIframeSelector`），中间那几层的偏移没人补 —— 传最里面那帧进去不是「够不着」，
    是**按错的坐标点了一下**（本项目最忌讳的失败形状）。所以这里明说「说不清」，
    由调用方决定退回哪一帧。
    """
    if isinstance(path, str) or not isinstance(path, (list, tuple)):
        return None
    rest = [str(seg or "").strip() for seg in path]
    rest = [seg for seg in rest if seg and seg.lower() != FRAME_MAIN]
    if not rest:
        return ""
    return rest[0] if len(rest) == 1 else None


def _find_element(pages: "_Pages", kind: str, selector: str, frame_id: str = ""):
    """在上一次观测的模型里找这个选择器对应的元素（找不到就 None）。

    ⚠️ 找的是**上一次观测**——「它当时看到的是哪个元素」这件事只有那份模型说得清。
    找不到不是错：target 退回「只有选择器」的形态，重放的声明式回退链照样能跑。

    同一个选择器**在主帧与子帧里可以是两个不同的元素**（跨源 iframe 的部件常常就是把
    同一套结构再渲染一遍）。所以先挑**帧对得上**的那一个；一个都对不上时退回原先的
    「文档序第一个」—— 「帧对不上」不等于「一定是它」，只是没有更好的线索。
    """
    model = pages.current_model
    if not model or not selector:
        return None
    pool = (model.get("fields") if kind == "field" else model.get("actions")) or []
    fallback = None
    for element in pool:
        if not isinstance(element, dict):
            continue
        if element.get("selector") == selector or selector in (element.get("alternates") or []):
            if _frame_id_of_path(element.get("frame_path")) == frame_id:
                return element
            if fallback is None:
                fallback = element
    return fallback


def _selectors_of(element, selector: str) -> list:
    out = []
    if element:
        for candidate in [element.get("selector")] + list(element.get("alternates") or []):
            if candidate and candidate not in out:
                out.append(candidate)
    if selector and selector not in out:
        out.append(selector)
    return out


def _target_of(element, action: str, selector: str, frame_id: str = "") -> dict:
    """声明式多元 target（§5.1b）：**不写死单个选择器**，文字 + 角色 + 语境优先。

    `frame_id` 是**这一步在哪一帧里做的**（主帧 = `""`）—— 见 `_frame_of`。
    它跟 `selectors` 是一体的两半：**同一个选择器在主帧与子帧里可以指两个不同的元素**，
    只搬选择器不搬帧，重放就会去主帧里找一个根本不存在的元素（实测：跨源 iframe 里的
    控件，主帧命中 0、帧内命中 1，每一遍都「页面上没找到」）。所以它**跟着 target 一起存**，
    产物在 click / form 时把它交给 `cdp --frame-id`。
    """
    if action == "form":
        element = element or {}
        label = element.get("label") or element.get("hint") or element.get("placeholder") or ""
        # ── 这一格的**身份证据**，逐条留着（别只留一个 label 就完事）──────────────
        #
        # 为什么（2026-09-17 真站实测，简报里那条「观察者收了、摘 target 时丢掉」）：
        # 原先这里只摘出一个 `label`（三选一的结果），而**三样是三个不同的证据**：
        #   label       页面上印着的名字（`<label>` / aria-label）
        #   hint        元素自报的 name / id（gowizard 上是 `textField-173862`）
        #   placeholder 人眼能直接看见的那行例子（`e.g. California or Texas`）
        #   nearby_text 它周围写着的字（题目正文；2026-09-17 起 observe 还爬 3 层祖先）
        # 三选一之后，**落选的那些就永远回不来了** —— 账本里那条 target 只剩一个
        # 不透明的 id，而且「复跑时按什么找回这一格」也只剩这一条。
        # 实测后果：那一格（「What state do you live in?」）判不出种类 → 兜底填了人名。
        #
        # 现在四个都给，各有各的用处：
        #   - `_fill_name` / `_field_kind`：在**探索那一刻**判这一格是什么（它们拿的是
        #     活的 element，不是这份 target —— 这份是给复跑用的）；
        #   - 产物 `_relocate`：复跑时声明里的选择器全挂了，靠这几条语义把这一格找回来
        #     （见 template.py `_relocate`：**两边用同一组来源**才配得上，不然就是
        #      「账本里的名字是 placeholder 来的、那边只认 label」那类错位）。
        near = list(element.get("nearby_text") or [])
        return {"text": None, "label": label or None, "role": None, "near": None,
                "hint": element.get("hint") or None,
                "placeholder": element.get("placeholder") or None,
                "nearby_text": near or None,
                "selectors": _selectors_of(element, selector), "above_fold_only": False,
                "frame_id": frame_id}
    return {
        "text": (element or {}).get("text") or None,
        "role": (element or {}).get("role") or None,
        "near": (element or {}).get("region") or None,
        "selectors": _selectors_of(element, selector),
        "above_fold_only": False,
        "frame_id": frame_id,
    }


#: 一个「看起来就是美国邮编」的值（5 位数字）。用在下面那种**歧义**场合。
_US_ZIP_RE = re.compile(r"^\d{5}$")


#: 认「这个值是个美国州名」用的小池子 —— **与生产脚本同一套**
#: （`forms/sites/lifynest.py:16` 的 STATES）。它只用来**认**（认不出就照旧保守兜底）。
#:
#: ⚠️⚠️ **2026-09-17：这一条不再当判据用了**（保留常量与这条注释，是为了让下一个人
#: 看见「它存在过、以及为什么撤了」）。撤它的理由：
#:
#:   它把「美国州名表」当成了**识别字段种类的判据** —— 而那正是控制器在 R-75/R-76
#:   点过名的「规则折叠回潮」（判据要一个站一个站地维护，换个国家整个失效）。
#:   当时只能这么办，是因为**没有别的路**：`nearby_text` 在字段这一路一直是空的
#:   （observe 算了、Go 侧 `Field` 没这个字段、被静默丢掉 —— 见 observe.go 的注释），
#:   而 placeholder 那一档根本不存在。
#:
#:   现在两条通用路都通了，实测都能认对（2026-09-17 活页面上量的）：
#:     placeholder 形状：`e.g. 06801`         → `postcode`（`_SHAPE_RULES`）
#:     题目正文：       `What state do you live in?` → `state`（`nearby_text` 第三档）
#:   ⇒ 判据不再依赖任何词表。**硬编码池子留着当兜底值池是对的**（生产一直这么干，
#:     R-76），但**不当判据** —— 这两件事 R-76 分得很清楚，这里按那条办。
_RECOGNIZABLE_STATES = ("California", "Texas", "Arizona", "Florida", "New York", "Illinois",
                        "Ohio", "Georgia", "Virginia", "Washington", "Pennsylvania",
                        "Michigan", "Colorado", "Tennessee", "Missouri", "Maryland")


def _kind_from_recorded_value(kind, value, element):
    """语义判不出来时，**看探索那一趟自己往这个框里写了什么**（证据，不是猜）。

    为什么需要它（2026-09-17 真站实测，量出来的）：
    gowizard 的邮编框身上**一个语义信号都没有** —— `label` 空、`hint` 是不透明的 MUI id
    （`textField-173838`）、`placeholder` 只是个例子（`e.g. 06801`）、`nearby_text` 是空的，
    而 `type="tel"`（为了弹数字键盘）。于是三档全落空、按 type 判成**手机号** →
    复跑时手机号被打进邮编框、页面红字拒收（用户在窗口里看到的就是它）。
    但账本里有一件**现成的证据**：探索那一趟模型自己往这个框里写过 `90210` / `75201` ——
    **邮编形状**。所以：`type=tel`（它本身就有歧义）而记下来的值像美国邮编 → 判 postcode。

    ⚠️ 只在**歧义**时用（`tel` 这一类），而且只认「5 位数字」这一种形状：
    手机号的形状（10~11 位、带括号/横线）不会被它读成邮编。
    """
    if kind != "phone":
        return kind
    if str((element or {}).get("type") or "").strip().lower() != "tel":
        return kind
    if _US_ZIP_RE.match(str(value or "").strip()):
        return "postcode"
    return kind


def _fill_info(args: dict, target: dict, element, journey: Journey) -> dict:
    """这一步填什么（`args` 是模型真给的），以及重放时值从哪来。"""
    if args.get("check") is not None:
        kind, value = "check", ("true" if args.get("check") else "false")
    elif args.get("select") is not None:
        kind, value = "select", str(args.get("select"))
    else:
        kind, value = "value", str(args.get("value") or "")
    label = target.get("label") or ""
    # 名字（= source）与随机值都要按「这一步实际是什么字段」来定 ——
    # 语义判不出来时，用**探索那一趟自己写进去的那个值**当证据（见上面那个函数）。
    semantic = _kind_from_recorded_value(_field_kind(label, element), value, element)
    name = _fill_name(label, element, journey, semantic=semantic)
    return {"name": name, "source": name, "kind": kind,
            "label": label or name, "value": value,
            "fallback": (_fallback(kind, value, label, element) if semantic is None
                         else [{"random": semantic}])}


def _fill_name(label: str, element, journey: Journey, semantic=None) -> str:
    """这个字段叫什么 —— 它同时是 `source`：**产物拿它去运营的 form-file 里找真数据**。

    ⚠️ 所以名字要**对得上运营那份资料的键**（`zip` / `postcode` / `email` / `phone` …），
    否则产物每次都只能退回随机池 —— 用户 2026-09-17 在真窗口上问的正是这件事
    （「是不是资料没对齐」）：那个 ZIP 框的标签是个不透明的 MUI id（`textField-173838`），
    名字就成了 `textfield_173838`，运营的 `zip`/`postcode` 键**一个都对不上**。

    现在：**先看这个字段在页面上说的是什么**（`_field_kind`：自己的名字 → 周围写着的字 →
    html 的 type），认得出就用那个**语义名**（`postcode`/`email`/`phone`/…），
    认不出才退回原来的「标签 / placeholder / type」那条老路（不编名字）。
    """
    kind = semantic if semantic is not None else _field_kind(label, element)
    base = (kind or label or (element or {}).get("placeholder")
            or (element or {}).get("type") or FALLBACK_FILL_NAME)
    stem = _snake(base) or FALLBACK_FILL_NAME
    used = {(s.get("result") or {}).get("fill", {}).get("name")
            for s in journey.steps if (s.get("result") or {}).get("fill")}
    if stem not in used:
        return stem
    index = 2
    while f"{stem}-{index}" in used:
        index += 1
    return f"{stem}-{index}"


#: `random` 池子认的几类值（与产物里 `_random_value` 同一套 —— 产物认不出的类型会抛）。
_RANDOM_HINTS = (
    (("password",), "password"),
    (("email", "e-mail"), "email"),
    (("phone", "tel", "mobile"), "phone"),
    (("zip", "postcode", "postal"), "postcode"),
    # 「州」：**生产脚本本来就是小池子 + 随机选**（`forms/sites/lifynest.py:16` 的 STATES）——
    # 真站实测：某个站的「州」框 label / hint / placeholder / nearby_text **一个语义信号都没有**
    # （placeholder 只是两个地名例子 `e.g. California or Texas`），于是名字落在不透明的 MUI id 上，
    # 运营 form-file 的 `state` 对不上 → 随机兜底给了 `full_name` → **往「州」里填人名**。
    (("state", "province"), "state"),
    (("birth", "dob"), "dob"),
    # 「整名」这一族。原先**没有这一条** —— 于是 `Full Name:` 判不出种类，
    # 靠 `_fallback` 的兜底默认值**碰巧**也给了 `full_name`（真站实测：
    # 那一格的名字对得上运营的键，但那是巧合，不是判据）。
    # ⚠️ 只认「整名」的写法，**不许**收宽成 `name`：`username` / `nickname` /
    # `company_name` 都会被它误伤（那种框填一个人名是错的）。
    (("full name", "fullname", "your name"), "full_name"),
    (("first name", "firstname", "given name"), "first_name"),
    (("last name", "lastname", "surname", "family name"), "last_name"),
)

#: html 的 `type` 认哪几类 —— **只在字段自己没名字、周围也没写字时才轮到它**。
#:
#: ⚠️ 这里**故意不是** `_RANDOM_HINTS` 那张表（2026-09-17 真站实测的教训）：
#: `type` 是**键盘提示**，不是「这个框是什么」。真站那趟里 ZIP 那个框是
#: `<input type="tel">`（很多站为了让手机弹数字键盘就这么写），标签又是个不透明的
#: MUI id（`textField-173838`）—— 于是 `tel` 成了唯一能匹配上的词，判成 **phone**，
#: **复跑时手机号被打进了邮编框**（用户在图上看出来的就是这个）。
#: `password` / `email` 没有这个歧义（没有一个密码框叫 tel），所以它们可以认；
#: `tel` 留在这里当**最后一档**（页面上一个字都没写时，tel 是手机的可能性仍然最大），
#: 但只要字段旁边写着「ZIP code」，第二档就会先命中它。
_TYPE_HINTS = {
    "password": "password",
    "email": "email",
    "tel": "phone",
}


def _kind_by_words(blob: str):
    """这段字里有没有认得出种类的关键词（`_RANDOM_HINTS` 那三张词表）。"""
    text = str(blob or "").lower()
    if not text.strip():
        return None
    for words, random_kind in _RANDOM_HINTS:
        if any(w in text for w in words):
            return random_kind
    return None


#: placeholder 的**例子值**长什么样 —— 人眼一眼看得出，而机器只认形状、不认词。
#:
#: 为什么需要这一档（2026-09-17 真站实测，简报 §3 的第 2 层）：
#: 那一格的 placeholder 是 `e.g. 06801` —— **5 位数字**。它不是「只是个例子」，
#: **它就是证据**：运营/站点写这个例子，就是说「这里填美国邮编」。
#: 原先这一档不存在，于是 `e.g. 06801` 被当成噪音丢掉（关键词表里没有 `06801`）。
#:
#: 为什么是**形状**不是词表：形状跨站点成立（`e.g. 06801` 在任何美国邮编站都一样），
#: 而词表要一个站一个站地维护 —— 用户 2026-09-16 明确要求过别走回「规则折叠」那条路。
#: 这一档只认**几种世界通用的形状**（邮编 / 邮箱 / 电话），认不出就交回后面的档，
#: **绝不猜**（`_fallback` 认不出时那条「不填」的规矩原样保留）。
_SHAPE_RULES = (
    # 美国邮编：`e.g. 06801`。`\b` 夹住，免得把 `e.g. 1234567890`（10 位电话）读成邮编。
    (re.compile(r"(?<![\d-])\d{5}(?![\d-])"), "postcode"),
    # 英国邮编：`e.g. RG24 8PE`（生产 JSON 那条线真正在用的形状 —— 见
    # `form_executor/auto_fixer.py:86 _fix_field_placeholder` 的原文例子）。
    (re.compile(r"\b[A-Z]{1,2}\d{1,2}[A-Z]?\s*\d[A-Z]{2}\b", re.I), "postcode"),
    # 邮箱：`e.g. example@email.com`（有 `@` 且 `@` 后面有点号就算）
    (re.compile(r"[^\s@]+@[^\s@.]+\.\S"), "email"),
    # 电话：`e.g. (512) 494-9400` / `555-123-4567` / `+1 512 555 0142`
    (re.compile(r"\(\d{3}\)\s*\d{3}[-.\s]?\d{4}"), "phone"),
    (re.compile(r"(?<!\d)\d{3}[-.\s]\d{3}[-.\s]\d{4}(?!\d)"), "phone"),
)


def _kind_by_placeholder(placeholder):
    """placeholder 的**例子值**能不能说明这一格是什么（只认形状，认不出给 `None`）。"""
    text = str(placeholder or "").strip()
    if not text:
        return None
    for pattern, kind in _SHAPE_RULES:
        if pattern.search(text):
            return kind
    return None


def _field_kind(label: str, element):
    """这个字段**是什么**（`postcode` / `email` / `phone` / `full_name` …）—— 认不出给 `None`。

    四档，顺序就是判据的一部分（别合回去，理由见 `_fallback` 的 docstring）：
    ① 字段自己的名字（`label` / `hint` / `placeholder`）→ 关键词
    ② **placeholder 的例子值**的形状（`e.g. 06801` 是 5 位数字 → 邮编）—— 2026-09-17 新加
    ③ 页面上它周围写着的字（`nearby_text`）→ 关键词
    ④ html 的 `type`（只认不歧义的）

    ⚠️ ② 为什么夹在 ① 和 ③ 中间：① 是「这一格自己怎么说」，② 是「这一格自己举的例子」，
    ③ 是「它旁边写着什么」—— 离这一格越近的证据越可信。实测那两格：
      州：placeholder `e.g. California or Texas`（②认不出形状）→ ③ 拿到题目正文
          `What state do you live in?` → `state` ✓
      邮编：placeholder `e.g. 06801` → ② 当场判 `postcode` ✓（不必等 ③）

    ⚠️ 这个「是什么」有两个用处，**必须是同一个答案**：
      - `_fallback`：form-file 里没有这个键时，填什么随机值；
      - `_fill_name`：这个字段叫什么（= `source`，产物拿它去 form-file 里找运营的真数据）。
    两处各判一次必然漂（一处改了另一处没改），所以只有这一个函数说这件事。
    """
    element = element or {}
    own = " ".join(str(x or "") for x in (label, element.get("label"), element.get("hint"),
                                          element.get("placeholder")))
    kind = _kind_by_words(own)
    if kind:
        return kind
    kind = _kind_by_placeholder(element.get("placeholder"))
    if kind:
        return kind
    kind = _kind_by_words(" ".join(str(x or "") for x in (element.get("nearby_text") or [])))
    if kind:
        return kind
    return _TYPE_HINTS.get(str(element.get("type") or "").strip().lower())


def _fallback(kind: str, value: str, label: str, element) -> list:
    """重放时这个字段填什么：先读 form-file 的键（`source`），没有就用这里。

    ⚠️ 标签猜不出语义时给 `full_name` —— 一个保守的默认值，**不编**具体内容
    （真值要么来自运营的 form-file，要么来自随机池；产物那边的随机化本身是拟人需要）。

    ## 判语义的三档（**顺序是有理由的，别合回去**）

    1. **这个字段自己的名字**：`label` / `hint` / `placeholder`；
    2. **页面上它周围写着的字**：`nearby_text`（observe 一直有，这里原先没用）；
    3. **html 的 `type`**：只在上面两档一个字都没命中时 —— 见 `_TYPE_HINTS` 的注释
       （`type="tel"` 是键盘提示，邮编框也用它）。

    为什么要有第 2 档、为什么 `type` 必须降到第 3 档（**2026-09-17 真站实测**）：
    gowizard 的 ZIP 框是 `<input type="tel">`，标签是个不透明的 MUI id
    （`textField-173838`）—— 原先那一版把 `type` 和标签**混在同一个 blob 里**，
    于是 `tel` 成了唯一匹配得上的词，判成 **phone**：账本里那一步 `value='33101'`
    （邮编）配着 `fallback=[{"random":"phone"}]`，**复跑时手机号被打进邮编框**，
    用户在窗口里一眼看出来「zipcode 填成了手机号导致过不去」。
    加了第 2 档之后，同一个框的 blob 里有那句「What's your ZIP code?」→ 判成 **postcode** ✓。
    """
    if kind == "check":
        return [value or "true"]
    if kind == "select":
        return [value] if value else []
    guessed = _field_kind(label, element)
    if guessed:
        return [{"random": guessed}]
    return [{"random": "full_name"}]


def _diag_row(d: dict) -> dict:
    """一条诊断在账本里长什么样：**哪一帧、因为什么**。

    ⚠️ 别退回「只留 `kind`」（2026-09-18 homebuddy 真站实测的后果）：`cmd/observe.go:215`
    写着「诊断的详情就是**这一行的全部价值**」，而这里原先只抄了 `kind` ——
    那趟探路的账本里只剩 `["frame-blind", "frame-error", "frame-error"]` 一串光秃秃的
    kind，**没有理由、没有帧**。`frame-blind` 有**两个分支**（iframe 计数求值失败 /
    数量对不上），光看 kind 分不出是哪一种；`frame-error` 的真实报错也只在 `detail` 里
    ⇒ 事后**查不出它为什么瞎**，而那正是当时最要紧的问题。

    形状与工具返回**同形**（`{kind, detail, frame_path}`，见 `internal.Diagnostic`）：
    读账的人手里那份 observe JSON 怎么读，这一行就怎么读，不用再学一套键名。
    ⚠️ 但**是白名单**、不是整条抄：`Journey.steps` 会进 checkpoint
    （`graph.MSGCPACK_ALLOWLIST` 点名允许 `Journey`），而工具返回里可能有几百 KB 的
    base64 截图 / 整段 DOM（`_summarize` 的存在理由就是这件事）—— 今天多抄一个键，
    明天谁给诊断挂上 `screenshot`，账本就跟着涨。
    ⚠️ 空的那两格**不写**（`detail` 为空时不写 `""`）：账本是给人读的，
    写一个空字符串只会让人以为「详情就是空的」。
    """
    row = {"kind": d.get("kind")}
    path = d.get("frame_path")
    if isinstance(path, (list, tuple)):
        path = [str(x) for x in path]
    elif path:
        # 契约上它是数组（Go 侧 `[]string`，主帧那条给 `["main"]`）——这一步是**防御**：
        # 标量进来也编成数组，别让账本里出现两种形状。
        path = [str(path)]
    if path:
        row["frame_path"] = path
    if d.get("detail"):
        row["detail"] = str(d["detail"])
    return row


def _summarize(name: str, args: dict, raw: Any, elapsed_ms: int, fill: dict | None) -> dict:
    """这一步的**结果**（进 Journey 的那份）。

    这是一份**摘要**，不是原始返回 —— 原始返回里可能有几百 KB 的 base64（截图），
    整份抄进账本只会让内存和落盘都变得没法读。模型那边拿到的是**原样的**结果。
    """
    out: dict = {"ok": True, "elapsed_ms": elapsed_ms}
    if name == "observe" and isinstance(raw, dict):
        out["url"] = raw.get("url") or ""
        out["title"] = raw.get("title") or ""
        out["page_text_head"] = _norm(raw.get("page_text") or "")[:PAGE_HEAD_CHARS]
        out["actions"] = len(raw.get("actions") or [])
        out["fields"] = len(raw.get("fields") or [])
        out["option_groups"] = len(raw.get("option_groups") or [])
        out["honeypots"] = len(raw.get("honeypots") or [])
        # 诊断：**哪一帧、因为什么**（`_diag_row` 的 docstring 写了「只留 kind」的后果）
        out["diagnostics"] = [_diag_row(d) for d in (raw.get("diagnostics") or [])
                              if isinstance(d, dict)]
    elif name == "goto":
        out["url"] = (raw or {}).get("url") if isinstance(raw, dict) else None
    elif name == "diff":
        out["actionable"] = (raw or {}).get("actionable") if isinstance(raw, dict) else None
    elif name == "screenshot":
        out["bytes"] = len(str(raw)) if raw is not None else 0
    elif isinstance(raw, dict):
        out.update({k: v for k, v in raw.items() if k != "note"})
    if name in ("click", "form", "scroll") and args.get("selector"):
        out.setdefault("selector", args.get("selector"))
    if fill:
        out["fill"] = fill
    # ── R3 的**前提**：这一键只有 `observe` 写（破了就抛，见 `_only_looks_carry_the_text`）──
    # 判的是**键在不在**，不是值真不真：一串空正文照样说明「有别的工具也在写它」，
    # 而 R3 的整套推导（「窗口里任何一眼看见 ⇔ 最后一眼看见」）正是从「只有 observe 写」来的。
    # 放在这里而不是只放在读的那一侧：**前提是在这儿产生的**，破了要当场知道是哪个工具。
    if name != "observe" and "page_text_head" in out:
        raise _TextPremiseBroken(
            "工具「%s」的结果里冒出了 `page_text_head` —— R3 的**前提破了**"
            "（前提：这一键只有 `observe` 写；本函数是唯一的写点）。"
            "R3 判「过没过成功线」时只认 `observe` 行当落点，别的行带正文会让"
            "「窗口里看见过」漏判 ⇒ **提交会留在前缀里**，重放它 = **往真实站点"
            "再交一次真实表单**。要么把这个键从这个工具的结果里去掉，要么先改 R3 的落点"
            "（`_landing_index`）让它也认这一类行 —— 别让它就这么过去。" % name)
    return out


def _say(action: str, target, ok: bool) -> str:
    """这一步在**人话**里叫什么（D16：使用者是非技术人员 —— 不是错误码、不是选择器）。"""
    label = _label_of(target)
    if action == "observe":
        return "看了一眼页面"
    if action == "diff":
        return "比了一下：这一步之后页面有没有变"
    if action == "screenshot":
        return "截了一张图"
    if action == "click":
        return f"点了「{label}」" if ok else f"页面上没找到「{label}」，这一步没做成"
    if action == "form":
        return f"填好了「{label}」" if ok else f"没找到「{label}」这个输入框，没填成"
    if action == "scroll":
        return f"把「{label}」滚进了视口" if ok else f"滚不动「{label}」"
    if action == "goto":
        url = (target or {}).get("url") or ""
        return f"打开了 {url}" if ok else f"打不开 {url}"
    return f"做了一下「{action}」" if ok else f"「{action}」没做成"


def _label_of(target) -> str:
    target = target or {}
    for key in ("text", "label", "name"):
        if target.get(key):
            return str(target[key])
    # ⚠️ 没有文字名字时**不许**把选择器端给人看（2026-09-18 复审 F3）：这句话进的是
    # `_say` → `step["note"]` → 时间线的 `events[].say` **和**轮次卡片 —— 那一屏是给
    # 运营看的（D16：主视图里没有选择器）。选择器**本来就在** `target["selectors"]` 里
    # （账本上没丢），排查时对得回页面；端进句子里是**多此一举地把实现细节摆上主视图**。
    # 与 `template.py` 渲染进产物那份同一个说法（那边写着「不把选择器端给人看」）。
    return "没写名字的元素"


def _last_fill_wins(steps: list) -> list:
    """同一个状态里**同一个字段被填过多次** ⇒ 只留**最后一次**（顺序不动，别的步一步不删）。

    ★ 2026-09-22 真事（用户原话：「第一次生日填过了，为啥还会消掉填第二次。这个比较关键」）：
    探索期模型为了试出掩码/校验的脾气，会把同一格**反复填**（真账本里 `#InputDOB` 出现了
    4 次 `form` + 1 次 `click` + 1 次 `scroll`）。这些步在账上**每一步都是「做成了」**
    （回执说动作下发成功、页面也变了 —— 掩码确实动了），所以 `_replay_step` 那道 `ok` 筛
    **筛不掉它们** ✗。而重放时前面那几次**毫无意义**，还会把后填的那个值**覆盖掉** ——
    用户看到的「填了又被消掉」正是这个。

    ⇒ 留最后一次：探索者最后停下的那个形态，才是它**试出来**的那个（值由 `fills()`
    那一跳给 —— 它本来就取最后一条）。
    ⚠️ 只按**同一个状态内**去重：跨状态填同一格是**另一件事**（那是流程里第二次问它）。
    """
    last: dict = {}
    for i, s in enumerate(steps or []):
        if s.get("action") == "form" and s.get("fill"):
            last[s["fill"]] = i
    return [s for i, s in enumerate(steps or [])
            if not (s.get("action") == "form" and last.get(s.get("fill")) != i)]


def _replay_step(step: dict):
    """一个 Journey 步骤 → 产物里的一步（不可重放的返回 None）。"""
    action = step.get("action")
    if action not in REPLAY_ACTIONS:
        return None
    if not (step.get("result") or {}).get("ok"):
        return None                      # 没做成的动作不该在重放里再来一遍
    target = step.get("target") or {}
    note = step.get("note") or ""
    if action == "click":
        return {"action": "click", "note": note, "target": target}
    if action == "form":
        fill = (step.get("result") or {}).get("fill") or {}
        if not fill.get("name"):
            return None                  # 连字段名都不知道，重放时填不了
        return {"action": "form", "fill": fill["name"], "note": note, "target": target}
    if action == "scroll":
        # 这一步重放的是**它的元素**（「把「X」滚进视口」），与探索时那次是同一件事 ——
        # cdp 的 `scroll [selector]` 正是这个动作。**不再编一个 `pixels`**：那是把
        # 「滚到哪个元素」硬翻成「滚多少像素」，而产物照着它发出去的是一条永远跑不通的
        # 命令（`cdp scroll 400` → element not found，真窗口实测）。
        return {"action": "scroll", "note": note, "target": target}
    if action == "goto":
        url = (step.get("target") or {}).get("url") or (step.get("result") or {}).get("url") or ""
        if not url:
            return None
        return {"action": "goto", "url": url, "note": note}
    return {"action": action, "note": note}


# ═══════════════ 重放：照账本走回去（Task 5；设计注 §1.4 / §1.6）═══════════════
#
# 窗口死了之后，新窗口是一个**干净身份**的浏览器（§1.5：cookie / localStorage / 指纹
# 全换了）—— 所以重放**不能**理解为「在那一页接着做」，它只能是「**从入口重走一遍**」。
#
# 这条路上最要紧的一件事是：**重放出来的动作，没有任何人要求过**。第一遍是人让它做的
# （描述里写着），第二遍是系统自己做的 —— 一件真实世界里有效果的动作，第二遍就是
# 一次没人要的重复。所以这里不是一个「怎么走回去」的问题，是**一条边界线**的问题：
# 走到哪儿为止是安全的（`replayable_prefix`），以及走偏了怎么办（`replay`）。

#: 重放整段最多来几遍（§1.6：窗口又死了 → 整段重来；到顶就停并说明）。
REPLAY_ATTEMPTS = 3

#: 账上「这一步**被看见了**」认哪两个动作（R2）。设计注 §1.4.3 那行写的是 `observe`／`diff`。
#: ⚠️ §1.4.2 那段散文里还列了 `screenshot`，本实现**不认它**：账上那张图只留下一个字节数
#: （`_summarize`），**没有地址也没有正文** —— 它证明不了「页面被带到哪儿了」，
#: 而 R2 的全部力量就在这句话上。见 `replayable_prefix` 的 docstring。
_SEEN_ACTIONS = ("observe", "diff")


class _TextPremiseBroken(RuntimeError):
    """**R3 的前提破了**：账本里有一行**不是 `observe`** 却带着 `page_text_head`。

    为什么这件事值得一个自己的异常类型（而不是一句 `RuntimeError`）：它是**前提**、
    不是「这一趟数据脏了」—— 破了之后 R3 的判据**不再成立**，而它唯一的职责是
    「别让提交留在前缀里」。见 `_only_looks_carry_the_text`。
    """


def _only_looks_carry_the_text(rows: list) -> None:
    """**R3 的前提**：正文（`page_text_head`）只有「看一眼」（`observe`）那些行才写 —— 破了就**抛**。

    为什么这是前提、为什么破了必须响（复审：**这条前提一破，性质本身就破**）：

    R3 的判据是「**最早**看见成功文案的那一行 ≤ 这一步的**落点**」，而落点**只认 `observe` 行**
    （`_landing_index` 返回的是 observe 的下标）。文案是**逐行累加**成一个 blob 的
    （下面那段循环对每一行都读一次 `page_text_head`）。「窗口里**任何一眼**看见了」
    ⇔「窗口里**最后一眼**看见了」这条等价 —— 以及 `_crossed_line_why` 那两种形状的推导 ——
    **全都要求「带正文的行 == 落点认的那些行」**。

    哪天有别的工具也写这一键（`_summarize` 的兜底那一支 `out.update(raw)` 就够，它不设防），
    等价就不成立：文案可以落在两个 `observe` 之间的某一行上，而落点**看不见它** ⇒
    **提交留在前缀里** ⇒ 重放它 = **往真实站点再交一次真实表单**。那是 R3 唯一要防的事。

    所以这里**不猜也不静默**：宁可当场停下说话，也不放一个可能重复提交的前缀出去。

    今天成立（三层核过，复审 2026-09-17）：`_summarize` 只在 `observe` 那一支写这一键；
    MCP 工具表是固定的 7 个（`tools/cdp/internal/mcp/registry_test.go`），其余工具的结果里
    没有那一段（`diff` 只回 `actionable`，`PageText` 只长在 observe 的 `PageModel` 上）；
    盘上真账本 342 行里，非 `observe` 行带这一键的 **0 行**。
    """
    for i, row in enumerate(rows or []):
        res = (row or {}).get("result")
        if not isinstance(res, dict) or not res.get("page_text_head"):
            continue
        action = str((row or {}).get("action") or "")
        if action != "observe":
            raise _TextPremiseBroken(
                "第 %d 行是「%s」，可它的结果里带着 `page_text_head` —— R3 的**前提破了**"
                "（前提：正文只有 `observe` 那些行才写）。R3 判「过没过成功线」时**只认 `observe` "
                "当落点**，别的行带正文会让「窗口里看见过」漏判 ⇒ **这一步（常常就是提交）"
                "会留在前缀里**，重放它 = **往真实站点再交一次真实表单**。"
                "这里不猜：先把前提收回来（该写这一键的只有 `_summarize` 的 `observe` 那一支），"
                "再决定 R3 的落点要不要跟着改大。"
                % (i + 1, action or "（这一行没写动作名）"))


def replayable_prefix(steps: list, success_text: str, *,
                      entry_url: str = "", pages: list | None = None) -> tuple:
    """账本里**能照着重放**的那一段，以及「为什么停在这」（人话）。设计注 §1.4.3。

    一步可重放 ⇔ 下面四条全真（**从头往后吃，遇到第一个不满足的就停在那儿**）：

    | 条 | 判据 | 为什么 |
    |---|---|---|
    | **R1 做成了** | `result.ok is True` | 没做成的动作重放它干嘛 |
    | **R2 被看见了** | 它**之后**还有一条**做成**的 `observe`／`diff` | 尾部那段没被观测的动作里藏着**提交** —— 「没被看见的动作一律不重放」是这条规则的全部力量 |
    | **R3 没跨过成功线** | 到这一步为止（**含它所到的那一页**）观测到的文字里没有 `success_text` | 成功文案出现 = 这一趟已经成了；过了那条线之后每一个动作都可能是**重复的真实请求**。用的是**人给的判据**，不是我们猜的 |
    | **R4 goto 只回自己去过的地方** | `goto` 要去的那串地址 ∈ 走过的页面地址 或 入口地址 | 深链可能是**一次性**的（确认链接、令牌链接） |

    返回的**是账本里那一段**（动作行 + 它那几条「被看见了」的感知行——**同一批 dict，
    不改一个字**）。为什么连感知行一起带：`replay` 要知道「这一步本该落在哪一页」
    才能逐页核验（§1.6），而那份信息只存在于那些 observe 行里
    （`journal` 一个文件只落 step，没有别的地方能还原页面）。`replay` 只对
    `_ACTIONS` 那几行动手，感知行是**证据**，不是要重演的动作。

    几处**读法**（都是判断，摆在这里，不当默认）：

    - **R2 认不认 `screenshot`**：不认。见 `_SEEN_ACTIONS`。
    - **R3 读哪份文字**：账上**按顺序**记下的 `observe` 的正文（`page_text_head`，
      `PAGE_HEAD_CHARS` 那么长 —— ⚠️ 别在这儿写死一个数：它改过一次，写死就成假的了）。
      两个刻意的选择：① 用**有序**的步骤，不用 `pages[]`（那份没有位置 —— 拿它判
      「这条线是什么时候过的」只能得到「一过就整段作废」，而设计只要「过线之后的动作不许重放」）；
      ② 只看得见**头 200 字**（`_summarize` 的截断）—— 比这更长的成功文案 R3 看不见。
    - **R4 看不看落地 URL**：不看。设计注那行括号里写着「（或其落地 URL）」，本实现
      **不采纳**：一条 goto 的落地 URL **按定义**就写在它后面那条 observe 上（`pages` 就是
      这么攒出来的）⇒ 拿它当判据的话，**只要这条 goto 后面有过观察，R4 就恒真** ——
      一条恒真的判据等于没有判据（本项目一整天在治的正是这个形状）。
      所以只看**要去的那串地址**；`test_the_landing_url_does_not_get_a_one_time_link_through`
      就是这条读法的哨兵。
    - **`entry_url` 给了、账本头上又不是 `goto`**：最前面**合成**一条 `goto`（§1.5：
      重放只能是「从入口重走一遍」）。不给入口地址**不合成** —— 不猜一个 URL 出来。
    - **`wait` 之类账上不该有的动作**：MCP 那道门上没有 `wait` 这个工具（模型从没调过它），
      所以账上不会有真的；真有的话 R1–R4 一条都不判它、`replay` 也不会去动它
      （它不在 `_ACTIONS` 里）—— 这里**不为不存在的形态发明规则**，只记下这个口子在哪。

    `success_text` 为空**抛**：它不是「没有约束」，是**少给了一个输入**
    （R3 唯一的输入就是它；`intake` 本来就该拦住这种载荷）。
    """
    want = _norm(str(success_text or ""))
    if not want:
        raise ValueError(
            "重放前缀要一个成功判据（`success_text`）—— 空的不算「没有约束」，是**少给了一个输入**"
            "（R3 唯一的输入就是它，而 R3 管的是「过了成功线之后不许再动真页面」）")

    rows = list(steps or [])
    #: R3 的**前提**先验（破了就抛，不静默）—— 下面每一行的推导都压在它上面。
    _only_looks_carry_the_text(rows)
    #: 走到每一行时「观测到的页面文字」累计到哪儿了（**按顺序**累，R3 要的就是这个顺序）。
    seen_text: list = []
    blob = ""
    for row in rows:
        blob = (blob + " " + _norm((row.get("result") or {}).get("page_text_head") or "")).strip()
        seen_text.append(blob)
    #: 成功文案**最早**在第几行那次观察里出现（None = 这一趟压根没出现过）。
    first_hit = next((j for j, text in enumerate(seen_text) if want in text), None)

    seen_urls = set()
    for page in (pages or []):
        if isinstance(page, dict) and _url_key(page.get("url")):
            seen_urls.add(_url_key(page.get("url")))
    for row in rows:
        if str(row.get("action") or "") == "observe" and _url_key((row.get("result") or {}).get("url")):
            seen_urls.add(_url_key((row.get("result") or {}).get("url")))
    if _url_key(entry_url):
        seen_urls.add(_url_key(entry_url))

    prefix: list = []
    for i, row in enumerate(rows):
        action = str((row or {}).get("action") or "")
        # ── R3：它**所到的那一页**上有没有那条成功文案 ─────────────────────────
        landing = _landing_index(rows, i)
        if first_hit is not None and first_hit <= landing:
            return prefix, _crossed_line_why(i, row, success_text, first_hit)
        # ── R1 / R2 / R4：只对**会改页面**的那几个动作判 ──────────────────────
        if action in _ACTIONS:
            if not _did_work(row):
                return prefix, (
                    "第 %d 步「%s」当年就**没做成**（%s）—— 没做成的动作不重放（R1），"
                    "前缀停在它前面。" % (i + 1, _step_label(row), _why_not_ok(row)))
            if not _was_seen(rows, i):
                return prefix, (
                    "第 %d 步「%s」之后**没有任何一次做成的观察**（observe／diff）—— "
                    "没被看见的动作一律不重放（R2）：**提交几乎总是最后一个动作**，"
                    "它就藏在这一段里。前缀停在它前面。"
                    % (i + 1, _step_label(row)))
            if action == "goto":
                target_url = _goto_url(row)
                if not target_url:
                    return prefix, (
                        "第 %d 步是「打开某个地址」，可账上**没记下要去的是哪** —— "
                        "拿不到那串地址就不重放它（R4：不拿一个别处的地址去导航，也不猜），"
                        "前缀停在它前面。" % (i + 1))
                if _url_key(target_url) not in seen_urls:
                    return prefix, (
                        "第 %d 步要打开的地址（%s）这一趟**没见过** —— 深链可能是一次性的"
                        "（确认链接、令牌链接），只重放亲眼见过是普通页面的地址（R4），"
                        "前缀停在它前面。" % (i + 1, target_url))
        prefix.append(row)

    why = "这一段没有碰到边界：账上这几行都满足重放的判据（R1–R4）—— 可以照着重走。"
    if _url_key(entry_url) and prefix and str(prefix[0].get("action") or "") != "goto":
        prefix.insert(0, _synthetic_entry_goto(entry_url))
        why += (" 另外：账本头上没有「打开入口」那一步 —— 新窗口是干净身份的浏览器（§1.5），"
                "所以最前面**合成**了一条「打开入口」，重放从入口重走一遍。")
    return prefix, why


def replay_actions(steps: list) -> list:
    """这一段里**会被重放**的那几行（`replay` 只对 `_ACTIONS` 那几行动手）。

    ⚠️ 判据就是 `replay` 自己用的那个 `_ACTIONS` —— 别在别处再抄一份
    （抄的那份会在某一侧改判据的那天开始骗人）。给读账的人（闸口那份摘要）用。
    """
    return [row for row in (steps or []) if str((row or {}).get("action") or "") in _ACTIONS]


def _synthetic_entry_goto(entry_url: str) -> dict:
    """合成的那条「打开入口」（§1.5）。形状与真账本里的 `goto` 行**一模一样** ——
    它在 `replay` 眼里不该是个特例。`result` 留空：**它还没发生**，记成做成了就是编。
    """
    return {"state": START_STATE, "action": "goto", "target": {"url": str(entry_url)},
            "result": None, "note": f"打开了 {entry_url}", "origin": "replay"}


def _did_work(row) -> bool:
    """R1：`result.ok is True`（**严格**——不是真值判断。设计注 §1.4.3 写的就是这一条）。"""
    return ((row or {}).get("result") or {}).get("ok") is True


def _why_not_ok(row) -> str:
    err = ((row or {}).get("result") or {}).get("error") or "工具报了错"
    return str(err)[:120]


def _was_seen(rows: list, i: int) -> bool:
    """R2：第 i 行**之后**还有没有一条**做成**的观察（`observe`／`diff`）。

    ⚠️ 两条都要：① 在那之后（在它之前看过的不算 —— 那看的是**别的页面**）；
    ② 那条观察自己也做成了（一次失败的 observe 说明**没看见**，不能顶数）。
    """
    return any(str((row or {}).get("action") or "") in _SEEN_ACTIONS and _did_work(row)
               for row in rows[i + 1:])


def _landing_index(rows: list, i: int) -> int:
    """第 i 行「**所到的那一页**」看到**哪儿为止**（R3 判据的右端）= **能归到它头上的最后一眼**。

    - **`observe` 行** → 它自己（那一页的正文就是它记下的）；
    - **其余行**（动作行、`diff`／`screenshot`）→ 它之后、**下一次动作之前**的**最后一眼**
      `observe`；但**不比旧口径更少**（旧口径 = 往后第一条 `observe`，可能跨过后面的动作）。

    ⚠️ **这里有一条前提**：右端**只认 `observe`**，而 R3 比的是「最早看见成功文案的那一行」——
    两者能对上，靠的是「**带正文的行 == 落点认的那些行**」（正文只有 `observe` 写）。
    这条前提**破了，R-E9 的性质本身就破**（提交会留在前缀里）⇒ 它由一个哨兵把着门：
    `_only_looks_carry_the_text`（`replayable_prefix` 一进来就验，破了**抛**
    `_TextPremiseBroken`）。改这里之前先读它。

    为什么要吃满「下一次动作之前」这一整段（**R-E9**）：只看到**第一眼**会漏掉
    「点了到新页、第二眼才渲染出来」那一形 —— 那时**那一步（常常就是提交）会留在前缀里**，
    而重放它 = **往真实站点再交一次真实表单**（用户原话：「刷太多不太好」）。
    裁定用的性质是：**只要某次动作之后的任何一眼（在下一次动作之前）看见了成功文案，
    那一步就不许进前缀。**

    为什么还要保留旧口径那一侧：这次改动只许**收紧** ——
    「这一步之后一眼都没有」（下一个就是动作）时，不能反而把原来拦下的行放行。
    取 `max` 把两条一起满足：**归得出**就用归得出的最后一眼，**归不出**就退回第一条 `observe`。
    """
    if str(rows[i].get("action") or "") == "observe":
        return i
    last = i
    for j in range(i + 1, len(rows)):
        action = str((rows[j] or {}).get("action") or "")
        if action in _ACTIONS:
            break                       # 下一次动作 = 这几眼到此为止
        if action == "observe":
            last = j
    first = next((j for j in range(i + 1, len(rows))
                  if str((rows[j] or {}).get("action") or "") == "observe"), i)
    return max(last, first)


def _crossed_line_why(i: int, row: dict, success_text: str, hit: int) -> str:
    """R3 那句人话。**两种形状分开说**（处置相同，但读账的人要能看出是哪一种）。

    ⚠️ **只有两种真会出现**：记下那句话的那一行**自己**也会被拦下（它是 observe ⇒ 落点就是它自己），
    所以「拦下某一行」的那次判断里 `hit` 只可能**等于或大于**它 —— `hit < i` 到不了。
    （前提说清楚：那句话只出现在 **observe 行的正文**里。账上真会这样 —— `page_text_head`
    只有 `_summarize("observe", …)` 写；别的工具的结果里没有那一段。哪天有工具也写了，
    这个分支就重新可达 —— 但**不会静默地走到这儿**：`_only_looks_carry_the_text`
    在 `replayable_prefix` 一进来就把这种账**抛**出来（`_TextPremiseBroken`），
    所以真要改的是**那条前提**，不是这句话。）

    - `hit == i`：拦下的**就是记下那句话的那次观察** —— 那句话是在**没有动作的那一眼**上
      才第一次看到的（上一条账也是观察）。**这一形有两种可能，而系统分不出**：
      ① 页面上本来就有的一句普通话**撞**了；② 更早某一步把页面带到这儿，
      那句话**这一刻才渲染出来**（点了到新页、第二眼才出来）。**两种都停**；
    - `hit > i`：拦下的是一步**动作**，而它**落到的那一页**上带着那句话 —— 提交就是这么过线的。

    （这两种形状在**返回的前缀**上总是分得开的：前者停下时最后一行是 observe，
    后者是一步动作。复审裁定②要的正是夹具能把这俩摆出来。）
    """
    if hit <= i:
        return ("第 %d 步「%s」不能重放：成功文案「%s」是在**没有动作的那一眼**上出现的"
                "（第 %d 行那次观察；它前面那条账也是一次观察）—— 可能是页面上本来就有的一句"
                "普通话**撞**了，也可能是更早某一步把页面带到这儿、那句话这一刻才渲染出来；"
                "**这两种系统分不出**，所以保守到底：这一步之前的前缀照重放，"
                "这里之后一步都不走（R3）。"
                % (i + 1, _step_label(row), success_text, hit + 1))
    return ("第 %d 步「%s」不能重放：它**落到的那一页**（第 %d 行那次观察）上已经出现了"
            "成功文案「%s」—— 过了那条线之后的每一个动作都可能是**重复的真实请求**（R3），"
            "前缀停在它前面。"
            % (i + 1, _step_label(row), hit + 1, success_text))


def _step_label(row: dict) -> str:
    """一行在**人话**里叫什么（D16：读账的人不读选择器）。"""
    action = str((row or {}).get("action") or "")
    if action == "goto":
        return "打开 %s" % (_goto_url(row) or "某个地址")
    if action in ("click", "form", "scroll"):
        return "%s「%s」" % ({"click": "点", "form": "填", "scroll": "滚到"}[action],
                            _label_of((row or {}).get("target")))
    return action or "这一步"


def _goto_url(row: dict) -> str:
    """这一行 `goto` **要去**的那个地址（不是它落在了哪儿）。

    ⚠️ **不许回退到 `result.url`**（R-E7 ②）：那是个**侧门** —— `target.url` 一空就静默返回
    落地地址，同一个谎换个地方又说一遍（判据看起来在读「要去的地址」，实际读的是别的东西）。
    取不到就返回 `""`，让调用方去处理「账上没记下地址」那一支（R4 与 `_fire` 都接得住）。
    """
    target = (row or {}).get("target") or {}
    return str(target.get("url") or "").strip()


def _url_key(url) -> str:
    """判「是不是同一个地址」时拿哪一串比：**去掉 fragment，query 一个字都不动**。

    fragment 不进服务器（`#step-2` 换不换都不产生一次真实请求）；query **会** ——
    而一次性令牌正好就在 query 里（`?token=…`），那正是 R4 要挡的东西。
    所以只削 fragment：削 query 等于把 R4 的牙拔掉。
    """
    return str(url or "").strip().split("#")[0]


# ─────────────────────────── 重放（0 模型）───────────────────────────


class _WindowGone(Exception):
    """重放途中窗口又死了（§1.6 第三行）—— 整段重来，不当事故事故记在某一歩头上。"""

    def __init__(self, done: int, landed: str, cause: str = ""):
        super().__init__(cause)
        self.done, self.landed, self.cause = done, landed, cause


class _ToolFailed(Exception):
    """**工具**说这一步没成（选择器找不到那类）—— 与「窗口死了」是两件事，分开处置。"""


def replay(session, steps, *, on_step=None, alive=None, fresh=None) -> dict:
    """照着 `steps`（`replayable_prefix` 给的那一段）走回去。**一次模型都不问。**

    返回 `{done, landed, why, attempts}`：走成了几步（**动作**步）／最后落在哪个地址／
    为什么停（人话，**整段走完时也是完整的一句**——空字符串那种「沉默」在这本账里读不出意思）／
    这一趟**试了几遍**。

    ⚠️ `attempts` 是给「这一趟算不算一次**干净**的观察」用的（复审 ②）：**走完了**不等于
    没抖过 —— 第 3 遍**可以走通**，于是「用满 3 次尝试」与「被打断」是**两件事**。
    只判「没走完」的话，一个「每趟都抖两下、第三遍刚好走通」的探路会被当成三趟干净观察，
    重探的乘数就上去了（3 趟 × 3 遍）。

    `alive`：问一句「窗口还活着吗」。工具调用失败时用它分辨两件事：
    **窗口死了**（→ 整段重来，最多 `REPLAY_ATTEMPTS` 遍）与**这一步没做成**（→ 停下来叫人）。
    ⚠️ 它自己坏掉时按**死了**处理 —— 反过来（坏掉 = 活着）会把「窗口没了」记成
    「页面上找不到那个按钮」，那是一句**指错方向**的话。

    `fresh`：`fresh(旧会话) -> 新会话`（换不到就返回 `None`，或者干脆抛）——
    **两次尝试之间**拿它换一个会话（§1.6 那句「整段重来」要真有意义，就得换）。

    ⚠️ **为什么这个口子开在参数上、而不是在这里自己换**：`_WindowGone` 的定义就是
    「探针说窗口死了」，而手里那个会话绑的**正是那串已经没了的 ws_url** ——
    在同一个会话上重来是**结构性无用**的（三遍都会撞同一堵墙）。
    谁能给得出一个能用的会话、怎么给，只有调用方知道（`explore` 会拿同一串 `ws_url`
    重起一个 MCP 会话；真窗口没了得走 `reopen` —— 那一步是人/服务的，`replay` 够不着，
    也**不该**自己去开一个窗口）。
    **不给 `fresh` = 老样子**（同一个会话重来，最多 `REPLAY_ATTEMPTS` 遍）；
    **给了但换不到**：沿用旧会话接着重来，并且**在 `why` 里写明**（不静默 ——
    不然读账的人会以为「3 遍都没走完」是窗口的问题，其实是没人能给它一个新会话）。

    ⚠️ **会话的所有权归调用方**（复审 Q1 ①）：这里换来的那个只是**本函数后面几遍**用；
    谁拥有它、谁来关它、`explore` 手里那个要不要跟着换，**一律由 `fresh` 那边说了算**
    （`explore` 的 `respawn` 就是用 `nonlocal` 把**它自己那个** `session` 换掉）。
    本函数**从头到尾不关任何会话**。

    这个函数**只做账上写着的事**：不判断、不绕开、不「看着不对就换成别的选择器」。
    走不通就停在原地说话（§1.6）—— 「静默跳过」正是 R-35 那次失败的形状。
    """
    rows = list(steps or [])
    done, landed = 0, ""
    #: 换会话没换成的那句人话（换不到不是致命错，但**要说出来**）。
    swap = ""
    for attempt in range(1, REPLAY_ATTEMPTS + 1):
        try:
            out = _replay_once(session, rows, on_step=on_step, alive=alive)
            return dict(out, attempts=attempt)          # ← 试了几遍（见 docstring）
        except _WindowGone as gone:
            done, landed = gone.done, gone.landed
            if attempt >= REPLAY_ATTEMPTS:
                return {"done": done, "landed": landed, "attempts": REPLAY_ATTEMPTS,
                        "why": ("窗口又死了：整段重来 %d 遍都没走完（%s）。停下来叫人 —— "
                                "重放里没有不可逆的动作，重来本身是安全的，"
                                "但窗口一直起不来就只能停在这儿。%s"
                                % (REPLAY_ATTEMPTS, gone.cause, swap))}
            if fresh is not None:
                try:
                    session = fresh(session) or session
                except Exception as exc:           # noqa: BLE001 —— 换不到**不是**致命错
                    swap = ("另外：换会话也没换成（%s: %s）—— 后面这几遍还是在同一个会话上"
                            "撞同一堵墙。" % (type(exc).__name__, exc))
    return {"done": done, "landed": landed, "attempts": REPLAY_ATTEMPTS,
            "why": "窗口一直没起来。"}   # 走不到（保险）


def _replay_once(session, rows: list, *, on_step, alive) -> dict:
    """走一遍。返回结果 dict；窗口死了抛 `_WindowGone`（由 `replay` 决定重来）。"""
    state = {"done": 0, "landed": ""}
    side = {"broken": False, "why": ""}

    def record(step: dict) -> None:
        """旁路（实时视图 / 账本）—— 坏掉不许带塌主路，但要**说出来**（与 `emit` 同规矩）。"""
        try:
            _emit(on_step, step)
        except Exception as exc:                       # noqa: BLE001
            if side["broken"]:
                return
            side["broken"] = True
            side["why"] = ("⚠️ 旁路（实时视图 / 账本）在重放这一步上没记成：%s: %s —— "
                           "重放照常往下走（旁路坏掉不许带塌主路），但这一趟的账可能是残的。"
                           % (type(exc).__name__, exc))

    def finish(why: str) -> dict:
        return {"done": state["done"], "landed": state["landed"],
                "why": (why + " " + side["why"]).strip() if side["why"] else why}

    if not rows:
        return finish("账上这一段是空的：没有可重放的步，一步都没走。")

    for i, row in enumerate(rows):
        action = str((row or {}).get("action") or "")
        if action in _ACTIONS:
            t0 = time.time()
            try:
                args, raw = _fire(session, row, alive, state)
            except _ToolFailed as exc:
                return finish(
                    "第 %d 行那一步「%s」停住了：%s —— 重放停在这一步**之前**"
                    "（不跳过、不换一条路：跳过之后剩下的动作会落在一页它们从没在上面做过"
                    "的页面上）。" % (i + 1, _step_label(row), exc))
            _absorb(state, raw)
            state["done"] += 1
            record(_replayed_step(row, action, args, raw, _ms(t0)))
            continue
        if action == "observe" and _is_checkpoint(rows, i):
            t0 = time.time()
            raw, why = _look(session, row, i, alive, state)
            if why:
                return finish(why)
            _absorb(state, raw)
            live = raw if isinstance(raw, dict) else {}
            record(_replayed_step(row, "observe", {}, raw, _ms(t0)))
            if not when_holds(_when_from_row(row), live):
                return finish(
                    "第 %d 行那个核验点没对上：我以为会到「%s」，实际是「%s」—— 停在这里，"
                    "别接着往下点（分不清「落到另一条分支」还是「选错了元素」，两条都停）。"
                    % (i + 1, _expect_say(row), _live_say(live)))
            continue

    if str((rows[-1] or {}).get("action") or "") in _ACTIONS:
        # 最后那一步的落点账上没记（它后面那条观察在**边界之外**）—— 如实看一眼落在哪，
        # **不判对错**（没有可比的判据）。`landed` 靠这一眼才对得上「现在在哪儿」。
        t0 = time.time()
        raw, why = _look(session, rows[-1], len(rows) - 1, alive, state)
        if why:
            return finish(why)
        _absorb(state, raw)
        record(_replayed_step(rows[-1], "observe", {}, raw, _ms(t0)))
    return finish("这一段都重放了：账上那 %d 个动作照本走成了。" % state["done"])


def _look(session, row: dict, i: int, alive, state: dict) -> tuple:
    """核验点 / 末尾那一瞥用的**一次 observe** → `(raw, why)`。

    `why` 非空 = 这一眼**没看成**（调用方拿它走 `finish`）。为什么要专门一个出口：
    这两处原先直接调 `_call` —— 工具报错而**窗口还活着**时，`_ToolFailed` 会**穿过 `replay`
    抛出去**（复审实测）。那同时违反两件事：返回值的契约（`{done, landed, why}`）与 §1.6
    「一律停下说话」—— 这里是**崩掉而不是说话**，而且这一步之前**已经重放掉的步**
    （`done` / `landed`）随异常一起丢，读账的人连「走到哪儿了」都看不到。

    为什么它当时没被任何用例打红：那批用例的失败桩**全打在 `click` / `goto` 上**，
    没有一条 observe 报错的桩 —— 接口的尾部没人看着。现在两处各有哨兵。
    """
    try:
        return _call(session, "observe", {}, alive, state), ""
    except _ToolFailed as exc:
        return None, (
            "第 %d 行那一眼没看成：%s —— 重放停在这里说话（§1.6：看不到这一页就确认不了"
            "自己走对了；不跳过、也不猜。这一步之前已经重放掉的那几步照实记在 `done` 里）。"
            % (i + 1, exc))


def _fire(session, row: dict, alive, state: dict) -> tuple:
    """把一行动作发出去 → `(args, raw)`。

    `goto`：账上那串地址直接发（R4 已经保证它是见过的）。
    click / form / scroll：`target.selectors` **一个个试，第一个能用的胜**（§1.4.4）——
    重放的分辨力比产物弱是**已知且接受**的（产物有「重新 observe 按 text+role+near
    重定位」那三跳，agent 侧没有）。
    """
    action = str(row.get("action") or "")
    if action == "goto":
        url = _goto_url(row)
        if not url:
            raise _ToolFailed("账上这一步只写了「打开某个地址」，地址没记下来")
        return {"url": url}, _call(session, "goto", {"url": url}, alive, state)

    target = row.get("target") or {}
    selectors = [str(s).strip() for s in (target.get("selectors") or []) if str(s or "").strip()]
    if not selectors:
        raise _ToolFailed("账上这一步**一条选择器都没留下**（`target.selectors` 是空的）")
    frame = str(target.get("frame_id") or "").strip()
    last = "工具说没找到"
    for selector in selectors:
        args = _action_args(action, row, selector, frame)
        try:
            return args, _call(session, action, args, alive, state)
        except _ToolFailed as exc:
            last = str(exc)                    # 这个选择器在这页上找不到 → 试下一个
    raise _ToolFailed("这几条选择器在页面上**一条都解析不出来**：%s（%s）"
                      % ("、".join(selectors), last))


def _action_args(action: str, row: dict, selector: str, frame_id: str) -> dict:
    """这一步发给工具的参数。**只发账上有的东西**。

    - `frame_id`：账上**有**（非空）才带。`_target_of` 总写这个键，主帧那一支是 `""` ——
      所以判据是「值非空」。**不许编一个 `"main"`**：那只是个显示用的名字，
      传给 `--frame-id` 会被当成一个不存在的帧（G4）。
    - 填表的值：**账上记的那个**（`result.fill.value`）—— 重放用的是**同一份数据**，
      不是重新随机一个（§1.4.1）。`check` / `select` / `value` **恰好给一个**（registry 的
      `formMode`：多给的那几个会被静默忽略）。
    - `track`：账上没记 → **不传**（不编）。
    """
    args: dict = {"selector": selector}
    if action == "form":
        fill = (row.get("result") or {}).get("fill") or {}
        kind = str(fill.get("kind") or "value")
        value = fill.get("value")
        if kind == "check":
            args["check"] = str(value).strip().lower() in ("true", "1", "yes")
        elif kind == "select":
            args["select"] = str(value or "")
        else:
            args["value"] = str(value or "")
    if frame_id:
        args["frame_id"] = frame_id
    return args


def _call(session, name: str, args: dict, alive, state: dict):
    """发一次工具调用。失败时分辨「窗口死了」与「这一步没做成」（§1.6 的两行）。"""
    try:
        return session.call_tool(name, args)
    except Exception as exc:                           # noqa: BLE001
        if _probe_dead(alive):
            raise _WindowGone(state["done"], state["landed"],
                              "%s: %s" % (type(exc).__name__, exc)) from exc
        raise _ToolFailed(str(exc)) from exc


def _probe_dead(alive) -> bool:
    """问一句「窗口还活着吗」。**探针自己坏掉 → 按死了处理**（与「闸坏掉当暂停」同规矩）。

    反过来（坏掉 = 活着）会把「窗口没了」说成「页面上找不到那个按钮」——
    一句指错方向的话，读账的人会去查选择器。而按死了处理最多多走两遍重放，
    重放里**没有不可逆的动作**，重来是安全的（§1.6）。

    ⚠️ 但「**问出来了，答案是「不知道」（`None`）**」是另一件事 —— 它**不当死**
    （复审 Q1 ③）：这一条与 `_window_is_dead` **必须是同一个取向**。两边一个当死、
    一个当活的最坏组合是真出现过的：重放这边**主动关掉一个还活着的会话**，
    而模型那边不认「窗口没了」⇒ 整趟在一堆假的「工具失败」里烧到预算类停因。
    `BitWindow.probe()` 自己的 docstring 就写着「`None` **问不出来**（别拿它当死）」——
    这里是照它说的做：**问不出来 = 不知道 = 不当作死**（工具这次失败就如实记成失败）。
    """
    if alive is None:
        return False
    try:
        return alive() is False
    except Exception:                                  # noqa: BLE001
        return True


def _absorb(state: dict, raw) -> None:
    """把这次工具调用回来的**地址**记下来（`landed` 就是「现在在哪儿」）。"""
    if isinstance(raw, dict) and str(raw.get("url") or "").strip():
        state["landed"] = str(raw["url"]).strip()


def _is_checkpoint(rows: list, i: int) -> bool:
    """第 i 行是不是「这一步**改了页**」那个核验点（§1.6：一页一次 observe，0 模型调用）。

    判据是**状态名变了**：`_describe` 把状态名记在**发起那一刻**（`pages.current_name`），
    而换页是 `observe` 干的、记在**下一行**头上 —— 所以「这一步改了页」看的是下一行的状态名。

    前缀**最后一行**也核：那是「落点对不对」那一问（§1.4.5 第 2 步）。
    """
    if str((rows[i] or {}).get("action") or "") != "observe":
        return False
    if i == len(rows) - 1:
        return True
    return _state_of(rows[i + 1]) != _state_of(rows[i])


def _state_of(row) -> str:
    return str((row or {}).get("state") or START_STATE)


def _when_from_row(row: dict):
    """那一页的判据 —— **用账上那行自己的 url 与正文重算一条**，与 `_when_for` 同一套。

    为什么不是逐字节比对：重放的判据必须是**产物那套放宽后的 `when`**（§1.6）——
    URL 取稳定前缀（`_stable_url`）、正文取一段（`_snippet`）。
    逐字节比对会把「同一页的轮换变体」读成失败，而那个失败会停掉整段重放。

    ⚠️ 这里**重算**而不是抄 `journey.pages`：`replay` 手里只有这些行（账本一个文件
    只落 step），而 `_when_for` 也就是这两样东西算出来的。
    """
    result = (row or {}).get("result") or {}
    when: dict = {}
    url = _stable_url(str(result.get("url") or ""))
    if url:
        when["url_contains"] = url
    snippet = _snippet(_norm(result.get("page_text_head") or ""))
    if snippet:
        when["text_contains"] = [snippet]
    return when or None


def _expect_say(row: dict) -> str:
    """「我以为会到」的那个 X（人话：地址，没有地址就给页面标题）。"""
    result = (row or {}).get("result") or {}
    return str(result.get("url") or result.get("title") or "账上那一页")


def _live_say(live: dict) -> str:
    """「实际是」的那个 Y。"""
    return str((live or {}).get("url") or (live or {}).get("title") or "一个地址都没报回来的页面")


def _replayed_step(row: dict, action: str, args: dict, raw, elapsed_ms: int) -> dict:
    """重放出来的一步 —— **与探索时那一步同形**（`origin` 是唯一的区别）。

    形状必须一样：账本里的一行就是 `Journey.steps` 的那一步，产物那侧（`states()` /
    `fills()` / 重放机）因此一个字的改动都不需要。

    ⚠️ **契约那五格在这儿只填得出一格**（2026-09-18，明知而留的口子，不是漏）：
    `receipt` 填得出来（这一步真发出去了、真回来了，原文就在手上），
    而 `step_no` / `sig_before` / `sig_after` **填不出来** —— 重放这条路手上没有
    「上一眼/这一眼」那套观测（`_look` 只在核验点上看，且不经过 `_Pages`）。
    所以那三格是 `None` + 一句 why（**「看不见」是一等值**，不许编）。
    这一轮只接了 `dispatch` 那条路（契约 §六 的验收走的就是它）；重放那条路
    **欠着**，账上看得出来。
    """
    target = None if action == "observe" else dict((row or {}).get("target") or {})
    why = {
        "sig_before": "重放这条路今天还没有把签接上（只有 `dispatch` 那条路接了）—— 量不到。",
        "sig_after": ("重放这条路今天还没有把签接上（只有 `dispatch` 那条路接了）—— 量不到。"),
    }
    sig_after = _raw_sig(raw) if action == "observe" else None
    if sig_after is not None:
        del why["sig_after"]
    return {"state": _state_of(row), "action": action, "target": target,
            "result": _summarize(action, args, raw, elapsed_ms, None),
            "note": _say(action, target, True), "origin": "replay",
            #: 契约那五格（脚本填的）—— 见上面那句「只填得出一格」。
            "step_no": (row or {}).get("step_no"), "receipt": _receipt_of(action, raw),
            "sig_before": None, "sig_after": sig_after, "why": why}


# ─────────────────────── 页面状态（换页 = 换状态）───────────────────────


class _StepShots:
    """探路时的**步拍策略**（设计注 §5.4）：只留「不对劲」的那些步。

    **留的集合 = {没做成} ∪ {做成了但页面没变}**；其余情况**一张不留** ——
    一次跑顺的探路可以一张都没有。

    三条纪律（写在这里，因为它们决定了下面每一行的形状）：

    1. **点前那条命令是下限、省不掉** —— 动手**之前**不可能知道这一步会不会出问题。
       所以「只对可疑的步拍」落在**留**上，落不到**拍**上；能省的只有**点后**。
    2. **「页面签没变」是免费信息** —— 键就是 `_Pages.note_page` 早就在算的
       `(url, title, page_text[:400])`，下一张 `observe` 一到就能比，**不额外发命令**。
    3. **判据只在「紧接着」的那次观测上生效** —— `click A → click B → observe` **不认**
       （那时「没变」说不清是谁造成的）。**认不出来就按「不留」办**（§5.4 判据 2）。

    **旁路纪律**：拍照永远不许把探路搞挂（与 `emit` 那条「旁路坏掉不许带塌主路」同源）——
    shooter **抛异常**或者**回一句「拍不成」**，都只记 `journey.shots_why`，然后照常往下走。
    两条路都要堵：`shots.capture_via_session` 是**不抛**的那种，它把失败说在返回值里。

    **一个名字 = 一次落盘**（账是按**名字**认的，所以名字必须唯一）。名字的形状：

        <本趟标记>-step-<第几步>-<before|after>[-<第几次>].png

    - **本趟标记**（`self.tag`）**每个实例一个**。为什么非要它：同一个 job 目录会被**多趟**
      探路共用（生产形状：一次节点**最多 3 趟**重探，`service.py` 的 `shots.dir_for(job_id)`），
      而 `finish()` 是**按名字**删的 —— 不带标记的话，第 2 趟写的 `step-1-before.png` 会
      顶掉第 1 趟特意留下的证据、再被第 2 趟的收口删掉（复审实测：**2 张丢 1 张**）。
      为什么用「每实例一个标记」而不是「趟号」：`explore()` 手上**没有**趟号（调用方
      每趟都是新起一个 `_StepShots`），而标记在**实例**上。
      ⚠️ **别把标记读成那道保证**：它是 `uuid4().hex[:6]`（24 位）⇒ **概率**，不是结构
      （3 趟撞一次 ≈ 1.8e-7，工程上够用）。真正兜住「换主」的是 `_name_for` 里那道
      `(self.where / name).exists()` —— 标记撞上也只是把名字岔开，没人会被顶掉。
    - **`[-<第几次>]` + 「盘上没人占」**：`_name_for` 一路数到「本趟没写过**而且盘上也没有**」
      为止。两个形状都靠它：
      ① 同一趟内 `(步号, 时刻)` 被写第二次 —— 实测那个形状
      （`click(没变) → observe(补拍) → click(失败)`）里两条步会引用**同一个**
      `step-3-after.png`，而盘上那串字节是后一条的 ⇒ 前一条的「点后」证据当场变成**假证据**；
      ② 两趟撞上同一个标记（见上一条）。
      **射程**（别读宽了）：那道 `exists()` 到「写下去」之间还留着一个窗口 —— 真·同时写同一个
      目录的两趟可以都查到「没人占」。**进程内这个形状不成立，而且那是结构、不是运气**：
      一个 job 的几趟在**一次节点执行**里**同步**跑完（`agent/graph.py:449-474` 的
      `pass_once` 循环，一趟跑完才起下一趟），而这次节点执行由**唯一**一个消费线程驱动
      （`agent/service.py:1326-1342`：`_ensure_worker` 只起一条 `siteforge-worker`，
      `_work` 从队列里一次取一个，`_advance` 是唯一会动图的地方）。两条都各有一条判据钉着
      （`tests/test_graph.py::test_the_retry_attempts_never_overlap_in_time`、
      `tests/test_service.py::test_only_one_thread_advances_jobs_at_a_time`）——
      谁把那两半拆了，它们当场红。

      ⚠️ **但它是「当前部署形状」这条前提，不是不变量**（别读成「不可能发生」）：
      **跨进程没有任何机制挡着** —— checkpointer 是 Postgres（状态层本来就多实例可共享）、
      shots 根可被 `SITEFORGE_SHOTS_DIR` 指到同一个目录，而这一层**没有文件锁、没有租约、
      没有 job 级所有权**。第二个实例、一次没杀干净的重启、或者运维伸手清 `runtime/`，
      都能让这个窗口当场变成真的（同一份前提也在咬 `_on_disk`：本趟之外的人删图，
      上限与那句话就跟着偏）。**要把它变成不变量是「加锁 / 租约」那一层的事**
      （Execution Truth），不在这一段里。

    **`kept` 的口径**（别读成「这个目录里有几张图」）：它数是**本趟步拍留在盘上的张数**
    —— `finish()` 按 `journey.steps` 引用到的名字重算。**别处**写进同一个目录的图
    （服务侧的闸拍 `pause-<n>.png`）**不计入**，收口也**不会碰它们**：收口删的名单是
    `_written - referenced`（**本趟写过的**），**不是扫目录**。

    **上限在要落盘的那一刻就硬**：`MAX_KEPT_SHOTS` 判的是**这一刻盘上属于本趟的张数**
    （`_on_disk`），判在 `_take` 落盘**之前** ⇒ 运行期任何时刻都不会超。为什么不能数
    `kept`：`kept` 只在结算（`_keep`）时涨，而结算钩子自己抛异常时它**永远不涨**
    （复审实测：上限 2、坏 `_keep`、5 个 click ⇒ **10 条命令、运行期盘上最多 9 张**，
    收工后才由收口抹平）—— **账平 ≠ 闸硬**。
    """

    def __init__(self, journey, where, shooter):
        self.journey = journey
        self.where = pathlib.Path(where)
        self.shooter = shooter
        #: **本趟留住的张数**（`finish()` 按 `journey.steps` 的引用重算）。别处写进同一个
        #: 目录的图（闸拍 `pause-<n>.png`）**不算**它 —— 口径见类注释。
        #: ⚠️ 上限**不**看它（看 `_on_disk`）：它只在结算时涨，坏 `_keep` 那条路上永远不涨。
        self.kept = 0
        #: **本趟的标记**：名字的第一段，让跨趟撞名**基本不发生**（见类注释「一个名字 = 一次落盘」）。
        #: 每个实例一个（= 每次 `explore()` 一个）—— **不用趟号**：`explore()` 手上没有它，
        #: 而标记在实例上。（顺带：两趟**同时**写同一个目录时它只是第一道；真兜住「换主」的
        #: 是 `_name_for` 里那道 `exists()`。）
        #: ⚠️ 它是 `uuid4().hex[:6]` = **24 位** ⇒ 撞上是**概率**（3 趟 ≈ 1.8e-7），不是保证；
        #: 保证在 `_name_for`：写之前先看盘上这个名字有没有人占。
        self.tag = uuid.uuid4().hex[:6]
        #: **本趟拍过的所有名字**（含后来被丢掉的）。收尾时拿它减「留住的步引用到的」
        #: 就是盘上该删的 —— 见 `finish()` 的 docstring：**账的真相在盘上，不在手上**。
        self._written: set = set()
        #: **本趟此刻还在盘上的那些** —— 上限判它，不判 `kept`（见类注释最后一段）。
        #: 落盘时加；`_drop` 减，判据是**盘上还有没有**、不是「我叫过 unlink 没有」：
        #: 删掉了减、**本来就已经不在盘上**（`FileNotFoundError`）也减，
        #: 只有**删不掉**（别的 `OSError`：它还在盘上占地方）才不减 —— 见 `_drop` 的 docstring。
        #: ⚠️ 射程：它跟得上的是**本趟自己动过手**的那些名字。本趟之外的人删了图、
        #: 而本趟后来**没再碰过那个名字**（那张图被留住了）时，它仍然把那张算在账上 ——
        #: 那是 D-2 那条前提（没有别人动这个目录）在咬它，不是这一行能修的。
        self._on_disk: set = set()
        #: 待结算的那一步：`{"step", "before", "key", "tainted"}`。`None` = 手上没有。
        self._pending: dict | None = None
        self._said_cap = False

    def _fail(self, why, when: str = "") -> None:
        """步拍没成的**那一刻**：两样一起记（Task 5 修复轮 2）。

        - `journey.shots_why` = 「**现在**坏着」（拍成了会被 `_take` 清掉，那是它的本分）；
        - `journey.shot_failures` = **只增**的那一条（`when` 给了才带 —— 带了 = 这是某一步的，
          没带 = 这条够不着「哪一步」）。

        ⚠️ **非空 = 出过事 ⇒ 必须有对账**（所以这个赋值只能待在这里，不能散到别处去）：
        这条不变量由 `tests/test_service_narration.py` 里那条 **AST 判据**钉着 ——
        它扫的是**全仓每一个 `.py`**（不是只看这一个文件），按**接收者**认
        `journey.shots_why`（产物自己那些 `self.shots_why` 不算），只放过这里与
        `_take` 里那句清空。**别把这句注释当成保证** —— 保证是那条用例
        （它的射程与已知盲点也写在那个文件里）。

        ⚠️ 为什么必须在**这一刻**记：读的人要的是「这一趟出过什么事」。只更新那一格状态的话，
        一张没成、下一张成了 ⇒ 时间线上**一条都没有** —— 而那一刻确实出过事
        （复审实测过这个洞，它是我上一轮那行「成功就清空」带出来的）。
        """
        reason = str(why or "拍不成，而且没说为什么")
        self.journey.shots_why = reason
        row = {"why": reason}
        if when:
            row["when"] = when
        self.journey.shot_failures.append(row)

    def _safe(self, what: str, fn, *args, **kwargs) -> None:
        """**旁路纪律的总闸**：步拍自己坏了，绝不许把探路带塌。

        为什么不靠「小心别写错」：`dispatch` 里抛出去的异常会被 `run_tool_loop` 的
        `except Exception` 记成**一次工具失败** —— 于是**模型会照着这条假错换路走**，
        而账上留下的是「工具没做成」。
        **实测栽过**：`on_observation` 里一个 `AttributeError` 就是这么变成「工具失败」的
        （用例报的是 `KeyError: 'shot_before'`，看起来像「没留图」，其实是**拍照把探路搞挂了**）。

        ⚠️ 与 `_take` 里那道**不是**同一个：那道挡的是 **shooter** 抛异常；
        这道挡的是**步拍自己的代码**抛异常（写错了、外部模块变了、……）。
        两道都要有 —— 只堵一道的话，另一道照样能把一趟真探路变成废账。
        """
        try:
            fn(*args, **kwargs)
        except Exception as exc:                   # noqa: BLE001 —— 旁路，什么都得吞
            # ⚠️ 走 `_fail`（两样一起记）：这一条**没有 `when`** —— `_safe` 够不着「哪一步」，
            # 所以时间线上那句说的是「探路里的步拍图这一次没留下」，不是「这一步」
            # （Task 5 修复轮 1 的 Important-2：说得出多少说多少，别编）。
            self._fail("步拍自己坏了（%s）：%s：%s" % (what, type(exc).__name__, exc))
            # ⚠️ 这里**故意不回滚**（`f0e3573` 那版在这儿有一句 `self._discard_pending()`，已删）。
            # 为什么原来那句是旧话：回滚只看得到 `_pending`（「还在手上」的那一个），而
            # **账的真相在盘上** —— 从结算钩子自己抛异常那一刻起它就够不着了
            # （`on_observation` / `after_mutation` 都是**先清 `_pending`、后调 `_keep`**），
            # 所以那句回滚对「盘上不留孤儿」**一次都没起作用**（复审实测：删掉它，27 条 0 红）。
            # 现在挡住孤儿的是 `finish()` 的收口（`_written - referenced`）：它对**每一条**
            # 「把引用弄丢」的路都成立，不挑「异常抛在哪一行」。
            # 残留的 `_pending` 也不会变成坏账：要么被紧接着那次观测正常结算，要么在
            # `finish()` 里被 `_discard_pending()` 收掉 —— 两条路都不留孤儿。

    # ── 对外的口（**都要走 `_safe`** —— 见上）─────────────────────

    def before_mutation(self, step: dict, session, key) -> None:
        """**动页面之前**拍一张，并把这步挂成待结算。

        `key` 是**动手那一刻**的页面签（`_Pages.current_key`）—— 后面拿它判「变了没有」。

        **「认不出来」只有一种来源**：手上还有一个没被观测结算的动页面动作
        （`click A → click B → observe` 那个形状）。`screenshot` / `wait` / `diff`
        这类**不动页面**的动作**不算** —— 它们夹在中间，页面的签照样可比（见 `on_observation`）。
        """
        tainted = self._pending is not None
        self._discard_pending()
        name = self._take(session, step, "before")
        self._pending = ({"step": step, "before": name, "key": key, "tainted": tainted}
                         if name else None)

    def after_mutation(self, step: dict, session, name: str, ok: bool) -> None:
        """动作回来之后结算一次。

        三种情形：

        - **做成了的动页面动作** → 什么都不做，**`pending` 留着** ——
          留不留由**紧接着那次观测**说了算。
          ⚠️ 在这里清掉 pending 的话，那次观测就没有东西可结算，点前那张会**永远留在盘上**
          （实测栽过：跑顺的步也留了一张，整个策略的主要收益当场归零）。
        - **没做成的动页面动作** → 当场补拍点后那张，两张都留（§5.4 的 a 支）。
        - **报错的 `observe`** → 它是唯一「**本该结算而没结算成**」的动作，
          而「紧接着」这个条件也**再也回不来了** ⇒ **按「不留」办**，把点前那张删掉。
          ⚠️ 报错的 `screenshot` / `diff` **不作废**：它们既不动页面、也不负责结算，
          凭什么叫那条链作废？（原来按 `ok` 判，管得比该管的宽 —— 复审点名。）
          （不删就是漏：那张图会留在盘上、没有任何 `shot_before` 指向它、**也不计入 `kept`**
          —— `MAX_KEPT_SHOTS` 于是管不住盘。）
        """
        if name not in MUTATING:
            # ⚠️ **只有「失败」的非动页面动作**才在这里作废那条链（一张报错的 `observe`
            # 结算不了手上那一步，而「紧接着」再也回不来了）。
            # **成功的** `observe` **绝不能**在这儿动 —— 它由 `on_observation` 结算，
            # 抢在它前面作废就是把它整个废掉（实测栽过：这一支写成无条件的之后，
            # 「跑顺」和「没变」两种情形**都**变成「不留」，`form`/`scroll` 那几条更是全灭）。
            # ⚠️ **只有报错的 `observe` 才作废那条链** —— 它是唯一**本该结算而没结算成**的那个。
            # 报错的 `screenshot` / `diff` 既不动页面、也不负责结算，凭什么把链作废？
            # （复审点名：原来按 `ok` 判，管得比该管的宽。）
            if not ok and name == "observe":
                self._discard_pending()
            return
        if ok:
            return                            # 做成了 ⇒ 挂着，等紧接着那次观测
        pending = self._pending
        self._pending = None
        step["shot_after"] = self._take(session, step, "after")
        step["shot_after_deferred"] = False       # 当场拍的 —— 不许标成「随后补拍」
        if pending is not None and pending["step"] is step:
            self._keep(pending, step)
        elif step.get("shot_after"):
            # 点前那张没拍成，但点后这张拍成了 —— 它是「哪一步不对」的证据，不该整条丢掉。
            self.kept += 1

    def on_observation(self, session, key) -> None:
        """一张 `observe` 到了：结算手上那一步。

        ⚠️ **夹在中间的 `screenshot` / `wait` / `diff` 不算断链** —— 它们不动页面，
        所以「动手时那一签」与「现在这一签」照样可比，判据仍然成立。
        真正让判据失效的只有**另一个动页面动作**（那个由 `tainted` 挡下）。
        """
        pending = self._pending
        self._pending = None
        if pending is None or pending["tainted"]:
            self._discard(pending)            # 认不出来 → 按「不留」
            return
        # 「页面变了没有」这条判据**只对** `click` / `goto` 成立 —— 与产物侧**同一张表**
        # （`template.DIFF_JUDGES`，`agent/template.py:192`）。填框、滚动**都不改**
        # `body.innerText`，拿它判它们必然**假阳性**（实测：一趟全部成功的漏斗
        # 因为 3×form + 1×scroll 在盘上留了 10 张，而跑顺的运行本该一张不留）。
        if str(pending["step"].get("action") or "") not in DIFF_JUDGES:
            self._discard(pending)            # 没有通用判据的动作：不回看，不留
            return
        if key is not None and key == pending["key"]:
            step = pending["step"]
            step["shot_after"] = self._take(session, step, "after")
            step["shot_after_deferred"] = True    # **随后**补拍的 —— 必须标出来
            self._keep(pending, step)
        else:
            self._discard(pending)            # 页面变了 = 这一步跑顺了 → 一张不留

    def finish(self) -> None:
        """一趟探路收尾：**按盘上的实数收口** —— 不是看手上还有什么。

        为什么**不能**只看 `_pending`：它只是「**还在手上**的那一个」，而**账的真相在盘上**。
        任何一条把引用弄丢的路都会让文件留在盘上而 `_pending` 上看不出来 ——
        实测栽过两次：`_keep` 自己抛异常（那时 `_pending` 已经清了）、
        `_take` 落盘与挂 pending 之间那个窗口。**在那两处加回滚，删掉也 0 条用例红**
        （复审当场证的：`fbe1ae1` 与上一版结果逐字相同）。

        所以收口用「**拍过的所有名字**」减「**留住的步引用到的名字**」——
        这样无论哪条路把引用弄丢，盘上都不会剩孤儿；`kept` 也一并按实数重算。

        ⚠️ 这条收口的**射程**（别读宽了）：
        - 它删的**只有本趟写过的名字**（`_written` 是本实例的，而名字带本趟标记 ⇒
          跨趟那些一个都碰不到）。**不是扫目录** —— 服务侧的闸拍 `pause-<n>.png`
          就落在同一个目录里，它不在 `_written` 里 ⇒ **不会被动**（这是对的：删谁由
          「本趟写过谁」说了算，不由目录里有什么说了算）。
        - `kept = len(referenced)` = **本趟留住的张数**（本趟步拍的），**不是**这个目录里
          的图数（闸拍不在里面）。口径写在类注释里。
        - 它**不**负责上限：上限在 `_take` 落盘那一刻就判（`_on_disk`）—— 收口是收尾的
          兜底，不是运行期的闸。

        ⚠️ 顺带：一趟**以动页面动作收尾**的探路（模型收工 / 预算到顶 / **人按停**）
        永远不会再来一次观测 —— 而「按停」正是这个功能的**主交互**。
        """
        self._discard_pending()
        referenced = {s.get("shot_before") for s in self.journey.steps}
        referenced |= {s.get("shot_after") for s in self.journey.steps}
        referenced.discard(None)
        for name in self._written - referenced:
            self._drop(name)
        self.kept = len(referenced)          # **按实数重算** —— 不靠一路加出来的那个数

    # ── 里面的 ──────────────────────────────────────────────────

    def _name_for(self, when: str) -> str:
        """这一张落在哪个名字上 —— **本趟、本次落盘**唯一（见类注释「一个名字 = 一次落盘」）。

        `%d` 是**此刻**的 `len(journey.steps)`（点前那张在步入账**之前**拍，所以它常常比
        那一步的序号小 1）—— 与产物侧同源，但它**不是**步的唯一号：同一个步号会被写两次
        （点前 / 点后，或者两条步的落点撞到一起），所以撞上已写过的名字要依次数下去。

        ⚠️ 数到「没人占」为止是**两道**，缺一不可：
        - `name in self._written` —— 本趟写过的（**只增不减**：被丢掉的名字也不复用，
          免得「删过的名字」又活过来）；
        - `(self.where / name).exists()` —— **盘上有别人的东西**压着这个名字。这一道是
          `self.tag` 那 24 位够不着时唯一的救兵：两趟撞上同一个标记 ⇒ 名字当场岔开
          （`…-before-2.png`），**第 1 趟那张一个字节都不会被顶掉**。
          没有它的话，「一个名字 = 一次落盘」就只是**概率**（3 趟 ≈ 1.8e-7），
          而它现在**是结构**：写之前先看盘上有没有人占。
          ⚠️ 这道 `exists()` 的**前提**（一个 job 的几趟是顺序跑的）写在类注释那段
          「射程」里 —— 它是**当前部署形状**（单进程单 worker），由两条判据钉着，
          跨进程没有任何机制挡着。**别把这两句读成同一句。**
        ⚠️ 还有一句**我证不出来的**：「两道，缺一不可」——本轮实测只证得出一半。
        把上面那道 `name in self._written` 去掉、只留 `exists()` ⇒ **红 0**：
        我构造不出让两道分开的形状（要它分开，得让「本趟写过、后来被删掉的」名字
        再出现一次，而那个名字里的步号 `len(journey.steps)` 是单调的）。
        留着它是因为它挡的正是那个形状（**只靠 `exists()` 拦不住「删掉之后再复用」**），
        但**别把这句读成「有判据钉着」**。
        """
        base = "%s-step-%d-%s" % (self.tag, len(self.journey.steps), when)
        name, n = base + ".png", 1
        while name in self._written or (self.where / name).exists():
            n += 1
            name = "%s-%d.png" % (base, n)
        return name

    def _take(self, session, step: dict, when: str) -> str | None:
        """拍一张落到 `where`。**不抛**：两条失败路（抛了 / 回了句拍不成）都只记账。

        ⚠️ 上限**在这一刻**判（`_on_disk` = 本趟此刻还在盘上的张数）—— 落盘之前判，
        所以「运行期任何时刻盘上都不超过 `MAX_KEPT_SHOTS`」。不数 `kept`：它要等结算才涨。

        ⚠️ **拍成了就把 `journey.shots_why` 清掉**（Task 5 修复轮 1 的 Minor-5）：
        那个字段说的是「这条**路**现在坏着吗」，不是一个历史记录。不清的话，这一趟里
        有一张没拍成之后**后面每一步都正常**，它仍然挂着那句话 —— 读它的人会以为
        「这一趟的图一直没留下」（事实被拉长了）。去重能挡住刷屏，挡不住这件事。

        ⚠️ 但**清空不等于没发生过**（修复轮 2）：没成的那一刻进的是
        `journey.shot_failures`（只增）—— 「一张没成、下一张成了」这件事，
        时间线靠**那本账**看得见，不靠这一格状态。
        """
        if len(self._on_disk) >= MAX_KEPT_SHOTS:
            if not self._said_cap:
                self._said_cap = True
                self.journey.note(
                    "本趟的步拍图有 %d 张还在盘上（上限 %d）—— **从这一步起不再拍**。"
                    "（这个数是**本趟**的，**不是**这个目录里的图数：服务侧的闸拍 "
                    "`pause-<n>.png` 就跟步拍落在同一个目录里，它不计入、也不受这道上限管。）"
                    "后面的步要是不对劲，账上不会再有图：这是**知道的**，不是漏了。"
                    % (len(self._on_disk), MAX_KEPT_SHOTS))
                # ⚠️ **上限生效这件事也要进那本只增的账**（Task 5 修复轮 3）：
                # 上面那句人话只进 `journey.notes`，而 `notes` **不上时间线**
                # （服务侧只把它写进 `attempts.jsonl`）—— 于是从这一步起
                # **既没有图、也没有账、也没有事件**，页面上就是一个没人解释的空图框，
                # 而那正是设计注 §3.2 第 6 行要治的形状。
                # 上限本身是对的（别抬它）；缺的只是「它生效了」这件事没人说。
                # ⚠️ 所以账里这一条**不是「没拍成」**：是**知道的、不再拍**（`capped`）。
                self.journey.shot_failures.append({
                    "why": "本趟的步拍图已经有 %d 张在盘上（上限 %d）"
                           % (len(self._on_disk), MAX_KEPT_SHOTS),
                    "capped": True})
            return None
        dest = self.where / self._name_for(when)
        try:
            name, why = self.shooter(session, dest)
        except Exception as exc:                   # noqa: BLE001 —— 外部世界，什么都可能抛
            self._fail("拍照时它抛了：%s：%s" % (type(exc).__name__, exc), when)
            return None
        if not name:
            self._fail(why, when)
            return None
        self._written.add(str(name))     # **落盘即登记** —— 收尾按这个收口
        self._on_disk.add(str(name))     # 上限按这个判（此刻它真的在盘上）
        self.journey.shots_why = ""      # 拍成了 ⇒ 这条**路**现在不坏（见 docstring）
        return str(name)

    def _keep(self, pending: dict, step: dict) -> None:
        if pending.get("before"):
            step["shot_before"] = pending["before"]
            self.kept += 1
        if step.get("shot_after"):
            self.kept += 1

    def _discard(self, pending) -> None:
        """不留：把点前那张从盘上删掉（点后那张**从没拍过**，所以没什么可删的）。"""
        if pending and pending.get("before"):
            self._drop(pending["before"])

    def _discard_pending(self) -> None:
        self._discard(self._pending)
        self._pending = None

    def _drop(self, name) -> None:
        """把一张从盘上删掉，并让上限**跟着盘上的实况**走。

        `_on_disk` 是「本趟此刻还在盘上的那些」，所以它的增减必须以**盘**为准，
        不是以「我叫过 unlink 没有」为准。两条出口**含义正相反**，不许合并：

        - **`FileNotFoundError` = 它已经不在盘上了** ⇒ 照样 `discard`。
          复审点名的那一格（「文件本来就**不在**，`_on_disk` 却永远留着它」）：原来它与
          「删不掉」共用一个 `except OSError`，于是**本来就不在**的名字永远占着上限 ——
          闸比盘紧（正是 C-2 说不许的**越收越紧**那个方向），而且那句话会**报大**
          （说 2、盘上 1）。它什么时候会发生：这一趟写过的图被
          **本趟之外的手**拿走了（运维清 `runtime/`、第二个实例指着同一个 shots 根 ——
          正是 D-2 那条前提被破的样子），之后本趟照常把它当作「要丢的那张」来处理。
        - **别的 `OSError` / `ValueError`（权限、只读、名字非法……）= 它还在那儿，
          只是我删不掉** ⇒ 不吃 `discard`。那条是对的：那张图还在盘上占地方，
          上限就该照数它（**别越收越紧**是这一格，不是上面那一格）。
        """
        try:
            (self.where / str(name)).unlink()
        except FileNotFoundError:           # 已经不在盘上了 ⇒ 不许继续占上限
            self._on_disk.discard(str(name))
            return
        except (OSError, ValueError):       # 删不掉不是错 —— 收尾失败不许盖掉别的人话
            return                          # ⚠️ 但它**还在盘上** ⇒ 上限那张照数（别越收越紧）
        self._on_disk.discard(str(name))    # 真没了才减


def _utf8_safe(step: dict) -> dict:
    """把这一步里**线上写不出去的码位**换掉（`events.safe_value`），并把个数记在 `why` 里。

    为什么在这条路上必须做（2026-09-18，Task 4 收口复审点名的洞）：一个孤立代理对
    （`"\\ud800"`）**过得了 `json.dumps`，过不了最后那次 `.encode("utf-8")`** ——
    而这一步要去两个地方：账本那一行（JSONL）与 `/live` 的 JSON。撞上的后果不是「这一步
    没记上」，是**整条时间线一条都读不出来**（`/live` 500）。
    字节来自**外面**（CDP 的回执、页面上的 url 与元素文字），所以不能靠「写的人小心」——
    在**转抄那一刻**处理掉，而且**数出来**（替换 = 有损，有损必须说）。
    """
    safe, replaced = events.safe_value(step)
    if not replaced:
        return step
    why = dict(safe.get("why") or {})
    why["unwritable_bytes"] = (
        "这一步里有 %d 个字节**线上写不出来**（孤立代理对，来自工具回执/页面上的字）—— "
        "已按 `�` 记。**不是它本来长这样**（不换掉的话 `/live` 会 500，整条时间线一条都读不出来）。"
        % replaced)
    safe["why"] = why
    return safe


def _raw_sig(raw) -> dict | None:
    """那一页的**原始签** —— 契约 §二 `sig_before` / `sig_after` 那两格的内容。

    三样，全部从**这一次观测的原始返回**（`observe` 的 `PageModel`）算出来：

    - `url`     —— 页面地址，原样；
    - `text`    —— **正文指纹**：归一化之后的 `page_text` 取 sha1 的前 12 位十六进制。
                   存指纹不存正文：签是要**比**的，正文会长到没法放进账本；
    - `visible` —— **可见元素计数**：`actions + fields + option_groups` 的条数
                   （陷阱元素 cdp 已经排掉了 —— 它不在 actions/fields 里，
                   单列在 `honeypots`，见 `observe.go` 那段「为什么单列」）。

    ⚠️ **为什么不是 `Journey.steps[].state` 或 `pages[].url/title`**（复审 2026-09-18 点名）：
    那两样是**我们自己写的摘要** —— 状态名是我们起的 slug，`title` 是页面自报的一句话。
    拿它们当签，等于让裁判去读**被测量者自己写的报告**，而「判断和执行是同一方」
    正是这份契约 §一要换掉的那个东西。签必须是原始观测算的，一个字段都不许借道摘要。

    ⚠️ **读不出来就是 `None`**（回执里没有 `page_text` 这一键、或者根本不是字典）：
    「看不见」是一等值（契约 §二②），**不许**折算成「没变化」，也不许编一个空签。
    调用方拿到 `None` 要给一句 `why`（`Timeline` 会把没解释的 `None` 挡在门外）。
    """
    if not isinstance(raw, dict):
        return None
    if "page_text" not in raw:
        return None                       # 这一眼没读到正文 = **看不见**，不是「正文是空的」
    text = _norm(raw.get("page_text") or "")
    visible = (len(raw.get("actions") or []) + len(raw.get("fields") or [])
               + len(raw.get("option_groups") or []))
    return {"url": str(raw.get("url") or ""),
            "text": hashlib.sha1(text.encode("utf-8")).hexdigest()[:12],
            "visible": visible}


def _receipt_of(name: str, raw) -> Any:
    """**CDP 回执的原文** —— 契约 §二第 3 格，脚本在这里只做一件事：**转抄**。

    为什么要专门一个函数（而不是 `step["receipt"] = raw`）：转抄要**逐字**，但账本与
    checkpoint 都装不下**长字符串**（`observe` 的整段正文、`screenshot` 的 base64）——
    两条判据钉着这件事（`test_a_diagnostic_row_…` 的 2000 字符、`test_screenshot_vision::
    test_explore_hands_the_screenshot…` 的「base64 不许进账本」）。所以：

    - **短的那份逐字照抄**（动作类的回执 —— `{"ok": true, "note": …}` / `{"url": …}` /
      那句报错 —— **永远**走这一条，契约 §六 要的就是它们）；
    - **单个字符串超过 `RECEIPT_STR_CHARS`** 的，那一格换成一句「有多大、没抄」；
    - **整份序列化之后超过 `RECEIPT_MAX_CHARS`** 的，整个换成一句「有多大、没抄」。

    ⚠️ 三种都是有损，而且都**说出来**（不留假的原文片段：截一段 base64 进账本，
    读的人只会以为 CDP 就回了这么一串乱码）。**损的是长度与图，不是判断词** ——
    契约 §二③ 的边界说 `receipt` 里**可以**有 `ok` / `success` 这种词（CDP 自己的原话，
    替它删 = 伪造笔录），所以这里**一个词都不动**。

    ⚠️ `screenshot` 那条**单独判**（与 `_summarize` 对它是同一个理由）：它的回执**就是一张图**
    —— 而「图不进账本」是这一片的**既有规矩**（`test_screenshot_vision.py::
    test_explore_hands_the_screenshot_to_the_model_and_keeps_the_journal_small` 逐字节钉着，
    理由见 `journal.py` 的模块 docstring）。所以那一格只记「有一张图、多大」。
    ⚠️ 尺寸闸拦不住它（测试里那张 1×1 的 PNG 只有一百来个字符）—— 所以判的是**工具名**，
    不是长度；这与 `_summarize` 的分支是同一条口径。
    """
    if name == "screenshot":
        try:
            size = len(json.dumps(raw, ensure_ascii=False))
        except (TypeError, ValueError):
            size = len(str(raw))
        return {"transcribed": False, "chars": size,
                "note": "这一次的回执**就是一张图**（%d 字符）—— 图不进账本（既有判据），"
                        "只记它有多大；要看图走 `/job/{id}/shot/…` 或 `result.bytes`。" % size}

    def bound(value):
        if isinstance(value, str):
            if len(value) <= RECEIPT_STR_CHARS:
                return value
            return {"transcribed": False, "chars": len(value),
                    "note": "这一格太长（%d 字符，逐字转抄的上限 %d）—— 没抄下来，"
                            "只记了它有多大。" % (len(value), RECEIPT_STR_CHARS)}
        if isinstance(value, dict):
            return {k: bound(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [bound(v) for v in value]
        return value

    try:
        size = len(json.dumps(raw, ensure_ascii=False))
    except (TypeError, ValueError):       # 实在转不了（不该发生：MCP 回的是 JSON）
        return {"isError": False, "text": bound(str(raw))}
    if size > RECEIPT_MAX_CHARS:
        return {"transcribed": False, "chars": size, "limit": RECEIPT_MAX_CHARS,
                "note": "这份回执比逐字转抄的上限长 —— **没有抄下来**：账本与 checkpoint "
                        "都装不下整份原始返回（已有一条判据钉着）。原文在工具返回里。"}
    return bound(raw)


class _Pages:
    """把观察到的页面归成状态。

    ⚠️ 判据不只是 URL：**正文变了也算换页**。SPA 与问卷站（blinkist 那类）每步都在同一
    个 URL 上换内容 —— 只认 URL 的话，一个状态的 `when` 会盖住好几个页面，
    而 `when` 是在**进这个状态时**判的（`_applies`），判错了整组步骤被静默跳过。
    """

    def __init__(self, site_url: str = "", journey=None):
        self.pages: list = []
        self._used = {START_STATE}
        self._current: dict | None = None
        #: 记一页时**顺手往账本上说一句话**的去处（`note_page` 里那条「判据只有 URL」）。
        #: 为什么它在这儿而不是在 `note_page` 里现取：`_Pages` 自己够不着账本，
        #: 而这一句话的**出处**就是「记了这一页」那一刻 —— 挪出去就变成了「谁记得谁来记」。
        #: 不给（测试直接 `_Pages()`）= 不记账，其余行为一个字节不差。
        self.journey = journey
        #: 这次要探的那个站点的主机名（判「第一页是不是站点自己的页」用）
        self._site_host = _host_of(site_url)
        #: **最近一眼那页的原始签**（契约 §二 `sig_before` 就是它）。
        #: 它比 `current_key` 细：`key` 只比 url / title / 正文前 400 字（判「换没换页」），
        #: 而签是**全正文指纹 + 可见元素计数** —— 同一页上多出一个按钮、正文改一个字，
        #: `key` 说「没换页」，签说「变了」。这是有意的：换页与**变化**是两件事。
        #: 一眼都还没看过时是 `None`（=「看不见」，不是「空签」）。
        self.sig: dict | None = None



    @property
    def current_name(self) -> str:
        return self._current["name"] if self._current else START_STATE

    @property
    def current_key(self):
        """当下这一页的**签**（`note_page` 用的那把键）；还没记过任何一页时是 `None`。

        步拍拿它判「这一步之后页面有没有变」（§5.4 判据 2）—— **免费信息**：
        键是 `note_page` 早就在算的东西，比一下不额外发命令。
        """
        return self._current["key"] if self._current else None

    @property
    def current_model(self):
        return self._current["model"] if self._current else None

    def note_page(self, model: dict) -> str | None:
        """记一页。返回**上一个状态名**（说明换页了），第一页返回 None。"""
        if not isinstance(model, dict):
            return None
        # ⚠️ **签在早退之前更新**：下面第一个 `return None` 是「没换页」，可**没换页不等于
        # 没变**（同一页上多一个按钮、正文改一个字）。签是比 `key` 细的那把尺子，
        # 它必须**每一眼**都换新，否则「页面变没变」这个问题会被拿旧尺子量。
        self.sig = _raw_sig(model)
        key = ((model.get("url") or "").split("#")[0],
               _norm(model.get("title") or ""),
               _norm(model.get("page_text") or "")[:400])
        if self._current is not None and self._current["key"] == key:
            return None
        previous = self._current["name"] if self._current else None
        when = _when_for(model)
        #: ★ 2026-09-22（生成侧建议第 1 条，真产物里量到的）：**相邻两页 `when` 完全相等 ⇒
        #: 合并成一个状态**（复用上一页的名字）。
        #: 为什么必须合并：`when` 是产物重放时**唯一的门** ⇒ 两个状态同门，就会演出
        #: 「同一页上填做了、点却被跳过」（真产物 `gowizard-14/-15` 就是这么**半执行**的 ✗）；
        #: 合并之后那两步**同生共死**，绝不会一半做一半不做。
        #: ⚠️ 只在 `when` **非空且完全相等**时合并：`None`/空判据那两个（「这一页不设门」）
        #: 不许拿它们当相等的钥匙 —— 那会把两页不相干的步揉进一组。
        same_gate = (self._current is not None and bool(when)
                     and self._current.get("when") == when)
        entry = {
            "name": self._current["name"] if same_gate else self._unique(_slug(model)),
            "when": when,
            "key": key,
            "model": model,
            "url": model.get("url") or "",
            "title": model.get("title") or "",
        }
        self.pages.append(entry)
        self._current = entry
        self._note_if_the_when_is_bare(entry)
        return previous

    def _note_if_the_when_is_bare(self, entry: dict) -> None:
        """这个状态的判据**只有 URL** 时，往账本上说一句（带状态名）。

        ⚠️ 为什么要在**记下这一页的那一刻**说：这句话说明的是「生成期没钉出正文判据」，
        而它唯一的出处就是 `_when_for` —— 挪到收尾时再扫一遍，等于把同一件事的判据
        在第二个地方重写一遍，两处早晚会漂（这句就是 `WHEN_TEXT_DROPPED` 那个常量的
        意思，一个字都不改地搬过来）。
        """
        when = entry.get("when")
        why = when.get("text_why") if isinstance(when, dict) else None
        if why and self.journey is not None:
            self.journey.note("「%s」这个状态：%s" % (entry.get("name") or "", why))

    def _unique(self, stem: str) -> str:
        name = stem
        index = 2
        while name in self._used:
            name = f"{stem}-{index}"
            index += 1
        self._used.add(name)
        return name


def _when_for(model: dict) -> dict | None:
    """一个状态的 `when`：**进这个状态时**那一页的判据。

    `url_contains` 用**稳定前缀**（`_stable_url`）：先去掉 query 与 fragment —— 重放时 query
    常常不一样（utm、step 号、A/B 参数）；再把**看着像轮换码的末段**丢掉 —— 真站实测
    （homebuddy）`…/walk-in-showers/cr640` 重放时是**同一个页面的轮换变体** `…/gt1791-1`，
    钉整条 path 的 `when` 一条都不成立、7 步**全被跳过**，而 `when` 存在的理由正是
    「防 A/B 变体、防步骤增减」。`when` 判太严的后果**永远是**整组步骤被静默跳过。
    正文那一段是从这一页的 `page_text` 里**取的原文** —— 它是当时那一页的**子串**，
    所以必然成立（`when_holds` 在生成时就会验一遍）。

    ⚠️ **正文那半条钉不出来的时候要说出来**：返回的 `when` 上带一句 `text_why`
    （见 `WHEN_TEXT_DROPPED`）。这是**有损**的（判据少了一半），而有损必须说 ——
    不然产物上「只有 URL」与「判据本来就只有 URL」长得一模一样，读的人分不出
    「查过了，只有 URL 稳」和「没钉出来」。**不许**悄悄退化成只有 `url_contains` 一条。
    """
    when: dict = {}
    url = _stable_url(model.get("url") or "")
    if url:
        when["url_contains"] = url
    text = _norm(model.get("page_text") or "")
    snippet = _snippet(text)
    if snippet:
        when["text_contains"] = [snippet]
    if not when:
        return None
    if snippet and when_holds(when, model):
        return when
    # ── 退化成「**只有 URL**」那一条 ────────────────────────────────────
    # ⚠️ 这一支是**有损**的（判据少了一半，站点改文案就整组跳过）⇒ **有损必须说**
    #    （Global Constraints：没有静默的路径）。所以不是返回一个光秃秃的
    #    `{'url_contains': …}`，而是**带上那句为什么**（读产物/日志的人靠它分辨
    #    「查过、只有 URL 稳」与「没钉出来」）。
    if not url:
        return None
    why = WHEN_NO_TEXT_WHY if not snippet else WHEN_TEXT_UNVERIFIED_WHY
    return {"url_contains": url, "text_why": WHEN_TEXT_DROPPED % why}


def _stable_url(url: str) -> str:
    """判据里用哪一段地址：**稳定前缀**。

    去掉 query 与 fragment，再把**看着像轮换码的末段丢掉**（`…/walk-in-showers/cr640` →
    `…/walk-in-showers/`）。两条底线，都是为了别把判据弄成「谁都能命中」：

    - **path 只有一段时绝不动它**（`…/cr640` 保持原样）—— 切了就等于拿主机名当判据
    - 末段**不像**轮换码时原样留着（`/checkout`、`/walk-in-showers`、`/funnel` 是全路径）

    ⚠️ 为什么放松 URL 不会让状态匹配到无关页面：`when_holds` / 产物的 `_applies` 要求
    **URL 与文本两条都命中** —— 鉴别力在文本那一侧，URL 这一侧只需要挡住「另一条路」。
    放宽只影响同一页的**变体**（真站实测的那件事），而这正是 `when` 存在的理由。
    """
    bare = (url or "").split("#")[0].split("?")[0]
    scheme, sep, rest = bare.partition("://")
    if not sep:
        return bare                       # 不是 http(s)://host/… 的形状：原样返回，不猜
    authority, slash, path = rest.partition("/")
    if not slash:
        return bare                       # 连 path 都没有（`https://host`）
    segments = [s for s in path.split("/") if s]
    if len(segments) < 2 or not _id_like(segments[-1]):
        return bare
    return f"{scheme}://{authority}/" + "/".join(segments[:-1]) + "/"


def _id_like(segment: str) -> bool:
    """这一段像不像**轮换的 id / 短码**（同一页每个变体换一个：`cr640` / `gt1791-1` / `12345`）。

    判据：整段是字母数字（允许 `-_.` 分隔）**且至少含一个数字**。
    只有字母的那种（`checkout`、`walk-in-showers`、`index.html`）是**真 slug**，
    不是轮换码 —— 切掉它们等于把判据退化成主机名，那比钉太死还糟。
    """
    seg = str(segment or "").lower()
    return bool(_ID_LIKE_RE.fullmatch(seg)) and any(c.isdigit() for c in seg)


#: 页面正文开头那些**不稳定的装饰字符**（广告占位、分隔线、装饰性下划线……）。
#: `observe` 的 page_text 开头常常是它们（真站实测：`___ The listings featured are…`，
#: 那个 `___` 是广告位的占位符），而**它们会变**（广告加载完就没了）。
_SNIPPET_JUNK = "_\u2014-\u00b7*| \t\r\n"


def _snippet(text: str) -> str:
    """一个状态的判据里带哪一段正文（从这一页的 page_text 里**取原文**）。

    ⚠️ 从开头取，但要**跳过开头那些装饰字符**（真站实测，2026-09-17 第七轮）：
    某站首页的 page_text 是 `___ The listings featured are compensated and…` ——
    那个 `___` 是广告位的占位符，第二次跑页面时它没了 →
    判据要含「___ The listings…」而页面上是「The listings…」→ **整组步骤被跳过**，
    而两句话**明明是同一句**。取判据的时候把开头那串装饰字符削掉，这个坑就没了。
    """
    text = str(text or "").lstrip(_SNIPPET_JUNK)
    head = text[:WHEN_SNIPPET_CHARS]
    if len(text) > WHEN_SNIPPET_CHARS and " " in head:
        head = head.rsplit(" ", 1)[0]           # 别把一个词从中间切断（读起来是半截话）
    return head.strip()


def _drop_incidental_start_when(pages: list, journey: Journey) -> None:
    """起点那一页是**旁枝**时，撤掉它的 `when`。

    判据：它的主机名与**后面每一页**都不同 → 它不是站点自己的页，而是「我们碰巧从那儿开始」
    （真站实测：Bit 的**工作台页** `console.bitbrowser.net/…?id=…&port=…`）。
    那种页的 `when` 会钉住「那一刻那个窗口的首页」，而里面的 `?id=…&port=…`
    **每开一次窗口都不一样** —— 自测换的是**干净窗口**（R-F1）→ 判据不成立 →
    起点那组的 `goto` **一步都没轮到** → 后面全部静默跳过（实测 0 执行 / 29 跳过）。

    为什么要等到整趟走完才判：**要在那一刻知道「后面那些页长什么样」**。
    从入口开跑的探索（第一页就是站点自己的页）不受影响 —— 它的主机名与后面一致，判据照旧。
    """
    if len(pages) < 2:
        return
    first_host = _host_of(pages[0].get("url") or "")
    if not first_host:
        return
    if any(_host_of(pg.get("url") or "") == first_host for pg in pages[1:]):
        return
    pages[0]["when"] = None
    journey.note(
        "起点那一页（%s）与后面**每一页**都不同源 —— 它是「我们碰巧从那儿开始」的旁枝，"
        "不是站点自己的页。所以它的状态**不设判据**（那种页的地址每开一次窗口都不一样，"
        "设了判据会在换窗之后把起点那组步骤整组跳过）。" % first_host)


def _host_of(url: str) -> str:
    """地址里的主机名（小写）；取不出来给空串。**不猜**：不是 http(s) 就返回空串。"""
    text = str(url or "").strip()
    scheme, sep, rest = text.partition("://")
    if not sep or scheme.lower() not in ("http", "https"):
        return ""
    return rest.split("/")[0].split("?")[0].split("#")[0].lower()


def _slug(model: dict) -> str:
    url = (model.get("url") or "").split("#")[0].split("?")[0].rstrip("/")
    tail = url.rsplit("/", 1)[-1]
    stem = tail.rsplit(".", 1)[0] if "." in tail else tail
    slug = _snake(stem) or _snake(model.get("title") or "")
    return slug or FALLBACK_STATE_NAME


# ─────────────────────── 小工具 ───────────────────────


def _norm(text: str) -> str:
    """与 `cdp observe` 的 page_text / 产物的 `page_signature()` **同一口径**。"""
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _snake(text: str) -> str:
    return re.sub(r"[^0-9a-z]+", "_", str(text or "").lower()).strip("_")


def _title_of(model: dict) -> str:
    return (model or {}).get("title") or (model or {}).get("url") or "没标题的页面"


def _hints_block(hints) -> str:
    """人另外交代的那几句 —— **只在有人说了话时才是一段**（没人说 ⇒ 空串，一个字节都不多）。

    ★ 为什么要有它（2026-09-22 真事）：运营在面板上把步骤**写得很细**，可新站那条路
    **一个字都没收到** —— 那条路的稿是「账本 → `template.render`」**算**出来的，
    而模型在那条路上**只有探路这一处有判断力**；人的话原来只喂给「修站出补丁」那条路
    （`fix.patch_user` 那一路）。运营看到的就是「它完全没按我的来」。
    接到这儿之后：探路**照人说的走** ⇒ 账本记的就是那套 ⇒ 渲染出来的稿自然也照那套。

    ⚠️ 措辞是**命令式**（「这就是规格，照它来」）：这几句是**规格**，不是背景资料。
    ⚠️ 改这一段没关系（它不在那条「逐字节钉死」的断言里 —— 只有**没人说话**那一条才钉）；
    但**别**把它掺进没计划那一版的固定部分，那会把 B4 那颗钉子撞掉。
    """
    said = [str(h).strip() for h in (hints or []) if str(h or "").strip()]
    if not said:
        return ""
    return ("\n**人另外交代的（这就是规格，照它来 —— 顺序、选择器、等待都按他写的）**：\n"
            + "\n".join("- " + one.replace("\n", "\n  ") for one in said))


def _brief(url: str, goal: str, budget: Budget, plan: "plan_module.Plan | None" = None,
           hints=None, success_text: str = "") -> str:
    """开场白（模型的 user 消息）。**有计划 / 没计划是两版**（§2.2）。

    ⚠️ 没计划那一版**与今天逐字节相同** —— 它是 B4 那条判据钉的东西，
    `tests/test_browser_agent.py` 把**今天那串字节硬编码**在断言里。
    所以这一支**不许**顺手改措辞：想改就先去改那颗钉子（偷偷漂 = 自由模式的行为悄悄变了）。
    ⚠️ `hints`（人说的话）是**追加**的：没人说 ⇒ `_hints_block` 回空串 ⇒ 上面那句一字不差。
    """
    free = (f"目标站点：{url}\n要做的事：{goal}\n"
            f"（你最多走 {budget.max_steps} 步、{budget.max_rounds} 轮。"
            f"现在这个浏览器窗口可能停在别的页上，先确认自己在哪。\n"
            f"⚠️ **能回答了就直接停下来说**（不调工具就是结束）—— 一直调工具会把预算耗光，"
            f"那一次你的结论一个字都留不下来。\n"
            #: ★ 2026-09-22 真事（`job-76fe990d4d62`）：24 步里只看了 **5 眼**，提交之后
            #: 没再看 ⇒ 判据**只在「看一眼」（observe）读到的正文里找** ⇒ 那一趟被判
            #: 「没见到成功文案」（字面为真、但根因是**没看**）。这一句就是让模型知道
            #: 「看」不是可选项。
            f"⚠️ **每一次「提交 / 继续 / 换页」之后，都要再看一眼那一页**："
            f"系统只认**你「看一眼」（observe）读到的正文** —— 不看，这一趟就按「没走到成功」算。)")
    if plan is None or not plan.actionable():
        return free + _success_block(success_text) + _hints_block(hints)
    return _planned_brief(url, goal, plan, budget) + _success_block(success_text) + _hints_block(hints)


def _success_block(success_text: str) -> str:
    """**「什么算成功」那串字本身**（TDD 探针量出来的缺项，2026-09-22）。

    为什么它必须进开场白：判据是**人去页面上找那串字**、而系统只在 `observe` 读到的正文里找。
    开场白里原来**一个字都没提它** ⇒ 模型既不知道要找什么、也不知道**找到就能收摊** ——
    真事 `job-76fe990d4d62`（24 步只看 5 眼 ⇒ 判据扑空 ⇒ 自动重探 ⇒ 空转、没产物）。

    ⚠️ 没人给判据（空串）⇒ **一个字节都不加**（那份「没计划那一版逐字节相同」的钉子因此不动 ✓）。
    """
    text = str(success_text or "").strip()
    if not text:
        return ""
    return ("\n⚠️ **什么算成功**：页面上出现这串字就算成 —— 『%s』。"
            "**见到它就可以收摊**（那之后每一次点击都可能是重复提交）。" % text)


def _planned_brief(url: str, goal: str, plan, budget: Budget) -> str:
    """有计划那一版：**清单（原话）+ 原文（一字不删）+ 三条走法**（§2.2）。

    三样缺一不可：

    - **清单**是「应该到这儿」的检查点（不是「第 3 步点 X」那样的脚本）。号**原样**搬
      （描述可能从 0 开始、可能跳号，重编就与人的话对不上了）；
    - **原文**里夹着**约束**（`禁止点击: Cookie Policy,Privacy Policy,Terms`）——
      只给清单等于把运营明写的禁区吞掉，模型就会去点它；
    - **走法**是这套系统与模型的**约定**：位置标记长什么样（系统**只读那个**）、
      矛盾要怎么说（系统**只认那个说法**）—— 约定不写清楚，`plan_ledger` 就只能是空的。

    ## 相对自由版，哪些东西**带着**、哪些**有意不带**（不许静悄悄，逐条写在这）

    - **带**：预算与「窗口可能停在别的页上」（那是自由版第二行的括号）。计划版**没有任何
      别的地方**覆盖它 —— 模型不知道自己的上限，而「停得早」这条收益论证正建立在预算上。
    - **带**：`goal`（换一个标签）。标签「要做的事：」不能用，因为它与自由版那版**长得一样
      就等于说要走自由那条路**；但意图本身一个字都不能掉：**修站那条路的 `raw` 是失败证据、
      `goal` 才是意图**，掉了它模型就不知道这一趟要摸清什么。
    - **有意不带**：自由版收尾那句（「能回答了就直接停下来说」）—— `_SYSTEM` 规矩 6
      逐字覆盖了它（「别再调工具，用一段话说明……没有 done 这个工具」）。
    """
    checklist = "\n".join(f"【第 {s.n} 步】{s.text}" for s in plan.steps)
    return (
        f"目标站点：{url}\n"
        f"这一趟要摸清的是：{goal}\n"
        f"（你最多走 {budget.max_steps} 步、{budget.max_rounds} 轮。"
        "现在这个浏览器窗口可能停在别的页上，先确认自己在哪。）\n\n"
        "人给了**一份检查点清单**（下面两段都是运营的原文，一个字没改）。\n\n"
        "清单 —— **这是一条走法的样子，不是站点的结构**：分支站上走到哪儿算哪儿，"
        "**少走几步、多走几步都是正常的**，别为了凑步数去点清单和原文里都没有的东西。\n"
        f"{checklist}\n\n"
        "原文（一字未删；里面的**约束**同样算数，尤其是「禁止点击」那种）：\n"
        f"{plan.raw}\n\n"
        "怎么走（三条）：\n"
        "1. **一步一确认**：做一个动作就看一眼页面（observe / diff），确认自己到了清单上的第几步，"
        "再决定下一步 —— 别一口气点完。\n"
        "2. **每轮开头用 `【第 k 步】` 说自己在哪一步**（k = 清单上的号）。位置**只认这个标记**："
        "没报，系统就当你没说、位置停在原地；跳着报也照实报（这一趟少走几步是正常的）。\n"
        "3. **与页面不符就当场说出来**，照这个说法：`【第 k 步】描述说 …，页面上是 …` —— "
        "只说看见什么，**不判**是什么原因（定性归人）。\n"
        "⚠️ **描述没说的别点** —— 清单和原文里都没提到的元素，别顺手去动它。")


def _emit(on_step, step: dict) -> None:
    if on_step is not None:
        on_step(step)


def _ms(t0: float) -> int:
    return int((time.time() - t0) * 1000)
