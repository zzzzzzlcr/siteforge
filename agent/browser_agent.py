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

import ast
import hashlib
import inspect
import json
import pathlib
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from . import events, lessons, llm, plan as plan_module, shots, template, tools

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
#: ★★ **2026-10-08 又放了一次（80 / 100 → 160 / 200）—— 上面那句「偏在哪一边我没量」量到了**：
#: 放开之后的第一趟真站探路就响了，而且**响在估计的上方** ⇒ 那个估计是**偏低**的。
#: 样本：`job-42daa38499e9`（hormn.com，build）—— 13 题的问卷 + 注册漏斗，账本记
#: `rounds=86 / steps=95`、`stop_reason=budget_rounds`、`end_reason=explore_unfinished`、
#: `delivered=false`（**一个 py 都没有**）。⚠️ 它**不是「卡住」**：95 步里**只有 1 步**
#: 真的没做成（一个选择器没找到），一路在推进，最后已经走到 app 页 ⇒ 是**路比预算长**。
#: ⇒ 用户 2026-10-08 裁定「放大点」（原话）⇒ **160 / 200**：≈1.9 倍于实测最坏 86，
#: 且仍在 `<= 4 × 64 = 256` 那条「放开 ≠ 取消」的线内。
#: 用例：`tests/test_budget_calibration.py::
#: test_the_budget_also_covers_a_real_long_funnel_not_just_the_gowizard_one`。
#:
#: ⚠️ **这一放，松开的是【唯一】那根刹车**（下一个人再调它之前必须知道这一条）：
#: `stall_limit`（停滞判据）**在生产路径上一次都不参与** —— `explore` 只有拿到 `plan`
#: 才建那个 `watch`（`browser_agent.py` 里 `if plan is not None and plan.actionable()`），
#: 而生产那条路上**没有任何一处**交得出 `plan`：图那一跳（`graph._explore` →
#: `deps.explore(...)`）不带 `plan=`，服务那一层（`service._explore_for` 里的
#: `run(url, goal, budget=None, …)`）连这个参数都没有，而 `Deps.explore` 的默认值
#: `browser_agent.explore` 的 `plan` 默认就是 `None`。
#: ⇒ 20 → 80 → 160 **不是**「两根上限里松开了一根」，是**把那唯一的一根一次次翻番**：
#: 一个卡住的模型在一趟里最多能对**真站**做 160 个会改页面的动作（原先 20 个）。
#: 墙钟（四趟账本实测 2.5 / 3.35 / 4.0 / 4.25 秒每轮；hormn 那趟 86 轮 224 秒 ≈ 2.6 秒/轮）：
#: 20 轮 ≈50–85 秒 → 160 轮 **≈7 分钟**。
#: 用例：`tests/test_budget_calibration.py::
#: test_the_production_path_never_hands_explore_a_plan_so_the_round_budget_is_the_only_brake`。
#:
#: ⚠️ **「模型自然要用多少轮」这个数今天仍然没量到**（量它要跑一趟真模型，那一轮不许）。
#: 上面 64 是个**按手头那几条（偏的）样本折出来的估计**（见上），不是「模型自然要用多少」。
#: 什么时候再校准：**下一趟把 160 也烧光的真站探路**（读 `attempts.jsonl` 的 `rounds`）
#: —— 那才是「这个数还不够」的证据。⚠️ 反过来**不响也不能说明松**（可能只是没碰到长漏斗）。
DEFAULT_MAX_STEPS = 200
DEFAULT_MAX_ROUNDS = 160

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

#: ★ 2026-09-28（缺陷单《产物判据跨元素拼接》）：判据那一段**跨了两个元素**时说的话。
#: 有损就必须说 —— 读产物/日志的人要能分辨「用的是哪一个元素的哪句话」。
WHEN_TEXT_SPLICE = ("这一段判据原来是**跨两个元素拼**出来的（在页面上根本不是一句话）——"
                    "已经换成落在**单个元素**里的那一段：%r")
#: 剪出来那一段**跨了两个元素**、又换不出单元素的一段时说的话（这一组只剩 URL）。
#: 与 `WHEN_TEXT_SPLICE`（换成了另一段）是**两件事**：一个是「改用了哪段」，
#: 一个是「这段没法用 ⇒ 判据少了一半」—— 混成一句，读的人就分不出该不该动手。
WHEN_TEXT_SPLICED_AWAY = ("剪出来的那一段是**跨两个元素拼**的（页面上根本没有这一句话）：%r；"
                          "而这一页**找不到**任何单个元素能顶替它")

#: ★ 2026-10-08：剪判据时**躲开了「前面那些页上也有的那段」**时说的话。
#: 与 `WHEN_TEXT_SPLICE`（跨元素拼接）是**两件事**，得分开说：那一句讲的是「取的时候取歪了」，
#: 这一句讲的是「取到的这段**别的页上也有** ⇒ 门分不出两页」。读的人靠它决定要不要动判据。
WHEN_TEXT_AVOIDED = ("原来剪到的那段（%r）在**前面走过的那几页**上也有 ⇒ 门分不出两页 ——"
                     "已经换成这一页自己的一段：%r")

#: ★ 2026-10-09：判据剪在了**公用那一层**上（页头/导航/页脚/弹窗）时说的话。
#: 与 `WHEN_TEXT_AVOIDED`（躲开「别的页上也有的那段」）**不是同一件事**：那一句讲的是
#: 「按别的页避让」，这一句讲的是「**那段本身就是壳**（页头那种全站每页都有的）——
#: 改从这一页自己的话里剪了」。读账本的人靠它分辨「是避让换的」还是「是壳换的」。
WHEN_TEXT_FROM_PUBLIC = ("这一段判据原来剪在**公用那一层**上（%r，页头/导航那类全站每页都有的）"
                         "——已经改从这一页**自己**的话里剪：%r")

#: ★ 2026-10-08（用户：「探路的时候告诉我们这个对应的文本到底是在啥元素找到的」）：
#: 判据那一段**是从哪个元素剪的**（`标签@区域`，如 `a@body`、`h1@main`）。
#: 为什么光有那段文字不够：`Skip to main content` 可以是页头那个无障碍跳转链接
#: （全站每页都有 ⇒ 门分不出两页），也可以是正文里的一句话 —— 文字本身看不出来，
#: 回头只能再跑一趟真站去猜。写这一句，读账本的人一眼看得见。
WHEN_TEXT_FROM = "这一段是从「%s」这个元素剪的"


def _other_pages_text(models) -> list:
    """**别的页**上有什么文字（归一化、小写；一页一条）—— 剪判据时拿它避让。

    ⚠️ 两边都要收：`page_text`（工具截到 600 字）+ **逐元素正文**（实测那份真回执
    45 条 / 1844 字，比前者厚三倍）。只比一个会漏，而漏的后果是「以为躲开了、其实没躲开」。
    """
    out = []
    for model in models or []:
        parts = [_norm((model or {}).get("page_text") or "").lower()]
        parts += [_norm(str(t or "")).lower()
                  for t in ((model or {}).get("node_texts") or []) if str(t or "").strip()]
        out.append(" \n ".join(p for p in parts if p))
    return out


def _one_page_text(model: dict) -> str:
    """这一页**自己**有什么文字（归一化、小写）—— **正例那一向的尺子**。

    就是 `_other_pages_text` 的单页版（一处口径：它俩调的是同一段实现）。

    ★ 2026-10-09：为什么正例要用它、而不是 `when_holds` 那把 600 字的尺子 ——
    这一问的真实答案在**产物那一侧**：`template.page_signature()` 的 docstring 原话是
    「**不裁长度** —— 成功文案可能在第 600 字之后」（trace 里落的那份才裁到 600）。
    也就是说**判据落在 600 字之后照样成立**。生成侧只看 `page_text`（工具硬切 600 字）
    等于拿一把**比产物更窄**的尺子去验正例 ⇒ 把本来成立的好判据判成「不成立」⇒
    重挑手上没牌（`_unique_snippet_for` 的 ③），整趟被 IR 砍死。
    真账本：`job-f86ac12fda15`（shorecrestwpb 五趟）/ `job-c8730acb27c5`（policyquail 两趟）。

    ⚠️ **只给正例用**。反面（`_gates_that_hold_elsewhere` 拿 `when_holds` 比**别的页**）
    有意仍是那把窄尺子 —— 放宽反面 = 少拦坏判据，那是**另一刀**，没动。
    """
    return next(iter(_other_pages_text([model])), "")


#: 属于「公用那一层」的区域（全站每页都有的壳）：导航 / 页头 / 页脚 / 侧栏 / 弹窗。
#:
#: ★ 2026-10-09（用户「有没有啥能提取关键字的插件不用一次性扔给AI的」）：**不用引插件** ——
#: 区域那一格 `cdp observe` **早就逐条带回来了**（`node_elements` 的 `标签@区域`），
#: 而它是在浏览器里**当场算的纯函数**（`tools/cdp/internal/observe.go` 的 `region()`：
#: landmark 标签优先、class 派生兜底）：确定性、零依赖、零模型、零额外调用。
_CHROME_REGIONS = frozenset({"nav", "header", "footer", "aside", "dialog"})


def _is_chrome(kind: str, *, has_main: bool) -> bool:
    """这一条（`标签@区域`）算不算**公用那一层**。

    ⚠️ `body` 是**混的**：没有 landmark 祖先的元素都落到它 —— 跳转链接、cookie 小条
    是 `a@body` / `div@body`（真样本：kotzheating 首页前四条全是），而**整页没有 `<main>`**
    的站点，正文本身也在 `body` 里。⇒ **有 `main` 时** `body` 那些就是壳，一起算公用；
    没有 `main` 时留着。丢正文比留着壳坏得多。
    """
    region = str(kind or "").split("@")[-1].strip().lower()
    if region in _CHROME_REGIONS:
        return True
    return region == "body" and has_main


def _has_main(kinds) -> bool:
    """这一页有没有 `main` 这个 landmark（决定 `body` 那格怎么算，见 `_is_chrome`）。"""
    return any(str(k or "").split("@")[-1].strip().lower() == "main" for k in (kinds or []))


def _own_word_nodes(model: dict) -> list:
    """这一页**自己**的话（丢掉公用那一层的元素；保序）。

    ★ 2026-10-09：为什么要有这一格 —— 判据那一侧活在一个 **600 字**的窗口里
    （`page_text` 是工具**每帧切 600 字**拼的，`observe.go` 的 `pageText.slice(0, 600)`），
    而**页头 + 导航常常先把那 600 字占满** ⇒ 第一个状态又没有「上一页」可比（`avoid=""`）
    ⇒ 判据必然从开头剪 ⇒ 剪到的全是全站公用文案 ⇒ 撞车 ⇒ IR 拦下整趟。
    实测（2026-10-09 live，`kotzheating.com` 首页那一眼，402 条）：
    `nav 272 / main 98 / header 20 / body 6 / dialog 3 / footer 3`，**`main` 的第一条排在第 299 条**，
    而 `page_text` 那 600 字全是壳。

    ⚠️ 元素身份那一格与正文**逐条对应**（同一个循环产出的）。对不上时**不筛**
    （宁可多留壳，也不许错位 —— 错位比没有更坏，同 `_summarize` 里那条纪律）。
    """
    nodes = model.get("node_texts")
    if not isinstance(nodes, list) or not nodes:
        return []
    kinds = model.get("node_elements")
    if not isinstance(kinds, list) or len(kinds) != len(nodes):
        return [str(t) for t in nodes if str(t or "").strip()]
    has_main = _has_main(kinds)
    return [str(t) for t, k in zip(nodes, kinds)
            if str(t or "").strip() and not _is_chrome(k, has_main=has_main)]


def _also_in_chrome(text: str, model: dict) -> bool:
    """这段字在**这一页的公用区域**里也有吗。

    ⚠️ ★ 2026-10-09（真站当场逮到，`job-c4c0f4ee0ad7`）：`/contact/` 那道门定成了
    `'Contact Us'` —— 它**确实**是从 `h1@main` 剪的（合法），可**同一个字符串也是导航项的
    标签**：那一页的逐元素正文里它出现在**两个 `a@nav`** 上 ⇒ 这道门在任何带「Contact Us」
    导航的页面上都成立，而**没人喊** —— 反面那把尺子（`when_holds`）只看得到别的页那
    **600 字**，导航不在里面。

    ⚠️ 这一问与「反面那把尺子太窄」**不是同一件事**、也**不取代**它：这一问是**局部**的
    （只看自己这一页）、**免费**的，当场问得出来；它只说「这段字不是这一页独有的**话**」。
    """
    low = _norm(text).lower()
    nodes = model.get("node_texts")
    kinds = model.get("node_elements")
    if not low or not isinstance(nodes, list) or not nodes:
        return False
    if not isinstance(kinds, list) or len(kinds) != len(nodes):
        return False
    has_main = _has_main(kinds)
    return any(_is_chrome(k, has_main=has_main) and low in _norm(str(t or "")).lower()
               for t, k in zip(nodes, kinds))


def _cut_landed_on_chrome(model: dict) -> bool:
    """`page_text` 的开头那一截，落点是不是**公用那一层**（剪点就在那儿）。

    判法：拿 `page_text` 前 24 个字去认它落在哪个元素里。认不出来（跨元素拼、
    或那一格没有 / 对不上）⇒ **不算**落在壳上 —— 这一问只用来决定「要不要改道」，
    认不准就不动（宁可照旧，也不许瞎改）。
    """
    text = _norm(model.get("page_text") or "")
    nodes = model.get("node_texts")
    kinds = model.get("node_elements")
    probe = text[:24].lower()
    if not probe or not isinstance(nodes, list) or not nodes:
        return False
    if not isinstance(kinds, list) or len(kinds) != len(nodes):
        return False
    idx = next((i for i, t in enumerate(nodes) if probe in _norm(str(t or "")).lower()), None)
    if idx is None:
        return False
    return _is_chrome(kinds[idx], has_main=_has_main(kinds))


def _snippet_in_one_node(snippet: str, nodes, others=(), kinds=()) -> tuple:
    """把跨元素拼出来的 `snippet` 换成**落在单个元素里**的那一段（换不了给空串）。

    怎么挑：与这段判据**共有最长一串字**的那个元素（≥8 字才算数），
    然后取**它自己的开头**那一段（口径与 `_snippet` 完全一样：≤`WHEN_SNIPPET_CHARS`、
    落在词边界上、跳过开头装饰字符）。

    为什么不是「取共有的那串字」：与原件共有最多的那一段在真站上常是**半句**
    （`Schedule your residential`），而判据要的是**这一屏独有的那句话** ——
    元素自己的开头才是「它自己的话」（`Schedule your residential clipboard energy aud`）。
    ⚠️ 为什么要「≥8 字」这个门槛：短判据的鉴别力等于零（一页上到处都成立），
    那会让**每一组**的门都成立 —— 比整组跳过更坏（它会误做）。

    ★ 2026-10-08：`others`（**前面走过的那几页**）里有的人话，**优先不选**。理由见
    `WHEN_TEXT_AVOIDED` 那条：页头那句跳转链接**全站每页都有**，而它恰恰是与上一页
    「一个共同前缀都没有」时（上一页是 reCAPTCHA 那种）剪到的那一段 ⇒ 门分不出两页。
    两条纪律：
      · **只是优先，不是硬拒**：全都撞上时退回原来那一条（今天的行为一个字不改）——
        硬拒会把这一组打成「只有 URL」，那比一条不够独有的门更坏（整组静默跳过）。
      · 比的是**真要写进判据的那一段**（`_snippet(节点)`，≤48 字），不是整个节点的全文 ——
        长句子全站重复的概率本来就低，拿它当判据的是那 48 字的窗口。

    返回 `(挑中的那一段, 被躲开的元素正文, 挑中那条**是啥元素**)` —— 后两个没有时是空串。
    ⚠️ 「是啥元素」（标签@区域）由 `kinds` 递进来（与 `nodes` 逐条对应，来自 `observe` 的
    `node_elements`）：它是**「这一段是从哪个元素剪的」唯一的证据**，写在判据那句话里
    （见 `WHEN_TEXT_FROM`）—— 读账本的人不用再跑一趟真站去猜。
    """
    want = _norm(snippet)
    elsewhere = _other_pages_text(others)
    kinds = [str(k or "") for k in (kinds or [])]
    if len(kinds) != len(nodes or []):
        kinds = [""] * len(nodes or [])          # 对不上就别猜（缺了比错位好）
    best_node, best_len, best_el = "", 0, ""
    uniq_node, uniq_el = "", ""
    #: ★ 2026-10-13：**短、但只在已知的页面上出现过**的那一个（见函数尾那段）。
    short_node, short_el = "", ""
    for pos, text in enumerate(nodes or []):
        node = _norm(str(text or ""))
        if not node:
            continue
        run = 0
        for start in range(len(want)):
            n = 0
            while (start + n < len(want) and n < len(node)
                   and want[start + n].lower() == node[n].lower()):
                n += 1
            run = max(run, n)
        if run > best_len:
            best_node, best_len, best_el = node, run, kinds[pos]
        #: 备选：**按顺序**取这一页第一个「别处没见过」的元素（不是最像的那个）。
        #: 为什么按顺序而不是按像不像：这会儿要的已经不是「判据原来出自哪个元素」，
        #: 而是「这一页上哪一段是它自己的」—— 页面上第一个独有元素就是那句话。
        if not uniq_node:
            cand = _snippet(node)
            if len(cand) >= _WHEN_REPICK_MIN_CHARS and not _on_other_pages(cand, elsewhere):
                uniq_node, uniq_el = node, kinds[pos]
        #: ★ 2026-10-13：**短的也算 —— 只要它在已知的页面上没出现过**（见下面那段注释）。
        if not short_node:
            cand = _snippet(node)
            if len(cand) >= SHORT_WHEN_MIN_CHARS and not _on_other_pages(cand, elsewhere):
                short_node, short_el = node, kinds[pos]
    if best_len < 8:
        return "", "", ""
    head = _snippet(best_node)
    if not _on_other_pages(head, elsewhere):
        return head, "", best_el            # 页头这段是本页自己的 ⇒ 照旧（今天的行为）
    if uniq_node:
        return _snippet(uniq_node), best_node, uniq_el
    #: ★ 2026-10-13（真事 `job-0bee2dd7903e` · healthinsurancewatch.com.au）：走到这儿说明
    #: 「≥8 字且别处没有」的一个都没有。原来**直接退回最像的那个**（`best_node`）—— 而它
    #: 恰恰是**已知在别的页上也有的**那一个（那趟退成了全站页脚）⇒ 这一屏的门在别页上照样
    #: 成立 ⇒ 判「分不开」⇒ 并组 ⇒ 收尾重挑 ⇒ 那一组整组跳过（6 步）。
    #:
    #: 这一问问错了：**判据能不能用，看的是「在已知的页里出现过没有」，不是「有多长」**。
    #: 长度只是它的**代理**（当年没有 `others` 才用长度顶着）。`others` 现在一直在传，
    #: 所以第二遍放宽到 `SHORT_WHEN_MIN_CHARS`，但**只收「别处没见过」的那些** ——
    #: 真事那一屏上，`Name`（4 字，全站只这一屏有）被 ≥8 挡住，而页脚（45 字，全站每页都有）
    #: 反倒当选。⚠️ 放宽的**只有**这条兜底：上面两条（≥8 的两支）一个字没动。
    if short_node:
        return _snippet(short_node), best_node, short_el
    return head, "", best_el                # 全页都撞上 ⇒ 退回原来那条（不硬拒，见上）


def _on_other_pages(cand: str, elsewhere) -> bool:
    """这一段**别的页上也有**吗（`elsewhere` = `_other_pages_text` 那一份）。"""
    low = _norm(cand).lower()
    return bool(low) and any(low in text_of for text_of in elsewhere)


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

