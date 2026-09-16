# siteforge — 看着真页面产出 cdp-first py 脚本的 agent 系统

2026-09-16

---

## 一、要解决的问题

### 1.1 「补坑」走到尽头

现行路子是「规则折叠 + 逐坑修补」：clickthrough 看着页面跑 → 旅程被
`_trace_to_json` 压成 version:2 JSON → 重放；压不进去的（分支、跨轮状态、
「同一状态重复出现就换策略」）就没了。

这条路的代价已经很明显：

- **2026-09-15 一天**：交付「cdp 穿透 shadow」+「性质一记账诚实」两件，
  georgiapower 仍然一个字段填不进去（`13/13` 谎 → `2/13` 诚实，但站点没跑通）
- **2026-09-16 上午**：继续修 `_smart_form` 回读，做完了 —— 而 georgiapower 纹丝不动
- 同日上午在 `py_emitter`（折叠成 py 那条路）上发现**三个一跑就死的断口**：
  1. `_pretty` 产的是 JSON 不是 py 字面量 → 产物 `import` 就
     `NameError: name 'false' is not defined`
  2. `_ev()` 多行值只返回第一行 → 页面正文被截成页头一行
  3. 两边签名归一化口径不一致 → `_match` 恒 None、真站上永远迈不动

三个断口有一个共同性质：**这一层从来没人跑过**。规则折叠的每一个规则都是
一个必须被验证的假设，而验证成本落在真站上。

### 1.2 改动落在错的基线上（维护性的痛）

一天里反复撞到：

- newTaskTest 的**生产代码在分支 `repush-verify-first`，不在 master** —— 改 master = 白改
- 两套并存的 JSON 执行器（生产 `form_executor/` vs 实验 `src/lanuage/`）
- `cdp` 二进制 5 处部署副本
- `tests/` 被 `.gitignore` 忽略 —— 测试从不进 git，机器重建即丢
- 容器内 supervisorctl 不可用、生产用 gbind mount、实验用 docker cp……

**这些不是「绕」，是边界没划清。** 新项目的第一价值就是把这些边界一次划死。

### 1.3 结论

不再靠规则去逼近页面，**让一个能看着页面写代码的 agent 直接产出 py**；
py 是生产已验证的产物形式，cdp 是生产已验证的动作层。

---

## 二、已确认的设计决策（8 条）

| # | 决策 | 内容 |
|---|---|---|
| **D1** | **项目与边界** | 新项目 `/company/siteforge`。**迁入** `cdpcli`（Go，4644 行）作工具层；**不动** `auto-farm-skill`（生产 py 执行路径）、worker、Bit 窗口机制 |
| **D2** | **agent 主视角 = `observe`** | agent 看页面走一个**结构化**工具，不是 DOM 树、也不是让 agent 自己写 JS。一次调用返回可动作元素表 + 选择器候选 + 平台/环境指纹 |
| **D3** | **选择器候选 + 稳定性评级，第一版就做** | 每个元素给多个选择器候选和 `high/medium/low` 评级。**选择器是 py 准不准的头号因素** |
| **D4** | **环境指纹随 py 落盘（契约）** | 产物头部写 `PROVENANCE` 元数据块（代理国家/DPR/UA/视口/平台/自测结果/来源）。同一个 URL 在不同代理国家是**不同的页面** |
| **D5** | **自测 3 遍** | 在真浏览器上连跑 3 遍全过才算「自测通过」；任一遍挂 = 自测未过 + 卡在哪 |
| **D6** | **agent 用自己的 Bit 窗口** | **硬约束**。worker 的 `/task` 自己会 `bit.sh open`；agent 若用同一个 `bit_id`，必然复现「两任务抢同一窗口 → 窗口进程僵死」事故 |
| **D7** | **cdp 出 MCP 门（同内核双出口）** | 不绕：不是包一层壳去 subprocess CLI，而是 Go 内核直接多一个 MCP 入口。传输先 **stdio**，YAGNI |
| **D8** | **两个 skill 给 agent 用** | `bit-window`（窗口生命周期 + 指纹/代理下发）+ `cdp-browser`（页面操作）。**skill 里编码「坑」比编码「用法」值钱** —— 尤其 §4.6 那三个陷阱 |
| **D9** | **agent 用自己的 gost 出口端口** | 与 D6 同构的硬约束。宿主 :1081 归**实验容器**（`gost-watch.sh` 维护）、:1080 归**生产**（单例热换）。agent 若共用，换链会互相踩 —— 实测过：换掉生产正在用的链、或用别的站的链 → 页面打不开只有 3 个元素。agent 单开一个端口 + 独立 gost 实例 |

