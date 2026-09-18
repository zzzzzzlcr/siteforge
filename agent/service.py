"""Task 8：agent 服务 —— 把 Task 7 的图变成一个**能提交、能看进展、能答话**的服务。

```
POST /run                 收开场白 → {job_id}（立刻返回；活在一个工作线程上排队跑）
GET  /job/{id}            走到哪了、在问你什么、结果是什么（**人话**）
POST /job/{id}/reply      回答图停下来的那个问题（继续 / 喊停 / 一句纠正 / 这版不行）
POST /job/{id}/reopen     窗口没了 → 重开一个，**从断点接着跑**（P6）
GET  /job/{id}/shot/{n}   闸拍那张图（`image/png`）—— **字节走这儿，不进 JSON**（Task 3）
GET  /health              活着吗、状态存哪儿了、cdp 在哪
```

## 这个服务**不肯**做的四件事（每一件都是被吃过的亏）

1. **不猜成功判据**（§6.1）。载荷里没有 `success_text` → **提交那一刻**就拒（400）。
   图里的 `intake` 闸也能拦（`END_NO_SUCCESS_TEXT`），但那要等一次提交 + 一次调度，
   而这条输入是免费的 —— 「先贵后像别的问题」正是 Task 7 修复轮刚拆掉的形状。
2. **不替图发明默认值**（R-31）。没人给窗口层那根线、也没人点名允许跳过第 4 遍扰动 →
   **照收**，让图在 `intake` 停下并说清「缺 `set_viewport`，谁给得了」。
   服务在这儿塞一个默认，等于替人**预授权跳过**（R-5 明令不许：
   「没验到」不许读成「验过了」）。所以那条 `missing_knob` **不是**要在服务里糊掉的 bug。
3. **不许默默丢掉调用方给的约束**：
   - `allow_skips` 里有不认识的遍（图的边界**不校验**，P14：写错名字会先烧掉一次探路再炸）；
   - 显式的空 `allow_skips=[]`（和「没给」在图的边界**不可区分**，P15：约束会被静默吃掉，
     于是 `country` 回到默认放行）；
   - 载荷要了 `set_viewport`（换窗口大小）而这个部署**给不了那根线**。
   三条都在这儿（免费）拦下，并在人话里说清该怎么改。
4. **不把「跑挂了」写成「跑成了」**。`status` 说的是**job 走到哪了**，`delivered` 说的是
   **产物真的落盘了吗** —— 两件事分开摆，跑挂的 job 的 `result` 一律是 `None`。
   下游接的是「这份产物能不能上生产」这个判断，读错一次就完了。

## 状态住在 saver 里，不在这个进程里（R-19）

`GET /job/{id}` 的正文是从 **checkpoint** 投影出来的，登记表只补两件它答不了的事
（「正在跑」和「跑挂了」）。所以服务重启之后，同一个 job 仍然读得到、接得下去 ——
这正是 R-19 要的东西：状态属于 saver，不属于某个进程里的对象。
`/health` 会**明说**这次用的是哪种 saver（内存的那种，重启就没了）。

## 一次只跑一个（D6）

agent 只有**一个** Bit 窗口（外加一个自己的 gost 端口）。两个 run 并发跑会在同一个窗口上
互相踩 —— 所以这个服务是**单飞**的：一个工作线程按队列跑，后面的 job 排队等
（`status="queued"`）。这不是省事，是照着硬件的形状来的。
"""

from __future__ import annotations

import dataclasses
import datetime
import functools
import json
import os
import pathlib
import queue
import re
import threading
import time
import traceback
import urllib.error
import urllib.request
import uuid
from typing import Any, Callable, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from langgraph.types import Command
from pydantic import BaseModel, Field

from agent import (browser_agent, events, graph, journal, measure, rounds, selftest, shots,
                   tools)
from agent.graph import NODES, STEP_SAY
from agent.state import (END_DELIVERED, END_EXPLORE_UNFINISHED, END_LINT_CAP,
                         END_NO_WINDOW, END_PAUSED, END_REVISION_CAP,
                         END_SELFTEST_CAP, END_WINDOW_GONE)

__all__ = ["create_app", "app", "BitWindow", "Service", "Checkpointer",
           "QUEUED", "RUNNING", "WAITING", "DONE", "FAILED"]

# ── job 的状态（**说的都是「走到哪了」，不是「成没成」**）──────────────────────
#: 排队等（单飞：前面还有别的 run 在用那个窗口）
QUEUED = "queued"
#: 图正在跑（这一步做完或需要人时会停下）
RUNNING = "running"
#: 停在闸口，等人回话
WAITING = "waiting"
#: 图走到了 END —— **不代表成功**，成功看 `delivered`
DONE = "done"
#: 这个服务自己挂了（图抛异常 / 队列里的活炸了）—— **绝不许**被读成跑成了
FAILED = "failed"

#: 窗口层那个对象要有哪两个方法（`set_viewport` / `alive`）——
#: 服务只认这两个名字（`BitWindow` 是它的一个实现）。
WINDOW_LAYER = ("set_viewport", "alive")

#: Bit 的窗口服务（§4.6）。bit.sh 里写死的就是这个端口。
BIT_API_PORT = 54345

#: `reopen` 接得住的**两步**，以及各自要接在**哪一步**上（= 重跑那一步）：
#:   探路 ← intake（探路重跑）；自测 ← lint（自测重跑，探路与起草都不重来）
WINDOW_STEPS = {"explore": "intake", "selftest": "lint"}
#: 反查：某个「上一步」下面接着的是哪一步（给 `failed` 那一支用）
_AFTER = {prev: step for step, prev in WINDOW_STEPS.items()}
#: 「停在那一步」的哪几种结局算**窗口**造成的（只有 DONE 的 job 需要看这个）。
#:
#: 探路那一支 Task 6 起收了三种（它们的**处置相同**：账本都还在盘上，重开一个窗口
#: 就能接着走，不用从头再探）：
#:   - `explore_unfinished`：没走完（预算到顶 / 模型没给出结论）；
#:   - `paused`：**人喊停** —— 原先接不住，出路只剩「重新开一个任务」= 整轮重来；
#:   - `window_gone`：窗口没了（§1.8 的**一等停因**，与「预算走完」分开报）。
WINDOW_END_REASONS = {"explore": (END_EXPLORE_UNFINISHED, END_PAUSED, END_WINDOW_GONE),
                      "selftest": (END_NO_WINDOW,)}

#: 闸拍：**硬超时**（秒）。拍照跑在旁路线程上，超过这个数就当它没成、记一句人话 ——
#: 单飞的工作线程是唯一推图的地方（D6），被一张图按在那里 = 整个服务停摆（计划 §R6）。
SHOT_TIMEOUT_SECONDS = 20.0

#: 「这个窗口」那一块的两句人话（设计注 §十）。**写死在这里**、页面原样显示 ——
#: 不编话那条规矩在这一块的样子：方法与事实是服务说得清的，
#: 而「那台机器怎么连」是**运维知识**（仓库里没有证据），所以只说「连到那台机器」。
#: ⚠️ **不许出现 URL**：设计注明说「Bit 有一个能远程用的网页控制台 URL」是**猜测、没核实**，
#: 编一个摆上去，运营会照着一个不存在的地址去点。
WINDOW_HOW = ("连到**那台机器**的桌面（远程桌面，或那台机器上装着的 BitBrowser 客户端），"
              "在 BitBrowser 里打开下面这个窗口 id —— 就是 agent 正在用的这个浏览器；"
              "连得上就**看得见、也能直接接管**（鼠标键盘）。"
              "⚠️ 服务不代你看窗口，也不给你一个网页入口：仓库里没有证据说 Bit 有能远程用的"
              "控制台地址，所以这里不编一个。")
WINDOW_WHEN = ("**它正在跑（running）的时候不要动手** —— agent 在同一个窗口里点、填、滚，"
               "两边的鼠标会打架：动作落到别处，失败还会被记成产物的问题。看一眼可以，动手等它停下。\n"
               "**停在闸上（waiting）是唯一适合动手的时刻**（图没在动，接管不会跟它抢），"
               "但你动过页面之后，下一步就是在**你留下的那个页面**上跑的 —— "
               "**用「说一句」告诉它你动过**。\n"
               "窗口只活几分钟，去看它**不会让它活更久**；把它**关掉** = 这一趟的窗口没了"
               "（图会停在「自测那一步没有窗口」，那是一条正常的路，不是 bug）。")

#: **降级 B** 那句话（设计注 §5.5）：关掉每步抓拍时，页面上**一直**显示它。
#: ⚠️ 不许静默降级 —— 运营看到一次「没有逐步图」的运行，必须同时看到「为什么」。
#: ⚠️ 而且**每一句都得是真的**（修复轮 1 抓到的）：接线补上之前，「去掉开关就恢复」是空话
#: （`explore(shots_dir=...)` 在生产里没有调用方）—— 现在 `_explore_for` 给了目录，
#: 这句话才成立；「**下一次探路**」那几个字也不许省：开关不会把已经跑过的那些步补回来。
SHOTS_OFF_SAY = ("这一次没留逐步的图：%s"
                 "每一道闸拍的那张还在（`pause-<n>.png`），逐步的一句话清单也还在。"
                 "要恢复逐步的图：把 `SITEFORGE_STEP_SHOTS` 去掉、或设成 1 —— "
                 "**下一次探路**就会重新逐步拍（这一次已经跑过的那几步补不回来）。")


# ── 时间线（Task 4 / 契约七格）：目录表那九条各自的**人话**─────────────────
# 这些话**写死在这儿**（不在九个调用点上现拼）：它们是给运营看的（D16），
# 散着写早晚漂成两套口径。**只有真事实才在调用点上填**（哪一步、为什么、哪个错误），
# 而且填进去的必须是从快照/回执/异常里**读出来的** —— 读不出来就明说读不出来，不编。
#: 目录表第 3 行：交上去了 / 前面还有 run 在排队等窗口
SUBMITTED_SAY = "收到了，排队开跑。"
QUEUED_SAY = "排队等窗口（前面还有别的 run 在用）。"
#: 目录表第 4 行（与 `/job/{id}` 上那句**同一句** —— 一件事一处口径）
RUNNING_SAY = "在跑：真浏览器 + 模型，做完一步或需要你时就会停下来。"
#: 目录表第 7 行
RECOVERED_SAY = "（服务重启过：这个任务是从 checkpoint 里捡回来的）"
#: 目录表第 1 行。`%s` = 它停在哪一步**之前**（从快照里读出来的人话步骤名）
WINDOW_DEAD_SAY = ("窗口没了 —— Bit 的窗口只活几分钟。它停在「%s」之前；"
                   "接着走之前得先重开一个窗口。")
#: 目录表第 8 行：**说了就要让人知道这句话去哪了**
HUMAN_SAID_THROUGH_SAY = "这句话会一路带进「写这一版 py」。"
#: 没有 note 的那次「继续」：不能说「这句话带进去了」（没有话）
HUMAN_SAID_PLAIN_SAY = "（你在「%s」那道闸上按了继续，没有多说。）"
#: 目录表第 6 行的**闸拍**那一半。`%s` = 拍不成的原因**原文**（不许让空图框冒充页面）
SHOT_MISSING_SAY = "这一轮没留下图：%s"
#: 目录表第 6 行的**步拍**那一半（Task 5）里**说得出是哪一步**的那一支
#: （`step["shots_why"]`）。与上面那两句都不同 —— 三句话读的人要能**对号**：
#:   ① 闸拍 = `SHOT_MISSING_SAY`（这一轮，闸上那一张）；
#:   ② 步拍·某一步 = 这一句；
#:   ③ 步拍·整条路 = 下面那句（说不出是哪一步，就**不许**说「这一步」）。
STEP_SHOT_MISSING_SAY = "这一步没留下图：%s"
#: 步拍**整条路**坏了那一支（`journey.shots_why` 的原文）。
#: ⚠️ 它**说得出的是整条路**、不是某一步（那个字段就没有「哪一步」）—— 所以它既不许
#: 跟着 ② 说「这一步」，也不许跟着 ① 说「这一轮」（那样读的人会以为是闸上那张图）。
#: **说得出多少说多少，别编**（修复轮 1 的 Important-2）。
STEP_SHOT_CHANNEL_SAY = ("探路里的步拍图这一次没留下：%s"
                         "（说的是探路途中每一步的那些图，不是闸上停下来那一轮的那张 —— "
                         "闸上那张没留成会另说。）")
#: 目录表第 6 行的**第四支**（Task 5 修复轮 3）：步拍撞上上限，**从这一步起不再拍**。
#: 与上面三句都不同：这不是「拍不成」，是**知道的、不再拍**（上限本身是对的）——
#: 缺的只是「它生效了」这件事没人说。人话里必须写清**这是知道的，不是漏了**，
#: 否则读的人看到的就是一个没人解释的空图框（那正是 §3.2 第 6 行要治的形状）。
STEP_SHOT_CAPPED_SAY = ("逐步留在盘上的图到顶了，**从这一步起不再拍**：%s"
                        " —— 这是**知道的**，不是漏了（后面的步要是不对劲，账上不会再有图）。")
#: Task 5 修复轮 1（Important-1）：**自测的播报没送到时间线**。
#: `%s` = 原因原文。这条是给读时间线的人看的 ——「这一趟你看到的自测结果可能不全」。
NARRATION_BROKEN_SAY = ("自测的播报没送到时间线上：%s —— 自测**照常跑完**了"
                        "（旁路坏掉不许带塌它），但这一趟的时间线上会少掉那几遍的结果。")
#: 目录表第 6 行：撞上限时那句话的前缀（后面照抄 `end_note`）
CAP_SAY_PREFIX = "撞上限了："
#: 到头了但快照里**没留人话** —— 明说「它没说清」（`say` 不许空着，更不许不记）
END_UNSAID_SAY = ("这一趟到头了（%s），但快照里没留一句人话说明是怎么结束的 —— "
                  "这本身就不正常，别把它当成跑成了。")
#: 一条**新**的、会静默的路（不在目录表九条里）：跑完这一步之后状态读不回来。
#: 只 `traceback.print_exc()` 的话，「窗口还在不在」「这一趟是不是到头了」就没人说过。
STATE_UNREADABLE_SAY = ("这一步结束之后，它读不回自己的状态（%s）—— 「窗口还在不在」"
                        "「这一趟是不是到头了」这两件事这次没记进时间线。"
                        "任务本身的进展还在 checkpoint 里。")
#: `/live` 的 `note`：时间线**不持久**这件事要明说（设计注 §3.5）。
#: 不说的话，人会把「空」读成「它什么都没干」。
#: ⚠️ 后半句 Task 6 改了（原先写「轮次与截图仍然完整」—— 那句话在**捡回来的 job** 上
#: 是假的）：闸拍那本账（`Job.shot_notes`）与轮号一样是 **process-local** 的，
#: 而轮号从 1 重新数 ⇒ 同一个名字（`pause-1.png`）的旧图会被新的一轮顶掉
#: （`/job/{id}/shot/{name}` 那条 `no-cache` 就是为这件事写的）。
#: 真话分两半：**图还在盘上**（这一条留），**轮次是从这次启动重新数的**（这一条补上）。
RESTART_NOTE = ("服务重启过：这之前的时间线没有了。运行的状态还在（从 checkpoint 里读）；"
                "之前拍下的图还在盘上，但**这一屏的轮次从这次启动起重新数**"
                "（同一个名字的旧图会被新的一轮顶掉）。**重启之前那几轮的卡片这一屏不显示** "
                "—— 轮次账在进程里，没从盘上反推：反推会把轮号与新旧文件名混在一起，"
                "比不显示更容易骗人。")
#: `/live` 上「读不回状态」那句话（**跑着/排队**那几档：`_live_facts` 兜得住）。
#: ⚠️ **读**这一侧（GET）不许写时间线（「读不许写」），所以这句话随响应回去、
#: 在页面上看得见 —— 那也是「没有静默的路径」在这一侧的样子。
LIVE_STATE_UNREADABLE_SAY = ("这一屏少了几格：读不回这个任务的状态（%s）—— "
                             "轮次与闸口这一次说不出来（时间线不受影响）。")
#: 同一件事、另一档：**停在闸上 / 到头了**的 job 读不回状态时给的那句话。
#: 那两档 `_view` **自己**就要读 state（在 `_live_facts` 之前），兜不住 ⇒ 只能响 ——
#: 但响的是一句人话（503），不是一页 `Internal Server Error`（页面上原样显示 detail + 重拉）。
LIVE_STATE_UNREADABLE_503 = ("读不回这个任务的状态（%s）—— 它现在是**在等人还是到头了**"
                             "这一次说不出来，所以这一屏给不了。过一会儿重拉一次；"
                             "运行本身的进展还在 checkpoint 里。")
#: `GET /runs` 空列表时的 `note`：这个列表是 **process-local** 的，
#: 不说的话人会把「空」读成「什么都没提交过」。
NO_RUNS_SAY = ("还没有任何运行。这个列表是**这个进程**记得的那些 —— 服务重启过的话，"
               "之前提交的就不在这儿了（它们的状态还在 checkpoint 里，知道 job id 就还能看）。")
#: 「撞上限」那三种停因（设计注 §3.2 第 4 行）—— 只用来挑 `kind`；
#: 人话一律照抄快照里的 `end_note`（原话），不在这儿另写一句。
CAP_END_REASONS = (END_REVISION_CAP, END_LINT_CAP, END_SELFTEST_CAP)


# ─────────────────────────────── 窗口层（§4.6）───────────────────────────────


