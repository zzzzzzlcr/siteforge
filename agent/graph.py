"""Task 7：LangGraph 图 —— 把 Task 1/3/4/5/6 串起来跑，**人每一步都在**（§6.2）。

```
START → intake → explore → draft → lint → selftest → deliver → END
                     ↑          │       │        │  ↑
                     │          │       │        └──┘  人否了这一版（带他的话）
                     │  带违规行 ←┘       │
                     └────── 带证据 ←─ diagnose ←┘
```

## 三处必须写对的（brief 点名的）

### 1. 人不是最后一道关，是**每一步都在**（§6.2）

每个**花钱 / 动真页面**的节点**开工之前**都 `interrupt()` 一次：`explore` 之前、
`deliver` 之前，一视同仁。不是「以后加个 UI」—— 它是架构约束：
**任何一步都不许设计成「不可打断、跑完才汇报」**。所以这道闸写在 `_enter()` 里，
节点第一件事就是过它。

⚠️ **唯一的例外是 `intake`，而它是有理由的**（2026-09-20 改）：运营点「开一趟」那一下
**就是**「开工前的确认」，在 `intake` 上再停一次是**同一件事问两遍** —— 而第二遍
**等不到人时一声不出**（2026-09-20 真面板上连撞三次，详见 `_intake` 里那段）。
`intake` 做的是**免费**的事（校验开场白、读底稿），它一步贵的事都不做；
而它后面那道闸就是 `explore`，**照停**，且**排在开真窗口之前**。
⇒ 这条改动**放松的只是「几道闸」这个数**（6 → 5），
**没有放松它承重的那半句**：人没按「继续」之前，一步贵的都不会跑。
用例：`test_no_browser_and_no_model_before_a_human_confirms` /
`test_the_operator_is_not_asked_to_confirm_the_start_a_second_time`。

中断之后**真能接着跑**，靠的是 checkpointer（R-19）：`build(checkpointer=…)` 是**必需**参数，
不许默认 `None` —— 「没有 saver」正是「中断之后恢复不了」的根因：
没有 saver 时 `interrupt()` 不报错，图安安静静地停在那儿，而**谁也没法让它再动**
（langgraph 不会拦你，它只是永远返回同一个中断）。存哪儿是**接线**（Task 8 用 Postgres），
但**必须有**。

### 2. 两条回灌都有硬上限（§6.5）

- `lint` 不通过 → 回 `draft`，**带着违规行**（`{line, code, message, snippet}`）——「你手拼 JS 了」
  它找不到地方，得指着行说
- `selftest` 挂了 → `diagnose`（说清哪一遍、卡在第几步）→ 回 `draft`，**带着证据**
- 两条各自的上限在 `state.Caps`。到顶就**停**，并把「为什么停」说成人话

**还有第三条回灌：人自己**（§6 里 `review` 那一行的正身「人否 → 回 `draft` 带人的纠正」）。
人在 `REVISABLE`（lint / selftest / deliver）那几道闸上说「这版不行」→ 那一步**不做**，
回 `draft` 按他的话重写。这条与「喊停」分得开：打回进 `state["revisions"]`（谁、在哪、说了什么），
喊停进 `end_reason == "human_stop"`。它也有上限（`Caps.max_revisions`）：
每转一圈都要有人回话，所以它跑不飞；上限防的是**自动化调用方**一直回「重来」把真浏览器
拖进无尽的自测，同时把 §6.3 的信号摆明（同一处反复打回 = 问题不在这一版稿上）。

上限要防的是**「跑不完也不会停」的图**，不是省钱（P5）—— 所以别拿砍轮数当优化。

### 2.5 图**跑之前**要的输入（Task 8 请照这张表给）

这张图**不自己发明**任何「没验到也算过」的默认值，所以有几件事只有调用方给得了。
缺了它们图会**停**并**点名**（`missing_knob` / `no_success_text`），**不会**写出一份假通过：

| 输入 | 谁给 | 不给会怎样 |
|---|---|---|
| `success_text`（什么算成功） | 人（`POST /run` 的载荷） | **在 `intake` 就结束这一趟**（`end_reason=no_success_text`）—— 不猜，也不先烧一个窗口。成功判据只有人知道（§6.1） |
| `ws_url` / `form_file` | §4.6 前提层（Task 8：拉链 → 下发指纹 → `bit.sh open`） | `selftest` 停（`no_window`），**不许跳过自测当通过** |
| `Deps.set_viewport`（**窗口层**那根线） | Task 8 的服务（换窗口大小 = `POST /browser/update`） | 停（`missing_knob`）并点名 —— 因为第 4 遍扰动跳过了就**不算过**（R-5），而图不许自己放过它 |
| `allow_skips`（点名放弃哪几遍） | 人（载荷） | 不给 = 用 Task 6 的默认（只允许跳 country） |
| `entry_url`（第 2 遍刷新回哪） | 人 / 前提层 | 不给 = 第 2 遍就「接着再跑一遍」（R-6 的字面读法要它） |
| `env` / `platform`（指纹 / 平台） | 前提层 / 平台分类 | 不带（`PROVENANCE` 里留 `None`，不编内容） |

**窗口层那根线在不在，是「自测的结论完不完整」的分水岭**：不给它，第 4 遍必然记成
「这一类没验到」，`_judge` 必然判不过 —— 那不是产物不行，是**少给了一个输入**。
所以「缺旋钮」这件事在 `intake`（免费）与 `selftest`（跑到那儿时手上这根线还在不在）各查一次，
两次都是**停**下来点名，不是转到自测上限。

⚠️ 两处的**停法不一样**（2026-09-20 起）：`intake` 那处**结束这一趟**（`end_reason=missing_knob`，
它不设闸 —— 运营点「开一趟」就是确认，没有「停下这一步」可言）；`selftest` 那处照旧**停在闸上**等人。

### 3. `deliver` 写出的 py 带 `PROVENANCE`（§5.3）

产物要把**自己的自测结果**写进 `PROVENANCE`，而自测结果只有跑完才知道 ——
所以顺序是：`draft` 先渲一版（`selftest: None`）→ 自测跑**这一版** → `deliver` 用同一个 spec
再渲一版（带上自测结果）落盘。两版之间**只差 `PROVENANCE` 那一块**，
`tests/test_graph.py::test_the_delivered_bytes_differ_from_the_tested_bytes_only_in_provenance`
拿 `ast` 钉着这件事；`deliver` 落盘前还会**再 lint 一次要落的字节**（那块里有自由文本）。

## 谁填 `PROVENANCE` 的哪些键 —— 图**只填它真知道的**

| 键 | 谁填 |
|---|---|
| `generated_at` / `generator` | 图 |
| `env`（代理国家/DPR/UA/视口） | **§4.6 前提层**（Task 8 接线：拉链 → `POST /browser/update` → `bit.sh open`）。图只搬运：有人告诉它就带上，没有就是 `None` |
| `platform` | **不在本计划**（平台分类）；图同样只搬运 |
| `selftest` | 图（跑完才知道） |
| `source.kind` / `source.evidence` | 图（人给的意图 / 失败证据，原样带上） |
| `source.runtime` | `agent.runtime.provenance()`（R-15：这份 py 跑起来用的是**哪一份** `common.py`）|

**没人告诉图的事一律 `None`** —— 缺的键补 `None`，不编内容（§5.3 的同一条）。

## 有一件事这个文件**故意**不做

`deps.write`（「按账本写 py」那一步）默认是**恒等**的确定性翻译（`Journey.states()` /
`journey.fills()` → `template.render()`）：**它改不了自己写出来的东西**。
所以默认接线里，lint / selftest 打回只会走到上限就停 —— 那是**诚实的**（图不假装修好了），
不是藏着缺陷：**能改产物的那个角色（模型 / Console 里的人）从这里注入**
（`write=(spec, feedback) -> spec`，feedback 里就是违规行 / 诊断证据 / 人说的话）。

没有这个缝，「回灌」就只能测到「loop 转了几圈」，测不到「证据真的到了能改它的那双手里」。
"""

from __future__ import annotations

import datetime
import pathlib
import copy
import difflib
import re
from dataclasses import dataclass
from typing import Callable, Optional

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from agent import browser_agent
from agent import fix as fix_mod, lint as lint_mod, runtime
from agent import selftest as selftest_mod
from agent import template
from agent.state import (
    END_DELIVERED, END_DELIVER_LINT, END_DRAFT_FAILED, END_EXPLORE_UNFINISHED,
    END_HUMAN_STOP, END_LINT_CAP, END_MISSING_KNOB, END_NO_BRIEF, END_NO_SUCCESS_TEXT,
    END_NO_WINDOW, END_PAUSED, END_REVISION_CAP, END_SELFTEST_CAP, END_WINDOW_GONE,
    FINISHED_EXPLORATION, GENERATOR, MODE_BUILD, MODE_FIX, REVISE, STOP, Caps, SiteState,
    human_reply,
)

__all__ = ["Deps", "build", "Caps", "MSGPACK_ALLOWLIST", "allowlisted", "NODES",
           "HUMAN_CAN", "STEP_SAY"]

_REPO = pathlib.Path(__file__).resolve().parents[1]

#: 产物默认落在哪（生产就是 `forms/sites/`，与 63 个手写脚本同一层）。
DEFAULT_OUT_DIR = _REPO / "forms" / "sites"

#: 图里所有的节点，按走的顺序（测试与文档都认这一份）。
NODES = ("intake", "explore", "draft", "lint", "selftest", "deliver", "diagnose")

#: 每一步在**人话**里叫什么（D16：闸口上问的不是错误码、不是选择器）。
STEP_SAY = {
    "intake": "开工前的确认",
    "explore": "打开浏览器探路",
    "draft": "写这一版 py",
    "lint": "检查这一版有没有手拼 JS",
    "selftest": "在真浏览器上按扰动序列自测",
    "deliver": "把它写进站点目录",
    "diagnose": "从自测记录里定位卡在哪",
}

#: 闸口上人**能做什么**（每次都说清楚，免得人以为自己只能点「继续」）。
HUMAN_CAN = ("让它继续（回 continue / 空 / 不回话）",
             "喊停（回 stop）—— 停在这一步之前，这一步不会做",
             "说一句纠正（回一句话或 {action: revise, note: …}）—— 接着走，这句话带进 draft",
             "在 lint / selftest / deliver 门口说「这版不行」（同上，带 note）—— "
             "这一版不要了，回 draft 按你说的重写")

#: 哪几道闸上「打回」= **这一版不要了，回 draft**（§6 里 `review` 那一行的正身：
#: 「人否 → 回 `draft` 带人的纠正」）。intake / explore / draft 那三道闸不是这个意思：
#: 那儿的纠正只是「接着说一句」，本来就要往下写。
REVISABLE = ("lint", "selftest", "deliver")

#: 五遍扰动里，**哪几遍需要调用方在窗口层/代理层那根线**（Task 6 的 `run()` 的旋钮）。
#: 这张表就是「缺旋钮」检查的判据：一遍扰动要么**跑得了**（旋钮在），要么**被人明确允许不跑**
#: （写进 `allow_skips`），两者都不成立时图**停**并把旋钮点出来（R-31）——
#: 不许自己发明一个默认让它跳过去（R-5：跳过不算过），也不许转到自测上限假装是产物不行。
#:
#: `(旋钮名, 这一遍在打什么, 谁给得了)`。⚠️ 第 5 遍（换代理国家）这张图**故意没接**
#: （计划里它是可选的，要重拉 gost 链）—— 所以它必须留在 `allow_skips` 里（Task 6 的默认
#: 正是如此）。要真跑第 5 遍，加 `Deps.set_country` + `Deps.country` 两处即可。
ROUND_NEEDS = {
    "viewport": ("set_viewport", "换个窗口大小再跑一遍（打折叠 / 遮挡 / 坐标假设）",
                 "调用方在**窗口层**动手（换窗口大小就是 `POST /browser/update`）——"
                 "Task 8 的服务给得了"),
    "country": ("set_country", "换个代理国家再跑一遍（打地区内容差异）",
                "要重拉 gost 链；这张图**没有接**这根线（计划里这一遍是可选的）"),
}

#: 存进 checkpoint 的那几个 dataclass。langgraph 的 serde 要**点名允许**它们
#: （不然新版本会拒收：`Deserializing unregistered type … will be blocked in a future version`）。
#: R-19：换 saver 是**接线**（Task 8 的 Postgres）——把 `PostgresSaver(...)` 包一层
#: `allowlisted(...)` 就行，别改这里。
MSGPACK_ALLOWLIST = (("agent.browser_agent", "Journey"),
                     ("agent.selftest", "Report"),
                     ("agent.selftest", "Run"))


def allowlisted(saver):
    """给 saver 加上 `MSGPACK_ALLOWLIST`（拿不到这个能力的 saver 原样返回）。"""
    adder = getattr(saver, "with_allowlist", None)
    return adder(MSGPACK_ALLOWLIST) if callable(adder) else saver


