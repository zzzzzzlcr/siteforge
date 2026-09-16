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

## 二、已确认的设计决策（12 条）

| # | 决策 | 内容 |
|---|---|---|
| **D1** | **项目与边界** | 新项目 `/company/siteforge`。**迁入** `cdpcli`（Go，4644 行）作工具层；**不动** `auto-farm-skill`（生产 py 执行路径）、worker、Bit 窗口机制。**MVP 不引入 OpenClaw**（见 D10） |
| **D2** | **agent 主视角 = `observe`（且必须 CLI 化）** | agent 看页面走一个**结构化**工具，不是 DOM 树、也不是让 agent 自己写 JS。一次调用返回可动作元素表 + 选择器候选 + 平台/环境指纹。**`observe` 必须同时是 CLI 子命令（`cdp observe`）** —— 系统有两个阶段：生成阶段 agent 走 MCP，**运行阶段 py 在 worker 容器里只能调 CLI**。若只做 MCP，「selector 全挂 → 重新 observe」这条回退在运行时根本调不到 |
| **D10** | **OpenClaw 移出 MVP** | MVP 用 **LangGraph(+Agent Server) + MCP + cdp** 即可，4 个节点跑两个框架是纯负担 —— 与「两套执行器」同类病。OpenClaw 留到 **Phase 3** 作运营入口（「修一下 georgiapower」），它接 MCP，届时零改动接入 |
| **D11** | **perception ≠ cognition：语义不进 `observe`** | `observe` 只给**感知**（布局/视觉原始信号），不给**判断**。烘焙 `"intent":"book appointment"` / `"importance":"primary CTA"` 等于又写一套规则（`_trace_to_json` 同类病）。语义由 agent 从原始信号推 |
| **D12** | **视觉是按需工具，不进 `observe` 契约** | `vision.inspect()` 单独一个工具，agent 需要时才调（同名按钮、SPA、shadow/iframe 里 DOM 分不出时）。**2026-09-16 已实测可行**：`deepseek-v4-flash` 看图准确（数矩形 → `2`、认颜色 → `红色`），pro 在 800 token 预算下看图返回空（reasoning 吃满） |
| **D3** | **选择器候选 + 稳定性评级，第一版就做** | 每个元素给多个选择器候选和 `high/medium/low` 评级。**选择器是 py 准不准的头号因素** |
| **D4** | **环境指纹随 py 落盘（契约）** | 产物头部写 `PROVENANCE` 元数据块（代理国家/DPR/UA/视口/平台/自测结果/来源）。同一个 URL 在不同代理国家是**不同的页面** |
| **D5** | **自测 3 遍** | 在真浏览器上连跑 3 遍全过才算「自测通过」；任一遍挂 = 自测未过 + 卡在哪 |
| **D6** | **agent 用自己的 Bit 窗口** | **硬约束**。worker 的 `/task` 自己会 `bit.sh open`；agent 若用同一个 `bit_id`，必然复现「两任务抢同一窗口 → 窗口进程僵死」事故 |
| **D7** | **cdp 出 MCP 门（同内核双出口）** | 不绕：不是包一层壳去 subprocess CLI，而是 Go 内核直接多一个 MCP 入口。传输先 **stdio**，YAGNI |
| **D8** | **两个 skill 给 agent 用** | `bit-window`（窗口生命周期 + 指纹/代理下发）+ `cdp-browser`（页面操作）。**skill 里编码「坑」比编码「用法」值钱** —— 尤其 §4.6 那三个陷阱 |
| **D9** | **agent 用自己的 gost 出口端口** | 与 D6 同构的硬约束。宿主 :1081 归**实验容器**（`gost-watch.sh` 维护）、:1080 归**生产**（单例热换）。agent 若共用，换链会互相踩 —— 实测过：换掉生产正在用的链、或用别的站的链 → 页面打不开只有 3 个元素。agent 单开一个端口 + 独立 gost 实例 |
| **D13** | **两个入口，人的位置不同（修 / 新）** | 「修」的**意图已知**（以前能跑，证据写着卡在哪）→ 人只在**输出闸门**；「新」的**意图只有人知道**（哪条路是业务要的、什么算成功）→ 人还必须在**输入闸门教意图**。**页面能告诉 agent 机制，只有人能告诉 agent 意图** —— 两者缺一不可（§6.1）。Phase 1 用两个 CLI（`siteforge new` / `siteforge fix`），不做 UI |

