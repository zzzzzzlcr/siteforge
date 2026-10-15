#!/usr/bin/env python3
"""修站那一刀的两条回归子线：跑一遍新判据，把「错误新增」与「历史证据缺失」分开。

## 为什么要有这个工具（spec §5）

盘上交付过的产物**本来就缺证据**（click/scroll 带锚 09-28～10-12 全 0），所以新判据
有理由在它们身上报 no-basis —— 那既不是回归，也不能靠放宽新规则去消掉。
分法就一句话：**看冻结那份里到底有没有那几样**。

| 子线 | 判据 | 处置 |
|---|---|---|
| **① 错误新增**（**硬线 = 0**） | 冻结那份里**有**锚，而判据仍报了 | 就是回归：改判据，不许改验收 |
| **② 历史证据缺失**（**单列**） | 冻结那份里**确实没有** ⇒ 报得对 | 不静默放行、也不放宽规则：逐条列出来交人裁 |

⚠️ **不许为了让子线 ② 变绿而放宽规则** —— 那是这条硬线的全部意义。

## 用法（**账本一律只读**打开，绝不写）

    python3 tools/addr_basis_report.py --ledger runtime/checkpoints.sqlite --limit 30

⚠️ **在宿主上跑**（账本在宿主：`runtime/` 是 8099 的、`runtime-222/` 是 8100 的）。
⚠️ **`msgpack` / `ormsgpack` 两个都试**：宿主上装的是 `msgpack`，容器（跑测试那一侧）里
   装的是 `ormsgpack`（`langgraph-checkpoint` 4.x 用的就是它）—— 写死一个 import 的话，
   另一半环境里这个模块**根本 import 不起来**（实测：容器里 `import msgpack` 是
   `ModuleNotFoundError`，而 `tools/` 底下这个文件在容器里是要被用例 import 的）。
"""
from __future__ import annotations

import argparse
import collections
import pathlib
import sqlite3
import sys

#: ⚠️ 少了这三行，brief 里那条命令（`python3 tools/addr_basis_report.py …`）**跑不起来**：
#: 直接跑脚本时 `sys.path[0]` 是 `tools/` 而不是仓库根，`from agent import …` 立刻
#: `ModuleNotFoundError: No module named 'agent'`（实测）。同款写法见 `tools/same-generation.py`。
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:                      #: 容器里（langgraph 那一套的依赖）
    import ormsgpack as _ormsgpack
except ImportError:       #: 宿主上是反过来：只有 `msgpack`
    _ormsgpack = None
try:
    import msgpack as _msgpack
except ImportError:
    _msgpack = None

from agent import fix, lint  # noqa: E402  （路径见上面那三行）

#: 「有锚」的判据 = 卡上那几样里**至少一样有值**（与 `lint._CARD_ANCHOR_KEYS` 同一组）
ANCHOR_KEYS = ("label", "aria_label", "hint", "placeholder", "nearby_text", "text")


def has_anchors(card) -> bool:
    return any((card or {}).get(k) not in (None, "", [], {}) for k in ANCHOR_KEYS)


def has_evidence(card) -> bool:
    """冻结那份里**有没有可依的** ⇒ 「判据本可以给出路，却报了」= ①。

    ⚠️ **两句**，与 `lint._card_has_nothing` 的**卡那一侧**逐句对齐（spec §5 的表也是两样：
    「有**那几样锚** / **有 Observed 候选**」）：
      ① 卡上有锚（`label`/`aria_label`/`hint`/`placeholder`/`nearby_text`/`text`）；
      ② 卡上有一根**验过唯一、又不在脆那一族**的候选（`tools/cdp` 的 `address()` 验过才记下来的）。
    ★ 2026-10-15 评审（Important）：原来只写了第 ① 句 ⇒ `{"role": "link",
      "selectors": ["a[href='/comp']"]}` 这种卡（**没锚、但有一根好候选**）上的真回归
      会被记进「②历史证据缺失 = 交人裁」那一节 —— 而那正是这条硬线唯一要抓的东西。
    ⚠️ **不**把 `_card_has_nothing` 的第三句（产物自己那几根 `kept`）算进来：那是**被审的那份
      产物**的地址，用它当判据就成了自己证自己（产物那根若真站得住，判据本来就**不会报**）。
    """
    if has_anchors(card):
        return True
    return any(not lint._fragile(s) for s in ((card or {}).get("selectors") or []))


def classify(src: str, observed) -> str:
    """这一份产物报出来的 no-basis，算**回归**还是**历史欠账**。

    ⚠️ 这是那条**硬线**的守门人：① 必须是 0；② 再长也不许把规则放宽（spec §5）。
    ⚠️ **2026-10-15 更正**：这里不再「按 `snippet` 反查是哪一步」（那个写得糙），改用**真生产者**
    `lint.no_basis_slots` —— 它产的就是判据自己认的那几处（带状态名、步序、那根地址、卡指纹），
    与 `_no_basis_say` 教人说的那句「接受地址 组名#步序」用的是**同一个把手**。
    ⚠️ 没有冻结那份（`observed=None`）⇒ `no_basis_slots` 给空表 ⇒ 这里给空串
    （**一个 ① 都不报**：连证据都没有的时候，唯一诚实的输出是「什么都没说」；
    否则这条硬线就成了恒真式，量了等于没量）。
    """
    slots = lint.no_basis_slots(src, observed)
    if not slots:
        return ""
    for one in slots:
        # 报的那一步，在冻结那份里**有可依的** ⇒ 判据本可以给出路（却报了）= 回归
        if has_evidence(_card_at(observed, one.get("state"), int(one.get("index") or 0))):
            return "①错误新增"
    return "②历史证据缺失"


