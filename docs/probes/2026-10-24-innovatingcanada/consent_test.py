#!/usr/bin/env python3
"""第一访问（清 cookie）下：点 Agree 还是 Disagree，Hustle 弹层还出不出来。

用法：python3 -I consent_test.py agree|disagree|none [seconds]
流程：清浏览器 cookie → 导航失败 URL → 等同意条按钮出现 → 按选择点 →
      之后每 2 秒看 `#hustle-field-first_name-module-6` 是否**可见**，最多 seconds。
"""
import asyncio
import json
import subprocess
import sys
import time
import urllib.request
import pathlib

import websockets

HERE = pathlib.Path(__file__).resolve().parent
URL = (HERE / "failure_url.txt").read_text().strip()
CDP = "/company/siteforge/tools/cdp/cdp"
HOST, PORT = "192.168.1.222", "61129"
HOSTPORT = "%s:%s" % (HOST, PORT)

STATE_JS = r"""JSON.stringify((()=>{
  const vis=e=>{if(!e)return false;const r=e.getBoundingClientRect();return r.width>0&&r.height>0;};
  const q=s=>document.querySelector(s);
  const f=q('#hustle-field-first_name-module-6');
  const a=q('#didomi-notice-agree-button'), d=q('#didomi-notice-disagree-button');
  return {t:(document.title||'').slice(0,40), agree:vis(a), disagree:vis(d),
          hustle_vis:vis(f), hustle_exists:!!f,
          modal:vis(q('.hustle-modal')), len:((document.body&&document.body.innerText)||'').length};
})())"""


def page_ws():
    with urllib.request.urlopen("http://%s/json" % HOSTPORT, timeout=10) as r:
        pages = [p for p in json.loads(r.read().decode()) if p.get("type") == "page"]
    for p in pages:
        if "innovatingcanada" in (p.get("url") or ""):
            return p["webSocketDebuggerUrl"]
    if pages:
        return pages[0]["webSocketDebuggerUrl"]
    raise SystemExit("no page")


async def clear_cookies():
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
        await send("Network.clearBrowserCookies")
        await send("Network.setCacheDisabled", {"cacheDisabled": True})


def cdp(*args, timeout=60):
    return subprocess.run([CDP, "--host", HOST, "--port", PORT, *args],
                          capture_output=True, text=True, timeout=timeout)


def state():
    out = cdp("eval", STATE_JS).stdout.strip()
    try:
        return json.loads(json.loads(out))
    except Exception:
        return {"raw": out[:160]}


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "none"
    seconds = float(sys.argv[2]) if len(sys.argv) > 2 else 90.0
    asyncio.run(clear_cookies())
    print("cookies cleared", flush=True)
    cdp("navi", "about:blank", timeout=60)
    time.sleep(1.0)
    cdp("navi", URL, timeout=90)
    t0 = time.monotonic()
    clicked = False
    last = None
    while time.monotonic() - t0 < seconds:
        d = state()
        key = json.dumps(d, sort_keys=True, ensure_ascii=False)
        if key != last:
            print(round(time.monotonic() - t0, 1), key, flush=True)
            last = key
        if action == "scroll" and not clicked:
            cdp("eval", "'(function(){window.scrollTo(0,document.body.scrollHeight);return 1;})()'")
            print("SCROLLED_BOTTOM", flush=True)
            clicked = True
        if action in ("agree", "disagree") and not clicked:
            want = d.get("agree") if action == "agree" else d.get("disagree")
            if want:
                sel = "#didomi-notice-agree-button" if action == "agree" else "#didomi-notice-disagree-button"
                r = cdp("click", sel)
                print("CLICK", sel, r.returncode, flush=True)
                clicked = True
        if clicked and d.get("hustle_vis"):
            print("HUSTLE_VISIBLE_AT", round(time.monotonic() - t0, 1), flush=True)
            break
        time.sleep(2)


if __name__ == "__main__":
    main()