---

## 三、架构

```
┌──────────────────────────────────────────────────────────────┐
│ OpenClaw   入口 / skill 管理 / 工具网关        ← **Phase 3**    │
│  · 接运营的自然语言请求（「修一下 georgiapower」）              │
│  · skills: bit-window、cdp-browser                            │
└───────────────────────────┬──────────────────────────────────┘
                            │ MCP
┌───────────────────────────▼──────────────────────────────────┐
│ LangGraph   业务流程 / 状态 / 人机介入     ← MVP 从这里开始      │
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
│  + vision.inspect  │            │ + site_memory · correction  │
└─────────┬──────────┘            └────────────────────────────┘
          │ MCP（生成阶段）
┌─────────▼──────────────────────────────────────────────────┐
│ cdp 工具层（Go 内核，迁自 /company/cdpcli）                    │
│   ┌───────────────── internal/ ─────────────────┐           │
│   │ 穿透解析 __cdpQ · 拟人手势 · 表单/下拉/勾选 · 帧 │           │
│   └──────┬──────────────────────────┬───────────┘           │
│          │                          │                       │
│   cmd/cdp（CLI）              cmd/mcp（MCP server）          │
│   生产 py 脚本用               agent 用                      │
│   ↑ 运行阶段 py 只能走这里                                    │
│   （observe 也必须在这里有；见 D2）                            │
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

| 工具 | 作用 | 形态 |
|---|---|---|
| `observe` | **主视角**：结构化页面模型（见 §4.3） | **CLI + MCP 双形态**（D2） |
| `diff` | 动作前后差分：可见元素集合 / URL / 正文 / 报错 → 回答「刚才那一下有没有推进」 | CLI + MCP |
| `vision.inspect` | **按需**视觉：截图给多模态模型（D12）。不是主视角，只在 DOM 分不出时调 | MCP |
| `click` | 拟人点击（穿透 shadow / 跨源 iframe） | CLI + MCP |
| `form` | 填值 / 选下拉 / 勾选（拟人手势） | CLI + MCP |
| `scroll` | 滚动（穿透） | CLI + MCP |
| `goto` | 导航 | CLI + MCP |
| `eval` | 逃生舱：执行 JS（只能用来**读**） | CLI + MCP |
| `window_open` / `window_update` / `window_close` | Bit 窗口生命周期（对应 skill `bit-window`） | MCP（Agent 阶段专用） |

**为什么 `vision.inspect` 不进 `observe`**（D11/D12）：`observe` 给感知、不给判断。
把视觉理解塞进 `observe` 会让它慢慢长成一个专家系统（`if hero: primary CTA`），
就是我们要逃出来的那个坑。视觉单独一个工具，agent 需要时才付出它的代价（token）。

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
      "shadow_depth": 2, "frame_path": ["main"], "bbox": [812, 640, 180, 44],
      // ── 感知信号（D11：只给原始事实，不给「这是主 CTA」这种判断）──
      "region": "hero",            // header / hero / main / aside / footer / nav
      "above_fold": true,
      "relative_size": 1.8,        // 相对同 tag 同级元素的中位面积
      "peer_count": 3,             // 同区域内同类元素个数（同名按钮有几个）
      "z_index": 99999,
      "contrast": "high",          // 前景/背景对比度分档
      "nearby_text": ["Get Started", "Free Quote"]   // 邻近静态文本（帮助判断语境）
    }
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

#### ⚠️ R3 探针实测出的三个实现陷阱（2026-09-16，**都会静默产出错误页面模型**）

探针在 light / 两层嵌套 shadow / 跨源 iframe / 跨源 iframe 套两层 shadow 四档
上验过（结论：**可行**，见 §12 R3）。但第一版实现踩了三个不报错的坑：

| # | 坑 | 症状（实测） | 正确做法 |
|---|---|---|---|
| 1 | **`ShadowRoot` 没有 `innerText`** —— 那是 `HTMLElement` 的属性 | 逐 root 收文本时 shadow root 拿到 `undefined` → shadow 页 `page_text` 只剩 **10 个字符**（修好后 141） | shadow root 要取它**子元素**的 `innerText`/`textContent` |
| 2 | **`document.elementsFromPoint` 不穿透 shadow**（返回的是 host） | 用它判遮挡 → **所有 shadow 元素全被误判为被遮挡**（实测 5/5 假阳性） | 命中的元素若在 el 的**合成树祖先链**上，就不算遮挡 |
| 3 | **`parentElement` 出不了 shadow 边界** | `region` 全部退化成 `body`（分不出 hero/footer） | 走 `getRootNode().host` 的**合成树祖先链** |

这三条与「`success_steps` 15/15」「`_smart_form` 说填好了」是同一类病：
**不报错，只是悄悄错**。实现时必须钉测试。

#### 跨帧：必须逐帧 observe 再合并

同源策略决定**单次 eval 看不见跨源帧的内容**。所以 `observe` 的实现是
「枚举帧 → 逐帧取 → 合并」，每条动作带上 `frame_path`（如 `["main","child"]`）。

> 探针中一度误判「`cdp eval --frame-id` 对跨源帧静默返回主帧」——
> 实际是我 runner 解析帧 id 的 bug。**`cdp eval --frame-id` 对跨源帧正常**。
> 记下来是因为：误判一次就会写出错误的规避代码。

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

### 5.1b target 是**声明式多元描述**，运行时逐级回退

py 里不写死单个选择器，写一个 target 声明（**这是 §7 那 7 类信息的落地形态**）：

```python
TARGET_SCHEDULE = {
    "text": "Schedule Now", "role": "button",
    "near": "hero",                       # 语境约束：优先 hero 区那个
    "selectors": ["#schedule-now",        # 按稳定性排序，逐级回退
                  "button[data-testid=schedule]",
                  "div.hero > button"],
}
```

运行时的回退链（**不是 selector 挂了就结束**）：

```
selector A 失败 → selector B 失败 → 重新 observe 当前页面 → 按 text+role+near 重定位 → 再失败才算失败
```

> **设计后果（D2 的来源）**：最后那一步「重新 observe」发生在 **worker 容器里的 py 中**，
> 它只能调 `cdp` CLI。所以 `observe` 必须是 CLI 子命令，不能只做 MCP 工具。

**为什么不能只靠 selector**：现状 `forms/sites/*.py` 是手写 JS + 硬编码 selector +
`try/except`（实测 `ace.py` 直接 `document.querySelector('input[placeholder*="First"]')`
+ native setter + dispatchEvent —— 正是要禁掉的那种写法）。selector 一改版就挂，
而 text+role+语境 比 CSS 路径稳。

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

### 6.1 两个入口：修 与 新 —— 人的位置不同

**2026-09-16 用户指出：给新站走流程一样需要人教。** 这条推翻了本设计早先的
一个错误推理链：

> 原文：「描述是回忆，会漏会错」→ 让 agent 去看页面
>
> **错在哪**：页面能告诉 agent **机制**，但**只有人能告诉 agent 意图**。

| | **修**（已挂的站） | **新**（没跑过的站） |
|---|---|---|
| 意图 | **已知** —— 它以前能跑，失败证据写着卡在哪 | **只有人知道** —— 哪条路是业务要的、什么算成功 |
| 输入 | URL + `formLog`/`formStep` 证据（+ 旧 py） | URL + **人给的意图** |
| agent 干什么 | 定位为什么坏了、修 | 把人的意图翻译成可执行的走法 |
| **人的位置** | **输出闸门**（审产物） | **输入闸门（教意图）+ 输出闸门** |

**人给的是意图，不是机制：**

- 成功条件（什么算完成）
- 必须走哪条路（若人知道）—— 例：homebuddy 那张长表单有 4 个选项组同时可见，
  业务要哪条，**页面上看不出来**
- 字段语义（哪个框填什么；profile 里没有的新字段）
- 人知道但页面看不出来的坑（例：「这站的 cookie 横幅会盖住按钮」）

**agent 摸的是机制**：页面上有哪些可动作元素、选择器稳不稳、点了有没有推进。
**「修」还有一个新站没有的优势：旧 py 在手。** 所以修站的 `explore` 应当
**以旧 py 的行走路线为起点** —— 拿旧路线在当前页面上跑一遍，看**哪一步开始对不上**
（选择器失效？页面多了一步？按钮改名了？），而不是白纸探索。这比从零摸准得多，
也便宜得多。

**取证链已就绪**（2026-09-16 核实，见 §11）：
`① GET /api/quest/formLog?site=&status=failed` → 挑站拿 `task_id`
→ `formStep`/`formLog` 拉证据 → 进 `explore`。客户端现成，在
`fail-script/src/diagnosis/datasource.py`。

⚠️ **这不是「加需求」，是现有验收标准跑不通**：MVP 的验收案例 **homebuddy 本身
就是新站**（`/tmp/desc_hb.txt`）。不含输入闸门，§10 那条端到端验收根本走不完。

**第三个入口（Phase 2，用户 2026-09-16 提出）：JSON 修复。**

产线里 py 与 JSON 并存（`forms/sites/*.py` 63 个 + `json-configs/*.json` **69 个**），
两类都会挂。JSON 修复分三层，**第一层已经存在**：

| 层 | 谁做 | 现状 |
|---|---|---|
| 1. 结构/类型/格式 | `form_executor/auto_fixer.py` 约 10 条确定性规则（`_fix_field_types` / `_fix_field_placeholder` / `_fix_button_eval` / `_remove_form_id` / `_fix_success` / `_fix_loop_until` / `_fix_missing_wait`…），0 LLM。**它跑在产出期** —— `json_pipeline.py:286` 在 LLM 生成完、浏览器验证前立刻过一遍，是**生成物的清洗器**，不是运行时修复器 | ✅ **已有，但只覆盖纯规则能判的那层** |
| 2. **选择器失效**（页面改版） | 重跑 → 看哪步开始对不上 → 找新选择器 → 改 JSON | ❌ 没有 ← **siteforge 该补这层** |
| 3. 表达力不够（分支 / 跨轮状态 / 换策略） | 升级成 py | 见 §1.1 |

**第 2 层恰好不需要 agent 写代码** —— 只要 `observe`/`diff` 找新选择器、改 JSON 的
一个字段、跑一遍验证。所以它**比产 py 便宜**：

- 产物是**数据**不是代码 → 契约检查就是 schema 校验，不需要 §5.2 那套 lint
- 改动**局部**（一个选择器字段）→ 人审比审 py 容易得多
- **旧 JSON 在手** —— 同「修站有旧 py」那个优势
- `auto_fixer` 已把机械的那一半做掉

**与 `auto_fixer` 的关系（两者是串行，不是替代）：**

```
LLM/agent 产出 JSON → auto_fixer（纯规则清洗）→ 【siteforge 补的那层】→ 验证 → 人审 → 上线
```

`auto_fixer` 覆盖的规则，siteforge **不要重写**；siteforge 补的是**规则判不了、
必须看着页面才知道**的那层（选择器失效、页面多了一步、按钮改名）。

**而且闭环在这里合上**：siteforge 每次人审通过的修法，**反复出现若干次后就该
沉淀成 `auto_fixer` 的一条新规则**。这是 §6.5「让人的投入累积」的具体形态 ——
人工纠正不该永远停在 agent 那里，**确定性规则才是它的终点**：规则跑起来不要钱、
不会「自信地错」（R0）。

**升级判据**（不要重新发明，用 §1.1 已有的边界）：
修这个 JSON 若需要**加一个 JSON 表达不了的原语**（分支 / 跨轮状态 / 换策略），
就放弃 JSON、升级成 py。

**Phase 1 的最小形态**：不做 UI，两个 CLI

```
siteforge new --url <站点URL> --goal "<成功条件>" \
              --must-path "<人知道必须走的那条路，可空>" \
              --notes "<人知道但页面看不出来的事，可空>"
siteforge fix --site <站点> --evidence <formLog/formStep 引用>
```

两条路进**同一张图**，区别只在 `intake` 收到什么、以及 `review` 的门槛：
新站的 review 要额外确认一条 —— **「agent 理解的意图 == 人给的意图」**
（这是最贵也最容易错的一步，见 §6.2 的 R0）。

```
intake → explore → draft → lint → selftest → review(人) → deliver
                    ↑        │        │         │            │
                    └────────┴────────┘         │            └─ 入库
                          diagnose              └─ Correction Event（§13.3）
```

| 节点 | 做什么 | 失败去向 |
|---|---|---|
| `intake` | 收站点 URL + 失败证据（FMR `formLog`/`formStep`）或运营描述。**开工前提层（§4.6）在这里做**：拉对应国家的链（D9 的独立 gost 端口）→ POST `/browser/update` 下发指纹 → `bit.sh open` 拿 ws_url | 链拉不到 / 窗口不可用 → HITL（R11） |
| `explore` | Browser Agent 用 `observe`/`diff` 摸真实页面，产出结构笔记 | 页面打不开 → HITL |
| `draft` | 按模板骨架写 py，填「怎么走」 | — |
| `lint` | 契约检查（§5.2） | 不通过 → **回 `draft`**（带违规行） |
| `selftest` | 在真浏览器上跑**扰动序列**（§10，不是同环境 3 遍） | 任一遍挂 → `diagnose` |
| **`review`** | **人审闸门**（见 §6.2）。产出 Correction Event | 人否 → 回 `draft` 带人的纠正 |
| `diagnose` | 从运行日志定位「哪个选择器 / 哪一步 / 什么错」 | 回 `draft` 带证据 |
| `deliver` | 写 `forms/sites/<site>.py` + 落到 C15 的注册处 + 人话上报 | — |

### 6.2 人的位置：不是「卡住时」，是「每次交付」

**这是本设计最重要的一条，2026-09-16 被实证修正。**

原设计把 HITL 放在「预算耗尽 / 连续 N 轮无进展」—— 即**只在 agent 知道自己卡住时**
才找人。当天事实验翻了它：90 分钟内用户纠正了 9 次，**没有一次是「卡住」**，
9 次全是「**agent 自信地错**」。实例（全部由用户发现，agent 自己无一察觉）：

| agent 说的 | 实际 |
|---|---|
| 「cdp 填不了 shadow DOM」 | 错 —— `cdp form` 填得进，瞎的只是 `cdp eval` |
| 手搓穿透 `qsa()` | 违背「别各自重写一套」原则，且未自知 |
| 规格漏掉窗口/代理前提层 | 用户点出才有 §4.6 |
| 把目标说成「agent 系统」 | 要的是**产出 py**，agent 只是手段 |
| 没验「产物现有系统能不能直接用」 | 用户问了才挖出 C15（计划漏了路由表） |
| 「MVP 触发不需要人」 | 过度声称 |

**不卡住的错，await-and-see 式的 HITL 一次都抓不到。** 所以人的位置必须在
**输出闸门**上：

```
selftest 通过 ≠ 可以交付
自测通过 → 【人审】 → 上线
```

**人的角色不是救火，是训练数据的来源。** 上表那 9 条，每一条都是可复用的事实
（CDP form 能穿 shadow / 动页面优先走 cdp 命令 / DPR 字段名是 `devicePixelRatio` /
`bit.sh update` 是残缺包装…）。它们现在只活在人的脑子里。**进了系统，下一轮就不必
再纠正同一条** —— 这才是「运营教 AI」的真实含义：不是不需要人，是**让人的投入累积
而不是每次蒸发**。

### 6.3 抓「自信地错」的信号（不靠 agent 自报）

| 信号 | 为什么可疑 |
|---|---|
| **扰动测试结果不稳定**（5 遍过 3 遍） | 比「全过」**更**可疑 —— 全过可能是同环境假通过；不稳定说明有未覆盖的条件依赖 |
| **lint 反复打回同一处** | agent 在同一处反复犯同类错 → 它的页面模型有问题 |
| **observe 出来的选择器 `stability` 全是 `low`** | 它对页面的理解建立在易变结构上 |
| **与 `site_memory` 里同平台既有经验冲突** | 例：模型打算用 `eval` 填，而平台经验写着「必须用 `form`」 |
| **`diagnose` 连续两轮归因不同** | 它没定位到真因，在猜 |

任一命中 → **提前进 `review`**，不等预算耗尽。

### 6.4 人审审什么（不逐行读代码）

逐行读代码会让人审流于形式 —— 人不是编译器。待审的是三样**人能快速判断**的东西：

1. **observe 快照** —— agent 看到的页面，以及它据此做的判断
2. **行走路线** —— py 里每步的「状态 → 动作 → 推进判据」（读这个，不读代码）
3. **扰动测试的逐遍结果** —— 哪遍挂、挂在哪

产出：**Correction Event**（§13.3）。

### 6.5 correction 是一等公民，不是埋点

Correction 不是「顺手记一下」，**它就是产品本体**（§13.3）。理由：会操作网页的
agent 遍地都是，**「运营教 AI 且教了有用」才是护城河**。因此：

- `review` 是人审闸门，不是可跳过的步骤（§6.2）
- 每条 Correction 必须能回答「下次遇到同类页面，应该怎么做」
- **`site_memory` 与 Correction 的检索要在 `draft` 之前发生**，不是事后审计

**预算**：agent 比规则折叠贵 1~2 个数量级，所以 `explore` 的轮数与
`draft↔selftest` 的循环次数都要有硬上限；**但预算耗尽不是唯一的 HITL 入口**（§6.3）。

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

## 十、验收：扰动测试，不是「跑 3 遍」

**同一环境连跑 3 遍容易假通过** —— 它证明的是「同一个条件下能重复」，
而生产失败几乎都来自条件变化。所以自测是**扰动序列**：

| Run | 扰动 | 打的是什么 |
|---|---|---|
| 1 | 正常 | 基线 |
| 2 | **刷新页面后重跑** | 状态残留 / 首次加载假设 |
| 3 | **注入延迟**（每步 +2s） | 时序竞争 / 未等待就点 |
| 4 | **换 viewport**（如 1280×800 → 1024×768） | 折叠/遮挡/坐标假设 |
| 5 | **换代理国家** | 地区内容差异（R1 的主要来源） |

任一遍挂 → 自测未过 + 卡在哪。**每遍淘汰的是不同类失败**，这才是「准确」的证据。

仍必须如实标注（沿用 09-15 设计规格 R1）：**工具侧自测通过 ≠ 生产一定过** ——
工具侧浏览器与 worker 的代理出口、指纹、时序都不是一套。扰动测试缩小这个差，
但不消除它。

**端到端验收（Phase 1）**：拿 **homebuddy**（非 shadow 站，描述现成
`/tmp/desc_hb.txt`）跑通「描述 → observe → draft → lint → 扰动自测 → 交付」，
产出一条真能跑的 `forms/sites/homebuddy.py`。

---

## 十一、范围与非目标

**MVP（Phase 1）做**：两个入口 CLI（`siteforge new` / `siteforge fix`）· 工具层（内核 + CLI/MCP 双门 + `observe`/`diff`）·
py 产出契约与 lint · 扰动自测 · LangGraph 图 · `site_memory`/`correction` 埋点 ·
容器化 · 债务清理。详见 §13.1。

**不做**：
- **不引入 OpenClaw**（D10，Phase 3 再说）
- 不改 `auto-farm-skill` 的生产 py 执行路径
- 不做自动上线（py 进 worker 仍是人工 `docker cp`/`scp`）
- ~~不做 farmer 的 failures 接口本身（是前置依赖，另立项）~~ → **该结论已过时**：
  `GET /api/quest/formLog?site=&status=failed&since=&limit=` **2026-09-10 已建**
  （farmer 侧还做了「失败 >10 次/日 → 停用 + 钉钉告警」），
  `fail-script/src/diagnosis/datasource.py` 已有带 `X-Api-Token` 的客户端。
  2026-09-16 实测 `formStep`/`formLog` 返回 401（接口活着、要鉴权）而非 404。
  **「修」入口的取证能力已就绪，不要重复建**
- 不重写 `clickthrough` / `json_executor` / `py_emitter`（它们留在原处，
  新项目不依赖它们；是否退役后续单独决定）
- 不做完整知识图谱（§13.2 只埋点）

---

## 十二、风险与未决

| # | 项 | 状态 |
|---|---|---|
| **R0** | **agent「自信地错」—— 本设计最大的风险** | **2026-09-16 实证**：90 分钟内用户纠正 9 次，**没有一次是「卡住」，9 次全是自信地错**（§6.2 有实例表）。原设计的 HITL（预算耗尽/连续无进展）对此**零防御**。缓解见 §6.1–6.4：**新站加输入闸门（人教意图）** + 人的位置移到输出闸门 + 五类「自信地错」信号 + 人审不读代码只读快照/路线/扰动结果 |
| **R1** | 工具侧自测通过 ≠ 生产通过（环境不同） | 已知；界面如实标注，不假装 |
| **R2** | `cdp eval` 不注入穿透助手 `__cdpQ`（`cmd/eval.go`）→ agent 若用 eval 看页面在 shadow 站上会瞎 | 已定位到行；**要么修，要么 agent 的看只走 `observe`**（后者更符合 D2） |
| **R3** | `observe` 在 shadow 站 / 跨源 iframe 上的覆盖率 | ✅ **2026-09-16 探针通过**：light / 两层嵌套 shadow / 跨源 iframe / 跨源 iframe 套两层 shadow **四档全部取齐**（actions / fields / option_groups / region / occluded_by / shadow_depth 全可用）。同时挖出三个会静默出错的实现陷阱，见 §4.3。探针原型存 `docs/probes/2026-09-16-observe-r3/`（**探针产物，非生产代码**） |
| **R4** | OpenClaw 的版本/接口细节（skill 格式、MCP host 能力）需在动手前核实 | 未核 |
| **R5** | 选择器稳定性评级的「随机 hash」判据可能误判（有些 hash 其实是稳定的） | 需真站校准 |
| **R6** | agent 成本：比规则折叠贵 1~2 个数量级 | 用预算上限 + 「py 沉淀后走便宜重放」摊薄 |
| **R7** | cdpcli 工作树的未提交 WIP(OOPIF) 归属 | **迁移前必须处理**，见 §9 |
| **R8** | agent 独立窗口用哪个 `bit_id` / `worker_ip` | 未定，需运营/开发指定 |
| **R9** | **`bit.sh update` 是残缺包装**（收 10 个参数只下发 4 个，见 §4.6）—— 拿它下发指纹/代理会静默失效 | 已定位；skill 里必须写明「直接 POST `/browser/update`」，并且 **agent 侧不要调用 `bit.sh update`** |
| **R10** | agent 的 gost 端口未定（见 D9）；`config/gost*.chain` + `gost-watch.sh` 现在只维护 :1080/:1081 | 未定，需指定端口 |
| **R11** | `PROXY_API`（`https://tmk.3tkj.cn/api/get_proxies`）的可用性与配额 | 未核；拉链失败时 agent 必须有降级路径（否则 explore 直接卡死） |
| **R12** | 视觉能力 | ✅ **2026-09-16 已实测**：`deepseek-v4-flash` 看图准确（数矩形→`2`、认颜色→`红色`）。`pro` 在 800 token 预算下看图返回空（reasoning 1432 字符吃满），纯文本正常 —— 视觉走 flash，pro 要调预算或不用 |
| **R13** | Phase 1 的 correction 记录路径 | ✅ **已定（方案 B）**：最小 CLI `siteforge correct`，触发点是 selftest 失败后工程师手工改 py 那一刻。见 §13.3 |
| **R14** | 扰动测试里「换代理国家」那遍要重新拉链 + 重启 gost，单遍成本高 | 已知；可在 Phase 1 先跑 Run1–4，代理扰动作为可选 |
| **R15** | `observe` 的 `relative_size` / `contrast` / `region` 计算依赖布局，**在 shadow/iframe 里是否可靠未验** | 归入 R3 的能力探针一起验 |

---

## 十三、分期与埋点

### 14.1 分期

| 期 | 做什么 | 交付判据 |
|---|---|---|
| **Phase 1（MVP）** | **两个入口 CLI（新站教意图 / 修站带证据）** · `observe`（CLI+MCP）· `draft` · `lint` · **扰动自测** · **`review` 人审闸门** · LangGraph 图 · **Correction 采集（一等公民）** · site_memory 埋点 | homebuddy 出一条**经人审通过**的 py |
| **Phase 2** | HITL UI（人工选正确元素，把 §6.4 的三样做成界面）· Site Memory 消费（平台经验复用）· **JSON 修复入口（§6.1 第三入口）** | 人工修正进得去、**同类站复用得上**、**JSON 挂掉的站也能修** |
| **Phase 3** | OpenClaw 入口（运营自然语言提单）· 自动运营交互 · 呈现接已有界面（§6 呈现缺口，见 R16） | 运营自助 |

**Phase 1 的「人审」不是 UI，是流程**：产物 + observe 快照 + 扰动结果落到一个
固定位置，人（开发/运营）看过才 `docker cp` 上线。**不为了自动化而跳过这一关** ——
R0 说的就是跳过它的代价。

**MVP 不引入 OpenClaw**（D10）—— 4 个节点跑两个框架是纯负担。

### 14.2 `site_memory`（Phase 1 只埋点，不做知识图谱）

```jsonc
{ "site": "www.georgiapower.com",
  "platform": "salesforce-lightning",
  "successful_action": { "text": "Schedule Now", "role": "button", "region": "hero",
                         "selector_used": "#schedule-now", "fallback_level": 0 },
  "failed_action":     { "text": "Schedule Now", "reason": "occluded_by:#onetrust-banner" },
  "selector_pattern":  { "id_stable": false, "text_unique": true } }
```

**为什么 Phase 1 就开始记**：整套方案的经济学是「agent 贵一次、py 便宜一万次」，
**这只在 py 真泛化时成立**。若每个站、每次改版都要重跑 agent，成本模型就崩 ——
而那正是现在 62 个脚本各写各的、改版就挂的现状。所以 Site Memory 不是锦上添花，
**它是成本模型的一部分**。而记忆要有价值，必须**从第一天开始积累**，不能等 Phase 2。

### 14.3 Correction Event（Phase 1 定 schema + 最小记录路径）

```jsonc
{ "task": "fill form",
  "ai_action":       { "click": "#submit" },
  "human_correction":{ "click": "#continue" },
  "context": { "url": "...", "dom_snapshot_ref": "...", "screenshot_ref": "...",
               "observe_ref": "..." },
  "diff": { "text": "Submit→Continue", "region": "main→main", "selector": "#submit→#continue" } }
```

**差异化在这里**：会操作网页的 agent 遍地都是（OpenAI / Anthropic / 各种
Browser Agent），**「运营教 AI」才是这套系统的护城河**。所以修正数据要从第一版
就结构化落库。

**Phase 1 的记录路径（已定，方案 B）**：Phase 1 没有 HITL UI，所以不做页面，
只加一条**最小 CLI**：

```
siteforge correct --site <site> --from-observe <ref> \
                  --ai-action '{"click":"#submit"}' \
                  --human-action '{"click":"#continue"}'
```

触发点是 **selftest 失败后工程师手工改 py** 的那一刻 —— 把
`(observe 快照, ai_action, 人工改法)` 落库。**从第一天就有真数据**，
而不是等 Phase 2 的 UI（那样 schema 会空转一整期，就是「埋点变摆设」）。

## 十四、与既有资产的关系

| 既有资产 | 关系 |
|---|---|
| `cdpcli` | **迁入**，成为工具层 |
| `py_emitter` / `clickthrough` | 不在新链路里；其教训（断口 1/2/3）已写进本规格 §1.1 |
| `forms/sites/*.py` 62 个 | **是产物的目标形式**，也是「同平台复用」的知识来源 |
| `common.py` / `CDPHelper` | 产物仍 import 它；只改 `CDP_PATH` 一处 |
| `auto-farm-skill` 的 `bit.sh` | skill `bit-window` 的原型；其内嵌的坑处理逻辑要一并编码进 skill |
