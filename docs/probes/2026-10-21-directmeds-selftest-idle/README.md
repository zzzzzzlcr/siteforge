# 2026-10-21 · directmeds 自测「打开网站后不动」+ 输入框的话没出去

- 任务：`job-6203bc32c1c4`（8099，site `directmeds`）
- 结论状态：**已定位**；两处修复见下（未提交、未重启服务、未重跑真站）

## 一、自测现场（证据在盘上）

`runtime/selftest/directmeds-20261021-013929/baseline/directmeds.baseline.trace.jsonl`
（容器时区 UTC，日志 01:39:46 → 01:41:09）：

| 步 | 行 |
|---|---|
| 0 | `popup`（没探到）、`goto` **跳过**：`不导航：页面由平台按任务开好` |
| 1–60 | `action: "none"`，`progress_why: "没有可动作的东西（1..60）"` |
| 60 | `ok: false`，`连 60 步没有可动作的东西（答过 0 题）` |

同一份 `.log` 里写着：

- `这一版**不自己导航**（谁开页面谁负责）——就在当前这一页上开干`
- `前置：到了（地址里有 https://directmeds.com/dm-directmax/）`
- 每步的页面标题都是 `After 15 Years of Viagra Disappointment...`（**广告落地页**，不是问卷）

⇒ 浏览器确实打开了落地页；产物从头到尾没离开它。

## 二、根因：把「同一目录下的多页问卷」判成了单页漏斗

1. `_when_for` 用 `_stable_url` 放宽地址判据；`_id_like` 把带数字的末段一律当轮换码，
   于是 `article-3in1-tab-ed.php` / `index-stc-2.php` / `questionnaire-1..18.php`
   全被截成 `https://directmeds.com/dm-directmax/`。
2. `funnel_states` 原来按 `when.url_contains` 分组 ⇒ 18 个状态「共用同一个地址」
   ⇒ 判成**步进循环型**（`shape_why` 原话）。
3. 循环型的 `pre_steps` 只收「漏斗之前」的状态；落地页被算进了漏斗
   ⇒ `PROVENANCE["pre_steps"] = []`（checkpoint 里可查）
   ⇒ 落地页那两下进问卷的 CTA（`See If You Qualify ›` / `Get Started`）**一个都没进产物**。
4. 循环型的读取器**不收 `<a href>`**（防页脚链接被当选项）——
   而落地页上那两个 CTA 正是 `<a>` ⇒ `inputs=0 / options=0` ⇒ 60 步空转。
5. `ARRIVE_URL` 也是那截宽前缀，所以 `run_pre` 站在落地页上就报「到了」——**没有报警**。

产品侧验证（在 8099 容器里按同一份 checkpoint 直接渲固定步骤表）：

```
states=18  fills=21  lint violations=0
STATES[0] = article_3in1_tab_ed
  scroll | See If You Qualify ›
  click  | See If You Qualify ›
STATES[1] = index_stc_2
  scroll | Get Started
  click  | Get Started
```

⇒ 形状改对之后，落地页那两下就在产物里，自测不会再「打开就站着」。

## 三、输入框的话为什么没出去

- `agent.log` 里这个 job **只有 `POST /reply`**（10 次，全 200），**没有一次 `POST /say`**。
- checkpoint 的 `hints` 通道只在 `intake` 写过一次，值是 `[]` ——
  **没有任何一句人的话进过图**。
- 面板代码：闸上 `btnContinue` 原来只发 `{"action":"continue"}`，**不读 `#sayBox`**；
  带话只有 `btnSay`（或输入框回车走 `send()`）那条路。运营写了话再按「继续」⇒ 字被丢掉。
- 另外：节点跑着的时候（自测那 2 分钟）两个按钮都是 `disabled`（`!waiting`），
  点按钮不会触发任何请求；输入框回车才会 `POST /say` 排队。日志里没有 `/say`
  ⇒ 那一次也没送出去。

## 三·附：修复轮为什么来回丢

- `src-3`（diagnose 之后那一版）**有** `pre_steps`，但它带的 `FILLS` 是旧的那一版
  （`number-2.source` 又变回 `number-2`）—— 上一轮 lint 的修复**在这一版里没了**。
