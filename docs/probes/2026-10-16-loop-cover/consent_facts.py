#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""**只读**：这一屏上「同意层还在不在」+「广告 creative 钩子挑中的是谁」。

一眼一屏，只读不点。两个问题分开答（2026-10-16 真站验收第二趟用得着）：

* **问一**：CookieScript 那族容器在不在 DOM 里、几何多少、盖住了谁
  （Task 1 的 `cover_probe.js` 量过同一件事，这里只做交叉核对）
* **问二**：钩子表第 4 行此刻答什么，以及它挑中的那些候选长什么样
  （几何 / `display` / `visibility` / 祖先 / 中心点落点是不是它自己）

用法:
  python3 -I consent_facts.py <产物.py> <ws_url> [--eyes 3] [--every 10]
"""
from __future__ import annotations

import argparse
import logging
import pathlib
import sys
import time

sys.path.insert(0, "/company/siteforge/forms-222")
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import verify_popup as V                                              # noqa: E402
from ad_creative_watch import CAND                                    # noqa: E402

CONSENT = r"""
var out={url:location.href};
var sel=['#cookiescript_injected','#cookiescript_injected_wrapper','#cookiescript_header',
         '[id*=ookiescript]','[class*=CookieScript]','[id*=onetrust]','[id*=consent]'];
out.hits=[];
for(var i=0;i<sel.length;i++){var els=document.querySelectorAll(sel[i]);
 for(var j=0;j<els.length;j++){var e=els[j];var r=e.getBoundingClientRect();var s=getComputedStyle(e);
  out.hits.push({sel:sel[i],id:e.id,cls:String(e.className||'').slice(0,50),
   rect:[Math.round(r.left),Math.round(r.top),Math.round(r.width),Math.round(r.height)],
   pos:s.position,z:s.zIndex,display:s.display,visibility:s.visibility});}}
var cover=[];
['button','a'].forEach(function(t){document.querySelectorAll(t).forEach(function(e){
 var tx=String(e.innerText||'').trim();if(tx!=='Get Started'&&tx!=='Privacy Policy')return;
 var r=e.getBoundingClientRect();if(r.width<8)return;
 var x=r.left+r.width/2,y=r.top+r.height/2;
 var st=document.elementsFromPoint(x,y)||[];
 cover.push({txt:tx,rect:[Math.round(r.left),Math.round(r.top),Math.round(r.width),Math.round(r.height)],
  stack:st.slice(0,4).map(function(h){return String(h.tagName||'').toLowerCase()+(h.id?('#'+h.id):'')+(h.className?('.'+String(h.className).split(' ')[0]):'');}),
  self_at:st.indexOf(e)});});});
out.cover=cover;
return JSON.stringify(out);
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("artifact")
    ap.add_argument("ws_url")
    ap.add_argument("--eyes", type=int, default=3)
    ap.add_argument("--every", type=float, default=10.0)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    mod = V.load(a.artifact)
    form = pathlib.Path("/tmp/task8_form.json")
    form.write_text("{}", encoding="utf-8")
    f = V.make_filler(mod, a.ws_url, form, pathlib.Path("/tmp/task8_facts.trace.jsonl"))
    for i in range(a.eyes):
        _, _, ans = V.timed_eval(f, mod._POPUP_PROBE_JS)
        _, _, cons = V.timed_eval(f, CONSENT)
        _, _, cand = V.timed_eval(f, CAND)
        print("\n===== 眼 %d =====" % (i + 1))
        print("钩子表答：%r" % (str(ans)[:90],))
        print("同意层  ：%s" % cons)
        print("候选    ：%s" % str(cand)[:1200])
        if i + 1 < a.eyes:
            time.sleep(a.every)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