#: ★ **经验库**（2026-09-23，用户提的：「把我们写的成功案例整理成知识库，给生成脚本的 AI 参考」）。
#: 接在**稳定层**（`_SYSTEM`）而**不是**简报（`_brief`）里：简报那条没计划的路被
#: B4 那条钉子**逐字节**钉死（`tests/test_browser_agent.py:1778`）—— 往里加东西就是
#: 偷偷把自由模式的行为改了；而 `_SYSTEM` 那边是**子串**断言，追加是安全的。
#: 两条纪律（有出处、短）写在 `agent/lessons.py` 的模块 docstring 里，改之前先读它。
_SYSTEM += lessons.block()


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

    ## ★ 2026-10-08 再放一次：80 / 100 → **160 / 200**

    **上面那条「偏在哪一边我没量」量到了**：放开之后的第一趟真站探路就响了，而且
    **响在估计的上方**（⇒ 那个 64 是偏低的）。样本 `job-42daa38499e9`（hormn.com）：
    13 题的问卷 + 注册漏斗，`rounds=86 / steps=95`、停因 `budget_rounds`、
    `end_reason=explore_unfinished`、**一个 py 都没有** —— 而它 95 步里只有 1 步真的
    没做成，一路在推进 ⇒ 是**路比预算长**，不是卡住。用户裁定「放大点」⇒ 160 / 200
    （≈1.9 倍于实测最坏 86，仍在「放开 ≠ 取消」那条线 `4 × 64 = 256` 之内）。

    ⚠️ **那四个折算率来自一个偏的子集 ⇒ 上面那个 64 是「估计」，不是「下界」**
    （复审 2026-09-20 §5④ 补的那条）：4 条样本**全是同一个站**（gowizard），而且
    **全是被 20 轮钉死的那一档** —— 能走完的早就走了（走完那几趟只用 2–4 轮）
    ⇒ 这个子集天然是「动作密度最低」的那一类。**偏在哪一边我没量**（要别的站、别的档的
    样本才量得出）⇒ 它是「按手头这几条折出来的估计」，**不是**一条从无偏样本上量出来的下界。

    ⚠️ **160 松开的是【唯一】那根刹车**（下一个人再调它之前必须知道这一条）：
    `stall_limit` 在生产路径上**一次都不参与**（`explore` 只有拿到 `plan` 才建那个
    `watch`，而生产那条路交不出 `plan` —— 逐条见模块顶上那段）。所以这里其实是
    **两个上限、一个在起作用**：实测每轮正好一次工具调用 ⇒ `max_rounds` 永远先到。
    ⇒ 20 → 80 → 160 **不是**「两根里松开了一根」（那个口径会低估代价）：墙钟
    ≈50–85 秒 → **≈7 分钟**（实测 2.5–4.25 秒/轮）。这条由
    `tests/test_budget_calibration.py::test_the_production_path_never_hands_explore_a_plan_so_the_round_budget_is_the_only_brake`
    钉着。

    ⚠️ **「模型自然要用多少轮」仍然没量到**（量它要跑一趟真模型）—— 160 是**那个估计**
    之上的余量，不是那个自然数。**再校准的时机**：下一趟**把 160 也烧光**的真站探路
    （`attempts.jsonl` 的 `rounds`）—— 那才是「这个数还不够」的证据。
    ⚠️ 反过来**不响也不能说明松**（可能只是没碰到长漏斗）。两种都要记下来。
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
    #: ★ 2026-09-30：这一趟资料（`--form-file`）里**真有的那几个键** —— 只用来给
    #: `FILLS[*].source` 起名（`template.library_key`）：资料里真有的那个名字才写进产物，
    #: 运行期就**直接命中**，不必再靠骨架的别名表兜（兜不到就静默掉随机池的那条路）。
    #: 拿不到（服务没给 / 文件读不到）⇒ 空列表 = **不知道**，一个字节都不动。
    data_keys: list = field(default_factory=list)
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
    #: ★ 2026-09-29（交接《骨架还缺什么》§1）：**判据分不开页**的那几条
    #: （`_gates_that_hold_elsewhere` 的产物，一个状态一条）。
    #: 为什么它要活到 `graph` 那一层：生成期验一遍只回答「这一趟账本有没有这个病」，
    #: 而**拦住产物**是 `draft` 那一格的事 —— 这一格就是两者之间那条线。
    #: ⚠️ 它是**账本自己的事实**（不是产物的），所以照旧会跟着 journey 进 checkpoint。
    gate_collisions: list = field(default_factory=list)

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
        alias = _same_address_same_state(self.steps)
        #: ★ 2026-10-09：**探路自己就作成过也做败过**的那些目标（见 `_not_load_bearing_clicks`）。
        #: 整趟算一次（要看全每一趟的记录才知道谁不稳）。
        flaky = _not_load_bearing_clicks(self.steps)
        #: ★ 2026-10-13：**东西是后出来的**那些状态（见 `late_states`）—— 它们那几步带上
        #: `late_element`，产物据此把「没找到」的等待预算给足（弹窗那类站：等的是定时器）。
        late = self.late_states()
        for step in self.steps:
            name = step.get("state") or START_STATE
            if name not in groups:
                groups[name] = []
                order.append(name)
            replay = _replay_step(step)
            if replay is not None:
                if replay.get("fill"):
                    replay["fill"] = alias.get(replay["fill"], replay["fill"])
                #: ★ 2026-10-09：两条都满足才标 —— ① 目标**没名字**（有名字的是漏斗的一部分，
                #: 它失败必须照样否决）；② 同一趟里**同一目标作成过也做败过**。
                target = replay.get("target") or {}
                if (replay.get("action") == "click" and not _has_a_name(target)
                        and _target_key(target) in flaky):
                    replay["auxiliary"] = True
                if name in late:
                    replay["late_element"] = True
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

        ⚠️ 与 `states()` **同一把尺子**：同一个状态里、同一个地址被记了两遍（名字不同）
        只留前面那个（见 `_same_address_same_state`）—— 两处口径不一样的话，产物里
        会留下一格**没有任何一步引用**的死条目。
        """
        alias = _same_address_same_state(self.steps)
        out: dict = {}
        for step in self.steps:
            info = (step.get("result") or {}).get("fill")
            if not info or info["name"] in alias:
                continue
            out[info["name"]] = {
                "name": info["name"],
                #: ★ 2026-09-30（用户「修下吧」）：这一格**是产物拿去资料里找真数据**的那个键。
                #: 页面字段名（`phone`）与资料库列名（`phone_number`）两边没有约定 ⇒ 在**这一处**
                #: 换成资料里真有的那个名字（`library_key`，只在真有它时才换；
                #: 资料不知道 ⇒ 原样 —— 老行为一个字不变，运行期的别名兜底也照旧留着）。
                "source": library_key(info["source"], self.data_keys),
                "kind": info["kind"],
                "label": info["label"],
                "target": step.get("target") or {},
                "fallback": list(info.get("fallback") or []),
                #: ★ 2026-10-13：**探路那趟在真站上真填进去、页面认了的那个值** ——
                #: 产物在「资料里没有这一格」时照它的**形状**编一个新的（`template._shaped_like`），
                #: 而不是走按国家分的池子。为什么：`proxy_country` 说的是**代理出口**在哪，
                #: 站点在哪是另一件事 —— 真事 `job-a5f74744a7f9`（US 出口 + 澳洲站）：池子给美国
                #: 手机形状 ⇒ 站方红字拒收、**提交那一下卡死**，而每一步日志都写着「填好了」。
                #: 只给 `kind == "value"` 的自由输入格（下拉/勾选的值是**页面上真有的**，见上）。
                "sample": (str(info.get("value") or "").strip() if info.get("kind") == "value"
                           else ""),
            }
        return out

    def late_states(self) -> set:
        """哪些状态的东西是**后出来的**（落到这一页的**第一眼**里没有那一格，后来有一步真用上了它）。

        ★ 2026-10-13（`job-4d12a95edf34` · innovatingcanada 的 Hustle 弹窗）：那一页的表格是
        **加载后约 25~31 秒**才弹出来的（触发是一次 25 秒的定时器）。产物据此把那一组的
        「没找到」等待预算给足（`template.LATE_ELEMENT_WAIT_SECONDS`）—— 而「该不该多等」
        这件事**不用猜**，账本里有两半证据：

          ① **第一眼里没有它**：那一页的第一条 `observe` 的 `page_fields` 里没有这一格；
          ② **后来有一步真用上了它**：同状态里某一步 `form` **填成过**，`result.selector` 就是它。

        两半都要。只有 ①（没出现过）不算 —— 那是「选择器压根不对」，给它加预算就是白等
        （生产上 249 趟自测日志里「没找到」等到头的是 28 次、等出来的是 2 次，正是这种占多数）。

        ⚠️ ②用的是**步自己**的凭据而不是「后面的眼里有没有」：真账本 `job-5ca190173123`
        整整一趟只有两行 `observe`（都是文章页那个下拉），弹窗的格**一眼都没进过**
        `page_fields` —— 只认眼的话这一趟会被判成「没证据」而漏掉，可它明明填成了两步。
        ⚠️ 判据的最细粒度是**状态**（账本每一眼只存**格**的清单，动作只有个数，见 `_summarize`）
        ⇒ 标的是整组（那一组里的提交按钮也是弹窗里的东西，该一起给足）。
        ⚠️ 归属走 `observed_pages()`（按账本自己的判据算归属，与 `states()` 同一把尺子）；
        某个状态一眼都没分到（同页两屏时后一屏常把两眼都收走）⇒ 退回**全趟第一眼**当基准。
        """
        #: 基准按**地址**收口，不按状态：同一页上常有好几屏（同意条一屏 / 弹窗一屏），
        #: 状态分得开、地址分不开 —— 而归到状态上会被「后一屏把两眼都收走」骗过去
        #: （真事 `job-b9653c77ecec`：那个状态的「第一眼」已经是**带弹窗**的那一眼 ⇒
        #: 判成「不晚」⇒ 长预算一格没挂上，那一趟是**蒙**过去的）。
        first_on_page: dict = {}
        for step in self.steps:
            if str(step.get("action") or "") != "observe":
                continue
            result = step.get("result") or {}
            key = _url_key(result.get("url"))
            if not key or key in first_on_page:
                continue
            got = {str((r or {}).get("selector") or "")
                   for r in (result.get("page_fields") or [])}
            got.discard("")
            first_on_page[key] = got
        #: 这个状态在哪一页上（`pages` 里那一格的模型记着地址）。
        where = {str((p or {}).get("name") or ""): _url_key(((p or {}).get("model") or {}).get("url"))
                 for p in self.pages}
        #: 兜底：地址认不出来（老账本 / 没记 url）⇒ 拿**全趟第一眼**当基准。
        run_first = next(iter(first_on_page.values()), set())
        used: dict = {}
        for step in self.steps:
            if str(step.get("action") or "") != "form":
                continue
            result = step.get("result") or {}
            if not result.get("fill"):
                continue              # 没填成的一步不算凭据：它没证明那一格存在过
            selector = str(result.get("selector") or "")
            if selector:
                used.setdefault(str(step.get("state") or START_STATE), set()).add(selector)
        return {state for state, sels in used.items()
                if sels - first_on_page.get(where.get(state, ""), run_first)}

    def observed_pages(self) -> list:
        """`[{state, url, text, fields}]` —— 账本里**真看过的那几眼**（每一眼一条）。

        只收 `observe` 那一行（同 R3 那条前提的口径：一页长什么样，只有「看一眼」说了算）。

        ⚠️ **归属按账本自己的判据算，不按 `step["state"]`**：那一格是**动手之前**抓的
        （`dispatch` 里 `pages.current_name`），于是「这一步发现了新的一页」那一行会被记成
        **上一页**的名字（实测：同一页的两次观测落进了两个组）。这里用的是 `when_holds`
        —— 与产物 `_applies` 逐条对齐的那一条判据，再按「正文也对得上」优先、后出现的页优先。
        真的谁也判不出来（没有一页的判据在这页上成立）⇒ 退回 `step["state"]`（老口径，
        一个字节不变）。
        """
        whens = {str(p.get("name") or ""): p.get("when") for p in self.pages}
        out: list = []
        for step in self.steps:
            if str(step.get("action") or "") != "observe":
                continue
            result = step.get("result") or {}
            model = {"url": result.get("url") or "",
                     "page_text": result.get("page_text_head") or ""}
            best, best_text = "", False
            for name, when in whens.items():
                if not name or not when_holds(when, model):
                    continue
                hit = bool(when.get("text_contains"))
                if not best or (hit and not best_text):
                    best, best_text = name, hit
            out.append({
                "state": best or str(step.get("state") or START_STATE),
                "url": str(model["url"]),
                "text": str(model["page_text"]),
                "fields": list(result.get("page_fields") or []),
            })
        return out

    def fields_by_state(self) -> dict:
        """`{状态名: [紧凑字段表]}` —— 这一组的页面上**见过哪些格**。

        从 `observed_pages()` 收（同一份归属，不另抄一份）：同一格（选择器/标签/hint 同）
        **只留一次** —— 同一个状态在不同时刻看了两眼，不该记成两格（`_field_id`）。
        """
        out: dict = {}
        for page in self.observed_pages():
            bucket = out.setdefault(str(page.get("state") or START_STATE), [])
            seen = {_field_id(r) for r in bucket}
            for row in page.get("fields") or []:
                key = _field_id(row)
                if key in seen or not any(key):
                    continue
                seen.add(key)
                bucket.append(row)
        return out


def when_holds(when, model: dict) -> bool:
    """一条 `when` 在**这一份页面模型**上成不成立。

    判据与产物里的 `_applies` **逐条对齐**（url 子串 + 正文里含任一段文字），
    因为「生成时的 `when`」与「重放时的 `when`」不是两件事：一条当时就不成立的 when，
    在重放时会让整组步骤**静默跳过**。

    ⚠️ ★ 2026-10-09 **更正**：「逐条对齐」说的是**算法**（那两条怎么与、怎么或），
    **不是取材宽度** —— 两者其实不一样，别把这句话读成「正文那份也一样」：
      · 这里读的是 `page_text`，而它是工具**硬切 600 字**的（`observe.go`）；
      · 产物那边 `page_signature()` 的 docstring 原话是「**不裁长度** —— 成功文案可能在
        第 600 字之后」。
    ⇒ **这一把比产物那把窄**。所以它只适合当**反面**的尺子（「别的页上也有吗」——
    窄尺子说「有」就一定真有；说「没有」则未必），**不许**拿它验正例
    （「自己这页上成立吗」—— 那会把排在第 600 字之后的好判据判成不成立）。
    正例那一向用 `_one_page_text`（正文 **加逐元素正文**）。这条区分是 2026-10-09
    真账本逼出来的（shorecrestwpb 五趟 / policyquail 两趟都是栽在把两者当成同一把上）。
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
            #: ★ 2026-09-24（用户点名「那不能一样加个 success_url 吗」）：判据的**第二格** ——
            #: 「走通之后**网址**里会出现哪一截」。老脚本本来就常这么判（`japansdates` 的
            #: `/wizard`、`warthunder` 的 `#/confirm`），而正文那格搬不过来它们。
            #: ⚠️ 它是**另外一格**，**不是**把正文那格放宽成「正文+网址」——
            #: 「判据一个字没放宽，要不要改得人点头」（`graph._criterion_say` 那一族的口径）。
            success_urls: str = "",
            data_keys=None,
            hints=None) -> Journey:
    """在真浏览器里为 `goal` 探 `url` 这条路，返回 `Journey`。

    参数：
      - `url` / `goal`：探哪一页、要摸清什么
      - `hints`：**人另外交代的话**（面板上「开工前先说一句」/「步骤表」那一格，逐字）。
        ★ 2026-09-22 加的（真事）：新站那条路的稿是「账本 → 模板」算出来的，模型在这条路上
        **只有探路这一处有判断力**；人的话原来只进「修站出补丁」那条路 ⇒ 运营把步骤写得再细
        也**一个字都到不了这儿**，屏幕上就成了「它完全没按我的来」。现在进 `_brief` ✓。
      - `data_keys`：★ 2026-09-30 —— 这一趟资料（`--form-file`）里**真有的键**。只用来给
        `FILLS[*].source` 起名（`template.library_key`）：资料里真有的那个名字才写进产物 ⇒
        运行期直接命中。**不给 = 不知道**（照旧按页面字段名记，运行期靠别名表兜）。
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
    journey = Journey(data_keys=[str(k) for k in (data_keys or []) if str(k or "").strip()])
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
                                                   success_text, success_urls),
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
            _stop_or_raise(paused, journey, taken, limits, success_text, success_urls)
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
            #: ★ 2026-10-15：**回执那一行里就有名字** —— 空卡那几步从它自己这条回执里捡回来
            #: （见 `_card_from_receipt_into`；`job-3e99eb67519e` 那三步的病）。
            #: 挂在这一处是因为**回执到手的那一刻**只有这里有：它一收进 `step["receipt"]`
            #: 就被 `_receipt_of` 按上限削过（`target` 那一格超过 200 字符的会被换成一句
            #: 「有多大、没抄」），再往后就没有原文可读了。
            _card_from_receipt_into(step, raw)
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
            #: ★ 2026-10-13（`job-4d12a95edf34`）：**系统自己**再读一眼整页正文 ——
            #: `observe` 那份被工具硬切 600 字，够不着挂在文档末尾的浮层文案。
            #: ⚠️ 排在 `journey.steps.append` **之前**：这一格是这一步的一部分
            #: （账本那行、checkpoint、判据三处读的是同一份 dict）。
            #: ⚠️ 它**不落步、不花预算**（不是 `dispatch`）：一步还是一行。
            _watch_the_page(session, journey, step, name, success_text)
            journey.steps.append(step)
            if name == "observe":
                # ★ 2026-09-29：逐元素正文**并进了 `cdp observe` 的模型**（`node_texts`）——
                # 所以这里照旧只是一次观测：判据那一格（「落在单个元素里吗」）**零额外调用**。
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
            _stop_or_raise(paused, journey, taken, limits, success_text, success_urls)
            _enter_target(session, url, journey)

        opening = _brief(url, goal, limits, plan, hints=hints, success_text=success_text,
                         success_urls=success_urls)
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
        _final_success_check(journey, dispatch, limits, success_text, success_urls,
                             specs=specs, gate=gate)
        #: ★ 2026-09-23 真站实测（用户把那一屏贴回来）：模型**在提交之后就收工**了，而回来
        #: 的那一页还在漏斗中间（「answer the questions truthfully…」那一屏，上面还有
        #: 12 组选择题 + 两个空格子没答）⇒ 判据要的那串字要到**更后面**才出现 ⇒
        #: 结算说「没走到成功」⇒ 图那边就**重探**，而重探 = 把整条漏斗**再交一遍表单**
        #: （用户口径「不要重复执行」）。⇒ 这里补一手：**还没见到、页面还在漏斗里，
        #: 就地接着走几轮**（有上限，不重开窗口、不重交）。
        #:
        #: ⚠️ 排在**收尾那一读之后**：先让代码真读一遍页面（整页正文），读出来有就不折腾模型。
        #: ⚠️ 只在「还没见到成功文案」时发生；`_Stop`（预算/暂停/窗口没了）照旧穿出去。
        #: ⚠️ 轮数**接着数**：`_wrap_up` 是**覆盖**（`journey.rounds = len(rounds)`），
        #: 两次各叫一次会把这一趟的轮数写成**后半段**那个数 —— 读账的人会以为它才问了三轮。
        if (success_text or success_urls) and _success_hit(
                journey.steps, success_text, success_urls) is None:
            room = min(CONTINUE_ROUNDS, max(0, int(limits.max_rounds or 0)))
            if room > 0:
                journey.note("还没见到成功文案，而页面还在漏斗里 ⇒ **就地接着走**"
                             "（最多再 %d 轮；不重开窗口、不重交表单）" % room)
                rounds2 = llm.run_tool_loop(
                    _SYSTEM, _unfinished_brief(success_text, success_urls), specs, dispatch,
                    max_rounds=room, max_tokens=MAX_TOKENS, steer=steer, _client=gate,
                )
                if rounds2:
                    _wrap_up(journey, list(rounds) + list(rounds2), limits)
                    #: 走完**再读一遍**（`specs=()` ⇒ 只读正文，不再叫模型看图：
                    #: 图那一眼是**证据**，上一遍已经存过一份了，别为同一件事再花一次）。
                    _final_success_check(journey, dispatch, limits, success_text, success_urls)
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
        # ★ 2026-09-30：「**同一页**被判成两页」的那些先并掉（`_merge_one_page_states`）——
        # ⚠️ 必须排在判据那一验**之前**：并掉之后下面那一验才不会拿「同一页的自己」当
        # 「**别的页**」报（它一报，`graph._draft` 就**一份 py 都不出**；实测那一天 20 趟里
        # 10 趟是这么死的）。并掉这件事**记在账上**（没有静默的路径）。
        for gone in _merge_one_page_states(pages.pages, journey.steps):
            #: 两种「并」各有各的理由（一处选，别在别处再判一次）：同一页分不开 / 那一页没读到正文。
            journey.note((UNREAD_MERGED_SAY if gone.get("kind") == "unread"
                          else SAME_PAGE_MERGED_SAY)
                         % (gone["name"], gone["into"], gone["url"]))

        # ★ 2026-09-29（交接《骨架还缺什么》§1）：判据的**反面**那一向 ——
        # 「这句话在**别的页**上还成不成立」。生成期验正例是循环论证（判据就是从这一页剪的
        # 原文 ⇒ 必然成立），没验过的正是这一向，而它命中就是「重放走到那页、这组步骤真做一遍」。
        # ⚠️ 排在 `_drop_incidental_start_when` **之后**：那个旁枝起点有意不设门，
        # 先撤掉它，这里才不会拿它当「一条判据」去比。
        #: ⚠️ **留在账本上**（不只是记一句话）：`graph._draft` 拿它**拦住产物** ——
        #: 「生成期就该拦住」这句要落在产物那道门上，光喊一声还是会把这一版发出去。
        #: ★ 2026-10-08：**先把能救的救回来，再算还剩几个**（`_repair_ambiguous_whens`）——
        #: 撞车多半是「第一个状态在正文开头剪到了全站公用文案」（跳转链接 / `<title>`），
        #: 而这一刻每一页的正文都在手边，够得着**按全站避让重挑一段**。
        #: ⚠️ 顺序不能反：反了的话救回来的那些也照样进 `gate_collisions`、照样把整趟砍死。
        for fixed in _repair_ambiguous_whens(pages.pages):
            journey.note(WHEN_REPICKED_SAY % (fixed["name"],
                                              "、".join(fixed["pages"]),
                                              fixed["had"][0] if fixed["had"] else None,
                                              fixed["now"]))
        journey.gate_collisions = _gates_that_hold_elsewhere(pages.pages)
        for gate in journey.gate_collisions:
            journey.note(GATE_HOLDS_ELSEWHERE % (gate["name"],
                                                 "、".join(gate["pages"]),
                                                 gate["name"]))
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
#: 而这里要的是**整页**。
#:
#: ★ 2026-10-21（真事 `job-be76b7ecd92c` · lendio）：**必须逐 root 收** ——
#: `document.body.innerText` **不穿 shadow**，而那一趟的成功文案（`h3`「Congratulations!」+
#: `h5`「Your business is eligible …」）整棵都在 open shadow root 里 ⇒ `page_text_full`
#: 里连一道题都没有 ⇒ 判据永远搜不到 ⇒ 自动重探 3 趟（每趟都是一次真提交）。
#: 原先那句「shadow 那条坑由同一轮 observe 兜着」是**假的**：`observe` 的正文也是从
#: 正文根起收的，而且**截在 600 字**——落地页文案一铺满，shadow 里的字就被切掉了。
#: ⚠️ 这份必须与**产物那份**（`template._PAGE_TEXT_JS`）同一个口径：只跳 style/script
#: 那几族、逐 root 收、`innerText` 压空白 —— 两边分家就会「探路认得出、交付出去的脚本
#: 认不出」（产物那份 2026-09-23/28 已经修过同一个坑）。
_FULL_TEXT_JS = (
    "(function(){try{"
    "var SKIP={SCRIPT:1,STYLE:1,TEMPLATE:1,LINK:1,META:1,NOSCRIPT:1,TITLE:1};"
    "var roots=[document],i=0;"
    "while(i<roots.length){var r=roots[i++];"
    "var els=(r.getElementsByTagName?r.getElementsByTagName('*'):r.querySelectorAll('*'));"
    "for(var j=0;j<els.length;j++){if(els[j].shadowRoot){roots.push(els[j].shadowRoot);}}}"
    "var parts=[];"
    "for(var k=0;k<roots.length;k++){var rt=roots[k];"
    "if(rt.body){try{parts.push(rt.body.innerText||'');}catch(e){}}"
    "else{for(var m=0;m<rt.children.length;m++){var c=rt.children[m];"
    "if(SKIP[c.tagName]){continue;}"
    "try{parts.push(c.innerText||'');}catch(e){}}}}"
    "return parts.join(' ').replace(/\\s+/g,' ').trim().slice(0, %d);"
    "}catch(e){return '';}})()")

#: 收尾「照着图找那串字」那句的 system（**只问这一件事**：别让它顺手改页面）。
_FINAL_LOOK_SYSTEM = (
    "你只做一件事：看一眼当前页面的**截图**，回答「下面这串字有没有出现」。"
    "先调一次 `screenshot`，然后**照抄**你在图上看到的那串字；确实没有就说「没有」。"
    "**不要**点任何东西、不要填任何东西 —— 页面已经走完了，你只看。"
)

#: 「还没见到成功文案 ⇒ 就地再走几轮」的上限（★ 2026-09-23）。
#:
#: 真站实测（用户把那一屏贴回来）：提交之后回来的页面**还在漏斗里**
#: （正文是「It's important that you answer the questions truthfully…」那一屏，
#: 上面还有 12 组选择题 + 两个空格子没答），而模型**已经收工了** ⇒ 结算说「没走到成功」
#: ⇒ 图那边就**重探**（= 把整条漏斗**再交一遍表单**）。这里补一手：**就地接着走**。
#: ⚠️ 它只在「还没见到成功文案」时发生，而且**有上限** —— 不点第二次提交、不重开窗口。
CONTINUE_ROUNDS = 6


def _unfinished_brief(success_text: str, success_urls: str = "") -> str:
    """「还没走到，接着走」那句（只在这种时候发，见 `CONTINUE_ROUNDS`）。

    两句话是**这一手的关键**，都不能省：
      · 「交出去了」**不等于**「走到了」—— 上一趟就是在这儿栽的；
      · 就在**当前这一页**上接着做（别导航回入口、别重交）。
    """
    wants = wanted_texts(success_text)
    urls = wanted_urls(success_urls)
    #: ⚠️ 先算好再插：`"…%s…" % x if cond else y` 里 `%` 比条件表达式**紧**，
    #: 写成一行会变成「(格式化) if cond else y」—— 剥完什么都不剩时整句话会没掉。
    shown = "」、「".join(wants) if wants else success_text
    #: ★ 2026-09-24：判据也可能是**网址**那一格（`success_urls`）—— 那句话里两格都要说清，
    #: 不然模型收到的是「页面上还没出现『』」（网址判据时文字那格是空的）。
    if urls:
        shown = ("%s；或者网址里出现「%s」" % (shown, "」、「".join(urls))
                 if shown else "网址里出现「%s」" % "」、「".join(urls))
    return ("你刚才收工了，但这一趟**还没走到**：页面上**还没有**出现"
            "「%s」。⚠️ 「把表单交出去了」不等于「走到了」—— 交完之后回来的那一屏\n"
            "常常**还在漏斗里**（上面还有没答的问题、没点完的按钮）。\n"
            "就在**当前这一页**上接着做（不要导航回入口、不要重点提交按钮）：\n"
            "先把页面上还没答的问题答完、该点的按钮点掉，一步一步往下走，\n"
            "直到页面上**真的出现**那串字为止。确实走不下去（元素找不到 / 页面报错 /\n"
            "卡住了）就照实说走不下去，别硬凑。" % shown)


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


#: ★ 2026-10-13（`job-4d12a95edf34`）：**每一步**都由系统自己再读一眼**整页可见正文**。
#:
#: 为什么非有这一手：判据要的那串字常常**不在 `observe` 给的那 600 字里**
#: （`observe.go` 的 `pageText.slice(0, 600)`）—— 弹窗/通知条那种**浮层挂在文档末尾**，
#: 于是它在 `body.innerText` 里排在整页正文的**尾部**。真账本实测（innovatingcanada 的
#: Hustle 弹窗）：「Thank You!」落在**第 12,968 字**（全页 12,978），三趟探路各 observe 了
#: 7~8 次，**一次都没够着它** ⇒ 判「没见着成功」⇒ 图那边不出 py、还自动重探
#: （每趟重探 = 往站方**再交一次真表单**）。
#:
#: 读回来的**不进模型上下文**（模型看不见，省 token）；账本上只留**判据那一截**
#: （`success_line`）—— 每步都留整页的话账本会涨十倍，而判据只认那一截。
#: 没给正文判据时**一次都不读**（没有要找的东西，白花）。
_STEP_READ_FRAMES = 2

#: 动手之后**盯多久**、隔多久读一眼（秒）。见 `_watch_the_page`。
#:
#: 为什么不能只读「动手那一下」：真站实测（同一趟）点完提交 **5.8 秒**才出那句成功文案，
#: 出现 **7 秒**后弹窗自己收走（`display:none`）—— 而模型下一步常常比它早
#: （那三趟的下一步都在 +1~4 秒）。
WATCH_SECONDS = 8.0
WATCH_EVERY_SECONDS = 1.2

#: 哪几个动作之后要**盯一会儿**（其余动作只读一眼）。
#: `click` / `goto` 是「会提交 / 会走」的那两个；`form` / `scroll` **不盯** ——
#: 填一格通常不动正文（值不进 `innerText`），盯它等于每一步白等满 `WATCH_SECONDS`。
_WATCH_AFTER = ("click", "goto")

#: 判据那一截在账本里前后各留多少字（够人一眼看出它落在哪句话里）。
SUCCESS_LINE_PAD = 120


def _step_read_frames(journey) -> list:
    """每一步那一读读哪些帧：主帧 + 这一趟动手用过的帧（与收尾那一读**同一条规矩**）。

    ⚠️ 帧数收到 `_STEP_READ_FRAMES`：收尾那一读是一趟一次（读满没事），这一读是**每步**一次。
    """
    return ["", *_frames_used(journey)[:_STEP_READ_FRAMES]]


def _read_page_text(session, journey, wants) -> str:
    """读一眼**整页可见正文**（`_FULL_TEXT_JS`，只读、逐帧）→ 归一化后的文字；读不到给空串。

    ⚠️ `session.call_tool` 而**不是** `dispatch`：它不落步、不花预算、模型看不见 ——
    它是**旁路**（与步拍、账本同一条规矩），坏掉不许把这一趟带塌 ⇒ 一律吞成空串。
    「没读到」与「页面上没有」在调用方那里是两件事（空串不写进账本）。

    ⚠️ 判据已经在手里了（`wants`）⇒ **见到就收手**，剩下的帧不读了：这一读的读者几乎只为那一串字。
    """
    parts: list = []
    for frame_id in _step_read_frames(journey):
        try:
            raw = session.call_tool("eval", dict(
                {"code": _FULL_TEXT_JS % FINAL_TEXT_CHARS},
                **({"frame_id": frame_id} if frame_id else {})))
        except Exception:                          # noqa: BLE001 —— 读不到不是错，是没读到
            continue
        got = _norm(raw if isinstance(raw, str)
                    else ((raw or {}).get("result") or (raw or {}).get("value") or ""))
        if got:
            parts.append(got)
            if wants and any(w in got.lower() for w in wants):
                break
    return _norm(" ".join(parts))


def _squash_positions(text: str):
    """去掉**所有**空白之后的那份 + 每个字在原串里的下标（判据比对用）。

    ⚠️ 只给 `criterion_in` / `_success_line_in` 用：`_norm` 那一把尺子是**页面身份**
    （`page_signature` / diff 靠它认页），**不许**去掉空白 —— 那会把两页认成一页。
    """
    out: list = []
    pos: list = []
    for i, ch in enumerate(text or ""):
        if not ch.isspace():
            out.append(ch)
            pos.append(i)
    return "".join(out), pos


def criterion_in(text: str, wants) -> bool:
    """判据在不在正文里 —— **空白可有可无**（「同一把尺子」的那个口子）。

    先逐字比（原来的行为不变）；不中再比「去掉所有空白」的那一份。为什么必须这样
    （★ 2026-10-21 真事 `job-be76b7ecd92c` · lendio）：页面把同一句成功文案拆在两块上
    （`h3`「Congratulations!」+ `h5`「Your business is eligible …」）—— 视觉上是换行，
    正文拼出来中间有一个空白；而运营给的那格没有那个空格
    （`Congratulations!Your business …`）⇒ 逐字子串**永远**对不上 ⇒ 探路自动重探 3 趟，
    每趟都是一次真提交。**空白不是内容**：同一句话被排版拆开仍然是同一句。

    ⚠️ 与产物那份（`template` 骨架里的 `_criterion_in`）**必须同一口径** —— 两边分家
    就是「探路认得出、交付出去的脚本认不出」（这个病已经犯过两次：大小写、同形字）。
    ⚠️ 去空白**只**动判据比对，不动 `_norm` / `row_text` / `page_signature`。
    """
    low = str(text or "").lower()
    if not low:
        return False
    flat = None
    for word in wants or []:
        want = str(word or "").lower()
        if not want:
            continue
        if want in low:
            return True
        #: ⚠️ 这里用 `re.sub` 而**不是** `_squash_positions`：这一支在每个读者里
        #: 是**按行**调的（R3 的 `seen_text` 越滚越大），逐字循环会变成 O(行数×正文)。
        #: 只有 `_success_line_in` 需要「去掉空白后的下标」，所以那个才用逐字的那份。
        if flat is None:
            flat = re.sub(r"\s+", "", low)
        if re.sub(r"\s+", "", want) in flat:
            return True
    return False


def _success_line_in(text: str, wants) -> str:
    """判据在这份正文里的**那一截**（前后各留 `SUCCESS_LINE_PAD` 字）；没有给空串。

    与 `criterion_in` 同一把尺子：先逐字找，不中再在「去掉所有空白」的那份里找，
    再把下标映射回原串（页面把一句话拆成两块时，原串里含着人给的那串字的**全部**字）。
    """
    raw = text or ""
    low = raw.lower()
    for word in wants or []:
        at = low.find(word)
        if at >= 0:
            return raw[max(0, at - SUCCESS_LINE_PAD):at + len(word) + SUCCESS_LINE_PAD]
    flat, pos = _squash_positions(low)
    for word in wants or []:
        want = _squash_positions(str(word or "").lower())[0]
        at = flat.find(want) if want else -1
        if at >= 0:
            start = pos[at]
            end = pos[at + len(want) - 1] + 1
            return raw[max(0, start - SUCCESS_LINE_PAD):end + SUCCESS_LINE_PAD]
    return ""


def _watch_the_page(session, journey, step, name: str, success_text: str) -> None:
    """这一步走完之后，**系统自己**读一眼整页正文；会走页面的那几个动作还要**盯一小会儿**。

    写进账本的只有**判据那一截**（`step["result"]["success_line"]`）—— 见那两组常数的注释。
    判据那一把尺子（`row_text`）认这一格，所以 `_success_hit` / `graph._explore_reached_success`
    / R3 三处看到的**是同一个事实**（这是这个项目里最容易裂的地方）。

    盯的规矩（`_WATCH_AFTER` 那几个动作）：
      · 读到了判据 ⇒ 记下来、收工；
      · **页面回话了**（正文变了）⇒ 收工 —— 盯是为了等它回话，不是为了白等满 8 秒；
      · 到 `WATCH_SECONDS` 为止（有界；它是**产物**跑在生产的那条路上的东西，不许无限等）。

    ⚠️ 盯的这一会儿里**不动页面**（只有读）—— 所以「停是安全的，它不让任何一步发生」
    那条不变量原样成立；人这时候喊停，最多晚 `WATCH_SECONDS` 秒停在下一道闸上。
    """
    wants = [w.lower() for w in wanted_texts(success_text)]
    if not wants:
        return                                    # 没有要找的东西 ⇒ 一次都不读
    #: 这一步**自己**读到的正文里就有判据（`observe` 那 600 字里就写着）⇒ 不必再读整页：
    #: 它已经在账本上了，三个读者（活的闸 / R3 / 结算）都在这一行上认得出它。
    if _success_line_in(row_text(step), wants):
        return
    first = _read_page_text(session, journey, wants)
    seen = _success_line_in(first, wants)
    if seen:
        step["result"]["success_line"] = seen
        return
    #: 第一读就没读到（窗口正在导航 / 求值失败）⇒ **没有基准**，不盯：
    #: 没有基准时「正文变了」这一条恒真，盯下去只会每一步白等满 `WATCH_SECONDS` 秒。
    if name not in _WATCH_AFTER or not first:
        return
    #: 次数上限与墙钟**双保险**：`time.sleep` 在测试里被换成 no-op（全项目的规矩），
    #: 只靠 `time.monotonic()` 会让这个循环空转。
    polls = int(WATCH_SECONDS / WATCH_EVERY_SECONDS) + 1
    deadline = time.monotonic() + WATCH_SECONDS
    while polls > 0 and time.monotonic() < deadline:
        polls -= 1
        time.sleep(WATCH_EVERY_SECONDS)
        got = _read_page_text(session, journey, wants)
        seen = _success_line_in(got, wants)
        if seen:
            step["result"]["success_line"] = seen
            return
        if got and got != first:
            return                                # 页面回话了（换了一屏）—— 盯到头了


def _final_success_check(journey, dispatch, limits, success_text: str,
                         success_urls: str = "", *, specs=(), gate=None) -> None:
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
    if (not success_text and not success_urls) \
            or _success_hit(journey.steps, success_text, success_urls) is not None:
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
    #: ②c **逐帧取整页正文**（`eval`，只读）—— ⚠️ **判据要读的正文就在这儿**：
    #: `observe` 那一手自己把正文截到 600 字（`tools/cdp/internal/observe.go` 的
    #: `pageText.slice(0, 600)`），而真站的成功文案常在 600 字之后
    #: ⇒ 2026-09-22 那趟屏幕上「言行不一致」就是这么来的（模型在图上看见了、代码在 600 字里找不着）。
    #: 用户口径：**这一步让代码判断**（图只是给人看的证据）。
    #:
    #: ⚠️ **必须逐帧**（2026-09-23 真站实测）：先只读主帧，拿回来 **357 字**
    #: （主帧常常只是个壳，正文挂在**跨源 iframe** 里 —— 这个站的表单就是），
    #: 判据照样什么也搜不到。读的是 ② 里 `observe` 走过的那同一串帧。
    #:
    #: ⚠️ 两条**曾经**让它整条都没跑起来（同一趟里查出来的）：`cdp` 的 MCP 那张表里
    #: **原先根本没有 `eval`**（只有 CLI 有）⇒ 每一次调用都被这里的 `except` 吞掉，
    #: 「接上了但不响」比没接更坏。现在表里有它了；下面这个 `except` 留着，
    #: 但**每帧都试**、只要有一帧拿到正文就算数。
    texts: list = []
    for frame_id in ["", *_frames_used(journey)]:
        try:
            raw = dispatch("eval", dict({"code": _FULL_TEXT_JS % FINAL_TEXT_CHARS},
                                        **({"frame_id": frame_id} if frame_id else {})))
        except BaseException:                  # noqa: BLE001 —— 旁路不许带塌这一趟
            continue
        got = _norm(raw if isinstance(raw, str)
                    else ((raw or {}).get("result") or (raw or {}).get("value") or ""))
        if got:
            texts.append(got)
    full = _norm(" ".join(texts))[:FINAL_TEXT_CHARS]
    if len(journey.steps) > before and full:
        journey.steps[-1]["result"]["page_text_full"] = full
        journey.steps[-1]["note"] = ("收摊前**系统自己**取的**整页正文**（只读、逐帧；"
                                     "`observe` 那份只有前 600 字）：判据就在这份里找")
    if len(journey.steps) == before:
        return                                 # 一步都没记上（被闸拦住等）⇒ 不假装
    for step in journey.steps[before:]:
        #: 它们是**系统**看的，不是模型看的（`origin` 要让读账的人一眼看出来）——
        #: 顺带把来由说清楚（账本里不许有来路不明的一步）。
        step["origin"] = "final_check"
        step.setdefault("note", ("收摊前**系统自己**看了一眼（模型这一趟没看它）："
                                 "拿这一眼的正文判成功文案"))
    if _success_hit(journey.steps, success_text, success_urls) is not None:
        return                                 # 代码在正文里见着了 ⇒ 收工
    #: ③ 正文里还是没有 ⇒ 照一张图存成**证据**（⚠️ **判据不由它定** —— 用户口径：代码判断）
    #: ★ 2026-09-24：**图那一眼只看文字那一条** —— 网址不是「图上的一串字」。
    #: 只给了网址判据时**不照那个图**（拿一个空判据去问模型，等于让它随便回一句「没有」）；
    #: 这件事**说出来**（不静默），人自己知道收尾那一眼这回帮不上忙。
    if not wanted_texts(success_text):
        journey.note("收尾那一眼（照图找那串字）**没有做**：这一趟的判据只有**网址**那一条，"
                     "而图上看不出网址 —— 代码已经按网址判过了。")
        return
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
    journey.vision_hit = _vision_hit(said, success_text)
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


#: 「照图那一眼说见着了」的判据里，**否定句不算**。
#:
#: 实测（2026-09-26，`job-748efe71deaf`）：模型原话是
#: 「没有。图上只有「Payment」…**并没有出现「Payment details」这一串字**。」
#: —— 它**引用了判据那句话**，于是子串判据在这里命中了 ⇒ `vision_hit = True`。
#: 代价有两层：① 面板上写「收尾照图那一眼**在图上见到了**」（**假话**，用户当场就问
#: 「我页面看到是成功了的，为啥不产 py」）；② `graph._retry_wont_help` 也读它 ⇒
#: **本来就该自动重探的那一趟被这条假阳性拦下了**（不再去真站试第二趟）。
#: ⇒ 匹配点**前后各一段**里出现否定词就不算「见着」。
#: ⚠️ 天花板（`ponytail:`）：拿自由文本做判据，否定词表永远漏 —— 正解是让模型先回一个
#: 「有/没有」，判据只读那个词（那要改 `_vision_look` 的提问与解析，等有人要更稳的时候再做）。
_NEG_BEFORE = ("没有", "没", "未", "并不", "看不到", "看不见", "无")
_NEG_AFTER = ("没有出现", "没出现", "未出现", "看不到", "看不见", "并没有")
_NEG_BEFORE_WINDOW = 12
_NEG_AFTER_WINDOW = 8


def _said_seen(said: str, wants) -> bool:
    """模型那句话里，`wants` 里有没有哪一串是**肯定地**说了见着了（否定句不算）。"""
    text = _norm(said).lower()
    for want in wants:
        at = text.find(want)
        while at >= 0:
            before = text[max(0, at - _NEG_BEFORE_WINDOW):at]
            after = text[at + len(want):at + len(want) + _NEG_AFTER_WINDOW]
            if not any(n in before for n in _NEG_BEFORE) \
                    and not any(n in after for n in _NEG_AFTER):
                return True
            at = text.find(want, at + 1)
    return False


def _vision_hit(said: str, success_text) -> bool:
    """专给 `_vision_look` 用的那一格（判据只写一处 —— 别在别处再拼一遍子串比对）。"""
    wants = [w.lower() for w in wanted_texts(success_text)]
    return bool(wants) and _said_seen(said, wants)


def _spec_name(spec) -> str:
    """一个工具 spec 里那个**名字**（形状由 MCP/OpenAI 那一侧定）。"""
    try:
        return str(((spec or {}).get("function") or {}).get("name") or (spec or {}).get("name") or "")
    except AttributeError:
        return ""


#: 判据那一格写成**说明句**时的开头（「我要它出现」的意思）—— 这些词是**壳**，
#: 不是页面上会有的字。
#:
#: ★ 2026-09-29：**它从一张手列的表改成了一条正则**。真事（`job-5850107f65ed`，thegiraffe）：
#: 运营把那一格写成「**页面中出现**found your offers」，而表里只有「页面上出现」「页面出现」
#: —— **差一个「中」字** ⇒ 整句被当成**逐字判据** ⇒ 页面上明明写着 `We've found your offers!`，
#: 三趟却都报「没见到成功文案」⇒ 图判 `explore_unfinished` ⇒ **一个 py 都不出**，
#: 而每一趟都是一次**真提交**。这一族（页面 / 页面上 / 页面中 / 页面里 + 会 / 出现 / 显示 / 看到）
#: 不该再靠人往里补词 —— 补一个，下一个字还能把它漏过去。
#: ⚠️ 英文那三个照旧：`show(?!\S)` 那一条不许收成裸 `show`，少了它 `showcase` 会被剥成 `case`。
CRITERION_HEAD_RE = re.compile(
    r"^(?:"
    r"出现文字\s*[:：]?"
    r"|页面\s*[上中里]?\s*(?:会\s*)?(?:出现|显示|看到)"
    r"|页面\s*[上中里]"
    r"|出现|显示|看到"
    r"|shows|show(?!\S)|contains"
    r")\s*[:：,，。;；\-—]?\s*", re.I)

#: 判据那一格里**像说明句**的记号（出现在任意位置都算）。它只用来**说一句话**，不参与匹配 ——
#: 匹配的尺子只有 `strip_criterion_head` 那一把。
CRITERION_SHELLISH_RE = re.compile(r"出现|显示|看到|shows|contains|show\b", re.I)


#: 判据那一格里**页面上真会有的那几段字**：连续可打印 ASCII（字母 / 数字 / 空格 / 标点）。
#: 为什么连数字和标点一起要（`#A12345` 这种编号、`Order #12` 里那个 `#`）：子串判据**少一个字就更松**，
#: 把编号切掉就成了「页面上有 `Order` 就算成」—— 那是往「谎报成功」那一侧走。
_ASCII_RUN_RE = re.compile(r"[ -~]+")


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

    ★ 2026-09-29：剥的是 `CRITERION_HEAD_RE` 那一族，**不再是一张手列的词表** ——
    「页面中出现…」原来是剥不动的（表里没有那一种写法），于是它被当成逐字判据、
    页面上永远找不到（上面 `CRITERION_HEAD_RE` 那段真事）。**判据那一侧与产物那一侧
    共用这一个函数**（`template._success_texts` → `wanted_texts` → 这里）。
    ★ 2026-09-30（用户原话「改成成功只提取英文就行了」）：剥完壳**再只留 ASCII 那几段** ——
    中文那部分是**人话**（「我要它出现」），页面上要认的那串字是 ASCII。理由与那一档
    纯中文判据的兜底，见下面函数体里那段注释。
    """
    text = _norm(str(word or ""))
    text = _norm(CRITERION_HEAD_RE.sub("", text, count=1)).lstrip(" :：,，。;；-—").strip()
    #: ★ 2026-09-30（用户原话「改成成功只提取英文就行了」）：剥完壳**只留 ASCII 那几段**
    #: （多段用空格接起来）。`job-5850107f65ed` 那一格「页面中出现found your offers」本来就该
    #: 落到 `found your offers` 上 —— 与其把「页面中 / 页面上 / 页面里…」这一族写法一个个
    #: 往词表里补，不如认下这条：**判据那一格要的是照抄页面上真会出现的字**；说明词是人话（中文），
    #: 要认的那串字是 ASCII。上面那条正则因此只剩一个用途：**纯中文判据**那一档。
    #: ⚠️ 一格 ASCII 都没有（`页面上出现提交成功`）⇒ 返回**剥完壳的那份中文**，不许整格丢掉 ——
    #: 中文站的成功文案就是中文（`tests/test_template.py` 里 `SUCCESS_TEXTS == ["READY"]` 那种）。
    runs = [r.strip() for r in _ASCII_RUN_RE.findall(text)]
    return _norm(" ".join(r for r in runs if r)) or text


