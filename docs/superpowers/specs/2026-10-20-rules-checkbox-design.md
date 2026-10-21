# 生成 py 时的「规则勾选」：手机号 / 生日 / 密码

日期：2026-10-20 · 状态：**已实现**；2026-10-21 手机号规则从 `02/07` 勾选改为**前缀输入框**（例 `04` / `0207`，总位数补到 10 位）

**上游**
- 用户 2026-10-20 18:35：「能不能做成一个规则？让运营在生成 py 的时候勾选规则，比如手机号以 02 或 07 开头；勾了就按我们的规则随机，而不是走资料。」
- 用户 2026-10-20 18:37：「生日格式要可以描述吧？或者说探路的时候能不能知道？」
- 已有的 `phone_area_code` 先例（`RunRequest` / `state.SiteState` / `graph._draft` 里那段
  `_area`）——但它只对**循环型**产物生效；表型产物（`template.py`）不读 `prefix`。
- 已手工处理过的两单：`rebates.com.au/guides/battery-rebate`（手机 `04`+8 位随机）、
  `www.belong.com.au/go/mobile`（DOB 随机 `DD/MM/YYYY` 18–68、密码强随机）。
  这两单的改法都还停在**产物/模板**里，运营在面板上没有入口。

> ⚠️ 本文件按**符号名**指路；行号会随工作区未提交改动漂移。

---

## 1. 这件事治什么

今天「这一单的手机号/生日/密码不要用资料，按我们的规则现编」有三条路，都不适合运营：

| 做法 | 问题 |
|---|---|
| 手改产物再推 FMR | 每站一次；重出即丢；运营不会改 py |
| 在「开工前说一句」里写自然语言 | 模型可能不听；没有「绝不取资料」的硬保证 |
| `phone_area_code` | 只有循环型骨架读；表型产物静默无效；只覆盖手机号 |

**目标形状**：运营在「开一趟新的」面板勾选规则 → `/run` 带结构化 `rules` → 图在模型写完之后
**确定性地**把规则套到 `FILLS` 上 → 产物自带规则（生产不需要额外传参）→ 运行时先看规则、
再看资料。**勾了就完全不看资料**；没勾的字段保持现状。

## 2. 目标与非目标

**目标**
1. 面板新增「规则」区：手机号前缀输入框（例 `04`/`0207`）/ 生日随机 / 密码强随机；
2. 规则按趟生效、烤进产物；`FILLS` 与 `PROVENANCE` 里看得见；
3. 勾选 = **强制现编**，不取 `form_data`；同一规则命中多个字段（phone / confirm phone）用**同一个值**；
4. 生日格式：**自动识别 + 手填覆盖**；识别不出且没手填 ⇒ 生成期明确停下，不静默按某个格式编；
5. 表型（`template.py`）与循环型（`template_loop.py`）两套骨架都支持；
6. 现有 `phone_area_code` 保留兼容；无 `rules` 时行为**逐字节不变**。

**非目标**
- 不做运行时规则参数（规则是生成期烤进产物的；FMR 不需要知道规则实现）。
- 不做规则 DSL/任意脚本；第一批只有这四类。
- 不做「按站点记忆上次勾选」；规则是**按趟**的。
- 不把资料里的日期/密码自动转换（规则勾了就是现编；没勾就维持现有资料/兜底逻辑）。
- 不改 FMR 推送、路由表（C15）与生产执行器。

## 3. 规则契约

### 3.1 规则 ID 与语义

| 规则 | 产物里的 `rule` | 生成 | 命中哪些格 |
|---|---|---|---|
| 手机号前缀（输入框） | `phone_prefix`，`prefixes` | 前缀 + 随机数字补到 **10 位**（例 `04` + 8、`0207` + 6） | name/kind/source/label 含 `phone`/`tel`/`mobile` |
| 生日随机 | `dob_random`，`date_format` | 18–68 岁，按格式输出 | 含 `dob`/`birth`/`date of birth`/`birthday` |
| 密码强随机 | `password_strong` | ≥8 位，含大写/小写/数字/`!@#$%^&*` 之一 | 含 `password` |

- 手机号前缀可填一个或多个（空格/逗号/斜杠分隔）；多个时**每趟随机挑一个前缀**；同一趟里所有 phone 字段用同一个号。
- 生日/密码同理：同一规则组的所有字段共用一个生成值。
- 勾了但**没有任何字段命中** ⇒ 闸上明确写「规则 X 没找到对应字段」，不假装做了。

