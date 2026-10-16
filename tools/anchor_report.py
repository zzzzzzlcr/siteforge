#!/usr/bin/env python3
"""修站第二刀的量证：三处「往紧里收」各会新咬到几条。**只读**打开账本。

## 为什么要先量（spec §5.1）

本刀三处改动**都是往紧里收**，按仓里的规矩先量再改 —— 不然就是拿真语料赌。

| 桶 | 数什么 | 硬线 |
|---|---|---|
| **① 合并后新拒的步** | 用冻结那份合并之后，那条步「既没选择器也没锚」⇒ `_steps_must_be_findable` 会当场拒稿 | **0** |
| **② 空卡上的纯位置地址** | 今天放行、新规会报 `judge-fragile-no-basis` 的 | 单列交人裁 |
| **③ 只差大小写的标识** | 今天判 `observed`、新规判 `unsupported` 的 | 单列交人裁 |

**冻结那份怎么来**：账本里**没有** `observed` 那一格也能量 —— `_freeze_observed` 冻的就是
**第一次出稿时那张表**，而**第一版 `states`/`fills` 就是它**（build 路是 `journey.states()`、
fix 路是 `fix_states`，两条路的第一版都逐字等于冻的那份）。所以直接用**最早那一条**
（`addr_basis_report._rows` 已经按这个口径给）。真冻过的那份在就优先用它（同 `addr_basis_report`）。

## 三个桶「凭什么」—— 每一处都接到生产那根**唯一**的尺子上（别另写一份）

- **①** 用**真闸**（`template._steps_must_be_findable`）判，不另写判据。⚠️ 只数**新**拒的：
  **那一步**在交付那一版里本来就认不出来的不算（那不合并也拒 —— 那种草稿账本里本来就有，
  它是 render 当初拒掉的那一份；记进来会让这条硬线**虚红**，而虚红的下场就是有人去放宽规则）。
  ⚠️ 而这条守卫**落在每一步上**、**不许按整版判**（★ 2026-10-16 修复轮 1 评审）：交付那一版里
  只要有一条坏兄弟步，按整版判一次就会把「合并前认得出、合并后认不出」的那些步**一起漏掉**
  ⇒ 那是**假 0**，比虚报还坏（屏幕上与「真干净」长得一模一样）。
- **②** 三句合起来才是「今天放行、新规才报」，少一句都会走岔（2026-10-16 在两本真账本上量过）：
    1. 候选 = **生产采信的剩货**（`_kept_of`：`address_basis` 逐条过、`UNSUPPORTED` 的剔掉
       —— 与 `_rule_i` 里 `if level != UNSUPPORTED: kept.append(one)` **逐字同一句**）。
       拿产物的**整张**候选表去喂，一根**编的**地址就能把「纯位置那根」压下去 ⇒ 新规真会报的
       那一步**数漏**（漏报比虚报坏：人裁看不到，就放行了）。
    2. `_card_has_nothing(card, kept)` —— 本刀改过的**末句**（零断言的地址不算「有答案」）。
    3. 「**今天放行**」那一半 = 老末句为假 = `kept` 里有一根**不脆、又什么都没主张**的
       （`not _fragile(s) and not assertions_of(s)` = 纯位置）。⚠️ 少了这一句，那族
       「空卡 + 全是脆链」的步（老末句 `not [s for s in kept if not _fragile(s)]` 本来就会报它，
       即 nitrous 那族历史欠账）会被记成「新咬到」—— 实测 8100 上 17 条、8099 上 6 条全是这一族。
  ⚠️ **两支都要量**（`states` 里的步 **与** `FILLS` 里的字段 —— ★ 2026-10-16 修复轮 1 评审）：
  M2 落在 `_card_has_nothing` 上，而 `_rule_i` 的 **FILLS 支同样调它**
  （`if _card_has_nothing(card, kept) or not kept:` → `judge-fragile-no-basis`），old/new 的分裂
  一模一样 ⇒ 只循环 `states` 的话，「② = 0」这句话比它读起来**窄**（FILLS 那一支的槽位一个没数）。
  `FILLS` 那边的卡按**键**取：`card_of(冻结那一格.get("target"))` —— 与那一支**逐字同一句**
  （⚠️ 不取 `.get("target")` 而拿**整格**当卡是不行的：那一格自己带着 `label`，那就成了
  「拿产物自己的话当证据」，与那边「不许用 `fix._literals(src)` 取卡」同一条规矩）。
- **③** 与 `_rule_i` 同一句 `address_basis(产物那根, 卡, 卡上的候选)`；只要它 `UNSUPPORTED`、
  且与卡上某根**只差大小写**（本刀 M3 改的就是这一处比较：标识不再 `casefold`）⇒ 新判的。
  两支同样都数（`address_basis` 是共用的那一句，`FILLS` 那边只是卡与把手不同）。

⚠️ **不许为了让 ① 变绿而放宽规则** —— 那是这条硬线的全部意义（与上一刀 §5 同一条）。

## 用法（**在宿主上跑**，账本在宿主）

    python3 tools/anchor_report.py --ledger runtime/checkpoints.sqlite     # 8099 生产
    python3 tools/anchor_report.py --ledger runtime-222/checkpoints.sqlite # 8100 测试
"""
from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))     #: `_rows` / `_site_of` 住在隔壁那个工具里

