#!/usr/bin/env python3
"""8100 Bit 窗口：开 / 活 / 关。只给本次探路用。"""
import json, sys, time, urllib.request

WORKER = "192.168.1.222"
BIT_ID = "982d158b41b14fbb87754667859b4719"
API = "http://%s:54345" % WORKER
OPEN_ARGS = ["--remote-debugging-address=0.0.0.0", "--remote-debugging-port=61129",
             "--remote-allow-origins=*", "--disable-session-crashed-bubble"]

def post(path, body, timeout=80):
    req = urllib.request.Request(API + path, data=json.dumps(body).encode(),
                                 method="POST", headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))

def alive():
    out = post("/browser/pids/alive", {"ids": [BIT_ID]})
    d = out.get("data") or {}
    return d.get(BIT_ID)

def open_win():
    out = post("/browser/open", {"id": BIT_ID, "args": list(OPEN_ARGS), "queue": True}, timeout=90)
    data = out.get("data") or {}
    return out.get("success"), data.get("ws")

def close_win():
    try:
        post("/browser/close", {"id": BIT_ID})
    except Exception as exc:
        print("close err", exc)
    for _ in range(10):
        time.sleep(1.5)
        if not alive():
            return True
    return False

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "alive"
    if cmd == "alive":
        print("alive:", alive())
    elif cmd == "open":
        print(open_win())
    elif cmd == "close":
        print("closed:", close_win())
