"""Task 6：轮次投影 —— checkpoint（+ 闸拍清单 + 接线信息）→ **运营看得懂的轮次卡片**。

「一轮」= 图**到过几次闸口**（设计注 §六：轮是「回答」的单位，步是「证据」的单位）。
这个模块把那次停留投影成一张卡：它**正要**做什么（闸口原话）、窗口里现在是哪张图、
刚才那一步做了什么（连它的步子清单与自己的结论）。

## ⚠️ 「轮」这个词有两个事实 —— 先说清是哪两个

| 词 | 指的是 | 谁在管（谁是真源） |
|---|---|---|
| **闸拍轮次**（**本模块**） | 图**到过几次闸口** —— 每停一次等人 = 一轮 | 这里投影出来的卡片；轮数的算在 `count()`（⚠️ **不是** `Job.pauses` —— 那是**闸拍张数**，跑完/跑挂那一次也拍 ⇒ 到头了那两档 = 轮数+1） |
| **探路的模型轮数** | 一次探路里**问了几轮模型** | `journey.rounds`（`rounds_measured` 说它量到没有）/ `attempts.jsonl` 的那一格 |

两个都是真事实、都叫 `rounds`（线缆上也是：§8.2 的 `/live.rounds` 是**卡片**、
§8.1 的 `/runs[].rounds` 是**闸拍轮次的个数**）。**但它们一个字节都不许互相顶替**：
`journey.rounds` 在「没量到」的时候是 0（Task 3 遗留 3：那种情况一律记 `None`，不许记 0），
拿它当轮次数会凭空多出或少掉几轮。

⇒ 这个模块**一次都不读 `journey.rounds`**（有一条用例专门钉它：模型轮数记 7、闸拍三张，
卡片就是三张），而「轮数」这个算法**只有一处**（`count()`）—— `/runs` 上那个数字与
`/live` 里的卡片张数同源。要在代码里指「探路的模型轮数」时，请写成 `journey.rounds`
（带上主语），别写一个光秃秃的 `rounds`。

## 配对规则（写死；§六 + 计划 Task 6）

第 n 道闸上拍的那张叫 `pause-<n>`：

```
rounds[n-1].step       = gate.step                    # 这一轮**正要**做的
rounds[n-1].now        = pause-<n>                    # 现在窗口里是这样（轮 1 也有）
rounds[n-1].done.step  = values["visits"][-1]         # 刚做完的那个节点
rounds[n-1].done.shots = (pause-<n-1>, pause-<n>)     # 夹住刚才那个节点的一对
rounds[0].done         = None                         # 第一道闸之前什么都没跑
```

那四条写的是**脚下这一道闸**（`rounds[n-1]`）那一轮。历史轮次按 `visits` 的顺序落位
（第 k 轮正要做的 = 第 k 个节点 = `visits[k-1]`；刚才做完的 = `visits[k-2]`）——
对最后一轮，这两条与上面逐字相同（`visits[-1]` 就是它）。**不这么展开的话，每张卡都会说
同一个节点**，那是编话：站在第 3 道闸上，第 1 轮那张卡说的必须是 `intake`。

⚠️ 落位是**从 `visits` 的末尾对齐**的，不是从开头（`_node_at`）：服务重启过的话，
`visits` 里还带着重启之前那些节点，而轮次账是从这次启动**重新数**的 ——
起点对齐会把第 1 张卡说成重启之前那个最老的节点。

`now` 与 `done.shots.after` 是**同一张文件**（一份字节、两处引用）—— 页面别显示两遍。

## 清单比轮数多一条（`_capture_pause` 在**每一次** advance 之后都拍）

**跑完 / 跑挂那一次也拍** ⇒ 清单的最后一张在 `DONE` / `FAILED` 那两档**不是某一轮的闸**
（它既不在闸上、也没有「正要做的那个节点」）。所以：

- **卡片的张数 = 闸数**（`count(pauses, status)`，`/runs` 上那个数字与这里**同一个算法**）；
  最后那张「这一趟最后一张图」不冒充一轮，它挂在**最后一张卡**的 `last_shot` 上
  （一张卡都没有时，`rounds_note` 里点名说它 —— 不许有一张图**没人提**）。
- 一条卡说「**正要做的**」和「**刚做完的**」时，那件事必须**真的会发生 / 真的发生过**：
  - 「正要做的」：闸上说 `gate.step`；没有闸的时候（跑着 / 跑挂）说 `stage`
    （服务记着的那一步：跑着时是**正在跑**的那个节点、跑挂时是**卡在的那道闸**上那个节点）
    —— ⚠️ **但兜底不许兜出「刚做完的那一个」**（那会变成「正要开始做一件刚做完的事」，
    幽灵卡的形状）；图走到 END 那一档**什么都不说**（本来就没有「正要做的」这回事）。
  - 「刚做完的」：`visits` 的最后那一项 —— ⚠️ 但**闸把人拦下来**那两条路上
    （喊停 / 打回这一版），那一步**进过、一步都没做**（`_bailed_indices`）：卡片照旧说它是
    「刚才那一步」，但**当场说清它没做**，不许让它看起来像做完了。
- **对齐看的是「最后一道闸那个节点进没进 `visits`」这个事实，不是状态**（复审 N1/N1-bis）：
  - 跑到头（`done`）⇒ **落了盘**（终局那一支 `_enter` 正常返回）⇒ 对齐**往回一格**；
  - 跑挂（`failed`）**分两种**：节点**里**抛 ⇒ 它这一趟的写盘**被丢掉** ⇒ 不往回；
    **节点之间**抛（上一个节点已落盘、图自己中断）⇒ **落了盘** ⇒ 往回一格。
    ⚠️ 「跑挂 ⇒ 一定没落盘」是**过度概括**（我上一版就是这么写的，被复审实测打掉）。
  - 谁管什么：`OVER` 管**轮数**那条（去掉「最后一张图」）；**对齐**归 `_landed`（`LANDED`
    只是它里面那个「`done` ⇒ 落了盘」的档）。

## 这个函数的纪律

- **纯函数**：输入全在参数里，**不读盘、不读时间、不碰全局**（有一条 AST 用例钉着）。
- **不编话**：每一句只从它该来的地方来（闸口原话 / `journey` 的 `note` / `report.summary()` /
  `values["visits"]`）；没有那句话就写「这一步没说它做了什么」。
- **D16**：这一屏里**没有选择器、没有 `target`、没有原始 `result`**；图只有文件名
  （字节走 `/job/{id}/shot/{name}`，一个字节都不进 JSON）。
- `window` / `shots_note` / `stage` 是**接线信息**：原样带上，一个字不加工。
- `stage` 另有一处用处：**跑着的时候没有闸**，这一轮「正要做的」那个节点只能从它来
  （它是服务记着的「刚交下去的是哪一步」）。
"""