- lint 再打回 → `src-4` 把 `number-2` 修回去，但 `funnel_states` 仍然给出
  `pre_steps=[]` ⇒ 落地页步骤**又丢了**。
- 于是「修好的两半」永远不在同一版里；运营连按 5 次「继续」也只是把这两版来回翻。

## 四、修复（工作树，未提交）

- `agent/template_loop.py::funnel_states`：判「是不是同一页」改用账本里**逐页的 `url`**
  （去 query / fragment），不再用放宽过的 `when.url_contains`；
  没有 `url` 的旧账本才退回 `when`（量不到不等于不是同一页）。
  ⇒ 同一目录下的多页问卷回到**固定步骤表**，落地页 CTA 作为 STATES 的前两组。
- `agent/console.html`：`btnContinue` 在框里有字时按「说一句」那条路送
  （`action: "say"` + note，服务收进 `hints`）；框里没字仍是纯 `continue`。
  这一条**改了规格 §9.1 的分工**（原设计：继续不带话 / 说一句才带话）——
  是否保留由人拍板；不保留的话可改成「按继续时提示先去按说一句」。
- `agent/template.py::_COVER_JS`（重跑时新撞到的，第二个骨架层假阳性）：
  **目标自己的 `<label>`（`el.labels`，含 `for=` 与包裹两种）及它里面的文字不算遮挡**。
  现场：directmeds 问卷选项是「隐藏 checkbox + 可见 `span.option-text`」，
  `elementsFromPoint` 在 checkbox 正中拿到它自己的 label 文字 ⇒ 判成遮挡 ⇒ 第 5 步不填。
  无关的 `div#onetrust-banner` 仍然照报。
- 测试：
  - `tests/test_loop_template.py`：目录前缀 ≠ 同一页；query 变但 path 同 ⇒ 仍是单页漏斗。
  - `tests/test_graph.py::_funnel_journey`：落地页的 `url` 改成另一页（夹具原来填成同址）。
  - `tests/test_console_js.py` + 驱动：闸上「框里有话 + 按继续」⇒ `/reply` 正文是
    `{"action":"say","note":"…"}`。
  - `tests/test_template.py::test_the_cover_probe_ignores_the_targets_own_label`：
    node + 假 DOM **真跑** `_COVER_JS` —— 自己的 label 放行、真遮挡照报。
- 验：`tests/test_loop_template.py tests/test_graph.py tests/test_console_js.py
  tests/test_console_page.py` = **312 passed**（8100 容器，2:56）；
  `test_states_writer + test_fix` = **54 passed**；cover 那两条在 8100 容器单独跑过 2 passed。
  （`test_template.py` 整份在这个容器里会卡在 **`test_covered_and_invisible_candidates_are_rejected`**
  —— 与本次改动无关的既有慢/阻塞用例，按单条跑 cover 相关的即可。）

## 五、重跑结果（8100，`job-49e6dd44475f`）

- 重启 8100 加载两处骨架修复后，从原 `job-6203bc32c1c4` 的账本**没有**复用；
  新探路 83 步（`not-qualified.php` 见到成功文案），`funnel_states` = **table**
- 自测 baseline：**54 步全 ok、0 失败、0 跳过**，走到
  `https://directmeds.com/dm-directmax/not-qualified.php`（成功文案在页）
- deliver 闸放行后落盘：`forms-222/sites/directmeds.py`（369886 字节），
  `end_reason=delivered`；**没有上传到 FMR / 没动 8099 生产**

## 六、自修完善度的结论（人问的）

- 这一趟的两处真卡点都是**骨架/判据层**（`funnel_states` / `_COVER_JS`），
  自修（diagnose→draft）改不到它们 —— 只能人改代码。
- 自修在**站点脚本层**是能用的：它按账本重写了 STATES，并按人说的一句
  （`number-2.source`）改对了 fills。
- 仍然不完善的两块：
  1. **骨架判据的同类模式**（`_id_like` 对 `.php` 末段、label/radio 之外的遮挡变体…）；
  2. **一轮里改好的东西会被下一轮冲掉**（`number-2` 与 `pre_steps` 的 ping-pong）——
     没有人的一句话，它会在两版之间来回翻。
