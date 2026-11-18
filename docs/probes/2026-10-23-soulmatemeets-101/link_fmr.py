#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 soulmatemeets.com/land/sp/23d247da 重新挂回 config 101，并回读确认。

前提：脚本补丁已经写进 config 101（sha 35444610…，见 push_fmr.py 的输出）。
只做一件事：`form_config_link(site, 101)`，然后逐项回读：
  · `formConfig/sites` 里它是不是 config_id=101；
  · `formScript?site=…23d247da` 是不是能读到（读的是 config 101 那一份脚本）；
  · 读回来的 sha256 是不是 `35444610…`。
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sys

ROOT = pathlib.Path("/company/siteforge")
sys.path.insert(0, str(ROOT))
from agent import fmr  # noqa: E402

KEY = "soulmatemeets.com/land/sp/23d247da"
CONFIG_ID = 101
EXPECT = "35444610d22671b2d5516a4e4430b32b7d732a0aa3401113046fc3d372f674b4"


def read_env() -> dict:
    env = {}
    for line in (ROOT / "siteforge.env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k] = v
    return env


def main() -> int:
    env = read_env()
    c = fmr.FmrClient(token=env.get("FMR_AGENT_TOKEN", ""),
                      write_token=env.get("FORM_SCRIPT_WRITE_TOKEN", ""))

    before = [r for r in c.form_config_sites() if str(r.get("site")) == KEY]
    print("BEFORE:", json.dumps(before, ensure_ascii=False))

    out = c.form_config_link(
        KEY, CONFIG_ID,
        operator="claude (用户 2026-10-23 11:28 选方案1：修好后挂回 101)",
        reason="cookie 浮层变体补丁 sha 35444610 已写入 config 101，重新挂回")
    print("LINK:", json.dumps(out, ensure_ascii=False))

    after = [r for r in c.form_config_sites() if str(r.get("site")) == KEY]
    print("AFTER sites:", json.dumps(after, ensure_ascii=False))
    if not after or str(after[0].get("config_id")) != str(CONFIG_ID):
        print("!! 挂载回读不是 101")
        return 1

    d = c.form_script(KEY)
    src = d.get("source") or ""
    got = hashlib.sha256(src.encode("utf-8")).hexdigest()
    print("SCRIPT:", json.dumps({k: d.get(k) for k in
                                 ("site", "requested_site", "type", "version", "sha256")},
                                ensure_ascii=False))
    print("READ sha256:", got[:16], "bytes", len(src.encode("utf-8")),
          "| sentinels:", src.count("cookie-popup-consent-accept-all"),
          src.count(".cookie-popup-variation-consent"))
    if got != EXPECT:
        print("!! 读回来的不是补丁那一份")
        return 1
    print("== 已挂回 101，且读到的就是补丁脚本（35444610…）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
