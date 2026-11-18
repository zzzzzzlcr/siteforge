#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 .222 窗口上 charmingtalks 的同意状态清掉（cc_cookie），刷新，确认弹层回来了。

用法: python3 -I reset_consent.py <ws_url>
"""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, "/opt/skills/auto-farm-skill/forms")
os.environ.setdefault("SITEFORGE_CDP_BIN", "/opt/skills/auto-farm-skill/cdp")
from common import CDPHelper  # noqa: E402

WS = sys.argv[1]
URL = "https://charmingtalks.com/land/sp/79229f81/"
CDP = "/opt/skills/auto-farm-skill/cdp"

cdp = CDPHelper(WS)


def unwrap(raw):
    v = raw
    for _ in range(3):
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except Exception:
                break
        else:
            break
    return v


print("清前 cookies:", unwrap(cdp.eval("(function(){return document.cookie.slice(0,200);})()", "")))
print("清:", unwrap(cdp.eval(
    "(function(){['cc_cookie','cc_analytical'].forEach(function(n){"
    "document.cookie=n+'=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/';"
    "document.cookie=n+'=; expires=Thu, 01 Jan 1970 00:00:00 GMT; path=/; domain=.charmingtalks.com';});"
    "return 'done';})()", "")))
print("清后 cookies:", unwrap(cdp.eval("(function(){return document.cookie.slice(0,200);})()", "")))

r = subprocess.run([CDP, "navi", URL, "--host", "192.168.1.222", "--port", "61129"],
                   capture_output=True, text=True, timeout=60)
print("navi rc:", r.returncode)
time.sleep(14)

print("弹层:", json.dumps(unwrap(cdp.eval(
    "(function(){var d=document.querySelector('.cookie-popup-consent');if(!d)return 'no container';"
    "var r=d.getBoundingClientRect();var b=d.querySelector('button');"
    "return JSON.stringify({vis:getComputedStyle(d).display,wh:[Math.round(r.width),Math.round(r.height)],"
    "btn:b?(b.innerText||'').trim():null});})()", "")), ensure_ascii=False))
print("hash:", unwrap(cdp.eval("(function(){return location.hash;})()", "")))
