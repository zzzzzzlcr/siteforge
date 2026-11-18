#!/usr/bin/env python3
"""8100 自动修复一条指定失败单：rebates.com.au/guides/battery-rebate（2026-10-20 16:00+）。

- 放宽榜（`min_streak=1, settle=0`）只保留这一条站；失败行只收 `created_at >= 16:00`；
- `auto.run_once(service_url=http://127.0.0.1:8100)` → 服务收 `mode=fix, auto:true`，自动过闸；
- 只读 FMR + 写 8100 服务；**不手改 py**。
"""
from __future__ import annotations

import datetime
import json
import pathlib
import sys
import time
import urllib.request

sys.path.insert(0, "/company/siteforge")
from agent import auto, fmr  # noqa: E402

SITE = "rebates.com.au/guides/battery-rebate"
ROOT = "/tmp/auto8100"
CUT = datetime.datetime(2026, 10, 20, 16, 0, 0)
TODAY = "2026-10-20"


def _token() -> str:
    env = {}
    for line in open("/company/siteforge/siteforge.env", encoding="utf-8"):
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k] = v
    return env.get("FMR_AGENT_TOKEN") or ""


class OneFailure:
    def __init__(self, client, site: str):
        self.c = client
        self.site = site

    def fetch_rank(self, date=None, limit=None):
        r = self.c._call("/api/quest/formLogRank",
                         {"date": date or TODAY, "limit": limit or 200,
                          "min_streak": 1, "settle": 0}, shape=dict)
        r = dict(r)
        r["rank"] = [x for x in (r.get("rank") or []) if str(x.get("site") or "") == self.site]
        return r

    def fetch_failures(self, site, since=None, limit=None, debug=False):
        rows = self.c.fetch_failures(site, since=since, limit=limit, debug=debug)
        out = []
        for f in rows or []:
            try:
                dt = datetime.datetime.fromisoformat(str(f.get("created_at") or "")).replace(tzinfo=None)
            except ValueError:
                continue
            if dt >= CUT:
                out.append(f)
        return out

    def fetch_steps(self, task_id):
        return self.c.fetch_steps(task_id)


LAST = {}


def post(url, payload):
    code, body = auto._post_json(url, payload)
    LAST["code"], LAST["body"] = code, body
    print("POST", code, body[:500], flush=True)
    return code, body


def get_job(jid):
    with urllib.request.urlopen("http://127.0.0.1:8100/job/%s" % jid, timeout=15) as r:
        return json.loads(r.read().decode())


def main() -> int:
    pathlib.Path(ROOT, "runtime", "auto").mkdir(parents=True, exist_ok=True)
    pathlib.Path(ROOT, "runtime", "auto", "flags.json").write_text(
        json.dumps({"on": True, "note": "8100 自动修 rebates.com.au（用户 2026-10-20 16:28 点名）"},
                   ensure_ascii=False), encoding="utf-8")
    client = fmr.FmrClient(token=_token())
    out = auto.run_once(fmr=OneFailure(client, SITE), post=post, root=ROOT,
                        service_url="http://127.0.0.1:8100")
    print("RUN_ONCE", json.dumps(out, ensure_ascii=False), flush=True)
    jid = ""
    try:
        jid = str(json.loads(LAST.get("body") or "{}").get("job_id") or "")
    except ValueError:
        pass
    print("JOB", jid, flush=True)
    if not jid:
        return 1
    last = None
    for _ in range(60):                     # 最多 ~20 分钟
        try:
            d = get_job(jid)
        except Exception as exc:            # noqa: BLE001
            print("poll err", type(exc).__name__, exc, flush=True)
            time.sleep(20)
            continue
        st, gate = d.get("status"), (d.get("gate") or {}).get("step")
        say = (d.get("say") or "")[:200]
        if (st, gate, say) != last:
            print(time.strftime("%H:%M:%S"), st, "gate", gate, "|", say, flush=True)
            last = (st, gate, say)
        if st in ("done", "failed", "stopped") or d.get("delivered"):
            r = d.get("result") or {}
            print("FINAL", st, "delivered", d.get("delivered"), "end_reason", r.get("end_reason"),
                  "py_path", r.get("py_path"), flush=True)
            print("say:", (d.get("say") or "")[:800], flush=True)
            return 0
        time.sleep(20)
    print("TIMEOUT watching", jid, flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
