#!/usr/bin/env python3
"""盯 blavity 文章页，等一个**我们还没适配过的弹窗**冒出来。

用法：python3 -I watch.py [分钟]
每 10 秒看一次：
  · 正文里出现 ad blocker / Ads help us keep / 广告拦截 这类文案；
  · 新出现的可见浮层（fixed/sticky、面积 > 8% 视口、z-index > 100）；
  · 有 data-click 属性的可见元素（blavity 已知那族的稳定标记）。
有变化就往 stdout 打一行 JSON（同一份签名只打一次），没有就安静。
"""
import json
import subprocess
import sys
import time

CDP = "/company/siteforge/tools/cdp/cdp"
HOST, PORT = "192.168.1.222", "61129"

JS = r"""JSON.stringify((function(){
  var vw=innerWidth||1, vh=innerHeight||1;
  function vis(e){var r=e.getBoundingClientRect(),s=getComputedStyle(e);
    return r.width>1&&r.height>1&&s.visibility!=='hidden'&&s.display!=='none'&&Number(s.opacity||1)>0.05;}
  function txt(e){return ((e.innerText||e.textContent||'').replace(/\s+/g,' ').trim());}
  function box(e){var r=e.getBoundingClientRect();
    return {w:Math.round(r.width),h:Math.round(r.height),top:Math.round(r.top),left:Math.round(r.left)};}
  function sel(e){
    if(e.id) return '#'+e.id;
    var c=(e.className&&typeof e.className==='string')?e.className.trim().split(/\s+/).slice(0,3).join('.'):'';
    return e.tagName.toLowerCase()+(c?'.'+c:'');
  }
  var body=(document.body?document.body.innerText:'')||'';
  var phrases=['ad blocker','adblock','ads help us keep','blocker','ads help'];
  var hit=null;
  for(var i=0;i<phrases.length;i++){ if(body.toLowerCase().indexOf(phrases[i])>=0){hit=phrases[i];break;} }
  var overlays=[];
  var nodes=document.querySelectorAll('[class*=modal],[class*=popup],[class*=overlay],[class*=Modal],[class*=Popup],[class*=Overlay],[role=dialog],[aria-modal=true],[data-click]');
  for(var j=0;j<nodes.length;j++){
    var e=nodes[j]; if(!vis(e)) continue;
    var s=getComputedStyle(e), r=e.getBoundingClientRect();
    var fixed=(s.position==='fixed'||s.position==='sticky');
    var big=(r.width*r.height)/(vw*vh)>0.08;
    var wide=(r.width/vw)>0.5;
    var zi=parseInt(s.zIndex||'0',10)||0;
    if(!(fixed&&(big||wide||zi>100))) continue;
    var btns=[];
    var bs=e.querySelectorAll('button,a,[role=button],[data-click]');
    for(var k=0;k<bs.length&&k<8;k++){ if(!vis(bs[k]))continue;
      btns.push({t:txt(bs[k]).slice(0,40),sel:sel(bs[k]),
                 dc:bs[k].getAttribute('data-click'),aria:bs[k].getAttribute('aria-label')}); }
    overlays.push({sel:sel(e),cls:String(e.className||'').slice(0,120),
                   pos:s.position,zi:zi,box:box(e),text:txt(e).slice(0,200),btns:btns});
  }
  // ★ 专抓 adblocker 那一族：class/id/aria 里带 adblock 的，**不看面积/z-index**
  var ab=[];
  var an=document.querySelectorAll('[class*=adblock],[class*=ad-block],[class*=ad_block],[id*=adblock],[id*=ad-block],[aria-label*=ad block],[aria-label*=adblock]');
  for(var q=0;q<an.length&&q<6;q++){
    var e2=an[q]; if(!vis(e2)) continue;
    var s2=getComputedStyle(e2);
    var btns2=[];
    var bs2=e2.querySelectorAll('button,a,[role=button],[data-click],[class*=close],[id*=close]');
    for(var k2=0;k2<bs2.length&&k2<10;k2++){ if(!vis(bs2[k2]))continue;
      btns2.push({t:txt(bs2[k2]).slice(0,40),sel:sel(bs2[k2]),
                  dc:bs2[k2].getAttribute('data-click'),
                  aria:bs2[k2].getAttribute('aria-label'),
                  outer:String(bs2[k2].outerHTML||'').slice(0,300)}); }
    ab.push({sel:sel(e2),cls:String(e2.className||'').slice(0,160),
             id:String(e2.id||''),tag:e2.tagName,
             pos:s2.position,zi:parseInt(s2.zIndex||'0',10)||0,box:box(e2),
             text:txt(e2).slice(0,240),
             outer:String(e2.outerHTML||'').slice(0,1400),
             btns:btns2});
  }
  var dc=[];
  var ds=document.querySelectorAll('[data-click]');
  for(var m=0;m<ds.length&&m<10;m++){ if(!vis(ds[m]))continue;
    dc.push({sel:sel(ds[m]),v:ds[m].getAttribute('data-click'),t:txt(ds[m]).slice(0,30)}); }
  return {url:location.href.slice(0,90),title:document.title.slice(0,60),rs:document.readyState,
          hit:hit, overlays:overlays, adblock_nodes:ab, data_click:dc,
          adblock_text: hit? body.slice(Math.max(0,body.toLowerCase().indexOf(hit)-60), body.toLowerCase().indexOf(hit)+160):''};
})())"""


def snapshot():
    out = subprocess.run([CDP, "--host", HOST, "--port", PORT, "eval", JS],
                         capture_output=True, text=True, timeout=40)
    raw = (out.stdout or "").strip()
    try:
        return json.loads(json.loads(raw))
    except Exception:
        return {"raw": raw[:300]}


def main():
    minutes = float(sys.argv[1]) if len(sys.argv) > 1 else 20.0
    end = time.monotonic() + minutes * 60
    seen = set()
    while time.monotonic() < end:
        try:
            d = snapshot()
        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"at": time.strftime("%H:%M:%S"), "err": str(exc)[:120]}), flush=True)
            time.sleep(10)
            continue
        sig = json.dumps({k: d.get(k) for k in ("hit", "overlays", "adblock_nodes", "data_click")},
                         sort_keys=True, ensure_ascii=False)
        if d.get("hit") or d.get("overlays") or d.get("adblock_nodes"):
            if sig not in seen:
                seen.add(sig)
                print(json.dumps({"at": time.strftime("%H:%M:%S"), **d}, ensure_ascii=False),
                      flush=True)
        time.sleep(10)


if __name__ == "__main__":
    main()
