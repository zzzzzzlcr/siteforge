#!/usr/bin/env python3
"""把落地页按「桌面 Chrome」的 UA/视口重载，再看页头的 Subscribe 在哪。

用法：python3 -I desktop_probe.py [width] [height] [mobile 0/1]
默认 1366x768 桌面。跑完把 UA/视口还原并重载。
"""
import asyncio
import json
import sys
import urllib.request

import websockets

HOSTPORT = "192.168.1.222:61129"
TARGET_ID = sys.argv[4] if len(sys.argv) > 4 else "28B84F79A8ABBF44414DC26280F99E9D"
DESKTOP_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36")

NAV_JS = (open("/company/siteforge/docs/probes/2026-10-24-southernliving-repair/nav.js",
               encoding="utf-8").read())


def page_ws():
    with urllib.request.urlopen("http://%s/json" % HOSTPORT, timeout=10) as r:
        pages = json.loads(r.read().decode())
    for p in pages:
        if p.get("id") == TARGET_ID:
            return p["webSocketDebuggerUrl"]
    raise SystemExit("target %s not found; have %s" % (TARGET_ID, [p.get("id") for p in pages]))


async def main():
    w = int(sys.argv[1]) if len(sys.argv) > 1 else 1366
    h = int(sys.argv[2]) if len(sys.argv) > 2 else 768
    mobile = bool(int(sys.argv[3])) if len(sys.argv) > 3 else False
    url = page_ws().replace("ws://0.0.0.0", "ws://192.168.1.222")
    async with websockets.connect(url, max_size=2**24) as ws:
        n = 0

        async def send(method, params=None):
            nonlocal n
            n += 1
            await ws.send(json.dumps({"id": n, "method": method, "params": params or {}}))
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("id") == n:
                    return msg

        await send("Network.enable")
        await send("Network.setUserAgentOverride", {"userAgent": DESKTOP_UA,
                                                    "acceptLanguage": "en-US,en;q=0.9",
                                                    "platform": "Win32"})
        await send("Emulation.setDeviceMetricsOverride",
                   {"width": w, "height": h, "deviceScaleFactor": 1, "mobile": mobile})
        await send("Page.enable")
        await send("Page.reload", {"ignoreCache": False})
        await asyncio.sleep(9)
        out = await send("Runtime.evaluate", {"expression": NAV_JS, "returnByValue": True})
        val = out.get("result", {}).get("result", {}).get("value")
        data = json.loads(val) if isinstance(val, str) else val
        meta = await send("Runtime.evaluate", {
            "expression": "JSON.stringify({ua:navigator.userAgent,w:innerWidth,h:innerHeight,"
                          "title:document.title,"
                          "status:(performance.getEntriesByType('navigation')[0]||{}).responseStatus,"
                          "sel:(()=>{const e=document.querySelector('#utility-nav_1-0 > ul:nth-of-type(1) > li:nth-of-type(5) > a:nth-of-type(1)');"
                          "if(!e)return null;const r=e.getBoundingClientRect();"
                          "return {text:(e.innerText||'').trim(),rect:[r.x|0,r.y|0,r.width|0,r.height|0]};})()})",
            "returnByValue": True})
        meta_val = json.loads(meta["result"]["result"]["value"])
        print(json.dumps({"meta": meta_val, "nav": data}, ensure_ascii=False, indent=1)[:6000])
        # restore
        await send("Network.setUserAgentOverride", {"userAgent": ""})
        await send("Emulation.clearDeviceMetricsOverride")
        await send("Page.reload", {"ignoreCache": False})


if __name__ == "__main__":
    asyncio.run(main())