### 3.2 `/run` 载荷

```json
"rules": {
  "phone_prefixes": ["04", "0207"],
  "dob": { "random": true, "format": "DD/MM/YYYY" },
  "password": "strong"
}
```

- 三块都可省略；整格省略 = 老行为。
- `phone_prefixes`：每个前缀得是 **0 开头、2–10 位数字**（例 `04`/`0207`）；重复去掉；空数组视为未填。
- `dob.format`：可空；空 = 按页面识别。非空时只允许日期 token 组合（见 §4.4）。
- `password`：只允许 `"strong"`。
- **未知 key / 非法值 ⇒ `/run` 400**，把人话摆出来；不静默忽略。

## 4. 设计

### 4.1 面板（`agent/console.html`）

在「开一趟新的」表单里加一个 `规则` 区（放在「这是哪种活」之后、「开工前先说一句」之前）：

- 一个输入框：`手机号前缀（可空；例 04 / 0207，多个用空格/逗号/斜杠分隔）`
- 勾选框：`生日随机（18–68 岁）`、`密码强随机（8+ 大小写数字特殊字符）`
- 勾了生日时显示/启用一格 `生日格式（可空 = 按页面识别）`，placeholder `DD/MM/YYYY`。
- 文案说清：
  - 填了/勾了就**完全不看任务资料**；
  - 手机号前缀多个时每趟随机一个，所有 phone 字段同一个号，总位数补到 10 位；
  - 生日格式空着就按探路记下的页面格式。
- `runPayload()` 只在**至少填/勾了一项**时加 `rules`；一项没填/勾 ⇒ 正文里没有这个键
  （与「没填时逐字节不变」同一条纪律）。
- 面板不校验格式；服务在门口校验并回人话。

### 4.2 服务与状态

- `RunRequest.rules: Optional[dict]`（`agent/service.py`）。
- `_payload` 白名单加 `"rules"`（否则服务算出来了、图收不到——`fix_py` 当年就是这么无声消失的）。
- `state.SiteState.rules: Optional[dict]`（langgraph 按字段名过滤，必须声明）。
- `brief`/闸上 facts 加「规则」一行；`PROVENANCE` 加 `rules`（生效后的那一份）。
- 现有 `phone_area_code` 保留：
  - `rules.phone_prefixes` 有值 ⇒ 以 `rules` 为准；
  - 没有 ⇒ 继续按 `phone_area_code` 的旧路径；
  - 两者都不给 ⇒ 行为与今天逐字节相同。

### 4.3 图：规则在哪儿套

位置：`graph._draft` 里，`deps.write(spec, feedback)` **之后**、render/lint 之前
（与现有 `phone_area_code` 那段同一个位置；模型交回整张 `fills` 也盖不掉）。

新增一个共用函数（建议名 `_apply_rules(spec, state)`），做四件事：

1. 解析 `state["rules"]`（或 `phone_area_code` 的兼容映射）；
2. 找命中字段：
   - phone：与现有 `phone_area_code` 块/`template_loop._is_phone` **同一条判据**
     （name/kind 含 `phone`/`tel`/`mobile`；再加上 source/label 同字样的兜底）；
   - dob：name/kind/source/label 含 `dob`/`birth`/`date of birth`/`birthday`；
   - password：含 `password`；
3. 写 `FILLS`：
   - phone：`rule="phone_prefix"`、`prefixes=[…]`；
   - dob：`rule="dob_random"`、`date_format="<解析结果>"`；
   - password：`rule="password_strong"`；
   （规则**优先于** `source`/`fallback`；运行时先看 `rule`。）
4. 产出 `applied` / `unmatched` / `format_source`，写进 `out` 与闸上 facts；
   `unmatched` 只警告，不停这一趟。

**生日格式解析（§4.4）失败** ⇒ 这一趟**停在生成期**：新增
`end_reason=rule_needs_format`（`state.END_RULE_NEEDS_FORMAT`），面板按这一类展示并给出
「在面板哪一格填」的人话。**不许**默认 `DD/MM/YYYY` 继续跑。

### 4.4 生日格式：自动识别 + 手填覆盖

解析顺序（从高到低）：
1. 运营手填 `rules.dob.format`；
2. `FILLS[*].target.placeholder`；
3. `target.label` / `FILLS[*].label`；
4. `target.hint`；
5. 都没有 ⇒ `rule_needs_format`。

