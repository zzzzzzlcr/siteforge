#!/bin/bash
# siteforge 容器入口 —— agent 服务宿主侧进程。
# 工具层按需拉起：CLI 由 py 脚本 subprocess 调，MCP 由 OpenClaw 以 stdio 拉起，
# 两者都不需要常驻，所以这里只起 agent 服务本身。
set -euo pipefail

: "${PORT:=8080}"
: "${LOG_LEVEL:=INFO}"

mkdir -p /opt/siteforge/logs /opt/siteforge/runtime

if [ -z "${BIT_WORKER_IP:-}" ] || [ -z "${BIT_ID:-}" ]; then
    echo "WARN: BIT_WORKER_IP / BIT_ID 未设置 —— agent 的窗口生命周期不可用(规格 D6/R8)" >&2
fi

echo "siteforge 启动: port=${PORT} cdp=$(command -v cdp) mcp=$(command -v cdp-mcp)"

exec python3 -m uvicorn agent.service:app \
    --host 0.0.0.0 --port "${PORT}" \
    --log-level "$(echo "$LOG_LEVEL" | tr '[:upper:]' '[:lower:]')" \
    >> /opt/siteforge/logs/agent.log 2>&1
