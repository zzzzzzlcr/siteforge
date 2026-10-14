#!/usr/bin/env python3
"""根因库报告（宿主上跑，**只读**）：同一个根因攒到第几份了。

## 为什么有它

2026-10-13 那次统计给的判据：**「同根因 ≤ 2 次」**——
`17 缺陷单 / 32 提交（操作层 ~59%、生成层 ~35%，遮挡类第 3 次超阈值）
⇒ 建议先落根因计数 + 停止闸`。**那条建议当时没落**，
于是「修不完」的感觉一直在，而没人说得清是**哪一个**根因在重复。

它要回答的就是那一句：**这一份单进来时，这个根因是第几次？**
第 1、2 次照常治那个站；**第 3 次停手，去治工具层** ——
再治一次站，同一件事还会在别的站上再来一遍。

## 用法

    python3 tools/rootcause.py                    # 读 docs/根因库.md
    python3 tools/rootcause.py --table X.md --docs Y/

退出码：
    0  读到了，没有根因到阈值
    2  **一行都没读到** —— 这一次没检查（**不是**「没有根因超标」）
    3  读到了，但**有洞**（有单没进库 / 库里有单盘上没了）或**有根因到阈值**

⚠️ 只读：它不写库、不改单。库是人打的（`docs/根因库.md`），这个脚本只数数。
⚠️ **不自动分类** —— 从标题猜根因，正是 2026-10-14 栽过的那类错
   （自测报告里一句固定文案「打折叠 / 遮挡 / 坐标假设」让 46 个普通步骤被算成「弹窗」）。
"""
from __future__ import annotations

import argparse
import collections
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: 「同根因 **≤ 2** 次」⇒ 第 3 份就该治工具层。判据出处见模块 docstring。
THRESHOLD = 3

#: 表里 **不属于任何根因** 的占位（还没拆完的单）——照旧列出来，但不参与计数，
#: 也不许被读成「这个根因只有 0 次」。
PLACEHOLDER = "（待拆）"

#: `修在哪` 的合法取值 —— **四档的寿命完全不同**（见 `docs/根因库.md` 那张表）。
#: 多档用 `+` 连。出现别的字 ⇒ **点名**（防这一格悄悄漂，漂了这库就不值钱了）。
WHERE_KINDS = ("骨架", "生成侧", "cdp", "产物", "还没治")
#: ★ **这一档是「修站」不是「修类」**：只在 `forms-*/sites/<site>.py` 那一份里，
#: **重出这个站就丢了**。工具要把它**单独喊一声** —— 那条 ★ 底下三把刀寿命不同，
#: 第一版只看得出「有刀」，看不出「这把刀下次还在不在」。
PRODUCT_ONLY = "产物"


def _clean(w: str) -> str:
    """去掉 markdown 强调符与空白 —— 表里 `**产物**` 与 `产物` **是同一档**。

    ⚠️ 不剥的话，加粗那几行会被判成「不认识的字」，而它们恰恰是**最该被认出来**的
    那几档（`**产物**` = 只在那一份产物里）。这一条实测栽过一次。
    """
    return str(w or "").strip().strip("*").strip()


def where_of(where: str) -> list:
    """`修在哪` 那一格 → 几个档（`骨架+生成侧` ⇒ `["骨架","生成侧"]`）。"""
    return [w for w in (_clean(x) for x in
                        str(where or "").replace("＋", "+").split("+")) if w]


def unknown_where(where: str) -> list:
    """那一格里**不属于四档**的字 —— 空表 = 合法。"""
    return [w for w in where_of(where) if w not in WHERE_KINDS]


def is_product_only(where: str) -> bool:
    """**只在产物里**（有 `产物`、没 `骨架`）⇒ 重出这个站就丢。"""
    ws = where_of(where)
    return PRODUCT_ONLY in ws and "骨架" not in ws

_TICKET = re.compile(r"缺陷单-[\w一-鿿\-]+")