from __future__ import annotations

from typing import Optional

from agent.graph import REVISABLE, STEP_SAY
from agent.state import END_HUMAN_STOP, END_REVISION_CAP

__all__ = ["project", "count", "WAITING", "OVER", "VISITS", "JOURNEY", "REPORT", "EXPLORE"]

#: 「停在闸口等人」这个状态词 —— 与 `service.WAITING` **同一串字节**。
#: 为什么不 import：`service` 要 import 这个模块，反过来 import 会成环。
#: 两边不许漂：`tests/test_rounds.py` 有一条用例钉着 `rounds.WAITING == service.WAITING`
#: （`OVER` 那两档同一条用例一起钉）。
WAITING = "waiting"
#: 「这个服务自己挂了」那一档（`service.FAILED`）—— 只有 `_step_say` 用它挑那句话
#: （「跑到头」与「跑挂了」要分开说）。
FAILED = "failed"
#: 这一趟**已经到头**的两档（`service.DONE` / `service.FAILED`）：图走到了 END，或者
#: 这个服务自己挂了。它管的**只有一件事**：闸拍清单的最后一张是「这一趟最后那张图」
#: （`_capture_pause` 在跑完/跑挂那一次也拍），**不是某一轮的闸** —— `count` 去掉它。
OVER = ("done", "failed")
#: ⚠️ 「最后一道闸那个节点**进没进 `visits`**」——这是**对齐**要的那个事实，
#: 与 `OVER`（轮数那条）**不是一回事**（复审 2026-09-18 N1/N1-bis）：
#:   - `done`（图走到了 END）：终局那一支 `_enter` 是**正常返回**的 ⇒ 那个节点**在** `visits` 里；
#:   - `failed`（这一趟跑挂了）：**两回事**，看**抛在哪儿** ——
#:     节点**里**抛 ⇒ 它这一趟的写盘整个被丢掉（**不在** `visits` 里）；
#:     **节点之间**抛（上一个节点已经落了盘、随后图自己中断）⇒ 在。
#: ⚠️ **别只看 `status`**（复审实测：那是**代理**，会把「节点之间」那一档**整条往后错一格**，
#: 两张卡的两个字段都错）。事实由 `_landed()` 用服务递进来的 `stage` 判，判不出来就
#: `None` ⇒ **整条退成「说不出来」**（宁可不给名字，也不给一个错名字）。
LANDED = "done"

#: `checkpoint` 的价值里，这个模块读的那三个键（读不到就是读不到，一律不编）。
#: ⚠️ `visits` 是节点**进过**的顺序（`graph._enter` 记的）—— **不是「做过」的顺序**（复审
#: 2026-09-18 N1/N4）：闸把人拦下来（喊停/打回）那两条路上，最后那一项**一步都没做**；
#: 节点抛异常时它这一趟**根本没落盘**。要判「做没做」看 `_bailed_indices` / `_landed` 那两处。
VISITS = "visits"
JOURNEY = "journey"        #: 那一趟探路的账（`Journey`；步子清单与它自己的话从这儿来）
REPORT = "report"          #: 自测的结论（`selftest.Report`；`summary()` 是**原话**）
#: 人**打回**过哪几步（`graph._enter` 记的**只增**的账：`[{"at": <那一步>, "note": …}]`）。
#: ⚠️ 判「哪一次进过是被拦下的」要读这本账 —— `values["revised_at"]` 那个记号会被
#: `draft` 跑完清掉（复审 2026-09-18 ⑤：只看它的实现在下一道闸上就把「没做」说丢了）。
REVISIONS = "revisions"
#: 打回之后图回哪一步（`graph` 的三道路由都回它）—— 判「那次进过是被拦下的」用的凭据。
DRAFT = "draft"
#: 探路那一步的节点名 —— 这一趟探路的账长在**它刚做完**的那一轮上。
EXPLORE = "explore"
#: 自测那一步的节点名 —— 那一轮的结论是 `report.summary()`。
SELFTEST = "selftest"