def _card_at(observed, state_name, index):
    """`(状态名, 组内步序)` → **原件那张卡**（认不出给 `{}` = 当作没证据）。

    ⚠️ 与判据那边**同一个把手**：`(状态名, 步序)` 是全仓既有的「这一步是谁」
    （`_keep_repair_local` 按它钉、`_no_basis_say` 按它报、`_accept_from_note` 按它认）。
    """
    for group in ((observed or {}).get("states") or []):
        if not isinstance(group, dict) or str(group.get("name") or "") != str(state_name or ""):
            continue
        steps = [s for s in (group.get("steps") or []) if isinstance(s, dict)]
        if 0 <= int(index) < len(steps):
            return fix.card_of(steps[int(index)].get("target"))
        return {}
    return {}


def _unpack(blob):
    """账本里那一格 → Python 值（**两种 packer 都认**，理由见模块 docstring）。

    ⚠️ 两个都试的顺序**不要紧**（两边解的是同一种字节），要紧的是**不能写死一个**。
    """
    if _ormsgpack is not None:
        return _ormsgpack.unpackb(blob)
    return _msgpack.unpackb(blob, raw=False, strict_map_key=False)


def _rows(ledger: str):
    """账本里每一趟的 `src` 与（有就给的）`observed`。**只读**。"""
    con = sqlite3.connect("file:%s?mode=ro" % ledger, uri=True)
    per = collections.defaultdict(dict)
    #: ⚠️ **`order by rowid` 是承重的**，不是装饰：下面「只看最后一版 `src`」与
    #: 「最早的 `states`/`fills`」都按**追加顺序**读 —— 不写死这一句，那两条就依赖
    #: SQLite 恰好的扫描顺序（今天对得上，是运气，不是保证）。
    for tid, ch, ty, val in con.execute(
            "select thread_id, channel, type, value from writes order by rowid"):
        if ch not in ("src", "observed", "states", "fills", "site", "end_reason"):
            continue
        data = _unpack(val) if ty == "msgpack" else val
        per[tid].setdefault(ch, []).append(data)
    con.close()
    return per


def _site_of(ch) -> str:
    """那一趟的站名 —— **最后一条非空的**。

    ⚠️ 不能照 `[0]` 取：账本里 `site` 那一格跟 `end_reason` 一样是**先写一条空的、再写真名**
    （实测 `['', 'ic']` / `['', 'delivered']`）⇒ 取第一条的话，下面「逐条交人裁」那一节
    在真语料上**大半是空白**，而人裁要看的第一件事就是「哪一趟、哪个站」。
    """
    return next((str(v) for v in reversed(ch.get("site") or []) if str(v or "").strip()), "")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--limit", type=int, default=30)
    args = ap.parse_args(argv)
    buckets = collections.Counter()
    lines = []
    for tid, ch in sorted(_rows(args.ledger).items()):
        srcs = [s for s in (ch.get("src") or []) if isinstance(s, str) and s.strip()]
        if not srcs:
            continue
        # 老 job 没冻过 `observed` ⇒ 拿它**最早那一条** states/fills 重建（重建不出就如实记）
        #: ⚠️ `observed` 取**最后一条不是 `None`** 的（★ 评审 ④）：这一格与 `site`/`end_reason`
        #: **同形状**（先写一条空的、再写真那份）。照 `[0]` 取的话，第一个值是 `None` 时冻结那份
        #: 会被**静默跳过**、退回重建那条路 —— 两条路给出的卡不一定一样，而屏幕上什么都看不出来。
        #: ⚠️ 只跳 `None`、**不跳空 `{}`**：空的那份也是「真冻过的一份」（判据按「没量到」处理 ⇒ ②）；
        #: 把它跳过去退回重建，反而可能**凭空造出一条 ①**（这条硬线最怕的方向）。
        observed = next((x for x in reversed(ch.get("observed") or []) if x is not None), None)
        if observed is None:
            states = (ch.get("states") or [None])[0]
            fills = (ch.get("fills") or [None])[0]
            if states is None and fills is None:
                buckets["③没冻过也没账本"] += 1
                continue
            observed = fix.observed_of(states, fills)
        for src in srcs[-1:]:                       # 只看最后一版（交付的那一版）
            kind = classify(src, observed)
            if not kind:
                continue
            buckets[kind] += 1
            lines.append("%-22s %s  %s" % (tid, _site_of(ch), kind))
    print("分账：%s" % dict(buckets))
    print("①错误新增**必须是 0**；②逐条列出来交人裁（不许为了让它们变绿而放宽规则）")
    for one in lines[:args.limit]:
        print("  " + one)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