def read_library(path: pathlib.Path) -> list[dict]:
    """读 `根因库.md` 的那张表 → 一行一条。**不是表格的行不认**（标题/正文照旧跳过）。"""
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s.startswith("|") or s.startswith("|--"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        #: ★ 数据行的判据是「**它点了单号**」，不是「它以 `|` 开头」——
        #: 这份文档里还有**别的表**（四档说明那张），拿 `|` 当判据会把它们一起吃进来。
        #: 实测栽过：那一吃，库里凭空多出「值」「`骨架`」「`cdp`」这些根因，
        #: 还报出 6 份「库里有、盘上没了」。
        if len(cells) < 5 or not _TICKET.search(cells[1]):
            continue
        for t in _TICKET.findall(cells[1]):
            rows.append({"cause": cells[0], "ticket": t,
                         "where": cells[2], "fix": cells[3], "tests": cells[4]})
    return rows


def survey(docs_dir: pathlib.Path, table_path: pathlib.Path) -> dict:
    """库 + 盘 → 一份统计。**读不到库 ⇒ `{"empty": True}`**（不是一份空表）。"""
    if not table_path.exists():
        return {"empty": True, "reason": "库不在盘上：%s" % table_path}
    rows = read_library(table_path)
    on_disk = sorted(p.stem for p in docs_dir.glob("缺陷单-*.md"))
    listed = sorted({r["ticket"] for r in rows})

    groups: dict = collections.OrderedDict()
    for r in rows:
        if r["cause"] == PLACEHOLDER:
            continue                       # 占位不计数，也不当成「出现过 0 次」
        g = groups.setdefault(r["cause"], {"cause": r["cause"], "tickets": [], "fix": set(),
                                           "tests": set(), "where": set(), "bad_where": set()})
        g["tickets"].append(r["ticket"])
        g["where"].update(where_of(r["where"]) or [""])
        g["bad_where"].update(unknown_where(r["where"]))
        if r["fix"]:
            g["fix"].add(r["fix"])
        if r["tests"]:
            g["tests"].add(r["tests"])
    glist = sorted(groups.values(), key=lambda g: (-len(g["tickets"]), g["cause"]))
    for g in glist:
        g["n"] = len(g["tickets"])
        g["fix"] = sorted(g["fix"])
        g["tests"] = sorted(g["tests"])
        g["where"] = sorted(g["where"])
        g["bad_where"] = sorted(g["bad_where"])
        g["product_only"] = sorted({t for t, w in
                                    zip(g["tickets"], [r["where"] for r in rows
                                                       if r["cause"] == g["cause"]])
                                    if is_product_only(w)})
    return {
        "empty": False,
        "groups": glist,
        "flagged": [g for g in glist if g["n"] >= THRESHOLD],
        "placeholders": sorted({r["ticket"] for r in rows if r["cause"] == PLACEHOLDER}),
        #: ⚠️ **待拆的单不参与这一格** —— 它们连根因都还没定，`修在哪` 当然是空的
        #: （实测：那一行的 `—` 被算成「不认识的字」，报了个假警）。
        "bad_where": sorted({w for r in rows if r["cause"] != PLACEHOLDER
                             for w in unknown_where(r["where"])}),
        "missing_from_table": [t for t in on_disk if t not in listed],
        "not_on_disk": [t for t in listed if t not in on_disk],
    }


def render(data: dict) -> str:
    if data.get("empty"):
        return ("**一行都没读到** —— 这一次没检查（不是「没有根因超标」）：\n  %s\n"
                % data.get("reason", ""))
    out = []
    gs = data["groups"]
    if not gs:
        return "**一行都没有**（表是空的）—— 这一次没量到，不是「一切正常」。\n"
    out.append("═" * 78)
    out.append("根因库  根因 %d 类  单 %d 份  阈值 %d（同根因到第 %d 份就该治工具层）"
               % (len(gs), sum(g["n"] for g in gs), THRESHOLD, THRESHOLD))
    out.append("")
    for g in gs:
        mark = "  ★ **该治工具层了**（第 %d 份）" % g["n"] if g["n"] >= THRESHOLD else ""
        out.append("  %-16s %d 份   修在哪：%s%s"
                   % (g["cause"], g["n"], "、".join(g["where"]) or "（没写）", mark))
        for t in g["tickets"]:
            out.append("        %s" % t)
        if g["product_only"]:
            #: ★ 这是这一格存在的理由：那条 ★ 底下三把刀**寿命不同** —— 第一版只看得出
            #: 「有刀」，看不出「这把刀下次还在不在」。只在产物里的 = **修了那个站**。
            out.append("        ⚠️ **有一刀只在那一份产物里**（重出这个站就丢 —— 那是")
            out.append("           「**修站**」不是「修类」），这几份是：")
            for t in g["product_only"]:
                out.append("              %s" % t)
        if not g["fix"]:
            out.append("        ⚠️ **没有「治它的那一刀」** —— 这个根因还没被工具层治过")
        if not g["tests"]:
            out.append("        ⚠️ **没有「覆盖它的用例」** —— 治了也没东西盖着")
    out.append("")
    holes = False
    if data["missing_from_table"]:
        holes = True
        out.append("  ⚠️ **盘上有、库里没有** %d 份 —— 「只出现过 N 次」可能是假的："
                   % len(data["missing_from_table"]))
        for t in data["missing_from_table"]:
            out.append("        %s" % t)
    if data["not_on_disk"]:
        holes = True
        out.append("  ⚠️ **库里有、盘上没了** %d 份 —— 库锈了（单被改名 / 删了？）："
                   % len(data["not_on_disk"]))
        for t in data["not_on_disk"]:
            out.append("        %s" % t)
    if data["placeholders"]:
        out.append("  （还没拆出根因的 %d 份，**没算进上面任何一类**）：" % len(data["placeholders"]))
        for t in data["placeholders"]:
            out.append("        %s" % t)
    if data.get("bad_where"):
        holes = True
        out.append("  ⚠️ **`修在哪` 那格出现了不认识的字** %s —— 四档是"
                   "「骨架 / 生成侧 / cdp / 产物 / 还没治」，别的字会让这一格**悄悄漂**："
                   % "、".join(data["bad_where"]))
    if not holes and not data["placeholders"]:
        out.append("  盘上与库里对得上，也没有待拆的单。")
    out.append("")
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="根因库报告（只读）")
    ap.add_argument("--table", default=None, help="库文件；默认 docs/根因库.md")
    ap.add_argument("--docs", default=None, help="缺陷单目录；默认 docs/")
    args = ap.parse_args(argv)
    docs_dir = pathlib.Path(args.docs) if args.docs else ROOT / "docs"
    table = pathlib.Path(args.table) if args.table else ROOT / "docs" / "根因库.md"
    data = survey(docs_dir, table)
    if data.get("empty"):
        sys.stderr.write(render(data))
        return 2
    sys.stdout.write(render(data))
    holes = bool(data["missing_from_table"] or data["not_on_disk"])
    return 3 if (holes or data["flagged"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
