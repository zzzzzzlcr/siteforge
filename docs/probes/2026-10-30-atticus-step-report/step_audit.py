#!/usr/bin/env python3
"""只读：今天失败榜上的站，各自 formStep 有几行（看 py 脚本的上报到底进没进 formStep）。"""
import datetime
import os
import sys

ROOT = "/company/siteforge"
sys.path.insert(0, ROOT)
from agent import fmr  # noqa: E402


def read_env():
    env = {}
    for line in open(os.path.join(ROOT, "siteforge.env"), encoding="utf-8"):
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k] = v
    return env


def main():
    env = read_env()
    c = fmr.FmrClient(token=env.get("FMR_AGENT_TOKEN", ""))
    today = datetime.date.today().isoformat()
    rank = c.fetch_rank(today) or {}
    rows = (rank.get("rank") or [])[:12]
    print("date", today, "failed_total", rank.get("failed_total"), "sites", len(rows))
    for r in rows:
        site = str(r.get("site") or "")
        try:
            fails = c.fetch_failures(site, since=(datetime.date.today()
                                                  - datetime.timedelta(days=2)).isoformat())
        except Exception as exc:  # noqa: BLE001
            print("%-55s FAIL %s" % (site[:55], str(exc)[:60]))
            continue
        for f in fails[:2]:
            tid = str(f.get("task_id") or "")
            try:
                steps = c.fetch_steps(tid)
            except Exception as exc:  # noqa: BLE001
                print("%-55s %-10s ERR %s" % (site[:55], tid, str(exc)[:40]))
                continue
            names = [str(s.get("step")) for s in steps]
            print("%-55s %-10s steps=%d %s" % (site[:55], tid, len(steps), names[:6]))


if __name__ == "__main__":
    main()
