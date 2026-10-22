# 约会站自动分类与关联（classifier + formConfig/link）

日期：2026-10-22 · 状态：**设计已批准，待实现**

**上游**
- 用户 2026-10-22 14:27：「能不能加个模块就是能自动区分属于哪个的这种约会站每天不少。」
- 用户选择：**C 先 A 后 B**（先做人工前置的分类器，再考虑生产自动路由）、交互 **B**（允许点一次
  Start）、实现方案 **A**（规则指纹表 + 只读普查）。
- 后端 2026-10-22 新增 `POST /api/quest/formConfig/link`（`/company/farmer` 提交
  `df4938d feat: 新增 /api/quest/formConfig/link —— 只做站点↔配置关联（带审计）`）；
  文档见 `/company/farmer/docs/agent-data-api.md` **§18.4**。

**现状（为什么需要它）**
现在判「新 landing 属于哪份脚本」是手工的：开干净窗口 → 跑 `census.js` 数 `data-testid` 族和 hash
→ 对照已知平台。两个已发生的例子：

| 例子 | 手工结论 | 证据 |
|---|---|---|
| `soulmatemeets.com/land/sp/23d247da` | japansdates / config 101 | hash `#c3RlcDE=`（base64 `step1`）、`step-*` 64 / `option-*` 37 / `continue-*` 13、无 `age-*` / `image-button-*` |
| `soulfuldate.com/land/sp/a64f40a4` | japansdates（原先错配到 datewhirl） | 90 个 testid，同族标记 |

后端 `formConfig/sites` 里约会相关的配置组（2026-10-22 读）：

| config | 脚本（锚点实测） | 约会站数 |
|---|---|---|
| 101 | `JapanSDatesFiller`（`japansdates.com`） | 7 |
| 66 | `GoldenAgeSoulsFiller`（`goldenagesouls.com`） | 9 |
| 55 | `JapanSDatesFiller`（`datewhirl.com`，不同版本） | 5 |
| 142 / 144 | `DelachatFiller` | 5 / 2 |
| 93 | `ThisRomanceFiller` | 3 |
| 127 | `JapanSDatesFiller`（`fanlychat.com`，旧版本） | 1 |
| 172 | 站点专用 `Filler`（`agemate.com`） | 1 |

> ⚠️ **101 / 55 / 127 都是 `JapanSDatesFiller`，但是不同版本**（sha `41c74af2` / `ee42ce2b` /
> `aff60b6f`）。新站默认推荐**101**（规范锚点 `japansdates.com`），输出里注明还有 55/127 变体。

---

## 1. 目标与非目标

**目标**
1. 给一个 URL，自动判断它属于哪份约会站脚本族（先覆盖 japansdates / goldenagesouls / delachat），
   并输出**建议的 config**（通过锚点站反查，不写死数字）、**要登记的键**、**证据**。
2. 默认**只读**：不填表、不提交；`unknown` / 歧义时如实说，不猜。
3. `--apply` 时调用 `POST /api/quest/formConfig/link` 把站点挂到建议的 config，并**回读复核**。
4. 为后续「生产自动路由」提供纯函数地基：分类器是 `census -> verdict`，不依赖窗口/网络。

**非目标**
- 不做模型分类；分类是确定性的规则表。
- 不写脚本（只做站点↔配置关联；`formScript/update` 不碰）。
- 不建新 config（后端明确 404；本模块也不该建）。
- 不做生产 `ad-task` 自动路由（那是后续 B 阶段，另开规格）。
- 不覆盖全部平台（先三族；`thisromance` / `agemate` 留到指纹表扩展）。
- 不删除/重绑别的站点（`link` 是加性、只 upsert 这一个站）。

## 2. 架构与文件

```
agent/classify.py          新模块：census JS + 指纹表 + 纯分类器 + 锚点解析 + CLI
agent/fmr.py               + FORM_CONFIG_LINK_PATH / FmrClient.form_config_link()
tests/test_classify.py     新测试：分类 / 锚点 / link / CLI
```

后续（B 阶段，另开规格）：
- `agent/service.py` 增加 `POST /classify` / `POST /classify/link`，console 面板加按钮；
- `ad-task` 在「后端没有这个站」时调用分类器并路由。
**本规格只做 A：`python -m agent.classify`。**

### 2.1 命令

```bash
python -m agent.classify <url> \
  [--ws-url ws://…] [--worker 192.168.1.222 --bit-id …] \
  [--apply] [--config-id N] [--json] [--no-click] [--keep-window]
```

