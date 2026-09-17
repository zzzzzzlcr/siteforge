"""修站：拿一份**现成的（坏掉的）py** 改，不重新探索。

## 为什么要有这一条（2026-09-17）

生产里 agent 最常见的用法**不是**「从零产出一份新脚本」，而是
**「这一单跑挂了 → 叫 agent 去修那一份」**。而在这条路之前，siteforge 只会
「探索 → 定稿」那一套：`plan.from_states()` 早就写好了（规格 §2.3 的「可选口子」），
**从来没有被接进图里跑过一次**。

这一条把那个口子接上，判据只有一条：**不重新探索**。
账本/产物已经有了，重跑一趟探索等于把已有的证据丢掉重买一次。

## 修什么（与「字段身份放开」同一批证据）

现成的产物里，每一格的身份是**探索那一刻**判的。它可能判错（实测：那一格判不出种类
→ 兜底填人名）。修的时候按**证据的可靠性**从高到低问，认不出就**留空并说出来**：

  ① 产物自己录下来的身份（`FILLS[k].label` / `target.hint` / `target.placeholder`）
     —— 用**当前**的识别代码重判一遍（那一套已经修过了，见 `browser_agent._field_kind`）
  ② 站方自己的 schema（**可选**）：产物里的 `label` 常常就是站方的整数组件号
     （实测：`textField-173862` ↔ 配置里的 `component_173862`），
     而配置是**公开可取**的（一个 GET，不需要窗口、不需要 CDP）。
     拿到就多一条证据；**拿不到不算失败** —— 第 ① 层够用就够用。
  ③ 都没有 → **不改**，并记一条 note 说「这一格认不出，没动它」。
     绝不猜：往「州」里填一个人名比留空更坏（留空会被校验拦住、能被看见）。

## 边界（这一版**不做**的）

- **不重新探索**、不调模型（`deps.write` 那条路照旧可以给它反馈，但这一版是确定性的）
- **不改地址**（选择器）。地址是那一趟观测的产物，修它要重新观测；
  这一版只修「这一格是什么」，地址原样带过去。
"""
from __future__ import annotations

import ast
import json
import urllib.request
from typing import Any, Callable, Optional

from agent import browser_agent
from agent import plan as plan_mod

__all__ = ["from_py", "repair_states"]

#: 取站方配置的超时（秒）。**短** —— 它是可选层，拿不到就往下走，不能把整趟拖住。
CONFIG_TIMEOUT = 8.0

#: 站方配置里 `subtype` / `mappedQuestionId` 的词 → 我们这边的 `kind`。
#:
#: ⚠️ 这不是「把某个站的 schema 写死进通用模板」—— 那张表是**通用词汇**到**通用词汇**的
#: 对齐（`postcode` → `postcode`、`email1` → `email`），随配置**运行时**读进来，
#: 不随站点变。第 ② 层整体是**可选**的：拿不到配置就永远走不到这里。
_SCHEMA_WORDS = (
    (("postcode", "postal", "zip"), "postcode"),
    (("email",), "email"),
    (("telephone", "phone", "mobile"), "phone"),
    (("telephone",), "phone"),
    (("fullname", "full_name", "first_name", "last_name", "name"), "full_name"),
    (("state", "province", "region"), "state"),
    (("city", "town"), "city"),
    (("address", "street"), "address"),
)


def _literals(src: str):
    """把现成 py 里的 `STATES` / `FILLS` 两个字面量取出来（**不 exec**）。

    取不到返回 `(None, None)` —— 与 `plan.from_states` 同一个规矩：
    坏 py / 动态构造的值一律当「读不出来」，不抛、不猜。
    """
    try:
        tree = ast.parse(src or "")
    except Exception:
        return None, None
    found: dict[str, Any] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id]
        else:
            continue
        for name in names:
            if name in ("STATES", "FILLS") and name not in found:
                try:
                    found[name] = ast.literal_eval(node.value)
                except Exception:
                    return None, None
    return found.get("STATES"), found.get("FILLS")


