#!/usr/bin/env python3
"""Site script for goldenagesouls.com — the 2026-09 redesigned quiz.

Why this exists
---------------
goldenagesouls.com used to be routed to datewhirl.py, which recognises the
old quiz shape: <label> elements and input[type=text|email|password]. The
platform rewrote the quiz to buttons carrying data-testid, so on this site
datewhirl.py found zero labels and zero inputs, and its fallback branch

    if "Thanks" in labels or (not labels and not inp_name):   # datewhirl.py:173

read "blank page" as "quiz finished", clicked nothing, and reported
`submitted` unconditionally. Every run from 2026-08-21 on logged
`Step1: labels= input=` and the log holds zero `step=success`.

Measured live on https://goldenagesouls.com/land/sp/<id>/ (mobile 440x956):

    1/19  Select your age              button[data-testid=age-18-24] …
    2/19  Your gender                  gender-female / gender-male
    3/19  Is your time your most…      image-button-yes / image-button-no,-i-take-my-time
    4/19  What brings you here today?  image-button-{just-some-fun,find-a-friend,romance,open-to-all}
    5/19  interstitial                 only continue-button ("Next") is visible
    6/19  Is ambition a must-have…     image-button-yes / image-button-no
    7/19  Can a conversation keep…     image-button-yes / image-button-no
    8/19  interstitial                 Next
    9/19  Who captures your interest?  pref-gender-male / pref-gender-female
   10/19  What makes a man beautiful…  image-button-{manners,energy,mindset,elegance}
   11/19  What catches your eye first? image-button-{softness,sense-of-style,the-eyes-&-gaze,overall-charisma}
   12/19  interstitial                 Next
   13/19  Where do you find your peace? image-button-{maldives,above-the-clouds}
   14/19  How do you prefer to express affection? image-button-{meaningful-details,grand-gestures}
   15/19  What defines your perfect weekend? image-button-{travel,socializing,art-&-culture,wellness}
   16/19  interstitial                 Next
   17/19  What's your educational background? image-button-{high-school,bachelor's-degree,master's-degree,doctorate}
   18/19  Which industry describes…    image-button-{business,tech,finance,medicine,real-estate,law}
   19/19  interstitial                 Next
    --    What can users call you?     input[data-testid=firstname]
    --    You're very close!           input[data-testid=email]
    --    Create a solid password     input[data-testid=password]
    --    Thanks for your answers!     Next
    SUCCESS: URL becomes https://goldenagesouls.com/wizard?referrer=…

Two mechanics matter
--------------------
1. Only the *current* question's options are `visibility: visible`; the rest
   sit in the DOM as `visibility: hidden`. offsetWidth>0 is NOT a visibility
   test here — it is true for hidden elements too. Walk the ancestor chain.
2. Answering a question removes it and reveals the next one in place, so the
   script is a loop, not a fixed step list.

Honesty
-------
The old failure mode was reporting success when the page could not be read.
This script reports `success` only on a URL-level signal, and `max_steps`
(otherwise) — never `submitted` for work it did not do.
"""

import argparse
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import CDPHelper, setup_logger, report_url  # noqa: E402

NAMES = ["James", "John", "Robert", "Michael", "David", "William",
         "Richard", "Joseph", "Thomas", "Daniel", "Paul", "Mark"]
EMAIL_DOMAINS = ["outlook.com", "gmail.com", "yahoo.com", "hotmail.com"]

# Question options carry these data-testid prefixes. Ordering/contents come
# from the live walk recorded in the module docstring.
OPTION_RE = (r"^(age-|gender-|pref-gender-|image-button-|option-|interest-|goal-)")
# "Under 18 - not eligible" — clicking it disqualifies the run.
SKIP_OPTIONS = {"age-under-18"}
# image-button-foo and image-button-foo-text are the same control; click the
# container, not the label inside it.
TEXT_SUFFIX = "-text"

COOKIE_BTN = '[data-testid="cookie-popup-common-agree-and-close"]'
NEXT_BTN = '[data-testid="continue-button"]'

# 「页面在忙」和「没事干」必须分开 —— 这是 2026-09-17 生产单 25997728 教的。
#
# 那一单答到 18/19 后进入 `Almost done / Processing your gender preferences /
# Setting up your profile`，**9 秒**没动静就被当成"没事干"放弃（连续 6 次阈值）。
# 同一个位置在同一天的测试里只停 **4 秒**就过去了 —— 阈值刚好卡在边界上，
# 环境一慢（真 profile、真代理、机器负载）就误杀。
#
# 这几段文案是站点**自己说的"我在处理"**，是真实的加载态，不是在偷懒。
# 给它一个**独立且长得多**的耐心，别和"真的什么都没得点"共用一套计数。
BUSY_MARKERS = ("Almost done", "Processing your", "Setting up your profile",
                "Just a moment", "getting things ready", "Hold on")
