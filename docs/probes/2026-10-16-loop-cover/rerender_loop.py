#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把一份**已经交付的循环型产物**用**当前骨架**重新渲一遍。

为什么不是 `tools/rerender_site.py`：那个工具只认**固定步骤表**那一族
（`render_with()` 里喂的是 `SITE/SUCCESS_TEXTS/SUCCESS_URLS/STATES/FILLS/PROVENANCE`，
`States` 那一格循环型压根没有）。循环型的四格是
`READ_PAGE_JS / FILLS / PROVENANCE / 成功判据` 外加 `pick / max_steps / idle_limit /
busy_timeout` 四个旋钮 —— 参数形状不同，所以单开这一个。

两道核对（都是**只读**，不碰后端、不碰真站）：

1. **业务那几格逐字节守恒**：旧产物与本工具抠出来的字面量，与**新渲染里同名的那几个字面量**
   逐个 `==` 比（不是比源码文本，是 `ast.literal_eval` 出来的值）。
2. **只加不改**：新旧两份逐行 diff，**不许有 `-`（删除）行、不许有 replace** ——
   有就说明这一次重渲顺手动了别的，停，别往下走。

用法:
    python3 -I rerender_loop.py <旧产物.py> [--out 新产物.py] [--force]

⚠️ `--force`：**跨版本重渲**（骨架删/改了行）时用 —— 它只跳过 ② 的「只加不改」闸；
① 的业务字面量守恒与逐行差异**照旧打印**。没有它时，有删/改行就停在写文件之前（退出码 2）。
"""
from __future__ import annotations

import argparse
import ast
import difflib
import importlib.util
import pathlib
import sys

SF = pathlib.Path("/company/siteforge")
sys.path.insert(0, str(SF))

#: 循环型产物里那几格**业务字面量**（名字与 `template_loop.render_loop` 的入参一一对应）
WANTED = ("SITE", "SUCCESS_URL_MARKERS", "SUCCESS_TEXTS", "FILLS", "PICK",
          "MAX_STEPS", "IDLE_LIMIT", "BUSY_TIMEOUT", "READ_PAGE_JS", "PROVENANCE")


def lift(src: str) -> dict:
    got: dict = {}
    for node in ast.parse(src).body:
        if not isinstance(node, ast.Assign):
            continue
        for t in node.targets:
            name = getattr(t, "id", None)
            if name in WANTED and name not in got:
                got[name] = ast.literal_eval(node.value)
    missing = [n for n in WANTED if n not in got]
    if missing:
        raise SystemExit("产物里少了这几个字面量，抠不出来：%s" % missing)
    return got


def _load(path: pathlib.Path):
    spec = importlib.util.spec_from_file_location("template_loop_now", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def render_with(mod, got: dict) -> str:
    return mod.render_loop(
        site=got["SITE"],
        success_text=got["SUCCESS_TEXTS"][0] if got["SUCCESS_TEXTS"] else "",
        success_urls=got["SUCCESS_URL_MARKERS"],
        fills=got["FILLS"],
        provenance=got["PROVENANCE"],
        read_js=got["READ_PAGE_JS"],
        pick=got["PICK"],
        max_steps=got["MAX_STEPS"],
        idle_limit=got["IDLE_LIMIT"],
        busy_timeout=got["BUSY_TIMEOUT"],
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("artifact")
    ap.add_argument("--out", default="")
    ap.add_argument("--force", action="store_true",
                    help="跨版本重渲：有删/改行也继续写（只跳过「只加不改」闸）")
    a = ap.parse_args()

    p = pathlib.Path(a.artifact)
    old_src = p.read_text(encoding="utf-8")
    got = lift(old_src)
    print("抠出来：")
    for k in WANTED:
        v = got[k]
        size = len(v) if hasattr(v, "__len__") else "-"
        print("  %-18s len=%-6s %s" % (k, size, repr(v)[:90]))

    mod = _load(SF / "agent" / "template_loop.py")
    new_src = render_with(mod, got)

    # ① 业务那几格：新旧两份**值**逐个比
    got2 = lift(new_src)
    diff_keys = [k for k in WANTED if got2[k] != got[k]]
    print("\n① 业务字面量守恒：%s" % ("全部逐字节相同 ✓" if not diff_keys
                                     else "**有差**：%s" % diff_keys))

    # ② 只加不改
    o = old_src.splitlines()
    n = new_src.splitlines()
    sm = difflib.SequenceMatcher(None, o, n)
    ops = [op for op in sm.get_opcodes() if op[0] != "equal"]
    removed = sum(op[2] - op[1] for op in ops if op[0] in ("delete", "replace"))
    added = sum(op[4] - op[3] for op in ops if op[0] in ("insert", "replace"))
    kinds = sorted({op[0] for op in ops})
    print("② 逐行：+%d / -%d 行（op=%s）" % (added, removed, kinds))
    if removed or kinds and any(k in ("delete", "replace") for k in kinds):
        print("⇒ **有行被删/被改** —— 这一次重渲不只换了骨架。")
        if not a.force:
            print("   停（没写文件）。跨版本重渲请加 `--force`。")
            return 2
        print("   按 `--force` 继续写（①的业务字面量守恒、②的差异照旧以打印为准）。")

    d = list(difflib.unified_diff(o, n, "旧产物", "新骨架重渲", lineterm="", n=0))
    print("\n差异（只列 hunk 头 + 前 24 行）：")
    print("\n".join(d[:24]))

    dst = pathlib.Path(a.out) if a.out else p.with_suffix(".rerender.py")
    dst.write_text(new_src, encoding="utf-8")
    print("\n写到: %s（%d 字节，旧的那份 %d 字节）"
          % (dst, len(new_src.encode()), len(old_src.encode())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
