#!/usr/bin/env python3
"""把 2026-10-26 修的 blavity 产物推到 FMR（key = `blavity.com`）。

用法：
  python3 push_fmr.py            # 只读：读后端现值、打印 sha（不写）
  python3 push_fmr.py --apply    # 真写：update_form_script(enable=True)，再回读校验

口径与 docs/probes/2026-10-24-southernliving-repair/push_fmr.py 同一条：
  - key 是后端那一行存的站点键（这站是裸域 `blavity.com`，不是文章长键）
  - source 逐字节上传；后端读回来会把 LF 换成 CRLF ⇒ 比对前按行尾归一
  - 写之前先核后端现值（EXPECT_BEFORE）：不一致就停
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

KEY = "blavity.com"
SOURCE = pathlib.Path("/company/siteforge/docs/probes/2026-10-26-blavity-adblock-nag/blavity.py")
#: 改之前那份（2026-10-10 那版，含广告弹窗补丁 + Quantcast 隐私弹层）。按行尾归一后的 sha。
EXPECT_BEFORE = "60d79a2bd3218ac0fb0defc6488d721a79d1caece4352117d76335a53332c6dd"


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
    ap.add_argument("--operator", default="claude (用户 2026-10-26：适配 adblocker 全屏弹窗)")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    env = read_env()
    client = fmr.FmrClient(
        token=env.get("FMR_AGENT_TOKEN", ""),
        write_token=env.get("FORM_SCRIPT_WRITE_TOKEN", ""),
    )

    raw = pathlib.Path(args.source).read_bytes()
    source = raw.decode("utf-8")
    print("LOCAL", args.source, "bytes", len(raw),
          "sha256", hashlib.sha256(raw).hexdigest()[:16],
          "norm", norm_sha(source)[:16])

    before = None
    for allow in (False, True):
        try:
            before = client.form_script(args.key, allow_disabled=allow)
            break
        except Exception as exc:  # noqa: BLE001
            print("READ_BEFORE_ERR allow_disabled=%s %s %s"
                  % (allow, type(exc).__name__, str(exc)[:200]))
    if before is None:
        print("!! 读不到后端现值 —— 停（不猜）")
        return 1
    bsrc = before.get("source") or ""
    print("BEFORE", json.dumps({k: before.get(k) for k in
                                ("site", "type", "version", "sha256", "status")},
                               ensure_ascii=False))
    print("BEFORE_NORM", norm_sha(bsrc)[:16], "matches_expect:",
          norm_sha(bsrc) == EXPECT_BEFORE)

    if not args.apply:
        print("DRY_RUN —— 没写。要写加 --apply")
        return 0
    if norm_sha(bsrc) != EXPECT_BEFORE:
        print("!! 后端现值与预期（%s）不一致 —— 停：别覆盖别人的版本" % EXPECT_BEFORE[:16])
        return 1

    result = client.update_form_script(args.key, source, operator=args.operator,
                                       enable=True)
    print("WRITE", json.dumps(result.as_dict(), ensure_ascii=False)[:600])
    if not result.ok:
        return 1

    after = None
    try:
        after = client.form_script(args.key)
        print("AFTER_NORMAL_READ ok")
    except Exception as exc:  # noqa: BLE001
        print("AFTER_NORMAL_READ_ERR", type(exc).__name__, str(exc)[:160])
        after = client.form_script(args.key, allow_disabled=True)
    asrc = after.get("source") or ""
    print("AFTER", json.dumps({k: after.get(k) for k in
                               ("site", "type", "version", "sha256", "status")},
                              ensure_ascii=False))
    print("AFTER_NORM", norm_sha(asrc)[:16],
          "equals_local:", norm_sha(asrc) == norm_sha(source))
    return 0 if norm_sha(asrc) == norm_sha(source) else 1


if __name__ == "__main__":
    sys.exit(main())