# ─────────────────────────── 依赖（测试从这里注入桩）───────────────────────────


def _writer_from_journey(spec: dict, feedback: dict) -> dict:
    """默认的「写 py」：**确定性的翻译**（账本 → `states` / `fills`），恒等，不改东西。

    ⚠️ 它**修不了**自己被 lint / 自测打回的那一版（看一眼就知道为什么：它没有判断力）。
    这不是缺陷，是**能力的边界**：能改产物的那双手（模型 / 人）从 `Deps.write` 注入，
    `feedback` 就是递给那双手的东西。默认接线里，打回只会走到上限就停 —— 诚实的那种停。
    """
    return spec


@dataclass
class Deps:
    """这张图跟外面世界的每一个接触面（**全部**可注入，测试里全是桩）。

    两个可调用的旋钮为什么在这里而不是在 state 里：状态要进 checkpoint，
    **可调用的东西进不去**。
      - `should_pause`：Console 那只「停」按钮伸进浏览器的那根线（§6.2）
      - `set_viewport`：**窗口层**那根线（`POST /browser/update`）。扰动自测的第 4 遍
        「换个窗口大小再跑」只有调用方够得着，产物和 cdp 内核都动不了窗口（R-5）。
        没接上时的处置见 `_missing_knobs()`：**停下并点名**，不是跳过、不是假装过了。
      - `window_alive`：**窗口层**那根「窗口还活着吗」的线（§1.8）——
        `window_alive() -> True / False / None`（三态：`None` = 问不出来）。
        探路**连着几次工具失败**时问它一次：说死了 → `_Stop("window_gone")`。没接上
        （或问不出来）时的处置是**不停**：不编一个停因出来（那会把「选择器没找到」
        记成「窗口死了」，而重开窗口恰恰解决不了它）。
      - `fresh_session`：**窗口层**的另一根线（R-F1）。`fresh_session() -> ws_url`：
        关掉旧窗口、开一个**干净**的（启动时清 cookie/缓存 —— 生产每单都是这么起的），
        返回新的 ws_url。自测在**探路之后**跑，探路的会话里 cookie 已经同意过，
        「首次访问才有」的步骤在那时**元素真的不在**了 —— 那测的是生产里不会出现的场景。
        没接上（或这个部署给不了）时的处置见 `_selftest`：**照跑，但在 facts 里说清
        「这一次不是干净会话」**，不假装干净、也不因此判不过（判据一个字不改）。
    """

    explore: Callable = browser_agent.explore
    write: Callable = _writer_from_journey
    #: **老写法那份 py 的改稿那双手**（B 线 ③ 乙）：`(old_src, feedback) -> 模型原话`。
    #: 与 `should_pause` / `set_viewport` 同一条规矩：**没接上就停下并点名** ——
    #: 「这一族今天修不了」不许被写成「读不出 STATES/FILLS」（那是两件事）。
    patch_source: Optional[Callable] = None
    lint: Callable = lint_mod.check
    selftest: Callable = selftest_mod.run
    provenance: Callable = runtime.provenance
    should_pause: Optional[Callable] = None
    set_viewport: Optional[Callable] = None
    fresh_session: Optional[Callable] = None
    #: 「窗口还活着吗」（§1.8）。⚠️ **可调用的东西不进 checkpoint** —— 与 `should_pause`
    #: 同一条规矩（状态里只放数据，回调一律挂在 `Deps` 上）。
    window_alive: Optional[Callable] = None
    #: **跑一遍旧脚本拿证据**（修站那条路，2026-09-21）：`(py, ws_url, form_file, site) -> dict`，
    #: 形状见 `selftest.run_once`，人话见 `selftest.evidence_say`。
    #: ⚠️ 它**动真页面 + 一次真提交** ⇒ 排在 `explore` 那道闸**之后**，不是顺手就跑的。
    #: 没接上 = 这一趟没有真证据（照常出稿，但要在事实里说清）——
    #: 【我量的·2026-09-21】没有证据时模型交回来的是「只改掉一个空格」或「一处都没改」；
    #: 把那份失败日志给它之后，它交出的是一份**有推理、过闸**的补丁（交接 §3.6c）。
    evidence_run: Optional[Callable] = None


# ───────────────────────────── 人的那道闸 ─────────────────────────────


def _visited(state, step: str) -> list:
    return list(state.get("visits") or []) + [step]


def _held(out: dict) -> bool:
    """该收住了吗：人**喊停**（`end_reason`）或人**否了这一版**（`revised_at`）。

    两种都不是「这一步失败了」，两件事也分得开（状态里一个进 `end_reason`、一个进
    `revisions`）—— 后面读这份记录的人要能说出「他杀了这次运行」还是「他让这一版重写」。
    """
    return bool(out.get("end_reason") or out.get("revised_at"))


def _enter(state, caps: Caps, step: str, say: str, facts: Optional[dict] = None) -> dict:
    """**节点开工之前**过这道闸（§6.2）。返回该写回状态的那部分。

    ⚠️ **`intake` 不过它**（2026-09-20）：运营点「开一趟」那一下就是确认，
    在那儿再停一次是同一件事问两遍。理由与承重的那半句见模块 docstring §1 与 `_intake`。

    - `interrupt()` 在这里抛出去：图就停在**这一步之前**，这一步**没有做**
    - 人回来说的话：纠正 → 收进 `hints`（一路带着，进 draft）；喊停 → 写 `end_reason`
      /`end_note`；在 `REVISABLE` 那几道闸上说「这版不行」→ 写 `revised_at`（路由据此回
      `draft`，那一步的工作就不做了）
    - 调用方那个节点看到 `_held(out)` 为真就**直接收摊**，别再往下做

    `say` 是给人看的人话，`facts` 是原始事实（D11：给感知不给判断 —— 人要看得到原料）。
    """
    reply = interrupt({"step": step, "say": say, "facts": facts or {}, "can": list(HUMAN_CAN)})
    action, note = human_reply(reply)
    out: dict = {"visits": _visited(state, step)}
    if note:
        out["hints"] = list(state.get("hints") or []) + [note]
    if action == STOP:
        out["end_reason"] = END_HUMAN_STOP
        out["end_note"] = ("人喊停：在「%s」这一步**之前**停下来，这一步没有做，页面与文件都保持原样。"
                           "接着走就再发起一次 —— 已经探到的账本还在（checkpoint 里）。"
                           % STEP_SAY.get(step, step))
        _drop_candidate(state)          # 停下来的这次运行，交付目录里不留东西
    elif action == REVISE and step in REVISABLE:
        revisions = list(state.get("revisions") or []) + [{"at": step, "note": note}]
        out["revisions"] = revisions
        if len(revisions) > caps.max_revisions:
            # 同一处反复被打回，本身就是「问题不在这一版稿上」的信号（§6.3）。
            # 停，并且说清是人打回的 —— 别让它看起来像产物自己挂了。
            out["end_reason"] = END_REVISION_CAP
            out["end_note"] = ("停：这一版被人打回了 %d 次（最后一句话：「%s」）。"
                               "同一条路上反复被打回，多半说明问题不在这版稿怎么写上 —— "
                               "人接手看一眼路线，或者直接说清要什么（§6.3）。"
                               % (len(revisions), note or "（只说了重来）"))
            _drop_candidate(state)
        else:
            out["revised_at"] = step
    return out


# ─────────────────────────────── 六个节点 ───────────────────────────────
# ⚠️ 六个节点，但**五道闸**（2026-09-20 起）：`intake` 不设闸（见模块 docstring §1）。
#    下面 `_brief_facts` 是**节点用的助手**，不是第七个节点 —— 它算的是第一道闸要摊开的东西。


def _brief_facts(state, deps: Deps, missing: list) -> dict:
    """开场白那几项 —— **人点「开一趟」时确认的东西**，摊在**第一道闸**上。

    ⚠️ **为什么它现在长在 `explore` 那一侧**（2026-09-20）：`intake` 不再设闸了
    （运营点「开一趟」那一下**就是**确认，在那儿再停一次是同一件事问两遍）。
    这几项原先摊在 `intake` 那道闸上 —— 没人接手的话，第一道闸换成 `explore` 之后
    运营就**少看了一半信息**：那不叫「少问一次」，那叫「把确认变成走过场」。
    ⇒ 第一道闸要带着**同一份开场白**。

    ⚠️ **现算，不搬运**：算的是**这一刻**的状态（`_missing_knobs` 读 `deps` 上那几根线）。
    从 `intake` 搬一份存进 state 的话，两道闸之间哪根线掉了它也不会说 ——
    而「哪根线还在」正是这道闸要让人看见的东西之一。
    """
    return {"url": str(state.get("url") or "").strip(),
            "goal": str(state.get("goal") or state.get("evidence") or "").strip(),
            "成功判据": state.get("success_text"),
            "mode": state.get("mode") or MODE_BUILD,
            "要用的窗口": state.get("ws_url"),
            "允许跳过的扰动": list(state.get("allow_skips") or []),
            "还缺的窗口旋钮": [k["knob"] for k in missing]}


def _intake(state, deps: Deps, caps: Caps) -> dict:
    """收开场白：哪个站、要做什么、成功长什么样。**开场白缺东西在这儿就拦下**。

    为什么这几项检查全在 intake：后面每一步都**贵**（开一个真窗口、跑一次模型探路、
    在真站上填一遍表单）。缺一个输入却拖到 draft 才发现，回报的是「写不出来」——
    那个失败看上去像模型/产物的问题，其实是**少给了一个输入**（评审 Important 1b）。
    在这儿拦下是免费的。

    §4.6 的前提层（拉链 → 下发指纹 → 开窗口）**不在这里**：那是接线（Task 8），
    产物（`ws_url` / `env`）由调用方放进来。图只负责把它带上。
    """
    url = str(state.get("url") or "").strip()
    goal = str(state.get("goal") or state.get("evidence") or "").strip()
    missing = _missing_knobs(state, deps)
    # ⚠️ 读底稿**排在**返回之前：这样「这是修站、读到了什么、改了哪些格」能进
    # **第一道闸**（现在是 `explore`）的 facts —— 人在这儿就能看见它要拿哪份稿去改。
    # （2026-09-20 之前第一道闸是 `intake` 自己；`intake` 不设闸之后，这几项由
    # `_explore` 那边的 `_brief_facts` / 修站那一支接手，见它们的注释。）
    fix_py = str(state.get("fix_py") or "").strip()
    fix_read: dict = {}
    fix_failed = ""
    if fix_py:
        try:
            src_before = pathlib.Path(fix_py).read_text(encoding="utf-8")
        except OSError as exc:
            fix_failed = "修不了：读不到那份 py（%s）。给一个能读的路径再发起。" % exc
        else:
            plan, fix_states, fix_fills, fix_notes = fix_mod.from_py(src_before)
            shape = ({"kind": "template", "why": []} if fix_states
                     else fix_mod.shape_of(src_before))
            if fix_states:
                fix_read = {"mode": MODE_FIX, "fix_py": fix_py, "fix_src": src_before,
                            "fix_states": fix_states, "fix_fills": fix_fills,
                            "fix_notes": fix_notes, "fix_plan_steps": len(plan.steps)}
            elif shape["kind"] == "legacy":
                # ★ B 线 ③（用户 2026-09-21 拍板的「乙」）：线上那一族写法里**没有**
                # `STATES/FILLS` 那张数据表 —— 所以底稿就是它**本身**，改稿走
                # `deps.patch_source`（整份源码出补丁，过闸才往下走）。
                # ⚠️ 仍然**不探索**：修站那条路的判据是「不重新探索」。
                fix_read = {"mode": MODE_FIX, "fix_py": fix_py, "fix_src": src_before,
                            "fix_style": "legacy",
                            "fix_states": None, "fix_fills": None, "fix_notes": fix_notes}
            else:
                why = "；".join(shape["why"]) or (fix_notes[0] if fix_notes else "那份 py 读不出来")
                fix_failed = ("修不了：%s\n（修站这条路要的是**读得出 STATES/FILLS** 的旧 py，"
                              "或者线上那一族**老写法**的完整 py（走 report_url、认生产那四个"
                              "开关、`sys.exit(0 if … else 1)`）；这一份两样都不是 —— "
                              "读不出来就只能当新站从零探索，那是另一条路。）" % why)
    # ⚠️ **`intake` 不设闸**（2026-09-20 真面板实测逼出来的）。
    #
    # 运营点「开一趟」那一下**就是**「开工前的确认」。在这儿再停一次 = **同一件事问两遍**，
    # 而第二遍**等不到人时一声不出** —— 2026-09-20 一天里连撞三次：
    # ①停在 intake 上没人按 ⇒ 运营以为「失败了」，把页面关了；
    # ②同一趟再派一次时按成了「停」（时间线上记的是「你按了「停」」）；
    # ③运营原话：「我作为一个使用者我现在就是看着他页面卡住啥也操作不了也不知道啥情况啊」。
    #
    # `_enter` 那四件事在 `intake` 上**本来就只有一件是真的需要的**（复审口径：量出来的，不是猜的）：
    #   - `REVISABLE` 打回：**空转** —— `intake` 不在 `REVISABLE` 里（`("lint","selftest","deliver")`）；
    #   - `end_reason` 喊停：`intake` 是**刚点完「开一趟」**那一下，没有「停下这一步」可言
    #     （而它后面那道闸就是 `explore`，那一道**照停**，且排在真窗口之前 —— 见下一条）；
    #   - `hints` 收话：还没人说过话，而且 `_intake` 本来就把 `state["hints"]` 原样带着走；
    #   - `visits` 记账：**这个要留** —— 它是「走过的节点」（§6.4 人看路线用），
    #     `rounds.py` 给第一张卡片配「刚做完的是哪一步」读的也是它。少记一格，
    #     卡片就会把「刚做完的」说成下一个节点 —— 那是编话。
    #
    # ⚠️ **承重的那条不变量没被这条改动放松**：「任何一步都不许设计成不可打断、跑完才汇报」。
    # 它现在落在 `_explore`：那道 `_enter` **排在 `deps.explore`（开真窗口 + 跑模型）之前**，
    # 所以 **人没按「继续」之前，一步贵的都不会跑**（用例：`test_no_browser_and_no_model_before_a_human_confirms`）。
    # 这条闸之后那几道（explore / draft / lint / selftest / deliver）**一道没少**。
    out: dict = {"visits": _visited(state, "intake")}
    if not url or not goal:
        out.update({"end_reason": END_NO_BRIEF,
                    "end_note": ("开不了工：得先说清**哪个站点**（url）和**要做什么**（goal 或失败证据）。"
                                 "没有这两样，探路会去开一个浏览器、然后在空页面上乱走。")})
        return out
    if not state.get("success_text"):
        # 成功判据只有人知道（§6.1：页面能告诉 agent **机制**，只有人能告诉它**意图**）。
        # 猜一个 = 产出「跑到底再谎报成功」的东西 —— 本计划最忌讳的那类谎。
        out.update({"end_reason": END_NO_SUCCESS_TEXT,
                    "end_note": ("开不了工：还没说**什么算成功**（`success_text`：走通之后页面上会出现"
                                 "哪段文字）。这一条只有人知道，猜不得 —— 猜出来的成功判据会让产物"
                                 "「跑到底再报成功」。\n"
                                 "补上它再发起：它就在开场白里，不用等探完路。")})
        return out
    if missing:
        out.update({"end_reason": END_MISSING_KNOB, "end_note": _knob_note(missing)})
        return out
    if fix_failed:
        out.update({"end_reason": END_DRAFT_FAILED, "end_note": fix_failed})
        return out
    if fix_read:
        out.update(fix_read)

    out.update({
        "url": url,
        "goal": goal,
        "mode": fix_read.get("mode") or state.get("mode") or MODE_BUILD,
        "site": state.get("site") or site_name(url),
        # 人在**开工前**那道闸上说的话也要留着（`_enter` 刚记进 out，不能在这儿盖掉）
        "hints": list(out.get("hints") or state.get("hints") or []),
        "lint_bounces": int(state.get("lint_bounces") or 0),
        "diagnoses": int(state.get("diagnoses") or 0),
        "revisions": list(state.get("revisions") or []),
        "out_dir": str(state.get("out_dir") or DEFAULT_OUT_DIR),
        "end_reason": "",
        "end_note": "",
    })
    return out