#: 页面处于已知处理态时最多等这么久（秒）
BUSY_TIMEOUT = 90
#: 真的没什么可点 / 页面读不出来，连续这么多次就收摊
IDLE_LIMIT = 6

# Success is a URL change, not page text — page text was how the old script
# fooled itself.
SUCCESS_URL_MARKERS = ("/wizard", "/news-feed")

# One page state in a single round trip: what can be clicked, what can be
# filled, and whether Next is showing.
READ_PAGE_JS = """
(function(){
  function vis(e){var n=e;while(n&&n.nodeType===1){var cs=getComputedStyle(n);
    if(cs.visibility==='hidden'||cs.display==='none')return false;n=n.parentElement;}
    return true;}
  var opt=[],ins=[],i,e,r,t;
  var es=document.querySelectorAll('[data-testid]');
  for(i=0;i<es.length;i++){e=es[i];t=e.getAttribute('data-testid');r=e.getBoundingClientRect();
    if(r.width<=0||r.height<=0||!vis(e))continue;
    if(/^(age-|gender-|pref-gender-|image-button-|option-|interest-|goal-)/.test(t)
       &&t.indexOf('-text')<0) opt.push(t);}
  var is=document.querySelectorAll('input');
  for(i=0;i<is.length;i++){e=is[i];r=e.getBoundingClientRect();
    if(r.width<=0||r.height<=0||!vis(e))continue;
    t=e.getAttribute('data-testid')||e.name||'';
    if(t&&t.indexOf('legal')<0&&t.indexOf('checkbox')<0) ins.push([t,e.value||'']);}
  var cb=document.querySelector('[data-testid="continue-button"]');
  var bt=document.body?document.body.innerText.replace(/\\s+/g,' ').trim():'';
  return JSON.stringify({url:location.href,
    progress:(bt.match(/\\d+\\s*\\/\\s*\\d+/)||[''])[0],
    head:bt.substring(0,120), options:opt, inputs:ins,
    nextVisible:!!(cb&&vis(cb))});
})()
"""


