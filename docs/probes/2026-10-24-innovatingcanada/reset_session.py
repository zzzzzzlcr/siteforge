#!/usr/bin/env python3
"""删掉这个站的会话 cookie（horizon_session）+ 弹层抑制 cookie，再重开页面。

用法：python3 -I reset_session.py [--keep-hustle]
"""
import asyncio
import json
import sys
import urllib.request

import websockets

HOSTPORT = "192.168.1.222:61129"
URL = open("/company/siteforge/docs/probes/2026-10-24-innovatingcanada/failure_url.txt").read().strip()


def page_ws():
    with urllib.request.urlopen("http://%s/json" % HOSTPORT, timeout=10) as r:
        pages = [p for p in json.loads(r.read().decode()) if p.get("type") == "page"]
    for p in pages:
        if "innovatingcanada" in (p.get("url") or ""):
            return p["webSocketDebuggerUrl"]
    return pages[0]["webSocketDebuggerUrl"]


async def main():
    keep_hustle = "--keep-hustle" in sys.argv
    async with websockets.connect(page_ws(), max_size=2**24) as ws:
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
        names = [] if keep_hustle else ["hustle-hide-module-6",
                                        "inc_optin_popup_long_hidden-6",
                                        "inc_optin_never_see_again-popup-6"]
        for name in names:
            await send("Network.deleteCookies", {"name": name, "domain": "www.innovatingcanada.ca"})
        await send("Network.deleteCookies", {"name": "horizon_session",
                                             "domain": "www.innovatingcanada.ca"})
        print("deleted", names + ["horizon_session"], flush=True)
        await send("Page.navigate", {"url": "about:blank"})
        await asyncio.sleep(1.0)
        await send("Page.navigate", {"url": URL})
        print("navigated", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
