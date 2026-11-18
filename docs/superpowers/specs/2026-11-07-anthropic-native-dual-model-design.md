# Anthropic 原生协议 + 主备模型自动切换 — 设计

日期：2026-11-07
状态：待评审（2026-11-07 复审意见已合并，见文末）

## 1. 意图

`agent/llm.py` 目前只走 OpenAI 兼容协议（`/v1/chat/completions`）。主模型
`deepseek-v4-flash` **能通但经常断**（用户原话），现在靠人肉发现。

这是**应急方案**：现有 OpenAI 通路**原样保留**（行为不变），Anthropic 原生协议作为
**可切换的备用路**加在旁边。目标：

1. 两种协议**按配置选择**，回切只改配置（env + 重启），**不需要改代码**；
2. 主模型断线时**自动切到备用模型**（默认 `claude-sonnet-4-6`，走 Anthropic 原生），
   job 不中断；主模型恢复后自动切回，不需要人工；
3. 关闭备用路 = 把 `SPIKE_FALLBACK_MODEL` 设为空串，回到今天逐字节相同的单路行为
   （空流/坏参数那两条新判据也一并关闭，见 §5.5）。

## 2. 已验证的网关事实（2026-11-07 对 `llm.3tkj.cn` 实测）

| 模型 | OpenAI 协议（tools/role:tool） | Anthropic 原生 `/v1/messages` |
|---|---|---|
| deepseek-v4-flash | ✅（线上现用） | ✅ 工具往返 + 流式 |
| deepseek-v4-pro | ✅ | ✅ |
| claude-sonnet-4-6 | ❌ 422（不认 `type:function`，也不认 `role:tool`） | ✅ |
| claude-opus-5 | ❌ 422 | ✅ |
| claude-haiku-4-5-20251001 | ❌ 422 | ✅ |

其它实测事实（决定了下面的转换规则）：

- 工具定义必须是 Anthropic 形状 `{name, description, input_schema}`；
- 消息角色只有 `user` / `assistant`，`system` 是**顶层参数**；
- thinking 模式下，assistant 的 `thinking` 块（含 `signature`）**建议**原样回传；
  特定构造下不回传会 400 `The content[].thinking in the thinking mode must be passed
  back`，但后续实测（3b/3c）表明旧轮甚至最新一轮去掉 thinking 块也能 200 ——
  保险策略见 §5.4；
- 工具的返回必须是一条 `user` 消息里的 `tool_result` 块，紧跟带 `tool_use` 的
  assistant 消息；
- 流式事件：`message_start` / `content_block_start` / `content_block_delta`
  （`text_delta` / `thinking_delta` / `signature_delta` / `input_json_delta`）/
  `content_block_stop` / `message_delta` / `message_stop`；
- **跨模型续跑可用**：把 deepseek 产出的 thinking/tool_use 块交给 claude 续跑，
  以及反过来，都回 200 —— 同一次 job 中途换模型在协议层是通的；
- `system` 顶层参数可用；`image` 内容块（base64 data URL）可用（实测 200）。
- **当时打出去的请求形状（已按探针脚本 `/tmp/probe_ctx*.py` 核对）**：
  `POST {base}/v1/messages`（`base` 就是 `OPENAI_BASE_URL` 那一格，本身已带 `/v1`），
  头 `x-api-key: <key>` + `anthropic-version: 2023-06-01` + `Content-Type: application/json`；
  体只有 `model` / `max_tokens` / `messages`（按需 `system` / `tools`），
  **没有** `thinking`、**没有** `temperature`。即使不点名 thinking，claude 的返回里
  仍出现 thinking 块 ⇒ thinking 的处理必须防御性写（见 §5.4），不能假设「没开就没有」。

## 3. 目标 / 非目标

### 目标

- **保留现有 OpenAI 通路**（`run_tool_loop` 的现有逻辑），另加一条 Anthropic 原生
  `/v1/messages`（流式）通路；对外签名与返回形状不变，`browser_agent` / `service` /
  `fix` 的调用点不动。
