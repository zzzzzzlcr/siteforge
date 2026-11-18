#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用**产物本体** japansdates.py（真构造、真 CDPHelper、真窗口）走几步，验证它能不能驱动
soulmatemeets.com/land/sp/23d247da。

按 japansdates.run() 的答题顺序走：testid 选项 → labels 文本兜底 → 可见 continue。
走 N 步就停 —— **从不调用 run()**，走到第 N 步为止，不提交任何东西。

用法: python3 -I drive_probe.py <ws_url> [steps]
"""
import importlib.util
import json
import logging
import os
import sys

ART = "/opt/skills/auto-farm-skill/forms/sites/japansdates.py"
FORM = "/tmp/sm_probe_miniform.json"
sys.path.insert(0, "/opt/skills/auto-farm-skill/forms")
os.environ.setdefault("SITEFORGE_CDP_BIN", "/opt/skills/auto-farm-skill/cdp")

ws_url = sys.argv[1]
STEPS = int(sys.argv[2]) if len(sys.argv) > 2 else 4

with open(FORM, "w") as fh:
    json.dump({"first_name": "Alex", "email": "alexprobe4417@outlook.com"}, fh)

spec = importlib.util.spec_from_file_location("jd", ART)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

CLS = [v for v in vars(mod).values() if isinstance(v, type) and hasattr(v, "read")][0]
f = CLS(ws_url, FORM, "sm_drive_probe")


def show(tag, p):
    if p is None:
        print("  %-8s <读不出来>" % tag)
        return False
    print("  %-8s hash=%-8s step=%-64s\n           head=%s\n           options=%s labels=%s next=%s"
          % (tag, mod.hash_state(p.get("url", "")) or "?", str(p.get("step"))[:62],
             (p.get("head") or "")[:60], p.get("options"), p.get("labels"), p.get("nextVisible")))
    return True


try:
    f._close_cookie()
    print("  cookie 点过了")
except Exception as exc:  # noqa: BLE001
    print("  cookie 这一步报错：%s" % exc)

for i in range(STEPS):
    p = f.read()
    if not show("前%d" % (i + 1), p):
        break
    if mod.is_success(p.get("url", "")):
        print("  >>> 已到成功态，停")
        break
    pick = mod.pick_option(p.get("options", []) or [])
    if pick:
        print("  -> click_option(%s)" % pick)
        f.click_option(pick)
        continue
    text = mod.pick_option_by_text(p.get("labels", []) or [])
    if text and f.click_text(text):
        print("  -> click_text(%r)" % text)
        continue
    if p.get("nextVisible"):
        print("  -> click_visible(continue-button)")
        f.click_visible(mod.CONTINUE_BTN)
        continue
    print("  -> 没得点，停")
    break

show("最终", f.read())
print("== 结束（未提交任何东西）==")