def criterion_shell_unrecognized(text) -> str:
    """那一格**剥完剩下的字里还带着说明词** ⇒ 返回那个记号；否则空串。

    ★ 2026-09-29（`job-5850107f65ed`）：那一格写着「页面中出现found your offers」——
    带着说明词，壳却差一个字没被剥掉 ⇒ 它成了**逐字判据** ⇒ 三趟都「没见到成功文案」
    ⇒ 没有 py。而屏幕上**没有一句话**能让人看出「是这一格填错了」还是「站点没成」：
    人只能去猜。这一句就是那句缺的话。

    ★ 2026-09-30：原来只在「**一个字都没剥掉**」时才吭声，于是「页面上有出现字样」这种
    **剥了一半**的漏过去（剥掉「页面上」还剩「有出现字样」，里面那个「出现」正说明它还
    是一句人话、不是页面上会有的字）。⇒ 判据改成看**剩下的那串**里还有没有说明词：
    页面上真会有的字（`Tailor Your Cover` / `found your offers`）里没有这些词，所以
    「更严不更松」这条不受影响（真写着「显示更多」按钮的那种，剥完是「更多」）。
    ⚠️ 它**不参与匹配**（匹配只有 `strip_criterion_head` 那一把尺子），也不拦任何东西 ——
    它只负责把「我按**字面**在找」这件事摆到人说得出话的地方。
    """
    text = _norm(str(text or ""))
    if not text:
        return ""
    hit = CRITERION_SHELLISH_RE.search(strip_criterion_head(text))
    return hit.group(0) if hit else ""


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


def wanted_urls(success_urls) -> list:
    """判据的**网址那一格** → 真要去找的那几截（去空、去重、保序）。

    ★ 2026-09-24（用户点名「那不能一样加个 success_url 吗」）：老脚本本来就常判网址
    （`japansdates` 的 `/wizard`、`warthunder` 的 `#/confirm`），而正文那格搬不过来它们。

    ⚠️ **不剥说明壳**（`strip_criterion_head` 那一套是给「页面上会出现哪段文字」那格用的）：
    这里要的是**网址里的一截**，`/wizard` 这种本来就是它该有的样子。
    ⚠️ 与 `wanted_texts` 一样：**空的不留**、判不出来的不猜 —— 空列表 = 这一格没内容。
    """
    words = [success_urls] if isinstance(success_urls, str) else list(success_urls or [])
    out: list = []
    for word in words:
        got = str(word or "").strip()
        if got and got not in out:
            out.append(got)
    return out


def row_text(row) -> str:
    """一行账**读到的正文** —— **判据唯一的那把尺子**（三个读者都走这里）。

    ⚠️ 三处必须是同一个函数（分家过一次就够贵了：活的探路说见着了、结算说没见着）：
      · `page_text_head`：`observe` 那一手的（cdp 自己**截到 600 字**，`observe.go` 的
        `pageText.slice(0, 600)`）；
      · `page_text_full`：收尾那次 `eval` 取的**整页正文**（2026-09-22：用户要求
        「让**代码**判断……有个可以提取文字的」—— 600 字装不下很多真实页面）；
      · `success_line`：**每一步**那一读里**含判据的那一截**（★ 2026-10-13，见
        `_watch_the_page`）—— 浮层文案常在整页正文的**尾部**，600 字够不着，
        而每步都留整页账本会涨十倍，所以只留判据那一截。
    判据读的是**加起来**的那份文字，所以三处都算。
    """
    result = (row or {}).get("result") or {}
    return _norm("%s %s %s" % (result.get("page_text_head") or "",
                               result.get("page_text_full") or "",
                               result.get("success_line") or ""))


def row_url(row) -> str:
    """一行账**在哪一页上**（`observe`／动作行都把当时的地址记在 `result.url`）。

    ★ 2026-09-24：网址判据（`success_urls`）要它 —— 与 `row_text` 并列，
    同一个「从这一行里读什么」的口子，别在调用方各写一遍。
    读不出来回空串（**不猜一个地址**）。
    """
    return _norm(str(((row or {}).get("result") or {}).get("url") or ""))


def is_look(row) -> bool:
    """这一行**算不算「一眼」**（判据只在「看过」的行里找）。

    三条都算：`observe`（模型或系统看的）、**任何带着 `page_text_full` 的行**
    （收尾那次 `eval` 读的整页正文 —— ⚠️ 它读的**也是正文**，只是那一手比 `observe`
    取的多；把它排除在外，就等于判据看不见自己刚读回来的那两万字），
    以及**任何带着 `success_line` 的行**（★ 2026-10-13：每一步那一读里含判据的那一截 ——
    最常见的载体就是**提交那一下的 `click` 行**，而它恰恰是最要紧的那一眼：
    把它排除在外，结算就会说「没见着成功」而活的探路当场就见了）。
    ⚠️ `page_text_head` 仍然只有 `observe` 行才写（R3 的前提，`_only_looks_carry_the_text`
    会当场抛）—— 这一条判的是「算不算一眼」，不是「谁写了哪个键」。
    """
    if str((row or {}).get("action") or "") == "observe":
        return True
    result = (row or {}).get("result") or {}
    return bool((result.get("page_text_full") or "").strip()
                or (result.get("success_line") or "").strip())


def _success_hit(steps: list, success_text: str, success_urls: str = "") -> int | None:
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
    urls = [u.lower() for u in wanted_urls(success_urls)]
    if not wants and not urls:
        return None                       # 两格都没给 / 剥完什么都不剩 ⇒ **判不了就不猜**
    for i, row in enumerate(steps or []):
        if not is_look(row):
            continue                      # 没看过页面的一行（动作类）⇒ 判据不该在它身上找
        head = row_text(row).lower()
        if head and wants and criterion_in(head, wants):
            return i
        #: ★ 2026-09-24：网址那一格在**同一行的地址**上找（与正文同一把尺子：小写 + 子串）。
        page_url = row_url(row).lower()
        if page_url and urls and any(u in page_url for u in urls):
            return i
    return None


def _stop_reason(paused, journey, taken: int, budget: Budget,
                 success_text: str = "", success_urls: str = "") -> str | None:
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
    if _success_hit(journey.steps, success_text, success_urls) is not None:
        return STOP_REACHED_SUCCESS
    if taken >= budget.max_steps:
        return "budget_steps"
    return None


