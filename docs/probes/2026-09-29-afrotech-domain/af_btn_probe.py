#!/usr/bin/env python3
"""afrotech 第 3 步那 30~44 秒：**带着「滑动久了会出 cookie/弹层」这个维度**量。

三问：
  ① `__cdpQ('button[aria-label="Subscribe to Newsletter"]')` 选中哪个元素、矩形多少、
     在不在弹窗里？（比例判据 = |视口∩矩形| / |矩形|，矩形一离谱就永远过不了 0.8）
  ② 长滚之后有没有 cookie/同意弹层？它盖在**手势起点**（视口最底下那条 y≈792~812）上吗？
  ③ 每一滚前后 scrollY 怎么动（看是不是"滚反方向"）。

**只滚不点按钮**（那是真提交）。截图直接调二进制（helper 指的 /company/cdpcli/cdp 没这条命令）。
"""
import base64
import json
import os
import subprocess
import sys
import time

SKILL = "/opt/skills/auto-farm-skill"
OUT = "/tmp/af_probe"
os.makedirs(OUT, exist_ok=True)
sys.path.insert(0, os.path.join(SKILL, "src"))
sys.path.insert(0, os.path.join(SKILL, "forms"))
from browser import BrowserManager, DeviceConfig, ProxyConfig  # noqa: E402
from common import CDPHelper  # noqa: E402

URL = "https://afrotech.com/the-gathering-spots-ryan-wilson-on-afrotech"
INPUT = 'input[placeholder="Enter e-mail address"]'
BTN = 'button[aria-label="Subscribe to Newsletter"]'
CDP = os.path.join(SKILL, "cdp")