class GoldenAgeSoulsFiller:
    def __init__(self, ws_url, form_file, correlation_id, task_id=""):
        self.cdp = CDPHelper(ws_url)
        with open(form_file) as f:
            self.form_data = json.load(f)
        self.cid = correlation_id
        self.tid = task_id or correlation_id.split('_')[0]
        self.log = setup_logger('goldenagesouls')
        self._last_url = ""
        self._filled = set()

    # ---------- plumbing ----------

    def _dly(self, a=0.4, b=1.6):
        time.sleep(random.uniform(a, b))

    def _rpt(self, step):
        u = self.cdp.eval("window.location.href", "").strip().strip('"').strip("'")
        if u:
            self._last_url = u
        return report_url(self.cdp, self.tid, step, self.log)

    def read(self):
        """Current page state, or None when the page cannot be read."""
        raw = self.cdp.eval(READ_PAGE_JS, "").strip()
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except ValueError:
            return None
        # cdp eval returns the JSON string wrapped in another JSON layer.
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except ValueError:
                return None
        return data if isinstance(data, dict) else None

    def is_success(self, url):
        return any(m in (url or "") for m in SUCCESS_URL_MARKERS)

    # ---------- answers ----------

    def _profile(self):
        p = self.form_data
        first = (p.get("first_name")
                 or (p.get("username", "").split()[0] if p.get("username") else "")
                 or random.choice(NAMES))
        email = p.get("email") or "{}{}@{}".format(
            first.lower(), random.randint(100, 999), random.choice(EMAIL_DOMAINS))
        return first, email, "Pass{}!".format(random.randint(100, 999))

    def _value_for(self, testid, first, email):
        t = testid.lower()
        if "firstname" in t or t == "name":
            return first
        if "email" in t:
            return email
        if "password" in t:
            return "Pass{}!".format(random.randint(100, 999))
        if "birth" in t or "date" in t:
            return "05-14-1985"
        if "phone" in t or "tel" in t:
            return "5551234567"
        return ""

    def _pick_option(self, options):
        """Random allowed option; never 'Under 18 - not eligible'."""
        usable = [o for o in options if o not in SKIP_OPTIONS]
        return random.choice(usable) if usable else None

    # ---------- the loop ----------

    def run(self, max_steps=60):
        try:
            first, email, _ = self._profile()
            self.log.info("[%s] goldenagesouls: %s %s", self.cid, first, email)

            # Cookie banner covers the options on the landing page.
            try:
                self.cdp.click(COOKIE_BTN)
                self._dly(0.5, 1.2)
            except Exception:
                pass

            answered = 0
            idle = 0
            busy_since = None
            for step in range(1, max_steps + 1):
                self._dly(0.6, 1.8)
                page = self.read()
                if page is None:
                    idle += 1
                    self.log.info("[%s] step %d: page unreadable (%d)", self.cid, step, idle)
                    if idle >= IDLE_LIMIT:
                        self.log.error("[%s] page unreadable for %d steps — giving up honestly",
                                       self.cid, idle)
                        self._rpt("max_steps")
                        return False
                    continue

                url = page.get("url", "")
                if self.is_success(url):
                    self.log.info("[%s] SUCCESS: %s", self.cid, url[:90])
                    self._rpt("success")
                    return True

                # 1) fill whatever is empty and visible
                todo = [t for t, v in page.get("inputs", []) if t and not v]
                if todo:
                    tid = todo[0]
                    val = self._value_for(tid, first, email)
                    if val and (tid, val) not in self._filled:
                        self.cdp.form('[data-testid="%s"]' % tid, value=val)
                        self._filled.add((tid, val))
                        self.log.info("[%s] step %d: filled %s", self.cid, step, tid)
                        idle = 0
                        busy_since = None
                        continue

                # 2) answer the visible question
                pick = self._pick_option(page.get("options", []))
                if pick:
                    self.cdp.click('[data-testid="%s"]' % pick)
                    answered += 1
                    idle = 0
                    busy_since = None
                    self.log.info("[%s] step %d [%s] %s -> %s", self.cid, step,
                                  page.get("progress", "?"), page.get("head", "")[:52], pick)
                    continue

                # 3) interstitial / form page: press Next
                if page.get("nextVisible"):
                    self.cdp.click(NEXT_BTN)
                    idle = 0
                    busy_since = None
                    self.log.info("[%s] step %d [%s] Next  (%s)", self.cid, step,
                                  page.get("progress", "?"), page.get("head", "")[:52])
                    continue

                # 4) 没得点：先分清「页面在处理」还是「真的没事干」
                head = page.get("head", "") or ""
                if any(m.lower() in head.lower() for m in BUSY_MARKERS):
                    if busy_since is None:
                        busy_since = time.time()
                    waited = time.time() - busy_since
                    if waited >= BUSY_TIMEOUT:
                        self.log.error("[%s] 页面停在处理态 %.0f 秒没出来（%s）—— 如实报失败",
                                       self.cid, waited, head[:50])
                        self._rpt("max_steps")
                        return False
                    idle = 0            # 忙 ≠ 空转，不记 idle
                    self.log.info("[%s] step %d: 页面在处理中，继续等（已 %.0fs / 上限 %ds）%s",
                                  self.cid, step, waited, BUSY_TIMEOUT, head[:44])
                    continue

                # 5) 真的没事干 —— 短暂过渡可能，一直如此就收摊
                busy_since = None
                idle += 1
                self.log.info("[%s] step %d: nothing actionable (%d) %s", self.cid, step,
                              idle, head[:60])
                if idle >= IDLE_LIMIT:
                    self.log.error("[%s] stalled after %d answers at %s — reporting honestly",
                                   self.cid, answered, url[:90])
                    self._rpt("max_steps")
                    return False

            self.log.error("[%s] hit max_steps (%d answers) — reporting honestly",
                           self.cid, answered)
            self._rpt("max_steps")
            return False

        except Exception as e:
            self.log.error("[%s] Error: %s", self.cid, e)
            return False


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--ws-url", required=True)
    p.add_argument("--form-file", required=True)
    p.add_argument("--correlation-id", required=True)
    p.add_argument("--log-level", default="INFO")
    p.add_argument("--task-id", default="")
    a = p.parse_args()
    log = setup_logger('goldenagesouls')
    log.setLevel(a.log_level)
    f = GoldenAgeSoulsFiller(a.ws_url, a.form_file, a.correlation_id, a.task_id)
    sys.exit(0 if f.run() else 1)


if __name__ == "__main__":
    main()
