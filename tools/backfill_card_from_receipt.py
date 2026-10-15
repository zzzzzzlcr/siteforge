#!/usr/bin/env python3
"""把**已经丢掉的**身份卡从账本里捡回产物 py（一次性工具，★ 2026-10-15）。

## 为什么要有它

探路那一趟，工具**当场读到了元素的名字**（回执那一行：`<li> "Every now and then"
aria-label="Every now and then" [61,252,240,45]`），而账本里留下的那张卡是空的
（`text`/`role`/`near` 三个 `null`）—— 根因是摘卡那一跳在上一次观测的模型里没找到这个元素
（见 `agent/browser_agent.py` 的 `_card_from_receipt_into`）。① 那一半治的是**今后**不再丢；
这个工具治的是**已经丢掉的那些**：拿同一把解析器（`card_from_receipt`）把名字从
**每一步自己那条回执**里捡回来，写进产物 py 的 `STATES` / `FILLS` 字面量。

真事（`job-3e99eb67519e` · wildnutrition，`runtime/explore/job-3e99eb67519e/attempt-1.jsonl`）：
第 30/32/37 步那三张空卡 ⇒ 下游判据说这一步「量不到更稳的地址」
（`lint._has_text_anchor` 认的 `label` / `text` 一个都没有）。

## 用法

    python3 tools/backfill_card_from_receipt.py \\
        --trace runtime/explore/job-3e99eb67519e/attempt-1.jsonl \\
        --py /tmp/sf_mix/forms/sites/wildnutrition.py            # 先干跑，看它要改哪几处

    ... --write                                                  # 真写（先备份 `<py>.bak-<日期>`）

⚠️ **干跑是默认**：不写盘、不建备份，只把每一处要补的东西列出来。

## 纪律（与 ① 那半边**同一条**，一处也不松）

- **同一把解析器**：`agent.browser_agent.card_from_receipt`（认不出就 `None`，一个字不猜）。
- **只在卡是空的那一刻补**：`target` 上那几样（`text`/`label`/`hint`/`placeholder`/
  `nearby_text`）**全都没有值**才是「上一次观测里没找到」那一张。已经带着证据的卡
  **一个字节都不动** —— 那些步里回执说的是「cdp 解析选择器之后点到的那一个」，
  与观测里的那一张**可能不是同一个元素**（实测：`job-3e99eb67519e` 第 20 步，
  观测说 `Hair, skin + nails`，回执说 `Bones, joints + muscles`）。
- **只补空着的那几样、不加别的键**：`label`（来自 `aria-label`）与 `text`
  —— 这两个键名在 `target` 这份契约里本来就有。
- **改字面量、不重渲**：产物是**那一刻**的模板渲出来的，今天再渲一遍会换掉**整份**骨架
  （实测：当前 `template.render` 重渲这份 py 有 587 处 hunk）—— 所以走**机械改字面量**
  那条路，并且：
    · 用的是**同一把**字面量渲染器（`template._lit` + `_stable_first`）；
    · 动手之前先拿它把**没改过**的那份重渲一遍、逐字节比对（对不上就**拒绝写**：那说明
      这一份字面量与渲染器不是同一版，再动手就是整段重排）；
    · 改完再核一遍 diff：**每一处增删都必须落在 `'label'` / `'text'` 这两个键的行上**
      （见 `_only_card_keys_changed`），别处一个字节都不许动。
"""
from __future__ import annotations

import argparse
import ast
import datetime
import difflib
import json
import pathlib
import sys

#: ⚠️ 少了这三行，直接跑脚本时 `sys.path[0]` 是 `tools/`，`from agent import …` 立刻
#: `ModuleNotFoundError`（同款写法见 `tools/addr_basis_report.py` / `tools/same-generation.py`）。
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import browser_agent, template  # noqa: E402  （路径见上面那三行）

#: 「卡上什么都没有」看的那几样（与 `lint._CARD_ANCHOR_KEYS` / `_card_has_nothing` 同一组）。
CARD_KEYS = ("text", "label", "hint", "placeholder", "nearby_text")
#: 这个工具**只补**这两样：`aria-label` 落 `label`（判据认的键是它），文本落 `text`。
FILL_KEYS = ("label", "text")


def _span(src: str, node) -> tuple:
    """`ast` 节点 → 它在源码里的 `(起点, 终点)` 偏移（按字符）。"""
    lines = src.splitlines(keepends=True)

    def at(lineno, col):
        return sum(len(x) for x in lines[:lineno - 1]) + col

    return at(node.lineno, node.col_offset), at(node.end_lineno, node.end_col_offset)


def _module_literal(src: str, name: str):
    """模块级 `NAME = <字面量>` → `(节点, 起点, 终点)`；没有这一条就是 `None`。"""
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id == name:
            start, end = _span(src, node.value)
            return node.value, start, end
    return None


