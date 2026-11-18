#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""wildnutrition：quiz 里那三个「没写名字的」li，身上/旁边到底有没有字或稳属性？

要回答的一句话
--------------
账本里这三处 target 是 `text: None / role: None / near: None`，只有一根长 nth 链：

    #octane-quiz-wrapper > div:nth-of-type(1) > div:nth-of-type(3) > ul:nth-of-type(1) > li:nth-of-type(3|4|1)

而**同一页另一屏**（组 `what_supplements_should_i_take-2`）里同类的 li 是**带字**的
（`text='Hair, skin + nails…'`、`role='radio'`、`near='main'`）。⇒ 两种可能：

  · 那几个 li **真的**没字  ⇒ 回页面现读也没用（删步/请人/接受是唯一出路）
  · **当时读的那一瞬**没读到 ⇒ 现读一下就读到（回页面现读这条路值得做）

怎么答
------
开一个窗口 → 按账本把 quiz 走到那几屏 → **每走一步 observe 一次**，把 quiz 选项列表里
每个 li 的卡**原样**打出来（含 Continue 那颗**有字**的按钮做对照），再补一份**裸 DOM**
（outerHTML / 全部属性 / 盒子 / 可见性 / 中心点上是谁）—— 「有没有稳属性」只有裸 DOM 说得清。

不猜：`#octane-quiz-wrapper div[data-element*="oct-quiz-submit-btn"] button`（同一组第 5 步用的）
说明这个 widget **确实**会下发 `data-element` —— li 上有没有，看裸 DOM。

边界
----
只读：**不填邮箱、不点 submit**（账本第 5、6 步不走）。窗关在 finally 里。
不动任何被跟踪文件（本脚本与它的产物都落在本目录下）。

在哪儿跑
--------
**宿主上**（不在容器里）—— 容器里 `/opt/skills/auto-farm-skill/` 只有 `config` 与 `logs`，
`src/`（BrowserManager/CDPHelper）与 `cdp` 都不在；宿主上两样都在，`docs/probes/*`
历来就是在宿主机上跑的。开窗逻辑照抄 `agent/service.py` 的 `class BitWindow`。

跑法：
    python3 -I /company/siteforge/docs/probes/2026-10-15-wildnutrition-quiz-li/wq_li_probe.py
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

WORKER = "192.168.1.222"
BIT_ID = "982d158b41b14fbb87754667859b4719"   # 8100（siteforge-222）那一颗
API = "http://%s:54345" % WORKER
CDP = "/opt/skills/auto-farm-skill/cdp"        # docs/probes/* 一直用的那个
CDP_REPO = "/company/siteforge/tools/cdp/cdp"  # 产物跑的那个（只在关键屏做一次交叉对照）
OUT = os.path.dirname(os.path.abspath(__file__))

URL_QUIZ = "https://www.wildnutrition.com/pages/what-supplements-should-i-take"
#: 账本那根链的**共同前缀**（三个 li 所在的那个 ul）
UL = ("#octane-quiz-wrapper > div:nth-of-type(1) > div:nth-of-type(3) > ul:nth-of-type(1)")
#: 账本里三步用到的 li（-3 组第 2、3、4 步）
LI = {3: UL + " > li:nth-of-type(3)", 4: UL + " > li:nth-of-type(4)", 1: UL + " > li:nth-of-type(1)"}
#: -2 组那两颗**带字**的（对照：同一根链、同一层）
LI2_H = UL + " > li:nth-of-type(6)"   # 'Hair, skin + nails…'
LI2_W = UL + " > li:nth-of-type(2)"   # 'Greater wellbeing…'
CONTINUE = "#octane-quiz-wrapper > div:nth-of-type(2) > button:nth-of-type(1)"

#: BitWindow.OPEN_ARGS / _LOOPBACK_RE —— 原样抄（见 agent/service.py:1491-1497）
OPEN_ARGS = ["--remote-debugging-address=0.0.0.0", "--remote-debugging-port=61129",
             "--remote-allow-origins=*", "--disable-session-crashed-bubble"]
