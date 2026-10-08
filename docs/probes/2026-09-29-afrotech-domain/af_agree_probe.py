#!/usr/bin/env python3
"""afrotech：把「滑动久了会出的 cookie 弹层（AGREE）」摆到台面上 —— 找到它、点掉它、
再看那个按钮还卡不卡。

顺序（每一步都量小的、分开的 eval，免得再被 cdp 的输出截断吃掉）：
  ⓪ 落地         量 y/docH/根数 + 被 __cdpQ 选中的元素
  ① cdp scroll 输入框（长滚 27 秒，用户说弹层是"滚久了"才出）
  ② 找弹层（含 shadow root 里的 AGREE/Accept）→ 打印它    ← 点之前
  ③ cdp click 那个 AGREE                                   ← 点掉它
  ④ cdp scroll 按钮 ×2   ← 判别：点掉弹层之后它还卡不卡
另测一次 cdp eval 的输出截断长度（3000 个 x 能不能回来）。

不点提交按钮（button[aria-label="Subscribe to Newsletter"] = 真订阅）。
"""
import json
import os
import re
import subprocess
import sys
import time

SKILL = "/opt/skills/auto-farm-skill"
sys.path.insert(0, os.path.join(SKILL, "src"))
sys.path.insert(0, os.path.join(SKILL, "forms"))
from browser import BrowserManager, DeviceConfig, ProxyConfig  # noqa: E402
from common import CDPHelper  # noqa: E402

URL = "https://afrotech.com/the-gathering-spots-ryan-wilson-on-afrotech"
INPUT = 'input[placeholder="Enter e-mail address"]'
BTN = 'button[aria-label="Subscribe to Newsletter"]'
CDP = os.path.join(SKILL, "cdp")

#: 每个 eval 都短（cdp 的 stdout 会把长回执截断 —— 今天栽了两次）
E_BASE = r"""
var roots=[document];
(function walk(r){var all;try{all=r.querySelectorAll('*')}catch(e){return}
 for(var i=0;i<all.length;i++){var sr=all[i].shadowRoot;if(sr){roots.push(sr);walk(sr)}}})(document);
var SEL=%(btn)s,picked=null,rootIdx=-1;
for(var i=0;i<roots.length;i++){var f;try{f=roots[i].querySelector(SEL)}catch(e){continue}
 if(f){picked=f;rootIdx=i;break}}
function rr(el){var r=el.getBoundingClientRect();
 return [Math.round(r.x),Math.round(r.y),Math.round(r.width),Math.round(r.height)];}
function ratio(el){var r=el.getBoundingClientRect();
 var va=(Math.min(r.right,innerWidth)-Math.max(r.left,0))*(Math.min(r.bottom,innerHeight)-Math.max(r.top,0));
 var ta=r.width*r.height;return ta>0?+(va/ta).toFixed(2):-1;}
function where(el){return (el.closest('footer')?'F':'')+(el.closest('dialog,[role="dialog"],[class*="modal" i],[class*="popup" i]')?'M':'')+
 (el.closest('[hidden],[aria-hidden="true"]')?'H':'');}
var o={y:Math.round(scrollY),docH:document.documentElement.scrollHeight,roots:roots.length};
if(picked){o.pick=[picked.tagName,rr(picked),ratio(picked),getComputedStyle(picked).display,
  !!picked.offsetParent,picked.getClientRects().length,where(picked),rootIdx];}
return JSON.stringify(o);
""" % {"btn": json.dumps(BTN)}

#: 弹层：含 agree/accept/consent/cookie 字样、且占地方的元素（含 shadow root 内）
E_CONSENT = r"""
var roots=[document];
(function walk(r){var all;try{all=r.querySelectorAll('*')}catch(e){return}
 for(var i=0;i<all.length;i++){var sr=all[i].shadowRoot;if(sr){roots.push(sr);walk(sr)}}})(document);
var out=[],re2=/agree|accept|consent|cookie|got it|allow all/i;
for(var i=0;i<roots.length;i++){
  var els;try{els=roots[i].querySelectorAll('button,[role="button"],a,input[type="button"],input[type="submit"]')}catch(e){continue}
  for(var j=0;j<els.length&&out.length<6;j++){
    var el=els[j],r=el.getBoundingClientRect();
    if(r.width<20||r.height<10)continue;
    var txt=((el.innerText||el.value||el.getAttribute('aria-label')||'')+'').replace(/\s+/g,' ').trim();
    if(!re2.test(txt))continue;
    if(!el.offsetParent&&getComputedStyle(el).position!=='fixed')continue;
    var host=(el.getRootNode()===document)?'light':(el.getRootNode().host?el.getRootNode().host.tagName:'shadow');
    out.push({root:i,host:host,txt:txt.slice(0,40),rect:[Math.round(r.x),Math.round(r.y),Math.round(r.width),Math.round(r.height)],
              tag:el.tagName,cls:String(el.className||'').slice(0,50),id:el.id||''});
  }}
return JSON.stringify({n:out.length,list:out});
"""

