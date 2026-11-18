#!/usr/bin/env python3
"""把 firstday 的产物（购物车抽屉那一刀修好之后）推到 FMR。

用法：
  python3 push_fmr.py            # 只读：读后端现值、留底、打印 key/sha（不写）
  python3 push_fmr.py --apply    # 真写：update_form_script(enable=True)，再回读校验

口径（与 docs/probes/2026-10-22-atticus-loop-limit/push_fmr.py 同一条）：
  - FMR 的 key 是「页面 URL（host+path，不带 scheme）」= `firstday.com`
  - 写口要 FORM_SCRIPT_WRITE_TOKEN（读口用 FMR_AGENT_TOKEN）
  - source 逐字节上传（不做 strip），回读 sha256 与本地 sha256 必须一致
  - 后端读回来会把 LF 换成 CRLF（2026-10-22 实测）⇒ 比对前先按行尾归一
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

KEY = "firstday.com"
SOURCE = pathlib.Path("/company/siteforge/forms/sites/firstday.py")
HERE = pathlib.Path(__file__).resolve().parent


def read_env() -> dict:
    env = {}
    for line in (ROOT / "siteforge.env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k] = v
    return env


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default=KEY)
    ap.add_argument("--source", default=str(SOURCE))
    ap.add_argument("--operator", default="claude (用户 2026-10-23 20:13：推 FMR)")
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
    print("MARKERS",
          "guard_body:", "_MODAL_HOLDS_TARGET_BODY" in source,
          "| guard_method:", "def _modal_holds_target(" in source,
          "| hook_name:", '_MODAL_HOOK_NAME = "盖住整页的模态"' in source,
          "| checkout:", "'text': 'CHECKOUT+'" in source,
          "| success:", "Yes, keep me up to date on tips and exclusive offers from First Day" in source,
          "| fills_empty:", "FILLS = {}" in source)

    before = None
    for allow in (False, True):
        try:
            before = client.form_script(args.key, allow_disabled=allow)
            break
        except Exception as exc:  # noqa: BLE001
            print("READ_BEFORE_ERR", "allow_disabled", allow, type(exc).__name__,
                  str(exc)[:300])
    if before is not None:
        bsrc = before.get("source") or ""
        print("BEFORE", json.dumps({k: before.get(k) for k in
                                    ("site", "requested_site", "type", "version",
                                     "sha256", "status", "config_status")},
                                   ensure_ascii=False))
        if bsrc:
            bsha = hashlib.sha256(bsrc.encode("utf-8")).hexdigest()
            out = HERE / ("fmr_before_%s.py" % bsha[:12])
            if not out.exists():
                out.write_bytes(bsrc.encode("utf-8"))
            print("BACKUP", out, "bytes", len(bsrc.encode("utf-8")), "sha256", bsha)

    if not args.apply:
        print("DRY_RUN (加 --apply 才写)")
        return 0

    res = client.update_form_script(args.key, source, operator=args.operator, enable=True)
    print("WRITE", res.verdict, "status", res.status, "msg", res.msg)
    print("WRITE_SAY", res.say)

    after = None
    for allow in (False, True):
        try:
            after = client.form_script(args.key, allow_disabled=allow)
            break
        except Exception as exc:  # noqa: BLE001
            print("READ_AFTER_ERR", "allow_disabled", allow, type(exc).__name__,
                  str(exc)[:300])
    if after is None:
        print("READ_AFTER_FAILED")
        return 1

    asrc = after.get("source") or ""
    #: 后端读回来会把 LF 换成 CRLF ⇒ 按行尾归一之后再比，否则会报一个假的「没对上」。
    def _norm(text: str) -> bytes:
        return text.replace("\r\n", "\n").encode("utf-8")

    after_sha = hashlib.sha256(_norm(asrc)).hexdigest()
    after_raw_sha = hashlib.sha256(asrc.encode("utf-8")).hexdigest()
    print("AFTER", json.dumps({k: after.get(k) for k in
                               ("site", "requested_site", "type", "version",
                                "sha256", "status", "config_status", "linked")},
                              ensure_ascii=False))
    print("AFTER_SHA_LOCAL(normalized)", after_sha)
    print("AFTER_SHA_RAW", after_raw_sha)
    print("MATCH_LOCAL", after_sha == local_sha)
    print("MATCH_BACKEND_REPORT", str(after.get("sha256") or "") in (local_sha, after_raw_sha))
    print("AFTER_MARKERS",
          "guard_body:", "_MODAL_HOLDS_TARGET_BODY" in asrc,
          "| guard_method:", "def _modal_holds_target(" in asrc,
          "| checkout:", "'text': 'CHECKOUT+'" in asrc)
    return 0 if after_sha == local_sha else 2


if __name__ == "__main__":
    raise SystemExit(main())
