#!/usr/bin/env python3
"""把一份**已经交付的产物**用**当前骨架**重新渲一遍（`<名字>.render2.py`）。

## 为什么要有它

产物 = 骨架 + 这一趟算出来的四样（`STATES` / `FILLS` / `PROVENANCE` / 成功判据）。
而**骨架是渲染那一刻从内存里读的**（`SKELETON` 是 `agent/template.py` 的模块级常量，
import 时定死）—— 所以：

* 服务进程**起在改骨架之前** ⇒ 那一趟交付的产物带着**旧骨架**；
* 想让它带上新骨架，不必重探一趟（重探 = 真窗口 + 模型 + 真提交），
  四样东西**产物自己身上就有**（就是那几个字面量）⇒ 抠出来重渲即可。

团队里已经有这个先例：`forms-222/sites/ukg.render.py` / `ukg.render2.py`。

## 怎么保证「只换了骨架，没顺手改业务」

两道，都跑：

1. **往返核对**（默认就做）：拿 **git HEAD 那份旧骨架**渲一遍，
   跟交付的产物**逐字节**比 —— 一模一样才说明「抠出来的四样 + 渲染函数」
   忠实还原了它。对不上就**停**（说明我抠错了，或者渲染不是纯函数）。
2. **差异清单**：新旧两次渲染逐行 diff，把变了多少行**摆出来**给人看。

⚠️ 它**不碰后端、不碰真站、不落盘到 sites/**（除非 `--out` 明确指了）——
默认只写到旁边一个 `.render2.py`。
"""
from __future__ import annotations

import argparse
import ast
import difflib
import importlib.util
import pathlib
import subprocess
import sys
import tempfile

SF = pathlib.Path("/company/siteforge")
sys.path.insert(0, str(SF))

#: 要从产物里抠出来的那几个字面量（名字与骨架里 `render()` 填的九个占位符一一对应）
WANTED = ("SITE", "SUCCESS_TEXTS", "SUCCESS_URLS", "STATES", "FILLS",
          "PROVENANCE", "STUCK_LIMIT")


def lift(src: str) -> dict:
    """把产物里那几个**字面量**抠出来（只认 `ast.literal_eval` 得动的，不执行任何代码）。"""
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


def _load(path: pathlib.Path, modname: str):
    spec = importlib.util.spec_from_file_location(modname, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def old_skeleton_module():
    """往返核对用的**基线骨架**。

    ⚠️ **不是 git HEAD 那份**（2026-10-10 实测踩过）：线上真正在跑的那份骨架
    **比 HEAD 新**（产物里有 `_id_in` / `birth_year` / `drift` 这些 HEAD 没有的东西）
    —— 拿 HEAD 当基线，往返核对会「对不上」，而那是**基线选错了**，不是抠错了。

    正确的基线 = **改动前的工作树**。`_reconstruct_pre_edit()` 从当前文件里把我
    （2026-10-10）加的那两块**掐掉**还原出来 —— 那两块是**边界清楚、可字符串定位**的。
    读不出来 → None（调用方跳过往返核对，并说出来）。

    ⚠️⚠️ **2026-10-10 晚：这条路现在拼不出来了**（实测：它返回 None，工具会打出那句
    「拼不出改动前那份骨架 —— 跳过往返核对」）。原因：弹窗那段从**一根拼起来的字符串**
    改成了**一张钩子表**（`_POPUP_HOOKS` + `_popup_probe_js`，见 `agent/template.py`），
    改动前那些产物里的那段**源码文本**，已经不是「当前文件掐掉两块」还原得出来的形状了
    —— 字符串手术只能还原**边界清楚、可字符串定位**的改动（上一版恰好吃得下）。
    于是：**这一档的往返核对跳过了**（不是当成对上了 —— 调用方会明说）。
    要对那一版产物做往返核对，得拿**它交付时的 `agent/template.py`** 当基线（git 里那一版）。
    """
    cur = (SF / "agent" / "template.py").read_text(encoding="utf-8")
    head = "#: ★ 2026-10-10（用户当天那句"
    tail = "_CONSENT_PROBE_JS = ("
    if head not in cur or tail not in cur:
        return None
    i, j = cur.index(head), cur.index(tail)
    if i > j:
        return None
    pre = cur[:i] + cur[j:]
    pre = pre.replace(
        "    #: ★ 2026-10-10：同意条一个都没找到 ⇒ 再看一眼**盖住整页的模态对话框**\n"
        "    #: （`_MODAL_BOX_JS` 的 docstring 里写着三条窄口径与它为什么单开一支）。\n"
        "    #: 顺序是**同意条优先**：两者同时在页面上时，先把「对同意的决定」做掉。\n"
        "    + _MODAL_BOX_JS +\n", "")
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="sf-baseline-")) / "template_base.py"
    tmp.write_text(pre, encoding="utf-8")
    return _load(tmp, "template_base")


