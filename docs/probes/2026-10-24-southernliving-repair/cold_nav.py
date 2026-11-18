#!/usr/bin/env python3
"""清缓存 + 发起导航，**立刻返回**（不等 load）—— 复现「脚本比页面先到」。

用法：python3 -I cold_nav.py
"""
import asyncio
import json
import urllib.request

import websockets

HOSTPORT = "192.168.1.222:61129"
URL = open("/company/siteforge/docs/probes/2026-10-24-southernliving-repair/failure_url.txt").read().strip()


def page_ws():
    with urllib.request.urlopen("http://%s/json" % HOSTPORT, timeout=10) as r:
        pages = [p for p in json.loads(r.read().decode()) if p.get("type") == "page"]
    for p in pages:
        if "southernliving.com" in (p.get("url") or ""):
            return p["webSocketDebuggerUrl"]
    if pages:
        return pages[0]["webSocketDebuggerUrl"]
    raise SystemExit("no page target")


async def main():
    async with websockets.connect(page_ws(), max_size=2**24) as ws:
        n = 0

        async def send(method, params=None):
            nonlocal n
            n += 1
            mid = n
            await ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("id") == mid:
                    return msg

        await send("Network.enable")
        await send("Network.clearBrowserCache")
        await send("Network.setCacheDisabled", {"cacheDisabled": True})
        await send("Page.navigate", {"url": "about:blank"})
        await asyncio.sleep(0.5)
        await send("Page.navigate", {"url": URL})
        print("navigated", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
