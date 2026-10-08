#!/usr/bin/env python3
"""georgiapower 定点探测：对着**生产那条真实广告 URL** 点一下落地页的「Schedule Now」，
量清楚这一下到底发生了什么。

只点、不填、不提交任何表单。要回答三件事：
  ① 这个选择器命中了几个元素、它们是 <a target=_blank> 还是别的东西；
  ② 点完之后**有没有新 tab**、什么时候出现、地址是什么（生产日志看不出这个）；
  ③ 当前页的地址/正文有没有变、多久之后变。

用法：python3 /tmp/gp_probe.py [轮数，默认 2]
"""
import base64
import json
import os
import re
import sys
import time
import urllib.request

SKILL = "/opt/skills/auto-farm-skill"
OUT = "/tmp/gp_probe"
os.makedirs(OUT, exist_ok=True)
sys.path.insert(0, os.path.join(SKILL, "src"))
sys.path.insert(0, os.path.join(SKILL, "forms"))
from browser import BrowserManager, DeviceConfig, ProxyConfig  # noqa: E402
from common import CDPHelper  # noqa: E402

SEL = "a.btn.btn-outline-tertiary--inversed"
SEL2 = ("body:nth-of-type(1) > div:nth-of-type(1) > div:nth-of-type(1) > div:nth-of-type(2) > "
        "div:nth-of-type(1) > div:nth-of-type(3) > div:nth-of-type(1) > div:nth-of-type(1) > "
        "div:nth-of-type(1) > section:nth-of-type(1) > div:nth-of-type(1) > div:nth-of-type(1) > "
        "div:nth-of-type(1) > div:nth-of-type(1) > div:nth-of-type(1) > a:nth-of-type(1)")


def ad_url():
    """生产现场里那条真实 URL（从 failDiag 日志里抠）。"""
    rows = json.load(open("/tmp/gp_faildiag.json"))
    for r in rows:
        m = re.search(r"手里这些地址都不含它：(\S+?)(?:；)", r.get("lines") or "")
        if m and "georgiapower.com/residential" in m.group(1):
            return m.group(1)
    raise SystemExit("没从现场里抠到 URL")


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


def targets(cdp):
    try:
        raw = urllib.request.urlopen("http://%s:%s/json/list" % (cdp.host, cdp.port), timeout=3).read()
        return [(t.get("id"), (t.get("url") or "")[:110], (t.get("title") or "")[:40])
                for t in json.loads(raw) if t.get("type") == "page"]
    except Exception as exc:
        return [("ERR", str(exc)[:80], "")]


def shot(cdp, name):
    s = cdp.screenshot()
    if s:
        p = os.path.join(OUT, name)
        open(p, "wb").write(base64.b64decode(s))
        return p
    return ""


CTA_JS = (
    "var out=[];"
    "var e=document.querySelectorAll(%s);"
    "for(var i=0;i<e.length;i++){var r=e[i].getBoundingClientRect();"
    "out.push({sel:'cls',tag:e[i].tagName,href:e[i].getAttribute('href')||'',"
    "target:e[i].getAttribute('target')||'',text:(e[i].innerText||'').replace(/\\s+/g,' ').slice(0,40),"
    "xywh:[Math.round(r.x),Math.round(r.y),Math.round(r.width),Math.round(r.height)],"
    "shown:!!(e[i].offsetWidth||e[i].offsetHeight)});}"
    "var all=document.querySelectorAll('a,button');"
    "for(var i=0;i<all.length;i++){var t=(all[i].innerText||'').replace(/\\s+/g,' ').trim();"
    "if(t.indexOf('Schedule Now')>=0){var r=all[i].getBoundingClientRect();"
    "out.push({sel:'text',tag:all[i].tagName,cls:String(all[i].className).slice(0,60),"
    "href:all[i].getAttribute('href')||'',target:all[i].getAttribute('target')||'',"
    "text:t.slice(0,40),xywh:[Math.round(r.x),Math.round(r.y),Math.round(r.width),Math.round(r.height)],"
    "shown:!!(all[i].offsetWidth||all[i].offsetHeight)});}}"
    "return JSON.stringify(out);" % json.dumps(SEL))