- 不带 `--ws-url`：用 `BIT_WORKER_IP` / `BIT_ID`（或 `--worker/--bit-id`）`fresh_open()` 开**干净窗口**
  （清 cookie/缓存；与生产每单新窗口一致），导航到 `<url>`，跑完关掉（`--keep-window` 保留）。
- 带 `--ws-url`：复用现有窗口，**同样导航到 `<url>`**；跑完不关（窗口归调用方）。
- `--apply`：只在「恰好一个平台命中 + config 解析成功」时写；缺 `--config-id` 且锚点解析失败 → 退出 5。
- `--no-click`：禁用 Start 点击（只看第一屏）。
- `--json`：stdout 只输出一个 JSON 对象（人话走 stderr）。

## 3. Census（只读 + 一次 Start）

### 3.1 `CENSUS_JS`

返回一个 JSON 对象：

```json
{
  "url": "…",
  "title": "…",
  "hash": "#c3RlcDE=",
  "testids": ["step-content-item-Basic", "option-have-fun", "continue-button", "…"],
  "classes": ["terms__button", "step_active", "…"],
  "buttons": ["Start", "Continue", "…"],
  "links": ["Privacy Policy", "…"],
  "inputs": [{"type": "text", "name": "", "placeholder": "Age", "testid": ""}],
  "body_text": "前 500 字（压空白）",
  "start_candidates": [{"text": "Start", "tag": "button"}]
}
```

- `testids`：**全部** `[data-testid]`（穿透 shadow root，复用 `deepAll` 那套；**不按可见性过滤**
  —— 平台签名常常藏在首屏之外，2026-10-22 真站实测：只看可见 testid 会把 japansdates 判成 unknown），
  **去重**；上限 300。
- `classes`：全部元素的 class token（按空白拆；同样**不按可见性过滤**），
  **过滤** `css-*` / `Mui*` / 长度 < 3；上限 200。
- `buttons` / `links`：可见 `button, [role=button], a[href]` 的文字；上限 80。
- `start_candidates`：可见、文字命中
  `^(start|begin|continue|join|see if|qualify|get started|take the quiz|find out|check)\b` 的
  `button/a/[role=button]`；上限 5，带 `tag`。点击时由运行期给第一个候选打
  `data-sf-start="1"`，再 `cdp click '[data-sf-start="1"]'`（census 本身不返回选择器）。
- **不收集**任何输入框的值（只 type/name/placeholder/testid）。

### 3.2 Start 点击（最多一次）

1. 第一轮 census；若 `classify_census` 返回非 `unknown`/非歧义 → 不点。
2. 否则若 `--no-click` → 停。
3. 若 `start_candidates` 为空 → 停（报「没找到可点的开始」）。
4. 选**第一个**候选：用 JS 给它打 `data-sf-start="1"`，`cdp click '[data-sf-start="1"]'`。
5. 轮询「页面变了没有」：比较 census 的 `url + title + testids 排序后的 sha1`，每 0.5s 一次，上限 5s。
6. 变了 → 第二次 census → 重新分类；没变 → 停（报「点了 X，页面没变」）。
7. 永远不填表、不提交；输出里必须写「点了哪个 / 没点」。

## 4. 指纹表与分类

### 4.1 `FINGERPRINTS`（初版）

每条：`platform` / `anchor`（锚点站，用于反查 config）/ `all_of` / `any_of` / `none_of`。
标记类型：`testid_prefix` / `testid_exact` / `class_token` / `hash_b64_prefix` / `text_contains` /
`input_type`。

| 平台 | anchor | all_of | any_of（min） | none_of |
|---|---|---|---|---|
| `japansdates` | `japansdates.com` | `testid_prefix: step-content-item-` | `testid_prefix: option-`、`testid_exact: legal-checkbox`、`testid_exact: continue-button`、`hash_b64_prefix: step`（min 2） | `testid_prefix: image-button-` |
| `goldenagesouls` | `goldenagesouls.com` | `testid_exact: option-man` | `testid_exact: gender-male`、`testid_exact: option-both`、`testid_exact: birth-date`、`class_token: terms__checkbox`、`class_token: reg-form__body`（min 2） | `testid_prefix: step-content-item-` |
| `delachat` | `chattyromance.com/land/sp/81d0a481` | `class_token: terms__button` | `class_token: step_active`、`class_token: terms__checkbox`、`testid_exact: continue-button`（min 1） | `testid_prefix: step-content-item-` |

> ⚠️ **2026-10-22 真站订正**：goldenagesouls 原来按 `age-*` / `image-button-*` 写，实测
> `goldenagesouls.com` 是 `option-man` / `gender-male` / `birth-date` / `terms__checkbox` /
> `reg-form__body` 这套（无 `step-content-item-*`）。delachat 的 `terms__button` 指纹**还没在真站命中过**
> —— 抽到的 config-142 站（chattyromance 81d0a481）其实是 `step-content-item-*` 变体，归 japansdates。
> delachat 先保留为**未验证条目**；`thisromance` / `agemate` 先不覆盖，遇到就 `unknown` + 证据。

