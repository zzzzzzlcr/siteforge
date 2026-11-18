"""2026-11-07：youmetalks.com/land/sp/b4dd2154 条款屏到底长什么样。

背景（FMR 单 27042691 / 27053769）：生产脚本 `japansdates` 在
`step-content-item-CheckboxTerms` 上空转到 45s，现场读数
`nextVisible=false legalChecked=false options=[] labels=[四条法律链接] inputs=[]`。
脚本只认 `[data-testid="legal-checkbox"]`，所以怀疑这一屏换了勾选框记号。

本探针只读：拿线上同一份脚本真跑一趟，走到 CheckboxTerms 时把 DOM 打出来，
再试一次 `cdp form --check`（不点提交）。`_rpt` 换成空函数，不写生产记录。

用法（容器内）：
  docker cp probe_terms.py siteforge-222:/tmp/probe_terms.py
  docker exec siteforge-222 python3 /tmp/probe_terms.py
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
FORM = "/tmp/youmetalks_form.json"


class ProbeDone(Exception):
    pass


DUMP_JS = r"""(function(){
  function vis(e){if(!e)return false;var r=e.getBoundingClientRect();
    if(r.width<=0||r.height<=0)return false;
    var n=e;while(n&&n.nodeType===1){var cs=getComputedStyle(n);
      if(cs.visibility==='hidden'||cs.display==='none')return false;n=n.parentElement;}
    return true;}
  function deepAll(sel){var out=[];function walk(r){
    r.querySelectorAll(sel).forEach(function(e){out.push(e);});
    var a=r.querySelectorAll('*');
    for(var i=0;i<a.length;i++){if(a[i].shadowRoot)walk(a[i].shadowRoot);}}
    walk(document);return out;}
  function brief(e){var r=e.getBoundingClientRect();
    return {tag:e.tagName.toLowerCase(),testid:e.getAttribute('data-testid'),
      type:e.getAttribute('type'),name:e.getAttribute('name'),
      role:e.getAttribute('role'),checked:!!e.checked,
      aria:e.getAttribute('aria-checked'),w:Math.round(r.width),h:Math.round(r.height),
      vis:vis(e),text:(e.innerText||e.textContent||'').replace(/\s+/g,' ').trim().slice(0,80)};}
  var roots=deepAll('[data-testid*="CheckboxTerms"]').filter(vis);
  var boxes=deepAll('input[type=checkbox],[role=checkbox]').filter(vis).map(brief);
  var terms=deepAll('[data-testid="legal-checkbox"]').map(brief);
  var cbish=deepAll('[data-testid]').filter(function(e){
    var t=e.getAttribute('data-testid')||'';
    return /legal|terms|checkbox/i.test(t)&&vis(e);}).map(brief);
  var buttons=deepAll('button,[role=button],a').filter(vis).map(brief)
    .filter(function(b){return b.text;}).slice(0,12);
  var container=roots.length?roots[0].outerHTML.replace(/\s+/g,' ').slice(0,3500):'';
  return JSON.stringify({url:location.href,
    visible_terms_roots:roots.length,
    legal_checkbox:terms,
    checkbox_like:cbish,
    checkboxes:boxes,
    buttons:buttons,
    container_html:container},null,1);
})()"""


def load():
    spec = importlib.util.spec_from_file_location("jd_probe", ART)
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
        f = mod.JapanSDatesFiller(ws, FORM, "ymterms_%d" % int(time.time()), "")
        f._rpt = lambda step: None

        orig_read = f.read

        def read():
            page = orig_read()
            if page and "CheckboxTerms" in (page.get("step") or ""):
                print("== 现场 page =", json.dumps(page, ensure_ascii=False)[:1800], flush=True)
                print("== DOM =", f.cdp.eval(DUMP_JS, ""), flush=True)
                # 试一下：可见 terms 容器里的 input[type=checkbox] / role=checkbox
                probe = r"""(function(){
                  function vis(e){if(!e)return false;var r=e.getBoundingClientRect();
                    if(r.width<=0||r.height<=0)return false;
                    var n=e;while(n&&n.nodeType===1){var cs=getComputedStyle(n);
                      if(cs.visibility==='hidden'||cs.display==='none')return false;n=n.parentElement;}
                    return true;}
                  var roots=[];
                  document.querySelectorAll('[data-testid*="CheckboxTerms"]').forEach(function(e){
                    if(vis(e))roots.push(e);});
                  for(var q=0;q<roots.length;q++){
                    var cand=roots[q].querySelector('input[type=checkbox],[role=checkbox]');
                    if(cand){cand.setAttribute('data-probe-terms','1');
                      return 'found:'+cand.tagName+':'+(cand.getAttribute('data-testid')||'');}
                  }
                  return 'none';
                })()"""
                print("== 探针标记 =", f.cdp.eval(probe, ""), flush=True)
                try:
                    print("== cdp form --check =", f.cdp.form('[data-probe-terms="1"]', check="true"),
                          flush=True)
                except Exception as exc:  # noqa: BLE001
                    print("== cdp form --check 失败：%s" % exc, flush=True)
                time.sleep(2)
                verify = r"""(function(){var e=document.querySelector('[data-probe-terms="1"]');
                  return JSON.stringify({found:!!e,checked:!!(e&&e.checked),
                    aria:e&&e.getAttribute('aria-checked'),url:location.href});})()"""
                print("== 勾选后 =", f.cdp.eval(verify, ""), flush=True)
                raise ProbeDone()
            return page

        f.read = read
        f.cdp.navigate(URL)
        time.sleep(3)
        print("落地 =", str(f.cdp.eval("window.location.href", ""))[:200], flush=True)
        try:
            f.run(max_steps=45)
        except ProbeDone:
            print("== 到达条款屏，已停（未提交）", flush=True)
        return 0
    finally:
        try:
            window.close()
        except Exception as exc:  # noqa: BLE001
            print("关窗报错：%s" % exc, flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