- 协议选择：`SPIKE_PROTOCOL=auto|openai|anthropic`（默认 `auto`：模型名 `claude-*`
  走 anthropic，其余走 openai）。**回切 = 改 env + 重启，不改代码**。
- 主模型一次调用在现有退避重试耗尽后，自动用备用模型（不同协议）重发**同一次调用**。
- 熔断：连续 N 次主模型失败后，冷却窗口内直接走备用模型；窗口过后先试主模型，
  成功即恢复。
- 可观测：能看出「哪一轮用了哪个模型/协议、有没有切换」，不用再靠人肉发现。

### 非目标

- 不做模型**质量**（软波动）的自动判定。只有硬失败（超时 / 断流 / 5xx / 连接错误 /
  **空流** / **工具参数非 JSON**）参与切换。
- 不改浏览器、工具层、图（`graph.py`）的语义。
- 不做按站点/按 job 的模型偏好。
- **不删除、不改写现有 OpenAI 通路的行为**：它是默认路，也是随时能回切的那条。

### 反目标（做出来就算失败）

- 切换把参数错误/鉴权错误也带走，把真错拖成慢错。
- 换协议后 `records` / `usage` / `finish_reason` 的形状变了，导致下游（fix.py、
  measure、console）悄悄退化。
- thinking 块没回传导致 400，或每轮把历史 thinking 全带上导致上下文爆炸。

## 4. 不变的对内契约

- `run_tool_loop(system, user, tool_specs, dispatch, max_rounds=8, *, model=None,
  max_tokens=..., steer=None, _client=None)` **签名不变**（不新增对外参数）。
- `_client` 是一个**客户端形状**的对象（`chat.completions.create(**kwargs)`）。
  它在生产里**不是测试替身**：`browser_agent` 传的是 `_Gate`（`:1267` 建、`:1553`
  传），每轮调用前跑停闸、收流时记叙述。两条协议都必须经过它（见 §5.1「客户端门面」）。
  测试注入点是客户端门面的传输层（httpx `MockTransport`），不是 `run_tool_loop` 的
  新参数。
- `records` 每条：`round` / `content` / `tool_calls` / `usage` / `elapsed_ms` /
  `finish_reason` / `messages` / `steered`。**只增字段**：新增 `model`（这一轮实际
  用的模型）与 `failover`（`None` 或 `{"from": ..., "to": ..., "reason": "..."}`）。
- `finish_reason` 值域保持：`tool_calls` / `stop` / `length` / 其它原样。映射：
  Anthropic `stop_reason` `tool_use`→`tool_calls`，`end_turn`/`stop_sequence`→`stop`，
  `max_tokens`→`length`。
- `usage` 保持 `prompt_tokens` / `completion_tokens` / `total_tokens`，并尽量补
  `completion_tokens_details.reasoning_tokens`（见 §6 第 6 条的已知退化）。
- `ModelCallFailed` 仍带现场（耗时 / 发出去几个请求 / 消息数 / 模型 / 旋钮 /
  长重试），新增「试过哪些模型」，以及空流/坏参数的账（`finish_reason` /
  `tool_calls` 条数 / `reasoning_tokens` / `max_tokens`）。
- `_Stop`（`BaseException`）不等待、不包装、不切换。
- `steer`：调用前插入；调用有结果后 `went_out()`，抛则 `missed()`。
- env 旋钮保留：`SITEFORGE_LLM_BACKOFF`（`off` = 关）、`SITEFORGE_LLM_RETRIES`、
  `SITEFORGE_LLM_TIMEOUT`、`SPIKE_MAX_TOKENS`、`SPIKE_MODEL`。
- `client()` 的两条「没配就当场报错并点名变量」的规则保留（`OPENAI_API_KEY`、
  `OPENAI_BASE_URL`）。

