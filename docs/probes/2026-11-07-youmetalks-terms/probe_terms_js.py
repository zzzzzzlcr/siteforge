"""2026-11-07 #2：勾选框在，但 cdp 点击被“别的东西”挡住 —— 试 JS .click()。

probe_terms.py 已确认 DOM：`[data-testid="legal-checkbox"]` 可见（13×13），
`cdp form --check` 拒绝点击：「它中心的整摞里没有它」→ 中心点被覆盖。
生产脚本的兜底也走 `cdp click`（label / input），同样会被这层挡住，
所以 `_check_terms()` 一直 False → 空转到 45s。

这一趟在条款屏只做两件事：
  1. 把覆盖它中心的元素打出来（elementFromPoint）；
  2. 用 JS `.click()` 勾上（2026-09-20 在同族真站实测有效），然后让 run() 自己走完。
`_rpt` 仍是空函数，不写生产记录。
"""
import importlib.util
import json
import os
import random
import sys
import time

sys.path.insert(0, "/opt/siteforge")
from agent import service  # noqa: E402

URL = "https://youmetalks.com/land/sp/b4dd2154"
ART = "/opt/siteforge/forms/sites/_probe_youmetalks_terms.py"
FORM = "/tmp/youmetalks_form2.json"

DUMP_JS = r"""(function(){
  function vis(e){if(!e)return false;var r=e.getBoundingClientRect();
    if(r.width<=0||r.height<=0)return false;
    var n=e;while(n&&n.nodeType===1){var cs=getComputedStyle(n);
      if(cs.visibility==='hidden'||cs.display==='none')return false;n=n.parentElement;}
    return true;}
  var cb=document.querySelector('[data-testid="legal-checkbox"]');
  if(!cb)return JSON.stringify({found:false});
  var r=cb.getBoundingClientRect(),cx=r.left+r.width/2,cy=r.top+r.height/2;
  var hit=document.elementFromPoint(cx,cy);
  function desc(e){if(!e)return null;return {tag:e.tagName.toLowerCase(),
    testid:e.getAttribute('data-testid'),cls:(e.className||'').toString().slice(0,120),
    text:(e.innerText||e.textContent||'').replace(/\s+/g,' ').trim().slice(0,60)};}
  var chain=[];var e2=cb;while(e2&&e2.nodeType===1&&chain.length<5){chain.push(desc(e2));e2=e2.parentElement;}
  return JSON.stringify({found:true,checked:!!cb.checked,
    rect:{x:Math.round(r.left),y:Math.round(r.top),w:Math.round(r.width),h:Math.round(r.height)},
    point:{x:Math.round(cx),y:Math.round(cy)},hit:desc(hit),
    chain:chain,
    label_html:cb.closest('label')?cb.closest('label').outerHTML.replace(/\s+/g,' ').slice(0,1200):null,
    parent_html:cb.parentElement?cb.parentElement.outerHTML.replace(/\s+/g,' ').slice(0,1600):null,
    all_invisible_overlays:(function(){var out=[];
      document.querySelectorAll('body *').forEach(function(x){
        var rr=x.getBoundingClientRect();var cs=getComputedStyle(x);
        if(rr.width>300&&rr.height>300&&(cs.position==='fixed'||cs.position==='sticky'))
          out.push(desc(x));});
      return out.slice(0,8);})()},null,1);
})()"""


def load():
    spec = importlib.util.spec_from_file_location("jd_probe2", ART)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    profile = {"first_name": "Alex",
               "email": "alexprobe%d@outlook.com" % random.randint(1000, 9999)}
    with open(FORM, "w", encoding="utf-8") as fh:
        json.dump(profile, fh)
    print("资料：", json.dumps(profile, ensure_ascii=False), flush=True)

    window = service.bit_window_from_env()
    if window is None:
        print("这个容器没配 Bit 窗口")
        return 3
    ws = str(window.fresh_open() or "").strip()
    print("ws=%s" % ws, flush=True)
    if not ws:
        return 3
    try:
        mod = load()
        f = mod.JapanSDatesFiller(ws, FORM, "ymterms2_%d" % int(time.time()), "")
        f._rpt = lambda step: None
        orig_read = f.read
        done = {"v": False}

        def read():
            page = orig_read()
            if page and "CheckboxTerms" in (page.get("step") or "") and not done["v"]:
                done["v"] = True
                dump = f.cdp.eval(DUMP_JS, "")
                print("== TERMS DOM =", dump, flush=True)
                open("/tmp/youmetalks_terms_dom.json", "w", encoding="utf-8").write(str(dump))
                js = (r"""(function(){var e=document.querySelector('[data-testid="legal-checkbox"]');
                      if(!e)return 'no';e.click();return 'clicked';})()""")
                print("== JS click =", f.cdp.eval(js, ""), flush=True)
                time.sleep(1.5)
                chk = (r"""(function(){var e=document.querySelector('[data-testid="legal-checkbox"]');
                       return JSON.stringify({checked:!!(e&&e.checked),url:location.href});})()""")
                print("== 勾选后 =", f.cdp.eval(chk, ""), flush=True)
            return page

        f.read = read
        f.cdp.navigate(URL)
        time.sleep(3)
        ok = f.run(max_steps=60)
        print("== run() 返回 %r" % ok, flush=True)
        print("== 最终 =", str(f.cdp.eval("JSON.stringify([location.href,document.title])", ""))[:500],
              flush=True)
        return 0 if ok else 1
    finally:
        try:
            window.close()
        except Exception as exc:  # noqa: BLE001
            print("关窗报错：%s" % exc, flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