class BitWindow:
    """窗口层：跟 Bit 的窗口服务说话的那根线（§4.6）。

    只做两件事，都是 `POST {worker}:54345/browser/...`：
      - `set_viewport(w, h)` —— 换窗口大小（扰动自测的第 4 遍要用；`bit.sh update` 不能用，
        它是残缺包装）。**不走 `bit.sh`**：直接 POST。
      - `alive()` —— 窗口还活着吗（`/browser/pids/alive`）。窗口只活几分钟（§4.6/P6），
        所以在「等人回话」之后、下一步之前值得再问一次。

    字段名与行为都是**量出来的**（2026-09-16 在真 worker 上量的，不是猜的）：
    窗口尺寸在 `browserFingerPrint.openWidth` / `openHeight`（回读 393×852），
    而 `/browser/update` 是**整条记录更新**（只发一小段会被拒：`请选择代理方式`）。
    两个硬事实写在 `set_viewport` 的 docstring 里 —— 其中第二条（**写了配置也不动活窗口**）
    正是「只回读配置就报成功」会变成谎的地方。
    """

    def __init__(self, worker_ip: str, bit_id: str, *, port: int = BIT_API_PORT,
                 timeout: float = 15.0):
        self.worker_ip = str(worker_ip).strip()
        self.bit_id = str(bit_id).strip()
        self.port = int(port)
        self.timeout = float(timeout)
        if not self.worker_ip or not self.bit_id:
            raise ValueError("窗口层要 worker_ip 和 bit_id 两样（D6：agent 用自己的窗口）")

    # ── HTTP ──────────────────────────────────────────────────────
    def _post(self, path: str, body: dict) -> dict:
        url = "http://%s:%d%s" % (self.worker_ip, self.port, path)
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except (urllib.error.URLError, OSError) as exc:
            raise RuntimeError("窗口服务连不上（%s）：%s" % (path, exc)) from exc
        try:
            return json.loads(raw)
        except ValueError:
            return {"raw": raw}

    def detail(self) -> dict:
        """读回这个窗口的配置（只读）。找不到/读不了就抛 —— 不静默返回空。"""
        out = self._post("/browser/detail", {"id": self.bit_id})
        data = out.get("data") if isinstance(out, dict) else None
        if not isinstance(data, dict):
            raise RuntimeError("窗口服务没给出这个窗口的详情（%s）：%s"
                               % (self.bit_id, str(out)[:200]))
        return data

    # ── 那三根线 ───────────────────────────────────────────────────
    def fresh_open(self) -> str:
        """**开一个干净的窗口**：先确保「启动时清 cookie/缓存」是开着的 → 关 → 开 → 返回新的 ws_url。

        为什么需要它（R-F1，2026-09-17 裁定）：**生产每一单都是新窗口** ——
        `clearCookiesBeforeLaunch` 只在**启动那一刻**生效，所以生产跑的一定是干净会话。
        而自测是在探路**之后**、**同一个会话**里跑的：探路自己点过 cookie 同意，
        「首次访问才有」的那些步骤（cookie 横幅）在复跑时**元素真的不在页面上**了
        （实测：刚开窗 `#onetrust-reject-all-handler` = 1；探路跑完 = 0，再导航回入口还是 0）。
        在脏会话里自测，测的是一个**生产里不会出现**的场景。

        ⚠️ 三个实测出来的硬事实（都写在各自的注释里，别照直觉改）：
        1. `/browser/update` 是**整条记录**更新（只发一小段会被拒：`请选择代理方式`）——
           所以这里跟 `set_viewport` 一样：`detail()` 读回整条，只翻那两个开关，其余原样写回。
        2. `clearCacheFilesBeforeLaunch` / `clearCookiesBeforeLaunch` **只在启动时生效** ——
           所以改完必须**关掉重开**，光写配置等于没做（`set_viewport` 的注释里记着同一个坑）。
        3. `close` 返回成功**不等于窗口真关了**（SKILL §孤儿窗口）：所以后面用 `probe()`
           确认真死了再开；开完再确认活着、并把 `127.0.0.1`/`0.0.0.0` 换成 worker_ip
           （Bit 回的 ws 是它自己眼里的地址，SKILL 里写着这一步）。
        """
        body = dict(self.detail())                  # 整条原样带回（见第 1 条）
        body["clearCacheFilesBeforeLaunch"] = True
        body["clearCookiesBeforeLaunch"] = True
        body["id"] = self.bit_id
        out = self._post("/browser/update", body)
        if not (isinstance(out, dict) and out.get("success") is True):
            raise RuntimeError("把「开窗时清 cookie/缓存」写进配置没被确认：%s" % str(out)[:200])

        self.close()
        ws = self.open()
        return ws

    def open(self) -> str:
        """开窗口，返回 ws_url（`.data.ws`，并把回环地址换成 worker_ip —— SKILL 里写着这步）。"""
        out = self._post("/browser/open", {
            "id": self.bit_id,
            "args": ["--remote-debugging-address=0.0.0.0", "--remote-debugging-port=61129",
                     "--remote-allow-origins=*", "--disable-session-crashed-bubble"],
            "queue": True,
        })
        data = out.get("data") if isinstance(out, dict) else None
        ws = (data or {}).get("ws") if isinstance(data, dict) else None
        if not (isinstance(out, dict) and out.get("success") is True and ws):
            raise RuntimeError("开窗口没成：%s" % str(out)[:200])
        ws = re.sub(r"(127\.0\.0\.1|0\.0\.0\.0)", self.worker_ip, str(ws))
        probe = self.probe()
        if probe["alive"] is not True:
            raise RuntimeError("窗口开了但**探活说它不是活的**（%s）—— 不把这种窗口交出去"
                               "（SKILL：alive 与 close 都可能骗人，开完必须回读）" % probe)
        return ws

    def close(self) -> None:
        """关窗口，并且**确认真死了**（SKILL：close 返回成功 ≠ 窗口真关了）。

        ⚠️ 「本来就已经死了」不算失败（`fresh_open` 的第一件事就是关掉旧窗，
        而旧窗**经常**已经自然死亡 —— 它只活 25~33 分钟）。真死了就没什么可关的，
        直接确认「它确实不在」就完事；否则每次窗口自然死亡之后的 `fresh_open`
        都会以「关窗口没被确认」失败，把一次**已经满足**的前置说成没做到。
        """
        if self.probe()["alive"] is False:
            return
        out = self._post("/browser/close", {"id": self.bit_id})
        if not (isinstance(out, dict) and out.get("success") is True):
            raise RuntimeError("关窗口没被确认：%s" % str(out)[:200])
        probe = self.probe()
        if probe["alive"] is not False:
            raise RuntimeError("关窗口的请求回了成功，但**探活说它还活着/问不出来**（%s）—— "
                               "不确认死掉就往下走，下一个任务会拿到一个正在死掉的窗口" % probe)

    def set_viewport(self, width: int, height: int) -> None:
        """把窗口的尺寸**写进配置**（`width x height`）。

        ⚠️ 两个实测出来的硬事实（2026-09-16，在真 worker 上量的）：

        1. **`/browser/update` 是整条记录更新，不是补丁。** 只发 `{id, browserFingerPrint}`
           会被拒：`{"success":false,"msg":"请选择代理方式"}` —— 也就是说，随手发一小段 JSON
           去改尺寸，**会把代理那几项一起弄丢**（`bit.sh update` 那个残缺包装的同一个坑）。
           所以这里先 `detail()` 读回整条，**只改尺寸、其余一个字节不动地写回去**。
           真应答有 **126 个顶层键 / 112 个指纹键**（`tests/fixtures/bit_window_detail.json`
           就是从真 worker 抓的那份）—— 手工挑几个键回写，挑漏的那个就是被悄悄弄丢的设置
           （比如 `resolution`：它会把重开窗口的宽度卡住）。实测整条回写
           → `success:true`、回读 126/112 个键一个不少。
        2. **写进去的尺寸要等下次启动才生效。** 实测：`success:true`、回读 `openWidth/Height`
           已经是新值，而**活着的那个窗口纹丝不动**（还是 377x757）。所以这个方法**只写配置**，
           「活窗口到底变没变」由调用方去量（见 `Service._viewport_cb`）——
           只回读配置就报「改好了」，等于把一次空转记成跑过了。
        """
        want = (int(width), int(height))
        body = dict(self.detail())                   # **整条原样带回**（见下面第 1 条）
        fp = dict(body.get("browserFingerPrint") or {})
        fp["openWidth"], fp["openHeight"] = want
        body["browserFingerPrint"] = fp
        body["id"] = self.bit_id
        out = self._post("/browser/update", body)
        # 只有**明确说成功**才算成功：实测成功是 `{"success":true,…}`、被拒是
        # `{"success":false,"msg":"请选择代理方式"}` —— 而「没这个键」的应答**什么也没承诺**，
        # 按「不是 false 就是成功」读，等于把没答应当答应。
        if not (isinstance(out, dict) and out.get("success") is True):
            raise RuntimeError("换窗口大小的请求没被确认成功：%s" % str(out)[:200])

    def probe(self) -> dict:
        """窗口还活着吗 + **它是哪个进程**：`{"alive": bool|None, "pid": int|None}`。

        为什么要 PID（计划四 Task 1 / G3）：`alive()` 一直把它扔掉，于是「窗口换过没有」
        谁也答不了 —— 而 E2 实测**一次跑里 PID 至少换过 3 次**。没有 PID，
        「一个 run 重开了几次」「这一次探路跨了几个窗口」这两件事都只能靠猜。

        ⚠️ 三态照旧：`True` 活 / `False` 死 / `None` **问不出来**（别拿它当死）。
        `pid` 只在「活着」那条路上有（`data` 是空 dict 时什么都没有）——
        问不出来就给 `None`，**不许沿用上一次那个 PID**。
        """
        try:
            out = self._post("/browser/pids/alive", {"ids": [self.bit_id]})
        except RuntimeError:
            return {"alive": None, "pid": None}
        if not isinstance(out, dict):
            return {"alive": None, "pid": None}
        data = out.get("data")
        if isinstance(data, dict):
            pid = data.get(self.bit_id)
            if pid is None and self.bit_id not in data:
                return {"alive": False, "pid": None}
            return {"alive": True, "pid": pid if isinstance(pid, int) else None}
        if isinstance(data, list):                     # 老形状（P6 之前遇到过）
            return {"alive": self.bit_id in [str(x) for x in data], "pid": None}
        if isinstance(data, bool):
            return {"alive": data, "pid": None}
        if isinstance(data, str):
            low = data.strip().lower()
            if low in ("true", "1", "yes"):
                return {"alive": True, "pid": None}
            if low in ("false", "0", "no", ""):
                return {"alive": False, "pid": None}
            # 比如「操作成功」——那只说明这次调用成了，没说窗口活着
            return {"alive": None, "pid": None}
        return {"alive": None, "pid": None}

    def alive(self) -> Optional[bool]:
        """窗口还活着吗。**三态**：True 活 / False 死 / None 问不出来（别拿它当死）。

        ⚠️ `data` 的真实形状是**测出来的**（2026-09-16 在真 worker 上量的）：
        活着的窗口 → `{"success":true,"data":{"<bit_id>": 4256}}`（一个 **dict**，值是该窗口的 PID）；
        关掉之后 → `{"success":true,"data":{}}`（空 dict）。
        **一开始这里只认 list/bool/str，于是真跑时恒返回 `None`（「不知道」）——
        这根线看起来接好了，其实永远不响。** 这种「接上了但不响」比没接更坏：
        它让 P6 的那道前置看起来存在。测试钉住这两种形状。

        形状的判定全在 `probe()` 里（同一个应答、同一个坑）；这里只把它折成 bool。
        **返回类型一个字都不许变** —— 它是 `WINDOW_LAYER` 的两个名字之一，被测试钉着。
        """
        return self.probe()["alive"]