---

## 三、架构

```
┌──────────────────────────────────────────────────────────────┐
│ OpenClaw   入口 / skill 管理 / 工具网关                        │
│  · 接 worker 的请求（失败单 / 新站）                            │
│  · skills: bit-window、cdp-browser                            │
└───────────────────────────┬──────────────────────────────────┘
                            │ MCP
┌───────────────────────────▼──────────────────────────────────┐
│ LangGraph   业务流程 / 状态 / 人机介入                          │
│  intake → explore → draft → lint → selftest → deliver         │
│                    ↑        │        │                        │
│                    └────────┴────────┘  diagnose              │
│  checkpointer: Postgres（可恢复 / 可回放 / HITL interrupt）      │
└─────────┬─────────────────────────────────┬──────────────────┘
          │                                 │
┌─────────▼──────────┐            ┌─────────▼──────────────────┐
│ Browser Agent      │            │ Memory / DB                 │
│ （LangGraph 子图）  │            │ 站点指纹 · 失败证据 ·        │
│  ReAct over cdp    │            │ py 版本 · 自测记录 · 环境指纹 │
└─────────┬──────────┘            └────────────────────────────┘
          │ MCP（stdio）
┌─────────▼──────────────────────────────────────────────────┐
│ cdp 工具层（Go 内核，迁自 /company/cdpcli）                    │
│   ┌───────────────── internal/ ─────────────────┐           │
│   │ 穿透解析 __cdpQ · 拟人手势 · 表单/下拉/勾选 · 帧 │           │
│   └──────┬──────────────────────────┬───────────┘           │
│          │                          │                       │
│   cmd/cdp（CLI）              cmd/mcp（MCP server）          │
│   生产 py 脚本用               agent 用                      │
└──────────────────────────────────────────────────────────────┘
```

**三个关键取舍：**

1. **Browser Agent 是 LangGraph 的子图，不是独立服务** —— 否则会变成两套状态机互相甩锅
2. **OpenClaw↔LangGraph 的接缝用 MCP** —— 这是公开的标准接法，不用给两个框架各写一套适配
3. **agent 跑在宿主，不进 worker 容器** —— 它只需 worker 递来的 `ws_url`，驱动同一个 Bit 窗口

---

## 四、工具层

### 4.1 同内核双出口（D7 的落地形态）

```
        ┌──────────── Go 内核（internal/）────────────┐
        │  穿透解析 · 拟人手势 · 表单 · 帧 · 快照      │
        └──────┬──────────────────────────┬──────────┘
               │                          │
        cmd/cdp（CLI，文本）        cmd/mcp（MCP，结构化）
               │                          │
        生产 py 脚本                 agent（进程内直调内核）
```

**❌ 绕的做法**：写个 Python/Node MCP server，里面 `subprocess.run(["cdp", ...])`
再解析 stdout —— 多一层进程、照样吃 Go 日志噪声、照样双重转义。

**两条路共用同一内核**，所以行为一致（穿透/拟人/帧处理同一套）。

### 4.2 工具清单（MCP 暴露给 agent）

| 工具 | 作用 |
|---|---|
| `observe` | **主视角**：结构化页面模型（见 §4.3） |
| `diff` | 动作前后差分：可见元素集合 / URL / 正文 / 报错 → 回答「刚才那一下有没有推进」 |
| `click` | 拟人点击（穿透 shadow / 跨源 iframe） |
| `form` | 填值 / 选下拉 / 勾选（拟人手势） |
| `scroll` | 滚动（穿透） |
| `goto` | 导航 |
| `eval` | 逃生舱：执行 JS（只能用来**读**） |
| `window_open` / `window_update` / `window_close` | Bit 窗口生命周期（对应 skill `bit-window`） |

### 4.3 `observe` 契约

