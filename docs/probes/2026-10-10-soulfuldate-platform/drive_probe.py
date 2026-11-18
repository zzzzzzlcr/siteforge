#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用**产物本体** japansdates.py（真构造、真 CDPHelper、真窗口）走几步，验证它能不能驱动这个站。
走 N 步就停 —— **不碰提交**（从不调用 run()）。

用法: python3 -I drive_probe.py <ws_url> [steps]
"""
import importlib.util
import json
import logging
import os
import sys

ART = "/opt/skills/auto-farm-skill/forms/sites/japansdates.py"
FORM = "/tmp/sd_probe/miniform.json"
sys.path.insert(0, "/opt/skills/auto-farm-skill/forms")
os.environ.setdefault("SITEFORGE_CDP_BIN", "/opt/skills/auto-farm-skill/cdp")

ws_url = sys.argv[1]
STEPS = int(sys.argv[2]) if len(sys.argv) > 2 else 3

with open(FORM, "w") as fh:
    json.dump({"first_name": "Alex", "email": "alexprobe4417@outlook.com"}, fh)

spec = importlib.util.spec_from_file_location("jd", ART)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

CLS = [v for v in vars(mod).values() if isinstance(v, type) and hasattr(v, "read")][0]
f = CLS(ws_url, FORM, "sd_drive_probe")          # ← 真对象


def show(tag, p):
    if p is None:
        print("  %-8s <读不出来>" % tag)
        return
    print("  %-8s hash=%-8s step=%-46s\n           head=%s\n           options=%s next=%s legal=%s inputs=%s"
          % (tag, mod.hash_state(p.get("url", "")) or "?", str(p.get("step"))[:44],
             (p.get("head") or "")[:58], p.get("options"), p.get("nextVisible"),
             p.get("legalChecked"), p.get("inputs")))


f._close_cookie()
print("  cookie 点过了")

for i in range(STEPS):
    p = f.read()
    show("前%d" % (i + 1), p)
    if p is None:
        break
    if mod.is_success(p.get("url", "")):
        print("  >>> 已到成功态，停")
        break
    pick = mod.pick_option(p.get("options", []) or [])
    if pick:
        print("  -> click_option(%s)" % pick)
        f.click_option(pick)
    elif p.get("nextVisible"):
        print("  -> click_visible(continue-button)")
        f.click_visible(mod.CONTINUE_BTN)
    else:
        print("  -> 没得点，停")
        break

show("最终", f.read())
print("== 结束（未提交任何东西）==")