JS = r"""
var SEL=%(btn)s;
function box(el){var r=el.getBoundingClientRect();
  var va=(Math.min(r.right,innerWidth)-Math.max(r.left,0))*(Math.min(r.bottom,innerHeight)-Math.max(r.top,0));
  var ta=r.width*r.height;
  return {rect:[Math.round(r.x),Math.round(r.y),Math.round(r.width),Math.round(r.height)],
          ratio:(ta>0?+(va/ta).toFixed(3):'NaN(面积0)')};}
function desc(el){return el.tagName+(el.id?('#'+el.id):'')+
  (el.className?('.'+String(el.className).trim().split(/\s+/).slice(0,2).join('.')):'');}
function where(el){return (el.closest('footer')?'footer':'')+
  (el.closest('dialog,[role="dialog"],[class*="modal" i],[id*="modal" i],[class*="popup" i]')?'/弹窗':'')+
  (el.closest('[hidden],[aria-hidden="true"]')?'/hidden':'');}
var out={y:Math.round(scrollY),docH:document.documentElement.scrollHeight,vw:innerWidth,vh:innerHeight,
         bodyOverflow:getComputedStyle(document.body).overflow};
/* ① 复刻 internal/shadow.go:38 的 __cdpQ：light DOM 先，再逐个 shadow root */
var roots=[document];
(function walk(r){var all;try{all=r.querySelectorAll('*')}catch(e){return}
 for(var i=0;i<all.length;i++){var sr=all[i].shadowRoot;if(sr){roots.push(sr);walk(sr)}}})(document);
out.nRoots=roots.length;
var picked=null,pickedRoot=-1;
for(var i=0;i<roots.length;i++){var f;try{f=roots[i].querySelector(SEL)}catch(e){continue}
 if(f){picked=f;pickedRoot=i;break}}
out.picked=null;
if(picked){var p=box(picked),cs=getComputedStyle(picked);
  out.picked={root:pickedRoot,tag:picked.tagName,rect:p.rect,ratio:p.ratio,disp:cs.display,
    vis:cs.visibility,op:cs.opacity,offParent:!!picked.offsetParent,nRects:picked.getClientRects().length,
    where:where(picked),html:picked.outerHTML.slice(0,150),chain:[]};
  var e=picked;
  for(var k=0;k<8&&e;k++){var s=getComputedStyle(e);
    out.picked.chain.push(desc(e)+' ['+(e.getRootNode()===document?'light':'shadow')+'] disp='+
      s.display+' vis='+s.visibility+' op='+s.opacity+' rect='+JSON.stringify(box(e).rect));
    e=e.parentElement;}}
/* 同名元素逐个（含 shadow）*/
out.all=[];
for(var i=0;i<roots.length;i++){var fs;try{fs=roots[i].querySelectorAll(SEL)}catch(e){continue}
 for(var j=0;j<fs.length;j++){var el=fs[j],q=box(el),s=getComputedStyle(el);
  out.all.push({root:i,tag:el.tagName,rect:q.rect,ratio:q.ratio,disp:s.display,vis:s.visibility,
    offParent:!!el.offsetParent,where:where(el),html:el.outerHTML.slice(0,130)});}}
/* 还有没有别的订阅框（那个 placeholder="Email" 的可疑兄弟）*/
out.otherSubs=[];
var os2=document.querySelectorAll('input[placeholder="Email"],input[type="email"]');
for(var i=0;i<os2.length&&i<8;i++){var el2=os2[i],q2=box(el2),s2=getComputedStyle(el2);
  out.otherSubs.push({tag:el2.tagName,type:el2.type||'',ph:el2.placeholder||'',name:el2.name||'',
    rect:q2.rect,ratio:q2.ratio,disp:s2.display,offParent:!!el2.offsetParent,where:where(el2)});}
/* ② 手势起点上站着谁（上滑：x≈0.75vw，y≈vh-4 / -20 / -60）*/
out.gestureStart=[];
var pts=[[Math.round(innerWidth*0.75),innerHeight-4],[Math.round(innerWidth*0.75),innerHeight-20],
         [Math.round(innerWidth*0.75),innerHeight-60],[Math.round(innerWidth*0.85),innerHeight-4]];
for(var i=0;i<pts.length;i++){var h=document.elementFromPoint(pts[i][0],pts[i][1]);
  out.gestureStart.push(pts[i][0]+','+pts[i][1]+' -> '+(h?desc(h):'(null)'));}
/* ③ 贴边固定层 */
out.fixedLayers=[];
var all2=document.querySelectorAll('body *');
for(var i=0;i<all2.length;i++){var st=getComputedStyle(all2[i]);
  if(st.position!=='fixed'&&st.position!=='sticky')continue;
  var rr=all2[i].getBoundingClientRect();
  if(rr.height<8||rr.width<40)continue;
  if(rr.bottom>innerHeight-240||rr.top<200){
    out.fixedLayers.push(desc(all2[i])+' rect='+JSON.stringify([Math.round(rr.x),Math.round(rr.y),
      Math.round(rr.width),Math.round(rr.height)])+' '+st.position+' z='+st.zIndex);}}
out.fixedLayers=out.fixedLayers.slice(0,8);
/* ④ 同意/cookie 组件 + 正文里的 cookie 字样 */
out.consent=[];
var csel=['#onetrust-banner-sdk','[id*="cookie" i]','[class*="cookie" i]','[id*="consent" i]',
  '[class*="consent" i]','[id*="gdpr" i]','#didomi-host','[class*="cmp" i]','[class*="privacy" i]'];
for(var i=0;i<csel.length;i++){var fs2;try{fs2=document.querySelectorAll(csel[i])}catch(e){continue}
  for(var j=0;j<fs2.length&&j<3;j++){var el3=fs2[j],rr3=el3.getBoundingClientRect(),st3=getComputedStyle(el3);
    if(rr3.width*rr3.height<100)continue;
    out.consent.push(csel[i]+' -> <'+desc(el3)+'> rect='+JSON.stringify([Math.round(rr3.x),Math.round(rr3.y),
      Math.round(rr3.width),Math.round(rr3.height)])+' disp='+st3.display+' z='+st3.zIndex+' 文案='+
      JSON.stringify((el3.innerText||'').replace(/\s+/g,' ').slice(0,80)));}}
out.consent=out.consent.slice(0,8);
var t=(document.body?document.body.innerText:'').replace(/\s+/g,' ');
out.cookieText=(t.match(/.{0,40}(cookie|Cookie|consent|Accept all|Accept All).{0,40}/g)||[]).slice(0,4);
return JSON.stringify(out);
""" % {"btn": json.dumps(BTN)}


def ev(cdp, js):
    r = cdp.eval("(function(){%s})()" % js, "")
    if isinstance(r, (dict, list)):
        return r
    for line in str(r).strip().split("\n"):
        line = line.strip()
        if not line or line.startswith(("ERROR:", "Usage:", "Flags:")):
            continue
        try:
            return json.loads(line)
        except Exception:
            return line.strip('"')
    return ""


def shot(cdp, tag):
    p = subprocess.run([CDP, "screenshot", "--host", cdp.host, "--port", str(cdp.port)],
                       capture_output=True, text=True, timeout=40)
    data = (p.stdout or "").strip()
    if not data:
        print("  [截图 %s] 空（rc=%s %s）" % (tag, p.returncode, (p.stderr or "")[:80]), flush=True)
        return
    path = os.path.join(OUT, "click-%s.png" % tag)
    open(path, "wb").write(base64.b64decode(data))
    print("  [截图 %s] %s" % (tag, path), flush=True)


