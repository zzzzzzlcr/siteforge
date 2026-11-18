# Atticus `try.atticus.com/ssd/benefits`：邮编永远填 10001（2026-10-29）

## 用户报的现象

「还是失败。是不是 zipcode 没取资料？10001 过不去啊，我看截图也还是卡在邮编。」

## 根因（两条，都在代码里量到）

### ① 资料字段名对不上（主因）

- 后端 `QuestFormMessageController::getFormMessage` 返回的资料键是**数据库列名**：
  `postal_code` / `phone_number` / `username` / `email` …
- 产物 `FILLS` 的键是照**页面字段名**起的：`postcode` / `phone` / `first_name` …
- `value_for()` 只查 `p.get(name) or p.get(kind)` —— 只有恰好同名的 `email` 命中，
  其余全部**静默**落到 `EXAMPLES`（探路时的示例值）或随机池。
- 实证：拿真实资料形状（`postal_code: "90210"`）跑产物的 `value_for`：

  ```
  '\x04zipcode' -> '10001'      # 资料里写的是 90210
  ```

  线上两单（26919522 / 26919524）的截图里邮编永远是 10001，就是这个原因。

### ② `_nudge` 一屏只给一次机会

站点 `benefitsByZip` 偶发被中止（`responseStatus: 0`），而旧实现用 `_nudged_sig`
把「同一屏已重打过」钉死 ⇒ 第一次撞上中止之后再也不碰那一格，只能干点 8 次
`Next`（日志原样：`step 10..17: Next（What's your ZIP code? …）`）。

## 改了什么

模板 `agent/template_loop.py` 与产物 `forms-222/sites/try.loop.py` 两处同改：

1. `PROFILE_ALIASES` / `PROFILE_SPLIT_FROM`：资料列名 ↔ 页面字段名的回退表
   （与固定步骤表 `agent/template.py::_ALIASES` 同源），并加**全名拆分**
   （`username: "Lin Cameron"` → First / Last）。
2. `_profile_value()`：原名 → 别名 → 全名拆分，只加回退，不动原来的键。
3. **`source` 排第一**：探路给的 `source`（2026-09-30 起就是资料库列名）先查。
4. **说出取值来源**：`value_for` 写 `self._value_from`，填充日志与 trace 变成
   `填了 zipcode（任务资料「postal_code」（别名 → postcode））` ——
   「填上了 ≠ 填对了」从此一眼可见。
5. `_nudge` 改成原生 setter + 全套事件，且**同一屏最多打 `NUDGE_TRIES=3` 次**。
6. **生成期闸门**：`render_loop` 把「键/source/kind 都认不出、又没有 rule /
   from_profile:false」的格子写进 `PROVENANCE['profile_unmapped']`。

## 测试（先红后绿）

- 新增 7 条：zip 取 `postal_code`、电话取 `phone_number`、姓名拆 `username`、
  同一屏可重打 3 次、`source` 优先、取值来源日志、生成期闸门（含负控）。
- 变异检查：把 source 优先 / 别名来源日志 / 闸门各自去掉，对应用例**全部变红**。
- `test_loop_template.py + test_tool_loop.py`：**61 passed, 5 skipped**。
- 全量 `tests/` 跑不了：41 个收集错误全是 `ModuleNotFoundError: No module named
  'langgraph'`（环境缺依赖，与本次改动无关）。

## 真站 headless 整单（不开生产窗口）

用资料形状 `postal_code: "60601"`、`username: "Lin Cameron"`、`phone_number: …`
跑补丁后的产物：

- `step 1: 填了 zipcode` → 下一屏 **“Atticus got 216 Illinoisans approved”**
  ⇒ 用的是资料里的 60601（10001 会显示 New York），**不是写死的 10001**；
- First / Last 从 `username` 拆出并填上；
- **80 秒跑完，`SUCCESS(url)` 落在 `survey/rejection/legal-representati…`**，EXIT=0。

## 推 FMR（两次）

第一版（别名 + 重试，84976 字节）：

```
LOCAL  sha256 112360cc6a8d607d…
BEFORE f76bdad8622f1bb6…（= LF 归一的 d037b034）
WRITE  status 200 / success / config_id 162 / created:false
READBACK 112360cc6a8d607d…  逐字节一致（84976 字节）
```

第二版（再加 `source` 优先 + 取值来源日志，87179 字节）：

```
LOCAL  sha256 1475f39b01d150ce…
BEFORE 112360cc6a8d607d…
READBACK 1475f39b01d150ce…  逐字节一致（87179 字节）
```

第三版（点击回执 / 禁用等待 / 填充重试，90978 字节）：

```
LOCAL  sha256 229af4a25b4680e1…
BEFORE 1475f39b01d150ce…
READBACK 229af4a25b4680e1…  逐字节一致（90978 字节）
```

第四版（点完 Next 先等页面变，20 秒预算，93641 字节）：