### 4.2 分类规则

- `all_of` 必须全中；`none_of` 哪一个中了就**否掉**这一条；`any_of` 命中数 ≥ `min`。
- 命中多个平台 → `ambiguous`，**不自动写**，输出全部候选与证据。
- 一个都不中 → `unknown`，输出 census 证据（testid/class/hash 摘要），提示「该加指纹」。
- 置信度：
  - `high`：`all_of` 全中且 `any_of` ≥ min；
  - `medium`：`all_of` 全中但 `any_of` 为 0（只有在 `min=0` 的平台会出现）；
  - `low` / `unknown`：不满足。
- `hash_b64_prefix`：把 `hash` 去掉 `#` 后做 base64 解码（容错 padding），解出来以给定前缀开头即命中。
  解不出 → 不命中，不猜。

### 4.3 纯函数

```python
def classify_census(census: dict) -> dict:
    """{"platform": str|None, "confidence": "high"|"medium"|"low"|"unknown",
        "matches": [{"platform", "score", "evidence": [...]}], "ambiguous": bool}"""
```

不读全局、不碰网络；测试只喂 fixture。

## 5. 解析 config 与写入

### 5.1 锚点 → config_id

```python
def resolve_config(platform: str, site_rows: list[dict], *, override: int|None = None) -> int
```

- `override` 给了就直接用（`--config-id`）。
- 否则取 `FINGERPRINTS[platform].anchor`，在 `formConfig/sites` 里**精确匹配** `site`；
  找到 → `config_id`；找不到/多于一条 → 抛错（退出 5），不猜。
- `site_rows` 由 `FmrClient.form_config_sites()` 提供（读 token）。

### 5.2 `FmrClient.form_config_link`

```python
FORM_CONFIG_LINK_PATH = "/api/quest/formConfig/link"

def form_config_link(self, site: str, config_id: int, *, operator: str,
                     reason: str = "") -> dict:
    """POST JSON；返回 {"site","config_id","created"}。
    业务码在 body.status（/api/quest/* 的旧约定：HTTP 恒 200）：
    200 = 成功；400 = 参数/形状错；404 = config 不存在；401 = token 错。"""
```

- 走写 token（`configs.agentWriteToken`，现有 `write_token`）。
- 只接受 `application/json`（与 `formScript/update` 同一族）。
- 写前**不**读旧映射；写后由 CLI 回读复核。
- 同一指向重复调用后端不写库、不写审计（`created:false`），CLI 如实打印。

### 5.3 `site_key` 归一化

CLI 输出与写入用的键 = `url` **去协议、去 `?`/`#` 及其后、去末尾 `/`**（例：
`https://soulmatemeets.com/land/sp/23d247da/` → `soulmatemeets.com/land/sp/23d247da`）。
与后端 `formConfig/link` 的归一化**同一套**；回读比对也用这个形式（别拿原文比）。

### 5.4 `--apply` 的次序

1. 分类 → 必须恰好一个平台命中（`ambiguous` 或 `unknown` → 退出 2）。
2. `resolve_config` → 失败退出 5。
3. 打印将发的 payload（site / config_id / operator / reason）。
4. `form_config_link(...)` → body.status 非 200 → 退出 4，原话摆出来。
5. `form_config_sites()` 回读：找 `site` 这一行，`config_id` 必须等于目标、`status` 为 1；
   否则退出 4（**不许只说「写成功了」**）。
6. 打印 `created` + 回读行；退出 0。

## 6. 输出与退出码

### 6.1 人话（示例）

```
URL    https://soulmatemeets.com/land/sp/23d247da
平台   japansdates（high）  → config 101（锚点 japansdates.com）
键     soulmatemeets.com/land/sp/23d247da
证据   testid: step-content-item-* ×23, option-* ×24, legal-checkbox, continue-button
       hash: #c3RlcDE= → step1
动作   未点 Start（第一屏就命中）；只读普查
变体   japansdates 另有 config 55 / 127（不同脚本版本），默认推荐 101
写入   未写（加 --apply 才写）
```

### 6.2 `--json`

```json
{
  "url": "…", "site_key": "…", "platform": "japansdates",
  "confidence": "high", "ambiguous": false,
  "config_id": 101, "anchor": "japansdates.com",
  "evidence": ["testid_prefix: step-content-item-", "…"],
  "clicked_start": false,
  "census": {"testids": ["…"], "hash": "#c3RlcDE=", "title": "…"},
  "linked": null
}
```

