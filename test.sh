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
BIT_WORKER_IP=192.168.1.197
BIT_ID="<agent 专用那个 32 位；本机真值在 ./siteforge.env 里，别 commit 进仓>"
OPENAI_API_KEY="<把 key 贴这儿>"
OPENAI_BASE_URL="https://llm.3tkj.cn/v1"
SPIKE_MODEL="deepseek-v4-flash"
SPIKE_MAX_TOKENS="12000"          # ⚠️ 别调小：flash 思考重，小预算 content 会回空
FMR_AGENT_TOKEN="<读 token>"
FORM_SCRIPT_WRITE_TOKEN="<写 token>"
# DATABASE_URL=postgresql://user:pass@host:5432/siteforge   # 可选：设了状态就不随重启丢
# ═══════════════════════════════════════════════════════════════════════

NAME=siteforge
IMAGE=siteforge:latest
# ⚠️ 值不进命令行：`-e NAME="$VAR"` 只是把**这个脚本自己**的环境变量传进去，
#    `ps`/history 里看不到值（写 `-e OPENAI_API_KEY=sk-…` 那种才有那个问题）。
export BIT_WORKER_IP BIT_ID OPENAI_API_KEY OPENAI_BASE_URL SPIKE_MODEL
export SPIKE_MAX_TOKENS FMR_AGENT_TOKEN FORM_SCRIPT_WRITE_TOKEN

echo "[test.sh] 出口=${OPENAI_BASE_URL}  模型=${SPIKE_MODEL}  端口=${PORT}"
[ -n "${OPENAI_API_KEY}" ] && [ "${OPENAI_API_KEY}" != "<把 key 贴这儿>" ] \
  || { echo "[test.sh] ✗ OPENAI_API_KEY 还没填（改这个文件顶上那几行）" >&2; exit 1; }

# ── 构建（已经 build 过就注释掉这一行；⚠️ 改过代码必须重建，容器不像宿主那样重启就生效）──
docker build -t "$IMAGE" .

# ── 同名的旧容器先停掉：两条服务连**同一个 Bit 窗口**会互相踩（规格 D6/R8）─────────
docker rm -f "$NAME" >/dev/null 2>&1 || true

# ── 起（`--dns` 照你们别的容器；挂两处：产物与运行账）──────────────────────────
docker run -d --name "$NAME" --restart unless-stopped \
  --dns 192.168.1.1 \
  -p "${PORT}:8080" \
  -e BIT_WORKER_IP -e BIT_ID -e OPENAI_API_KEY -e OPENAI_BASE_URL -e SPIKE_MODEL \
  -e SPIKE_MAX_TOKENS -e FMR_AGENT_TOKEN -e FORM_SCRIPT_WRITE_TOKEN \
  ${DATABASE_URL:+-e DATABASE_URL} \
  -v /company/siteforge/forms:/opt/siteforge/forms \
  -v /company/siteforge/runtime:/opt/siteforge/runtime \
  "$IMAGE"

echo "[test.sh] 起来了：http://127.0.0.1:${PORT}/console   手册 /manual"
#: 容器里的日志落到文件（entrypoint `>>` 进 /opt/siteforge/logs/agent.log），`docker logs` 看不到
exec docker exec "$NAME" tail -f /opt/siteforge/logs/agent.log