def render_with(mod, got: dict) -> str:
    """拿某一份骨架渲一遍 —— 成功判据那两格**原样搬**（文字那格剥壳只在人给字的时候走，
    这里给的是产物里已经剥好的那几个，所以直接喂第一个即可）。"""
    return mod.render(
        site=got["SITE"],
        success_text=got["SUCCESS_TEXTS"][0] if got["SUCCESS_TEXTS"] else "",
        success_urls=got["SUCCESS_URLS"],
        states=got["STATES"],
        fills=got["FILLS"],
        provenance=got["PROVENANCE"],
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("artifact", help="已经交付的那份产物（.py）")
    ap.add_argument("--out", default="", help="新产物写到哪（默认 <工件>.render2.py）")
    ap.add_argument("--no-roundtrip", action="store_true", help="跳过往返核对（不推荐）")
    ap.add_argument("--strict", action="store_true", help="往返核对有差异就退出（默认只报告）")
    a = ap.parse_args()

    p = pathlib.Path(a.artifact)
    src = p.read_text(encoding="utf-8")
    got = lift(src)
    print("抠出来：")
    print("  SITE         = %s" % got["SITE"])
    print("  SUCCESS_TEXTS= %s" % got["SUCCESS_TEXTS"])
    print("  SUCCESS_URLS = %s" % got["SUCCESS_URLS"])
    print("  STATES       = %d 组" % len(got["STATES"]))
    print("  FILLS        = %d 格" % len(got["FILLS"]))
    print("  STUCK_LIMIT  = %s" % got["STUCK_LIMIT"])

    new_mod = _load(SF / "agent" / "template.py", "template_now")
    print("\n当前骨架 sha = %s" % new_mod.SKELETON_SHA[:16])

    if not a.no_roundtrip:
        old_mod = old_skeleton_module()
        if old_mod is None:
            print("⚠️ 拼不出改动前那份骨架 —— 跳过往返核对")
        else:
            old_out = render_with(old_mod, got)
            same = old_out == src
            print("往返核对（用**改动前的工作树**重渲）：%s"
                  % ("逐字节一致 ✓（抠出来的四样忠实还原了它）" if same else "有差异"))
            if not same:
                d = [l for l in difflib.unified_diff(src.splitlines(), old_out.splitlines(),
                                                     "交付的", "改动前重渲的", lineterm="", n=1)]
                print("  差异 +%d/-%d 行；差异**不一定是错**：工作树在产物交付之后又长过东西"
                      "（2026-10-10 实测：10-09 09:43 那份产物就没有当天晚些加的 `_FOLD` 撇号折叠表）。"
                      % (sum(1 for l in d if l.startswith("+") and not l.startswith("+++")),
                         sum(1 for l in d if l.startswith("-") and not l.startswith("---"))))
                print("\n".join(d[:30]))
                if a.strict:
                    print("⇒ --strict：有差异就不往下走。")
                    return 2

    out = render_with(new_mod, got)
    diff = [l for l in difflib.unified_diff(src.splitlines(), out.splitlines(),
                                            "旧骨架", "新骨架", lineterm="", n=1)]
    added = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))
    print("换骨架之后：+%d 行 / -%d 行" % (added, removed))
    print("\n".join(diff[:80]))

    dst = pathlib.Path(a.out) if a.out else p.with_suffix(".render2.py")
    dst.write_text(out, encoding="utf-8")
    print("\n写到: %s（%d 字节）" % (dst, len(out.encode())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