- 支持的 token：`D`/`DD`（日）、`M`/`MM`（月）、`YYYY`/`YY`（年）；
  分隔符支持 `/`、`-`、`.`；例子：`DD/MM/YYYY`、`MM/DD/YYYY`、`YYYY-MM-DD`、`D/M/YYYY`。
- 记录 `date_format` 与 `format_source`（operator / placeholder / label / hint）。
- 运行时按 `date_format` 输出；带 `D`/`M` 的不补零，带 `DD`/`MM` 的补零。
- 识别不出、运营也没填 ⇒ 停；人话里给出一个例子（`DD/MM/YYYY`）和「在面板哪一格填」。
- 不把 `format` 当成「自动转换资料」：它只影响**规则现编**的输出；没勾生日规则时这一格不生效。

### 4.5 运行时（两套骨架）

**表型 `agent/template.py`**
- `Filler.__init__` 加 `self._rule_values = {}`。
- `_fill_value(fill, step)` 开头先看 `fill.get("rule")`：
  - 有 ⇒ `return self._rule_value(fill)`，**完全不看 `form_data`**；
  - 没有 ⇒ 走现有 source/alias/shaped/fallback 链，一字不改。
- 新增 `_rule_value(fill)`：
  - `phone_prefix`：从 `fill["prefixes"]` 随机挑一个；`前缀 + 随机数字补到 10 位`；
  - `dob_random`：生成 18–68 岁的日期，按 `fill["date_format"]` 格式化；
  - `password_strong`：生成满足 `^(?=.*[A-Z])(?=.*[a-z])(?=.*[0-9])(?=.*[!@#$%^&*]).{8,}$` 的值；
  - 按 `rule` 组缓存：`self._rule_values[rule]` 命中直接返回 ⇒ phone/confirm 同值；
  - `_value_from = "规则「…」现编"`；不记录值本身。
- `_random_value` 保留现有 `dob`/`password`；新增只用规则调用的分支或私有函数
  （`_rule_dob` / `_rule_password`），避免影响没勾规则的站。

**循环型 `agent/template_loop.py`**
- `value_for` 在现有 phone/`prefix` 分支之前加同样的 `spec["rule"]` 检查；
- `_rule_value` 与表型同一套算法。两套骨架不能 import 彼此 ⇒ **两份实现、同一套判据**；
  `tests/test_template.py` 与 `tests/test_loop_template.py` 各钉一遍（与现有
  `phone_area_code` 的 `_is_phone`/`prefix` 两处同形的做法一致）。
- 现有 `prefix`（`phone_area_code`）路径保留：没有 `rule` 时照旧。

### 4.6 产物与可见性

- `FILLS[*]` 多三格：`rule` / `prefixes` / `date_format`（按规则需要）。
- `PROVENANCE["rules"]`：
  ```json
  {"phone_prefixes": ["04","0207"],
   "dob": {"random": true, "format": "DD/MM/YYYY", "format_source": "placeholder"},
   "password": "strong",
   "applied": ["phone:04","phone:0207","dob_random","password_strong"],
   "unmatched": []}
  ```
- 闸上 facts 加「规则」与「没命中」两行；运营能一眼看到这版烤了什么。
- `PROVENANCE.rules` 记的是**生效后的那一份**（含 `format_source`/`applied`/`unmatched`），
  不是 `/run` 的原始请求；闸上请求与生效结果都能看到。
- 产物仍然自包含：生产按接口下载就能跑，不需要额外参数。

### 4.7 错误与边界

| 情形 | 处理 |
|---|---|
| `rules` 未知 key / 非法值 | `/run` 400，列出允许值 |
| `dob` 勾了，格式识别不出且没手填 | 停在生成期（`rule_needs_format`），不产 py |
| 规则勾了但一个字段都没命中 | 闸上警告；这一趟照常继续（可能是这站本来就没有该字段） |
| 手机号前缀多个 | 每趟随机一个；同趟同值；总位数补到 10 位 |
| phone/confirm 两个字段 | 同一规则组缓存 ⇒ 同值 |
| 没勾规则 | 与今天逐字节相同；`phone_area_code` 旧路径可用 |
| 生成的密码 | 满足站点规则；不写进日志/闸上（只报来源） |
| 生成的生日 | 18–68；按 `date_format` 输出；不写进日志/闸上 |

