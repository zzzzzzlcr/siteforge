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

### 在镜像里跑同一套测试（R-29）

宿主的 python 与**镜像里的**不是一套（宿主 venv 有、镜像没有的包；浏览器路径；Go 工具链的有无），
所以验收要在**镜像里**过一遍，不能只在宿主 venv 里过。镜像里跑**不需要 Go**：
两条真浏览器 e2e 用的 `cdp` / `cdp-mcp` 就是 stage 1 构建进镜像的那两个
（`SITEFORGE_CDP_BIN` / `CDP_MCP_BIN` 指过去），浏览器是 apt 装的 `chromium`。

```bash
docker build -t siteforge:acc .
docker run --rm --entrypoint /bin/bash siteforge:acc \
  -lc 'cd /opt/siteforge && python3 -m pytest tests/ -q'

# 真 Postgres 那条（R-19 的保存点）另给一个库；不给就是 skip（套件默认不依赖外部服务）
docker network create siteforge-acc
docker run -d --name sf-pg --network siteforge-acc \
  -e POSTGRES_PASSWORD=sfacc -e POSTGRES_DB=siteforge postgres:16-alpine
docker run --rm --network siteforge-acc --entrypoint /bin/bash siteforge:acc -lc \
  'cd /opt/siteforge && SITEFORGE_PG_URL=postgresql://postgres:sfacc@sf-pg:5432/siteforge \
     python3 -m pytest tests/ -q'
```

## agent 服务（计划二 Task 8）

```bash
docker run -d --name siteforge -p 8080:8080 \
  -e BIT_WORKER_IP=<agent 自己的窗口 worker> -e BIT_ID=<agent 自己的 bit_id> \
  -e OPENAI_API_KEY=... -e DATABASE_URL=postgresql://... siteforge:latest
```

| 路由 | 干什么 |
|---|---|
| `GET /health` | 活着吗、**状态存哪儿**（内存还是 Postgres；内存的「能恢复」只活在进程里）、cdp 在哪 |
| `POST /run` | 收开场白 → `{job_id}`（立刻返回，活在一个工作线程上排队跑）。载荷要带 `success_text`（什么算成功，只有人知道）与窗口层的旋钮（`set_viewport` / `allow_skips` / `entry_url`） |
| `GET /job/{id}` | 走到哪了、在问你什么、结果是什么 —— **人话**，不是错误码 |
| `POST /job/{id}/reply` | 回答图停下来的那个问题（继续 / 喊停 / 一句纠正 / 这版不行） |
| `POST /job/{id}/reopen` | 窗口没了（P6：Bit 窗口只活几分钟）→ 重开一个，**从断点接着跑** |

三条设计上的硬规矩（都有测试与变异钉着，见 `tests/test_service.py`）：

1. **提交那一刻就把缺的输入拦下**：缺 `success_text`、`allow_skips` 里有不认识的遍、
   显式空 `allow_skips=[]`、要了 `set_viewport` 但没接窗口层 —— 全部 400。
   图的 `intake` 闸是兜底，不是唯一防线（那些输入**免费**，而一次探路很贵）。
2. **`status` 与 `delivered` 是两回事**：跑挂的 job 是 `failed`、`result` 为 `None` ——
   **绝不许**被读成跑成了。
3. **服务不替图发明默认值**（R-31）：没人给窗口层那根线、也没人点名允许跳过 →
   **照收**，让图在 `intake` 停下并说清缺哪根线。「没验到」不许读成「验过了」（R-5）。

Go 侧（工具层）两个环境事实，踩过：

```bash
export PATH=/usr/local/go/bin:$PATH          # go 不在默认 PATH 里
export GOPROXY=https://goproxy.cn            # 默认 proxy.golang.org 在这里 i/o timeout
cd tools/cdp && go test ./... -count=1
```

本仓库**没有 remote**：一律本地 commit，不 push。

## 状态

**计划二 Task 1–8 已交付**（2026-09-16/17）：spike（LLM 工具循环，结论「有条件能」）、
MCP 门、py 产物骨架、契约检查器（lint）、浏览器 Agent 的工具循环（ReAct over MCP）、
扰动自测、LangGraph 图（每个节点之前都能被人拦下）、agent 服务（`agent/service.py`）。

**镜像现在真的能起来了**（Task 8）：`ENTRYPOINT` 要的 `agent.service:app` 已就位。

**计划一（工具层）已交付**（2026-09-16）：cdp 已自 `/company/cdpcli` 迁入 `tools/cdp/`，
`observe` / `diff` 两条子命令在真浏览器的四档页面（light DOM / 两层 shadow / 跨源 iframe）上
验过。`tools/cdp/cmd/mcp` 起来了，七个工具（observe / diff / screenshot / click / form /
scroll / goto）走同一份 `internal/`；浏览器目标两种给法都收（`--ws-url` 吃 `bit.sh open`
吐出来的那串，或 `--host/--port`），窗口没了立刻报错并点名 `host:port`。

计划三（Agent Debug Console + 记忆层）未开工。