def _explore(state, deps: Deps, caps: Caps) -> dict:
    """在真浏览器里走一遍，拿回账本（Task 5）。

    ⚠️ **修站那条路在这道闸之前就分叉**（`state["fix_states"]` 在 ⇒ 不探索）。
    为什么分叉要排在 `_enter` **之前**：闸口上那句人话得说**这一步真要做什么** ——
    修站这一步根本不打开浏览器，闸上就不该写着「接下来要打开真浏览器」。
    顺序反了的话，人是在一句假话上点「继续」的（2026-09-17 实测就是这么错的）。
    """
    # ── 「修站」这条路**不探索**（判据就这一条：不重新探索）──────────────────
    #
    # 手上已经有一份读得出的旧 py（intake 那一步读进来的），它的每一步、每一格
    # 都录在那份产物里。再开一个浏览器重走一遍 = 把已有的证据丢掉重买一次。
    # 所以这一步**原样跳过**，并把「跳过了什么、凭什么是它」写进 facts 让人看得见。
    if state.get("fix_states") or state.get("fix_style") == "legacy":
        legacy = state.get("fix_style") == "legacy"
        notes = list(state.get("fix_notes") or [])
        if legacy:
            say = ("这次是**修站**，不重新探索：拿的是**线上正在跑的那一份 py**（%s）—— "
                   "它是**老写法**（里面没有 `STATES/FILLS` 那张数据表），所以这一趟是"
                   "**出补丁**：交给模型只改该改的那一处，过闸之后才往下走。" % state.get("fix_py"))
            if deps.evidence_run is not None:
                say += ("\n⚠️ 这一步会**先跑一遍那份旧脚本**（真页面 + **一次真实提交**）："
                        "拿到「它停在哪儿、它自己打了什么」，作为**给模型的证据** —— "
                        "没有这份证据，模型只能瞎改。")
            else:
                say += ("\n⚠️ 这个部署**没接「跑一遍拿证据」那根线**：模型手上只有失败那一行，"
                        "改出来的稿很可能只是瞎改（实测过：只改掉一个空格 / 一处没改）。")
            fixed = ["（老写法：这一趟不改数据表，改的是源码本身）"]
        else:
            say = ("这次是**修站**，不重新探索：拿的是现成的那份 py（%s，%d 步），"
                   "按当前的识别代码把每一格的身份重判了一遍。" % (
                       state.get("fix_py"), int(state.get("fix_plan_steps") or 0)))
            fixed = notes
            if notes:
                say += " 改动：" + "；".join(notes)
        out = _enter(state, caps, "explore", say,
                     facts=dict(_brief_facts(state, deps, _missing_knobs(state, deps)),
                                **{"模式": "修站（MODE_FIX）", "底稿": state.get("fix_py"),
                                   "写法": ("老写法（线上那一族）" if legacy
                                            else "模板形（STATES/FILLS）"),
                                   "改法": ("整份源码出补丁" if legacy
                                            else "按当前识别代码重判每一格的身份"),
                                   "底稿步数": int(state.get("fix_plan_steps") or 0),
                                   "改了哪些格": fixed,
                                   "探路": "**没有探路**（修站这条路不探索：账本/产物已经有了）"}))
        if _held(out):
            return out
        if legacy:
            #: ★ 跑一遍旧脚本拿证据（**闸之后**才做：它动真页面 + 一次真提交）。
            ev = _evidence_run(state, deps)
            out["fix_evidence"] = ev
            out["explore_say"] = say + "\n\n—— 旧脚本现在停在哪儿（这一段是要给模型的证据）：\n" + ev
        else:
            out["explore_say"] = say
        out["explore_reached_success"] = None      # 没探路 ⇒ **量不到**，不是「没走到」
        return out

    spent = _explore_spent(state)
    budget = _budget_left(caps, spent)
    resume_from = list(state.get("resume_from") or [])
    resume_note = str(state.get("resume_note") or "")
    say = ("接下来要打开真浏览器，把「%s」按这个目标走一遍：「%s」。"
           "这一步会动到真页面（点、填、滚），探完把「怎么走」记下来。" % (state["url"], state["goal"]))
    if resume_from:
        say += ("这一趟**接着上一趟走**：开头先照账本重放 %d 行（0 模型调用），再从断点接着探。"
                % len(resume_from))
    # ⚠️ 第一道闸带着**开场白那几项**（`_brief_facts`）：`intake` 不设闸之后，
    # 人是在这一道闸上第一次（也是唯一一次）确认「这就是我要开的那一趟」——
    # 少摊一项就是让他少看一半信息去点「继续」。
    facts = _brief_facts(state, deps, _missing_knobs(state, deps))
    facts["预算"] = _budget_say(budget, spent)
    if resume_from:
        facts["重放"] = _resume_facts(resume_from, resume_note)
    out = _enter(state, caps, "explore", say, facts=facts)
    if _held(out):
        return out

    # ⚠️ 这里**不接** `_Stop`（它继承 BaseException，就是为了不被吞成工具失败 ——
    #    browser_agent:206）。人喊停的信号必须原样穿出去，不许被降级成「探路失败」。
    #
    # ── 「走到成功文案没有」＋**有界重探**（2026-09-17 第十二轮，控制器裁定）──
    #
    # 原先没有任何一处检查过「这一趟有没有见到成功文案」（`success_text` 只在
    # intake / draft / selftest 三处被用）→ 拿一条**死胡同账本**去定稿+自测**必然白跑**
    # （第九轮实测：30 分钟全废）。
    #
    # 裁定：**为 False → 自动重探，最多 2 次；仍为 False → 停，如实报，不进入定稿+自测。**
    # 为什么敢重探：它是**便宜、有界、非破坏**的（一趟 ~150 秒；最多 2 趟），
    # 而且**它本身就是「能不能避开那条死路」的解法** —— 实测「匹配不上」与答案相关
    # 而非必然（一支成功、一支失败），换一组随机答案就可能走通，
    # 于是**不必先知道「哪个答案触发它」**。
    # 代价（控制器认了）：若那条路是地区/邮编决定的、重探永远走不通，
    # 最多白花 2 趟探索然后**停下如实报** —— 不会产出假成功，也不会无限重试。
    #
    # ⚠️ **Task 6 起多了一道闸**（`_worth_retrying`）：重探只对「这一趟是对这条路的一次
    #    完整观察」有意义。窗口没了 / 预算花光了 / 人喊了停 —— 重探什么也做不到，
    #    而每一趟都**要花一个真窗口**（一趟 ~150 秒，而窗口只活 25–33 分钟）。
    attempts = []
    journeys = []

    def pass_once(n: int):
        """探一趟，并把这一趟记进 `attempts`（重探那几趟与第一趟走的是同一条路）。"""
        book = deps.explore(state["url"], state["goal"], budget=budget,
                            should_pause=deps.should_pause,
                            resume_from=resume_from or None,
                            resume_note=resume_note,
                            window_alive=deps.window_alive,
                            # ⚠️ **人给的那句成功文案**（Task 15）：探路靠它判断
                            # 「这一趟已经成了 ⇒ 当场收摊」，不再往下点（过了那条线之后
                            # 每一个动作都可能是重复的真实请求）。图上**只有这里**能给得出它
                            # —— 它活在 state 里（载荷 → intake → state），而 `explore()`
                            # 原先收不到。少传这一个参数：这一趟会照旧在成功之后继续点下去
                            # （2026-09-20 真站那一趟的形状），而且**没有任何地方会响**。
                            success_text=state.get("success_text"))
        journeys.append(book)
        attempts.append({"n": n,
                         "reached": _explore_reached_success(book, state.get("success_text")),
                         "steps": len(book.steps), "stop": getattr(book, "stop_reason", ""),
                         "answers": _explore_answers(book)})
        return book

    journey = pass_once(1)
    for n in range(2, EXPLORE_ATTEMPTS + 1):
        if not _worth_retrying(attempts[-1]["reached"], journey, resume_from):
            break
        note = ("⚠️ 第 %d 趟探路**没有在页面上见到成功文案** —— 账本里很可能没有那条通向"
                "成功的路（拿它去定稿+自测会白跑，第九轮实测过）。**自动重探一趟**"
                "（换一组随机答案；最多重探 %d 次）。" % (n - 1, EXPLORE_ATTEMPTS - 1))
        journey.note(note)
        journey = pass_once(n)
    reached = attempts[-1]["reached"]
    out["journey"] = journey
    out["explore_say"] = _journey_say(journey)
    out["explore_reached_success"] = reached
    out["explore_attempts"] = attempts
    # **这一趟的消耗加回 job 级累计**（§1.8）—— 下一趟（或下一次 `reopen`）从这里减。
    # ⚠️ 它必须在**每一条出口**上都加（包括下面那几条提前 return 的），
    #    否则「没走完就停」的那几次的花费会凭空消失（而它们恰恰是最贵的几次）。
    out["explore_spent"] = _spent_after(spent, journeys)
    if len(attempts) > 1:
        # **两次账本的差异**（问题 2/3 的答案顺手就有）：各自填了什么、哪一趟没走通
        out["explore_attempts_note"] = _attempts_note(attempts)
        journey.note(out["explore_attempts_note"])
    stop = str(getattr(journey, "stop_reason", "") or "")
    if stop not in FINISHED_EXPLORATION:
        # **停因排在「没见到成功文案」前面**（顺序有讲究）：这一趟**根本没走完**的时候，
        # 「没见到成功文案」是必然的、也是没有信息的 —— 拿它当结论会写出**假话**：
        # 窗口死掉那一趟会报「重探了 1 趟都没在页面上见到成功文案」（而一趟都没重探）。
        # **窗口没了**在这里是单独一种结局（§1.8）：它说得出理由，而且账本还在 ——
        # 处置与「预算走完」相反（一个该续跑，一个该人看），不许混成一个。
        out["end_reason"] = {"paused": END_PAUSED,
                             "window_gone": END_WINDOW_GONE}.get(stop, END_EXPLORE_UNFINISHED)
        out["end_note"] = _unfinished_note(stop, journey)
        return out
    if reached is False:
        # ⚠️ 人话要说**真发生的事**：没重探就别写「重探了 N 趟」（那种假话本片已经收过一次）。
        retries = len(attempts) - 1
        if retries:
            note = ("⚠️ **重探了 %d 趟都没在页面上见到成功文案** —— 停下，如实报，"
                    "**不进入定稿 + 自测**（拿一条走不通的账本去定稿+自测是必然白跑）。"
                    "两次账本的差异见上。判据一个字没放宽：要不要改判据得人或控制器点头。"
                    % retries)
        else:
            note = ("⚠️ **这一趟没在页面上见到成功文案**，而且**没有重探** ——"
                    "这一趟的重放**走得不干净**（没走完，或者抖过一遍）。"
                    "**为什么走不干净，系统分不出**：可能是窗口在重放途中抖了，"
                    "也可能是页面上找不到那一步的元素 —— 两条都会走到这儿（判据只看数）。"
                    "所以不重探：这一趟不算对这条路的一次干净观察，"
                    "重探只会把同一段在真页面上再撞一遍。停下，如实报，"
                    "**不进入定稿 + 自测**（拿一条走不通的账本去定稿+自测是必然白跑）。")
        journey.note(note)
        out["end_reason"] = END_EXPLORE_UNFINISHED
        out["end_note"] = note
    return out


