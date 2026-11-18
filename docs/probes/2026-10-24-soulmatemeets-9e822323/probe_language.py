"""9e822323 首屏是语言选择（step0）——点 English 后再普查一次，看它落到哪一族。

只读：开干净窗口 → 导航 → 点一次语言 → 读 DOM；不填表、不提交。
跑法（容器 siteforge-222 内，Bit 192.168.1.222）：
  BIT_WORKER_IP=192.168.1.222 BIT_ID=982d158b41b14fbb87754667859b4719 \
  BIT_API_PORT=54345 python3 docs/probes/2026-10-24-soulmatemeets-9e822323/probe_language.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

from agent import selftest, service
from agent.classify import (CENSUS_JS, _as_census, census_has_content,
                            census_signature, classify_census, site_key,
                            wait_for_census, wait_for_window)

URL = "https://soulmatemeets.com/land/sp/9e822323/"
LANG = '[data-testid="image-button-english"]'


def brief(census: dict) -> dict:
    testids = list(census.get("testids") or [])
    classes = list(census.get("classes") or [])
    return {
        "hash": census.get("hash"),
        "title": str(census.get("title") or "")[:60],
        "testid_count": len(testids),
        "testids": testids,
        "step_content_item": [t for t in testids
                              if str(t).startswith("step-content-item-")],
        "classes_interesting": [c for c in classes
                                if "step" in c or "terms" in c or "theme" in c][:40],
        "buttons": (census.get("buttons") or [])[:14],
        "body": str(census.get("body_text") or "")[:160],
        "verdict": classify_census(census),
    }


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
        first = wait_for_census(eval_fn, timeout=30.0)
        if not census_has_content(first):
            sys.stderr.write("census 空。\n")
            return 3
        print("BEFORE", json.dumps(brief(first), ensure_ascii=False))

        host, port = selftest._host_port(ws_url)
        cp = subprocess.run([cdp_bin, "click", LANG, "--host", host,
                             "--port", str(port)],
                            capture_output=True, text=True, timeout=60)
        print("CLICK", json.dumps({"rc": cp.returncode,
                                   "out": (cp.stdout or "").strip()[:200],
                                   "err": (cp.stderr or "").strip()[:200]},
                                  ensure_ascii=False))

        before = census_signature(first)
        deadline = time.time() + 20.0
        second = first
        while True:
            time.sleep(0.5)
            second = _as_census(eval_fn(CENSUS_JS))
            if census_signature(second) != before:
                break
            if time.time() >= deadline:
                break
        print("AFTER", json.dumps(brief(second), ensure_ascii=False))
        print("SITE_KEY", site_key(URL))
        return 0
    finally:
        try:
            window.close()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    raise SystemExit(main())