## 5. 方案

### 5.1 传输层：一个门面，两条协议

- **客户端门面（复审补 —— 这是整个方案的落点）**：`client()` 返回的**不是裸 OpenAI
  SDK**，而是一个按模型路由的门面：对外仍然是 `chat.completions.create(**kwargs)`；
  收到 `model=` 后按 `SPIKE_PROTOCOL`（`auto` 看前缀）选 OpenAI SDK 或 Anthropic
  传输层。
  - **两条协议都从这一个口进出。** `browser_agent._Gate` 包在门面**外面**、一个字节
    不改 —— 它每轮调用前的停闸（人喊停 / 预算 / 成功文案 / 窗口）与收流时的叙述记录
    （`journey.notes` / `watch` 停滞判据）因此对 Anthropic 路照样生效。
    **Anthropic 传输层绝不允许绕过 `cli` 直接 POST** —— 绕过去 = 停闸、叙述、停滞
    判据三样静默消失（§3 反目标第一条）。
  - §5.5 的「切到下一个模型重发同一次调用」= 把 `model=` 换掉再调一次
    `cli.chat.completions.create`；协议选择与请求转换由门面负责，**切换判定仍留在
    `run_tool_loop`**（只有那里看得见退避与「这一次调用」的边界）。
- 协议选择：`SPIKE_PROTOCOL`（默认 `auto`：`claude-*` → anthropic，其余 → openai）。
  显式写死**只影响 primary**；fallback 永远按模型名判（见 §5.5 / §5.7）。
- **OpenAI 通路保持原样**：现有 `client()` 里的 SDK 构造 / `read_stream()` /
  `cli.chat.completions.create(...)` 一个字节不改，是默认路，也是回切的那条。
- **Anthropic 通路新增**：门面内部用 `httpx`（已有依赖）直接 POST
  `{OPENAI_BASE_URL}/messages`，流式读 SSE。不引入 `anthropic` SDK：少一个依赖，
  且网关是自定义兼容层，手写可控。传输层对外**回吐 OpenAI 形状的 chunk**
  （`choices[].delta.content` / `delta.tool_calls` / `finish_reason` / `usage`）——
  `read_stream`、`records`、`_Gate._watched`（靠 `llm.delta_content`）全都不用动。
  thinking 在 chunk 层只走旁路（见 §5.3），**不许进 `delta.content`**：否则 `_Gate`
  会把思考当叙述写进账本。
- `_http_client()` 的请求钩子保留，「发出去几个请求」照样量得到；Anthropic 通路复用
  同一个 httpx client —— ⚠️ **但必须每次请求显式带 `timeout=timeout_seconds()`**：
  这个 client 是裸建的，httpx 默认超时 **5 秒**（OpenAI SDK 自己会按请求传 timeout，
  所以今天没暴露）。直接 `_HTTP.post/stream` 会让长生成在 5 秒被掐，现象和「模型挂了」
  一模一样。
- `SITEFORGE_LLM_RETRIES`：OpenAI 通路仍由 SDK 提供；Anthropic 通路在传输层实现为
  **连接类 + `RETRYABLE_STATUS` 的立即重试**（与 SDK 的 `max_retries` 同一张表，
  含 408/409/425/429/5xx）。长退避（`backoff_seconds()`）语义不变，仍是外层那一层，
  两条通路共用。
- `_worth_waiting` **只做加法**：保住现有的 `status_code` 判断与 OpenAI 的两个连接类
  异常，再认 httpx 的 `ConnectError` / `ReadTimeout`，以及 `HTTPStatusError` **且**
  `exc.response.status_code in RETRYABLE_STATUS`。**不认字样**。
  ⚠️ `HTTPStatusError` 没有 `.status_code`（在 `.response.status_code`），也不能
  `isinstance` 一刀切 —— 否则 400/401 也会被当可重试并触发切换。
