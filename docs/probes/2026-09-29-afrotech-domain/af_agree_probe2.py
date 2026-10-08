#!/usr/bin/env python3
"""afrotech 收口：① 点掉 AGREE（这次真点）② 老选择器还卡不卡 ③ `footer button[…]` 是不是秒回。

三个假设，一次窗里分开验：
  H1（用户提的）：cookie 弹层（Quantcast `qc-cmp-cleanslate` + `#accept-btn`）= 卡的原因
                  ⇒ 点掉它之后，老选择器应该变快。**如果没变快，H1 就被否掉。**
  H2（我量的）：`__cdpQ` 选中的是个 **0×0 隐藏重复件**（rect=[0,0,0,0], 在 [hidden] 里）
                  ⇒ 与弹层无关，落地时就已经是死的；换 `footer button[…]` 应该秒回。
  H3：隐藏重复件不止一个 —— 把同名 button 全列出来。

不点提交按钮本身（真订阅）；只点 AGREE（= 同意 cookie，用户的指示）。
"""
import json
import os
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
BTN_FOOTER = 'footer button[aria-label="Subscribe to Newsletter"]'
AGREE = '#accept-btn'
CDP = os.path.join(SKILL, "cdp")

E_ROOTS = (r"""
var roots=[document];
(function walk(r){var all;try{all=r.querySelectorAll('*')}catch(e){return}
 for(var i=0;i<all.length;i++){var sr=all[i].shadowRoot;if(sr){roots.push(sr);walk(sr)}}})(document);
""")

#: 同名 button 全清单（谁是真身、谁是隐藏重复件）
E_ALLBTN = E_ROOTS + r"""
var SEL=%(btn)s,out=[];
for(var i=0;i<roots.length;i++){var fs;try{fs=roots[i].querySelectorAll(SEL)}catch(e){continue}
 for(var j=0;j<fs.length;j++){var el=fs[j],r=el.getBoundingClientRect();
  out.push([i,[Math.round(r.x),Math.round(r.y),Math.round(r.width),Math.round(r.height)],
   getComputedStyle(el).display,!!el.offsetParent,el.getClientRects().length,
   (el.closest('footer')?'F':'-')+(el.closest('[hidden],[aria-hidden="true"]')?'H':'-')+
   (el.closest('dialog,[role="dialog"]')?'D':'-')]);}}
return JSON.stringify({y:Math.round(scrollY),roots:roots.length,n:out.length,l:out.slice(0,6)});
""" % {"btn": json.dumps(BTN)}

#: 弹层还在不在
E_CMP = r"""
var el=document.querySelector('qc-cmp-cleanslate,.qc-cmp-cleanslate,#accept-btn');
return JSON.stringify({alive:!!el,y:Math.round(scrollY),
  rect:el?[Math.round(el.getBoundingClientRect().x),Math.round(el.getBoundingClientRect().y),
           Math.round(el.getBoundingClientRect().width),Math.round(el.getBoundingClientRect().height)]:null,
  z:el?getComputedStyle(el).zIndex:null});
"""

#: 候选选择器的目标长什么样（真身）
E_FOOTER_BTN = r"""
var el=document.querySelector(%(fsel)s);
if(!el)return JSON.stringify({found:0});
var r=el.getBoundingClientRect();
var va=(Math.min(r.right,innerWidth)-Math.max(r.left,0))*(Math.min(r.bottom,innerHeight)-Math.max(r.top,0));
var ta=r.width*r.height;
return JSON.stringify({found:1,rect:[Math.round(r.x),Math.round(r.y),Math.round(r.width),Math.round(r.height)],
 ratio:ta>0?+(va/ta).toFixed(2):-1,offParent:!!el.offsetParent});
""" % {"fsel": json.dumps(BTN_FOOTER)}


def ev(cdp, js):
    """回执可能是「被包了两层的 JSON 字符串」——两种都认，别再静默跳过。"""
    r = cdp.eval("(function(){%s})()" % js, "")
    for _ in range(3):
        if isinstance(r, (dict, list)):
            return r
        s = str(r or "").strip()
        if not s:
            return ""
        if s[0] == '"' and s[-1] == '"':
            try:
                r = json.loads(s)
                continue
            except Exception:
                return s
        for line in s.split("\n"):
            line = line.strip()
            if not line or line.startswith(("ERROR:", "Usage:", "Flags:")):
                continue
            try:
                return json.loads(line)
            except Exception:
                pass
        return s
    return r


def show(tag, m):
    print("  [%s] %s" % (tag, json.dumps(m, ensure_ascii=False)[:400] if not isinstance(m, str) else m[:400]), flush=True)


def timed(cdp, argv, cap=75):
    t0 = time.time()
    try:
        p = subprocess.run([CDP] + argv + ["--host", cdp.host, "--port", str(cdp.port)],
                           capture_output=True, text=True, timeout=cap)
        return time.time() - t0, p.returncode, (p.stdout + p.stderr).strip()[:120]
    except subprocess.TimeoutExpired:
        return time.time() - t0, "TIMEOUT(%ds)" % cap, ""


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

        print("\n===== ⓪ 落地：同名 button 有几个、真身在哪（H2/H3）=====", flush=True)
        show("同名按钮清单", ev(cdp, E_ALLBTN))
        show("footer 真身", ev(cdp, E_FOOTER_BTN))

        print("\n===== ① 长滚到输入框（把 cookie 弹层勾出来）=====", flush=True)
        d, rc, _ = timed(cdp, ["scroll", INPUT])
        print("  scroll 输入框：%.1f 秒 rc=%s" % (d, rc), flush=True)
        rows.append(("scroll 输入框", d, rc))
        show("弹层", ev(cdp, E_CMP))

        print("\n===== ② 点掉 AGREE（这次真点）=====", flush=True)
        d, rc, tail = timed(cdp, ["click", AGREE])
        print("  click %s：%.1f 秒 rc=%s %s" % (AGREE, d, rc, tail[:90]), flush=True)
        rows.append(("click AGREE", d, rc))
        show("点完 AGREE 后弹层", ev(cdp, E_CMP))
        show("点完 AGREE 后 footer 真身", ev(cdp, E_FOOTER_BTN))

        print("\n===== ③ H1 检验：老选择器（那个 0×0 隐藏件）还卡不卡 =====", flush=True)
        d, rc, _ = timed(cdp, ["scroll", BTN])
        print("  scroll %s：%.1f 秒 rc=%s" % (BTN[:34], d, rc), flush=True)
        rows.append(("★老选择器 scroll", d, rc))
        show("滚后状态", ev(cdp, E_ALLBTN))

        print("\n===== ④ H2 检验：footer 限定之后呢 =====", flush=True)
        d, rc, tail = timed(cdp, ["scroll", BTN_FOOTER])
        print("  scroll footer button[...]：%.1f 秒 rc=%s %s" % (d, rc, tail[:80]), flush=True)
        rows.append(("★★footer 限定 scroll", d, rc))
        show("滚后状态", ev(cdp, E_FOOTER_BTN))
        d2, rc2, _ = timed(cdp, ["scroll", BTN_FOOTER])
        print("  再来一次：%.1f 秒 rc=%s" % (d2, rc2), flush=True)
        rows.append(("★★footer 限定 scroll #2", d2, rc2))

        print("\n===== 汇总 =====", flush=True)
        for name, d, rc in rows:
            print("  %-24s %6.1f 秒  rc=%s" % (name, d, rc), flush=True)
    finally:
        try:
            mgr.close()
        except Exception as exc:
            print("关窗报错:", exc, flush=True)
        print("窗口已关", flush=True)


if __name__ == "__main__":
    main()
