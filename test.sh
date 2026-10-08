#!/bin/bash
# siteforge —— 一条命令起容器（照 auto-farm 的 `test.sh` 那套写法：**值全写在这个文件里**）。
#
#   改 key / 出口 / 模型：就改下面「要传进去的环境」那几行。
#   跑：  ./test.sh
#   只想重开（不重建镜像）：把 `docker build` 那行注释掉。
#
# ⚠️ 这个文件填了真 key 之后**别 commit**（`git status` 会显示它被改过）。
#    想让它不再提示：`git update-index --assume-unchanged test.sh`
#    或者把值挪到一个 `.gitignore` 掉的文件里（仓里那份 `siteforge.env` 已经被 ignore ✓）。
set -euo pipefail
cd "$(dirname "$0")"

# ═══ 要传进去的环境（改这几行）═══════════════════════════════════════════
PORT=8099
#: ★ 2026-10-08：**密钥不进仓** —— 顶上原先那句老话就是这意思:
#: 「本机真值在 ./siteforge.env 里，别 commit 进仓」。工作区那一版把三个真值
#: （`BIT_ID` / `OPENAI_API_KEY` / `FMR_AGENT_TOKEN` / `FORM_SCRIPT_WRITE_TOKEN`）
#: 直接填了进来 —— 那是**要进 git 历史**的，提交前改回从那个文件读。
#: `./siteforge.env` 已被 `.gitignore:22` 忽略、权限 600，八格都在里面。
#: ⚠️ 它要是缺了/读不出来，下面那条检查会当场拦住（不会拿空 key 去起容器）。
if [ -f ./siteforge.env ]; then
    # shellcheck disable=SC1091
    . ./siteforge.env
else
    echo "[test.sh] ✗ 找不到 ./siteforge.env（密钥不进仓，都放在那个文件里）" >&2
    echo "         照这么写：BIT_ID=… / OPENAI_API_KEY=… / FMR_AGENT_TOKEN=…" >&2
    echo "         / FORM_SCRIPT_WRITE_TOKEN=… （权限 600，已在 .gitignore 里）" >&2
    exit 1
fi
BIT_WORKER_IP="${BIT_WORKER_IP:-192.168.1.197}"
OPENAI_BASE_URL="${OPENAI_BASE_URL:-https://llm.3tkj.cn/v1}"
SPIKE_MODEL="${SPIKE_MODEL:-deepseek-v4-flash}"
SPIKE_MAX_TOKENS="12000"          # ⚠️ 别调小：flash 思考重，小预算 content 会回空
# 换代理国家那一格（面板上那个按钮）在**容器里**要能换：容器够不着宿主的 gost ⇒
#   ① 出口地址指到 docker 网桥上的宿主（宿主那条链是 `0.0.0.0:1081`，所以网桥进得来）；
#   ② 链文件那个目录**挂进来** —— watcher 在宿主上盯的就是这个路径，写进去它才看得见。
# ⚠️ 这两样不配 = 面板那一格会**如实说「没换成」**（它核不到出口国家），不会假装换好了。
SITEFORGE_GOST_SOCKS="172.17.0.1:1081"
# DATABASE_URL=postgresql://user:pass@host:5432/siteforge   # 可选：设了状态就不随重启丢
# ═══════════════════════════════════════════════════════════════════════

NAME=siteforge
IMAGE=siteforge:latest
# ⚠️ 值不进命令行：`-e NAME="$VAR"` 只是把**这个脚本自己**的环境变量传进去，
#    `ps`/history 里看不到值（写 `-e OPENAI_API_KEY=sk-…` 那种才有那个问题）。
export BIT_WORKER_IP BIT_ID OPENAI_API_KEY OPENAI_BASE_URL SPIKE_MODEL
export SPIKE_MAX_TOKENS FMR_AGENT_TOKEN FORM_SCRIPT_WRITE_TOKEN SITEFORGE_GOST_SOCKS