- `RETRYABLE_STATUS` 补 **529**（Anthropic `overloaded_error` 用的就是它）。这是加法：
  现网 OpenAI 路回 529 的概率极低，且语义就是「上游没空」。

### 5.2 消息与内容块（复审改：内部只保留一种形状）

**内部历史保持今天的 OpenAI 形状**（`system/user/assistant/tool` 的 OpenAI message）。
这是「OpenAI 通路一个字节不改」（§5.1）与「failover 时 messages 不动」（§5.5）同时
成立的唯一办法；`record["messages"]` 的形状也随之不变（§6 第 5 条）。

协议差异全部在**发请求前的那一步**转换，且转的是**整段历史**（不是只转首轮）：

- `to_anthropic(messages) -> (system, anthropic_messages)`：
  - `system` 角色 → 顶层 `system` 参数；
  - `user` 字符串 → `[{"type":"text","text":...}]`；OpenAI `image_url` data URL →
    Anthropic `image` 块（base64 + media_type）；
  - `assistant` + `tool_calls` → `[{"type":"text"...}, {"type":"tool_use","id","name",
    "input"}]`（`arguments` JSON → `input` 对象；解析失败的那条按 `{}` 转，错误文本
    已经在那条 tool_result 里）；
  - `role:"tool"` 消息 → `tool_result` 块；**连续多条合并成一条 user 消息**，
    顺序与 assistant 的 tool_use 一致；
  - `tool_result` 里的图片 parts 同样转成 Anthropic `image` 块（不只是 user 消息）；
  - steer 消息本来就是 user 文本，原样。
- thinking 块：内部是 OpenAI 形状，本来就没有；要回传时由传输层保存的旁路字段
  `_anthropic_blocks` 提供（见 §5.4）。发给 OpenAI 前**剥掉所有 `_` 开头的私有键**。
- 协议转换是纯函数：同一份 `messages` 原样交给两条路，谁发谁转。

### 5.3 流式解析（Anthropic 传输层内部；`read_stream` 不动）

- `read_stream`（OpenAI 路）**一个字节不改**。新增 `anthropic_stream(response)`：
  读 SSE，**产出 OpenAI 形状的 chunk**，让现成的 `read_stream` 与 `_Gate._watched`
  直接消费。
- 事件映射：
  - `message_start`：usage 的 `input_tokens` 先记着；
  - `content_block_start`：建块；`tool_use` 的 `id`/`name` 进 `delta.tool_calls` 首片；
    ⚠️ 这一事件里可能已经带非空 `input`（初值）—— 解析时以它为准，别只认 delta；
  - `content_block_delta`：`text_delta` → `delta.content`；`input_json_delta` →
    该 tool_call 的 `arguments` 碎片；`thinking_delta` / `signature_delta` →
    **只进旁路块**（`delta.reasoning_content` 或不产出 chunk），不进 `content`；
  - `message_delta`：`stop_reason`（映射见 §4）与 `output_tokens`；
  - `message_stop`：收尾 chunk（带 usage；`choices` 为空数组 —— `read_stream` 认这个形状）；
  - `ping`：忽略。
- **`event: error` 必须当失败**：流中间的 `overloaded_error` 等若被忽略，会留下一个
  「没有 tool_calls 的 reply」→ 循环当成「模型讲完了」→ **假成功**。映射成可重试异常，
  已收到的块带在异常现场里。
- **必须见到 `message_delta`/`message_stop` 才算完整回复**：连接被干净地提前关掉
  （没收到终止事件）也按断流处理，不能把半截流当完整轮。
- 中途断流：已收到的块保留在异常现场里（与今天「流被掐也落在同一个 try」一致）。
- 传输层把这一次的原始块（thinking/signature/text/tool_use）挂在收尾 chunk 的旁路
  字段 `_anthropic_blocks` 上；`read_stream` **只做加法**把它捞出来（OpenAI 路永远
  没有这个字段）。

