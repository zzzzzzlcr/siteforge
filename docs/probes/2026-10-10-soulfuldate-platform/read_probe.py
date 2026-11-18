#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跑**产物本体** japansdates.py 的 read()（真 CDPHelper、真窗口），只读不点。

用法: python3 -I read_probe.py <ws_url>
"""
import importlib.util
import json
import logging
import os
import sys

ART = "/opt/skills/auto-farm-skill/forms/sites/japansdates.py"
sys.path.insert(0, "/opt/skills/auto-farm-skill/forms")
os.environ.setdefault("SITEFORGE_CDP_BIN", "/opt/skills/auto-farm-skill/cdp")

ws_url = sys.argv[1]
spec = importlib.util.spec_from_file_location("jd", ART)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger("probe")

CLS = [v for v in vars(mod).values() if isinstance(v, type) and hasattr(v, "read")][0]
print("== 类:", CLS.__name__)


class Fake:
    pass


f = Fake()
f.cid = "sd-probe"
f.log = log
f.missing = set()
f.probed = False
f.commands = None
f.cdp = mod.CDPHelper(ws_url)
f._filled = set()
f._last_q = None
f._repeat = 0
for name in ("_run", "_probe", "_note_missing", "_command_absent", "_check_command",
             "_cdp", "_action_cdp", "_ev", "read", "fill_visible", "click_option",
             "click_visible", "_check_terms", "click_visible_skip", "click_text"):
    fn = CLS.__dict__.get(name)
    if fn is not None:
        setattr(f, name, fn.__get__(f, Fake))
    else:
        print("   (产物里没有 %s)" % name)

page = f.read()
print("== read() 返回 ==")
print(json.dumps(page, ensure_ascii=False, indent=1)[:4500])
