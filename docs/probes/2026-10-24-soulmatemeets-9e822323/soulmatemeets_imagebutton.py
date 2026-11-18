#!/usr/bin/env python3
"""Probe-grade filler for the soulmatemeets image-button variant (9e822323).

为什么单独写：这个 landing 的入口是「语言闸 -> image-button 问卷」，现有三族脚本
（japansdates / goldenagesouls / delachat）都不干净匹配。这一份只为一件事：
在 8100 的干净窗口里真跑一趟，看这条路能不能走完（真提交），以及成功态是不是
hash `wizard2`。

不是交付件：没有 FMR 上报、没有 --task-id、成功判据只给这一趟看。
要变成产线脚本，再按 forms/sites 的形状收（report_url / CLI / 状态名）。
"""
from __future__ import annotations

import base64
import json
import random
import re
import sys
import time

from common import CDPHelper  # PYTHONPATH=/opt/siteforge/forms（或放进 forms/sites）

NAMES = ["James", "John", "Robert", "Michael", "David", "William",
         "Richard", "Joseph", "Thomas", "Daniel"]
EMAIL_DOMAINS = ["outlook.com", "gmail.com", "yahoo.com", "hotmail.com"]

OPTION_RE = r"^(age-|gender-|pref-gender-|image-button-|option-|interest-|goal-)"
# 这一版的 under-18 testid 是 `age-under-18-–-not-eligible`，不是 `age-under-18`
SKIP_PREFIXES = ("age-under-18",)
LANGUAGES = {
    "image-button-english", "image-button-français", "image-button-italiano",
    "image-button-deutsch", "image-button-português", "image-button-español",
}
NEXT_BTN = '[data-testid="continue-button"]'
LANG_EN = '[data-testid="image-button-english"]'
CONSENT_SELECTORS = (
    '[data-testid="cookie-popup-consent-accept-all"]',
    '[data-testid="cookie-popup-common-agree-and-close"]',
)
BUSY_MARKERS = ("Almost done", "Processing your", "Setting up your profile",
                "Just a moment", "getting things ready", "Hold on")
BUSY_TIMEOUT = 90
IDLE_LIMIT = 6

READ_PAGE_JS = r"""
(function(){
  function vis(e){var n=e;while(n&&n.nodeType===1){var cs=getComputedStyle(n);
    if(cs.visibility==='hidden'||cs.display==='none')return false;n=n.parentElement;}
    return true;}
  function tid(e){return (e.getAttribute&&e.getAttribute('data-testid'))||'';}
  function box(e){var r=e.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height};}
  var opt=[],ins=[],chk=[],i,e,r,t;
  var es=document.querySelectorAll('[data-testid]');
  for(i=0;i<es.length;i++){e=es[i];t=tid(e);r=e.getBoundingClientRect();
    if(r.width<=0||r.height<=0||!vis(e))continue;
    if(/^(age-|gender-|pref-gender-|image-button-|option-|interest-|goal-)/.test(t)
       &&t.indexOf('-text')<0)opt.push(t);}
  var is=document.querySelectorAll('input');
  for(i=0;i<is.length;i++){e=is[i];r=e.getBoundingClientRect();
    if(r.width<=0||r.height<=0||!vis(e))continue;
    var it=tid(e),nm=e.name||'';
    if((e.type||'')==='checkbox'){chk.push({tid:it,name:nm,checked:!!e.checked,
      cls:String(e.className||'').slice(0,80)});continue;}
    if((it||nm)&&it.indexOf('legal')<0&&it.indexOf('checkbox')<0
       &&nm.indexOf('legal')<0&&nm.indexOf('checkbox')<0)
      ins.push({tid:it,name:nm,value:e.value||'',type:e.type||''});}
  var cb=document.querySelector('[data-testid="continue-button"]');
  var consent='';
  var ca=document.querySelector('[data-testid="cookie-popup-consent-accept-all"]');
  var cc=document.querySelector('[data-testid="cookie-popup-common-agree-and-close"]');
  if(ca&&vis(ca))consent='cookie-popup-consent-accept-all';
  else if(cc&&vis(cc))consent='cookie-popup-common-agree-and-close';
  var lang=document.querySelector('[data-testid="image-button-english"]');
  var bt=document.body?document.body.innerText.replace(/\s+/g,' ').trim():'';
  var probe=opt[0]||'';
  var atPoint='';
  if(probe){var pe=document.querySelector('[data-testid="'+probe+'"]');
    if(pe){var b=box(pe),el=document.elementFromPoint(b.x+b.w/2,b.y+b.h/2);
      if(el)atPoint=tid(el)||String(el.className||'').slice(0,60);}}
  return JSON.stringify({url:location.href,hash:location.hash,
    progress:(bt.match(/\d+\s*\/\s*\d+/)||[''])[0],head:bt.substring(0,140),
    options:opt,inputs:ins,checkboxes:chk,
    nextVisible:!!(cb&&vis(cb)),
    nextText:cb?((cb.innerText||cb.textContent||'').replace(/\s+/g,' ').trim().slice(0,24)):'',
    consent:consent,
    langVisible:!!(lang&&vis(lang)),atPoint:atPoint,
    firstOption:probe});
})()
"""


