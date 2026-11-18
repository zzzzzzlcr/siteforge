"""用**修过的** try.loop.py 在 8100 上从 `survey/claim/zip` 真跑一趟。

验证 2026-10-24 的最小改动：`_nudge` 从 `cdp form` 重打改成原生 setter + 全套事件，
zip 填完后 Next 应在 1–2 秒内变可用并继续。真提交；`no_report=True`，不写生产记录。

跑法（容器 siteforge-222 内）：
  PYTHONPATH=/opt/siteforge/forms:/opt/siteforge \
  SITEFORGE_CDP_BIN=/opt/siteforge/tools/cdp/cdp \
  BIT_WORKER_IP=192.168.1.222 BIT_ID=982d158b41b14fbb87754667859b4719 \
  BIT_API_PORT=54345 python3 /tmp/run_loop_zip.py
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
FORM = "/tmp/atticus_loop_form.json"
URL = "https://www.atticus.com/disability-benefits/n/get-help/survey/claim/zip"


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
        time.sleep(3.0)

        email = "alexprobe%d@outlook.com" % random.randint(100000, 999999)
        with open(FORM, "w", encoding="utf-8") as fh:
            json.dump({"first_name": "Alex", "last_name": "Probe",
                       "email": email, "postcode": "10001",
                       "zip": "10001", "zipcode": "10001"}, fh)
        print("RUN loop from %s  email=%s" % (URL, email), flush=True)

        spec = importlib.util.spec_from_file_location("try_loop_patched", ART)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        # 直接从 zip 屏开跑：不导航、到达判据就认这一屏。
        mod.ENTRY_URL = ""
        mod.ARRIVE_URL = "survey/claim/zip"

        f = mod.Filler(ws_url, FORM, "atticus_zip_%d" % int(time.time()),
                       no_report=True)
        t0 = time.time()
        ok = f.run(max_steps=80)
        print("RUN_DONE ok=%r  %.0fs" % (ok, time.time() - t0), flush=True)

        raw = selftest._cdp_eval(cdp_bin, ws_url, "JSON.stringify(location.href)", cdp_env)
        try:
            final = json.loads(raw) if isinstance(raw, str) else str(raw or "")
            if isinstance(final, str) and final.startswith('"'):
                final = json.loads(final)
        except Exception:  # noqa: BLE001
            final = str(raw or "")
        print("FINAL %s" % str(final)[:200], flush=True)
        return 0 if ok else 1
    finally:
        try:
            window.close()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    raise SystemExit(main())
