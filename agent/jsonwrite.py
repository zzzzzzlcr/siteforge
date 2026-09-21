"""JSON 配置安全写回所需的纯函数与本地票据/备份存储。

这一层不碰网络；生产读写仍只经过 :mod:`agent.fmr`。把状态放在服务端，避免让
浏览器拿一份可以篡改的“已验过”声明回来。
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import os
import pathlib
import secrets
import threading
from dataclasses import dataclass
from typing import Any, Optional

DEFAULT_TTL_SECONDS = 600


def canonical_bytes(value: Any) -> bytes:
    """稳定的 UTF-8 JSON；既用于指纹，也用于写后逐字义比较。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def rerun_passed(summary: Any) -> bool:
    """只认执行器明确给出的 success；缺失/新枚举一律不猜。"""
    if not isinstance(summary, dict):
        return False
    return str(summary.get("status") or "").strip().casefold() == "success"


@dataclass(frozen=True)
class Ticket:
    token: str
    site: str
    original: dict
    proposed: dict
    original_sha256: str
    proposed_sha256: str
    summary: dict
    rules: dict
    expires_at: dt.datetime


class TicketStore:
    """进程内、一次性、短时票据。重启即失效是安全属性，不做透明恢复。"""

    def __init__(self, ttl_seconds: int = DEFAULT_TTL_SECONDS, now=None):
        self._ttl = int(ttl_seconds)
        self._now = now or (lambda: dt.datetime.now(dt.timezone.utc))
        self._items: dict[str, Ticket] = {}
        self._lock = threading.Lock()

    def issue(self, *, site: str, original: dict, proposed: dict,
              summary: dict, rules: dict) -> Ticket:
        now = self._now()
        item = Ticket(
            token=secrets.token_urlsafe(32), site=site,
            original=copy.deepcopy(original), proposed=copy.deepcopy(proposed),
            original_sha256=fingerprint(original),
            proposed_sha256=fingerprint(proposed),
            summary=copy.deepcopy(summary), rules=copy.deepcopy(rules),
            expires_at=now + dt.timedelta(seconds=self._ttl),
        )
        with self._lock:
            self._items[item.token] = item
        return item

    def take(self, token: str) -> tuple[Optional[Ticket], str]:
        """取走即销毁，杜绝双击/重放。返回 ``(票据, 原因)``。"""
        with self._lock:
            item = self._items.pop(str(token or ""), None)
        if item is None:
            return None, "missing"
        if self._now() >= item.expires_at:
            return None, "expired"
        return item, ""


class BackupStore:
    """写前原件的本地持久备份；文件名不含站点键，避免路径注入。"""

    def __init__(self, root: os.PathLike | str):
        self.root = pathlib.Path(root)
        self._lock = threading.Lock()

    def save(self, *, site: str, original: dict, replacement: dict) -> str:
        backup_id = "json-" + secrets.token_hex(16)
        payload = {
            "version": 1, "backup_id": backup_id, "site": site,
            "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "original_sha256": fingerprint(original),
            "replacement_sha256": fingerprint(replacement),
            "original": copy.deepcopy(original),
        }
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            path = self.root / (backup_id + ".json")
            # x：理论上随机名不会撞；真撞时也绝不覆盖旧备份。
            with path.open("x", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2, sort_keys=True)
                fh.write("\n")
        return backup_id

    def load(self, backup_id: str) -> dict:
        key = str(backup_id or "")
        if not key.startswith("json-") or not key[5:].isalnum():
            raise ValueError("备份编号形状不对")
        path = self.root / (key + ".json")
        with path.open(encoding="utf-8") as fh:
            payload = json.load(fh)
        if not isinstance(payload, dict) or payload.get("backup_id") != key:
            raise ValueError("备份内容与编号对不上")
        return payload
