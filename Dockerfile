#TAG=registry.cn-shenzhen.aliyuncs.com/tomoko/siteforge
#BUILD=docker build -t $(grep '#TAG=' Dockerfile|grep -v grep|tail -1|sed 's/#TAG=//') -f Dockerfile .
#
# siteforge —— 看着真页面产出 cdp-first py 脚本的 agent 系统
# 设计规格: docs/superpowers/specs/2026-09-16-siteforge-design.md
#
# 多阶段:
#   stage 1  用 Go 构建 cdp 工具层(CLI + MCP 两个入口, 同一个 internal 内核)
#   stage 2  Python 运行时跑 LangGraph / agent 服务
#
# 与现有容器的关系(规格 §8.2): 不动 auto-farm / auto-llm-script。
# 本容器是宿主侧的 agent 服务, 不是 worker。

# ─────────────────────────── stage 1: 构建工具层 ───────────────────────────
FROM golang:1.26-bookworm AS tools

# 国内构建加速(与仓库其他 Dockerfile 保持一致的镜像源策略)
ENV GOPROXY=https://goproxy.cn,direct
ENV CGO_ENABLED=0

WORKDIR /src
# 先拷 go.mod/go.sum 让依赖层可缓存
COPY tools/cdp/go.mod tools/cdp/go.sum ./
RUN go mod download

COPY tools/cdp/ ./
# CLI 与 MCP 是同一个内核的两个入口(规格 §4.1) —— 不是包一层壳去调 CLI
# 入口是仓库根目录的 main.go(cobra 根命令在 cmd/ 下), 不是 ./cmd/cdp
# 第二个入口 ./cmd/mcp 与它**共用 internal/**: 两条路的行为因此一致
RUN go build -ldflags="-s -w" -o /out/cdp main.go \
 && go build -ldflags="-s -w" -o /out/cdp-mcp ./cmd/mcp \
 && /out/cdp --help >/dev/null \
 && /out/cdp-mcp --help >/dev/null \
 && echo "工具层构建成功(CLI + MCP 两个入口)"

# ─────────────────────────── stage 2: 运行时 ──────────────────────────────
FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y ca-certificates && \
    sed -i 's#http://.*.debian.org#http://mirrors.cloud.tencent.com#g' /etc/apt/sources.list.d/debian.sources && \
    apt-get update && apt-get install -y \
    python3 python3-pip curl jq procps dumb-init postgresql-client \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# agent 侧依赖。刻意与 worker 的依赖不重叠(规格 §8.1):
# 混装会重演「两套执行器」那种边界不清。
RUN pip3 install -i https://mirrors.aliyun.com/pypi/simple/ --break-system-packages \
    langgraph langchain-core langchain-openai \
    fastapi uvicorn pydantic httpx \
    "psycopg[binary]" pyyaml

RUN useradd -m -s /bin/bash appuser

RUN mkdir -p /opt/siteforge/logs \
    /opt/siteforge/tmp \
    /opt/siteforge/runtime \
    /opt/siteforge/config \
    /opt/siteforge/skills

# 工具层: CLI 给生产 py 脚本用, MCP 给 agent 用 —— 同一内核两个门
COPY --from=tools /out/cdp     /usr/local/bin/cdp
COPY --from=tools /out/cdp-mcp /usr/local/bin/cdp-mcp

COPY . /opt/siteforge/

# ⚠️ chmod 必须**分条**写。原先是一条
#     chmod +x /usr/local/bin/cdp /usr/local/bin/cdp-mcp && chmod +x /opt/siteforge/entrypoint.sh 2>/dev/null || true
# 而 `A && B || C` 在 A 失败时**短路** —— B **永远不执行**，末尾的 `|| true` 只救退出码、
# 不救 B（2026-09-16 终审在 /tmp 复现了这条 shell 语义）。当时 cdp-mcp 还不存在
# → 第一段必然失败 → entrypoint.sh 的 chmod 从不执行 → 它在 git 里是 100644（不可执行）
# → 容器起不来：ENTRYPOINT 直接 Permission denied(126)。
#
# 两个二进制现在都在了, 但仍然**分条**: 这条链的教训是"一条失败会连坐后面的",
# 而不是"只有 cdp-mcp 不在时才要分条"。
RUN chmod +x /usr/local/bin/cdp \
    && chmod +x /opt/siteforge/entrypoint.sh
RUN chmod +x /usr/local/bin/cdp-mcp
RUN chown -R appuser:appuser /opt/siteforge/tmp /opt/siteforge/runtime /opt/siteforge/config \
    && chmod -R 777 /opt/siteforge/logs

WORKDIR /opt/siteforge

# 生产 py 脚本靠这个找 cdp(规格 §9: 原先硬编码 /company/cdpcli/cdp)
ENV CDP_PATH=/usr/local/bin/cdp
# agent 的 MCP 门(stdio)。agent 侧按这个路径起 cdp-mcp, 并给它 --ws-url/--host/--port
# —— 连的是 agent **自己的** Bit 窗口(D6/D9), 不是 worker 那个。
ENV CDP_MCP_BIN=/usr/local/bin/cdp-mcp
# Bit 窗口 worker(运营指定, 规格 R8)
ENV BIT_WORKER_IP=""
ENV BIT_ID=""

EXPOSE 8080

# ⚠️ 现状（计划二 Task 2 收尾，2026-09-16）：**镜像可构建，但暂时不可运行** ——
# 工具层这一侧两个入口都齐了（/usr/local/bin/cdp 与 cdp-mcp 都在镜像里，stage 1 各自
# 跑过一次 --help 冒烟），但 ENTRYPOINT 要的 agent.service:app 还没人写
# （agent/ 眼下只有 llm.py 与 tools.py），起来就是 ModuleNotFoundError。
# 构建成功 ≠ 能起容器，别把它读成「已经能跑」。
#
# 用法:
#   docker build -t siteforge:latest .
#   docker run -d --name siteforge --restart unless-stopped \
#     -p 8080:8080 \
#     -e BIT_WORKER_IP=192.168.1.222 \
#     -e BIT_ID=<agent 专用, 不与 worker 共用> \
#     -e OPENAI_API_KEY=... -e OPENAI_BASE_URL=... \
#     -e DATABASE_URL=postgresql://... \
#     siteforge:latest
ENTRYPOINT ["/usr/bin/dumb-init", "--", "/opt/siteforge/entrypoint.sh"]
