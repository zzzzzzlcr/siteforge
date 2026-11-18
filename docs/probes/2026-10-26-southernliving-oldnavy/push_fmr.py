#!/usr/bin/env python3
"""把 2026-10-26 修好的 southernliving 产物推到 FMR（premium 文章那条键）。

用法：
  python3 push_fmr.py            # 只读：读后端现值、打印 key/sha（不写）
  python3 push_fmr.py --apply    # 真写：update_form_script(enable=True)，再回读校验；
                                 #       仍是 status=0 就 POST formConfig/status 启用

口径与 2026-10-24-southernliving-repair/push_fmr.py 同一条：
  - key = 页面 URL（host+path，不带 scheme）
  - 写口要 FORM_SCRIPT_WRITE_TOKEN（读口用 FMR_AGENT_TOKEN）
  - source 逐字节上传；后端读回来会把 LF 换成 CRLF ⇒ 比对前先按行尾归一
  - 写之前先核后端现值（EXPECT_BEFORE）：不一致就停，别覆盖别人的版本
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
import urllib.request

ROOT = pathlib.Path("/company/siteforge")
sys.path.insert(0, str(ROOT))
from agent import fmr  # noqa: E402

KEY = ("www.southernliving.com/featured/premium/"
       "a-new-take-on-sunday-supper-with-venita-aspen-spellegrino")
SOURCE = pathlib.Path("/company/siteforge/forms/sites/southernliving.py")
#: 改之前那份（10-24 16:19 生成、FMR 上现在这份）。按行尾归一后的 sha。
EXPECT_BEFORE = "27b61e816300a2785648d85a6268624a0a2901524a49c117c793ac43ba439fca"

STATUS_API = "https://fmr.3tkj.cn/api/quest/formConfig/status"


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


def enable(key: str, token: str, operator: str) -> dict:
    body = json.dumps({"site": key, "status": 1, "operator": operator},
                      ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        STATUS_API, data=body, method="POST",
        headers={"Content-Type": "application/json; charset=utf-8",
                 "X-Api-Token": token})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--key", default=KEY)
    ap.add_argument("--source", default=str(SOURCE))
    ap.add_argument("--operator", default="claude (用户 2026-10-26：旧商品链接改直跳 Old Navy)")
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
    for allow in (True, False):
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

    # 回读：正常读 404 = 仍是停用；再带 allow_disabled 读一次拿 status
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

    if int(after.get("status") or 0) != 1:
        print("STATUS_STILL_0 —— 走 formConfig/status 启用")
        print("ENABLE", json.dumps(enable(args.key,
                                          env.get("FORM_SCRIPT_WRITE_TOKEN", ""),
                                          args.operator), ensure_ascii=False)[:300])
        after = client.form_script(args.key)      # 这一次必须走正常读
        print("AFTER_ENABLE", json.dumps({k: after.get(k) for k in
                                          ("site", "type", "sha256", "status")},
                                         ensure_ascii=False))
        asrc = after.get("source") or ""
        print("AFTER_ENABLE_NORM equals_local:", norm_sha(asrc) == norm_sha(source))
    return 0 if norm_sha(asrc) == norm_sha(source) else 1


if __name__ == "__main__":
    sys.exit(main())
