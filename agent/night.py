"""夜里那批**预设单**：门口校验 / 载荷 / 早上的账单。

与 `agent/auto.py` 那条「看榜挑站」分开：这条路的单**是人下班前排好的**（网址 + 成功判据）。
过闸、两遍为限、自动上传**全用服务现成的 auto 路**，这里不另造判据。

⚠️ 「算不算成」只有一条：`end_reason == "delivered"`（`state.END_DELIVERED` ——
唯一一个「东西真的落盘了」的结局）。`_keep_unverified` 落盘的那些**不算**、也不会上传。
⚠️ 过不去就**记一笔、下一单**：不在同一单上重试，也不替人重开窗口（与现在 auto 的限一样）。
"""
from __future__ import annotations

import re
import json
import pathlib
import uuid
import time as _time

#: 服务认的两个模式（`RunRequest.mode`）。
MODES = ("build", "fix")

#: 账单的分档（顺序 = 早上读的顺序）。
BUCKETS = (
    ("pushed", "已推送"),
    ("generated", "已生成、没推送"),
    ("needs_human", "需要人"),
    ("not_generated", "没生成"),
    ("failed", "跑挂了"),
    ("rejected", "没提交（队列不合格）"),
    ("timeout", "超时停下"),
    ("not_run", "这次没轮到"),
)
#: 已经有结论的档 —— 同一晚重跑时跳过（`not_run` / `rejected` 要再试；
#: `timeout` 也算结论：supervisor 常驻时不能对同一单无限重跑）。
FINAL = ("pushed", "generated", "needs_human", "not_generated", "failed", "timeout")
#: 窗口那一类停因（`reopen` 接得住的那几种）—— 夜里不替人重开，只在账单上点名。
WINDOW_REASONS = ("explore_unfinished", "paused", "window_gone", "no_window")

_COUNTRY = re.compile(r"^[A-Za-z]{2}$")


def country_of(order: dict) -> str:
    """这一单要切的国家（两字母码，大写）。没给 / 不成形状 ⇒ 空串（**不猜**）。"""
    env = order.get("env") if isinstance(order.get("env"), dict) else {}
    raw = str(env.get("proxy_country") or "").strip()
    return raw.upper() if _COUNTRY.match(raw) else ""


def urls_of(order: dict) -> list:
    """网址那格：列表或「一行一条」都收（与服务 `_success_urls_text` 同一种输入）。"""
    raw = order.get("success_urls")
    if isinstance(raw, (list, tuple)):
        return [str(x).strip() for x in raw if str(x).strip()]
    return [x.strip() for x in str(raw or "").splitlines() if x.strip()]


def problems(order: dict) -> list:
    """门口先拦的那几件（**免费**；别让它白开一趟才报 400）。

    判据照服务 `_intake_problems`，只少不少：
      · 任何模式都要 `goal` 或 fix 的 `evidence`；
      · `auto` 路必须给国家（服务那条切链闸，不猜）；
      · build 必须给成功判据（fix 可空 —— 服务会搬旧脚本自己声明的那条）。
    """
    out = []
    if not str(order.get("url") or "").strip():
        out.append("没有 url")
    mode = str(order.get("mode") or "build").strip()
    if mode not in MODES:
        out.append("mode 只认 build / fix（给的是 %r）" % mode)
    if not country_of(order):
        out.append("没给国家：`env.proxy_country` 要两字母码（例 US）—— 自动路不猜")
    if not str(order.get("goal") or "").strip() and not str(order.get("evidence") or "").strip():
        out.append("没有 goal（fix 也可以给 evidence）")
    if mode == "build" and not str(order.get("success_text") or "").strip() and not urls_of(order):
        out.append("build 没有成功判据：`success_text` 或 `success_urls` 至少一格")
    return out


#: 原样交给服务的字段（其余的是本脚本自己的账，服务不认）。
_KEEP = (
    "url", "goal", "mode", "success_text", "success_urls", "evidence", "site",
    "fix_site", "fix_site_url", "form_file", "form_data", "platform", "out_dir",
    "allow_skips", "entry_url", "expects", "set_viewport", "phone_area_code",
    "rules", "hints", "note", "allow_disabled",
)


def payload(order: dict) -> dict:
    """清单那一行 → `POST /run` 的载荷（`auto` 恒为 true）。"""
    out = {k: order[k] for k in _KEEP if order.get(k) is not None}
    env = dict(order.get("env") or {})
    env["proxy_country"] = country_of(order)
    out["env"] = env
    out["auto"] = True
    return out


def render(rows: list, title: str) -> str:
    """早上的账单（`runtime/auto/night/<date>/morning.md`）—— 运营只看这一份。"""
    lines = ["# 夜间批量 · %s" % title, ""]
    for key, label in BUCKETS:
        got = [r for r in rows if r.get("bucket") == key]
        if not got:
            continue
        lines.append("## %s（%d）" % (label, len(got)))
        for r in got:
            site = (" %s" % r["site"]) if r.get("site") else ""
            job = (" · job `%s`" % r["job_id"]) if r.get("job_id") else ""
            lines.append("- `%s`%s%s — %s" % (r.get("id"), site, job, r.get("say") or ""))
        lines.append("")
    if not rows:
        lines.append("（队列是空的）")
    return "\n".join(lines).rstrip() + "\n"


