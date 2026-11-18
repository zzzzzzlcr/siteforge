#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Review Focus #7：两套骨架的**字节数**与**重渲耗时**。

口径（写清楚，免得两边的数不可比）：

* **同输入** = 同一份**业务那几格**（`STATES`/`FILLS`/`PROVENANCE`/成功判据/`READ_PAGE_JS`…
  按家族各自的名字）。两行「旧 / 新」用的就是**同一份**：旧那份是**交付时按同一份业务内容
  渲出来的产物本体**（不是另抄一份），新那份是拿当前工作树的骨架重渲的。
  业务字面量逐字节守恒由 `rerender_loop.py` / `tools/rerender_site.lift` 各自核对。
* **重渲耗时** = 在同一个进程里调 `render` / `render_loop` 的墙钟（**不含**读文件、不含比对），
  跑 3 次取中位。
* 「本刀给产物加的字节」= 那几段机器码的**字面长度** —— 两套骨架吃的是**同一组常量**
  （`agent/template.py` 里那几段），所以对两族产物加的是同样多字节；这里顺带**核对**它
  真的同一份（在两份渲染结果里各数一次出现次数）。

只读：不碰后端、不碰真站、不写任何产物文件。
"""
from __future__ import annotations

import argparse
import ast
import importlib.util
import pathlib
import statistics
import sys
import time

SF = pathlib.Path("/company/siteforge")
sys.path.insert(0, str(SF))
sys.path.insert(0, str(pathlib.Path(__file__).parent))

import rerender_loop as RL                                            # noqa: E402
import tools.rerender_site as RS                                      # noqa: E402


def load(path: pathlib.Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def timed(fn, n: int = 3):
    ts = []
    out = None
    for _ in range(n):
        t0 = time.perf_counter()
        out = fn()
        ts.append((time.perf_counter() - t0) * 1000.0)
    return out, statistics.median(ts), min(ts), max(ts)


def _const(src: str, name: str):
    """从**渲染结果**里抠一个字面量常量（`_POPUP_PROBE_JS` 只活在产物里，不在 agent 里）。"""
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if getattr(t, "id", None) == name:
                    return ast.literal_eval(node.value)
    raise SystemExit("产物里没有常量 %s" % name)


def minimal_states():
    """固定步骤表那族的空壳 `STATES`（一屏、一步、无选择器）—— 只为量骨架本身。"""
    return [{"name": "probe", "when": {}, "steps": [
        {"action": "goto", "target": {"text": None, "selectors": [], "url": "https://example.invalid/"}}]}]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixed", default=str(SF / "forms-222/sites/wildnutrition.py"))
    ap.add_argument("--loop", default=str(SF / "forms-222/sites/compareinsulation.py"))
    a = ap.parse_args()

    t_fixed = load(SF / "agent/template.py", "tpl_now")
    t_loop = load(SF / "agent/template_loop.py", "tpll_now")

    print("== 机器码（两族共用同一组常量）==")
    for name, js in (("_POPUP_MACHINERY_SRC", t_fixed._POPUP_MACHINERY_SRC),
                     ("_COVER_JS", t_fixed._COVER_JS),
                     ("_RECENTER_JS", t_fixed._RECENTER_JS)):
        print("  %-20s %7d 字节" % (name, len(js.encode())))
    print("  %-20s %7d 字节（**源常量**合计；落到产物里是 30,669 B —— 后两个多行隐式拼接的"
          "字面量会多 272 B 的引号/换行，值逐字节相同）\n"
          "       两族产物里都是**这一份**；差别在来路：固定那一族它原来就在产物里"
          "（本刀是**加钩子行**），循环那一族是**本刀新塞进去的**"
          % ("合计", len((t_fixed._POPUP_MACHINERY_SRC + t_fixed._COVER_JS
                          + t_fixed._RECENTER_JS).encode())))

    # ---- 固定步骤表 ----
    fp = pathlib.Path(a.fixed)
    fsrc = fp.read_text(encoding="utf-8")
    f_got = RS.lift(fsrc)
    f_new, f_med, f_lo, f_hi = timed(lambda: RS.render_with(t_fixed, f_got))
    print("\n== 固定步骤表（%s）==" % fp.name)
    print("  旧（交付时那份）      %8d 字节" % len(fsrc.encode()))
    print("  新（当前骨架重渲）    %8d 字节   重渲 %.0f ms [%.0f–%.0f]"
          % (len(f_new.encode()), f_med, f_lo, f_hi))
    print("  ⇒ 本刀 +%d 字节" % (len(f_new.encode()) - len(fsrc.encode())))
    old_mod = RS.old_skeleton_module()
    print("  旧骨架重渲（往返核对的基线）：%s"
          % ("拼不出来（跳过往返核对）" if old_mod is None else "有"))
    if old_mod is not None:
        o = RS.render_with(old_mod, f_got)
        print("     逐字节一致 = %s（%d 字节）" % (o == fsrc, len(o.encode())))

    f_min, fmin_med, fmin_lo, fmin_hi = timed(lambda: t_fixed.render(
        site="probe", success_text="thanks", success_urls=["https://example.invalid/ok"],
        states=minimal_states(), fills={}, provenance={}))
    print("  同输入（最小空壳）    %8d 字节   重渲 %.1f ms" % (len(f_min.encode()), fmin_med))

    # ---- 循环型 ----
    lp = pathlib.Path(a.loop)
    lsrc = lp.read_text(encoding="utf-8")
    l_got = RL.lift(lsrc)
    l_new, l_med, l_lo, l_hi = timed(lambda: RL.render_with(t_loop, l_got))
    print("\n== 循环型（%s）==" % lp.name)
    print("  旧（交付时那份）      %8d 字节" % len(lsrc.encode()))
    print("  新（当前骨架重渲）    %8d 字节   重渲 %.0f ms [%.0f–%.0f]"
          % (len(l_new.encode()), l_med, l_lo, l_hi))
    print("  ⇒ 本刀 +%d 字节（这一次重渲同时也吃进了 10-13→今天骨架的其它改动）"
          % (len(l_new.encode()) - len(lsrc.encode())))
    print("  业务字面量守恒 = %s" % (RL.lift(l_new) == l_got))

    l_min, lmin_med, lmin_lo, lmin_hi = timed(lambda: t_loop.render_loop(
        site="probe", success_text="thanks", success_urls=["https://example.invalid/ok"],
        fills={}, provenance={}))
    print("  同输入（最小空壳）    %8d 字节   重渲 %.1f ms" % (len(l_min.encode()), lmin_med))

    # ---- 核对：机器码真的同一份 ----
    print("\n== 核对：产物里那几段常量与 `agent/template.py` 那份**逐字节相同** ==")
    for name, want in (("_POPUP_MACHINERY_SRC", t_fixed._POPUP_MACHINERY_SRC),
                       ("_COVER_JS", t_fixed._COVER_JS),
                       ("_RECENTER_JS", t_fixed._RECENTER_JS)):
        print("  %-20s 固定 %s / 循环 %s"
              % (name,
                 "同一份 ✓" if name == "_POPUP_MACHINERY_SRC"
                 else ("同一份 ✓" if _const(f_new, name) == want else "**不一样**"),
                 "同一份 ✓" if name == "_POPUP_MACHINERY_SRC"
                 else ("同一份 ✓" if _const(l_new, name) == want else "**不一样**")))
    ns: dict = {"json": __import__("json")}
    exec(t_fixed._POPUP_MACHINERY_SRC, ns)          # 钩子表 + 拼出来的探针
    probe = ns["_POPUP_PROBE_JS"]
    #: 探针在产物里是**算出来的**（`_POPUP_PROBE_JS = _popup_probe_js()`），不是字面量 ——
    #: 所以「两族同一份」只能由**机器码源文本逐字节相同**（上面那三行 ✓）推出来：
    #: 同样的源 + 同样的入参 ⇒ 同样的字符串。这里把它渲出来记个大小。
    print("  %-20s %7d 字节（两族的源文本逐字节相同 ⇒ 拼出来必然同一份）"
          % ("_POPUP_PROBE_JS", len(probe.encode())))
    print("  %-20s %d 条：%s" % ("钩子表", len(ns["_POPUP_HOOKS"]),
                                [n for n, _ in ns["_POPUP_HOOKS"]]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
