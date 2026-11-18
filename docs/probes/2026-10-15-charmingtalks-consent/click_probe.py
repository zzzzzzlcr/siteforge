#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对照实验：在 .222 真窗口上，三种点击方式哪种能推进 charmingtalks 第 1 题。

  ① cdp form --check（产物走的那条，作用于零尺寸 input）
  ② JS input.click()
  ③ cdp click 坐标点击（拟人手势）

每步后读一次页面状态（用产物自己的 READ_PAGE_JS），看 hash 有没有从 step1 走到 step2。
用法: python3 -I click_probe.py <ws_url>
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
TID = "gender-female"


def _unwrap(raw):
    """cdp 把 JS 字符串回执又 JSON 包了一层 ⇒ 要解开两层才是 dict。"""
    v = raw
    for _ in range(3):
        if isinstance(v, str):
            try:
                v = json.loads(v)
            except Exception:
                break
        else:
            break
    return v if isinstance(v, dict) else {}


def state(tag):
    p = _unwrap(cdp.eval(m.READ_PAGE_JS, ""))
    print("%-26s hash=%-9s step=%-46s options=%s next=%s"
          % (tag, m.hash_state(p.get("url", "")) or "?", str(p.get("step"))[:44],
             p.get("options"), p.get("nextVisible")))
    return p


def mark(tid):
    mk = "mk%d" % (int(time.time() * 1000) % 100000)
    js = CLS._FIND_JS % (tid, mk)
    kind = cdp.eval("(function(){%s})()" % js, "")
    return (kind or "").strip('"'), '[data-%s="1"]' % mk


def checked(sel):
    r = cdp.eval("(function(){var e=document.querySelector('%s');if(!e)return 'no';"
                 "var i=e.querySelector('input')||e;"
                 "return (i.checked||e.getAttribute('aria-checked')==='true')?'yes':'no';})()" % sel, "")
    return (r or "").strip('"')


def clear(sel, mk):
    cdp.eval("(function(){var e=document.querySelector('%s');"
             "if(e)e.removeAttribute('data-%s');})()" % (sel, mk), "")


p = state("初始")
if m.hash_state(p.get("url", "")) not in (None, "step1"):
    print("!! 已经不在 step1，实验前提不成立，停")
    sys.exit(0)

kind, sel = mark(TID)
print("① 标记结果 kind=%r  sel=%s" % (kind, sel))

# ---- ① cdp form --check ----
try:
    cdp.form(sel, check="true")
except Exception as e:
    print("   form --check 抛异常:", type(e).__name__, str(e)[:120])
print("   checked(读回) =", checked(sel))
time.sleep(4)
p = state("① form --check 之后")
adv = m.hash_state(p.get("url", ""))
if adv != "step1":
    clear(sel, "")
    print(">>> ① 能推进:", adv)
    sys.exit(0)

# ---- ② JS input.click() ----
cdp.eval("(function(){var e=document.querySelector('%s');"
         "var i=e?e.querySelector('input'):null;if(i)i.click();})()" % sel, "")
time.sleep(4)
p = state("② input.click() 之后")
if m.hash_state(p.get("url", "")) != "step1":
    clear(sel, "")
    print(">>> ② 能推进:", m.hash_state(p.get("url", "")))
    sys.exit(0)

# ---- ③ 坐标点击（拟人手势） ----
try:
    cdp.click(sel)
except Exception as e:
    print("   cdp click 抛异常:", type(e).__name__, str(e)[:120])
time.sleep(5)
p = state("③ cdp click 之后")
print(">>> 结论 ③:", m.hash_state(p.get("url", "")))
clear(sel, "")