```jsonc
{
  "url": "https://...",
  "title": "...",
  "env": { "proxy_country": "US", "dpr": 1, "ua": "...", "viewport": [1280, 800] },
  "platform": { "guess": "salesforce-lightning", "confidence": 0.9,
                "evidence": ["lightning-* 自定义元素", "aura 命名空间"] },
  "page_text": "归一化后的前 600 字（与 py 里 page_signature() 同口径）",
  "obstructions": [
    { "kind": "cookie-banner", "selector": "#onetrust-banner",
      "dismiss_selector": "#onetrust-accept-btn-handler" }
  ],
  "actions": [
    { "selector": "#schedule-now",
      "alternates": ["button[data-testid=schedule]", "div.hero > button"],
      "stability": "high",
      "text": "Schedule Now", "role": "button", "tag": "BUTTON",
      "visible": true, "occluded_by": null,
      "shadow_depth": 2, "frame_path": ["main"], "bbox": [812, 640, 180, 44] }
  ],
  "fields": [
    { "selector": "input#firstName", "alternates": ["input[name=firstName]"],
      "stability": "high", "label": "First Name", "hint": "firstName",
      "type": "text", "required": true, "maps_to": "first_name" }
  ],
  "option_groups": [
    { "scope": "chooseOption__list", "role": "option",
      "options": ["Tub to walk-in shower", "I need help deciding"] }
  ]
}
```

**为什么必须是「一次调用」**：Bit 窗口存活只有几分钟，agent 每轮拆成七八次
调用会把窗口耗死。所有静态信息一次取齐。

### 4.4 选择器稳定性评级（D3）

| 评级 | 判据 |
|---|---|
| `high` | 有稳定 `id` / `name` / `data-*`，且不含随机 hash；或文本全局唯一 |
| `medium` | 文本唯一但选择器依赖结构（`nth-of-type` 链 ≤ 3 层） |
| `low` | 依赖随机 class hash / 深结构路径 > 3 层 / 文本不唯一 |

随机 hash 的判据：`id`/`class` 含 ≥ 8 位连续 hex/base36 片段。**两次加载对比**
可以确认随机性（窗口允许时做，不允许时用启发式）。

### 4.5 能力边界（必须写进 skill，否则 agent 会踩）

- `form` / `click` / `scroll` **能**穿透 shadow DOM（两层嵌套实测通过）与**跨源 iframe**
- `eval` **不能**穿透 shadow DOM —— 用 eval 找元素在 Salesforce 类站点上必瞎
- `--host/--port` 必须放位置参数**前面**（CLI 的坑；MCP 门没有这个问题）

### 4.6 窗口与代理 —— 开工前提层（**最容易做错的一层**）

agent 在能看页面之前，必须先把这个窗口**配对**。顺序不能反。

#### 正确的调用序列

```
① 拉代理链（要换出口国家时）
   GET  {PROXY_API}?url={target}&country=US
        → {code:0, proxies:[{server,user,passwd,jumper,proxy_id}]}
   ProxyManager().restart_gost(info)      # 写链
   pkill -x gost                          # 强制重拉（不 kill 不生效）
   国家从 proxy_id 解析：B_78982_CA___ → CA

② 下发窗口配置（**直接 POST /browser/update，不走 `bit.sh update`**）
   见下方 JSON。必须在 `open` **之前** —— 里面是
   clearCacheFilesBeforeLaunch / clearCookiesBeforeLaunch，启动时才生效。

③ 开窗口
   bit.sh open <worker_ip> <bit_id>       # → ws_url（127.0.0.1 已改写成 worker_ip）

④ 干活（observe / click / form / …）

⑤ 关窗口 + **验死**
   bit.sh close <worker_ip> <bit_id>
   POST /browser/pids/alive {"ids":[bit_id]}   # rc=0 ≠ 真关了
```

#### ② 的权威 JSON

```jsonc
{ "id": "<bit_id>", "proxyMethod": 1, "proxyType": "socks5",
  "host": "<宿主IP>", "port": <agent 自己的 gost 端口，见 D9>,
  "syncTabs": false,
  "clearCacheFilesBeforeLaunch": true, "clearCookiesBeforeLaunch": true,
  "browserFingerPrint": {
    "coreVersion": "<random 130|132|134|136|138|140|142>",
    "ostype": "PC", "os": "Win32", "osVersion": "11,10",
    "devicePixelRatio": 1
  } }
```