echo "[test.sh] 出口=${OPENAI_BASE_URL}  模型=${SPIKE_MODEL}  端口=${PORT}"
[ -n "${OPENAI_API_KEY}" ] && [ "${OPENAI_API_KEY}" != "<把 key 贴这儿>" ] \
  || { echo "[test.sh] ✗ OPENAI_API_KEY 还没填（改这个文件顶上那几行）" >&2; exit 1; }

# ── Go 那两个二进制在**宿主**上建：它们等一下挂进容器，所以永远等于当前源码 ─────
#    ⚠️ 镜像里那份只在改依赖（go.mod / Dockerfile / entrypoint.sh）时才需要重跑。
PATH=/usr/local/go/bin:$PATH
export PATH
( cd tools/cdp \
  && go build -ldflags="-s -w" -o cdp main.go \
  && go build -ldflags="-s -w" -o cdp-mcp ./cmd/mcp ) \
  || { echo "[test.sh] ✗ Go 二进制没建起来，别急着起容器" >&2; exit 1; }

# ── 镜像：没有才建（代码走挂载，改代码不用重建）────────────────────────────
docker image inspect "$IMAGE" >/dev/null 2>&1 || docker build -t "$IMAGE" .

# ── 同名的旧容器先停掉：两条服务连**同一个 Bit 窗口**会互相踩（规格 D6/R8）─────────
docker rm -f "$NAME" >/dev/null 2>&1 || true

# ── 起（`--dns` 照你们别的容器；挂四处：agent、forms、runtime、cdp 二进制）────────
#    ⚠️ 2026-09-23 的教训：以前只挂 forms/runtime ⇒ `agent/` 与 `tools/cdp/` 用的都是
#    **镜像里**那份 ⇒ 改了源码不重建镜像，容器还在跑旧逻辑（真站自测跑的是旧行为，
#    白追半天）。现在源码与二进制都挂进来：改完直接 `./test.sh`，不重建也是最新的。
#    那两个 `-e` 是指到挂进来的二进制（不然跑的还是镜像里 `/usr/local/bin/` 那份）。
#: ★ 2026-09-24：**手册那一份要挂进来**（就是下面末尾那条 `-v .../docs`）——
#: 它是**每次请求读盘**的（`agent/manual.py`），但 `docs/` 原先没挂 ⇒ 容器里吃的是
#: **烤进镜像的那份拷贝**：仓库里改了手册，面板上 `/manual` 那一页照旧（实测：
#: 仓库 503 行 / 容器 423 行，页面上一个字都没变）。手册是运营照着做事的唯一一份东西，
#: **它不许悄悄过期**。
docker run -d --name "$NAME" --restart unless-stopped \
  --dns 192.168.1.1 \
  -p "${PORT}:8080" \
  -e BIT_WORKER_IP -e BIT_ID -e OPENAI_API_KEY -e OPENAI_BASE_URL -e SPIKE_MODEL \
  -e SPIKE_MAX_TOKENS -e FMR_AGENT_TOKEN -e FORM_SCRIPT_WRITE_TOKEN -e SITEFORGE_GOST_SOCKS \
  -e SITEFORGE_CDP_BIN=/opt/siteforge/tools/cdp/cdp \
  -e CDP_MCP_BIN=/opt/siteforge/tools/cdp/cdp-mcp \
  -e PYTHONDONTWRITEBYTECODE=1 \
  -v /opt/skills/auto-farm-skill/config:/opt/skills/auto-farm-skill/config \
  ${DATABASE_URL:+-e DATABASE_URL} \
  -v "$PWD/agent:/opt/siteforge/agent" \
  -v "$PWD/forms:/opt/siteforge/forms" \
  -v "$PWD/tools/cdp:/opt/siteforge/tools/cdp" \
  -v "$PWD/runtime:/opt/siteforge/runtime" \
  -v "$PWD/docs:/opt/siteforge/docs" \
  "$IMAGE"

echo "[test.sh] 起来了：http://127.0.0.1:${PORT}/console   手册 /manual"
#: 容器里的日志落到文件（entrypoint `>>` 进 /opt/siteforge/logs/agent.log），`docker logs` 看不到
exec docker exec "$NAME" tail -f /opt/siteforge/logs/agent.log
