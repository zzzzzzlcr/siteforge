#!/usr/bin/env python3
"""afrotech 定点探测：钉死「cdp scroll 卡死 30 秒」那条链。

要回答：
  ① 视口最底下（cdp 上滑的起点那儿）站着的是谁 —— 是不是 `.htlad-adhesion`；
  ② 复现：同一条命令 `cdp scroll 'input[placeholder="Enter e-mail address"]'`，量它多久返回、页面滚没滚；
  ③ 顺手核第二个状态的判据：/newsletter 那页的正文里有没有
     'Sign up here for AfroTech newsletter Follow the'（骨架判据，按 innerText 折行归一后比）。

只读 + 滚动；不填任何框、不提交。窗关在 finally 里。
"""
import base64
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

SKILL = "/opt/skills/auto-farm-skill"
OUT = "/tmp/af_probe"
os.makedirs(OUT, exist_ok=True)
sys.path.insert(0, os.path.join(SKILL, "src"))
sys.path.insert(0, os.path.join(SKILL, "forms"))
from browser import BrowserManager, DeviceConfig, ProxyConfig  # noqa: E402
from common import CDPHelper  # noqa: E402

URL = "https://afrotech.com/the-gathering-spots-ryan-wilson-on-afrotech"
SEL = 'input[placeholder="Enter e-mail address"]'
CDP = os.path.join(SKILL, "cdp")


def ev(cdp, js):
    r = cdp.eval("(function(){%s})()" % js, "")
    for line in str(r).strip().split("\n"):
        line = line.strip()
        if not line or line.startswith(("ERROR:", "Usage:", "Flags:")):
            continue
        try:
            return json.loads(line)
        except Exception:
            return line.strip('"')
    return ""


BOTTOM_JS = (
    "var vw=window.innerWidth, vh=window.innerHeight;"
    "var out={vw:vw, vh:vh, y:window.scrollY, docH:document.documentElement.scrollHeight};"
    "var pts=[[Math.round(vw*0.75), vh-4],[Math.round(vw*0.75), vh-14],[Math.round(vw*0.8), vh-30],"
    "[Math.round(vw*0.5), vh-4],[Math.round(vw*0.75), vh*0.5]];"
    "out.at=pts.map(function(p){var e=document.elementFromPoint(p[0],p[1]);"
    "return p[0]+','+p[1]+' -> '+(e?(e.tagName+(e.id?('#'+e.id):'')+'.'+String(e.className||'').split(' ').slice(0,2).join('.')):'(null)')});"
    "var el=document.querySelector('.htlad-adhesion');"
    "out.adhesion=el?({rect:[Math.round(el.getBoundingClientRect().x),Math.round(el.getBoundingClientRect().y),"
    "Math.round(el.getBoundingClientRect().width),Math.round(el.getBoundingClientRect().height)],"
    "pe:getComputedStyle(el).pointerEvents, pos:getComputedStyle(el).position, z:getComputedStyle(el).zIndex}):'没有这个元素';"
    "var inp=document.querySelector(%s);"
    "if(inp){var r=inp.getBoundingClientRect();"
    "var va=(Math.min(r.right,vw)-Math.max(r.left,0))*(Math.min(r.bottom,vh)-Math.max(r.top,0));"
    "var ta=r.width*r.height;"
    "out.input={rect:[Math.round(r.x),Math.round(r.y),Math.round(r.width),Math.round(r.height)],"
    "ratio:ta>0?+(va/ta).toFixed(3):0};}else{out.input='没找到';}"
    "var fixeds=[];var all=document.querySelectorAll('body *');"
    "for(var i=0;i<all.length;i++){var s=getComputedStyle(all[i]);"
    "if(s.position==='fixed'||s.position==='sticky'){var rr=all[i].getBoundingClientRect();"
    "if(rr.height>10&&rr.width>50&&rr.bottom>vh-160&&rr.bottom<=vh+2){"
    "fixeds.push(all[i].tagName+(all[i].id?('#'+all[i].id):'')+'.'+String(all[i].className||'').split(' ')[0]"
    "+' ['+Math.round(rr.x)+','+Math.round(rr.y)+','+Math.round(rr.width)+','+Math.round(rr.height)+'] '+s.position);}}}"
    "out.bottomLayers=fixeds.slice(0,6);"
    "return JSON.stringify(out);" % json.dumps(SEL))


