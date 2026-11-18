"""2026-11-07：用打好补丁的线上脚本真跑一遍 youmetalks 条款屏。

`_rpt` 是空函数 → 不写 FMR；真浏览器、真提交。成功判据走脚本自己的
`is_success`（URL/hash），不另造。
"""
import importlib.util
import json
import random
import sys
import time

sys.path.insert(0, "/opt/siteforge")
from agent import service  # noqa: E402

URL = "https://youmetalks.com/land/sp/b4dd2154"
ART = "/opt/siteforge/forms/sites/_probe_youmetalks_patched.py"
FORM = "/tmp/youmetalks_form_patched.json"


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
        spec = importlib.util.spec_from_file_location("jd_patched", ART)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        f = mod.JapanSDatesFiller(ws, FORM, "ympatched_%d" % int(time.time()), "")
        f._rpt = lambda step: None
        f.cdp.navigate(URL)
        time.sleep(3)
        t0 = time.time()
        ok = f.run(max_steps=60)
        print("== run() 返回 %r，耗时 %.0fs" % (ok, time.time() - t0), flush=True)
        raw = f.cdp.eval("JSON.stringify([location.href,document.title])", "")
        print("== 最终 =", str(raw)[:500], flush=True)
        return 0 if ok else 1
    finally:
        try:
            window.close()
        except Exception as exc:  # noqa: BLE001
            print("关窗报错：%s" % exc, flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