#: 脚本那一步没留下人话时写的那句（设计注 §8.3 第 3 条的原话，一个字不改）。
STEP_UNSAID_SAY = "这一步没说它做了什么"
#: 读不出来是哪一步时写的那句（§8.2 的 `stage` 那一格是同一个态度：不知道就说不知道）
UNKNOWN_STEP_SAY = "不知道这一步是哪一步"
#: 最后那一轮、而且**已经到头**、而这一格**连一个节点名都给不出来**：这一趟没有「正要做的」
#: 那件事了。**不许**拿兜底凑一个节点出来当「正要做的」（凑出来的那个常常正是「刚做完的」
#: = 幽灵卡）。
#: ⚠️ 够到它要三个条件同时成立（最后一轮 + 到头了 + 一个名字都没有）—— 穷举下来只剩
#: `visits=()`（一个节点都没进过）那一种形状。**它还是一条真路，只是窄**
#: （复审 2026-09-18 ⑥ 与我各自穷举过：命中都很少、而且全是那一种形状）。
FINISHED_SAY = "这一轮没有闸：这一趟到头了，这是它最后的样子"
#: 同一格、**跑挂**那一支的那句话（与 `FINISHED_SAY` **分开**：「跑到头」与「跑挂了」
#: 不是一件事）。括注里说的是**哪张图**：卡片上的 `now` 是**闸上拍的那张**，
#: 挂掉那一刻那张在 `last_shot` 上（复审 2026-09-18 ⑤ 先前那句指错了）。
#: ⚠️ **现在走不到**（复审 2026-09-18 ⑥ 穷举 `status × visits≤4 × stage × shots≤6` ⇒ **0 命中**）：
#: 跑挂那一档里，对齐认不出来时先返回 `ALIGNMENT_UNKNOWN_SAY`，对得上时兜底要求 `stage` 非空
#: ⇒ `step` 不会是空的。**留着**是为了 `_landed` 将来换成信号①（服务递出错 task 的节点名）
#: 那一档 —— 别把它当成一条活路（`_step_say` 里那一支同理）。
CRASHED_SAY = "这一趟跑挂了：这一刻它正要做什么，state 里说不出来（挂掉那一刻那张图在 `last_shot` 上）"
UNKNOWN_DONE_SAY = "不知道刚才那一步是哪一步"
#: **对齐认不出来**时那一格说的话（判据 A/C）：宁可不给名字，也不给一个错的。
#: ⚠️ **不许**在这里替服务指定「抛在哪」（复审 2026-09-18 ①）：这句话的触发条件正是
#: 「服务报不出它停在哪」（`stage` 是空的）—— 那时连**节点之间**还是**节点里**都说不上来
#: （节点里抛 + 快照瞬时读不回来就是反例，穷举 7775 例命中）。只说我们真知道的那一句。
ALIGNMENT_UNKNOWN_SAY = ("这一轮说不上是哪一步：服务这一趟报不出它停在哪 —— "
                         "**宁可不给名字，也不给一个错的**。")
#: 一条**空记录**（没有图，也没人写 `why`）—— 空图框不许冒充页面（§3.2 第 6 行）
NO_SHOT_ROW_SAY = ("这一轮的闸拍没有留下记录（服务那本闸拍账上这一条是空的）—— "
                   "所以这一轮没有图，也没有人说为什么。")
#: 「最后一张图」（跑完 / 跑挂那一刻拍的）—— 它不是某一轮的闸拍，所以没有卡片挂它
#: 的时候得有人提它一句（**不许有一张图没人提**）
LAST_SHOT_SAY = ("这一趟最后还拍过一张 `%s`（跑到头 / 跑挂那一刻的窗口）—— 它不是某一轮的闸拍"
                 "（那会儿没有闸），所以没有卡片挂它；要看它就走 `/job/%s/shot/%s`。")
LAST_SHOT_MISSING_SAY = "这一趟最后那张图没拍成：%s（它不是某一轮的闸拍，所以没有卡片挂它。）"
#: **闸把人拦下来**那两条路上，「刚才那一步」是**进过、但一步都没做**的
#: （`graph._enter` 收下人的话就返回，节点看到 `_held(out)` 直接收摊）。
#: 卡片照旧说它是「刚才那一步」（它确实是那一轮要做的那个节点），但**当场说清它没做**。
BAILED_SAY = "这一步**没做**：人在闸上把它拦下来了（喊停，或者打回这一版）。"
BAILED_STEPS_SAY = "这一步没有步子清单：它一步都没做（人在闸上把它拦下来了）。"
#: 同一个事实、另一个槽位：被拦下的那一步**正好是这一轮「正要做的」那个**（喊停 / 连否到
#: 上限 ⇒ 图直接到头）时，它挂在这一轮的名字上（那一轮压根没跑）。
#: ⚠️ 两处都要认的**是同一个节点名**（`visits[-1]`），不是「这一张卡」——
#: 喊停那一支里，卡片的「刚做完的」是**上一个**节点（那一个真做了），
#: 把话说在它头上就是新的一句假话。
BAILED_STEP_SUFFIX = "（**没做**：人在闸上把它拦下来了）"
#: 「闸把节点拦下来」= `graph._enter` 自己写下 `end_reason` 的那两条路（喊停 / 连否到上限）。
#: ⚠️ **这张名单的根在 `graph._enter` 里**（别的停因是节点做完了才写的）。
#: ⚠️ 盯它的那条用例（`test_the_bail_markers_come_from_the_gates_own_code`）的射程**只有这些**
#: （复审 2026-09-18 ② 实测，与那条用例里的表同源）：
#:   - ✅ 认得：`_enter` 里 `out["end_reason"] = <常量名>`（唯一认得的写法）；
#:   - 🔴 会红：`_enter` 里**提到了**那个字面量的别的写法（`out.update({"end_reason": …})`）；
#:   - 🔴 会红：`_enter` **按名字直接叫、且定义在同一个文件里**的 helper 用下标赋值写它；
#:   - ⚪ **看不见**：helper 再叫 helper（间接）/ helper 里用 `out.update({...})` /
#:     **方法调用**（`_h.hold(out)`，`ast.Call.func` 是 `Attribute`）/ **跨模块** helper /
#:     拼 key 或变量当 key。
#: 最后一行是它**做不到**的事 —— 那种写法得靠人把停因加进这张名单。
BAILED_END_REASONS = (END_HUMAN_STOP, END_REVISION_CAP)
#: 步子清单空着时的三句话（**一句都不能省**：三件事各不相同，读的人要能对号）
NO_JOURNEY_SAY = ("这一轮没有探路的步子清单：state 里没有这一次探路的账（`journey`）—— "
                  "它走过哪几步这一屏说不出来（不编）。")
