#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单选屏「Are you ready for lots of attention?（Yes/Maybe/No）」点击不推进 —— 哪种点法有效。

15:33 那一趟（短路径）在 step 29/30 连点 yes、no 都没前进，随后被判「同题重复」收摊。
这一支走到那屏，依次试四种点法，每次读回 hash/checked：
  ① click_option（cdp form --check）
  ② JS input.click()
  ③ cdp click 可见 label（坐标）
  ④ click_text("Yes")
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

STATE_JS = r"""
(function(){
  function vis(e){var n=e;while(n&&n.nodeType===1){var cs=getComputedStyle(n);
    if(cs.visibility==='hidden'||cs.display==='none')return false;n=n.parentElement;}
    return true;}
  var opts=[];
  var os=document.querySelectorAll('[data-testid^="option-"]');
  for(var i=0;i<os.length;i++){var o=os[i],r=o.getBoundingClientRect();
    if(r.width<=0||r.height<=0||!vis(o))continue;
    var inp=o.querySelector('input');
    opts.push({tid:o.getAttribute('data-testid'),checked:inp?inp.checked:null,
               type:inp?inp.type:null});}
  return JSON.stringify({hash:location.hash,opts:opts,
    body:(document.body?document.body.innerText:'').replace(/\s+/g,' ').slice(0,120)});
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


def st(f, tag):
    try:
        d = json.loads(unq(f.cdp.eval(STATE_JS, "")))
    except Exception as exc:                       # noqa: BLE001
        P.say("  %-6s 读不出来：%s" % (tag, exc))
        return None
    P.say("  %-6s hash=%-10s opts=%s" % (tag, d.get("hash"),
                                          [(o["tid"], o["checked"]) for o in d.get("opts", [])]))
    return d


def walk_to_yesno(f, mod, limit=45):
    """照 run() 的决策走到 yes/maybe/no 那屏（目标：options 含 option-yes 且含 option-no）。"""
    for step in range(limit):
        time.sleep(1.0)
        f._close_cookie()
        p = f.read()
        if p is None:
            continue
        opts = p.get("options") or []
        if "option-yes" in opts and "option-no" in opts:
            P.say("到了目标屏（第 %d 轮）%r" % (step + 1, opts))
            return True
        todo = [t for t, v in p.get("inputs", []) if t and not v]
        if todo:
            tid = todo[0]
            val = mod.value_for(tid, "Alex", "alexprobe@outlook.com")
            if val and (tid, val) not in getattr(f, "_filled", set()):
                f.fill_visible(tid, val)
                f._filled.add((tid, val))
                continue
        if not p.get("legalChecked") and f._check_terms():
            continue
        if p.get("nextVisible") and p.get("checkedOptions"):
            f.click_visible(mod.CONTINUE_BTN)
            continue
        pick = mod.pick_option([o for o in opts if o not in (p.get("checkedOptions") or [])])
        if pick and f.click_option(pick):
            continue
        text = mod.pick_option_by_text(p.get("labels", []))
        if text and f.click_text(text):
            continue
        if p.get("nextVisible"):
            f.click_visible(mod.CONTINUE_BTN)
            continue
        time.sleep(1.2)
    P.say("没走到目标屏")
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
        f = mod.JapanSDatesFiller(ws, FORM, "sm101yesno_%d" % int(time.time()), "")
        f._rpt = lambda step: None
        f.cdp.navigate(URL)
        time.sleep(4)
        if not walk_to_yesno(f, mod):
            return 1
        st(f, "到达")
        base = unq(f.cdp.eval("location.hash", ""))

        P.say("① click_option(option-yes)")
        P.say("   -> %r" % f.click_option("option-yes"))
        time.sleep(2.5)
        d = st(f, "①后")
        if d and d.get("hash") != base:
            P.say(">>> ① 前进了"); return 0

        P.say("② JS input.click()（option-yes）")
        f._ev("var e=document.querySelector('[data-testid=\"option-yes\"]');"
              "var i=e?e.querySelector('input'):null;if(i)i.click();")
        time.sleep(2.5)
        d = st(f, "②后")
        if d and d.get("hash") != base:
            P.say(">>> ② 前进了"); return 0

        P.say("③ cdp click 可见 label（option-yes）")
        try:
            f.cdp.click('[data-testid="option-yes"]')
        except Exception as exc:                   # noqa: BLE001
            P.say("   报错：%s" % exc)
        time.sleep(2.5)
        d = st(f, "③后")
        if d and d.get("hash") != base:
            P.say(">>> ③ 前进了"); return 0

        P.say("④ click_text('Yes')")
        try:
            P.say("   -> %r" % f.click_text("Yes"))
        except Exception as exc:                   # noqa: BLE001
            P.say("   报错：%s" % exc)
        time.sleep(2.5)
        d = st(f, "④后")
        if d and d.get("hash") != base:
            P.say(">>> ④ 前进了")
        else:
            P.say(">>> 四种都没前进")
        return 0
    finally:
        P.close_window()


if __name__ == "__main__":
    sys.exit(main())
