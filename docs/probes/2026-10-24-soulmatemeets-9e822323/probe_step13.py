"""9e822323 卡在 step13 的取证：这一屏是单选、多选、还是要按 Next？

只读式：走到 step13 停住 → dump → 点一个选项 → 再看选中态/hash → 若 Next 在，点一次 Next 看是否前进。
不填表、不提交（step13 离末尾还有距离）。

跑法（容器 siteforge-222 内）：
  PYTHONPATH=/opt/siteforge/forms:/opt/siteforge \
  SITEFORGE_CDP_BIN=/opt/siteforge/tools/cdp/cdp \
  BIT_WORKER_IP=192.168.1.222 BIT_ID=982d158b41b14fbb87754667859b4719 \
  BIT_API_PORT=54345 python3 /tmp/probe_step13.py
"""
from __future__ import annotations

import json
import os
import random
import sys
import time

from agent import selftest, service
from agent.classify import wait_for_window
from soulmatemeets_imagebutton import (Filler, LANGUAGES, SKIP_PREFIXES,
                                       hash_state)

URL = "https://soulmatemeets.com/land/sp/9e822323/"
STOP = "step13"

DUMP_JS = r"""
(function(){
  function vis(e){var n=e;while(n&&n.nodeType===1){var cs=getComputedStyle(n);
    if(cs.visibility==='hidden'||cs.display==='none')return false;n=n.parentElement;}
    return true;}
  function tid(e){return (e.getAttribute&&e.getAttribute('data-testid'))||'';}
  function cls(e){return String(e.className||'').slice(0,140);}
  function info(e){var r=e.getBoundingClientRect();var inp=e.querySelector?e.querySelector('input'):null;
    return {tid:tid(e),tag:String(e.tagName||'').toLowerCase(),cls:cls(e),
      ariaChecked:e.getAttribute('aria-checked'),ariaPressed:e.getAttribute('aria-pressed'),
      inputChecked:inp?!!inp.checked:null,disabled:!!e.disabled,
      x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)};}
  var opts=[],es=document.querySelectorAll('[data-testid]');
  for(var i=0;i<es.length;i++){var e=es[i],t=tid(e);var r=e.getBoundingClientRect();
    if(r.width<=0||r.height<=0||!vis(e))continue;
    if(/^(age-|gender-|pref-gender-|image-button-|option-|interest-|goal-)/.test(t)
       &&t.indexOf('-text')<0)opts.push(info(e));}
  var cb=document.querySelector('[data-testid="continue-button"]');
  var sel=opts.filter(function(o){return o.ariaChecked==='true'||o.ariaPressed==='true'
    ||o.inputChecked===true||/_checked|selected|active/.test(o.cls);});
  var first=opts[0]?document.querySelector('[data-testid="'+opts[0].tid+'"]'):null;
  var at='';
  if(first){var b=first.getBoundingClientRect(),el=document.elementFromPoint(b.x+b.width/2,b.y+b.height/2);
    if(el)at=tid(el)||cls(el);}
  var bt=document.body?document.body.innerText.replace(/\s+/g,' ').trim():'';
  return JSON.stringify({url:location.href,hash:location.hash,head:bt.substring(0,220),
    options:opts.slice(0,25),next:cb?info(cb):null,selected:sel.map(function(o){return o.tid;}),
    atFirstOption:at});
})()
"""

PICK_JS = r"""(function(){
  function vis(e){var n=e;while(n&&n.nodeType===1){var cs=getComputedStyle(n);
    if(cs.visibility==='hidden'||cs.display==='none')return false;n=n.parentElement;}
    return true;}
  var es=document.querySelectorAll('[data-testid]');
  for(var i=0;i<es.length;i++){var e=es[i],t=e.getAttribute('data-testid')||'';
    if(!t||t.indexOf('-text')>=0)continue;
    if(!/^(image-button-|option-|age-|gender-|pref-gender-|interest-|goal-)/.test(t))continue;
    if(t.indexOf('under-18')>=0)continue;
    var r=e.getBoundingClientRect();if(r.width<=0||r.height<=0||!vis(e))continue;
    return JSON.stringify(t);}
  return JSON.stringify('');
})()"""


def brief(d: dict) -> str:
    return json.dumps({
        "hash": d.get("hash"), "head": str(d.get("head") or "")[:140],
        "selected": d.get("selected"),
        "next": d.get("next"),
        "atFirstOption": d.get("atFirstOption"),
        "options": [{k: o.get(k) for k in ("tid", "tag", "ariaChecked", "ariaPressed",
                                           "inputChecked", "disabled", "cls")}
                    for o in (d.get("options") or [])[:12]],
    }, ensure_ascii=False)


def dump(f) -> dict:
    raw = f.cdp.eval(DUMP_JS, "").strip()
    try:
        d = json.loads(raw)
        if isinstance(d, str):
            d = json.loads(d)
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {"_raw": str(raw)[:400]}


def main() -> int:
    window = service.bit_window_from_env(os.environ)
    if window is None:
        sys.stderr.write("没配 BIT_WORKER_IP / BIT_ID。\n")
        return 3
    ws_url = str(window.fresh_open() or "")
    if not ws_url:
        sys.stderr.write("开不出窗口。\n")
        return 3
    cdp_bin = os.environ.get("SITEFORGE_CDP_BIN") or "cdp"
    cdp_env = os.environ.copy()

    def eval_fn(js):
        return selftest._cdp_eval(cdp_bin, ws_url, js, cdp_env)

    try:
        if not wait_for_window(eval_fn):
            sys.stderr.write("窗口没就绪。\n")
            return 3
        why = selftest._navigate(cdp_bin, ws_url, URL, cdp_env)
        if why:
            sys.stderr.write("导航没成：%s\n" % why)
            return 3
        time.sleep(3.0)

        email = "alexprobe%d@outlook.com" % random.randint(100000, 999999)
        f = Filler(ws_url, {"first_name": "Alex", "email": email})
        got = f.run(max_steps=40, stop_state=STOP)
        if f.stopped_at != STOP:
            print("DID_NOT_REACH %s (got %r)" % (STOP, f.stopped_at), flush=True)
            return 1

        before = dump(f)
        print("BEFORE", brief(before), flush=True)

        pick = json.loads(f.cdp.eval(PICK_JS, "").strip() or '""')
        print("PICK", pick, flush=True)
        if not pick:
            print("NO_OPTION", flush=True)
            return 1
        out = f.cdp.click('[data-testid="%s"]' % pick)
        print("CLICK", str(out).strip()[:160], flush=True)
        time.sleep(1.5)
        after = dump(f)
        print("AFTER", brief(after), flush=True)
        print("HASH_CHANGED", before.get("hash") != after.get("hash"),
              hash_state(str(after.get("url") or "")) or "-", flush=True)

        if (after.get("next") or {}).get("disabled") is False or after.get("next"):
            nout = f.cdp.click('[data-testid="continue-button"]')
            print("NEXT", str(nout).strip()[:160], flush=True)
            time.sleep(2.5)
            final = dump(f)
            print("AFTER_NEXT", brief(final), flush=True)
            print("NEXT_HASH", hash_state(str(final.get("url") or "")) or "-", flush=True)
        return 0
    finally:
        try:
            window.close()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    raise SystemExit(main())
