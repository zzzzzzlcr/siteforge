#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""卡在 ChoosePrefGender（Who can we help you meet?）—— 真页面 DOM 取证。

复现结论（run_prod.py）：线上 config 101 的 japansdates.py 在 23d247da 上走到
「Your profile / Preferences」第 3 屏（testid `step-content-item-ChoosePrefGender`，
选项 `pref-gender-male` / `pref-gender-female`）后，连点两次 male 都不前进，
46 秒后按 max_steps 报失败。

这一支只回答一件事：**那次点击到底有没有落到选项上、选项有没有被选中、这一屏等的是什么**。
走到那一屏 → 点一次 → 前后各 dump 一份「可见 DOM + 复选框状态 + 可见按钮 + 点击中心点上是谁」。

跑法（宿主）：
    python3 -I probe_prefgender.py [sibling_url]
sibling_url 给了就先在它上面走同一段（对照：同族站点这屏点一下会不会前进）。
"""
import importlib.util
import json
import os
import re
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ART = os.path.join(HERE, "jd_pkg", "sites", "japansdates_prod.py")
FORM = os.path.join(HERE, "form.json")
OUT = os.path.join(HERE, "prefgender")

WORKER = "192.168.1.222"
BIT_ID = "982d158b41b14fbb87754667859b4719"
API = "http://%s:54345" % WORKER
OPEN_ARGS = ["--remote-debugging-address=0.0.0.0", "--remote-debugging-port=61129",
             "--remote-allow-origins=*", "--disable-session-crashed-bubble"]
LOOPBACK = re.compile(r"(\[::1\]|localhost|127\.0\.0\.1|0\.0\.0\.0)")


def say(*a):
    print(*a, flush=True)


def post(path, body, timeout=25):
    req = urllib.request.Request(API + path, data=json.dumps(body).encode("utf-8"),
                                 method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def alive():
    try:
        out = post("/browser/pids/alive", {"ids": [BIT_ID]})
    except Exception as exc:                     # noqa: BLE001
        return "?"
    data = out.get("data") if isinstance(out, dict) else None
    if isinstance(data, dict) and data.get(BIT_ID):
        return int(data[BIT_ID])
    return None


def open_ws():
    out = post("/browser/open", {"id": BIT_ID, "args": list(OPEN_ARGS), "queue": True},
               timeout=70)
    data = out.get("data") if isinstance(out, dict) else None
    ws = (data or {}).get("ws") if isinstance(data, dict) else None
    if isinstance(out, dict) and out.get("success") is True and ws:
        return LOOPBACK.sub(WORKER, str(ws))
    raise RuntimeError("开窗没成：%s" % str(out)[:300])


def close_window():
    if alive() is None:
        say("  关窗：本来就不在")
        return
    try:
        post("/browser/close", {"id": BIT_ID})
    except Exception as exc:                     # noqa: BLE001
        say("  关窗报错：%s" % exc)
    for _ in range(10):
        time.sleep(1.5)
        if alive() is None:
            say("  关窗：确认已关")
            return
    say("  关窗：**没能确认**")


DUMP_JS = r"""
(function(){
  function vis(e){var n=e;while(n&&n.nodeType===1){var cs=getComputedStyle(n);
    if(cs.visibility==='hidden'||cs.display==='none')return false;n=n.parentElement;}
    return true;}
  function box(e){var r=e.getBoundingClientRect();
    return {x:Math.round(r.x),y:Math.round(r.y),w:Math.round(r.width),h:Math.round(r.height)};}
  var out={url:location.href,hash:location.hash,title:document.title,
           steps:[],opts:[],inputs:[],buttons:[],atPoint:[]};
  var ses=document.querySelectorAll('[data-testid^="step-content-item-"]');
  for(var i=0;i<ses.length;i++){
    var e=ses[i],r=e.getBoundingClientRect();
    out.steps.push({tid:e.getAttribute('data-testid'),vis:vis(e),
                    w:Math.round(r.width),h:Math.round(r.height),
                    text:(e.innerText||'').replace(/\s+/g,' ').trim().slice(0,160)});
  }
  var os=document.querySelectorAll('[data-testid^="pref-gender-"]');
  for(var j=0;j<os.length;j++){
    var o=os[j],r2=o.getBoundingClientRect(),inp=o.querySelector('input');
    out.opts.push({tid:o.getAttribute('data-testid'),vis:vis(o),box:box(o),
                   tag:o.tagName.toLowerCase(),cls:String(o.className||'').slice(0,80),
                   html:o.outerHTML.slice(0,700),
                   input: inp?{type:inp.type,checked:inp.checked,value:inp.value,
                               box:box(inp),cls:String(inp.className||'').slice(0,60)}:null,
                   ariaChecked:o.getAttribute('aria-checked')});
  }
  var is=document.querySelectorAll('input');
  for(var k=0;k<is.length && out.inputs.length<20;k++){
    var q=is[k];
    out.inputs.push({type:q.type,name:q.name||'',tid:q.getAttribute('data-testid')||'',
                     checked:q.checked,box:box(q),vis:vis(q)});
  }
  var bs=document.querySelectorAll('button,[role=button],a');
  for(var b=0;b<bs.length && out.buttons.length<30;b++){
    var x=bs[b],r3=x.getBoundingClientRect();
    if(r3.width<4||r3.height<4||!vis(x))continue;
    out.buttons.push({tag:x.tagName.toLowerCase(),tid:x.getAttribute('data-testid')||'',
                      text:(x.innerText||x.value||'').replace(/\s+/g,' ').trim().slice(0,40),
                      box:box(x),disabled:!!x.disabled});
  }
  var m=document.querySelector('[data-testid="pref-gender-male"]');
  if(m){var mm=m.getBoundingClientRect(),cx=mm.x+mm.width/2,cy=mm.y+mm.height/2;
    var chain=[],el=document.elementFromPoint(cx,cy);
    while(el&&chain.length<6){chain.push({tag:el.tagName.toLowerCase(),
      tid:el.getAttribute&&el.getAttribute('data-testid')||'',
      cls:String(el.className||'').slice(0,60)});el=el.parentElement;}
    out.atPoint=chain;}
  return JSON.stringify(out);
})()
"""


def dump(f, tag):
    raw = f.cdp.eval(DUMP_JS, "")
    path = os.path.join(OUT, "%s.json" % tag)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(str(raw))
    try:
        d = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(d, str):
            d = json.loads(d)
    except Exception:                            # noqa: BLE001
        say("[%s] 解析不了，原文在 %s：%s" % (tag, path, str(raw)[:300]))
        return None
    say("[%s] hash=%s url=%s" % (tag, d.get("hash"), str(d.get("url"))[:110]))
    for s in d.get("steps", []):
        say("   step %-45s vis=%s %sx%s %r" % (s["tid"], s["vis"], s["w"], s["h"],
                                               s["text"][:90]))
    for o in d.get("opts", []):
        say("   opt  %-22s vis=%s %s cls=%r inp=%s" % (o["tid"], o["vis"], o["box"],
                                                       o["cls"], o["input"]))
    say("   inputs=%s" % json.dumps(d.get("inputs", []), ensure_ascii=False)[:400])
    say("   buttons=%s" % json.dumps(d.get("buttons", []), ensure_ascii=False)[:700])
    say("   atPoint=%s" % json.dumps(d.get("atPoint", []), ensure_ascii=False)[:300])
    return d


def walk_to_gender(f, mod, limit=40):
    """照 run() 的决策走到 ChoosePrefGender（**不点这一屏**）。"""
    idle = 0
    for step in range(limit):
        time.sleep(1.0)
        f._close_cookie()
        p = f.read()
        if p is None:
            idle += 1
            continue
        if not (p.get("step") or p.get("options") or p.get("labels")
                or p.get("inputs") or p.get("nextVisible")):
            idle += 1                      # 载入中的空壳：等，不算「走不动」
            if idle == 1:
                say("  页面还是空的（%s）—— 等它载入" % str(p.get("url"))[:90])
            if idle >= 12:
                say("  连续空壳 %d 次，放弃" % idle)
                return False
            continue
        idle = 0
        url = p.get("url", "")
        if mod.is_success(url):
            say("已成功态，停")
            return True
        if "ChoosePrefGender" in str(p.get("step")):
            say("到了 ChoosePrefGender（第 %d 轮）" % (step + 1))
            dump(f, "before_click")
            return True
        todo = [t for t, v in p.get("inputs", []) if t and not v]
        if todo:
            tid = todo[0]
            val = mod.value_for(tid, "Alex", "alexprobe@outlook.com")
            if val:
                f.fill_visible(tid, val)
                continue
        if not p.get("legalChecked") and f._check_terms():
            continue
        pick = mod.pick_option(p.get("options", []))
        if pick and f.click_option(pick):
            continue
        text = mod.pick_option_by_text(p.get("labels", []))
        if text and f.click_text(text):
            idle = 0
            continue
        if p.get("nextVisible"):
            f.click_visible(mod.CONTINUE_BTN)
            idle = 0
            continue
        idle += 1
        if idle == 1:
            say("  这一瞬没得点（等渲染）：%s" % json.dumps(p, ensure_ascii=False)[:300])
        if idle >= 12:
            say("  连续 %d 次没得点，放弃" % idle)
            return False
        time.sleep(1.5)
    return False


def try_things(f, mod):
    """点一次 male，前后各 dump；还不走就试别的交互（都不提交）。"""
    f.click_option("pref-gender-male")
    time.sleep(2.5)
    dump(f, "after_click_option")
    p = f.read()
    say("   read 之后 step=%r hash=%r options=%s" %
        (p.get("step") if p else None, p.get("url", "")[-40:] if p else None,
         p.get("options") if p else None))
    if p and "ChoosePrefGender" not in str(p.get("step")):
        say("   >>> 点一次就前进了")
        return True

    # ① 文本点击（label 上的字）
    say("   试 click_text('Man')")
    try:
        f.click_text("Man")
    except Exception as exc:                     # noqa: BLE001
        say("   click_text 报错：%s" % exc)
    time.sleep(2.5)
    dump(f, "after_click_text")
    p = f.read()
    if p and "ChoosePrefGender" not in str(p.get("step")):
        say("   >>> 文本点击前进了")
        return True

    # ② 直接 .click() 里层 input
    say("   试 JS input.click()")
    f._ev("var e=document.querySelector('[data-testid=\"pref-gender-male\"]');"
          "var i=e?e.querySelector('input'):null;if(i)i.click();")
    time.sleep(2.5)
    dump(f, "after_js_input_click")
    p = f.read()
    if p and "ChoosePrefGender" not in str(p.get("step")):
        say("   >>> JS 点 input 前进了")
        return True

    # ③ 选中之后有没有 continue 可按
    say("   试 click_visible(CONTINUE_BTN)")
    try:
        f.click_visible(mod.CONTINUE_BTN)
    except Exception as exc:                     # noqa: BLE001
        say("   continue 报错：%s" % exc)
    time.sleep(2.5)
    dump(f, "after_continue")
    p = f.read()
    if p and "ChoosePrefGender" not in str(p.get("step")):
        say("   >>> continue 前进了")
        return True
    return False


def main():
    os.makedirs(OUT, exist_ok=True)
    with open(FORM, "w", encoding="utf-8") as fh:
        json.dump({"first_name": "Alex", "email": "alexprobe@outlook.com"}, fh)

    spec = importlib.util.spec_from_file_location("jd_prod", ART)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    urls = ["https://soulmatemeets.com/land/sp/23d247da"]
    if len(sys.argv) > 1:
        urls.append(sys.argv[1])

    say("探活：%r" % alive())
    ws = open_ws()
    say("ws=%s" % ws)
    try:
        f = mod.JapanSDatesFiller(ws, FORM, "sm101dom_%d" % int(time.time()), "")
        f._rpt = lambda step: None
        for url in urls:
            say("\n===== %s" % url)
            f.cdp.navigate(url)
            time.sleep(3)
            dump(f, "landing_%s" % url.rsplit("/", 1)[-1])
            if walk_to_gender(f, mod):
                try_things(f, mod)
        # 让窗口停在这一屏（先不关，方便人看）—— 由 finally 关
    finally:
        close_window()


if __name__ == "__main__":
    try:
        main()
    except Exception:                            # noqa: BLE001
        import traceback
        traceback.print_exc()
        sys.exit(2)
