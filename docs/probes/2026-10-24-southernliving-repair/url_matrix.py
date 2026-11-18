#!/usr/bin/env python3
"""逐条 URL 在「全新 Bit 窗口」里的第一跳状态码 —— 找出 Cloudflare 403 是谁带的。

用法：python3 -I url_matrix.py
每条都：关窗 → 开全新窗 → navi → 等 → 读 title/status → 关。
"""
import json
import pathlib
import re
import sys
import time
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import win  # noqa: E402

CDP = "/company/siteforge/tools/cdp/cdp"
HOST = "192.168.1.222"
PORT = "61129"


def cdp(*args, timeout=60):
    import subprocess
    return subprocess.run([CDP, "--host", HOST, "--port", PORT, *args],
                          capture_output=True, text=True, timeout=timeout)


def read_state():
    js = ('JSON.stringify({title:document.title,'
          'status:(performance.getEntriesByType("navigation")[0]||{}).responseStatus,'
          'txt:(document.body&&document.body.innerText||"").slice(0,80)})')
    out = cdp("eval", js).stdout.strip()
    try:
        return json.loads(json.loads(out))
    except Exception:
        return {"raw": out[:200]}


def open_fresh():
    win.close_win()
    time.sleep(2)
    ok, ws = win.open_win()
    if not ok or not ws:
        raise RuntimeError("open failed: %r" % (ws,))
    time.sleep(7)
    return ws


def case(name, url):
    ws = open_fresh()
    r = cdp("navi", url)
    time.sleep(8)
    st = read_state()
    print(json.dumps({"case": name, "url": url[:120], "nav": r.stdout[:80].strip(),
                      **st}, ensure_ascii=False), flush=True)
    return ws


if __name__ == "__main__":
    full = (HERE / "failure_url.txt").read_text().strip()
    path = full.split("?")[0]
    q = full.split("?", 1)[1].split("#")[0]
    parts = dict(p.split("=", 1) for p in q.split("&"))
    base = "https://www.southernliving.com"
    cases = [
        ("clean", path),
        ("utm_only", path + "?utm_campaign=50424026&utm_source=taboolapaid&utm_medium=paid"),
        ("tblci", path + "?tblci=" + parts.get("tblci", "")),
        ("response_session", path + "?response.session=" + parts.get("response.session", "")),
        ("full", full),
        ("adt_clean", base + "/featured/original-plus/security-for-your-style-adt"),
        ("home", base + "/"),
    ]
    which = sys.argv[1:] or [c[0] for c in cases]
    try:
        for name, url in cases:
            if name in which:
                case(name, url)
    finally:
        win.close_win()
