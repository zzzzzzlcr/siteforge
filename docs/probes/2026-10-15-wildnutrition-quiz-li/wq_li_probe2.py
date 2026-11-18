#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第二趟：走到「那三个 li」被点的那几屏（第一趟点错了，点了页头那颗 Take the quiz）。

第一趟已经量到：直接导航到 /pages/what-supplements-should-i-take 落地那一刻，
quiz 就已经在第一题上（"What's your main health goal right now?"），14 个 li
**个个有字、有 role/aria-label/data-option-id**。这一趟要的是**后面那几屏**
（账本 `what_supplements_should_i_take-3` 第 2/3/4 步点的 li:nth-of-type(3|4|1)）。

按账本走，每一步 observe + 裸 DOM，把「现在这一屏是哪一题 / 有哪些 li / 有哪些按钮」都打出来。
**不填邮箱、不点 submit**。窗关在 finally 里。
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

WORKER = "192.168.1.222"
BIT_ID = "982d158b41b14fbb87754667859b4719"
API = "http://%s:54345" % WORKER
CDP = "/opt/skills/auto-farm-skill/cdp"
CDP_REPO = "/company/siteforge/tools/cdp/cdp"
OUT = os.path.dirname(os.path.abspath(__file__))

URL_QUIZ = "https://www.wildnutrition.com/pages/what-supplements-should-i-take"
UL = "#octane-quiz-wrapper > div:nth-of-type(1) > div:nth-of-type(3) > ul:nth-of-type(1)"
LI = {k: UL + " > li:nth-of-type(%d)" % k for k in range(1, 15)}
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
    except Exception as exc:
        log("  探活问不出来：%s" % exc)
        return "?"
    data = out.get("data") if isinstance(out, dict) else None
    if isinstance(data, dict) and data.get(BIT_ID):
        return int(data[BIT_ID])
    return None


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


def cdp(argv, timeout=150, binary=None):
    cmd = [binary or CDP] + list(argv) + ["--host", HOST, "--port", str(PORT)]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout, p.stderr


def ev(js, binary=None):
    rc, out, err = cdp(["eval", js], binary=binary)
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


def cmd(argv, tag, timeout=150, binary=None):
    t0 = time.time()
    try:
        rc, out, err = cdp(argv, timeout=timeout, binary=binary)
    except subprocess.TimeoutExpired:
        log("  [%s] **超时**（>%ss）" % (tag, timeout))
        return 124, "", "TIMEOUT"
    dt = time.time() - t0
    tail = [t for t in (err or "").strip().split("\n") if t.strip()][-2:]
    log("  [%s] rc=%s %.1fs %s" % (tag, rc, dt, ("｜" + " ｜ ".join(tail)) if tail else ""))
    return rc, out, err


#: 裸 DOM：这一屏是**哪一题**、ul 里每个 li 是谁、wrapper 里有什么按钮
STATE_JS = r"""
(function(){
  function attrsOf(el){var o={};for(var i=0;i<el.attributes.length;i++)o[el.attributes[i].name]=String(el.attributes[i].value).slice(0,120);return o;}
  function boxOf(el){var r=el.getBoundingClientRect();return [Math.round(r.left),Math.round(r.top),Math.round(r.width),Math.round(r.height)];}
  function pathOf(el){var parts=[],n=el;
    while(n&&n.nodeType===1&&n.tagName!=='HTML'){var p=n.parentElement;if(!p)break;
      var i=1,s=n;while((s=s.previousElementSibling))if(s.tagName===n.tagName)i++;
      parts.unshift(n.tagName.toLowerCase()+':nth-of-type('+i+')');n=p;}
    return parts.join(' > ');}
  var out={url:location.href,scrollY:Math.round(window.scrollY),
           has_continue:(document.body.innerText||'').indexOf('CONTINUE')>=0,
           has_back:(document.body.innerText||'').indexOf('← BACK')>=0};
  var wrap=document.querySelector('#octane-quiz-wrapper');
  out.wrap=!!wrap;
  if(!wrap) return JSON.stringify(out);
  out.wrap_text=(wrap.innerText||'').replace(/\n+/g,' | ').slice(0,260);
  var uls=wrap.querySelectorAll('ul'); out.ul_count=uls.length; out.uls=[];
  for(var u=0;u<uls.length;u++){
    var ul=uls[u];
    var lbl=ul.getAttribute('aria-labelledby');
    var qel=lbl?document.getElementById(lbl):null;
    var uo={path:pathOf(ul),attrs:attrsOf(ul),box:boxOf(ul),
            question:qel?(qel.innerText||'').trim().slice(0,160):null,lis:[]};
    var lis=ul.querySelectorAll(':scope > li');
    for(var i=0;i<lis.length;i++){
      var li=lis[i],cs=getComputedStyle(li);
      uo.lis.push({n:i+1,attrs:attrsOf(li),text:(li.innerText||'').replace(/\n+/g,'/').slice(0,90),
        tc:(li.textContent||'').trim().slice(0,90),
        aria:li.getAttribute('aria-label'),checked:li.getAttribute('aria-checked'),
        box:boxOf(li),vis:cs.visibility,disp:cs.display,rects:li.getClientRects().length,
        outer:li.outerHTML.slice(0,300)});
    }
    out.uls.push(uo);
  }
  var bs=wrap.querySelectorAll('button'); out.buttons=[];
  for(var b=0;b<bs.length;b++){
    var bt=bs[b];
    out.buttons.push({path:pathOf(bt),attrs:attrsOf(bt),text:(bt.innerText||'').trim().slice(0,60),
      box:boxOf(bt),disabled:!!bt.disabled,vis:getComputedStyle(bt).visibility});
  }
  var others=document.querySelectorAll('button,a');
  out.other_buttons=[];
  for(var k=0;k<others.length;k++){
    var o=others[k];
    if(wrap.contains(o)) continue;
    var t=(o.innerText||'').trim();
    if(/CONTINUE|BACK|Continue|Back/i.test(t) && t.length<40 && out.other_buttons.length<8)
      out.other_buttons.push({tag:o.tagName.toLowerCase(),text:t.slice(0,40),path:pathOf(o),box:boxOf(o)});
  }
  return JSON.stringify(out);
})()
"""