from agent import fix, lint, template  # noqa: E402
import addr_basis_report as basis      # noqa: E402  （同一份「账本怎么读」的定义）


def _frozen_of(ch):
    """这一趟**冻结的那一份** —— 有就优先用，没冻过就拿第一版重建（`addr_basis_report` 的口径）。

    ⚠️ 只跳 `None`、**不跳空 `{}`**（那里「评审 ④」那段写着理由）：空的那份也是「真冻过的一份」，
    判据拿着它走的是**新路**（每一步的卡都空）—— 跳过它退回重建，量出来的就不是它了。
    """
    got = next((x for x in reversed(ch.get("observed") or []) if x is not None), None)
    if got is not None:
        return got
    states = (ch.get("states") or [None])[0]
    fills = (ch.get("fills") or [None])[0]
    if states is None and fills is None:
        return None
    return fix.observed_of(states, fills)


def _findable(states) -> bool:
    """这一版表**能不能进产物** —— 走**真闸**（`render` 里那一句就是它）。"""
    try:
        template._steps_must_be_findable(states)
        return True
    except ValueError:
        return False


def _kept_of(card, sels) -> tuple:
    """这一步**生产采信的候选** → `(kept, {那根地址: 级别})`（与 `lint._rule_i` 同一把尺子）。

    ⚠️ 顺序也要一样：逐条 `address_basis`，`UNSUPPORTED` 的当场剔掉（那根**编的**不算答案）。
    """
    kept: list = []
    levels: dict = {}
    for one in sels:
        level, _ = lint.address_basis(one, card, card.get("selectors") or [])
        levels[one] = level
        if level != lint.UNSUPPORTED:
            kept.append(one)
    return kept, levels


def _sweep(out: dict, tid: str, site: str, where: str, card, sels) -> None:
    """**一格**（`states` 里的一步 / 一个 `FILLS` 字段）的 ②③ —— 判据与生产**同一把尺子**。

    `where` 是给人的把手：`states` 那边是 `组名#步序`、`FILLS` 那边是 `FILLS:<键>`
    （与 `_rule_i` 的 `handle` **同形** —— 人拿着它回产物里找得到那一格）。
    ⚠️ 两支**共用这一处**（M2/M3 落在 `_card_has_nothing` 与 `address_basis` 上，
    而那两样是两支共用的）⇒ 只写一支等于少量一半，两边也不会各说各的。
    """
    kept, levels = _kept_of(card, sels)
    #: ② 见模块 docstring 那三句（缺一句不是虚报就是漏报）
    position_only = [s for s in kept if not lint._fragile(s) and not lint.assertions_of(s)]
    if kept and position_only and lint._card_has_nothing(card, kept):
        out["②空卡上的纯位置地址"].append((tid, site, "%s  %s" % (where, position_only[0])))
    for one in sels:
        if levels[one] != lint.UNSUPPORTED:
            continue                    # ③ 数的是**新**判 `unsupported` 的那些
        for other in (card.get("selectors") or []):
            if str(one).strip().casefold() == str(other).strip().casefold():
                out["③只差大小写的标识"].append(
                    (tid, site, "%s  %s  ←观测 %s" % (where, one, other)))
                break                   # 一根地址只记一次（卡上可能同名重复）