NOT_EXPLORE_SAY = ("这一轮没有探路的步子清单：刚才那一步是「%s」，"
                   "探路那一步的步子不在这一轮上。")
NO_STEP_READ_SAY = ("这一轮没有探路的步子清单：state 里连「走到过哪些步」都没有"
                    "（`visits` 是空的）—— 所以刚才那一步是哪一步也说不出来（不编）。")
EMPTY_JOURNEY_SAY = "这一轮没有探路的步子清单：这一趟探路一步都没走（账上就是空的）。"


def count(pauses, status: str = WAITING) -> int:
    """这个 job **到过几道闸**（= 几轮）。**「轮数」这个算法只有这一处。**

    `/runs` 上那个数字、`/live` 里卡片的张数 —— 同源（`project` 也是调它）。

    ⚠️ 闸拍清单比「轮数」多一条：`_capture_pause` 在**每一次** advance 之后都拍，
    **跑完 / 跑挂那一次也拍** —— 那一张是「这一趟最后一张图」（`last_shot`），
    它既不在闸上、也没有「正要做的那个节点」，**不是一轮**。所以到头了的那两档
    （`OVER`）要把最后一张从轮数里去掉。

    ⚠️ 别拿 `journey.rounds`（探路的模型轮数）当这个数：那**是另一个事实**
    （模块 docstring 里那张表）。
    """
    n = len(pauses or [])
    if n and status in OVER:
        n -= 1
    return n


def project(values: dict, gate: Optional[dict], *, job_id: str, status: str, say: str,
            delivered: bool, pauses: list, window: Optional[dict] = None,
            shots_note: str = "", stage: str = "", max_rounds: int = 50) -> dict:
    """把 checkpoint 的 values + 闸 + 闸拍清单 + 接线信息投影成 `/live` 上那几格。

    参数（全是**输入** —— 这个函数不读任何别的东西）：
      - `values`：checkpoint 的状态（只读 `visits` / `journey` / `report` 三个键）；
      - `gate`：`Service._project` **算好的**那道闸（`/job/{id}` 用的同一份）；
      - `pauses`：闸拍清单（`Job.shot_notes` 的形状 `[{"n", "name", "why"}, …]`）—— 一轮一条；
      - `job_id` / `status` / `say` / `delivered`：这个 job 的身份与状态 —— **原样带上**
        （投影不改事实；`/runs` 那一行的同几格也从 `_view` 来，两处口径一致）。
        `status` 另有一处用处：**闸只在 `waiting` 时露头**（见下）；
      - `window` / `shots_note` / `stage`：接线信息，**原样带上、一个字不加工**；
      - `max_rounds`：最多回几轮（超了留最近的，并说清丢了几轮）。

    返回（`/live` 上那几格的补丁，见 `Service.live`）：
      `rounds`（卡片，**旧 → 新** —— 与配对规则同一个下标方向，**一轮一张**）/
      `gate`（**只在 `waiting` 时非 null**：跑着/排队/到头了都没有闸，露着头页面上就是
      一个「还能按」的按钮）/ `stage` / `window` / `shots_note` / `truncated` /
      `rounds_note`（轮次这一块要额外说的那句人话：截断，或者「有一张最后图没卡片挂」）
      + 上面那四个原样带上的身份字段。
    """
    values = dict(values or {})
    visits = [str(v) for v in (values.get(VISITS) or [])]
    journey = values.get(JOURNEY)
    report = values.get(REPORT)

    rows = [x if isinstance(x, dict) else {} for x in (pauses or [])]
    limit = int(max_rounds)
    if limit < 1:
        # 「一轮都不回」不是一种上限，是一个编程错误（真那么干，页面上什么都不会有，
        # 而那正是「没有静默的路径」要治的形状）—— 当场炸，别把它翻译成一句含糊的人话。
        raise ValueError("max_rounds 至少是 1（一轮都不回没有意义）：%r" % (max_rounds,))

    waiting = bool(status == WAITING)
    over = bool(status in OVER)
    #: 最后一道闸那个节点**进没进 `visits`**（决定对齐往回几格；`None` = 认不出来）
    landed = _landed(status, visits, stage)
    #: **轮数 = 闸数**（到最后那一张「这一趟最后一张图」不算一轮）—— 一轮一张卡。
    gates = count(rows, status)
    gate_rows = rows[:gates]
    kept = gate_rows[gates - limit:] if gates > limit else gate_rows
    #: 留下来的第一张是**第几轮** —— 轮号用**真号**（`pause-<n>` 上的 n），别从 1 重数：
    #: 重数的话「第几轮」与「哪张图」当场错开，页面会把上一轮的图挂到这一轮上。
    first = gates - len(kept) + 1
    #: `visits` 里**哪几项是被闸拦下的**（喊停/连否到上限那一项 + 打回那些项的**下标**）。
    #: ⚠️ 认的是**下标**（不是名字）：同一个节点可能在打回之后又跑到过一次（那一次真做了）。
    bailed = _bailed_indices(values, visits, gate)

    gate_on = _gate_row(gate) if waiting else None
    cards = [_card(first + i, total=gates, shots=gate_rows, visits=visits, journey=journey,
                   report=report, gate=gate, stage=str(stage or ""), waiting=waiting,
                   status=status, over=over, landed=landed, bailed=bailed)
             for i in range(len(kept))]
    #: 「这一趟最后一张图」：到头了才有（`_capture_pause` 在跑完/跑挂那一次也拍）。
    #: 挂在**最后一张卡**上（卡片的形状统一 —— 别的卡上是 `null`）；一张卡都没有时
    #: 在 `rounds_note` 里点名（**不许有一张图没人提**）。
    last_shot = _shot_row(gates + 1, rows) if (over and len(rows) > gates) else None
    if last_shot is not None and cards:
        cards[-1]["last_shot"] = last_shot

    notes = []
    if gates > len(kept):
        notes.append(_truncated_say(gates, first, len(kept)))
    if last_shot is not None and not cards:
        notes.append(_last_shot_say(last_shot, job_id))

    return {
        # ── 身份与状态：原样带上（一个字不加工）──────────────────────────
        # 「跑完了」与「交付了」是**两件事**，它们一起走过这一段路 —— 别在半路上被并成一个。
        "job_id": job_id,
        "status": status,
        "say": say,
        "delivered": delivered,
        # ── 接线信息：原样带上，一个字不加工 ────────────────────────────────
        "stage": stage,
        "window": window,
        "shots_note": shots_note,
        # ── 投影出来的 ───────────────────────────────────────────────────
        "gate": gate_on,
        "rounds": cards,
        "truncated": bool(gates > len(kept)),
        "rounds_note": "\n".join(notes),
    }


