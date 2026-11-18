"""2026-11-07 #3：勾上之后为什么还不走？把条款屏所有可见按钮打出来。

probe_terms_js.py 已证明：off-screen input 用 JS `.click()` 能勾上
（`legalChecked=true`），但页面仍无 `continue-button`、无选项、45s 不推进。
这一趟在勾选前后各 dump 一次**可见的按钮/链接**和条款容器的完整 HTML，
看它是「勾上后出现一个别名的继续按钮」还是「勾选本身就该触发翻页」。
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
FORM = "/tmp/youmetalks_form3.json"


class ProbeDone(Exception):
    pass


DUMP_JS = r"""(function(){
  function vis(e){if(!e)return false;var r=e.getBoundingClientRect();
    if(r.width<=0||r.height<=0)return false;
    var n=e;while(n&&n.nodeType===1){var cs=getComputedStyle(n);
      if(cs.visibility==='hidden'||cs.display==='none')return false;n=n.parentElement;}
    return true;}
  function d(e){var r=e.getBoundingClientRect();return {tag:e.tagName.toLowerCase(),
    testid:e.getAttribute('data-testid'),cls:((e.className||'')+'').slice(0,120),
    type:e.getAttribute('type'),role:e.getAttribute('role'),
    disabled:!!e.disabled,aria_disabled:e.getAttribute('aria-disabled'),
    x:Math.round(r.left),y:Math.round(r.top),w:Math.round(r.width),h:Math.round(r.height),
    text:(e.innerText||e.textContent||'').replace(/\s+/g,' ').trim().slice(0,60)};}
  var cb=document.querySelector('[data-testid="legal-checkbox"]');
  var root=document.querySelector('[data-testid="step-content-item-CheckboxTerms"]');
  var btns=[];
  document.querySelectorAll('button,[role=button],a,input[type=submit]').forEach(function(e){
    if(vis(e))btns.push(d(e));});
  var step=root?root.closest('.step')||root.parentElement:null;
  return JSON.stringify({checked:!!(cb&&cb.checked),
    url:location.href,
    buttons:btns,
    terms_root_html:root?root.outerHTML.replace(/\s+/g,' ').slice(0,5000):'',
    step_html:step?step.outerHTML.replace(/\s+/g,' ').slice(0,9000):''},null,1);
})()"""


def load():
    spec = importlib.util.spec_from_file_location("jd_probe3", ART)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    profile = {"first_name": "Alex",
               "email": "alexprobe%d@outlook.com" % random.randint(1000, 9999)}
    with open(FORM, "w", encoding="utf-8") as fh:
        json.dump(profile, fh)
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
        f = mod.JapanSDatesFiller(ws, FORM, "ymterms3_%d" % int(time.time()), "")
        f._rpt = lambda step: None
        orig_read = f.read
        done = {"v": False}

        def read():
            page = orig_read()
            if page and "CheckboxTerms" in (page.get("step") or "") and not done["v"]:
                done["v"] = True
                before = f.cdp.eval(DUMP_JS, "")
                print("== BEFORE =", before, flush=True)
                open("/tmp/youmetalks_terms_before.json", "w", encoding="utf-8").write(str(before))
                print("== JS click =", f.cdp.eval(
                    "(function(){var e=document.querySelector('[data-testid=\"legal-checkbox\"]');"
                    "if(e)e.click();return e?'clicked':'no';})()", ""), flush=True)
                time.sleep(2.5)
                after = f.cdp.eval(DUMP_JS, "")
                print("== AFTER =", after, flush=True)
                open("/tmp/youmetalks_terms_after.json", "w", encoding="utf-8").write(str(after))
                raise ProbeDone()
            return page

        f.read = read
        f.cdp.navigate(URL)
        time.sleep(3)
        try:
            f.run(max_steps=45)
        except ProbeDone:
            print("== 已 dump 完，停（未提交）", flush=True)
        return 0
    finally:
        try:
            window.close()
        except Exception as exc:  # noqa: BLE001
            print("关窗报错：%s" % exc, flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
