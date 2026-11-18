#!/usr/bin/env python3
"""把 healthinsurancewatch 生产脚本的 phone 填值改成「不用资料、04 + 随机」。

口径（与 `docs/probes/2026-10-20-rebates-phone/` 同一条）：
  - `source` 换成一个**资料里永远没有的键** `__our_au_mobile__`
    ⇒ `_fill_value()` 取不到任务资料 ⇒ 落到 `_shaped_like(sample)`
  - `sample` 保持 `0412345678`（真站收过的那个值）
    ⇒ `_shaped_like` 规则：通体数字 ⇒ 头 2 位照抄（`04`）+ 其余随机
  - 于是每趟都是 `04` + 8 位随机，不再吃资料里的 `phone`

逐字节：只替换那一行，CRLF/LF 原样；锚点必须唯一（不是 1 处就退出）。
用法：
  python3 -I patch_phone.py            # 只读：打印将要改的那一行
  python3 -I patch_phone.py --apply    # 写出手工修好的那份（不碰 FMR）
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
SRC = HERE / "healthinsurancewatch_ORIGINAL_a6ca371c24c8.py"
OUT = HERE / "healthinsurancewatch_PATCHED.py"

OLD = "             'source': 'phone',\n"
NEW = "             'source': '__our_au_mobile__',\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    raw = SRC.read_bytes()
    text = raw.decode("utf-8")
    n = text.count(OLD)
    print("ANCHOR_COUNT", n)
    if n != 1:
        print("REFUSE: 锚点不是恰好一处 —— 不动")
        return 2

    patched = text.replace(OLD, NEW, 1)
    ast.parse(patched)  # 语法闸

    # 值确实变了：FILLS['phone']['source']
    def phone_fill(src: str):
        tree = ast.parse(src)
        for node in tree.body:
            if (isinstance(node, ast.Assign)
                    and getattr(node.targets[0], "id", None) == "FILLS"):
                return ast.literal_eval(node.value)["phone"]
        raise SystemExit("FILLS not found")

    before = phone_fill(text)
    after = phone_fill(patched)
    print("BEFORE_SOURCE", before.get("source"), "SAMPLE", before.get("sample"))
    print("AFTER_SOURCE ", after.get("source"), "SAMPLE", after.get("sample"))
    if after.get("source") != "__our_au_mobile__" or after.get("sample") != "0412345678":
        print("REFUSE: 改完的值不对")
        return 2

    # 逐行 diff：只有那一行不同
    a = text.splitlines(keepends=True)
    b = patched.splitlines(keepends=True)
    diffs = [(i + 1, x, y) for i, (x, y) in enumerate(zip(a, b)) if x != y]
    print("LINES", len(a), len(b), "DIFF_LINES", len(diffs))
    for ln, x, y in diffs:
        print("  %d- %s" % (ln, x.rstrip()))
        print("  %d+ %s" % (ln, y.rstrip()))

    body = patched.encode("utf-8")
    print("PATCHED_BYTES", len(body), "SHA256", hashlib.sha256(body).hexdigest())

    if not args.apply:
        print("DRY_RUN（加 --apply 才写盘）")
        return 0
    OUT.write_bytes(body)
    print("WROTE", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
