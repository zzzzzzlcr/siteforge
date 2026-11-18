#!/usr/bin/env python3
"""同一个全新窗口里，撞到 CF 挑战页之后**重复导航**，看第几跳能过。

用法：python3 -I challenge_retry.py [trials]
每次 trial：关窗 → 开全新窗 → navi 失败URL → 读状态；
若 403 就再 navi 同一 URL（最多 6 跳），每跳记 status/title/正文长度。
"""
import json
import pathlib
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import win  # noqa: E402

CDP = "/company/siteforge/tools/cdp/cdp"
HOST = "192.168.1.222"
PORT = "61129"
JS = ('JSON.stringify({title:document.title,'
      'status:(performance.getEntriesByType("navigation")[0]||{}).responseStatus,'
      'len:(document.body&&document.body.innerText||"").length,'
      'txt:(document.body&&document.body.innerText||"").slice(0,60)})')


def cdp(*args, timeout=60):
    return subprocess.run([CDP, "--host", HOST, "--port", PORT, *args],
                          capture_output=True, text=True, timeout=timeout)


def state():
    out = cdp("eval", JS).stdout.strip()
    try:
        return json.loads(json.loads(out))
    except Exception:
        return {"raw": out[:160]}


def one_trial(url, hops=6):
    win.close_win()
    time.sleep(2)
    ok, ws = win.open_win()
    if not ok:
        print("OPEN_FAIL", flush=True)
        return
    time.sleep(7)
    for i in range(1, hops + 1):
        cdp("navi", url)
        time.sleep(7)
        st = state()
        print(json.dumps({"hop": i, **st}, ensure_ascii=False), flush=True)
        if st.get("status") == 200 and st.get("len", 0) > 50:
            return


if __name__ == "__main__":
    url = (HERE / "failure_url.txt").read_text().strip()
    trials = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    try:
        for t in range(trials):
            print("== trial", t + 1, flush=True)
            one_trial(url)
    finally:
        win.close_win()