# ─────────────────────────── 卡片 ───────────────────────────


def _card(k: int, *, total: int, shots: list, visits: list, journey, report, gate, stage: str,
          waiting: bool, status: str, over: bool, landed, bailed: set) -> dict:
    """第 k 轮（1 起）那张卡。`k == total` = **最后那一轮**（脚下这一道闸就在它上面）。

    ⚠️ `here`（脚下这一道闸）要**三个条件全中**：是最后一轮 **且** 现在真的在等人
    （`waiting`）**且** 有闸。少了 `waiting` 这一条，非等人的那几档
    （跑着 / 到头了）只要调用方手上还拿着一道闸，这一张卡就会照抄它的 `step` / 原话 /
    `revisable` —— 页面上于是出现一个「还能按」的按钮，而那时**没有人等你回话**。

    ⚠️ `landed is None`（对齐**认不出来**，见 `_landed`）时**整条退**：这一张卡的
    `step` 与 `done.step` 都**不给名字**（判据 A：宁可说不出，也不给一个错名字）。
    """
    now = _shot_row(k, shots)
    here = bool(k == total and waiting and gate)
    #: 对齐认不出来（`_landed` 给的是 `None`）⇒ **整条不给名字**（判据 A/C）
    names_ok = landed is not None
    #: 这一轮**刚做完**那个节点在 `visits` 里的下标（`None` = 对不上/认不出来）
    i_done = _node_index(visits, k, -1, total, landed=landed) if k > 1 else None
    done_step = visits[i_done] if i_done is not None else ""

    step, i_step = "", None
    if here:
        step = str(gate.get("step") or "")
    else:
        i_step = _node_index(visits, k, 0, total, landed=landed)
        step = visits[i_step] if i_step is not None else ""
    if not step and names_ok and k == total and stage and stage != done_step:
        # 前面都没有：这一轮正要做的那个节点只能问接线信息（`stage` = 服务记着的那一步：
        # 跑着时是**正在跑**的那个节点、跑挂时是**它卡在的那道闸**上那个节点）。
        # ⚠️ **兜底不许兜出「刚做完的那一个」**（复审 2026-09-18 N1）：那样这张卡会说
        # 「正要开始做一件刚做完的事」—— 那正是幽灵卡的形状。同一个节点就**不编**，
        # 由 `_step_say` 说清「说不出来」。
        step = str(stage)

    #: 这个节点**被闸拦下来了**（喊停 / 打回）—— 它「进过、一步都没做」。
    #: ⚠️ 认的是**下标**（`visits` 里那一项），不是名字：同一个节点可能被打回之后**又跑过一次**
    #: （那一次真做了）。下标对不上（比如 `step` 是从闸上抄来的）时退一步看名字 —— 但那时
    #: 只认「最后那一项」那一条（喊停那一支：图就到那儿为止）。
    stopped = bool(step and (i_step in bailed if i_step is not None
                             else (len(visits) - 1 in bailed and step == visits[-1])))
    card = {
        "n": k,
        "step": step or None,
        "step_say": _step_say(step, stopped=stopped,
                              finished=bool(k == total and not step and over), status=status,
                              unknown_alignment=bool(not names_ok and k == total)),
        #: 闸口的**原话**（「它刚要做什么」）—— 只有脚下这一道闸有；历史轮次的闸话
        #: 早就不在 state 里了，编一句出来就是假话。
        "say": str((gate or {}).get("ask") or "") if here else "",
        #: 「打回」只对脚下这一道闸有意义（`lint` / `selftest` / `deliver` 三道）
        "revisable": _revisable(step, here),
        "now": now,
        #: 「这一趟最后一张图」（跑完/跑挂那一刻）—— 只有最后一张卡上可能非 null
        "last_shot": None,
        "done": None,
    }
    if k > 1:
        card["done"] = _done_row(k, total=total, shots=shots, visits=visits, journey=journey,
                                 report=report, now=now, step=done_step, i_step=i_done,
                                 bailed=bailed, names_ok=names_ok)
    return card


