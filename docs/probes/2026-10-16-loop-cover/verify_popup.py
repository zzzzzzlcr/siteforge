#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""拿**产物本体**在真站上跑「弹层那一刀」，并量它的代价。

被验的是**产物里那份代码本身**（`Filler` 是真的、`CDPHelper` 是真的、`cdp` 进程是真的），
不是复刻出来的等价物 —— 与 `docs/probes/2026-10-10-blavity-ad-popup/verify_ad_popup.py`
同一条路子，只是这里不绕「假 self」：直接 `Filler(...)` 造一个真的（构造只读一个 form json）。

三种 mode：

  fixed   固定步骤表那族：`Filler._clear_obstructions()`（开跑前/导航后/每步动手前调的就是它）
  loop    循环型那族：     `Filler.clear_popups()`（Task 4 搬进循环型骨架的那条路）
  cost    量代价（不进 click）：read / 探针 / 两者合一趟 —— 端到端 + 页内两把尺子

⚠️ 只**读**页面 + 至多**点**一下探针点名的那个元素；不填表、不提交、不导航到站内别的页
（`--entry-url` 那一次 `navi` 除外，那是进站必需的）。

用法:
  python3 -I verify_popup.py <产物.py> <ws_url> fixed --entry-url https://… --wait 300
  python3 -I verify_popup.py <产物.py> <ws_url> cost  --entry-url https://…
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import os
import pathlib
import sys
import time

FORMS_DIR = "/company/siteforge/forms-222"
CDP_BIN = "/opt/skills/auto-farm-skill/cdp"
sys.path.insert(0, FORMS_DIR)
os.environ.setdefault("SITEFORGE_CDP_BIN", CDP_BIN)

LOG = logging.getLogger("verify")


def load(artifact: str):
    spec = importlib.util.spec_from_file_location("proof_artifact", artifact)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def make_filler(mod, ws_url: str, form: pathlib.Path, trace: pathlib.Path):
    try:
        return mod.Filler(ws_url, str(form), "verify-task8", trace_path=str(trace))
    except TypeError:                       # 固定步骤表那族的关键字叫 trace
        return mod.Filler(ws_url, str(form), "verify-task8", trace=str(trace))


def _clean(raw: str) -> str:
    """与产物里 `_clean_eval` 同一件事（`cdp eval` 的 stdout 是 JSON，字符串带引号）。"""
    text = (raw or "").strip()
    if not text or text.startswith(("Error:", "ERROR:", "Usage:", "Flags:")):
        return ""
    if text.startswith('"'):
        try:
            return json.loads(text)
        except ValueError:
            return text.strip('"')
    return text


def timed_eval(filler, js: str):
    """一次 `cdp.eval`：端到端（进程 + websocket + eval）与页内（performance.now 自夹）。

    ⚠️ 页内那格**必须是页面自己量的** —— 端到端里 99% 是起进程，拿它当「探针有多贵」会
    得出完全不同的结论（2026-10-16 那趟真站量到的两个数差 1000 倍）。
    """
    #: ⚠️ 写成**函数体**（`var …; return …`），交给 `_ev` 去包那一层 —— 自己再包一层
    #: IIFE 的话，返回值被外面那层吞掉，cdp eval 交出来是**空串**（第一版就是这么哑的）。
    wrapped = ("var __t0=performance.now();"
               "var __r=(function(){%s})();var __t1=performance.now();"
               "return JSON.stringify({ms:__t1-__t0,r:__r});" % js)
    t0 = time.perf_counter()
    if hasattr(filler, "_ev"):
        raw = filler._ev(wrapped)                    # 固定步骤表那族
    else:
        #: 循环型那族**没有** `_ev` —— 它自己的 `_popup_answer()` 走的就是 `cdp.eval`，
        #: 所以这里照它那条路走（包一层 + 去掉 cdp stdout 那层 JSON 引号）。
        raw = _clean(filler.cdp.eval("(function(){%s})()" % wrapped, ""))
    dt = (time.perf_counter() - t0) * 1000.0
    try:
        got = json.loads(raw)
        return dt, float(got.get("ms") or 0.0), got.get("r")
    except Exception:
        return dt, None, raw


def do_fixed(filler, mod, a) -> int:
    """固定步骤表：等（有界）→ 一旦探针答应就调产物本体那一下。"""
    deadline = time.time() + a.wait
    n_probe = 0
    answers = []
    while True:
        dt, ms, raw = timed_eval(filler, mod._POPUP_PROBE_JS)
        n_probe += 1
        answers.append((round(dt, 3), None if ms is None else round(ms, 3), str(raw)[:120]))
        print("[probe#%d] e2e=%.3fs page=%s ans=%r" % (n_probe, dt / 1000.0, ms, str(raw)[:120]))
        if str(raw or "").strip():
            break
        if time.time() >= deadline:
            print("[等] %d 秒里探针一直返空（%d 次）—— 这一屏没有那四类东西" % (a.wait, n_probe))
            return 0
        time.sleep(a.interval)
    print("[探到] 交给产物本体 `_clear_obstructions()`")
    filler._clear_obstructions(why=a.why)
    return 0


