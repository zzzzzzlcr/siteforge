#!/usr/bin/env python3
"""用 **supervisor** 管夜间 worker（项目自带，不需要 root / 不碰 /etc/supervisor）。

用法（仓库根）：
    python3 tools/night-supervisor.py start      # 起 supervisord + worker
    python3 tools/night-supervisor.py status
    python3 tools/night-supervisor.py restart
    python3 tools/night-supervisor.py stop
    python3 tools/night-supervisor.py logs       # 看 worker 最近 40 行

它管的是 `tools/night-shift.py --watch`：队列里没单就等，有单就按顺序跑；
进程挂了 `autorestart=true` 拉起来。配置 / socket / 日志都落在
`runtime/auto/night/supervisor/`（**不是**系统那套 supervisord）。
"""
from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
HOME = ROOT / "runtime" / "auto" / "night" / "supervisor"
CONF = HOME / "supervisord.conf"


def config_text(root: pathlib.Path, python: str, worker: pathlib.Path,
                home: pathlib.Path | None = None) -> str:
    """一份**只属于这个仓库**的 supervisord 配置（路径全是绝对的）。"""
    home = pathlib.Path(home) if home else HOME
    return """[unix_http_server]
file={home}/supervisord.sock
chmod=0700

[supervisord]
logfile={home}/supervisord.log
pidfile={home}/supervisord.pid
nodaemon=false
minfds=1024

[rpcinterface:supervisor]
supervisor.rpcinterface_factory = supervisor.rpcinterface:make_main_rpcinterface

[supervisorctl]
serverurl=unix://{home}/supervisord.sock

[program:night]
directory={root}
command={python} {worker} --watch --root {root} --seconds 60
autostart=true
autorestart=true
startsecs=2
startretries=3
stopasgroup=true
killasgroup=true
redirect_stderr=true
stdout_logfile={home}/worker.log
stdout_logfile_maxbytes=10MB
stdout_logfile_backups=3
environment=PYTHONUNBUFFERED="1"
""".format(home=home, root=root, python=python, worker=worker)


def _write_config() -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    CONF.write_text(config_text(ROOT, sys.executable, ROOT / "tools" / "night-shift.py"),
                    encoding="utf-8")


def _ctl(*args: str) -> int:
    return subprocess.run(["supervisorctl", "-c", str(CONF), *args], text=True).returncode


def _status() -> int:
    return _ctl("status")


def start() -> int:
    _write_config()
    if _status() == 0:
        print("夜间 worker 已经在跑（supervisor 管的）。")
        return 0
    got = subprocess.run(["supervisord", "-c", str(CONF)], capture_output=True, text=True)
    if got.returncode != 0:
        print("supervisord 起不来：%s" % (got.stderr or got.stdout).strip(), file=sys.stderr)
        return 2
    for _ in range(40):                       # socket 起来要一小会儿
        if _status() == 0:
            return 0
        time.sleep(0.25)
    print("supervisord 起来了，但 worker 没进入 RUNNING —— 看 %s" % (HOME / "worker.log"),
          file=sys.stderr)
    return 2


def stop() -> int:
    if not (HOME / "supervisord.sock").exists():
        print("夜间 worker 没在跑。")
        return 0
    _ctl("shutdown")
    print("夜间 worker 停了。")
    return 0


def restart() -> int:
    _write_config()
    if not (HOME / "supervisord.sock").exists():
        return start()
    return _ctl("restart", "night")


def logs() -> int:
    path = HOME / "worker.log"
    if not path.exists():
        print("还没有日志（worker 没跑过）。")
        return 0
    print("\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-40:]))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="夜间 worker 的 supervisor 控制（项目自带）")
    ap.add_argument("action", choices=["start", "stop", "status", "restart", "logs"])
    got = ap.parse_args()
    return {"start": start, "stop": stop, "status": _status,
            "restart": restart, "logs": logs}[got.action]()


if __name__ == "__main__":
    raise SystemExit(main())
