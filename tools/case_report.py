#!/usr/bin/env python3
"""案例账本报告（宿主上跑，**只读**）：失败族分布 + 局部修复覆盖率 + 可回灌的成功修法。

账本由 `agent/graph.py::_record_case` 写（`forms/sites/.repair-fingerprints.jsonl`），
一行 = 一次失败的收尾（交付 / 触顶 / 卡住）。**只有 `verification.delivered` 为真的行**
算「已验证经验」；失败方案不进提示词。

这份报告回答三个问题（用户 2026-11-03 裁定的那三格）：
  ① 哪些失败族在反复出现（该治工具层，不是继续让模型改稿）；
  ② 局部修复到底覆盖了多少（拿不到步号 / 有步号但没锚 / 有锚且约束生效）；
  ③ 有多少条**已验证**的成功修法真的能回灌给下一次。
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_LEDGER = ROOT / "forms" / "sites" / ".repair-fingerprints.jsonl"


def read_rows(path: pathlib.Path) -> list:
    """读账本；坏行跳过（**不是**「没有案例」的判据）。读不到 ⇒ 空表。"""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows = []
    for line in lines:
        try:
            got = json.loads(line)
        except ValueError:
            continue
        if isinstance(got, dict):
            rows.append(got)
    return rows


def _verification(row: dict) -> dict:
    got = row.get("verification")
    return got if isinstance(got, dict) else {}


def _repair(row: dict) -> dict:
    got = row.get("repair")
    return got if isinstance(got, dict) else {}


def survey(rows: list) -> dict:
    """账本 → 统计。**纯函数**（测试拿它算，不看渲染）。"""
    by_family = collections.OrderedDict()
    local = {"no_step": 0, "step_no_scope": 0, "scope": 0, "scope_local": 0}
    delivered = 0
    hinted = 0
    legacy = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        family = str(row.get("family") or "unknown")
        one = by_family.setdefault(family, {"family": family, "n": 0, "delivered": 0})
        one["n"] += 1
        verification = _verification(row)
        repair = _repair(row)
        if "verification" not in row:
            legacy += 1                   # 2026-11-03 之前的行只有 site/fingerprint/at
        ok = verification.get("delivered") is True
        if ok:
            delivered += 1
            one["delivered"] += 1
            if verification.get("selftest") is True and str(repair.get("diff") or "").strip():
                hinted += 1
        step = row.get("failed_step")
        scope = repair.get("changed_scope")
        if step is None:
            local["no_step"] += 1
        elif not isinstance(scope, dict) or not scope:
            local["step_no_scope"] += 1
        else:
            local["scope"] += 1
            if repair.get("local") is True:
                local["scope_local"] += 1
    groups = sorted(by_family.values(), key=lambda g: (-g["n"], g["family"]))
    return {"total": len(rows), "delivered": delivered,
            "failed": len(rows) - delivered, "hinted": hinted,
            "legacy": legacy, "groups": groups, "local": local}


def render(data: dict) -> str:
    if not data["total"]:
        return ("**一行案例都没有** —— 这不是「没有失败」，是这一次没读到账本。\n")
    out = ["═" * 78,
           "案例账本  共 %d 例（已验证交付 %d / 未交付 %d），可回灌 %d 条"
           % (data["total"], data["delivered"], data["failed"], data["hinted"]),
           ""]
    if data.get("legacy"):
        out.append("（其中 %d 行是 2026-11-03 之前的旧格式：没有族/步号/验证结果）"
                   % data["legacy"])
    out.append("失败族")
    for one in data["groups"]:
        out.append("  %-14s %d 例   已验证 %d"
                   % (one["family"], one["n"], one["delivered"]))
    local = data["local"]
    out += ["",
            "局部修复覆盖",
            "  没拿到失败步号（只能整版重写）          %d" % local["no_step"],
            "  有步号但算不出锚（也只能整版重写）      %d" % local["step_no_scope"],
            "  有锚（局部约束）：%d   其中约束生效：%d"
            % (local["scope"], local["scope_local"])]
    if local["scope"] and local["scope"] != local["scope_local"]:
        out.append("  ⚠️ 有锚却没生效 %d 例 —— `_keep_repair_local` 的接线断了？"
                   % (local["scope"] - local["scope_local"]))
    if data["hinted"] < data["delivered"]:
        out.append("  （已交付里 %d 例没有可回灌的 diff：改前/改后没量到）"
                   % (data["delivered"] - data["hinted"]))
    out.append("")
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="案例账本报告（只读）")
    ap.add_argument("--ledger", default=None, help="账本文件；默认 forms/sites/.repair-fingerprints.jsonl")
    args = ap.parse_args(argv)
    path = pathlib.Path(args.ledger) if args.ledger else DEFAULT_LEDGER
    data = survey(read_rows(path))
    sys.stdout.write(render(data))
    return 0 if data["total"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
