"""自动那条路（检测到就自己开修）—— 与「人按着走」分开的那一半。

**为什么单独一个文件**（用户 2026-09-24 点名：「别把代码写个好几千行可读性很差」）：
`service.py` 已经 6500+ 行，这套东西（到闸替人按 / 两遍为限 / 刹车）再堆进去没人读得动。
所以：**话与判据在这儿**（纯函数、可单测、一眼看完），`service.py` 只留两个碰 I/O 的口
（`_auto_reply`：读快照 + 交 resume；`_auto_give_up`：记进 job + narrate）和一行调用。

这一版（Task 1/2）只有「按几下、什么时候停」；值班脚本那半（看榜 / 对账 / 记账 / 心跳）
在 Task 5 加进这个文件。
"""
from __future__ import annotations

# ── 到闸替人按（Task 1）──────────────────────────────────────────────
#: `auto` 那一趟到闸时，服务替人按「继续」留下的那句。
#: ⚠️ 它**不是**「人说的」—— 时间线上两张嘴不许混成一张（所以单有一个 kind）。
AUTO_REPLIED_SAY = "自动模式：这一步自己走了（没人按）。"

#: 一次最多替人按几下（**防死循环**的兜底，不是「修几遍」那个计数）。
AUTO_MAX_PRESSES = 30

#: 刹车踩下去时那句人话（`%s` = 哪一道闸，`%d` = 已经按过几下）。
AUTO_STUCK_SAY = ("自动模式：**停在这儿了** —— 第 %s 这道闸按过之后没往前走"
                  "（已经替人按过 %d 下）。再按下去就是在原地空转（真站上=一直烧钱），"
                  "所以停住等人。")

# ── 两遍为限（Task 2，spec §5）───────────────────────────────────────
#: 最多修两遍 —— 一遍 = 出一版稿 + 自测一次。两遍都不过 ⇒ 停、报人。
AUTO_MAX_ATTEMPTS = 2

#: 两遍都没过那句人话（`%s` = 为什么需要人）。
AUTO_GIVE_UP_SAY = ("自动模式：**两遍都没过**（一遍 = 出一版稿 + 自测一次），再试下去只是在猜 ⇒ "
                    "停下、**需要人接手**。为什么：%s")

#: 「**做不成**」的停因：这些**不等两遍**、立刻报人（再试也不会变）。
#: ⚠️ 名字来自 `state.END_*`（那几个常量没全在服务里 import，所以按它们的**字面值**列；
#: `tests/test_service_auto.py` 钉着这张表与 `state` 里那几个名字逐字对得上）。
AUTO_CANNOT_DO = ("no_window", "missing_knob", "no_success_text", "window_gone")

#: 做不成那句人话的头（后面接那一趟自己留下的 `end_note`）。
AUTO_CANNOT_DO_SAY = "自动模式：这一趟**做不成**（不是没修好）—— 不等第二遍，直接报人。为什么：%s"


def attempts(values: dict) -> int:
    """这一趟**已经到第几遍** —— 只认账上有的东西：`visits` 里 `draft` / `selftest` 的次数。

    ⚠️ 为什么数 `visits`：图的每一步都会往 `visits` 里落一个节点名（`graph._visited`），
    所以「写过几版稿」这件事**账上本来就有** —— 不另开一格状态（那会是第二个真相源）。
    """
    visits = [str(v) for v in (values or {}).get("visits") or []]
    return max(visits.count("draft"), visits.count("selftest"))


def give_up_reason(values: dict) -> str:
    """该**不再替人按**了吗 ⇒ 为什么（空串 = 继续按）。两道判都在这一处（一处口径）。

    ① `AUTO_CANNOT_DO`（窗口没了 / 缺旋钮 / 底稿读不到 / 判据搬不出来）—— **不等第二遍**；
    ② 已经到 `AUTO_MAX_ATTEMPTS` 遍 —— 这就是领导拍的那个停点（「修两遍不通才抛给人」）。
    """
    values = values or {}
    reason = str(values.get("end_reason") or "")
    if reason in AUTO_CANNOT_DO:
        return AUTO_CANNOT_DO_SAY % (str(values.get("end_note") or reason)[:200])
    reached = attempts(values)
    if reached >= AUTO_MAX_ATTEMPTS:
        return "已经到第 %d 遍（上限 %d）都没过" % (reached, AUTO_MAX_ATTEMPTS)
    return ""