def _card_is_empty(target) -> bool:
    """这一张卡**什么都没有**吗（= 上一次观测里没找到那个元素的那一张）。"""
    if not isinstance(target, dict):
        return False
    return not any(str(target.get(k) or "").strip() for k in CARD_KEYS) \
        and not (target.get("nearby_text") or [])


def _fill(target: dict, card: dict) -> list:
    """把卡里那几样**补进** `target`（只补空着的、一个已有值都不换）。返回补上的键。

    `label` 的落位照 `_target_of` 今天那一支的顺序（click：`text` → `role` → `near` →
    **`label`**）—— 插在 `near` 后面（没有 `near` 就插在 `text` 后面）：位置不影响语义，
    但这正是渲染器自己会把它放在的地方。
    """
    filled = []
    for key in FILL_KEYS:
        if str(target.get(key) or "").strip():
            continue                       # 已有值 ⇒ 一个字节都不动
        if card.get(key):
            target[key] = card[key]
            filled.append(key)
    if "label" in filled:
        label = target.pop("label")
        out = {}
        for k, v in target.items():
            out[k] = v
            if k == "near" or (k == "text" and "near" not in target):
                out["label"] = label
        if "label" not in out:             # 没有 `near` / `text` 可依 ⇒ 放最前面
            out = {"label": label, **out}
        target.clear()
        target.update(out)
    return filled


def _selector_key(target) -> tuple:
    sel = (target or {}).get("selectors")
    return tuple(str(s) for s in sel) if isinstance(sel, (list, tuple)) else ()


def _match_rows(rows: list, state_name: str, steps: list) -> dict:
    """py 里那一步 → 账本里**它自己那一行**（按 `(状态, 动作, 选择器)` 顺序对齐）。

    为什么按这个对齐：产物那一组步骤就是从**这一组账本行**里摘出来的（同一趟、同一个状态名），
    而 `(action, selectors)` 就是这一步的身份（`_target_key` 用的也是它）。对齐是**保序**的：
    账本里被丢掉的行（observe / eval / screenshot）直接跳过。

    配不上就整组**不认**（返回空表）—— 认错一行 = 拿别的元素的回执往这张卡上写，而屏幕上
    什么都看不出来。配上的还要过最后一道：**重放那一步与 py 里那一步逐字段相同**
    （`_replay_step(row)['target']` vs py 的 `target`）—— 那是「这一行真是它的」的凭据
    （这一趟 15 步 15/15 都过得了；过不了就是这两份记录对不上，别猜）。
    """
    out = {}
    mine = [r for r in rows if str(r.get("state") or "") == str(state_name)]
    at = 0
    for index, step in enumerate(steps):
        want = (str(step.get("action") or ""), _selector_key(step.get("target")))
        while at < len(mine) and (str(mine[at].get("action") or ""),
                                  _selector_key(mine[at].get("target"))) != want:
            at += 1
        if at >= len(mine):
            return {}
        row = mine[at]
        replay = browser_agent._replay_step(row) or {}
        if json.dumps(replay.get("target"), sort_keys=True, ensure_ascii=False) \
                != json.dumps(step.get("target"), sort_keys=True, ensure_ascii=False):
            return {}
        out[index] = row
        at += 1
    return out


def _try_fill(target, row, where: str, out: dict) -> None:
    """**一处**：卡是空的 + 回执里那一行解得出来 ⇒ 补；补了什么、跳过了什么都记下来。"""
    if not _card_is_empty(target):
        out["skipped"].append("%s（卡上已经有证据）" % where)
        return
    if not _card_is_empty(row.get("target")):
        out["skipped"].append("%s（账本里那张卡不是空的 —— 两份记录对不上）" % where)
        return
    receipt = row.get("receipt")
    card = browser_agent.card_from_receipt(
        receipt.get("target") if isinstance(receipt, dict) else None)
    if not card:
        out["skipped"].append("%s（回执里没有可解的那一行）" % where)
        return
    filled = _fill(target, card)
    if not filled:
        out["skipped"].append("%s（没什么可补的）" % where)
        return
    out["filled"].append("%s：补了 %s = %s" % (
        where, "、".join("`%s`" % k for k in filled),
        "、".join(repr(card[k]) for k in filled)))


def _only_card_keys_changed(old: str, new: str) -> tuple:
    """diff 里**每一处增删**都落在 `'label'` / `'text'` 这两个键的行上吗。

    这一条是给「机械改字面量」兜底的：只要有一行别的东西被带上（缩进、邻居、排版），
    这里就报 False —— 那份 py 不该写出去。
    """
    bad = []
    for line in difflib.unified_diff(old.split("\n"), new.split("\n"), lineterm="", n=0):
        if line[:1] not in ("-", "+") or line[:3] in ("---", "+++"):
            continue
        if not (("'label':" in line[1:]) or ("'text':" in line[1:])):
            bad.append(line)
    return (not bad), bad


def _literal_of(name: str, value) -> str:
    """那份字面量**用产物自己的渲染器**渲出来（与 `template.render` 填进去的是同一把）。"""
    if name == "STATES":
        return template._lit(template._stable_first(list(value)))
    return template._lit(template._stable_first(template._fills_map(value)))