def main():
    mgr = BrowserManager(bit_sh=os.path.join(SKILL, "bit.sh"), worker_ip="192.168.1.222",
                         bit_id="41bde47e4cfd4eb19a7a0e5cf941a0ae", skill_dir=SKILL)
    dev = DeviceConfig(ua=("Mozilla/5.0 (iPhone; CPU iPhone OS 16_7_8 like Mac OS X) "
                           "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"),
                       sw=375, sh=812, dpr=3, core=4, mem=8)
    ws = mgr.open(dev, ProxyConfig(server="192.168.1.51", port=1081))
    print("窗口已开（代理 51:1081）", flush=True)
    cdp = CDPHelper(ws)
    try:
        cdp.navigate(URL)
        time.sleep(12)                      # 等正文 + 广告位起来
        print("地址:", (ev(cdp, "return location.href;") or "")[:100], flush=True)
        print("readyState:", ev(cdp, "return document.readyState;"),
              " 正文", len(ev(cdp, "return document.body?document.body.innerText:'';") or ""), "字", flush=True)

        print("\n===== ① 视口底部站着谁 / 元素可见比 =====", flush=True)
        m = ev(cdp, BOTTOM_JS)
        try:
            m = json.loads(m)
        except Exception:
            m = {}
        for k in ("vw", "vh", "y", "docH"):
            print("  %-10s %s" % (k, m.get(k)), flush=True)
        for line in (m.get("at") or []):
            print("  elementFromPoint:", line, flush=True)
        print("  .htlad-adhesion:", json.dumps(m.get("adhesion"), ensure_ascii=False), flush=True)
        print("  订阅框:", json.dumps(m.get("input"), ensure_ascii=False), flush=True)
        print("  贴在底部那层的候选:", json.dumps(m.get("bottomLayers"), ensure_ascii=False), flush=True)

        print("\n===== ② 复现：同一条 cdp scroll（生产用的就是这个二进制）=====", flush=True)
        for att in (1, 2):
            y0 = ev(cdp, "return String(window.scrollY);")
            t0 = time.time()
            try:
                p = subprocess.run([CDP, "scroll", SEL, "--host", cdp.host, "--port", str(cdp.port)],
                                   capture_output=True, text=True, timeout=45)
                dur, rc, tail = time.time() - t0, p.returncode, (p.stdout + p.stderr).strip()[-200:]
            except subprocess.TimeoutExpired:
                dur, rc, tail = time.time() - t0, "TIMEOUT(45s)", "(45 秒没返回 —— 生产那边是 30 秒上限)"
            y1 = ev(cdp, "return String(window.scrollY);")
            mm = ev(cdp, BOTTOM_JS)
            try:
                mm = json.loads(mm)
            except Exception:
                mm = {}
            print("  第 %d 次：耗时 %.1f 秒，退出码 %s" % (att, dur, rc), flush=True)
            print("     回执尾部:", tail[:160], flush=True)
            print("     scrollY: %s → %s ｜ 订阅框现在: %s" % (y0, y1, json.dumps(mm.get("input"), ensure_ascii=False)), flush=True)
            if att == 1:
                time.sleep(2)

        print("\n===== ③ /newsletter 那页的判据核一遍（只读，不填不提交）=====", flush=True)
        cdp.navigate("https://afrotech.com/newsletter")
        time.sleep(10)
        txt = ev(cdp, "return document.body?document.body.innerText:'';") or ""
        norm = re.sub(r"\s+", " ", txt)
        want = "Sign up here for AfroTech newsletter Follow the"
        print("  地址:", (ev(cdp, "return location.href;") or "")[:80], flush=True)
        print("  正文 %d 字，开头: %r" % (len(txt), txt[:120]), flush=True)
        for frag in ["Sign up here", "Sign up here for AfroTech newsletter", want,
                     "Follow the latest updates"]:
            print("    %-42s %s" % (frag, "在" if frag in norm else "**不在**"), flush=True)
        s = cdp.screenshot()
        if s:
            open(os.path.join(OUT, "newsletter.png"), "wb").write(base64.b64decode(s))
            print("  截图:", os.path.join(OUT, "newsletter.png"), flush=True)
    finally:
        try:
            mgr.close()
        except Exception as exc:
            print("关窗报错:", exc, flush=True)
        print("窗口已关", flush=True)


if __name__ == "__main__":
    main()
