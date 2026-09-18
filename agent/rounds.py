"""Task 6：轮次投影 —— checkpoint（+ 闸拍清单 + 接线信息）→ **运营看得懂的轮次卡片**。

「一轮」= 图**到过几次闸口**（设计注 §六：轮是「回答」的单位，步是「证据」的单位）。
这个模块把那次停留投影成一张卡：它**正要**做什么（闸口原话）、窗口里现在是哪张图、
刚才那一步做了什么（连它的步子清单与自己的结论）。

## ⚠️ 「轮」这个词有两个事实 —— 先说清是哪两个

| 词 | 指的是 | 谁在管（谁是真源） |
|---|---|---|
| **闸拍轮次**（**本模块**） | 图**到过几次闸口** —— 每停一次等人 = 一轮 | `Job.pauses` / `pause-<n>.png` / 这里投影出来的卡片 |
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
  - 「正要做的」：闸上说 `gate.step`；跑着的时候说 `stage`（服务记着刚交下去的是哪一步）；
    **已经到头的那一档什么都不说**（没有「正要做的」这回事，编一个就是幽灵卡）。
  - 「刚做完的」：`visits` 的最后那一项 —— ⚠️ 但**闸把人拦下来**那两条路上
    （喊停 / 打回这一版），那一步**进过、一步都没做**（`_bailed`）：卡片照旧说它是
    「刚才那一步」，但**当场说清它没做**，不许让它看起来像做完了。

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
#: 这一趟**已经到头**的两档（`service.DONE` / `service.FAILED`）：图走到了 END，或者
#: 这个服务自己挂了。它们决定两件事（两件都是**同一个事实**的两面）：
#:   ① 闸拍清单的最后一张是「这一趟最后那张图」，不是某一轮的闸（`count` 去掉它）；
#:   ② 最后一道闸那个节点**已经进过 `visits`**（终局那一支 `_enter` 是**正常返回**的，
#:      它把 `visits` 提交了）—— 所以对齐要再往回一格（`_node_at` 的 `over`）。
OVER = ("done", "failed")

#: `checkpoint` 的价值里，这个模块读的那三个键（读不到就是读不到，一律不编）。
VISITS = "visits"          #: 节点**按顺序**的账（闸在节点之前 ⇒ 它的最后一项就是刚做完那个）
JOURNEY = "journey"        #: 那一趟探路的账（`Journey`；步子清单与它自己的话从这儿来）
REPORT = "report"          #: 自测的结论（`selftest.Report`；`summary()` 是**原话**）
#: 探路那一步的节点名 —— 这一趟探路的账长在**它刚做完**的那一轮上。
EXPLORE = "explore"
#: 自测那一步的节点名 —— 那一轮的结论是 `report.summary()`。
SELFTEST = "selftest"

#: 脚本那一步没留下人话时写的那句（设计注 §8.3 第 3 条的原话，一个字不改）。
STEP_UNSAID_SAY = "这一步没说它做了什么"
#: 读不出来是哪一步时写的那句（§8.2 的 `stage` 那一格是同一个态度：不知道就说不知道）
UNKNOWN_STEP_SAY = "不知道这一步是哪一步"
#: 最后那一轮、而且**已经到头**：这一趟没有「正要做的」那件事了（它不在闸上 ——
#: 图走到了 END，或者这个服务自己挂了）。**不许**拿兜底凑一个节点出来当「正要做的」。
FINISHED_SAY = "这一轮没有闸：这一趟到头了，这是它最后的样子"
UNKNOWN_DONE_SAY = "不知道刚才那一步是哪一步"
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
#: ⚠️ **这张名单的根在 `graph._enter` 里**（别的停因是节点做完了才写的）——
#: `tests/test_rounds.py` 有一条用例拿 AST 从那个函数的赋值点把它长出来，多一条就红。
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
    #: **轮数 = 闸数**（到最后那一张「这一趟最后一张图」不算一轮）—— 一轮一张卡。
    gates = count(rows, status)
    gate_rows = rows[:gates]
    kept = gate_rows[gates - limit:] if gates > limit else gate_rows
    #: 留下来的第一张是**第几轮** —— 轮号用**真号**（`pause-<n>` 上的 n），别从 1 重数：
    #: 重数的话「第几轮」与「哪张图」当场错开，页面会把上一轮的图挂到这一轮上。
    first = gates - len(kept) + 1
    #: 闸把**哪一个节点**拦下来了（喊停 / 打回 / 连否到上限）—— 空串 = 没有。
    #: ⚠️ 认的是**节点名**（`visits[-1]`），不是「哪一张卡」：那个名字可能出现在
    #: 最后一张卡的 `done.step`（打回那一支：图回 draft，那一轮已经过去了），
    #: 也可能出现在它的 `step`（喊停那一支：图直接到头，那一轮压根没跑）。
    bailed_node = visits[-1] if _bailed(values, visits) else ""

    gate_on = _gate_row(gate) if waiting else None
    cards = [_card(first + i, total=gates, shots=gate_rows, visits=visits, journey=journey,
                   report=report, gate=gate, stage=str(stage or ""), waiting=waiting,
                   over=over, bailed_node=bailed_node)
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
          waiting: bool, over: bool, bailed_node: str) -> dict:
    """第 k 轮（1 起）那张卡。`k == total` = **最后那一轮**（脚下这一道闸就在它上面）。

    ⚠️ `here`（脚下这一道闸）**两道判据缺一不可**：是最后一轮 **且** 现在真的在等人
    （`waiting`）**且** 有闸。少了 `waiting` 这一条，非等人的那几档（跑着 / 到头了）
    只要调用方手上还拿着一道闸，这一张卡就会照抄它的 `step` / 原话 / `revisable` ——
    页面上于是出现一个「还能按」的按钮，而那时**没有人等你回话**。
    """
    now = _shot_row(k, shots)
    here = bool(k == total and waiting and gate)

    step = str(gate.get("step") or "") if here else ""
    if not step:
        step = _node_at(visits, k, 0, total, over=over)
    if not step and k == total:
        # 前面都没有：跑着的时候**没有闸**，这一轮正要做的那个节点只能问接线信息
        # （`stage` = 服务记着刚交下去的那一步）。**到头了那一档不编** ——
        # 没有「正要做的」这回事，硬给一个就是一张幽灵卡（它的 `step` 会与 `done.step`
        # 是同一个节点，读起来像「正要开始做一件已经做完的事」）。
        step = str(stage or "") if not over else ""

    #: 这个节点**被闸拦下来了**（喊停 / 打回）—— 它「进过、一步都没做」。
    #: 命中它的是**这一轮正要做的那个**（图直接到头那一支：那一轮压根没跑）。
    stopped = bool(step and bailed_node and step == bailed_node)
    card = {
        "n": k,
        "step": step or None,
        "step_say": _step_say(step, stopped=stopped,
                              finished=bool(k == total and over and not step)),
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
                                 report=report, now=now, over=over, bailed_node=bailed_node)
    return card


def _step_say(step: str, *, stopped: bool, finished: bool) -> str:
    """这一轮「正要做的」那一格的人话（三种情形分开说）。

    被拦下的那一步挂在这一格上时（喊停 / 连否到上限 ⇒ 图直接到头），**当场说清它没做** ——
    不然那一轮读起来像「正要开始做一件刚做完的事」（F1 的那个幽灵形状）。
    """
    if stopped:
        return "%s%s" % (STEP_SAY.get(step, step), BAILED_STEP_SUFFIX)
    if step:
        return STEP_SAY.get(step, step)
    return FINISHED_SAY if finished else UNKNOWN_STEP_SAY


def _done_row(k: int, *, total: int, shots: list, visits: list, journey, report, now: dict,
              over: bool, bailed_node: str) -> dict:
    """这一轮「**刚才那一步**做了什么」—— 最后那一轮上就是 `values["visits"][-1]` 那件事。

    历史轮次按 `visits` 的顺序往回数（第 k 轮的刚才那一步 = 第 k-1 个节点）。两个索引
    都对不上（`visits` 比轮次短/长）时退回 `visits[-1]` —— 那也是**读出来的**一个事实
    （最近进过的是谁），不是编的。

    ⚠️ **`bailed`**：喊停 / 打回那两条路上，这一步**进过、一步都没做**
    （`_bailed` 的三个记号）。卡片照旧说它是「刚才那一步」，但 `say` 与 `steps_note`
    **当场说清它没做** —— 不许让它看起来像做完了（那正是「刚做完的必须真发生过」那条）。
    """
    step = _node_at(visits, k, -1, total, over=over) or (visits[-1] if visits else "")
    #: 这个节点**被闸拦下来了**吗 —— 认的是**节点名**（打回那一支它就落在这一格里）
    bailed = bool(step and bailed_node and step == bailed_node)
    steps, steps_note = _steps_of(journey, step, bailed=bailed)
    return {
        "step": step or None,
        "step_say": STEP_SAY.get(step, step) if step else UNKNOWN_DONE_SAY,
        #: 这一步**自己的结论**（自测那一步是 `report.summary()` 的原话；被闸拦下来的
        #: 那一步是「它没做」这句 —— 那确实是它的结论）
        "say": BAILED_SAY if bailed else _report_say(report, step),
        #: 夹住刚才那个节点的一对闸拍（`pause-<k-1>` / `pause-<k>`）
        "shots": {"before": _name_at(shots, k - 1), "after": now.get("name")},
        "steps": steps,
        "steps_note": steps_note,
    }


def _node_at(visits: list, k: int, offset: int, total: int, *, over: bool) -> str:
    """第 k 轮（1 起）对应的那个节点：`offset=0` = 这一轮**正要**做的，`-1` = **刚做完的**。

    从 `visits` 的**末尾**对齐：这 `total` 轮对应的是 `visits` 最后的那几个节点
    （服务重启过的话，前面那些是上一趟的账 —— 起点对齐会把第 1 张卡说成**第一个**节点，
    而它说的是第 `total` 轮）。

    `over`（到头了）时再往回一格：终局那一支 `_enter` 是**正常返回**的 ⇒ 最后一道闸
    那个节点也在 `visits` 里 —— 它正是 `offset=0` 要的那一个；而闸在等人/跑着的那两档
    `_enter` 是被 `interrupt()` 打断的，它**没落盘** ⇒ 不用往回。

    对不上就返回空串，由调用方决定说什么（**不许在这儿编一个节点名**）。
    """
    i = len(visits) - (total - k) - (1 if over else 0) + offset
    if 0 <= i < len(visits):
        return visits[i]
    return ""


def _bailed(values: dict, visits: list) -> bool:
    """`visits` 的最后那一项**进过、但一步都没做**吗（闸把它拦下来了）。

    两个记号，都由 `graph._enter` 在**闸上**写：
      - `revised_at == 那一步` —— 人在那一道闸上说「这版不行」（回 `draft` 重写）；
      - `end_reason` 是**闸自己**写的那两条（`human_stop` / `revision_cap`）——
        节点看到 `graph._held(out)` 就直接收摊。

    ⚠️ 这不是「停因名单」：别的停因（`lint_cap` / `explore_unfinished` / …）是**节点自己
    做完了**才有的结论 —— 那些步**做了**。这张名单的根在 `graph._enter` 里，
    有一条用例拿 AST 从那个函数的赋值点把它**长出来**（多一条拦人的路就红）。
    """
    if not visits:
        return False
    if str(values.get("revised_at") or "") == visits[-1]:
        return True
    return str(values.get("end_reason") or "") in BAILED_END_REASONS


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