## 5. 测试

**单元**
- `tests/test_service.py`：`RunRequest.rules` 接受合法对象；未知 key/值 400；`_payload` 带 `rules`。
- `tests/test_graph.py`：`_apply_rules` 命中 phone/dob/password；同组同值；无命中警告；
  `rule_needs_format`；`phone_area_code` 兼容；build/fix 都走。
- `tests/test_template.py`：`_rule_value` 的 phone（04/0207 补到 10 位、同值缓存）、dob（各格式 + 年龄 18–68）、
  password（站点正则）；`_fill_value` 有规则时**不读** `form_data`。
- `tests/test_loop_template.py`：循环型 `value_for` 同样行为；现有 `prefix` 不回归。
- `tests/test_console_page.py` / `tests/test_console_js.py` / `tests/console_js_driver.js`：
  勾选生成 `rules`；不勾正文无 `rules`；生日格式输入跟随勾选显隐。

**端到端**
- 一份带 `form_data` 的脚本，勾 phone/dob/password 规则 ⇒ 填的是规则值、不是资料值；
  不勾 ⇒ 仍取资料。
- 生日格式：placeholder `DD/MM/YYYY` / 手填 `MM/DD/YYYY` / 无格式停下，三条各一例。

## 6. 影响文件（预计）

- `agent/console.html`（面板规则区 + `runPayload`）
- `agent/service.py`（`RunRequest.rules`、`_payload`、门口校验）
- `agent/state.py`（`SiteState.rules`、`END_RULE_NEEDS_FORMAT`）
- `agent/graph.py`（`_apply_rules`、facts、`PROVENANCE`、`rule_needs_format`）
- `agent/template.py`（`_rule_value`、`_fill_value` 分支、日期格式化）
- `agent/template_loop.py`（`value_for` 规则分支）
- `tests/…`（上面 §5）

## 7. 默认假设与风险

- **手机号格式**：前缀输入（0 开头、2–10 位数字；例 `04`/`0207`），运行时补随机数字到 **10 位**。
  如果站方要空格/括号格式，先按纯数字填；需要再补格式参数。
- **生日**：只支持数字日期 token（`D/DD/M/MM/YYYY/YY` + `/ - .`）；月份英文名等先不做。
- **密码**：`password_strong` 是通用强密码；如果某站有更严/更怪的要求，规则可能需要再加参数。
- **字段匹配**：按 name/kind/source/label 字样；认不出就 `unmatched` 警告，不静默。
- **模板改动要重启**：`agent/template.py` / `template_loop.py` 改了以后，8100/8099 都要重启
  才对新生成的脚本生效；已交付的产物自带规则，不受影响。
- **`phone_area_code`**：保留兼容；新面板不暴露它；`rules.phone_prefixes` 优先。

## 8. 验收标准

1. 填 `手机号前缀 04` / `0207` 生成的 py：phone 字段带 `rule=phone_prefix`/`prefixes`；
   运行时补到 10 位、多个前缀每趟随机一个、同趟同值、**不读 form_data**。
2. 勾生日：格式按「手填 > placeholder > label > hint」定；产物带 `date_format`；
   运行时 18–68、按格式输出；识别不出且没手填 ⇒ 停在生成期并说清，不产 py。
3. 勾密码：运行时生成满足 `8+ 大写 小写 数字 !@#$%^&*` 的值；不读资料。
4. 不勾任何规则：`/run` 正文、产物、运行行为与今天逐字节相同。
5. 规则可见：闸上 facts 与 `PROVENANCE.rules` 能看到勾了什么、生日格式从哪来、哪些没命中。
6. 未知规则/格式在 `/run` 门口 400；规则没命中在闸上警告。

## 9. 决策记录

- 2026-10-20 18:35–18:38（用户）：
  - 第一批：手机 02/07 + 生日 + 密码；
  - 02/07 同勾：每趟随机一个号段；
  - **2026-10-21（用户）**：「把 02/04 的勾选去掉，改成 input 框，可以填 0207 / 04；补齐到手机号位数」——AU 手机号是 `04` 开头；手机号规则改为前缀输入（0 开头 2–10 位数字），总位数补到 10 位。
  - 勾了三类都**强制现编**、不取资料；
  - 生日格式：自动识别 + 手填覆盖；识别不出且没填就停，不静默默认；
  - 规则按趟、不记忆；build/fix 都可勾。
