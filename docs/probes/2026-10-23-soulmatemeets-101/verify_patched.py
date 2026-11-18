#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证补丁：`japansdates_PATCHED.py` 的 `_close_cookie()` 能不能自己把这个变体点掉。

背景：上一趟（11:10 左右）我们在那颗 Bit profile 里点过 Accept all，这个域现在
默认不弹了 —— 所以先**把站点自己的身份 cookie 删掉再重载**，看浮层回不回来；
回来了才谈得上验证。

验证只走到「性别屏点一下男」为止，**不填邮箱/密码、不提交**：
  · `_close_cookie()` 返回 True，且浮层 `cookie-popup-variation-consent` 不在了；
  · 再点 `pref-gender-male`，hash 从 step3 前进到 step4。

跑法（宿主）：python3 -I verify_patched.py
退出码 0 = 补丁在真站上按预期工作；1 = 没验成（原因打在输出里）。
"""
import importlib.util
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import probe_prefgender as P                          # noqa: E402

ART = os.path.join(HERE, "jd_pkg", "sites", "japansdates_patched.py")
FORM = os.path.join(HERE, "form.json")
URL = "https://soulmatemeets.com/land/sp/23d247da"


def load_mod():
    spec = importlib.util.spec_from_file_location("jd_patched", ART)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def clear_via_cdp(ws_url):
    """用 CDP 清这个源的 **全部** 存储（HttpOnly cookie / localStorage / IndexedDB）。

    `document.cookie` 删不掉 HttpOnly —— 上一趟就是卡在这儿（删完 uuid 浮层也不回来）。
    """
    import re
    import urllib.request
    import websocket                                # websocket-client（宿主上有）
    base = re.sub(r"^ws://([^/]+)/.*$", r"http://\1", ws_url)
    with urllib.request.urlopen(base + "/json/list", timeout=10) as resp:
        tabs = json.load(resp)
    page = next((t for t in tabs if t.get("type") == "page"), None)
    if not page:
        raise RuntimeError("没有 page target：%r" % [(t.get("type"), t.get("url")) for t in tabs])
    ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=15)
    try:
        seq = [0]

        def call(method, params=None):
            seq[0] += 1
            mid = seq[0]
            ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
            while True:
                msg = json.loads(ws.recv())
                if msg.get("id") == mid:
                    return msg

        out = {}
        out["cookies"] = call("Network.clearBrowserCookies")
        out["origin"] = call("Storage.clearDataForOrigin",
                             {"origin": "https://soulmatemeets.com",
                              "storageTypes": "all"})
        return out
    finally:
        ws.close()

POPUP_JS = r"""
(function(){
  var e=document.querySelector('[data-testid="cookie-popup-variation-consent"]');
  if(!e) return JSON.stringify({popup:false});
  var r=e.getBoundingClientRect(), cs=getComputedStyle(e);
  return JSON.stringify({popup:true, w:Math.round(r.width), h:Math.round(r.height),
                         display:cs.display, visibility:cs.visibility,
                         cls:String(e.className).slice(0,80)});
})()
"""


def main():
    with open(FORM, "w", encoding="utf-8") as fh:
        json.dump({"first_name": "Alex", "email": "alexprobe@outlook.com"}, fh)
    mod = load_mod()
    P.say("探活：%r" % P.alive())
    ws = P.open_ws()
    P.say("ws=%s" % ws)
    try:
        f = mod.JapanSDatesFiller(ws, FORM, "sm101verify_%d" % int(time.time()), "")
        f._rpt = lambda step: None

        f.cdp.navigate(URL)
        time.sleep(4)
        P.say("① 先看浮层在不在：%s" % str(f.cdp.eval(POPUP_JS, ""))[:300])
        P.say("② CDP 清源存储：%s" % json.dumps(clear_via_cdp(ws), ensure_ascii=False)[:400])
        f.cdp.navigate(URL)
        time.sleep(4)
        st = str(f.cdp.eval(POPUP_JS, ""))
        P.say("③ 重载后浮层：%s" % st[:300])
        if '"popup":true' not in st:
            P.say("!! 浮层没回来 —— 这个 profile 记的同意不在可删的 cookie 里，"
                  "这一支验不了「补丁点掉它」那一步")
            return 1

        # ── 补丁的那一步：_cookie_gone() 现在应当认得出它 ──────────────
        P.say("④ 补丁的 _cookie_gone() = %r（期望 False）" % f._cookie_gone())
        clicked = f._close_cookie()
        time.sleep(1.0)
        st2 = str(f.cdp.eval(POPUP_JS, ""))
        P.say("⑤ _close_cookie() = %r；之后浮层：%s" % (clicked, st2[:300]))
        if '"popup":true' in st2:
            P.say("!! 补丁没把它点掉")
            return 1

        # ── 走到性别屏并点一次：确认页面真能前进 ────────────────────
        ok = P.walk_to_gender(f, mod)
        if not ok:
            P.say("!! 没走到性别屏")
            return 1
        p = f.read()
        before_hash = mod.hash_state(p.get("url", ""))
        out = f.click_option("pref-gender-male")
        time.sleep(3)
        p2 = f.read()
        after_hash = mod.hash_state(p2.get("url", "")) if p2 else ""
        P.say("⑥ click_option=%r hash %r -> %r, step=%r" %
              (out, before_hash, after_hash,
               p2.get("step") if p2 else None))
        ok = before_hash != after_hash and "ChoosePrefGender" not in str(
            p2.get("step") if p2 else "")
        P.say("== 结论：%s" % ("补丁在真站上按预期工作" if ok else "还没验成"))
        return 0 if ok else 1
    finally:
        P.close_window()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:                                  # noqa: BLE001
        import traceback
        traceback.print_exc()
        sys.exit(2)
