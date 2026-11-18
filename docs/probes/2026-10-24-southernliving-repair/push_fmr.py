#!/usr/bin/env python3
"""把 southernliving 的产物（等落地页那一刀修好之后）推到 FMR。

用法：
  python3 push_fmr.py            # 只读：读后端现值、打印 key/sha（不写）
  python3 push_fmr.py --apply    # 真写：update_form_script(enable=True)，再回读校验

口径（与 docs/probes/2026-10-23-firstday-checkout-drawer/push_fmr.py 同一条）：
  - FMR 的 key 是「页面 URL（host+path，不带 scheme）」= 这条广告落地页的键
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

KEY = "www.southernliving.com/featured/original-plus/where-design-meets-real-life-ge"
SOURCE = pathlib.Path("/company/siteforge/forms-222/sites/southernliving.py")
#: 推之前后端那一份的 sha（2026-10-24 读到的现值；**按行尾归一**后的那份，
#: 与本地修改前的文件逐字节一致 = 525c3f84…）—— 不一致就停下来，别覆盖别人的版本。
EXPECT_BEFORE = "525c3f84b354646d88ad184eee47547249df3f3692df1890a898295ba8936709"


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
    ap.add_argument("--operator", default="claude (用户 2026-10-24：修 southernliving 落地页等待)")
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
          "landing_js:", "_LANDING_STATE_JS" in source,
          "| landing_wait:", "def _wait_landing_ready(" in source,
          "| call_run:", "self._wait_landing_ready()" in source,
          "| call_goto:", 'self._wait_landing_ready(why="这一次导航")' in source,
          "| const:", "LANDING_WAIT_SECONDS = 60.0" in source)

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

    #: ⚠️ 【我量的·2026-10-16】「上传 = 顺手启用」不成立：源码写进去了、`status` 可能仍是 0。
    #: 所以正常读失败时退回 `allow_disabled=True` 再读一次，并且把 status 原样打出来。
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
    print("AFTER_SHA_LOCAL_NORM", norm_sha(asrc), "equals_local:", norm_sha(asrc) == norm_sha(source))
    try:
        debug = client.form_script(args.key, allow_disabled=True)
        print("DEBUG_READ status", debug.get("status"), "config_status", debug.get("config_status"))
    except Exception as exc:  # noqa: BLE001
        print("DEBUG_READ_ERR", type(exc).__name__, str(exc)[:300])
    try:
        cfg = client.form_config(args.key)
        print("CONFIG", json.dumps({"site": cfg.get("site")}, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001
        print("CONFIG_ERR", type(exc).__name__, str(exc)[:200])
    return 0 if norm_sha(asrc) == norm_sha(source) else 1


if __name__ == "__main__":
    sys.exit(main())