def measure(ledger: str) -> dict:
    """三个口径的分账 → `{桶名: [(thread_id, 站名, 位置), …]}`。

    ⚠️ 「位置」那一格三桶各长各的：① 是 `组名#步序`，②③ 在它后面接上**那根地址**
    （②③ 是交人裁的一栏，人第一眼看的就是「哪一趟、哪个站、哪一组第几步」——
    与 `_site_of` 那段注释同一条理由：没有把手的那一栏，人裁不了）。
    """
    out = {"①合并后新拒的步": [], "②空卡上的纯位置地址": [], "③只差大小写的标识": []}
    for tid, ch in sorted(basis._rows(ledger).items()):
        frozen = _frozen_of(ch)
        if frozen is None:          #: 连证据都没有的时候，唯一诚实的输出是「什么都没说」
            continue
        site = basis._site_of(ch)
        versions = [v for v in (ch.get("states") or []) if isinstance(v, list)]
        if not versions:
            continue
        # ① 只看**最后一版**（要交出去的那一版）：合并后认不认得出来。
        # ⚠️ `_findable` 只看 `states` ⇒ `fills` 取哪一版都不影响 ① 的数（这里取最后一版，
        #    与 `states` 同一口径）；判据是**真闸**，一个字没放宽。
        delivered = versions[-1]
        merged, _, _ = fix.anchors_from_observed(
            delivered, (ch.get("fills") or [{}])[-1] or {}, frozen)
        #: ⚠️ 合并是**逐个组、逐条步**重建的（`anchors_from_observed` 那两句 append）⇒
        #: 位置对齐就是 (组名, 步序) 对齐，「回交付那一版取同一步」照位置取即照那两个取
        #: （同名重复组也不会取错 —— 按名字找反而会）。
        for group, before in zip(merged, delivered):
            if not isinstance(group, dict):
                continue
            name = str(group.get("name") or "?")
            was = ((before or {}).get("steps") or []) if isinstance(before, dict) else []
            for index, step in enumerate(group.get("steps") or []):
                if _findable([dict(group, steps=[step])]):
                    continue                # 合并后照样认得出 ⇒ 不是「新拒」
                #: ★ 「新」落到**这一步**上：**那一步**在交付那一版里本来就认不出来 ⇒ 与合并
                #: 无关（账本里那族是 render 当初就拒掉的草稿），记进来这条硬线就**虚红**。
                #: ⚠️ 守卫**不许按整版判**：交付版里只要有一条坏兄弟步，整版跳过就会把
                #: 「合并前认得出、合并后认不出」的那些步**一起漏掉**（那是假 0）。
                if 0 <= index < len(was) and not _findable([dict(before, steps=[was[index]])]):
                    continue
                out["①合并后新拒的步"].append((tid, site, "%s#%d" % (name, index)))
        # ② / ③ 逐版看（判据是纯函数，逐版才是它在生产的每一次判决）
        for version in versions:
            for group in version:
                if not isinstance(group, dict):
                    continue
                name = str(group.get("name") or "?")
                for index, step in enumerate(group.get("steps") or []):
                    if not isinstance(step, dict):
                        continue
                    target = step.get("target") or {}
                    sels = [str(s) for s in (target.get("selectors") or []) if str(s or "").strip()]
                    if not sels:
                        continue
                    #: 卡走 `find_observed`（`lint._observed_card` 那一支就是它）——
                    #: 指纹优先，表被重排 / 地址被改写时照样认得出「这一步是谁」。
                    card = fix.find_observed(frozen, name, index, fix.card_of(target)) or {}
                    _sweep(out, tid, site, "%s#%d" % (name, index), card, sels)
        # ② / ③ 的 `FILLS` 那一支（`_rule_i` 里那一支**同样**调 `_card_has_nothing`，
        # 见模块 docstring）—— 把手是**键**，不是 (组名, 步序)
        for fill_version in (ch.get("fills") or []):
            if not isinstance(fill_version, dict):
                continue
            for key, fill in fill_version.items():
                if not isinstance(fill, dict):
                    continue
                target = fill.get("target") or {}
                sels = [str(s) for s in (target.get("selectors") or []) if str(s or "").strip()]
                if not sels:
                    continue
                frozen_fill = (frozen.get("fills") or {}).get(key)
                if not isinstance(frozen_fill, dict):
                    frozen_fill = {}        #: 冻结那份里没这一格 ⇒ 空卡（同 `anchors_from_observed`）
                #: ⚠️ 卡按**生产那一句**取（`_rule_i` 的 FILLS 支）：`card_of(那一格.get("target"))`
                #: —— **不是**拿整格当卡（那一格自己带着 `label`，那就成了「拿产物自己的话当证据」）。
                card = fix.card_of(frozen_fill.get("target"))
                _sweep(out, tid, site, "FILLS:%s" % key, card, sels)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", required=True)
    ap.add_argument("--limit", type=int, default=30)
    args = ap.parse_args(argv)
    got = measure(args.ledger)
    for name, rows in got.items():
        print("%s：%d" % (name, len(rows)))
    print("①必须是 0；②③逐条列出来交人裁（不许为了让它们变绿而放宽规则）")
    for name, rows in got.items():
        for one in rows[:args.limit]:
            print("  %-22s %-24s %s  %s" % (one[0], one[1], name, one[2]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
