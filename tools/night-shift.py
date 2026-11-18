#!/usr/bin/env python3
"""夜间批量的**队列消费者**（宿主上跑，由 supervisor 常驻；也可以手动跑一遍）。

用法：
    python3 tools/night-shift.py                 # 跑一遍队列里还没结论的单，然后退出
    python3 tools/night-shift.py --watch         # 常驻：没单就等，有单就跑（supervisor 用这个）
    python3 tools/night-shift.py --dry-run       # 只校验队列（不碰服务、不落盘）
    python3 tools/night-shift.py --only o3       # 只跑这一单（试跑用）

由 supervisor 管（仓库根）：
    python3 tools/night-supervisor.py start|stop|status|restart

队列在 `runtime/auto/night/queue.json`（面板「排进夜间队列」写的就是这份）。
**这一层只做排队和记账**；跑法与判据在 `agent/night.py` / `agent/night_run.py`：
  · **一次只提交一单** —— 服务单飞，且 auto 路的切国家闸在队列非空时回 409；
  · 过不去（needs_human / 没出 py / 跑挂 / 窗口没了）⇒ 记一笔、直接下一单；
  · 超时/服务一直忙 ⇒ 记下并停一会儿，剩下的记「没轮到」（不硬塞、不谎报）；
  · 开跑前先记一条 `started`：进程半路没了就**不重跑**（怕重复真提交），记「跑到一半」。
账单：runtime/auto/night/<date>/morning.md（运营看）+ results.jsonl（跨天去重）。
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import night, night_run  # noqa: E402

#: 服务真在听的那个口（与 `agent/auto.py::SERVICE_URL` 同一个默认）。
SERVICE = "http://127.0.0.1:8099"
#: 停过一次之后至少歇这么久再转下一圈（给服务把窗口让出来）。
STOP_SLEEP = 600.0


def _lock(root: pathlib.Path):
    """单实例：supervisor 与手动各起一个也只会有一个在跑。拿不到就返回 `None`。"""
    path = night.night_dir(root) / "worker.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = path.open("a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def _orders(queue: pathlib.Path, watch: bool) -> list:
    if not queue.exists():
        if watch:
            return []
        raise FileNotFoundError(str(queue))
    body = json.loads(queue.read_text(encoding="utf-8"))
    if isinstance(body, dict):
        body = body.get("orders") or []
    return [dict(x) for x in body if isinstance(x, dict)]


def _rows(path: pathlib.Path) -> list:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _append(path: pathlib.Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _row(oid: str, site: str, got: dict) -> dict:
    return {"id": oid, "site": site, **got}


def _one(root: pathlib.Path, base: str, order: dict, got) -> tuple:
    """排一单：校验 → 跑 → 返回 `(row, stop_reason)`。"""
    oid = order["id"]
    site = str(order.get("site") or order.get("url") or "")
    bad = night.problems(order)
    if bad:
        return _row(oid, site, {"bucket": "rejected", "say": "；".join(bad)}), ""
    got_row, stopped = night_run.run_order(root, base, order, got.poll, got.job_timeout)
    return _row(oid, site, got_row), stopped


def _pass(root: pathlib.Path, base: str, queue: pathlib.Path, got) -> tuple:
    """跑一遍队列。返回 `(处理了几单, 停下的原因)`。"""
    today = time.strftime("%Y-%m-%d")
    out_dir = root / "runtime" / "auto" / "night" / today
    results = out_dir / "results.jsonl"
    rows = _rows(results)
    seen = set()
    half = night.interrupted(root)
    done = night.settled(root)
    processed, stopped = 0, ""

    for i, order in enumerate(_orders(queue, got.watch), 1):
        oid = str(order.get("id") or "order-%02d" % i).strip()
        order["id"] = oid
        if got.only and oid != got.only:
            continue
        if oid in done:
            continue
        dup, seen = oid in seen, seen | {oid}
        site = str(order.get("site") or order.get("url") or "")
        if dup:
            row, stopped = _row(oid, site, {"bucket": "rejected", "say": "id 重复"}), stopped
            if any(r.get("id") == oid and r.get("bucket") == "rejected" for r in rows):
                continue
        elif stopped:
            row, stopped = _row(oid, site, {"bucket": "not_run", "say": stopped}), stopped
        elif oid in half:
            row, stopped = _row(oid, site, {"bucket": "failed",
                                            "say": "上一次跑到一半进程没了 —— 不重跑（怕重复真提交），"
                                                   "等人看。"}), "上一单跑到一半进程没了"
        elif got.dry_run:
            bad = night.problems(order)
            row = _row(oid, site, {"bucket": "rejected", "say": "；".join(bad)} if bad
                       else {"bucket": "ok", "say": json.dumps(night.payload(order),
                                                               ensure_ascii=False)})
            stopped = ""
        else:
            _append(results, {"id": oid, "site": site, "bucket": "started",
                              "at": time.strftime("%Y-%m-%dT%H:%M:%S")})
            row, stopped = _one(root, base, order, got)
        processed += 1
        if got.dry_run:
            print("[dry] %-8s %-10s %s — %s" % (oid, row["bucket"], site, row.get("say") or ""))
            continue
        rows.append(row)
        _append(results, row)
        _remember(out_dir, rows, today)
        print("%-8s %-10s %s — %s" % (oid, row["bucket"], site, row.get("say") or ""))

    if not got.dry_run:
        _remember(out_dir, rows, today)
    return processed, stopped


def _remember(out_dir: pathlib.Path, rows: list, title: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "morning.md").write_text(night.render(rows, title), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="夜间批量：队列消费 + auto 跑 + 早上账单")
    ap.add_argument("--queue", default="", help="队列文件（默认 <root>/runtime/auto/night/queue.json）")
    ap.add_argument("--root", default=str(ROOT), help="状态与产物目录的根")
    ap.add_argument("--service", default=os.environ.get("SITEFORGE_SERVICE_URL") or SERVICE)
    ap.add_argument("--poll", type=float, default=10.0, help="轮询间隔（秒）")
    ap.add_argument("--job-timeout", type=float, default=7200.0, help="单岗最长跑多久（秒）")
    ap.add_argument("--seconds", type=float, default=60.0, help="--watch 没单时多久再看一眼（秒）")
    ap.add_argument("--only", default="", help="只跑这一个 id（试跑用）")
    ap.add_argument("--watch", action="store_true", help="常驻（supervisor 用）；没单就等")
    ap.add_argument("--dry-run", action="store_true", help="只校验队列，不提交也不落盘")
    got = ap.parse_args()

    root = pathlib.Path(got.root).resolve()
    queue = pathlib.Path(got.queue) if got.queue else night.queue_path(root)
    if got.watch and got.dry_run:
        print("--watch 与 --dry-run 是两件事，别一起用。", file=sys.stderr)
        return 2
    guard = None if got.dry_run else _lock(root)
    if not got.dry_run and guard is None:
        print("已经有夜间 worker 在跑（worker.lock 被占）—— 这一个退出。", file=sys.stderr)
        return 0
    base = got.service.rstrip("/")
    try:
        while True:
            worked, stopped = _pass(root, base, queue, got)
            if not got.watch:
                return 0
            if stopped:
                time.sleep(STOP_SLEEP)
            elif worked == 0:
                time.sleep(max(5.0, got.seconds))
    except FileNotFoundError as exc:
        print("没有队列：%s" % exc, file=sys.stderr)
        return 2
    finally:
        if guard is not None:
            guard.close()


if __name__ == "__main__":
    raise SystemExit(main())
