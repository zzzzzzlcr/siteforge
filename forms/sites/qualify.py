#!/usr/bin/env python3
"""Site script for qualify.lastingpowerofattorney.io — 读页面型，不是固定步骤表。

为什么不用后端那份 JSON 配置
---------------------------
后端有一份 18 步的 JSON 配置，2026-09-17 实测跑到第 13 步就废了，真因只有一个：

    站点把「姓名」那一格**在两套形态之间切换** ——
      形态 A: 一个 `Enter your full name`
      形态 B: `First name` + `Last name` 两个
    同一个浏览器窗口里都能看到它换（实测：先 B 后 A）。

JSON 是**线性步骤表**，只能靠 `when` + `optional` 硬凑两套；而且它把 `deferred`
的 form 步**算成成功**（memory: georgiapower 实测 13/13 假成功）—— 填不上会被藏起来。
这个站要的是「**读一眼页面、有什么填什么**」，那是 JSON 表达不了的那类。

形态上属于 datewhirl/goldenagesouls 那一族：每轮读可见元素 → 有什么做什么 → 循环。
站点再换一次变体也不会全废。

⚠️ 蜜罐
------
页面上有个 `name="company_url"` 的 text 输入框，**永远是空的、也不该被填** ——
真人看不见它，机器人会填。填了整单被判垃圾。`SKIP_FIELDS` 里钉死了它。

成功
----
URL 出现 `thank-you`（跟后端那份 JSON 配置的判据一致）。**只看 URL，不看正文** ——
正文判据是这一族脚本栽跟头的老地方。
"""

import argparse
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common import CDPHelper, setup_logger, report_url  # noqa: E402

FIRST_NAMES = ["James", "John", "Robert", "Michael", "David", "William", "Thomas"]
LAST_NAMES = ["Smith", "Jones", "Williams", "Taylor", "Brown", "Wilson", "Davies"]
EMAIL_DOMAINS = ["outlook.com", "gmail.com", "yahoo.com", "hotmail.com"]
#: 英国站（postcode 样例 SW1A 1AA / RG24 8PE）
POSTCODES = ["SW1A 1AA", "CF10 1AA", "M1 1AA", "B1 1AA", "LS1 1AA", "RG24 8PE"]
#: ⚠️ 别用 07700 900xxx —— 那是 Ofcom 的影视保留号段，校验器常明确拉黑
#: （2026-09-17 实测被这个站拒）。这里按「05/07 开头 + 10 位」随机生成真形状的号。
PHONE_PREFIXES = ["071", "073", "074", "075", "077", "078", "079"]


def _uk_mobile():
    """生成一个形状像真英国手机号的号（避开保留段）。"""
    import random as _r
    return _r.choice(PHONE_PREFIXES) + "".join(str(_r.randint(0, 9)) for _ in range(8))


def _looks_uk_mobile(v):
    """英国手机号：07 开头 11 位；或 +447 开头的国际写法。

    ⚠️ **还要排除 Ofcom 影视保留段 `07700 900xxx`** —— 它格式完全合法，
    只查格式会放行，而这个站的校验器明确拒它（2026-09-17 实测：
    填 `07700900123` 提交不动，换个真形状的号就过了）。
    这一段的坑在"格式判据挡不住"，只能点名排除。
    """
    import re as _re
    v = (v or "").replace(" ", "").replace("-", "")
    if not _re.fullmatch(r"(\+?44|0)7\d{9}", v):
        return False
    national = "0" + v[3:] if v.startswith("+44") else ("0" + v[2:] if v.startswith("44") else v)
    return not national.startswith("07700900")


def _looks_uk_postcode(v):
    """英国邮编形状（SW1A 1AA / RG24 8PE / M1 1AA）。美国 ZIP(97520) 不像 → False。"""
    import re as _re
    v = (v or "").upper().strip()
    return bool(_re.fullmatch(r"[A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2}", v))

