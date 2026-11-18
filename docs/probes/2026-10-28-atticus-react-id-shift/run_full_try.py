"""在 8100 的 Bit（192.168.1.222）上把 Atticus 整条漏斗真跑一遍。

验证 2026-10-28 的修复：联系屏 `How can we reach you?` 上 React 自动 id 漂移时，
读取器应按 placeholder 认格（`\\x02First name`…），四格各就各位、Submit 可点。

从**广告落地页**开跑（与生产同一条路）：`try.atticus.com/ssd/benefits`
→ 前置点「See if you qualify for benefits」→ survey → … → 联系屏 → 提交。

跑法（容器 siteforge-222 内）：
  PYTHONPATH=/opt/siteforge/forms:/opt/siteforge \
  SITEFORGE_CDP_BIN=/opt/siteforge/tools/cdp/cdp \
  BIT_WORKER_IP=192.168.1.222 BIT_ID=982d158b41b14fbb87754667859b4719 \
  BIT_API_PORT=54345 python3 /tmp/run_full_try.py
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
FORM = "/tmp/atticus_full_form.json"
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

        spec = importlib.util.spec_from_file_location("try_loop_patched", ART)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        f = mod.Filler(ws_url, FORM, "atticus_full_%d" % int(time.time()),
                       no_report=True)
        t0 = time.time()
        ok = f.run(max_steps=148)
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
