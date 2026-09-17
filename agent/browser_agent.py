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

import inspect
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from . import llm, plan as plan_module, tools

#: **C1**：`deepseek-v4-*` 把思考 token 算进 `max_tokens`。给 4000 时最终答案会**静默变空**
#: （spike 实测 1/8，提到 12000 后 5/5 正常）。别往下调 —— 那不是省钱，是把能力削掉。
MAX_TOKENS = 12000

#: 预算的目的是**防跑飞**，不是省钱（规格 §6.5 / P5）。到顶就停，且**明确说出来**。
DEFAULT_MAX_STEPS = 30
DEFAULT_MAX_ROUNDS = 20

#: 骨架认的五个重放动作（其余动作出现在 STATES 里会被当成「产物写错了」）。
REPLAY_ACTIONS = ("click", "form", "scroll", "goto", "wait")

#: 停滞判据（§2.5）里的「**动作**」= `_SYSTEM` 规矩 2 里那几个**会改页面**的工具。
#: `observe` / `diff` / `screenshot` 是**感知**，不在里面 —— 一个只在那儿看来看去的模型
#: 本来就不算「在推进」（不然停滞判据永远响不了：看一眼也是成功的）。
#: ⚠️ 与 `REPLAY_ACTIONS` 现在几乎重合，但**不是同一件事**：那个是「产物能重放什么」，
#: 这个是「这一轮算不算做了事」。哪天要分家，改的应当是那一个。
_ACTIONS = ("click", "form", "scroll", "goto")

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

#: 看着像轮换码的 path 末段长什么样（字母数字，可带 `-_.` 分隔；**还要含数字**才算，
#: 见 `_id_like`）。`cr640` / `gt1791-1` / `a3f9c2` / `12345` 都算，`checkout` 不算。
_ID_LIKE_RE = re.compile(r"[0-9a-z]+(?:[-_.][0-9a-z]+)*")

#: `result["page_text_head"]` 留多少字（给人核对「模型那一眼看到了什么」）。
PAGE_HEAD_CHARS = 200

