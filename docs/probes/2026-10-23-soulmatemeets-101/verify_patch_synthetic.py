#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""补丁 A/B：同一颗真浏览器、同一份合成浮层，**旧 vs 新** 两版脚本各问一次。

为什么不用真浮层：这颗 Bit profile 已经点过 Accept all，清光 cookie/storage 之后
站点也不再把浮层挂出来（同意显然记在服务端/更外面），没法再拿真浮层验一次。
于是把**真浮层的形状原样**（class `cookie-popup-variation-consent` +
按钮 `[data-testid="cookie-popup-consent-accept-all"]`，文字 Accept all）注入页面：

  旧版（41c74af2）：`_cookie_gone()` 应当 **认不出来**（True）⇒ `_close_cookie()` 不动手
  新版（PATCHED）：`_cookie_gone()` 应当 **认出来**（False）⇒ `_close_cookie()` 点掉它

合成的接受键带一个只记数、删掉自己的 click handler（不产生任何站点请求）。

跑法（宿主）：python3 -I verify_patch_synthetic.py
退出码 0 = 新旧行为与预期逐条对上；1 = 有一条不对（原因打出来）。
"""
import importlib.util
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import probe_prefgender as P                          # noqa: E402

OLD = os.path.join(HERE, "jd_pkg", "sites", "japansdates_prod.py")
NEW = os.path.join(HERE, "jd_pkg", "sites", "japansdates_patched.py")
FORM = os.path.join(HERE, "form.json")
URL = "https://soulmatemeets.com/land/sp/23d247da"

INJECT_JS = r"""
(function(){
  window.__sfClicked = 0;
  var old = document.querySelector('[data-sf-synth="1"]');
  if (old) old.remove();
  var box = document.createElement('div');
  box.className = 'cookie-popup-variation-consent';
  box.setAttribute('data-testid', 'cookie-popup-variation-consent');
  box.setAttribute('data-sf-synth', '1');
  box.style.cssText = 'position:fixed;left:20px;top:300px;width:320px;height:220px;'
                    + 'z-index:2147483647;background:#fff;border:2px solid #000';
  var btn = document.createElement('button');
  btn.setAttribute('data-testid', 'cookie-popup-consent-accept-all');
  btn.textContent = 'Accept all';
  btn.style.cssText = 'display:block;width:300px;height:48px;margin:20px auto';
  btn.addEventListener('click', function(){
    window.__sfClicked = (window.__sfClicked || 0) + 1;
    box.remove();
  });
  box.appendChild(btn);
  document.body.appendChild(box);
  return 'injected';
})()
"""

STATE_JS = ("(function(){return JSON.stringify({clicked:window.__sfClicked||0,"
            "popup:!!document.querySelector('[data-sf-synth=\"1\"]')});})()")


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def state(f):
    try:
        out = f.cdp.eval(STATE_JS, "")
        if isinstance(out, str):
            out = json.loads(out)
            if isinstance(out, str):              # cdp eval 会再包一层 JSON
                out = json.loads(out)
        return out if isinstance(out, dict) else {"raw": str(out)[:120]}
    except Exception as exc:                      # noqa: BLE001
        return {"err": str(exc)[:120]}


def main():
    with open(FORM, "w", encoding="utf-8") as fh:
        json.dump({"first_name": "Alex", "email": "alexprobe@outlook.com"}, fh)
    old_mod = load(OLD, "jd_old")
    new_mod = load(NEW, "jd_new")

    P.say("探活：%r" % P.alive())
    ws = P.open_ws()
    P.say("ws=%s" % ws)
    try:
        f_old = old_mod.JapanSDatesFiller(ws, FORM, "sm101synth_old", "")
        f_new = new_mod.JapanSDatesFiller(ws, FORM, "sm101synth_new", "")
        for f in (f_old, f_new):
            f._rpt = lambda step: None
        f_old.cdp.navigate(URL)
        time.sleep(4)

        fails = []
        P.say("注入合成浮层：%s" % str(f_old.cdp.eval(INJECT_JS, ""))[:80])
        time.sleep(0.5)
        P.say("注入后现场：%s" % state(f_old))

        gone_old = f_old._cookie_gone()
        P.say("① 旧版 _cookie_gone() = %r（期望 True —— 认不出来）" % gone_old)
        if gone_old is not True:
            fails.append("旧版居然认出来了")
        closed_old = f_old._close_cookie()
        st = state(f_old)
        P.say("② 旧版 _close_cookie() = %r，clicked=%r popup=%r（期望 False/0/true）"
              % (closed_old, st.get("clicked"), st.get("popup")))
        if closed_old is not False or st.get("clicked") or not st.get("popup"):
            fails.append("旧版动了手")

        # 重注入（上一步没动它，但保证干净）
        f_new.cdp.eval(INJECT_JS, "")
        time.sleep(0.5)
        gone_new = f_new._cookie_gone()
        P.say("③ 新版 _cookie_gone() = %r（期望 False —— 认出来了）" % gone_new)
        if gone_new is not False:
            fails.append("新版认不出来")
        closed_new = f_new._close_cookie()
        st = state(f_new)
        P.say("④ 新版 _close_cookie() = %r，clicked=%r popup=%r（期望 True/1/false）"
              % (closed_new, st.get("clicked"), st.get("popup")))
        if closed_new is not True or st.get("clicked") != 1 or st.get("popup"):
            fails.append("新版没点掉")

        # 收尾：把合成元素拿掉
        f_new.cdp.eval("var e=document.querySelector('[data-sf-synth=\"1\"]');"
                       "if(e)e.remove();'clean'", "")
        if fails:
            P.say("== 不合格：%s" % "；".join(fails))
            return 1
        P.say("== A/B 全对上：旧版认不出、不动手；新版认得出、点掉了")
        return 0
    finally:
        P.close_window()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:                             # noqa: BLE001
        import traceback
        traceback.print_exc()
        sys.exit(2)
