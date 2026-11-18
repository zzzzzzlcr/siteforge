"""用 config 101 的线上脚本（sha 4c8b9fbe）在 8100 真跑 lauradate 这个 landing。

真提交；`_rpt` 换成空函数，不在生产记录里留行。
跑法（容器 siteforge-222 内）：
  PYTHONPATH=/opt/siteforge/forms:/opt/siteforge \
  SITEFORGE_CDP_BIN=/opt/siteforge/tools/cdp/cdp \
  BIT_WORKER_IP=192.168.1.222 BIT_ID=982d158b41b14fbb87754667859b4719 \
  BIT_API_PORT=54345 python3 /tmp/run_101.py
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import pathlib
import random
import sys
import time

from agent import selftest, service
from agent.classify import wait_for_window

URL = "https://lauradate.com/land/sp/a34293f4/"
ART = "/tmp/japansdates_from_fmr.py"
FORM = "/tmp/lauradate_form.json"
EXPECT = "4c8b9fbe52ca9b479085c8161f59975bf1a70b061c1db2d910a7fa5e7277b948"


def main() -> int:
    raw = pathlib.Path(ART).read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    print("SCRIPT sha", sha, "bytes", len(raw), flush=True)
    if sha != EXPECT:
        print("!! 不是期望的那份 101 脚本 —— 停")
        return 1

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
        time.sleep(4.0)

        email = "alexprobe%d@outlook.com" % random.randint(100000, 999999)
        with open(FORM, "w", encoding="utf-8") as fh:
            json.dump({"first_name": "Alex", "email": email}, fh)
        print("RUN %s  email=%s" % (URL, email), flush=True)

        spec = importlib.util.spec_from_file_location("jd_from_fmr", ART)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        f = mod.JapanSDatesFiller(ws_url, FORM, "lauradate101_%d" % int(time.time()), "")
        f._rpt = lambda step: None
        t0 = time.time()
        ok = f.run(max_steps=60)
        print("RUN_DONE ok=%r  %.0fs" % (ok, time.time() - t0), flush=True)

        raw_url = selftest._cdp_eval(cdp_bin, ws_url,
                                     "JSON.stringify(location.href)", cdp_env)
        final = ""
        try:
            final = json.loads(raw_url) if isinstance(raw_url, str) else str(raw_url or "")
            if isinstance(final, str) and final.startswith('"'):
                final = json.loads(final)
        except Exception:  # noqa: BLE001
            final = str(raw_url or "")
        print("FINAL %s" % str(final)[:180], flush=True)
        print("HASH_STATE %r" % (mod.hash_state(str(final)) if hasattr(mod, "hash_state") else "?",),
              flush=True)
        return 0 if ok else 1
    finally:
        try:
            window.close()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    raise SystemExit(main())
