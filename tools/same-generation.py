#!/usr/bin/env python3
"""跨站同代体检（宿主上跑，**只读**）：拉分组 → 拉每站产物 → `reuse.same_generation`。

【为什么有它 —— 2026-09-29 交接 §5 的真事】funchatt 与 thisromance / asiatalks /
sparkydates 同属**一个 config 组**，平台那套新问卷（`data-testid` 元素标记 + 状态写在
hash 的 base64 里）早在 09-17/09-18 就给**另一个组**迁过了 —— 那一组**漏在外面十来天**，
失败是**稳定**的，而发现它的方式是人开窗手工撞见。这个脚本把那一撞变成一句机器话。

用法：
    python3 tools/same-generation.py                 # 全量：每个站各拉一次（196 个请求）
    python3 tools/same-generation.py --sample 2      # 每个 config 组**抽** 2 家（抽样，会点名）
    python3 tools/same-generation.py --sleep 0.3 --timeout 15 --tries 2
    python3 tools/same-generation.py --base https://fmr.3tkj.cn

退出码：
    0  全量跑完、每一家都读到了、比成的组**都是同一代**（真·干净）
    1  **有不同代**（上面逐组点名：哪几家、差在哪几条）
    2  分组清单**拿不到** —— 这一次**没检查**（不是「都没问题」）
    3  跑完了但**有洞**：抽样了 / 有家读不到产物 / 有家没有 config 号 / 一组都没比成
       （「有洞」不等于「不同代」，更不等于「没问题」—— 三件事分开报）

⚠️ 只读：这个脚本**不改**任何配置、不写任何产物（那一族改东西的口它一个都不碰）。
⚠️ token 从 `FMR_AGENT_TOKEN` 来（与 `tools/auto-repair.py` 同一条：构造那一刻读环境）。
⚠️ **没有静默的路径**：拿不到分组、读不到源码、抽样、跳过 —— 全都出声，且分得开。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import fmr, reuse  # noqa: E402


def _source_of(client, site, *, tries):
    """这一家的**产物文本**（py 源码 / JSON 配置文本）→ `(文本, 归口, 为什么没读到)`。

    ⚠️ **读不到就是读不到**（回 `""` + 一句原因），绝不回一份空文本冒充「这个站没内容」。
    `归口` 是给报告**按类型归口**用的一小串机器味标签（`refused/404` / `unreachable`…），
    免得每一家的整句话各占一行（那一串里往往只差一个站名）。
    ⚠️ 重试**只对「连不上 / 超时」**（`FmrUnreachable`）—— 后端说 404 / 没配 token 是
    **定了的事**，重试只会把同一句话拖长（重试上限 = `--tries`）。
    """
    last = ("unreachable", "")
    for attempt in range(max(1, tries)):
        if attempt:
            time.sleep(2.0 ** attempt)                 # 退避；圈数由 tries 封死
        try:
            got = client.form_script(site)
        except fmr.FmrUnreachable as exc:
            last = ("unreachable", "连不上 / 超时（试了 %d 次）：%s" % (attempt + 1, exc))
            continue
        except fmr.FmrUnmeasured as exc:
            return "", _cause(exc), str(exc)           # 定了的事，重试没用
        kind = got.get("type")
        if kind == "py":
            return str(got.get("source") or ""), "", ""
        if kind == "json":
            # json 站没有源码，它跑的是**配置** —— 拿配置文本去比（这一关读的是文本形状：
            # testid 词汇 / 判据通道那几样，JSON 里照样读得出来）。
            try:
                return json.dumps(client.form_config(site), ensure_ascii=False,
                                  sort_keys=True), "", ""
            except fmr.FmrUnmeasured as exc:
                return "", _cause(exc), str(exc)
        return "", "no-type", "`type` 既不 `py` 也不 `json`（读回来的是 %r）" % (kind,)
    return "", last[0], last[1] or "试了 %d 次都没读到" % tries


def _cause(exc) -> str:
    """`FmrUnmeasured` 那颗 → 一小串归口标签（`refused/404` 这种）。"""
    status = getattr(exc, "status", None)
    return "%s/%s" % (getattr(exc, "kind", "?"), status) if status is not None else \
        str(getattr(exc, "kind", "?"))


def main() -> int:
    ap = argparse.ArgumentParser(
        description="跨站同代体检：同一个 config 组该同一代，不同代就喊（只读）")
    ap.add_argument("--base", default=None,
                    help="后端地址（默认 = 环境变量 FMR_BASE_URL，再默认线上那个）")
    ap.add_argument("--sample", type=int, default=0,
                    help="每个 config 组最多抽几家（0 = 不抽，全量）—— 抽样会在报告里点名")
    ap.add_argument("--sleep", type=float, default=0.1, help="每两个请求之间的节流（秒）")
    ap.add_argument("--timeout", type=float, default=15.0, help="单个请求的超时（秒）")
    ap.add_argument("--tries", type=int, default=2, help="每个站最多请求几次（重试上限）")
    got = ap.parse_args()

    client = fmr.FmrClient(base=got.base, timeout=got.timeout,
                           config_timeout=got.timeout)
    try:
        rows = client.form_config_sites()
    except fmr.FmrUnmeasured as exc:
        print("!! 分组清单拿不到 —— 这一次**没检查**（**不是**「都没问题」）：\n   %s"
              % exc, file=sys.stderr)
        return 2

    groups: dict = {}
    ungrouped: list = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        site = str(row.get("site") or "").strip()
        if not site:
            continue
        cid = row.get("config_id")
        if cid is None:
            ungrouped.append(site)
        else:
            groups.setdefault(cid, []).append(site)

    # 抽样：每组按站名排序取前 N 家（**确定性**，跑两遍抽的是同一批）。
    picked: list = []
    for cid in sorted(groups, key=lambda c: str(c)):
        sites = sorted(set(groups[cid]))
        limit = got.sample if got.sample > 0 else len(sites)
        picked.extend((cid, s) for s in sites[:limit])
    picked_sites = {site for _, site in picked}

    print("跨站同代体检（只读）—— 清单 %d 个站 / %d 个 config 组；"
          "这一次拉了 %d 家的产物去比。"
          % (len(rows), len(groups), len(picked)))
    if got.sample > 0:
        print("⚠️ **抽样**（`--sample %d`）：每组只拉了前 %d 家（按站名排序），"
              "**组里其余的家没检查** —— 这一次**不是全量**。" % (got.sample, got.sample))
        for cid in sorted(groups, key=lambda c: str(c)):
            head = sorted(set(groups[cid]))
            print("   组 %s：抽了 %s%s"
                  % (cid, "、".join(head[:got.sample]),
                     "" if len(head) <= got.sample else
                     "（组里另有 %d 家没抽）" % (len(head) - got.sample)))
    if ungrouped:
        print("⚠️ %d 个站**没有 config 号**（分不进任何一组，这一关没管它们）：%s"
              % (len(set(ungrouped)), "、".join(sorted(set(ungrouped)))))

    sources: dict = {}
    bad: dict = {}
    for i, (_, site) in enumerate(picked):
        if i:
            time.sleep(max(0.0, got.sleep))
        src, cause, why = _source_of(client, site, tries=got.tries)
        if src:
            sources[site] = src
        else:
            bad[site] = (cause, why)

    # ⚠️ 拿**被抽中那几家**的清单进比对：没抽中的站根本不进 `rows` ——
    # 不然 `same_generation` 会把「抽样没拉到」说成「读不到源码」，两件事就混了。
    sub_rows = [row for row in rows
                if isinstance(row, dict)
                and str(row.get("site") or "").strip() in picked_sites]
    out = reuse.same_generation(sub_rows, sources)
    for line in out["say"]:
        print(line)
    if bad:
        # ⚠️ 「哪几家读不到」上面那句已经点了名，这里补**为什么**（按归口标签分组，
        # 各组给一句样例）—— 「读不到」与「有没有问题」是两件事，原因不同下一步也不同。
        by_cause: dict = {}
        for site, (cause, why) in bad.items():
            by_cause.setdefault(cause, {"why": why, "sites": []})["sites"].append(site)
        print("⚠️ 读不到产物的 %d 家，原因是（按归口分组）：" % len(bad))
        for cause, info in sorted(by_cause.items(), key=lambda kv: -len(kv[1]["sites"])):
            print("   %d 家 [%s]：%s" % (len(info["sites"]), cause, info["why"]))
            print("      这几家：%s" % "、".join(sorted(info["sites"])))

    differs = any(one["same"] is False for one in out["groups"])
    if differs:
        print("—— 结论：%s（见上面逐组的「哪几家 / 差在哪」）" % out["verdict"])
        return 1
    holes = bool(bad or ungrouped or got.sample > 0 or not out["checked"])
    if holes:
        print("⚠️ 这一次**有洞**（上面带 ⚠️ 的每一行都是一处）—— "
              "别把这次的「没喊」当成「都没问题」。")
        return 3
    print("—— 结论：%s（全量跑完，每一家都读到了）" % out["verdict"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