def _worth_retrying(reached, journey, resume_from) -> bool:
    """这一趟没见到成功文案 —— **还值不值得再探一趟**（重探真窗口要花钱）。

    ⚠️ 判据是 `stop_reason`（R-E6），**不是 `stall_rounds`**：那个数是「连着几轮没推进」的
    **计数**，而**一次干净走完的探路也常是 1**（`_PlanWatch.finish()` 补结算最后一轮）——
    拿它当布尔量用，正常走完的探路会全被判成停滞。停滞只有 `plan_stalled` 这一个写法。

    不重探的四种停法 + 一条**干净度**判据：

    - `window_gone`：窗口没了 —— 重探只会再去开一次会话（多半直接炸）；该走 `reopen`；
    - `budget_steps` / `budget_rounds`：预算已经花掉了（job 级累计之后起点只会更低）；
    - `paused`：人喊了停 —— 重探是**无视人的话**；
    - **重放走得不干净**（`browser_agent.replay_went_clean` 为假）：要么**没走完**，
      要么**重来过第二遍**（重放期间出现过一次 `_WindowGone`）—— 两条都说明
      「这一趟不是对这条路的一次干净观察」，重探只会把同一段在真页面上再撞一遍。
      ⚠️ **「为什么」系统分不出**（Task 6 修复轮 3 就地改正）：判据只看数，
      而走到这个数的路有两条 —— 窗口真抖过 / **页面上找不到那一步的元素**（窗口全程活着）。
      别把它一律说成前者（复审在同一处证伪过这条等价，见 `R2-note-blames-the-window`）。

    **留着**的（`model_done` / `ended` / `plan_stalled`）都是「这一趟对这条路做了一次干净的
    观察、只是没走到成功」—— 换个随机答案可能走通，那正是重探的立意。

    ⚠️ **「见到了成功文案而停」（`browser_agent.STOP_REACHED_SUCCESS`，Task 15）走不到这里**
    —— 最上面那条 `if reached is not False: return False` 先拦住了它：那个停因的定义就是
    「页面上见到了那句文案」⇒ `_explore_reached_success` 必为 True ⇒ 这一趟**本来就不该重探**
    （重探 = 在真页面上把同一段再撞一遍）。这里写下来是因为上面那张名单看着像「所有停因」。

    ⚠️ **它同时是「不许内外两层 3 次叠加」的那根结构线**（复审 Q3 / 修复轮 2 的 ②）：
    `replay` 内部最多 `REPLAY_ATTEMPTS=3` 遍；而**烧了不止一遍的那一趟一定是最后一趟**
    ⇒ **一次 `explore` 节点执行**（= 一趟续跑）里，重放遍数 **≤ 1 + 1 + 3 = 5**（不是 9），
    换会话 **≤ 2 次**（不是 6）。这个界**是紧的**：前两趟干净（各 1 遍）、最后一趟烧满 3 遍
    就取到 5（复审 P3 实测 `[1,1,3]`，旁无第二个组合 —— 趟数被 `EXPLORE_ATTEMPTS=3` 封死）。

    ⚠️ **这个界的作用域是「一次节点执行」，不是「全场」**（Task 6 修复轮 3 就地改正，
    复审实测；原先那句写的是「**全场**重放遍数 ≤ 1+1+3 = 5」—— **按字面是假的**）：
    这根闸的输入是**那一个 `Journey`**（`replay_went_clean(journey, …)`），所以它管住的
    粒度就是一次 `explore` 节点执行。而 `reopen` 会**重进一次 `explore` 节点**
    （`service.py` 的 `update_state(..., as_node=…)`；图里 `explore` 没有自环）——
    每次重进都拿到**一份重置的 5**，而 `reopen` 的**次数没有上限**（`service.py` 自己写着）。
    复审构造（P7，服务级 / 真账本）：同一个 job、**2 次 `reopen` ⇒ 10 遍重放**
    （`attempts` 序列 `[1, 1, 3, 1, 1, 3]`，外加 13 次开会话）；到第 3 次 `reopen` 才走不动，
    **而挡住它的是预算**（`max_rounds` 花光 ⇒ `budget_steps` 就地停），**不是这根闸**。
    所以一个 job 的总数是 `5 × 能被接住的 reopen 次数`，**真正兜底的是预算**。
    ⚠️ **别拿「≤5」去估一个「窗口反复抖 + 人按 reopen 接着跑」的 job 的最坏成本** ——
    会**少算一半**（10 遍 × `MCP_TIMEOUT_S=120s` ≈ 20 分钟，还没算那几趟各自跑完模型循环）。

    ⚠️ **「≤5」原先写错过一次**（修复轮 1）：那时只判「没走完」，而**第 3 遍可以走通** ——
    「用满 3 次尝试」并不等于「被打断」，于是 3 趟 × 3 遍 = 9 仍然够得着（复审构造出来了）。
    现在 `attempts` 由 `replay` 自己报出来，判据看的是「**试了几遍**」，那一形才被挡住。
    兜底的那条边界仍然如实写着：**趟数**由 Task 3 的 `EXPLORE_ATTEMPTS=3` 管着，
    而那 3 趟是**真探路**（窗口活着、每趟在真页面上走一段、会真提交动作）。

    ⚠️ **已知代价（Task 3 遗留 2，没量过）**：`plan_stalled` 也会再探 2 趟 ≈ 2 个真窗口。
    留着它的理由：停滞与「这一趟的随机答案」有关，而重探正是为那个加的。
    **真实的界是「3 趟 × 各自一份满预算 + 3 个真窗口」**（复审实测：三趟拿到的预算是
    同一个 —— 节点入口算一次，不是每趟递减；`explore_spent` 只在**跨节点**（`reopen`）时才减），
    而且每一趟都会**把同一段前缀在真页面上再做一遍**（这笔代价没进 `explore_spent` 的账）。
    要不要收掉它，等 Task 7 的真站验收量出「停滞重探到底有没有用」再说。
    """
    if reached is not False:
        return False
    if not browser_agent.replay_went_clean(journey, resume_from or []):
        return False                      # 这一趟的重放不干净（详见 docstring）
    stop = str(getattr(journey, "stop_reason", "") or "")
    return stop not in ("window_gone", "budget_steps", "budget_rounds", "paused")


def _explore_spent(state) -> dict:
    """这个 job 在探路上**已经花掉多少**（键缺了就当 0 —— **不猜**一个数出来）。"""
    spent = state.get("explore_spent") or {}
    return {key: int(spent.get(key) or 0) for key in ("steps", "rounds", "attempts")}


def _budget_left(caps: Caps, spent: dict) -> browser_agent.Budget:
    """**这一次**能花多少 = `Caps` 的上限 − 这个 job 已经花掉的（§1.8）。

    ⚠️ 夹到 0（不给负数）：花超了就是花超了，**如实**变成「一步都不许走」——
    这一趟会以 `budget_steps` 停住（`END_EXPLORE_UNFINISHED`，该人看），
    而不是一个看不出是配置问题的负数。
    """
    return browser_agent.Budget(
        max_steps=max(0, int(caps.explore_steps) - spent["steps"]),
        max_rounds=max(0, int(caps.explore_rounds) - spent["rounds"]))


def _budget_say(budget: browser_agent.Budget, spent: dict) -> str:
    """闸口上那句预算（人话）。job 级累计之后必须说清**这一次还剩多少** ——
    只说上限的话，人看到的是个**已经不对的**数（前面那几趟已经花掉了）。"""
    said = "最多 %d 步 / %d 轮（防跑飞，不是省钱）" % (budget.max_steps, budget.max_rounds)
    if spent["steps"] or spent["rounds"]:
        said += ("—— 这是**这个 job 还剩的**：前面几趟已经花掉 %d 步 / %d 轮。"
                 % (spent["steps"], spent["rounds"]))
    return said


def _resume_facts(rows: list, note: str) -> str:
    """闸口 facts 上的**重放摘要**（A3）：重放几行、其中几个动作、**为什么停在这儿**。

    ⚠️ 它在**节点开工之前**就能算 —— 只依赖账本（`rows` 是 `reopen` 从 journal 切出来的，
    `note` 是同一处算出来的边界理由）。人是在这一步**之前**点「继续」的：他得先知道
    系统打算照账本重走哪几步、走到哪儿停、为什么停在那儿。
    """
    actions = len(browser_agent.replay_actions(rows))
    return ("照账本走回去：%d 行（其中 %d 个动作，0 模型调用）。停在这儿：%s"
            % (len(rows or []), actions, str(note or "（没说明为什么停在这儿）")))


def _spend_of(journey) -> dict:
    """一趟探路花了多少（§1.8 的口径）：

    - `steps`：**模型驱动的那几步** —— 重放的步**不计**（它不花模型的钱）；
    - `rounds`：`journey.rounds`。⚠️ 被 `_Stop` 打断的那几趟它是 0，而那个 0 的意思是
      **没量到**（`browser_agent.rounds_measured`）—— 于是 job 级轮数是个**偏小**的数，
      它只会让下一趟的轮预算**更松**，不会造成假成功。步数那一项是精确的，硬线在它上面；
    - `attempts`：**这算一趟**（+1），**再加上重放的那些动作** —— 设计注 §1.8 的原话是
      「重放的步不计入 `steps`，但要计入 `attempts`（它有真动作、有代价）」。
      所以这个数**比「探了几趟」大**：名字容易读错，口径在这里写死。
    """
    steps = list(getattr(journey, "steps", None) or [])
    replayed = [s for s in steps if str((s or {}).get("origin") or "") == "replay"]
    return {"steps": len(steps) - len(replayed),
            "rounds": int(getattr(journey, "rounds", 0) or 0),
            "attempts": 1 + len(replayed)}


def _spent_after(spent: dict, journeys: list) -> dict:
    """`spent` + 这几趟的消耗。

    ⚠️ **每一趟都要算**（重探的那几趟也花掉了真窗口与模型轮数），而且这个值要在
    `_explore` 的**每一条出口**上写回去 —— 否则「没走完就停」的那几次的花费会凭空消失，
    而它们恰恰是最贵的几次。
    """
    out = dict(spent)
    for journey in journeys:
        for key, value in _spend_of(journey).items():
            out[key] = out.get(key, 0) + value
    return out


#: 补丁最多试几次：第一次没过闸 / 没套上，把**逐条**原因回灌再补一次。
#: ⚠️ 【我量的·2026-09-21】这个数是拿实测定的：非思考模型答一次 **4 秒 / 583 token**，
#: 所以「再补一次」比「停下等人重发起」便宜得多；而它第一次**抄错了上下文那一行**
#: （套用那层严格拒了 —— 拒得对）。两次都过不了就停下，把原因摆出来。
PATCH_ATTEMPTS = 2


#: 老写法那一版的改动最多摆多少字（给人看的，见 `_patch_diff`）。
PATCH_DIFF_CHARS = 4000