def _step_say(step: str, *, stopped: bool, finished: bool, status: str,
              unknown_alignment: bool = False) -> str:
    """这一轮「正要做的」那一格的人话（五种情形分开说，一句都不许混）。

    被拦下的那一步挂在这一格上时（喊停 / 连否到上限 ⇒ 图直接到头），**当场说清它没做** ——
    不然那一轮读起来像「正要开始做一件刚做完的事」（F1 的那个幽灵形状）。
    「说不出来」那一档再分两句话：**对齐认不出来** / **到头了**。
    ⚠️ `CRASHED_SAY` 那一支**现在走不到**（见那个常量那头：跑挂那一档 `step` 不会是空的）——
    留着是为了 `_landed` 将来换成信号① 的那一天，别把它当成活路。
    """
    if stopped:
        return "%s%s" % (STEP_SAY.get(step, step), BAILED_STEP_SUFFIX)
    if step:
        return STEP_SAY.get(step, step)
    if unknown_alignment:
        return ALIGNMENT_UNKNOWN_SAY
    if not finished:
        return UNKNOWN_STEP_SAY
    return CRASHED_SAY if status == FAILED else FINISHED_SAY


def _done_row(k: int, *, total: int, shots: list, visits: list, journey, report, now: dict,
              step: str, i_step, bailed: set, names_ok: bool = True) -> dict:
    """这一轮「**刚才那一步**做了什么」—— **脚下那一轮**上它就是 `values["visits"]` 的最后一项。

    历史轮次按 `visits` 的顺序往回数（第 k 轮的刚才那一步 = 第 k-1 个节点）。**下标对不上**
    （`visits` 比轮次短/长）时退回 `visits[-1]` —— 那也是**读出来的**一个事实（最近进过的
    是谁），不是编的；⚠️ **不是**「跑到头」那一档（那里 `visits[-1]` 正是最后一道闸的节点，
    `_node_index` 给得出下标）。
    ⚠️ **对齐认不出来时（`names_ok=False`）不退回**：那时连名字都不给
    （`ALIGNMENT_UNKNOWN_SAY`）—— 复审 2026-09-18 ④ 实测过：那种输入下 `done.step` 全是
    `None`，`visits[-1]` 一次都没被拿上来。

    ⚠️ **`bailed`**：喊停 / 打回那两条路上，这一步**进过、一步都没做**（`_bailed_indices`
    那一组下标）。卡片照旧说它是「刚才那一步」，但 `say` 与 `steps_note` **当场说清它没做**
    —— 不许让它看起来像做完了（那正是「刚做完的必须真发生过」那条）。
    """
    step = step or (visits[-1] if (names_ok and visits) else "")
    #: 这一步**被闸拦下来了**吗 —— 认**下标**（同一个节点可能后来又跑过一次，那一次真做了）
    was_bailed = bool(step and i_step is not None and i_step in bailed)
    steps, steps_note = _steps_of(journey, step, bailed=was_bailed)
    if not names_ok:
        steps_note = ALIGNMENT_UNKNOWN_SAY
    return {
        "step": step or None,
        "step_say": (STEP_SAY.get(step, step) if step else
                     (UNKNOWN_DONE_SAY if names_ok else ALIGNMENT_UNKNOWN_SAY)),
        #: 这一步**自己的结论**（自测那一步是 `report.summary()` 的原话；被闸拦下来的
        #: 那一步是「它没做」这句 —— 那确实是它的结论）
        "say": BAILED_SAY if was_bailed else _report_say(report, step),
        #: 夹住刚才那个节点的一对闸拍（`pause-<k-1>` / `pause-<k>`）
        "shots": {"before": _name_at(shots, k - 1), "after": now.get("name")},
        "steps": steps,
        "steps_note": steps_note,
    }


