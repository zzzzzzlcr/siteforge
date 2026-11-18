#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 Atticus 的「资料字段名对不上」修复推到 FMR（config 162 的唯一站点）。

修复两处：
  ① 取值加别名：资料库的键是 `postal_code` / `phone_number` / `username`，
     产物原来只查 `postcode` / `phone` / `first_name` ⇒ 永远落回账本示例 `10001`。
  ② `_nudge` 同一屏最多重打 3 次（原来 `_nudged_sig` 一屏只给一次机会）。

口径（照 2026-10-24 那次）：
  · key = `try.atticus.com/ssd/benefits`（config 162 只有这一个站）；
  · source 逐字节上传（本地 LF），写完回读 sha/source 逐字节比对；
  · 不动 config status（站点行本来就是 status=1）。

用法：
    python3 -I push_fmr.py            # 只读：留底、打印计划
    python3 -I push_fmr.py --apply    # 真写
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
BEFORE = HERE / "fmr_before_229af4a2.py"
RESULT = HERE / "fmr_push_result4.json"
OPERATOR = "claude (2026-10-29 用户确认：点完 Next 先等页面变化再点)"
REASON = ("点完 Next 立刻重读、同屏就再点 ⇒ 8 次点击全被吞（页面在停止点击后才走）；"
          "改成点完轮询「这一屏的形状」变化（URL+题面+进度+格子+选项），最多 12 秒；"
          "另带测试用 SITEFORGE_NEXT_SETTLE 旋钮")


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