### 5.4 上下文体积与 thinking 回传

- thinking 块**只保留最近一条 assistant 消息上的**（旁路字段 `_anthropic_blocks`）；
  更早的 assistant 消息在转换时重建为 `text` / `tool_use` 块，**不带** thinking /
  signature。理由：全量保留会让每轮上下文线性膨胀。
- 实现落点：`run_tool_loop` 把 `reply["_anthropic_blocks"]` 贴到那条 assistant message
  的私有键上；`to_anthropic` 只对**最后一条** assistant 使用它。发给 OpenAI 前剥掉。
- 2026-11-07 实测：三条 assistant、两条历史 thinking 全去掉 → 200；只去旧的、
  保留最新的 → 200；连最新一条也去掉 → 200。但早期构造确实出现过
  `thinking must be passed back` 的 400 ⇒ 采「保留最新一条」的保守策略，
  实现时用回放测试固定这个行为。**若网关后续要求全量，退化为全量并在本文档
  记录实测**（风险见 §7）。

### 5.5 故障切换与熔断

- 模型链（每档 = 协议 + 模型名）：
  - `primary = (protocol(SPIKE_MODEL), model or SPIKE_MODEL)`；
  - `fallback = (协议, SPIKE_FALLBACK_MODEL)`：协议**永远按模型名判**（不受
    `SPIKE_PROTOCOL` 写死影响），默认 `claude-sonnet-4-6`；设为空串 = 关闭切换
    （回到今天的单路行为）；
  - `protocol()` 由 `SPIKE_PROTOCOL` 决定：`auto` 按前缀（`claude-*` → anthropic，
    其余 → openai），也可以显式写死 `openai` / `anthropic` —— 但**只作用于 primary**。
- 单次调用内：
  1. 对当前模型按现有退避表重试；
  2. 退避耗尽且错误**属于可重试类**（`_worth_waiting`）且还有下一个模型 ⇒
     记录 `failover`，切到下一个模型重发同一次调用（messages 不动）；
  3. 备用模型也失败 ⇒ `ModelCallFailed`，现场里列出「试过哪些模型」。
- **硬失败的判定（拍板：空流与工具参数也算）**：
  - **空流**：这一次调用正常结束（拿到了终止事件），但 `content` 去掉空白后为空、
    且 `tool_calls` 为空。`finish_reason="length"` 的空回复也算（预算被思考吃满 ——
    同一次重试救不了，但换模型可能）。
  - **工具参数非 JSON**：`tool_calls` 里任何一条的 `arguments` 解析失败、或解析出来
    不是对象。参数解析只写一处（与后面 dispatch 用的是同一个函数），不许两处各写一份。
  - 判定发生在**追加 assistant 消息之前**：这一轮整轮作废（assistant 消息与工具结果
    都不进历史），按 503 同一条路：退避重试 → 切换 → `ModelCallFailed`。
  - 实现上：判成硬失败时抛一个内部异常（如 `BadReply`），`_worth_waiting` 认这个
    类型 —— 与 503 走同一张退避表、同一条切换路径，**不靠字样匹配**。
  - `ModelCallFailed` 的现场要带上这一次的 `finish_reason` / `content` 字符数 /
    `tool_calls` 条数 / `usage.completion_tokens_details.reasoning_tokens` /
    `max_tokens`（`fix.py` 原来那条「预算被思考吃满」的诊断事实不能丢）。
  - 空回复/坏参数不算「有结果」：`steer` 的 `went_out()` 仍只在拿到可用回复后调用。
  - ⚠️ 这两条判据属于 **failover 特性的一部分**：`SPIKE_FALLBACK_MODEL=` 空串时
    一并关闭（空 reply 仍按今天的行为「模型讲完了」收工）—— §1 目标 3 的回切承诺
    必须是整条的，不能只关模型切换、留着新判据。
