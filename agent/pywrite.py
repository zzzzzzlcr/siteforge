"""py 上传（写回后端那份脚本）需要的纯函数与本地票据/备份存储。

与 :mod:`agent.jsonwrite` **同形状、同纪律**，只有两处不同（那两处正是不能照抄的原因）：
  · 指纹算的是**源码字节**（`sha256(text.encode("utf-8"))`），不是 `canonical_bytes` 那份
    规范化 JSON —— 后端原话「首尾换行是源码的一部分」，规范化一下就不是同一份文件了；
  · 备份存的是**原文**（`.py`），不是一份 dict。

⚠️ 这一层不碰网络（生产读写仍只经过 :mod:`agent.fmr`），也不碰浏览器：
票据放在**服务端**，免得浏览器拿一份可以篡改的「已经验过了」回来。
⚠️ 票据**进程内、一次性、短时**：重启即失效是安全属性（与 JSON 那条同一条裁定），
不做透明恢复；要重来就重新预检。
⚠️ 但**备份落盘**：上传把线上那份换掉了，万一要回滚，人得有一份拿得回来的原件。
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import pathlib
import secrets
import threading
from dataclasses import dataclass
from typing import Optional

DEFAULT_TTL_SECONDS = 600


def sha256_text(text: str) -> str:
    """源码的指纹。**逐字节**（UTF-8），不做任何规范化 —— 后端也是这么算的。"""
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class UploadTicket:
    """一次「预检通过、等着人确认」的上传。"""

    ticket: str
    #: 后端那个键（FMR 的 `site`）—— 不是本地文件名。
    key: str
    #: 本地那份（`forms/sites/<短名>.py`，这一趟落盘的产物）。
    path: str
    #: 要传上去的源码 + 它的指纹（逐字节）。
    source: str
    source_sha256: str
    #: 预检那一刻后端**现在**那份 + 它的指纹（写前拿它防覆盖，回滚拿它兜底）。
    original: str
    original_sha256: str
    #: 谁确认的（后端必填的审计字段）。
    operator: str
    expires_at: dt.datetime


class UploadTickets:
    """进程内、一次性、短时票据（与 `jsonwrite.TicketStore` 同一个立场）。"""

    def __init__(self, ttl_seconds: int = DEFAULT_TTL_SECONDS, now=None):
        self._ttl = int(ttl_seconds)
        self._now = now or (lambda: dt.datetime.now(dt.timezone.utc))
        self._items: dict[str, UploadTicket] = {}
        self._lock = threading.Lock()

    def issue(self, *, key: str, path: str, source: str, original: str,
              operator: str) -> UploadTicket:
        now = self._now()
        item = UploadTicket(
            ticket=secrets.token_urlsafe(32), key=str(key), path=str(path),
            source=str(source), source_sha256=sha256_text(source),
            original=str(original), original_sha256=sha256_text(original),
            operator=str(operator), expires_at=now + dt.timedelta(seconds=self._ttl))
        with self._lock:
            self._items[item.ticket] = item
        return item

    def take(self, ticket: str) -> tuple[Optional[UploadTicket], str]:
        """取走即销毁，杜绝双击/重放。返回 `(票据, 原因)`（原因：`missing` / `expired`）。"""
        with self._lock:
            item = self._items.pop(str(ticket or ""), None)
        if item is None:
            return None, "missing"
        if self._now() >= item.expires_at:
            return None, "expired"
        return item, ""


class TextBackupStore:
    """上传前**原件**的本地持久备份；文件名是随机名（不含站点键 ⇒ 没有路径注入那一说）。"""

    def __init__(self, root: os.PathLike | str):
        self.root = pathlib.Path(root)
        self._lock = threading.Lock()

    def save(self, *, key: str, original: str, replacement_sha256: str) -> str:
        backup_id = "py-" + secrets.token_hex(16)
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            path = self.root / (backup_id + ".py")
            meta = self.root / (backup_id + ".json")
            # 信里那一行交代「这是谁的备份」：写成**同伴文件**，不往源码里塞任何东西
            # （源码要能逐字节回滚回去，动它一下就不是原件了）。
            # x：随机名理论上不会撞；真撞了也绝不覆盖旧备份。
            with path.open("x", encoding="utf-8", newline="") as fh:
                fh.write(str(original))
            with meta.open("x", encoding="utf-8") as fh:
                json.dump({"version": 1, "backup_id": backup_id, "site": key,
                           "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                           "original_sha256": sha256_text(original),
                           "replacement_sha256": str(replacement_sha256)}, fh,
                          ensure_ascii=False, indent=2, sort_keys=True)
                fh.write("\n")
        return backup_id

    def load(self, backup_id: str) -> tuple[str, dict]:
        """`(原件源码, 信)`。编号形状不对 / 对不上 ⇒ `ValueError`（不猜）。"""
        bid = str(backup_id or "")
        if not bid.startswith("py-") or not bid[3:].isalnum():
            raise ValueError("备份编号形状不对")
        meta = self.root / (bid + ".json")
        path = self.root / (bid + ".py")
        with meta.open(encoding="utf-8") as fh:
            info = json.load(fh)
        if not isinstance(info, dict) or info.get("backup_id") != bid:
            raise ValueError("备份内容与编号对不上")
        with path.open(encoding="utf-8", newline="") as fh:
            original = fh.read()
        if sha256_text(original) != str(info.get("original_sha256") or ""):
            raise ValueError("备份原件与信里那个指纹对不上（文件被动过？）")
        return original, info
