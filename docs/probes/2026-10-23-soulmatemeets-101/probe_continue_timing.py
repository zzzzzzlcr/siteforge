#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多选屏：选一个 → 等 ~1.8s → 读 → 按 Next → 每 0.5s 看 6 秒，页面到底有没有走。

线上（26800520/522/681）与本地 run() 都卡在同一处：夜里那题（checkbox + "Next"）
点了一个之后按 Next，页面不前进、Next 随即消失。人工探针（等 2.0s 再按）能过。
这一支就量「按下去之后那 6 秒」发生了什么。
"""
import importlib.util
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import probe_prefgender as P                          # noqa: E402
import probe_perfect_evening as E                     # noqa: E402

ART = os.path.join(HERE, "jd_pkg", "sites", "japansdates_hooked.py")
FORM = os.path.join(HERE, "form.json")
URL = "https://soulmatemeets.com/land/sp/23d247da"

POLL_JS = r"""
(function(){
  function vis(e){var n=e;while(n&&n.nodeType===1){var cs=getComputedStyle(n);
    if(cs.visibility==='hidden'||cs.display==='none')return false;n=n.parentElement;}
    return true;}
  var cb=[],chk=[];
  var es=document.querySelectorAll('[data-testid^="option-"]');
  for(var i=0;i<es.length;i++){var e=es[i],r=e.getBoundingClientRect();
    if(r.width<=0||r.height<=0||!vis(e))continue;
    var inp=e.querySelector('input');
    if(inp&&inp.checked)chk.push(e.getAttribute('data-testid'));}
  var cs=document.querySelectorAll('[data-testid="continue-button"]');
  for(var j=0;j<cs.length;j++){var c=cs[j],r2=c.getBoundingClientRect();
    if(r2.width<=0||r2.height<=0||!vis(c))continue;
    cb.push({text:(c.innerText||'').trim().slice(0,20),disabled:!!c.disabled,
             aria:c.getAttribute('aria-disabled'),w:Math.round(r2.width),h:Math.round(r2.height)});}
  return JSON.stringify({hash:location.hash,checked:chk,continues:cb,
    body:(document.body?document.body.innerText:'').replace(/\s+/g,' ').slice(0,160)});
})()
"""


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


def poll(f, tag):
    try:
        d = json.loads(unq(f.cdp.eval(POLL_JS, "")))
    except Exception as exc:                       # noqa: BLE001
        P.say("   %s poll 读不出来：%s" % (tag, exc))
        return None
    P.say("   %-8s hash=%-12s checked=%-30s continues=%s body=%r"
          % (tag, d.get("hash"), d.get("checked"), d.get("continues"),
             d.get("body", "")[:80]))
    return d


def main():
    with open(FORM, "w", encoding="utf-8") as fh:
        json.dump({"first_name": "Alex", "email": "alexprobe@outlook.com"}, fh)
    spec = importlib.util.spec_from_file_location("h", ART)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    ws = P.open_ws()
    P.say("ws=%s" % ws)
    try:
        f = mod.JapanSDatesFiller(ws, FORM, "sm101timing_%d" % int(time.time()), "")
        f._rpt = lambda step: None
        f.cdp.navigate(URL)
        for _ in range(20):
            time.sleep(1)
            n = unq(f.cdp.eval(
                "document.querySelectorAll('[data-testid^=\"step-content-item-\"]').length", ""))
            if n.strip() not in ("0", ""):
                break
        if not E.walk_to_evening(f, mod):
            return 1
        p = f.read()
        P.say("到达：step=%r checked=%s next=%r" %
              (p.get("step"), p.get("checkedOptions"), p.get("nextVisible")))
        # 选一个（随机，与 run() 同一条）
        pick = mod.pick_option(p.get("options", []))
        P.say("-> click_option(%r)" % pick)
        P.say("   click_option=%r" % f.click_option(pick))
        time.sleep(1.8)                                   # 与 run() 的节奏一致
        poll(f, "选后1.8s")
        f.click_visible(mod.CONTINUE_BTN)
        P.say("-> click_visible(continue-button)")
        for i in range(12):                               # 6 秒，每 0.5s
            time.sleep(0.5)
            poll(f, "+%.1fs" % ((i + 1) * 0.5))
        return 0
    finally:
        P.close_window()


if __name__ == "__main__":
    sys.exit(main())