#### ⚠️ 三个必须写进 skill 的陷阱

| 陷阱 | 后果 |
|---|---|
| **`bit.sh update` 是残缺包装，不能用它下发指纹/代理** | 它收了 `UA/SW/SH/DPR/PROXY_USER/PROXY_PASS` 这些参数，但真正 POST 的 JSON 里**只有** `id/proxyMethod/proxyType/host/port` + `browserFingerPrint{coreVersion,ostype,os,osVersion,webGL*}` —— **UA 字符串不下发**（只拿来推 OS 类型）、**`sw`/`sh`/`dpr` 收了完全没用**（JSON 里连键都没有）、**代理用户名密码不下发** |
| **DPR 字段名必须是 `devicePixelRatio`** | 写 `dpr` 被 BitBrowser **静默忽略**，回读仍是 3。Bit 默认 DPR=3 → cdp 手势按 CSS 像素发坐标、底层按设备像素落 → **点击偏移 3 倍点空气**。实测对照：写 `dpr` 回读 3；写 `devicePixelRatio` 回读 1。DPR=3 时同一个 `target=_blank` 链接点 25 秒开不出新 tab，DPR=1 后 **2 秒**开出 |
| **「换出口国家」= 换宿主 gost 的链，不是改窗口配置** | 窗口连的是宿主 gost（`host=宿主IP`），出网走 gost 的链。改窗口配置改不了国家 |

#### 另外两条（时效/归属）

- **窗口存活只有几分钟** —— 探针要合并成尽量少的调用（这也是 `observe` 必须一次取齐的原因，§4.3）
- **`close` 返回 `rc=0` ≠ 窗口真关了** —— 必须查 `/browser/pids/alive`；不查就复用，下次任务会拿到正在死掉的窗口

---

## 五、py 产出契约

### 5.1 骨架由模板固定，agent 只填「怎么走」

- CLI 契约不变：`--ws-url --form-file --correlation-id --log-level --task-id`
- `from common import CDPHelper, setup_logger, report_url`
- **动作必须走 cdp**：`self.cdp.form(...)` / `self.cdp.click(...)` / `self.cdp.scroll(...)`
- `eval` **只准用来读**

### 5.2 契约检查器（lint）—— 把原则变成机器可执行的约束

产物里出现下列手拼填充/点击 → **打回 agent 重写**：

- `dispatchEvent(new Event('input'` / `'change'`
- `Object.getOwnPropertyDescriptor(...'value')`
- `e.click()` / `.click()` 形式的 JS 点击
- 裸 `document.querySelector` 出现在**写**路径

这条是 2026-09-16 用户定的原则的机器化：**优先用 cdp 工具，别手拼 JS** ——
不靠提醒，靠打回。

### 5.3 `PROVENANCE` 元数据块（D4）

```python
# ── 产出元数据（siteforge 自动写入，勿手工编辑）────────────
PROVENANCE = {
    "generated_at": "2026-09-16T10:30:00+08:00",
    "generator": "siteforge/v0.1",
    "env": {"proxy_country": "US", "dpr": 1, "ua": "...", "viewport": [1280, 800]},
    "platform": {"guess": "salesforce-lightning", "confidence": 0.9},
    "selftest": {"runs": 3, "passed": 3, "at": "..."},
    "source": {"kind": "fix", "evidence": "FMR formStep step=6 ..."},
}
```

**为什么必须落盘**：同一个 URL 在不同代理国家是**不同的页面**（parents.com：
英国代理没有菜单、美国代理有）。环境漂了而没人知道，就是下一轮「昨天还好
今天不行」的排查地狱。

---

## 六、LangGraph 图

```
intake → explore → draft → lint → selftest → deliver
                    ↑        │        │
                    └────────┴────────┘
                          diagnose（带证据回灌）
```

