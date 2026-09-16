# siteforge

**看着真页面，产出 cdp-first py 脚本的 agent 系统。**

生产 worker 在**任务失败或遇到新站**时调用它：agent 驱动真浏览器把站走通，
产出 `forms/sites/<site>.py`，自测通过后交付。之后走现有确定性重放 ——
**agent 只在产出期贵，跑起来还是便宜的 py**。

> 设计规格：[`docs/superpowers/specs/2026-09-16-siteforge-design.md`](docs/superpowers/specs/2026-09-16-siteforge-design.md)
> —— 动代码前先读它，8 条设计决策与能力边界都在里面。

## 为什么另起一个项目

原链路是「规则折叠 + 逐坑修补」：clickthrough 跑完 → 旅程被规则压成 JSON →
重放，压不进去的（分支 / 跨轮状态 / 换策略）就没了。2026-09-15 一天 + 09-16
一上午的实测结论是：**规则折叠的每一条规则都是一个必须被真站验证的假设**，
而验证成本全落在真站上。

改法是让一个能**看着页面写代码**的 agent 直接产出 py —— py 是生产已验证的
产物形式，`cdp` 是生产已验证的动作层。

## 目录

```
docs/superpowers/specs/   设计规格（先读）
tools/cdp/                cdp 工具层（Go，已自 /company/cdpcli 迁入）
                          main.go + cmd/  = CLI，生产 py 脚本用
                                            （入口是**模块根**的 main.go，cobra 根命令在 cmd/ 下）
                          cmd/mcp         = MCP server（agent 用，stdio）—— 与 CLI **同内核**，
                                            不是包一层壳去调 CLI（规格 §4.1）
                          internal/mcp/   = 工具表 + 参数校验 + 浏览器目标（与传输解耦，可单测）
                          internal/       = 两个门共用的同一个内核
agent/                    LangGraph 图 + agent 服务
skills/                   bit-window / cdp-browser 两个 skill
tests/                    测试（**必须进 git**）
Dockerfile                多阶段：Go 构建工具层 → Python 运行时
```

## 三条不能破的约束

1. **动页面一律走 cdp 工具**（`form` / `click` / `scroll`），不要手拼 JS。
   二进制已修好穿透 shadow DOM 且走拟人手势；手拼 JS 在 shadow 站上必瞎。
   产出物由契约检查器（lint）强制 —— 不是靠提醒，是靠打回。
2. **agent 用自己的 Bit 窗口**（独立 `bit_id` / `worker_ip`）。
   与 worker 共用会复现「两任务抢同一窗口 → 窗口进程僵死」事故。
3. **agent 用自己的 gost 出口端口**。宿主 :1080 归生产（单例热换）、:1081 归实验，
   共用会在换链时互相踩。

## 开工前提层（最容易做错的一层）

配窗口的顺序不能反，且 **不能用 `bit.sh update` 下发指纹/代理**（它是残缺包装，
收 10 个参数只下发 4 个）。正确做法、权威 JSON、以及三个陷阱（`devicePixelRatio`
字段名 / 换国家要换 gost 链 / close 要验死）都写在规格 **§4.6**。

## 开发环境（宿主上跑测试用）

**宿主 python 里没有 `langgraph` / `fastapi`** —— 它们只装在镜像里（`Dockerfile` 的
`pip3 install` 那行）。在宿主上跑测试请用项目自带的 venv（`.venv/` 已在 `.gitignore` 里）：

```bash
python3 -m venv .venv
.venv/bin/pip install -i https://mirrors.aliyun.com/pypi/simple/ -r requirements.txt
.venv/bin/python -m pytest tests/ -q
```

Go 侧（工具层）两个环境事实，踩过：

```bash
export PATH=/usr/local/go/bin:$PATH          # go 不在默认 PATH 里
export GOPROXY=https://goproxy.cn            # 默认 proxy.golang.org 在这里 i/o timeout
cd tools/cdp && go test ./... -count=1
```

本仓库**没有 remote**：一律本地 commit，不 push。

## 状态

**计划二 Task 1–5 已交付并过审查**（2026-09-16）：spike（LLM 工具循环，结论「有条件能」）、
MCP 门、py 产物骨架、契约检查器（lint）、浏览器 Agent 的工具循环（ReAct over MCP，
含真依赖链 e2e）。Task 6（扰动自测）进行中；Task 7（LangGraph 图）/ Task 8（服务 + 真站端到端）
未开工。

**计划一（工具层）已交付**（2026-09-16）：cdp 已自 `/company/cdpcli` 迁入 `tools/cdp/`，
`observe` / `diff` 两条子命令在真浏览器的四档页面（light DOM / 两层 shadow / 跨源 iframe）上
验过。

**计划二 Task 2（MCP 门）已交付**（2026-09-16）：`tools/cdp/cmd/mcp` 起来了，七个工具
（observe / diff / screenshot / click / form / scroll / goto）走同一份 `internal/`。
浏览器目标两种给法都收（`--ws-url` 吃 `bit.sh open` 吐出来的那串，或 `--host/--port`），
窗口没了立刻报错并点名 `host:port`。计划二其余任务（py 骨架 + lint + 扰动自测 + LangGraph 图）
未开工。

镜像**可构建，但暂时不可运行** —— 工具层这一侧两个入口（`cdp` 与 `cdp-mcp`）都已进镜像，
但 `ENTRYPOINT` 要的 `agent.service:app` 还没人写（`agent/` 眼下只有 `llm.py` 与 `tools.py`），
起来是 ModuleNotFoundError。计划三（Agent Debug Console + 记忆层）未开工。
