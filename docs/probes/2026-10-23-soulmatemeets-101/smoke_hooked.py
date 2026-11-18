#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""冒烟：装好钩子之后，正常流程不许被拖慢/带偏。

做三件（**不提交**）：
  1. 干净页面上 `_dismiss_consent()` 必须 False（没弹层时不许误报）；
  2. 照 `run()` 的决策走到性别屏；
  3. 点一次 male，hash 必须前进。

跑法：python3 -I smoke_hooked.py
"""
import importlib.util
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import probe_prefgender as P                          # noqa: E402

ART = os.path.join(HERE, "jd_pkg", "sites", "japansdates_hooked.py")
FORM = os.path.join(HERE, "form.json")
URL = "https://soulmatemeets.com/land/sp/23d247da"


def main():
    if not os.path.exists(ART):
        print("没有 %s" % ART)
        return 2
    with open(FORM, "w", encoding="utf-8") as fh:
        json.dump({"first_name": "Alex", "email": "alexprobe@outlook.com"}, fh)
    spec = importlib.util.spec_from_file_location("h", ART)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    ws = P.open_ws()
    P.say("ws=%s" % ws)
    try:
        f = mod.JapanSDatesFiller(ws, FORM, "sm101smoke_%d" % int(time.time()), "")
        f._rpt = lambda step: None
        f.cdp.navigate(URL)
        for _ in range(20):
            time.sleep(1)
            n = str(f.cdp.eval("document.querySelectorAll('[data-testid^=\"step-content-item-\"]').length", ""))
            if n.strip().strip('"') not in ("0", ""):
                break
        t0 = time.time()
        clean = f._dismiss_consent()
        dt_ms = (time.time() - t0) * 1000
        P.say("① 干净页 _dismiss_consent()=%r（耗时 %.0f ms，期望 False）" % (clean, dt_ms))
        if clean:
            P.say("!! 干净页误报有弹层")
            return 1

        if not P.walk_to_gender(f, mod):
            P.say("!! 没走到性别屏")
            return 1
        p = f.read()
        before = mod.hash_state(p.get("url", ""))
        out = f.click_option("pref-gender-male")
        time.sleep(3)
        p2 = f.read()
        after = mod.hash_state(p2.get("url", "")) if p2 else ""
        P.say("② click_option=%r hash %r -> %r step=%r" %
              (out, before, after, p2.get("step") if p2 else None))
        ok = before != after and "ChoosePrefGender" not in str(p2.get("step") if p2 else "")
        P.say("== %s" % ("正常流程没被带偏" if ok else "没前进"))
        return 0 if ok else 1
    finally:
        P.close_window()


if __name__ == "__main__":
    sys.exit(main())
