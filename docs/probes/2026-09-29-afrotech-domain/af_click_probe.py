#!/usr/bin/env python3
"""afrotech 第 3 步（cdp click 按钮）那 35 秒的 bisect —— 只滚不点按钮。

要回答（按重要性）：
  A. 页面**已经在页脚**时，`cdp scroll <那个按钮>` 快不快？  ← 快 ⇒ 按钮本身没病，35 秒是「点的时候页面不在页脚」
                                                               慢 ⇒ 按钮**永远判不成可见**（比例判据对它是死局）
  B. 复现生产 step 2 的 `cdp form`（**只填不提交**）之后，页面被挪走了吗？（scrollY / performance.now 看有没有重载）
  C. `cdp click` 那条路对**可见元素**的固定开销是多少（拿输入框当靶子，点击输入框 = 聚焦，不提交）

不点 `button[aria-label="Subscribe to Newsletter"]`（那是真提交）。窗关在 finally 里。
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
CDP = os.path.join(SKILL, "cdp")

#: 量「点这一下到底谁慢」要的就是完整的 getBoundingClientRect —— 与
#: /company/cdpcli/internal/client.go:864 IsElementVisible 用**同一个算式**（|视口∩矩形|/|矩形|），
#: 这样"我算出来 ratio=1，cdp 却跑了 20 轮"这句话才是可比的。
MEASURE_JS = r"""
var out={href:location.href,rs:document.readyState,pn:Math.round(performance.now()),
         y:Math.round(window.scrollY),docH:document.documentElement.scrollHeight,
         vw:window.innerWidth,vh:window.innerHeight};
var els=document.querySelectorAll('[aria-label="Subscribe to Newsletter"]');
out.n=els.length;out.matches=[];
for(var i=0;i<els.length;i++){
  var el=els[i],r=el.getBoundingClientRect(),s=getComputedStyle(el);
  var va=(Math.min(r.right,window.innerWidth)-Math.max(r.left,0))*(Math.min(r.bottom,window.innerHeight)-Math.max(r.top,0));
  var ta=r.width*r.height;
  var cx=Math.round(r.left+r.width/2),cy=Math.round(r.top+r.height/2);
  var hit=(cx>=0&&cy>=0&&cx<window.innerWidth&&cy<window.innerHeight)?document.elementFromPoint(cx,cy):null;
  out.matches.push({i:i,tag:el.tagName,type:el.type||'',ph:el.placeholder||'',
    rect:[Math.round(r.x),Math.round(r.y),Math.round(r.width),Math.round(r.height)],
    ratio:(ta>0?+(va/ta).toFixed(3):'NaN(面积0)'),
    disp:s.display,vis:s.visibility,op:s.opacity,
    offParent:!!el.offsetParent,nRects:el.getClientRects().length,
    inFooter:!!el.closest('footer'),
    hiddenAnc:(el.closest('[hidden]')||el.closest('[aria-hidden="true"]'))?1:0,
    atCenter:(hit?(hit.tagName+(hit===el?'(=自己)':'')):'(不在视口)')});
}
return JSON.stringify(out);
"""


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


def measure(cdp, tag):
    m = ev(cdp, MEASURE_JS)
    if not isinstance(m, dict):
        print("  [%s] 量不出来：%r" % (tag, str(m)[:120]), flush=True)
        return {}
    print("  [%s] y=%s docH=%s perfNow=%sms rs=%s ｜ 同名元素 %d 个"
          % (tag, m.get("y"), m.get("docH"), m.get("pn"), m.get("rs"), m.get("n")), flush=True)
    for x in m.get("matches") or []:
        print("      #%d <%s type=%s ph=%r> rect=%s ratio=%s disp=%s vis=%s op=%s "
              "offsetParent=%s nRects=%s footer=%s hiddenAnc=%s 中心点上是:%s"
              % (x["i"], x["tag"], x["type"], x["ph"], x["rect"], x["ratio"], x["disp"],
                 x["vis"], x["op"], x["offParent"], x["nRects"], x["inFooter"],
                 x["hiddenAnc"], x["atCenter"]), flush=True)
    return m


def timed(cdp, argv, cap=70):
    t0 = time.time()
    try:
        p = subprocess.run([CDP] + argv + ["--host", cdp.host, "--port", str(cdp.port)],
                           capture_output=True, text=True, timeout=cap)
        return time.time() - t0, p.returncode, (p.stdout + p.stderr).strip()[:200]
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
        print("\n===== ⓪ 落地（页面在顶部，生产 step 1 之前）=====", flush=True)
        measure(cdp, "落地")

        print("\n===== ① 生产 step 1 原样复现：滚到那个输入框 =====", flush=True)
        d, rc, tail = timed(cdp, ["scroll", INPUT])
        print("  scroll 输入框：%.1f 秒，退出码 %s %s" % (d, rc, tail[:100]), flush=True)
        rows.append(("scroll 输入框（=生产step1）", d, rc))
        m1 = measure(cdp, "滚完")

        print("\n===== ② 对照：cdp click 打**可见的输入框**（=聚焦，不提交）=====", flush=True)
        d, rc, tail = timed(cdp, ["click", INPUT])
        print("  click 输入框：%.1f 秒，退出码 %s %s" % (d, rc, tail[:140]), flush=True)
        rows.append(("click 输入框（可见元素的固定开销）", d, rc))

        print("\n===== ③ ★判别 A：页面已在页脚，滚那个**按钮** =====", flush=True)
        d, rc, tail = timed(cdp, ["scroll", BTN])
        print("  scroll 按钮：%.1f 秒，退出码 %s %s" % (d, rc, tail[:100]), flush=True)
        rows.append(("★scroll 按钮（页面在页脚）", d, rc))
        measure(cdp, "滚按钮后")

        print("\n===== ④ 生产 step 2 原样复现：只填不提交 =====", flush=True)
        d, rc, tail = timed(cdp, ["form", INPUT, "--value", "probe.only@example.com"], cap=70)
        print("  form 填输入框：%.1f 秒，退出码 %s %s" % (d, rc, tail[:140]), flush=True)
        rows.append(("form 填输入框（=生产step2）", d, rc))
        m3 = measure(cdp, "填完")

        print("\n===== ⑤ ★判别 B：填完之后再滚一次按钮（页面被挪走了吗）=====", flush=True)
        d, rc, tail = timed(cdp, ["scroll", BTN])
        print("  scroll 按钮：%.1f 秒，退出码 %s %s" % (d, rc, tail[:100]), flush=True)
        rows.append(("★scroll 按钮（填完之后）", d, rc))
        measure(cdp, "第二滚按钮后")

        print("\n===== 汇总 =====", flush=True)
        for name, d, rc in rows:
            print("  %-34s %6.1f 秒   退出码 %s" % (name, d, rc), flush=True)
    finally:
        try:
            mgr.close()
        except Exception as exc:
            print("关窗报错:", exc, flush=True)
        print("窗口已关", flush=True)


if __name__ == "__main__":
    main()
