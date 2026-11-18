"""2026-10-24：在 8100（siteforge-222）上**确定性**复现联系信息屏的 relationship 下拉。

线上那两趟（26807011 / 26807496）都是随机答到「Patient or client」「Family member or friend」
之后卡在关系下拉上。这里把 `pick_option` 拧成「有 Patient or client 就选它、有
I'm a care professional 就选它」，好让一趟就能走到那一屏；走到之后把 DOM 原样读出来
（id / name / placeholder / aria-label / data-testid / role / tag），**不改任何文件**。

用法（容器内）：
  docker cp repro_relationship.py siteforge-222:/tmp/repro_relationship.py
  docker exec siteforge-222 python3 /tmp/repro_relationship.py
"""
import importlib.util
import json
import pathlib
import subprocess
import sys
import time

sys.path.insert(0, "/opt/siteforge")
from agent import selftest, service  # noqa: E402

ARTIFACT = "/opt/siteforge/forms/sites/try.loop.py"
ENTRY = "https://try.atticus.com/ssd/benefits"
FORM_FILE = "/tmp/atticus_form.json"
CDP = "/opt/siteforge/tools/cdp/cdp"
STAMP = time.strftime("%Y%m%d-%H%M%S")
RUN_DIR = pathlib.Path("/opt/siteforge/runtime/selftest/repro-attitus-rel-%s" % STAMP)

#: 把这几句「优先挑」—— 线上那两趟正是挑了它们才走到关系下拉的
PREFER = ["Patient or client", "I'm a care professional", "Family member or friend"]

DUMP_JS = r"""
(function(){
  function deepAll(sel){var out=[];function walk(root){root.querySelectorAll(sel).forEach(function(e){out.push(e);});
    var all=root.querySelectorAll('*');for(var i=0;i<all.length;i++){if(all[i].shadowRoot)walk(all[i].shadowRoot);}}
    walk(document);return out;}
  function vis(e){return e && e.offsetWidth>0 && e.offsetHeight>0;}
  var rows=[];
  deepAll('input,select,textarea,[role=combobox],[data-testid]').forEach(function(e){
    if(!vis(e))return;
    var a={tag:e.tagName.toLowerCase(),type:e.getAttribute('type'),
           id:e.id||null,name:e.getAttribute('name'),
           ph:e.getAttribute('placeholder'),aria:e.getAttribute('aria-label'),
           testid:e.getAttribute('data-testid'),role:e.getAttribute('role'),
           value:(e.value!==undefined?e.value:null),
           disabled:!!e.disabled,
           html:(e.outerHTML||'').replace(/\s+/g,' ').slice(0,300)};
    rows.push(a);});
  var labels=[];
  deepAll('label').forEach(function(e){ if(vis(e)) labels.push((e.innerText||'').replace(/\s+/g,' ').trim().slice(0,80)); });
  return JSON.stringify({url:location.href, head:(document.querySelector('h1,h2,h3')||document.body).innerText.replace(/\s+/g,' ').slice(0,160),
                         nodes:rows, labels:labels}, null, 1);
})()
"""


def cdp(args, timeout=45):
    done = subprocess.run([CDP] + args, capture_output=True, text=True, timeout=timeout)
    return done.returncode, (done.stdout or "").strip().strip('"')


def load_artifact():
    spec = importlib.util.spec_from_file_location("try_loop", ARTIFACT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    origin = mod.Filler.pick_option

    def pick(self, options):
        cand = [o for o in (options or []) if str(o or "").strip()]
        for want in PREFER:
            for o in cand:
                if str(o).endswith(want) or want in str(o):
                    self.log.info("[probe] 优先挑「%s」（原池子 %s）", want, cand)
                    return o
        return origin(self, options)

    mod.Filler.pick_option = pick
    return mod


def main():
    pathlib.Path(FORM_FILE).write_text("{}", encoding="utf-8")
    window = service.bit_window_from_env()
    ws_url = str(window.fresh_open() or "").strip()
    print("=== fresh_open: %s" % ws_url)
    if not ws_url:
        print("!! 开不出窗口")
        return 3
    host, port = selftest._host_port(ws_url)
    here = ""
    deadline = time.time() + 90
    while time.time() < deadline:
        rc, here = cdp(["eval", "location.href", "--host", host, "--port", str(port)])
        if rc == 0 and here:
            break
        time.sleep(3)
    print("=== 起跑前：%r" % here)
    print("=== navi：%s" % selftest._navigate(CDP, ws_url, ENTRY, None))
    time.sleep(3)
    rc, here = cdp(["eval", "location.href", "--host", host, "--port", str(port)])
    print("=== 导航后：%r" % here)
    if rc != 0 or "try.atticus.com" not in here:
        print("!! 不是入口，停")
        return 5

    mod = load_artifact()
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    trace = str(RUN_DIR / "repro.trace.jsonl")
    log_path = str(RUN_DIR / "repro.log")
    f = mod.Filler(ws_url, FORM_FILE, "repro-rel", trace_path=trace, no_report=True)
    mod.setup_logger("loop")
    #: 产物自己那条 logger 写到 stderr；这里再挂一个文件 handler
    import logging
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    f.log.addHandler(fh)
    ok = f.run()
    print("=== run() -> %s | trace=%s log=%s" % (ok, trace, log_path))

    rc, url = cdp(["eval", "location.href", "--host", host, "--port", str(port)])
    print("=== 停在哪：%r" % url)
    rc, dump = cdp(["eval", DUMP_JS, "--host", host, "--port", str(port)], timeout=60)
    out = RUN_DIR / "contact-dom.json"
    out.write_text(dump or "", encoding="utf-8")
    print("=== DOM 落盘：%s" % out)
    print((dump or "")[:6000])
    print("=== 窗口留着不关：%s" % ws_url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
