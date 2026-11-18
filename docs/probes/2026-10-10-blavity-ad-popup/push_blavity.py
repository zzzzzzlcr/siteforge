#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把验过的 blavity 产物推到 FMR（key `blavity.com`）。照 `push_delachat.py` 那套动作：
读后端现状 → 写前备份 → 写 → **回读逐字节核对**；对不上自动传回原来那份。

★ 与 `push_delachat.py` 的**唯一**区别：**按字节读源文件**。
   blavity 这份是 CRLF（3132 个），而 `Path.read_text()` 会静默把 CRLF 全变成 LF
   —— 本机实测 206005 → 202873 字节。直接照抄 `read_text` 那版 = 把整份文件的
   行尾换掉（就是「留档是 LF 假货」那个坑，这里会真的写进后端）。

用法:
  python3 -I push_blavity.py --dry-run   # 只读、比对，不写
  python3 -I push_blavity.py             # 真推
"""
import ast
import os
import pathlib
import sys

sys.path.insert(0, "/company/siteforge")

#: 从 siteforge.env 取 token（不打印任何值）
_ENV = "/company/siteforge/siteforge.env"
for _line in open(_ENV, encoding="utf-8"):
    _line = _line.strip()
    if not _line or _line.startswith("#") or "=" not in _line:
        continue
    _k, _v = _line.split("=", 1)
    os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

from agent import fmr, pywrite  # noqa: E402

KEY = "blavity.com"
OPERATOR = "claude-agent（222 真站验过 / 用户指令 2026-10-10）"
SRC = pathlib.Path("/tmp/bl_probe/blavity_patched.py")
BACKUP_ROOT = pathlib.Path("/tmp/bl_probe/json-backups")
FLAGS = ("--ws-url", "--form-file", "--correlation-id", "--log-level")


def main() -> int:
    dry = "--dry-run" in sys.argv
    with open(SRC, encoding="utf-8", newline="") as fh:   # ← 保 CRLF（关键那一行）
        source = fh.read()
    ast.parse(source)
    missing = [f for f in FLAGS if f not in source]
    if missing:
        print("产物少了生产要的开关：", missing, "—— 不推。")
        return 2
    src_sha = pywrite.sha256_text(source)
    print("[0] 本地那份: %d 字节 / CRLF %d / sha %s"
          % (len(source.encode()), source.count("\r\n"), src_sha[:16]))

    c = fmr.FmrClient()
    try:
        current = str(c.form_script(KEY, allow_disabled=True).get("source") or "")
    except fmr.FmrRefused as exc:
        if int(getattr(exc, "status", 0) or 0) == 404:
            current = ""
        else:
            raise
    cur_sha = pywrite.sha256_text(current)
    print("[1] 后端现状: %d 字节 / CRLF %d / sha %s"
          % (len(current.encode()), current.count("\r\n"), cur_sha[:16]))
    if cur_sha == src_sha:
        print("后端已经是这份了 —— 不用推。")
        return 0
    if dry:
        print("[dry-run] 到此为止：没有写任何东西。")
        return 0

    store = pywrite.TextBackupStore(str(BACKUP_ROOT))
    backup_id = store.save(key=KEY, original=current, replacement_sha256=src_sha)
    print("[2] 写前备份:", backup_id)
    got = c.update_form_script(KEY, source, operator=OPERATOR)
    print("[3] 写:", got.verdict, "|", str(got.say)[:140])
    if not got.ok:
        return 3
    after = str(c.form_script(KEY, allow_disabled=True).get("source") or "")
    same = pywrite.sha256_text(after) == src_sha
    print("[4] 回读: %d 字节 / CRLF %d / 一致: %s"
          % (len(after.encode()), after.count("\r\n"), same))
    if not same:
        c.update_form_script(KEY, current, operator=OPERATOR + "（自动回滚）")
        print("回读对不上 —— 已传回原来那份；备份", backup_id)
        return 4
    print("推好了。备份:", backup_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
