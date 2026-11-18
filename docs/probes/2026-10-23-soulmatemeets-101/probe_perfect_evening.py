#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""14:xx 的生产新卡点：「The perfect evening with her is..」这一屏点了不前进。

线上日志（任务 26800520/26800522/26800681）已经过了 cookie 那屏（step 1..15 都动了），
卡在 `step-content-item-Preferences`：
```
卡住诊断 step=step-content-item-Preferences next=False legal=False
options=['option-watch-films', 'option-sports', 'option-hiking', ...]
step 16: nothing actionable (1, 0s) … 44 (29, 45s)
```
前一步（step 14）点的是 `option-other`，step 15 按了 continue（同屏重复被当成多选），
之后 45 秒什么都不点。

这一支走到那屏，把它原样 dump 出来（选项/复选框状态/continue 是否禁用/点了有没有反应），
**不提交**。跑法：python3 -I probe_perfect_evening.py
"""
import importlib.util
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import probe_prefgender as P                          # noqa: E402

ART = os.path.join(HERE, "jd_pkg", "sites", "japansdates_hooked.py")
FORM = os.path.join(HERE, "form.json")
URL = "https://soulmatemeets.com/land/sp/23d247da"

DUMP_JS = r"""
(function(){
  function vis(e){var n=e;while(n&&n.nodeType===1){var cs=getComputedStyle(n);
    if(cs.visibility==='hidden'||cs.display==='none')return false;n=n.parentElement;}
    return true;}
  function box(e){var r=e.getBoundingClientRect();
    return {x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)};}
  var out={url:location.href,hash:location.hash,steps:[],opts:[],buttons:[]};
  var ses=document.querySelectorAll('[data-testid^="step-content-item-"]');
  for(var i=0;i<ses.length;i++){var e=ses[i],r=e.getBoundingClientRect();
    if(!vis(e)||r.width<5)continue;
    out.steps.push({tid:e.getAttribute('data-testid'),box:box(e),
      text:(e.innerText||'').replace(/\s+/g,' ').trim().slice(0,300)});}
  var os=document.querySelectorAll('[data-testid^="option-"]');
  for(var j=0;j<os.length;j++){var o=os[j];
    if(!vis(o))continue;
    var inp=o.querySelector('input');
    out.opts.push({tid:o.getAttribute('data-testid'),tag:o.tagName.toLowerCase(),
      cls:String(o.className||'').slice(0,80),box:box(o),
      ariaChecked:o.getAttribute('aria-checked'),
      input: inp?{type:inp.type,checked:inp.checked,value:inp.value}:null,
      text:(o.innerText||'').replace(/\s+/g,' ').trim().slice(0,50)});}
  var bs=document.querySelectorAll('[data-testid^="continue"], button');
  for(var k=0;k<bs.length && out.buttons.length<12;k++){var b=bs[k];
    if(!vis(b))continue;
    var r2=b.getBoundingClientRect(); if(r2.width<4)continue;
    out.buttons.push({tid:b.getAttribute('data-testid')||'',text:(b.innerText||'').trim().slice(0,30),
      disabled:!!b.disabled,ariaDisabled:b.getAttribute('aria-disabled'),
      cls:String(b.className||'').slice(0,60),box:box(b)});}
  return JSON.stringify(out);
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


def dump(f, tag):
    try:
        d = json.loads(unq(f.cdp.eval(DUMP_JS, "")))
    except Exception as exc:                       # noqa: BLE001
        P.say("[%s] dump 读不出来：%s" % (tag, exc))
        return None
    P.say("[%s] url=%s hash=%s" % (tag, str(d.get("url"))[:100], d.get("hash")))
    for s in d.get("steps", []):
        P.say("   step %-42s %s %r" % (s["tid"], s["box"], s["text"][:160]))
    for o in d.get("opts", []):
        P.say("   opt  %-24s %-5s cls=%-30s aria=%s input=%s %s %r"
              % (o["tid"], o["tag"], o["cls"], o["ariaChecked"], o["input"], o["box"],
                 o["text"][:24]))
    for b in d.get("buttons", []):
        P.say("   btn  %-18s %-22r disabled=%s aria=%s %s"
              % (b["tid"], b["text"], b["disabled"], b["ariaDisabled"], b["box"]))
    return d


def walk_to_evening(f, mod, limit=50):
    """照 run() 的决策走（含「同题重复 → 当多选、只按 continue」），直到目标屏。"""
    idle = 0
    for step in range(limit):
        time.sleep(1.0)
        f._close_cookie()
        p = f.read()
        if p is None:
            idle += 1
            continue
        opts = p.get("options", []) or []
        head = (p.get("head") or "")
        if "option-watch-films" in opts or "perfect even" in head.lower():
            P.say("到了目标屏（第 %d 轮）" % (step + 1))
            return True
        if not (p.get("step") or opts or p.get("labels") or p.get("inputs")
                or p.get("nextVisible")):
            idle += 1
            if idle >= 12:
                P.say("连续空壳，放弃")
                return False
            continue
        idle = 0
        P.say("  #%02d step=%-42r next=%-5r opts=%s" %
              (step + 1, str(p.get("step"))[:42], p.get("nextVisible"),
               (p.get("options") or [])[:6]))
        todo = [t for t, v in p.get("inputs", []) if t and not v]
        if todo:
            tid = todo[0]
            val = mod.value_for(tid, "Alex", "alexprobe@outlook.com")
            if val and (tid, val) not in getattr(f, "_filled", set()):
                f.fill_visible(tid, val)
                f._filled.add((tid, val))
                P.say("     filled %s" % tid)
                continue
        if not p.get("legalChecked") and f._check_terms():
            P.say("     勾选条款")
            continue
        # 与 run() 同一条：同一题连着 ≥3 次 = 多选（选完要按下一屏）
        key = "%s|%s" % (p.get("step", ""), (p.get("head", "") or "")[:40])
        if key == getattr(f, "_last_q", ""):
            f._repeat = getattr(f, "_repeat", 0) + 1
        else:
            f._repeat = 0
            f._last_q = key
        stuck = f._repeat >= 2
        pick = None if stuck else mod.pick_option(opts)
        if pick and f.click_option(pick):
            P.say("     点 %s" % pick)
            continue
        text = None if stuck else mod.pick_option_by_text(p.get("labels", []))
        if text and f.click_text(text):
            P.say("     文本点 %r" % text)
            continue
        if p.get("nextVisible"):
            f.click_visible(mod.CONTINUE_BTN)
            P.say("     continue%s" % ("（同题重复）" if stuck else ""))
            continue
        idle += 1
        if idle >= 10:
            P.say("走不动了：%s" % json.dumps(p, ensure_ascii=False)[:400])
            return False
        time.sleep(1.2)
    P.say("走满 %d 轮还没到目标屏" % limit)
    return False


def main():
    with open(FORM, "w", encoding="utf-8") as fh:
        json.dump({"first_name": "Alex", "email": "alexprobe@outlook.com"}, fh)
    spec = importlib.util.spec_from_file_location("h", ART)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    ws = P.open_ws()
    P.say("ws=%s" % ws)
    try:
        f = mod.JapanSDatesFiller(ws, FORM, "sm101evening_%d" % int(time.time()), "")
        f._rpt = lambda step: None
        f.cdp.navigate(URL)
        for _ in range(20):
            time.sleep(1)
            n = unq(f.cdp.eval("document.querySelectorAll('[data-testid^=\"step-content-item-\"]').length", ""))
            if n.strip() not in ("0", ""):
                break
        if not walk_to_evening(f, mod):
            return 1
        d0 = dump(f, "到达时")
        if not d0:
            return 1
        # ① 点一下 watch-films（线上 step 14 点的是 option-other）
        P.say("-> click_option(option-other)（线上 step 14 点的就是它）")
        out = f.click_option("option-other")
        P.say("   click_option=%r" % out)
        time.sleep(2.0)
        dump(f, "点完选项")
        p = f.read()
        P.say("   read: step=%r next=%r options=%s"
              % (p.get("step") if p else None, p.get("nextVisible") if p else None,
                 p.get("options") if p else None))
        # ② 有 continue 就按一下
        P.say("-> click_visible(continue-button)")
        try:
            f.click_visible(mod.CONTINUE_BTN)
        except Exception as exc:                   # noqa: BLE001
            P.say("   continue 报错：%s" % exc)
        time.sleep(2.5)
        dump(f, "点完 continue")
        p2 = f.read()
        P.say("   现在 step=%r hash=%r" %
              (p2.get("step") if p2 else None,
               mod.hash_state(p2.get("url", "")) if p2 else None))
        return 0
    finally:
        P.close_window()


if __name__ == "__main__":
    sys.exit(main())
