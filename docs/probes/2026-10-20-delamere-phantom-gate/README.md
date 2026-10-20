# 2026-10-20 delamere：**看不见的门** + 回头重点那一击

两个 job、同一个病：

- `job-4f7f53be42eb`（10-19 02:54Z 起，`repair_stuck`）：自测卡在「填资料」不动，
  报「第 3 步：页面上没找到「For myself」」；`yourself` 组的门是 `Facebook`。
- `job-2b027b30f0b0`（10-20 02:24Z，等人回话）：走「For a loved one」那一支，
  报「第 4 步：页面上没找到「For a loved one」」；`loved_one` / `loved_one-2` 的门是
  `Instagram` / `Facebook`。

## 一、证据链（真站 + 真 trace）

1. **失败那一步其实点成了。** `delamere-20261019-030029` 的 trace 顺序是：
   - 第 3 步 `click For myself`：`ok=true`、`progress=true`，网址已到 `/contact/yourself`；
   - 下一组 `yourself` 的门 `Facebook` 不成立；
   - 日志原话：`⚠️ 「yourself」的判据不成立，而页面**一点没动** —— 把第 3 步（For myself）**重来一次**`；
   - 重来的那一下在**新的一页**上找不到「For myself」⇒「第 3 步没找到」；
     `yourself-2` 又触发一次 ⇒ 同一句话出现两次 ⇒ 诊断判成 `selector`（「改稿治得了」）
     ⇒ 修稿一直在改那个选择器 ⇒ `repair_stuck`。

2. **`Facebook` / `Instagram` 在页面上根本没有文字。** 在 222 真站（377×757）实测：
   - 社交链接是 `<a aria-label="Facebook"><img alt="…Facebook page">`，**innerText 为空**；
   - Gravity Forms 的蜜罐 `div.gfield_description`（"This field is for validation…"）
     是 `visibility:hidden`、`getClientRects().length = 0`；
   - 但 `e.innerText` 对**不渲染**的元素会**回退到 textContent** ⇒ 它会进 `observe` 的
     `node_texts`；而产物 `page_signature()` 读的是 `body.innerText` ⇒ **门永远不成立**。

3. **挑选逻辑现场复现**（用真站抓下来的 `observe` 模型跑 `_unique_snippet_for`）：
   - 旧逻辑（只比别的地址）：第一屏挑中 `'This field is for validation purposes and'`
     —— 它**不在 `page_text`（可见正文）里**；更早那一版挑中的是 `'Facebook'`。
   - 同一屏上**可见且独有**的候选一直存在：`Thank you` / `Who are we talking to today?` /
     `Step 1 of 4,`。
   - 四个兄弟屏（Step 1/2/3/4 of 4）全都挑出同一个蜜罐句 —— 因为
     `_pin_own_gates` 的 `others` **把同址兄弟屏全排除了**：兄弟屏**共有**的字于是
     在每一屏都算「独有」。

## 二、修复（两条生成侧 + 一条骨架侧）

1. `agent/browser_agent.py` · `_unique_snippet_for`：**看得见的优先** +
   **短的那一档**（≥4 字，按唯一性收，见 2026-10-13 那条）。`page_text` 是 600 字/帧的
   硬切窗口，落在那之后的**好**判据仍可兜底当选（不许退回 2026-10-09 之前的窄尺子）。
2. `agent/browser_agent.py` · `_pin_own_gates`：`others` 改为**除这一屏以外的每一页**
   （兄弟屏 + 别的地址）。兄弟屏共有的字**不是**这一屏的门。真·每屏唯一的是
   `Step N of 4,` 这种，运行时页面上看得见。
3. `agent/template.py` · `_verify_the_hit`：`_last_hit` 连 `progress` 一起记；
   **`progress is True` 时不许回头重点那一击** —— 页面已经动过，判据不成立是
   **这页不像那个状态**，不是「没落地」。跳过照旧记（跳过 = 没做）。

## 三、验证

- 单测（8100 容器，改前红、改后绿）：
  - `tests/test_template.py::test_a_hit_that_moved_the_page_is_not_retried`
  - `tests/test_browser_agent.py::test_a_hidden_leaf_never_beats_a_visible_gate_candidate`
  - `tests/test_browser_agent.py::test_an_invisible_candidate_is_still_picked_when_nothing_visible_is_unique`
  - `tests/test_browser_agent.py::test_pinning_per_screen_gates_ignores_text_shared_by_the_sibling_screens`
  - 相邻「重试」两条（`test_a_hit_that_did_not_land_…` / `test_the_retry_happens_only_once_per_group`）
    与 55 条 gate/merge/when 用例全绿；原「三屏各给各的门」用例补了
    **门必须互不相同、且在兄弟屏上不成立**的断言（旧夹具只验了自己那屏）。
- 真站 222（只读/停止在提交前那一趟）：修正 `yourself` 的门后，
  第 1–5 步全成；日志当场出现新的那句话：
  `「yourself-2」的判据不成立 —— 但**上一击判得出页面动过**（progress=True），不再回头重点第 5 步`。
  见 `stop-at-10.trace.jsonl`（6–11 步跳过是**旧的** yourself-2 门，正是第二处要修的病）。
