"""直接验 loop 的 `_set_value_events`：zip 屏上先 cdp form，再原生事件，看 Next。

不提交。跑法（容器 siteforge-222 内）：
  PYTHONPATH=/opt/siteforge/forms:/opt/siteforge \
  SITEFORGE_CDP_BIN=/opt/siteforge/tools/cdp/cdp \
  BIT_WORKER_IP=192.168.1.222 BIT_ID=982d158b41b14fbb87754667859b4719 \
  BIT_API_PORT=54345 python3 /tmp/probe_nudge_method.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import time

from agent import selftest, service
from agent.classify import wait_for_window

ART = "/opt/siteforge/forms/sites/try.loop.py"
FORM = "/tmp/nudge_form.json"
URL = "https://www.atticus.com/disability-benefits/n/get-help/survey/claim/zip"


def main() -> int:
    with open(FORM, "w", encoding="utf-8") as fh:
        json.dump({"first_name": "Alex", "last_name": "Probe",
                   "email": "alexprobe@outlook.com", "postcode": "10001"}, fh)

    window = service.bit_window_from_env(os.environ)
    if window is None:
        return 3
    ws_url = str(window.fresh_open() or "")
    if not ws_url:
        return 3
    cdp_bin = os.environ.get("SITEFORGE_CDP_BIN") or "cdp"
    cdp_env = os.environ.copy()

    def eval_fn(js):
        return selftest._cdp_eval(cdp_bin, ws_url, js, cdp_env)

    try:
        if not wait_for_window(eval_fn):
            return 3
        why = selftest._navigate(cdp_bin, ws_url, URL, cdp_env)
        if why:
            sys.stderr.write("导航没成：%s\n" % why)
            return 3
        time.sleep(3.0)

        spec = importlib.util.spec_from_file_location("try_loop_nudge", ART)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        f = mod.Filler(ws_url, FORM, "nudge_probe", no_report=True)

        page = f.read()
        if not page:
            print("READ_FAIL")
            return 1
        token = ""
        for item in page.get("inputs") or []:
            if len(item) >= 2 and str(item[0]).endswith("zipcode"):
                token = str(item[0])
                break
        print("TOKEN", repr(token), "nextDisabled(before)", page.get("nextDisabled"),
              flush=True)
        if not token:
            print("NO_ZIP_TOKEN", json.dumps(page.get("inputs") or [])[:300], flush=True)
            return 1

        print("== A: f.fill(token, 10001)  [cdp form]", flush=True)
        print("   fill ->", f.fill(token, "10001"), flush=True)
        time.sleep(3)
        p1 = f.read() or {}
        print("   after fill: nextDisabled=", p1.get("nextDisabled"),
              "inputs=", json.dumps(p1.get("inputs") or [])[:120], flush=True)

        print("== B: f._set_value_events(token, 10001)  [native setter+events]", flush=True)
        print("   events ->", f._set_value_events(token, "10001"), flush=True)
        for i in range(10):
            time.sleep(0.5)
            p2 = f.read() or {}
            print("   +%.1fs nextDisabled=%s" % ((i + 1) * 0.5, p2.get("nextDisabled")),
                  flush=True)
            if not p2.get("nextDisabled"):
                break
        return 0
    finally:
        try:
            window.close()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    raise SystemExit(main())