def _patch_diff(old: str, new: str) -> str:
    """这一版**改了哪里**（unified diff 形状，人看）—— 摆在自测 / 交付那两道闸的事实里。

    为什么要有它（2026-09-21 真跑时看见的缺口）：老写法那一版是**模型整段改**出来的，
    而闸上那几格只说「第几版 / 几行」—— 人要在**放它去跑真页面**之前看见**它动了什么**，
    不然那一道「继续」是按在一句概括上点的。
    ⚠️ **截断**并说清截断（人看的东西不怕长，怕的是**看不完还不知道没看完**）。
    """
    text = "\n".join(difflib.unified_diff(old.splitlines(), new.splitlines(),
                                          fromfile="改前", tofile="改后", lineterm="", n=2))
    if len(text) > PATCH_DIFF_CHARS:
        text = (text[:PATCH_DIFF_CHARS]
                + "\n…（后面还有 %d 字没摆出来）" % (len(text) - PATCH_DIFF_CHARS))
    return text or "（这一版与旧脚本**逐字一样** —— 一处都没改）"


def _patch_draft(state, deps: Deps, feedback: dict) -> tuple:
    """**老写法那份 py 的这一次稿**：整份源码交给 `deps.patch_source`，交回来的过闸。

    返回 `(src, bad)`：`bad` 非空 = 这一版**没有出来**（逐条人话），此时 `src` 是空串。

    ⚠️ **没接上那根线**（`deps.patch_source is None`）时**停下并点名** —— 与
    `should_pause` / `set_viewport` 同一条规矩：「这个部署没接改稿的手」与
    「这份 py 修不了」是两件事，写成一件就是编话。
    ⚠️ 打回的原因**逐条**回灌（`fix.check_patch` 的原文），不概括 ——
    概括一次，模型就看不到「到底是哪一条没过」。
    """
    if deps.patch_source is None:
        return "", ["这个部署**没接「改稿」那根线**（`Deps.patch_source`）—— "
                    "老写法那一族（线上那 66 份 py 全是这一族）只能靠它改。"
                    "接上一个会出补丁的模型再发起；没接上就停在这儿，"
                    "**不许**把它写成「这份 py 修不了」（那是另一件事）。"]
    old = str(state.get("fix_src") or "")
    said: list = []
    #: ★ 证据走 `feedback` 递进 `patch_source`（那根线是「模型看得到的输入」那条）——
    #: 【我量的·2026-09-21】有没有它，出稿是两个东西：没有 ⇒ 只改一个空格 / 一处没改；
    #: 有 ⇒ 一份有推理、过闸的补丁（交接 §3.6c）。
    fb = dict(feedback or {}, fix_evidence=str(state.get("fix_evidence") or ""))
    for attempt in range(1, PATCH_ATTEMPTS + 1):
        try:
            reply = deps.patch_source(old, fb)
        except Exception as exc:                  # noqa: BLE001 —— 那一路任何一种炸法都在这儿
            return "", ["改稿那一步炸了：`%s: %s`" % (type(exc).__name__, str(exc)[:400])]
        #: 回话 → 新源码：**有 `@@` 就机械套 diff**（严格，对不上就抛），整份 py 照收，
        #: 都不是就红 —— 不许把一段散文当补丁（`source_from_reply`）。
        try:
            src = fix_mod.source_from_reply(old, reply)
        except ValueError as exc:
            said = [str(exc)]
        else:
            said = fix_mod.check_patch(old, src)
            if not said:
                return src, []
        if attempt < PATCH_ATTEMPTS:
            #: 原因**逐条**走 `feedback["violations"]` 回灌（那是模型看得到的线）——
            #: `hints` / `diagnosis` 原样留着，只换这一格。
            fb = dict(fb, violations=[str(x) for x in said])
    return "", said


def _patch_failed_say(bad: list) -> str:
    """这一版补丁没出来时，摆在闸上的那句话 —— **逐条**列出没过闸的原因。"""
    return ("这一版补丁**没出来**（没过闸，一份都没落盘、也没往下走）：\n· "
            + "\n· ".join(str(b) for b in bad)
            + "\n（重发起一次就带着这些原因再补一版；闸只判结构，改得对不对由自测和人说了算。）")


def _draft(state, deps: Deps, caps: Caps) -> dict:
    """把账本翻译成 py 源码（`template.render`），**带上回灌的证据**。

    回灌的东西一律走 `feedback` 递给 `deps.write`：违规行（指着行说）、诊断证据
    （哪一遍、卡在第几步）、以及人在闸口说过的话。
    """
    was = _feedback(state)
    out = _enter(state, caps, "draft", _draft_say(state, was),
                 facts={"violations": was["violations"], "diagnosis": was["diagnosis"],
                        "第几版": list(state.get("visits") or []).count("draft") + 1,
                        # ★ 给模型的那份证据，人也看得见（不然闸上判不了「这稿有没有依据」）
                        "给模型的证据（旧脚本停在哪儿）": state.get("fix_evidence")})
    if out.get("end_reason"):
        return out
    # 人在**这一道闸**上说的话，属于**这一版**稿（所以 feedback 在闸之后组装）
    feedback = _feedback(state, hints=out.get("hints"))
    # ── 底稿从哪来，三种走法 ──────────────────────────────────────────
    #
    #  · **老写法**（`fix_style == "legacy"`，B 线 ③ 乙）：线上那一族 py 里**没有**
    #    `STATES/FILLS` 那张数据表可改 —— 所以整份源码交给 `deps.patch_source` 出补丁，
    #    过闸（`fix.check_patch`）才许往下走。⚠️ 这条支**排在模板那条之前**。
    if state.get("fix_style") == "legacy":
        src, bad = _patch_draft(state, deps, feedback)
        if bad:
            out.update({"end_reason": END_DRAFT_FAILED, "end_note": _patch_failed_say(bad)})
            return out
        out.update({"states": [], "fills": {}, "success_text": state.get("success_text"),
                    "src": src, "violations": [],
                    "src_diff": _patch_diff(str(state.get("fix_src") or ""), src),
                    # 这一版就是为那次打回写的（与模板那条同一个道理，见下面那段注释）。
                    "revised_at": "", "diagnosis": None})
        return out

    # ── 底稿从哪来：探索的账本，**或者**修站那条路读进来的旧 py ────────────────
    #
    # 两条路的形状是**同一个**（`states` / `fills`）—— 这正是「修」能复用整条下游
    # （lint / 自测 / 交付）的原因：产物怎么写、怎么自测、往哪落，修与建**一模一样**，
    # 差别只在「那份 states/fills 是从哪来的」。
    if state.get("fix_states"):
        spec = {"site": state["site"], "success_text": state.get("success_text"),
                "states": state.get("fix_states"), "fills": state.get("fix_fills") or {}}
        journey = None
    else:
        journey = state.get("journey")
        if journey is None:
            out.update({"end_reason": END_DRAFT_FAILED,
                        "end_note": "写不了：这次没有探路账本（没有账本就没有「怎么走」）。"})
            return out
        spec = {"site": state["site"], "success_text": state.get("success_text"),
                "states": journey.states(), "fills": journey.fills()}
    spec = deps.write(spec, feedback)
    try:
        src = template.render(spec["site"], spec["success_text"], spec["states"], spec["fills"],
                              provenance=_provenance(state, deps, report=None))
    except ValueError as exc:
        # 最常见的一种：没人说「什么算成功」。**不猜** —— 猜出来的成功判据就是
        # 「跑到底再谎报成功」的入口（template.py 也拒这种产物）。
        out.update({"end_reason": END_DRAFT_FAILED,
                    "end_note": ("先别写 py：%s\n（成功判据只有人知道 —— §6.1：页面能告诉 agent "
                                 "**机制**，只有人能告诉它**意图**。）" % exc)})
        return out

    out.update({"states": spec["states"], "fills": spec["fills"],
                "success_text": spec["success_text"], "src": src, "violations": [],
                # 这一版就是为那次打回写的 → 把路上的那个标记收掉（`revisions` 留着当记录）。
                # 不清的话路由会一直把它往回送（`_after_lint` 会以为「刚被人否过」）。
                "revised_at": "", "diagnosis": None})
    return out


def _lint(state, deps: Deps, caps: Caps) -> dict:
    """契约检查（Task 4）：产出的 py 里有没有手拼 JS 这类写模式。"""
    out = _enter(state, caps, "lint", "接下来要把刚写好的这一版逐行过一遍契约检查（重点是手拼 JS）。",
                 facts={"检查的是": "刚写好的那一版（%d 行）" % len((state.get("src") or "").splitlines())})
    if _held(out):
        return out

    violations = [_violation_dict(v) for v in (deps.lint(state["src"]) or [])]
    out["violations"] = violations
    if not violations:
        return out

    out["lint_bounces"] = int(state.get("lint_bounces") or 0) + 1
    if out["lint_bounces"] > caps.max_lint_bounces:
        out.update({"end_reason": END_LINT_CAP,
                    "end_note": _lint_cap_note(out["lint_bounces"], violations)})
    return out


def _evidence_run(state, deps: Deps) -> str:
    """跑一遍旧脚本 → 那段**人话证据**（没接那根线 / 起不来 / 没窗口，都如实说）。

    ⚠️ **任何一种失败都不许静默**：模型手上的证据少一份、出稿就低一档 ——
    而「这一趟到底有没有证据」必须留在状态里给人看（`fix_evidence`）。
    """
    if deps.evidence_run is None:
        return ("这个部署**没接「跑一遍拿证据」那根线** —— 这一趟模型手上只有失败那一行，"
                "没有「旧脚本停在哪儿」。")
    if not state.get("ws_url") or not state.get("form_file"):
        return ("跑不了那一遍拿证据：**没有可用的窗口或表单数据** —— 这一趟模型手上"
                "只有失败那一行。（没有证据的稿，闸上要按「没验到」读。）")
    try:
        #: ⚠️ `entry_url` 是**承重**的：不给它，那一趟就是在浏览器自己的控制台页上跑
        #: （2026-09-21 实测），拿回来的证据全是废的。给人的选择只有失败证据里那串网址。
        ev = deps.evidence_run(py=state.get("fix_py"), ws_url=state.get("ws_url"),
                               form_file=state.get("form_file"), site=state.get("site"),
                               entry_url=(state.get("entry_url") or state.get("url") or ""))
    except Exception as exc:                      # noqa: BLE001 —— 外面世界
        return ("跑那一遍旧脚本的时候炸了：`%s: %s`（这一趟模型手上没有真证据）"
                % (type(exc).__name__, str(exc)[:200]))
    try:
        return selftest_mod.evidence_say(ev)
    except Exception as exc:                      # noqa: BLE001 —— 证据的形状对不上
        return "那一遍跑了，可证据读不出来：`%s: %s`" % (type(exc).__name__, str(exc)[:200])


def _fresh_session(state, deps: Deps) -> tuple:
    """自测之前换一个**干净会话**（R-F1）。返回 `(ws_url, 一句人话)`。

    为什么值得为它多开一次窗（2026-09-17 裁定，证据在 `fix-frame-through-report.md` §7.2）：
    自测跑在**探路之后**、同一个浏览器会话里，而探路自己已经点过 cookie 同意 ——
    「首次访问才有」的那一步（cookie 横幅）复跑时**元素真的不在页面上**，
    于是自测挂在一个**生产里不会出现**的场景上（生产每单开新窗、每次都清 cookie）。

    ⚠️ **这不是放宽判据**：五遍的判据、`_judge`、`STUCK_LIMIT` 一个字没动 ——
    换的只是**跑的条件**，而换完的那个条件**更接近生产**。这句话别被后人读反。

    三态，都不静默：
      - 没接这根线（部署给不了）→ 照旧用原来那个 ws_url，人话里说明「不是干净会话」；
      - 接上了但换失败（窗口服务抖了）→ 同上（**不**因此判不过；条件差不等于产物不行，
        但要说出来）；
      - 换成了 → 用新 ws_url，人话里说明「换了干净会话」。
    """
    if deps.fresh_session is None:
        return state["ws_url"], ("这一次**不是干净会话**（这个部署没接 `fresh_session`）："
                                 "探路那一趟的 cookie 还在，"
                                 "「首次访问才有」的步骤复跑时元素不会出现 —— 那是环境差，不是产物差。")
    try:
        ws_url = str(deps.fresh_session())
    except Exception as exc:                     # noqa: BLE001 —— 外面世界，什么都可能抛
        return state["ws_url"], ("换干净会话没成（%s）—— 这一次**不是干净会话**，照旧跑；"
                                 "那几遍的结论要按「条件更差」读。" % exc)
    if not ws_url:
        return state["ws_url"], "换干净会话没给出新的 ws_url —— 这一次**不是干净会话**，照旧跑。"
    return ws_url, "换了**干净会话**（关旧窗、开新窗；生产每单都是这么起的）。"