- 下一步优先级（未做）：先修「改好的东西不被重渲冲掉」，再扫骨架判据的同类模式。

## 七、2026-10-21 补的「自修不自撞」那一刀（工作树）

- `agent/graph.py::_draft`：下一版**从上一版出发** —— `state.states` / `state.fills` 非空时
  拿它们当底，不再每版都用 `journey.states()` / `journey.fills()`（账本原件）；
  `loop_spec` 同理：上一版是 `loop` 时把它当底，再让账本算出来的那份兜底。
- `agent/graph.py::_explore`：**新账本 ⇒ 旧表作废**（`states=[]` / `fills={}` /
  `loop_spec=None`），免得重探后拿上一本账的表去渲新账。
- 测试（`tests/test_graph.py`）：
  - `test_a_second_draft_starts_from_the_last_version_not_the_raw_journey`
  - `test_a_second_draft_keeps_the_pre_steps_the_first_one_added`
  两条都先红后绿：改之前第二版确实退回 `postcode` / 只剩 `_loop_spec` 的 `pre_steps`。
- 另有两条既有用例的**夹具**跟着新语义改了（第二版从上一版出发 ⇒ 「返回原样」不再等于
  「回到干净账本」）：`test_a_lint_violation_is_handed_back_to_draft_with_its_lines`、
  `test_a_version_that_stops_fabricating_clears_the_row`。
- 验：`tests/test_graph.py tests/test_states_writer.py tests/test_fix.py` = **215 passed**。

## 八、失败族数据（`tools/fam_report.py`，两本账 332 趟）

去掉「人喊停 / 交付 / 窗口基建」后，出现最多的族：

| 趟数 | 去重站数 | 族 |
|---:|---:|---|
| 24 | 10 | 判据分不开页 |
| 18 | 11 | 编译层（goto 后有动作 / 只有地址当门） |
| 16 | 10 | 探路没走完 |
| 14 | 12 | 同一处修不动（图自己停的） |
| 12 | 9 | 自测挂了修到顶 |
| 10 | 6 | lint 打回 3 次 |
| 10 | 3 | 改稿那双手一处都没改 |

⇒ 下一步若继续「完善骨架」，最大的两族是 **判据分不开页** 与 **编译层**；
「同一处修不动 / 一处都没改」这两族与本节的 `draft` 底座修复直接相关，值得重量一遍。

## 九、FMR 推送（2026-10-21，`job-49e6dd44475f`）

- key（**页面 URL**）：`directmeds.com/dm-directmax/article-3in1-tab-ed.php`
- 本地 sha `7822d4447a3faf78…` / 369886 字节；后端**原来不认识这个键**（新建）
- 预检 → 提交 → **写后回读逐字节一致**；`config_status=1`，`enabled=True`
- 写前备份：`py-8e45cb9d1fc5258ea331444d89f26a1c`
- ⚠️ 这次「上传 = 顺手启用」**成立**（status=1）；10-16 那次不成立，别把两次混成一条规矩

## 十、判据分不开页：`when.url_equals`（2026-10-21）

- 现场（`job-e41de4bf38a4` · delamere）：`/contact` 与 `/contact/loved-one` 顶层正文
  **逐字一样**（表单在跨源 iframe 里，顶层只读到页头/电话）；`url_contains` 是**子串**判据，
  `/contact` 在 `/contact/loved-one` 上也成立 ⇒ `_unique_snippet_for` 挑不出候选 ⇒
  `_repair_ambiguous_whens` 空手 ⇒ IR 判「判据分不开页」⇒ 整族不出 py。
- 修：`when.url_equals`（**精确地址**，去 query/fragment/尾斜杠，整条逐字比）——
  - `browser_agent._exact_url` / `when_holds`：精确那一侧不成立就是不成立；
  - `_repair_ambiguous_whens`：正文挑不出候选、而这一组自己那几眼都在**同一个精确地址**上
    ⇒ 钉 `url_equals`（保留正文判据），碰撞消失；组里跨多条地址的照旧交给 IR；
  - 产物 `template.py` `_matches` / `_when_why` / `_say_url_only_whens` 认这一格；
  - `lint.judge-url-only`：带 `url_equals` 的不报（精确地址是门，不是「SPA 上地址不变」）；
  - `_explore` 的账本那句话带上 `say`（换的是地址判据，不是「重挑了一段」）。
