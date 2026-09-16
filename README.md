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
tools/cdp/                cdp 工具层（Go，自 /company/cdpcli 迁入）
                          cmd/cdp  = CLI，生产 py 脚本用
                          cmd/mcp  = MCP server，agent 用
                          internal/ = 两者共用的同一个内核
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

## 状态

设计中（2026-09-16）。工具层尚未迁入，`Dockerfile` 依赖 `tools/cdp/` 就位后才能构建。