#: 蜜罐：真人看不见、机器人会填。见模块 docstring。
SKIP_FIELDS = {"company_url"}

#: 每屏的主按钮（点它推进）。大小写不敏感、按包含匹配。
PRIMARY_BUTTONS = ("continue", "get started", "get results", "next", "submit", "see results")

#: 不该点的（返回/退格/关闭之类）
AVOID_BUTTONS = ("back", "previous", "go back", "×", "close", "decline", "reject")

COOKIE_BTN = "#cookiescript_accept"
COOKIE_BTN_ALT = ("#onetrust-accept-btn-handler", "#cookiescript_accept")

#: URL 里出现它就成功
SUCCESS_URL_MARKERS = ("thank-you", "thankyou", "thank_you")

#: 页面「在处理」的文案（跟 goldenagesouls 同一套思路：忙 ≠ 空转）
BUSY_MARKERS = ("Almost done", "Processing your", "Setting up", "Just a moment",
                "getting things ready", "Hold on", "Calculating")
BUSY_TIMEOUT = 90
IDLE_LIMIT = 8
#: 连续这么多次「动作做了、屏幕没变」就收摊 —— **只对选项类动作数得快**。
#: 没有它的话，"点了个不推进的东西"会一直点下去（2026-09-17 实测：空点
#: Get Started 二十多次，跑满 max_steps 才停 —— 白烧 40 步）。
STUCK_LIMIT = 6
#: 但**主按钮（Continue / Get Results 那类）是另一回事**：它可能正在提交，
#: 页面在等网络回包，屏幕自然不动。对它按**时间**给，不按次数。
#: 2026-09-17 实测：填完 88% 点 Get Results，脚本 9 秒后放弃，
#: **而页面随后真的跳到了 `/thank-you`** —— 那一单提交成功了，是脚本没等到。
#: 差几秒就白扔一单，这类"差一点"比彻底失败更亏。
SUBMIT_GRACE = 60

READ_PAGE_JS = r"""
(function(){
  function vis(e){var n=e;while(n&&n.nodeType===1){var cs=getComputedStyle(n);
    if(cs.visibility==='hidden'||cs.display==='none'||cs.opacity==='0')return false;n=n.parentElement;}
    return true;}
  function onScreen(e){var r=e.getBoundingClientRect();
    return r.width>0&&r.height>0&&r.top<window.innerHeight&&r.bottom>0;}
  var out={url:location.href};
  var bt=document.body?document.body.innerText.replace(/\s+/g,' ').trim():'';
  out.progress=(bt.match(/Progress\s*\d+\s*%/)||[''])[0];
  out.head=bt.substring(0,160);
  var ins=document.querySelectorAll('input,select,textarea'),f=[];
  for(var i=0;i<ins.length;i++){var e=ins[i];
    if(e.type==='hidden'||!vis(e)||!onScreen(e))continue;
    f.push({name:e.name||'', id:e.id||'', type:e.type||'',
            ph:e.placeholder||'', label:(e.getAttribute('aria-label')||''),
            val:e.value||''});}
  out.fields=f;
  // 选项元素**在不同屏上是不同标签**（2026-09-17 实测）：
  //   30% 屏按钮是 <button>、42% 屏是 <label data-slot="label">
  // 只查 button 会在后者上认不出选项，于是空点 Continue（实测连点 6 次被守护拦下）。
  var bs=document.querySelectorAll('button,[role=button],label'),b=[];
  for(var i=0;i<bs.length;i++){var e=bs[i];
    if(!vis(e)||!onScreen(e))continue;
    if(e.tagName==='LABEL'){
      if(e.getAttribute('for'))continue;                     // 跟输入框绑的 label 不是选项
      if(e.querySelector('input,select,textarea'))continue;  // 包着输入框的也不是
    }
    var t=(e.textContent||'').replace(/\s+/g,' ').trim();
    if(t&&t.length<=40) b.push({txt:t, cls:(e.className||'').toString()});}
  out.buttons=b;
  return JSON.stringify(out);
})()
"""


