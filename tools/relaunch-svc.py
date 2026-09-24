"""重启**服务容器**（一切都走容器 —— 用户 2026-09-24 定的）。

用法：
    python3 tools/relaunch-svc.py                # 重启 siteforge 容器
    python3 tools/relaunch-svc.py siteforge      # 点名容器（默认就是它）

⚠️ 为什么不再是「起一个宿主进程」了（这一版之前的样子，留一行让人不再走回去）：
  2026-09-24 实测：8099 被 **docker-proxy** 占着（→ 容器 172.17.0.7:8080），
  而那个老脚本会在**执行它的机器上**起一个 `uvicorn --port 8099` ⇒ 在宿主上只会
  bind 失败、或者起出一个**没人看的第二实例**（当天真出过一次：一份活开在容器里、
  人在宿主那份的页面上找它 ✗）。⇒ 服务的口、环境、挂载都由**容器自己**带，一处口径。

⚠️ 那几把秘密（`FMR_AGENT_TOKEN` / `FORM_SCRIPT_WRITE_TOKEN` / `OPENAI_API_KEY`）现在由
  容器的环境给（compose / `docker run -e` 那一层）—— 这个脚本**一个都不碰**，
  所以它也不再需要 `/proc/<pid>/environ` 那套搬运与指纹那一段。

⚠️ `python3 tools/relaunch-svc.py` 在**哪台机器上跑都行**（只要那儿有 docker）——
  这正是这一版的目的：不存在第二份服务。
"""
import subprocess
import sys

#: 服务在**宿主上**的那个门牌（容器内是 8080，宿主上发布成这个）。
#: ★ 值班脚本的默认服务口要跟它对得上 —— `tests/test_auto_run_once.py` 有一条钉子钉着
#: （读不到这一格就报错，不许静默通过）。
HOST_PORT = 8099

def main() -> int:
    #: ⚠️ 以 `-` 开头的参数**不当容器名**（否则有人手滑敲 `--help`，那就成了
    #: `docker restart --help` —— 一次莫名其妙的尝试；这一条只为不让人踩这个 ✗）。
    if len(sys.argv) > 1 and sys.argv[1].startswith("-"):
        print(__doc__)
        return 0
    name = (sys.argv[1] if len(sys.argv) > 1 else "siteforge").strip() or "siteforge"
    got = subprocess.run(["docker", "restart", name], capture_output=True, text=True)
    if got.returncode != 0:
        print("重启失败：docker restart %s -> %s" % (name, (got.stderr or got.stdout).strip()),
              file=sys.stderr)
        return 2
    print("重启了 %s" % name)
    #: 回读一眼它现在什么样（**说「重启了」之前先看一眼** —— 这是本仓的老规矩）。
    shown = subprocess.run(["docker", "ps", "--filter", "name=" + name,
                            "--format", "{{.Names}}\t{{.Status}}\t{{.Ports}}"],
                           capture_output=True, text=True)
    print((shown.stdout or "").strip() or "（docker ps 没列出它 —— 去看一眼它是不是起不来）")
    print("服务在宿主上的门牌：http://127.0.0.1:%d" % HOST_PORT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
