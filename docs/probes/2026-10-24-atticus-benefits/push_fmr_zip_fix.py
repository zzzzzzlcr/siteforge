#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 Atticus zip 屏的修复推到 FMR（config 162 的唯一站点）。

修复：`_nudge` 原来用 `cdp form` 重打 —— 只改 DOM、不进 React state（真站量到 45 秒 Next
仍禁用）；改成原生 setter + keydown/keypress/input/keyup/change/blur，1–2 秒 Next 变可用。

口径：
  · key = `try.atticus.com/ssd/benefits`（config 162 只有这一个站）；
  · source 逐字节上传（本地是 LF；线上旧版是 CRLF，归一化后 diff 只有这一刀）；
  · 带 sha256 让后端入库前校验；写完回读 sha/source 逐字节；
  · 不动 config status（站点行本来就是 status=1）。

用法：
    python3 -I push_fmr_zip_fix.py            # 只读：留底、打印计划
    python3 -I push_fmr_zip_fix.py --apply    # 真写
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
import urllib.request

ROOT = pathlib.Path("/company/siteforge")
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from agent import fmr  # noqa: E402

KEY = "try.atticus.com/ssd/benefits"
SRC = ROOT / "forms-222/sites/try.loop.py"
BEFORE = HERE / "fmr_before_e5aaf969.py"
RESULT = HERE / "fmr_push_result.json"
OPERATOR = "claude (2026-10-24 用户确认：zip 屏原生 setter+事件修复)"
REASON = ("cdp form 只改 DOM 不进 React state；_nudge 改原生 setter+全套事件"
          "（真站量证 45s Next 仍禁用 vs 事件后 1-2s 可用）")


def read_env() -> dict:
    env = {}
    for line in (ROOT / "siteforge.env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k] = v
    return env


def post_update(base: str, write_token: str, source: str, local_sha: str) -> dict:
    body = {"site": KEY, "source": source, "operator": OPERATOR, "type": "py",
            "sha256": local_sha, "status": 1, "reason": REASON}
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        base + fmr.FORM_SCRIPT_UPDATE_PATH, data=raw, method="POST",
        headers={"Content-Type": "application/json; charset=utf-8",
                 "X-Api-Token": write_token})
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    env = read_env()
    client = fmr.FmrClient(token=env.get("FMR_AGENT_TOKEN", ""),
                           write_token=env.get("FORM_SCRIPT_WRITE_TOKEN", ""))
    raw = SRC.read_bytes()
    source = raw.decode("utf-8")
    local_sha = hashlib.sha256(raw).hexdigest()
    print("LOCAL", SRC, "bytes", len(raw), "sha256", local_sha)

    before = client.form_script(KEY)
    before_src = str(before.get("source") or "")
    BEFORE.write_bytes(before_src.encode("utf-8"))
    print("BEFORE", KEY, "sha256", before.get("sha256"), "version", before.get("version"),
          "bytes", len(before_src.encode("utf-8")))
    print("BEFORE 留底 ->", BEFORE.name)

    sites = [r for r in client.form_config_sites()
             if int(r.get("config_id") or 0) == 162]
    print("CONFIG 162 sites", len(sites), json.dumps(sites, ensure_ascii=False))
    if len(sites) != 1 or sites[0].get("site") != KEY:
        print("!! config 162 不只有这一个站 —— 停手")
        return 1

    if not args.apply:
        print("[只读] 没写。加 --apply 才写。")
        return 0

    payload = post_update(client.base, env.get("FORM_SCRIPT_WRITE_TOKEN", ""),
                          source, local_sha)
    status = int(payload.get("status") or 0)
    data = payload.get("data") or {}
    print("WRITE status", status, "msg", payload.get("msg"),
          "data", json.dumps(data, ensure_ascii=False))
    if status != 200:
        print("!! 写没成功 —— 不做后面的事")
        return 1
    if data.get("created"):
        print("!! 意外：这次建了新行 —— 停手，先查 key 解析到哪条 config")
        return 1

    d = client.form_script(KEY)
    rb_sha = str(d.get("sha256") or "")
    rb_src = str(d.get("source") or "")
    print("READBACK sha", rb_sha[:16], "bytes", len(rb_src.encode("utf-8")))
    if rb_sha != local_sha or rb_src.encode("utf-8") != raw:
        print("!! 回读与本地不一致")
        return 1

    sites2 = [r for r in client.form_config_sites()
              if int(r.get("config_id") or 0) == 162]
    print("AFTER config 162 sites", len(sites2), json.dumps(sites2, ensure_ascii=False))
    if len(sites2) != 1 or sites2[0].get("site") != KEY:
        print("!! config 162 的站点行变了")
        return 1

    result = {"key": KEY, "local_sha256": local_sha, "before_sha256": before.get("sha256"),
              "backend_sha256": data.get("sha256"), "readback_sha256": rb_sha,
              "created": data.get("created"), "sites": sites2}
    RESULT.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n",
                      encoding="utf-8")
    print("== OK：%s 已更新为 %s（config 162 唯一站点，status=1）"
          % (KEY, local_sha[:12]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