def _stop_or_raise(paused, journey, taken: int, budget: Budget,
                   success_text: str = "", success_urls: str = "") -> None:
    """该停就抛 `_Stop` —— **两道闸共用这一个出口**（每步之前 / 每轮之前）。

    ⚠️ 人那道闸**自己抛异常**时，这里把它**归一成「暂停」**。不这么做的话，闸的错误
    会以普通 `Exception` 的身份落到 `llm.run_tool_loop` 的 `except Exception` 上 ——
    被记成**一次工具失败**、然后**循环继续**：人的中断静默降级成「有个步骤失败了，继续吧」。
    那是这条路上最坏的形状（喊停没停，而且没有任何人看得出来）。
    闸坏了要**停下来**（带上它坏在哪），不能带着一个坏掉的闸往下跑。
    """
    try:
        reason = _stop_reason(paused, journey, taken, budget, success_text, success_urls)
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
        return self._watched(self._inner.chat.completions.create(**kwargs))

    def _watched(self, stream):
        """把流**原样**放过去；收流那一刻记下这一轮模型说了什么。

        ★ 2026-10-14：`run_tool_loop` 改走**流式**了（治网关那层 60 秒读超时，
        见 `llm.read_stream`）—— `create` 回的是**一股流**、不是一句回复，
        内容要**收完**才拼得出来。所以「记人话」这一步跟着挪到**收流之后**
        （原先是在 `create` 返回处读 `resp.choices[0].message.content`）。
        ⚠️ 拼话用的是 `llm.delta_content` —— **与 `read_stream` 同一个口**，
        这里不另写一份解析（两份迟早会漂，而叙述是要给人看的那一份）。
        """
        said: list = []
        for chunk in stream:
            said.append(llm.delta_content(chunk))
            yield chunk
        text = "".join(said).strip()
        self._note(text)
        if self._watch is not None:
            self._watch.note_round(text)

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
        if element is None:
            #: ★ 2026-10-15：**找不到元素要说出来**（不许静默给一张空卡）。
            #: 为什么：这一张卡是下游两件事的唯一依据 —— 判据判「这根地址是不是量到的」、
            #: 重放按语义把这一格找回来。它静默变空的时候，那两件事都变成「凭感觉」，
            #: 而屏幕上一切正常（`agent/browser_agent.py:2903` 那段注释记着同一形状的四个症状：
            #: 判不出格子是什么 ⇒ 值掉进随机池 ⇒ 名字退化成 field-N）。
            #: `why` 这一格本来就是为「看不见」准备的：「看不见」是一等值，**必须**配一句话。
            step["why"]["card"] = ("上一次观测里**没找到**这一步指的那个元素"
                                   "（选择器 `%s`）—— 这一格的**身份证据量不到**："
                                   "它身上/旁边有什么字、自报的名字是什么，账本里都没有。"
                                   % (selector or "（空）"))
        if name == "form":
            fill = _fill_info(args, step["target"], element, journey)
    return step, fill


#: 回执那一行里**捡回来**的那几样，按落位顺序（`label` 在前：判据先认它）。
#: ⚠️ 只有这两样 —— 键名是 `target` 这份契约里**本来就有**的
#: （`_target_of` 的两支都发它们），不是新键。
_CARD_FROM_RECEIPT_KEYS = ("label", "text")


def _card_from_receipt_into(step: dict, raw) -> list:
    """回执到手那一刻：把回执那一行里的名字**捡**进这一步的身份卡，返回补上的键。

    ★ 2026-10-15（`job-3e99eb67519e` · wildnutrition 账本第 30/32/37 步）：**同一个元素、
    同一次工具调用** —— 回执里 `aria-label="Every now and then"` 印得清清楚楚，而账本里
    那张卡 `text`/`role`/`near` 三个 `null`。根因在摘卡那一跳（`_describe`）：判据是
    `_find_element(上一次观测的模型, 选择器)`，它**在那份模型里没找到这个元素** ⇒
    `_target_of(None, …)` 出来一张空卡 ⇒ 下游判据说这一步「量不到更稳的地址」
    （`lint._has_text_anchor` 认的 `label` / `text` 一个都没有）。

    纪律（三条，别松）：

    - **只在卡是空的那一刻补** —— 「上一次观测里没找到这个元素」正是 `_describe` 写下
      `why["card"]` 的那一种（它就是这里那道闸）。**找到了的那些步一个字节都不动**：
      那张卡是从上一次观测里摘的（同一份证据），而回执说的是「cdp 解析选择器之后点到
      的那一个」—— 选择器有歧义时两者可能不是同一个元素，盖掉就是拿另一份证据换掉这份，
      而屏幕上什么都不会说。
    - **只补空着的那几样**（`target` 里已有值的照原样留着），**不加别的键** ——
      `target` 是产物契约的一部分（`label` / `text` 本来就在这份契约里）。
    - **认不出就不补**（`card_from_receipt` 给 `None`）：宁可留一张空卡，也不猜一个名字。

    `aria-label` 落 `label` 是照 `_target_of` 的既有约定（那一支写的是
    `label = element.get("label") or element.get("aria_label")`）。**判据认的键是 `label`
    不是 `aria_label`** —— 落错地方等于白补。
    """
    if not (step.get("why") or {}).get("card"):
        return []                       # 上一次观测里**找到**了 ⇒ 卡不是空的 ⇒ 什么都不动
    target = step.get("target")
    if not isinstance(target, dict):
        return []                       # 这一步没有卡可补（goto / observe / eval 那几步）
    told = raw.get("target") if isinstance(raw, dict) else None
    card = card_from_receipt(told)
    if not card:
        return []
    filled = []
    for key in _CARD_FROM_RECEIPT_KEYS:
        if str(target.get(key) or "").strip():
            continue                    # 已有值 ⇒ 一个字节都不动
        if card.get(key):
            target[key] = card[key]
            filled.append(key)
    if not filled:
        return []
    got = "、".join("`%s`" % key for key in filled)
    step["why"]["card"] += (
        "—— 不过这一张**从回执里捡回来了**：这一下点到的元素，回执那一行把它叫什么"
        "写下来了（补上了 %s：`%s`）。" % (got, told))
    return filled


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


#: 选择器里**能指认这个元素**的那几段原文。两种写法（今天两条真账本各撞上一种）：
#:   · `#id`            —— `input#phone-3848a5d5`      → `phone-3848a5d5`
#:   · `[attr="值"]`    —— `input[data-label="@Firstname"]` → `@Firstname`
#: ⚠️ **属性名本身（`data-label`）不算** —— 同一种格子上都是一样的，指认不了谁是谁；
#: 算的是它**那个值**。
_SELECTOR_TOKEN = re.compile(
    r"#([A-Za-z_][-\w]*)"
    r"|\[\s*[-\w]+\s*[~^$*|]?=\s*[\"']([^\"']*)[\"']\s*\]"
    r"|\[\s*[-\w]+\s*=\s*([^\]\s\"']+)\s*\]")


def _selector_tokens(selector) -> set:
    """这个选择器里点名的几段身份字（`#id` 与 `[attr="值"]` 的值）。"""
    out = set()
    for m in _SELECTOR_TOKEN.finditer(str(selector or "")):
        for one in m.groups():
            if one:
                out.add(one)
    return out


def _token_key(text) -> str:
    """归一化成一个可比对的形式：**只留小写字母数字**。

    为什么非归一化不可：同一件事在三个地方是三种写法 —— 页面上印的是 `First Name`、
    元素自报的是 `input-e3d6d0f7`、而模型写的选择器里是 `@Firstname`。按原文比，
    这三个**永远对不上**；归一化之后（`firstname` / `inpute3d6d0f7` / `firstname`）才是同一个东西。
    """
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


#: 归一化之后**短于这个长度的 token 不参与比对** —— 例如 `#a` / `[id="x1"]` 这种，
#: 拿去"包含"一个几十字的 hint 几乎必然命中，那就不是身份、是噪声。
_MIN_TOKEN_KEY = 4


#: 尾段里要有**鉴别力**的那一段（`label:nth-of-type(7)` 这种）才算证据：
#: `nth-of-type(k)` / `#id` / `[attr=…]`。`div > input` 那种到处都有，认它是噪声。
_TAIL_DISTINCTIVE = re.compile(r"nth-of-type\(|#|\[")


#: `:nth-of-type(1)` 与**不写**是**同一个元素**（第一个同标签的兄弟）—— 归一化时去掉它，
#: 两边的写法才可比。真站实测（`job-bcb1863324f4`）：观测写
#: `… > label:nth-of-type(7) > input:nth-of-type(1)`、模型写 `… > label:nth-of-type(7) > input`
#: —— 只差这一处，尾巴就「不等」了。
_TAIL_FIRST_OF_TYPE = re.compile(r":nth-of-type\(1\)")


def _selector_parts(selector) -> list:
    """把选择器切成几段 —— `>` 与**空白**都算组合符（都是「后代/子」），
    但**括号、方括号、引号里的**不算（`[placeholder="A friend's code"]` 里那个空格
    与 `:has(…)` 里那些都不许当组合符 —— 切碎了就再也对不上）。

    为什么连空格也要切（真站 `job-bcb1863324f4`·同一天的 `select` 那格）：模型写的是
    `form:has(input[placeholder="A friend's code"]) select`（**空格后代**写法），
    只按 `>` 切的话整串算一段、尾段恒为空 ⇒ 那一格永远认不回来。
    """
    text = str(selector or "")
    parts, depth, quote, cur = [], 0, "", []
    for ch in text:
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = ""
            continue
        if ch in "\"'":
            quote = ch
            cur.append(ch)
            continue
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        if depth == 0 and (ch == ">" or ch.isspace()):
            if cur:
                parts.append("".join(cur))
                cur = []
            continue
        cur.append(ch)
    if cur:
        parts.append("".join(cur))
    return parts


def _selector_tail(selector, n: int = 2) -> tuple:
    """选择器的**尾段**（最后 `n` 段，**原样**，不归一化）：
    `… > label:nth-of-type(7) > input:nth-of-type(1)` → `("label:nth-of-type(7)", "input:nth-of-type(1)")`。
    段数不够给空元组（那就不算证据）。

    ⚠️ **原始尾段用来判「有没有鉴别力」**（`_TAIL_DISTINCTIVE`）—— 归一化会把
    `label:nth-of-type(1)` 抹成 `label`，鉴别力跟着被抹掉（真站那格就卡在这条缝里：
    它明明有 `nth-of-type(1)`，抹完只剩 `label` ⇒ 两条规则都不认 ✗）。
    比相等走 `_tail_key`（见那儿）。
    """
    parts = [p for p in _selector_parts(selector) if p]
    return tuple(parts[-n:]) if len(parts) >= n else ()


def _tail_key(tail: tuple) -> tuple:
    """尾段**比相等用的钥匙**：`:nth-of-type(1)` 去掉 —— 它与不写是**同一个元素**
    （第一个同标签的兄弟）。真站：观测写 `… > label:nth-of-type(7) > input:nth-of-type(1)`、
    模型写 `… > label:nth-of-type(7) > input` —— 只差这一处。"""
    return tuple(_TAIL_FIRST_OF_TYPE.sub("", p) for p in tail)


def _id_is_the_elements_hint(element: dict, selector: str, frame_id: str = ""):
    """选择器里那个 `#id` 与这条元素自报的 id（`hint`）对得上到什么程度（`None` = 对不上）。

    返回值是**确信度档**（数字小的先要）：
      · `2` / `3` = **一字不差**（选择器里那段 id 就是 `hint` 本身），帧对得上 / 对不上；
      · `4` / `5` = 一个**含**另一个（帧对得上 / 对不上）。

    ★ 2026-10-08（真账本 `job-7f43337b2ba0`，laterlifechoices.uk）：填格那一步模型用的
    选择器是 `input#phone-3848a5d5`，而观察给这一格报的 `selector` 是
    `input[placeholder="+44 0000 000000"]` —— 只比「原样的 selector 字符串」就认不回来，
    那一步的 `label` / `hint` / `placeholder` **全丢**：
      · 判不出这一格是什么 ⇒ `_fallback` 走「用账本里那一趟真填进去的值」那条兜底
        ⇒ **手机号写死成同一个 `02071234567`**（每条 lead 都一样）；
      · 四格的名字退化成 `field` / `field-2` / `field-3` / `field-4`。
    四格**全中** —— 一个形状，四个症状。

    ⚠️ `hint` 是**元素自报的 name / id**（`_target_of` 那段 2026-09-17 的注释写的就是这件事），
    它本来就是身份的一部分 —— 与 `ir` 那一刀（选择器原文纳入身份比对）同一个形状：
    **线索摆在那儿，代码没读它**。

    ⚠️ **相等必须压过包含**（所以是四档不是两档）：`phone` 与 `phone-2` 两个框同时在场时，
    「包含」那一档会让 `input#phone-2` 认到 `phone` 上去 —— 那是把这一步指到**别的元素**上，
    比认不回来坏得多。真账本那次是**尾段**包含（`phone-3848a5d5` ⊂
    `phone-input-phone-3848a5d5`），而框架自造号那种 `input-e3d6d0f7` 是**完全相等** ——
    哪一头长不固定，只收一边就会漏，所以两边都收，但排在相等后面。
    """
    want = {k for k in (_token_key(t) for t in _selector_tokens(selector))
            if len(k) >= _MIN_TOKEN_KEY}
    if not want:
        return None
    #: 元素那几样**身份证据**（`_target_of` 那段 2026-09-17 的注释列的就是这几样）——
    #: hint = 元素自报的 name/id、label = 页面上印的名字、placeholder = 那行例子。
    #: ⚠️ 三样**都认**：今天两条真账本一条撞在 `hint` 上（`#phone-3848a5d5`）、
    #: 另一条撞在 `label` 上（`@Firstname` vs `First Name`）—— 只认一样就漏一半。
    said = {k for k in (_token_key(element.get(k))
                        for k in ("hint", "label", "placeholder")) if k}
    if not said:
        return None
    ok = _frame_id_of_path(element.get("frame_path")) == frame_id
    if want & said:
        return 2 if ok else 3
    if any(w in s or s in w for w in want for s in said):
        return 4 if ok else 5
    return None


def _find_element(pages: "_Pages", kind: str, selector: str, frame_id: str = ""):
    """在上一次观测的模型里找这个选择器对应的元素（找不到就 None）。

    ⚠️ 找的是**上一次观测**——「它当时看到的是哪个元素」这件事只有那份模型说得清。
    找不到不是错：target 退回「只有选择器」的形态，重放的声明式回退链照样能跑。

    同一个选择器**在主帧与子帧里可以是两个不同的元素**（跨源 iframe 的部件常常就是把
    同一套结构再渲染一遍）。所以先挑**帧对得上**的那一个；一个都对不上时退回原先的
    「文档序第一个」—— 「帧对不上」不等于「一定是它」，只是没有更好的线索。

    ★ 2026-10-08：**按信任度排队**（`rank` 小的先要），六档：
      0/1 = 选择器**原样**对得上（帧对得上 / 对不上）—— 老行为，一个字没动；
      2/3 = 选择器里那个 `#id` 与元素的 `hint` **一字不差**（帧对得上 / 对不上）；
      4/5 = 一个**含**另一个（帧对得上 / 对不上）。
    每一档里都是**文档序第一个**说了算（`rank` 严格小于才换人 ⇒ 先到的不被后来者顶掉）。

    ⚠️ 顺序有讲究：**原样对得上的永远排在 id 推断前面**，**相等永远排在包含前面** ——
    同前缀的两个元素（`phone` / `phone-2`）才不会因为推断而认错，而「认错了」比
    「认不回来」坏得多（后者只是少了几条语义证据，前者会把这一步指到**别的元素**上）。
    """
    model = pages.current_model
    if not model or not selector:
        return None
    pool = (model.get("fields") if kind == "field" else model.get("actions")) or []
    #: ★ 2026-10-12（真事 `job-bcb1863324f4` · nitrouscompetitions.com/auth）：**尾段唯一就能认**。
    #:
    #: 现场：地址 / 城市 / 邮编 / 勾选那几格**没有 id、没有 name、type 又是 `text`** ⇒ `observe`
    #: 给它们的选择器只能是**位置链**，而模型填的时候用的是**同一个元素的另一种写法**
    #: （`form:has(…) > label:nth-of-type(7) > input` vs 观测的
    #: `body:… > label:nth-of-type(7) > input`）——**尾巴一样、前缀不同**。前几档看的是
    #: 「整串 / token（`#id` 与 `[attr="值"]`）」，于是**全对不上** ⇒ 那一格的
    #: `label/hint/placeholder/nearby_text` 全丢 ⇒ 名字退成 `field-2`…（重放时资料对不上、
    #: 只能吃随机池，而日志照样写「填好了」）。而 `input[type=email]` / `[type=date]` 那几格
    #: 有**唯一属性** ⇒ 原样对得上 ⇒ 它们从来没丢过 —— 丢的正是这份「没有唯一属性」的。
    #:
    #: ⚠️ **判据是「唯一」**（与 `_id_is_the_elements_hint` 那条纪律同一句：认错比认不回来坏
    #: 得多）：池子里有两格尾巴一样 ⇒ **一条都不认**；尾段里没有鉴别力的（`div > input`）也不认。
    tail = _selector_tail(selector)
    tail_owner = None
    if tail and _TAIL_DISTINCTIVE.search(">".join(tail)):
        key = _tail_key(tail)
        same = [e for e in pool
                if isinstance(e, dict) and _tail_key(_selector_tail(e.get("selector"))) == key]
        if len(same) == 1:
            tail_owner = same[0]
    if tail_owner is None and tail and re.fullmatch(r"[a-z][a-z0-9-]*", tail[-1]):
        #: ⚠️ 另一副面孔（真站同一天的 `select` 那格）：模型写的是
        #: `form:has(…) select`（**光秃秃一个标签名**，尾巴没有鉴别力），
        #: 观测写的是 `… > label:nth-of-type(10) > select:nth-of-type(1)`。
        #: 判据照旧是**唯一**：这种标签在池子里**只有一个** ⇒ 认它。
        #: （`input` 那种满页都是的标签永远不会走到这儿 —— 唯一性先把它挡了。）
        same_tag = [e for e in pool
                    if isinstance(e, dict)
                    and _tail_key(_selector_tail(e.get("selector"), 1)) == (tail[-1],)]
        if len(same_tag) == 1:
            tail_owner = same_tag[0]
    best, best_rank = None, 99
    for element in pool:
        if not isinstance(element, dict):
            continue
        if element.get("selector") == selector or selector in (element.get("alternates") or []):
            rank = 0 if _frame_id_of_path(element.get("frame_path")) == frame_id else 1
        elif element is tail_owner:
            rank = 6 if _frame_id_of_path(element.get("frame_path")) == frame_id else 7
        else:
            rank = _id_is_the_elements_hint(element, selector, frame_id)
        if rank is None:
            continue
        if rank < best_rank:
            best, best_rank = element, rank
            if rank == 0:
                break
    return best


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
        #: ★ 2026-10-13（真站 `job-37d8e6bfa080` 那一族）：「**身上没字、标签在隔壁**」的
        #: 控件（compareinsulation 那排 `role=radio` 的圆点：`text` 空、id 是
        #: `#option-<时间戳>`）—— 只摘 text/role/near 的话，旁边那列标签**永远回不来**：
        #: 地址一漂，重放那一跳（`_relocate` → `_intent_score`）手里只剩 role 可用，
        #: 同一排里**挑中谁是运气**（先红用例实测：`#option-2` 被点成了上一颗）。
        #: form 那一支 2026-09-17 起就带这四样；click 这一支补上同一组 ——
        #: 重放那边本来就认 `nearby_text`，缺的只是它从没进过账本。
        #: `aria-label` 也是页面上印着的名字（只有它的按钮照样拿得到锚）。
        "label": ((element or {}).get("label") or (element or {}).get("aria_label")) or None,
        "hint": (element or {}).get("hint") or None,
        "placeholder": (element or {}).get("placeholder") or None,
        "nearby_text": list((element or {}).get("nearby_text") or []) or None,
        "selectors": _selectors_of(element, selector),
        "above_fold_only": False,
        "frame_id": frame_id,
    }

#: 一个「看起来就是美国邮编」的值（5 位数字）。用在下面那种**歧义**场合。
_US_ZIP_RE = re.compile(r"^\d{5}$")


def _kind_from_recorded_value(kind, value, element):
    """**看探索那一趟自己往这个框里写了什么**（证据，不是猜）—— 语义判不出来时的那一票。

    为什么需要它（2026-09-17 真站实测，量出来的）：
    gowizard 的邮编框身上**一个语义信号都没有** —— `label` 空、`hint` 是不透明的 MUI id
    （`textField-173838`）、`placeholder` 只是个例子（`e.g. 06801`）、`nearby_text` 是空的，
    而 `type="tel"`（为了弹数字键盘）。于是三档全落空、按 type 判成**手机号** →
    复跑时手机号被打进邮编框、页面红字拒收（用户在窗口里看到的就是它）。
    但账本里有一件**现成的证据**：探索那一趟模型自己往这个框里写过 `90210` / `75201` ——
    **邮编形状**。所以：`type=tel`（它本身就有歧义）而记下来的值像美国邮编 → 判 postcode。

    ★ 2026-10-09（用户：「**没有对应资料的话就对应随机** —— 之前填的是数字，生成的也要是数字」）：
    上一条从「只认 `tel` + 5 位邮编」**收成一条通例** —— `_shape_kind(value)` 说这一格要什么
    形状，就按那个形状给随机值（产物于是每趟生成**同形状**的随机，而不是把账本里那一个值
    每趟重填一遍）。真事（`job-5d7c20fbfe14`，fleet-solutions）：`#fleet_size` 那一格的
    `nearby_text` 抄的是**隔壁 State 下拉的选项长串**（`Select a State or Province*Alabama…`）
    ⇒ 判成 `state` ⇒ 名字成 `state-2`、兜底成了随机**州名**；而账本里那一趟填的是 `"5"`
    ⇒ 站方 `This field is required.` ⇒ 12 步全做完、点 Contact Us 没反应，从没到成功。

    ⚠️ **边界（这一票压得过谁、压不过谁）**：
      · 压得过**弱证据**（③ 周围写的字 / ④ html 的 `type`）—— 那一格自己什么都没说；
      · 压不过**强证据**（①② 字段自己的名字 / 它自己举的例子）—— 名字写着 `email`、
        而账本里是个数字，那是账本脏，不是这一格要数字。
    ⚠️ 形状认不出来（`_shape_kind` 给空串）⇒ **原样返回**，一个字都不改。
    """
    if (kind == "phone"
            and str((element or {}).get("type") or "").strip().lower() == "tel"
            and _US_ZIP_RE.match(str(value or "").strip())):
        return "postcode"                                # 老判据（2026-09-17）一个字不动
    shape = _shape_kind(value)
    if not shape or shape == kind:
        return kind                                      # 认不出形状 / 本来就是它 ⇒ 一个字不改
    if _strong_kind((element or {}).get("label") or "", element):
        return kind                                      # ①② 说了算
    return shape


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
    #: ★ 2026-10-08（真账本 `job-6589d75582d5`，lanternfocus.com）：**`select` / `check`
    #: 不吃随机池** —— 它们的值必须是**页面上真有的那个**（下拉的选项 / 勾选框的真假）。
    #: 池子里编出来的值在页面上根本不存在，重放时那一格必定失败。
    #:
    #: 真事：那一页的 `Country` 下拉，`nearby_text`（observe 爬三层祖先收的）里带着**邻格的
    #: 名字** `"Email"` ⇒ `_field_kind` 命中 `email` ⇒ 这一格原先会走下面那支 ⇒ 兜底值成了
    #: `{"random": "email"}` ⇒ 重放时拿一个随机邮箱地址去下拉框里找 ⇒ 那一步失败
    #: （报的是「没找到「Country」这个输入框，没填成」）。同一个页面上**没被误判**的
    #: `Hours` 那格拿到的是 `["4 to 6"]`（账本里真选过的那个选项）⇒ **一次过**。
    #:
    #: ⚠️ 这是**防御性的**：`_field_kind` 将来再怎么误判，下拉框/勾选框都不该被塞假值。
    #: ⚠️ 只管 `select` / `check`：**自由输入格照旧**吃随机池（拟人那件事一个字不改）——
    #: 所以判据落在 `kind` 上，不是「有 semantic 就不给池子」。
    random_ok = semantic is not None and kind == "value"
    #: ⚠️ 这一格记的是**账本上那个名字**（页面字段名）；给产物用的那个键在 `fills()` 里换
    #: （一处口径：谁写进产物只在那一处决定 —— 记录保持原样，读账的人看到的才是这一趟真记的）。
    return {"name": name, "source": name, "kind": kind,
            "label": label or name, "value": value,
            "fallback": ([{"random": semantic}] if random_ok
                         else _fallback(kind, value, label, element))}


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


#: 骨架里那两张表（`_ALIASES` / `_SPLIT_FROM`）——**从骨架自己那一份里读出来**给生成侧用。
#: ⚠️ **不另抄一份**：手抄一张表就是第二份真相源、会过期（`lint.py:387` 那条纪律；
#: 探针里那张 `LIB_KEYS` 之所以只能留在探针里，正是这个理由）。
#: 做法与 `lint.py` 同一路：模板占位符（`$states` 这种）不是合法 py ⇒ 先换成 `None` 再解析。
#: 读不动 ⇒ 两张空表：调用方按「**不知道**」办（绝不按「没有」办）。
_SKELETON_PLACEHOLDER = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*")


