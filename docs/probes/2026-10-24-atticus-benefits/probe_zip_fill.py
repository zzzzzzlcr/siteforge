"""2026-10-24：zip 屏 Next 一直是禁用态 —— 拆「填值那一下到底哪一步没进到页面状态」。

现象（线上 `26789962`、8100 的 034221/034446 两趟）：
  `#zipcode` 的 DOM value 是 10001，可 `Next` 是 `disabled`；连点 8 次页面不动。
手工量过一次：JS 写值 + 补齐 keydown/keypress/input/keyup/change/blur ⇒ Next 变可用。

这一份把那一串**拆开**，一次只加一样，看是哪一步让它翻的：
  A. `cdp form --value`（换一个值）
  B. 原生 setter 写值（不发事件）
  C. 只发 input
  D. 只发 keydown+keyup
  E. 全套事件
每一步都记 `{value, nextDisabled, url}`。

用法（容器内）：
  docker cp probe_zip_fill.py siteforge-222:/tmp/probe_zip_fill.py
  docker exec siteforge-222 python3 /tmp/probe_zip_fill.py
"""
import json
import pathlib
import subprocess
import sys
import time

sys.path.insert(0, "/opt/siteforge")
from agent import selftest, service  # noqa: E402

ENTRY = "https://try.atticus.com/ssd/benefits"
CDP = "/opt/siteforge/tools/cdp/cdp"
STAMP = time.strftime("%Y%m%d-%H%M%S")
RUN_DIR = pathlib.Path("/opt/siteforge/runtime/selftest/probe-zip-%s" % STAMP)

STATE_JS = r"""(function(){
  var e=document.getElementById("zipcode");
  var b=[].slice.call(document.querySelectorAll("button")).filter(function(x){return x.offsetWidth>0;})[0];
  return JSON.stringify({url:location.href,
                         has:!!e,
                         value:e?e.value:null,
                         nextTxt:b?((b.innerText||"").replace(/\s+/g," ").trim().slice(0,20)):null,
                         nextDisabled:b?!!b.disabled:null,
                         active:document.activeElement?(document.activeElement.id||document.activeElement.tagName):null});
})()"""

MARK_CTA_JS = r"""(function(){
  var bs=[].slice.call(document.querySelectorAll('a,button,[role=button]'));
  for(var i=0;i<bs.length;i++){var t=(bs[i].innerText||'').replace(/\s+/g,' ').trim();
    if(/see if you qualify/i.test(t)){bs[i].setAttribute('data-sf-probe','1');return t.slice(0,60);}}
  return '';
})()"""

SET_JS = r"""(function(v){var e=document.getElementById("zipcode");e.focus();
  var d=Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,"value");
  d.set.call(e,v);return e.value;})"""

EVENT_JS = {
    "input": r"""(function(){var e=document.getElementById("zipcode");e.dispatchEvent(new Event("input",{bubbles:true}));return e.value;})""",
    "key": r"""(function(){var e=document.getElementById("zipcode");
      e.dispatchEvent(new KeyboardEvent("keydown",{bubbles:true,key:"1",keyCode:49}));
      e.dispatchEvent(new KeyboardEvent("keypress",{bubbles:true,key:"1",keyCode:49}));
      e.dispatchEvent(new KeyboardEvent("keyup",{bubbles:true,key:"1",keyCode:49}));
      return e.value;})""",
    "full": r"""(function(){var e=document.getElementById("zipcode");
      ["keydown","keypress","input","keyup","change","blur"].forEach(function(t){
        e.dispatchEvent(t==="input"||t==="change"?new Event(t,{bubbles:true}):new KeyboardEvent(t,{bubbles:true,key:"1",keyCode:49}));});
      return e.value;})""",
}


def cdp(args, timeout=45):
    done = subprocess.run([CDP] + args, capture_output=True, text=True, timeout=timeout)
    return done.returncode, (done.stdout or "").strip().strip('"')


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
    print("=== navi:", selftest._navigate(CDP, ws_url, ENTRY, None))
    time.sleep(3)

    rc, said = cdp(["eval", MARK_CTA_JS] + H)
    print("=== 标记 CTA:", said)
    if said:
        print("=== 点 CTA:", cdp(["click", '[data-sf-probe="1"]'] + H))

    # 等到 zip 输入出现
    deadline = time.time() + 60
    while time.time() < deadline:
        rc, state = cdp(["eval", STATE_JS] + H)
        try:
            s = json.loads(state)
        except Exception:
            s = {}
        if s.get("has"):
            print("=== zip 屏出现:", state)
            break
        time.sleep(2)
    else:
        print("!! 等不到 zip 格")
        return 4

    def snap(tag):
        rc, state = cdp(["eval", STATE_JS] + H)
        print("   [%-18s] %s" % (tag, state))
        return json.loads(state)

    time.sleep(3)
    snap("baseline")

    print("== A: cdp form --value 20002")
    print("   said:", cdp(["form", "#zipcode", "--value", "20002"] + H))
    time.sleep(2)
    snap("A cdp-form")

    print("== B: 原生 setter 写 30003（不发事件）")
    print("   said:", cdp(["eval", SET_JS + '("30003")'] + H))
    time.sleep(2)
    snap("B setter")

    print("== C: 只发 input")
    print("   said:", cdp(["eval", EVENT_JS["input"]] + H))
    time.sleep(2)
    snap("C input")

    print("== D: 只发 keydown/keypress/keyup")
    print("   said:", cdp(["eval", EVENT_JS["key"]] + H))
    time.sleep(2)
    snap("D key")

    print("== E: 全套事件")
    print("   said:", cdp(["eval", EVENT_JS["full"]] + H))
    time.sleep(2)
    snap("E full")

    RUN_DIR.mkdir(parents=True, exist_ok=True)
    print("=== 窗口留着:", ws_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
