#!/usr/bin/env python3
"""Site script for lauradate.com dating signup.
Same platform as placetochat - all steps in DOM simultaneously.
Detect step via body text patterns, click options/buttons accordingly.
"""

import argparse, json, os, sys, time, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import CDPHelper, setup_logger, report_url


NAMES = ["James","John","Robert","Michael","David","Alex","Chris","Sam"]
EMAIL_DOMAINS = ["gmail.com","outlook.com","yahoo.com","hotmail.com"]


class LauraDateFiller:
    def __init__(self, ws_url, form_file, correlation_id, task_id=""):
        self.cdp = CDPHelper(ws_url)
        with open(form_file) as f: self.form_data = json.load(f)
        self.cid = correlation_id; self.tid = task_id or correlation_id.split('_')[0]
        self.log = setup_logger('lauradate'); self._last_url = ""

    def _rpt(self, s):
        u = self.cdp.eval("window.location.href","").strip().strip('"').strip("'")
        if u and u != self._last_url: self._last_url = u
        return report_url(self.cdp, self.tid, s, self.log)

    def _dly(self, a=0.3, b=1.5): time.sleep(random.uniform(a, b))

    def _ev(self, js):
        r = self.cdp.eval(f"(function(){{{js}}})()","")
        # Return full value (not just first line) for multi-line results
        lines = r.strip().split('\n')
        cleaned = []
        for l in lines:
            l = l.strip().strip('"').strip("'")
            if l and 'ERROR:' not in l and 'Usage:' not in l and 'Flags:' not in l:
                cleaned.append(l)
        result = '\n'.join(cleaned)
        # cdp eval returns literal \n (two chars), convert to real newlines
        return result.replace('\\n', '\n')

    def _click_btn(self, text):
        """Click button by text: JS finds it, CDP clicks it."""
        escaped = text.replace("'", "\\'")
        found = self._ev(
            f"var bs=document.querySelectorAll('button');"
            f"for(var i=0;i<bs.length;i++){{"
            f"if(bs[i].offsetWidth>0&&bs[i].textContent.trim().toLowerCase().indexOf('{escaped}')!==-1)"
            f"{{bs[i].setAttribute('data-click-target','x');return'found';}}}}return'none';")
        if found == 'found':
            try:
                self.cdp.click('[data-click-target="x"]')
                self._ev('var b=document.querySelector("[data-click-target]");if(b)b.removeAttribute("data-click-target");')
                return 'clicked'
            except Exception:
                self._ev('var b=document.querySelector("[data-click-target]");if(b)b.removeAttribute("data-click-target");')
        return 'none'

    def _click_option_by_class(self, cls):
        """JS finds element (random, skip Under 18), CDP clicks it."""
        r = random.randint(0, 999)
        found = self._ev(
            f"var o=document.querySelectorAll('.{cls}');var opts=[];"
            f"for(var i=0;i<o.length;i++){{if(o[i].offsetWidth>0&&o[i].textContent.indexOf('Under 18')===-1)opts.push(i);}}"
            f"if(opts.length>0){{o[opts[{r}%opts.length]].setAttribute('data-click-target','x');return'found';}}return'none';")
        if found == 'found':
            try:
                self.cdp.click('[data-click-target="x"]')
                self._ev('var b=document.querySelector("[data-click-target]");if(b)b.removeAttribute("data-click-target");')
                return 'clicked'
            except Exception:
                self._ev('var b=document.querySelector("[data-click-target]");if(b)b.removeAttribute("data-click-target");')
        return 'none'

    def run(self):
        try:
            p = self.form_data
            fn = (p.get("first_name") or (p.get("username","").split()[0] if p.get("username") else "")) or random.choice(NAMES)
            domain = random.choice(EMAIL_DOMAINS)
            email = p.get("email","") or f"{fn.lower()}{random.randint(100,999)}@{domain}"
            pw = f"Pass{random.randint(100,999)}!"
            self.log.info(f"[{self.cid}] LauraDate: {fn} {email}")

            self._dly(1, 3)
            self._ev("window.scrollBy("+str(random.randint(200,500))+");"); self._dly(0.5, 1.5)

            # Dismiss cookie
            self._ev("var b=document.querySelector('#cc-main .cm__btns button');if(b&&b.offsetWidth>0){b.click();}")

            name_done = False; email_done = False; pw_done = False
            last_body = ""; stuck = 0

            for _ in range(80):
                self._dly(0.5, 1.5)
                body = self._ev("return document.body?document.body.innerText.substring(0,800):'';")
                url = self._ev("return window.location.href;")
                bl = body.lower()
                self.log.info(f"[{self.cid}] body={body[:80]}")

                # --- Success ---
                if any(m in body for m in ["welcome credits","Explore now","Getting things ready"]):
                    self._dly(1, 3)
                    self._click_btn("explore")
                    self._rpt("success"); return True
                if "/wizard/" in url or "/main-page" in url:
                    self._rpt("success"); return True

                # Stuck detection
                if body == last_body: stuck += 1
                else: stuck = 0
                last_body = body

                # --- Step: Welcome ---
                if "let me see it" in bl or "3-minute quiz" in bl:
                    self._click_btn("let me see")
                    self._dly(2, 4)
                    continue

                # --- Step: Yes/No (sc-tropic-button) ---
                yes_no_qs = [
                    "small talk", "fulfill their wishes", "late-night",
                    "texts first", "highlight verified", "do you like",
                    "don’t be surprised"
                ]
                if any(m in bl for m in yes_no_qs):
                    result = self._click_option_by_class("sc-tropic-button")
                    self._dly(1, 3)
                    continue

                # --- Step: Multi-choice (.sc-option visible) ---
                multi_choice = [
                    "so, you", "select your sex", "who can we help",
                    "your age", "how’s your mood", "how’s your mood",
                    "are you ready to respect",
                    "is it ok if users", "would you like to receive",
                    "are you ready for lots", "are you up to join"
                ]
                if any(m in bl for m in multi_choice):
                    self._click_option_by_class("sc-option")
                    self._dly(1, 3)
                    continue

                # --- Step: Name input ---
                if any(m in bl for m in ["call you", "nickname", "what is your name", "your partner call"]) and not name_done:
                    self.cdp.form("input", value=fn)
                    name_done = True
                    self._dly(0.5, 1.5)
                    self._click_btn("next")
                    self._dly(3, 6)
                    continue

                # --- Step: Email input ---
                if any(m in bl for m in ["enter your email", "leave your email", "email address"]) and not email_done:
                    self.cdp.form("input", value=email)
                    email_done = True
                    self._dly(0.5, 1.5)
                    self._click_btn("next")
                    self._dly(3, 6)
                    continue

                # --- Step: Password input ---
                if "password" in bl and not pw_done:
                    self.cdp.form("input", value=pw)
                    pw_done = True
                    self._dly(0.5, 1.5)
                    self._click_btn("next")
                    self._dly(3, 6)
                    continue

                # --- Step: Terms "I Agree" ---
                if "i agree" in bl or "by clicking" in bl:
                    self._click_btn("i agree")
                    self._rpt("submitted")
                    self._dly(5, 10)
                    continue

                # --- Info/transition pages (only after options are handled) ---
                # Also handle post-name "are you ready to make the first step" page
                if "final touch" in bl or "make the first step" in bl:
                    self._click_btn("next")
                    self._dly(2, 4)
                    continue
                for bt in ["finish up", "next", "continue"]:
                    if self._click_btn(bt) == 'clicked':
                        break
                self._dly(2, 4)

                # Stuck escape
                if stuck >= 5:
                    for bt in ["next", "continue", "finish up", "i agree"]:
                        self._click_btn(bt)
                    stuck = 0
                    self._dly(3, 6)

            # Post-loop
            for _ in range(8):
                self._dly(1, 3)
                body = self._ev("return document.body?document.body.innerText.substring(0,800):'';")
                url = self._ev("return window.location.href;")
                if "welcome credits" in body:
                    self._click_btn("explore")
                    self._dly(3, 6)
                if "/wizard/" in url or "/main-page" in url:
                    self._rpt("success"); return True
            self._rpt("max_steps"); return False

        except Exception as e:
            self.log.error(f"[{self.cid}] Error: {e}")
            return False


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--ws-url", required=True); p.add_argument("--form-file", required=True)
    p.add_argument("--correlation-id", required=True); p.add_argument("--log-level", default="INFO")
    p.add_argument("--task-id", default=""); a = p.parse_args()
    log = setup_logger('lauradate'); log.setLevel(a.log_level)
    f = LauraDateFiller(a.ws_url, a.form_file, a.correlation_id, a.task_id)
    sys.exit(0 if f.run() else 1)

if __name__ == "__main__": main()