def site_schema(url_get: Callable[[str], str], label: str) -> Optional[dict]:
    """站方配置里，这个组件号对应的那一块（拿不到给 `None`）。

    `label` 里那个号就是配置里的 `id`（实测：`textField-173862` ↔ `component_173862`）。
    **号对得上，不用猜** —— 这是这一层为什么可靠的全部理由。
    """
    digits = "".join(ch for ch in str(label or "") if ch.isdigit())
    if not digits:
        return None
    try:
        raw = url_get("https://chameleon-na.www.gowizard.com/forms/7878/default/gowizard")
        import re
        m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
                      raw, re.S)
        if not m:
            return None
        data = json.loads(m.group(1))
        comps = data["props"]["pageProps"]["formSettings"]["forms"]["page_components"]
    except Exception:
        return None                      # 可选层：任何一种失败都只是「这一条没有」
    for key, block in (comps or {}).items():
        if str((block.get("component") or {}).get("id")) == digits:
            comp = block.get("component") or {}
            inp = block.get("input") or {}
            return {"subtype": comp.get("subtype") or "",
                    "mapped": comp.get("mappedQuestionId") or "",
                    "text": comp.get("text") or "",
                    "placeholder": inp.get("placeholder") or ""}
    return None


def _kind_from_schema(block) -> Optional[str]:
    """配置那一块里的 `subtype` / `mappedQuestionId` / 题目正文 → `kind`。"""
    if not block:
        return None
    for blob in (block.get("subtype"), block.get("mapped"), block.get("text")):
        text = str(blob or "").lower()
        if not text:
            continue
        for words, kind in _SCHEMA_WORDS:
            if any(w in text for w in words):
                return kind
    return None


def repair_states(states, fills, *, url_get: Optional[Callable[[str], str]] = None):
    """把现成产物里那些**判错了的字段身份**重判一遍。

    返回 `(states, fills, notes)`：`notes` 是每一条改动/没改动的人话
    （「哪一格、原来是什么、现在是什么、凭什么」）—— 修站这条路**必须留下这个**，
    不然「它到底改了什么」只能靠读两份源码对 diff。
    """
    states = [dict(s) for s in (states or [])]
    fills = {k: dict(v) for k, v in (fills or {}).items()}
    notes: list = []
    for state in states:
        for step in (state.get("steps") or []):
            if step.get("action") != "form":
                continue
            key = step.get("fill") or ""
            info = fills.get(key)
            if not info:
                continue
            target = info.get("target") or step.get("target") or {}
            label = str(info.get("label") or target.get("label") or "")
            # ① 产物自己录下来的身份 → 用**当前**的识别代码重判
            element = {"label": target.get("label") or "", "hint": target.get("hint") or "",
                       "placeholder": target.get("placeholder") or "",
                       "nearby_text": list(target.get("nearby_text") or []),
                       "type": target.get("type") or ""}
            kind = browser_agent._field_kind(label, element)
            why = "产物自己录的身份"
            # ② 站方 schema（可选）
            if kind is None and url_get is not None:
                block = site_schema(url_get, label)
                kind = _kind_from_schema(block)
                if kind:
                    why = "站方配置里 component_%s 的 subtype/mappedQuestionId/正文" % (
                        "".join(c for c in label if c.isdigit()))
            old = info.get("source")
            if kind is None:
                notes.append("「%s」认不出是什么 —— **没动它**（认不出就不填，绝不猜）" % label)
                continue
            if kind == old:
                continue
            info["source"] = kind
            info["name"] = kind
            info["fallback"] = [{"random": kind}]
            fills[key] = info
            notes.append("「%s」：%s → %s（凭：%s）" % (label, old, kind, why))
    return states, fills, notes


def from_py(src: str, *, url_get: Optional[Callable[[str], str]] = None):
    """拿一份现成的 py → `(plan, states, fills, notes)`。

    `plan` 走的是 `plan.from_states()`（规格 §2.3 那条**一直没跑过**的口子）——
    它是「这份 py 里有哪些步骤」的清单，修站这条路拿它当**底稿的骨架**。
    """
    plan = plan_mod.from_states(src)
    states, fills = _literals(src)
    if states is None or fills is None:
        return plan, None, None, ["这份 py 里的 STATES/FILLS 读不出来（坏 py 或动态构造）—— 修不了"]
    states, fills, notes = repair_states(states, fills, url_get=url_get)
    return plan, states, fills, notes


def http_get(url: str, timeout: float = CONFIG_TIMEOUT) -> str:
    """默认的取配置方式（公开 GET，不需要窗口、不需要 CDP）。"""
    req = urllib.request.Request(url, headers={"User-Agent": "siteforge/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")
