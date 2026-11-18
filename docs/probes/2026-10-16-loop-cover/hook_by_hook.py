#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""**只读**：把钩子表拆开，一条一条单独问（看是哪一条哑了），再问几个「为什么」。

产物里 `_popup_probe_js()` 是**拼成一段**的（第一个答应的就 `return`，后面的不再问）——
所以「整段答什么」看不出**哪一条**哑了。这个脚本把每一条钩子的**函数体**单独包起来跑
（与 `_popup_probe_js` 里那段 `(function(all,vis){…})(all,vis)` 逐字同一个包法），
外加 `_CONSENT_BOX_JS`（「这一屏上还看得见同意类容器吗」那条老判据）做交叉参照。

用法:
  python3 -I hook_by_hook.py <产物.py> <ws_url>
"""
from __future__ import annotations

import argparse
import json
import logging
import pathlib
import sys

sys.path.insert(0, "/company/siteforge/forms-222")
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import verify_popup as V                                              # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("artifact")
    ap.add_argument("ws_url")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    mod = V.load(a.artifact)
    form = pathlib.Path("/tmp/task8_form.json")
    form.write_text("{}", encoding="utf-8")
    f = V.make_filler(mod, a.ws_url, form, pathlib.Path("/tmp/task8_hbh.trace.jsonl"))

    print("钩子表（次序 = 优先级）：")
    for row in mod._POPUP_HOOKS:                     # 行可能是两/三/四格（三=只报不点，四=站点锚）
        name, js = row[0], row[1]
        anchor = ""
        if len(row) > 3:
            anchor = row[3]
        elif len(row) > 2 and row[2] != mod._REPORT_ONLY:
            anchor = row[2]
        body = ("var all=[];var roots=[document],ri=0;"
                "while(ri<roots.length){var r=roots[ri++];"
                "var els=(r.getElementsByTagName?r.getElementsByTagName('*'):r.querySelectorAll('*'));"
                "for(var j=0;j<els.length;j++){all.push(els[j]);"
                "if(els[j].shadowRoot){roots.push(els[j].shadowRoot);}}}"
                "var boxLabel=function(e){return String(e.id||'')+' '+String(e.className||'')"
                "+' '+String((e.getAttribute&&e.getAttribute('aria-label'))||'')"
                "+' '+String(e.innerText||'').replace(/\\s+/g,' ').slice(0,200);};"
                "var vis=function(e){var r=e.getBoundingClientRect();"
                "return r.width>0&&r.height>0&&r.bottom>0&&r.top<window.innerHeight;};"
                "return String((function(all,vis,anchor){" + js + "})(all,vis,"
                + json.dumps(anchor) + "));")
        _, ms, ans = V.timed_eval(f, body)
        print("  %-22s %-40r  (%sms)" % (name, str(ans)[:38], ms))

    _, _, box = V.timed_eval(f, "return String((function(){" + mod._CONSENT_BOX_JS + "})());")
    print("\n_CONSENT_BOX_JS（还有没有同意类容器）: %r" % box)

    WHY = r"""
var out={};
var box=document.getElementById('cookiescript_injected');
out.box_rect=box?JSON.stringify(box.getBoundingClientRect().toJSON?box.getBoundingClientRect().toJSON():{}):null;
out.viewport=[window.innerWidth,window.innerHeight];
var acc=document.getElementById('cookiescript_accept');
var rej=document.getElementById('cookiescript_reject');
var f=function(e){if(!e)return null;var r=e.getBoundingClientRect();var s=getComputedStyle(e);
 return {tag:e.tagName,txt:String(e.innerText||e.value||'').trim().slice(0,40),
  aria:(e.getAttribute&&e.getAttribute('aria-label'))||'',
  rect:[Math.round(r.left),Math.round(r.top),Math.round(r.width),Math.round(r.height)],
  display:s.display,visibility:s.visibility,
  vis_rect_only:(r.width>0&&r.height>0&&r.bottom>0&&r.top<window.innerHeight)};};
out.accept=f(acc);out.reject=f(rej);
out.header_txt=(document.getElementById('cookiescript_header')||{}).innerText||'';
out.btnW_match=[/accept|agree|allow|got it|close|reject|decline|deny|同意|接受|关闭/i.test(out.accept?out.accept.txt:''),
                /accept|agree|allow|got it|close|reject|decline|deny|同意|接受|关闭/i.test(out.accept?out.accept.aria:'')];
return JSON.stringify(out);
"""
    _, _, why = V.timed_eval(f, WHY)
    print("\n现场细读：%s" % why)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