# ── 队列 / 账单落在哪儿（面板与宿主脚本**同一处口径**）────────────────────

def night_dir(root=".") -> pathlib.Path:
    return pathlib.Path(root) / "runtime" / "auto" / "night"


def queue_path(root=".") -> pathlib.Path:
    return night_dir(root) / "queue.json"


def load_queue(root=".") -> list:
    """读队列；文件不在 = 空队列。**读坏了就抛**（不许当空队列静默跳过）。"""
    path = queue_path(root)
    if not path.exists():
        return []
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise RuntimeError("夜间队列文件读不出来（%s）：%s" % (path, exc))
    if isinstance(body, dict):
        body = body.get("orders") or []
    return [dict(x) for x in body if isinstance(x, dict)]


def save_queue(root=".", orders=None) -> None:
    """原子写（先 `.tmp` 再换）：宿主脚本正在读的时候不会读到半截。"""
    path = queue_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        #: 容器里是 root、宿主 worker 是 dev —— 一侧建的目录另一侧也要能写
        #: （否则宿主建不了日期目录 / supervisor 目录，夜里的账就落不下来）。
        path.parent.chmod(0o777)
    except OSError:
        pass
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps({"orders": list(orders or [])}, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(path)


def add_order(root=".", order=None) -> list:
    """追加一单；同一个 id 不许进两次（否则夜里会重复真提交）。"""
    orders = load_queue(root)
    oid = str((order or {}).get("id") or "")
    if any(str(o.get("id") or "") == oid for o in orders):
        raise ValueError("队列里已经有 id=%s 的单了" % oid)
    orders.append(dict(order or {}))
    save_queue(root, orders)
    return orders


def remove_order(root=".", oid="") -> bool:
    orders = load_queue(root)
    keep = [o for o in orders if str(o.get("id") or "") != str(oid)]
    if len(keep) == len(orders):
        return False
    save_queue(root, keep)
    return True


def report_text(root=".", date="") -> str:
    """今天那份账单（还没有 = 空串）。"""
    path = night_dir(root) / str(date or _time.strftime("%Y-%m-%d")) / "morning.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def queue_view(root=".", today="") -> dict:
    """面板那一栏要的几格（话在服务这一侧拼好 —— 页面只摆字）。"""
    day = str(today or _time.strftime("%Y-%m-%d"))
    try:
        orders = load_queue(root)
    except RuntimeError as exc:
        return {"say": str(exc), "orders": [], "report": report_text(root, day),
                "date": day, "queue": str(queue_path(root))}
    return {"say": "夜间队列 **%d 单**。下班后由宿主脚本按顺序跑；跑完的看下面那份账单。"
                   % len(orders),
            "orders": orders, "report": report_text(root, day),
            "date": day, "queue": str(queue_path(root))}


def queue_add(root=".", raw=None, oid="", country="") -> str:
    """追加一单（判据与宿主脚本**同一处**）；返回最终那个 id。不合格就抛 `ValueError`。"""
    order = dict(raw or {})
    oid = str(oid or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,40}", oid):
        oid = "n-" + uuid.uuid4().hex[:8]
    order["id"] = oid
    env = dict(order.get("env") or {})
    code = str(country or "").strip().upper()
    if code:
        env["proxy_country"] = code
    order["env"] = env
    order.setdefault("mode", "build")
    bad = problems(order)
    if bad:
        raise ValueError("这一单**没排进去**（先说清缺什么，别让它夜里空跑）：\n- "
                         + "\n- ".join(bad))
    add_order(root, order)
    return oid


# ── 跨天的账：supervisor 常驻后，同一单不许因为「换了一天」再跑一遍 ──────────

def _result_files(root=".") -> list:
    base = night_dir(root)
    return sorted(base.glob("*/results.jsonl")) if base.exists() else []


def _result_rows(root=".") -> list:
    out = []
    for path in _result_files(root):
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                out.append(row)
    return out


def settled(root=".") -> set:
    """已经有结论的 id（**所有日期**的 results.jsonl 合起来看）。"""
    return {str(r.get("id")) for r in _result_rows(root)
            if r.get("id") and r.get("bucket") in FINAL}


def interrupted(root=".") -> set:
    """只有 `started`、没有任何结论的 id —— 上一次进程跑到一半没了。

    ⚠️ 这种**不重跑**：重跑就是再往真站提交一遍（宁可少做一单，不悄悄多做）。
    """
    seen = {}
    for row in _result_rows(root):
        oid = str(row.get("id") or "")
        if oid:
            seen.setdefault(oid, set()).add(str(row.get("bucket") or ""))
    return {oid for oid, buckets in seen.items()
            if "started" in buckets and not (buckets & set(FINAL))}
