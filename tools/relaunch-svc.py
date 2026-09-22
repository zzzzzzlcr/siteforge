"""把 env 从旧进程原样搬给新进程，再叠加 argv 里给的那几格（秘密只走 /proc 与自己的环境）。

用法：`python tools/relaunch-svc.py <旧 pid> [K=V | 变量名] ...`
  · `K=V`          —— 直接叠上这个值（⚠️ 值会进 argv/ps/历史，别拿它传 key）
  · 只给**变量名**   —— 从**我自己的环境**里取那个值（★ 换 key 用这一种：值不进 argv、
                     不进 ps、不进 shell 历史）
  · `-变量名`       —— **删掉**旧进程里的那一格（撤掉一次试验留下的旋钮用这一种）

末尾会把新进程里那几把秘密的**指纹**（sha256 前 12 位）打出来 —— 值一个字符都不打，
但那 12 位够你核对「换成了没有」（与你要用的那把比一比）。
"""
import hashlib
import os
import subprocess
import sys
import time

old_pid = sys.argv[1]
extra = {}
drop = set()
for arg in sys.argv[2:]:
    if "=" in arg:
        k, v = arg.split("=", 1)
        extra[k] = v
    else:
        name = arg.strip()
        if name.startswith("-"):
            drop.add(name[1:])
            continue
        if os.environ.get(name):
            extra[name] = os.environ[name]
            print("从我这儿取到了 %s（值不打）" % name)
        else:
            print("⚠️ 我这里也没有 %s 的值 —— 这一格跳过（没配成）" % name)

raw = open("/proc/%s/environ" % old_pid, "rb").read().split(b"\0")
env = {}
for item in raw:
    if b"=" in item:
        k, v = item.split(b"=", 1)
        env[k.decode()] = v.decode()
for name in drop:
    if env.pop(name, None) is not None:
        print("删掉了旧进程里的 %s" % name)
env.update(extra)
print("搬过来的那几格：", sorted(k for k in env if k.startswith(("BIT_", "SPIKE", "FMR"))
                               or "API_KEY" in k))
print("额外叠上的：", sorted(extra))

os.kill(int(old_pid), 15)
for _ in range(40):
    time.sleep(0.25)
    try:
        os.kill(int(old_pid), 0)
    except OSError:
        break
print("旧进程停了")

log = open("/tmp/svc.log", "a")
proc = subprocess.Popen(
    [".venv/bin/python", "-m", "uvicorn", "agent.service:app", "--host", "0.0.0.0", "--port", "8099"],
    env=env, cwd="/company/siteforge", stdout=log, stderr=subprocess.STDOUT,
    start_new_session=True)
print("新进程 pid =", proc.pid)
time.sleep(1.0)
print("新进程里那几把秘密的指纹（sha256 前 12 位；值不打）:")
for k in sorted(env):
    if "KEY" in k or "TOKEN" in k:
        print("  %-26s %s" % (k, hashlib.sha256(env[k].encode("utf-8")).hexdigest()[:12]))
