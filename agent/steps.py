"""把产物 trace 的一行，翻译成面板上要的**三层 + 诊断**。

为什么单独一个模块：**判据只有一处**（本仓反复踩的坑是「同一件事两套判据，
其中一套哪天静默失效」）。面板只搬字，不许自己推「这算失败」。
三层口径必须与产物**自己的**判断一致 —— 下面是逐字对齐的：

  · 动作层 ← `ok`（`ok is None` + `skipped` = 被 `when` 判据跳过，那是**第三种**状态，
    不是失败：它说「这一步没轮到」，不是「做了没成」）
  · 元素层 ← `fallback_level` / `selector_used` / `recovery`（★ 2026-09-23 的恢复记录）
  · 业务层 ← `progress` / `progress_why`（`None` 且说了原因 = 「没算出」，不是「没推进」）

⚠️ **不产出任何百分比**：本项目没有概率模型，编出来的数字是**假证据**（附件里那张
「65% / 25% / 10%」是故意不做的）。这里每一条原因都必须**指着具体字段**。
"""
import json
from pathlib import Path

#: 产物 trace 里「第几个找法」的字段名（与 `agent/template.py` 的 `_perform` 同源）。
FIELD_LEVEL = "fallback_level"


def read_trace(path):
    """读出 trace 的所有行，返回 `(行列表, 坏行数)`。坏行**数出来**，不静默吞。"""
    rows, broken = [], 0
    for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            got = json.loads(line)
        except ValueError:
            broken += 1
            continue
        if isinstance(got, dict):
            rows.append(got)
        else:
            broken += 1
    return rows, broken


def _act(row):
    if row.get("skipped"):
        return "跳过"
    if row.get("ok") is True:
        return "成了"
    if row.get("ok") is False:
        return "没做成"
    return "没记下成不成"


def _elem(row):
    rec = row.get("recovery") or None
    if rec:
        return ("靠**恢复**找到的（账本第一条「%s」失效；命中 %s；把握 %.2f）"
                % (str(rec.get("primary_selector") or "（没记）")[:60],
                   rec.get("resolved_by") or "（没记）", float(rec.get("confidence") or 0.0)))
    level = row.get(FIELD_LEVEL)
    if level is None:
        return "没记下用哪条地址"
    if int(level) == 0:
        return "账本第一条地址"
    return "换到第 %d 个找法" % (int(level) + 1)


def _biz(row):
    if row.get("skipped"):
        return "这一步没轮到（判据说这一页不是那个状态）"
    if row.get("ok") is False:
        return "动作没做成 ⇒ 没轮到看页面变化"
    if row.get("progress") is True:
        return "页面推进了"
    if row.get("progress") is False:
        return "页面没有变化"
    why = str(row.get("progress_why") or "").strip()
    return ("没算出页面有没有变化（%s）" % why) if why else "没算出页面有没有变化（它没说为什么）"


def layer_of(row):
    """一行 → 三层。面板直接搬这三个词。"""
    return {"act": _act(row), "elem": _elem(row), "biz": _biz(row)}


def diagnose(row):
    """**有出处**的可能原因：每条都指着具体字段，不编概率、不猜动机。

    读的人要能顺着这句话回到 trace 里那格去核对 —— 这是「不是猜的」与「猜的」的分界。
    """
    out = []
    if row.get("skipped"):
        out.append("被 `when` 判据跳过：%s" % str(row.get("note") or "")[:200])
    if row.get("ok") is False:
        out.append("动作没做成：%s" % str(row.get("note") or "")[:200])
        out.append("账本里的地址都试过了（含重新 observe 重找）；`selector_used` 是最后一条")
    # ★ 2026-09-23（用户点的那件事：截图不好分析 ⇒ 运营懵、AI 也懵）：**现场**那一段
    # 是产物在没做成时录下来的（`agent/template.py` 的 `_SCENE_JS`），这里只搬字。
    # 这几句的价值在于「不需要看截图」（视觉模型今天没配）—— 页面当时什么样，写成字。
    sc = row.get("scene") or {}
    if sc.get("at_point"):
        out.append("**点到的其实是 `%s`**（不是账本里那个元素）—— 多半有东西压在它上面"
                   % str(sc["at_point"])[:80])
    if sc.get("overlay"):
        out.append("页面上还有**同意类容器**：`%s`" % str(sc["overlay"])[:80])
    if sc.get("ready") and str(sc["ready"]) != "complete":
        out.append("这一页**还没加载完**（readyState=%s）" % sc["ready"])
    if sc.get("texts"):
        out.append("当时视口里最显眼的三段字：%s"
                   % " ｜ ".join(str(t)[:30] for t in list(sc["texts"])[:3]))
    rec = row.get("recovery") or {}
    if rec:
        out.append("**原本那条地址失效了**：「%s」⇒ 靠 %s 救回来（把握 %.2f）"
                   % (str(rec.get("primary_selector") or "")[:60],
                      rec.get("resolved_by") or "（没记）", float(rec.get("confidence") or 0.0)))
    if row.get("ok") is True and row.get("progress") is False:
        out.append("动作报成功但**页面没有变化** —— 常见于：点到被盖住的元素、"
                   "页面还没加载完、或那一下本来就不推进（同一步重试过的话看 note 前缀）")
    if row.get("progress") is None and str(row.get("progress_why") or "").strip():
        out.append("推进判不了：%s" % str(row.get("progress_why"))[:200])
    if row.get("error"):
        out.append("产物自己在这一步出错：%s" % str(row.get("error"))[:200])
    return out


def step_view(row):
    """面板要的整格。`raw` 原样带上 —— 「面板没画到的字段」在页面上仍旧看得见。"""
    return {
        "n": row.get("step"),
        "action": row.get("action") or "",
        "target": row.get("target") or "",
        "note": row.get("note") or "",
        "layers": layer_of(row),
        "why": diagnose(row),
        "selector_used": row.get("selector_used") or "",
        "frame_id": row.get("frame_id") or "",
        "recovery": row.get("recovery") or None,
        "shots": {"before": row.get("shot_before"), "after": row.get("shot_after")},
        "raw": row,
    }
