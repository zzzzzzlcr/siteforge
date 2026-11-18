#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第四趟（= 第三趟打补丁：先等 cookie 横幅弹出来再点掉，click 失败先清浮层再重试）：把 cookie 横幅先点掉，然后**一题一题**往下走，每屏都把 ul 里的 li 逐条打出来。

前两趟已经量到：
  · 直接导航落地就是 quiz 第 1 题（"What's your main health goal right now?"），14 个 li
    **个个有字**（text / aria-label / role=radio / data-option-id / data-option-index 全有）
  · 每点一个选项**自动进下一题**；走到第 3 题（"How old are you?"）时 cookie 横幅迟到地
    弹出来（`div.pd-cookie-banner-window.cc-window`）+ 国际站浮层 ⇒ 后面的点击全被挡（rc=1）

这一趟要的是：**后面每一题**的 li 到底有没有字（账本 -3 组那三个「没写名字的」li
到底是哪一屏上的、那一屏上的 li 真没字还是当时没读到）。

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


def cmd(argv, tag, timeout=150, show_all_stderr=False):
    t0 = time.time()
    try:
        rc, out, err = cdp(argv, timeout=timeout)
    except subprocess.TimeoutExpired:
        log("  [%s] **超时**" % tag)
        return 124, "", "TIMEOUT"
    dt = time.time() - t0
    lines = [t for t in (err or "").strip().split("\n") if t.strip()]
    keep = lines if show_all_stderr else [t for t in lines[:3] if not t.startswith(("Usage:", "Flags:", "  -", "      --"))]
    log("  [%s] rc=%s %.1fs %s" % (tag, rc, dt, ("｜" + " ｜ ".join(keep)[:400]) if keep else ""))
    return rc, out, err


STATE_JS = r"""
(function(){
  function attrsOf(el){var o={};for(var i=0;i<el.attributes.length;i++)o[el.attributes[i].name]=String(el.attributes[i].value).slice(0,120);return o;}
  function boxOf(el){var r=el.getBoundingClientRect();return [Math.round(r.left),Math.round(r.top),Math.round(r.width),Math.round(r.height)];}
  function pathOf(el){var parts=[],n=el;
    while(n&&n.nodeType===1&&n.tagName!=='HTML'){var p=n.parentElement;if(!p)break;
      var i=1,s=n;while((s=s.previousElementSibling))if(s.tagName===n.tagName)i++;
      parts.unshift(n.tagName.toLowerCase()+':nth-of-type('+i+')');n=p;}
    return parts.join(' > ');}
  function vis(el){var cs=getComputedStyle(el),r=el.getBoundingClientRect();
    return !(cs.display==='none'||cs.visibility==='hidden'||+cs.opacity===0||r.width<1||r.height<1);}
  var out={url:location.href,scrollY:Math.round(window.scrollY),
           email_inputs:document.querySelectorAll('input[type=email],input[name*=email i],input[placeholder*=mail i]').length,
           body_text:(document.body.innerText||'').replace(/\n+/g,' | ').slice(0,200)};
  var wrap=document.querySelector('#octane-quiz-wrapper');
  out.wrap=!!wrap;
  if(!wrap) return JSON.stringify(out);
  out.has_continue=(wrap.innerText||'').indexOf('CONTINUE')>=0;
  out.has_back=(wrap.innerText||'').indexOf('BACK')>=0;
  out.wrap_text=(wrap.innerText||'').replace(/\n+/g,' | ').slice(0,300);
  var uls=wrap.querySelectorAll('ul'); out.ul_count=uls.length; out.uls=[];
  for(var u=0;u<uls.length;u++){
    var ul=uls[u],lbl=ul.getAttribute('aria-labelledby'),qel=lbl?document.getElementById(lbl):null;
    var uo={path:pathOf(ul),attrs:attrsOf(ul),box:boxOf(ul),
            question:qel?(qel.innerText||'').trim().slice(0,160):null,lis:[]};
    var lis=ul.querySelectorAll(':scope > li');
    for(var i=0;i<lis.length;i++){
      var li=lis[i],cs=getComputedStyle(li);
      uo.lis.push({n:i+1,attrs:attrsOf(li),text:(li.innerText||'').replace(/\n+/g,'/').slice(0,70),
        tc:(li.textContent||'').trim().slice(0,70),aria:li.getAttribute('aria-label'),
        checked:li.getAttribute('aria-checked'),role:li.getAttribute('role'),
        box:boxOf(li),vis:vis(li),rects:li.getClientRects().length,
        outer:li.outerHTML.slice(0,260)});
    }
    out.uls.push(uo);
  }
  out.buttons=[];
  var bs=wrap.querySelectorAll('button');
  for(var b=0;b<bs.length;b++){
    var bt=bs[b];
    out.buttons.push({path:pathOf(bt),attrs:attrsOf(bt),text:(bt.innerText||'').trim().slice(0,40),
      box:boxOf(bt),disabled:!!bt.disabled,vis:vis(bt)});
  }
  return JSON.stringify(out);
})()
"""