def alias_tables() -> tuple:
    """`(骨架里的 _ALIASES, 骨架里的 _SPLIT_FROM)`。"""
    try:
        tree = ast.parse(_SKELETON_PLACEHOLDER.sub("None", template.SKELETON.template))
    except SyntaxError:
        return {}, {}
    got: dict = {"_ALIASES": {}, "_SPLIT_FROM": {}}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            name = getattr(target, "id", "")
            if name in got:
                try:
                    got[name] = ast.literal_eval(node.value)
                except ValueError:
                    pass
    return got["_ALIASES"], got["_SPLIT_FROM"]


def _key_shape(text) -> str:
    """一个键的**形状**（只留小写字母数字）—— `firstName` / `first_name` / `first-name` 同形。"""
    return re.sub(r"[^0-9a-z]+", "", str(text or "").lower())


def library_key(name: str, data_keys=()) -> str:
    """页面字段名 → **这次那份资料里真有的那个键**；两边都对不上 ⇒ **原样返回**（不编）。

    ★ 2026-09-30（用户「修下吧」+ 探针报的三条 `[G·提示]`）：产物 `FILLS[*].source` 原来照
    **页面字段名**起（`phone`），而资料是**资料库的列名**（`phone_number`）—— 两边没有约定
    ⇒ 只有恰好同名的（`email`）取得到，其余靠骨架的别名表在**运行期**兜，兜不到就**静默**
    掉进随机池（真事：加拿大站被填了池子里的**英国**手机号，整单被拒）。
    这一格把「**资料里真有它**」这件事在生成期就落实：`phone_number` / `username` … 直接写进
    产物 ⇒ 运行期**直接命中**，不再依赖兜底（兜底那一套照旧留着，管别的拼法与老产物）。

    ⚠️ **只在资料里真有它时才改**：猜一个名字写进产物，会在资料用另一种拼法的部署上把
    「本来能取到」变成「取不到」——那是把病治成另一种病。资料不知道（没给 / 读不到）⇒
    一个字节都不动（就是今天的行为）。
    顺序：**同名 → 形状同（`firstName` ↔ `first_name`）→ 骨架别名表 → `_SPLIT_FROM`**。
    """
    given = [str(k) for k in (data_keys or []) if str(k or "").strip()]
    if not name or not given:
        return name
    if name in given:
        return name
    shape = {_key_shape(k): k for k in given}
    if _key_shape(name) in shape:
        return shape[_key_shape(name)]
    aliases, splits = alias_tables()
    #: ⚠️ ★ 2026-09-30（**用户问「确认资料不会乱映射吧」时当场逮到的**）：**「拆名」那一族
    #: 不参与改写**。骨架里那条路是**按 `source` 的名字**触发的
    #: （`_fill_value`：`whole = take(_SPLIT_FROM.get(source, ""))` 且 `parts[0] if
    #: source == "first_name"`）⇒ 把 `first_name` 改写成 `username` 之后那一支**不会触发**，
    #: 资料只给一个全名（`username: "Lin Cameron"`）时整串会被填进名字框 ✗
    #: （仓库注释里记着的那个老事故）。**整取**的那些（`phone → phone_number`）照旧改写。
    if name in splits:
        return name
    for cand in aliases.get(name) or ():
        if cand in given:
            return cand
    return name


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
    #: ★ 2026-10-08（policyquail 真站 `job-c8730acb27c5`：那份产物在 8100 上验出来
    #: 「Year of birth」被填了整串日期）：**「出生年」与「生日」不是一个形状**。
    #: 原先只有下面那一行 `(("birth", "dob"), "dob")` —— `birth` 一命中就判成 `dob`，
    #: 而 `dob` 池吐的是 `MM/DD/YYYY` ⇒ 往 `placeholder="YYYY"` 那一格打了 `07/23/1981`
    #: （每步都 ok、成功文案一直没出现；探路那趟其实**填对了** —— 账本里是 `"1975"`）。
    #: ⚠️ **必须排在 `birth`/`dob` 那一行前面**：它是子串匹配，`birth_year` 与
    #: `Year of birth` 都含 `birth`，排在后面等于没加。
    #: ⚠️ **不许收宽成裸 `year`**：`Vehicle year`（要的是近年，不是 1970-1995）、
    #: `Years at address`（要的是个位数）都会被它误伤 —— 同 `number` 那条的纪律。
    (("birth year", "birth_year", "year of birth"), "birth_year"),
    (("birth", "dob"), "dob"),
    #: ★ 2026-09-28（用户报的那一趟 `georgiapower`：「How many people live in your household?」
    #: 探路时 MCP 明明填的是**数字**，产出的 py 却填了个**姓名」）—— 那一格原先**没有**这一条，
    #: 于是落进 `_fallback` 的兜底默认值（`full_name`）⇒ 往「家里几口人」里填人名 ✗。
    #: ⚠️ **顺序是判据的一部分**：这一条排在 `phone` / `postcode` / `state` **之后** ——
    #: `Phone number` 会先命中 `phone`、`Zip/Postal Code` 先命中 `postcode`（别把它挪到前面，
    #: 否则「number」这个词会把手机号那一格吃掉）。
    #: ⚠️ **不许**放 `count` 这种短词：这一档是**子串**匹配，`count` 会命中 `Country` ✗✗
    #: （2026-09-28 当场撞上：`country` 那一格被这条判成了 `number`）。要加词先想一遍「它会不会
    #: 出现在别的字段名里」（`account` / `discount` / `country` 都含 count）。
    #: ⚠️ `years`（复数）是 2026-10-12 跟着下面那条 `address` 一起加的：`Years at address`
    #: 要的是**个位数**，而它原先哪一条都不命中 ⇒ 加了 `address` 之后会被认成街道
    #: （往「住了几年」里填一整串门牌）。复数写法不会误伤 `Vehicle year` / `Year of birth`
    #: （`year` 单数、且后者排在更前面）。
    (("how many", "household", "occupants", "people", "persons", "number of",
      "quantity", "years"), "number"),
    #: ★ 2026-10-12（真事 `job-7c0cb8a45c25` · nitrouscompetitions.com/auth）：**地址那一格没有这一条** ——
    #: 骨架那边本来就有 `street` 这个种类（`_ALIASES` 的键 + `_pool("street")` 那一支），
    #: 只有这张词表里没有 `address`/`street` 两个词 ⇒ 语义名永远轮不到它 ⇒ 退回**标签蛇形化**
    #: （`Address line 1` → `address_line_1`）⇒ 既不是语义名、也不在别名表里、又带数字 ⇒
    #: lint 规则 G 连打三轮（「形状不像运营资料的列名（带数字串）」）把整单卡停。
    #: ★ 2026-10-12（真数据复验当场逮到的）：`Town / City` 那一格**本来一个词条都不命中** ⇒
    #: 落到 ③ 的 `nearby_text` 上，而那一格旁边写着**邻格的** `Address line 2 (optional)`
    #: ⇒ 被下面那条 `address` 认成街道 ✗（真账本 `job-bcb1863324f4` 步 28）。
    #: 骨架的池子与 `_ALIASES` 里本来就有 `city` 这个种类，缺的只是这张表里没有词。
    (("town", "city"), "city"),
    #: ⚠️ **排在 `email` / `number` 之后**：`Email address` 先命中 `email`；而
    #: `Years at address` 要的是**个位数**，先命中 `number`（顺序反了就会把它认成街道）。
    (("address", "street"), "street"),
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


#: 「账本里那个值是什么形状」——**只认一眼看得出来的那几种**（认不出给空串，不猜）。
#:
#: ⚠️ **5 位数字故意不算 `number`**：那是美国邮编的形状（`_US_ZIP_RE`），
#: 而「邮编」与「件数」都是数字串 —— 这一档不许把邮编读成人数（下面 `_kind_from_recorded_value`
#: 那条 `tel`+邮编的老判据照旧先前一步生效）。
_SHAPE_NUMBER_RE = re.compile(r"^\d{1,4}$")
_SHAPE_PHONE_RE = re.compile(r"^\+?\d{10,11}$")
_SHAPE_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


def _shape_kind(value) -> str:
    """这个**值本身**是什么形状（`number` / `phone` / `email`）——认不出给空串。

    ★ 2026-10-09（用户：「没有对应资料的话就对应随机 —— 之前填的是数字，生成的也要是数字」）：
    账本里那一趟**真填进去、站点收下过**的那个值，是判定这一格要什么形状的**最后一票**。
    """
    v = str(value or "").strip()
    if _SHAPE_EMAIL_RE.match(v):
        return "email"
    if _SHAPE_NUMBER_RE.match(v):
        return "number"
    if _SHAPE_PHONE_RE.match(v):
        return "phone"
    return ""


def _strong_kind(label, element) -> str:
    """**强证据**那一半（`_field_kind` 的 ①②）：这一格自己的名字 / 它自己举的例子。

    为什么单独一个函数：弱证据（③ 周围写的字 / ④ html 的 type）**可以被账本里那个值的
    形状推翻**，强证据不行（见 `_kind_from_recorded_value`）—— 两半必须能分开问。
    """
    element = element or {}
    own = " ".join(str(x or "") for x in (label, element.get("label"), element.get("hint"),
                                          element.get("placeholder")))
    return _kind_by_words(own) or _kind_by_placeholder(element.get("placeholder"))


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
    kind = _strong_kind(label, element)                  # ①② 强证据（见 `_strong_kind`）
    if kind:
        return kind
    kind = _kind_by_words(" ".join(str(x or "") for x in (element.get("nearby_text") or [])))
    if kind:                                             # ③ 周围写的字（**弱**证据）
        return kind
    return _TYPE_HINTS.get(str(element.get("type") or "").strip().lower())   # ④ 也是弱证据


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
    #: ★ 2026-09-28（用户报的那一趟 `georgiapower`）：这一行原来一律给 `full_name` ——
    #: 于是「认不出是什么」的格子**拿到一个人名**（那一站往「家里几口人」里填了姓名 ✗✗）。
    #: 认不出来还塞一个姓名，既不是证据也不是兜底，是**编**，而且错得很难发现（它确实填了东西）。
    #: ⇒ 认不出时用**账本里那一趟真填进去的值**（站点当时收下过它 ✓）；
    #: 连值都没有 ⇒ **不给兜底**（产物那边就空着，如实报「没做成」），不再拿姓名去顶。
    return [value] if str(value or "").strip() else []


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


#: ★ 2026-09-30（用户裁定「IR 必须先证明完整」）：**账本里每页留下哪些格**。
#: 为什么非留不可：`observe` 回执里那串 `fields`（每个带 `required` / `stability` / `label`
#: / `selector`）是**唯一**能证明「这一页的必填格，产物到底有没有去填」的原料 ——
#: 而回执一大就整份不抄（`_receipt_of`），实测 551 行「页面上真有格」的 observe 里，
#: 账本留下的字段数是 **0** ⇒ `ir.validate` 那时候**想证明也证明不了**（无米之炊）。
#: 所以只抄**紧凑表**（白名单键 + 条数上限）：与 `_diag_row` 同一条纪律 ——
#: 少抄一格是「读的人看不见」，多抄一格是「账本和 checkpoint 一起涨」。
LEDGER_FIELD_KEYS = ("label", "hint", "type", "required", "stability", "selector")
#: 一页最多记几个格（同一页几十个格的站不少；超出的那部分**不静默丢**：见 `_field_row` 的尾巴）。
MAX_LEDGER_FIELDS = 30

#: ★ 2026-10-08：一页最多记几条**逐元素正文**（`node_texts`）。同 `MAX_LEDGER_FIELDS` 的理由：
#: 账本和 checkpoint 会一起涨。数量级是**量出来的**，不是拍的：
#: 【我量的·2026-10-08】真站一份 `observe` 回执（policyquail，`/tmp/pq-observe.json`）：
#: 45 条 / 共 1844 字（均 ~41 字一条）—— 而工具那边上限是 600 条（`nodeTextsLimit`，
#: 子帧多的页面才够得着），600 × 41 字 ≈ 24KB/行，比 `PAGE_HEAD_CHARS`(8000) 还大。
#: 120 条 ≈ 5KB 封顶。
#: ⚠️ ★ 2026-10-09 **更正**：这段原先还写着「这一格要服务的那道纠正（`_snippet_in_one_node`）
#: 找的是页头那 48 字落在哪个元素里」——**那句话是假的**，而且它当场误导过读代码的人
#: （2026-10-09 有人照着它推出「重挑只能从这 120 条里挑」，差点去改一个**修不了任何东西**的地方）。
#: 判据那一侧（`_when_for` / `_snippet_in_one_node` / `_unique_snippet_for`）读的是**模型里的
#: 那一份** —— `note_page(raw)` 收的是工具原始返回（单帧 400、合并多帧 600），**不是账本这一格**；
#: 全仓**没有任何一处**读账本的 `node_texts`（只有 `lint.py` 的一句注释提到它）。
#: 这一格是**给人看、供事后查**的 ⇒ 它只受「账本别涨太大」这一条约束。超出的**不静默丢**
#: （见下面那两个键）。
MAX_LEDGER_NODES = 120

#: 账本里**只有 `observe` 行**许写的那几个键（R3 的**前提**，破了当场抛 —— 见 `_summarize`
#: 的尾巴与 `_only_looks_carry_the_text`）。三格是**同一件事的三个投影**：「这一页长什么样」——
#: 一个整段（`page_text_head`）、一个逐元素（`node_texts`）、一个逐元素的**身份**
#: （`node_elements`）。判据那头读的是「带正文的行 == 落点认的那些行」这条等价，
#: 而「这页长什么样」一旦能从别处（别的一手工具）混进账本，等价就不成立了。
_TEXT_KEYS = ("page_text_head", "node_texts", "node_elements")


def _field_row(f: dict) -> dict:
    """一个字段进账本的样子：**白名单键、空值不写**（同 `_diag_row` 的理由与纪律）。

    ⚠️ `required` **只在为真时写**：`False` 与「没有这个键」在这条路上是同一个意思
    （"这一格不是必填"），而写一个 `False` 进去只会让账本里全是噪音键。
    """
    row = {k: f.get(k) for k in LEDGER_FIELD_KEYS if f.get(k) not in (None, "", False)}
    if f.get("required") is True:
        row["required"] = True
    #: ★ 2026-10-08（policyquail `job-c8730acb27c5`）：**看过这一格时它是不是空的**。
    #: `observe` 一直**回读**每一格的值（工具那边是**三态**：`null` = 不是值控件（按钮）/
    #: `""` = 是值控件但现在是空的 / 有值 = 现在装着什么），可投影进账本时被丢掉了
    #: （`LEDGER_FIELD_KEYS` 里没有它）⇒ IR 那一层只看得见「见过这个框」、看不见
    #: 「见的时候它空着」。两者差着一条真缺口 —— 这个站上 `#zip` 是站点按 IP **自己填好的**，
    #: 探路**正确地**没动它；而 `#birth-year` 是空的、产物必须填。
    #: ⚠️ 只落**是/否**这个「空」，**不落值本身**：那是访问者留在页面上的东西
    #: （`92582` 就是他的邮编），账本没有理由替它留档，判据也只需要那个是/否。
    #: ⚠️ 只认**空串**：`None` 是「不是值控件」，不许读成空。
    if f.get("value") == "":
        row["empty"] = True
    path = f.get("frame_path")
    if isinstance(path, (list, tuple)) and path:
        row["frame_path"] = [str(x) for x in path]
    return row


def _field_id(row: dict) -> tuple:
    """「是不是同一格」的判据：选择器 + 标签 + hint（任一处相同就算同一格）。"""
    return (str(row.get("selector") or ""), str(row.get("label") or ""),
            str(row.get("hint") or ""))


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
        #: ★ 2026-09-30：**格的清单**（紧凑表）—— `fields` 那个数只够说「有几个」，
        #: 说不清「是哪几个、哪几个必填」；IR 那一层要证明「必填格有没有对应的一步」，
        #: 靠的就是这一格。上限到了就停下来并**记一个数**（不静默丢，见 `_field_row`）。
        rows = [_field_row(f) for f in (raw.get("fields") or []) if isinstance(f, dict)]
        out["page_fields"] = rows[:MAX_LEDGER_FIELDS]
        if len(rows) > MAX_LEDGER_FIELDS:
            out["page_fields_capped"] = len(rows)
        #: ★ 2026-10-08：**逐元素正文**（`node_texts`）—— 抄进账本是为了**能事后回答
        #: 「这条判据当时为什么是这么剪的」**（形状 A 那一趟就卡在这儿：盘上任何一本账本都
        #: 答不出来，只能重跑一趟真站）。同 `page_fields` 的纪律：白名单 + 上限 + **超了记一个数**。
        #: ⚠️ ★ 2026-10-09：原先这里写着它是「判据那道纠正（`_snippet_in_one_node`）**唯一的
        #: 原料**」——**假的**。那道纠正读的是**模型里的** `node_texts`（`note_page(raw)` 那一份），
        #: 账本这一格**没有任何判据逻辑在读**（见 `MAX_LEDGER_NODES` 那段更正）。
        raw_nodes = [_norm(t) for t in (raw.get("node_texts") or [])]
        #: ★ 2026-10-08：**每一段正文配一个「它是啥元素」**（标签@区域）—— 与 `node_texts`
        #: 逐条对应（工具那边同一个循环产出的，见 `PageModel.NodeElements`）。
        #: 为什么账本要它：光有文字 ⇒「这一组为什么是这个门」事后**查不出来**
        #: （真站形状：判据落在 `Skip to main content` 上，只有回头看元素身份才知道那是
        #: 页头那个无障碍跳转链接、全站每页都有）⇒ 读账本的人只能再跑一趟真站。
        #: ⚠️ 两条**一起**滤、**一起**截：错位比没有这一格更坏（每一句都配着别人的名字）。
        raw_kinds = [str(k or "") for k in (raw.get("node_elements") or [])]
        if len(raw_kinds) != len(raw_nodes):
            raw_kinds = [""] * len(raw_nodes)     # 对不上就别猜：宁可缺，不许错位
        pairs = [(t, k) for t, k in zip(raw_nodes, raw_kinds) if t]
        nodes = [t for t, _ in pairs]
        kinds = [k for _, k in pairs]
        out["node_texts"] = nodes[:MAX_LEDGER_NODES]
        out["node_elements"] = kinds[:MAX_LEDGER_NODES]
        #: ⚠️ `node_texts_truncated` 对读的人**只有一个意思**：这份清单**不是这一页的全部**
        #: （工具那边就截过，或者账本这一道上限截过）⇒「这段判据不在单个元素里」**不能当结论**
        #: （与 `_snippet_in_one_node` 拿它做的事一致）。账本自己截掉多少另记一格（诊断用），
        #: 两个原因不许混成一个数：一个是「页面上元素多到看不全」，一个是「账本没抄那么多」。
        if raw.get("node_texts_truncated") or len(nodes) > MAX_LEDGER_NODES:
            out["node_texts_truncated"] = True
        if len(nodes) > MAX_LEDGER_NODES:
            out["node_texts_capped"] = len(nodes)
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
    leaked = [k for k in _TEXT_KEYS if k in out] if name != "observe" else []
    if leaked:
        raise _TextPremiseBroken(
            "工具「%s」的结果里冒出了 %s —— R3 的**前提破了**"
            "（前提：正文那几个键只有 `observe` 写；本函数是唯一的写点）。"
            "R3 判「过没过成功线」时只认 `observe` 行当落点，别的行带正文会让"
            "「窗口里看见过」漏判 ⇒ **提交会留在前缀里**，重放它 = **往真实站点"
            "再交一次真实表单**。要么把这几个键从这个工具的结果里去掉，要么先改 R3 的落点"
            "（`_landing_index`）让它也认这一类行 —— 别让它就这么过去。"
            % (name, "、".join("`%s`" % k for k in leaked)))
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
    #: ★ 2026-10-13（真站那一趟：一排 `role=radio` 的圆点全没文字，账本里六步都叫
    #: 「没写名字的元素」——**挑对了也看不出挑的是哪一颗**）：**标签也算名字**，取写在
    #: 这颗**旁边**的那一句（`nearby_text` 的第一条 = 离它最近的那句）。
    #: 与「取值名不许退成 field-N」同一族：名字是给运营和诊断读的那一格。
    #: ⚠️ 有 text/label/name 时**仍用它们**（这里是兜底，不抢位）；取的是页面上写着的字，
    #: 所以也不碰下面那条 F3（它禁的是把**选择器**端出来）。
    near = [str(x).strip() for x in (target.get("nearby_text") or []) if str(x).strip()]
    if near:
        return near[0]
    # ⚠️ 没有文字名字时**不许**把选择器端给人看（2026-09-18 复审 F3）：这句话进的是
    # `_say` → `step["note"]` → 时间线的 `events[].say` **和**轮次卡片 —— 那一屏是给
    # 运营看的（D16：主视图里没有选择器）。选择器**本来就在** `target["selectors"]` 里
    # （账本上没丢），排查时对得回页面；端进句子里是**多此一举地把实现细节摆上主视图**。
    # 与 `template.py` 渲染进产物那份同一个说法（那边写着「不把选择器端给人看」）。
    return "没写名字的元素"


def _same_address_same_state(steps: list) -> dict:
    """同一个状态里**同一个地址**被记了两遍（名字却不同）⇒ 后面的名字让给前面那个。

    ★ 2026-09-28 真事（travelandleisure 那一单）：探路时来回走了一趟，同一张报名表的
    6 个格子被记了**两遍**，第二遍拿到了别的名字（`field` 与 `field-7` 都指
    `#enroll-firstName`）。`_last_fill_wins` 只按**名字**去重 ⇒ 一个都没删 ✗
    ⇒ 产物把同一张表**填两轮**，第二轮那页上已经没得填了：真实日志里第 8 步起
    「没找到 field-4」，重试组一跳之后 7 格全找不到。
    ⇒ 名字不同、**地址相同**的，按同一个字段论：留前面的名字，后面的改指过去。

    ⚠️ 仍然只在**同一个状态内**：跨状态填同一格是另一件事（流程里第二次问它）。
    """
    seen: dict = {}
    alias: dict = {}
    for step in steps or []:
        info = step.get("result") or {}
        name = ((info.get("fill") or {}) or {}).get("name")
        sels = (step.get("target") or {}).get("selectors") or []
        if not name or not sels:
            continue
        key = (step.get("state") or START_STATE, str(sels[0]).strip())
        if key in seen and seen[key] != name:
            alias[name] = seen[key]
        else:
            seen.setdefault(key, name)
    return alias


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


def _has_usable_target(target) -> bool:
    """这一步**有没有可用落点**：`selectors` 里有一串，或 `text`/`role`/`near` 至少一个有话。

    ★ 判据与 `template._steps_must_be_findable`（产物门口那道闸）**同源** —— 一处口径两种用法：
    那边拒的是「认不出来」，这边让它在**翻译成产物之前**就不可重放。
    ⚠️ 「有意图 ⇒ 放行」不是放松：仓里有一整族产物是「声明里一条选择器都不给、全靠运行期
    `observe` 给的池子认」的（随机选答案那族也走它）。
    """
    t = target or {}
    if [s for s in (t.get("selectors") or []) if str(s or "").strip()]:
        return True
    return any(str(t.get(k) or "").strip() for k in ("text", "role", "near"))


#: 一个 click 的目标**叫不叫得出名字**（判 `auxiliary` 的第一条，见 `_not_load_bearing_clicks`）。
_TARGET_NAME_KEYS = ("text", "label", "placeholder", "aria_label", "name", "hint")


def _has_a_name(target: dict) -> bool:
    """这个目标**叫得出名字**吗（文字 / label / placeholder / aria-label / name / hint 之一）。

    ⚠️ 为什么这一条是 `auxiliary` 的**前提**：有名字的（`Submit` / `Schedule Online`）是漏斗
    的一部分，它失败**必须**照样否决 —— 否则「产物其实没走通」会被判成「过了」。
    """
    return any(str((target or {}).get(k) or "").strip() for k in _TARGET_NAME_KEYS)


def _target_key(target: dict) -> tuple:
    """一个目标的**身份**（重放时拿什么去认它）—— 就是它那串选择器。"""
    sel = (target or {}).get("selectors")
    return tuple(str(s) for s in sel) if isinstance(sel, (list, tuple)) else ()


def _not_load_bearing_clicks(steps) -> set:
    """**探路自己就作成过、也做败过**的那些目标 —— 它们那一步不承重。

    ★ 2026-10-09（用户：「浮窗本来就是类似弹窗有时候有有时候没有，不能把它认为不做就
    继续不下去」）。现场（`job-22e8c86afe10` 的账本，逐字）：
      step 30  click 「没写名字的元素」  ok=True    ← 浮窗那个 ✕，当时挡住了 Submit，顺手关掉
      step 45  click 「没写名字的元素」  ok=False   ← 同一趟里**又碰到它、没找着**
    产物忠实地把 step 30 抄成一步 ⇒ 自测时浮窗没出来 ⇒ 那一步没做成 ⇒ `selftest` 判
    「**承重步**失败」⇒ **整趟否决**（而产物自己一路跑到成功页、退出码 0）。

    为什么这条是**量出来的**、不是猜的：它说的是「**这一次探路里它自己就不稳**」。
    另一条路（认「这一下点的是不是遮挡物」）现场走不通 —— `observe` 的 `obstructions`
    在那一页上报的是**空**（`[]`），没有可靠的来源。

    ⚠️ 只在**同一个目标**上比（`_target_key`），不是「这一趟有失败就都松」。
    """
    failed: set = set()
    for step in steps or []:
        if step.get("action") != "click":
            continue
        if (step.get("result") or {}).get("ok"):
            continue                       # 只收**没做成**的那些
        key = _target_key(step.get("target") or {})
        if key:
            failed.add(key)
    return failed


