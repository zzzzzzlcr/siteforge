#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断：同意条那条路上，按文字到底标记到了谁、点下去为什么没反应。

只做一支：注入真形状的同意条（同 test_dismiss_consent 的 A）→ 问探针 → 按文字打标记 →
把**被标记的那一颗**（tag/class/box/outerHTML 前缀/中心点上是谁）原样打出来 → 走 `cdp click`
→ 看 handler 有没有被调用。
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

FIXTURE = """
var box=document.createElement('div');
box.className='cookie-popup-variation-consent';
box.setAttribute('data-testid','cookie-popup-variation-consent');
box.setAttribute('data-sf-test','1');
box.style.cssText='position:fixed;z-index:2147483647;background:#fff;border:2px solid #000;left:10px;bottom:10px;width:320px;height:180px';
var b=document.createElement('button');
b.setAttribute('data-testid','cookie-popup-consent-accept-all');
b.textContent='Accept all';
b.style.cssText='display:block;width:280px;height:48px;margin:20px auto';
b.addEventListener('click',function(){window.__sf=(window.__sf||{});window.__sf.A=(window.__sf.A||0)+1;box.remove();});
box.appendChild(b);document.body.appendChild(box);
window.__sf=window.__sf||{};'ok'
"""

MARKED_JS = r"""
(function(){
  var e=document.querySelector('[data-sf-consent="1"]');
  if(!e)return 'no-mark';
  var r=e.getBoundingClientRect();
  var x=r.left+r.width/2,y=r.top+r.height/2;
  var hit=document.elementFromPoint(x,y);
  var chain=[],n=hit;
  while(n&&chain.length<5){chain.push(String(n.tagName||'').toLowerCase()
    +(n.getAttribute&&n.getAttribute('data-testid')?('['+n.getAttribute('data-testid')+']'):''));n=n.parentElement;}
  return JSON.stringify({tag:String(e.tagName||'').toLowerCase(),
    cls:String(e.className||'').slice(0,60),text:String(e.innerText||'').trim().slice(0,40),
    box:{x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)},
    vw:window.innerWidth,vh:window.innerHeight,
    atPoint:chain,
    isSelf:hit===e||e.contains(hit)});
})()
"""

STATE_JS = "(function(){return JSON.stringify({sf:window.__sf||{},fixture:!!document.querySelector('[data-sf-test=\"1\"]')});})()"


def unq(raw):
    raw = str(raw or "").strip()
    for _ in range(2):
        if raw.startswith('"') and raw.endswith('"'):
            try:
                raw = str(json.loads(raw))
            except ValueError:
                break
        else:
            break
    return raw


def main():
    with open(FORM, "w", encoding="utf-8") as fh:
        json.dump({"first_name": "Alex", "email": "alexprobe@outlook.com"}, fh)
    spec = importlib.util.spec_from_file_location("h", ART)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    ws = P.open_ws()
    P.say("ws=%s" % ws)
    try:
        f = mod.JapanSDatesFiller(ws, FORM, "sm101mark_%d" % int(time.time()), "")
        f._rpt = lambda step: None
        f.cdp.navigate(URL)
        time.sleep(4)
        f.cdp.eval("var es=document.querySelectorAll('[data-sf-test=\"1\"]');"
                   "for(var i=0;i<es.length;i++)es[i].remove();window.__sf={};'clean'", "")
        f.cdp.eval(FIXTURE, "")
        time.sleep(0.4)
        P.say("state0=%s" % unq(f.cdp.eval(STATE_JS, "")))

        raw = f._ev_text(mod._CONSENT_PROBE_JS)
        P.say("probe=%r" % raw)
        parts = raw.split("|", 2)
        if len(parts) == 3 and parts[1] == "TEXT":
            m = f._ev_text(mod._MARK_CONSENT_BY_TEXT_JS % json.dumps(parts[2]))
            P.say("mark=%r" % m)
            P.say("marked=%s" % unq(f.cdp.eval(MARKED_JS, "")))
            try:
                out = f.cdp.click('[data-sf-consent="1"]')
                P.say("click rc=%r" % str(out)[:160])
            except Exception as exc:               # noqa: BLE001
                P.say("click 抛：%s: %s" % (type(exc).__name__, exc))
            time.sleep(0.8)
            P.say("state1=%s" % unq(f.cdp.eval(STATE_JS, "")))
            f.cdp.eval("var a=document.getElementsByTagName('*');"
                       "for(var i=0;i<a.length;i++){if(a[i].removeAttribute)"
                       "a[i].removeAttribute('data-sf-consent');}return 'clean';", "")
        else:
            P.say("探针没答同意条（或不是 TEXT）：%r" % raw[:120])
        return 0
    finally:
        try:
            f.cdp.eval("var es=document.querySelectorAll('[data-sf-test=\"1\"]');"
                       "for(var i=0;i<es.length;i++)es[i].remove();'clean'", "")
        except Exception:                          # noqa: BLE001
            pass
        P.close_window()


if __name__ == "__main__":
    sys.exit(main())