def live_viewport(ws_url: str, *, cdp_bin: Optional[str] = None) -> Optional[tuple]:
    """量**活着的**那个窗口现在多大 —— `cdp eval` 读 `window.innerWidth/innerHeight`（只读）。

    为什么要真去量（而不是回读 Bit 的配置）：**配置改了 ≠ 窗口变了**。实测过：
    `POST /browser/update` 回 `success:true`、`/browser/detail` 回读也是新尺寸，
    而活着的窗口还是 377x757（Bit 的窗口尺寸是**启动时**生效的）。
    只回读配置就报「换好了」，会把一次**空转**的扰动记成跑过了 —— R-5 里最贵的那种谎。

    量不出来（没有 cdp 二进制 / 连不上 / 输出看不懂）→ `None`（**不知道，不许当死**）。

    ⚠️ **`cdp_bin` 由调用方给**（修复轮 2）：这里以前自己读环境 —— 于是
    「服务构造时定死的那个二进制」在这条路上**根本不作数**，而这条路照样起子进程、
    照样在**它自己被调用的时候**才读环境（探针是在自测的第 4 遍里被调的，
    那时候设环境的东西早没了）。不传就按老规矩解析（`shots.cdp_bin_for`，**一份读法**）。
    """
    import re as _re
    import subprocess
    cdp_bin = shots.cdp_bin_for(cdp_bin)
    m = _re.match(r"^wss?://(?P<host>[^:/]+):(?P<port>\d+)/", str(ws_url or ""))
    if not m or not os.path.exists(cdp_bin):
        return None
    try:
        done = subprocess.run(
            [cdp_bin, "--host", m.group("host"), "--port", m.group("port"), "eval",
             "window.innerWidth + 'x' + window.innerHeight"],
            capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    found = _re.search(r"(\d+)\s*x\s*(\d+)", done.stdout + done.stderr)
    return (int(found.group(1)), int(found.group(2))) if found else None


def bit_window_from_env(env: Optional[dict] = None) -> Optional[BitWindow]:
    """按环境变量建窗口层；没配就返回 `None`（= 「这个部署给不了那根线」）。

    ⚠️ 没配 → `None`，**不是**一个“什么都答应、其实什么也没做”的空壳：R-31 要的是
    「要么真给得了，要么图停下点名」，一个假回调会把第 4 遍扰动变成静默的空转。
    """
    env = os.environ if env is None else env
    worker = (env.get("BIT_WORKER_IP") or "").strip()
    bit_id = (env.get("BIT_ID") or "").strip()
    if not worker or not bit_id:
        return None
    try:
        port = int(env.get("BIT_API_PORT") or BIT_API_PORT)
    except ValueError:
        port = BIT_API_PORT
    return BitWindow(worker, bit_id, port=port)


# ─────────────────────────────── checkpointer（R-19）───────────────────────────────


class Checkpointer:
    """按配置**懒**建 saver：`import agent.service` 不该去连数据库（测试会 import 它）。

    生产的形态是 Postgres（R-19：中断之后要恢复，状态就不能只活在进程里）；
    没配 `DATABASE_URL` 时退回内存 saver —— 但要**说出来**（`/health` 里那句人话），
    不然「能恢复」会被读成「重启也还在」。
    """

    def __init__(self, *, saver=None, url: Optional[str] = None):
        self._saver = saver
        self._url = url or os.environ.get("DATABASE_URL") or None
        self._lock = threading.RLock()
        self._read_lock = threading.RLock()
        self._reader = None
        self._conn = None
        #: 「这是哪种 saver」在**建的那一刻**就定下来，之后不变。
        #: ⚠️ 不能靠「还没建」（`_saver is None`）去判：第一次真要用之后 `_saver` 就有值了，
        #: 那样 `/health` 会在一个**状态明明在 Postgres 里**的部署上说「重启就没了」
        #: —— 而那句话恰好是这套系统最不该说错的一句（R-19）。
        self._kind = "memory" if saver is not None else ("postgres" if self._url else "memory")
        #: 读是不是走**另一条连接**（只有那种情况才该绕开写锁）。
        #: ⚠️ 与 `kind` 同一个坑：必须**在构造时**定下来，不能等 `get()` 建完再判。
        self._separate_reader = saver is None and bool(self._url)

    @property
    def kind(self) -> str:
        return self._kind

    def say(self) -> str:
        if self.kind == "postgres":
            return ("状态存在 Postgres 里：中断之后接着跑、服务重启也还在"
                    "（这就是「人能停下来再回来」靠的东西）。")
        return ("状态只存在这个进程的内存里：**服务一重启就没了**，中断之后也就恢复不了。"
                "要能恢复就给 `DATABASE_URL`，用 Postgres 存（R-19）。")

    @property
    def lock(self) -> threading.RLock:
        """saver 的串行闸：一个 Postgres 连接**不能**被两个线程同时用。"""
        return self._lock

    def get(self):
        """写侧 saver（图跑起来用的那条连接）。"""
        with self._lock:
            if self._saver is not None:
                return self._saver
            if not self._url:
                from langgraph.checkpoint.memory import InMemorySaver
                self._saver = graph.allowlisted(InMemorySaver())
                self._kind = "memory"
                return self._saver
            self._saver = self._connect(self._url)
            self._kind = "postgres"
            return self._saver

    def reader(self):
        """**读**侧 saver：另开一条连接，专给 `GET /job/{id}` 这类读用。

        为什么要有第二条：一次 `invoke` 可能跑几分钟（真浏览器 + 模型），
        而它整段都持着写锁（一个 Postgres 连接不能被两个线程同时用）。
        读要是也走那条连接，Console 的「进展」就会排在一次探路后面 —— 人以为卡死了。
        """
        with self._read_lock:
            if self._reader is not None:
                return self._reader
            if self._saver is not None:
                self._reader = self._saver          # 调用方给的 saver：读也问它
                return self._reader
            if not self._url:
                self._reader = self.get()           # 内存 saver：同一条（没有连接可抢）
                return self._reader
            conn = self._connect_conn(self._url)
            from langgraph.checkpoint.postgres import PostgresSaver
            reader = graph.allowlisted(PostgresSaver(conn))
            # 表没建过就建（幂等）。不建的话，「**新库第一次请求就是读**」会在
            # psycopg 那边报「relation \"checkpoints\" does not exist」→ **500**，
            # 而那时候该说的其实是「没这个任务」（404）—— 把「没有」说成「坏了」。
            reader.setup()
            self._reader = reader
            return self._reader

    @property
    def separate_reader(self) -> bool:
        """读连接**真的是另一条连接**吗。

        只有在这种情况下才该绕开写锁：`InMemorySaver` **没有内部锁**，
        读绕过写锁撞上一次 `invoke` 就可能「dictionary changed size during iteration」→ 500。
        同一条 saver 上，读**该**排队（排队是对的，不是 bug）。
        """
        return self._separate_reader

    @property
    def reader_lock(self) -> threading.RLock:
        """读侧那把锁（与写侧分开 —— 读不该排在一次 invoke 后面）。"""
        return self._read_lock

    def _connect_conn(self, url: str):
        import psycopg
        return psycopg.connect(url, autocommit=True)

    def _connect(self, url: str):
        """建 Postgres saver 并 `setup()`（建表）。

        `allowlisted(...)` 是**必须**的：`Journey` / `Report` / `Run` 是 dataclass，
        msgpack 的 serde 要**点名允许**它们（Task 7 实测过那条警告）。
        ⚠️ **不要**开 `LANGGRAPH_STRICT_MSGPACK`（那会把警告变成硬错误）。
        """
        import psycopg
        from langgraph.checkpoint.postgres import PostgresSaver
        self._conn = psycopg.connect(url, autocommit=True)
        saver = PostgresSaver(self._conn)
        saver.setup()
        return graph.allowlisted(saver)


# ─────────────────────────────── 收上来的东西 ───────────────────────────────


class RunRequest(BaseModel):
    """`POST /run` 的载荷 —— 等于图的开场白（`state.SiteState` 的前几项）。

    图 **不自己发明** 任何「没验到也算过」的默认值（§2.5），所以它要的那几样只有调用方给得了：
    `success_text`（什么算成功，只有人知道）、`ws_url` / `form_file`（§4.6 前提层的产物）、
    以及窗口层的旋钮（`set_viewport` / `allow_skips` / `entry_url`，R-31）。
    """

    url: str = Field("", description="站点 URL")
    goal: str = Field("", description="人给的意图：要摸清什么 / 什么算完成")
    mode: str = Field("build", description="build（没跑过的站）或 fix（挂着的老站）")
    success_text: str = Field("", description="成功判据：走通之后页面上会出现哪段文字（**只有人知道**）")
    evidence: str = Field("", description="fix 模式：失败证据的引用（FMR formLog / formStep）")
    site: str = Field("", description="站点短名（不给就从 URL 推）")
    ws_url: Optional[str] = Field(None, description="§4.6 前提层开出来的窗口（bit.sh open 吐的那串）")
    form_file: Optional[str] = Field(None, description="表单数据文件（自测用）")
    env: Optional[dict] = Field(None, description='{"proxy_country","dpr","ua","viewport"}；没人给就不带')
    platform: Optional[dict] = Field(None, description='{"guess","confidence"}；平台分类不在这张图里')
    out_dir: Optional[str] = Field(None, description="产物落在哪个目录（默认 forms/sites/）")
    allow_skips: Optional[list[str]] = Field(
        None, description="**点名**允许不跑的那几遍扰动（认的是那五个名字）。"
                          "不给 = 用默认（只允许跳 country）；给空列表没有意义，会被拒")
    entry_url: Optional[str] = Field(None, description="第 2 遍刷新回哪个 URL（R-6）")
    expects: Optional[list] = Field(
        None, description="**运营逐步写的期望**（契约 §四）：第 i 项就是第 i 步该以什么结束。"
                          "每一项要么是从菜单里选的一项 —— `{\"url_contains\": \"/wizard\"}` / "
                          "`{\"text_appears\": \"Thank you\"}` / "
                          "`{\"button_clickable\": \"下一步\"}` / `{\"screen_changed\": true}` —— "
                          "要么是 `\"未声明\"`（这一步他说不好，**不许逼他编一个**）。"
                          "比步数短的那几项 = 运营**没写**（与「未声明」**不是**一回事）。"
                          "⚠️ 它只给**服务**（判那一步的是服务），不往下发给脚本："
                          "期望必须来自业务，不能来自执行者（契约 §四）")
    set_viewport: bool = Field(
        False, description="把**窗口层那根线**接上（第 4 遍扰动要换窗口大小，`POST /browser/update`）。"
                           "尺寸由自测那一步定（`selftest.DEFAULT_VIEWPORT`）；这个部署没接窗口层时"
                           "给 true 会被拒 —— 不替你默认放过那一遍（R-5）")


class ReplyRequest(BaseModel):
    """人在闸口的回话（`state.human_reply` 认的那三种写法 + 这几种同义写法）。"""

    action: str = Field("continue", description="continue / stop / revise（不认识的当纠正处理，不丢）")
    note: str = Field("", description="纠正的话：直接说该点哪（会一路带进 draft）")


class ReopenRequest(BaseModel):
    """窗口死了之后重开一个（P6）：把**新窗口**放回状态，从断点接着跑。"""

    ws_url: str = Field(..., description="新开出来的窗口（bit.sh open 吐的那串）")
    entry_url: Optional[str] = Field(None, description="顺便更新第 2 遍刷新回哪个 URL")
    set_viewport: bool = Field(False, description="顺便把窗口层那根线接上（第 4 遍扰动要用）")


# ───────── 执行事实：运营写的期望、服务算的判（契约 §二 / §四 / §六）─────────
#
# 这一节是**换裁判**那件事在代码里的样子。契约 §一：病根不是「字段不够」，是
# **判断和执行是同一方**（`datewhirl.py:175` 把「什么都没看见」判成「做完了」）。
# 所以七格里有两格**永远不归脚本**：
#
#   - `expect`（第 6 格）——**运营**写，从 §四那张菜单里选（本文件只负责**收**：形状不对
#     当场拒，见 `expect_problem`）。⚠️ 它**从载荷进来**，脚本一个字节都碰不到它
#     （`_payload` 不往下发；`events.SCRIPT_MAY_NOT_FILL` 再从词表那一侧挡一道）；
#   - `verdict`（第 7 格）——**收到上面那些的那一方**算，也就是**这里**（`judge_step`）。
#
# ⚠️ 这两件事**必须都在服务侧**。放在脚本里（哪怕只是「顺手算一下」），就是
# `datewhirl` 那个病换了个地方长出来 —— 契约 §四 的原话：「模型提议的期望，仍然是
# **执行的那一方在当裁判**，只是换了个更聪明的裁判」。

#: 契约 §四 那张菜单的**机器那一列**（运营在页面上看到的是左边那句人话）。
#: 菜单是**封闭**的：不在这四个里的形状一律拒收（`expect_problem` 会说清该选哪个）。
EXPECT_MENU = {
    "url_contains": "「网址变成…」→ 填一段网址片段",
    "text_appears": "「页面上出现…」→ 填一段文字",
    "button_clickable": "「写着…的按钮变成可点」→ 填按钮上的字",
    "screen_changed": "「换了一屏」→ 不用填（**弱判据**，判的时候会标出来）",
}


def expect_problem(item) -> str:
    """一条期望**不成形状**就说清该怎么改；空串 = 这一条没问题。

    为什么要在**载荷进来的那一刻**拒（免费的那一道闸，与 `_intake_problems` 同一条规矩）：
    `expect` 是整个契约的**诚实阀门**（§四），而一个「随便什么 JSON 都收」的阀门
    等于没有阀门 —— 复审 2026-09-18 实测过：`{"随便什么": 1}` / `"Thank you"` / `["/wizard"]`
    三种**显然不对**的值与四种对的值**一模一样地收下**，于是「机器怎么判」那一列
    在代码里一行都没有。这一版把它补上：**形状是封闭的**，错的那种当场响。
    """
    if item == events.UNDECLARED:
        return ""                                   # 「这一步我说不好」是**一等值**，能选
    if not isinstance(item, dict):
        return ("期望只有两种写法：`%s`（运营说这一步他说不好），或者从菜单里选一项 "
                "%s —— 拿到的是 %r。**不许**填选择器、也不许留空。"
                % (events.UNDECLARED, "、".join("`%s`" % k for k in EXPECT_MENU), item))
    keys = [k for k in item if k not in ("why",)]
    if len(keys) != 1 or keys[0] not in EXPECT_MENU:
        return ("期望要从菜单里选**一项**：%s —— 拿到的是 %r。"
                "（选一项就够：一步该以什么结束只有一件事说得清，两件凑一起是两条期望。）"
                % ("、".join("`%s`（%s）" % (k, v) for k, v in EXPECT_MENU.items()), item))
    key, value = keys[0], item[keys[0]]
    if key == "screen_changed":
        if not isinstance(value, bool):
            return ("`screen_changed` 要的是 `true`（「这一步该换一屏」）—— 拿到的是 %r。"
                    "它是**弱判据**（正文指纹变了就算），算出来的 `met` 会被标出来。" % (value,))
        return ""
    if not isinstance(value, str) or not value.strip():
        return ("`%s` 要的是一段**人写的文字**（%s），拿到的是 %r —— "
                "**不许填选择器**（那是脚本的事，运营的文字要能直接与页面对上）。"
                % (key, EXPECT_MENU[key], value))
    return ""


def _expect_at(expects: list, step_no) -> tuple:
    """第 `step_no` 步的期望 → `(键在不在, 值)`。

    ⚠️ **三种「不知道」在纸上必须分得开**（契约 §四 + §二②），这一版把它们分成了三格：
      - 运营**没写**到这一步（列表比步数短）→ **键不在**（「这一格没人碰过」）；
      - 运营写了 `未声明` → 值是 `events.UNDECLARED`（「他说不好」）；
      - 运营真声明了 → 值是菜单里那一项。
    合成一个（比如把「没写」也算成 `未声明`）就是在替运营说「他说不好」—— 那句话他没说。
    """
    if not isinstance(step_no, int) or step_no < 1:
        return False, None                          # 步号都不知道，谈不上「第几步的期望」
    if step_no > len(expects):
        return False, None
    return True, expects[step_no - 1]


def _receipt_says_sent(receipt) -> Optional[bool]:
    """**动作发出去没有** —— 从 CDP 回执的原文里读（契约 §一那一层：Action Truth）。

    判据只有一条，而且是**协议自己**给的：`{"isError": true, ...}` = 那次工具调用**没成**
    （`tools.call_tool` 就是拿这个字段分支的，脚本在 `browser_agent` 里**逐字转抄**了它）；
    工具回来了（别的任何形状）= 命令**下发了**。

    ⚠️ 为什么读的是回执而不是脚本那句 `result.ok`：**回执是别人的原话**，`result` 是脚本
    自己写的摘要 —— 契约 §六 那条验收要的正是「脚本说成了、回执说没成，系统看得出不一样」。
    读 `result.ok` 就等于**又把裁判还给了脚本**。

    `None` = **说不准**（回执根本没在手上 / 形状不认识）：那是一等值，不许折算成「发出去过」
    也不许折算成「没发出去」。
    """
    if receipt is None:
        return None
    if isinstance(receipt, dict):
        if "isError" in receipt:
            return not bool(receipt["isError"])
        return True                     # 工具回来了（没有 isError 那一层）⇒ 命令下发了
    return None                         # 认不出的形状：不猜


def _meets(expect, present: bool, sig_before, sig_after, changed, step_no=None) -> tuple:
    """运营那条期望**成立没有** → `(值, 一句为什么)`。契约 §四那张菜单的**机器怎么判**。

    三值：`True` / `False` / **`None`（「这一条没被验到」）**。第三种不是含糊其辞 ——
    「量不到」与「不成立」是两件事，而把它们合成一件正是这份契约要治的病。
    """
    if not present:
        if not isinstance(step_no, int):
            # ⚠️ 不许把「不知道问的是第几步」说成「运营没写」—— 那是两句不同的话，
            # 而前者的下一步是**去把步号补上**，后者才是去问运营。
            return None, ("这一步**没有步号**在手上（旧账 / 别的来源），对不上运营那张表 —— "
                          "有没有写期望**不知道**，不是「没写」。")
        return None, ("这一步运营**没写**期望（那不是「他说不好」—— 那是另一件事："
                      "他没说。）")
    if expect == events.UNDECLARED:
        return None, ("运营说这一步**他说不好**（`%s`）—— Business Truth 就是「不知道」，"
                      "**不拿动作的回执冒充**。" % events.UNDECLARED)
    kind = list(expect)[0] if isinstance(expect, dict) and expect else ""
    if kind == "url_contains":
        if not isinstance(sig_after, dict) or not sig_after.get("url"):
            return None, "这一条要拿**动作之后那一页的地址**比，而那份签没量到。"
        hit = str(expect["url_contains"]) in str(sig_after["url"])
        return hit, ("运营说「网址应该变成含 `%s`」，实际是 `%s`。"
                     % (expect["url_contains"], sig_after["url"]))
    if kind == "text_appears":
        return None, ("这一条要拿**正文**比，而原始签里只有正文的**指纹**（存正文的话"
                      "账本就装不下了）—— 今天判不了，**不猜**。")
    if kind == "button_clickable":
        return None, ("这一条要按文字找到那个按钮、还要问它可不可点，而原始签里只有"
                      "**可见元素计数** —— 今天判不了，**不猜**。")
    if kind == "screen_changed":
        if changed is None:
            return None, "这一条要拿两份签比，而其中一份没量到。"
        return bool(changed), ("**弱判据**（契约 §四自己标的）：判的是正文指纹变没变，"
                               "变了不代表变对了地方。")
    return None, ("这条期望的形状我不认识（%r）—— 载荷那道闸本该在提交那一刻挡住它。"
                  % (expect,))


def judge_step(*, step_no, receipt, sig_before, sig_after, expect, expect_present) -> tuple:
    """**契约 §二第 7 格**：从两份原始签 + 回执算出「变没变、成没成」→ `(verdict, say)`。

    这是**裁判**，所以它只吃**别人写下来的东西**（CDP 的回执、脚本量的签、运营写的期望），
    一个字节都不来自「它自己觉得」：

    - **① Action Truth**（动作发出去没有）← 回执原文；
    - **② State Truth**（页面变没变）← 两份原始签；
    - **③ Business Truth**（运营那条期望成立没有）← 期望 + 上面两样。

    三层**分开报**，`verdict` 里三个值各自独立（`sent` / `changed` / `met`）——
    合并成一个 `success` 正是 `datewhirl` 那个病的形状。

    ⚠️ **这两条要分开报，是因为同一句「页面没变」在两处的意思完全相反**：
    动作**发出去了**而页面没变 = 「这一步没生效」；动作**没发出去**而页面没变 = 「这一步
    压根没发生」。前者要看选择器，后者要看**为什么工具会报错**。把两句合成一句，
    读的人就永远分不出来 —— 契约 §六 那条验收问的正是这个。
    """
    sent = _receipt_says_sent(receipt)
    if sig_before is None or sig_after is None:
        changed = None
    else:
        changed = dict(sig_before) != dict(sig_after)
    met, met_why = _meets(expect, expect_present, sig_before, sig_after, changed,
                          step_no=step_no)

    # ── 人话：三层各说一句，最后给一句结论 ─────────────────────────────
    if sent is True:
        action_say = "回执说动作**已经下发**"
    elif sent is False:
        action_say = "回执说**动作根本没发出去**"
    else:
        action_say = "回执没在手上（看不见），动作发没发**说不准**"
    if changed is True:
        state_say = "页面**变了**"
    elif changed is False:
        state_say = "页面**没变**"
    else:
        state_say = "页面变没变**量不到**"

    if sent is False:
        verdict_say = ("%s —— 这一步**没有发生**：%s 不是它的结果，**别当成功**。"
                       % (action_say, state_say))
        if changed is True:
            verdict_say = ("%s，可%s —— 两句话对不上：那一下**不是这一步干的**，"
                           "查别处（谁改的页面）。" % (action_say, state_say))
    elif sent is True and changed is False:
        verdict_say = "%s，而%s —— 动作真送出去了、页面纹丝不动：这一步**没生效**。" % (
            action_say, state_say)
    elif sent is True and changed is True:
        verdict_say = "%s，而且%s。" % (action_say, state_say)
    else:
        verdict_say = "%s，%s —— 这一步成没成**判不了**（缺的那一层就是缺的那一层）。" % (
            action_say, state_say)

    if met is True:
        verdict_say += "（运营那条期望：对上了。）"
    elif met is False:
        verdict_say += "（运营那条期望：**没对上**。）"
    if met_why:
        verdict_say += "（%s）" % met_why

    where = ("第 %s 步" % step_no) if isinstance(step_no, int) else "这一步"
    return ({"sent": sent, "changed": changed, "met": met},
            "%s：%s" % (where, verdict_say))


# ─────────────────────────────── job ───────────────────────────────


@dataclasses.dataclass
class Job:
    job_id: str
    brief: dict
    status: str = QUEUED
    say: str = ""
    error: Optional[str] = None
    graph: Any = None
    lock: threading.Lock = dataclasses.field(default_factory=threading.Lock)
    created_at: str = ""
    #: **到过几次闸口**（= 第几轮）。`pause-<n>.png` 里的 n 就是它 ——
    #: Task 6 的配对规则「第 n 道闸上拍的那张叫 `pause-<n>`」靠的正是这个数。
    #: ⚠️ 拍不成的那一轮**也占号**（否则轮号与闸号会错开），差别记在 `shot_notes` 里。
    pauses: int = 0
    #: 每一轮一条：`{"n", "name", "why"}`。拍成了 `name="pause-<n>.png"` 且 `why=""`；
    #: 没拍成 `name=None` 且 `why` 是一句**人话**（拍不成绝不许静默）。
    shot_notes: list = dataclasses.field(default_factory=list)
    #: 时间线（Task 4 / 契约七格，`agent/events.py`）。**唯一的写入口是 `Service.narrate`** ——
    #: 谁都别直接 `timeline.add`（否则人话纪律与「谁填哪一格」会散）。
    #: ⚠️ 它是 **process-local** 的：服务一重启就没了（`/live` 的 `note` 明说这件事）。
    timeline: events.Timeline = dataclasses.field(default_factory=events.Timeline)
    #: 这个 job 是不是**服务重启之后从 checkpoint 捡回来的**（`/live` 的 `note` 靠它明说）。
    recovered: bool = False
    #: 「窗口已经死了」这件事**说过了**没有：每次 `_advance` 返回都去问窗口层，
    #: 但只有「不是没了 → 没了」那一翻要记一条（同一件事记十遍不是信息）。
    window_dead: bool = False
    #: **步拍**报过的 `why`（人话原文）。**同一句只报一次**（Task 5，目录表第 6 行）：
    #: 拍照坏掉通常是**每一步**都坏，每一步刷一条会把时间线灌满 —— 而多条不是信息。
    #: 认的是**那句话**、不是第几步：同一趟探路里两步的图没了往往是同一个原因。
    shots_reported: set = dataclasses.field(default_factory=set)
    #: **播报坏了**这件事报过的原因（Task 5 修复轮 1）。同上：报告会一直躺在 state 里，
    #: 而每次 `_advance` 返回都会去读它一遍 —— 不去重就是同一句话每推一步记一条。
    narration_reported: set = dataclasses.field(default_factory=set)

    def snapshot_for_view(self) -> tuple:
        with self.lock:
            return self.status, self.say, self.error


# ─────────────────────────────── 服务本体 ───────────────────────────────


def _two_readers(*writers) -> Optional[Callable]:
    """把几个 `on_step` 读者串成一个；全是 `None` 就返回 `None`（=不接这条线）。

    为什么要串（2026-09-18）：探路的**一步**从这一天起有**两个**读者 —— 账本
    （落盘，给重放和事后查）与时间线（契约七格，给人读）。两个都是旁路：
    `browser_agent.explore` 的 `emit()` 统一兜异常，坏掉的那个只留一句 note，
    **不许带塌主路**。所以顺序只影响「谁先坏」，不影响主路 —— 账本放前面，
    因为它是**能重放**的那一份。
    """
    live = [w for w in writers if w is not None]
    if not live:
        return None

    def on_step(step: dict) -> None:
        for write in live:
            write(step)

    return on_step


def _tighter(recorded, on_disk) -> dict:
    """两份「这个 job 花了多少」取**更紧的**（逐键 max）。

    为什么不是相加、也不是取其中一份：两份都是**下界**（状态里那份可能因为节点抛异常而
    缺了后面几趟；盘上那份的 `rounds` 对「没量到」的趟记 0）—— 取 max 是**保守**的那一侧
    （预算只会更小、不会凭空变大），与 `graph._round_spends` 那条「不知道的一律按花算」同族。

    ⚠️ **这一格 `rounds` 是「探路的模型轮数」**（`journey.rounds` / `attempts.jsonl`）——
    **不是**「闸拍轮次」（图到过几次闸口）。后者运营看见的那份投影在 `/live.rounds` 与
    `/runs[].rounds`，轮数的算在 `agent/rounds.py` 的 `count()`（⚠️ **不是** `Job.pauses`
    —— 那是**闸拍张数**：跑完/跑挂那一次也拍，到头了那两档 = 轮数+1）。
    **两个事实同名，别互相顶替**：
    这一个「没量到」时会退成 0（`journey.rounds_measured` 才说得清），拿它当轮次数会多算。
    """
    out = {}
    for key in ("steps", "rounds", "attempts"):
        out[key] = max(int((recorded or {}).get(key) or 0), int((on_disk or {}).get(key) or 0))
    return out


class Service:
    """登记表 + 一个工作线程 + 一组投影。

    工作线程是**单飞**的（D6：agent 只有一个窗口，两个 run 并发会在同一个窗口上互相踩）。
    """

    def __init__(self, *, graph_factory: Optional[Callable] = None,
                 window: Any = None, checkpointer=None, checkpointer_url: Optional[str] = None,
                 out_dir: Optional[str] = None, viewport_probe: Optional[Callable] = None,
                 explore_dir: Optional[str] = None,
                 window_probe_seconds: Optional[float] = None,
                 shots_dir: Optional[str] = None, capture: Optional[Callable] = None,
                 shot_timeout: Optional[float] = None, capture_bin: Optional[str] = None,
                 selftest_dir: Optional[str] = None, mcp_bin: Optional[str] = None):
        self._jobs: dict[str, Job] = {}
        self._jobs_lock = threading.Lock()
        self._queue: "queue.Queue[tuple]" = queue.Queue()
        self._check = (checkpointer if isinstance(checkpointer, Checkpointer)
                       else Checkpointer(saver=checkpointer, url=checkpointer_url))
        self._window = window
        self._graph_factory = graph_factory
        self._out_dir = out_dir or str(graph.DEFAULT_OUT_DIR)
        # ── 构造时**定死**的那几样（部署配置；之后再也不看环境）──────────────────
        #: 图落哪、账落哪、用哪个 cdp / 哪个 cdp-mcp。⚠️ 为什么不「用的时候再读环境」：
        #: 抓拍 / 窗口探针 / 自测 / 探路会话都跑在**工作线程或子进程**里，
        #: 可能比设那段环境的东西活得久 —— 修复轮 1/2 各实测过一次漏（子进程里环境已经是
        #: 空的，回退链于是落回**仓库里那个真二进制**，而去连的是真地址）。
        #: ⚠️ 次数是**竞态量**：全量套件里**每趟 2–4 次**（取决于哪几条「发了 job 不等它」的
        #: 用例的 worker 活过了它的 fixture）——**别把它当仪器读数**，它是「有这个病」的证据。
        #: **定死了就没有「以后再看一眼环境」这回事。**
        #: `shots_dir` ⇒ `SITEFORGE_SHOTS_DIR` ⇒ 仓库里的 `runtime/shots`；
        #: `capture_bin` ⇒ `SITEFORGE_CDP_BIN` ⇒ `CDP_PATH` ⇒ 仓库里的 `tools/cdp/cdp`。
        self._shots_dir = shots.root_for(shots_dir)
        self._cdp_bin, self._cdp_bin_source = shots.cdp_bin_with_source(capture_bin)
        #: 自测每一遍的 trace 落哪（`<root>/<site>-<时刻>/`）—— 同上，构造时定死。
        #: ⚠️ 以前这里没有：`run_dir` 由 `selftest.run` 在**调用那一刻**解析成
        #: **仓库里**的 `runtime/selftest/…`（修复轮 3 的 F2，实测真被建出来）。
        self._selftest_root = str(selftest_dir
                                  or os.environ.get("SITEFORGE_SELFTEST_DIR")
                                  or selftest.DEFAULT_ROOT)
        #: 探路那条会话（`McpSession.open` → `cdp-mcp`）用哪个可执行文件 ——
        #: **唯一真连浏览器**的通道也在同一条不变量里（`tools.MCP_BIN` 是导入期读的，
        #: 那次读数**不是**这个服务定的；这里把它按服务的意愿定死，测试/运维才换得动）。
        self._mcp_bin = str(mcp_bin or os.environ.get("CDP_MCP_BIN") or tools.MCP_BIN)
        #: 「活着的窗口现在多大」怎么量（默认走 cdp 读页面；测试注入桩）。
        #: ⚠️ 默认那根线是**同类通道里的第二条**（它自己也读环境、自己也起子进程）——
        #: 所以把**定死的那个二进制**绑给它（`live_viewport(cdp_bin=…)`），它不再自己看环境。
        self._viewport_probe = (viewport_probe
                                or functools.partial(live_viewport, cdp_bin=self._cdp_bin))
        self._probe = None
        self._worker: Optional[threading.Thread] = None
        self._worker_lock = threading.Lock()
        # ── 运行产物（计划四 Task 1）：`runtime/explore/<job_id>/` ──────
        #: 账落在哪（`runtime/` 不进 git；与 `runtime/selftest/` 同层不同目录）
        self._explore_root = str(explore_dir or os.environ.get("SITEFORGE_EXPLORE_DIR")
                                 or measure.DEFAULT_ROOT)
        #: 窗口时间线多久探一次。**这个间隔就是 M1 的精度**（要连着它一起读寿命）。
        self._probe_seconds = float(window_probe_seconds
                                    or os.environ.get("SITEFORGE_WINDOW_PROBE_SECONDS")
                                    or 15.0)
        self._window_probe_thread: Optional[threading.Thread] = None
        self._active_job: Optional[str] = None
        # ── 闸拍（设计注 §5.5）：`runtime/shots/<job_id>/pause-<n>.png` ──────────
        #: 真去拍一张的那个函数：`(ws_url, dest, *, timeout) -> (文件名|None, 人话)`。
        #: 不给就用 `shots.capture_via_cli`（闸口上没有 MCP 会话，手上只有 `ws_url`）；
        #: ⚠️ **调用时才取**（不是构造时），测试要换掉它才换得动。
        self._capture = capture
        #: 硬的：超过这么多秒就当这张没拍成（旁路线程，见 `_shoot`）。
        self._shot_timeout = float(SHOT_TIMEOUT_SECONDS if shot_timeout is None
                                   else shot_timeout)

    # ── 外面那三层：图、窗口、检查点 ────────────────────────────────
    def _build_graph(self, brief: dict, job_id: str = ""):
        """给一个 job 拼一张图。**旋钮就在这儿给**（R-31：服务是窗口层的供给方）。

        `graph_factory(brief, deps)` 收到的是**本服务拼好的 `Deps`** —— 于是调用方
        （测试 / 计划三的 Console）可以只换掉贵的那些（探路、自测），而**继承**窗口层那根线。
        生产走 `graph_factory=None` 那条：全是真接线。
        """
        deps = graph.Deps(explore=self._explore_for(brief, job_id),
                          selftest=self._selftest_cb(job_id),
                          set_viewport=(self._viewport_cb(brief.get("ws_url"))
                                        if brief.get("set_viewport") else None),
                          fresh_session=self._fresh_session_cb(),
                          window_alive=self._window_alive_cb())
        if self._graph_factory is not None:
            return self._graph_factory(brief, deps)
        return graph.build(checkpointer=self._check.get(), deps=deps)

    def _selftest_cb(self, job_id: str = "") -> Callable:
        """`Deps.selftest`：把**构造时定死的**那两样交给自测（二进制、trace 落哪）。

        为什么（修复轮 2 的第三条通道 + 修复轮 3 的 F2）：

        - **二进制**：`Deps.selftest` 的默认是 `selftest.run`（`graph.Deps`），而它自己会在
          `selftest.py` 里**再读一次环境**（`SITEFORGE_CDP_BIN` → `_default_cdp_bin()`，
          第二候选直接是仓库里那个二进制），读的时刻是**调用的时刻**。
        - **trace 落哪**：`run_dir` 不给就落到 `_default_run_dir(site)` = **仓库里**的
          `runtime/selftest/<site>-<时刻>/`（`_selftest_kwargs` **不给** `run_dir`）——
          名字里带时刻 ⇒ 只能在调用那一刻算 ⇒ 与 `_shots_dir`/`_explore_root` 同一个病：
          **服务路径上「调用时再解析」= 测试与运维都管不住它**（实测：不给 `run_dir`
          调一次，仓库里立刻多一个目录）。所以这里**连目录一起给**（根是构造时定死的）。

        ⚠️ 与 `explore`/`shots`/`viewport_probe` 同一条不变量：
        **构造时定死，之后不再看环境**（同一个进程里出现两个不同的 cdp = 漂）。
        ⚠️ 调用方**显式**给了 `run_dir` 就用它的（`setdefault`）：这是注入点，不是覆盖点。

        第三样（Task 5）：**每一遍跑完当场播一条**（`on_run=_run_teller(job_id)`）。
        为什么不图在 `run_dir`/`cdp_bin` 旁边直接写死：时间线挂在**这个 job** 上，
        没有 `job_id` 就无处可记（空字符串 = 今天那条路，不接这条线）。
        同上用 `setdefault`：显式给了 `on_run` 的调用方（测试 / 计划三的 Console）赢。
        """

        def run_selftest(py_path, ws_url, form_file, site, **kw):
            kw.setdefault("run_dir", str(selftest.default_run_dir(site, root=self._selftest_root)))
            if job_id:
                kw.setdefault("on_run", self._run_teller(job_id))
            return selftest.run(py_path, ws_url, form_file, site, cdp_bin=self._cdp_bin, **kw)

        return run_selftest

    def _explore_for(self, brief: dict, job_id: str = "") -> Optional[Callable]:
        """探路要朝**载荷里那个窗口**去（服务是知道窗口的那一层）。

        为什么要这一根线：`browser_agent.explore()` 自己不收 `ws_url`（图调它时只给
        url/goal/预算/暂停谓词），窗口是从 `tools.McpSession.open(ws_url=…)` 或环境变量
        `CDP_WS_URL` 进去的。载荷里那个窗口不给它，它就会退回默认的 `127.0.0.1:9222` ——
        也就是**别的**浏览器（本机那个 headless，或者更糟：别的任务的窗口）。

        另外两件（计划四 Task 1，都是**加**）：每一步落 journal（G1：`on_step` 早就有，
        服务没接）、一次探路收场时记一行账（`attempts.jsonl` —— 基线 M2/M3 的输入）。

        第三件（Task 3 修复轮 1）：**步拍那个目录也归这里给**（`shots_dir=<job 级目录>`）。
        不给的话 Task 2 整片步拍在**生产里是死的**（`explore(shots_dir=...)` 只有测试传），
        而页面上那句「关掉逐步的图是为了省钱」就成了空话。目录由 `shots.dir_for` 按需建
        （**写**的那一侧），根与闸拍同一个（服务构造时定死的那个）。
        """
        ws_url = str(brief.get("ws_url") or "").strip()
        if not ws_url:
            return None                       # 没人给窗口 → 用默认（图会在自测那步停下点名）
        #: 步拍落在哪（这一趟探路的 job 级目录）。**算不出来就不接** ——
        #: `job_id` 不像话时 `dir_for` 报错，而这里是图的路径上，不许抛；
        #: 闸拍那条路自己会把这个原因记成一句人话（`_shoot_pause`）。
        #:
        #: ⚠️ **这个目录是「一轮」共用的，而 Task 2 的上限是「一趟」的**（修复轮 3 的判定）：
        #: `MAX_KEPT_SHOTS = 40` 判的是**本趟**盘上属于这一趟的张数（`_StepShots` 每一趟
        #: 新建一个），而一个节点最多重探 `graph.EXPLORE_ATTEMPTS = 3` 趟 ⇒
        #: **一个 job 目录里最多 3×40 = 120 张步拍图**（典型 ~110KB/张 ⇒ ~13MB）
        #: **+ 每轮一张闸拍**（`pause-<n>.png`，**不计入**那个上限 —— 它是服务侧另一条路）。
        #: 这不是 bug，是「上限=每趟」这件事没说出口：设计注 §5.5 的「最坏 ~20MB」本来就
        #: 按 40 张/次 + 闸图算的，量级对得上。**要收紧就收在「每 job」那一层**
        #: （跨趟计数），那是清理/保留策略的事，不在这一片。
        try:
            shots_where = str(shots.dir_for(job_id, root=self._shots_dir)) if job_id else None
        except ValueError:
            shots_where = None
        #: 账本第一个**没记成**的原因。`on_step` 吞掉异常，但要留下它 —— 由 `run()` 写进
        #: `journey.notes`（那一层才有 journey）。见 `_journal_for` 的注释。
        #: ⚠️ **每一趟清空一次**（M-6）：不在 `run()` 开头清的话，上一趟的故障会跟着
        #: 后面那一趟（它自己每步都写成功了）一路记下去 —— 那是**假 note**。
        journal_broken: list = []
        #: 时间线那一本的「第一个没记成的原因」。⚠️ **与账本那本分开**（2026-09-18）：
        #: 两个旁路的坏法不一样，合成一本之后那句 note 会说成「账本缺步」——
        #: 而账本一个字节都没缺。同上，**每一趟清空一次**。
        timeline_broken: list = []

        def run(url, goal, budget=None, should_pause=None, resume_from=None,
                resume_note="", window_alive=None):
            started = measure._now()
            # ⚠️ **每一趟取一次号**（I-1）：`deps.explore` 在一次节点执行里最多被调
            # `graph.EXPLORE_ATTEMPTS`(=3) 趟（重探），而「一趟 = 一个 `attempt-<n>.jsonl`」。
            # 号要是**建图时**算一次，三趟就全挤进同一个文件、也没有任何边界标记 ——
            # 而 `attempts.jsonl` 那边是**一趟一行**，两本账当场对不上（`rows != journey.steps`）。
            journal_broken.clear()
            timeline_broken.clear()
            # 一步有**两个读者**（都是旁路，坏掉都不许带塌主路 —— `explore.emit` 统一兜底）：
            #   ① 账本（`_journal_for`，落盘的那一份，Task 4 就在）；
            #   ② 时间线（`_step_teller`，契约七格 —— 2026-09-18 接上）。
            # ⚠️ 分成两个而不是合成一个：账本是**一步一行**的 JSONL（给重放和事后查），
            #    时间线是**给人读**的事件流（契约那七格）。两者的坏法不一样，
            #    `_journal_for` 自己吞异常并记一句，时间线那条**要响**（形状错 = 编程错误）。
            tell = self._step_teller(brief, job_id, timeline_broken) if job_id else None
            on_step = _two_readers(
                (self._journal_for(job_id, self._next_attempt_no(job_id), journal_broken)
                 if job_id else None), tell)
            # 模型**每一轮**的话（`AI 说：…`）—— 第三根线（Task 5，设计注 §3.3）。
            # ⚠️ 它只有**一个**读者（时间线），所以不走 `_two_readers`；
            # 坏掉那件事与上面两条共用 `timeline_broken` 那一本（同一个落点：时间线）。
            on_note = self._note_teller(job_id, timeline_broken) if job_id else None
            try:
                journey = browser_agent.explore(url, goal, budget=budget,
                                                should_pause=should_pause, ws_url=ws_url,
                                                on_step=on_step,
                                                on_note=on_note,
                                                resume_from=resume_from or None,
                                                resume_note=resume_note or "",
                                                window_alive=window_alive,
                                                shots_dir=shots_where,
                                                binary=self._mcp_bin)
            except BaseException as exc:       # noqa: BLE001 —— `_Stop` 也是 BaseException
                # 探路自己炸了 —— 也得留一行，不然「这一次尝试」凭空消失，
                # 而消失的那一次恰恰是最该被看见的那一次。记完**原样再抛**。
                # ⚠️ 这一条**到不了下面那句 note**（没有 journey）—— 账本没记成这件事
                # 只能落在 `attempts.jsonl` 的「这一趟连账本都没生成」那一行上（I-3）。
                self._note_attempt(job_id, started=started, journey=None, boom=exc)
                raise
            if journal_broken:
                journey.notes.append(
                    "⚠️ 这一步之后的账本没记全：%s —— 探路照常走完（旁路坏掉不许带塌主路），"
                    "但这一趟的 journal 是残的（`attempt-*.jsonl` 里缺步）。" % journal_broken[0])
            if timeline_broken:
                # 时间线那一本**单独说**（两个旁路的坏法不一样，合并会把话说过头）。
                journey.notes.append(
                    "⚠️ 这一步之后的时间线没记全：%s —— 探路照常走完（旁路坏掉不许带塌主路），"
                    "但这一趟的 `/live` 上会缺事件。" % timeline_broken[0])
            self._note_attempt(job_id, started=started, journey=journey)
            return journey

        return run

    # ── 旁路：运行产物（计划四 Task 1）──────────────────────────────
    # ⚠️ 这一整节都是**旁路**：它坏掉不许把主路带塌（同 Console 那片对 shooter 的规矩）。
    #    「记不上账」是可惜，「跑挂了」是另一件事 —— 两件事不能混成同一件。

    def _window_alive_cb(self) -> Optional[Callable]:
        """`Deps.window_alive`：问一句「窗口还活着吗」（§1.8）。零参数、**三态**。

        ⚠️ **问接口，不猜文本**：探路那侧只在「工具连着失败几次」之后调它一次，
        拿回来的 `False` 才是 `window_gone` 的证据（工具那句错误文字不作数）。
        没接窗口层的部署返回 `None` = 这**不是一根线**，探路于是不会编一个停因出来
        （与 `set_viewport` 那条同一条规矩：给不了就说给不了，不许塞一个假回调顶上）。
        """
        window = self._window
        if window is None or not hasattr(window, "alive"):
            return None
        return window.alive

    def _explore_dir(self, job_id: str) -> pathlib.Path:
        """`runtime/explore/<job_id>/`。⚠️ `job_id` 是从 HTTP 进来的字符串 —— 必须挡住 `../`，
        否则一个能爬出去的 id 就等于**任意写**。

        ⚠️ 这道守卫**只写在一处**（`journal.dir_for`）—— 同一份**安全判据**写两遍，
        收紧一处、另一处不动就是一个洞（M-2）。这里只把根换成本服务的。
        """
        return journal.dir_for(self._explore_root, job_id)

    def _next_attempt_no(self, job_id: str) -> int:
        """这是第几次尝试。从**盘上已有的** `attempt-*.jsonl` 数出来 —— 于是服务重启过、
        或者图又进了一次 `explore`，编号都接着走（不会把上一趟的账覆盖掉）。"""
        try:
            done = [int(p.stem.split("-", 1)[1]) for p in self._explore_dir(job_id).glob("attempt-*.jsonl")
                    if p.stem.split("-", 1)[1].isdigit()]
        except (OSError, ValueError):
            return 1
        return (max(done) + 1) if done else 1

    def _journal_for(self, job_id: str, n: int, broken: Optional[list] = None) -> Callable:
        """`on_step`：每一步**发生的那一刻**追加一行（G1：今天一个字节都不落）。

        为什么必须是**当场**而不是跑完再写：窗口就在这一步到下一步之间死掉
        （`operTime`→`closeTime` 那一段）—— 跑完再写的话，死的正是**没写下来的那一段**。

        ⚠️ **旁路坏掉不许带塌主路**（R-19 / 与 Console 那片对 `shooter` 的规矩同一条）：
        账本写不进去**不许**变成「这一步的工具失败了」——那等于把旁路的故障记到产物头上，
        模型还会照着这条假错换一条路走。所以这里**吞掉**异常；但**不许静默**：
        第一个原因记进 `broken` 这个列表，由 `run()` 写进 `journey.notes`
        （这一层够不着 journey，`run()` 那个闭包够得着 —— **两半合起来才成立**）。

        `broken` 由调用方给（**每一趟一份**：`run()` 开头会清空它，见那里的注释）；
        不给就自己造一份（直接调这个方法的人只需要「写下去」这件事）。

        ⚠️ 三种坏法走**同一个出口**（`_remember`）：建路径失败 / 建目录失败 / 写失败 ——
        `ValueError` 那条**也走它**（M-3）：job_id 不像话时返回 no-op 就是**静默**，
        而这个文件自己刚立过「no-op 等于静默」的规矩。生产上那条不可达
        （job_id 是服务端生成的 `job-<12 位 hex>`），但规矩不该有例外。
        """
        broken = broken if broken is not None else []

        def _remember(exc: BaseException) -> None:
            if not broken:
                broken.append("%s: %s" % (type(exc).__name__, exc))

        def _broken(exc: BaseException) -> Callable:
            """每步都记一次「没记成」（只留第一条），而不是当没事发生。"""
            def on_step(step: dict, _exc: BaseException = exc) -> None:
                _remember(_exc)
            return on_step

        try:
            path = journal.attempt_path(self._explore_root, job_id, n)
        except ValueError as exc:
            return _broken(exc)
        except OSError as exc:
            # **连目录都建不出来**（盘满了 / 没权限 / 路径上有个文件）——
            # 返回 no-op 就等于**静默**：这一趟没有账本，而没有任何人看得出来。
            return _broken(exc)

        # ⚠️ **占号**（I-1）：这一趟**跑过**就得留下一个文件 —— 哪怕一步都没走成
        # （空文件），或者整趟都写不进去。理由：`_next_attempt_no` 是**数文件**的
        # （重启/重探都靠它接着编号），不占号的话下一趟会复用同一个号 ⇒
        # 「一趟一个文件」当场失效、两本账（`attempt-*.jsonl` 与 `attempts.jsonl`）对不上。
        try:
            if not path.exists():
                path.touch()
        except OSError as exc:
            return _broken(exc)

        def on_step(step: dict) -> None:
            try:
                journal.append(path, step)
            except Exception as exc:                  # noqa: BLE001
                _remember(exc)
        return on_step

    # ── 闸拍：推进一步之后**把这一轮的眼睛留下**（设计注 §5.5）─────────────
    # ⚠️ 这一节整节是**旁路**（与 journal / 步拍同一条规矩）：拍不成只记一句人话，
    #    **绝不**抛到 `_advance` 上 —— 那会把「一次截图失败」变成「整趟跑挂」。
    #    更要命的是**闸**：抓拍坏掉不许把「停在闸上等人」弄丢
    #    （闸没了 = 人的交互点被吃掉，比没有图坏得多）。

    def _capture_pause(self, job: Job) -> None:
        """跑到闸口（或跑挂了）之后拍一张：`pause-<n>.png`。**不抛**。

        `n` 是**第几轮**（`job.pauses`）—— Task 6 的配对规则「第 n 道闸上拍的那张叫
        `pause-<n>`」靠的就是它，所以**拍不成的那一轮也占号**：不占号的话，
        「第 n 轮」与「`pause-<n>`」当场错开，页面会把上一轮的图挂到这一轮上。

        ⚠️ **不许在锁里拍**（简报点名）：名字先取（拿一次锁）、拍完再记（再拿一次）。
        攥着 `job.lock` 拍 = 那 0.2 秒里 `GET /job/{id}` 读不动；
        攥着 `self._check.lock` 拍 = 整个单飞的工作线程都在等它。
        """
        with job.lock:
            job.pauses += 1
            n = job.pauses
        name, why = self._shoot_pause(job, n)
        with job.lock:
            job.shot_notes.append({"n": n, "name": name, "why": why})
        if name is None:
            # 目录表第 9 行：这一轮没留下图要**说出来**（why 原文照抄）。
            # 空图框冒充页面是设计注明令禁止的（§3.2 第 6 行）—— 人话里就写着为什么没有。
            self.narrate(job, "shot_missing", SHOT_MISSING_SAY % (why or "没说为什么"),
                         n=n, why=why)

    def _shoot_pause(self, job: Job, n: int) -> tuple[Optional[str], str]:
        """真去拍一张（`_capture_pause` 的下半截）。**不抛** —— 连算落点的 `ValueError` 也收在这儿。"""
        ws_url = str((job.brief or {}).get("ws_url") or "").strip()
        if not ws_url:
            # ⚠️ 这一格比 `capture_via_cli` 里那格更靠前：**压根没有窗口**，
            #    所以一个进程都不该起（那一格管的是「给了串但认不出来」，
            #    它会明说**绝不**退回 127.0.0.1:9222 —— 那是本机的**另一个**浏览器）。
            return None, ("还没开浏览器（这份开场白里没有 ws_url）—— 这一轮没有窗口可拍。"
                          "窗口开出来（`bit.sh open` 那串）之后的轮次才有图。")
        try:
            dest = shots.path_for(job.job_id, root=self._shots_dir) / ("pause-%d.png" % n)
        except ValueError as exc:
            return None, "落点算不出来：%s" % exc
        return self._shoot(ws_url, dest)

    def _shoot(self, ws_url: str, dest) -> tuple[Optional[str], str]:
        """`capture` 跑在**旁路线程**上，`self._shot_timeout` 秒没回来就当它没成（硬超时）。

        为什么要多一层线程（设计注 §5.5 / 计划 §R6）：单飞的工作线程是**唯一**推图的地方
        （D6）—— 拍照那条路（真实现是 `cdp` 子进程）卡住的话，整个服务就停在那儿了。
        超时只丢这一张图：闸照旧在、账照旧记，人话里说清是超时。
        """
        # ⚠️ 二进制**用构造时定下的那个**（`functools.partial` 把它绑死）——
        #    `capture_via_cli` 不给 `cdp_bin` 时会去读环境，而读的时刻是**调用的时刻**：
        #    抓拍在工作线程上，它可能比设那段环境的代码活得久
        #    （全量套件里实测**每趟 2–4 次**漏 —— 竞态量，取决于哪几条「发了 job 不等它」
        #    的用例的 worker 活过了它的 fixture；别把它当仪器读数）。
        capture = (self._capture
                   or functools.partial(shots.capture_via_cli, cdp_bin=self._cdp_bin))
        box: dict = {}

        def work() -> None:
            try:
                box["out"] = capture(ws_url, str(dest), timeout=self._shot_timeout)
            except BaseException as exc:                  # noqa: BLE001 —— 外面世界
                box["boom"] = exc

        worker = threading.Thread(target=work, name="siteforge-shot", daemon=True)
        worker.start()
        worker.join(self._shot_timeout)
        if worker.is_alive():
            return None, ("拍照超过 %.0f 秒还没回来（超时）—— 窗口可能卡住了；"
                          "这张图没留下，闸照旧在" % self._shot_timeout)
        if "boom" in box:
            exc = box["boom"]
            return None, "拍照时它抛了 %s：%s" % (type(exc).__name__, exc)
        out = box.get("out")
        if not (isinstance(out, tuple) and len(out) == 2):
            return None, "拍照没给出（文件名, 人话）这样的结果：%r" % (out,)
        name, why = out
        if not name:
            return None, str(why or "拍不成，而且没说为什么")
        return str(name), ""

    def _shot_note_for(self, job: Optional[Job], name: str) -> Optional[dict]:
        """这一轮**拍过吗**（按文件名反查 `pause-<n>` 那条记录）。没有这个 job 就是 `None`。"""
        if job is None:
            return None
        with job.lock:
            notes = [dict(x) for x in job.shot_notes]
        for note in reversed(notes):
            if name == "pause-%s.png" % note.get("n"):
                return note
        return None

    def shot_file(self, job_id: str, name: str) -> pathlib.Path:
        """`/job/{id}/shot/{name}` 的两个字符串 → 一个**安全**的落点；不像话就 404（人话）。

        三道判据（缺一不可）：
          - `name` 过 `shots.name_ok`（一段、以 `.png` 结尾）—— 这个参数是外面来的；
          - `job_id` 过 `shots.path_for`（**只解析、不建目录**：这是**读**的那一侧，
            用 `dir_for` 就等于「一个 GET 建一个目录」）；
          - 解析完**再确认一次它真的在那个目录里** —— 名字合法不等于路径老实：
            一个指向 `/etc/passwd` 的 `pause-1.png` 也是「一段、以 .png 结尾」。
        """
        if not shots.name_ok(name):
            raise HTTPException(status_code=404, detail=(
                "这个名字不能当图名：%r —— 图名只许是 `pause-3.png` 那样的一段"
                "（字母数字与 . _ -，1–64 字，以 .png 结尾）。这一个参数是外面来的，"
                "带 `/` 或 `..` 的名字能把人带去别的地方读文件。" % (name,)))
        try:
            where = shots.path_for(job_id, root=self._shots_dir)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="这个任务 id 不能当目录名：%s" % exc)
        path = where / str(name)
        inside = os.path.realpath(str(where))
        real = os.path.realpath(str(path))
        if os.path.dirname(real) != inside:
            raise HTTPException(status_code=404, detail=(
                "这张图不在那个任务的目录里：%s —— 它顺着链接跑到别处去了，不给你读。"
                % name))
        if not path.is_file():
            raise HTTPException(status_code=404, detail=self._no_shot_say(job_id, name))
        return path

    def _no_shot_say(self, job_id: str, name: str) -> str:
        """「没有这张图」的人话。这一轮**拍过、只是没拍成**的话，把那句 `why` 一起带上
        （不然页面上只剩「没有这张图」，而原因明明就在手上 —— 那也是一种静默）。"""
        said = ("没有这张图：%s/%s —— 盘上就没有它。图是**跑到闸口时**才拍的"
                "（一轮一张，`pause-<n>.png`）。" % (job_id, name))
        note = self._shot_note_for(self._jobs.get(job_id), name)
        if note and note.get("why"):
            said += "这一轮拍过，但没拍成：%s" % note["why"]
        return said

    # ── 两块接线信息（页面不自己编）────────────────────────────────

    def window_public(self) -> Optional[dict]:
        """「这个窗口」那一块（设计注 §十）：**只摆事实 + 方法，不编 URL**。

        没接窗口层 → `None`（页面据此**明说**「看不到活窗口」，而不是显示一块空表）。
        `how` / `when` 两句写死在这个文件里（`WINDOW_HOW` / `WINDOW_WHEN`）：页面原样显示。
        身份那三样里缺的（桩窗口、或者别的实现没带）给 `None` —— 不知道就说不知道。
        """
        window = self._window
        if window is None:
            return None
        return {"worker": getattr(window, "worker_ip", None),
                "bit_id": getattr(window, "bit_id", None),
                "api_port": getattr(window, "port", BIT_API_PORT),
                "how": WINDOW_HOW, "when": WINDOW_WHEN}

    def shots_note(self) -> str:
        """**降级 B** 那句话（设计注 §5.5）：每步抓拍开着 → `""`；关着 → 一句人话。

        ⚠️ 判据走 `shots.step_shots_on()` —— 与 `explore` **同一份读法**。
        这里要是自己再解析一次（哪怕只差一个 `strip`），就会出现
        「图没了、一个字没解释」= 设计注明令禁止的**静默降级**。
        """
        if shots.step_shots_on():
            return ""
        return SHOTS_OFF_SAY % self._shot_price_say()

    @staticmethod
    def _shot_price_say() -> str:
        """「一张图多少钱」那半句。**没给实测数就不吹具体数字**（那是个猜测，写上去就成了事实）。"""
        raw = str(os.environ.get("SITEFORGE_SHOT_SECONDS") or "").strip()
        if not raw:
            return ("每步抓拍被关掉了（`SITEFORGE_STEP_SHOTS=0`）——"
                    "这里没有实测的每张耗时，就不编一个数字。")
        try:
            seconds = float(raw)
        except ValueError:
            return ("每步抓拍被关掉了（`SITEFORGE_STEP_SHOTS=0`）——"
                    "`SITEFORGE_SHOT_SECONDS=%r` 不是一个数，实测耗时读不出来。" % raw)
        return "截图实测 %.1f 秒/张，太贵。" % seconds

    def _note_attempt(self, job_id: str, *, started: str, journey, boom: BaseException = None) -> None:
        """一次尝试收场 → `attempts.jsonl` 一行（墙钟 / 轮数 / 步数 / 停因）。

        ⚠️ **`rounds` 没量到的那些路一律记 `None`，不许记 0**（Task 3 遗留 3）。
        `_Stop`（人喊停 / 预算到顶 / 计划停滞 / **窗口没了**）一穿出 `run_tool_loop`，
        `rounds` 那个局部变量就没了 —— 记 0 会被读成「这一趟没花轮数」，于是 M3 偏低，
        而偏低看起来像好消息（`measure.record_attempt` 的 docstring）。

        ⚠️ 判据走 `browser_agent.rounds_measured(journey)`，**不在这里再比一次
        `stop_reason == "paused"`**：那个写法在本片新增 `window_gone` 之后就已经漏了
        （复审 I-5 点名的正是这条遗留）。两处各写一份 = 早晚分家。
        """
        if not job_id:
            return
        try:
            path = self._explore_dir(job_id) / "attempts.jsonl"
            if journey is None:
                why = "%s: %s" % (type(boom).__name__, boom) if boom is not None else "没有账本"
                measure.record_attempt(path, started_at=started, ended_at=measure._now(),
                                       rounds=None, steps=None, path_shape=None,
                                       stop_reason="failed", notes=["这一趟连账本都没生成：" + why])
                return
            measured = browser_agent.rounds_measured(journey)
            steps = list(getattr(journey, "steps", []) or [])
            measure.record_attempt(
                path, started_at=started, ended_at=measure._now(),
                rounds=int(getattr(journey, "rounds", 0) or 0) if measured else None,
                steps=len(steps),
                stop_reason=str(getattr(journey, "stop_reason", "") or ""),
                # M9 的载体（**一次样本一存**）：分支站的两次跑长度不同，只有存下每趟的形状，
                # M10 的跨度才算得出来（§2.2）。
                path_shape=measure.path_shape(steps),
                notes=list(getattr(journey, "notes", []) or [])[-6:])
        except Exception:                      # noqa: BLE001
            traceback.print_exc()

    def _last_window_row(self, job_id: str) -> Optional[dict]:
        """上一行时间线（判「PID 换了吗」只能靠它）—— 从盘上读，服务重启也不丢。"""
        try:
            rows = [r for r in measure.read_rows(self._explore_dir(job_id) / "window.jsonl")
                    if "_corrupt" not in r]
        except Exception:                      # noqa: BLE001
            return None
        return rows[-1] if rows else None

    def _window_lifecycle(self) -> dict:
        """`/browser/detail` 的 `operTime` / `closeTime` —— 窗口**开于/关在**哪一刻（§3.1）。

        为什么要它（M6）：探针每 15 秒才看一眼，它给的寿命**带着一个探测间隔的误差**；
        而 `operTime → closeTime` 是**精确的**那一对 —— 判「固定租约 vs 空闲回收」
        看的正是这个差**恒不恒定**（§3.1 的人给的判据）。

        ⚠️ 只在窗口状态**变了**的那一刻读（开/死各一次），不进常规轮询：
        常规探活用 `pids/alive` 就够了，每 15 秒多打一个接口是白花的。
        `closeTime` 没关时是**当天零点**（实测 `2026-09-16 00:00:00`）—— 认它 = 认「还没关」。
        """
        if self._window is None or not hasattr(self._window, "detail"):
            return {}
        try:
            data = dict(self._window.detail() or {})
        except Exception:                      # noqa: BLE001 —— 读不到就是读不到，不许编
            return {}
        out: dict = {}
        oper = str(data.get("operTime") or "").strip()
        if oper and not oper.endswith("00:00:00"):
            out["oper_at"] = oper
        close = str(data.get("closeTime") or "").strip()
        if close and not close.endswith("00:00:00"):
            out["close_at"] = close
        return out

    def _probe_window_row(self, job_id: str, *, at: Optional[str] = None,
                          note: str = "") -> dict:
        """一次探活 → 时间线一行（落 `window.jsonl`）。

        探不出来（没有窗口层 / 接口答非所问）→ 记 `unknown`，**不许记成死**。
        """
        probe = {"alive": None, "pid": None}
        try:
            if self._window is not None:
                probe = (self._window.probe() if hasattr(self._window, "probe")
                         else {"alive": self._window.alive(), "pid": None})
        except Exception:                      # noqa: BLE001
            traceback.print_exc()
            probe = {"alive": None, "pid": None}
        row = measure.window_row(probe, at=at or measure._now(), note=note,
                                 prev=self._last_window_row(job_id))
        if row["new_window"] or row["alive"] == "dead":
            row.update(self._window_lifecycle())   # 开/死各读一次（§3.1 的 operTime/closeTime）
        measure.append_row(self._explore_dir(job_id) / "window.jsonl", row)
        return row

    def _start_window_probe(self) -> None:
        """起一条窗口时间线探针（只在**能给 PID** 的窗口层上起 —— 桩窗口没有 PID，
        那样的时间线只有活/死，判不出「重开了几次」）。"""
        if self._window_probe_thread is not None or self._window is None:
            return
        if not hasattr(self._window, "probe"):
            return
        t = threading.Thread(target=self._window_probe_loop, name="window-timeline", daemon=True)
        self._window_probe_thread = t
        t.start()

    def _window_probe_loop(self) -> None:
        while True:
            time.sleep(self._probe_seconds)
            job_id = self._active_job
            if not job_id:
                continue
            try:
                self._probe_window_row(job_id)
            except Exception:                  # noqa: BLE001 —— 旁路不许把工作线程带死
                traceback.print_exc()

    def _write_baseline(self, job_id: str, *, end: Optional[dict] = None) -> Optional[dict]:
        """把这一趟的账压成 `baseline.json`（M1~M8）。**能算的算，算不出来的给 None + 为什么。**"""
        try:
            d = self._explore_dir(job_id)
            rows = [r for r in measure.read_rows(d / "window.jsonl") if "_corrupt" not in r]
            steps: list = []
            # 按**数字**排（`attempt-10` 排在 `attempt-2` 后面）：字符串序会把两次尝试
            # 的步**接反**，而 M9 的「状态序列」是靠顺序读出来的。
            for p in sorted(d.glob("attempt-*.jsonl"), key=measure.attempt_no):
                steps.extend(r for r in measure.read_rows(p) if "_corrupt" not in r)
            return measure.baseline(
                d / "baseline.json",
                window_lifetimes=measure.window_lifetimes(rows),
                attempts=measure.read_attempts(d / "attempts.jsonl"),
                end=dict(end or {}), window_rows=rows,
                path_steps=steps, probe_seconds=self._probe_seconds)
        except Exception:                      # noqa: BLE001
            traceback.print_exc()
            return None

    def _measure_after(self, job_id: str) -> None:
        """job 停下时：记一行时间线 + 汇总一份 baseline。**全程吞异常**（旁路不许带塌主路）。"""
        try:
            self._probe_window_row(job_id, note="job 停在这一刻")
        except Exception:                      # noqa: BLE001
            traceback.print_exc()
        end: dict = {}
        try:
            values = dict(getattr(self._snapshot(job_id), "values", None) or {})
            end = {"end_reason": values.get("end_reason") or "",
                   "delivered": bool(values.get("delivered"))}
        except Exception:                      # noqa: BLE001
            traceback.print_exc()
        self._write_baseline(job_id, end=end)

    def _clean_window_for_explore(self, brief: dict) -> None:
        """**探路也要在干净会话里跑**（R-F1 的另一半，2026-09-17 裁定）。

        为什么：生产**每一单都是新窗口**（`clearCookiesBeforeLaunch` 只在启动那一刻生效），
        所以生产遇到的第一个东西往往是**cookie 同意弹层**。而探路要是跑在一个
        「同意过 cookie」的会话里，它**学到的是一条没有弹层的路** —— 账本里没有那一步，
        产物到了生产（有弹层）就会点到弹层上。这不是 cookie 一件事，是**系统性的**：
        探索的条件必须与自测/生产一致，否则账本学的路径在生产里不成立。

        做法：这个部署给得了 `fresh_open` 就换一个干净窗口（清 cookie/缓存），
        把新 ws_url 写进 brief（探路与后面所有步骤都用它）。
        ⚠️ 换不了（没接窗口层 / 窗口服务抖了）时**照旧用调用方给的那个**，
        把原因记在日志里 —— 那是「条件更差」，不是「这一单不能跑」。
        """
        if self._window is None or not hasattr(self._window, "fresh_open"):
            return
        try:
            ws = self._window.fresh_open()
        except Exception as exc:                     # noqa: BLE001 —— 外面世界
            print("[siteforge] 探路前换干净窗口没成（%s）—— 用调用方给的那个窗口接着跑"
                  "（账本学的路径可能带着「弹层已经点过」的前提）" % exc)
            return
        if ws:
            brief["ws_url"] = str(ws)

    def _fresh_session_cb(self) -> Optional[Callable]:
        """R-F1 那根线：自测之前换一个**干净会话**（关旧窗 → 开新窗，返回新的 ws_url）。

        - 这个部署没接窗口层、或窗口层给不了 `fresh_open` → `None`：图**照跑**，
          但在 `selftest` 的 facts 与 `diagnose` 里**说清「这一次不是干净会话」**
          （不是跳过、不是假装干净 —— 判据一个字不改）。
        - 接上了 → 真回调。**换失败要抛**：图接住它，把「不是干净会话」如实记下再照跑 ——
          条件差不是产物不行，但读报告的人必须知道。
        """
        if self._window is None or not hasattr(self._window, "fresh_open"):
            return None

        def cb() -> str:
            ws = self._window.fresh_open()
            if not ws:
                raise RuntimeError("换干净会话没给出新的 ws_url")
            return str(ws)
        return cb

    def _viewport_cb(self, ws_url: Optional[str] = None) -> Optional[Callable]:
        """窗口层那根线（第 4 遍扰动要换窗口大小）。

        - 没人点名要它（载荷里 `set_viewport=false`）→ 返回 `None`：**图会停在 `intake`
          点名**「缺 `set_viewport`，谁给得了」—— 那是 R-31 要的诚实停止，不是缺陷。
        - 要了、但这个部署没接窗口层 → `_intake_problems` 已经在**提交那一刻**拒了。
        - 要了、也接了 → 真回调：**先写配置，再量活窗口**。量出来没变就**抛**
          （`selftest` 会把这一遍记成「没跑」，而「没跑」不算过，R-5）。
          尺寸用自测那一步给的那个（`selftest.run` 按 `DEFAULT_VIEWPORT` 调），服务不替它定。
        """
        if self._window is None or not hasattr(self._window, "set_viewport"):
            return None

        def cb(width, height):
            self._window.set_viewport(width, height)
            got = self._viewport_probe(ws_url) if ws_url else None
            if got is None:
                # **量不出来 ≠ 做成了**：没有 cdp / 连不上 / ws_url 不认识时探针给 None，
                # 而「没量到」被当成「没问题」的话，第 4 遍会被记成**跑过了**，
                # 于是产物带着「折叠/遮挡/坐标假设验过了」出门 —— 而一个数都没量到。
                # 抛出去的后果是**对的**那一头：`selftest.run` 把回调抛错记成
                # 「这一遍没跑」（`selftest.py:475`），不是把整跑杀掉。
                raise RuntimeError(
                    "换窗口大小之后**量不出活窗口现在多大**（没有 cdp 二进制 / 连不上 / "
                    "ws_url 认不出来），所以这一遍扰动没法诚实地说自己做成了。"
                    "把它当成「没验到」：要么修好这根线（`SITEFORGE_CDP_BIN` / 窗口还活着），"
                    "要么明确放弃第 4 遍（把 \"viewport\" 写进 `allow_skips`）。")
            if tuple(got) != (int(width), int(height)):
                raise RuntimeError(
                    "把窗口换成 %sx%s 了，但**活着的那个窗口**量出来还是 %sx%s —— 这一遍扰动"
                    "等于没做。Bit 的窗口尺寸是**启动时**生效的（实测：写配置回 success、"
                    "回读也是新值，活窗口纹丝不动）。所以第 4 遍要真跑，得**关掉重开**："
                    "要么放弃这一遍（写进 allow_skips），要么把「关窗口 → 改尺寸 → 重开 → "
                    "换 ws_url」做成一条流程 —— 那要改自测的接口（现在五遍共用一个窗口）。"
                    % (width, height, got[0], got[1]))
        return cb

    def _probe_graph(self):
        """一个只用来 `get_state()` 的图（不跑节点）—— 服务重启后靠它把 job 读回来。

        ⚠️ 它建在**本服务的** checkpointer 的**读连接**上（不是 `graph_factory` 那个、
        也不是写连接）：一个 job 的图是调用方拼的，而「这个 job 现在什么样」这件事得由一个
        **确定接在同一份状态上**的东西来回答（R-19：状态住在 saver 里 —— 那就得问同一个 saver）；
        而**读**这条线不该排在一次 `invoke` 后面（那个可能跑几分钟）。
        """
        if self._probe is None:
            self._probe = graph.build(checkpointer=self._check.reader(), deps=graph.Deps())
        return self._probe

    def _cfg(self, job_id: str) -> dict:
        return {"configurable": {"thread_id": job_id}}

    def _snapshot(self, job_id: str):
        """读这个 job 的状态。

        - **服务自己拼的图**（生产，`graph_factory is None`）→ 走**读连接**：
          读不跟正在跑的那次 `invoke` 抢写锁（那个可能持几分钟）。
        - **调用方自己拼的图** → 读也问它那张图（它接在哪份 saver 上，它最清楚）。
        """
        job = self._jobs.get(job_id)
        if self._graph_factory is None and self._check.separate_reader:
            with self._check.reader_lock:
                return self._probe_graph().get_state(self._cfg(job_id))
        g = job.graph if (job is not None and job.graph is not None) else self._probe_graph()
        with self._check.lock:
            return g.get_state(self._cfg(job_id))

    # ── 时间线：**唯一的写入口**（Task 4；契约 `docs/执行事实契约-2026-09-18.md`）────
    # 目录表那九条触发点各自的调用点见 `_advance` / `start` / `reply` / `reopen` /
    # `_recover` / `_capture_pause`；这里放的是**入口**与几个共用的小工具。
    # ⚠️ 这一节整节是**旁路**（与 journal / 闸拍同一条规矩）：记时间线**绝不许**把
    #    主路带塌 —— 但「读不回状态」那一支是**响的**（记一条 `state_unreadable`），
    #    因为那正是「没有静默的路径」在**读**那一侧的样子。

    def narrate(self, job: Job, kind: str, say: str, *, who: str = "system", **data) -> dict:
        """往这个 job 的时间线上记一条 —— **谁都别直接碰 `job.timeline`**。

        为什么要只有一个口子（计划 §「跨任务接口」1）：人话纪律与七格的形状是靠
        「只有一条路进来」守住的；直接 `add` 的地方一多，「给旧的 `step` 换个名字」就会长回来。

        回调是从**工作线程**里来的（探路的步、抓拍、闸口），所以 `job.lock` 只包**一次**
        append（微秒级）：绝不在这里读快照、起进程、发请求。
        形状不对（`events.Timeline.add` 那几条：空 `say`、第四个 `who`、判断词当字段名…）**抛** ——
        那是编程错误，不是运行时状况（人话写不出来就说明还没想清）。

        ⚠️ **人话里写不出去的字节在这儿换掉，并当场说出来**（2026-09-18，Task 4 收口复审
        点名的那个洞）：`/live` 的返回值最后要过一遍 `.encode("utf-8")`，而**一个孤立代理对
        过得了 `json.dumps`、过不了那一次编码** —— 结果是 `/live` **500、整条时间线一条都
        读不出来**（不是那一条坏掉）。那几句人话里混着**外面来的字**（页面上的 url、
        运营写的期望），所以这条缝正好在这条路上。
        换掉而不是抛：抛掉整条事件 = **这一步的记录没了**，而那正是这份契约要治的病
        （契约 §二②：「看不见」是一等值）。换完在人话尾巴上报个数，读的人知道少了什么。

        ⚠️ 它**只管外壳那一层**（`say`）：`data` 里出现这种值仍然由 `events._facts` 当场拒
        —— 那是写的人的编程错误，不是外面来的东西（外面来的那些在转抄时就已经换过了，
        见 `browser_agent._utf8_safe`）。
        """
        safe_say, replaced = events.safe_value(say)
        if replaced:
            safe_say += ("（这条人话里有 %d 个字节线上写不出来（孤立代理对），"
                         "已按 `�` 记 —— 不是它本来长这样。）" % replaced)
        with job.lock:
            return job.timeline.add(kind, safe_say, who=who, data=data or None)

    def _step_teller(self, brief: dict, job_id: str, broken: Optional[list] = None) -> Callable:
        """`on_step` 的**第二个读者**：探路的一步 → 时间线上**两条**事件。

        为什么是**两条**而不是一条七格齐全的（契约 §二那张表是一行七格）：
        这个 schema 的**每一条事件只有一张嘴**（`who`），而七格是**两方填的** ——
        脚本填前五格、服务填 `verdict`、运营填 `expect`。合成一条，那条事件就得用
        **一个** `who` 说出两方的话，而 `SCRIPT_MAY_NOT_FILL` 那条闸（脚本不许填
        `verdict` / `expect`）正是靠「谁在说」执行的 —— 合成一条要么得关掉那道闸，
        要么得让服务替脚本签名。两种都是「承重的不是有哪几格，是**谁填**」被折掉。

        所以照着**人**来分（契约 §二 的原话）：

        | 事件 | `who` | `say` | `data` |
        |---|---|---|---|
        | 脚本报了这一步 | `agent` | 脚本自己的人话（「点了「下一步」」） | 前五格 |
        | 服务核了这一步 | `system` | **裁判那句话** | `verdict` + `expect` |

        两格口径不同，正是因为它们是**两个人在说话** —— 而那正是契约 §一 要的东西
        （今天病根是「判断和执行是同一方」）。
        """
        expects = list(brief.get("expects") or [])
        broken = broken if broken is not None else []

        def tell(step: dict) -> None:
            job = self._jobs.get(job_id)
            if job is None:
                # **不许静默**：探路在报步，可这个 job 不在登记表里 —— 时间线没地方记。
                # 但不抛：`_explore_for(...)` 返回的那个 `run` 是可以**直接调**的
                # （测试与将来的调用方都这么用，它们的 job_id 本来就不在登记表里），
                # 抛出去会把「这一趟探路」整个带塌 —— 而旁路坏掉**不许带塌主路**
                # （与 `_journal_for` 那条一模一样的规矩）。
                # ⚠️ 进的是**时间线自己那一本**账（`timeline_broken`），不是账本那本 ——
                # 两件事各有各的说法：混成一条，读的人会以为是 journal 缺了步。
                if not broken:
                    broken.append("第 %s 步没记上（job %s 不在登记表里）"
                                  % (step.get("step_no"), job_id))
                return
            self._note_step(job, step, expects)

        return tell

    def _note_teller(self, job_id: str, broken: Optional[list] = None) -> Callable:
        """`on_note`：模型**每一轮**说的话 → 时间线一条 `agent_said`（Task 5）。

        为什么这一条要单独一根线（设计注 §3.3 原话）：那是**它的推理** ——
        「我能充当他的眼睛或者纠错员」那句话里最需要的那一半，
        而今天它只在跑完之后才看得到（`journey.notes` 里躺着，还要展开）。

        `say` **逐字转抄**（`AI 说：…` 那整句，与 `journey.notes` 里那一行是同一句）：
        转抄不是改写 —— 换一个字，读的人就是在读服务的话而不是它的话，
        而这一片的承重句正是「承重的不是有哪几格，是**谁填**」（契约 §二①）。

        ⚠️ 与 `_step_teller` 一模一样的两条：job 不在登记表里**不抛**（抛会把这一趟
        探路整个带塌），但**不许静默**（原因进 `timeline_broken`，由 `run()` 写进
        `journey.notes`）。
        """
        broken = broken if broken is not None else []

        def tell(said: str) -> None:
            job = self._jobs.get(job_id)
            if job is None:
                if not broken:
                    broken.append("模型这一轮的话没记上（job %s 不在登记表里）" % job_id)
                return
            self.narrate(job, "agent_said", said, who="agent")

        return tell

    def _run_teller(self, job_id: str) -> Callable:
        """`on_run`：自测**每一遍**跑完 → 时间线一条 `selftest_run`（Task 5）。

        `say` = `Run.label` + `Run.note` **原文**（设计注 §3.2 第 3 行的形状）——
        那两句本来就是人话（「第 4 遍：换个窗口大小再跑…」+「这一遍没跑：…」），
        照搬，不重写、不再判断一次。

        ⚠️ **没跑的那几遍也要说**（`skipped` / `not_needed`）：它们正是「哪一类失败这次
        **没验到**」的载体，只在报告里看得见就等于没人看见（R-5 治的就是这个病）。

        ⚠️ 没有 job 可记时**抛**（与 `_note_teller` 不同，理由见那一头）：自测的播报在
        外面**没有** `journey.notes` 那样的落点，能看见它的只有 `selftest.run` 的护栏 ——
        而护栏正是照「回调抛了」来处置的（记进 `Report.narrate_broken`，随报告回到
        叫它的那一层）。**吞掉才是静默**：那样服务和人都不会知道这一趟的时间线少了每一遍。
        """
        def tell(run: Any) -> None:
            job = self._jobs.get(job_id)
            if job is None:
                raise RuntimeError(
                    "自测的那一遍没地方播报（job %s 不在登记表里）—— 时间线挂在这个 job 上，"
                    "没有它就无处可记。" % job_id)
            data = {"run": str(getattr(run, "name", "")),
                    "status": str(getattr(run, "status", ""))}
            step_no = getattr(run, "failed_step", None)
            if isinstance(step_no, int):
                data["failed_step"] = step_no
            self.narrate(job, "selftest_run",
                         "%s\n%s" % (getattr(run, "label", ""), getattr(run, "note", "")),
                         **data)

        return tell

    def _note_step(self, job: Job, step: dict, expects: list) -> None:
        """一条探路的步 → 时间线那两条事件（见 `_step_teller` 的表）。

        **前五格一个字都不动**（脚本写什么就是什么，这一层只转抄）—— 除了一件事：
        缺席与 `None` 是两件事，而 `Timeline` 要求「写了 `None` 就得配一句 `why`」。
        脚本没解释的那些格，这里如实补一句「**脚本没说为什么**，但这一格确实是
        「看不见」而不是「没变化」」—— 补的是**这件事本身**，不是替它编一个原因。
        """
        step_no = step.get("step_no")
        cells = {"action": {"what": str(step.get("action") or ""),
                            "target": step.get("target")},
                 "receipt": step.get("receipt"),
                 "sig_before": step.get("sig_before"),
                 "sig_after": step.get("sig_after")}
        if isinstance(step_no, int):
            cells["step_no"] = step_no
        why = dict(step.get("why") or {})
        for cell, value in cells.items():
            if value is None and not str(why.get(cell) or "").strip():
                why[cell] = ("脚本把这一格写成了空的，而 `why` 里没有它的解释 —— "
                             "照契约 §二②，空 = 「**看不见**」这个一等值，**不是**「没变化」；"
                             "原因没人说，这里也不替它编。")
        if why:
            cells["why"] = why
        script_say = str(step.get("note") or "").strip() or (
            "第 %s 步（`%s`）：脚本这一步没留下人话。" % (step_no, cells["action"]["what"]))
        self.narrate(job, "step", script_say, who="agent", **cells)

        # ── 第 6、7 格：期望是运营的（从载荷来），判是**这里**算的 ──────────────
        present, expect = _expect_at(expects, step_no)
        verdict, verdict_say = judge_step(
            step_no=step_no, receipt=step.get("receipt"),
            sig_before=step.get("sig_before"), sig_after=step.get("sig_after"),
            expect=expect, expect_present=present)
        judged = {"verdict": verdict}
        if present:
            # 运营写的字也是**外面来的**：写不出去的码位在这儿换掉（不换的话 `_facts`
            # 那道闸会**整条拒掉**这一条事件 —— 而丢记录比换一个字节坏得多）。
            judged["expect"] = events.safe_value(expect)[0]
        if isinstance(step_no, int):
            judged["step_no"] = step_no
        self.narrate(job, "step", verdict_say, **judged)

    def _where_it_stopped(self, job_id: str) -> tuple:
        """它停/走在**哪一步**：`(节点名, 人话)` —— 「窗口没了」那句话的 `%s` 与 `/live` 的 `stage`。

        三档，**全是从快照里读出来的**（读不出来就写「不知道哪一步」，绝不编）：
          ① 有闸口 → 闸在问哪一步（那正是它这一趟接着要做的事，也是设计注里
             「它停在「写这一版 py」之前」那个例子）；
          ② 没闸口但有 `next` → 图下一步要跑的节点；
          ③ 都没有 → `visits` 里最后落下的那一步。
        """
        unknown = ("", "不知道哪一步")
        try:
            snap = self._snapshot(job_id)
        except Exception:                      # noqa: BLE001 —— 读不出来就说不知道
            traceback.print_exc()
            return unknown
        values = dict(getattr(snap, "values", None) or {})
        gates = self._interrupts(snap)
        if gates:
            value = gates[0].value if hasattr(gates[0], "value") else gates[0]
            step = str((value or {}).get("step") or "")
            if step:
                return step, STEP_SAY.get(step, step)
        nxt = tuple(getattr(snap, "next", None) or ())
        if nxt:
            node = str(nxt[0])
            return node, STEP_SAY.get(node, node)
        visits = [str(v) for v in (values.get("visits") or [])]
        if visits:
            return visits[-1], STEP_SAY.get(visits[-1], visits[-1])
        return unknown

    def _note_window_died(self, job: Job, values: dict, *, dead: Optional[bool] = None) -> None:
        """目录表第 1 行：窗口**从「不是没了」翻成「没了」**的那一刻记一条。

        ⚠️ 三个判据（少一个就成了另一种毛病）：
        - **只有明确说死了才算**（`_window_is_gone`：问不出来一律当不知道 —— 不许误杀）；
        - **只有那一翻才记**：每次 `_advance` 返回都去问，但同一件事不记第二遍；
        - **重开窗口之后要能再记**（`reopen` 把 `window_dead` 清掉）——
          换的那个窗口也会死，那是一条**新**事实。
        `dead` 是**已经问过了**的那个答案（`reply()` 手上就有）：传进来就不必再问一次
        （每 15 秒多打一个 `/browser/pids/alive` 是白花的 —— 同一个理由）。
        """
        if job.window_dead:
            return
        ws_url = values.get("ws_url") or (job.brief or {}).get("ws_url")
        if not (self._window_is_gone(ws_url) if dead is None else dead):
            return
        token, where = self._where_it_stopped(job.job_id)
        with job.lock:
            job.window_dead = True
        self.narrate(job, "window_died", WINDOW_DEAD_SAY % where,
                     where=token, ws_url=str(ws_url or ""))

    def _note_end(self, job: Job, values: dict) -> None:
        """目录表第 6 行：快照里已经有终局 → `done` / `cap_hit`。

        `done` 说的是「**图走到了 END**」（与 `DONE` 那个状态同一个意思，
        **不是**「成了」—— 成没成看 `end_note` 的原话与 `/live` 的 `delivered`）。
        撞上限（`revision_cap` / `lint_cap` / `selftest_cap`）单独一个 `kind`：
        那是**它自己的一个结论**（再来一次还是同样的地方不过），人话前面加「撞上限了：」。
        """
        reason = str(values.get("end_reason") or "")
        if not reason:
            return                              # 没到头（停在闸上 / 还在跑）—— 没有终局可说
        note = str(values.get("end_note") or "").strip()
        if reason in CAP_END_REASONS:
            say = CAP_SAY_PREFIX + (note or "（快照里没写是撞了哪条上限 —— 这本身就不正常。）")
            self.narrate(job, "cap_hit", say, end_reason=reason, end_note=note)
            return
        self.narrate(job, "done", note or (END_UNSAID_SAY % reason),
                     end_reason=reason, end_note=note)

    def _note_after_advance(self, job: Job, *, ended: bool) -> None:
        """一次 `_advance` 返回之后要记的两件事（目录表第 1、6 行）。

        ⚠️ 顺序：**先现场，后结论** —— 「窗口还在不在」决定了下一步能不能走，
        「这一趟到头了」是这一跳的结果。
        ⚠️ `ended=False` 是**跑挂**那一支：那儿只记现场，**不许**记 `done`
        （「跑挂 ≠ 跑成」：快照里可能还留着上一次的 `end_reason`，照抄它就成了假话）。
        """
        try:
            values = dict(getattr(self._snapshot(job.job_id), "values", None) or {})
        except Exception as exc:               # noqa: BLE001 —— 旁路，但**响**
            traceback.print_exc()
            raw = "%s: %s" % (type(exc).__name__, exc)
            self.narrate(job, "state_unreadable", STATE_UNREADABLE_SAY % raw, error=raw)
            return
        self._note_window_died(job, values)
        self._note_shots_missing(job, values)     # 目录表第 6 行的**步拍**那一半
        self._note_narration_broken(job, values)  # 播报那条旁路自己坏了（修复轮 1）
        if ended:
            self._note_end(job, values)

    def _note_shots_missing(self, job: Job, values: dict) -> None:
        """目录表第 6 行的**步拍**那一半：图没拍成 → 时间线上一条（Task 5）。

        为什么要有这一条：第 6 行原先只有**闸拍**那一半（`_capture_pause` 的
        `shot_missing`），而探路里每一步自己也在拍（`shots_dir` 那条线）——
        那些拍不成的时候，原因今天只落在 `journey` 上（`step["shots_why"]` /
        `journey.shots_why`），**时间线上一个字都没有**：一张空白的步拍图框
        与「这一步没有图是因为窗口连不上」在页面上长得一模一样，而设计注
        §3.2 第 6 行明令不许让空图框冒充页面。

        两种来源都认（都是「图没留下」这件事的载体）—— 而**每一支的句子都不一样**
        （修复轮 1 的 Important-2：一致不该靠抹平两个事实来达成）：
          - `step["shots_why"]`：**那一步**的图没成（`.get` 读 —— 没这个字段的
            journey 一个字节都不受影响）→「这一步没留下图」；
          - `journey.shot_failures`：步拍**当场**记的那本只增的账（修复轮 2/3）——
            带 `when` 的 = 某一步的（同上那句）；不带 `when` 的（`_safe`，步拍自己的
            代码抛了）= 够不着哪一步 ⇒ 只说这一路，**不编「哪一步」**；
            `capped` 的 = 撞上 `MAX_KEPT_SHOTS`、**从这一步起不再拍**
            （修复轮 3：那不是「拍不成」，是**知道的**）⇒ 单独一句，话说清「不是漏了」。

        ⚠️ **四支一支都不能省**（修复轮 3 的正身）：`capped` 那一支原先只有
        `journey.notes` 一句话，而 `notes` **不上时间线** —— 于是上限一生效，
        从那一步起**既没有图、也没有账、也没有事件**，页面上就是一个没人解释的空图框。
        那正是设计注 §3.2 第 6 行（本片的行）要治的形状。

        ⚠️ **为什么扫的是那本只增的账，而不是 `journey.shots_why`**（修复轮 2 的正身）：
        那一格状态说的是「这条**路现在**坏着吗」（拍成了就清，Minor-5 要的就是它），
        而**残留状态天生会漏掉「发生又消失」的事实** —— 一张没成、下一张成了，
        于是时间线上一条都没有，读的人以为一切顺利，可那一刻确实出过事。
        这不是假设：修复轮 1 那行清空就是这么把一条真事件抹掉的（复审判的洞）。
        **那一刻的事只有当场记下来才留得住**，所以出口是只增的账、不是残留状态。

        ⚠️ **射程：只有最后一趟**（修复轮 1 的 Minor-4）。重探时**前几趟**的 Journey
        不进 state（`graph.explore` 只把最后一次的 `out["journey"] = journey` 留下），
        所以前几趟的步拍失败**这一条线看不见** —— 那是简报设计本身带来的形状，
        不是这里漏了。要让前几趟也说出来，得让图把每一趟的账都留下（不在这一片）。

        ⚠️ **同一句只报一次**（`job.shots_reported`）：拍照坏掉通常每一步都坏，
        而且这本账**只增** —— 不按「那句话」去重就会每推一步把同一句再报一遍。
        ⚠️ 读 journey 的那几行是**防御性**的（属性可能缺、步理论上可能不是字典），
        但 `self.narrate(...)` **不吞异常** —— 那与 `_note_window_died` / `_note_end`
        **一模一样**：`narrate` 抛是**编程错误**（形状写歪了），吞掉它就等于把
        「时间线上少一条」变成静默（说得准比说得好听重要）。
        """
        journey = values.get("journey")
        if journey is None:
            return
        whys = []                                 # [(why, step_no|None, 哪一支)]
        for step in list(getattr(journey, "steps", None) or []):
            try:
                why = str(step.get("shots_why") or "").strip()
            except AttributeError:                # 不是字典的步（不该有）—— 跳过它，不抛
                continue
            if why:
                whys.append((why, step.get("step_no"), "step"))
        for row in list(getattr(journey, "shot_failures", None) or []):
            try:
                why = str(row.get("why") or "").strip()
                # 哪一支（修复轮 3 起有三种）：知道是哪一步的 / 撞上上限不再拍的 /
                # 够不着哪一步的（`_safe`）。
                which = ("capped" if row.get("capped")
                         else ("step" if row.get("when") else "channel"))
            except AttributeError:                # 同上：不是字典的记录，跳过
                continue
            if why:
                whys.append((why, None, which))
        for why, step_no, which in whys:
            with job.lock:
                if why in job.shots_reported:
                    continue
                job.shots_reported.add(why)
            data = {"why": why}
            if isinstance(step_no, int):
                data["step_no"] = step_no
            # **四句话四个事实**（修复轮 1 的 Important-2 + 修复轮 3）：**这一步**没了 /
            # 探路里**整条步拍路**坏了（说不出哪一步）/ **到顶了不再拍**（知道的）/
            # 闸上**那一轮**那张没了（最后那一句在 `_capture_pause` 里）。
            # 四句必须读得出来是哪一件 —— 而**每一件都要说出来**（一句都不许省）。
            say = {"step": STEP_SHOT_MISSING_SAY,
                   "channel": STEP_SHOT_CHANNEL_SAY,
                   "capped": STEP_SHOT_CAPPED_SAY}[which] % why
            self.narrate(job, "shot_missing", say, **data)

    def _note_narration_broken(self, job: Job, values: dict) -> None:
        """**播报那条旁路自己坏了** → 时间线上一条（Task 5 修复轮 1，Important-1）。

        为什么要有它：`selftest.run` 的护栏把「回调抛了」记进 `Report.narrate_broken`
        —— 那是「**旁路坏掉不许带塌自测**」那一半；另一半「**没有静默的路径**」在那一刻
        还没兑现：自测那一层够不着时间线，报告要等到这里（`_advance` 返回、`report`
        已经在 state 里）才有人读得到。

        不加这一条会怎样：那个 `except` 就是**一条只有报告知道、页面上一个字都没有的
        异常** —— 而它**不属于**「时间线自己坏了所以记不上」那一类（时间线好得很，
        是回调自己抛了，比如 job 不在登记表里那种编程错误）。全局约束的字面是
        「**任何一个 `except`** 都要有一条对应的时间线事件」。

        去重：同一个原因只报一次（`job.narration_reported`）—— 报告会一直躺在 state 里，
        而每次 `_advance` 返回都会来读一遍。
        """
        report = values.get("report")
        for reason in list(getattr(report, "narrate_broken", None) or ()):
            why = str(reason or "").strip()
            if not why:
                continue
            with job.lock:
                if why in job.narration_reported:
                    continue
                job.narration_reported.add(why)
            self.narrate(job, "narration_broken", NARRATION_BROKEN_SAY % why, why=why)

    def _note_human_said(self, job: Job, job_id: str, body: ReplyRequest) -> None:
        """目录表第 8 行：人的原话 + **这句话去哪了**。

        ⚠️ 没有 note 的那次「继续」**也要有一条**（那是最常见的一次交互）：
        不记的话「人按了什么」在时间线上是空白。只是它不能说「这句话带进去了」（没有话）。
        ⚠️ 这句话的 `who` 是 `"you"` —— 页面靠它决定气泡长相。
        """
        note = str(body.note or "").strip()
        token, where = self._where_it_stopped(job_id)
        if note:
            say = "%s\n%s" % (note, HUMAN_SAID_THROUGH_SAY)
        else:
            say = HUMAN_SAID_PLAIN_SAY % where
        self.narrate(job, "human_said", say, who="you",
                     reply=str(body.action or ""), note=note, step=token)

    def _something_is_ahead(self) -> bool:
        """交上去的这一刻，**前面还有没有别的 run**（目录表第 3 行那一半）。

        两样事实：队列里压着活、或者正有一个 job 在跑（单飞：一次只有一个在窗口上）。
        ⚠️ 这是**快照**，不是保证 —— 判完队列还可能变。所以只在**真说得出「前面有东西」**
        时才说「排队等窗口」：漏说一句只是少一句话，说反了就是编话。
        """
        if not self._queue.empty():
            return True
        with self._jobs_lock:
            return any(j.status == RUNNING for j in self._jobs.values())

    # ── 排队与跑 ──────────────────────────────────────────────────
    def _ensure_worker(self):
        with self._worker_lock:
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._work, name="siteforge-worker",
                                                daemon=True)
                self._worker.start()

    def _submit(self, job: Job, payload) -> None:
        self._ensure_worker()
        self._queue.put((job, payload))

    def _work(self):
        while True:
            job, payload = self._queue.get()
            try:
                self._advance(job, payload)
            except BaseException as exc:             # noqa: BLE001 —— 工作线程**不许**死
                traceback.print_exc()
                self._note_escaped(job, exc)
            finally:
                self._queue.task_done()

    def _note_escaped(self, job: Job, exc: BaseException) -> None:
        """`_advance` 里**逃出来**的东西（最后一层网）—— 不许静默，更不许报「在跑」。

        今天能走到这儿的只有「`narrate` 自己抛了」（形状判据是设计成编程错误的），
        而那正好是**时间线坏掉**的那一刻 —— 所以这一层**不许**依赖 narrate 成功。
        两件事的次序就是照这个排的：

          ① **先把状态改对**：`/live` 再也不许说「在跑」。一句会一直说下去的假话
             （job 永远不会再动，因为没人推它了）比一条记不上的事件坏得多；
          ② 再试一次 narrate（记不上也已经在日志里了 —— 时间线此刻本来就是坏的）。

        ⚠️ 图要是已经到头（`DONE`）或者已经挂过（`FAILED`），这一层**不动它**
        —— 状态本来就是诚实的，没有谎要收；那种情况下少的是**时间线里的一条**，
        而时间线坏了的时候，任何一条事件都记不上（那条路只剩日志）。
        （登记表里只有 `queued` / `running` / `done` / `failed` 四种；「在等人」是从
        checkpoint 投影出来的，从来不写进登记表 —— 所以这里不必考虑它。）
        """
        raw = "%s: %s" % (type(exc).__name__, exc)
        with job.lock:
            if job.status not in (QUEUED, RUNNING):
                return                               # 状态是诚实的，别覆盖它
            job.status = FAILED
            job.error = raw
            job.say = ("这一步没跑成，停下了：%s\n"
                       "（任务没有交付任何东西 —— 产物目录里不会有它写的 py。）" % exc)
            said = job.say
        try:
            self.narrate(job, "failed", said, error=raw)
        except Exception:                            # noqa: BLE001 —— 网里的网：这一层不能再抛
            traceback.print_exc()

    def _advance(self, job: Job, payload) -> None:
        """把图往前推一步 —— **这是唯一一个 job 会动的地方**（单飞）。

        抛出来的东西一律变成 `failed`：一个跑挂的 job **绝不许**被读成跑成了
        （它 `result` 为 `None`，状态是 `failed`，人话里留着原始错误）。
        ⚠️ 「一律」的边界：`try` **只包**「说一句在跑 + 推图」那两句。except 里那条 narrate
        自己再抛、或者 `_capture_pause` / `_note_after_advance` 抛，会从 `_advance` 里
        **逃出去** —— 那一路归 `_work` 的最后一层网（`_note_escaped`），它先改状态再试一次记。
        """
        with job.lock:
            job.status = RUNNING
            job.say = RUNNING_SAY
        try:
            # 目录表第 4 行：拿到 job 就说「在跑」（与 `/job/{id}` 上那句**同一句**）。
            # ⚠️ 这一条必须在 `try` **里面**（复审 2026-09-18 实测）：写在 try 外面的话，
            #    它一抛就绕过下面那个 except —— 时间线一个字节没变、`job.status` 停在
            #    `running`、`/live` 一直报「在跑」，而全部后果只是 `_work` 的一行 print。
            #    「没有静默的路径」在这儿的正身就是：**记不下这一条，也要说它失败了**。
            self.narrate(job, "running", RUNNING_SAY)
            with self._check.lock:                   # 一个 saver 连接不被两个线程同时用
                out = job.graph.invoke(payload, self._cfg(job.job_id))
        except BaseException as exc:                 # noqa: BLE001 —— 包括 _Stop 之类的 BaseException
            with job.lock:
                job.status = FAILED
                job.error = "%s: %s" % (type(exc).__name__, exc)
                job.say = ("这一步没跑成，停下了：%s\n"
                           "（任务没有交付任何东西 —— 产物目录里不会有它写的 py。）" % exc)
                said, raw = job.say, job.error
            self._measure_after(job.job_id)          # 旁路：跑挂了也要留账（吞异常）
            # 目录表第 5 行：跑挂了**也要说**（原始错误进 `data`，人话不把它盖掉）。
            # ⚠️ 放在抓拍**之前**：抓拍最坏要等 20 秒，而「它挂了」人该立刻看见。
            self.narrate(job, "failed", said, error=raw)
            # 跑挂了那一屏更该看得见 —— 同一个出口、同一条纪律（旁路，不抛）。
            # ⚠️ 这条路上 `FAILED` **先**落（人该立刻看见它挂了），图随后才到 ——
            #    所以这一刻的快照里可能还没有这一轮的 note，下一次读就有。
            self._capture_pause(job)
            self._note_after_advance(job, ended=False)   # 只记现场（跑挂 ≠ 跑成）
            return
        self._measure_after(job.job_id)              # 旁路：记一行时间线 + 汇总 baseline
        # ⚠️ 闸拍放在**这里**：`invoke` 之外（写锁已经放开）、`job.lock` 也没拿着 ——
        #    拍照那 0.2 秒（最坏 20 秒）里不该按着整个服务（简报点名的第一条）。
        self._capture_pause(job)
        with job.lock:
            # 登记表只补「正在跑 / 跑挂了」这两件 checkpoint 答不了的事。
            # 「停在等人」还是「跑到头了」、以及**为什么停**，一律**从 checkpoint 投影**
            # （状态的唯一真源在 saver 里，R-19）—— 所以这里不再存一份 `say`。
            job.status = DONE
            job.say = ""
        self._note_after_advance(job, ended=True)    # 目录表第 1、6 行

    # ── 服务重启之后：从 checkpoint 把 job 捡回来（R-19）───────────────
    def _recover(self, job_id: str) -> Optional[Job]:
        """登记表里没有这个 job，但 checkpoint 里有 → 就地从状态里把它捡回来。

        这是 R-19 在服务侧的正身：**状态住在 saver 里，不在进程里的对象里**。
        没有它，「中断之后恢复不了」只是从图那一层挪到了服务这一层 ——
        服务一重启，人就没法让那次运行接着走了。

        ⚠️ 有一件东西 checkpoint 里**没有**：调用方在开场白里说的那个 `set_viewport`
        （那是服务这一层的开关，不是图的状态）。所以捡回来的 job 用的是
        「**这个部署有没有窗口层**」—— 这是安全的：给上那根线只会让第 4 遍扰动**真跑**，
        永远不可能变成「静默跳过」（R-5 禁止的是跳过，不是执行）。
        """
        snap = self._snapshot(job_id)
        values = dict(getattr(snap, "values", None) or {})
        if not values:
            return None
        keep = ("url", "goal", "mode", "success_text", "evidence", "site", "ws_url",
                "form_file", "env", "platform", "out_dir", "allow_skips", "entry_url")
        brief = {k: values[k] for k in keep if values.get(k) is not None}
        brief["set_viewport"] = bool(self._window is not None
                                     and hasattr(self._window, "set_viewport"))
        job = Job(job_id=job_id, brief=brief, status=DONE, say=RECOVERED_SAY, recovered=True,
                  created_at=datetime.datetime.now().astimezone().isoformat(timespec="seconds"))
        job.graph = self._build_graph(brief, job_id)
        with self._jobs_lock:
            self._jobs.setdefault(job_id, job)
            landed = self._jobs[job_id]
        if landed is job:
            # 目录表第 7 行：捡回来的 job 要**说** —— `job.say` 下一次 `_advance` 就把它冲掉了
            # （P9），而「这个任务是捡回来的」是**系统已经知道**的一件事。
            # ⚠️ 只记在**真的落进登记表**那一个上（两个线程同时捡的时候，另一个那份时间线
            #    没人看得到 —— 记在它上面等于没记）。
            self.narrate(landed, "recovered", RECOVERED_SAY)
        return landed

    # ── 投影：把 checkpoint 变成给人看的那个东西 ─────────────────────
    def _view(self, job_id: str) -> dict:
        job = self._jobs.get(job_id)
        if job is not None:
            status, say, error = job.snapshot_for_view()
            if status == FAILED:
                return {"job_id": job_id, "status": FAILED, "delivered": False,
                        "say": say or "这一步没跑成，停下了。", "error": error,
                        "result": None, "gate": None, "site": job.brief.get("site")}
            if status in (QUEUED, RUNNING):
                return {"job_id": job_id, "status": status, "delivered": False,
                        "say": say or ("排队等窗口（前面还有别的 run 在用）" if status == QUEUED
                                       else "在跑。"),
                        "error": None, "result": None, "gate": None,
                        "site": job.brief.get("site")}
        snap = self._snapshot(job_id)
        values = dict(getattr(snap, "values", None) or {})
        if not values and job is None:
            raise KeyError(job_id)          # 登记表没有、checkpoint 也没有 = 没这个任务
        return self._project(job_id, snap)

    def _project(self, job_id: str, snap) -> dict:
        values = dict(getattr(snap, "values", None) or {})
        gates = [g for g in self._interrupts(snap)]
        base = {"job_id": job_id, "delivered": False, "error": None,
                "site": values.get("site"), "gate": None, "result": None}

        if gates:
            value = gates[0].value if hasattr(gates[0], "value") else gates[0]
            value = dict(value or {})
            step = str(value.get("step") or "")
            return {**base, "status": WAITING,
                    "say": str(value.get("say") or "停下来了，在等你一句话。"),
                    "gate": {"step": step, "step_say": STEP_SAY.get(step, step),
                             "ask": value.get("say"), "facts": value.get("facts") or {},
                             "can": value.get("can") or []}}

        end_reason = str(values.get("end_reason") or "")
        py_path = values.get("py_path") if end_reason == END_DELIVERED else None
        delivered = bool(end_reason == END_DELIVERED and py_path
                         and pathlib.Path(str(py_path)).is_file())
        report = values.get("report")
        note = str(values.get("end_note") or "").strip()
        return {**base, "status": DONE, "delivered": delivered,
                "say": note or ("这一趟跑完了，但没说清是怎么结束的（state 里没有 end_reason）—— "
                                "这本身就不正常，别把它当成功。"),
                "result": {"end_reason": end_reason, "delivered": delivered,
                           "py_path": str(py_path) if py_path else None,
                           "site": values.get("site"),
                           "visits": list(values.get("visits") or []),
                           "say": note,
                           "selftest": self._report_brief(report)}}

    @staticmethod
    def _interrupts(snap) -> list:
        """中断可能挂在 `snapshot.interrupts` 上，也可能在 `tasks[*].interrupts` 里。

        两处都看 —— langgraph 的版本间这块换过位置，只认一处会在升级时**静默**变成
        「没有中断」（那会把「在等人」读成「跑完了」）。
        """
        out = list(getattr(snap, "interrupts", None) or ())
        if out:
            return out
        for task in (getattr(snap, "tasks", None) or ()):
            out.extend(list(getattr(task, "interrupts", None) or ()))
        return out

    @staticmethod
    def _report_brief(report) -> Optional[dict]:
        if report is None:
            return None
        runs = [r.as_dict() if hasattr(r, "as_dict") else dict(r)
                for r in (getattr(report, "runs", None) or ())]
        return {"passed": bool(getattr(report, "passed", False)),
                "say": report.summary() if hasattr(report, "summary") else "",
                "runs": runs}

    # ── `/live`：页面的**唯一**数据源（Task 4 先出骨架）────────────────
    def live(self, job_id: str) -> dict:
        """`GET /job/{id}/live` 的正文（设计注 §8.2；这一版是**骨架**）。

        为什么合成一份一次给完（§8.3 第 1 条）：页面只有一个 `job_id` + 一个定时器，
        取一份 JSON 就画完。分成几个端点会让「时间线说在跑、卡片还显示在等人」这种
        **自相矛盾**的画面变得可能。
        **字节不进 JSON**：图走 `/job/{id}/shot/{name}`（文件名带轮号、内容永不变）。

        状态与人话**只有一份口径**：`status`/`say`/`delivered` 直接取 `_view`（不编话）。
        时间线**不持久**（§3.5）：不在登记表里（重启过）或者是捡回来的 → `note` 明说。

        **轮次与闸**（Task 6）：`rounds` / `gate` / `stage` 走 `rounds.project` 那一跳
        （`agent/rounds.py`，纯函数 —— checkpoint + 闸拍清单 + 接线信息 → 一屏卡片）。
        这一层只做三件事：把输入凑齐、把投影回来的那几格填上、把该说的话并进 `note`。
        ⚠️ **闸只在 `waiting` 时非 null**（跑着/排队/到头了都没有闸）：那一格是
        页面「还能不能按」的判据，露着头就是一个按钮（`rounds.project` 里再兜一次底）。

        ⚠️ **状态读不回来**时的两条口径（**不一样**，因为读发生的地方不一样）：
          - 跑着 / 排队 / 跑挂那三档：`_view` 不读 state ⇒ `_live_facts` 兜得住 ⇒
            **200 + 一句人话**（`LIVE_STATE_UNREADABLE_SAY` 进 `note`）；
          - **停在闸上 / 到头了**那两档：`_view` 自己就要读 state（在 `_live_facts` **之前**）
            ⇒ 兜不住 ⇒ **503 + 一句人话**（`LIVE_STATE_UNREADABLE_503`，不是裸 500）。
            「没这个 job」那条路照旧 `KeyError` → 404。
        """
        try:
            view = self._view(job_id)             # 没这个 job 就 KeyError → 路由转 404
        except KeyError:
            raise
        except Exception as exc:                  # noqa: BLE001 —— 读不回状态：响，但说人话
            traceback.print_exc()
            raw = "%s: %s" % (type(exc).__name__, exc)
            raise HTTPException(status_code=503, detail=LIVE_STATE_UNREADABLE_503 % raw)
        job = self._jobs.get(job_id)
        timeline = job.timeline if job is not None else None
        shown = timeline.all() if timeline is not None else []
        note = RESTART_NOTE if (timeline is None or job.recovered) else ""
        truncated = bool(timeline is not None
                         and (timeline.dropped() or len(timeline) > len(shown)))
        events_note = self._truncated_say(timeline, len(shown)) if truncated else ""
        token, where = self._where_it_stopped(job_id)

        # 轮到闸拍清单与 state（读不回来也要说清 —— 见 `_live_facts`）
        pauses, values, facts_note = self._live_facts(job, job_id)
        proj = rounds.project(
            values, view.get("gate"), job_id=job_id, status=view["status"],
            say=str(view.get("say") or ""), delivered=bool(view.get("delivered")),
            pauses=pauses, window=self.window_public(), shots_note=self.shots_note(),
            stage=token)
        note = "\n".join(x for x in (note, events_note, proj["rounds_note"], facts_note) if x)
        return {
            "job_id": job_id,
            "status": view["status"],
            "say": str(view.get("say") or ""),
            "delivered": bool(view.get("delivered")),
            #: 它现在/正要做的那个节点：`stage` 是节点名（页面按它对按钮/文案），
            #: `stage_say` 是它的**人话**（D16：给运营看的字段是人话；与 `/job/{id}`
            #: 那道闸的 `step` / `step_say` 同一个形状）。
            "stage": token,
            "stage_say": where,
            "note": note,
            "events": shown,                      # 旧 → 新；最多最近 500 条（`Timeline.all` 的默认）
            #: 闸口投影（**只在 `waiting` 时非 null**）。原话照抄 `_view` 算好的那道闸，
            #: 另加 `revisable`（`lint`/`selftest`/`deliver` 三道闸上「打回」才有那个意思）。
            "gate": proj["gate"],
            #: 常开输入的语义（Task 8 定、Task 9 扩展）。**恒 queue**：这一版没有 `/say`。
            "input": {"mode": "queue", "queued": []},
            #: 「停」这条路这一版**还没有** —— 只说「没请求停」（真话）。
            #: 不许在这儿编一句「几秒内就会停」：那是 Task 8 的事，现在写上去就是假话。
            "stop": {"requested": False},
            "shots_note": proj["shots_note"],
            "window": proj["window"],
            #: 轮次卡片（Task 6 从 checkpoint 投影）：**旧 → 新**，`rounds[n-1]` 就是第 n 轮。
            "rounds": proj["rounds"],
            "truncated": bool(truncated or proj["truncated"]),
        }

    def _live_facts(self, job: Optional[Job], job_id: str) -> tuple:
        """`/live` 要的两样输入 + 一句人话：闸拍清单、checkpoint 的 values。

        闸拍清单**只从登记表来**（`Job.shot_notes`，一轮一条）—— 不在登记表里的 job
        就没有它的轮次（那是 R12：轮号是 process-local 的，`note` 里明说）。

        ⚠️ 状态读不回来时**在这一层不抛**（`/live` 是 GET，抛出去就是整页 500），也**不静默**：
        原因随响应回到页面上（`LIVE_STATE_UNREADABLE_SAY`）+ 日志里一份 traceback。
        **GET 不许写时间线**（「读不许写」），所以这件事的落点是那句话，不是一条事件。

        ⚠️ **这一层的射程只有三档**（跑着 / 排队 / 跑挂）：那三档 `_view` 不读 state，
        所以读失败到得了这里。**停在闸上 / 到头了**那两档 `_view` 自己先读、先抛 ——
        那一支由 `Service.live` 兜（503 + 一句人话）。别把这条读成「`/live` 永远不抛」。
        """
        pauses: list = []
        if job is not None:
            with job.lock:
                pauses = [dict(x) for x in job.shot_notes]
        try:
            snap = self._snapshot(job_id)
        except Exception as exc:                 # noqa: BLE001 —— 读不回来就说读不回来
            traceback.print_exc()
            raw = "%s: %s" % (type(exc).__name__, exc)
            return pauses, {}, LIVE_STATE_UNREADABLE_SAY % raw
        return pauses, dict(getattr(snap, "values", None) or {}), ""

    def runs(self) -> dict:
        """`GET /runs` 的正文（设计注 §8.1）：**能挑运行的最小列表**。

        一行 = 一个 job：`{job_id, site, status, say, created_at, rounds, delivered}`。
        `rounds` 是**到过几道闸**（`rounds.count(job.shot_notes, status)`）—— 与 `/live`
        里卡片的张数**同源**（同一份清单、同一个算法）。⚠️ **跑到头了的 job 不算最后那张图**
        （`_capture_pause` 在跑完/跑挂那一次也拍 —— 那一张不是某一轮的闸拍），
        所以「6 道闸」的 job 这里就是 **6**，不是 7。
        ⚠️ 别把它读成探路的模型轮数（`journey.rounds`）：**那是另一个事实**
        （`agent/rounds.py` 的模块 docstring 里那张表）。

        顺序：**最近的在前**（页面左边那一栏据此长）。
        `note`：空列表时那句话说清「这个列表是 process-local 的」—— 不说的话，
        人会把「空」读成「什么都没提交过」（§3.5 同一条纪律）。
        """
        with self._jobs_lock:
            jobs = list(self._jobs.values())
        jobs.sort(key=lambda j: j.created_at, reverse=True)
        rows = []
        for job in jobs:
            view = self._view(job.job_id)         # 登记表里的 job 读得回来（读不回来是 500，不是少一行）
            rows.append({
                "job_id": job.job_id,
                "site": (job.brief or {}).get("site"),
                "status": view["status"],
                "say": str(view.get("say") or ""),
                "created_at": job.created_at,
                #: 「到过几道闸」—— 轮数**只有这一个算法**（`rounds.count`）；
                #: 到头了的那两档要把「最后那张图」去掉（它不是一轮）
                "rounds": rounds.count(job.shot_notes, view["status"]),
                "delivered": bool(view.get("delivered")),
            })
        return {"note": "" if rows else NO_RUNS_SAY, "runs": rows}

    @staticmethod
    def _truncated_say(timeline, shown: int) -> str:
        """`truncated` 为真时**说清**丢了什么/回了多少（设计注 §8.2：「并说明」）。"""
        parts = []
        dropped = timeline.dropped()
        if dropped:
            parts.append("最早那 %d 条已经丢掉了（时间线上限 %d 条）。"
                         % (dropped, events.MAX_EVENTS))
        rest = len(timeline) - shown
        if rest > 0:
            parts.append("这一次只回最近 %d 条（前面还有 %d 条没回）。" % (shown, rest))
        return "时间线太长：" + "".join(parts)

    # ── 输入检查（**免费的那些**：在烧掉一次探路之前）────────────────────
    def _intake_problems(self, body: RunRequest) -> list:
        problems = []
        if not (body.url or "").strip():
            problems.append("还没说**是哪个站点**：`url`。")
        if not (body.goal or body.evidence or "").strip():
            problems.append("还没说**这次要做什么**：`goal`（或者 fix 模式下的失败证据 `evidence`）。")
        if not (body.success_text or "").strip():
            problems.append("还没说**什么算成功**：`success_text` —— 走通之后页面上会出现哪段文字。"
                            "这一条只有人知道，猜不得（猜出来的成功判据会让产物「跑到底再报成功」）。")
        if body.allow_skips is not None:
            if not body.allow_skips:
                problems.append("`allow_skips` 是空的（`[]`）—— 空列表没有意义：它和「没给」在图的边界"
                                "**分不开**，于是你以为「什么也不许跳」，实际会用默认（只允许跳 country）。"
                                "要么别给这个字段（用默认），要么把要跳的那几遍列出来：%s。"
                                % "、".join(selftest.RUN_NAMES))
            else:
                unknown = [n for n in body.allow_skips if n not in selftest.RUN_NAMES]
                if unknown:
                    problems.append("`allow_skips` 里有不认识的遍：%s。认的是这五个：%s。"
                                    "（写错的名字如果拖到自测那一步才炸，会先白烧掉一次探路 —— "
                                    "真窗口 + 一次模型跑。）"
                                    % ("、".join(unknown), "、".join(selftest.RUN_NAMES)))
        if body.expects is not None:
            # 契约 §四：`expect` 由运营写、**形状是封闭的**。免费的那一道闸 —— 形状错了
            # 在这儿响，而不是等到某一步判不出来时才发现「那格当初填的是什么鬼」。
            for i, item in enumerate(body.expects):
                bad = expect_problem(item)
                if bad:
                    problems.append("`expects` 第 %d 项不合形状：%s" % (i + 1, bad))
        if body.set_viewport:
            can_do = (self._window is not None and hasattr(self._window, "set_viewport"))
            allowed = list(body.allow_skips or ())
            if not can_do and "viewport" not in allowed:
                problems.append("载荷里要了 `set_viewport`（第 4 遍换窗口大小），但这个部署**没有接窗口层** —— "
                                "换不了。两条路，都摆在明面上：① 把窗口层接上（`BIT_WORKER_IP`/`BIT_ID`），"
                                "那这一遍就真跑；② 明确放弃它：把 `\"viewport\"` 写进 `allow_skips` —— "
                                "交出来的产物会带着「这一类失败这次**没验到**」。"
                                "服务不会替你默认放过它（R-5）。")
        return problems

    # ── 三个动作 ──────────────────────────────────────────────────
    def start(self, body: RunRequest) -> dict:
        problems = self._intake_problems(body)
        if problems:
            raise HTTPException(status_code=400, detail=self._problems_say(problems))

        brief = body.model_dump(exclude_none=True)
        brief.setdefault("out_dir", self._out_dir)
        brief["success_text"] = body.success_text
        self._clean_window_for_explore(brief)     # R-F1 的另一半：**探路也要干净会话**
        job_id = "job-%s" % uuid.uuid4().hex[:12]
        job = Job(job_id=job_id, brief=brief, status=QUEUED, say=SUBMITTED_SAY,
                  created_at=datetime.datetime.now().astimezone().isoformat(timespec="seconds"))
        with self._jobs_lock:
            self._jobs[job_id] = job
        try:
            job.graph = self._build_graph(brief, job_id)
        except BaseException as exc:                 # 拼不起来（saver 连不上之类）
            with job.lock:
                job.status = FAILED
                job.error = "%s: %s" % (type(exc).__name__, exc)
                job.say = ("起不来：%s\n（这一趟**没有**开浏览器、也没有写任何产物。）" % exc)
                said, raw = job.say, job.error
            # 目录表第 5 行说的是 `_advance` 的 except；**这一条**（图都拼不起来）
            # 同样是「跑挂」，同样不许静默 —— 时间线上得有一条，否则它像没存在过。
            self.narrate(job, "failed", said, error=raw)
            return self._view(job_id)
        # 窗口时间线探针：从这一刻起盯住「窗口是哪个进程」（Task 1 / G3）。
        # **只在能给 PID 的窗口层上起** —— 桩窗口没有 PID，那样的线判不出「重开了几次」。
        self._active_job = job_id
        self._start_window_probe()
        # 目录表第 3 行：先记「收到」，再（前面有东西时）记「排队等窗口」。
        # ⚠️ **先说后交**是有意的：交给队列之后工作线程可能立刻喊「在跑」，顺序就反了。
        self.narrate(job, "submitted", SUBMITTED_SAY)
        if self._something_is_ahead():
            self.narrate(job, "queued", QUEUED_SAY)
        self._submit(job, self._payload(brief))
        return self._view(job_id)

    @staticmethod
    def _payload(brief: dict) -> dict:
        """开场白 → 图认得的那几个键（服务这一层的开关，比如 `set_viewport`，不往里塞）。"""
        keep = ("url", "goal", "mode", "success_text", "evidence", "site", "ws_url",
                "form_file", "env", "platform", "out_dir", "allow_skips", "entry_url")
        return {k: brief[k] for k in keep if k in brief}

    def reply(self, job_id: str, body: ReplyRequest) -> dict:
        job = self._jobs.get(job_id) or self._recover(job_id)   # 重启过就从 checkpoint 捡回来
        if job is None:
            raise KeyError(job_id)
        view = self._view(job_id)
        if view["status"] != WAITING:
            raise HTTPException(status_code=409, detail=
                                "这个任务没在等人（它现在是「%s」）—— 没有要你回的话。"
                                "跑到头了就另起一个任务；要它接着走，先看 `say` 里说了什么。"
                                % self._status_say(view["status"]))

        # P6：窗口只活几分钟，而人可能过了十分钟才回话。先问一句窗口还在不在 ——
        # 不先问的后果是自测拿着一个死窗口跑，报告把「连不上」记成**产物的问题**。
        values = dict((self._snapshot(job_id).values or {}))
        dead = self._window_is_gone(values.get("ws_url"))
        if dead:
            # 目录表第 1 行的另一处落点：这件事是**在这儿**被发现的（人回了话，服务才去查），
            # 那就得**在这儿**说 —— 否则「窗口没了」只有那个 409 知道，
            # 而坐在页面前面的人只看到「回话被拒」：时间线上一片安静（那正是这一片要治的）。
            self._note_window_died(job, values, dead=True)
            raise HTTPException(status_code=409, detail=
                                "先别接着走：这个窗口**已经不在了**（§4.6：Bit 窗口只活几分钟，"
                                "而这一步可能跑很久）。现在接着走的话，自测会拿着一个死窗口跑，"
                                "报告会把「连不上」说成产物的问题 —— 那是误导。\n"
                                "重开一个窗口，再从这里接着走（账本和这一步的进展都还在）：\n"
                                "    POST /job/%s/reopen  {\"ws_url\": \"<新窗口>\"}" % job_id)
        # ⚠️ 先把 job 从「停住」挪开，**再**交下去。
        # 不挪的话：`_view` 会从 checkpoint 投影（那个中断还在）→ 响应说「在等人」+
        # 旧那道闸 → 调用方再回一次话 → 那一句 resume 落到**下一道闸**上 →
        # **一步在没人看着的情况下跑掉了**（§6.2/D16 的核心承诺）。
        with job.lock:
            job.status = RUNNING
            job.say = "收到你的话，接着跑（下一个要你拿主意的地方会再停下来）。"
        # 目录表第 8 行：人的原话与它去哪了（**交下去之前**记，否则工作线程先喊「在跑」）。
        self._note_human_said(job, job_id, body)
        self._submit(job, Command(resume={"action": body.action, "note": body.note}))
        return self._view(job_id)

    def reopen(self, job_id: str, body: ReopenRequest) -> dict:
        """窗口没了 → 换一个新窗口，**从 checkpoint 接着跑**（P6）。

        接得住的是**窗口那一支**，两步：
        - **自测**（`no_window`）：探路那一段**不重来**（账本在 checkpoint 里）；
        - **探路**（`explore_unfinished` / `paused` / `window_gone`，或者探路里**炸了**
          的那个 `failed`）：探路重跑，但**不是从头再探**（Task 6 / §1.9）——
          `reopen` 从**最新那本账**切出可重放的前缀（`replayable_prefix`，R1–R4），
          第二次进 `explore` 时先照它走回去（**0 模型调用**），再从断点接着探。
          那句人话必须说清这件事（重放了几步、停在哪、接着探）—— 它意味着又一次真窗口，
          不许含糊过去。
        """
        job = self._jobs.get(job_id) or self._recover(job_id)
        if job is None:
            raise KeyError(job_id)
        view = self._view(job_id)
        point = self._resume_point(job_id, view)
        if point is None:
            raise HTTPException(status_code=409, detail=self._cannot_reopen_say(job_id, view))
        if not (body.ws_url or "").strip():
            raise HTTPException(status_code=400, detail="`ws_url` 是空的 —— 重开窗口要给出新窗口那串。")
        if body.set_viewport and not (self._window is not None
                                      and hasattr(self._window, "set_viewport")):
            raise HTTPException(status_code=400, detail=(
                "要接窗口层那根线（`set_viewport`），但这个部署没有窗口层 —— 换不了窗口大小。"
                "要么把窗口层接上（`BIT_WORKER_IP`/`BIT_ID`），要么重开时别要它"
                "（那就还是老样子：没人给这根线，图停在自测那一步点名）。"))

        resume_from, step = point
        # 把新窗口放回状态，并**把上一次那个诚实的停止收掉**（它是上一次的结论，不是这一次的）。
        # `as_node=<上一步>`：图接着跑的正是**停下来的那一步** ——
        # 自测接在 lint 上（探路与起草都不重来）、探路接在 intake 上（探路重跑）。
        patch = {"ws_url": body.ws_url, "end_reason": "", "end_note": ""}
        if body.entry_url:
            patch["entry_url"] = body.entry_url
        say = "窗口重开了，从上次停下的地方接着跑（探路那一段不重来）。"
        if step == "explore":
            patch["explore_say"] = ""          # 上一趟探路的说法收掉（新的探路会写新的）
            patch["end_note"] = ""
            # **接着走，不是从头再探**（§1.9）：从最新那本账切出可重放的前缀。
            # ⚠️ 这两样**无条件写**（切不出来就是 `None`）：上一趟 `reopen` 留下的前缀
            # 不许留在状态里被这一次复用 —— 那是**别的窗口**上的账。
            prefix, note = self._resume_for(job_id, job.brief)
            patch["resume_from"] = list(prefix) or None
            patch["resume_note"] = note
            # **把它前面那几趟的账读回来**（复审 ⑥）：探路抛异常时节点不返回，
            # `explore_spent` 那一整份都不会进 checkpoint —— 而盘上记着。
            # 与状态里那份**逐键取更紧的**（两边都是「花了多少」的下界）。
            patch["explore_spent"] = _tighter(
                dict(getattr(self._snapshot(job_id), "values", None) or {}).get("explore_spent"),
                self._spent_from_attempts(job_id))
            say = self._reopen_explore_say(prefix, note)
        with job.lock:
            job.brief.update({"ws_url": body.ws_url})
            job.brief.pop("_failed_at", None)
            if body.entry_url:
                job.brief["entry_url"] = body.entry_url
            # ⚠️ **窗口一换就必须重拼那张图** —— 不是「只有新打开 set_viewport 时才要」。
            #
            # `Deps.explore`（`_explore_for`）与窗口层那根线（`_viewport_cb`）都是**拼图时**
            # 对 `brief["ws_url"]` 闭包出来的。只把新 url 写进 brief / checkpoint 而不重拼，
            # 得到的是：`reopen` 回 200、状态里写着新窗口，而探路手里那根线**还指着旧窗口** ——
            # 续跑会又一次死在旧窗口上，出路只剩「重新开一个任务」= 整轮重来（P6 明令不许）。
            # 探针那根线同理：不重拼它就一直量**旧**窗口（要么以莫名其妙的理由判没做成，
            # 要么旧窗口刚好是那个尺寸时**判成做成了**）。
            #
            # 换一张图接着跑是安全的：状态在 checkpoint 里（Test Graph 那条
            # 「另一个图对象接着跑」的用例钉的就是这件事）。
            job.brief["set_viewport"] = bool(job.brief.get("set_viewport") or body.set_viewport)
            job.graph = self._build_graph(job.brief, job_id)
            job.status = RUNNING
            job.error = None                    # 上一次那个失败不再是这个 job 的现状
            job.say = say
            # 换了一个窗口 = 上一趟那个「窗口没了」不再适用（新的那个也会死，那是**新**事实）
            job.window_dead = False
        with self._check.lock:
            job.graph.update_state(self._cfg(job_id), patch, as_node=resume_from)
        # 目录表第 2 行：**照搬它现有那句 `say`**（探路重跑 / 从断点接着跑，两句不同）。
        # ⚠️ 位置有讲究：放在 `update_state` **之后**（状态真写进去了才算「重开了」——
        #    写失败还报一句「重开了」，那句话就是假的），放在 `_submit` **之前**
        #    （交下去之后工作线程会立刻喊「在跑」）。
        self.narrate(job, "window_reopened", say, where=step, ws_url=str(body.ws_url))
        self._submit(job, None)                      # None = 「接着跑」，不是新的输入
        return self._view(job_id)

    def _resume_for(self, job_id: str, brief: dict) -> tuple:
        """能不能**接着走**：从**最新那本账**切出可重放的前缀（§1.9；切法归 Task 5 的 R1–R4）。

        返回 `(前缀, 人话)`。前缀为空时，人话**说清为什么** —— 不静默。

        ⚠️ **读不出来 = 没得重放，不是异常**（R-19 的判据）：账本不在、那是本空账、
        缺 `success_text`（`replayable_prefix` 会抛）、盘读不动 —— 一律回到「从入口重探」
        （也就是**今天那条路**，一个字节不差），只是**说出来**。账本在这条链上是**旁路**：
        它坏掉不许把续跑带塌（与 `_journal_for` 那条规矩同源）。

        ⚠️ 那些**读不出来的行**（被杀在写一半留下的半行）不算致命：`read` 把好行照读，
        而重放自己那四条判据（尤其 R2「这一步之后得有做成的观察」）本来就会把
        「尾巴上少了观察的动作」挡在边界之外 —— 所以照切，只在人话里说一句。
        """
        try:
            books = journal.attempts(self._explore_root, job_id)
            if not books:
                return [], "这个 job 还没有账本（一趟都没记上）"
            rows, skipped = journal.read(books[-1])
            if not rows:
                return [], "最新那本账是空的（%s）" % books[-1].name
            # R4 要「走过的页面地址」：最后一趟的 `pages` 在 checkpoint 里（探路炸掉那一支没有）。
            journey = dict(getattr(self._snapshot(job_id), "values", None) or {}).get("journey")
            prefix, why = browser_agent.replayable_prefix(
                rows, brief.get("success_text") or "",
                entry_url=str(brief.get("url") or ""),
                pages=list(getattr(journey, "pages", None) or []))
            if skipped:
                why += (" 另外：这本账里有 %d 行读不出来（多半是被杀在写一半的地方），"
                        "能重放的是**读得出来的那一段**。" % len(skipped))
            return list(prefix), why
        except Exception as exc:                        # noqa: BLE001 —— 旁路，见 docstring
            return [], ("账本读不出来（%s: %s）—— 这一次没有前缀可重放，探路从入口重新开始。"
                        % (type(exc).__name__, exc))

    def _spent_from_attempts(self, job_id: str) -> dict:
        """从 `attempts.jsonl` 把**已经记下来的**消耗读回来（`{steps, rounds, attempts}`）。

        为什么要有它（复审 ⑥ / 修复轮 2）：探路**抛异常**时节点不返回 ⇒ `explore_spent`
        （含那一次节点执行里**前面已经跑完的那几趟**）**不会进 checkpoint** ——
        而 `reopen`（**没有次数上限**）正是下一步，它按一份**满预算**再探一遍。
        那些账**就在盘上**：`attempts.jsonl` 一次尝试一行，`steps` / `rounds` 都在
        （正常收场那几趟一定记着；「连账本都没生成」的那种本来就 0 步）。

        ⚠️ **口径差异如实写着**：账上的 `steps` 把**重放的步**也算进去了
        （`_note_attempt` 记的是账本长度）⇒ 它只会**偏大** ⇒ 预算更**紧**
        （方向是对的：与 `_round_spends` 那条「不知道的一律按花算」同族）；
        而 `rounds` 里 `None`（没量到）在这里按 0 算 ⇒ 那一项偏松。取**逐键 max**
        （与状态里那份比），所以两边都不会被对方放松。

        ⚠️ **这一格 `rounds` 是「探路的模型轮数」**（`journey.rounds` 那条线），
        **不是**运营看见的那个「轮」（闸拍轮次 = 到过几道闸，投影在 `/live.rounds` /
        `/runs[].rounds`，算法在 `agent/rounds.py` 的 `count` —— ⚠️ **不是** `Job.pauses`：
        那是**闸拍张数**，跑完/跑挂那一次也拍 ⇒ 到头了那两档它 = 轮数+1）。
        """
        out = {"steps": 0, "rounds": 0, "attempts": 0}
        try:
            rows = measure.read_rows(self._explore_dir(job_id) / "attempts.jsonl")
        except Exception:                                   # noqa: BLE001 —— 旁路
            return out
        for row in rows:
            if not isinstance(row, dict) or row.get("_corrupt"):
                continue
            out["steps"] += int(row.get("steps") or 0)
            out["rounds"] += int(row.get("rounds") or 0)
            out["attempts"] += 1
        return out

    @staticmethod
    def _reopen_explore_say(prefix: list, note: str) -> str:
        """`reopen` 之后那句人话（§1.9）：**说清重放了几步、停在哪、接着探**。

        ⚠️ 原先那句是「探路要从头再走一遍」—— 在新形状下它是**假话**（账本还在，
        能接着走），而且它把代价说大了（人以为要再花一整趟）。
        """
        if prefix:
            return ("窗口重开了，探路**接着上一趟走**：先照账本重放 %d 行"
                    "（其中 %d 个动作，0 模型调用），再从断点接着探 —— 不用从头再探一遍。"
                    "停在这儿：%s" % (len(prefix),
                                      len(browser_agent.replay_actions(prefix)),
                                      note or "（没说明为什么停在这儿）"))
        return ("窗口重开了，探路从入口重新开始（**这一次没有可重放的前缀**：%s）—— "
                "你之前说的话和开场白都还在，图接着往下跑。"
                % (note or "账本里没有读得出的段"))

    def _resume_point(self, job_id: str, view: dict) -> Optional[tuple]:
        """这个 job 能不能用「重开窗口 + 接着跑」接住？能就回 `(接在哪一步, 要重跑哪一步)`。

        两种「停」，判据不同（这是这一条最容易写错的地方）：

        - **DONE**：图是**在那一步里**停下的 —— 那一步的结论已经落进状态了
          （`visits[-1]` 就是它）→ 接在它的**上一步**上，于是接着跑的正是它。
        - **FAILED**：图是**在那一步的下一跳里**炸的（那一步落下了、下一步没有）
          → 接在**最后落下的那一步**上。窗口在探路途中断开就是这个形状：
          `visits[-1] == "intake"`，炸掉的是 `explore`。

        两种情况都只认**窗口那一支**（`WINDOW_STEPS` 里那两步）。
        """
        values = dict(getattr(self._snapshot(job_id), "values", None) or {})
        visits = list(values.get("visits") or [])
        last = str(visits[-1]) if visits else ""
        failed = view["status"] == FAILED
        if failed:
            step = _AFTER.get(last)             # 在「上一步」的下一跳里炸的
            return (last, step) if step else None
        if view["status"] != DONE:
            return None
        reason = str(values.get("end_reason") or "")
        if last in WINDOW_STEPS and reason in WINDOW_END_REASONS.get(last, ()):
            return (WINDOW_STEPS[last], last)
        return None

    # ── 人话 ──────────────────────────────────────────────────────
    def _window_is_gone(self, ws_url) -> bool:
        """窗口死了吗。**只有明确说死了才拦**（问不出来一律当不知道 —— 不许误杀）。"""
        if not ws_url or self._window is None or not hasattr(self._window, "alive"):
            return False
        return self._window.alive() is False

    def _cannot_reopen_say(self, job_id: str, view: dict) -> str:
        """接不住时说的**人话**：这个任务现在什么形状、以及「重开窗口」接得住的是哪两种。"""
        can = ("能接住的是**窗口那一支**两种：①自测那一步发现窗口没了（探路不重来）；"
               "②探路没走完 / 人喊停 / 窗口没了、或者探路里炸了"
               "（探路重跑，但**按账本接着走**，不是从头再探）。")
        if view["status"] in (QUEUED, RUNNING):
            return ("重开窗口要等这个任务停下来 —— 它现在是「%s」。%s"
                    % (self._status_say(view["status"]), can))
        result = view.get("result") or {}
        reason = result.get("end_reason")
        if reason == END_DELIVERED:
            return ("不用重开：这一趟**已经交付**了（产物在 %s）。要再走一遍就另起一个任务。%s"
                    % (result.get("py_path"), can))
        # ⚠️ 不能从 `view["result"]` 里读 `visits` —— `FAILED` 的 job 那个字段是 `None`
        # （**那是对的**：跑挂的没有结果），于是这句会永远渲染成「不知道哪一步」。
        # 走到哪儿了这件事只在 checkpoint 里。
        visits = list((self._snapshot(job_id).values or {}).get("visits") or [])
        where = str(visits[-1]) if visits else ""
        if view["status"] == FAILED:
            return ("不用重开：这一趟是**炸**在「%s」那一步的（不是窗口没了）：\n%s\n%s\n"
                    "重开一个窗口解决不了它 —— 按上面那句话说的办。"
                    % (STEP_SAY.get(where, where or "不知道哪一步"),
                       str(view.get("say") or "")[:400], can))
        return ("不用重开：这一趟停下来不是因为窗口没了，而是「%s」：\n%s\n%s\n"
                "重开一个窗口解决不了它 —— 按上面那句话说的办。"
                % (STEP_SAY.get(where, reason or "没说清"),
                   str(view.get("say") or "")[:400], can))

    @staticmethod
    def _problems_say(problems: list) -> str:
        return ("这份开场白还开不了工（**没有**碰浏览器、没有跑模型、没有写任何文件）：\n  · "
                + "\n  · ".join(problems))

    @staticmethod
    def _status_say(status: str) -> str:
        return {QUEUED: "还在排队", RUNNING: "正在跑", WAITING: "在等人回话",
                DONE: "跑完了", FAILED: "跑挂了"}.get(status, status)