def main():
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    url = ad_url()
    print("真实广告 URL（%d 字）: %s ..." % (len(url), url[:120]), flush=True)
    mgr = BrowserManager(bit_sh=os.path.join(SKILL, "bit.sh"), worker_ip="192.168.1.222",
                         bit_id="41bde47e4cfd4eb19a7a0e5cf941a0ae", skill_dir=SKILL)
    dev = DeviceConfig(ua=("Mozilla/5.0 (iPhone; CPU iPhone OS 16_7_8 like Mac OS X) "
                           "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"),
                       sw=375, sh=812, dpr=3, core=4, mem=8)
    ws = mgr.open(dev, ProxyConfig(server="192.168.1.51", port=1081))
    print("窗口已开（代理 51:1081）", flush=True)
    open(os.path.join(OUT, "ws.txt"), "w").write(ws)
    cdp = CDPHelper(ws)
    try:
        for rd in range(1, rounds + 1):
            print("\n================ 第 %d 轮 ================" % rd, flush=True)
            cdp.navigate(url)
            time.sleep(9)                     # 生产是开始后 ~6 秒动的手，这里给到 9
            print("  落地页地址:", (ev(cdp, "return location.href;") or "")[:150], flush=True)
            txt = ev(cdp, "return document.body?document.body.innerText:'';") or ""
            print("  正文 %d 字，开头: %r" % (len(txt), txt[:90]), flush=True)
            print("  就绪状态:", ev(cdp, "return document.readyState;"), flush=True)
            ctas = ev(cdp, CTA_JS)
            try:
                ctas = json.loads(ctas) if isinstance(ctas, str) else ctas
            except Exception:
                ctas = []
            print("  命中「Schedule Now」/那个 class 的元素 %d 个:" % len(ctas or []), flush=True)
            for c in (ctas or []):
                print("    ", json.dumps(c, ensure_ascii=False)[:190], flush=True)
            before = targets(cdp)
            print("  点之前 tabs:", json.dumps(before, ensure_ascii=False)[:260], flush=True)
            shot(cdp, "r%d-before.png" % rd)

            t0 = time.time()
            receipt = cdp.click(SEL)
            print("  ★ cdp click 回执(%d 字): %s" % (len(str(receipt)), str(receipt).strip()[:400]), flush=True)

            seen = []
            changed_at = None
            for i in range(90):               # 最多看 45 秒
                time.sleep(0.5)
                now = targets(cdp)
                href = ev(cdp, "return location.href;") or ""
                if now != before or (href and href != url):
                    if changed_at is None:
                        changed_at = time.time() - t0
                        print("  ⏱ 第 %.1f 秒：变了 → tabs=%d 个；当前地址=%s"
                              % (changed_at, len(now), href[:110]), flush=True)
                        print("     tabs:", json.dumps(now, ensure_ascii=False)[:320], flush=True)
                        shot(cdp, "r%d-changed.png" % rd)
                    break
            if changed_at is None:
                print("  ⏱ 45 秒内**什么都没变**（tabs 还是 %d 个，地址还是落地页）" % len(targets(cdp)), flush=True)
            t1 = time.time()
            after = targets(cdp)
            print("  点之后 tabs:", json.dumps(after, ensure_ascii=False)[:320], flush=True)
            print("  当前地址:", (ev(cdp, "return location.href;") or "")[:150], flush=True)
            txt2 = ev(cdp, "return document.body?document.body.innerText:'';") or ""
            print("  正文 %d 字，开头: %r" % (len(txt2), txt2[:90]), flush=True)
            print("  这一轮耗时 %.1f 秒" % (t1 - t0), flush=True)
        print("\n截图都在 %s/" % OUT, flush=True)
    finally:
        try:
            mgr.close()
        except Exception as exc:
            print("关窗报错:", exc, flush=True)
        print("窗口已关", flush=True)


if __name__ == "__main__":
    main()
