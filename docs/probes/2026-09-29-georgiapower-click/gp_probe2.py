#!/usr/bin/env python3
"""georgiapower 定点探测 v2：把「点一下之后那 60 秒」逐秒量出来。

v1 已经量到：那个 CTA 是 <a target="_blank">，点完**新 tab 是过了约 6 秒才冒出来的**，
而且冒出来时**地址还是空的**。v2 要钉死的是：
  ① 新 tab 出现的时刻、以及它的地址**什么时候**变成排程页；
  ② 这一段时间里，产物那两处「跟新 tab」的判据各自会看到什么
     （`_follow_new_tab` 是点完立刻看一次；`_follow_tab_matching` 是每个状态判据不成立时看一次）；
  ③ cdp 会话的「当前页」是落地页还是新 tab（生产日志里那句「手里这些地址」读的就是它）。

只点、不填、不提交。
"""
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
WANT = "https://utility-energyservices.my.site.com/s/GPClipboardAuditScheduler"


def ad_url():
    rows = json.load(open("/tmp/gp_faildiag.json"))
    for r in rows:
        m = re.search(r"手里这些地址都不含它：(\S+?)(?:；)", r.get("lines") or "")
        if m and "georgiapower.com/residential" in m.group(1):
            return m.group(1)
    raise SystemExit("没抠到 URL")


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


def tabs(cdp):
    try:
        raw = urllib.request.urlopen("http://%s:%s/json/list" % (cdp.host, cdp.port), timeout=3).read()
        return [((t.get("id") or "")[:8] + "…", (t.get("url") or "(空)")[:95])
                for t in json.loads(raw) if t.get("type") == "page"]
    except Exception as exc:
        return [("ERR", str(exc)[:70])]


def main():
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    url = ad_url()
    mgr = BrowserManager(bit_sh=os.path.join(SKILL, "bit.sh"), worker_ip="192.168.1.222",
                         bit_id="41bde47e4cfd4eb19a7a0e5cf941a0ae", skill_dir=SKILL)
    dev = DeviceConfig(ua=("Mozilla/5.0 (iPhone; CPU iPhone OS 16_7_8 like Mac OS X) "
                           "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"),
                       sw=375, sh=812, dpr=3, core=4, mem=8)
    ws = mgr.open(dev, ProxyConfig(server="192.168.1.51", port=1081))
    print("窗口已开", flush=True)
    cdp = CDPHelper(ws)
    try:
        for rd in range(1, rounds + 1):
            print("\n================ 第 %d 轮 ================" % rd, flush=True)
            cdp.navigate(url)
            time.sleep(10)
            print("  落地页 readyState=%s 正文%d字" % (
                ev(cdp, "return document.readyState;"),
                len(ev(cdp, "return document.body?document.body.innerText:'';") or "")), flush=True)
            before = [t[0] for t in tabs(cdp)]
            print("  点之前 tabs:", json.dumps(tabs(cdp), ensure_ascii=False)[:220], flush=True)

            t0 = time.time()
            receipt = cdp.click(SEL)
            # 产物点完**立刻**看一次（_follow_new_tab 就是这个时刻）
            t_imm = time.time() - t0
            tabs_imm = tabs(cdp)
            new_imm = [t for t in tabs_imm if t[0] not in before]
            print("  ★ 点完 %.2f 秒时：tabs=%d 个，其中新的=%s  → 产物这一步会「跟」到：%s"
                  % (t_imm, len(tabs_imm), json.dumps(new_imm, ensure_ascii=False),
                     (new_imm[-1][1] if new_imm else "（没有新 tab，返回空串）")), flush=True)

            hit_at, hit_what, last = None, "", ""
            for i in range(120):                        # 60 秒
                time.sleep(0.5)
                tl = tabs(cdp)
                now = str(tl)
                if now != last:
                    last = now
                    print("  +%5.1fs tabs=%d %s" % (time.time() - t0, len(tl),
                          json.dumps(tl, ensure_ascii=False)[:300]), flush=True)
                for _, u in tl:
                    if WANT in u and hit_at is None:
                        hit_at = time.time() - t0
                        hit_what = u
                        print("  ✅ +%.1f 秒：/json/list 里第一次出现排程页地址" % hit_at, flush=True)
                cur = ev(cdp, "return location.href;") or ""
                if i % 6 == 0:
                    print("       +%5.1fs 会话当前页=%s" % (time.time() - t0, cur[:95]), flush=True)
            print("  小结：新 tab 里出现排程页地址的时刻 = %s"
                  % ("+%.1f 秒" % hit_at if hit_at else "**60 秒内从没出现**"), flush=True)
            print("  最终 tabs:", json.dumps(tabs(cdp), ensure_ascii=False)[:300], flush=True)
            print("  最终会话当前页:", (ev(cdp, "return location.href;") or "")[:110], flush=True)
    finally:
        try:
            mgr.close()
        except Exception as exc:
            print("关窗报错:", exc, flush=True)
        print("窗口已关", flush=True)


if __name__ == "__main__":
    main()
