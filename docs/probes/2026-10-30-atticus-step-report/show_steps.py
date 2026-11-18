#!/usr/bin/env python3
"""只读：列某个 task_id 在 FMR 上的 formStep 记录（step / url）。"""
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
    client = fmr.FmrClient(token=env.get("FMR_AGENT_TOKEN", ""))
    for tid in sys.argv[1:]:
        rows = client.fetch_steps(tid)
        print("== task %s -> %d rows" % (tid, len(rows)))
        for r in rows:
            print("   id=%s step=%r url=%s" % (r.get("id"), r.get("step"),
                                               str(r.get("url") or "")[:110]))


if __name__ == "__main__":
    main()
