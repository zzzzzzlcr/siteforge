"""Task 7：这张图的**形状** —— 状态里有什么、上限是多少、人说的话怎么读。

## 为什么状态和逻辑分两个文件

`agent/graph.py` 是行为（探路 / 写 / 打回 / 交付），这里是**接口**：图往里塞什么、
往外拿什么，以及 Task 8 的服务（`POST /run` → `GET /job/{id}`）要照着哪个形状读。
分开放的理由与 `two-json-executors` 那条教训同源：**形状写在一处**，两边才不会各写一套。

## 状态的两条约定

1. **缺的键一律 `None`，不编内容**（§5.3 的同一条原则）。`env`（代理国家/DPR/UA/视口）
   来自 §4.6 的前提层，`platform` 来自平台分类 —— 这两样**都不在这张图里**，
   图只做搬运：有人告诉它就带上，没有就是 `None`。
2. **图往里放的东西必须能被 checkpointer 序列化**。所以：可以放 dataclass
   （`Journey` / `Report` —— 见 `graph.MSGPACK_ALLOWLIST`），可以放 dict / 字符串；
   **不许放可调用的东西**（人的暂停信号因此挂在 `graph.Deps` 上，不在状态里）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, TypedDict

from agent.browser_agent import DEFAULT_MAX_ROUNDS, DEFAULT_MAX_STEPS, Journey
from agent.selftest import Report

__all__ = ["SiteState", "Caps", "GENERATOR", "MODE_BUILD", "MODE_FIX",
           "CONTINUE", "STOP", "REVISE", "human_reply",
           "END_DELIVERED", "END_HUMAN_STOP", "END_NO_BRIEF", "END_NO_SUCCESS_TEXT",
           "END_MISSING_KNOB", "END_REVISION_CAP", "END_EXPLORE_UNFINISHED",
           "END_PAUSED", "END_DRAFT_FAILED", "END_LINT_CAP", "END_SELFTEST_CAP",
           "END_NO_WINDOW", "END_DELIVER_LINT", "FINISHED_EXPLORATION"]

#: §5.3 的 `generator`。与 `fixtures/reference_site.py` 里那份参考产物同一个值。
GENERATOR = "siteforge/v0.1"

MODE_BUILD = "build"     # 没跑过的站：意图只有人知道（§6.1）
MODE_FIX = "fix"         # 挂着的老站：失败证据写着卡在哪（§6.1）

# ── 人在闸口说的话（§6.2）────────────────────────────────────────────
#: 继续（`None` / `"continue"` / 空话一律算这个）
CONTINUE = "continue"
#: 喊停：**这一步没做**，图就停在那儿
STOP = "stop"
#: 纠正：接着走，但把这句话记下来带进 draft（「直接说该点哪」）
REVISE = "revise"

#: 人喊停认的几个说法（其余非空的话一律当纠正 —— 从不许**静默丢掉**人说的话）
_STOP_WORDS = ("stop", "halt", "abort", "停", "停下", "喊停", "算了")
_CONTINUE_WORDS = ("", "continue", "ok", "go", "继续", "过", "好的")


def human_reply(reply) -> tuple[str, str]:
    """把人的回话读成 `(动作, 那句话)`。

    收三种写法（其余当「继续，但这句话我记下了」，**不丢**）：
        `None` / `"continue"`                  → 继续
        `"stop"` / `{"action": "stop"}`        → 喊停
        `"登录弹窗要先点掉"`（一句话）          → 纠正，那句话带进 draft
        `{"action": "revise", "note": "…"}`    → 同上
    """
    if reply is None:
        return CONTINUE, ""
    if isinstance(reply, dict):
        action = str(reply.get("action") or reply.get("do") or "").strip().lower()
        note = str(reply.get("note") or reply.get("say") or "").strip()
        if action in _STOP_WORDS:
            return STOP, note
        if action in ("", CONTINUE):
            return (REVISE if note else CONTINUE), note
        if action == REVISE:
            return REVISE, note
        # 不认识的动作：**当纠正处理**（带着那句话继续），总比把人的话扔掉强
        return REVISE, note or action
    text = str(reply).strip()
    low = text.lower()
    if low in _STOP_WORDS:
        return STOP, ""
    if low in _CONTINUE_WORDS:
        return CONTINUE, ""
    return REVISE, text


# ── 图是怎么结束的（每个都配一句人话，见 graph.py 的 `end_note`）──────
#: 交付成功（**唯一**一个「东西真的落盘了」的结局）
END_DELIVERED = "delivered"
#: 人在某一道闸上喊停
END_HUMAN_STOP = "human_stop"
#: 开场白里连站点/目标都没有 —— 不开浏览器
END_NO_BRIEF = "no_brief"
#: 没人说「什么算成功」（`success_text`）—— 在 intake 就停，**不许猜**（§6.1）
END_NO_SUCCESS_TEXT = "no_success_text"
#: 窗口层缺一根线（`set_viewport` 之类）—— 那几遍扰动跑不了，而没人允许跳过它（R-31）
END_MISSING_KNOB = "missing_knob"
#: 人反复打回同一版到达上限（§6.3：反复打回同一处本身就是信号）
END_REVISION_CAP = "revision_cap"
#: 探路没走完（预算到顶 / 模型没给出结论）—— 不许拿半份账本去写 py（R0）
END_EXPLORE_UNFINISHED = "explore_unfinished"
#: 人在浏览器里喊的停（§6.2 的 `should_pause`）—— **不是失败**
END_PAUSED = "paused"
#: 写不出 py（最常见的一种：没人说「什么算成功」—— 成功判据只有人知道，§6.1）
END_DRAFT_FAILED = "draft_failed"
#: lint 打回次数到顶（§6.5：上限的目的是「别写出跑不完也不停的图」）
END_LINT_CAP = "lint_cap"
#: 自测挂了、修的次数到顶
END_SELFTEST_CAP = "selftest_cap"
#: 没有可用的浏览器窗口（§4.6 前提层 / P6 窗口只有几分钟）—— 不许跳过自测当通过
END_NO_WINDOW = "no_window"
#: 交付前最后一道自检没过（要落盘的那串字节自己脏了）—— 一个字节都不落
END_DELIVER_LINT = "deliver_lint"

#: 探路的账本**算走完了**的标记。只有这一种：模型自己说「讲完了」。
#: 其余（`budget_*` / `no_rounds` / `ended`）都是「没走完」，一律停下交给人（R0）。
FINISHED_EXPLORATION = ("model_done",)


@dataclass(frozen=True)
class Caps:
    """这张图所有的**硬上限**（§6.5）。

    为什么要有：不是为了省钱，是为了**别写出「跑不完也不会停」的图**。
    为什么是这几个数：`lint` 打回两次还过不去、或者自测挂了两轮还修不好，
    说明问题不在「再试一次」上 —— 该人看了（§6.3：lint 反复打回同一处本身就是
    「自信地错」的信号）。真到那一步，图停，把人和证据留在原地。

    `explore_steps` / `explore_rounds` 是 Task 5 那两道上限，图**照传**（不砍能力，P5）。
    """

    #: lint 打回最多几次（首版不算）。第 N+1 次不过就停。
    max_lint_bounces: int = 2
    #: `selftest` 挂了之后「诊断 + 重写」最多几轮。
    max_diagnoses: int = 2
    #: 人在门口打回最多几次（§6.2：人否 → 回 draft 带纠正）。
    #: 人驱动的循环**跑不飞**（每转一圈都得有人回话），所以这个上限不是防跑飞 ——
    #: 它防的是**自动化调用方**（Console / 脚本）一直回「重来」把真浏览器拖进无尽的
    #: 自测里；同时它把 §6.3 的信号摆到明面上（同一处反复打回 = 问题不在这一版稿上）。
    max_revisions: int = 5
    explore_steps: int = DEFAULT_MAX_STEPS
    explore_rounds: int = DEFAULT_MAX_ROUNDS


class SiteState(TypedDict, total=False):
    """一次生成任务的全部状态。

    `total=False` 是**故意**的：状态是一路长出来的（intake 之前只有开场白），
    节点只写自己那几项，langgraph 负责合并。
    """

    # ── 开场白（CLI / `POST /run` 收上来的）──────────────────────
    url: str                      # 站点 URL
    goal: str                     # 人给的意图：要摸清什么 / 什么算完成
    mode: str                     # MODE_BUILD / MODE_FIX
    site: str                     # 站点短名（不给就从 URL 推）
    success_text: Any             # **成功判据**（页面上出现哪段文字）—— 只有人知道（§6.1）
    evidence: str                 # fix 模式：失败证据的引用（FMR formLog / formStep）
    #: §4.6 前提层的产物：窗口、代理指纹、表单数据。都是**外面**给的，图不自己弄
    ws_url: Optional[str]
    form_file: Optional[str]
    env: Optional[dict]           # {"proxy_country","dpr","ua","viewport"}；没人给就 None
    platform: Optional[dict]      # {"guess","confidence"}；平台分类不在这张图里
    out_dir: Optional[str]        # 产物落在哪个目录（默认 `forms/sites/`）
    #: 扰动自测的两件**数据**旋钮（窗口层那根**回调**在 `graph.Deps.set_viewport` 上，
    #: 因为可调用的东西进不了 checkpoint）：点名允许跳过哪几遍、以及第 2 遍刷新回哪个 URL。
    #: 没给 = 用 Task 6 的默认（`DEFAULT_ALLOWED_SKIPS` = 只允许跳 country）。
    allow_skips: Optional[list]
    entry_url: Optional[str]

    # ── explore ───────────────────────────────────────────────
    journey: Optional[Journey]
    explore_say: str              # 人话：探路是怎么结束的

    # ── draft ─────────────────────────────────────────────────
    states: list                  # 「怎么走」（`journey.states()` 的产物）
    fills: dict                   # 字段值从哪来（`journey.fills()` 的产物）
    src: str                      # 这一版 py 的源码（lint 看的就是它）

    # ── lint / selftest ───────────────────────────────────────
    violations: list              # 打回的行（`{line, code, message, snippet}`）
    report: Optional[Report]
    candidate_path: Optional[str]  # 自测跑的那份候选产物（交付成功后会被清掉）

    # ── 人的话与两次回灌的计数 ───────────────────────────────────
    hints: list                   # 人在闸口说过的话（§6.2「直接说该点哪」）
    #: 人**打回**过几次、在哪道闸、说了什么 —— 一路留着（区别于 `end_reason ==
    #: "human_stop"` 那种「人把这次运行杀了」）。`revised_at` 是它在路上的临时形态：
    #: 路由靠它回 draft，`draft` 收下之后清掉（`revisions` 是那份记录的正身）。
    revisions: list
    revised_at: str
    lint_bounces: int
    diagnoses: int
    diagnosis: Optional[dict]     # 回灌给 draft 的证据：{run, failed_step, say, note}

    # ── 收尾 ──────────────────────────────────────────────────
    visits: list                  # 走过的节点（人看路线用，§6.4）
    py_path: Optional[str]
    provenance: Optional[dict]
    end_reason: str               # 上面的 END_* 之一；空 = 还没结束
    end_note: str                 # 为什么结束 —— **人话**（D16）
