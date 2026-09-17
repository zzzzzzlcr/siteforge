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
    #: 每一轮都算数：最后一轮由 `_PlanWatch.finish()` 补结算（它没有下一次边界）。
    stall_rounds: int = 0

    #: 这一趟**问了几轮模型**（G2：`_wrap_up` 原先用完 `rounds` 就丢，于是基线 M3 量不到）。
    #: ⚠️ `0` 有四种来源，读的时候要连 `stop_reason` 一起看：
    #:   - `no_rounds`     → 真的 0 轮（模型一次都没回话）；
    #:   - `paused` / `budget_steps` / `plan_stalled` → **没量到**（`_Stop` 一穿出
    #:     `run_tool_loop`，`rounds` 那个局部变量就没了 —— 与是哪一条停因无关）。
    #:     ⚠️ `measure.baseline()` 今天**只按 `paused`** 把它记成 `None`，其余几条会被记成
    #:     真 0（M3 于是偏低，而偏低看起来像好消息）—— 那是**假数**，修点在
    #:     `service._note_attempt`（不在本文件，复审 I-5）：接计划模式消费者的那一轮必须先收掉。
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
            on_step: Callable[[dict], None] | None = None) -> Journey:
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

        def dispatch(name: str, args: dict) -> Any:
            nonlocal taken
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
                _emit(on_step, step)
                raise        # 还给 run_tool_loop：模型也必须看见这条错（不吞）
            step["result"] = _summarize(name, args, raw, _ms(t0), fill)
            if name == "goto" and isinstance(raw, dict) and raw.get("url"):
                step["target"] = {"url": raw["url"]}    # 落到哪了（可能与给的不一样）
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
            _emit(on_step, step)
            return raw

        rounds = llm.run_tool_loop(
            _SYSTEM, _brief(url, goal, limits, plan), specs, dispatch,
            max_rounds=limits.max_rounds, max_tokens=MAX_TOKENS, _client=gate,
        )
        _wrap_up(journey, rounds, limits)
    except _Stop as stop:
        journey.stop_reason = stop.reason
        # 被停下来这一路**拿不到轮数**：`rounds` 是 `run_tool_loop` 的局部变量，
        # `_Stop`（BaseException）一穿出去就没了。所以只能是 0 + 一句人话 ——
        # **不许编一个数**（P5）。那个 0 的意思是「没量到」，读账的人（`measure.baseline`）
        # 按 `stop_reason == "paused"` 把它记成 `None`，不记成「这一趟没花轮数」。
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


def _wrap_up(journey: Journey, rounds: list, budget: Budget) -> None:
    """判定它**是怎么结束的**。

    ⚠️ 模型每一轮说的话**不在这里记** —— 归 `_Gate`（它每轮都在场，包括被人打断的
    那条路上；见它的 docstring）。两处都记就会在没被打断时记成两份。

    轮数（G2）与它的汇总账**在这里落**：原先 `rounds` 用完就丢，于是「一次探路问了几轮」
    这个数**算出来了却没人接住**，基线 M3 只能靠 `steps` 反推 —— 而 steps 数的是
    **工具调用**（含 observe），与「模型想了几轮」不是一回事（实测 60 步 ≠ 60 轮）。
    """
    journey.rounds = len(rounds)
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
    step 的形状（`{state, action, target, result, note}`）是要给外面看的，
    多塞一个键就会在「报错那一步」上现形（报错也要能看出「本来想填哪个字段」）。
    """
    step = {"state": pages.current_name, "action": name, "target": None, "result": None, "note": ""}
    fill = None
    selector = str(args.get("selector") or "")
    if name == "goto":
        # goto 的 target 就是那个地址。**先记「要打开哪个」**，真打开了之后再盖成
        # 「落到哪了」（很多站会重定向）—— 失败时也还有话说（「打不开 <url>」）。
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
