#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 lauradate 这个 landing 的**路径键**挂到 config 101（主机键的 75 不动）。

背景：真站 DOM 是 japansdates 那套（step-content-item-* + option-*），但后端只有
`lauradate.com` → config 75（LauraDateFiller，按文字点，读不到这页），3 条 failed 全停在
step1。101 的线上脚本（sha 4c8b9fbe）已在 8100 真跑成功，所以加一条**最长前缀**映射：

    lauradate.com/land/sp/a34293f4 -> 101
    lauradate.com                  -> 75（保持）

用法：
    python3 -I link_101.py            # 只读：拍 before、打印计划
    python3 -I link_101.py --apply    # 真写
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path("/company/siteforge")
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from agent import fmr  # noqa: E402

KEY = "lauradate.com/land/sp/a34293f4"
HOST = "lauradate.com"
TARGET = 101
EXPECT_DEEP = "4c8b9fbe52ca9b479085c8161f59975bf1a70b061c1db2d910a7fa5e7277b948"
EXPECT_HOST = "80b5c9bf05f85a337af199db5d7372c1cae241870e1cc007c15620d946ef112c"
BEFORE_JSON = HERE / "fmr_before_link.json"
AFTER_JSON = HERE / "fmr_after_link.json"
BEFORE75_PY = HERE / "fmr_before_config75.py"
RESULT = HERE / "fmr_link_result.json"
OPERATOR = "claude (2026-10-24 用户确认：101 真跑成功后，路径键 link 到 101)"
REASON = "lauradate a34293f4 的 DOM 是 japansdates；101 已真跑成功；只加路径级映射"


def read_env() -> dict:
    env = {}
    for line in (ROOT / "siteforge.env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k] = v
    return env


def row_of(rows, site):
    return next((r for r in rows if r.get("site") == site), None)


def snapshot(client) -> dict:
    rows = client.form_config_sites()
    deep = row_of(rows, KEY)
    host = row_of(rows, HOST)
    deep_sha = str(client.form_script(KEY).get("sha256") or "")
    host_sha = str(client.form_script(HOST).get("sha256") or "")
    return {"deep_row": deep, "host_row": host,
            "deep_script_sha256": deep_sha, "host_script_sha256": host_sha}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    env = read_env()
    client = fmr.FmrClient(token=env.get("FMR_AGENT_TOKEN", ""),
                           write_token=env.get("FORM_SCRIPT_WRITE_TOKEN", ""))

    before = snapshot(client)
    BEFORE75_PY.write_text(client.form_script(HOST).get("source") or "",
                           encoding="utf-8")
    BEFORE_JSON.write_text(json.dumps(before, ensure_ascii=False, indent=1) + "\n",
                           encoding="utf-8")
    print("BEFORE deep:", json.dumps(before["deep_row"], ensure_ascii=False),
          "script", before["deep_script_sha256"][:12])
    print("BEFORE host:", json.dumps(before["host_row"], ensure_ascii=False),
          "script", before["host_script_sha256"][:12])
    if before["deep_script_sha256"] != EXPECT_HOST:
        print("!! 深键现在解析到的不是 75 那份（80b5c9bf…）—— 先查清楚再写")
        return 1
    if not before["host_row"] or int(before["host_row"].get("config_id") or 0) != 75:
        print("!! 主机键现在不指向 75 —— 先查清楚再写")
        return 1

    if not args.apply:
        print("[只读] 没写。加 --apply 才写。")
        return 0

    linked = client.form_config_link(KEY, TARGET, operator=OPERATOR, reason=REASON)
    print("LINK", json.dumps(linked, ensure_ascii=False))

    after = snapshot(client)
    AFTER_JSON.write_text(json.dumps(after, ensure_ascii=False, indent=1) + "\n",
                          encoding="utf-8")
    print("AFTER deep:", json.dumps(after["deep_row"], ensure_ascii=False),
          "script", after["deep_script_sha256"][:12])
    print("AFTER host:", json.dumps(after["host_row"], ensure_ascii=False),
          "script", after["host_script_sha256"][:12])

    ok = True
    row = after["deep_row"] or {}
    if int(row.get("config_id") or 0) != TARGET or int(row.get("status") or 0) != 1:
        print("!! 深键映射不是 101/status 1")
        ok = False
    if after["deep_script_sha256"] != EXPECT_DEEP:
        print("!! 深键解析到的脚本不是 4c8b9fbe…")
        ok = False
    if (after["host_row"] or {}) != before["host_row"]:
        print("!! 主机键那一行变了")
        ok = False
    if after["host_script_sha256"] != EXPECT_HOST:
        print("!! 主机键解析到的脚本变了（75 那份被动过）")
        ok = False

    result = {"key": KEY, "target_config_id": TARGET, "linked": linked,
              "after": after, "host_unchanged": (after["host_row"] == before["host_row"]
                                                 and after["host_script_sha256"] == before["host_script_sha256"])}
    RESULT.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n",
                      encoding="utf-8")
    print("== OK" if ok else "!! 回读不对", "| 深键 -> 101, 主机键 -> 75 未动")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
