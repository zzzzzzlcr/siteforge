"""2026-10-22：把 SAME_NEXT_LIMIT 从 3 调到 8 之后，重跑 Atticus 那版循环型产物。

只在 222（8100）上跑：开一个**干净**窗口（清 cookie/缓存）→ 导航到入口 →
在入口页上起 `try.loop.py`（--trace --no-report）→ 跑完读一眼最后那一页。

用法（容器内）：
  docker cp rerun_loop.py siteforge-222:/tmp/rerun_loop.py
  docker exec siteforge-222 python3 /tmp/rerun_loop.py
"""
import json
import pathlib
import subprocess
import sys
import time

sys.path.insert(0, "/opt/siteforge")
from agent import selftest, service  # noqa: E402

PY = "/opt/siteforge/forms/sites/try.loop.py"
ENTRY = "https://try.atticus.com/ssd/benefits"
FORM_FILE = "/tmp/atticus_form.json"
CDP = "/opt/siteforge/tools/cdp/cdp"
HOST_OF_ENTRY = "try.atticus.com"
STAMP = time.strftime("%Y%m%d-%H%M%S")
RUN_DIR = pathlib.Path("/opt/siteforge/runtime/selftest/manual-atticus-loop-%s" % STAMP)


def cdp(args, timeout=45):
    done = subprocess.run([CDP] + args, capture_output=True, text=True, timeout=timeout)
    return done.returncode, (done.stdout or "").strip().strip('"')


pathlib.Path(FORM_FILE).write_text("{}", encoding="utf-8")
window = service.bit_window_from_env()
print("=== 窗口层：worker=%s bit_id=%s" % (window.worker_ip, window.bit_id))

ws_url = str(window.fresh_open() or "").strip()
print("=== fresh_open: %s | 探活 %s" % (ws_url, window.probe()))
if not ws_url:
    print("!! 开不出窗口")
    sys.exit(3)
host, port = selftest._host_port(ws_url)

here = ""
deadline = time.time() + 90
while time.time() < deadline:
    try:
        rc, here = cdp(["eval", "location.href", "--host", host, "--port", str(port)])
    except Exception as exc:  # noqa: BLE001
        print("   读不到（%s）" % exc)
        rc, here = 1, ""
    if rc == 0 and here:
        break
    time.sleep(3)
print("=== 起跑前那一页：%r" % here)
if not here:
    print("!! 窗口起不来")
    sys.exit(4)

print("=== navi 回话：%s" % selftest._navigate(CDP, ws_url, ENTRY, None))
time.sleep(3)
rc, here = cdp(["eval", "location.href", "--host", host, "--port", str(port)])
print("=== 导航后那一页：%r" % here)
if rc != 0 or HOST_OF_ENTRY not in here:
    print("!! 这一页不是入口（%s）—— 停在这里，不动它。" % HOST_OF_ENTRY)
    sys.exit(5)

RUN_DIR.mkdir(parents=True, exist_ok=True)
rep = selftest.run_once(PY, ws_url, FORM_FILE, "try", run_dir=RUN_DIR,
                        cdp_bin=CDP, timeout=420, entry_url=ENTRY or None)
print("=== 这一遍：%s" % json.dumps({k: rep.get(k) for k in
                                     ("rc", "timed_out", "navi", "trace_path", "log_path")},
                                    ensure_ascii=False)[:600])
rc2, final_url = cdp(["eval", "location.href", "--host", host, "--port", str(port)])
rc3, final_head = cdp(["eval", "(document.querySelector('h1,h2,h3')||document.body).innerText.slice(0,160)",
                       "--host", host, "--port", str(port)])
print("=== 最后那一页：%r | 标题头：%s" % (final_url, (final_head or "").replace("\n", " ")[:160]))
print("=== 人话版本：")
print(selftest.evidence_say(rep))
print("=== 窗口留着不关（要用 cdp 复核就趁现在）：%s" % ws_url)
