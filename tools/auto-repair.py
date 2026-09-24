#!/usr/bin/env python3
"""值班（宿主上跑，**不进容器**）：看榜 → 对账 → 开修。逻辑全在 `agent/auto.py::run_once`。

用法：
    python3 tools/auto-repair.py --once                # 只转一圈（验收用）
    python3 tools/auto-repair.py --seconds 300         # 常驻：每 5 分钟一圈
    python3 tools/auto-repair.py --once --service http://127.0.0.1:8080
    python3 tools/auto-repair.py --once --root /company/siteforge

⚠️ 三条硬规矩（写在设计文档里，这里只是照着用）：
  · 总开关**默认关**（`runtime/auto/flags.json` 里 `{"on": true}` 才干活）—— 这个脚本自己**不改**开关；
  · 一次只开一个（`run_once` 一圈只挑第一个够格的站）；
  · 「量不到」**要报错**（不许静默当「没有站要修」）⇒ 抛出来这里打到 stderr、退出码 2。
"""
from __future__ import annotations

import argparse
import os
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import auto, fmr  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="自动值班：看榜 → 对账 → 开修（一次一个）")
    ap.add_argument("--once", action="store_true", help="只转一圈就退出（验收用）")
    ap.add_argument("--seconds", type=float, default=300.0, help="常驻时每圈间隔（秒）")
    ap.add_argument("--root", default=str(ROOT), help="状态目录的根（开关/账本/心跳都在这儿）")
    ap.add_argument("--service", default=os.environ.get("SITEFORGE_SERVICE_URL") or auto.SERVICE_URL,
                    help="服务地址（默认 = 服务真正在听的那个口；SITEFORGE_SERVICE_URL 可覆盖）")
    got = ap.parse_args()

    client = fmr.FmrClient()                       # token 在构造那一刻读环境（`FMR_AGENT_TOKEN`）
    while True:
        try:
            out = auto.run_once(fmr=client, root=got.root, service_url=got.service)
        except Exception as exc:                   # noqa: BLE001 —— 「量不到」不许被读成「没事」
            print("!! 这一圈没量着：%s" % exc, file=sys.stderr)
            return 2
        print("%s %s %s%s" % (time.strftime("%H:%M:%S"), out["did"],
                              out["site"] and "「%s」 " % out["site"] or "", out["why"]))
        if got.once:
            return 0 if out["ok"] else 1
        time.sleep(max(5.0, float(got.seconds)))


if __name__ == "__main__":
    raise SystemExit(main())
