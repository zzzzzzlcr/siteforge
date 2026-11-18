#!/usr/bin/env python3
"""量 innovatingcanada 的 Hustle 弹层到底什么时候出来、要不要先同意。

用法：python3 -I watch_popup.py [seconds] [agree|disagree|none]
默认 120 秒、不动同意条（只观察）。
打印每次变化的：秒数 / 标题 / 同意条按钮 / hustle 字段 / 正文关键句。
"""
import json
import subprocess
import sys
import time
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
CDP = "/company/siteforge/tools/cdp/cdp"
HOST, PORT = "192.168.1.222", "61129"
URL = (HERE / "failure_url.txt").read_text().strip()

JS = r"""JSON.stringify((()=>{
  const q=s=>!!document.querySelector(s);
  const btn=s=>{const e=document.querySelector(s);return e?((e.innerText||'').trim().slice(0,40)):null;};
  const txt=(document.body&&document.body.innerText)||'';
  return {
    t:(document.title||'').slice(0,50),
    agree:btn('#didomi-notice-agree-button'),
    disagree:btn('#didomi-notice-disagree-button'),
    didomi:q('#didomi-notice')||q('[id*=didomi]'),
    hustle:q('#hustle-field-first_name-module-6'),
    hustle_email:q('#hustle-field-email-module-6'),
    hustle_vis:(()=>{const e=document.querySelector('#hustle-field-first_name-module-6');if(!e)return false;const r=e.getBoundingClientRect();return r.width>0&&r.height>0;})(),
    hustle_modal:q('.hustle-modal')||q('[class*=hustle]'),
    partners:txt.includes('our 18 partners'),
    ads:txt.includes('Advertisements'),
    links:(()=>{let n=0;document.querySelectorAll('a').forEach(a=>{const r=a.getBoundingClientRect();if(r.width>0&&r.height>0)n++;});return n;})(),
    len:txt.length
  };
})())"""


def cdp(*args, timeout=60):
    return subprocess.run([CDP, "--host", HOST, "--port", PORT, *args],
                          capture_output=True, text=True, timeout=timeout)


def state():
    out = cdp("eval", JS).stdout.strip()
    try:
        return json.loads(json.loads(out))
    except Exception:
        return {"raw": out[:160]}


def main():
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 120.0
    action = sys.argv[2] if len(sys.argv) > 2 else "none"
    cdp("navi", "about:blank", timeout=60)
    time.sleep(1.0)
    cdp("navi", URL, timeout=90)
    t0 = time.monotonic()
    last = None
    acted = False
    while time.monotonic() - t0 < seconds:
        d = state()
        key = json.dumps(d, sort_keys=True, ensure_ascii=False)
        if key != last:
            print(round(time.monotonic() - t0, 1), key, flush=True)
            last = key
        want = (d.get("agree") if action == "agree" else
                d.get("disagree") if action == "disagree" else None)
        if action != "none" and not acted and want is not None:
            sel = "#didomi-notice-agree-button" if action == "agree" else "#didomi-notice-disagree-button"
            r = cdp("click", sel)
            print("CLICK", sel, r.returncode, (r.stdout or r.stderr)[:120].replace("\n", " "), flush=True)
            acted = True
        if d.get("hustle_vis"):
            print("HUSTLE_VISIBLE_AT", round(time.monotonic() - t0, 1), flush=True)
            break
        time.sleep(2)


if __name__ == "__main__":
    main()
