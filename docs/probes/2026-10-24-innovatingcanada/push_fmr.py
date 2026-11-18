#!/usr/bin/env python3
"""把 innovatingcanada 的产物（弹层那一刀修好之后）推到 FMR。

用法：
  python3 push_fmr.py            # 只读：读后端现值、打印 key/sha（不写）
  python3 push_fmr.py --apply    # 真写：update_form_script(enable=True)，再回读校验

口径与 docs/probes/2026-10-23-firstday-checkout-drawer/push_fmr.py 同一条：
  - FMR 的 key 是「页面 URL（host+path，不带 scheme）」
  - 写口要 FORM_SCRIPT_WRITE_TOKEN（读口用 FMR_AGENT_TOKEN）
  - source 逐字节上传（不做 strip），回读 sha256 与本地 sha256 必须一致
  - 比对前按行尾归一
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

KEY = ("www.innovatingcanada.ca/industry-and-business/canada-nuclear-future-2026/"
       "why-canadas-nuclear-choice-is-also-a-supply-chain-choice")
SOURCE = pathlib.Path("/company/siteforge/forms-222/sites/innovatingcanada.py")
#: 推之前后端那一份的 sha（2026-10-24 读到的现值；行尾归一后与本地修改前一致）。
EXPECT_BEFORE = "fcdec2f7f87e41d62aed19003de1de0d904d5b241a9dd247a9387a82ced975d8"


def read_env() -> dict:
    env = {}
    for line in (ROOT / "siteforge.env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k] = v
    return env


def norm_sha(text: str) -> str:
    return hashlib.sha256(text.replace("\r\n", "\n").encode("utf-8")).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default=KEY)
    ap.add_argument("--source", default=str(SOURCE))
    ap.add_argument("--operator", default="claude (用户 2026-10-24：修 innovatingcanada 弹层)")
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
          "cookie_clear:", "def _clear_site_cookies(" in source,
          "| prefixes:", 'COOKIE_CLEAR_PREFIXES = ("hustle-", "inc_optin")' in source,
          "| real_reload:", 'self._do("goto", "", "about:blank")' in source,
          "| modal_holds:", "def _modal_holds_target(" in source,
          "| modal_call:", "target=target)" in source)

    before = None
    for allow in (False, True):
        try:
            before = client.form_script(args.key, allow_disabled=allow)
            break
        except Exception as exc:  # noqa: BLE001
            print("READ_BEFORE_ERR", "allow_disabled", allow, type(exc).__name__,
                  str(exc)[:300])
    if before is None:
        print("!! 读不到后端现值 —— 停（不猜）")
        return 1
    bsrc = before.get("source") or ""
    print("BEFORE", json.dumps({k: before.get(k) for k in
                                ("site", "requested_site", "type", "version",
                                 "sha256", "status", "config_status")},
                               ensure_ascii=False))
    print("BEFORE_SHA_LOCAL_NORM", norm_sha(bsrc), "matches_expect:",
          norm_sha(bsrc) == EXPECT_BEFORE)

    if not args.apply:
        print("DRY_RUN —— 没写。要写加 --apply")
        return 0
    if norm_sha(bsrc) != EXPECT_BEFORE:
        print("!! 后端现值与预期（%s）不一致 —— 停：别覆盖别人的版本" % EXPECT_BEFORE[:16])
        return 1

    result = client.update_form_script(args.key, source, operator=args.operator,
                                       enable=True)
    print("WRITE", json.dumps(result.as_dict(), ensure_ascii=False)[:800])
    if not result.ok:
        return 1

    after = None
    try:
        after = client.form_script(args.key)
    except Exception as exc:  # noqa: BLE001
        print("AFTER_NORMAL_READ_ERR", type(exc).__name__, str(exc)[:200])
        after = client.form_script(args.key, allow_disabled=True)
        print("!! 正常读读不到（多半 status=0）—— 停用行要面板手工启用")
    asrc = after.get("source") or ""
    print("AFTER", json.dumps({k: after.get(k) for k in
                               ("site", "type", "version", "sha256", "status")},
                              ensure_ascii=False))
    print("AFTER_SHA_LOCAL_NORM", norm_sha(asrc), "equals_local:",
          norm_sha(asrc) == norm_sha(source))
    try:
        debug = client.form_script(args.key, allow_disabled=True)
        print("DEBUG_READ status", debug.get("status"))
    except Exception as exc:  # noqa: BLE001
        print("DEBUG_READ_ERR", type(exc).__name__, str(exc)[:200])
    return 0 if norm_sha(asrc) == norm_sha(source) else 1


if __name__ == "__main__":
    sys.exit(main())