def _target_for_replay(step: dict, target: dict) -> dict:
    """重放用的 target：把**这一步实际命中过的那个选择器**提到候选最前面。

    ★ 2026-10-10（`job-d037348fba4d` 那趟 agemate；用户报的是「点了继续一直不动」）。

    运行期的规矩是「`selectors[0]` 试起，**第一个命中就停**」—— 所以**顺序就是那个决定**，
    而声明顺序是 `_selectors_of` 拼出来的（`element.selector` + `alternates` + 兜底），
    **它不是「哪一条真的管用」**。这两件事能岔开：真站那一步的候选里，页脚的订阅框排第 0、
    弹窗里那个排第 5，而账本 `result.selector`（这一次真发出去、也真成了的那个）是第 5 个。
    照原顺序重放 ⇒ 当场命中页脚那格 ⇒ 邮件填错框、弹窗提交被站方顶回
    （`Please try again / Verify Email`）⇒ 自测永远走不到成功 ⇒ `diagnose` → `draft` 重写
    ⇒ **重写出来的还是同一份（同一个顺序）** ⇒ 三版稿撞同一堵墙，图停在闸上等人点「继续」。

    ⚠️ 依据是**账本自己记的那一格**（`result.selector` = 这一次真发出去的参数），不是猜：
    没记（老账本 / 工具没这一步）就**原样返回**，一条都不动 —— 那时候编一个顺序出来
    就是拿猜当证据（负控钉在 `test_a_step_with_no_recorded_selector_keeps_the_declared_order`）。
    ⚠️ 命中的那条**不在候选里**也照样提到最前：它在这页上真成过，比候选里任何一条都硬。
    候选**一条都不丢**，只是顺序变了（它们仍然是「这一格还可能在哪」的证据）。
    ⚠️ 这里**只管「先用哪条」**：脆不脆由 `template._stable_first` 另判
    （它按渲染序号 / `nth-of-type` 跳数把那几族贬到最后一档）—— 两件事，别混。
    """
    used = str((step.get("result") or {}).get("selector") or "").strip()
    declared = [s for s in (target.get("selectors") or []) if s]
    if not used or not declared:
        return target
    return dict(target, selectors=[used] + [s for s in declared if s != used])


# ── 「这一步是环境噪声」的判据（★ 2026-10-13）────────────────────────────
#
#: ★ 2026-10-13（用户裁定「都收吧」）：**「点掉同意条 / 弹层」那一类点击是环境噪声，不是业务动作。**
#:
#: 为什么它在步骤表里是个错：这类东西**天生有时有有时没有** —— 生产每单都是新窗口（清 cookie）
#: ⇒ 有；上一趟已经点掉过 ⇒ 没有；它还可能**后冒出来**（lovesac 的 Fides 是请求之后才插进来的）。
#: 而产物的**前提层**本来每步都在问一遍钩子表（`template._POPUP_HOOKS`：开跑前 + 每一步动手前后）
#: —— 所以表里那一步是**冗余**的；冗余还硬（找不到就判死、还打回整组）就是两堵墙。
#:
#: 真事 `job-c02d5768e535`（compareinsulation）：模型对**同一条同意条**点了两次
#: （`Accept all` / `Accept All Cookies`）⇒ 表里两条步骤 ⇒ 产物开跑前钩子已经点掉过一次
#: ⇒ 第 1 条豁免、**第 2 条判死** ⇒ 整组重试 ⇒ 自测挂两遍 ⇒ 人只能喊停。
#:
#: 判据（**宁可漏，不可误标** —— 标错 = 真失败被读成跳过）：
#:   ① **强同意短语**（`allow all` / `reject all` / `accept cookies` / 全部接受 / 全部拒绝）；
#:   ② 或者文字里**同时**有「同意语境」（cookie/consent/gdpr/privacy/同意/隐私 + 常见供应商
#:      slug：fides / onetrust / osano / trustarc / cookieyes / usercentrics / didomi / klaro …）
#:      与「对同意做个决定」的动作词（accept / agree / allow / reject / decline / deny / 接受 / 拒绝）。
#: 光一个 `Accept`（业务按钮，比如「Accept offer」）**不算** —— 那就是要漏掉的那种。
#: 判的文本 = 这一步的 note + 目标的文字 + 它声明的选择器（三者里任意一处带证据就算）。
_CONSENT_STRONG = re.compile(
    r"(?:reject|accept|allow|deny|decline)\s+(?:all|cookies?)"
    r"|(?:全部|接受全部|拒绝全部)\s*(?:接受|拒绝)?", re.I)
_CONSENT_CTX = re.compile(
    r"cookie|consent|gdpr|privacy|tracking|同意|隐私|fides|onetrust|osano|trustarc|cookieyes"
    r"|usercentrics|didomi|klaro|termly|iubenda|quantcast|cassie|complianz|borlabs|securiti"
    r"|sf-consent|cc-dismiss|\bcmp\b", re.I)
_CONSENT_DECIDE = re.compile(
    r"accept|agree|allow|reject|decline|deny|接受|同意|拒绝", re.I)


def _consent_click_like(note: str, target: dict) -> bool:
    """这一步是不是「对同意做个决定」（见上面那段：标 `soft` 用）。判据与理由都在那儿。"""
    text = " ".join([str(note or ""),
                     str((target or {}).get("text") or ""),
                     str((target or {}).get("label") or ""),
                     " ".join(str(s) for s in ((target or {}).get("selectors") or []))])
    if _CONSENT_STRONG.search(text):
        return True
    return bool(_CONSENT_CTX.search(text) and _CONSENT_DECIDE.search(text))