- 熔断（进程级，模块级状态 + `threading.Lock`）：
  - **计数单位 = 一次模型调用**（含它自己的退避与 fallback），**不按 HTTP 尝试**；
    fallback 的成败**不**计入 primary 的连续失败；primary 一次成功即清零。
  - `primary` 连续失败 `SITEFORGE_LLM_FAILOVER_FAILS`（默认 3）次 ⇒
    `demoted_until = now + SITEFORGE_LLM_FAILOVER_COOLDOWN`（默认 300 秒），
    同时把计数器归零；
  - 熔断期内：调用直接从 `fallback` 开始（不再先撞 primary）；
  - 窗口过后是**半开**：下一次调用先试 primary，成功即恢复；失败**一次**就重新熔断
    （不必再攒 N 次）；
  - `SITEFORGE_LLM_FAILOVER_FAILS=0` = 每次调用都先试 primary（只做单次切换）。
  - `record["failover"]` 的 `reason` 值域：退避耗尽后切换 = `"error"`；
    熔断期内直接走备用 = `"breaker"`。
- 只切换**可重试错误**：参数错、鉴权错、模型名错（状态码不在 `RETRYABLE_STATUS`
  里，且不是连接类异常）不切换，直接 `ModelCallFailed`。

### 5.6 可观测性

- 每次切换写一行容器日志：
  `[llm] primary=deepseek-v4-flash failed (ReadTimeout): ... → fallback=claude-sonnet-4-6`
  （不打印 key、不打印消息正文）。熔断期内直接走备用时同样写一行（带剩余秒数）：
  `[llm] primary=... demoted (breaker open, 212s left) → fallback=...`。
- `records[i]["model"]` 与 `records[i]["failover"]` 进 `journey`/checkpoint，
  `/runs` 的诊断可以显示「这一轮换过模型」。
- `summarize()` 增加 `models`：`{model: 轮次数}`，以及 `failovers` 次数。

### 5.7 配置

| env | 默认 | 说明 |
|---|---|---|
| `SPIKE_MODEL` | `deepseek-v4-flash` | 主模型 |
| `SPIKE_PROTOCOL` | `auto` | `auto` / `openai` / `anthropic`；**只决定 primary 的协议**（fallback 永远按模型名判，见 §5.5） |
| `SPIKE_FALLBACK_MODEL` | `claude-sonnet-4-6` | 备用模型；空串 = 关闭切换（应急回切开关；空流/坏参数判据也一并关闭） |
| `SITEFORGE_LLM_FAILOVER_FAILS` | `3` | 连续失败几次后熔断 |
| `SITEFORGE_LLM_FAILOVER_COOLDOWN` | `300` | 熔断冷却秒数 |
| `SITEFORGE_LLM_BACKOFF` | `5,20,60` | 不变；`off` 关闭长退避 |
| `SITEFORGE_LLM_RETRIES` | `2` | 传输层立即重试次数（语义与今天一致） |
| `SITEFORGE_LLM_TIMEOUT` | 现有默认 | 不变 |

## 6. 关键决定（我的判断，评审时可推翻）

1. **手写 httpx 传输层**，不引入 `anthropic` SDK。
2. **只在可重试错误上切换** —— 参数/鉴权错误换模型也救不了。
3. **thinking 只保留最近一条 assistant**（§5.4，走 `_anthropic_blocks` 旁路字段），
   实现时用真网关验证。
4. **熔断状态进程级**，不持久化：重启归零可接受（重启后先试主模型本来就是期望行为）。
5. **~~`record["messages"]` 改为 Anthropic 形状~~（复审撤回）**：内部历史保持 OpenAI
   形状（§5.2），`record["messages"]` 的形状因此不变。核对过：`record["messages"]`
   全仓只写不读 —— 但既然不必改，就不去动它。
6. **`usage` 的 `reasoning_tokens` 可能缺失**（Anthropic 不单列思考 token）：
   `fix.py` 的「预算全花在思考上」提示会退化，但主流程不受影响。不为此造伪值。
