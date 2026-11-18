#!/usr/bin/env python3
"""列出这个站**全部** cookie（含 HttpOnly）—— 找 Hustle 的抑制 cookie。"""
import asyncio
import json
import urllib.request

import websockets

HOSTPORT = "192.168.1.222:61129"


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
        out = await send("Network.getCookies", {"urls": [
            "https://www.innovatingcanada.ca/"]})
        cookies = out.get("result", {}).get("cookies", [])
        for c in cookies:
            print(json.dumps({k: c.get(k) for k in ("name", "value", "domain", "path",
                                                    "httpOnly", "secure", "sameSite",
                                                    "expires", "session")},
                             ensure_ascii=False)[:300])


if __name__ == "__main__":
    asyncio.run(main())