def hash_state(url: str) -> str:
    raw = str(url or "")
    if "#" not in raw:
        return ""
    h = raw.split("#", 1)[1]
    try:
        return base64.b64decode(h + "=" * (-len(h) % 4)).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return ""


class Filler:
    def __init__(self, ws_url: str, form_data: dict | None = None, log=print):
        self.cdp = CDPHelper(ws_url)
        self.form = form_data or {}
        self.log = log
        self._filled: set = set()
        self._dead_inputs: set = set()
        self.first = (self.form.get("first_name")
                      or random.choice(NAMES))
        self.email = (self.form.get("email")
                      or "%s%d@%s" % (self.first.lower(), random.randint(100, 9999),
                                      random.choice(EMAIL_DOMAINS)))
        self.password = "Aa1!" + str(random.randint(100000, 999999))
        self.last_url = ""
        #: 长问卷路线：japansdates 那套实测 pref-gender=female 会进长分支；
        #: 第一趟在这个站上也是 start-a-relationship + female 才走到 step13 多选屏。
        self.prefer = ["pref-gender-female", "image-button-start-a-relationship"]

    # ---------- plumbing ----------
    def _dly(self, a=0.6, b=1.4):
        time.sleep(random.uniform(a, b))

    def read(self):
        raw = self.cdp.eval(READ_PAGE_JS, "").strip()
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except ValueError:
            return None
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except ValueError:
                return None
        return data if isinstance(data, dict) else None

    def is_success(self, url: str) -> bool:
        u = str(url or "")
        if any(m in u for m in ("/wizard", "/news-feed", "/newsfeed")):
            return True
        return hash_state(u).startswith("wizard")

    def _value_for(self, testid: str) -> str:
        t = (testid or "").lower()
        if "firstname" in t or t in ("name", "first-name"):
            return self.first
        if "email" in t:
            return self.email
        if "password" in t:
            return self.password
        if "birth" in t or "date" in t:
            return "05-14-1985"
        if "phone" in t or "tel" in t:
            return "2125550147"
        if "zip" in t or "postal" in t:
            return "10001"
        return ""

    # ---------- actions ----------
    def click_sel(self, sel: str) -> str:
        """点一下；`position not stable` / 找不到元素时重试一次（真站动画常见）。"""
        out = ""
        for _ in range(2):
            try:
                out = str(self.cdp.click(sel))
            except Exception as exc:  # noqa: BLE001
                out = "EXC: %s" % exc
            if "Error:" not in out:
                return out
            time.sleep(0.4)
        return out

    def dismiss_consent(self, sel: str) -> bool:
        if not sel:
            return False
        css = '[data-testid="%s"]' % sel
        try:
            out = self.click_sel(css)
            self.log("  consent: %s -> %s" % (sel, str(out).strip()[:110]))
            return True
        except Exception as exc:  # noqa: BLE001
            self.log("  consent: %s 点击报错 %s" % (sel, str(exc)[:90]))
            return False

    def run(self, max_steps: int = 80, stop_state: str = ""):
        """返回 True=成功、False=失败、None=按 stop_state 停住（探针用）。"""
        answered = 0
        idle = 0
        busy_since = None
        self.stopped_at = ""
        for step in range(1, max_steps + 1):
            self._dly()
            page = self.read()
            if page is None:
                idle += 1
                self.log("step %d: page unreadable (%d)" % (step, idle))
                if idle >= IDLE_LIMIT:
                    self.log("FAIL: page unreadable x%d" % idle)
                    return False
                continue

            url = str(page.get("url") or "")
            self.last_url = url or self.last_url
            state = hash_state(url)
            self.log("step %d  hash=%s  head=%r" % (
                step, state or "-", str(page.get("head") or "")[:70]))
            if stop_state and state == stop_state:
                self.stopped_at = state
                self.log("STOP at %s（探针按 stop_state 停住，不动这一屏）" % state)
                return None
            if self.is_success(url):
                self.log("SUCCESS: %s" % url[:120])
                return True

            #: 同一屏上「点一个选项 <-> 按 Next/Skip」交替：
            #: 单选屏点完就换 hash；多选屏（9e822323 的 step13）点选项只切换选中，
            #: 必须按 Skip/Next 才前进。只按 hash/head/选项集判断「还是同一屏」。
            sig = "|".join([state, str(page.get("head") or "")[:80],
                            ",".join(sorted(page.get("options") or [])),
                            "N" if page.get("nextVisible") else ""])
            if sig != getattr(self, "_screen_sig", ""):
                self._screen_sig = sig
                self._screen_clicks = 0

            # 1) 新同意浮层（选完语言才出现）—— 每轮先关
            if page.get("consent"):
                self.dismiss_consent(str(page.get("consent")))
                time.sleep(1.0)
                idle = 0
                busy_since = None
                continue

            # 2) 语言闸：固定点 English
            if page.get("langVisible"):
                try:
                    out = self.click_sel(LANG_EN)
                    self.log("  language: English -> %s" % str(out).strip()[:90])
                except Exception as exc:  # noqa: BLE001
                    self.log("  language: 点击报错 %s" % str(exc)[:90])
                time.sleep(2.0)
                idle = 0
                busy_since = None
                continue

            # 3) 填空输入
            todo = [x for x in (page.get("inputs") or [])
                    if (x.get("tid") or x.get("name")) and not x.get("value")
                    and (x.get("tid") or x.get("name")) not in self._dead_inputs]
            if todo:
                it = todo[0]
                tid, nm = str(it.get("tid") or ""), str(it.get("name") or "")
                key = tid or nm
                val = self._value_for(key)
                if val and (key, val) not in self._filled:
                    sel = ('[data-testid="%s"]' % tid) if tid else ('input[name="%s"]' % nm)
                    try:
                        self.cdp.form(sel, value=val)
                        self._filled.add((key, val))
                        self.log("  fill %s = %r" % (key, val))
                    except Exception as exc:  # noqa: BLE001
                        self.log("  fill %s 报错 %s" % (key, str(exc)[:90]))
                        self._dead_inputs.add(key)
                    idle = 0
                    busy_since = None
                    continue
                self._dead_inputs.add(key)
                self.log("  fill %s: 没有可用的值，跳过" % key)
                continue

            # 4) 条款/同意类复选框（可见且未勾）
            boxes = [b for b in (page.get("checkboxes") or []) if not b.get("checked")]
            legal = [b for b in boxes
                     if any(k in (str(b.get("tid")) + " " + str(b.get("name"))
                                  + " " + str(b.get("cls"))).lower()
                            for k in ("legal", "terms", "agree", "consent"))]
            if legal:
                b = legal[0]
                sel = ('[data-testid="%s"]' % b["tid"]) if b.get("tid") else \
                      ('input[name="%s"]' % b["name"])
                try:
                    out = self.cdp.form(sel, check="true")
                    self.log("  check %s -> %s" % (sel, str(out).strip()[:90]))
                except Exception as exc:  # noqa: BLE001
                    self.log("  check %s 报错 %s" % (sel, str(exc)[:90]))
                idle = 0
                busy_since = None
                continue

            # 5) 选项
            opts = [o for o in (page.get("options") or [])
                    if o and not o.startswith(SKIP_PREFIXES)
                    and o not in LANGUAGES and not o.endswith("-text")]
            if opts:
                if page.get("nextVisible") and getattr(self, "_screen_clicks", 0) % 2 == 1:
                    out = self.click_sel(NEXT_BTN)
                    self.log("  Next/Skip (%r) -> %s" % (
                        page.get("nextText") or "", out.strip()[:80]))
                else:
                    pick = next((p for p in self.prefer if p in opts), None) \
                        or random.choice(opts)
                    out = self.click_sel('[data-testid="%s"]' % pick)
                    self.log("  option %s -> %s%s" % (
                        pick, out.strip()[:80],
                        " (preferred)" if pick in self.prefer else ""))
                    answered += 1
                self._screen_clicks = getattr(self, "_screen_clicks", 0) + 1
                idle = 0
                busy_since = None
                continue

            # 6) Next / 提交
            if page.get("nextVisible"):
                out = self.click_sel(NEXT_BTN)
                self.log("  Next/Skip (%r) -> %s" % (
                    page.get("nextText") or "", out.strip()[:80]))
                self._screen_clicks = getattr(self, "_screen_clicks", 0) + 1
                idle = 0
                busy_since = None
                continue

            # 7) 忙 vs 空转
            head = str(page.get("head") or "")
            if any(m.lower() in head.lower() for m in BUSY_MARKERS):
                if busy_since is None:
                    busy_since = time.time()
                waited = time.time() - busy_since
                if waited >= BUSY_TIMEOUT:
                    self.log("FAIL: 处理态 %.0fs 没出来 (%s)" % (waited, head[:60]))
                    return False
                idle = 0
                self.log("  busy %.0fs: %s" % (waited, head[:60]))
                continue

            busy_since = None
            idle += 1
            self.log("  nothing actionable (%d) %s" % (idle, head[:80]))
            if idle >= IDLE_LIMIT:
                self.log("FAIL: stalled after %d answers at %s" % (answered, url[:100]))
                return False

        self.log("FAIL: max_steps (%d answers) at %s" % (answered, self.last_url[:120]))
        return False