def state(tag):
    d = ev(STATE_JS)
    with open(os.path.join(OUT, "r3-state-%s.json" % tag), "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=1)
    if not isinstance(d, dict) or d.get("__eval_failed__"):
        log("  [%s] 没量到：%s" % (tag, json.dumps(d, ensure_ascii=False)[:160]))
        return d, ""
    log("  [%s] url=%s wrap=%s ul=%s CONTINUE=%s BACK=%s email框=%s scrollY=%s"
        % (tag, d.get("url", "")[-46:], d.get("wrap"), d.get("ul_count"), d.get("has_continue"),
           d.get("has_back"), d.get("email_inputs"), d.get("scrollY")))
    q = ""
    for uo in d.get("uls") or []:
        q = uo.get("question") or ""
        log("      题面=%r  UL attrs=%s" % (q, json.dumps(uo["attrs"], ensure_ascii=False)[:180]))
        for li in uo["lis"]:
            log("        li#%-2d aria=%-26r role=%-6s checked=%-5s text=%-40r vis=%s box=%s"
                % (li["n"], li["aria"], li["role"], li["checked"], li["text"], li["vis"], li["box"]))
    for bt in d.get("buttons") or []:
        log("      BUTTON text=%r disabled=%s vis=%s box=%s attrs=%s"
            % (bt["text"], bt["disabled"], bt["vis"], bt["box"],
               json.dumps(bt["attrs"], ensure_ascii=False)[:150]))
    return d, q


def observe(tag):
    rc, out, err = cdp(["observe"])
    if rc != 0 or not (out or "").strip():
        log("  [observe %s] 失败 rc=%s %s" % (tag, rc, (err or "")[:150]))
        return None
    try:
        m = json.loads(out)
    except Exception:
        return None
    with open(os.path.join(OUT, "r3-observe-%s.json" % tag), "w", encoding="utf-8") as fh:
        json.dump(m, fh, ensure_ascii=False, indent=1)
    inside = [a for a in (m.get("actions") or [])
              if "#octane-quiz-wrapper" in (a.get("selector") or "")]
    lis = [a for a in inside if a.get("tag") == "li"]
    log("  [observe %s] actions=%d quiz里=%d（li %d）diag=%d obstructions=%d"
        % (tag, len(m.get("actions") or []), len(inside), len(lis),
           len(m.get("diagnostics") or []), len(m.get("obstructions") or [])))
    empt = [a for a in lis if not (a.get("text") or "").strip() and not (a.get("aria_label") or "").strip()]
    log("      li 卡里 text 与 aria_label **都空**的：%d 条" % len(empt))
    for a in lis:
        log("        sel=%-72s text=%r aria=%r role=%s stab=%s near=%s"
            % ((a.get("selector") or "")[-72:], a.get("text"), a.get("aria_label"),
               a.get("role"), a.get("stability"), a.get("nearby_text")))
    for a in inside:
        if a.get("tag") == "li":
            continue
        log("      %s text=%r role=%s aria=%r stab=%s box=%s sel=%s"
            % (a.get("tag"), a.get("text"), a.get("role"), a.get("aria_label"),
               a.get("stability"), a.get("bbox"), (a.get("selector") or "")[:70]))
    for o in m.get("obstructions") or []:
        log("      ⚠ obstruction %s dismiss=%s" % (o.get("kind"), (o.get("dismiss_selector") or "")[:80]))
    return m


def dismiss_overlays():
    """账本在入口页点的 `button.cc-allow`（Cookiebot 允许全部）+ 国际站浮层关掉。"""
    js = r"""
    (function(){
      var out={clicked:[]};
      function hit(sel){var e=document.querySelector(sel);
        if(e){var r=e.getBoundingClientRect();
          if(r.width>0&&r.height>0){e.click();out.clicked.push(sel);}}}
      hit('button.cc-allow');
      hit('#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll');
      hit('#shopify-section-international-popup button');
      hit('div.pd-cookie-banner-window button');
      return JSON.stringify(out);
    })()
    """
    log("  清浮层：%s" % json.dumps(ev(js), ensure_ascii=False))


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
            d = ev("JSON.stringify({w:!!document.querySelector('#octane-quiz-wrapper')})")
            if isinstance(d, dict) and d.get("w"):
                log("  等了 %ds，quiz 容器出现" % ((i + 1) * 2))
                break
        log("  等 8 秒让迟到的 cookie 横幅弹出来…")
        time.sleep(8)
        dismiss_overlays()
        time.sleep(2)
        dismiss_overlays()
        time.sleep(1)

        #: 账本走的那三颗（-2 组 li6/li2）之后，一题一题往下：能对上账本就照账本的号，
        #: 对不上就用 1 号（本趟只关心「每屏 li 有没有字」，不追求复现账本的每一跳）
        plan = [6, 2, 3, 4, 1, 1, 1, 1, 1, 1, 1, 1]
        seen_q = set()
        for step, idx in enumerate(plan, 1):
            tag = "Q%d" % step
            log("\n########## %s（第 %d 次落屏） ##########" % (tag, step))
            d, q = state(tag)
            if not d or not d.get("wrap"):
                log("  quiz 不在了 —— 停")
                break
            if d.get("email_inputs"):
                log("  ★ 出现了 email 输入框 —— 到填表那一步了，**停在这里**（不填、不提交）")
                observe("%s-email前" % tag)
                break
            observe(tag)
            if q and q in seen_q:
                log("  ⚠ 题面和上一屏重复（点了没推进？）")
            seen_q.add(q)
            if not (d.get("uls") or []):
                log("  这一屏没有 ul（可能不是多选题）—— 停")
                break
            li_sel = UL + " > li:nth-of-type(%d)" % idx
            cmd(["scroll", li_sel], "%s scroll li%d" % (tag, idx))
            time.sleep(1.5)
            rc, _, err = cmd(["click", li_sel], "%s click li%d" % (tag, idx))
            if rc != 0:
                log("      ↑ 点不动，再清一次浮层后重试：%s" % " | ".join(
                    [t for t in (err or "").split("\n") if t.strip()][:3])[:300])
                dismiss_overlays()
                time.sleep(2)
                cmd(["scroll", li_sel], "%s re-scroll li%d" % (tag, idx))
                time.sleep(1)
                cmd(["click", li_sel], "%s re-click li%d" % (tag, idx), show_all_stderr=True)
            time.sleep(3.5)
        log("\n★ 走完（没填邮箱、没提交）。URL=%s" % ev("location.href"))
    finally:
        log("\n窗口收尾：")
        log("窗口已关：%s" % close_window())


if __name__ == "__main__":
    main()