def _landed(status: str, visits: list, stage: str) -> Optional[bool]:
    """最后一道闸那个节点**进没进 `visits`** —— 对齐要的那个**事实**（`None` = 认不出来）。

    ⚠️ **不许只看 `status`**（复审 2026-09-18 N1-bis 实测）：那是个**代理**，
    「跑挂」那一档里两种抛法**恰好相反** —— 节点**里**抛 ⇒ 这一趟的写盘被丢掉（**没进**）；
    **节点之间**抛 ⇒ 上一个节点已经落了盘（**进了**）。拿状态当事实会把后一种**整条错一格**
    （每张卡的 `step` 与 `done.step` **同时往后一个节点**）。

    事实从**服务递进来的 `stage`** 读（`_where_it_stopped` 的产物，那一位就是它现在停在哪）：
      - `done` ⇒ 进了（终局那一支 `_enter` 是正常返回的）；
      - `failed` ⇒ **看 `stage` 与 `visits` 最后那一项是不是同一个节点**：
        同一个 ⇒ 服务报的就是那个**已经落了盘**的节点（节点之间抛）⇒ 进了；
        不同（或者 `visits` 是空的）⇒ 报的是**还没落盘**的那一个（节点里抛）⇒ 没进；
      - `stage` 是空的 ⇒ **认不出来**（`None`）：调用方**整条退**，不给节点名。
      - 在闸上等人 / 跑着 ⇒ 没进（`_enter` 被 `interrupt()` 打断了，那一格没落盘）。

    ⚠️ 这一条是**代理的代理** —— 但它的洞**很窄**，别把它说大（复审 2026-09-18 更正过我一次）：
    够得着的只有「`status=failed` 而**快照半读得到**」那一种形状（`_where_it_stopped` 三路
    都空 ⇒ 回 `visits[-1]` ⇒ 那时 `stage == visits[-1]` 会把「节点里抛」读成「进了」）。
    **两个读一起失败时有安全网**：`values={}` ⇒ `visits` 空 ⇒ `landed=None` ⇒ 安全地整条退。
    跑着那一档**根本不看 `stage`**（`status != FAILED` 先返回 `False`）——
    我先前那个「跑着的时候会误判」的例子是**错的**。
    最稳的做法是服务把快照里**出错那个 task 的节点名**直接递进来（复审点名的信号①）——
    这一版先用信号②（`stage` 本来就在入参里），把这个边界写在这儿。
    """
    if status == LANDED:
        return True
    if status != FAILED:
        return False
    if not stage:
        return None
    return bool(visits) and str(stage) == visits[-1]


def _node_index(visits: list, k: int, offset: int, total: int, *, landed) -> Optional[int]:
    """第 k 轮（1 起）对应的那个节点**在 `visits` 里的下标**（对不上就是 `None`）。

    从 `visits` 的**末尾**对齐：这 `total` 轮对应的是 `visits` 最后的那几个节点
    （服务重启过的话，前面那些是上一趟的账 —— 起点对齐会把第 1 张卡说成**第一个**节点，
    而它说的是第 `total` 轮）。

    `landed`（见 `_landed`）为真时再往回一格：那时最后一道闸那个节点**也在 `visits` 里**
    （它正是 `offset=0` 要的那一个）。`landed` 为 `None`（认不出来）时**不给下标** ——
    调用方要整条退。
    """
    if landed is None:
        return None
    i = len(visits) - (total - k) - (1 if landed else 0) + offset
    return i if 0 <= i < len(visits) else None


def _node_at(visits: list, k: int, offset: int, total: int, *, landed) -> str:
    """第 k 轮（1 起）对应那个节点的**名字**（对不上 / 认不出来就空串，**不许编**）。"""
    i = _node_index(visits, k, offset, total, landed=landed)
    return visits[i] if i is not None else ""


def _bailed_indices(values: dict, visits: list, gate: Optional[dict]) -> set:
    """`visits` 里**哪几项进过、但一步都没做**（闸把人拦下来了）—— 一组下标。

    两个记号，都由 `graph._enter` 在**闸上**写：

      - **喊停 / 连否到上限**（`end_reason` 是闸自己写的那两条）：图就到那儿为止 ⇒
        **最后那一项**被拦下了（前一项是它之前那个节点，那个真做了）；
      - **打回**（`revisions` 那本**只增的账**点了名的节点）：那一次进过、被拦下 ⇒
        ⚠️ 凭据是「**紧接着一个 `draft` 轮**」：`_enter` 的 revise 分支只有
        `lint`/`selftest`/`deliver` 三道闸有，而那三道被打回之后**路由都回 `draft`**
        ⇒ `visits` 里那一项的下一项就是 `draft`。
        ⚠️ **别按节点名一竿子认**（复审 2026-09-18 ⑤ 的射程）：同一个节点可能在打回之后
        **又跑到过一次**（那一次是真做了），按名字认会把**那一次**也说成「没做」——
        相邻那一格的凭据就是用来把两次分开的。
        「下一项」的取法：`visits` 里紧接着的那一项；**最后那一项**没有下一项时看
        **脚下这一道闸**（`gate.step` —— 打回之后停在 `draft` 闸上时，`draft` 那个节点的
        `_enter` 被 `interrupt()` 打断了、还没落盘，所以它不在 `visits` 里）。
        ⚠️ 也别只看 `revised_at`（复审实测的假话）：那个记号在**人又在下一道闸上回了话**
        之后就没了（`draft` 跑完会清掉它）⇒ 历史轮次上的那句「没做」当场蒸发。

    ⚠️ 这不是「停因名单」：别的停因（`lint_cap` / `explore_unfinished` / …）是**节点自己
    做完了**才有的结论 —— 那些步**做了**。这张名单的根在 `graph._enter` 里，
    有一条用例拿 AST 从那个函数的赋值点把它**长出来**（认得的那种写法多一条就红）。
    """
    out = set()
    if not visits:
        return out
    if str(values.get("end_reason") or "") in BAILED_END_REASONS:
        out.add(len(visits) - 1)
    revised = {str((row or {}).get("at") or "")
               for row in (values.get(REVISIONS) or []) if isinstance(row, dict)}
    revised.discard("")
    nexts = list(visits[1:]) + [str((gate or {}).get("step") or "")]
    for i, name in enumerate(visits):
        if name in revised and nexts[i] == DRAFT:
            out.add(i)
    return out


