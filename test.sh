#!/usr/bin/env bash
# siteforge —— 一条命令把服务起起来（默认**容器**里跑，和宿主那套二选一）。
#
#   ./test.sh                         # 起容器（publish 到 $PORT，默认 8099）
#   ./test.sh host                    # 不起容器，直接宿主起（改一行就生效，开发用）
#   AI_BASE=… AI_KEY=… AI_MODEL=… ./test.sh      # 三格直接写在前面（出口 / key / 模型）
#   PORT=8098 ./test.sh host          # 换端口
#   NO_BUILD=1 ./test.sh              # 不重建镜像（已经 build 过、只想重开）
#
# 环境变量怎么「带过去」：**只有下面 PASS 列的那几格**会进 `./siteforge.env`（600），
# 容器用 `--env-file` 读它；宿主那一条直接 export。
# ⚠️ key 只在那个文件里，别写进仓（`.gitignore` 已挡住它）、别贴进对话。
set -euo pipefail
cd "$(dirname "$0")"

MODE="${1:-docker}"
PORT="${PORT:-8099}"
IMAGE="${IMAGE:-siteforge:latest}"
ENVFILE="${ENVFILE:-./siteforge.env}"

#: 要带到容器里去的变量（**就这几格**；没设的不会写进 env 文件）
PASS=(OPENAI_API_KEY OPENAI_BASE_URL SPIKE_MODEL SPIKE_MAX_TOKENS
      BIT_WORKER_IP BIT_ID BIT_API_PORT
      FMR_AGENT_TOKEN FORM_SCRIPT_WRITE_TOKEN AGENT_WRITE_TOKEN DATABASE_URL)

# ── ① 出口 / key / 模型：给了就用，没给就从当前环境或 env 文件里取 ─────────────
if [ -f "${ENVFILE}" ]; then
  # shellcheck disable=SC1090
  set -a; . "${ENVFILE}"; set +a
fi
export OPENAI_BASE_URL="${AI_BASE:-${OPENAI_BASE_URL:-https://llm.3tkj.cn/v1}}"
export SPIKE_MODEL="${AI_MODEL:-${SPIKE_MODEL:-deepseek-v4-flash}}"
if [ -n "${AI_KEY:-}" ]; then export OPENAI_API_KEY="$AI_KEY"; fi

# ── ② env 文件：没有就按当前环境造一份（以后改 key 就改它）─────────────────────
if [ ! -f "${ENVFILE}" ]; then
  echo "[test.sh] 没有 ${ENVFILE} —— 按当前环境的这几格造一份（chmod 600）"
  : > "${ENVFILE}"; chmod 600 "${ENVFILE}"
  for k in "${PASS[@]}"; do
    v="${!k:-}"
    [ -n "$v" ] && printf '%s=%s\n' "$k" "$v" >> "${ENVFILE}"
  done
fi
if [ ! -s "${ENVFILE}" ] || ! grep -q '^OPENAI_API_KEY=' "${ENVFILE}"; then
  echo "[test.sh] ✗ ${ENVFILE} 里没有 OPENAI_API_KEY —— 把 key 写进去，或先 export OPENAI_API_KEY" >&2
  exit 1
fi
echo "[test.sh] 出口=${OPENAI_BASE_URL}  模型=${SPIKE_MODEL}  端口=${PORT}  env=${ENVFILE}"

# ── ③ 起来 ────────────────────────────────────────────────────────────────
case "$MODE" in
  host)
    echo "[test.sh] 宿主模式：前台跑（Ctrl-C 停）。⚠️ 会和容器抢同一个端口，二选一。"
    exec .venv/bin/python -m uvicorn agent.service:app --host 0.0.0.0 --port "${PORT}"
    ;;
  docker)
    command -v docker >/dev/null 2>&1 || { echo "[test.sh] ✗ 这台机器没有 docker" >&2; exit 1; }
    if [ "${NO_BUILD:-0}" != "1" ]; then
      echo "[test.sh] 构建镜像 ${IMAGE}（几分钟；只想重开就 NO_BUILD=1）"
      docker build -t "${IMAGE}" .
    fi
    #: ⚠️ 同名的旧容器先停掉：两条服务同时连**同一个 Bit 窗口**会互相踩（规格 D6/R8）
    docker rm -f siteforge >/dev/null 2>&1 || true
    docker run -d --name siteforge --restart unless-stopped \
      -p "${PORT}:8080" --env-file "${ENVFILE}" "${IMAGE}" >/dev/null
    echo "[test.sh] 起来了：http://127.0.0.1:${PORT}/console   手册 /manual"
    #: 容器里的日志落到文件（entrypoint `>>` 进 /opt/siteforge/logs/agent.log），`docker logs` 看不到
    echo "[test.sh] 跟日志（Ctrl-C 只退出这个 tail，容器继续跑）："
    exec docker exec siteforge tail -f /opt/siteforge/logs/agent.log
    ;;
  *)
    echo "[test.sh] 用法：./test.sh [docker|host]" >&2
    echo "  环境变量：AI_BASE / AI_KEY / AI_MODEL（出口 / key / 模型）、PORT、ENVFILE、IMAGE、NO_BUILD=1" >&2
    exit 2
    ;;
esac