def _replay_step(step: dict):
    """一个 Journey 步骤 → 产物里的一步（不可重放的返回 None）。"""
    action = step.get("action")
    if action not in REPLAY_ACTIONS:
        return None
    if not (step.get("result") or {}).get("ok"):
        return None                      # 没做成的动作不该在重放里再来一遍
    target = _target_for_replay(step, step.get("target") or {})
    note = step.get("note") or ""
    if action == "click":
        #: ★ 2026-09-24（真站逼出来的）：点一个**认不出来**的元素 ⇒ 这一步**不可重放**。
        #: 现场：探路误点了一个没名字的元素（真站 trace 里那一步 `selector_used` 是空的 ✓）⇒
        #: 账本把它记成一步 ⇒ 产物忠实翻译 ⇒ 自测卡在第 8 步「页面上没找到」⇒ **三版稿撞同一堵墙**。
        #: 「不可重放」这个概念本来就有（返回 `None`）✓ —— 不用新造机制。
        if not _has_usable_target(target):
            return None
        out = {"action": "click", "note": note, "target": target}
        #: ★ 2026-10-13：**「点掉同意条/弹层」那一步标 `soft`** —— 它有就点、没有就算了，
        #: 找不到时**不算这一步没做成、也不打回整组**（产物那一侧的执行支见
        #: `template.py` 的 `soft` 那段）。判据与理由见上面 `_consent_click_like`。
        if _consent_click_like(note, target):
            out["soft"] = True
        return out
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
    MCP 工具表是固定的那 8 个（`tools/cdp/internal/mcp/registry.go`），其余工具的结果里
    没有那一段（`diff` 只回 `actionable`，`PageText` 只长在 observe 的 `PageModel` 上）；
    盘上真账本 342 行里，非 `observe` 行带这一键的 **0 行**。

    ★ 2026-10-13（`job-4d12a95edf34`）：`success_line`（每一步那一读里含判据的那一截）
    **是故意长在非 `observe` 行上的**，而且**不在** `_TEXT_KEYS` 里 —— 所以这一问照旧不响。
    那不是绕开它，是上面那条推导在它身上**方向相反**：那一键只会让 `first_hit` **更早**
    （多出来的文字不会让它变晚）⇒ R3 更早停下 ⇒ 前缀更短，「提交留在前缀里」这种危险
    **没有**。前提那句「带正文的行 == 落点认的那些行」今天要读成：
    「带 `_TEXT_KEYS` 的行 == 落点认的那些行」。
    """
    for i, row in enumerate(rows or []):
        res = (row or {}).get("result")
        if not isinstance(res, dict):
            continue
        carried = [k for k in _TEXT_KEYS if res.get(k)]
        if not carried:
            continue
        action = str((row or {}).get("action") or "")
        if action != "observe":
            raise _TextPremiseBroken(
                "第 %d 行是「%s」，可它的结果里带着 %s —— R3 的**前提破了**"
                "（前提：正文那几个键只有 `observe` 那些行才写）。R3 判「过没过成功线」时"
                "**只认 `observe` 当落点**，别的行带正文会让「窗口里看见过」漏判 ⇒ "
                "**这一步（常常就是提交）会留在前缀里**，重放它 = **往真实站点再交一次真实表单**。"
                "这里不猜：先把前提收回来（该写这几个键的只有 `_summarize` 的 `observe` 那一支），"
                "再决定 R3 的落点要不要跟着改大。"
                % (i + 1, action or "（这一行没写动作名）",
                   "、".join("`%s`" % k for k in carried)))


def replayable_prefix(steps: list, success_text: str, *,
                      success_urls: str = "",
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
    - **R3 读哪份文字**：账上**按顺序**记下的每一天的正文 —— `row_text(row)`，
      **与活着那一趟的 `_success_hit`、与结算的 `graph._explore_reached_success`
      是同一个函数**（★ 2026-10-13 之前这里只累计 `page_text_head`，而那把头 600 字的尺子
      够不着挂在文档末尾的浮层文案 ⇒ 出现「活的探路见着了、重放说没见着」⇒ 重放会把
      **提交**当成可重放的一步）。一个刻意的选择留着：用**有序**的步骤，不用 `pages[]`
      （那份没有位置 —— 拿它判「这条线是什么时候过的」只能得到「一过就整段作废」，
      而设计只要「过线之后的动作不许重放」）。
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
    wants = [w.lower() for w in wanted_texts(success_text)]
    urls = [u.lower() for u in wanted_urls(success_urls)]
    if not wants and not urls:
        raise ValueError(
            "重放前缀要一个成功判据（`success_text` 或 `success_urls`）—— "
            "两格都空不算「没有约束」，是**少给了一个输入**"
            "（R3 唯一的输入就是它，而 R3 管的是「过了成功线之后不许再动真页面」）")
    #: ★ 2026-09-24：判据**两格都算**（用户点名「那不能一样加个 success_url 吗」）——
    #: R3 与活着那一趟的 `_success_hit` 必须**一直是同一把尺子**（同一个 `_norm` + 小写 + 子串），
    #: 两边口径分家的后果是「活的探路说见着了、重放这边说没见着」⇒ 重放敢往成功线之后走
    #: ⇒ 真的再交一次表。

    rows = list(steps or [])
    #: R3 的**前提**先验（破了就抛，不静默）—— 下面每一行的推导都压在它上面。
    _only_looks_carry_the_text(rows)
    #: 走到每一行时「观测到的页面文字」累计到哪儿了（**按顺序**累，R3 要的就是这个顺序）。
    seen_text: list = []
    #: ★ 2026-09-24：**每一行落在哪一页**（网址那一格判据在这上面找）—— 同样按顺序累。
    #: ⚠️ 别叫 `seen_urls`：下面那个是给 R4 用的**去重集合**，两个名字撞在一起读不出来。
    seen_at: list = []
    blob = ""
    for row in rows:
        #: ★ 2026-10-13：读的是 `row_text`（**与活着那一趟、与结算同一把尺子**）——
        #: 原先只累计 `page_text_head`（头 600 字），于是「活的探路当场见着了、
        #: 重放这边说没见着」，而重放会把**提交**当成可重放的一步 = 再交一次真表单。
        #: ⚠️ 只会**更早**看见那条线（多读到的文字不会让 `first_hit` 变晚）⇒ 前缀只会更短，
        #: 方向是安全的那一边。
        blob = (blob + " " + row_text(row)).strip()
        seen_text.append(blob)
        seen_at.append(row_url(row).lower())
    #: 判据**最早**在第几行那次观察里出现（None = 这一趟压根没出现过）。
    #: ⚠️ 两格是**或**的关系（文字见着了、或网址对上了，都算过了那条线）。
    first_hit = next((j for j in range(len(seen_text))
                      if (wants and criterion_in(seen_text[j], wants))
                      or (urls and any(u in seen_at[j] for u in urls))), None)

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
            return prefix, _crossed_line_why(i, row, _criterion_why(success_text, success_urls),
                                             first_hit)
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


def _criterion_why(success_text, success_urls) -> str:
    """判据在**人话**里怎么写（`_crossed_line_why` 那句要用）。

    ⚠️ 之前这里直接把 `success_text` 塞进 `%s`：判据是一个**列表**时，那句人话会印成
    `['a', 'b']`（本仓判据本来就允许列表 —— `wanted_texts` 就是按列表写的）。
    """
    texts = wanted_texts(success_text)
    urls = wanted_urls(success_urls)
    parts = []
    if texts:
        parts.append("页面上的「%s」" % "」、「".join(texts))
    if urls:
        parts.append("网址里的「%s」" % "」、「".join(urls))
    return "、".join(parts)


def _crossed_line_why(i: int, row: dict, criterion: str, hit: int) -> str:
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
        return ("第 %d 步「%s」不能重放：成功判据（%s）是在**没有动作的那一眼**上出现的"
                "（第 %d 行那次观察；它前面那条账也是一次观察）—— 可能是页面上本来就有的一句"
                "普通话**撞**了，也可能是更早某一步把页面带到这儿、那句话这一刻才渲染出来；"
                "**这两种系统分不出**，所以保守到底：这一步之前的前缀照重放，"
                "这里之后一步都不走（R3）。"
                % (i + 1, _step_label(row), criterion, hit + 1))
    return ("第 %d 步「%s」不能重放：它**落到的那一页**（第 %d 行那次观察）上已经出现了"
            "成功判据（%s）—— 过了那条线之后的每一个动作都可能是**重复的真实请求**（R3），"
            "前缀停在它前面。"
            % (i + 1, _step_label(row), hit + 1, criterion))


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


#: 回执里那一行身份描述的形状（Go 侧 `ClickCandidate.Describe()`，`tools/cdp/internal/click.go`）：
#:
#:     <tag> ["文本"] [aria-label="标签"] [x,y,w,h] [disabled]
#:
#: ⚠️ 两个引号段在 Go 那侧是 `%q`（双引号 + 反斜杠转义），`文本` / `标签` 各按**字符**截到
#: 32 并加 `…`。这里**不做反转义**（见 `card_from_receipt` 的 docstring）：宁可认不出。
_RECEIPT_CARD = re.compile(
    r"^<(?P<tag>[A-Za-z][A-Za-z0-9]*)>"
    r"(?: (?P<text>\"[^\"\\]*\"))?"
    r"(?: aria-label=(?P<label>\"[^\"\\]*\"))?"
    r" \[(?P<box>-?\d+,-?\d+,-?\d+,-?\d+)\]"
    r"(?: disabled)?$")


def card_from_receipt(text) -> Optional[dict]:
    """回执里那一行描述 → **身份卡**（`{"label": …, "text": …}`，只放有值的那几样）。

    ★ 2026-10-12 现场（`job-3e99eb67519e` · wildnutrition，账本 `runtime/explore/
    job-3e99eb67519e/attempt-1.jsonl` 第 30/32/37 步），逐字：

        步 30  target={"text": null, "role": null, "near": null, "selectors": ["…li:nth-of-type(3)"]}
               receipt={"match_count": 1, "target": "<li> \\"Every now and thenEvery now and \\""
                                              " aria-label=\\"Every now and then\\" [61,252,240,45]", …}

    **同一个元素、同一次工具调用** —— 回执里连 `aria-label` 都印出来了，账本里那张卡却是
    空的（根因见 `_card_from_receipt_into`：摘卡那一跳在上一次观测的模型里没找到它）。
    名字**当时就量到了**，只是没留下来 —— 这个函数是留下它那一跳。

    键名照 `_target_of` 的既有约定：**`aria-label` 落 `label`**（判据认的键是 `label`，
    落成 `aria_label` 等于白补）；`text` 是元素自己的 innerText，**照抄原话**
    （真站上它会重复几遍 —— 那是事实，别替它洗）。

    ⚠️ **认不出 ⇒ `None`，一个字都不许猜**（这段文本是**证据**，下游判据与重放都拿它当
    「量到的事实」用）。具体挡在这儿的是：
      · `tag` 必须有（`<` 开头 `>` 结尾）、bbox 必须有且是四个整数；
      · 尾巴上的 `disabled` 可以有（Go 侧只在禁用时印它），多出别的词就不认；
      · 两个引号段里**不许有反斜杠** —— Go 的 `%q` 在这里会转义（`\\"` / `\\n` / `\\uXXXX`），
        而这一层**不做反转义**：转义表写错一格，捡回来的就是一个**假**名字，
        比没有名字更坏（判据会说「量到了」）。
      · 解出来 `text` / `label` 一个都没有（例：`<div> [1,2,3,4]`）⇒ 也是 `None`：
        什么都不带的卡不是卡。
    """
    if not isinstance(text, str) or len(text) > RECEIPT_STR_CHARS:
        #: 长度那条与逐字转抄**同一把尺子**（`RECEIPT_STR_CHARS`，见 `_receipt_of`）：
        #: Go 那侧那一行最长也就 ~130 字符（tag + 32 字符的文本 + 32 字符的标签 + bbox）
        #: —— 比它还长的「同一形状」，只能是从别处来的东西，不捡。
        return None
    m = _RECEIPT_CARD.match(text)
    if not m:
        return None
    card: dict = {}
    label = m.group("label")
    if label is not None and label[1:-1].strip():
        card["label"] = label[1:-1]
    raw_text = m.group("text")
    if raw_text is not None and raw_text[1:-1].strip():
        card["text"] = raw_text[1:-1]
    return card or None


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
        """记一页。返回**上一个状态名**（说明换页了），第一页返回 None。

        逐元素正文（`node_texts`）现在**就在 `model` 里**（`cdp observe` 2026-09-29 加的那一格）
        —— 所以这里既没有额外参数、也没有额外调用。
        """
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
        #: ★ 2026-10-08：除了「紧挨着的上一页」（`avoid`），把**前面每一页**都递进去 ——
        #: 判据要躲开的是「别的页上也有」的那段，而它常常不在上一页、在更早那几页上
        #: （真站：`inquire` 的上一页是 reCAPTCHA，撞的却是**首页**那句跳转链接）。
        when = _when_for(model,
                         avoid=_norm(((self._current or {}).get("model") or {})
                                     .get("page_text") or ""),
                         others=[(p or {}).get("model") or {} for p in self.pages])
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


def _when_for(model: dict, avoid: str = "", others=()) -> dict | None:
    """一个状态的 `when`：**进这个状态时**那一页的判据。

    `url_contains` 用**稳定前缀**（`_stable_url`）：先去掉 query 与 fragment —— 重放时 query
    常常不一样（utm、step 号、A/B 参数）；再把**看着像轮换码的末段**丢掉 —— 真站实测
    （homebuddy）`…/walk-in-showers/cr640` 重放时是**同一个页面的轮换变体** `…/gt1791-1`，
    钉整条 path 的 `when` 一条都不成立、7 步**全被跳过**，而 `when` 存在的理由正是
    「防 A/B 变体、防步骤增减」。`when` 判太严的后果**永远是**整组步骤被静默跳过。
    正文那一段是从这一页的 `page_text` 里**取的原文** —— 它是当时那一页的**子串**，
    所以必然成立（`when_holds` 在生成时就会验一遍）。
    ⚠️ ★ 2026-09-24：`avoid`（上一页的正文）**必须传**（`note_page` 传的就是它）——
    不然两页共用的页头会变成两页**同一个门**（见 `_snippet` 那条）。

    ⚠️ **正文那半条钉不出来的时候要说出来**：返回的 `when` 上带一句 `text_why`
    （见 `WHEN_TEXT_DROPPED`）。这是**有损**的（判据少了一半），而有损必须说 ——
    不然产物上「只有 URL」与「判据本来就只有 URL」长得一模一样，读的人分不出
    「查过了，只有 URL 稳」和「没钉出来」。**不许**悄悄退化成只有 `url_contains` 一条。

    ★ 2026-09-29（用户裁定「走 A」）：逐元素正文**由 `cdp observe` 一起给**
    （`model["node_texts"]`，零额外调用 —— 另开一次 eval 会撞探索期的预算）。
    它的用途只有一个：**别把跨元素拼出来的那句话当判据**（缺陷单的正题）。
    ⚠️ `node_texts_truncated` 为真时**不下结论**：那一格被**截断**过（叶子元素比上限多），
    判据的元素可能就排在截断线之后 ——「量不到」与「没有」是两件事。
    """
    when: dict = {}
    url = _stable_url(model.get("url") or "")
    if url:
        when["url_contains"] = url
    text = _norm(model.get("page_text") or "")
    snippet = _snippet(text, avoid=avoid)
    spliced, avoided, element = "", "", ""
    #: ★ 2026-10-09：剪到的那段**落在公用那一层**（页头/导航/页脚/弹窗）⇒ 改从这一页
    #: **自己**的话里剪。为什么非改不可：那 600 字被壳占满时，剪出来的必然是全站公用文案
    #: ⇒ 撞车 ⇒ IR 拦下整趟（`observe.go` 每帧切 600 字，而 `main` 常常排在第 299 条）。
    #: ⚠️ **只在「剪到的是壳」时改道** —— 没撞的门判据一个字节都不许变（同
    #: `_repair_ambiguous_whens` 那条纪律）。
    #: ⚠️ 改道之后取的是**某一个元素自己的开头**（`_snippet(单条)`），
    #: 所以顺手把「跨元素拼」那个病也躲开了；下面那道验照旧会跑，只是验的已经是新的。
    from_public = ""
    if snippet and _cut_landed_on_chrome(model):
        #: ⚠️ 还要躲开「在**自己这页的公用区域**里也出现」的那些 —— 真站当场逮到过
        #: `/contact/` 定成 `'Contact Us'`，而那句也是导航项的标签（见 `_also_in_chrome`）。
        own = [s for s in (_snippet(_norm(t)) for t in _own_word_nodes(model))
               if len(s) >= _WHEN_REPICK_MIN_CHARS and not _also_in_chrome(s, model)]
        better = next(iter(own), "")
        if better:
            from_public, snippet = snippet, better
    nodes = model.get("node_texts")
    if snippet and isinstance(nodes, list) and nodes and not model.get("node_texts_truncated"):
        # ★ 2026-09-28（缺陷单《产物判据跨元素拼接》§六 A.4 —— 那份单子的**正题**）：
        # 判据必须是**某一个元素自己的话**。从合并后的 `page_text` 里剪一段，剪点可能落在
        # **两个元素中间**（真站实测：`Main Content Schedule your residential` 是
        # a11y skip-link 的尾巴接上主标题开头拼出来的）⇒ 重放时那句话**在页面上根本不存在**
        # ⇒ 这一组静默跳过，而后面每一组跟着垮（georgiapower 那单：真做 2 步、跳 23 步）。
        # 这里把剪出来的那段拿**逐节点正文**验一遍：不落在单个节点上就换成落在的那个。
        got = [str(t or "") for t in nodes]
        if got and not any(_norm(snippet).lower() in t.lower() for t in got):
            better, skipped, element = _snippet_in_one_node(
                snippet, got, others=others, kinds=model.get("node_elements") or [])
            if better:
                spliced, snippet = snippet, better
                avoided = _snippet(_norm(skipped)) if skipped else ""
            else:
                snippet, spliced = "", snippet
    if snippet:
        when["text_contains"] = [snippet]
    if not when:
        return None
    if snippet and when_holds(when, model):
        if spliced and element:
            #: ★ 2026-10-08：**这一段是从哪个元素剪的**（标签@区域）—— 用户当场要的那句
            #: 「探路的时候告诉我们这个对应的文本到底是在啥元素找到的」。
            #: 为什么非要有它：判据是从正文里剪的一段话，而那句话**自己是看不出出处的**
            #: （`Skip to main content` 可以是页头那个跳转链接、也可以是正文里的一句话）。
            when["text_from"] = WHEN_TEXT_FROM % element
        if spliced:
            #: 换了判据（或换成只有 URL）时**必须说**：读产物的人要能看见
            #: 「原来剪出来那串跨了两个元素、现在用的是哪一个」。
            when["text_why"] = WHEN_TEXT_SPLICE % spliced[:60]
        if avoided:
            #: ★ 2026-10-08：躲开「别的页上也有的那段」这件事**必须说**（有损/有改就要说）：
            #: 读账本的人要能看见「门原来是那句公用文案、现在换成这一页自己的」——
            #: 不然「这一组为什么是这个门」在账上又变成一个查不出来的问题。
            when["text_why"] = WHEN_TEXT_AVOIDED % (avoided[:60], snippet[:60])
        if from_public:
            #: ★ 2026-10-09：改道这件事同样**必须说**（有改就要说）。
            when["text_why"] = WHEN_TEXT_FROM_PUBLIC % (from_public[:60], snippet[:60])
        return when
    # ── 退化成「**只有 URL**」那一条 ────────────────────────────────────
    # ⚠️ 这一支是**有损**的（判据少了一半，站点改文案就整组跳过）⇒ **有损必须说**
    #    （Global Constraints：没有静默的路径）。所以不是返回一个光秃秃的
    #    `{'url_contains': …}`，而是**带上那句为什么**（读产物/日志的人靠它分辨
    #    「查过、只有 URL 稳」与「没钉出来」）。
    if not url:
        return None
    if not snippet and spliced:
        #: 剪出来的那段**跨了两个元素**、又换不出单元素的一段 ⇒ 这一组只剩 URL。
        #: 原因必须说**这一条**：说成「没读到正文」是假话（正文明明读到了），
        #: 而读的人正是靠这句话决定「去改判据」还是「去查读页面那一层」。
        why = WHEN_TEXT_SPLICED_AWAY % spliced[:60]
    else:
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


def _snippet(text: str, avoid: str = "") -> str:
    """一个状态的判据里带哪一段正文（从这一页的 page_text 里**取原文**）。

    ⚠️ 从开头取，但要**跳过开头那些装饰字符**（真站实测，2026-09-17 第七轮）。

    ★★ 2026-09-24（真站逼出来的，最贵的一条）：**不许用「两页共用的开头」当判据**。
    单页漏斗是「地址不变、问题一屏一屏换」，而**页头那一截是共用的**（导航、logo、
    免责声明）⇒ 永远取开头 48 字 ⇒ 每一问的 `text_contains` **一模一样** ✗ ⇒
    `note_page` 按「同门」把它们**合并成一组**（3879 行，那是它该做的）⇒ 于是
    「下一问」的步跟着这一组一起执行 ✗ —— 真站 trace 里第 9–12 步 `ok:false`
    （它们**执行了** ⇒ 那道门当时成立 ✗）就是这个形状。
    ⇒ 从**与上一页不同的那一点**开始取（`avoid` 传上一页的正文）。
    ⚠️ 判据的**鉴别力本来就在文本这一侧**（`_stable_url` 的注释原话）—— 这一改正是
    把那一侧立起来，不是放松它 ✓。
    """
    text = str(text or "").lstrip(_SNIPPET_JUNK)
    a = str(avoid or "")
    if a:
        i = 0
        while i < len(text) and i < len(a) and text[i] == a[i]:
            i += 1
        diverged = text[i:].lstrip(_SNIPPET_JUNK)
        #: 与上一页**一字不差**（真的同一页）⇒ 退回原样：这时候「从头取」是对的。
        text = diverged or text
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


#: ★ 2026-09-29（交接《骨架还缺什么》§1）：一条判据**在别的页上也成立**时说的话。
#:
#: 为什么这条非喊不可：生成期那一验（`_when_for` 末尾的 `when_holds`）是**循环论证** ——
#: 判据就是从这一页的正文里剪的原文，那段注释自己就写着「必然成立」⇒ **那一验什么也没验**。
#: 真正没验过的是**反面**：拿**别的页**的模型去问同一句话。成立 ⇒ 这个门分不出这两页
#: ⇒ 产物重放走到那一页时，这一组的步骤会**在那页上真做一遍**（`_applies` 是按当前页
#: 逐组重判的，与那一页自己的门成不成立无关），而日志里每一步照样写着「点上了/填好了」。
#: 它是「整组静默跳过」的孪生病：跳过是判据太严，这是判据太松，两样长在同一个门上。
GATE_HOLDS_ELSEWHERE = (
    "「%s」这一组的判据在**别的页**上也成立（%s）—— 这个门分不出这几页。"
    "产物重放走到那一页时，「%s」这组步骤会**在那页上真做一遍**"
    "（它点的会是那一页上的元素），而日志里看不出任何异常。"
    "要动的是**判据本身**：让它落在这几页里**只有一页有**的那句话上"
    "（页头/导航那类公用文案、跨元素拼接、只钉地址，都会长成这个样子）。")


def _gates_that_hold_elsewhere(pages: list) -> list:
    """每条 `when` 在**别的页**上也成立的那些 —— 生成期的**反面**那一向（交接 §1）。

    与 `_when_for` 末尾那次 `when_holds` 的分工：那一次验的是**正例**，而判据是从这一页的
    正文里剪出来的原文字串 ⇒ 必然成立 ⇒ 那次等于没验。这一向（反例）今天谁都没看过。

    ⚠️ **同名状态之间不比**：`note_page` 把相邻同门的两页**有意**合并成一个状态
    （2026-09-22 那处：「两个状态同门 ⇒ 同一页上填做了、点却被跳过」）—— 同一个状态名
    底下的几个观测本来就该同门，拿它们互报等于把那条设计本身报成缺陷。
    ⚠️ **没有门的不参与**：`when` 为空是有意的（`_drop_incidental_start_when` 那个旁枝
    起点），把「有意不设门」报成缺陷 = 每一份正常产物都在喊狼来了。

    ⚠️ ★ 2026-09-30（**现场逮到的第三个形状**，`zekear` 那趟）：**同一个地址上的两个状态
    不算「别的页」**。判据：命中那条的稳定地址（`_stable_url`）与本组的**一样** ⇒ 跳过。
    为什么：那句警告怕的是「走**到那一页**时，前一组的步骤会**在那页上真做一遍**（它点的会是
    那一页上的元素）」—— 那是**跨页**的事（判据跨元素拼接那种）。而同一个地址上两组的元素
    本来就在那一页上、各自在**自己那次停留**里成立：像「商品页 → 去注册 → **回商品页** → 点
    Buy now」这种往返，重放时第一组在第一次停留里走完（最后一步 `goto` 走掉），回到商品页
    那次第二组才成立 ⇒ **照旧是对的**（`_merge_one_page_states` 只并**相邻**的那一次停留，
    往返不许被压平）。实测（盘上 108 本真账本）：同址命中不报之后，「会被拒」从 47 降回 **5**，
    而**不同地址**上真分不开的照旧报 —— 那一验的活儿一个字没少。

    返回 `[{"name", "url", "pages": [它**不该**成立的那几页 url]}, …]`：一个状态一条。
    """
    out: list = []
    seen: set = set()
    for i, entry in enumerate(pages):
        when = entry.get("when")
        if not when:
            continue
        name = entry.get("name") or ""
        hits: list = []
        own = _stable_url(entry.get("url") or "")
        for j, other in enumerate(pages):
            url = other.get("url") or ""
            if j == i or (other.get("name") or "") == name or url in hits:
                continue
            #: ⚠️ **同址不算「别的页」**（见 docstring）：那是同一页的两次停留，各自在自己
            #: 那次停留里成立 —— 报它只会把一份对的产物打回（实测：47 条误拒里绝大多数是它）。
            if own and _stable_url(url) == own:
                continue
            if when_holds(when, other.get("model") or {}):
                hits.append(url)
        if not hits:
            continue
        #: 同一个状态名底下可能有几个观测（`same_gate` 合并过）—— 它们会各自报一遍同样
        #: 的那几句，去重之后读的人才看得见「一共几个状态有事」。
        key = (name, tuple(hits))
        if key in seen:
            continue
        seen.add(key)
        out.append({"name": name, "url": entry.get("url") or "", "pages": hits})
    return out


#: ★ 2026-10-08：收尾时给**撞了的那道门**重挑一段正文（形状 A 的正题）。
#:
#: 现场（真账本，`job-f86ac12fda15` / `job-c8730acb27c5`）：
#:   · `shorecrestwpb` 首页那组 = url `https://www.shorecrestwpb.com/` + 正文 `Skip to main content`
#:     （无障碍跳转链接）⇒ 这道门在 `/inquire`、`/thankyou?token=…` 上**也成立** ⇒ IR 拦住整趟。
#:   · `policyquail` 的 `auto_wi` = `…/auto_wi` + `Auto Quiz - PolicyQuail`（**页面标题**）⇒ 同理。
#: 为什么剪的时候避不开：`_snippet` 的避让**只避上一页**，而「页头 / 跳转链接 / `<title>`」这类
#: 文字是**全站每一页**都有的 —— 只要它不紧挨着上一页就避不开；而**第一个状态根本没有上一页**
#: （`avoid=""`），直接在正文开头剪，剪到的就是它。
#:
#: 为什么等到收尾才做：只有那一刻「每一页的正文」才都在手边 —— 才谈得上**按全站避让**。
#: ⚠️ 顺序：**先重挑、再算撞车** —— `gate_collisions` 拦的是产物，它必须记的是重挑之后
#: 还剩几个真分不开的（反过来的话，救回来的那些也会照样把整趟砍死）。
#: ⚠️ 剪判据那一侧（`_snippet` / `_when_for` / `note_page`）**一个字不动**：
#: 没撞的门判据一个字节都不该变（不然所有站的产物判据一起动）。
WHEN_REPICKED_SAY = (
    "「%s」这一组的判据在**别的页**上也成立（%s）—— 收尾时**按全站避让重挑了一段**："
    "原来钉的是 %r（全站每一页都有），现在钉在 %r（只这一页有）。")

#: 写在**门自己身上**的那一句（`when["text_why"]`）。比上面那句短：读产物源码的人看的是它。
#: ⚠️ 与 `WHEN_TEXT_SPLICE`（2026-09-28 跨元素拼接那条）同一个位置、同一条规矩：
#: **换过判据就要在门上留一句**，读的人不必去翻账本才知道这段字不是原来那段。
WHEN_REPICKED_WHY = ("这一段判据**收尾时按全站避让重挑过**：原来钉的那段在别的页上也成立"
                     "（分不开页）——现在已经落在**只有这一页有**的那一段上。")

#: 重挑时候选正文至少要多长。与 `_snippet_in_one_node` 那个门槛**同一个口径**（8 字）：
#: 短判据的鉴别力等于零（一页上到处都成立），那会让**每一组**的门都成立 —— 比整组跳过更坏。
_WHEN_REPICK_MIN_CHARS = 8

#: ★ 2026-10-13（真事 `job-0bee2dd7903e` · healthinsurancewatch.com.au）：上面那道 8 字门槛
#: **兜底那一支**放宽到几字 —— 但只收「**在已知的页面上没出现过**」的那些。
#:
#: 为什么：单页漏斗上一屏与下一屏的差别常常只是**两个字的名字**（`Email address` → `Name`）。
#: 门槛把 `Name` 挡在门外之后，原来那一支会**退回最像的那个元素**，而它往往正是「又长又干净、
#: 可是全站每页都有」的那一句（那趟是页脚）⇒ 这一屏的门在别页上也成立 ⇒ 判「分不开」⇒ 并组
#: ⇒ 收尾重挑 ⇒ 整组跳过。**长度是「唯一性」的代理**（当年没有 `others` 才拿它顶），
#: 而唯一性是**量得出来的**：「在已知的页里出现过没有」。
SHORT_WHEN_MIN_CHARS = 4

#: ★ 2026-10-20（真事 delamere `job-4f7f53be42eb` / `job-2b027b30f0b0` / `job-4705e0b35df0`）：
#: **环境噪声**不许当门 —— 页脚社交部件的名字（`Facebook` / `Instagram` / `LinkedIn` …；会随
#: 会话与加载时机出现或消失，干净会话里整块 `visibility:hidden`）与 Gravity Forms 的蜜罐
#: （`This field is for validation purposes and should be left unchanged.`）。
#: 它们已经**三次**被挑中当 `when`（三种不同的社交名），而自测是**干净会话** ⇒ 那道门
#: 永远不成立。判据只认**整词/整段**（不是任意子串）：一个真页面完全可以把「LinkedIn」当正文讲。
_BOILERPLATE_GATES = frozenset({
    "facebook", "instagram", "linkedin", "twitter", "x", "youtube", "tiktok",
    "pinterest", "threads", "follow us on", "follow us", "share on",
})
_BOILERPLATE_GATE_PHRASES = (
    "this field is for validation purposes",
    "should be left unchanged",
)


def _is_boilerplate_gate(text: str) -> bool:
    """这一段是不是**页脚社交 / 表单蜜罐**那种环境噪声（见上面那条）。"""
    low = _norm(str(text or "")).lower().strip(" \t\r\n.,:;!·|–—-")
    if not low:
        return False
    if low in _BOILERPLATE_GATES:
        return True
    return any(p in low for p in _BOILERPLATE_GATE_PHRASES)


#: 走秒的那一串长什么样（倒计时 / 计时器）：`01:11:45` / `1:11:45` / `11:45`。
#: ⚠️ 只认**冒号形状的时刻**、两端都有界（`(?<![\d:]) … (?![\d:])`）：`12:30pm` 那种带尾巴的、
#: 以及 `Step 1 of 7` 那种不带冒号的**都不算** —— 后者正是单页漏斗里判页的真判据，
#: 收宽了会把它们一起废掉。
_CLOCK_RE = re.compile(r"(?<![\d:])\d{1,3}:\d{2}(?::\d{2})?(?![\d:])")


def _looks_like_a_clock(text: str) -> bool:
    """这一段里有**冒号形状的时刻**吗 —— 那一类会走秒，**不许当页面判据**。

    ★ 2026-10-12（真事 `job-7c0cb8a45c25` · nitrouscompetitions.com）：那一页的倒计时
    （`01:11:45` → `01:11:37`）先把**同一屏**按「url + 标题 + 正文前 400 字」裂成三个状态，
    又被撞车重挑选中当了门 —— 它对每一个兄弟页都「独一无二」（秒数当然不一样）——
    ⇒ 重放时那道门永远不成立 ⇒ 自测 18 步**全跳过**。
    见 `_unique_snippet_for`（候选这一侧）与 `_repair_ambiguous_whens`（「别的页」那一侧）。
    """
    return bool(_CLOCK_RE.search(_norm(str(text or ""))))


def _unique_snippet_for(model: dict, others: list) -> str:
    """这一页上**只有它有**的那一段正文（挑不到给空串）。

    候选 = 逐元素正文里每一个元素**自己的开头**（口径与 `_snippet_in_one_node` 一样：
    元素自己的开头才是「它自己的话」）。三条要求，缺一不可：

      ① 落在**单个元素**里 —— **这一条是构造出来的，不是验出来的**：候选逐条取自
         `node_texts`，而那一格是「每个叶子元素各一条，不合并」⇒ 候选天然是一句话。
         ⚠️ ★ 2026-10-09：原先这里还挂着「`node_texts_truncated` 为真就**直接不挑**」。
         那半句是**从 `_snippet_in_one_node` 抄过来的**，而那边防的是另一件事 ——
         「这段判据**不在任何单个元素里**」这个**结论**（判据的元素可能排在截断线之后，
         「量不到」与「没有」是两件事）。在这儿它挡掉的却是**挑一个候选**，而挑候选
         根本不看清单全不全：候选自己是不是一句话，跟这一页还有多少条没抄下来无关。
         真账本上它挡掉的是 kotzheating 那两趟（`job-c8d966155ec6` / `job-53e8caeece5e`）——
         首页 400 个叶子元素 ⇒ 必然置位 ⇒ 重挑一次都没跑，两趟都砍死在 IR 那道门上。
      ② 在**别的每一页**的正文里都不出现 —— 比 `_snippet` 那个「只避上一页」严，撞车正是
         从「只避上一页」那儿来的。
         ⚠️ ★ 2026-10-09：「别的页的正文」= `_other_pages_text`（`page_text` **加逐元素正文**），
         **不是**只比 `page_text`。`page_text` 是工具**硬切 600 字**的
         （`observe.go`：`pageText.slice(0, 600)`），页头 / 导航 / 页脚有大半落在切点之后 ⇒
         只比它就会把**全站公用的导航项**认成「这一页独有」⇒ 同名旧病换个名字复发，
         而这一次**没有任何一道门会喊**（产物照样在别的页上成立、照样乱点）。
         `_snippet_in_one_node` 早就在用 `_other_pages_text` 了 —— 这里是漏网的那一处。
      ③ 在**自己这一页**上真成立（正例）—— 不成立就是整组静默跳过，比撞车更坏。
         ⚠️ ★ 2026-10-09：这一条原先比的是 `page_text` 那 **600 字**（理由是「与剪判据那次
         `when_holds` 同一把尺子」）—— **那把尺子比产物那一侧的窄**：产物的
         `page_signature()` 明写着「不裁长度 —— 成功文案可能在第 600 字之后」。
         真账本上它的后果是 shorecrestwpb 五趟 / policyquail 两趟：判据钉在**全站公用**
         的页头那一截上，而这一页**自己**的话排在 600 字之后 ⇒ 那 600 字里一个字都不是
         这一页独有的 ⇒ **手上没牌**。现在改用 `_one_page_text`（正文 **加逐元素正文**，
         与产物一侧同宽）。⚠️ 只动正例这一向，反面一个字不改 —— 见 `_one_page_text`。
      ④ ★ 2026-10-20（真事 `job-4f7f53be42eb` / `job-2b027b30f0b0` · delamere）：**看得见的
         优先**。`node_texts` 是每个叶子元素自己的 `innerText`，而不渲染的元素（Gravity Forms
         的蜜罐 `visibility:hidden`、页脚社交链接那一类）上 innerText 会**回退到 textContent**
         ⇒ 页面上根本看不见的字也能当候选；而产物 `page_signature()` 读的是 `body.innerText`
         ⇒ 那种门**永远不成立**、整组静默跳过。真事挑中的是 `'This field is for validation
         purposes and'` / `'Facebook'`（都排在真正文前面）。
         ⚠️ 只是**排序**，不是硬拒：`page_text` 硬切 600 字/帧，落在那之后的**好**判据在产物
         一侧照样成立 —— 一个可见的候选都没有时，照旧退回原来的顺序（行为一个字不改）。
      ⑤ ★ 2026-10-20：**短的那一档也收**（`SHORT_WHEN_MIN_CHARS`，≥4 字），但**只收在已知的
         每一页（含同址兄弟屏）上都没出现过的**。理由与 `_snippet_in_one_node` 那条同一句：
         **长度只是「唯一性」的代理**，而唯一性是量得出来的。单页漏斗的差别常只是
         `Email address` → `Name`（4 字）—— 8 字门槛会把它挡住，于是退回页脚那种「又长、
         但兄弟屏都有」的字（真事 `job-0bee2dd7903e`）。⚠️ 它排在**可见的长候选之后**：
         优先顺序 = 长且可见 → 短且可见 → 长但只在 600 字之外 → 短且只在 600 字之外。

    挑到第一个合格的就要（按页面自己的元素顺序）：够用就行，不挑更长的 ——
    读起来才是这一页正文里的原话。④/⑤ 是仅有的两次**越过顺序**：可见压过不可见；
    长的（≥8）压过短的（≥4）。
    """
    nodes = model.get("node_texts")
    if not isinstance(nodes, list) or not nodes:
        return ""
    mine = _one_page_text(model)
    if not mine:
        return ""
    elsewhere = _other_pages_text(others)
    #: 这一页**看得见**的正文（`page_text` = 工具按帧切的 `body.innerText`）——
    #: 产物 `page_signature()` 读的就是同一张嘴（见 ④）。
    visible = _norm(model.get("page_text") or "").lower()
    long_visible = short_visible = long_hidden = short_hidden = ""
    for raw in nodes:
        cand = _snippet(_norm(str(raw or "")))
        n = len(cand)
        if n < SHORT_WHEN_MIN_CHARS:
            continue
        if _is_boilerplate_gate(cand):
            continue                    # 页脚社交 / 蜜罐那种环境噪声（见 _BOILERPLATE_GATES）
        low = cand.lower()
        if low not in mine:
            continue
        if _looks_like_a_clock(cand):
            continue                    # 走秒的那串不许当门（见 _looks_like_a_clock）
        if _on_other_pages(cand, elsewhere):
            continue
        #: ④/⑤ 的四档（顺序 = 优先级）；每档只留**第一个**（按页面自己的元素顺序）。
        if n >= _WHEN_REPICK_MIN_CHARS:
            if low in visible:
                long_visible = long_visible or cand
            else:
                long_hidden = long_hidden or cand
        elif low in visible:
            short_visible = short_visible or cand
        else:
            short_hidden = short_hidden or cand
    return long_visible or short_visible or long_hidden or short_hidden


def _repair_ambiguous_whens(pages: list) -> list:
    """★ 2026-10-08：收尾时给**已经撞了**的那些门重挑一段正文（形状 A）。

    只动 `_gates_that_hold_elsewhere` **点名的那几个门** —— 没撞的一个字都不碰（这一改要是
    溢出去，所有站的产物判据一起变）。挑不到（这一页真没有独有的正文）⇒ **一个字都不改**：
    那时候照旧交给 IR 拦住并说清，**不新增静默路径**。

    ⚠️ **按状态名成组**（与那一验、「`Journey.states()` 取最后一条」同一条口径）：同一个名字
    底下的几条是**同一个状态**的几次观测 —— 「别的页」只算**别的名字**的页；换的时候几条
    **一起换**成同一段，而且那一段要在**每一条自己那一页**上都成立（正例不许破）。
    只换最后一条（进产物的那条）的话，前面几条照旧撞车 ⇒ `gate_collisions` 不为空 ⇒
    整趟还是被砍死，而账本上却写着「重挑过了」—— 那是半句真话。

    返回重挑成了的那些 `[{"name", "had", "now", "pages"}]` —— 调用方据此**记账 + 重算**
    `gate_collisions`（那一格拦的是产物，必须记重挑之后还剩几个）。
    """
    out: list = []
    for hit in _gates_that_hold_elsewhere(pages):
        name = str(hit.get("name") or "")
        #: ⚠️ **按名字成组**（与上面那一验、「`Journey.states()` 取最后一条」同一条口径）：
        #: 同一个名字底下的几条是**同一个状态**的几次观测 ——
        #:   · 「别的页」= **别的名字**的页（同名之间不比，那一验自己就是这么写的）；
        #:   · 换的时候**几条一起换**成同一段，而且那一段必须在**每一条自己那一页**上都成立 ——
        #:     只换最后一条（进产物的那条）的话，前面几条照旧撞车 ⇒ `gate_collisions`
        #:     不为空 ⇒ 整趟还是被砍死，而账本上却写着「重挑过了」（半句真话）。
        mine = [p for p in pages if str(p.get("name") or "") == name]
        if not mine:
            continue
        #: ⚠️ ★ 2026-10-12（真事 `job-7c0cb8a45c25` · nitrouscompetitions.com）：**「别的页」要跟
        #: 撞车检测器同一条尺子** —— `_gates_that_hold_elsewhere` 明写着「同一个地址上的两个状态
        #: **不算别的页**」（那是同一页的几次停留），而这一格原先按**状态名**算 ⇒ 一个走秒的倒计时
        #: 把同一屏裂成 `…-2/-3/-4` 之后，**兄弟页**把稳的产品标题挤掉、**每屏都独一无二的秒数**
        #: 反而当选（账本原话：『原来剪到的那段（'2025 KTM SX 85 - Big Wheel'）在前面走过的那几页
        #: 上也有 ⇒ 门分不出两页 ——已经换成这一页自己的一段：'01:11:45'』）⇒ 重放时那道门
        #: 永远不成立、18 步全跳过。两处不一致的后果就是这种「检测器说没事、重挑却把门换坏」。
        own_url = _stable_url(mine[-1].get("url") or "")
        others = [p.get("model") or {} for p in pages
                  if str(p.get("name") or "") != name
                  and not (own_url and _stable_url(p.get("url") or "") == own_url)]
        cand = _unique_snippet_for(mine[-1].get("model") or {}, others)
        if not cand:
            continue
        low = cand.lower()
        #: ⚠️ ★ 2026-10-09：正例这一验与 `_unique_snippet_for` 的 ③ **同一把尺子**
        #: （`_one_page_text` = 正文 **加逐元素正文**，与产物一侧同宽）。两处若不同宽，
        #: 就会出现「挑的时候说成立、换的时候又说不成立」—— 那条路只会把整趟白白砍死。
        #: ⚠️ ★ 2026-10-13：这一组要是**并出来的**，正例就得在**并进来的每一屏**上都成立 ——
        #: 光验 `model`（= 并组时留下的**最新那一眼**）等于只验一屏：真事 `job-0bee2dd7903e`
        #: 就是这么把门换成 `Last Step` 的（只在第三屏上有），重放到头一屏整组 6 步全跳过。
        spans = [p["merged_texts"] if p.get("merged_texts") else [_one_page_text(p.get("model") or {})]
                 for p in mine]
        if not all(low in text for texts in spans for text in texts):
            continue                    # 这一条自己那页上没有这段 ⇒ 一起不换（正例不许破）
        had: list = []
        changed = False
        for p in mine:
            when = p.get("when")
            if not isinstance(when, dict) or not when.get("url_contains"):
                continue
            if not had:
                had = list(when.get("text_contains") or [])
            when["text_contains"] = [cand]
            when["text_why"] = WHEN_REPICKED_WHY
            changed = True
        if changed:
            out.append({"name": name, "had": had, "now": cand,
                        "pages": list(hit.get("pages") or [])})
    return out


#: ★ 2026-09-30：**同一页**被判成两个状态、并掉时记在账上的一句。
#:
#: 现场（用户报的「他老是说啥对不上的、然后就不产出 py」）：`getfixapro.com/offer-new/cart.php`
#: 一眼是**加载中**（正文开头是 `<script>` 的源码 `'use strict';var m={};class`）、一眼是加载完
#: 的横幅、再一眼只差**倒计时那几个数字**（10:00 → 09:44）—— `note_page` 的键是
#: 「url + 标题 + 正文前 400 字」，三眼三个键 ⇒ **一个页面三个状态**。而这三个状态的判据都是
#: 从**同一页**剪的 ⇒ `_gates_that_hold_elsewhere` 报「cart 这组的判据在 cart.php 上也成立」
#: （⚠️ 它嘴上说的是「**别的页**」），`graph._draft` 于是**一份 py 都不出**。
#: 实测那一天的面板：20 趟里 **10 趟**是这么死的（`draft_failed`）。
SAME_PAGE_MERGED_SAY = (
    "「%s」并进「%s」：**同一页**（%s）—— 地址一样、两边的判据分不开。"
    "同一页上两组步骤，重放时一组成立、另一组不成立，演出的就是「填做了、点却被跳过」，"
    "所以并成一组。")
#: ★ 2026-09-30（用户那句「那这样就不对吧」）：**判据是机器剪的** —— 观测撞上加载中的
#: 那一页时 `page_text` 是空的，那一页只能剪出「地址」这一条判据，而地址前缀在**同一个
#: 地址下的每一屏**上都成立 ⇒「分不开页」这一报**根子在我们这一侧**（那一页还没读完就被
#: 当成一页记下来了），运营一个字都没做错。处置见 `_merge_one_page_states`。
UNREAD_MERGED_SAY = (
    "「%s」并进「%s」：**那一页当时没读到正文**（%s）—— 只拿地址当门的话，同一个地址下的"
    "**每一屏**都成立，等于没有门。并进紧接着那个读到了正文的状态，判据用它的（那才是这一页"
    "该有的门）。")


def _read_something_but_no_pinnable_gate(prev, pg: dict, url: str) -> bool:
    """这一眼**读到了正文**、只是钉不出单元素判据，而且上一页就在同一个稳定地址上、它自己有门吗。

    见 `_merge_one_page_states` 里 ② 那段：这三条都成立时，这一眼是一条**真屏**的停留，
    并进上一页（`test_a_readable_look_without_a_pinnable_gate_is_not_merged_forward` 钉着它）。
    """
    if prev is None or not url:
        return False
    if _stable_url(prev.get("url") or "") != url:
        return False
    if not (prev.get("when") or {}).get("text_contains"):
        return False                       # 上一页只有 URL ⇒ 并过去等于这一组没有门
    return bool(_norm((pg.get("model") or {}).get("page_text") or ""))


def _page_pair_indistinguishable(a: dict, b: dict) -> bool:
    """这两页**说不清谁是谁**吗 —— 两边都**有正文判据**，且至少**一个方向**成立（`when_holds`）。

    一个方向就够的理由：`note_page` 剪判据时是**从这一页正文里取原文**，而两次观测的
    正文头可能被**截断**（`page_text` 只有 600 字，`observe` 自己截的）—— 于是「A 的判据
    在 B 那一眼的正文里找不到」可能只是**那一眼没截到**，不代表这两页分得开。
    ⚠️ 两向都不成立 = 真的分得开（单页漏斗每一问正是这个样子）⇒ **不并**。

    ⚠️ ★ 2026-09-30（**并过头**时逮到的）：**「只有地址」的那条判据不算「分不开」的证据**。
    它是「那一眼没读到正文」的产物（`observe` 撞上加载中的那一页），而**地址前缀**在
    同一个地址下的**每一屏**上都成立 ⇒ 拿它当证据，单页漏斗的十几屏会被并成一组
    （实测 `easystorebuilderpro.com/FUBGB/`：12 屏的问卷会被并成一屏、每一屏自己那条
    好判据全丢）。一次扫盘：68 本账本并了 300 组，其中 31 本出现「一次并掉 ≥3 组」。
    ⇒ 两边都得有正文判据，才谈得上「分不开」。地址那条只管「是不是同一页」。
    """
    aw, bw = a.get("when"), b.get("when")
    if not aw or not bw:
        return False
    if not aw.get("text_contains") or not bw.get("text_contains"):
        return False
    return bool(when_holds(aw, b.get("model") or {}) or when_holds(bw, a.get("model") or {}))


def _best_gate(group: list) -> dict | None:
    """这一组里**在并进来这几页的模型上都成立**的那条判据（成立得最多的那条；平手取最早的）。

    ⚠️ 挑的是**量出来的**那一条，不是好看的那一条：并进来的是这一页的**变体**，
    判据得在变体上都成立 —— 否则重放时这一组静默跳过（本仓最贵的那种病）。
    """
    best, best_n = None, -1
    for cand in group:
        when = cand.get("when")
        if not when:
            continue
        n = sum(1 for other in group if when_holds(when, other.get("model") or {}))
        if n > best_n:
            best, best_n = when, n
    return best or (group[0].get("when") or None)


def _rename_state(steps: list, gone: str, into: str) -> None:
    """把账上属于「并掉那个状态」的步骤改挂到留下的那个下面（`states()` 是按状态名分组的）。"""
    for step in steps or []:
        if (step.get("state") or "") == gone:
            step["state"] = into


#: ★ 2026-10-13：并组之前先试「各给各的门」时，挑出来的那段进 `when` 时说的话
#: （账本上要看得见这一组的门是怎么来的 —— 与 `WHEN_REPICKED_WHY` 同一个位置）。
WHEN_SPLIT_WHY = ("这一组的门**各给各的**：同一个地址上的几屏各有各的正文，"
                  "并成一个状态会让它们共用一道门 —— 而那样的门只可能在某一屏上成立。"
                  "挑到了就**不并**（并是那条拒绝话里的出路 ②，出路 ① 是各钉各的）。")


def _pin_own_gates(group: list, pages: list) -> bool:
    """能给这一组**每一眼各挑一段只属于它自己的正文**当门吗？挑到了就**就地换门**回 True。

    ★ 2026-10-13（真事 `job-0bee2dd7903e` · healthinsurancewatch.com.au）：同一个地址上的
    三屏（`… Email address` / `… Name` / `Last Step Mobile number`）判据都钉在**全站页脚**
    那句上 ⇒ `_page_pair_indistinguishable` 说「分不开」⇒ 并成一组；收尾 `_repair_ambiguous_whens`
    又给这一组重挑了一道门 —— 可它只看得见**并组后留下的那一眼**（`keep["model"]` 换成了
    `group[-1]["model"]`，别的几眼的模型当场就丢了）⇒ 挑中的 `Last Step` 只在**第三屏**成立
    ⇒ 重放到第一屏，整组 6 步**全跳过**（自测 15 步里 6 步被跳过、邮箱那一格空着、没走到成功）。

    而三屏各自的正文本来就不一样。所以并组之前先问一句：**各给各的门，行不行？**

    ★ 2026-10-20（真事 `job-4f7f53be42eb` / `job-2b027b30f0b0` · delamere）：**兄弟屏之间也要比**。
    现场四屏（`Step 1/2/3/4 of 4`）上，**每一屏都有**的那些字（Gravity Forms 的蜜罐
    `This field is for validation purposes and`、页脚社交链接）在「别的地址」上不一定有 ——
    按「只比别的地址」的旧口径，它们**每一屏都算独有** ⇒ 挑中的门在**另一趟会话**里根本
    不在页面正文上（蜜罐是隐藏 div）⇒ 整组跳过。**真·每屏唯一**的是 `Step 2 of 4,`
    这种（兄弟屏之间各不相同、运行时页面上看得见）。所以 `others` 收**除这一屏以外的
    每一页**：兄弟屏 + 别的地址，一个都不少。
    ⚠️ 与撞车检测器（`_gates_that_hold_elsewhere` 跳过同址）**不是同一把尺子**：那个问的是
    「这两页分不分得开」，同址兄弟本来就该算一页；这里问的是「这一屏的**门**独不独」——
    兄弟共有的字正是**恰恰不能**当门的那些。

    一处挑不到 ⇒ **一个字都不改**（回 False，照旧并：那时候并正是对的出路 —— 单页漏斗上
    真正分不开的两眼，比如「同一屏的第二眼」，就该并）。
    """
    picks: list = []
    for pg in group:
        #: ★ 2026-10-20：**除这一屏以外的每一页**（兄弟屏 + 别的地址）—— 见 docstring。
        others = [q.get("model") or {} for q in pages if q is not pg]
        cand = _unique_snippet_for(pg.get("model") or {}, others)
        if not cand:
            return False
        picks.append(cand)
    for pg, cand in zip(group, picks):
        when = dict(pg.get("when") or {})
        when["text_contains"] = [cand]
        when["text_from"] = "这一段是从这一屏自己的正文里剪的"
        when["text_why"] = WHEN_SPLIT_WHY
        pg["when"] = when
    return True


def _merge_one_page_states(pages: list, steps: list) -> list:
    """把**同一页**被判成几个状态的那些并成一个（就地改 `pages` 与 `steps`），返回并掉的清单。

    为什么是「并」不是「拒」：这正是那句拒绝话自己给的出路 ②（「承认这两页分不开，把它们
    **并成一个状态**」），也是 2026-09-22 那条的同一把尺子（相邻同门 ⇒ 并，**别半执行**）。
    而按地址拒（`_gates_that_hold_elsewhere` 照旧报）在今天这个形状上是**死路**：
    同一个地址上根本没有「只有那一页有」的那句话可以拿来当判据 —— 让运营去改判据，
    他改不动，于是**永远不出 py**。

    ⚠️ 只在**同一个稳定地址**（`_stable_url`：query / fragment 不算）上并，而且两页的判据
    分不开（`_page_pair_indistinguishable`）。两条都要：
      · 地址不一样 ⇒ 那是**两页**（§1 那一验正该拦，它的活儿一个字没少）；
      · 判据分得开 ⇒ 那是**真的两屏**（单页漏斗每一问一屏，2026-09-24 那条钉着它）。

    并掉之后：重放时这一组只有**一道门**，那几屏的步骤按**账上的先后**一起做 ——
    这正是那一趟**实际做过**的事（并进来的那些步本来就是在这一页上做的）。
    """
    merged: list = []
    #: ★ 2026-10-08：删的时候按**身份**（这两条被并掉的清单本身），**不按名字**。
    #: 为什么：并进来的那条与留下的那条**可能同名** —— `note_page` 见相邻两页 `when` 完全
    #: 相等时就把新的挂在**同一个状态名**下（2026-09-22 那条该做的事），而真站上确实会出现
    #: 「判据一字不差、`key` 不等」的两眼（`_SNIPPET_JUNK` 注释里那个形状：广告位占位符
    #: `___` 时有时无 —— 它被当装饰字符剥掉 ⇒ 判据不变，而 `key` 那 400 字里带着它 ⇒
    #: 不算「同一眼」）。按名字删的后果：**连留下的那条一起删** ⇒ 这一页从 `journey.pages`
    #: 里整个消失 ⇒ 产物里这一组成了 `when: null`（`template._matches(None)` 恒真）
    #: ⇒ 没有门 = **在任何页上都适用**（重放时在错的页上照做、日志还写着做成了）。
    gone_objs: list = []
    #: ① **「没读到正文」的那一眼不是一页**（见 `UNREAD_MERGED_SAY`）：并进**同一地址上、
    #: 紧接着的那个读到了正文的状态**（步骤按账上先后改挂过去；判据用那个读到的）。
    #: ⚠️ 只在**同一稳定地址**上并：地址都不一样时，那一眼里做的事与后面那一页的关系
    #: 本来就说不清（§1 那一验照旧拦，它的活儿一个字没少）。
    for pg in list(pages):
        when = pg.get("when") or {}
        if not when or when.get("text_contains"):
            continue
        url = _stable_url(pg.get("url") or "")
        if not url:
            continue
        here = pages.index(pg)
        readable = [q for q in pages
                    if _stable_url(q.get("url") or "") == url
                    and (q.get("when") or {}).get("text_contains")]
        #: ★ 2026-10-12（真事 `job-e7906a6c65d0` · freshstartrelief.org）：**方向先看上一页**。
        #:
        #: 现场：单页漏斗（一个地址、一屏一题）上，把年份选好之后那一屏的判据**钉不出来**
        #: （剪到的那段跨了两个元素：触发件的 `3 years` 接上底下那串选项）⇒ 它只剩 URL ⇒
        #: 按「往前后找」的旧规矩并进了**下一屏**（`Step 3 of 7`）。可挂在它名下的那一步是
        #: **这一屏上**那颗 Continue（`… form > div:nth-of-type(3) > button:nth-of-type(2)`）——
        #: 合并之后它成了「门 = `Step 3 of 7`」那一组的第一步，而那句话**正是这一步自己点出来的**
        #: ⇒ 门永远不成立 ⇒ 整组跳过 ⇒ 后面每组跟着跳过（自测 28 步里 **17 步**被跳过，四趟一个形状）。
        #:
        #: 判据（量得到、不猜）：**上一页的门，在这一眼的正文里还成立吗** ——
        #:   · 成立 ⇒ 页面是**原地变的**（还在同一屏上，只是多答了一题）⇒ 并回**上一页**
        #:     （判据用它那条；步骤也跟着它，重放时在那道门成立的那一屏上做，正是它们当初做的位置）；
        #:   · 不成立 / 读不到 ⇒ 那是「它接下来变成的那一页」（正常的形状：观测撞上刚跳转的
        #:     加载中页面，那一眼的正文是空的）⇒ 照旧并往前。
        #: ⚠️ **不许**拿「上一页与它同址吗」当判据：单页漏斗上**每一屏都同址**，那条判据恒真，
        #: 会把「刚跳转的加载中页面」也并回上一屏去（那正是上面第二条要防的）。
        prev = pages[here - 1] if here > 0 else None
        if prev is not None and when_holds(prev.get("when") or {}, pg.get("model") or {}):
            target = prev
        elif _read_something_but_no_pinnable_gate(prev, pg, url):
            #: ★ 2026-10-12（真事 `job-d1232badf5f6` · nitrouscompetitions.com/auth）：
            #: 「没读到正文」有**两副面孔**，原来这一支只认第一副 ——
            #:   ① 那一眼**一个字都没读到**（观测撞上加载中的页面）⇒ 它不是一页，并进**接着变成**
            #:      的那一页（照旧往前；`test_a_loading_look_is_still_merged_forward` 钉着它）；
            #:   ② 那一眼**读到了正文、只是钉不出单元素判据**（正文头是跨元素拼的、或正文头被
            #:      截断）⇒ 它**是一页**，在它名下做的那些步就发生在**它这一屏上** ⇒ 并进
            #:      同一地址上的**上一页**（那一屏）。
            #: 往前并的代价（本单实测）：把「下一屏的门」交给了一步**制造那扇门**的动作 —— 那一组里
            #: 第一步是「点 Create account」，而门 =「注册屏正文」⇒ 门永远不成立 ⇒ 自测 23 步里
            #: **17 步被跳过**（页面从没走到注册屏、日志每一步都写着「跳过」）。
            #: 判据都是**量得到的**：这一眼有没有正文 + 上一页在不在同一个稳定地址上 + 上一页**自己
            #: 有没有一段钉得住的判据**（只有 URL 的那条不许当门 —— 那等于这一组在任何一屏上都成立）。
            target = prev
        else:
            #: ★ 2026-09-30（现场逮到的第二个形状）：**先往后找，再往前找** ——
            #: 「没读到正文」的那一眼可能落在**这一页的最后一眼**上（`zekear` 那趟：
            #: `…_cleaner-2` 是那一页的最后一眼，后面直接去了别的地址）⇒ 只往后找的话它活下来，
            #: 那一组就带着一条「只有地址」的门，§1 照旧报「分不开页」、还是一份 py 都不出。
            #: 两边都不在 ⇒ 留着。
            target = next((q for q in pages[here + 1:] if q in readable), None) \
                or next((q for q in reversed(pages[:here]) if q in readable), None)
        if target is None:
            continue
        merged.append({"name": pg.get("name") or "", "into": target.get("name") or "",
                       "url": pg.get("url") or "", "kind": "unread"})
        gone_objs.append(pg)
        _rename_state(steps, pg.get("name"), target.get("name"))
    if gone_objs:
        pages[:] = [q for q in pages if not any(q is g for g in gone_objs)]

    #: ⚠️ ★ 2026-09-30（**现场逮到的第三个形状**，`zekear` 那趟自测倒在这儿）：
    #: 只并**相邻的**那几页。原来是在 `pages` 里**跨着找**同址、判据分不开的那些 —— 于是
    #: 「商品页 → 去 signIn → 注册 → **回商品页** → Buy now」这种**往返行程**被压成一组
    #: `[goto signIn, click Buy now]`：重放时第一步就走掉了，第二步在**另一个页面**上找
    #: 商品页的按钮 ⇒ 自测第 2 步「页面上没找到「Buy now」」（那条 trace 就在盘上）。
    #: 判据：`_Pages` 里**挨着**的两条才算一页的两眼（同一趟行程里连着发生的才是同一次停留）。
    groups: list = []
    for pg in pages:
        url = _stable_url(pg.get("url") or "")
        prev = groups[-1][-1] if groups else None
        if prev is not None and url and _stable_url(prev.get("url") or "") == url \
                and _page_pair_indistinguishable(prev, pg):
            groups[-1].append(pg)
        else:
            groups.append([pg])

    for group in groups:
        if len(group) < 2:
            continue
        #: ★ 2026-10-13：**先试「各给各的门」（出路 ①）** —— 挑得到就别并。并是出路 ②，
        #: 代价是这一组往后只能共用一道门，而收尾重挑那一步**只看得见留下的那一眼**
        #: （并组时模型就被换掉了）⇒ 一旦重挑，就可能挑到只在某一屏上成立的那句话，
        #: 整组跟着跳过（真事 `job-0bee2dd7903e`，见 `_pin_own_gates`）。
        if _pin_own_gates(group, pages):
            continue
        keep = group[0]
        keep["when"] = _best_gate(group)
        #: 判据随**最新那一眼**的模型走：下一页要与它比（`_gates_that_hold_elsewhere` 用的是 `model`）。
        keep["model"] = group[-1].get("model") or keep.get("model")
        keep["key"] = group[-1].get("key") or keep.get("key")
        #: ★ 2026-10-13：**并进来那几眼的正文也留着**（就留正文，不留整份模型 —— 那几眼
        #: 的模型本来就只有这一处还用得上）。收尾 `_repair_ambiguous_whens` 给这一组重挑门时
        #: 要拿它验正例：**这一段得在这一组的每一屏上都成立**。不留的话，重挑只对着**一屏**验
        #: ⇒ 挑出来的那段可能只在那一屏上成立 ⇒ 重放到头一屏整组跳过（真事 `job-0bee2dd7903e`：
        #: 并进来的三屏只剩第三屏的模型，重挑把门换成了只在第三屏成立的 `Last Step`）。
        keep["merged_texts"] = [t for t in (_one_page_text(g.get("model") or {}) for g in group) if t]
        for gone in group[1:]:
            merged.append({"name": gone.get("name") or "", "into": keep.get("name") or "",
                           "url": gone.get("url") or "", "kind": "same_page"})
            gone_objs.append(gone)
            _rename_state(steps, gone.get("name"), keep.get("name"))
    if gone_objs:
        pages[:] = [p for p in pages if not any(p is g for g in gone_objs)]
    return merged


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


#: ★ 2026-10-09：**同一个字的两种排版写法算同一个字**（用户裁定原话：「那就大小写吧」
#: —— 按 2026-09-22 那条**大小写**先例办：`Check your email` 与 `check your email` 算
#: 同一个词，那么 `’` 与 `'` 就是**同一个撇号**）。
#:
#: 真事（`job-27389cef3110`，enroll 那趟）：人给的判据是
#: `Thank You! We've Received Your Information!`（**直角**撇号），站点把那句渲染成
#: `We’ve`（**弯**撇号 U+2019）。代码**真读到了**那句（2339 字的整页正文里就是它），
#: 可两边只压空白 + 小写 ⇒ 子串不成立 ⇒ `_explore_reached_success` 返回 `False`
#: ⇒ `explore_unfinished` ⇒ **一份 py 都不出** —— 尽管探路 30 步走完、收尾那一眼
#: 也照着截图看见那句了。
#:
#: ⚠️ 折的只是**同一个字的排版变体**（弯引号 / 长短破折 / 省略号），**不是抹标点**：
#: `Thank You!` 与 `Thank You` 仍然是**两串不同的字**（钉在 `tests/test_template.py`
#: 的 `test_the_product_still_needs_the_punctuation_that_was_asked_for`）。
#: ⚠️ 这张表**必须与产物里那份逐字一致**（`SKELETON.template` 里那个 `_FOLD`）——
#: 交付出去的 py import 不到 agent 包，所以只能是两份。钉在
#: `tests/test_browser_agent.py::test_the_two_norms_are_one_yardstick`。
_FOLD = str.maketrans({
    # 弯单引号 → 直角撇号
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    # 角分 / 角秒
    "′": "'", "″": '"',
    # 弯双引号 → 直角双引号
    "“": '"', "”": '"', "„": '"', "‟": '"',
    # 各种横线 → 直角连字符（连字符 / 不换行连字符 / 短破折 / 长破折 / 横线 / 减号）
    "‐": "-", "‑": "-", "‒": "-", "–": "-",
    "—": "-", "―": "-", "−": "-",
    # 省略号 → 三个点
    "…": "...",
    # ── 全角 ASCII（U+FF01–U+FF5E）→ 半角 ──
    # ★ 2026-10-09 用户第二次点头（原话「算吧算同一个」）：宽度也是「同一个字的两种写法」。
    # ⚠️ **整段一起折** —— Unicode 里它们就是「同一个字符的两种宽度」，只折标点、
    # 不折字母数字是凭空划一条线。
    # ⚠️ 到 U+FF5E 为止。再往后的 U+FF61–FF9F 是**半角片假名**（ｱｲｳ）—— 那是**另一个
    # 字符**，不是谁的全角形；按「块」写就会把它们一起吃掉（钉在
    # `test_the_two_norms_do_not_fold_what_is_actually_a_different_character`）。
    **{chr(0xFF01 + i): chr(0x21 + i) for i in range(0x5E)},
})


def _norm(text: str) -> str:
    """与 `cdp observe` 的 page_text / 产物的 `page_signature()` **同一口径**。

    压空白（含 NBSP）+ **折同一个字的两种排版写法**（`_FOLD`）。后一半之所以必须与
    产物里那份一致：`page_text` 到了比较的那一步**两边都会再过一次这个函数**
    （`row_text` / `page_signature`），一边折一边不折就等于两把尺子。
    """
    return re.sub(r"\s+", " ", str(text or "")).translate(_FOLD).strip()


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
           hints=None, success_text: str = "", success_urls: str = "") -> str:
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
        return free + _success_block(success_text, success_urls) + _hints_block(hints)
    return (_planned_brief(url, goal, plan, budget) + _success_block(success_text, success_urls)
            + _hints_block(hints))


def _success_block(success_text: str, success_urls: str = "") -> str:
    """**「什么算成功」那两格本身**（TDD 探针量出来的缺项，2026-09-22）。

    为什么它必须进开场白：判据是**人去页面上找那串字**、而系统只在 `observe` 读到的正文里找。
    开场白里原来**一个字都没提它** ⇒ 模型既不知道要找什么、也不知道**找到就能收摊** ——
    真事 `job-76fe990d4d62`（24 步只看 5 眼 ⇒ 判据扑空 ⇒ 自动重探 ⇒ 空转、没产物）。

    ⚠️ 没人给判据（两格都空）⇒ **一个字节都不加**（那份「没计划那一版逐字节相同」的钉子因此不动 ✓）。
    ★ 2026-09-24：**网址那一格也进开场白**（两格一起说）—— 只给网址时原来会拼出一句
    「出现『』」的空话。
    """
    text = str(success_text or "").strip()
    urls = wanted_urls(success_urls)
    said = []
    if text:
        said.append("页面上出现这串字就算成 —— 『%s』。" % text)
    if urls:
        said.append("**或者网址里出现这一截也算成** —— 『%s』（老站常常这么判：SPA 换页时"
                    "正文可能一个字都不变，而地址变了）。" % "』『".join(urls))
    if not said:
        return ""
    return ("\n⚠️ **什么算成功**：%s"
            "**见到它就可以收摊**（那之后每一次点击都可能是重复提交）。" % "".join(said))


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
