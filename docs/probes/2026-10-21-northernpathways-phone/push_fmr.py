#!/usr/bin/env python3
"""把修好骨架的 northernpathways py 直接推到 FMR（写口）。

用法：
  python3 push_fmr.py            # 只读：读后端现值、留底、打印 key/sha（不写）
  python3 push_fmr.py --apply    # 真写：update_form_script(enable=True)，再回读校验

口径：
  - FMR 的 key 是「页面 URL（host+path，不带 scheme）」= northernpathways.pt/experiences
  - 写口要 FORM_SCRIPT_WRITE_TOKEN；source 逐字节上传（不做 strip）
  - 回读 sha256 必须与本地一致；不一致就用留底那份回滚
  - 写完再读 `form_config_sites` 的 status（上传 ≠ 启用）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path("/company/siteforge")
sys.path.insert(0, str(ROOT))
from agent import fmr  # noqa: E402

KEY = "northernpathways.pt/experiences"
HERE = pathlib.Path(__file__).resolve().parent
SOURCE = HERE / "northernpathways.fixed.py"


def read_env() -> dict:
    env = {}
    for line in (ROOT / "siteforge.env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k] = v
    return env


def read_script(client, key):
    last = None
    for allow in (False, True):
        try:
            return client.form_script(key, allow_disabled=allow)
        except Exception as exc:  # noqa: BLE001
            last = exc
            print("READ_ERR", "allow_disabled", allow, type(exc).__name__, str(exc)[:300])
    raise last


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default=KEY)
    ap.add_argument("--source", default=str(SOURCE))
    ap.add_argument("--operator", default="claude (用户 2026-10-21 16:07 确认，直接写口)")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    env = read_env()
    client = fmr.FmrClient(
        token=env.get("FMR_AGENT_TOKEN", ""),
        write_token=env.get("FORM_SCRIPT_WRITE_TOKEN", ""),
    )

    raw = pathlib.Path(args.source).read_bytes()
    source = raw.decode("utf-8")
    local_sha = hashlib.sha256(raw).hexdigest()
    print("LOCAL", args.source, "bytes", len(raw), "sha256", local_sha)
    assert "FILLS = {" in source, "这份不像产物（没有 FILLS）"
    assert "_glued_plus_number" in source, "这份没带骨架修复"
    assert "+12016423915" in source, "这份里没有测试用的电话"

    before = None
    try:
        before = read_script(client, args.key)
    except Exception as exc:  # noqa: BLE001
        print("READ_BEFORE_FAILED", type(exc).__name__, str(exc)[:300])
    if before is not None:
        bsrc = before.get("source") or ""
        print("BEFORE", json.dumps({k: before.get(k) for k in
                                    ("site", "requested_site", "type", "version",
                                     "sha256", "status", "config_status")},
                                   ensure_ascii=False))
        if bsrc:
            bsha = hashlib.sha256(bsrc.encode("utf-8")).hexdigest()
            out = HERE / ("fmr_before_%s.py" % bsha[:12])
            out.write_bytes(bsrc.encode("utf-8"))
            print("BACKUP", out, "bytes", len(bsrc.encode("utf-8")), "sha256", bsha)

    if not args.apply:
        print("DRY_RUN (加 --apply 才写)")
        return 0

    res = client.update_form_script(args.key, source, operator=args.operator, enable=True)
    print("WRITE", res.verdict, "status", res.status, "msg", res.msg)
    print("WRITE_SAY", res.say)

    after = None
    try:
        after = read_script(client, args.key)
    except Exception as exc:  # noqa: BLE001
        print("READ_AFTER_FAILED", type(exc).__name__, str(exc)[:300])
        after = None
    if after is None:
        print("ROLLBACK_REQUIRED: 写后读不回来（不知道写没写成）")
        return 1

    asrc = after.get("source") or ""
    after_sha = hashlib.sha256(asrc.encode("utf-8")).hexdigest()
    print("AFTER", json.dumps({k: after.get(k) for k in
                               ("site", "requested_site", "type", "version",
                                "sha256", "status", "config_status")},
                              ensure_ascii=False))
    print("AFTER_SHA_LOCAL", after_sha)
    print("MATCH_LOCAL", after_sha == local_sha)
    print("MATCH_BACKEND_REPORT", str(after.get("sha256") or "") == local_sha)

    if after_sha != local_sha:
        print("MISMATCH → 回滚")
        if before is not None and (before.get("source") or ""):
            rb = client.update_form_script(args.key, before["source"],
                                           operator=args.operator + "（自动回滚）")
            print("ROLLBACK", rb.verdict, rb.status, rb.say)
        else:
            print("ROLLBACK 没有留底（写之前读不到）—— 需要人工处理")
        return 2

    # 上传 ≠ 启用：单独读 status
    try:
        status = None
        for row in client.form_config_sites() or []:
            if str(row.get("site") or "") == args.key:
                status = row.get("status")
                break
        print("CONFIG_STATUS", status)
    except Exception as exc:  # noqa: BLE001
        print("CONFIG_STATUS_UNREADABLE", type(exc).__name__, str(exc)[:200])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
