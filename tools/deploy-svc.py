#!/usr/bin/env python3
"""把**最新镜像**装到服务容器上（重建容器，不是 `docker restart`）。

用法：
    docker build -t siteforge:latest -f Dockerfile .   # 先出新镜像
    python3 tools/deploy-svc.py                        # 重建两个（siteforge + siteforge-222）
    python3 tools/deploy-svc.py siteforge              # 只重建一个
    python3 tools/deploy-svc.py --dry-run              # 只把要执行的 docker run 打出来

与 `relaunch-svc.py` 的分工（两个都要，别拿一个当另一个）：
  · `relaunch-svc.py` = `docker restart` —— 进程重启。`agent/` `forms/` `tools/cdp/` `runtime/`
    都是**挂载**的，所以改这些只看它一眼就够。
  · 这个脚本 = **换镜像**。镜像里还有**没挂载**的那部分（`tools/` 别的脚本、`tests/`、
    `/usr/local/bin/cdp` 那两个二进制、pip 依赖）—— 改到那些地方，重启是看不见的。

⚠️ 为什么不是手敲 `docker run`：这两个容器的 env 里带着几把**真 token**
（`FMR_AGENT_TOKEN` / `FORM_SCRIPT_WRITE_TOKEN` / `OPENAI_API_KEY`），还有逐台不同的
`BIT_ID` / `BIT_WORKER_IP` / 挂载（8099 有 `forms/`+`runtime/`，8100 有 `forms-222/`+`runtime-222/`）。
手抄一份 = 迟早抄错，而错了的表现是「容器起来了但连不上 / 抢了另一台的 Bit 档案」。
这里**从容器自己身上**（`docker inspect`）把 env / 挂载 / 端口 / restart 原样搬过来。

安全网（顺序就是它的做法）：
  ① 老容器**改名 + 停掉**（不删）—— 宿主端口得让出来；
  ② 新的起来后 `/health` 不通 ⇒ 删掉新的、老的改回原名并起回来（**回退**，退出码 3）；
  ③ 通了才删老的。
⚠️ `/opt/siteforge/logs` **不在挂载里** ⇒ 重建等于把容器内的日志清掉。要留就先
   `docker cp <容器>:/opt/siteforge/logs /tmp/...`（部署前的日志就只有那一份）。
"""
import json
import subprocess
import sys
import time
import urllib.request

#: 容器 → 宿主端口。只有这里知道哪台发在哪个口上（`relaunch-svc.py` 那份只认 8099，
#: 因为它只管重启；这个脚本要按口验活/验回退，所以两个都得认）。
CONTAINERS = {"siteforge": 8099, "siteforge-222": 8100}
IMG = "siteforge:latest"


def sh(*args) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True)


def inspect(name: str) -> dict:
    got = sh("docker", "inspect", name)
    if got.returncode != 0:
        raise SystemExit("inspect %s 失败（容器在不在？）：%s" % (name, got.stderr.strip()))
    return json.loads(got.stdout)[0]


def run_args(spec: dict) -> list:
    """老容器那一份规格 → `docker run` 参数（逐条照搬，不做任何「顺手改成更好」）。"""
    args = ["docker", "run", "-d", "--name", spec["Name"].lstrip("/")]
    for one in (spec["HostConfig"].get("PortBindings") or {}).get("8080/tcp") or []:
        args += ["-p", "%s:8080" % one.get("HostPort")]
    args += ["--restart", spec["HostConfig"]["RestartPolicy"].get("Name") or "no"]
    for kv in spec["Config"].get("Env") or []:
        args += ["-e", kv]
    for m in spec.get("Mounts") or []:
        if m.get("Type") == "bind":
            args += ["-v", "%s:%s" % (m["Source"], m["Destination"])]
    return args + [IMG]


def health(port: int, tries: int = 40) -> dict | None:
    for _ in range(tries):
        try:
            return json.loads(urllib.request.urlopen(
                "http://127.0.0.1:%d/health" % port, timeout=5).read().decode())
        except Exception:
            time.sleep(1.5)
    return None


def deploy(name: str, port: int, dry: bool) -> int:
    spec = inspect(name)
    old_img = spec["Image"][7:19]
    new_img = sh("docker", "images", "-q", "--no-trunc", IMG).stdout.strip()[:12]
    args = run_args(spec)
    print("== %s（宿主 %d）容器镜像 %s → %s" % (name, port, old_img, new_img or "**没有这个镜像**"))
    if dry:
        print("   " + " ".join(args))
        return 0
    if not new_img:
        raise SystemExit("没有 %s —— 先 docker build -t %s ." % (IMG, IMG))
    old = "%s-old-%d" % (name, int(time.time()))
    if sh("docker", "rename", name, old).returncode != 0:
        return 1
    sh("docker", "stop", "-t", "10", old)
    got = sh(*args)
    if got.returncode != 0:                          # 新的根本没起来 ⇒ 原样退回去
        print("起新的失败：%s" % got.stderr.strip(), file=sys.stderr)
        sh("docker", "rename", old, name)
        sh("docker", "start", name)
        return 2
    new_id = got.stdout.strip()
    h = health(port)
    if h is None or not h.get("ok"):                 # 起来了但不干活 ⇒ 一样退回去
        print("新容器 /health 不通 —— 回退。日志尾巴：%s"
              % (sh("docker", "logs", "--tail", "30", new_id).stdout
                 + sh("docker", "logs", "--tail", "30", new_id).stderr), file=sys.stderr)
        sh("docker", "rm", "-f", new_id)
        sh("docker", "rename", old, name)
        sh("docker", "start", name)
        print("已回退到老容器（%s）" % name)
        return 3
    print("   /health ok（jobs=%s）—— 删掉老容器 %s" % (h.get("jobs"), old))
    sh("docker", "rm", old)
    return 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1].startswith("-") and sys.argv[1] != "--dry-run":
        print(__doc__)
        return 0
    dry = "--dry-run" in sys.argv
    picked = [a for a in sys.argv[1:] if not a.startswith("-")]
    names = picked or list(CONTAINERS)
    bad = [n for n in names if n not in CONTAINERS]
    if bad:
        raise SystemExit("不认识这几个容器名：%s（认得的只有 %s）"
                         % ("、".join(bad), "、".join(CONTAINERS)))
    rc = 0
    for name in names:
        got = deploy(name, CONTAINERS[name], dry)
        rc = rc or got
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
