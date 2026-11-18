#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第五趟：走到账本 -3 组那一屏（CONTINUE ← BACK，无 ul），点 Continue，再看后面每一屏的 li。

已经量到的（第 4 趟）：
  Q1 题面 What's your main health goal right now?  14 个 li，**个个有字**
  Q2 题面 What other health goal would you like to focus on?  9 个 li，个个有字
  Q3 题面 How old are you?  5 个 li，个个有字
  Q4 ← **就是账本 -3 组的 when 那一屏**（page_text 里正是 "CONTINUE ← BACK Community Community
        Sign up to"）：只有两颗按钮，**一个 ul/li 都没有**
        · Continue = `#octane-quiz-wrapper > div:nth-of-type(2) > button:nth-of-type(1)`（账本 -3 第 1 步那颗）
        · ← BACK  = `#octane-quiz-wrapper > button:nth-of-type(1)`

这一趟：点那颗 Continue → 一屏一屏往下，每屏把 ul 里每个 li 的
`text / aria_label / role / stability / nearby_text / 属性` 逐条打出来。
账本 -3 第 2/3/4 步（li3 / li4 / li1）就是这些屏上的。

不填邮箱、不点 submit（看见 email 输入框就停）。窗关在 finally 里。
"""
import json
import os
import re
import subprocess
import time
import urllib.request

WORKER = "192.168.1.222"
BIT_ID = "982d158b41b14fbb87754667859b4719"
API = "http://%s:54345" % WORKER
CDP = "/opt/skills/auto-farm-skill/cdp"
OUT = os.path.dirname(os.path.abspath(__file__))
URL_QUIZ = "https://www.wildnutrition.com/pages/what-supplements-should-i-take"
UL = "#octane-quiz-wrapper > div:nth-of-type(1) > div:nth-of-type(3) > ul:nth-of-type(1)"
CONTINUE = "#octane-quiz-wrapper > div:nth-of-type(2) > button:nth-of-type(1)"

OPEN_ARGS = ["--remote-debugging-address=0.0.0.0", "--remote-debugging-port=61129",
             "--remote-allow-origins=*", "--disable-session-crashed-bubble"]
LOOPBACK = re.compile(r"(\[::1\]|localhost|127\.0\.0\.1|0\.0\.0\.0)")
RATE_MARKS = ("降低接口请求频率", "接口请求频率", "请求频率")
HOST = PORT = None


def log(*a):
    print(*a, flush=True)


def post(path, body, timeout=25):
    req = urllib.request.Request(API + path, data=json.dumps(body).encode("utf-8"),
                                 method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def probe_alive():
    try:
        out = post("/browser/pids/alive", {"ids": [BIT_ID]})
    except Exception:
        return "?"
    data = out.get("data") if isinstance(out, dict) else None
    return int(data[BIT_ID]) if isinstance(data, dict) and data.get(BIT_ID) else None


def _open():
    said = ""
    for attempt in range(3):
        out = post("/browser/open", {"id": BIT_ID, "args": list(OPEN_ARGS), "queue": True}, timeout=70)
        data = out.get("data") if isinstance(out, dict) else None
        ws = (data or {}).get("ws") if isinstance(data, dict) else None
        if isinstance(out, dict) and out.get("success") is True and ws:
            return LOOPBACK.sub(WORKER, str(ws))
        said = str(out)[:200]
        if not any(m in str((out or {}).get("msg") or "") for m in RATE_MARKS) or attempt == 2:
            break
        time.sleep(6)
    raise RuntimeError("开窗没成：%s" % said)


def current_ws():
    if probe_alive() is None:
        return None
    out = post("/browser/open", {"id": BIT_ID, "args": list(OPEN_ARGS), "queue": True}, timeout=70)
    data = out.get("data") if isinstance(out, dict) else None
    ws = (data or {}).get("ws") if isinstance(data, dict) else None
    if isinstance(out, dict) and out.get("success") is True and ws:
        return LOOPBACK.sub(WORKER, str(ws))
    return None


def close_window():
    if probe_alive() is None:
        log("  关窗：本来就不在")
        return True
    try:
        post("/browser/close", {"id": BIT_ID})
    except Exception as exc:
        log("  关窗报错：%s" % exc)
    for _ in range(10):
        time.sleep(1.5)
        if probe_alive() is None:
            log("  关窗：确认已关")
            return True
    log("  关窗：**没能确认**")
    return False


def cdp(argv, timeout=150):
    p = subprocess.run([CDP] + list(argv) + ["--host", HOST, "--port", str(PORT)],
                       capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def ev(js):
    rc, out, err = cdp(["eval", js])
    for line in (out or "").strip().split("\n"):
        line = line.strip()
        if not line or line.startswith(("ERROR:", "Usage:", "Flags:")):
            continue
        try:
            v = json.loads(line)
        except Exception:
            return line
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                return v
        return v
    return {"__eval_failed__": True, "rc": rc, "stderr": (err or "")[:300]}


def cmd(argv, tag, timeout=150, first_lines=3):
    t0 = time.time()
    try:
        rc, out, err = cdp(argv, timeout=timeout)
    except subprocess.TimeoutExpired:
        log("  [%s] **超时**" % tag)
        return 124, "", "TIMEOUT"
    dt = time.time() - t0
    keep = [t for t in (err or "").strip().split("\n")
            if t.strip() and not t.startswith(("Usage:", "Flags:", "  -", "      --"))][:first_lines]
    log("  [%s] rc=%s %.1fs %s" % (tag, rc, dt, ("｜" + " ｜ ".join(keep))[:500] if keep else ""))
    return rc, out, err


PROBE_JS = r"""
(function(){
  function attrsOf(el){var o={};for(var i=0;i<el.attributes.length;i++)o[el.attributes[i].name]=String(el.attributes[i].value).slice(0,90);return o;}
  function boxOf(el){var r=el.getBoundingClientRect();return [Math.round(r.left),Math.round(r.top),Math.round(r.width),Math.round(r.height)];}
  function pathOf(el){var parts=[],n=el;
    while(n&&n.nodeType===1&&n.tagName!=='HTML'){var p=n.parentElement;if(!p)break;
      var i=1,s=n;while((s=s.previousElementSibling))if(s.tagName===n.tagName)i++;
      parts.unshift(n.tagName.toLowerCase()+':nth-of-type('+i+')');n=p;}
    return parts.join(' > ');}
  function vis(el){var cs=getComputedStyle(el),r=el.getBoundingClientRect();
    return !(cs.display==='none'||cs.visibility==='hidden'||+cs.opacity===0||r.width<1||r.height<1);}
  var out={url:location.href,scrollY:Math.round(window.scrollY),
    email_inputs:document.querySelectorAll('input[type=email],input[name*=email i],input[placeholder*=mail i]').length};
  var wrap=document.querySelector('#octane-quiz-wrapper');
  out.wrap=!!wrap;
  if(!wrap) return JSON.stringify(out);
  out.wrap_text=(wrap.innerText||'').replace(/\n+/g,' | ').slice(0,300);
  out.has_continue=(wrap.innerText||'').indexOf('CONTINUE')>=0;
  out.has_back=(wrap.innerText||'').indexOf('BACK')>=0;
  // 账本 -3 组的 when 判据：页面正文里有 "CONTINUE ← BACK Community Community Sign up to"
  var bt=document.body.innerText||'';
  out.when_minus3=(bt.indexOf('CONTINUE')>=0 && bt.indexOf('← BACK')>=0 && bt.indexOf('Community')>=0);
  var uls=wrap.querySelectorAll('ul'); out.ul_count=uls.length; out.uls=[];
  for(var u=0;u<uls.length;u++){
    var ul=uls[u],lbl=ul.getAttribute('aria-labelledby'),qel=lbl?document.getElementById(lbl):null;
    var uo={path:pathOf(ul),attrs:attrsOf(ul),box:boxOf(ul),
            question:qel?(qel.innerText||'').trim().slice(0,160):null,lis:[]};
    var lis=ul.querySelectorAll(':scope > li');
    for(var i=0;i<lis.length;i++){
      var li=lis[i],cs=getComputedStyle(li);
      uo.lis.push({n:i+1,attrs:attrsOf(li),
        text:(li.innerText||'').replace(/\n+/g,'/').slice(0,90),
        tc:(li.textContent||'').trim().slice(0,90),
        aria:li.getAttribute('aria-label'),role:li.getAttribute('role'),
        checked:li.getAttribute('aria-checked'),box:boxOf(li),vis:vis(li),
        kids:(function(){var a=[];for(var k=0;k<li.children.length;k++){var c=li.children[k];
              a.push(c.tagName.toLowerCase()+(c.className?'.'+String(c.className).slice(0,26):'')+
                     ':'+((c.innerText||'').trim().slice(0,30)||'(无字)'));}return a;})(),
        outer:li.outerHTML.slice(0,320)});
    }
    out.uls.push(uo);
  }
  out.buttons=[];
  var bs=wrap.querySelectorAll('button');
  for(var b=0;b<bs.length;b++){var x=bs[b];
    out.buttons.push({path:pathOf(x),attrs:attrsOf(x),text:(x.innerText||'').trim().slice(0,40),
      box:boxOf(x),disabled:!!x.disabled,vis:vis(x),
      aria_disabled:x.getAttribute('aria-disabled')});}
  return JSON.stringify(out);
})()
"""


def dismiss_overlays():
    js = r"""
    (function(){
      var out={clicked:[]};
      function hit(sel){var e=document.querySelector(sel);
        if(e){var r=e.getBoundingClientRect();
          if(r.width>0&&r.height>0){e.click();out.clicked.push(sel);}}}
      hit('button.cc-allow');
      hit('#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll');
      hit('div.pd-cookie-banner-window button.cc-allow');
      hit('#shopify-section-international-popup button');
      return JSON.stringify(out);
    })()
    """
    log("  清浮层：%s" % json.dumps(ev(js), ensure_ascii=False))


def screen(tag):
    d = ev(PROBE_JS)
    with open(os.path.join(OUT, "r5-%s.json" % tag), "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=1)
    if not isinstance(d, dict) or d.get("__eval_failed__"):
        log("  [%s] 没量到：%s" % (tag, json.dumps(d, ensure_ascii=False)[:160]))
        return None
    log("  [%s] wrap=%s ul=%s CONTINUE=%s BACK=%s 账本-3判据=%s email框=%s scrollY=%s"
        % (tag, d.get("wrap"), d.get("ul_count"), d.get("has_continue"), d.get("has_back"),
           d.get("when_minus3"), d.get("email_inputs"), d.get("scrollY")))
    log("      quiz 里印着：%s" % (d.get("wrap_text") or "(空)"))
    for uo in d.get("uls") or []:
        log("      UL %s" % uo["path"][-64:])
        log("         ul.attrs=%s 题面=%r" % (json.dumps(uo["attrs"], ensure_ascii=False)[:200], uo["question"]))
        for li in uo["lis"]:
            log("         li#%-2d aria=%-24r role=%-6s checked=%-5s text=%-46r vis=%s box=%s"
                % (li["n"], li["aria"], li["role"], li["checked"], li["text"], li["vis"], li["box"]))
            log("              attrs=%s" % json.dumps(li["attrs"], ensure_ascii=False)[:230])
            log("              子元素=%s" % json.dumps(li["kids"], ensure_ascii=False)[:260])
    for b in d.get("buttons") or []:
        log("      BUTTON text=%r disabled=%s aria-disabled=%s vis=%s box=%s attrs=%s"
            % (b["text"], b["disabled"], b["aria_disabled"], b["vis"], b["box"],
               json.dumps(b["attrs"], ensure_ascii=False)[:130]))
    return d


def observe(tag):
    rc, out, err = cdp(["observe"])
    if rc != 0 or not (out or "").strip():
        log("  [observe %s] 失败 rc=%s" % (tag, rc))
        return None
    try:
        m = json.loads(out)
    except Exception:
        return None
    with open(os.path.join(OUT, "r5-observe-%s.json" % tag), "w", encoding="utf-8") as fh:
        json.dump(m, fh, ensure_ascii=False, indent=1)
    inside = [a for a in (m.get("actions") or []) if "#octane-quiz-wrapper" in (a.get("selector") or "")]
    lis = [a for a in inside if (a.get("tag") or "").lower() == "li"]
    empt = [a for a in lis if not (a.get("text") or "").strip() and not (a.get("aria_label") or "").strip()]
    log("  [observe %s] actions=%d quiz里=%d（li %d）diag=%d obstructions=%d；li 里 text 与 aria 都空=%d"
        % (tag, len(m.get("actions") or []), len(inside), len(lis),
           len(m.get("diagnostics") or []), len(m.get("obstructions") or []), len(empt)))
    for a in lis:
        log("      LI sel=%-64s text=%r aria=%r role=%s stab=%s near=%s"
            % ((a.get("selector") or "")[-64:], a.get("text"), a.get("aria_label"),
               a.get("role"), a.get("stability"), a.get("nearby_text")))
    for a in inside:
        if (a.get("tag") or "").lower() == "li":
            continue
        log("      %-6s text=%r role=%s aria=%r stab=%s box=%s sel=%s"
            % (a.get("tag"), a.get("text"), a.get("role"), a.get("aria_label"),
               a.get("stability"), a.get("bbox"), (a.get("selector") or "")[:70]))
    return m


def robust(kind, sel, tag):
    rc, out, err = cmd([kind, sel], tag)
    if rc != 0:
        dismiss_overlays()
        time.sleep(2)
        cmd(["scroll", sel], "%s re-scroll" % tag)
        time.sleep(1)
        rc, out, err = cmd([kind, sel], "%s re-%s" % (tag, kind))
    return rc


def main():
    global HOST, PORT
    log("探针开始 %s" % time.strftime("%H:%M:%S"))
    log("窗口探活：%s" % (probe_alive() or "**不在**"))
    ws = current_ws()
    if not ws:
        log("窗口不在 ⇒ 开一个")
        ws = _open()
    log("ws = %s" % ws)
    m = re.match(r"ws://([^:/]+):(\d+)", ws)
    HOST, PORT = m.group(1), m.group(2)
    try:
        cmd(["navi", URL_QUIZ], "navi quiz 页", timeout=90)
        for i in range(20):
            time.sleep(2)
            if (ev("JSON.stringify({w:!!document.querySelector('#octane-quiz-wrapper')})") or {}).get("w"):
                log("  等了 %ds，quiz 容器出现" % ((i + 1) * 2))
                break
        time.sleep(8)
        dismiss_overlays()
        time.sleep(2)
        dismiss_overlays()
        time.sleep(1)

        #: 账本：-2 组 li6 → li6 → li2；-3 组 Continue → li3 → li4 → li1
        plan = [("li", 6, "账本-2 第1步 li6"),
                ("li", 2, "账本-2 第4步 li2"),
                ("li", 3, "年龄题 li3"),
                ("continue", 0, "★账本-3 第1步 Continue"),
                ("li", 3, "★账本-3 第2步 li3"),
                ("li", 4, "★账本-3 第3步 li4"),
                ("li", 1, "★账本-3 第4步 li1"),
                ("li", 1, "继续往下 li1"),
                ("li", 1, "继续往下 li1"),
                ("li", 1, "继续往下 li1"),
                ("li", 1, "继续往下 li1")]
        for step, (kind, idx, why) in enumerate(plan, 1):
            tag = "S%d" % step
            log("\n########## %s  %s ##########" % (tag, why))
            d = screen(tag)
            if not d or not d.get("wrap"):
                log("  quiz 不在 —— 停")
                break
            if d.get("email_inputs"):
                log("  ★ 出现 email 输入框 —— 到填表那一步，**停**（不填不提交）")
                observe("%s-email前" % tag)
                break
            observe(tag)
            uls = d.get("uls") or []
            if kind == "continue":
                if not d.get("has_continue"):
                    log("  这一屏没有 CONTINUE —— 跳过这步")
                    continue
                robust("click", CONTINUE, "%s click Continue" % tag)
                time.sleep(4)
                continue
            if not uls:
                log("  这一屏没有 ul（li 无从点起）—— 停")
                break
            n = len(uls[0]["lis"])
            use = idx if idx <= n else 1
            if use != idx:
                log("  这一屏只有 %d 个 li，账本的 li%d 点不到 ⇒ 退而点 li%d" % (n, idx, use))
            li_sel = UL + " > li:nth-of-type(%d)" % use
            cmd(["scroll", li_sel], "%s scroll li%d" % (tag, use))
            time.sleep(1.5)
            robust("click", li_sel, "%s click li%d" % (tag, use))
            time.sleep(4)
        log("\n★ 走完（没填邮箱、没提交）")
    finally:
        log("\n窗口收尾：")
        log("窗口已关：%s" % close_window())


if __name__ == "__main__":
    main()
