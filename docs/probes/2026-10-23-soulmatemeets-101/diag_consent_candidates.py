#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断：真页面上「同意条」钩子会误认谁？（只读，不改页面）

A/B 里量到两件真事：
  · 页脚的 "Arbitration Agreement" 会被当成同意条的按钮（btnW 里的 `agree` 是子串匹配），
    因为页脚容器的 innerText 里带着 "Privacy Policy / Cookie Policy" ⇒ boxW 也命中；
  · `_MARK_CONSENT_BY_TEXT_JS` 会标记**第一颗**文字相同的元素 —— 页面上有一颗隐藏的
    "Accept all" 时，标记落在它上面，真实按钮反而没被点（模板里那句已知的 caveat）。

这一支把「真页面上哪些容器会被 boxW 命中、它们的 position 是什么、里面哪颗按钮会被选」
原样列出来；顺带查 CSS 里 `.cookie-popup-variation-consent` 的 position（浮层原形已不在
DOM 里，但样式表还在）。
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import probe_prefgender as P                          # noqa: E402

URL = "https://soulmatemeets.com/land/sp/23d247da"

CAND_JS = r"""
(function(){
  function vis(e){var r=e.getBoundingClientRect();
    if(!(r.width>0&&r.height>0&&r.bottom>0&&r.top<window.innerHeight))return false;
    var cs=null;try{cs=getComputedStyle(e);}catch(err){}
    if(cs){var d=String(cs.display||''),v=String(cs.visibility||'');
      if(d==='none'||v==='hidden'||v==='collapse')return false;}
    return true;}
  var boxW=/(cookie|consent|gdpr|privacy)/i;
  var btnW=/(accept|agree|allow|got it|close|reject|decline|deny|同意|接受|关闭)/i;
  var out=[];
  var all=document.getElementsByTagName('*');
  for(var i=0;i<all.length;i++){
    var b=all[i];
    var lab=String(b.id||'')+' '+String(b.className||'')+' '
           +String((b.getAttribute&&b.getAttribute('aria-label'))||'')+' '
           +String(b.innerText||'').replace(/\s+/g,' ').slice(0,200);
    if(!boxW.test(lab)||!vis(b))continue;
    var r=b.getBoundingClientRect(),cs2=getComputedStyle(b);
    var hit=null;
    var inner=b.getElementsByTagName('*');
    for(var j=0;j<inner.length;j++){
      var c=inner[j],tag=String(c.tagName||'').toLowerCase();
      if(tag!=='button'&&tag!=='a'&&tag!=='input'&&tag!=='div'&&tag!=='span')continue;
      if((tag==='div'||tag==='span')&&c.children&&c.children.length){
        var tc=false;for(var q=0;q<c.children.length;q++){
          if(String(c.children[q].innerText||c.children[q].value||'').trim()){tc=true;break;}}
        if(tc)continue;}
      if(!vis(c))continue;
      var t=String(c.innerText||c.value||(c.getAttribute&&c.getAttribute('aria-label'))||'').trim();
      if(tag!=='button'&&tag!=='a'&&tag!=='input'&&t.length>40)continue;
      if(!btnW.test(t))continue;
      hit={tag:tag,text:t.slice(0,50),tid:c.getAttribute&&c.getAttribute('data-testid')||''};
      break;
    }
    out.push({tag:String(b.tagName||'').toLowerCase(),
              id:b.id||'',cls:String(b.className||'').slice(0,70),
              pos:cs2.position, z:cs2.zIndex,
              box:{x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)},
              firstBtn:hit});
    if(out.length>=12)break;
  }
  return JSON.stringify({n:out.length,cands:out});
})()
"""

TEXTS_JS = r"""
(function(){
  var wants=['Accept all','Arbitration Agreement','Agree & close'];
  var out=[];
  var all=document.getElementsByTagName('*');
  for(var i=0;i<all.length;i++){
    var e=all[i];
    var t=String(e.innerText||e.value||'').trim();
    if(wants.indexOf(t)<0)continue;
    var r=e.getBoundingClientRect(),cs=null;
    try{cs=getComputedStyle(e);}catch(err){}
    out.push({tag:String(e.tagName||'').toLowerCase(),text:t,tid:(e.getAttribute&&e.getAttribute('data-testid'))||'',
              cls:String(e.className||'').slice(0,50),
              disp:cs?cs.display:'',vis:cs?cs.visibility:'',
              box:{x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)}});
  }
  return JSON.stringify({n:out.length,items:out});
})()
"""

CSS_JS = r"""
(function(){
  var hits=[];
  try{
    for(var i=0;i<document.styleSheets.length;i++){
      var sh=document.styleSheets[i], rules=null;
      try{rules=sh.cssRules;}catch(e){continue;}
      if(!rules)continue;
      for(var j=0;j<rules.length;j++){
        var r=rules[j];
        if(r.selectorText && r.selectorText.indexOf('cookie-popup-variation-consent')>=0){
          hits.push({sel:r.selectorText.slice(0,160),css:r.style.cssText.slice(0,300)});
          if(hits.length>=6)break;
        }
      }
    }
  }catch(e){return JSON.stringify({err:String(e).slice(0,120)});}
  return JSON.stringify({n:hits.length,hits:hits});
})()
"""


def main():
    ws = P.open_ws()
    P.say("ws=%s" % ws)
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("cm", os.path.join(HERE, "jd_pkg", "common.py"))
        cm = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cm)
        h = cm.CDPHelper(ws)
        h.navigate(URL)
        # 等页面真出内容（前面几趟量到落地页要 5 秒左右才挂出 step 容器）
        for _ in range(20):
            time.sleep(1)
            n = str(h.eval("document.querySelectorAll('[data-testid^=\"step-content-item-\"]').length", ""))
            if n.strip().strip('"') not in ("0", ""):
                break
        P.say("url=%s steps=%s" % (str(h.eval("location.href", ""))[:90],
                                   str(h.eval("document.querySelectorAll('[data-testid^=\"step-content-item-\"]').length", ""))))
        for tag, js in (("CANDIDATES", CAND_JS), ("TEXTS", TEXTS_JS), ("CSS", CSS_JS)):
            raw = h.eval(js, "")
            try:
                d = json.loads(raw)
                if isinstance(d, str):
                    d = json.loads(d)
            except Exception:                # noqa: BLE001
                d = {"raw": str(raw)[:400]}
            if tag == "CANDIDATES":
                P.say("== %s n=%s" % (tag, d.get("n")))
                for c in d.get("cands", []):
                    P.say("   %-8s %-40s pos=%-8s z=%-8s %s btn=%s"
                          % (c["tag"], (c["id"] + "." + c["cls"])[:40], c["pos"], c["z"],
                             c["box"], c["firstBtn"]))
            elif tag == "TEXTS":
                P.say("== %s n=%s" % (tag, d.get("n")))
                for c in d.get("items", []):
                    P.say("   %-8s %-24r tid=%-38r %-10s %-10s %s"
                          % (c["tag"], c["text"], c["tid"], c["disp"], c["vis"], c["box"]))
            else:
                P.say("== %s n=%s err=%s" % (tag, d.get("n"), d.get("err")))
                for r0 in d.get("hits", []):
                    P.say("   %s\n      %s" % (r0["sel"], r0["css"]))
        return 0
    finally:
        P.close_window()


if __name__ == "__main__":
    sys.exit(main())
