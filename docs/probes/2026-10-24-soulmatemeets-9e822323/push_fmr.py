#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 9e822323 的 image-button 变体推成**自己的新 config**（不挂 66）。

为什么不是 66：`formScript/update` 写的是 **config 行**的 `script_source`。拿 66 组里
已有的键写，会把 66 那 13 个站的脚本一起换掉；拿**新键**写，后端 `createIfMissing=true`
会新建一条只含这个站的 config 行（`status=1`），脚本只进那一条。随后
`formConfig/link` 把站挂到**这条新 config_id**（不是 66），只加一条映射。

口径（与 2026-10-23 soulmatemeets 那趟同一条）：
  · 写口要 `FORM_SCRIPT_WRITE_TOKEN`（读口用 `FMR_AGENT_TOKEN`，都在 siteforge.env）；
  · `source` 逐字节上传（不 strip），带 `sha256` 让后端在入库前校验；
  · 写完回读 `formScript` 的 sha256 / 源码逐字节，再 link，再回读 `formConfig/sites`；
  · **前后各拍一次 config 66**：脚本 sha + 13 个站清单，必须逐字不变。

用法：
    python3 -I push_fmr.py            # 只读：拍 66 的底、打印计划
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

KEY = "soulmatemeets.com/land/sp/9e822323"
SOURCE = ROOT / "forms/sites/soulmatemeets_imagebutton.py"
BEFORE66_JSON = HERE / "fmr_before_config66.json"
BEFORE66_PY = HERE / "fmr_before_goldenagesouls.py"
AFTER66_JSON = HERE / "fmr_after_config66.json"
PUSH_RESULT = HERE / "fmr_push_result.json"
OPERATOR = "claude (2026-10-24 用户确认：推成独立新 config，不挂 66)"
REASON = "9e822323 = 语言闸 + image-button 变体；独立 config，避免改 66 组的脚本"


def read_env() -> dict:
    env = {}
    for line in (ROOT / "siteforge.env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k] = v
    return env


def sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def snapshot66(client) -> dict:
    rows = client.form_config_sites()
    sites = [{"site": r.get("site"), "config_id": r.get("config_id"),
              "status": r.get("status")}
             for r in rows if int(r.get("config_id") or 0) == 66]
    d = client.form_script("goldenagesouls.com")
    return {"script_sha256": d.get("sha256"), "version": d.get("version"),
            "source": d.get("source") or "", "sites": sites}


def public_snapshot(snap: dict) -> dict:
    return {"script_sha256": snap["script_sha256"], "version": snap["version"],
            "sites": snap["sites"]}


def post_update(base: str, write_token: str, source: str, local_sha: str) -> dict:
    body = {
        "site": KEY,
        "source": source,
        "operator": OPERATOR,
        "type": "py",
        "sha256": local_sha,
        "status": 1,
        "reason": REASON,
    }
    raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        base + fmr.FORM_SCRIPT_UPDATE_PATH, data=raw, method="POST",
        headers={"Content-Type": "application/json; charset=utf-8",
                 "X-Api-Token": write_token})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    env = read_env()
    client = fmr.FmrClient(token=env.get("FMR_AGENT_TOKEN", ""),
                           write_token=env.get("FORM_SCRIPT_WRITE_TOKEN", ""))
    raw = SOURCE.read_bytes()
    source = raw.decode("utf-8")
    local_sha = sha_bytes(raw)
    print("LOCAL", SOURCE.name, "bytes", len(raw), "sha256", local_sha)

    before = snapshot66(client)
    BEFORE66_PY.write_text(before["source"], encoding="utf-8")
    BEFORE66_JSON.write_text(json.dumps(public_snapshot(before), ensure_ascii=False,
                                        indent=1) + "\n", encoding="utf-8")
    print("BEFORE 66: sha", before["script_sha256"][:16], "version", before["version"],
          "sites", len(before["sites"]))
    for s in before["sites"]:
        print("   ", s["status"], s["site"])

    if not args.apply:
        print("[只读] 没写。加 --apply 才写。")
        return 0

    print("WRITE new key ->", KEY)
    payload = post_update(client.base, env.get("FORM_SCRIPT_WRITE_TOKEN", ""),
                          source, local_sha)
    status = int(payload.get("status") or 0)
    data = payload.get("data") or {}
    print("WRITE status", status, "msg", payload.get("msg"), "data", json.dumps(data))
    if status != 200:
        print("!! 写没成功 —— 不做后面的事")
        return 1
    if not data.get("created"):
        print("!! 意外：这次没有建新行（created=false）—— 停手，先查这个键现在属于哪条 config")
        return 1
    config_id = int(data.get("config_id") or 0)
    if config_id <= 0:
        print("!! 回执里没有 config_id —— 停手")
        return 1

    # 回读脚本：sha256 与源码逐字节
    d = client.form_script(KEY)
    rb_sha = str(d.get("sha256") or "")
    rb_src = str(d.get("source") or "")
    print("READBACK", KEY, "sha", rb_sha[:16], "bytes", len(rb_src.encode("utf-8")))
    if rb_sha != local_sha or rb_src.encode("utf-8") != raw:
        print("!! 回读与本地不一致 —— 不 link，先查")
        return 1

    # 只把站挂到新建的这条 config
    try:
        linked = client.form_config_link(KEY, config_id, operator=OPERATOR, reason=REASON)
    except Exception as exc:  # noqa: BLE001
        print("!! link 失败：", type(exc).__name__, str(exc)[:300])
        return 1
    print("LINK", json.dumps(linked, ensure_ascii=False))

    rows = client.form_config_sites()
    row = next((r for r in rows if r.get("site") == KEY), None)
    print("SITES ROW", json.dumps(row, ensure_ascii=False))
    if not row or int(row.get("config_id") or 0) != config_id \
            or int(row.get("status") or 0) != 1:
        print("!! 映射回读不对 —— config_id 应为 %s、status 应为 1" % config_id)
        return 1

    # 66 必须逐字不变
    after = snapshot66(client)
    AFTER66_JSON.write_text(json.dumps(public_snapshot(after), ensure_ascii=False,
                                       indent=1) + "\n", encoding="utf-8")
    same = (after["script_sha256"] == before["script_sha256"]
            and after["version"] == before["version"]
            and after["sites"] == before["sites"]
            and after["source"] == before["source"])
    print("AFTER 66: sha", after["script_sha256"][:16], "version", after["version"],
          "sites", len(after["sites"]), "| unchanged:", same)
    if not same:
        print("!! config 66 变了 —— 立刻停手核对！")
        return 1

    result = {"key": KEY, "local_sha256": local_sha, "config_id": config_id,
              "created": data.get("created"), "linked": linked,
              "readback_sha256": rb_sha, "sites_row": row,
              "config66_unchanged": same,
              "config66_sha256": after["script_sha256"]}
    PUSH_RESULT.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n",
                           encoding="utf-8")
    print("== OK：新 config", config_id, "只挂", KEY,
          "；config 66（13 站，sha", after["script_sha256"][:12], "）未动")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