# ─────────────────────────────── 拼成 app ───────────────────────────────


def create_app(*, graph_factory: Optional[Callable] = None, window: Any = None,
               checkpointer=None, checkpointer_url: Optional[str] = None,
               out_dir: Optional[str] = None, viewport_probe: Optional[Callable] = None,
               explore_dir: Optional[str] = None,
               window_probe_seconds: Optional[float] = None,
               shots_dir: Optional[str] = None, capture: Optional[Callable] = None,
               shot_timeout: Optional[float] = None,
               capture_bin: Optional[str] = None,
               selftest_dir: Optional[str] = None,
               mcp_bin: Optional[str] = None) -> FastAPI:
    """拼一个 app。测试从这里注入桩图 / 桩窗口 / 内存 saver。

    `window=None` 是**默认且合法**的：这个部署没接窗口层 —— 于是 `set_viewport` 那根线
    不存在，图会在 `intake` 停下点名（R-31 要的正是这个，不是要服务糊一个假回调）。

    `explore_dir` 是**运行产物**落哪（`runtime/explore/<job_id>/`，Task 1）。默认给的是
    仓库里那个 `runtime/`（不进 git）；测试一律传自己的 `tmp_path`。

    `shots_dir` 是**闸拍与步拍**落哪（`runtime/shots/<job_id>/pause-<n>.png` /
    `<本趟标记>-step-…`，Task 3）—— 步拍的名字**带本趟标记**（6 位 hex，`_StepShots`），
    因为同一个 job 目录会被最多 3 趟重探共用（见 `shots.dir_for` 那段）：
    `None` ⇒ `SITEFORGE_SHOTS_DIR` ⇒ 仓库里的 `runtime/shots`；`capture_bin` 是那个 cdp
    二进制（`None` ⇒ `SITEFORGE_CDP_BIN` ⇒ `CDP_PATH` ⇒ 仓库里的 `tools/cdp/cdp`）。
    ⚠️ 这两样都在**构造时定死**（不是在每次抓拍时再看一眼环境）—— 见 `Service.__init__`。
    **上面那条链是「构造那一刻」的解析规则**：解析完就落进 `self._shots_dir` / `self._cdp_bin`，
    之后**环境再变也不影响这个服务**（`/health` 报的就是这两个落下来的值 ——
    报「环境里现在写着什么」曾经是第四条通道：名字说 A、量的是 B）。
    `capture` / `shot_timeout` 是给测试注入桩用的（与 `viewport_probe` 同一个理由）。
    """
    svc = Service(graph_factory=graph_factory, window=window, checkpointer=checkpointer,
                  checkpointer_url=checkpointer_url, out_dir=out_dir,
                  viewport_probe=viewport_probe, explore_dir=explore_dir,
                  window_probe_seconds=window_probe_seconds, shots_dir=shots_dir,
                  capture=capture, shot_timeout=shot_timeout, capture_bin=capture_bin,
                  selftest_dir=selftest_dir, mcp_bin=mcp_bin)
    api = FastAPI(title="siteforge", version="0.1",
                  description="看着真页面产出 cdp-first py 脚本的 agent 服务（计划二 Task 8）")

    @api.get("/health")
    def health() -> dict:
        return {
            "ok": True,
            "service": "siteforge",
            "checkpointer": svc._check.kind,
            "say": svc._check.say(),
            "window_layer": (type(svc._window).__name__ if svc._window is not None
                             else "没接（换窗口大小那根线给不了）"),
            # ⚠️ 报的必须是**服务真正会用的那个**（构造时定死的），不是「环境里现在写着什么」：
            #    这两个东西可以不一样（显式给了 `capture_bin` 而环境里另有一个），
            #    而**名字说 A、量的是 B** 是这个项目最老的那条病。
            #    以前这里读活环境，两个变量都没设时还报 `null` —— 像「没有 cdp」，
            #    而服务实际会用**仓库里那个** `tools/cdp/cdp`（修复轮 2 的第四条，见 conftest）。
            "cdp": svc._cdp_bin,
            #: **哪一跳赢了**（`capture_bin` / `SITEFORGE_CDP_BIN` / `CDP_PATH` / `repo-default`）——
            #: 运维看到 `cdp` 会问的下一个问题。与 `cdp` 同一次解析的产物（不再读环境）。
            "cdp_source": svc._cdp_bin_source,
            "out_dir": svc._out_dir,
            "jobs": len(svc._jobs),
            "steps": {k: STEP_SAY[k] for k in NODES},
        }

    @api.post("/run", status_code=202)
    def run(body: RunRequest) -> dict:
        return svc.start(body)

    @api.get("/runs")
    def runs() -> dict:
        """**能挑运行的最小列表**（设计注 §8.1）—— 页面左边那一栏只取它一个。

        ⚠️ 它是 **process-local** 的：登记表里只有**这个进程**记得的那些 job
        （`note` 在空列表时明说这件事）。状态本身在 checkpoint 里，所以
        「不在这儿」不等于「没发生过」——知道 job id 就还能看。
        """
        return svc.runs()

    @api.get("/job/{job_id}")
    def job(job_id: str) -> dict:
        try:
            return svc._view(job_id)
        except KeyError:
            raise HTTPException(status_code=404,
                                detail="没这个任务：%s（服务里没有它，checkpoint 里也没有）。"
                                       "要么 id 写错了，要么它是在**另一个** saver 上跑的 —— "
                                       "状态住在 saver 里，不在这个进程里（R-19）。" % job_id)

    @api.get("/job/{job_id}/live")
    def live(job_id: str) -> dict:
        """**页面的唯一数据源**（Task 4 先出骨架）：一个轮询喂一个页面（设计注 §8.3）。

        与 `/job/{job_id}` 的关系：那边是**既有形状**，别的地方在读它，一个字段都不改；
        这边是新的那份（时间线 + 骨架里的闸口/输入/轮次/窗口）。**字节一个都不进来**（§8.3 第 2 条）。
        """
        try:
            return svc.live(job_id)
        except KeyError:
            raise HTTPException(status_code=404, detail=(
                "没这个任务：%s（服务里没有它，checkpoint 里也没有）。"
                "要么 id 写错了，要么它是在**另一个** saver 上跑的 —— "
                "状态住在 saver 里，不在这个进程里（R-19）。" % job_id))

    @api.get("/job/{job_id}/shot/{name:path}")
    def shot(job_id: str, name: str):
        """一张闸拍图（设计注 §8.1）：**字节走这儿，不进 `/live` 的 JSON**。

        ⚠️ `{name:path}`（不是 `{name}`）：名字里带 `/` 的请求也要**落到人手里** ——
        不然框架会拿一个英文的 `{"detail":"Not Found"}` 先把它挡掉，
        而这条路上最该说清的就是「这个名字不能当图名」（它是一次路径穿越的尝试）。
        名字与路径的判据全在 `Service.shot_file` 里（白名单 + 解析后仍在那个目录里）。
        """
        path = svc.shot_file(job_id, name)
        # `no-cache`：文件名带轮号，正常永远不会变 —— 但**服务重启后捡回来的 job**
        # 轮号从 1 重新数，同一个名字可能换一张图。让浏览器每次回来问一句（304 很便宜），
        # 否则页面上会出现一张**对不上的旧图**（看着像证据，比不显示坏得多）。
        return FileResponse(str(path), media_type="image/png",
                            headers={"Cache-Control": "no-cache"})

    @api.post("/job/{job_id}/reply")
    def reply(job_id: str, body: ReplyRequest) -> dict:
        try:
            return svc.reply(job_id, body)
        except KeyError:
            raise HTTPException(status_code=404, detail="没这个任务：%s。" % job_id)

    @api.post("/job/{job_id}/reopen")
    def reopen(job_id: str, body: ReopenRequest) -> dict:
        try:
            return svc.reopen(job_id, body)
        except KeyError:
            raise HTTPException(status_code=404, detail="没这个任务：%s。" % job_id)

    api.state.service = svc
    return api


#: uvicorn 的入口（`entrypoint.sh` 里 `agent.service:app`）。
#: ⚠️ 导入这个模块**不碰**数据库：saver 是第一次真要用的时候才建的（`Checkpointer`）。
app = create_app(window=bit_window_from_env())