- 测试：`when_holds` 精确/子页两条、`_repair_ambiguous_whens` 无独有正文的兜底、
  产物 `_matches` 的子页反面、lint 的 `url_equals` 不报 `judge-url-only`；
  两条既有用例的夹具按新语义改了（「没有独有正文」不再等于「一个字都不改」）。
- 验：`browser_agent + lint + ir + graph + states_writer + fix` = **610 passed, 1 skipped**
  （8100 容器，91 秒）。

## 十一、delamere 真站复验（2026-10-21，`job-4fd064c44b96`）

- 开场白与 `job-e41de4bf38a4` 相同（`what-happens-in-rehab` / 078 电话 / 同一句成功文案）
- 探路 21 步，**`gate_collisions: []`**（上一趟同一站是 `contact` → `/contact/loved-one`）
  - `contact` 的门 = `Talk to us in full confidence`（10-20 那刀「看得见的优先 + 兄弟屏」挑出来的），
    这一趟**不需要** `url_equals` 兜底；兜底留给「连正文都挑不出」的形状（单测钉着）
- 形状 `table`（4 条不同网址）；draft **lint 0**
- 自测 **11/11 步全 ok、0 失败、0 跳过**，第 11 步点 `Book a free confidential call`
  → `https://delamere.com/yourself-thanks`（成功页）
- deliver 放行后落盘：`forms-222/sites/delamere.py`（313205 字节），`end_reason=delivered`
- 这一趟**真提交了 1 次**（自测 baseline）

## 十二、`job-e45daea13b94`（event.monetise.com）「最后一步不会走」：MCP 会话卡死

- 开场白：`https://event.monetise.com/advertorial/1-v1-c`；成功文案 `Subscribe to Starter`
- 探路 63 步：广告页 → monetise survey → tally 嵌入问卷 `A71Peo`（8 屏）
  → 第 58 步点 `Submit`；**之后站点页没了**，浏览器只剩
  `https://console.bitbrowser.net/...`（Bit 工作台）这一个 target
- 第 59–63 步（observe ×3 / goto / screenshot）**全部**：
  `McpError: cdp-mcp 超过 120 秒没回 …`
  stderr：`DOM.documentUpdated when there's no top-level frame` /
  `could not retrieve document root … context deadline exceeded`
- `window.jsonl` 那段时间一直写 `alive: alive`（Bit **窗口**活着）⇒
  `_window_is_dead` 不成立 ⇒ 探路既停不下来也走不动 ⇒ 人喊停（`end_reason=paused`）
- **不是判据/骨架层的病**：站点页消失 + `cdp-mcp` 会话卡死；窗口接口看不见「页面没了」
- 修（`agent/tools.py` + `agent/browser_agent.py`）：
  - `McpSession.reopen()`：同一 argv/env 原地换一条 stdio 子进程（重新握手），
    旧泵线程把行灌进旧队列（不污染新会话）
  - `dispatch` 在 `McpError`（传输层超时/EOF/协议）上**换会话**后照旧报错；
    **不自动重放那一下**（超时的那一下可能已经提交过）
  - 模型的下一次工具调用走新会话（`observe`/`goto` 回去）
- 测试：`test_reopen_replaces_the_stdio_process_and_keeps_working`、
  `test_a_transport_timeout_reopens_the_session_and_the_next_call_works`
- 验：`browser_agent + service_steps + service_shots + notes_intake + graph`
  = **484 passed, 1 skipped**（8100 容器，92 秒）
- 8099 已两次重启加载（判据/形状/自修 + `McpSession.reopen`）；重启会清掉**在等闸的 job
  的实时时间线**（checkpoint 里的状态还在，按「继续」可接着走）
- ⚠️ 还没覆盖的：Submit 之后若站点开的是**新 tab**，探路的 `observe` 还没有
  「跟到新 tab」这一步（产物那一侧有 `_follow_tab_matching`）；这一条要真站再量一次。