#: 贴边固定层（弹层最爱待的地方）+ 手势起点上是谁
E_FIXED = r"""
var out=[],all=document.querySelectorAll('body *');
for(var i=0;i<all.length;i++){var st=getComputedStyle(all[i]);
 if(st.position!=='fixed'&&st.position!=='sticky')continue;
 var r=all[i].getBoundingClientRect();
 if(r.height<10||r.width<40)continue;
 if(r.bottom<=innerHeight-250&&r.top>=200)continue;
 out.push([all[i].tagName+(all[i].id?('#'+all[i].id):'')+'.'+String(all[i].className||'').trim().split(/\s+/)[0],
   [Math.round(r.x),Math.round(r.y),Math.round(r.width),Math.round(r.height)],st.position,st.zIndex]);}
var pts=[],vx=Math.round(innerWidth*0.75);
var ys=[innerHeight-4,innerHeight-20,innerHeight-60,innerHeight-160];
for(var k=0;k<ys.length;k++){var h=document.elementFromPoint(vx,ys[k]);
 pts.push(vx+','+ys[k]+'='+(h?(h.tagName+(h.id?('#'+h.id):'')+'.'+String(h.className||'').trim().split(/\s+/)[0]):'null'));}
return JSON.stringify({fixed:out.slice(0,6),start:pts});
"""

E_LEN = "return 'x'.repeat(3000);"


def ev(cdp, js, label=""):
    """短回执。**别**在这里做截断打印——先拿到原样长度，才知道 cdp 截没截。"""
    r = cdp.eval("(function(){%s})()" % js, "")
    if isinstance(r, (dict, list)):
        return r
    s = str(r or "")
    for line in s.split("\n"):
        line = line.strip()
        if not line or line.startswith(("ERROR:", "Usage:", "Flags:")):
            continue
        try:
            return json.loads(line)
        except Exception:
            pass
    if label:
        print("      [%s] 原样回执(%d 字节): %s" % (label, len(s), s[:200]), flush=True)
    return s


def timed(cdp, argv, cap=75):
    t0 = time.time()
    try:
        p = subprocess.run([CDP] + argv + ["--host", cdp.host, "--port", str(cdp.port)],
                           capture_output=True, text=True, timeout=cap)
        return time.time() - t0, p.returncode, (p.stdout + p.stderr).strip()[:140]
    except subprocess.TimeoutExpired:
        return time.time() - t0, "TIMEOUT(%ds)" % cap, ""


def base(cdp, tag):
    m = ev(cdp, E_BASE, tag)
    print("  [%s] %s" % (tag, json.dumps(m, ensure_ascii=False)[:260]), flush=True)
    return m


def main():
    mgr = BrowserManager(bit_sh=os.path.join(SKILL, "bit.sh"), worker_ip="192.168.1.222",
                         bit_id="41bde47e4cfd4eb19a7a0e5cf941a0ae", skill_dir=SKILL)
    dev = DeviceConfig(ua=("Mozilla/5.0 (iPhone; CPU iPhone OS 16_7_8 like Mac OS X) "
                           "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"),
                       sw=375, sh=812, dpr=3, core=4, mem=8)
    ws = mgr.open(dev, ProxyConfig(server="192.168.1.51", port=1081))
    print("窗口已开（代理 51:1081）", flush=True)
    cdp = CDPHelper(ws)
    rows = []
    try:
        cdp.navigate(URL)
        time.sleep(12)
        print("\n===== cdp eval 的输出上限（3000 个 x 回不回得来）=====", flush=True)
        r = ev(cdp, E_LEN)
        print("  回来 %d 字节：%s..." % (len(str(r)), str(r)[:60]), flush=True)

        print("\n===== ⓪ 落地 =====", flush=True)
        base(cdp, "落地")

        print("\n===== ① 生产 step 1：长滚到输入框 =====", flush=True)
        d, rc, _ = timed(cdp, ["scroll", INPUT])
        print("  scroll 输入框：%.1f 秒 rc=%s" % (d, rc), flush=True)
        rows.append(("scroll 输入框", d, rc))
        base(cdp, "滚完")

        print("\n===== ② 弹层在不在（含 shadow root）=====", flush=True)
        c = ev(cdp, E_CONSENT)
        print("  同意类按钮: %s" % json.dumps(c, ensure_ascii=False)[:700], flush=True)
        f = ev(cdp, E_FIXED)
        print("  贴边固定层/手势起点: %s" % json.dumps(f, ensure_ascii=False)[:700], flush=True)

        print("\n===== ③ 点掉它（AGREE）=====", flush=True)
        sel = None
        if isinstance(c, dict):
            for it in c.get("list") or []:
                if re.search(r"agree|accept|allow", it.get("txt", ""), re.I):
                    sel = it
                    break
        if sel:
            css = ("#" + sel["id"]) if sel["id"] else (sel["tag"].lower() + ("." + sel["cls"].split()[0] if sel.get("cls") else ""))
            print("  要点的: %r  -> 选择器 %r" % (sel["txt"], css), flush=True)
            d, rc, tail = timed(cdp, ["click", css])
            print("  click %s：%.1f 秒 rc=%s %s" % (css, d, rc, tail[:90]), flush=True)
            rows.append(("click AGREE", d, rc))
            base(cdp, "点完 AGREE")
        else:
            print("  没找到 agree/accept 类按钮（见上面 ② 的清单）", flush=True)

        print("\n===== ④ 判别：点掉弹层之后，那个按钮还卡不卡 =====", flush=True)
        for k in (1, 2):
            d, rc, _ = timed(cdp, ["scroll", BTN])
            print("  第 %d 次 scroll 按钮：%.1f 秒 rc=%s" % (k, d, rc), flush=True)
            rows.append(("★scroll 按钮 #%d" % k, d, rc))
            base(cdp, "按钮后%d" % k)

        print("\n===== 汇总 =====", flush=True)
        for name, d, rc in rows:
            print("  %-22s %6.1f 秒  rc=%s" % (name, d, rc), flush=True)
    finally:
        try:
            mgr.close()
        except Exception as exc:
            print("关窗报错:", exc, flush=True)
        print("窗口已关", flush=True)


if __name__ == "__main__":
    main()