7. **空流与工具参数非 JSON 算硬失败**（2026-11-07 用户拍板）：整轮作废再重试/切换；
   空回复的预算诊断事实（`finish_reason` / `reasoning_tokens` / `max_tokens`）搬进
   `ModelCallFailed` 现场（§5.5）。

## 7. 风险与取舍

- **手写 SSE 解析**：边界事件多（usage 分散、block 结束、thinking 无内容）；
  用真网关回放 + 单测覆盖。
- **跨协议行为差异**：错误码、`stop_reason`、usage 字段与 OpenAI 不同；一律在
  适配层归一，不让下游感知。
- **切换的耗时**：一次调用最坏 = primary 退避 85 秒 + fallback 自己的退避 85 秒 =
  **170 秒**；熔断就是为了避免每次都付这份钱。
- **httpx 的默认 5 秒超时**：`_http_client()` 是裸建的，SDK 会按请求覆盖它，直连
  httpx 不会 —— 这是最容易在实现时漏、且症状酷似「模型挂了」的一格（见 §5.1）。
- **跨模型续跑**：协议层已验证 200，但语义上模型可能对上一模型的 thinking 理解不佳；
  只作为故障兜底，不主动使用。
- **thinking 回传体积**：只留最新一条；若网关要求全量，退化为全量并在文档记录实测。
- **空流/坏参数判据对两条路都生效**：它改变 OpenAI 路今天「空 reply 直接收工」的
  行为 —— 这是本次拍板要的（§10），不是悄悄漂移。
- **`fix.py` 的空补丁诊断路径会提前**：`patch_from_rounds` 里那段「预算被思考吃满」
  不再有机会对空回复触发（循环先当硬失败重试/切换）。所以 `ModelCallFailed` 必须带上
  同样的账（`finish_reason` / `reasoning_tokens` / `max_tokens`），否则那条诊断就真丢了。

## 8. 测试计划

- 单元（新用例；替身沿用 `tests/test_llm.py` 的写法）：
  - tools 转换（OpenAI `type:function` → `input_schema`）；
  - user parts（text / image_url data URL）→ Anthropic 块；
  - 工具结果合并为一条 user 消息；
  - SSE 解析：text / thinking / signature / input_json_delta / usage / stop_reason；
  - `finish_reason` 与 `usage` 映射；
  - thinking 块只在最新 assistant 上回传；
  - steer 插入 / `went_out` / `missed`；
  - 退避重试与 `SITEFORGE_LLM_BACKOFF=off`；
  - 故障切换：可重试错误切换、不可重试错误不切换、熔断与恢复、`ModelCallFailed`
    现场包含试过的模型；
  - `summarize()` 的 `models` / `failovers`；
  - 传输层：`event: error` → 可重试失败；没收到 `message_stop` 的截断流 → 失败；
    `ping` 忽略；`content_block_start` 自带的 `input` 初值；
  - `_worth_waiting`：`HTTPStatusError(400)` 不重试、`HTTPStatusError(503)` 重试；
  - 空流：`content` 空 + 无 `tool_calls` → 走退避重试；主备都空 ⇒ `ModelCallFailed`；
  - 工具参数非 JSON / 非对象 → 整轮作废、重试切换；坏参数那一轮不进 `messages`；
  - `finish_reason="length"` 的空回复也算空流（现场带 `reasoning_tokens` / `max_tokens`）；
  - 回切开关：`SPIKE_FALLBACK_MODEL=""` 时空 reply 仍按今天的行为收工（不重试）；
  - `_Gate` 等价性：Anthropic 路每一轮仍然经过客户端门面（停闸被调用、叙述进
    `journey.notes`）—— 用一个与 `_Gate` 同形的假包装验证；
  - 熔断：打开后直接走 fallback、半开时 primary 失败一次即重新熔断；
    `record["failover"]` 的 `reason` 值域（`error` / `breaker`）。
