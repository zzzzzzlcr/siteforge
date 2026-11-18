#!/bin/bash
# siteforge 容器入口 —— agent 服务宿主侧进程。
# 工具层按需拉起：CLI 由 py 脚本 subprocess 调，MCP 由 OpenClaw 以 stdio 拉起，
# 两者都不需要常驻，所以这里只起 agent 服务本身。
set -euo pipefail

: "${PORT:=8080}"
: "${LOG_LEVEL:=INFO}"

mkdir -p /opt/siteforge/logs /opt/siteforge/runtime /opt/siteforge/runtime/auto

if [ -z "${BIT_WORKER_IP:-}" ] || [ -z "${BIT_ID:-}" ]; then
    echo "WARN: BIT_WORKER_IP / BIT_ID 未设置 —— agent 的窗口生命周期不可用(规格 D6/R8)" >&2
fi

echo "siteforge 启动: port=${PORT} cdp=$(command -v cdp) mcp=$(command -v cdp-mcp)"
#
# 值班（自动修复）在容器里常驻：总开关关着时它只跳心跳、不动手（见 agent/auto.py）。
# 阈值按部署各放一份：runtime/auto/min_streak（没有就按脚本默认 5）。
# 它「量不到」会 exit 2 —— 外面套一层，别让一次接口抖动把值班永久停掉。
AUTO_MIN_STREAK="$(cat /opt/siteforge/runtime/auto/min_streak 2>/dev/null || true)"
(
    while true; do
        python3 -u /opt/siteforge/tools/auto-repair.py \
            --service "http://127.0.0.1:${PORT}" \
            --min-streak "${AUTO_MIN_STREAK:-5}" --seconds 300 \
            >> /opt/siteforge/runtime/auto/daemon.log 2>&1 \
            || echo "$(date -Is) 值班退出 rc=$?，30 秒后再起" >> /opt/siteforge/runtime/auto/daemon.log
        sleep 30
    done
) &


exec python3 -m uvicorn agent.service:app \
    --host 0.0.0.0 --port "${PORT}" \
    --log-level "$(echo "$LOG_LEVEL" | tr '[:upper:]' '[:lower:]')" \
    >> /opt/siteforge/logs/agent.log 2>&1