def _revisable(step: str, here: bool) -> bool:
    """「打回」那一档（§九：`lint` / `selftest` / `deliver` 三道闸上是「这一版不要了，回 draft」）。

    ⚠️ 两道判据缺一不可：**是脚下这一道闸**（历史轮次上没有闸）+ 那一步在那三道里。
    名单从 `graph.REVISABLE` 来（不在这儿另抄一份 —— 两张名单早晚漂）。
    """
    return bool(here and step and step in REVISABLE)


def _gate_row(gate: dict) -> dict:
    """闸口那一格：**原话 + 能做什么照抄**，另加「打回」那一档（§8.2 的 `revisable`）。

    只在 `waiting` 时给（见 `project`）—— 跑着/排队时露着头，页面上就是一个「还能按」的按钮。
    """
    out = dict(gate or {})
    out["revisable"] = _revisable(str(out.get("step") or ""), True)
    return out


def _shot_row(k: int, shots: list) -> dict:
    """第 k 轮的闸拍（`pause-<k>`）。清单上一条一轮 —— 位置就是轮号。"""
    row = shots[k - 1] if 0 <= k - 1 < len(shots) else {}
    name = _name(row.get("name"))
    why = str(row.get("why") or "")
    if name is None and not why:
        # 没有图、也**没人说为什么** —— 那就是一条空记录。让它这么过去，页面上就是一个
        # 没人解释的空图框（§3.2 第 6 行明令不许）。
        why = NO_SHOT_ROW_SAY
    return {"n": k, "name": name, "why": why}


def _name_at(shots: list, k: int) -> Optional[str]:
    """第 k 轮那张图的**文件名**（没有就是 `None`）—— 只给名字，字节走 `/job/{id}/shot/`。"""
    row = shots[k - 1] if 0 <= k - 1 < len(shots) else {}
    return _name(row.get("name"))


def _name(x) -> Optional[str]:
    """账上那一格是不是一个**文件名**。不是就当没有 —— 图名只许是 `pause-3.png` 那样的一段。"""
    return str(x) if isinstance(x, str) and x.strip() else None


def _steps_of(journey, done_step: str, *, bailed: bool = False) -> tuple:
    """这一轮的步子清单（`done.steps`）+ 一句说清（空的时候**为什么**空）。

    四件事各不相同，所以四句话也各不相同（读的人要能对号）：**这一步没做** /
    没有账 / 不是探路那一轮 / 账是空的。**一句都不省** ——
    空清单没人解释，读的人会以为「这一轮没做事」。
    """
    if bailed:
        # 被闸拦下来的那一步：它**一步都没做** ⇒ 就算 state 里躺着一本探路的账
        # （上一趟的），也**不是它的**（挂上去就成了「它做了 30 步」的假话）。
        return [], BAILED_STEPS_SAY
    if done_step != EXPLORE:
        if not done_step:
            return [], NO_STEP_READ_SAY
        return [], NOT_EXPLORE_SAY % STEP_SAY.get(done_step, done_step)
    if journey is None:
        return [], NO_JOURNEY_SAY
    rows = [_step_row(s) for s in (getattr(journey, "steps", None) or [])
            if isinstance(s, dict)]
    if not rows:
        return [], EMPTY_JOURNEY_SAY
    return rows, ""


def _step_row(step: dict) -> dict:
    """探路里的一步 → 卡片上的一行（D16：人话 + 文件名，**不搬** `target` / `result`）。

    ⚠️ 键集合是**钉住的四样**（`note` / `shots` / `shot_after_deferred` / `n`）：
    `target` 里那些选择器、`result` 里那份原始回执，一个都不许进这一屏。
    """
    n = step.get("step_no")
    return {
        "n": n if isinstance(n, int) else None,
        "note": str(step.get("note") or "").strip() or STEP_UNSAID_SAY,
        "shots": {"before": _name(step.get("shot_before")),
                  "after": _name(step.get("shot_after"))},
        #: 点后那张是**随后补拍**的（做成了但页面没变）—— Task 7 要打「随后确认时补拍」
        "shot_after_deferred": bool(step.get("shot_after_deferred")),
    }


def _report_say(report, done_step: str) -> str:
    """这一步**自己的结论**的**原话**。今天只有自测那一步有（`report.summary()`）。

    别的步没有就空着 —— 不编一句像结论的话（§8.3 第 3 条：每句 `say` 只从它该来的地方来）。
    """
    if done_step != SELFTEST or report is None or not hasattr(report, "summary"):
        return ""
    return str(report.summary() or "")


def _last_shot_say(shot: dict, job_id: str) -> str:
    """一张卡都没有时，「这一趟最后一张图」得有人提它一句（**不许有一张图没人提**）。"""
    if shot.get("name"):
        return LAST_SHOT_SAY % (shot["name"], job_id, shot["name"])
    return LAST_SHOT_MISSING_SAY % (shot.get("why") or "没说为什么")


def _truncated_say(total: int, first: int, shown: int) -> str:
    """截断时那句人话（§8.2：`truncated` 为真时**并说明**）—— 丢了几轮、留下的从第几轮起。"""
    return ("轮次太多，这一屏只回了**最近 %d 轮**（第 %d–%d 轮）；前面 %d 轮没回 —— "
            "它们的图还在盘上（`pause-<n>.png`），这一屏不显示它们。" % (shown, first, total,
                                                                 first - 1))