def state(tag, show_lis=True, full_cards=False):
    d = ev(STATE_JS)
    with open(os.path.join(OUT, "state-%s.json" % tag), "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=1)
    if not isinstance(d, dict) or d.get("__eval_failed__"):
        log("  [屏 %s] 没量到：%s" % (tag, json.dumps(d, ensure_ascii=False)[:160]))
        return d
    log("  [屏 %s] url=%s wrap=%s ul=%s CONTINUE=%s ←BACK=%s scrollY=%s"
        % (tag, d.get("url"), d.get("wrap"), d.get("ul_count"), d.get("has_continue"),
           d.get("has_back"), d.get("scrollY")))
    log("      quiz 里印着：%s" % (d.get("wrap_text") or "(空)"))
    for uo in d.get("uls") or []:
        log("      UL %s" % uo["path"][-70:])
        log("         attrs=%s box=%s 题面=%r" % (json.dumps(uo["attrs"], ensure_ascii=False),
                                                 uo["box"], uo["question"]))
        if show_lis:
            for li in uo["lis"]:
                log("         li#%-2d aria=%-26r checked=%-5s text=%r box=%s vis=%s rects=%s id=%s optidx=%s"
                    % (li["n"], li["aria"], li["checked"], li["text"], li["box"], li["vis"],
                       li["rects"], (li["attrs"].get("id") or "")[:12],
                       li["attrs"].get("data-option-index")))
    for bt in d.get("buttons") or []:
        log("      BUTTON %s text=%r disabled=%s vis=%s box=%s attrs=%s"
            % (bt["path"][-56:], bt["text"], bt["disabled"], bt["vis"], bt["box"],
               json.dumps(bt["attrs"], ensure_ascii=False)[:200]))
    for ob in d.get("other_buttons") or []:
        log("      别处的按钮 <%s> %r box=%s path=%s" % (ob["tag"], ob["text"], ob["box"], ob["path"][-70:]))
    return d


def observe(tag, only=None, cross_check=False):
    rc, out, err = cdp(["observe"])
    if rc != 0 or not (out or "").strip():
        log("  [observe %s] 失败 rc=%s %s" % (tag, rc, (err or "")[:160]))
        return None
    try:
        m = json.loads(out)
    except Exception:
        return None
    with open(os.path.join(OUT, "observe-%s.json" % tag), "w", encoding="utf-8") as fh:
        json.dump(m, fh, ensure_ascii=False, indent=1)
    log("  [observe %s] actions=%d diag=%d obstructions=%d"
        % (tag, len(m.get("actions") or []), len(m.get("diagnostics") or []),
           len(m.get("obstructions") or [])))
    inside = [a for a in (m.get("actions") or [])
              if "#octane-quiz-wrapper" in (a.get("selector") or "")]
    log("      quiz 里的动作 %d 条；li 卡：" % len(inside))
    for a in inside:
        sel = a.get("selector") or ""
        if "> li" not in sel and a.get("tag") != "li":
            continue
        if only and not any(sel.endswith(x) for x in only):
            continue
        if a.get("tag") == "li":
            log(json.dumps(a, ensure_ascii=False, indent=2))
    for a in inside:
        sel = a.get("selector") or ""
        if "> li" in sel or a.get("tag") == "li":
            continue
        log("      tag=%-6s role=%-7s text=%r aria=%r near=%s stab=%s vis=%s box=%s sel=%s"
            % (a.get("tag"), a.get("role"), a.get("text"), a.get("aria_label"), a.get("nearby_text"),
               a.get("stability"), a.get("visible"), a.get("bbox"), sel[:80]))
    if cross_check:
        rc2, out2, _ = cdp(["observe"], binary=CDP_REPO)
        if rc2 == 0 and (out2 or "").strip():
            try:
                m2 = json.loads(out2)
            except Exception:
                m2 = None
            if m2:
                with open(os.path.join(OUT, "observe-%s-repo.json" % tag), "w", encoding="utf-8") as fh:
                    json.dump(m2, fh, ensure_ascii=False, indent=1)
                log("      —— 交叉对照：仓里那个 cdp 看到的 li 卡 ——")
                for a in (m2.get("actions") or []):
                    if "#octane-quiz-wrapper" in (a.get("selector") or "") and a.get("tag") == "li":
                        log(json.dumps(a, ensure_ascii=False, indent=2))
    return m


def act(kind, sel, tag, timeout=90):
    return cmd([kind, sel], tag, timeout=timeout)


def main():
    global HOST, PORT
    log("探针开始 %s" % time.strftime("%H:%M:%S"))
    pid0 = probe_alive()
    log("窗口探活：%s" % ("pid=%s" % pid0 if pid0 else "**不在**"))
    ws = current_ws()
    if not ws:
        log("窗口不在 ⇒ 开一个（BitWindow.open 的 args 原样抄）")
        ws = _open()
    log("ws = %s" % ws)
    m = re.match(r"ws://([^:/]+):(\d+)", ws)
    HOST, PORT = m.group(1), m.group(2)
    try:
        act("navi", URL_QUIZ, "navi quiz 页", timeout=90)
        for i in range(20):
            time.sleep(2)
            d = ev("JSON.stringify({w:!!document.querySelector('#octane-quiz-wrapper')})")
            if isinstance(d, dict) and d.get("w"):
                log("  等了 %ds，quiz 容器出现" % ((i + 1) * 2))
                break
        else:
            log("  等了 40s，quiz 容器还没出现")

        log("\n########## S0 落地（第 1 题） ##########")
        state("S0")
        observe("S0", cross_check=False)

        # 账本 -2 组：先点两颗**有字**的（那时 ul 里 li6 / li2）
        act("scroll", LI[6], "scroll li6 (Hair, skin + nails)")
        time.sleep(1.5)
        act("click", LI[6], "click li6 (Hair, skin + nails)")
        time.sleep(3)
        log("\n########## S1 点完 li6 ##########")
        state("S1")
        act("click", LI[2], "click li2 (Greater wellbeing)")
        time.sleep(3)
        log("\n########## S2 点完 li2（账本 -2 组走完这一屏） ##########")
        state("S2")
        observe("S2", cross_check=True)

        # 账本 -3 组：Continue → li3 → li4 → li1
        log("\n########## S3 账本 -3 第 1 步：点 Continue ##########")
        act("click", CONTINUE, "click Continue")
        time.sleep(4)
        state("S3")
        observe("S3", cross_check=True)

        log("\n########## S4 账本 -3 第 2 步：点 li3 ##########")
        act("click", LI[3], "click li3")
        time.sleep(4)
        state("S4")
        observe("S4", only=["li:nth-of-type(3)", "li:nth-of-type(4)", "li:nth-of-type(1)"])

        log("\n########## S5 账本 -3 第 3 步：点 li4 ##########")
        act("click", LI[4], "click li4")
        time.sleep(4)
        state("S5")
        observe("S5", only=["li:nth-of-type(3)", "li:nth-of-type(4)", "li:nth-of-type(1)"])

        log("\n########## S6 账本 -3 第 4 步：点 li1 ##########")
        act("click", LI[1], "click li1")
        time.sleep(4)
        state("S6")
        observe("S6", only=["li:nth-of-type(3)", "li:nth-of-type(4)", "li:nth-of-type(1)"])

        log("\n★ 到此为止：**不填邮箱、不点 submit**。当前 URL=%s" % ev("location.href"))
    finally:
        log("\n窗口收尾：")
        log("窗口已关：%s" % close_window())


if __name__ == "__main__":
    main()
