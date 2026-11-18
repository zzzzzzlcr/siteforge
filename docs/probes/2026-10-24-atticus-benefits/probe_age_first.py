"""Age-first 变体：loop 的 `value_for` 认不认得出年龄格？

只读（不填、不提交）。开 `landing/default`，用 loop 自己的 `read()`（穿透 shadow DOM）
看这一屏；对每个空格打印 token + `value_for` 会给出的值。

跑法（容器 siteforge-222 内）：
  PYTHONPATH=/opt/siteforge/forms:/opt/siteforge \
  SITEFORGE_CDP_BIN=/opt/siteforge/tools/cdp/cdp \
  BIT_WORKER_IP=192.168.1.222 BIT_ID=982d158b41b14fbb87754667859b4719 \
  BIT_API_PORT=54345 python3 /tmp/probe_age_first.py
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
FORM = "/tmp/age_first_form.json"
URL = "https://www.atticus.com/disability-benefits/n/get-help/survey/landing/default"


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
        time.sleep(4.0)

        spec = importlib.util.spec_from_file_location("try_loop_age", ART)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        f = mod.Filler(ws_url, FORM, "age_first_probe", no_report=True)
        page = f.read()
        if not page:
            print("READ_FAIL")
            return 1
        print("URL", page.get("url"), flush=True)
        print("HEAD", str(page.get("head") or "")[:160], flush=True)
        print("INPUTS", json.dumps(page.get("inputs") or [], ensure_ascii=False), flush=True)
        print("OPTIONS", json.dumps((page.get("options") or [])[:10], ensure_ascii=False),
              flush=True)
        print("NEXT", page.get("nextVisible"), "disabled", page.get("nextDisabled"),
              "txt", page.get("nextTxt"), flush=True)
        print("FILL_ORDER", mod.FILL_ORDER, flush=True)
        for i, pair in enumerate(page.get("inputs") or []):
            if not isinstance(pair, (list, tuple)) or len(pair) < 2:
                continue
            token, value = str(pair[0]), str(pair[1] or "")
            if value:
                continue
            print("FIELD", repr(token), "-> value_for =", repr(f.value_for(token, page, i)),
                  flush=True)
        return 0
    finally:
        try:
            window.close()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    raise SystemExit(main())