def _selftest(state, deps: Deps, caps: Caps) -> dict:
    """扰动自测（Task 6）：在真浏览器上按扰动序列跑，任一遍挂就不算过（§10）。

    没有窗口就**停**，不许「跳过自测当通过」—— 那是把「没验到」说成「过了」。
    """
    out = _enter(state, caps, "selftest",
                 ("接下来要在真浏览器上按扰动序列跑几遍：正常跑一遍、接着再跑一遍、放慢跑一遍、"
                  "再换个窗口大小跑一遍。**自测通过 ≠ 生产一定过** —— 工具侧跑的浏览器与生产 "
                  "worker 的代理出口/指纹/时序不是一套（规格 §10）。"),
                 facts={"窗口": state.get("ws_url"), "表单数据": state.get("form_file"),
                        # ★ 老写法那一版：人在这儿才第一次能看见「它到底改了什么」——
                        # 这一道「继续」是**放它去跑真页面**的那一下（见 `_patch_diff`）。
                        "这一版改了什么": state.get("src_diff"),
                        "第 2 遍刷新回哪个 URL": state.get("entry_url"),
                        "允许跳过的扰动": list(state.get("allow_skips") or []),
                        "窗口旋钮": "set_viewport=接上了" if deps.set_viewport else "没接上",
                        # R-F1：自测跑在探路**之后**的会话里，而生产每单都是新窗口 ——
                        # 接得上就换一个干净会话（cookie 已同意过的那些「首次访问才有」
                        # 的步骤，在脏会话里元素根本不会出现）。
                        "干净会话": ("会换（fresh_session 接上了）" if deps.fresh_session
                                     else "换不了（没接那根线）—— 这一次的结论要按「条件更差」读")})
    if _held(out):
        return out

    if not state.get("ws_url") or not state.get("form_file"):
        out.update({"end_reason": END_NO_WINDOW,
                    "end_note": ("自测跑不了：没有一个可用的浏览器窗口（或没给表单数据）。"
                                 "**不许跳过自测当通过** —— 没验到的东西说成过了，正是这套系统最贵的谎。"
                                 "先把窗口开起来（§4.6 的前提层；窗口本身只活几分钟，P6）再接着走。")})
        return out

    # 跑到这儿时手上这根线可能没了（恢复同一个 run 的另一个进程没接上）——
    # 那几遍扰动跑不了、又没人允许跳过，就停在这儿点名，别让报告把它记成「产物不行」。
    missing = _missing_knobs(state, deps)
    if missing:
        out.update({"end_reason": END_MISSING_KNOB, "end_note": _knob_note(missing)})
        return out

    # R-F1：**自测要在干净会话里跑**（生产每单都是新窗口 —— 干净会话才是生产里那个场景）。
    # 拿一根新 ws_url；拿不到就照旧跑，但**在 facts 里说清这一次不干净**（不假装）。
    ws_url, session_note = _fresh_session(state, deps)
    out["ws_url"] = ws_url
    out["session"] = session_note

    py = _stage_candidate(state)
    report = deps.selftest(str(py), ws_url, state["form_file"], state["site"],
                           **_selftest_kwargs(state, deps))
    out.update({"candidate_path": str(py), "report": report})
    if not report.passed:
        out["diagnoses"] = int(state.get("diagnoses") or 0) + 1
        if out["diagnoses"] > caps.max_diagnoses:
            _drop_candidate(state)
            out.update({"end_reason": END_SELFTEST_CAP,
                        "end_note": _selftest_cap_note(out["diagnoses"], report)})
    return out


def _diagnose(state, deps: Deps, caps: Caps) -> dict:
    """从自测报告里定位「哪一遍、卡在第几步、什么错」——**只说报告里真有的东西**。"""
    evidence = _diagnosis(state.get("report"))
    # R-84：**这一轮往站方提交了几次**要摆在人眼前。一次提交 = 产物把整个漏斗走一遍
    # = 一次真实的 lead 提交；裁定之前它固定是 3 次/轮，而**没有人看得见这个数**。
    say = ("自测没过：这一轮往站方提交了 %d 次。%s 接下来要拿这份记录去定位，"
           "定位完回 draft 改一版。" % (evidence["submissions"], evidence["say"]))
    out = _enter(state, caps, "diagnose", say, facts={"逐遍结果": evidence["per_run"],
                                                     "证据": evidence,
                                                     # 跑的条件（R-F1）：不是干净会话时，
                                                     # 有些失败是**环境差**，别记到产物头上
                                                     "会话": state.get("session")})
    if _held(out):
        return out
    out["diagnosis"] = evidence
    return out