- 真站 222（修正两道门后跑到底）：**11/11 步全成**，四个框都填了，第 11 步提交后
  `/yourself-thanks`、成功判据成立。见 `full-success.trace.jsonl`。
  这一趟真实提交了 **1 次**（与自测同性质）。
- 生成侧在**同一批真站模型**上直接跑 `_pin_own_gates`（四个兄弟屏 + 别的地址）：
  `True`，四道门 = `Step 1 of 4,` / `Step 2 of 4,` / `Step 3 of 4,` / `100%`。
  ⚠️ 前三道在**生成期的 `when_holds`（只看 600 字）**上不成立 —— 它们在 600 字之后；
  产物运行时读的是**整页 innerText**，端到端那趟已证明三道门都成立。
  这是 2026-10-09「正例那把尺子必须与产物同宽」那条的同一个道理。

## 三·b、8100 全程复跑（2026-10-20 14:32–14:43 CST，`job-4705e0b35df0`）

重启 8100 加载上面三处修复后，用与 `job-4f7f` 相同的开场白重新发起：

- 第一次自测仍挑中 `LinkedIn`（**运行中的服务是在 blocklist 之前加载的**）—— 但诊断已正确
  变成「11 步里 8 步被跳过」，不是假的「没找到」；repair 轮把门改成
  `Who are we talking to today?` / `Step 2 of 4,`。
- 第二次自测：Bit 窗口 `192.168.1.222:61129` 连不上（`no_window`，不是产物问题）；
  按面板的「重新开一个窗口，接着走」（`/reopen`）恢复。
- 第三次自测：**11/11 步全成**、真实提交 1 次、`/yourself-thanks` 成功；deliver 通过。
- 交付：`forms-222/sites/delamere.py`（8100 的目录，**不是** 8099/生产），
  `delivered: true`；产物的门 = `What happens in rehab` / `Talk to us in full confidence` /
  `Who are we talking to today?` / `Step 2 of 4,`，无 FMR 上传事件。
- 第 4 处防御（blocklist：社交名 + 蜜罐）在本趟**之后**才加（服务未重载），单测已覆盖；
  下次重启后，`LinkedIn` 这类候选根本进不了挑选。

## 三·c、推 FMR（2026-10-20 14:44 CST，用户确认）

- `upload/prepare` → `upload/commit`（`job-4705e0b35df0`）：
  key = `delamere.com/what-happens-in-rehab`（页面 URL，新建行）；
  本地 **308180 B / sha `e3e21675a9359fac…`**；后端原为 `e3b0c442…`（空串）⇒ 新建。
- commit 回读一致，`config_status: 1`、`enabled: true`；备份 `py-88f5c202bd433d0ba44021cc07d14947`。
- 本地 `forms-222/sites/delamere.py` 的 sha256 与后端逐字节一致（独立 `sha256sum` 复核）。
- ⚠️ 8099 的 `forms/sites/delamere.py` 仍是失败的那一版（loved-one 支）—— **别上传它**；
  FMR 那一行已经是这趟验证过的好产物。

## 三·d、8099 重启 + 重跑（2026-10-20 14:46 CST，用户点名）

- 重启 `siteforge`（8099）加载 blocklist 那版；`job-4f7f53be42eb` →「重新来一遍」：
  第一趟 `job-de36c78400df` 的 explore 用了重启前那个旧窗口，到 draft 闸时窗口到点
  （服务拒 continue，要求 stop + again）→ 停在 draft 前。
- 再「重新来一遍」→ `job-227eb409ba11`：新窗口重探，**第一版 draft 直接过**
  （visits = intake/explore/draft/lint/selftest/deliver，**没有 diagnose/repair**）；
  门 = `What happens in rehab` / `Talk to us in full confidence` /
  `Many of our team members are in active recovery`（blocklist 生效，LinkedIn 没再出现）。
- 自测 **11/11 过、提交 1 次**；deliver 成功：`forms/sites/delamere.py`
  （8099 本地，**307647 B / sha `c8c38867…`**）。
- 没有 auto-upload 事件 ⇒ FMR 仍是 8100 那版（`e3e21675…`），两者流程一致、都过了自测。
- ⚠️ 重启让 8099 上 19 个 waiting job 的窗口一起失效；它们恢复时要逐个「重新开一个窗口」。

## 四、注意

- 8099 盘上的 `forms/sites/delamere.py`（mtime 10-20 10:36）是**失败的那一版**
  （loved-one 支、门仍是 `Instagram`/`Facebook`）—— **不要上传**。
- 8099 的 agent 进程（uvicorn 无 `--reload`）还跑着旧模块；要吃到这两条修复必须
  **重启容器**。之后请用「重新来一遍」（重探）生成新的一版；
  **「继续」旧 job 只会重跑那份自带旧门/旧骨架的产物**。
- 观察器源码（`tools/cdp/internal/observe.go`）仍会把**不渲染的叶子**（innerText 回退到
  textContent）收进 `node_texts`。本刀在 agent 侧用「可见优先」躲开它；更彻底的一刀是在
  observe 收集时跳过没有 client rect 的叶子（需要 Go 工具链重建 cdp）。
