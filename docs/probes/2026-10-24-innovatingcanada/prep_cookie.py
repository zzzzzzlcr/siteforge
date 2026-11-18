#!/usr/bin/env python3
"""模拟生产那一类 profile：**新会话 + 只有客户端抑制 cookie**（365 天 opt-in 那种）。

用法：python3 -I prep_cookie.py
流程：删 horizon_session / hustle* / inc_optin* → CDP 设 `hustle-hide-module-6` →
about:blank → 打开页面（此时 cookie 在，弹层不出；会话干净）→ 留在那一页。
"""
import asyncio
import json
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
        for name in ("horizon_session", "hustle-hide-module-6",
                     "inc_optin_popup_long_hidden-6", "inc_optin_never_see_again-popup-6"):
            await send("Network.deleteCookies", {"name": name,
                                                 "domain": "www.innovatingcanada.ca"})
        r = await send("Network.setCookie", {"name": "hustle-hide-module-6", "value": "1",
                                             "domain": "www.innovatingcanada.ca",
                                             "path": "/"})
        print("set cookie", json.dumps(r.get("result", {}))[:120], flush=True)
        await send("Page.navigate", {"url": "about:blank"})
        await asyncio.sleep(1.0)
        await send("Page.navigate", {"url": URL})
        print("navigated (cookie present, clean session)", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
