#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chattyromance.com/land/sp/<id>/ 平台普查（headless，不开真窗口）。

用途：判定它是不是我们已经能直接用的那套（japansdates / goldenagesouls）。
手法复用 docs/probes/2026-10-10-soulfuldate-platform/census.js。

用法: python3 -I probe.py <url> <out_prefix>
"""
import json
import sys

from playwright.sync_api import sync_playwright

URL = sys.argv[1]
PREFIX = sys.argv[2]

CENSUS = r"""
(function(){
  var out={url:location.href,title:document.title,ready:document.readyState,
    innerText:(document.body?document.body.innerText:'').slice(0,900),
    testids:[],buttons:[],inputs:[],iframes:document.querySelectorAll('iframe').length,
    scripts:[], forms:document.querySelectorAll('form').length,
    cookieish:[]};
  var ts=document.querySelectorAll('[data-testid]');
  for(var i=0;i<ts.length;i++){out.testids.push(ts[i].getAttribute('data-testid'));}
  var bs=document.querySelectorAll('button,a[role=button],input[type=submit],div[role=button],a');
  for(var j=0;j<bs.length && out.buttons.length<40;j++){
    var b=bs[j],r=b.getBoundingClientRect(),cs;
    try{cs=getComputedStyle(b);}catch(e){continue;}
    if(r.width<4||r.height<4||cs.display==='none'||cs.visibility==='hidden')continue;
    out.buttons.push({tag:b.tagName.toLowerCase(),t:(b.innerText||b.value||'').trim().slice(0,50),
      id:b.id||'',cls:String(b.className||'').slice(0,60),tid:b.getAttribute('data-testid')||'',
      x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)});
  }
  var is=document.querySelectorAll('input');
  for(var k=0;k<is.length && out.inputs.length<30;k++){
    var e=is[k],r2=e.getBoundingClientRect();
    out.inputs.push({type:e.type,name:e.name||'',tid:e.getAttribute('data-testid')||'',
      ph:e.placeholder||'',w:Math.round(r2.width),h:Math.round(r2.height)});
  }
  var ss=document.querySelectorAll('script[src]');
  for(var m=0;m<ss.length && out.scripts.length<25;m++){out.scripts.push(ss[m].src.slice(0,160));}
  // 同意/弹层类：固定定位 + 高 z-index，抓得到就报
  var all=document.querySelectorAll('div,section,aside');
  for(var n=0;n<all.length && out.cookieish.length<10;n++){
    var d=all[n],rr=d.getBoundingClientRect(),c2;
    try{c2=getComputedStyle(d);}catch(e){continue;}
    if((c2.position==='fixed'||c2.position==='sticky') && parseInt(c2.zIndex||'0',10)>100
       && rr.width>60 && rr.height>60){
      out.cookieish.push({cls:String(d.className||'').slice(0,70),z:c2.zIndex,
        w:Math.round(rr.width),h:Math.round(rr.height),
        t:(d.innerText||'').trim().slice(0,90)});
    }
  }
  return JSON.stringify(out);
})()
"""


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path="/usr/bin/google-chrome",
            headless=True,
            args=[
                "--no-sandbox", "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
        )
        ctx = browser.new_context(
            viewport={"width": 375, "height": 812},
            user_agent=("Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/150.0.0.0 Mobile Safari/537.36"),
            locale="en-US", timezone_id="America/New_York",
            is_mobile=True, has_touch=True, device_scale_factor=3,
        )
        page = ctx.new_page()
        resp = None
        try:
            resp = page.goto(URL, wait_until="domcontentloaded", timeout=45000)
        except Exception as e:
            print("goto 异常:", type(e).__name__, str(e)[:200])
        print("http_status =", resp.status if resp else None)
        page.wait_for_timeout(9000)
        # 挑战页可能会自己跳一次
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        page.wait_for_timeout(2000)
        try:
            data = page.evaluate(CENSUS)
            out = json.loads(data)
        except Exception as e:
            print("census 失败:", type(e).__name__, str(e)[:200])
            out = {"error": str(e)[:300], "url": page.url, "title": page.title()}
        page.screenshot(path=PREFIX + ".png", full_page=False)
        with open(PREFIX + ".json", "w") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=1)
        print("final_url  =", out.get("url"))
        print("title      =", out.get("title"))
        print("ready      =", out.get("ready"), "iframes =", out.get("iframes"),
              "forms =", out.get("forms"))
        tids = out.get("testids") or []
        print("testids    =", len(tids))
        print("testid 列表 =", json.dumps(sorted(set(tids)), ensure_ascii=False))
        print("inputs     =", json.dumps(out.get("inputs"), ensure_ascii=False)[:600])
        print("buttons    =", json.dumps(out.get("buttons"), ensure_ascii=False)[:900])
        print("scripts    =", json.dumps(out.get("scripts"), ensure_ascii=False)[:700])
        print("innerText  =", (out.get("innerText") or "")[:400].replace("\n", " | "))
        print("screenshot =", PREFIX + ".png")
        browser.close()


main()
