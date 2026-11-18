"""跑一趟 forms-222/sites/soulmatemeets_imagebutton.py（产线形状），验证封装没跑偏。

真提交；`report=False`（--no-report 同义），不在生产 URL 记录里留一行。
跑法（容器 siteforge-222 内）：
  PYTHONPATH=/opt/siteforge/forms:/opt/siteforge/forms/sites:/opt/siteforge \
  SITEFORGE_CDP_BIN=/opt/siteforge/tools/cdp/cdp \
  BIT_WORKER_IP=192.168.1.222 BIT_ID=982d158b41b14fbb87754667859b4719 \
  BIT_API_PORT=54345 python3 /tmp/run_production.py
"""
from __future__ import annotations

import json
import os
import random
import sys
import time

from agent import selftest, service
from agent.classify import wait_for_window
from soulmatemeets_imagebutton import SoulmateMeetsImageButtonFiller, hash_state

URL = "https://soulmatemeets.com/land/sp/9e822323/"
FORM = "/tmp/sm_variant_form.json"


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
            json.dump({"first_name": "Alex", "email": email}, fh)
        print("RUN production script  email=%s" % email, flush=True)
        f = SoulmateMeetsImageButtonFiller(
            ws_url, FORM, "sm_prod_check_%d" % int(time.time()),
            force_long=True, report=False)
        ok = f.run()

        raw = selftest._cdp_eval(cdp_bin, ws_url, "JSON.stringify(location.href)", cdp_env)
        try:
            final = json.loads(raw) if isinstance(raw, str) else str(raw or "")
            if isinstance(final, str) and final.startswith('"'):
                final = json.loads(final)
        except Exception:  # noqa: BLE001
            final = str(raw or "")
        print("RESULT ok=%s final=%s hash_state=%s" % (
            ok, str(final)[:140], hash_state(str(final)) or "-"), flush=True)
        return 0 if ok else 1
    finally:
        try:
            window.close()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    raise SystemExit(main())