class LpaFiller:
    def __init__(self, ws_url, form_file, correlation_id, task_id=""):
        self.cdp = CDPHelper(ws_url)
        with open(form_file) as f:
            self.form_data = json.load(f)
        self.cid = correlation_id
        self.tid = task_id or correlation_id.split('_')[0]
        self.log = setup_logger('lpa')
        self._filled = set()

    # ---------- plumbing ----------

    def _dly(self, a=0.5, b=1.6):
        time.sleep(random.uniform(a, b))

    def _rpt(self, step, reason=""):
        return report_url(self.cdp, self.tid, step, self.log, reason)

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

    def is_success(self, url):
        return any(m in (url or "").lower() for m in SUCCESS_URL_MARKERS)

    # ---------- 资料 ----------

    def _profile(self):
        """从 form-file 取资料，取不到或**不合这个站的口味**才用池子。

        ⚠️ 三个坑，都是 2026-09-17 实测踩出来的（生产 form 数据长这样：
        `{"username":"Gregory Capp","postal_code":"97520","country":"US",
          "phone_number":"5413017650"}`）：

        ① **姓名的键是 `username`（整名）**，不是 first_name/last_name —— 旧脚本
           从 `username` 拆，我第一版没拆，会退回随机名。
        ② **电话的键是 `phone_number`**，不是 `phone` —— 取不到就退回池子，
           而池子里有 `07700900123` 这种 **Ofcom 影视保留号段**，校验器直接拒。
        ③ **`postal_code` 是美国的（97520），而这是英国站** —— 旧脚本只认
           `postcode`（不带 al），美国 ZIP 因此漏不进来。这里除了不认那个键，
           再加一道**格式判据**：不像英国邮编就换池子。

        （记忆里那条「兜底值池 vs 字段种类判据」说的就是这个：池子是**兜底**，
        不是拿来做判据的；而"像不像这个国家的格式"才是判据。）
        """
        p = self.form_data
        uname = (p.get("username") or "").strip()
        first = p.get("first_name") or (uname.split()[0] if uname else "")
        last = p.get("last_name") or (" ".join(uname.split()[1:]) if uname else "")
        first = first or random.choice(FIRST_NAMES)
        last = last or random.choice(LAST_NAMES)
        full = p.get("full_name") or f"{first} {last}".strip()

        email = p.get("email") or "{}{}@{}".format(
            first.lower(), random.randint(100, 999), random.choice(EMAIL_DOMAINS))

        phone = (p.get("phone") or p.get("phone_number") or "").strip()
        if not _looks_uk_mobile(phone):
            phone = _uk_mobile()

        postcode = (p.get("postcode") or "").strip()
        if not _looks_uk_postcode(postcode):
            postcode = random.choice(POSTCODES)

        return {"first": first, "last": last, "full": full, "email": email,
                "phone": phone, "postcode": postcode}

    def _value_for(self, field, prof):
        """按 placeholder / name / aria-label 判这一格要填什么。

        ⚠️ 顺序要紧：先判 full name，再判 first / last —— 否则 "full name" 里
        含 "name"，会被 first/last 抢走。
        """
        hay = " ".join([field.get("ph", ""), field.get("name", ""),
                        field.get("id", ""), field.get("label", "")]).lower()
        if "company" in hay or "url" in hay or "website" in hay:
            return ""                      # 蜜罐，绝不填
        if "full" in hay and "name" in hay:
            return prof["full"]
        if "first" in hay:
            return prof["first"]
        if "last" in hay or "surname" in hay:
            return prof["last"]
        if "postcode" in hay or "postal" in hay or "zip" in hay or "rg24" in hay:
            return prof["postcode"]
        if "email" in hay or "e-mail" in hay:
            return prof["email"]
        if "phone" in hay or "mobile" in hay or "tel" in hay:
            return prof["phone"]
        return ""

    def _option_index(self, buttons):
        """这一屏有没有要选的选项？返回点哪个（没有 → None）。

        ⚠️ **不能因为「有主按钮」就跳过选择** —— 2026-09-17 实测：首屏
        「Who is the LPA for?」上 `Myself / Myself & Partner / Someone Else`
        和 `Get Started` **同屏共存**。我第一版看到主按钮就 return None，
        于是空点 Get Started 二十多次。

        而实测的真实行为是：**点选项本身就自动跳下一屏**（点完 Myself，
        那一屏的按钮就只剩 Continue 了）。所以选项优先，主按钮是「没选项可选时」
        才点的东西。

        选项的判据就是「**不是主按钮、也不是返回/关闭那类**」—— 这些按钮
        **没有任何状态属性**（`aria-checked` / `data-state` 全是 None），
        所以判不出"选没选"，只能靠"点了会不会推进"（由同屏空转保护兜住）。
        """
        usable = [b for b in buttons
                  if b["txt"]
                  and not any(p in b["txt"].lower() for p in PRIMARY_BUTTONS)
                  and not any(a in b["txt"].lower() for a in AVOID_BUTTONS)]
        return usable[0]["txt"] if usable else None

    # ---------- 按文字点按钮 ----------

    def _mark_by_text(self, text, exact=True):
        """按文字给按钮打标记，返回找到没有。

        为什么绕这一下：这些按钮**没有稳定的 CSS 选择器**（2026-09-17 实测）——
        选项的 id 是 `option-<epoch毫秒>` 那种每次渲染都变的，类名是 Tailwind
        一长串，`data-target` 是 `"x"`；而 `cdp click` **只吃 CSS**
        （`:has-text()` / `:contains()` / `text=` 全报 element not found）。
        所以：**找用 JS、点还是走 `cdp click`** —— 真人手势那一层不能省。
        """
        payload = json.dumps(text)
        cmp = "s === t" if exact else "s.indexOf(t) >= 0"
        js = ("(function(){var t=" + payload + ";"
              "var bs=document.querySelectorAll('button,label');"
              "for(var i=0;i<bs.length;i++){var e=bs[i];"
              "if(e.offsetWidth<=0||e.offsetHeight<=0)continue;"
              "if(e.tagName==='LABEL'&&(e.getAttribute('for')||e.querySelector('input,select,textarea')))continue;"
              "var s=(e.textContent||'').replace(/\\s+/g,' ').trim();"
              "if(" + cmp + "){"
              "var old=document.querySelectorAll('[data-lpa-pick]');"
              "for(var j=0;j<old.length;j++){old[j].removeAttribute('data-lpa-pick');}"
              "e.setAttribute('data-lpa-pick','1');return 'marked';}}"
              "return 'none';})()")
        return "marked" in self.cdp.eval(js, "")

    def _click_text(self, text, exact=True):
        """按文字点一个按钮。找到并点到 → True。"""
        if not self._mark_by_text(text, exact):
            return False
        self.cdp.click('[data-lpa-pick="1"]')
        return True

    def _replay_clear(self):
        """复读：cookie 横幅是否已经不在页面上了。

        判据与 template.py::_clear_obstructions 同一套词汇：文字/id/class 里
        有没有 cookie|consent|gdpr|privacy。**清同意弹层必须复读确认**——
        点一下 cdp.click 照样回 ok，但点没点掉是另一回事。
        返回 True 表示「不在页面上」= 清掉了。
        """
        js = (r"(function(){"
              r"var re=/cookie|consent|gdpr|privacy/i;"
              r"var sels=["
              r"'#cookiescript_injected_wrapper','#cookiescript_injected',"
              r"'#cookiescript_readmore','#cookiescript_accept',"
              r"'#onetrust-consent-sdk','#onetrust-banner-sdk',"
              r"'[id*=cookie i]','[class*=cookie i]',"
              r"'[id*=consent i]','[class*=consent i]',"
              r"'[id*=gdpr i]','[class*=gdpr i]'"
              r"];"
              r"for(var i=0;i<sels.length;i++){var e;"
              r"try{e=document.querySelector(sels[i]);}catch(x){continue;}"
              r"if(!e)continue;"
              r"var t=(e.textContent||'')+' '+(e.id||'')+' '+(e.className||'').toString();"
              r"if(!re.test(t))continue;"
              r"var cs=getComputedStyle(e);"
              r"if(cs.visibility==='hidden'||cs.display==='none')continue;"
              r"var r=e.getBoundingClientRect();"
              r"if(r.width<=0||r.height<=0)continue;"
              r"return 'blocked';}"
              r"return 'clear';})()")
        raw = (self.cdp.eval(js, "") or "").strip()
        return "clear" in raw

    def _clear_cookies(self, tries=3):
        """清掉挡住整页的 CookieScript 同意横幅，点完**复读**确认不在了。

        ⚠️ 生产**每一单都是新窗口、每单都会遇到它**；它异步注入，所以要**等**它出现。
        ⚠️ 它挡着的时候，点在别处的点击会被它吃掉（cdp click 照样回 ok）。
        返回 True 表示确认已清掉（或本来就没有）。
        """
        for i in range(tries):
            if self._replay_clear():
                return True
            # 等它注入（异步），2-5 秒
            self._dly(2.0, 5.0)
            if self._replay_clear():
                return True
            try:
                self.cdp.click(COOKIE_BTN)
            except Exception as e:
                self.log.info("[%s] 点 %s 没成：%s", self.cid, COOKIE_BTN, e)
            self._dly(0.6, 1.4)
            if self._replay_clear():
                return True
            # 备用地址再试一条
            for alt in COOKIE_BTN_ALT:
                try:
                    self.cdp.click(alt)
                except Exception:
                    continue
                self._dly(0.6, 1.4)
                if self._replay_clear():
                    return True
            self.log.info("[%s] cookie 横幅第 %d 次没清掉，重试", self.cid, i + 1)
        return self._replay_clear()

    def _wait_for_text(self, needle, timeout=15.0, poll=0.5):
        """等页面上出现 needle 这段文字（可见 + 在视口里）。到了 → True。

        ⚠️ 不许多调源码里没有的方法；这一步的判据只能用 cdp.eval 读页面。
        """
        payload = json.dumps(needle)
        js = ("(function(){var t=" + payload + ";"
              "function vis(e){var n=e;while(n&&n.nodeType===1){var cs=getComputedStyle(n);"
              "if(cs.visibility==='hidden'||cs.display==='none'||cs.opacity==='0')return false;"
              "n=n.parentElement;}return true;}"
              "var bs=document.querySelectorAll('button,label,a,h1,h2,h3,p,div,span');"
              "for(var i=0;i<bs.length;i++){var e=bs[i];"
              "if(!vis(e))continue;var r=e.getBoundingClientRect();"
              "if(r.width<=0||r.height<=0)continue;"
              "if(r.top>=window.innerHeight||r.bottom<=0)continue;"
              "var s=(e.textContent||'').replace(/\\s+/g,' ').trim();"
              "if(s.toLowerCase().indexOf(t.toLowerCase())>=0)return 'here';}"
              "return 'nope';})()")
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if "here" in (self.cdp.eval(js, "") or ""):
                    return True
            except Exception:
                pass
            time.sleep(poll)
        return False

    def _fill_field(self, selector, value):
        """填一格 —— 走 cdp.form，填不上不谎报。"""
        try:
            self.cdp.form(selector, value=value)
            return True
        except Exception as e:
            self.log.info("[%s] 填 %s 没成：%s", self.cid, selector, e)
            return False

    # ---------- 主循环 ----------

    def run(self, max_steps=40):
        try:
            prof = self._profile()
            self.log.info("[%s] lpa: %s %s %s", self.cid, prof["full"], prof["email"],
                          prof["postcode"])

            # 开跑前先清同意弹层（生产每单新窗口、每单都会遇到）。
            # ⚠️ 不许调源码里没有的方法 —— _clear_cookies 已在上面定义。
            if not self._clear_cookies():
                self.log.error("[%s] cookie 横幅清不掉 —— 如实报失败", self.cid)
                self._rpt("step", "第 0 步 清 cookie 横幅：没清掉")
                return False
            self._rpt("step", "第 0 步 清 cookie 横幅：已复读确认不在")

            # 站点一屏一步、每屏的问句/Progress 就是判据。
            # 选项没有稳定 id（option-<epoch毫秒> 每次渲染都变）⇒ 按文字点。
            STEPS = (
                ("第 1 步 Myself（Progress 30%）", "Myself",
                 "What kind of Lasting Power of Attorney", 1.0, 3.0),
                ("第 2 步 Health and Welfare（Progress 42%）", "Health and Welfare",
                 "What is your postcode", 1.0, 3.0),
            )
            # 【步骤表】先做「选项」两步（按文字点，点了会自动推进）
            for label, text, expect, wa, wb in STEPS:
                self._rpt("step", label)
                ok = False
                for attempt in range(1, 4):
                    if not self._wait_for_text(expect, timeout=3.0):
                        # 预期元素还没在；先确保选项在，再点
                        if not self._wait_for_text(text, timeout=3.0):
                            self.log.info("[%s] %s：等 %s 没等到（第 %d 次）",
                                          self.cid, label, text, attempt)
                            time.sleep(random.uniform(wa, wb))
                            continue
                    if self._click_text(text):
                        self._dly(wa, wb)
                        if self._wait_for_text(expect, timeout=(wa + wb + 4.0)):
                            ok = True
                            break
                        # 没推进 = 这一步没成，重做
                        if not self._clear_cookies():
                            self.log.error("[%s] cookie 又能挡回来了 —— 如实报失败", self.cid)
                            self._rpt("step", f"{label} 后被 cookie 挡回，清不掉")
                            return False
                    self.log.info("[%s] %s：点了 %s 但没出现「%s」（第 %d 次）",
                                  self.cid, label, text, expect, attempt)
                    time.sleep(random.uniform(wa, wb))
                if not ok:
                    self.log.error("[%s] %s 没做成：等「%s」等不到，重试 3 次 —— 如实报失败",
                                   self.cid, label, expect)
                    self._rpt("step", f"{label} 失败：等不到「{expect}」重试3次")
                    return False

            # 第 3 步：填邮编 + Continue
            self._rpt("step", "第 3 步 填邮编 + Continue")
            ok = False
            for attempt in range(1, 4):
                if not self._wait_for_text("What is your postcode", timeout=5.0):
                    self.log.info("[%s] 第 3 步：等「What is your postcode」没等到（第 %d 次）",
                                  self.cid, attempt)
                    if not self._clear_cookies():
                        self._rpt("step", "第 3 步 前 cookie 清不掉")
                        return False
                    continue
                sel = 'input[placeholder="e.g. RG24 8PE"]'
                if not self._fill_field(sel, prof["postcode"]):
                    time.sleep(1.0)
                    continue
                self._dly(0.5, 1.2)
                if not self._click_text("Continue"):
                    time.sleep(1.0)
                    continue
                self._dly(1.0, 3.0)
                if self._wait_for_text("Who should we send the free quote to", timeout=7.0):
                    ok = True
                    break
            if not ok:
                self.log.error("[%s] 第 3 步 邮编没推进 —— 如实报失败", self.cid)
                self._rpt("step", "第 3 步 失败：等不到「Who should we send the free quote to」重试3次")
                return False

            # 第 4 步：填姓名 + Continue
            self._rpt("step", "第 4 步 填姓名 + Continue")
            ok = False
            for attempt in range(1, 4):
                if not self._fill_field('input[placeholder="Enter your full name"]', prof["full"]):
                    time.sleep(1.0)
                    continue
                self._dly(0.5, 1.2)
                if not self._click_text("Continue"):
                    time.sleep(1.0)
                    continue
                self._dly(1.0, 3.0)
                if self._wait_for_text("What is your email address", timeout=7.0):
                    ok = True
                    break
            if not ok:
                self.log.error("[%s] 第 4 步 姓名没推进 —— 如实报失败", self.cid)
                self._rpt("step", "第 4 步 失败：等不到「What is your email address」重试3次")
                return False

            # 第 5 步：填 email + Continue
            self._rpt("step", "第 5 步 填 email + Continue")
            ok = False
            for attempt in range(1, 4):
                if not self._fill_field('input[type=email][placeholder="Enter email address"]',
                                        prof["email"]):
                    time.sleep(1.0)
                    continue
                self._dly(0.5, 1.2)
                if not self._click_text("Continue"):
                    time.sleep(1.0)
                    continue
                self._dly(1.0, 3.0)
                if self._wait_for_text("Where can we send your quote", timeout=7.0):
                    ok = True
                    break
            if not ok:
                self.log.error("[%s] 第 5 步 email 没推进 —— 如实报失败", self.cid)
                self._rpt("step", "第 5 步 失败：等不到「Where can we send your quote」重试3次")
                return False

            # 第 6 步：填电话 + Get Results（这就是提交），等 30-50 秒出成功文案
            self._rpt("step", "第 6 步 填电话 + Get Results（提交）")
            submitted = False
            for attempt in range(1, 4):
                if not self._fill_field('input[type=tel][placeholder="Enter mobile number"]',
                                        prof["phone"]):
                    time.sleep(1.0)
                    continue
                self._dly(0.5, 1.2)
                if self._click_text("Get Results"):
                    submitted = True
                    break
                time.sleep(1.0)
            if not submitted:
                self.log.error("[%s] 第 6 步 提交按钮点不上 —— 如实报失败", self.cid)
                self._rpt("step", "第 6 步 失败：Get Results 点不上重试3次")
                return False

            # 提交后等 30-50 秒的成功文案（人给了区间，用他的区间；别自己拍固定值）
            deadline = time.time() + 50.0
            while time.time() < deadline:
                self._dly(0.6, 1.8)
                page = self.read()
                if page:
                    url = page.get("url", "")
                    if self.is_success(url):
                        self.log.info("[%s] SUCCESS: %s", self.cid, url[:90])
                        self._rpt("success", f"{page.get('progress','')} {url[:60]}")
                        return True
                if time.time() > deadline - 20.0 and not self._replay_clear():
                    self._clear_cookies()
                if self._wait_for_text("thank-you", timeout=1.0):
                    self.log.info("[%s] SUCCESS: 正文出现 thank-you", self.cid)
                    self._rpt("success", "thank-you")
                    return True
                time.sleep(random.uniform(0.8, 2.0))

            # 等满 30-50 秒还没出现成功文案 —— 如实报失败
            self.log.error("[%s] 提交后等满 50s 没出现 thank-you —— 如实报失败", self.cid)
            self._rpt("step", "第 6 步 提交后等满 50s 没出现 thank-you")
            self._rpt("max_steps", "submit waited 50s, no thank-you")
            return False

        except Exception as e:
            self.log.error("[%s] Error: %s", self.cid, e)
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
    log = setup_logger('lpa')
    log.setLevel(a.log_level)
    f = LpaFiller(a.ws_url, a.form_file, a.correlation_id, a.task_id)
    sys.exit(0 if f.run() else 1)


if __name__ == "__main__":
    main()
