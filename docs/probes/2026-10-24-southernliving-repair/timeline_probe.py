#!/usr/bin/env python3
"""冷缓存下导航到落地页，量「页头那个 Subscribe 什么时候才出现在 DOM 里」。

用法：python3 -I timeline_probe.py [seconds]
先 about:blank 再导航（保证新文档），从**新文档提交那一刻**开始计时。
打印：相对提交（毫秒）的 readyState / 选择器是否命中 / 可见链接数。
"""
import asyncio
import json
import sys
import time
import urllib.request

import websockets

HOSTPORT = "192.168.1.222:61129"
TARGET_ID = "28B84F79A8ABBF44414DC26280F99E9D"
URL = open("/company/siteforge/docs/probes/2026-10-24-southernliving-repair/failure_url.txt").read().strip()
SEL = ("#utility-nav_1-0 > ul:nth-of-type(1) > li:nth-of-type(5) > a:nth-of-type(1)")
JS = ("JSON.stringify({href:location.href.slice(0,80),rs:document.readyState,"
      "sel:!!document.querySelector(%s),"
      "nav:!!document.querySelector('#utility-nav_1-0'),"
      "visible:(()=>{let n=0;document.querySelectorAll('a').forEach(a=>{const r=a.getBoundingClientRect();if(r.width>0&&r.height>0)n++;});return n;})(),"
      "title:document.title.slice(0,40)})" % json.dumps(SEL))


def page_ws():
    with urllib.request.urlopen("http://%s/json" % HOSTPORT, timeout=10) as r:
        pages = json.loads(r.read().decode())
    for p in pages:
        if p.get("id") == TARGET_ID:
            return p["webSocketDebuggerUrl"]
    raise SystemExit("target not found")


async def main():
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 20.0
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

        async def snap():
            out = await send("Runtime.evaluate", {"expression": JS, "returnByValue": True})
            val = out.get("result", {}).get("result", {}).get("value")
            try:
                return json.loads(val)
            except Exception:
                return {"raw": str(val)[:120]}

        await send("Network.enable")
        await send("Network.setCacheDisabled", {"cacheDisabled": True})
        await send("Page.enable")
        await send("Page.navigate", {"url": "about:blank"})
        await asyncio.sleep(1.0)
        await send("Page.navigate", {"url": URL})
        t0 = time.monotonic()
        committed = False
        last = None
        while time.monotonic() - t0 < seconds:
            d = await snap()
            if not committed:
                # 新文档提交：readyState 变 loading，或 href 到了目标且文档不是空白页
                if d.get("href", "").startswith("https://www.southernliving.com/") and d.get("rs") in ("loading", "interactive", "complete"):
                    if d.get("rs") in ("loading", "interactive"):
                        committed = True
                        t0 = time.monotonic()
                        print(0, json.dumps(d, ensure_ascii=False), flush=True)
                    else:
                        committed = True
                        t0 = time.monotonic()
                        print(0, json.dumps(d, ensure_ascii=False), flush=True)
                await asyncio.sleep(0.05)
                continue
            t = round((time.monotonic() - t0) * 1000)
            key = (d.get("rs"), d.get("sel"), d.get("nav"))
            if key != last or t < 300:
                print(t, json.dumps(d, ensure_ascii=False), flush=True)
                last = key
            if d.get("sel"):
                break
            await asyncio.sleep(0.1)


if __name__ == "__main__":
    asyncio.run(main())
