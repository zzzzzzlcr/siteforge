#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""**只读**观察：产物里那条「广告 creative」钩子在真页面上到底挑中了谁。

起因（Task 8 真站验收第一趟，2026-10-16）：wildnutrition 上把同意弹层点掉之后，
钩子表第 4 行（`广告 creative`）当场答 `广告 creative|TEXT|Close filters drawer`，
产物本体照答就**点了**。那一眼我手上没有那个元素的几何 —— 这个脚本就是回去补那一格：
**只读、不点**，把「谁被挑中、长什么样、在哪个祖先下面」摊出来。

用法:
  python3 -I ad_creative_watch.py <产物.py> <ws_url> --url <页面> [--seconds 150] [--every 15]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import pathlib
import sys
import time

sys.path.insert(0, "/company/siteforge/forms-222")
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import verify_popup as V                                              # noqa: E402

CAND = r"""
//: ⚠️ 这里**照抄**钩子表用的那一条（`agent/template.py` 的 `_VIS_JS`）：只看**矩形**与上下边，
//: **不看** `display` / `visibility` / `opacity` —— 第一版我自己加了一道「真的看得见」的闸，
//: 于是候选永远 0 条，而钩子每一眼都在答。两把尺子量同一件事的下场，记在这儿。
function vis(e){var r=e.getBoundingClientRect();
 return r.width>0&&r.height>0&&r.bottom>0&&r.top<window.innerHeight;}
function deepAll(root,out){root.querySelectorAll('*').forEach(function(e){out.push(e);});
 var a=root.querySelectorAll('*');for(var i=0;i<a.length;i++){if(a[i].shadowRoot)deepAll(a[i].shadowRoot,out);}}
var all=[];deepAll(document,all);
var closeAttr=function(e){var as=e.attributes||[];for(var i=0;i<as.length;i++){
 var n=String(as[i].name||'').toLowerCase();var v=String(as[i].value||'').trim().toLowerCase();
 if(/(^|[-_])close([-_]|$)/.test(n)&&(v===''||v==='true'||v==='1'||v==='close'))return true;}return false;};
var closeVal=function(e){var as=e.attributes||[];for(var i=0;i<as.length;i++){
 var v=String(as[i].value||'').trim().toLowerCase();if(v==='close'||v==='dismiss'||v==='关闭')return true;}return false;};
var closeWord=function(s){s=String(s||'').toLowerCase();
 return s.indexOf('close')>=0||s.indexOf('dismiss')>=0||s.indexOf('关闭')>=0;};
var closeT=function(s){s=String(s||'').trim().toLowerCase();if(!s)return false;
 if(s==='×'||s==='✕'||s==='✖')return true;return /^(close|dismiss|关闭)/.test(s)&&s.length<=24;};
var hits=[];
for(var i=0;i<all.length;i++){var e=all[i];var tg=String(e.tagName||'').toLowerCase();
 if(tg!=='button'&&tg!=='a'&&tg!=='div'&&tg!=='span'&&tg!=='input')continue;
 if(!vis(e))continue;var r=e.getBoundingClientRect();if(r.width<8||r.height<8)continue;
 var x=r.left+r.width/2,y=r.top+r.height/2;
 if(x<0||y<0||x>window.innerWidth||y>window.innerHeight)continue;
 var mark=(e.getAttribute&&e.getAttribute('aria-label'))||'';
 var txt=String(e.innerText||e.value||'').trim();
 var cls=String((e.getAttribute&&e.getAttribute('class'))||e.className||'');
 var small=(r.width<=120&&r.height<=120);
 var why=[];
 if(closeAttr(e))why.push('attrName');
 if(small&&(closeVal(e)||closeWord(e.id)||closeWord(cls)||closeT(mark)||closeT(txt)))why.push('small+valOrWordOrText');
 if(!why.length)continue;
 var st=document.elementsFromPoint(x,y)||[],top=st.length?String(st[0].tagName||'').toLowerCase()+(st[0].id?('#'+st[0].id):''):'';
 var anc=[],p=e.parentElement;for(var k=0;k<4&&p;k++){
   anc.push(String(p.tagName||'').toLowerCase()+(p.id?('#'+p.id):'')+(p.className?('.'+String(p.className).split(' ').slice(0,2).join('.')):''));p=p.parentElement;}
 var cs=getComputedStyle(e);
 var stk=document.elementsFromPoint(x,y)||[],landed=false;
 for(var k=0;k<stk.length;k++){if(stk[k]===e||e.contains(stk[k])||stk[k].contains(e)){landed=true;break;}}
 hits.push({tag:tg,cls:cls.slice(0,70),rect:[Math.round(r.left),Math.round(r.top),Math.round(r.width),Math.round(r.height)],
   pos:cs.position,z:cs.zIndex,display:cs.display,visibility:cs.visibility,opacity:cs.opacity,
   mark:mark.slice(0,40),txt:txt.slice(0,40),why:why,landed:landed,topAtCenter:top,anc:anc});}
return JSON.stringify({url:location.href,n:all.length,n_hits:hits.length,hits:hits.slice(0,5)});
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("artifact")
    ap.add_argument("ws_url")
    ap.add_argument("--url", default="")
    ap.add_argument("--seconds", type=float, default=150.0)
    ap.add_argument("--every", type=float, default=15.0)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    mod = V.load(a.artifact)
    form = pathlib.Path("/tmp/task8_form.json")
    form.write_text("{}", encoding="utf-8")
    f = V.make_filler(mod, a.ws_url, form, pathlib.Path("/tmp/task8_watch.trace.jsonl"))
    if a.url:
        print("navi -> %s" % a.url)
        print((f.cdp.navigate(a.url) or "")[:160])
        time.sleep(8)
    end = time.time() + a.seconds
    n = 0
    while True:
        n += 1
        dt, ms, ans = V.timed_eval(f, mod._POPUP_PROBE_JS)
        _, cms, cand = V.timed_eval(f, CAND)
        print("\n[眼 %d] probe=%.3fs/%sms 答=%r" % (n, dt / 1000.0, ms, str(ans)[:80]))
        print("       候选: %s" % cand)
        if time.time() >= end:
            print("\n[完] %d 眼，%s 秒" % (n, a.seconds))
            return 0
        time.sleep(a.every)


if __name__ == "__main__":
    raise SystemExit(main())
