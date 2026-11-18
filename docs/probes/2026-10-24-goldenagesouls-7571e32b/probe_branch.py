"""2026-10-24：确认 `goldenagesouls.com/land/sp/7571e32b` 这一页是**哪一族**的问卷。

两族的记号（从各自脚本头部的实测记录抄来，不是猜的）：

  · **japansdates 族**（`docs/probes/2026-10-23-soulmatemeets-101/japansdates_HOOKED.py`）：
    DOM 里是 `step-1 … step-21`（`step-content-item-<Type>`）、每一步一个 `continue-button`；
    选项是 `<label>`（里面包 `input[type=checkbox]`）；状态写在 **URL hash（base64）** 里
    （`c3RlcDE=` = step1 … `d2l6YXJkMg==` = wizard2）；路径恒为 `/land/sp/<id>/`。
  · **goldenagesouls 族**（`/opt/skills/auto-farm-skill/forms/sites/goldenagesouls.py`）：
    选项是 `button[data-testid=…]` —— `age-18-24` / `gender-female` / `image-button-*` /
    `pref-gender-*`；成功地址变成 `https://goldenagesouls.com/wizard?referrer=…`。

用法（容器内）：
  docker cp probe_branch.py siteforge-222:/tmp/probe_branch.py
  docker exec siteforge-222 python3 /tmp/probe_branch.py
"""
import json
import subprocess
import sys
import time

sys.path.insert(0, "/opt/siteforge")
from agent import selftest, service  # noqa: E402

URL = "https://goldenagesouls.com/land/sp/7571e32b"
CDP = "/opt/siteforge/tools/cdp/cdp"

MARK_JS = r"""(function(){
  function deepAll(sel){var out=[];function walk(r){r.querySelectorAll(sel).forEach(function(e){out.push(e)});
    var a=r.querySelectorAll('*');for(var i=0;i<a.length;i++){if(a[i].shadowRoot)walk(a[i].shadowRoot);}}
    walk(document);return out;}
  function vis(e){return e&&e.offsetWidth>0&&e.offsetHeight>0;}
  function names(sel){return deepAll(sel).filter(vis).map(function(e){return e.getAttribute('data-testid')||'';})
                              .filter(function(s){return s;});}
  var tids=names('[data-testid]');
  var cnt=function(pre){return tids.filter(function(s){return s.indexOf(pre)===0;}).length;};
  var hash=decodeURIComponent(location.hash||'');
  var b64='';
  try{ b64=atob(hash.replace(/^#/,'')); }catch(e){ b64='(not-base64)'; }
  var labels=deepAll('label').filter(vis).length;
  var cb=deepAll('label input[type=checkbox]').filter(vis).length;
  var btns=deepAll('button').filter(vis).map(function(e){return (e.innerText||'').replace(/\s+/g,' ').trim().slice(0,30);});
  return JSON.stringify({
    url: location.href, hash: hash, hash_decoded: b64,
    title: document.title,
    head: ((document.querySelector('h1,h2,h3')||document.body).innerText||'').replace(/\s+/g,' ').slice(0,180),
    step_tids: cnt('step-'), continue_buttons: cnt('continue-button'),
    image_buttons: cnt('image-button'), age_tid: cnt('age-18-24')+cnt('age-'),
    gender_tid: cnt('gender-'), pref_gender: cnt('pref-gender'),
    firstname: cnt('firstname'), email_tid: cnt('email'), password_tid: cnt('password'),
    visible_labels: labels, visible_label_checkboxes: cb, buttons: btns.slice(0,8),
    testids_sample: tids.slice(0,14)
  }, null, 1);
})()"""


def cdp(args, timeout=45):
    done = subprocess.run([CDP] + args, capture_output=True, text=True, timeout=timeout)
    return done.returncode, (done.stdout or "").strip()


def main():
    window = service.bit_window_from_env()
    ws_url = str(window.fresh_open() or "").strip()
    print("=== fresh_open:", ws_url)
    if not ws_url:
        return 3
    host, port = selftest._host_port(ws_url)
    H = ["--host", host, "--port", str(port)]
    here = ""
    deadline = time.time() + 90
    while time.time() < deadline:
        rc, here = cdp(["eval", "location.href"] + H)
        if rc == 0 and here:
            break
        time.sleep(3)
    print("=== 起跑前:", here)
    print("=== navi:", selftest._navigate(CDP, ws_url, URL, None))
    time.sleep(6)
    rc, dump = cdp(["eval", MARK_JS] + H, timeout=60)
    print("=== 这一页的记号：")
    print(dump)
    print("=== 窗口留着：", ws_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