```
LOCAL  sha256 153f144e2e5a981a…
BEFORE 229af4a25b4680e1…
WRITE  status 200 / success / config_id 162 / created:false
READBACK 153f144e2e5a981a…  逐字节一致（93641 字节）
AFTER  config 162 sites 1 [{site: try.atticus.com/ssd/benefits, status: 1}]
```

留底：`fmr_before_d037b034.py` / `fmr_before_112360cc.py` / `fmr_before_1475f39b.py` /
`fmr_before_229af4a2.py`；结果：`fmr_push_result{,2,3,4}.json`。

## 2026-10-29 10:23 真站整单（含「点完等页面变」）

**跑通**：`SUCCESS(url) → survey/rejection/ssi-initial`，EXIT=0，**2 分 17 秒**，16 次 Next。

- ZIP 屏：点 **1 次** Next（10:23:35）→ 等 **13 秒**页面自己变（10:23:48）→ 继续
  （旧逻辑那 13 秒里会点 8 次并收摊）；
- 没有出现「连着 8 次点 Next」的收摊行。

预算按这次实测从 12 秒提到 **20 秒**（正常的屏 1–2 秒就变，不受影响）。

## 第二趟真站复跑（带来源日志）

- 取值来源日志按预期出现：
  `填了 zipcode（任务资料「postal_code」（别名 → postcode））`、
  `填了 First name（任务资料「username」拆出（→ first_name））`。
- 但在第 125 步左右停在 `contact/personalization-question`
  （“What are you most interested in today?” 三选一，**这一屏没有 Next**），
  循环连着点了 8 次上一屏的 `Next` ⇒ 如实报失败。
  ⇒ 这是**「读到的是上一屏 / 点空气」**那一类（与本改动无关：只加日志与 `source` 优先，
  控制流没动）。另案。

## 第二刀（2026-10-29 下午）：三处「点空气 / 误报」

真站复跑暴露的三件事，模板与产物（`forms-222/sites/try.loop.py`，sha `229af4a2…`）同步：

1. **`click()` 不再谎报**：`cdp click` 的回话是 `Error: …` 时返回 False
   （原来不看回话、一律 True ⇒ trace 里每次 Next 都写着「成功」）。
2. **禁用态 Next 不再瞎点**：Next 禁用 + 没有可重打的格子 ⇒ **等**（`DISABLED_NEXT_WAIT`，
   上限 `BUSY_TIMEOUT`），而不是把「同一屏最多几次」的额度烧在禁用按钮上
   （第二趟就停在 `contact/personalization-question` 这种屏上连点）。
3. **`fill()` 的 `element not found` 短促重试**（`FILL_READY_TRIES=3`）：页面还没 hydrate
   完的那一下会回这句，旧实现立刻判失败（第一趟 step 1）；仍失败时把原因写进
   `_fill_why`，日志/trace 变成「没填上（cdp form 报错：…）」。
4. **点完 Next 先等页面变**（`_wait_for_change`，`NEXT_SETTLE_SECONDS=12`）：按「这一屏的
   形状」（URL + 题面 + 进度 + 格子 + 选项）轮询，变了才进下一轮 —— 不再 1.5 秒一次连点。
   题面每屏都一样的站（Free Eligibility Checker 型）也不会白等。
   测试/自测可用 `SITEFORGE_NEXT_SETTLE` 把预算压到 0（conftest 默认 0）。

测试：3 条新用例先红后绿；`test_loop_template + test_tool_loop` **65 passed / 5 skipped**。

## 还没做（另案，都不是这次的字邮编）

- **帮助/导航按钮被当成答案点**（第四趟 trace 实测 4 次：`Back`、`How does this work?`、
  `Why do we need to know?` ×2）。这一趟没挡路，但点开帮助弹层就可能卡住。
  建议：选项池排除问号结尾/帮助类按钮（`how|why|what.*\?|learn more|help`）。

- **插页 Next 连点 8 次不推进**（`age-interstitial-over-50-early-btr`）—— 已用旁路轮询器
  （0.6s/次）量清现场：

  ```
  12.9s  插页出现，Next enabled 但在视口外（top=441 / vh=437）
  14.2s  被 `_covered_before_acting` 滚进视口（top=197），`data-sf-pick=1` 一直打在它身上
  14.2–25.7s  页面一动不动，产物每 ~1.5s 点一次，8 次 cdp 全报成功
  页面在**停止点击之后**自己走到了下一屏
  ```

  同一时刻页面上有一批 **15–18 秒才失败**（`responseStatus: 0`）的第三方请求
  （doubleclick / taboola / bing / rudderlabs / applovin）。手工在上屏**单击一次**就能推进。
  ⇒ 更像「页面未就绪时点击被吞 / 点太多把过渡打断」，且与**本地网络**强相关
  （生产走美国代理时这些请求是通的）。下一步：点完先**等页面变不变**（URL/题面）
  再决定要不要再点，而不是 1.5 秒一次地连点。
