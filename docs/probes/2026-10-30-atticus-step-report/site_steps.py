#!/usr/bin/env python3
"""只读：任意站点键 → failDiag 里的任务，再逐单看 formStep 行数。"""
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
    for site in sys.argv[1:]:
        try:
            rows = c._call("/api/quest/failDiag",
                           {"site": site, "fdate": "2026-09-01",
                            "tdate": "2030-01-01", "per_page": 20}) or []
        except Exception as exc:  # noqa: BLE001
            print(site, "ERR", exc)
            continue
        print("== %s -> %d diag rows" % (site, len(rows)))
        for r in rows[:6]:
            tid = str(r.get("task_id") or "")
            steps = c.fetch_steps(tid)
            print("   task=%-10s at=%s steps=%d %s" % (
                tid, str(r.get("at"))[:19], len(steps),
                [str(s.get("step")) for s in steps[:6]]))


if __name__ == "__main__":
    main()