def _deliver(state, deps: Deps, caps: Caps) -> dict:
    """写 `forms/sites/<site>.py`（带 `PROVENANCE`），并**再查一遍要落盘的字节**。

    为什么最后还要查：`PROVENANCE` 里有自由文本（人给的意图/证据），而它是 lint 之后
    才写进去的 ——「查过的那份」与「交出去的那份」天生不是同一串字节。脏了就不落盘，
    也不许悄悄改一改糊过去。
    """
    out = _enter(state, caps, "deliver", _deliver_say(state),
                 facts={"自测": _selftest_block(state.get("report"),
                                                datetime.datetime.now().astimezone()
                                                .isoformat(timespec="seconds")),
                        "这一版改了什么": state.get("src_diff"),
                        "要写进哪": str(_delivery_path(state))})
    if _held(out):
        return out

    # 闸之后**重算一次**：人在闸上可能待了很久，`generated_at` 该是**落盘那一刻**，
    # 不是「他还没说话的那一刻」。
    prov = _provenance(state, deps, report=state.get("report"))
    try:
        legacy = state.get("fix_style") == "legacy"
        if legacy:
            #: ⚠️ 老写法（B 线 ③ 乙）：**没有 states/fills 可渲** —— 要落盘的就是 `draft`
            #: 那一版源码本身（模型出的补丁，过闸之后**一字未改**）。
            #: 也**不往里塞 PROVENANCE**：那一块是 siteforge 自己产物的惯例，线上那一族
            #: 没有它 —— 往别人的脚本里加东西就不是「只改该改的那一处」了。这一次的
            #: 出身记在运行记录里（`state["provenance"]`），不在产物文件里。
            src = str(state.get("src") or "")
        else:
            src = template.render(state["site"], state.get("success_text"),
                                  state.get("states"), state.get("fills"), provenance=prov)
    except ValueError as exc:                                  # spec 里少了东西（不该到这）
        out.update({"end_reason": END_DRAFT_FAILED, "end_note": "交付前重渲失败：%s" % exc})
        return out

    dirty = [_violation_dict(v) for v in (deps.lint(src) or [])]
    if dirty:
        _drop_candidate(state)
        #: 那句「多半是 PROVENANCE…」只对**模板形**成立：老写法那一版没有 PROVENANCE
        #: 那一段，脏了就是模型那一版本身带着手拼 JS（B 线 ③ 乙）。
        hint = ("PROVENANCE 里的自由文本撞上了写模式" if state.get("fix_style") != "legacy"
                else "模型交回来的那一版本身就带着手拼 JS")
        out.update({"end_reason": END_DELIVER_LINT,
                    "end_note": ("**没有落盘**：要写出去的那串字节自己没过契约检查（多半是 %s）：%s\n"
                                 "一个字节都没写 —— 宁可没有，也不交一份自己都没过检查的产物。"
                                 % (hint, "；".join("第 %s 行：%s" % (v["line"], v["message"])
                                                    for v in dirty)))})
        return out

    path = _delivery_path(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(src, encoding="utf-8")
    _drop_candidate(state)
    out.update({"py_path": str(path), "provenance": prov, "src": src,
                "end_reason": END_DELIVERED, "end_note": _delivered_note(state, path)})
    return out


# ─────────────────────────────── 路由 ───────────────────────────────


def _next(node: str) -> Callable:
    """往前走 —— 除非已经有 `end_reason`（人喊停 / 前面某一步停了）。"""
    def route(state) -> str:
        return END if state.get("end_reason") else node
    return route


def _after_lint(state) -> str:
    if state.get("end_reason"):
        return END
    if state.get("revised_at"):            # 人否了这一版 → 回 draft 带他的话（§6「review」那一行）
        return "draft"
    return "draft" if state.get("violations") else "selftest"


def _after_selftest(state) -> str:
    if state.get("end_reason"):
        return END
    if state.get("revised_at"):
        return "draft"
    report = state.get("report")
    return "deliver" if (report is not None and report.passed) else "diagnose"


def _after_deliver(state) -> str:
    if state.get("revised_at"):
        return "draft"                     # 人在这儿把这一版否了：别写，回去重写
    return END


# ─────────────────────────────── 拼图 ───────────────────────────────


def build(*, checkpointer, deps: Optional[Deps] = None, caps: Optional[Caps] = None):
    """编译这张图。

    参数：
        checkpointer  **必需**（R-19）。没有它，`interrupt()` 之后**恢复不了** ——
                      图会安静地停在那儿，而谁也没法让它再动一下。存哪儿是接线的事
                      （Task 8：Postgres；本地跑用 `InMemorySaver`），但必须有。
                      存 dataclass 的 saver 记得 `allowlisted(...)`。
        deps          跟外面世界的接触面（全部可注入；测试里全是桩）。
                      **窗口层那根线（`set_viewport`）就在这里** —— 不给它，自测的第 4 遍
                      没法真跑，图会在 `intake`（**结束这一趟**，不设闸）或 `selftest`
                      （**停在闸上**）点名（见模块 docstring §2.5）
        caps          硬上限（`state.Caps`）

    开场白里那几项**必需**的输入（`success_text` / `ws_url` / `form_file`）见 §2.5：
    缺了它们，图停在那儿点名，不会写出一份「没验到也算过」的产物。

    用法：

        app = build(checkpointer=allowlisted(InMemorySaver()))
        cfg = {"configurable": {"thread_id": "job-1"}}
        app.invoke({"url": …, "goal": …, "success_text": …}, cfg)   # 停在第一道闸前
        app.invoke(Command(resume="continue"), cfg)                 # 人说了「继续」
        # 想看停在哪儿：`app.get_state(cfg).next` / `out["__interrupt__"][0].value`
    """
    if checkpointer is None:
        # 这一条是**故意**报错的：它正是 R-19 那个根因（「没有 saver」=「中断之后恢复不了」）。
        # 报错比「安静地给你一张恢复不了的图」好一万倍。
        raise ValueError(
            "图必须带 checkpointer（R-19）：没有它，`interrupt()` 之后**恢复不了** —— "
            "人喊停、人纠正都会变成「停在那儿再也动不了」。存哪儿是接线的事"
            "（Task 8 用 Postgres），但**必须有**；本地跑测试用 `InMemorySaver`。")

    deps = deps if deps is not None else Deps()
    caps = caps if caps is not None else Caps()

    graph = StateGraph(SiteState)
    graph.add_node("intake", lambda s: _intake(s, deps, caps))
    graph.add_node("explore", lambda s: _explore(s, deps, caps))
    graph.add_node("draft", lambda s: _draft(s, deps, caps))
    graph.add_node("lint", lambda s: _lint(s, deps, caps))
    graph.add_node("selftest", lambda s: _selftest(s, deps, caps))
    graph.add_node("diagnose", lambda s: _diagnose(s, deps, caps))
    graph.add_node("deliver", lambda s: _deliver(s, deps, caps))

    graph.add_edge(START, "intake")
    graph.add_conditional_edges("intake", _next("explore"), ["explore", END])
    graph.add_conditional_edges("explore", _next("draft"), ["draft", END])
    graph.add_conditional_edges("draft", _next("lint"), ["lint", END])
    graph.add_conditional_edges("lint", _after_lint, ["draft", "selftest", END])
    graph.add_conditional_edges("selftest", _after_selftest, ["deliver", "diagnose", "draft", END])
    graph.add_conditional_edges("diagnose", _next("draft"), ["draft", END])
    graph.add_conditional_edges("deliver", _after_deliver, ["draft", END])
    return graph.compile(checkpointer=checkpointer)


# ───────────────────────── 小工具：名字、人话、证据 ─────────────────────────

def site_name(url: str) -> str:
    """从 URL 推一个站点短名（产物文件名、logger 名、路由表都用它）。

    规则朴素：取主机名的第一段有意义的部分（去掉 `www.` 这类），非字母数字压成 `-`。
    推出来的名字**只是默认值** —— 调用方给了 `site` 就用它的。
    """
    host = re.sub(r"^[a-z]+://", "", str(url or ""), flags=re.I).split("/")[0]
    host = host.split("@")[-1].split(":")[0]
    parts = [p for p in host.split(".") if p and p.lower() not in ("www", "m", "co", "com",
                                                                   "org", "net", "io", "uk", "cn")]
    stem = parts[0] if parts else host
    return re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-") or "site"


def _journey_say(journey) -> str:
    """人话：探路是怎么结束的（`Journey.notes` 里本来就有这句话）。

    ⚠️ **抬头那一句必须与真实的停因对得上**（Task 3 遗留 1）：`plan_stalled` 与
    `window_gone` 都是**说得出理由**的停 —— 抬头写「停得不明不白」是**假话**，
    而人话就在 `notes` 里（尾巴那句），一句话都没丢。内部停因的 token
    （`plan_stalled` 这种）也不许进人话（M-5）：读这份账的人是非技术的人。

    ⚠️ **有人插过话而它就收尾了**（Task 9 的 R11）那一句**不按位置认**（回归 1 / F4）：
    尾巴只取 `notes[-1]`，而 `service._explore_for.run` 在 `explore` 返回**之后**还会往
    `notes` 追两句（账本 / 时间线没记全）—— 押在位置上，旁路坏过一趟那句话就没了。
    所以按**它自己那句话**在不在 `notes` 里认（同一个常量，两个模块共用），
    并且**已经在尾巴上时不重复说**。
    """
    stop = str(getattr(journey, "stop_reason", "") or "")
    notes = [str(n) for n in (getattr(journey, "notes", None) or [])]
    tail = notes[-1] if notes else ""
    if stop == "model_done":
        head = "探路走完了：agent 自己说讲完了。"
    elif stop == browser_agent.STOP_REACHED_SUCCESS:
        # ⚠️ 这一条**必须**说成「走完了」：它是**成功的收尾**（页面上出现了人给的那句
        # 成功文案），不是「停得不明不白」——抬头写成后者是**假话**，而且内部停因的
        # token 也不许进人话（M-5：读这份账的人是非技术的人）。
        head = ("探路走完了：页面上见到了人给的那句**成功文案**（见到就收摊"
                "—— 过了那条线之后每一次点击都可能是重复的真实请求）。")
    elif stop == "paused":
        head = "探路被人喊停了。"
    elif stop.startswith("budget"):
        head = "探路没走完：预算到顶了。"
    elif stop == "window_gone":
        head = "探路停下了：窗口没了（工具连着失败，窗口服务说它已经不在了）。"
    elif stop == "plan_stalled":
        head = "探路停下了：计划停滞（模型连着几轮没有推进）。"
    else:
        head = "探路停得不明不白（%s）。" % (stop or "没说为什么")
    steps = len(getattr(journey, "steps", None) or [])
    said = "%s%s走了 %d 步。%s" % (head, _resume_say(journey), steps, tail)
    if browser_agent.STEER_WRAPPED_UP_NOTE in notes \
            and browser_agent.STEER_WRAPPED_UP_NOTE not in tail:
        said += browser_agent.STEER_WRAPPED_UP_NOTE
    return said


def _resume_say(journey) -> str:
    """这一趟开头**重放了什么**（`Journey.replay`）—— 没重放就是空串（**不假装**）。

    A3：边界那句**必须出现在 `explore_say` 里**（人读的那句话），不只是闸口 facts 里 ——
    边界理由说的是「为什么就走到这儿为止」，那是读这份账的人最先要问的事。
    """
    info = dict(getattr(journey, "replay", None) or {})
    if not info:
        return ""
    why = str(info.get("boundary_reason") or info.get("why") or "")
    return "（这一趟开头照账本重放了 %d 个动作，0 模型调用%s）" % (
        int(info.get("done") or 0), "；停在这儿：%s" % why if why else "")


#: 探路最多几趟（第一次 + 重探）。控制器的裁定：**最多重探 2 次**。
#: 为什么是 2：一趟 ~150 秒，两趟仍是分钟级；而「重探本身」就是「能不能避开那条死路」的解法。
EXPLORE_ATTEMPTS = 3


def _explore_answers(journey) -> list:
    """这一趟探路往表单里**真填/真选过什么**（`标签=值`）—— 两次账本差异的原料。"""
    out = []
    for step in getattr(journey, "steps", None) or []:
        info = ((step or {}).get("result") or {}).get("fill")
        if not info:
            continue
        out.append("%s=%s" % ((info.get("label") or "?")[:18], str(info.get("value"))[:14]))
    return out


def _attempts_note(attempts: list) -> str:
    """把几趟探路的差异说成人话（哪一趟走到成功、答案哪里不一样）。"""
    lines = ["这一次探路跑了 %d 趟（走到成功文案就停）：" % len(attempts)]
    for a in attempts:
        lines.append("  · 第 %d 趟：%s，%d 步，停止原因「%s」；填过：%s"
                     % (a["n"],
                        {True: "**见到了成功文案**", False: "没见到成功文案",
                         None: "判不了（没给判据/没观测）"}[a["reached"]],
                        a["steps"], a["stop"] or "?", "、".join(a["answers"][:6]) or "（没填过）"))
    return "\n".join(lines)


def _explore_reached_success(journey, success_text) -> Optional[bool]:
    """这一趟探路**在页面上见到过成功文案吗**。三态：True / False / **None = 判不了**。

    - `True`：某一步 observe 的正文里含成功文案（与产物的判据**同一口径**：归一化后子串）；
    - `False`：每一步都看过了，一次都没见着 → 账本里很可能没有那条路（见调用处的注释）；
    - `None`：没给成功判据、或这一趟一次 observe 都没有（判不了就**不猜**）。
    """
    wants = [success_text] if isinstance(success_text, str) else list(success_text or [])
    wants = [_norm_text(w) for w in wants if str(w or "").strip()]
    if not wants:
        return None
    seen_any = False
    for step in getattr(journey, "steps", None) or []:
        if (step or {}).get("action") != "observe":
            continue
        seen_any = True
        head = _norm_text((step.get("result") or {}).get("page_text_head") or "")
        if head and any(w in head for w in wants):
            return True
    return False if seen_any else None


def _norm_text(text) -> str:
    """与 `cdp observe` 的 page_text / 产物 `page_signature()` 同口径（空白压成一个空格）。"""
    return " ".join(str(text or "").split())


def _unfinished_note(stop: str, journey) -> str:
    """探路没走完时停下来的**人话**理由（R0：半份账本写不出对的 py）。"""
    if stop == "paused":
        return ("人喊停：在浏览器里就把这次探路停下了，所以没有往下写 py。"
                "已经探到的那部分在账本里（%s）。要接着走就再发起一次。" % _journey_say(journey))
    return ("这次探路没走完（%s），所以**没有**往下写 py：拿半份账本写出来的 py 会看着挺像、"
            "实际有洞 —— 那正是「自信地错」（R0）最贵的形状。这一回的账本留着（走了 %d 步，%s），"
            "人可以看完之后再决定：加预算重探、或者直接说该怎么做。"
            % (_journey_say(journey), len(getattr(journey, "steps", None) or []),
               "；".join(str(n) for n in (getattr(journey, "notes", None) or [])[-2:]) or "没有别的记录"))


def _submission_cap(state, deps: Deps) -> int:
    """这一轮的自测**最多提交几次**（R-84 的硬顶）。

    ⚠️ **图从不下发这个数**（`_selftest_kwargs` 只有 `set_viewport` / `allow_skips` /
    `entry_url` 三个键），所以下面那句 `kw.get(...)` 今天**永远走 fallback** ——
    两边都是 `selftest.MAX_SUBMISSIONS`，判据成立。留着这次查找是为了**将来接线时两边不漂**
    （谁往 `_selftest_kwargs` 里加 `max_submissions`，这里立刻跟着走）。
    别把它读成「有一条现行接线」（`b571ee5` 那一版 docstring 就是这么写的，是错的）。
    """
    kw = _selftest_kwargs(state, deps)
    return int(kw.get("max_submissions", selftest_mod.MAX_SUBMISSIONS))


def _round_spends(name: str, state, deps: Deps) -> bool:
    """这一遍**会不会花掉一次提交**（R-84：一次提交 = 产物把整个漏斗走一遍）。

    有两条**不花次数**的路（`selftest.run` 的阶梯里）：

    - **`rerun` 的 `cdp navi` 没成**时：记 `failed`，但产物**一次都没起来** ⇒ 不花。
      这一条是 I-2 的要害 —— 它会把后面**整个阶梯前移一位**。
    - **`viewport` / `country` 没回调**时：记 `skipped` ⇒ 也不花。

    ⚠️ **两处的「不知道怎么办」是相反的，这是有意的**（控制器裁定：行为保持，docstring 改成事实）：

    - **`rerun` 那一支按「可能不花」算**（给了 `entry_url` 就当作 navi 可能失败 ⇒ 不花）——
      取的是**乐观**那一侧。理由：这一侧错了（真花了而闸以为没花）只会让闸**更容易拦**，
      而它拦的是一个**真会响**的轮次；反过来「按一定花算」就是原来那个**烧窗口的坑**
      （复审实测：缺 `set_viewport` 时不点名，一路跑到第 4 遍才记 `skipped`，
      烧掉真窗口 + 3 次提交之后 `passed=False`，原因还埋在报告里）。
    - **别的不知道的一律按「花」算**（比如「回调给了，但它会不会自己炸」）——
      取的是**保守**那一侧（更不容易误报一个跑不到的扰动）。
    """
    if name == "rerun":
        # 给了 `entry_url` 才可能去导航；没给 = 接着再跑一遍 = 一定花
        return not str(state.get("entry_url") or "").strip()
    knob = (ROUND_NEEDS.get(name) or (None,))[0]
    if knob and getattr(deps, knob, None) is None:
        return False
    return True


def _round_reachable(name: str, state, deps: Deps) -> bool:
    """这一遍**这一轮到底轮不轮得到** —— 从第一遍往下数，前面花掉的次数还没到硬顶就轮得到。

    ⚠️ 为什么不能只看「在 `RUN_NAMES` 里排第几」（`b571ee5` 就是这么写的，**已被证伪**）：
    阶梯里**有的遍不花提交次数**（见 `_round_spends`），不花的那一遍会把后面**整体前移一位**。
    复审实测：默认硬顶 3、`entry_url` 给了而 `cdp navi` 挂了时，**第 4 遍（viewport）真的会跑**
    （它还花掉了第 3 次提交）。所以「第 4/5 遍这一轮根本轮不到」在**可达路径**下是假的。
    """
    cap = _submission_cap(state, deps)
    spent = 0
    for each in selftest_mod.RUN_NAMES:
        if each == name:
            return spent < cap
        if spent >= cap:                 # 到顶了：后面全轮不到
            return False
        spent += 1 if _round_spends(each, state, deps) else 0
    return False


def _missing_knobs(state, deps: Deps) -> list:
    """哪几遍扰动**既跑不了、又没人允许跳过** —— 报出缺的那根线（R-31）。

    一条扰动只有两种活法：**跑得了**（那根线在 `Deps` 上）或者**被人明确允许不跑**
    （写进 `allow_skips`）。两样都不成立时图**停**并把旋钮名字点出来 ——
    不许自己发明一个默认让它跳过去（R-5：跳过的遍不算过），也不许带着它往下走、
    让报告把这件接线的事记成「产物不行」。

    ⚠️ **只拦这一轮真的轮得到的那几遍**（`_round_reachable`）。两个方向都是裁定要的：

    - 轮不到的不拦 —— 给一个跑不到的扰动配一根闸就是「**接上了但不响**」；
    - **轮得到的必须拦** —— 撤掉它更糟（复审实测）：缺 `set_viewport` 时图不点名，
      一路跑到第 4 遍才记 `skipped`，**烧掉真窗口 + 最多 3 次提交之后 `passed=False`**，
      而真正的原因（缺一根线）埋在报告里 —— 那不是「产物不行」。
    """
    allowed = tuple(state.get("allow_skips") or selftest_mod.DEFAULT_ALLOWED_SKIPS)
    out = []
    for name, (knob, what, who) in ROUND_NEEDS.items():
        if name in allowed:
            continue
        nth = list(selftest_mod.RUN_NAMES).index(name) + 1
        if not _round_reachable(name, state, deps):
            continue                     # 这一轮轮不到它 —— 不拦（R-84）
        if getattr(deps, knob, None) is None:
            out.append({"round": name, "knob": knob, "what": what, "who": who, "nth": nth})
    out.sort(key=lambda item: item["nth"])
    return out


def _knob_note(missing: list) -> str:
    """缺旋钮时那段**人话**：缺的是哪一根、谁该给、以及另一条明摆着的路（§10 的诚实条款）。"""
    lines = ["跑不了：自测里有几遍扰动**既没有那根线、又没人允许跳过**，所以这次自测的结论"
             "会是残缺的 —— 而「没验到」不许说成「验过了」（R-5 / §10）。缺的是："]
    for item in missing:
        lines.append("  · 第 %s 遍「%s」需要 `%s=…`；这根线在 %s。"
                     % (item["nth"], item["what"], item["knob"], item["who"]))
    lines.append("两条路，都摆在明面上：")
    lines.append("  ① 把线接上（推荐）：那几遍就真跑，「没验到」的窟窿才算补上；")
    lines.append("  ② 明确放弃它：把 %s 放进开场白的 `allow_skips` —— "
                 "交出来的产物会带着「这一类失败这次**没验到**」。"
                 % "、".join('"%s"' % item["round"] for item in missing))
    lines.append("**不许**默认放过它：没验到的说成验过了，是这套系统最贵的谎。")
    return "\n".join(lines)


def _selftest_kwargs(state, deps: Deps) -> dict:
    """传给 `selftest.run` 的那几个旋钮 —— **只给调用方真给了的**。

    ⚠️ 没给的一律**不传**（不是传 `None`）：Task 6 的默认值自己说了算
    （`allow_skips` 默认只允许跳 country，`entry_url` 没给就只是「接着再跑一遍」）。
    图替它填默认 = 图替它决定「哪些没验到也算过」，那是 R-5 明令不许的事。

    评审 Important 1：原先这里一个旋钮都不传 → 真 `run()` 走 `set_viewport=None` 那一支 →
    第 4 遍记 `skipped` → `_judge` 判不过 → **真跑一次永远到不了 deliver**。
    """
    kw = {}
    if deps.set_viewport is not None:
        kw["set_viewport"] = deps.set_viewport
    #: ★ 老写法那一族（B 线 ③ 乙）：自测那条路要换一套调法（argv 不给 `--trace` 那几个
    #: 开关，证据从运行时进）—— 形状是 intake 那一步**量过**的（`fix.shape_of`），
    #: 这里只是把它带下去。⚠️ 只在**是**老写法时才给这一格：别的路一个字节不变。
    if state.get("fix_style") == "legacy":
        kw["legacy"] = True
    if state.get("allow_skips"):
        kw["allow_skips"] = tuple(state["allow_skips"])
    if state.get("entry_url"):
        kw["entry_url"] = state["entry_url"]
    return kw


def _feedback(state, hints=None) -> dict:
    """回灌给 draft 的东西（违规行 / 诊断证据 / 人说的话）——一处组装，两处消费。

    `hints` 覆盖时以它为准：人在**这一道闸**上说的话属于**这一版**稿
    （`_enter` 刚把它记下来，还没进 state）。
    """
    return {"violations": [dict(v) for v in (state.get("violations") or [])],
            "diagnosis": dict(state["diagnosis"]) if state.get("diagnosis") else None,
            "hints": list(state.get("hints") or []) if hints is None else list(hints)}


def _draft_say(state, feedback: dict) -> str:
    """draft 那道闸上问的话：**先把它为什么被叫回来**说清楚（人话，不是 code）。"""
    version = list(state.get("visits") or []).count("draft") + 1
    head = (
        ("接下来写第 %d 版：把**线上正在跑的那一份 py** 整个交给模型出一版补丁"
         "（老写法里没有 states/fills 可改），过闸之后才往下走。" % version)
        if state.get("fix_style") == "legacy"
        else "接下来写第 %d 版 py（按账本里的「怎么走」填骨架）。" % version)
    if state.get("revised_at"):
        # 人否掉了上一版（§6「人否 → 回 draft 带人的纠正」）——闸口上得把他的话摆出来，
        # 不然这一版看上去像是自己决定重写的
        where = STEP_SAY.get(state["revised_at"], state["revised_at"])
        note = (state.get("hints") or [""])[-1]
        head = ("你在「%s」那儿说这一版不行，所以回来重写一版（这一版按你说的改）：%s"
                % (where, note or "（只说重来，没留下话）"))
    elif feedback["violations"]:
        lines = "；".join("第 %s 行 —— %s" % (v.get("line"), v.get("message"))
                          for v in feedback["violations"])
        head = ("上一版没过契约检查，要重写一版：%s\n（这些是**写动作**上的问题："
                "动页面一律走 cdp 命令，别手拼 JS。规格 §5.2）" % lines)
    elif feedback["diagnosis"]:
        head = "上一版自测没过，要重写一版：%s" % feedback["diagnosis"].get("say", "")
    if feedback["hints"]:
        head += "\n人说过：%s" % "；".join(feedback["hints"])
    return head


def _violation_dict(v) -> dict:
    """一处违规 → 交给下游的朴素 dict（prompt / JSON / 人看的清单都是它）。"""
    get = (lambda k: v.get(k)) if isinstance(v, dict) else (lambda k: getattr(v, k, None))
    return {"line": get("line"), "code": get("code"),
            "message": get("message"), "snippet": get("snippet")}


def _lint_cap_note(bounces: int, violations: list) -> str:
    lines = "；".join("第 %s 行 —— %s" % (v.get("line"), v.get("message")) for v in violations)
    return ("停：这一版 py 被打回 %d 次还是同样的地方不过（%s）。"
            "**反复打回同一处**本身就是「agent 对页面的理解有问题」的信号（§6.3），"
            "再转下去只是烧时间。人接手：看一眼它写的路线，或者直接说该怎么做。" % (bounces, lines))


def _selftest_cap_note(diagnoses: int, report) -> str:
    return ("停：自测挂了 %d 次、修了 %d 轮还是不过，再修下去只是在猜。\n%s\n"
            "人接手：上面是每一遍的结果（哪遍挂、卡在第几步）。"
            % (diagnoses, diagnoses, _report_say(report)))


def _report_say(report) -> str:
    """自测报告的**人话**版本（Task 6 的 `Report.summary()` 就是为这个写的）。"""
    if report is None:
        return "（没有自测报告）"
    summary = getattr(report, "summary", None)
    return summary() if callable(summary) else "（这份报告不会说人话）"


def _diagnosis(report) -> dict:
    """报告 → 证据。**只说报告里真有的东西**：没说「卡在第几步」就不许编一个出来。

    R-84：证据里带一个 **`submissions`**（这一轮往站方真提交了几次）—— 它跟着
    diagnose 那道闸的 facts 一起给人看（**这次出事就是因为这个数没人看得见**）。
    """
    per_run = [{"name": getattr(r, "name", None), "status": getattr(r, "status", None),
                "ok": getattr(r, "ok", None), "failed_step": getattr(r, "failed_step", None),
                "note": getattr(r, "note", "")}
               for r in (getattr(report, "runs", None) or ())]
    submissions = int(getattr(report, "submissions", 0) or 0)
    if report is None:
        return {"run": None, "failed_step": None, "say": "没有自测报告可看。",
                "note": "", "per_run": per_run, "submissions": 0}

    blocking = list(getattr(report, "blocking", None) or ())
    first = blocking[0] if blocking else None
    if first is None:
        return {"run": None, "failed_step": None,
                "say": "自测没过，但报告里没有哪一遍说清是为什么（这份报告不完整）。",
                "note": "", "per_run": per_run, "submissions": submissions}

    name, label, note = getattr(first, "name", None), getattr(first, "label", "") or "", \
        getattr(first, "note", "") or ""
    if getattr(first, "status", None) == "skipped":
        # 「跳过」不许被读成「卡在哪一步」—— 这一遍压根没跑，没有步号可指（Task 6 的诚实条款）
        return {"run": name, "failed_step": None,
                "say": "%s 这一遍**没跑**（%s）—— 这一类失败这次没验到，不是「卡在哪一步」。"
                       % (label or name, note), "note": note, "per_run": per_run,
                "submissions": submissions}
    step = getattr(first, "failed_step", None)
    if step is None:
        say = "%s 这一遍挂了：%s" % (label or name, note)
    else:
        say = "%s 这一遍挂了：卡在第 %s 步 —— %s" % (label or name, step, note)
    return {"run": name, "failed_step": step, "say": say, "note": note, "per_run": per_run,
            "submissions": submissions}


def _delivery_path(state) -> pathlib.Path:
    """交付点：`<out_dir>/<site>.py`（生产就是 `forms/sites/<site>.py`）。"""
    return pathlib.Path(str(state.get("out_dir") or DEFAULT_OUT_DIR)) / ("%s.py" % state["site"])


def _stage_candidate(state) -> pathlib.Path:
    """把这一版落到 `<out_dir>/<site>.candidate.py` —— 自测跑的就是它。

    为什么是**这个位置**：产物开头那句路径算术
    （`sys.path.insert(0, dirname(dirname(abspath(__file__))))`）解析到的是 `out_dir`
    的上一层，`common.py` 得在那儿 —— 所以候选不能扔进 `runtime/` 之类的深目录，
    它必须与将来交付的那份**同层**。
    为什么不直接写交付路径：自测跑的是**没验过**的东西，别让它先出现在
    `forms/sites/<site>.py` 上（那条路径生产会按名字找；C15 的路由表还没条目是另一回事，
    但「没验过的东西不放在交付点上」这条不该靠别人的疏漏来兜）。
    """
    out_dir = pathlib.Path(str(state.get("out_dir") or DEFAULT_OUT_DIR))
    out_dir.mkdir(parents=True, exist_ok=True)
    py = out_dir / ("%s.candidate.py" % state["site"])
    py.write_text(state["src"], encoding="utf-8")
    return py


def _drop_candidate(state) -> None:
    """收摊：候选产物**任何结局都不留**。

    规则一句话：**交付目录里只该有交付物**。停了/挂了的那一版去哪看？源码在 checkpoint
    的 `src` 里，自测的逐遍 trace 在 `runtime/selftest/` 下 —— 而那一版 py 本身属于
    §6.4 说的「人不读代码」的那一类，留着只会在交付目录里多一份说不清来历的 py。
    """
    candidate = state.get("candidate_path")
    if not candidate:
        return
    try:
        pathlib.Path(candidate).unlink()
    except OSError:
        pass                                  # 已经不在了 / 删不掉：不值得让交付失败


def _provenance(state, deps: Deps, *, report) -> dict:
    """§5.3 的元数据块。**图只填它真知道的**，剩下的 `None`（不编内容）。"""
    now = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    return {
        "generated_at": now,
        "generator": GENERATOR,
        "env": state.get("env") or None,
        "platform": state.get("platform") or None,
        "selftest": _selftest_block(report, now),
        "source": {
            "kind": state.get("mode") or MODE_BUILD,
            "evidence": str(state.get("evidence") or state.get("goal") or ""),
            # R-15：这份 py 跑起来用的是**哪一份** `common.py`（出身 + md5 + 抄自哪个 commit）。
            # 环境漂了而没人知道，就是下一轮「昨天还好今天不行」的排查地狱（§5.3）。
            "runtime": dict(deps.provenance()),
        },
    }


def _selftest_block(report, now: Optional[str]) -> Optional[dict]:
    """`PROVENANCE["selftest"]`（§5.3 的形状：runs / passed / submissions / at）。

    `verdict` 是加出来的**判据**：`runs=5 passed=4` 读不出「那是允许跳过的第 5 遍」
    还是「挂了」，而图是**按 `report.passed` 走的** —— 它得跟着产物走，
    不然下一个人只看产物就以为「4/5 差不多过了」。跑过的才算 `runs`
    （`skipped`（没旋钮）与 `not_needed`（R-84：这一轮用不着跑）都不是「跑了」）。

    ⚠️ **`submissions` 是 R-84 那条裁定的可见性**：一次提交 = 产物把整个漏斗走一遍
    = 一次真实的 lead 提交。裁定之前是**固定 3 次/轮**，而流水线每循环又是一轮 ——
    「上百次提交」就是这么来的，且**当时没有人看得见这个数**（用户是亲手关掉 Bit 窗口才止住的）。
    这个键必须跟着产物走：**看不见的东西会再犯一次**。

    `runs` 与 `submissions` 几乎总是同一个数，**故意留两个**：
    `rerun` 那一步可能记挂但**没跑**（`cdp navi` 没成）—— 那一遍算 `runs` 不算 `submissions`
    （「这一遍有结论」与「往站方真提交了一次」是两件事）。
    """
    if report is None:
        return None
    runs = list(getattr(report, "runs", None) or ())
    ran = [r for r in runs if getattr(r, "status", None) in ("passed", "failed")]
    return {"runs": len(ran),
            "passed": len([r for r in ran if getattr(r, "ok", None) is True]),
            "submissions": int(getattr(report, "submissions", len(ran))),
            "at": now,
            "verdict": bool(getattr(report, "passed", False))}


def _deliver_say(state) -> str:
    """`deliver` 那道闸上问的话 —— **不吹**：自测过了不等于生产会过（§10）。"""
    out_dir = str(state.get("out_dir") or DEFAULT_OUT_DIR)
    return ("自测过了，接下来把它写进站点目录（%s）。"
            "写下去的是刚才自测那一版的**同一份**，只多一块 `PROVENANCE` 环境指纹"
            "（代理国家 / DPR / UA / 视口 / 平台 / 自测结果 / 出身）—— 同一个 URL 在不同"
            "代理国家是**不同的页面**，指纹得跟着产物走（§5.3）。\n"
            "提醒：**工具侧自测通过 ≠ 生产一定过**（规格 §10）；这份 py 也还**不能被生产"
            "调起来**，路由表条目归计划四（C15）。" % out_dir)


def _delivered_note(state, path: pathlib.Path) -> str:
    """交付成功之后那段**人话**（D16：给人看的一句话，不是错误码）。"""
    runs = (_selftest_block(state.get("report"), None) or {}).get("runs")
    return ("写好了：%s\n"
            "自测跑了 %s 遍，判据过了（自测结果也写进产物里了）。\n"
            "两件还没做的事：① 工具侧自测通过 ≠ 生产一定过 —— 自测的浏览器与生产 worker 的"
            "代理出口/指纹/时序不是一套（规格 §10）；② 这份 py 还不能被生产调起来，"
            "要在路由表里登记（C15，计划四）。" % (path, runs))
