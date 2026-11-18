"""真站整单：验证 `try.loop.py` 现在**边做边报 step**（同一屏只报一次）。

跑法（容器 siteforge-222 内）：
  PYTHONPATH=/opt/siteforge/forms:/opt/siteforge \
  SITEFORGE_CDP_BIN=/opt/siteforge/tools/cdp/cdp \
  BIT_WORKER_IP=192.168.1.222 BIT_ID=982d158b41b14fbb87754667859b4719 \
  BIT_API_PORT=54345 python3 /tmp/run_report_check.py

⚠️ 不上报生产接口：import 之后把产物里的 `report_url` 换成**本机收集器**，
   `no_report=False` 让 `_rpt_if_moved` 真的走这条线 —— 收集到什么就打印什么。
"""
from __future__ import annotations

import importlib.util
import json
import os
import random
import sys
import time

from agent import selftest, service
from agent.classify import wait_for_window

ART = "/opt/siteforge/forms/sites/try.loop.py"
FORM = "/tmp/atticus_report_form.json"
URL = "https://try.atticus.com/ssd/benefits"


def main() -> int:
    window = service.bit_window_from_env(os.environ)
    if window is None:
        sys.stderr.write("没配 BIT_WORKER_IP / BIT_ID。\n")
        return 3
    ws_url = str(window.fresh_open() or "")
    if not ws_url:
        sys.stderr.write("开不出窗口。\n")
        return 3
    cdp_bin = os.environ.get("SITEFORGE_CDP_BIN") or "cdp"
    cdp_env = os.environ.copy()

    def eval_fn(js):
        return selftest._cdp_eval(cdp_bin, ws_url, js, cdp_env)

    try:
        if not wait_for_window(eval_fn):
            sys.stderr.write("窗口没就绪。\n")
            return 3
        why = selftest._navigate(cdp_bin, ws_url, URL, cdp_env)
        if why:
            sys.stderr.write("导航没成：%s\n" % why)
            return 3
        time.sleep(5.0)

        email = "alexprobe%d@outlook.com" % random.randint(100000, 999999)
        form = {"age": "58", "postcode": "10001", "first_name": "James",
                "last_name": "Carter", "email": email, "phone": "2125550147"}
        with open(FORM, "w", encoding="utf-8") as fh:
            json.dump(form, fh)
        print("RUN full %s  email=%s" % (URL, email), flush=True)

        spec = importlib.util.spec_from_file_location("try_loop_report", ART)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        reports = []

        def capture(cdp, tid, step, log=None, reason=""):
            reports.append((tid, step, reason))
            print("REPORT tid=%r step=%r reason=%r" % (tid, step, reason), flush=True)
            return True

        mod.report_url = capture

        #: 生产那条路只给 `--correlation-id <task>_<ts>`（ad-task 不传 --task-id）——
        #: 这里照抄那个形状，验 tid 能从 correlation_id 拆出来。
        cid = "26999999_%d" % int(time.time())
        f = mod.Filler(ws_url, FORM, cid, no_report=False)
        t0 = time.time()
        ok = f.run(max_steps=148)
        print("RUN_DONE ok=%r  %.0fs" % (ok, time.time() - t0), flush=True)
        steps = [r for r in reports if r[1] == "step"]
        urls = [r for r in reports if r[1] != "step"]
        print("REPORT_COUNT step=%d other=%d" % (len(steps), len(urls)), flush=True)
        for r in steps:
            print("STEP %r" % (r[2],), flush=True)
        bad = sorted({r[0] for r in reports if r[0] != "26999999"})
        print("TIDS_OK %r" % (not bad,), bad, flush=True)
        return 0 if ok and steps and not bad else 1
    finally:
        try:
            window.close()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    raise SystemExit(main())
