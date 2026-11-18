#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从 **FMR 现取**这一份脚本（就是舰队每单下载的那条路），在 222 的 Bit 上真跑一趟。

为什么有它：本地的 `japansdates_HOOKED.py` 和推上去的那份 sha 一样，但「本地文件能跑」
不等于「后端下发的那一份能跑」。这一支：
  1. `GET formScript?site=…23d247da`（与 `ad-task.py` 的 resolve_site_script 同一个口）；
  2. **逐字节**落盘（不 strip），校验 sha == 期望值；不符就停；
  3. 在 8100 那颗 Bit（192.168.1.222）上打开落地页真跑 `run()`（`_rpt` 换成空函数）；
  4. 打印最终 url / hash_state / run() 返回值。

`--force-long`：把 pref-gender 固定成 female —— 生产那三单就是这条长问卷路线。

跑法（宿主）：
    python3 -I run_from_fmr.py
    python3 -I run_from_fmr.py --force-long
退出码：0 = run() 自己认为成功；1 = 失败 / 取到的不是期望那份。
"""
import argparse
import hashlib
import importlib.util
import json
import os
import pathlib
import random
import re
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, "/company/siteforge")
import probe_prefgender as P                          # noqa: E402
from agent import fmr                                # noqa: E402

KEY = "soulmatemeets.com/land/sp/23d247da"
EXPECT = "4c8b9fbe52ca9b479085c8161f59975bf1a70b061c1db2d910a7fa5e7277b948"
ART = os.path.join(HERE, "jd_pkg", "sites", "japansdates_from_fmr.py")
FORM = os.path.join(HERE, "form.json")
URL = "https://soulmatemeets.com/land/sp/23d247da"

WORKER = "192.168.1.222"
BIT_ID = "982d158b41b14fbb87754667859b4719"
API = "http://%s:54345" % WORKER
OPEN_ARGS = ["--remote-debugging-address=0.0.0.0", "--remote-debugging-port=61129",
             "--remote-allow-origins=*", "--disable-session-crashed-bubble"]
LOOPBACK = re.compile(r"(\[::1\]|localhost|127\.0\.0\.1|0\.0\.0\.0)")


def read_env() -> dict:
    env = {}
    for line in pathlib.Path("/company/siteforge/siteforge.env").read_text().splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k] = v
    return env


def post(path, body, timeout=25):
    req = urllib.request.Request(API + path, data=json.dumps(body).encode("utf-8"),
                                 method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def alive():
    try:
        out = post("/browser/pids/alive", {"ids": [BIT_ID]})
    except Exception:                              # noqa: BLE001
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
        P.say("  关窗：本来就不在")
        return
    try:
        post("/browser/close", {"id": BIT_ID})
    except Exception as exc:                       # noqa: BLE001
        P.say("  关窗报错：%s" % exc)
    for _ in range(10):
        time.sleep(1.5)
        if alive() is None:
            P.say("  关窗：确认已关")
            return
    P.say("  关窗：**没能确认**")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force-long", action="store_true")
    ap.add_argument("--max-steps", type=int, default=70)
    args = ap.parse_args()

    env = read_env()
    c = fmr.FmrClient(token=env.get("FMR_AGENT_TOKEN", ""),
                      write_token=env.get("FORM_SCRIPT_WRITE_TOKEN", ""))
    d = c.form_script(KEY)
    src = d.get("source") or ""
    sha = hashlib.sha256(src.encode("utf-8")).hexdigest()
    P.say("FMR: site=%s sha=%s bytes=%d" % (d.get("site"), sha[:16], len(src.encode("utf-8"))))
    if sha != EXPECT:
        P.say("!! 取到的不是期望的那份（期望 %s）—— 停" % EXPECT[:16])
        return 1
    pathlib.Path(ART).write_bytes(src.encode("utf-8"))

    spec = importlib.util.spec_from_file_location("jd_from_fmr", ART)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if args.force_long:
        orig = mod.pick_option

        def _pick(opts):
            pref = [o for o in (opts or []) if o == "pref-gender-female"]
            return pref[0] if pref else orig(opts)

        mod.pick_option = _pick
        P.say("已强制 pref-gender-female（生产那条长问卷路线）")

    profile = {"first_name": "Alex",
               "email": "alexprobe%d@outlook.com" % random.randint(1000, 9999)}
    with open(FORM, "w", encoding="utf-8") as fh:
        json.dump(profile, fh)
    P.say("资料：%s" % json.dumps(profile, ensure_ascii=False))

    P.say("探活：%r" % alive())
    ws = open_ws()
    P.say("ws=%s" % ws)
    try:
        f = mod.JapanSDatesFiller(ws, FORM, "sm101fmr_%d" % int(time.time()), "")
        f._rpt = lambda step: None
        f.cdp.navigate(URL)
        time.sleep(4)
        t0 = time.time()
        ok = f.run(max_steps=args.max_steps)
        dt = time.time() - t0
        try:
            raw = f.cdp.eval("(function(){return [window.location.href,"
                             "document.title]})()", "")
            url = raw
            if isinstance(raw, str):
                try:
                    url = json.loads(raw)
                except ValueError:
                    pass
        except Exception:                          # noqa: BLE001
            url = "读不出来"
        P.say("== run() 返回 %r，耗时 %.0fs，最终：%s" % (ok, dt, str(url)[:220]))
        if isinstance(url, list) and url:
            P.say("== hash_state=%r" % mod.hash_state(url[0]))
        return 0 if ok else 1
    finally:
        close_window()


if __name__ == "__main__":
    sys.exit(main())
