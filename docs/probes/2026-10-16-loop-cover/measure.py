#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""I2 的量证：给定一棵**骨架树**（`<state>/agent/template.py` + `template_loop.py`），
量三件事（与 `docs/probes/2026-10-16-loop-cover/bytes_and_rerender.py` 同一套口径）：

  1. `_POPUP_MACHINERY_SRC` / `_COVER_JS` / `_RECENTER_JS` 的字面字节数；
  2. 固定步骤表：`forms-222/sites/wildnutrition.py`（交付时那份）→ 拿这棵树重渲 ⇒ Δ；
  3. 循环型：`forms-222/sites/compareinsulation.py`（交付时那份）→ 重渲 ⇒ Δ。

用法：`python3 -I /tmp/ff/measure.py <state-dir> <label>`
⚠️ 每棵树**单独一个进程**跑（`agent.template` 会进 `sys.modules`，同进程里换树换不掉）。
"""
from __future__ import annotations

import importlib
import pathlib
import sys

SF = pathlib.Path("/company/siteforge")
PROBES = SF / "docs/probes/2026-10-16-loop-cover"


def main() -> int:
    state = pathlib.Path(sys.argv[1])
    label = sys.argv[2]
    sys.path.insert(0, str(SF))
    sys.path.insert(0, str(PROBES))
    sys.path.insert(0, str(state))            # 这棵树的 agent/ 优先（最后插 = 最先查）
    #: ⚠️ 先加载这棵树的骨架：下面那两个工具模块 import 时会把 `/company/siteforge` 插到
    #: `sys.path[0]`（`tools/rerender_site.py` 自己那行）—— 先加载完骨架就不受它影响。
    t = importlib.import_module("agent.template")
    tl = importlib.import_module("agent.template_loop")
    sys.path.insert(0, str(state))
    import rerender_loop as RL
    import tools.rerender_site as RS
    assert pathlib.Path(t.__file__).resolve() == (state / "agent/template.py").resolve(), \
        "加载的不是这棵树：%s" % t.__file__

    print("== %s ==" % label)
    for name in ("_POPUP_MACHINERY_SRC", "_COVER_JS", "_RECENTER_JS"):
        js = getattr(t, name, None)
        print("  %-20s %s" % (name, ("%7d 字节" % len(js.encode())) if js else "（这棵树里没有）"))
    if getattr(t, "_POPUP_MACHINERY_SRC", None):
        print("  %-20s %7d 字节（源常量合计）"
              % ("合计", len((t._POPUP_MACHINERY_SRC + t._COVER_JS
                              + t._RECENTER_JS).encode())))

    fsrc = (SF / "forms-222/sites/wildnutrition.py").read_text(encoding="utf-8")
    f_new = RS.render_with(t, RS.lift(fsrc))
    print("  表：旧 %8d → 新 %8d ⇒ Δ %+6d 字节（+%d 行 / −%d 行）"
          % (len(fsrc.encode()), len(f_new.encode()),
             len(f_new.encode()) - len(fsrc.encode()),
             len(f_new.splitlines()) - len(fsrc.splitlines()),
             len(fsrc.splitlines()) - len([l for l in fsrc.splitlines() if l in set(f_new.splitlines())])))

    lsrc = (SF / "forms-222/sites/compareinsulation.py").read_text(encoding="utf-8")
    l_new = RL.render_with(tl, RL.lift(lsrc))
    print("  循环：旧 %8d → 新 %8d ⇒ Δ %+6d 字节" % (
        len(lsrc.encode()), len(l_new.encode()),
        len(l_new.encode()) - len(lsrc.encode())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