| 节点 | 做什么 | 失败去向 |
|---|---|---|
| `intake` | 收站点 URL + 失败证据（FMR `formLog`/`formStep`）或运营描述。**开工前提层（§4.6）在这里做**：拉对应国家的链（D9 的独立 gost 端口）→ POST `/browser/update` 下发指纹 → `bit.sh open` 拿 ws_url | 链拉不到 / 窗口不可用 → HITL（R11） |
| `explore` | Browser Agent 用 `observe`/`diff` 摸真实页面，产出结构笔记 | 页面打不开 → HITL |
| `draft` | 按模板骨架写 py，填「怎么走」 | — |
| `lint` | 契约检查（§5.2） | 不通过 → **回 `draft`**（带违规行） |
| `selftest` | 在真浏览器上跑产物，**3 遍**（D5） | 任一遍挂 → `diagnose` |
| `diagnose` | 从运行日志定位「哪个选择器 / 哪一步 / 什么错」 | 回 `draft` 带证据 |
| `deliver` | 写 `forms/sites/<site>.py` + 人话上报 | — |

**HITL（人机介入）**：预算耗尽 / 连续 N 轮无进展 / 危险动作 → LangGraph
`interrupt`，交人。checkpointer 用 Postgres，保证中断后可恢复。

**预算**：agent 比规则折叠贵 1~2 个数量级，所以 `explore` 的轮数与 `draft↔selftest`
的循环次数都要有硬上限。

---

## 七、需要读的信息（agent 产出准确 py 的前提）

**现在缺的不是「眼睛」，是三种更具体的东西：结构化（不是 DOM 树）、
选择器、差分。** 设计规格 §2.2 说「眼睛一直在，只是三处用错了」；2026-09-16
又挖出更深一层：`eval` 穿不透 shadow，且**旅程里根本没记选择器** ——
`py_emitter` 那三个断口就是死在这。

| # | 类别 | 要读到什么 | 为什么必须 |
|---|---|---|---|
| 1 | **可动作元素表** | 每个可见可交互元素的 `{selector 候选, 文本, role, tag, type, label/placeholder, 可见, 被谁遮挡, shadow 层数, frame 路径, bbox}` | py 的每一行都是「对某个元素做什么」 |
| 2 | **选择器质量** | id/name/data-* 是否稳定、文本是否唯一、结构路径兜底；多候选 + 评级 | **py 准不准的头号因素** |
| 3 | **差分与时序** | 动作前后：可见元素集合 / URL / 正文 / 报错；「多久算稳定」 | 漏斗是状态机；「有没有推进」是分支与重试的唯一依据 |
| 4 | **平台/框架指纹** | Salesforce Lightning / FormAssembly / MUI / Typeform / HubSpot… | 决定交互策略；**同平台可直接复用已产出的 py**（sparkydates 复用 thisromance 即此） |
| 5 | **环境指纹** | 代理出口国家、DPR、UA、视口 | 同 URL 不同国家是不同页面 |
| 6 | **表单字段语义** | 每个输入框 → profile 的哪个字段（zip / phone / email…） | homebuddy 实测把手机号填进 ZIP 框（`#zip_content1` = `08034Michael Turner`） |
| 7 | **失败证据** | FMR `formLog` / `formStep`：卡在第几步、什么错 | 「修」入口的关键输入 |

---

## 八、环境隔离

### 8.1 容器化（Dockerfile）

沿用现有约定（`debian:bookworm-slim` + 中文镜像源 + 非 root `appuser`
+ `dumb-init`），**多阶段构建**：

```
stage 1  golang:1.26  →  go build ./cmd/cdp ./cmd/mcp  （Go 1.26.2，module cdp）
stage 2  debian:bookworm-slim + python3 + LangGraph 依赖
         ← COPY --from=stage1 两个二进制到 /usr/local/bin/
```

**为什么容器化**：agent 的依赖（LangGraph / langchain / psycopg）与 worker 的
完全不重叠，混装会重演「两套执行器」那种边界不清。

### 8.2 与现有容器的关系

| 容器 | 归属 | 本次是否动 |
|---|---|---|
| `auto-farm` | 生产 worker，跑 py | **不动** |
| `auto-llm-script` | 实验/生成端 | **不动**（后续单独决定是否退役） |
| `siteforge`（新） | agent 服务，跑在宿主侧 | 新建 |

**容器里的 cdp 副本 5 处保持不变** —— 生产 py 脚本走 CLI，仍需要它们。

---

## 九、迁移与债务清理