def backfill(trace: str, py_path: str) -> dict:
    """干跑一次：返回 `{filled, skipped, unmatched, src}`（**不写盘**）。"""
    rows = [json.loads(line) for line in
            pathlib.Path(trace).read_text(encoding="utf-8").splitlines() if line.strip()]
    src = pathlib.Path(py_path).read_text(encoding="utf-8")
    found = {name: _module_literal(src, name) for name in ("STATES", "FILLS")}
    if not found.get("STATES"):
        raise SystemExit("%s 里没有模块级的 STATES 字面量 —— 这不是产物 py？" % py_path)

    out: dict = {"filled": [], "skipped": [], "unmatched": []}

    def _checked(name):
        """读出那一份字面量，并**先核一遍**：同一把渲染器把没改过的那份渲出来，
        要与文件里的逐字节相同 —— 对不上就说明这一份与当前渲染器不是同一版，拒绝写。"""
        node, start, end = found[name]
        value = ast.literal_eval(node)
        if _literal_of(name, value) != src[start:end]:
            raise SystemExit(
                "%s：动手之前先核过 —— 当前 `template._lit` 重渲这一份字面量与文件里的**不是"
                "同一串字节**（%d vs %d 字符）。这份 py 不能用机械改字面量那条路改，别写。"
                % (name, len(_literal_of(name, value)), end - start))
        return value, start, end

    # ── STATES（顺带把 `fill` → 那一行 的把手记下来，FILLS 要用）────────────────
    states, s_start, s_end = _checked("STATES")
    by_fill: dict = {}
    for group in states:
        steps = group.get("steps") or []
        matched = _match_rows(rows, group.get("name"), steps)
        if not matched:
            if steps:                      #: 一组里哪怕只有一步配不上，整组不认
                out["unmatched"].append(str(group.get("name")))
            continue
        for index, step in enumerate(steps):
            row = matched[index]
            if step.get("fill"):
                by_fill[str(step["fill"])] = row
            _try_fill(step.get("target"), row, "%s[%d] %s ← 账本第 %s 步" % (
                group.get("name"), index, step.get("action"), row.get("step_no")), out)

    # ── FILLS（`fill` 那个键就是把手：它在 STATES 里指着的那一步就是它的账本行）────
    spans = {"STATES": (s_start, s_end, _literal_of("STATES", states))}
    if found.get("FILLS"):
        fills, f_start, f_end = _checked("FILLS")
        for key, entry in fills.items():
            row = by_fill.get(str(key))
            if row is None:
                out["skipped"].append("FILLS[%s]（账本里没有引用它的那一步）" % key)
                continue
            _try_fill((entry or {}).get("target"), row,
                      "FILLS[%s] ← 账本第 %s 步" % (key, row.get("step_no")), out)
        spans["FILLS"] = (f_start, f_end, _literal_of("FILLS", fills))

    #: 偏移只在**原文件**上有效 ⇒ 从后往前替换（不然前一处一动，后一处的偏移就废了）。
    new_src = src
    for _name, (start, end, literal) in sorted(spans.items(), key=lambda kv: -kv[1][0]):
        new_src = new_src[:start] + literal + new_src[end:]
    out["src"] = new_src
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--trace", required=True,
                    help="一趟探路的账本：runtime/explore/<job>/attempt-*.jsonl")
    ap.add_argument("--py", required=True, help="这一步产出的那份产物 py")
    ap.add_argument("--write", action="store_true", help="真写（先备份 `<py>.bak-<日期>`）")
    args = ap.parse_args(argv)

    got = backfill(args.trace, args.py)
    src = pathlib.Path(args.py).read_text(encoding="utf-8")
    ok, bad = _only_card_keys_changed(src, got["src"])

    print("补了 %d 处 / 没动 %d 处 / 认不出对齐的组：%s"
          % (len(got["filled"]), len(got["skipped"]),
             "、".join(got["unmatched"]) or "（没有）"))
    for one in got["filled"]:
        print("  + %s" % one)
    print("diff 里只有 `'label'` / `'text'` 那两把键的行：%s" % ("是" if ok else "否"))
    if not ok:
        print("  ⚠️ 这几行是别的东西，**不写**：")
        for line in bad[:20]:
            print("    %s" % line)
        return 2
    if got["src"] == src:
        print("这份 py 一个字节都不用改。")
        return 0
    if not args.write:
        print("（干跑；要写就加 --write）")
        return 0
    backup = pathlib.Path("%s.bak-%s" % (args.py, datetime.date.today().strftime("%Y%m%d")))
    backup.write_text(src, encoding="utf-8")
    pathlib.Path(args.py).write_text(got["src"], encoding="utf-8")
    print("写了：%s（备份：%s）" % (args.py, backup))
    return 0


if __name__ == "__main__":
    sys.exit(main())