#: 状态名/字段名的兜底（页面 slug 取不出来时）
FALLBACK_STATE_NAME = "page"
FALLBACK_FILL_NAME = "field"
#: 「还没看过页面」那个状态的名字。它会被**预占**，免得页面 slug 与它撞名。
START_STATE = "start"

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
    """跑飞的上限。到顶就停，并如实记下「为什么停」。"""

    max_steps: int = DEFAULT_MAX_STEPS
    max_rounds: int = DEFAULT_MAX_ROUNDS
    #: 计划模式下「连着几轮没有推进就停」（设计注 §2.5 的 `STALL_LIMIT`）。
    #: ⚠️ **6 这个数是设计注自己给的猜测，不是量出来的**：它是产物那边确定性重放的
    #: `STUCK_LIMIT = 3` 的两倍（探路有正常的「看几眼才动手」）。
    #: **Task 1 校准不了它** —— M3 实测是 20/20/20，而那个 20 被**预算钉死**
    #: （设计注原话：「这个数被预算钉死 —— 它是『预算允许多少』，不是『模型自然要用多少』」）。
    #: 要校准得先把预算放开再量，那是另一件事。
    #: ⚠️ 只有计划模式读它；**`<= 0` 一律当 1**（见 `_PlanWatch` 的注释，复审 I-3）。
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

    # ── 给 Task 7 的 draft 节点用：直接喂 template.render() ──

    def states(self) -> list:
        """`[{name, when, steps}]` —— 与 `template.render(states=...)` 同形。

        步骤按**状态**分组（同一个状态里的是连续发生的），没有可重放步骤的状态整组丢掉
        （骨架里空 steps 的状态等于没写，留着只会让人以为那儿本来有东西）。
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


def explore(url: str, goal: str, budget: Budget | int | dict | None = None, *,
            plan: "plan_module.Plan | None" = None,
            should_pause: Callable | None = None,
            client=None, session: "tools.McpSession | None" = None,
            ws_url: str | None = None, host: str | None = None, port: int | None = None,
            binary: str | None = None,
            on_step: Callable[[dict], None] | None = None,
            resume_from: list | None = None,
            resume_note: str = "",
            window_alive: Callable | None = None) -> Journey:
    """在真浏览器里为 `goal` 探 `url` 这条路，返回 `Journey`。

    参数：
      - `url` / `goal`：探哪一页、要摸清什么
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
    pages = _Pages()
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
        gate = _Gate(inner, lambda: _stop_or_raise(paused, journey, taken, limits),
                     journey, watch)

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
            """
            try:
                _emit(on_step, step)
            except Exception as exc:                       # noqa: BLE001
                if side_broken:
                    return
                side_broken.append(True)
                journey.notes.append(
                    "⚠️ 旁路（实时视图 / 账本）在这一步上没记成：%s: %s —— "
                    "探路照常往下走（旁路坏掉不许带塌主路），但这一趟的账本可能是残的。"
                    % (type(exc).__name__, exc))

        def dispatch(name: str, args: dict) -> Any:
            nonlocal taken, fails
            _stop_or_raise(paused, journey, taken, limits)   # ← 每一步之前（§6.2）
            taken += 1
            step, fill = _describe(name, args, pages, journey)
            t0 = time.time()
            try:
                raw = session.call_tool(name, args)
            except Exception as exc:                       # noqa: BLE001
                step["result"] = {"ok": False, "elapsed_ms": _ms(t0),
                                  "error": f"{type(exc).__name__}: {exc}"}
                step["note"] = _say(name, step["target"], False)
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
            step["result"] = _summarize(name, args, raw, _ms(t0), fill)
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
                if moved:
                    journey.notes.append(
                        f"页面变了：现在是「{_title_of(raw)}」（{raw.get('url') or '?'}）")
            if name == "scroll" and not any("滚进视口" in n for n in journey.notes):
                journey.notes.append(
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
            journey.notes.append(
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

        opening = _brief(url, goal, limits, plan)
        if journey.replay:
            opening = _with_resume(opening, resume_from, journey.replay)
        rounds = llm.run_tool_loop(
            _SYSTEM, opening, specs, dispatch,
            max_rounds=limits.max_rounds, max_tokens=MAX_TOKENS, _client=gate,
        )
        _wrap_up(journey, rounds, limits)
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
        journey.notes.append(_rounds_lost_note(stop.reason))
        journey.notes.append(_stop_note(stop.reason, len(journey.steps), stop.detail))
    finally:
        # 计划模式的账**在 `finally` 里收**：被打断 / 停滞 / 预算到顶那几条路上 `rounds`
        # 一样拿不到，但 `_PlanWatch` **每轮都在场** —— 「怎么停的」不该决定「账还在不在」。
        # （`plan.ledger()` 只读那几轮记录，一步没走到也照样「每一项都有交代」。）
        if watch is not None:
            journey.plan_ledger = plan_module.ledger(plan, watch.rounds)
            watch.finish()              # 最后一轮也要结算（它没有下一次边界）—— **不抛停**
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
    journey.notes.append(
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


# ─────────────────────── 停止条件（人 / 预算）───────────────────────


def _stop_reason(paused, journey, taken: int, budget: Budget) -> str | None:
    """该不该停下？返回理由或 None。**只看，不做**（做由调用方决定）。"""
    if paused is not None and paused(journey):
        return "paused"
    if taken >= budget.max_steps:
        return "budget_steps"
    return None


def _stop_or_raise(paused, journey, taken: int, budget: Budget) -> None:
    """该停就抛 `_Stop` —— **两道闸共用这一个出口**（每步之前 / 每轮之前）。

    ⚠️ 人那道闸**自己抛异常**时，这里把它**归一成「暂停」**。不这么做的话，闸的错误
    会以普通 `Exception` 的身份落到 `llm.run_tool_loop` 的 `except Exception` 上 ——
    被记成**一次工具失败**、然后**循环继续**：人的中断静默降级成「有个步骤失败了，继续吧」。
    那是这条路上最坏的形状（喊停没停，而且没有任何人看得出来）。
    闸坏了要**停下来**（带上它坏在哪），不能带着一个坏掉的闸往下跑。
    """
    try:
        reason = _stop_reason(paused, journey, taken, budget)
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
        journey.notes.append("一轮都没跑起来 —— 模型一次都没回话")
    elif not last.get("tool_calls"):
        # **C3**：没有 tool_calls = 它讲完了（那道门上没有 done()）
        journey.stop_reason = "model_done"
        journey.final_answer = (last.get("content") or "").strip()
    elif len(rounds) >= budget.max_rounds:
        journey.stop_reason = "budget_rounds"
        journey.notes.append(_stop_note("budget_rounds", len(rounds)))
    else:
        journey.stop_reason = "ended"
        journey.notes.append("循环停了，但最后一轮既没有 tool_calls 也没到预算上限")


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
            journey.notes.append(
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
            self.journey.notes.append(_NO_MARK_NOTE)
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
    """

    def __init__(self, inner, check: Callable[[], None], journey: Journey, watch=None):
        self._inner = inner
        self._check = check
        self._journey = journey
        self._watch = watch
        self.chat = _Namespace(completions=_Namespace(create=self._create))

    def _create(self, **kwargs):
        # 顺序：人 / 预算在前，结算上一轮在后（两条都抛 `_Stop`，但**理由要报对**：
        # 人喊停优先于「它自己卡住了」）。
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
        if content:
            self._journey.notes.append(f"AI 说：{content}")


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
    step = {"state": pages.current_name, "action": name, "target": None, "result": None,
            "note": "", "origin": "model"}
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
        out["diagnostics"] = [d.get("kind") for d in (raw.get("diagnostics") or [])
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
    selectors = target.get("selectors") or []
    if selectors:
        # 没有文字名字时把选择器带上 —— 不是给人读的，是排查时对得回页面
        return f"没写名字的元素（{selectors[0]}）"
    return "没写名字的元素"


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
    - **R3 读哪份文字**：账上**按顺序**记下的 `observe` 的正文（`page_text_head`，头 200 字）。
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
    """
    target = None if action == "observe" else dict((row or {}).get("target") or {})
    return {"state": _state_of(row), "action": action, "target": target,
            "result": _summarize(action, args, raw, elapsed_ms, None),
            "note": _say(action, target, True), "origin": "replay"}


# ─────────────────────── 页面状态（换页 = 换状态）───────────────────────


class _Pages:
    """把观察到的页面归成状态。

    ⚠️ 判据不只是 URL：**正文变了也算换页**。SPA 与问卷站（blinkist 那类）每步都在同一
    个 URL 上换内容 —— 只认 URL 的话，一个状态的 `when` 会盖住好几个页面，
    而 `when` 是在**进这个状态时**判的（`_applies`），判错了整组步骤被静默跳过。
    """

    def __init__(self, site_url: str = ""):
        self.pages: list = []
        self._used = {START_STATE}
        self._current: dict | None = None
        #: 这次要探的那个站点的主机名（判「第一页是不是站点自己的页」用）
        self._site_host = _host_of(site_url)



    @property
    def current_name(self) -> str:
        return self._current["name"] if self._current else START_STATE

    @property
    def current_model(self):
        return self._current["model"] if self._current else None

    def note_page(self, model: dict) -> str | None:
        """记一页。返回**上一个状态名**（说明换页了），第一页返回 None。"""
        if not isinstance(model, dict):
            return None
        key = ((model.get("url") or "").split("#")[0],
               _norm(model.get("title") or ""),
               _norm(model.get("page_text") or "")[:400])
        if self._current is not None and self._current["key"] == key:
            return None
        previous = self._current["name"] if self._current else None
        entry = {
            "name": self._unique(_slug(model)),
            "when": _when_for(model),
            "key": key,
            "model": model,
            "url": model.get("url") or "",
            "title": model.get("title") or "",
        }
        self.pages.append(entry)
        self._current = entry
        return previous

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
    return when if when_holds(when, model) else ({"url_contains": url} if url else None)


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
    journey.notes.append(
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


def _brief(url: str, goal: str, budget: Budget, plan: "plan_module.Plan | None" = None) -> str:
    """开场白（模型的 user 消息）。**有计划 / 没计划是两版**（§2.2）。

    ⚠️ 没计划那一版**与今天逐字节相同** —— 它是 B4 那条判据钉的东西，
    `tests/test_browser_agent.py` 把**今天那串字节硬编码**在断言里。
    所以这一支**不许**顺手改措辞：想改就先去改那颗钉子（偷偷漂 = 自由模式的行为悄悄变了）。
    """
    free = (f"目标站点：{url}\n要做的事：{goal}\n"
            f"（你最多走 {budget.max_steps} 步、{budget.max_rounds} 轮。"
            f"现在这个浏览器窗口可能停在别的页上，先确认自己在哪。\n"
            f"⚠️ **能回答了就直接停下来说**（不调工具就是结束）—— 一直调工具会把预算耗光，"
            f"那一次你的结论一个字都留不下来。）")
    if plan is None or not plan.actionable():
        return free
    return _planned_brief(url, goal, plan, budget)


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
