#!/usr/bin/env python3
"""只读：列这个站在 FMR formLog 上的任务（任意状态），再逐单看 formStep 行数。"""
import os
import sys

ROOT = "/company/siteforge"
sys.path.insert(0, ROOT)
from agent import fmr  # noqa: E402

SITE = "try.atticus.com/ssd/benefits"


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
    for status in ("success", "", "failed"):
        params = {"site": SITE, "since": "2026-10-20T00:00:00+08:00", "limit": 30}
        if status:
            params["status"] = status
        try:
            rows = c._call("/api/quest/formLog", params) or []
        except Exception as exc:  # noqa: BLE001
            print("status=%r ERR %s" % (status, exc))
            continue
        print("status=%r -> %d rows" % (status, len(rows)))
        for r in rows[:10]:
            tid = str(r.get("task_id") or "")
            steps = c.fetch_steps(tid)
            print("   task=%-10s status=%s created=%s steps=%d %s" % (
                tid, r.get("status"), r.get("created_at"), len(steps),
                [str(s.get("step")) for s in steps[:5]]))


if __name__ == "__main__":
    main()
