#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用**线上 config 101 的那份脚本原文**（sha 41c74af2…）真跑一遍
soulmatemeets.com/land/sp/23d247da，看它到底在哪一步失败。

为什么有它
----------
2026-10-23 用户把这个落地页挂了 config 101，随后失败很多、把挂载去掉了；
去掉之后后端 `formLog?site=…23d247da` 直接 404（config 不再存在 ⇒ 查不到那几单），
所以**拿不到那 5 单的 failDiag**。这里改从「脚本本体」侧复现：
把线上那份原文原样驱动一遍，看它读到什么、点到哪、最后停在哪。

它只做一件事：开窗 → 打开落地页 → `f.run()`（把 `_rpt` 换成空函数，**一个字节都不报给 FMR**）
         → 把最终 url / hash_state / 正文前 200 字打出来 → 关窗。

⚠️ `run()` 会走到真提交（这就是生产每一趟做的事）。资料是编的（probe 邮箱）。

跑法（**宿主上**，窗口走 8100 那颗 Bit 192.168.1.222）：
    python3 -I run_prod.py [max_steps]

退出码：脚本 run() 的返回值（0=它自己认为成功，1=它自己认为失败）+ 2 = 开窗/驱动炸了。
"""
import importlib.util
import json
import os
import random
import re
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ART = os.path.join(HERE, "jd_pkg", "sites", "japansdates_hooked.py")
FORM = os.path.join(HERE, "form.json")
LOG = os.path.join(HERE, "run_hooked.log")

WORKER = "192.168.1.222"
BIT_ID = "982d158b41b14fbb87754667859b4719"      # 8100（siteforge-222）那一颗
API = "http://%s:54345" % WORKER
URL = "https://soulmatemeets.com/land/sp/23d247da"
OPEN_ARGS = ["--remote-debugging-address=0.0.0.0", "--remote-debugging-port=61129",
             "--remote-allow-origins=*", "--disable-session-crashed-bubble"]
LOOPBACK = re.compile(r"(\[::1\]|localhost|127\.0\.0\.1|0\.0\.0\.0)")
MAX_STEPS = int(sys.argv[1]) if len(sys.argv) > 1 else 60


def say(*a):
    print(*a, flush=True)


def post(path, body, timeout=25):
    req = urllib.request.Request(API + path, data=json.dumps(body).encode("utf-8"),
                                 method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def alive():
    try:
        out = post("/browser/pids/alive", {"ids": [BIT_ID]})
    except Exception as exc:                     # noqa: BLE001
        say("  探活问不出来：%s" % exc)
        return "?"
    data = out.get("data") if isinstance(out, dict) else None
    if isinstance(data, dict) and data.get(BIT_ID):
        return int(data[BIT_ID])
    return None


def open_ws():
    out = post("/browser/open", {"id": BIT_ID, "args": list(OPEN_ARGS), "queue": True},
               timeout=70)
    data = out.get("data") if isinstance(out, dict) else None
    ws = (data or {}).get("ws") if isinstance(data, dict) else None
    if isinstance(out, dict) and out.get("success") is True and ws:
        return LOOPBACK.sub(WORKER, str(ws))
    raise RuntimeError("开窗没成：%s" % str(out)[:300])


def close_window():
    if alive() is None:
        say("  关窗：本来就不在")
        return True
    try:
        post("/browser/close", {"id": BIT_ID})
    except Exception as exc:                     # noqa: BLE001
        say("  关窗报错：%s" % exc)
    for _ in range(10):
        time.sleep(1.5)
        if alive() is None:
            say("  关窗：确认已关")
            return True
    say("  关窗：**没能确认**")
    return False


def main():
    with open(os.path.join(HERE, "jd_pkg", "common.py"), encoding="utf-8") as fh:
        assert "class CDPHelper" in fh.read()

    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    import logging
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(LOG, "w", encoding="utf-8"),
                                  logging.StreamHandler(sys.stdout)])

    profile = {"first_name": "Alex",
               "email": "alexprobe%d@outlook.com" % random.randint(1000, 9999)}
    with open(FORM, "w", encoding="utf-8") as fh:
        json.dump(profile, fh)
    say("资料：%s" % json.dumps(profile, ensure_ascii=False))

    spec = importlib.util.spec_from_file_location("jd_prod", ART)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    was_alive = alive()
    say("开窗前探活：%r" % was_alive)
    ws = open_ws()
    say("ws=%s" % ws)
    try:
        cls = mod.JapanSDatesFiller
        f = cls(ws, FORM, "sm101probe_%d" % int(time.time()), "")
        #: ★ 一个字节都不报给 FMR（自测不是生产任务）
        f._rpt = lambda step: None

        out = f.cdp.navigate(URL)
        say("navigate rc=%s" % str(out)[:160])
        time.sleep(3)
        say("落地 url=%s" % str(f.cdp.eval("window.location.href", ""))[:200])

        t0 = time.time()
        ok = f.run(max_steps=MAX_STEPS)
        dt = time.time() - t0

        try:
            raw = f.cdp.eval("(function(){return [window.location.href,"
                             "document.title, (document.body?document.body.innerText:'').slice(0,200)]})()", "")
        except Exception as exc:                 # noqa: BLE001
            raw = "读不出来：%s" % exc
        say("== run() 返回 %r，耗时 %.0fs" % (ok, dt))
        say("== 最终现场：%s" % str(raw)[:600])
        if isinstance(raw, str):
            try:
                url, title, body = json.loads(raw)
                say("== hash_state=%r" % mod.hash_state(url))
                say("== is_success(url)=%r" % mod.is_success(url))
                say("== title=%r" % title)
                say("== 正文前200：%s" % body.replace("\n", " | ")[:200])
            except Exception:
                pass
        return 0 if ok else 1
    finally:
        close_window()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:                     # noqa: BLE001
        import traceback
        traceback.print_exc()
        say("炸了：%s" % exc)
        sys.exit(2)
