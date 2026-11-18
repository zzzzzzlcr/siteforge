"""夜间批量的**跑一单**（HTTP 那半边）—— 与 `night.py` 的纯逻辑分开，CLI 那一层只负责排队。

这里一个函数只做一件事：提交 / 轮询 / 判终局 / 核对上传。
⚠️ 判据不新造：过闸与两遍为限都在服务那条 auto 路上；这里只认 `end_reason == "delivered"`。
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import time
import urllib.error
import urllib.parse
import urllib.request

from agent import night

#: 提交撞上「有活在跑」时再试几下（一单刚落幕那一小会儿）。
BUSY_TRIES = 5
BUSY_WAIT = 20.0


def call(method: str, url: str, body=None, timeout: float = 30.0) -> tuple:
    """→ `(HTTP 码, 正文 dict)`；连不上回 `(0, {"detail": ...})`（不抛给调用方）。"""
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as got:
            return int(got.status), _decode(got.read())
    except urllib.error.HTTPError as exc:
        return int(exc.code), _decode(exc.read())
    except Exception as exc:                       # noqa: BLE001 —— 外面世界
        return 0, {"detail": "连不上：%s" % exc}


def _decode(raw: bytes) -> dict:
    text = raw.decode("utf-8", "replace")
    try:
        got = json.loads(text)
    except ValueError:
        return {"detail": text[:500]}
    return got if isinstance(got, dict) else {"detail": str(got)[:500]}


def detail(body) -> str:
    return str((body or {}).get("detail") if isinstance(body, dict) else body)[:400]


def submit(base: str, order: dict) -> tuple:
    """提交一单；`409`（服务还在跑别的单）按固定间隔再试，别把「忙」当成「拒」。"""
    code, body = 0, {}
    for attempt in range(BUSY_TRIES):
        code, body = call("POST", base + "/run", night.payload(order))
        if code != 409 or attempt == BUSY_TRIES - 1:
            break
        time.sleep(BUSY_WAIT)
    return code, body


def needs_human(base: str) -> set:
    code, body = call("GET", base + "/auto")
    if code != 200:
        return set()
    return {str(n.get("job_id") or "") for n in (body.get("needs_human") or [])}


def py_path(root: pathlib.Path, raw) -> pathlib.Path | None:
    """`py_path` 可能是容器里那串（`/opt/siteforge/...`）—— 落在宿主这棵树上的那一个。"""
    if not raw:
        return None
    p = pathlib.Path(str(raw))
    cand = [p] if p.is_absolute() else [root / p]
    if p.is_absolute():
        cand.append(root / str(p).lstrip("/"))
        if str(p).startswith("/opt/siteforge/"):
            cand.append(root / str(p)[len("/opt/siteforge/"):])
    return next((x for x in cand if x.exists()), None)


def upload_state(root: pathlib.Path, order: dict, view: dict) -> tuple:
    """交付之后**后台是不是这一份**（服务上传时已经这样校验，这里给早上一个读数）。"""
    if order.get("allow_disabled"):
        return False, "停用行 —— 按规矩不自动上传，等人传。"
    path = py_path(root, (view.get("result") or {}).get("py_path"))
    if path is None:
        return False, "盘上找不到这一版 py，没法核对传没传。"
    key = str(order.get("fix_site") or order.get("site") or view.get("site")
              or order.get("url") or "").strip()
    try:
        from agent import fmr                       # 读口是公开的，不需要 token
        meta = fmr.FmrClient().form_script(key)
    except Exception as exc:                        # noqa: BLE001
        return False, "已交付，但后台回读不了（%s）—— 传没传要人工核。" % exc
    want = hashlib.sha256(path.read_bytes()).hexdigest()
    got = str((meta or {}).get("sha256") or "")
    if got and got == want:
        return True, "后台那份的 sha256 与盘上一致（已自动上传）。"
    return False, ("已交付，但后台不是这一份（后台 %s / 盘上 %s）—— 没传成或还没生效。"
                   % (got[:16] or "没有", want[:16]))


def settle(root: pathlib.Path, order: dict, view: dict) -> dict:
    """终局 → 账单上的那一行（`delivered` 才谈得上「推没推」）。"""
    reason = str((view.get("result") or {}).get("end_reason") or "")
    if reason != "delivered":
        bucket = "needs_human" if reason in night.WINDOW_REASONS else "not_generated"
        return {"bucket": bucket, "say": str(view.get("say") or ("end_reason=%s" % reason))}
    pushed, note = upload_state(root, order, view)
    return {"bucket": "pushed" if pushed else "generated",
            "say": ("%s %s" % (view.get("say") or "", note)).strip()}


def run_order(root: pathlib.Path, base: str, order: dict,
              poll: float = 10.0, timeout: float = 7200.0) -> tuple:
    """跑一单。返回 `(row, stop_reason)`；`stop_reason` 非空 = 后面的单别塞了。"""
    code, body = submit(base, order)
    if code not in (200, 202):
        said = "提交被拒（%s）：%s" % (code, detail(body))
        if code == 409:                             # 服务一直忙：不是这一单的错，别再硬塞
            return {"bucket": "failed", "say": "服务一直忙（409）：%s" % detail(body)}, \
                   "服务一直在跑别的单（409）—— 这一单没提交成功"
        if code == 0:
            return {"bucket": "failed", "say": detail(body)}, "服务连不上"
        return {"bucket": "rejected", "say": said}, ""
    job_id = str(body.get("job_id") or "")
    if not job_id:
        return {"bucket": "failed", "say": "服务没给 job_id：%s" % detail(body)}, "服务没给 job_id"
    return _poll(root, base, order, job_id, poll, timeout)


def _poll(root: pathlib.Path, base: str, order: dict, job_id: str,
          poll: float, timeout: float) -> tuple:
    deadline = time.time() + timeout
    while True:
        if time.time() > deadline:
            spent = ("%d 秒" % int(timeout)) if timeout < 120 else ("%d 分钟" % int(timeout // 60))
            return ({"bucket": "timeout", "say": "跑了 %s 还没结论（job 可能还占着窗口）" % spent},
                    "上一单超时（job 可能还占着窗口）—— 这一单今晚没轮到")
        code, view = call("GET", base + "/job/" + urllib.parse.quote(job_id))
        if code != 200:
            time.sleep(poll)
            continue
        status = str(view.get("status") or "")
        if status == "waiting":
            if job_id in needs_human(base):
                return {"bucket": "needs_human",
                        "say": str(view.get("say") or "停在闸上等人")}, ""
        elif status == "done":
            return settle(root, order, view), ""
        elif status == "failed":
            return {"bucket": "failed",
                    "say": str(view.get("say") or view.get("error") or "跑挂了")}, ""
        time.sleep(poll)
