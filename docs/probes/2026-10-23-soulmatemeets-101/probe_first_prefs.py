#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第一屏 Preferences（What are you looking for?）点击不推进：到底哪种点法有效。

短路径那一趟（15:29）在 step 5/6 连点了两个不同选项都没动，第三次被判「同题重复」收摊。
这一支只做：走到那屏 → dump（radio/checkbox、checked、buttons）→ 依次试
  ① `click_option`（cdp form --check）
  ② `click_text(选项文字)`（坐标点 label/文字）
  ③ JS `input.click()`
每一步都读回「有没有前进 / checked 是谁」。

跑法：python3 -I probe_first_prefs.py
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
  var out={hash:location.hash,opts:[],buttons:[]};
  var os=document.querySelectorAll('[data-testid^="option-"]');
  for(var i=0;i<os.length;i++){var o=os[i],r=o.getBoundingClientRect();
    if(r.width<=0||r.height<=0||!vis(o))continue;
    var inp=o.querySelector('input');
    out.opts.push({tid:o.getAttribute('data-testid'),tag:o.tagName.toLowerCase(),
      text:(o.innerText||'').replace(/\s+/g,' ').trim().slice(0,30),
      input:inp?{type:inp.type,checked:inp.checked}:null});}
  var bs=document.querySelectorAll('[data-testid^="continue"],button');
  for(var j=0;j<bs.length&&out.buttons.length<8;j++){var b=bs[j],r2=b.getBoundingClientRect();
    if(r2.width<4||r2.height<4||!vis(b))continue;
    out.buttons.push({tid:b.getAttribute('data-testid')||'',text:(b.innerText||'').trim().slice(0,20)});}
  out.body=(document.body?document.body.innerText:'').replace(/\s+/g,' ').slice(0,140);
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
        P.say("[%s] 读不出来：%s" % (tag, exc))
        return None
    P.say("[%s] hash=%s body=%r" % (tag, d.get("hash"), d.get("body", "")[:80]))
    for o in d.get("opts", []):
        P.say("   opt %-22s %-7s input=%s %r" %
              (o["tid"], o["tag"], o["input"], o["text"]))
    for b in d.get("buttons", []):
        P.say("   btn %-18s %r" % (b["tid"], b["text"]))
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
        f = mod.JapanSDatesFiller(ws, FORM, "sm101prefs_%d" % int(time.time()), "")
        f._rpt = lambda step: None
        f.cdp.navigate(URL)
        # 走到年龄屏 → 点年龄（用脚本自己的文本兜底）→ 等第一屏 Preferences
        for _ in range(30):
            time.sleep(1)
            p = f.read()
            if p and "option-have-fun" in (p.get("options") or []):
                break
            if p and p.get("labels") and any(
                    t in ("Under 18 – Not eligible",) for t in []):
                pass
            pick = mod.pick_option_by_text(p.get("labels", [])) if p else None
            if pick:
                f.click_text(pick)
        p = f.read()
        if not p or "option-have-fun" not in (p.get("options") or []):
            P.say("没走到第一屏 Preferences：%r" % (p,))
            return 1
        dump(f, "到达")

        P.say("① click_option(option-have-fun)")
        P.say("   返回 %r" % f.click_option("option-have-fun"))
        time.sleep(2.5)
        d1 = dump(f, "①后")
        if d1 and "option-have-fun" not in str(d1):
            pass
        if d1 and not any(o["tid"] == "option-have-fun" for o in d1.get("opts", [])):
            P.say(">>> ① 就前进了")
            return 0

        P.say("② click_text('Just some fun')")
        try:
            P.say("   返回 %r" % f.click_text("Just some fun"))
        except Exception as exc:                   # noqa: BLE001
            P.say("   报错：%s" % exc)
        time.sleep(2.5)
        d2 = dump(f, "②后")
        if d2 and not any(o["tid"] == "option-have-fun" for o in d2.get("opts", [])):
            P.say(">>> ② 前进了")
            return 0

        P.say("③ JS input.click()（option-have-fun 里的 input）")
        f._ev("var e=document.querySelector('[data-testid=\"option-have-fun\"]');"
              "var i=e?e.querySelector('input'):null;if(i)i.click();")
        time.sleep(2.5)
        d3 = dump(f, "③后")
        if d3 and not any(o["tid"] == "option-have-fun" for o in d3.get("opts", [])):
            P.say(">>> ③ 前进了")
        else:
            P.say(">>> 三种都没前进")
        return 0
    finally:
        P.close_window()


if __name__ == "__main__":
    sys.exit(main())