def do_loop(filler, mod, a) -> int:
    """循环型：产物本体那一条（`clear_popups`）。"""
    deadline = time.time() + a.wait
    while True:
        dt, ms, raw = timed_eval(filler, mod._POPUP_PROBE_JS)
        print("[probe] e2e=%.3fs page=%s ans=%r" % (dt / 1000.0, ms, str(raw)[:120]))
        if str(raw or "").strip():
            break
        if time.time() >= deadline:
            print("[等] %d 秒里探针一直返空 —— 这一屏没有那四类东西" % a.wait)
            return 0
        time.sleep(a.interval)
    print("[探到] 交给产物本体 `clear_popups()`")
    filler.clear_popups(why=a.why)
    print("[复核] 再问一遍同一张表：%r" % (filler._popup_answer() or "<空>"))
    return 0


def do_cost(filler, mod, a) -> int:
    """口径 §4-①：探针一次 eval 的两把尺子；§4-②：探针能不能与每次 read() 搭同一趟车。"""
    read_js = getattr(mod, "READ_PAGE_JS", "")
    n = a.n
    print("== ① 一把 eval 的两把尺子（每组 %d 次）==" % n)
    for label, js in (("read()", read_js), ("探针", mod._POPUP_PROBE_JS)):
        if not js:
            print("  %-8s （这一份产物没有这个常量）" % label)
            continue
        rows = [timed_eval(filler, js) for _ in range(n)]
        e2e = sorted(r[0] for r in rows)
        pg = sorted(r[1] for r in rows if r[1] is not None)
        print("  %-8s 端到端 中位 %.3fs [%.3f–%.3f]   页内 中位 %.2fms [%.2f–%.2f]"
              % (label, e2e[len(e2e) // 2] / 1000.0, e2e[0] / 1000.0, e2e[-1] / 1000.0,
                 pg[len(pg) // 2] if pg else -1, pg[0] if pg else -1, pg[-1] if pg else -1))

    if not read_js:
        return 0
    print("\n== ② 搭车：read 与探针**合进一次 eval**（同一份页面状态）==")
    #: ⚠️ `READ_PAGE_JS` **自己**就是一个 `(function(){…})()` 表达式 —— 写成
    #: `var __a=(function(){<它>})()` 的话，那层空壳的返回值是 undefined（第一版就这么量的，
    #: 页内耗时是对的、`read` 的长度恒为 9 = `"undefined"`）。要 `return` 一层才拿得到值。
    combined = ("var __a=(function(){return (%s);})();"
                "var __b=(function(){%s})();"
                "return JSON.stringify({read:String(__a).length,popup:__b});"
                % (read_js, mod._POPUP_PROBE_JS))
    rows = [timed_eval(filler, combined) for _ in range(n)]
    e2e = sorted(r[0] for r in rows)
    pg = sorted(r[1] for r in rows if r[1] is not None)
    print("  合一趟   端到端 中位 %.3fs [%.3f–%.3f]   页内 中位 %.2fms [%.2f–%.2f]"
          % (e2e[len(e2e) // 2] / 1000.0, e2e[0] / 1000.0, e2e[-1] / 1000.0,
             pg[len(pg) // 2] if pg else -1, pg[0] if pg else -1, pg[-1] if pg else -1))
    print("  样本：%r" % (rows[0][2],))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("artifact")
    ap.add_argument("ws_url")
    ap.add_argument("mode", choices=("fixed", "loop", "cost"))
    ap.add_argument("--entry-url", default="")
    ap.add_argument("--wait", type=float, default=300.0, help="等弹层的总秒数上界")
    ap.add_argument("--interval", type=float, default=15.0)
    ap.add_argument("--why", default="Task 8 真站验收")
    ap.add_argument("--n", type=int, default=5, help="cost 模式每组几次")
    ap.add_argument("--trace", default="/tmp/task8.trace.jsonl")
    a = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    mod = load(a.artifact)
    form = pathlib.Path("/tmp/task8_form.json")
    form.write_text("{}", encoding="utf-8")
    trace = pathlib.Path(a.trace)
    if trace.exists():
        trace.unlink()

    f = make_filler(mod, a.ws_url, form, trace)
    print("== 产物 %s / mode=%s ==" % (a.artifact, a.mode))
    print("== 钩子表：%s ==" % [row[0] for row in mod._POPUP_HOOKS])   # 行可能是两格或三格
    print("== 探针 %d 字节 ==" % len(mod._POPUP_PROBE_JS))
    if a.mode == "cost":
        return do_cost(f, mod, a)
    if a.entry_url:
        print("== navi %s ==" % a.entry_url)
        print((f.cdp.navigate(a.entry_url) or "")[:200])
        time.sleep(6)
    print("== 现在这一页：%s ==" % (getattr(f, "_url", None) and f._url()
                                   or timed_eval(f, "return window.location.href;")[2]))
    rc = do_fixed(f, mod, a) if a.mode == "fixed" else do_loop(f, mod, a)
    print("\n== trace ==")
    if trace.exists():
        for line in trace.read_text(encoding="utf-8").splitlines():
            print(line[:400])
    else:
        print("（这一趟没写 trace）")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
