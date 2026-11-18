#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""方案 2 的验收（真浏览器、合成 DOM）：`japansdates` 的通用同意钩子。

先写用例、后写实现（红 → 绿）。夹具都是**注入到真页面**的固定浮层：
  A 这次的真形状（`cookie-popup-variation-consent` + accept-all testid）
  B 换个名字的同意条（`privacy-prefs-layer` + "Agree & close"）
  C 满视口同意弹层（role=dialog、100vw×100vh、Reject all / Allow all）
  D 反例：普通业务弹窗（"Special offer" + "Accept offer"）—— **不许**动
  E 反例：只有 Close、没有同意语境的弹窗 —— **不许**动
  0 空页面 —— 不许误报

跑法（宿主，8100 那颗 Bit）：python3 -I test_dismiss_consent.py
退出码 0 = 全过；1 = 有用例没过（逐条打出来）。
"""
import importlib.util
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import probe_prefgender as P                          # noqa: E402

ART = os.path.join(HERE, "jd_pkg", "sites", "japansdates_hooked.py")
FORM = os.path.join(HERE, "form.json")
URL = "https://soulmatemeets.com/land/sp/23d247da"

COMMON = ("position:fixed;z-index:2147483647;background:#fff;border:2px solid #000;"
          "font-size:16px;")

FIXTURES = {
    "A_real_variant": """
      var box=document.createElement('div');
      box.className='cookie-popup-variation-consent';
      box.setAttribute('data-testid','cookie-popup-variation-consent');
      box.setAttribute('data-sf-test','1');
      box.style.cssText='%(common)s left:10px;bottom:10px;width:320px;height:180px';
      var b=document.createElement('button');
      b.setAttribute('data-testid','cookie-popup-consent-accept-all');
      b.textContent='Accept all';
      b.style.cssText='display:block;width:280px;height:48px;margin:20px auto';
      b.addEventListener('click',function(){window.__sf.A=(window.__sf.A||0)+1;box.remove();});
      box.appendChild(b);document.body.appendChild(box);
    """ % {"common": COMMON},
    "B_renamed_bar": """
      var box=document.createElement('div');
      box.className='privacy-prefs-layer';
      box.setAttribute('data-sf-test','1');
      box.style.cssText='%(common)s left:10px;bottom:10px;width:320px;height:180px';
      var b=document.createElement('button');
      b.textContent='Agree & close';
      b.style.cssText='display:block;width:280px;height:48px;margin:20px auto';
      b.addEventListener('click',function(){window.__sf.B=(window.__sf.B||0)+1;box.remove();});
      box.appendChild(b);document.body.appendChild(box);
    """ % {"common": COMMON},
    "C_full_consent_modal": """
      var box=document.createElement('div');
      box.setAttribute('role','dialog');
      box.className='consent-modal-root';
      box.setAttribute('data-sf-test','1');
      box.style.cssText='%(common)s left:0;top:0;width:100vw;height:100vh';
      box.innerHTML='<p>We use cookies to improve your experience. Manage your privacy settings.</p>';
      var r=document.createElement('button');
      r.textContent='Reject all';
      r.style.cssText='display:block;width:280px;height:48px;margin:20px auto';
      r.addEventListener('click',function(){window.__sf.Cr=(window.__sf.Cr||0)+1;box.remove();});
      var a=document.createElement('button');
      a.textContent='Allow all';
      a.style.cssText='display:block;width:280px;height:48px;margin:8px auto';
      a.addEventListener('click',function(){window.__sf.Ca=(window.__sf.Ca||0)+1;});
      box.appendChild(r);box.appendChild(a);document.body.appendChild(box);
    """ % {"common": COMMON},
    "D_business_dialog": """
      var box=document.createElement('div');
      box.setAttribute('role','dialog');
      box.className='business-dialog';
      box.setAttribute('data-sf-test','1');
      box.style.cssText='%(common)s left:0;top:0;width:100vw;height:100vh';
      box.innerHTML='<p>Special offer just for you</p>';
      var b=document.createElement('button');
      b.textContent='Accept offer';
      b.style.cssText='display:block;width:280px;height:48px;margin:20px auto';
      b.addEventListener('click',function(){window.__sf.D=(window.__sf.D||0)+1;box.remove();});
      box.appendChild(b);document.body.appendChild(box);
    """ % {"common": COMMON},
    "E_close_only_dialog": """
      var box=document.createElement('div');
      box.className='site-dialog';
      box.setAttribute('data-sf-test','1');
      box.style.cssText='%(common)s left:40px;top:120px;width:300px;height:160px';
      var b=document.createElement('button');
      b.textContent='Close';
      b.style.cssText='display:block;width:200px;height:44px;margin:20px auto';
      b.addEventListener('click',function(){window.__sf.E=(window.__sf.E||0)+1;box.remove();});
      box.appendChild(b);document.body.appendChild(box);
    """ % {"common": COMMON},
    "F_static_footer": """
      var box=document.createElement('div');
      box.className='site-footer';
      box.setAttribute('data-sf-test','1');
      box.style.cssText='position:static;width:360px;height:120px;background:#eee';
      box.innerHTML='<p>Privacy Policy · Cookie Policy · Terms of Use</p>';
      var a=document.createElement('a');
      a.textContent='Arbitration Agreement';
      a.href='#';
      a.style.cssText='display:block;width:220px;height:24px';
      a.addEventListener('click',function(ev){ev.preventDefault();window.__sf.F=(window.__sf.F||0)+1;});
      box.appendChild(a);document.body.appendChild(box);
    """ % {"common": COMMON},
}

EXPECT = {
    # 名字: (要 dismiss 成吗, 夹具应当消失吗, 允许哪些按钮被点)
    "0_empty": (False, None, []),
    "A_real_variant": (True, True, ["A"]),
    "B_renamed_bar": (True, True, ["B"]),
    "C_full_consent_modal": (True, True, ["Cr"]),
    "D_business_dialog": (False, False, []),
    "E_close_only_dialog": (False, False, []),
    "F_static_footer": (False, False, []),
}

STATE_JS = ("(function(){var e=document.querySelector('[data-sf-test=\"1\"]');"
            "return JSON.stringify({fixture:!!e, sf:window.__sf||{}});})()")


def load_mod():
    spec = importlib.util.spec_from_file_location("jd_hooked", ART)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def state(f):
    try:
        out = f.cdp.eval(STATE_JS, "")
        if isinstance(out, str):
            out = json.loads(out)
            if isinstance(out, str):
                out = json.loads(out)
        return out if isinstance(out, dict) else {"raw": str(out)[:120]}
    except Exception as exc:                      # noqa: BLE001
        return {"err": str(exc)[:160]}


def cleanup(f):
    try:
        f.cdp.eval("var es=document.querySelectorAll('[data-sf-test=\"1\"]');"
                   "for(var i=0;i<es.length;i++)es[i].remove();"
                   "window.__sf={};'clean'", "")
    except Exception:                             # noqa: BLE001
        pass


def main():
    if not os.path.exists(ART):
        print("RED: 还没有 %s（先红后绿）" % ART)
        return 1
    with open(FORM, "w", encoding="utf-8") as fh:
        json.dump({"first_name": "Alex", "email": "alexprobe@outlook.com"}, fh)
    mod = load_mod()
    if not hasattr(mod.JapanSDatesFiller, "_dismiss_consent"):
        print("RED: JapanSDatesFiller 还没有 _dismiss_consent")
        return 1

    P.say("探活：%r" % P.alive())
    ws = P.open_ws()
    P.say("ws=%s" % ws)
    fails = []
    try:
        f = mod.JapanSDatesFiller(ws, FORM, "sm101consent_test_%d" % int(time.time()), "")
        f._rpt = lambda step: None
        f.cdp.navigate(URL)
        time.sleep(4)
        cleanup(f)

        cases = [("0_empty", None)] + [(k, v) for k, v in FIXTURES.items()]
        for name, js in cases:
            cleanup(f)
            if js:
                f.cdp.eval(js + "window.__sf=window.__sf||{};'ok'", "")
                time.sleep(0.4)
            got = f._dismiss_consent()
            st = state(f)
            want_dismiss, want_gone, allowed = EXPECT[name]
            clicks = sorted(st.get("sf", {}).keys())
            ok = (bool(got) == want_dismiss
                  and all(c in allowed for c in clicks)
                  and (want_gone is None or st.get("fixture") is (not want_gone)))
            P.say("%-22s dismiss=%-5r fixture=%-5r clicks=%-10s  %s"
                  % (name, got, st.get("fixture"), clicks, "PASS" if ok else "**FAIL**"))
            if not ok:
                fails.append("%s（期望 dismiss=%r/gone=%r/只用 %s）"
                             % (name, want_dismiss, want_gone, allowed))
            cleanup(f)
        if fails:
            P.say("== 没过：%s" % "；".join(fails))
            return 1
        P.say("== 全过（0/A/B/C 认得出并点掉；D/E 没动；空页没误报）")
        return 0
    finally:
        try:
            cleanup(f)
        except Exception:                         # noqa: BLE001
            pass
        P.close_window()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:                             # noqa: BLE001
        import traceback
        traceback.print_exc()
        sys.exit(2)
