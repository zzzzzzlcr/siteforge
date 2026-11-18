"""三个 soulmatemeets 落地页的**首屏词表**对照（只读：不点、不填、不提交）。

  · 9e822323 —— 本次要判的；
  · e77eb1b0 —— 同域，formConfig/sites 里挂 config 66（goldenagesouls 脚本）；
  · 23d247da —— 同域，挂 config 101（japansdates 脚本）。

跑法（容器 siteforge-222 内）：
  PYTHONPATH=/opt/siteforge SITEFORGE_CDP_BIN=/opt/siteforge/tools/cdp/cdp \
  BIT_WORKER_IP=192.168.1.222 BIT_ID=982d158b41b14fbb87754667859b4719 \
  BIT_API_PORT=54345 python3 compare_entry.py
"""
from __future__ import annotations

import json
import os
import sys

from agent import selftest, service
from agent.classify import (CENSUS_JS, _as_census, census_has_content,
                            classify_census, wait_for_census, wait_for_window)

URLS = [
    "https://soulmatemeets.com/land/sp/9e822323/",
    "https://soulmatemeets.com/land/sp/e77eb1b0/",
    "https://soulmatemeets.com/land/sp/23d247da/",
]


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
        for url in URLS:
            why = selftest._navigate(cdp_bin, ws_url, url, cdp_env)
            if why:
                print("URL", url, "NAV_ERR", why)
                continue
            c = wait_for_census(eval_fn, timeout=30.0)
            if not census_has_content(c):
                print("URL", url, "EMPTY")
                continue
            testids = list(c.get("testids") or [])
            print("URL", url)
            print("  hash", c.get("hash"), "| title", str(c.get("title") or "")[:50])
            print("  testids(%d)" % len(testids), json.dumps(testids[:45], ensure_ascii=False))
            print("  buttons", json.dumps((c.get("buttons") or [])[:12], ensure_ascii=False))
            print("  body", str(c.get("body_text") or "")[:140])
            print("  verdict", json.dumps(classify_census(c), ensure_ascii=False))
        return 0
    finally:
        try:
            window.close()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    raise SystemExit(main())
