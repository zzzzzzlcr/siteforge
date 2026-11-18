#!/usr/bin/env python3
"""清缓存 + 导航到这条广告落地页（冷加载），给生产重跑的条件做压力测试。

用法：python3 -I cold_nav.py
和 2026-10-24-southernliving-repair/cold_nav.py 同一套做法，只是 URL 写死在这里。
"""
import asyncio
import json
import urllib.request

HOSTPORT = "192.168.1.222:61129"
URL = ("https://www.southernliving.com/featured/premium/"
       "a-new-take-on-sunday-supper-with-venita-aspen-spellegrino")


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
    async with websockets_connect(page_ws()) as ws:
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


def websockets_connect(url):
    import websockets
    return websockets.connect(url, max_size=2**24)


if __name__ == "__main__":
    asyncio.run(main())