- 8100 真站验证：
  - 用同一把 key、同一网关，跑一趟真 job，确认 `records` 的 `model` 字段、
    工具调用、`usage`、`finish_reason` 正常；
  - 人为把主模型改成不存在的名字，确认自动切到 claude 且 job 完成；
  - 恢复主模型，确认下一次 job 回到 deepseek；
  - 确认 Anthropic 路上 `_Gate` 的两件事照常：轮间喊停能停、模型叙述进了
    `journey.notes`（这一趟若这两样丢了，功能算没接上）。
- 回归（原 OpenAI 通路不许变）：
  - **例外一条**：`tests/test_llm.py:471` `test_giving_up_after_waiting_says_so`
    模拟「503 永久失败」并断言 `calls["n"] == 3`；默认启用 failover 后 primary 退避
    耗尽会再打 fallback（同一个假客户端）⇒ 计数变 6。这条必须
    `monkeypatch.setenv("SPIKE_FALLBACK_MODEL", "")`（或改断言为含 fallback 的调用数）。
  - 其余 `tests/test_llm.py` 不改、必须全绿；用同一组假客户端验证请求形状与今天
    逐字节相同。
- 8099 上线：先用 8100 通过，再重建 8099 容器（env 增加 `SPIKE_FALLBACK_MODEL`）。
  回滚 = 把 `SPIKE_FALLBACK_MODEL` 设为空串（或直接换回旧容器），**不改代码**。

## 9. 留给 builder 的决定

- 代码组织：新模块（如 `agent/anthropic_transport.py`）还是 `llm.py` 内部函数；
  无论哪种，**OpenAI 那条路的函数体不许被重写**，且两条协议都必须从 §5.1 的
  客户端门面进出。
- 具体函数拆分与命名。
- 熔断锁的粒度（全局一把锁 vs 按模型）。
- 日志格式的具体措辞。

## 10. 已拍板

- **空流与工具参数非 JSON 都算硬失败**（2026-11-07 用户拍板）：参与退避重试、
  切换与熔断；判定规则与现场要求见 §5.5。这条对两条协议都生效。
- 该判据挂在 failover 特性上：`SPIKE_FALLBACK_MODEL=` 空串时一并关闭（回切承诺
  见 §1 目标 3）。

---

## 附：2026-11-07 复审修订摘要

1. 新增「客户端门面」：两条协议都从 `client()` 返回的 router 进出，`_Gate` 保持在外层
   （§5.1）。原方案没有这一层 —— 按字面实现会绕过 `_Gate`，且「切换」根本发不出去
   （`gate.chat.completions.create(model="claude-…")` 仍走 OpenAI 协议 → 422）。
2. 内部历史改回 **OpenAI 形状**，新增全量 `to_anthropic()`；撤回「`record["messages"]`
   改 Anthropic 形状」（§5.2 / §6 第 5 条）。
3. 写明 httpx 的 5 秒默认超时、SSE `error` / `ping` / 截断、`HTTPStatusError` 的判定
   方式、`RETRYABLE_STATUS` 补 529（§5.1 / §5.3）。
4. `SPIKE_PROTOCOL` 写死只影响 primary，fallback 永远按模型名判协议（§5.5 / §5.7）。
5. 补熔断计数语义、半开行为、`failover.reason` 值域（§5.5 / §5.6）。
6. 测试计划承认一条现有测试必须改（`test_giving_up_after_waiting_says_so`），
   并补传输层 / 门面 / 熔断用例（§8）；最坏耗时改为 170 秒（§7）。
7. 空流与工具参数非 JSON 归入硬失败：整轮作废、走重试/切换/熔断，现场保留
   `finish_reason` / `reasoning_tokens`（§3 / §5.5 / §7 / §10）；该判据挂在
   failover 上，`SPIKE_FALLBACK_MODEL=` 空串时一并关闭。