def measure(cdp, tag):
    m = ev(cdp, JS)
    if not isinstance(m, dict):
        print("  [%s] 量不出来: %r ｜ eval 报错: %s"
              % (tag, str(m)[:80], str(cdp.last_eval_error)[:160]), flush=True)
        return {}
    print("  [%s] y=%s/%s 视口%sx%s body.overflow=%s 根%d个"
          % (tag, m.get("y"), (m.get("docH") or 0) - 812, m.get("vw"), m.get("vh"),
             m.get("bodyOverflow"), m.get("nRoots")), flush=True)
    p = m.get("picked")
    if p:
        print("      ★选中 <%s> rect=%s ratio=%s disp=%s vis=%s offParent=%s nRects=%s 位置=%s"
              % (p["tag"], p["rect"], p["ratio"], p["disp"], p["vis"], p["offParent"],
                 p["nRects"], p["where"]), flush=True)
        print("         %s" % p["html"], flush=True)
        for line in p["chain"]:
            print("         祖先 %s" % line, flush=True)
    else:
        print("      ★没选中任何元素", flush=True)
    for x in m.get("all") or []:
        print("      同名[根%s] <%s> rect=%s ratio=%s disp=%s offParent=%s 位置=%s"
              % (x["root"], x["tag"], x["rect"], x["ratio"], x["disp"], x["offParent"], x["where"]), flush=True)
    for x in m.get("otherSubs") or []:
        print("      别的订阅框 <%s type=%s ph=%r name=%r> rect=%s ratio=%s disp=%s 位置=%s"
              % (x["tag"], x["type"], x["ph"], x["name"], x["rect"], x["ratio"], x["disp"], x["where"]), flush=True)
    for line in m.get("gestureStart") or []:
        print("      手势起点 %s" % line, flush=True)
    for line in m.get("fixedLayers") or []:
        print("      贴边固定层 %s" % line, flush=True)
    for line in m.get("consent") or []:
        print("      同意组件 %s" % line, flush=True)
    if m.get("cookieText"):
        print("      正文 cookie 字样 %s" % json.dumps(m["cookieText"], ensure_ascii=False)[:200], flush=True)
    return m


def timed(cdp, argv, cap=75):
    t0 = time.time()
    try:
        p = subprocess.run([CDP] + argv + ["--host", cdp.host, "--port", str(cdp.port)],
                           capture_output=True, text=True, timeout=cap)
        return time.time() - t0, p.returncode, (p.stdout + p.stderr).strip()[:140]
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
        print("\n===== ⓪ 落地（还没滚过）=====", flush=True)
        measure(cdp, "落地")

        print("\n===== ① 生产 step 1：滚到输入框（长滚，可能勾出弹层）=====", flush=True)
        d, rc, tail = timed(cdp, ["scroll", INPUT])
        print("  scroll 输入框：%.1f 秒 rc=%s %s" % (d, rc, tail[:80]), flush=True)
        rows.append(("scroll 输入框", d, rc))
        measure(cdp, "滚完")
        shot(cdp, "1-长滚后")

        print("\n===== ② ★判别 A：页面已在页脚，滚那个按钮 =====", flush=True)
        d, rc, tail = timed(cdp, ["scroll", BTN])
        print("  scroll 按钮：%.1f 秒 rc=%s %s" % (d, rc, tail[:80]), flush=True)
        rows.append(("★scroll 按钮(页脚)", d, rc))
        measure(cdp, "滚按钮后")
        shot(cdp, "2-滚按钮后")

        print("\n===== ③ 对照：click 可见的输入框（=聚焦）=====", flush=True)
        d, rc, tail = timed(cdp, ["click", INPUT])
        print("  click 输入框：%.1f 秒 rc=%s %s" % (d, rc, tail[:80]), flush=True)
        rows.append(("click 输入框(可见)", d, rc))
        measure(cdp, "点输入框后")
        shot(cdp, "3-点输入框后")

        print("\n===== ④ 再滚一次按钮 =====", flush=True)
        d, rc, tail = timed(cdp, ["scroll", BTN])
        print("  scroll 按钮：%.1f 秒 rc=%s %s" % (d, rc, tail[:80]), flush=True)
        rows.append(("★scroll 按钮(第二次)", d, rc))
        measure(cdp, "第二滚后")

        print("\n===== 汇总 =====", flush=True)
        for name, d, rc in rows:
            print("  %-24s %6.1f 秒   rc=%s" % (name, d, rc), flush=True)
    finally:
        try:
            mgr.close()
        except Exception as exc:
            print("关窗报错:", exc, flush=True)
        print("窗口已关", flush=True)


if __name__ == "__main__":
    main()