LOOPBACK = re.compile(r"(\[::1\]|localhost|127\.0\.0\.1|0\.0\.0\.0)")
RATE_MARKS = ("降低接口请求频率", "接口请求频率", "请求频率")

HOST = PORT = None


def log(*a):
    print(*a, flush=True)


# ── Bit 那根线（照 BitWindow 抄） ─────────────────────────────────────────
def post(path, body, timeout=25):
    req = urllib.request.Request(API + path, data=json.dumps(body).encode("utf-8"),
                                 method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def probe_alive():
    """三态：pid / None（data 是空 dict = 窗口不在）/ '?'（问不出来）。"""
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
        log("  开窗被限流，退避 6 秒重试…")
        time.sleep(6)
    raise RuntimeError("开窗没成：%s" % said)


def current_ws():
    """窗口**现在**的地址；不在就给 None（绝不顺手开新的）—— BitWindow.current_ws。"""
    if probe_alive() is None:
        return None
    out = post("/browser/open", {"id": BIT_ID, "args": list(OPEN_ARGS), "queue": True}, timeout=70)
    data = out.get("data") if isinstance(out, dict) else None
    ws = (data or {}).get("ws") if isinstance(data, dict) else None
    if isinstance(out, dict) and out.get("success") is True and ws:
        return LOOPBACK.sub(WORKER, str(ws))
    return None


def close_window():
    """BitWindow.close()：先探活，不在就不算失败；关完**回读**确认真死了。"""
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
    log("  关窗：**没能确认**（探活还说它在）")
    return False


# ── cdp CLI 那根线 ────────────────────────────────────────────────────────
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
    tail = (err or "").strip().split("\n")
    tail = [t for t in tail if t.strip()][-2:]
    log("  [%s] rc=%s %.1fs %s" % (tag, rc, dt, ("｜" + " ｜ ".join(tail)) if tail else ""))
    return rc, out, err


# ── 裸 DOM：三个 li 到底长什么样 ──────────────────────────────────────────
RAW_JS = r"""
(function(){
  function tt(el,i){ while(el && el.nodeType!==1 && i--) el=el.previousElementSibling; return el; }
  function pathOf(el){
    var parts=[],n=el;
    while(n && n.nodeType===1 && n.tagName!=='HTML'){
      var p=n.parentElement; if(!p) break;
      var i=1,s=n; while((s=s.previousElementSibling)) if(s.tagName===n.tagName) i++;
      parts.unshift(n.tagName.toLowerCase()+':nth-of-type('+i+')');
      n=p;
    }
    return parts.join(' > ');
  }
  function attrsOf(el){
    var o={}; for(var i=0;i<el.attributes.length;i++) o[el.attributes[i].name]=String(el.attributes[i].value).slice(0,140);
    return o;
  }
  function boxOf(el){ var r=el.getBoundingClientRect();
    return [Math.round(r.left),Math.round(r.top),Math.round(r.width),Math.round(r.height)]; }
  function topAt(el){
    var b=boxOf(el);
    if(b[2]<=0||b[3]<=0) return '(盒子是 0 —— 量不到中心点)';
    var x=b[0]+b[2]/2,y=b[1]+b[3]/2;
    if(x<0||y<0||x>=window.innerWidth||y>=window.innerHeight) return '(中心点不在视口)';
    var hit=document.elementFromPoint(x,y);
    if(!hit) return '(elementFromPoint 没给东西)';
    return hit.tagName.toLowerCase()+((hit===el||el.contains(hit))?' (=自己/后代)':' (别人: '+pathOf(hit)+')');
  }
  var wrap=document.querySelector('#octane-quiz-wrapper');
  var out={url:location.href,title:document.title,ready:document.readyState,wrap:!!wrap,
           scrollY:Math.round(window.scrollY),
           text_head:(document.body?document.body.innerText:'').slice(0,300)};
  if(!wrap){ out.err='页面上没有 #octane-quiz-wrapper'; return JSON.stringify(out); }
  var uls=wrap.querySelectorAll('ul'); out.ul_count=uls.length; out.uls=[];
  for(var u=0;u<uls.length;u++){
    var ul=uls[u];
    var uo={path:pathOf(ul),attrs:attrsOf(ul),box:boxOf(ul),
            lis:[]};
    var lis=ul.querySelectorAll(':scope > li');
    for(var i=0;i<lis.length;i++){
      var li=lis[i],cs=getComputedStyle(li),kids=[];
      for(var k=0;k<li.children.length;k++){
        var c=li.children[k],ccs=getComputedStyle(c);
        kids.push({tag:c.tagName.toLowerCase(),attrs:attrsOf(c),
          text:(c.innerText||'').trim().slice(0,140),tc:(c.textContent||'').trim().slice(0,140),
          box:boxOf(c),disp:ccs.display,vis:ccs.visibility,op:ccs.opacity});
      }
      uo.lis.push({n:i+1,path:pathOf(li),attrs:attrsOf(li),
        text:(li.innerText||'').trim().slice(0,200),
        tc:(li.textContent||'').trim().slice(0,200),
        box:boxOf(li),rects:li.getClientRects().length,
        disp:cs.display,vis:cs.visibility,op:cs.opacity,pe:cs.pointerEvents,
        role:li.getAttribute('role')||'',aria:li.getAttribute('aria-label')||'',
        outer:li.outerHTML.slice(0,500),
        kids_n:li.children.length,kids:kids,top:topAt(li)});
    }
    out.uls.push(uo);
  }
  var btn=wrap.querySelectorAll('button'); out.buttons=[];
  for(var b=0;b<btn.length&&out.buttons.length<14;b++){
    var bt=btn[b];
    out.buttons.push({path:pathOf(bt),attrs:attrsOf(bt),
      text:(bt.innerText||'').trim().slice(0,80),box:boxOf(bt),disabled:!!bt.disabled});
  }
  return JSON.stringify(out);
})()
"""


def raw_dom(tag):
    d = ev(RAW_JS)
    with open(os.path.join(OUT, "rawdom-%s.json" % tag), "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=1)
    if not isinstance(d, dict) or d.get("__eval_failed__"):
        log("  [裸DOM %s] 没量到：%s" % (tag, json.dumps(d, ensure_ascii=False)[:200]))
        return d
    log("  [裸DOM %s] url=%s wrap=%s ul=%s scrollY=%s" % (tag, d.get("url"), d.get("wrap"),
                                                         d.get("ul_count"), d.get("scrollY")))
    log("      正文前 300 字：%s" % (d.get("text_head") or "").replace("\n", " | ")[:300])
    for uo in d.get("uls") or []:
        log("      UL %s  attrs=%s box=%s  li=%d"
            % (uo["path"], json.dumps(uo["attrs"], ensure_ascii=False), uo["box"], len(uo["lis"])))
        for li in uo["lis"]:
            log("        li#%d attrs=%s" % (li["n"], json.dumps(li["attrs"], ensure_ascii=False)))
            log("             text=%r  textContent=%r" % (li["text"], li["tc"]))
            log("             box=%s rects=%s disp=%s vis=%s op=%s pe=%s role=%r aria=%r"
                % (li["box"], li["rects"], li["disp"], li["vis"], li["op"], li["pe"],
                   li["role"], li["aria"]))
            log("             中心点上是：%s" % li["top"])
            log("             子元素 %d 个：%s" % (li["kids_n"],
                json.dumps(li["kids"], ensure_ascii=False)[:600]))
            log("             outerHTML[:500]=%s" % li["outer"][:500].replace("\n", " "))
    for bt in d.get("buttons") or []:
        log("      BUTTON %s text=%r disabled=%s attrs=%s"
            % (bt["path"][-60:], bt["text"], bt["disabled"],
               json.dumps(bt["attrs"], ensure_ascii=False)[:220]))
    return d


# ── observe 的卡 ─────────────────────────────────────────────────────────
def observe(tag, cross_check=False):
    rc, out, err = cdp(["observe"])
    if rc != 0 or not (out or "").strip():
        log("  [observe %s] **失败** rc=%s stderr=%s" % (tag, rc, (err or "")[:200]))
        return None
    try:
        m = json.loads(out)
    except Exception as exc:
        log("  [observe %s] 输出不是 JSON：%s" % (tag, exc))
        return None
    with open(os.path.join(OUT, "observe-%s.json" % tag), "w", encoding="utf-8") as fh:
        json.dump(m, fh, ensure_ascii=False, indent=1)
    acts = m.get("actions") or []
    inside = [a for a in acts if "#octane-quiz-wrapper" in (a.get("selector") or "")]
    log("  [observe %s] url=%s actions=%d（quiz 里 %d）diag=%d"
        % (tag, m.get("url"), len(acts), len(inside), len(m.get("diagnostics") or [])))
    txt = (m.get("page_text") or "")
    log("      判据：CONTINUE=%s ←BACK=%s ｜ goal问句=%s ｜ 正文前 120 字：%s"
        % ("CONTINUE" in txt, "← BACK" in txt,
           "What's your main health goal" in txt, txt[:120].replace("\n", " | ")))
    # ① 目标 li 的卡（原样，逐字）
    for a in inside:
        sel = a.get("selector") or ""
        if "> li" in sel or a.get("tag") == "li":
            log("    —— li 卡（原样）——")
            log(json.dumps(a, ensure_ascii=False, indent=2))
    # ② quiz 里其余动作，一行一条（能看到 Continue / Back / 别的选项）
    log("    —— quiz 里其余动作（一行一条）——")
    for a in inside:
        sel = a.get("selector") or ""
        if "> li" in sel or a.get("tag") == "li":
            continue
        log("      tag=%-6s role=%-7s text=%r aria=%r label=%r near=%s stab=%s vis=%s box=%s sel=%s"
            % (a.get("tag"), a.get("role"), a.get("text"), a.get("aria_label"), a.get("label"),
               a.get("nearby_text"), a.get("stability"), a.get("visible"), a.get("bbox"),
               sel[:90]))
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
                a2 = [a for a in (m2.get("actions") or [])
                      if "#octane-quiz-wrapper" in (a.get("selector") or "")
                      and (" > li" in (a.get("selector") or "") or a.get("tag") == "li")]
                log("    —— 交叉对照：仓里那个 cdp（%s）看到的 li 卡 ——" % CDP_REPO)
                for a in a2:
                    log(json.dumps(a, ensure_ascii=False, indent=2))
        else:
            log("    交叉对照没拿到（rc=%s）" % rc2)
    return m


def find_start_selector():
    """页面上那颗「开 quiz」的（拿不到就 None）—— 账本那根链是**别的页**上的，这儿自适应。"""
    js = r"""
    (function(){
      function pathOf(el){var parts=[],n=el;
        while(n&&n.nodeType===1&&n.tagName!=='HTML'){var p=n.parentElement;if(!p)break;
          var i=1,s=n;while((s=s.previousElementSibling))if(s.tagName===n.tagName)i++;
          parts.unshift(n.tagName.toLowerCase()+':nth-of-type('+i+')');n=p;}
        return parts.join(' > ');}
      var cands=document.querySelectorAll('a,button');
      for(var i=0;i<cands.length;i++){var e=cands[i],t=(e.innerText||'').trim(),r=e.getBoundingClientRect();
        if(r.width<2||r.height<2) continue;
        if(t.indexOf('Start the quiz')>=0||t.indexOf('Take the quiz')>=0)
          return JSON.stringify({sel:pathOf(e),text:t.slice(0,80)});}
      return JSON.stringify(null);
    })()
    """
    return ev(js)


def screen(tag, cross_check=False):
    log("\n===== 屏 %s =====" % tag)
    m = observe(tag, cross_check=cross_check)
    raw_dom(tag)
    return m


def main():
    global HOST, PORT
    started = time.strftime("%H:%M:%S")
    log("探针开始 %s" % started)
    pid0 = probe_alive()
    log("窗口探活：%s" % ("pid=%s" % pid0 if pid0 else "**不在**（data 空 dict）"))
    ws = current_ws()
    if not ws:
        log("窗口不在 ⇒ 按 BitWindow.open() 开一个（args 原样抄）")
        ws = _open()
    log("ws = %s" % ws)
    m = re.match(r"ws://([^:/]+):(\d+)", ws)
    HOST, PORT = m.group(1), m.group(2)
    log("cdp 目标 %s:%s（二进制 %s）" % (HOST, PORT, CDP))
    log("代理：不另设 —— 用这个窗口自己带的那条链（siteforge 的 :1081，PROVENANCE 是 US）")

    try:
        cmd(["navi", URL_QUIZ], "navi 直接进 quiz 页", timeout=90)
        # 等 quiz 落地
        for i in range(20):
            time.sleep(2)
            d = ev("JSON.stringify({w:!!document.querySelector('#octane-quiz-wrapper'),"
                   "t:(document.body?document.body.innerText:'').slice(0,200)})")
            if isinstance(d, dict) and d.get("w"):
                log("  等了 %ds，quiz 容器出现了" % ((i + 1) * 2))
                break
        else:
            log("  等了 40s，quiz 容器**还没出现** —— 照旧往下（下面会如实说量到了什么）")

        screen("A0-落地")

        st = find_start_selector()
        log("\n开 quiz 的元素：%s" % json.dumps(st, ensure_ascii=False))
        if isinstance(st, dict) and st.get("sel"):
            cmd(["click", st["sel"]], "click 开 quiz")
            time.sleep(4)
            screen("A1-开完quiz")

        # 组 -2：同一根链上两颗**带字**的（对照）
        cmd(["click", LI2_H], "组-2 第1步 click li6", timeout=90)
        time.sleep(3)
        screen("B1-click-li6")
        cmd(["scroll", LI2_H], "组-2 第2步 scroll li6", timeout=90)
        time.sleep(2)
        screen("B2-scroll-li6")
        cmd(["click", LI2_H], "组-2 第3步 click li6", timeout=90)
        time.sleep(3)
        screen("B3-click-li6-again")
        cmd(["scroll", LI2_W], "组-2 第4步 scroll li2", timeout=90)
        time.sleep(2)
        screen("B4-scroll-li2")
        cmd(["click", LI2_W], "组-2 第5步 click li2", timeout=90)
        time.sleep(3)
        screen("C0-组-2走完（这一屏就是 -3 组的 when：CONTINUE ← BACK）", cross_check=True)

        # 组 -3：第 1 步 Continue，然后三颗**没名字**的
        cmd(["click", CONTINUE], "组-3 第1步 click Continue", timeout=90)
        time.sleep(4)
        screen("D1-点完Continue（账本第2步 li3 所在的屏）", cross_check=True)
        cmd(["click", LI[3]], "组-3 第2步 click li3", timeout=90)
        time.sleep(4)
        screen("D2-点完li3（账本第3步 li4 所在的屏）")
        cmd(["click", LI[4]], "组-3 第3步 click li4", timeout=90)
        time.sleep(4)
        screen("D3-点完li4（账本第4步 li1 所在的屏）")
        log("\n★ 到此为止：**不点** li1、**不填**邮箱、**不点** submit（边界）")
    finally:
        log("\n窗口收尾：")
        ok = close_window()
        log("窗口已关：%s" % ok)


if __name__ == "__main__":
    main()
