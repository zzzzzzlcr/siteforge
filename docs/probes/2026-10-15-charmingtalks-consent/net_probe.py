#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在 .222 真窗口上抓「点下去之后站点干了什么」：
  装 fetch/XHR/error 钩子 → 点一次选项 → 倒出网络日志 + 加载失败的资源。

用法: python3 -I net_probe.py <ws_url>
"""
import importlib.util
import json
import os
import sys
import time

ART = "/opt/skills/auto-farm-skill/forms/sites/japansdates.py"
sys.path.insert(0, "/opt/skills/auto-farm-skill/forms")
os.environ.setdefault("SITEFORGE_CDP_BIN", "/opt/skills/auto-farm-skill/cdp")

ws = sys.argv[1]
spec = importlib.util.spec_from_file_location("jd", ART)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
from common import CDPHelper  # noqa: E402

cdp = CDPHelper(ws)
CLS = m.JapanSDatesFiller


def unwrap(raw):
    v = raw
    for _ in range(3):
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except Exception:
                break
        else:
            break
    return v


HOOK = """
(function(){
  if(window.__netlog) return 'already';
  window.__netlog=[];
  var of=window.fetch;
  window.fetch=function(){var a=arguments;var u=(a[0]&&a[0].url)||a[0];
    return of.apply(this,a).then(function(r){window.__netlog.push(['fetch',String(u),r.status]);return r;},
      function(e){window.__netlog.push(['fetch-ERR',String(u),String(e)]);throw e;});};
  var oo=XMLHttpRequest.prototype.open, os=XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open=function(mm,uu){this.__u=uu;this.__m=mm;return oo.apply(this,arguments);};
  XMLHttpRequest.prototype.send=function(){var x=this;
    try{x.addEventListener('loadend',function(){window.__netlog.push(['xhr',x.__m+' '+String(x.__u),x.status]);});}catch(e){}
    return os.apply(this,arguments);};
  window.addEventListener('error',function(e){
    window.__netlog.push(['jserror',String(e.message),String(e.filename)+':'+e.lineno]);},true);
  return 'hooked';
})()
"""

print("装钩子:", cdp.eval(HOOK, ""))

# 找到可见的 gender-female 并打标记
mk = "npx%d" % (int(time.time()) % 100000)
kind = cdp.eval("(function(){%s})()" % (CLS._FIND_JS % ("gender-female", mk)), "")
sel = '[data-%s="1"]' % mk
print("标记 kind=%r" % (kind,))


def page_state(tag):
    p = unwrap(cdp.eval(m.READ_PAGE_JS, "")) or {}
    print("%-16s hash=%-8s options=%s" % (tag, m.hash_state(p.get("url", "")) or "?",
                                          p.get("options")))
    return p


page_state("点击前")
try:
    cdp.click(sel)
    print("cdp click 发了")
except Exception as e:
    print("cdp click 异常:", type(e).__name__, str(e)[:140])

time.sleep(6)
page_state("点击后+6s")
time.sleep(6)
page_state("点击后+12s")

print("\n-- 网络日志（最近 25 条） --")
print(json.dumps(unwrap(cdp.eval("(function(){return JSON.stringify((window.__netlog||[]).slice(-25));})()", "")),
                 ensure_ascii=False, indent=1)[:1800])

print("\n-- 加载失败的资源 --")
print(json.dumps(unwrap(cdp.eval(
    "(function(){var r=performance.getEntriesByType('resource').filter(function(e){"
    "return (e.responseStatus&&e.responseStatus>=400)||e.transferSize===0;});"
    "return JSON.stringify(r.slice(0,25).map(function(e){"
    "return [String(e.name).slice(0,120),e.responseStatus||0,e.transferSize||0,e.duration?Math.round(e.duration):0];}));})()",
    "")), ensure_ascii=False, indent=1)[:1600])

cdp.eval("(function(){var e=document.querySelector('%s');if(e)e.removeAttribute('data-%s');})()"
         % (sel, mk), "")
