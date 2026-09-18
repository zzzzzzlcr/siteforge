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

from agent import browser_agent, graph, journal, measure, selftest, shots, tools
from agent.graph import NODES, STEP_SAY
from agent.state import (END_DELIVERED, END_EXPLORE_UNFINISHED, END_NO_WINDOW,
                         END_PAUSED, END_WINDOW_GONE)

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

    def snapshot_for_view(self) -> tuple:
        with self.lock:
            return self.status, self.say, self.error


# ─────────────────────────────── 服务本体 ───────────────────────────────


def _tighter(recorded, on_disk) -> dict:
    """两份「这个 job 花了多少」取**更紧的**（逐键 max）。

    为什么不是相加、也不是取其中一份：两份都是**下界**（状态里那份可能因为节点抛异常而
    缺了后面几趟；盘上那份的 `rounds` 对「没量到」的趟记 0）—— 取 max 是**保守**的那一侧
    （预算只会更小、不会凭空变大），与 `graph._round_spends` 那条「不知道的一律按花算」同族。
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
                          selftest=self._selftest_cb(),
                          set_viewport=(self._viewport_cb(brief.get("ws_url"))
                                        if brief.get("set_viewport") else None),
                          fresh_session=self._fresh_session_cb(),
                          window_alive=self._window_alive_cb())
        if self._graph_factory is not None:
            return self._graph_factory(brief, deps)
        return graph.build(checkpointer=self._check.get(), deps=deps)

    def _selftest_cb(self) -> Callable:
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
        """

        def run_selftest(py_path, ws_url, form_file, site, **kw):
            kw.setdefault("run_dir", str(selftest.default_run_dir(site, root=self._selftest_root)))
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

        def run(url, goal, budget=None, should_pause=None, resume_from=None,
                resume_note="", window_alive=None):
            started = measure._now()
            # ⚠️ **每一趟取一次号**（I-1）：`deps.explore` 在一次节点执行里最多被调
            # `graph.EXPLORE_ATTEMPTS`(=3) 趟（重探），而「一趟 = 一个 `attempt-<n>.jsonl`」。
            # 号要是**建图时**算一次，三趟就全挤进同一个文件、也没有任何边界标记 ——
            # 而 `attempts.jsonl` 那边是**一趟一行**，两本账当场对不上（`rows != journey.steps`）。
            journal_broken.clear()
            on_step = (self._journal_for(job_id, self._next_attempt_no(job_id), journal_broken)
                       if job_id else None)
            try:
                journey = browser_agent.explore(url, goal, budget=budget,
                                                should_pause=should_pause, ws_url=ws_url,
                                                on_step=on_step,
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
            except BaseException:                    # noqa: BLE001 —— 工作线程**不许**死
                traceback.print_exc()
            finally:
                self._queue.task_done()

    def _advance(self, job: Job, payload) -> None:
        """把图往前推一步 —— **这是唯一一个 job 会动的地方**（单飞）。

        抛出来的东西一律变成 `failed`：一个跑挂的 job **绝不许**被读成跑成了
        （它 `result` 为 `None`，状态是 `failed`，人话里留着原始错误）。
        """
        with job.lock:
            job.status = RUNNING
            job.say = "在跑：真浏览器 + 模型，做完一步或需要你时就会停下来。"
        try:
            with self._check.lock:                   # 一个 saver 连接不被两个线程同时用
                out = job.graph.invoke(payload, self._cfg(job.job_id))
        except BaseException as exc:                 # noqa: BLE001 —— 包括 _Stop 之类的 BaseException
            with job.lock:
                job.status = FAILED
                job.error = "%s: %s" % (type(exc).__name__, exc)
                job.say = ("这一步没跑成，停下了：%s\n"
                           "（任务没有交付任何东西 —— 产物目录里不会有它写的 py。）" % exc)
            self._measure_after(job.job_id)          # 旁路：跑挂了也要留账（吞异常）
            # 跑挂了那一屏更该看得见 —— 同一个出口、同一条纪律（旁路，不抛）。
            # ⚠️ 这条路上 `FAILED` **先**落（人该立刻看见它挂了），图随后才到 ——
            #    所以这一刻的快照里可能还没有这一轮的 note，下一次读就有。
            self._capture_pause(job)
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
        job = Job(job_id=job_id, brief=brief, status=DONE,
                  say="（服务重启过：这个任务是从 checkpoint 里捡回来的）",
                  created_at=datetime.datetime.now().astimezone().isoformat(timespec="seconds"))
        job.graph = self._build_graph(brief, job_id)
        with self._jobs_lock:
            self._jobs.setdefault(job_id, job)
            return self._jobs[job_id]

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
        job = Job(job_id=job_id, brief=brief, status=QUEUED,
                  say="收到了，排队开跑。",
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
            return self._view(job_id)
        # 窗口时间线探针：从这一刻起盯住「窗口是哪个进程」（Task 1 / G3）。
        # **只在能给 PID 的窗口层上起** —— 桩窗口没有 PID，那样的线判不出「重开了几次」。
        self._active_job = job_id
        self._start_window_probe()
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
        with self._check.lock:
            job.graph.update_state(self._cfg(job_id), patch, as_node=resume_from)
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

    @api.get("/job/{job_id}")
    def job(job_id: str) -> dict:
        try:
            return svc._view(job_id)
        except KeyError:
            raise HTTPException(status_code=404,
                                detail="没这个任务：%s（服务里没有它，checkpoint 里也没有）。"
                                       "要么 id 写错了，要么它是在**另一个** saver 上跑的 —— "
                                       "状态住在 saver 里，不在这个进程里（R-19）。" % job_id)

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
