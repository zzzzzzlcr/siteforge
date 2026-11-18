#!/usr/bin/env python3
"""只读监测：FMR 榜上 2026-10-20 16:00 以后的新失败单。

- 只读（`formLogRank` / `fetch_failures`），不建 job、不写 FMR、不改任何状态；
- 榜用 `min_streak=1 & settle=0` 取**全量**（2026-10-20 实测：默认 `min_streak=5` 只看得到 3 个站，
  放宽后 20 个；`settle=0` 把「结算中」的也摆出来）—— 仍然看不到没有站点键的（`unattributed`）
  与后端 404 的键（`try.atticus` / `taboola`），这两类会打印 ERR-FAILURES 而不是假装没有；
- token 从 `/company/siteforge/siteforge.env` 读；每 `--seconds` 一轮，新单按 task_id 去重。
"""
from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import sys
import time

sys.path.insert(0, "/company/siteforge")
from agent import fmr  # noqa: E402

CUT = datetime.datetime(2026, 10, 20, 16, 0, 0)   # 本地时间（FMR created_at 是 +08:00）
TODAY = "2026-10-20"
SEEN = pathlib.Path("/tmp/fmr_watch_1600_seen.json")


def _token() -> str:
    env = {}
    for line in open("/company/siteforge/siteforge.env", encoding="utf-8"):
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k] = v
    return env.get("FMR_AGENT_TOKEN") or ""


def _when(row: dict):
    try:
        return datetime.datetime.fromisoformat(str(row.get("created_at") or "")).replace(tzinfo=None)
    except ValueError:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=300.0)
    args = ap.parse_args()
    client = fmr.FmrClient(token=_token())
    seen = set()
    if SEEN.exists():
        try:
            seen = set(json.loads(SEEN.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            seen = set()
    while True:
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            rank = client._call("/api/quest/formLogRank",
                                {"date": TODAY, "limit": 200,
                                 "min_streak": 1, "settle": 0}, shape=dict)
            rows = list(rank.get("rank") or [])
            new = []
            for row in rows:
                site = str(row.get("site") or "").strip()
                if not site:
                    continue
                try:
                    fails = client.fetch_failures(site, since=TODAY)
                except Exception as exc:                       # noqa: BLE001
                    print("%s ERR-FAILURES %s %s: %s" % (stamp, site, type(exc).__name__, exc),
                          flush=True)
                    continue
                for f in fails or []:
                    dt = _when(f)
                    tid = str(f.get("task_id") or "").strip()
                    if dt is None or dt < CUT or not tid or tid in seen:
                        continue
                    seen.add(tid)
                    new.append((site, f))
                    print("%s NEW site=%s task_id=%s country=%s created_at=%s status=%s"
                          % (stamp, site, tid, f.get("country"), f.get("created_at"),
                             f.get("status")), flush=True)
            if new:
                SEEN.write_text(json.dumps(sorted(seen)), encoding="utf-8")
            else:
                print("%s NO-NEW rank=%d failed_total=%s below_streak=%s settling=%s"
                      % (stamp, len(rows), rank.get("failed_total"),
                         rank.get("below_streak"), rank.get("settling")), flush=True)
        except Exception as exc:                               # noqa: BLE001
            print("%s CYCLE-ERR %s: %s" % (stamp, type(exc).__name__, exc), flush=True)
        time.sleep(max(10.0, float(args.seconds)))


if __name__ == "__main__":
    raise SystemExit(main())