| 项 | 动作 |
|---|---|
| `cdpcli`（Go） | 迁入 `siteforge/tools/cdp/`；`cmd/mcp` 新增 |
| `common.py:14` 硬编码 `CDP_PATH=/company/cdpcli/cdp` | 改为环境变量 + 新路径默认值 |
| `tests/` 被 `.gitignore` | **新项目的测试必须进 git**（不重演「机器重建即丢」） |
| cdp 的 `eval` 穿透缺口 | 见 §11 R2 |
| cdpcli 工作树的未提交 WIP(OOPIF) | **迁入前先处理掉**，否则新项目从第一天就带着脏基线 |

---

## 十、验收

**「自测通过」= 在 siteforge 自己的浏览器上连跑 3 遍全过。**

但必须如实标注（沿用 09-15 设计规格 R1）：**工具侧自测通过 ≠ 生产一定过** ——
工具侧浏览器与 worker 的代理出口、指纹、时序都不是一套。

端到端验收（第一版）：拿 **homebuddy**（非 shadow 站，描述现成
`/tmp/desc_hb.txt`）跑通「描述 → observe → draft → lint → selftest 3 遍 → 交付」，
产出一条真能跑的 `forms/sites/homebuddy.py`。

---

## 十一、范围与非目标

**做**：工具层（内核 + MCP 门 + `observe`/`diff`）· py 产出契约与 lint ·
LangGraph 图 · 两个 skill · 容器化 · 债务清理。

**不做**：
- 不改 `auto-farm-skill` 的生产 py 执行路径
- 不做自动上线（py 进 worker 仍是人工 `docker cp`/`scp`）
- 不做 farmer 的 failures 接口本身（是前置依赖，另立项）
- 不重写 `clickthrough` / `json_executor` / `py_emitter`（它们留在原处，
  新项目不依赖它们；是否退役后续单独决定）

---

## 十二、风险与未决

| # | 项 | 状态 |
|---|---|---|
| **R1** | 工具侧自测通过 ≠ 生产通过（环境不同） | 已知；界面如实标注，不假装 |
| **R2** | `cdp eval` 不注入穿透助手 `__cdpQ`（`cmd/eval.go`）→ agent 若用 eval 看页面在 shadow 站上会瞎 | 已定位到行；**要么修，要么 agent 的看只走 `observe`**（后者更符合 D2） |
| **R3** | `observe` 在 shadow 站 / 跨源 iframe 上的覆盖率**尚未验证** —— 需要先做能力探针 | **开工第一件事** |
| **R4** | OpenClaw 的版本/接口细节（skill 格式、MCP host 能力）需在动手前核实 | 未核 |
| **R5** | 选择器稳定性评级的「随机 hash」判据可能误判（有些 hash 其实是稳定的） | 需真站校准 |
| **R6** | agent 成本：比规则折叠贵 1~2 个数量级 | 用预算上限 + 「py 沉淀后走便宜重放」摊薄 |
| **R7** | cdpcli 工作树的未提交 WIP(OOPIF) 归属 | **迁移前必须处理**，见 §9 |
| **R8** | agent 独立窗口用哪个 `bit_id` / `worker_ip` | 未定，需运营/开发指定 |
| **R9** | **`bit.sh update` 是残缺包装**（收 10 个参数只下发 4 个，见 §4.6）—— 拿它下发指纹/代理会静默失效 | 已定位；skill 里必须写明「直接 POST `/browser/update`」，并且 **agent 侧不要调用 `bit.sh update`** |
| **R10** | agent 的 gost 端口未定（见 D9）；`config/gost*.chain` + `gost-watch.sh` 现在只维护 :1080/:1081 | 未定，需指定端口 |
| **R11** | `PROXY_API`（`https://tmk.3tkj.cn/api/get_proxies`）的可用性与配额 | 未核；拉链失败时 agent 必须有降级路径（否则 explore 直接卡死） |

---

## 十三、与既有资产的关系

| 既有资产 | 关系 |
|---|---|
| `cdpcli` | **迁入**，成为工具层 |
| `py_emitter` / `clickthrough` | 不在新链路里；其教训（断口 1/2/3）已写进本规格 §1.1 |
| `forms/sites/*.py` 62 个 | **是产物的目标形式**，也是「同平台复用」的知识来源 |
| `common.py` / `CDPHelper` | 产物仍 import 它；只改 `CDP_PATH` 一处 |
| `auto-farm-skill` 的 `bit.sh` | skill `bit-window` 的原型；其内嵌的坑处理逻辑要一并编码进 skill |
