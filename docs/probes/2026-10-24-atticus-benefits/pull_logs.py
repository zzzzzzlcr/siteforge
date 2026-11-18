#!/usr/bin/env python3
"""拉 FMR 上 try.atticus.com/ssd/benefits 的线上失败日志（只读）。

⚠️ 不能走 `formLog`：这个站名下的 config（id 162）是**停用**的，`formLog` 只认启用的
⇒ 现在回 404 `config not found`（那是「量不到」，不是「没有失败」）。
能用的两个口：
  · `GET /api/quest/failDiag?site=<键>&fdate=&tdate=` —— 按站点键直查，不经过 config；
  · `GET /api/quest/formLogRank?date=&min_streak=1&days=7&settle=0` —— 每天失败榜，
    停用的配置也标得出来。

用法：
  python3 pull_logs.py site [since] [until]     # 按站点键列 diag（默认近 10 天）
  python3 pull_logs.py show <task_id> [...]     # 某几单的逐行日志
  python3 pull_logs.py rank [YYYY-MM-DD ...]    # 每天这个站失败几条（min_streak=1）

token / base 从 siteforge.env + 环境读（与 agent/fmr.py 同一套）。
"""
import datetime
import json
import os
import sys
import urllib.parse
import urllib.request

SITE = "try.atticus.com/ssd/benefits"
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../.."))


def load_env():
    path = os.path.join(ROOT, "siteforge.env")
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def call(path, params):
    """GET 一个 /api/quest/* 口。**HTTP 恒 200**，业务码在 body.status（旧约定）。"""
    base = (os.environ.get("FMR_BASE_URL") or "https://fmr.3tkj.cn").rstrip("/")
    url = base + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "X-Api-Token": os.environ.get("FMR_AGENT_TOKEN") or "",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=40) as resp:
        body = json.loads(resp.read().decode("utf-8", "replace"))
    if body.get("status") != 200:
        raise SystemExit("后端回 %s：%s（%s）" % (body.get("status"), body.get("msg"), path))
    return body.get("data")


def cmd_site(argv):
    until = argv[1] if len(argv) > 1 else datetime.date.today().isoformat()
    since = argv[0] if argv else (datetime.date.today() - datetime.timedelta(days=9)).isoformat()
    rows = call("/api/quest/failDiag",
                {"site": SITE, "fdate": since, "tdate": until, "per_page": 200}) or []
    print("%s  %s..%s -> %d 条" % (SITE, since, until, len(rows)))
    for r in rows:
        print("  id=%-4s task=%-10s at=%s machine=%s exit=%-10s lines=%d" % (
            r.get("id"), r.get("task_id"), r.get("at"), r.get("machine"), r.get("exit"),
            len((r.get("lines") or "").splitlines())))


def cmd_show(argv):
    if not argv:
        raise SystemExit("给 task_id")
    rows = call("/api/quest/failDiag",
                {"site": SITE, "fdate": "2026-01-01", "tdate": "2030-01-01",
                 "per_page": 1000}) or []
    by_task = {str(r.get("task_id")): r for r in rows}
    for tid in argv:
        r = by_task.get(str(tid))
        if r is None:
            print("== %s：这个站名下的 diag 里没有这一单" % tid)
            continue
        print("=" * 78)
        print("task=%s at=%s machine=%s exit=%s" % (
            tid, r.get("at"), r.get("machine"), r.get("exit")))
        for i, line in enumerate((r.get("lines") or "").splitlines()):
            print("%3d %s" % (i, line))


def cmd_rank(argv):
    days = argv or [datetime.date.today().isoformat()]
    print("date        total  atticus(fail/streak/status)")
    for day in days:
        data = call("/api/quest/formLogRank",
                    {"date": day, "limit": 200, "min_streak": 1, "days": 7, "settle": 0}) or {}
        hit = [r for r in (data.get("rank") or [])
               if "atticus" in str(r.get("site") or "").lower()]
        print("%s  %5s  %s" % (day, data.get("failed_total"),
                               "  ".join("%s/%s/%s" % (r.get("fail"), r.get("streak"),
                                                       r.get("config_status")) for r in hit) or "-"))


def main(argv):
    load_env()
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    {"site": cmd_site, "show": cmd_show, "rank": cmd_rank}.get(cmd, lambda _: print(__doc__))(rest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
