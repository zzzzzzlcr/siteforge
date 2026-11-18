#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""手工点掉 .222 窗口上的隐私弹层（Accept all），然后看点选项能不能推进。

用法: python3 -I consent_probe.py <ws_url>
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


def st(tag):
    p = unwrap(cdp.eval(m.READ_PAGE_JS, "")) or {}
    print("%-22s hash=%-8s options=%s" % (tag, m.hash_state(p.get("url", "")) or "?", p.get("options")))
    return p


def ev(js):
    return cdp.eval("(function(){%s})()" % js, "")


# 1) 弹层是什么、有哪些按钮
print("弹层信息:", json.dumps(unwrap(ev(
    "var d=document.querySelector('#onetrust-consent-sdk,.ot-sdk-container,[id*=consent],[class*=consent],[class*=privacy]');"
    "var bs=[].slice.call(document.querySelectorAll('button,a[role=button],div[role=button]'));"
    "var vis=bs.filter(function(b){var r=b.getBoundingClientRect();return r.width>40&&r.height>20;});"
    "var txt=vis.map(function(b){return [(b.innerText||b.value||'').trim().slice(0,40),b.id||'',String(b.className||'').slice(0,40)];});"
    "return JSON.stringify({wrap:d?(d.tagName+'#'+d.id+'.'+d.className).slice(0,90):null,buttons:txt.slice(0,12)});")),
    ensure_ascii=False)[:900])

# 2) 标记并点「Accept all」
kind = ev("var bs=[].slice.call(document.querySelectorAll('button,a,div[role=button]'));"
          "for(var i=0;i<bs.length;i++){var b=bs[i],r=b.getBoundingClientRect();"
          "var tx=(b.innerText||b.value||'').trim();"
          "if(r.width>40&&r.height>20&&/^accept all$/i.test(tx)){b.setAttribute('data-acc','1');return 'found';}}"
          "return 'none';")
print("Accept all 标记:", kind)
if "found" in (kind or ""):
    try:
        cdp.click('[data-acc="1"]')
        print("点了 Accept all")
    except Exception as e:
        print("点 Accept all 异常:", type(e).__name__, str(e)[:120])
    ev("var e=document.querySelector('[data-acc=\"1\"]');if(e)e.removeAttribute('data-acc');")
else:
    print("!! 没找到 Accept all")

time.sleep(4)
st("点掉弹层后")

# 3) 再点一次性别选项，看能不能推进
mk = "cp%d" % (int(time.time()) % 100000)
k2 = ev(CLS._FIND_JS % ("gender-female", mk))
sel = '[data-mk%s="1"]' % ""  # 占位，下面用真实 mk
sel = '[data-%s="1"]' % mk
print("性别标记 kind=%r" % (k2,))
if "none" not in (k2 or ""):
    try:
        cdp.click(sel)
        print("点了 gender-female")
    except Exception as e:
        print("点 gender-female 异常:", type(e).__name__, str(e)[:120])
    ev("var e=document.querySelector('%s');if(e)e.removeAttribute('data-%s');" % (sel, mk))
time.sleep(5)
st("点选项后+5s")
time.sleep(5)
p = st("点选项后+10s")
print(">>> 最终 hash:", m.hash_state(p.get("url", "")))