`--apply` 后 `linked` 为 `{"created": true, "config_id": 101}`。

### 6.3 退出码

| 码 | 含义 |
|---|---|
| 0 | 分类成功（`--apply` 时还要求写入 + 回读成功） |
| 2 | `unknown` / `ambiguous`（未写） |
| 3 | 窗口 / 导航 / census 失败 |
| 4 | `link` 失败或回读不一致 |
| 5 | config 解析失败（锚点不在 / `--config-id` 非法） |

## 7. 安全与纪律

- **默认只读**：不开写口、不填表、不提交；`--apply` 是显式动作。
- 最多**一次** Start 点击；点击前后都 census；输出里必须说「点了哪个」。
- 不写脚本、不建 config、不重绑其它站点；`formConfig/link` 是加性、只 upsert 一个站。
- `unknown` / 歧义**绝不猜**；宁可让人补指纹。
- 测试用 8100（Bit 192.168.1.222）；生产日常用同一命令，只换 worker 参数。
- operator 字符串格式：`claude (classify: <平台>, <URL>)`；`reason` 带命中证据摘要。

## 8. 测试

新文件 `tests/test_classify.py`：

1. **纯分类器**：
   - japansdates fixture（`step-content-item-*` + `option-*` + `legal-checkbox` + hash `#c3RlcDE=`）
     → `platform=japansdates`, `confidence=high`；
   - goldenagesouls fixture（`image-button-*` + `continue-button`）→ `goldenagesouls`；
   - delachat fixture（`terms__button` + `step_active`）→ `delachat`；
   - 只有 `continue-button` 的普通页 → `unknown`；
   - japansdates + goldenagesouls 标记同时出现 → `ambiguous`，两个候选都在；
   - 含 `image-button-` 的 japansdates 标记 → 被 `none_of` 否掉。
2. **锚点解析**：假 site 列表（`japansdates.com→101` 等）→ 正确 config；锚点缺失 → 抛错；
   `--config-id` 覆盖。
3. **`form_config_link`**：假 transport（monkeypatch `_call` / opener），覆盖
   `status 200 + created true/false`、`400`、`404`、`401`；确认 body 是 JSON、带写 token。
4. **CLI**：注入假 `census_fn`（`classify_url(..., census_fn=...)`）测：
   - 只读命中 → 退出 0 且不调 link；
   - unknown → 退出 2；
   - ambiguous → 退出 2；
   - `--apply` 命中 → 调 link + 回读成功 → 退出 0；
   - `--apply` 回读不一致 → 退出 4。
5. **真站只读抽查**（手工，写进 `docs/probes/`）：
   - `soulmatemeets.com/land/sp/23d247da` → japansdates / 101；
   - `soulfuldate.com/land/sp/5a4f738d` → japansdates / 101；
   - 一个 config 66 的站（如 `callyourdate.com/land/sp/5549e30e`）→ goldenagesouls / 66；
   - 一个 config 142 的站（如 `chattyromance.com/land/sp/81d0a481`）→ delachat / 142；
   - 一个非约会页（如 `www.wildnutrition.com`）→ unknown。
   `--apply` 的真写测试等后端部署后再做。

## 9. 上线次序与前提

1. **先做 A（本规格）**：CLI、只读；`--apply` 代码就位但**不跑真写**（当前 `fmr.3tkj.cn` 的
   `/api/quest/formConfig/link` 还是 **404** —— 本地 `/company/farmer` 已有实现（`df4938d`），
   部署后再验）。
2. 真站只读抽查通过 → 用 `--apply` 处理积压的新 landing（逐条人工确认）。
3. **B 阶段**（另开规格）：service 端点 + console 按钮 + `ad-task` 自动路由；
   B 复用 A 的 `classify_census` 纯函数与 `FINGERPRINTS`，不另写一套判据。

## 10. 未决与风险

- **goldenagesouls / delachat 的标记是初版**：必须真站量过再钉死；表里只放能复现的标记。
- **API 未部署**：live 404；`--apply` 的端到端验证等部署。
- **japansdates 变体**：101 / 55 / 127 都是 `JapanSDatesFiller` 不同版本；默认 101，输出里注明变体；
  若运营要求挂到 55/127，用 `--config-id` 覆盖。
- **Start 点击的覆盖面**：只点一次、只认一组词；点不动就如实报「看不清」，不连环点。
- **窗口寿命**：`fresh_open` 会关旧窗再开；Bit 限流时按现有 `BitWindow` 的退避重试。
- **不覆盖非约会平台**：遇到就 unknown；指纹表加一行即可扩展。
