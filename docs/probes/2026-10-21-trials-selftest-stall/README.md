# 2026-10-21 · trials 自测「走到一半就不动了」：反打转刹车误杀 + 诊断被软行顶掉 + 死帧每步白等 45 秒

- 任务：`job-6c2335e72ba9`（8099，site `trials`，`https://trials.lilly.com/en-US/trial/737635`）
- 结论状态：**已定位并修复**（工作树，未提交）；8100 复验见 §五
- 相关：`agent/template.py`、`tests/test_template.py`、`fixtures/reference_site.py`

## 一、现场：不是卡死，12:03 就结束了

`GET /job/job-6c2335e72ba9` 是 `status=done`、`end_reason=repair_noop`。时间线（CST）：

- 11:45 发起 → 11:55 出稿 → 11:55–12:01 自测基线 → 12:01 diagnose → 12:03 `repair_noop` 停

自测 trace（容器时区 UTC）只有 13 行，最后一行是第 8 步的 click；同目录 `.log` 的最后一行是：

```
04:01:12 [ERROR] 连着 3 步点了页面都没动，收摊（原地打转）
```

整条流程 38 步，问卷那组本身 27 步 ⇒ 看起来像「走到一半」。

## 二、真因：`cdp diff` 不判选择，反打转刹车误杀

第 4/6/8 步是三次选项点击（`40 to 75 years old` / `Yes` / `1 year ago or more`），都在
Trialbee 的跨站 iframe（`widget.trialbee.com/selfAssessment.html`）里。三次**都点上了**：

- `6-after.png`：Q2「Yes」已选中；`8-after.png`：Q3「1 year ago or more」已选中
- 问卷是「一次把所有题渲染出来、滚动显示」的形态 ⇒ 点选只改 radio 的选中态，
  URL / 可见正文 / 元素集合都不变
- `cdp diff --help` 自己写着：**「它判的是导航与组成变化，不判填写与选择」**
  ⇒ 三次都 `actionable=false` ⇒ 模板的 `stalled >= 3` 刹车触发（`template.py:4698`，
  `DIFF_JUDGES = ("click", "goto")`，见 `template.py:1295`）

另外三个点击都在跨站子帧里，`cdp click` 的回执本来就是 `landing_blind: true`
（「落点判据用不了」）—— 这一格在 trace 的 note 里可见。

## 三、为什么面板写的是「同意弹层没关掉」

那个「盖满视口的同意弹层」是 Lilly 的 Cassie cookie 条，被 Trialbee 问卷的 iframe 盖在下面。
钩子每一步都试着点它的 `#cassie_reject_all_pre_banner`，`cdp click` 因为落点被 iframe 挡住
而拒绝（探路账本 step 15 的原话：「那一点上站着的是 `tb-self-assessment` iframe」）。
它只是 `soft` 噪声，不是停下的原因。

但 `stalled` 早停**只写日志、不写 trace**。`selftest._verdict` 找不到承重失败行，
就回退到第一条 `ok=false` 的软行（`selftest.py:1165`）⇒ note 成了
「卡在第 None 步：…同意弹层…」。diagnose 因此把失败族判成 `overlay`，改稿被指向
「去修同意弹层」——可弹层根本不在产物里，模型两轮交回原稿 ⇒ `repair_noop`。

用当前 `selftest._verdict` 重放那份 trace，逐字复现面板那句 note；补一行「收摊」的
trace 行后，note 立刻变成「卡在第 8 步：连着 3 步…」——链条闭合。

## 四、附带的耗时问题：死帧上白等 45 秒/步

账本里的 `frame_id` 只活在录它的那次会话里；重放一开始页面重载、子帧重建 ⇒ 全是死号。
而 `late_element` 给这一组 45 秒预算：click/form 的快路只在**死帧**里重试，
等满 39 秒才走 `_relocate`（重新 observe、用活帧重定位）。

现场：8 步跑了 6 分钟（每步约 39–45 秒）。按这个速度，就算不误杀，整条 38 步
也会撞 600 秒的自测上限。`scroll` 那条早就有「声明选择器 × 活帧」的兜底，
click/form 没有排在等待之前。

## 五、修复（三处 + 复验中新撞到的第四处，都在 `agent/template.py`）

1. **收摊先落 trace**（`Filler._stop`）：`stalled` / `stuck` 早停写一行
   `action="give_up"`、`ok=false`、`note` + `progress_why` 两格同写。
   `selftest._verdict` 于是能挑到承重失败行，面板/诊断拿到真因。
2. **跨站子帧的 `progress=false` 降为 `None`**（`_landing_blind` + `_run_step`）：
   回执里 `landing_blind=true` 时，`cdp diff` 的 `actionable=false` 不能当「没推进」。
   降级排在 `DIFF_POLL_SECONDS`（5 秒）重试**之前** —— 排在后面时每个盲点步白等 5 秒。
   `_scroll` 的成功回执也带这一格（滚动步同样省掉白等）。
3. **死帧先试活帧**（`_try_declared_in_live_frames`）：click/form 在
   `frame not in live_frames` 时，先把声明选择器拿到活帧里试一遍，再谈
   `_wait_until_present`；与 `_scroll` 那条兜底同一跳。
4. **`cdp form --strict` 的 datepicker 假错**（`_count_matches` + `_do` 的 form 分支）：
   8100 复验跑到第 31 步（Trialbee 联系表单）时，`cdp form` 带 `--strict` 回
   `Error: datepicker detection: no input found in wrapper`，**同一选择器不带 strict
   就能填**。退回非严格之前先数命中：**唯一命中**才退；多个 / 数不出来照旧大声失败
   （严格闸「命中多个不许静默挑第一个」的理由不拆）。
   - 测试：`test_a_strict_form_that_hits_the_cdp_datepicker_bug_is_retried_without_strict`
     （正控：唯一命中 ⇒ 填进去；负控：命中 2 个 ⇒ 一步都不填）

测试（`tests/test_template.py`）：

- `test_stalled_early_stop_leaves_a_terminal_trace_row`（收摊行在、话对、不带 soft/aux）
- `test_landing_blind_clicks_are_not_counted_as_no_progress`（4 个盲点步都走完、
  `progress=None`、`progress_why` 有话、没有收摊行；并钉「<10 秒」防 5 秒白等回潮）
- `test_declared_selectors_are_tried_in_live_frames_before_waiting`
  （把 `_wait_until_present` 换成一叫就炸的桩 —— 帧号漂了时它不该被叫到）
- `test_shots_only_for_failed_steps` 更新：截图判据只看动作行，`give_up` 行单独钉

`fixtures/reference_site.py` 已按新骨架重渲（`python3 tests/test_template.py`）。
宿主与 8100 容器里**新增 4 条 + 更新 1 条**用例全过；受影响子集 **28 passed**（4:53）。

## 六、8100 复验（三趟，都是 `mode=fix` + `fix_local_py`，不重新探路）

做法：把被测底稿放进 `forms-222/sites/trials.py`，重启 8100 载入新骨架，
`POST /run` 用 fix 模式按底稿的 STATES/FILLS 重渲，再 lint / 自测（自测会真提交一次）。

| 趟 | job | 结果 | 说明 |
|---|---|---|---|
| 1 | `job-a3a5cb586839` | 跑到第 31 步，**600 秒超时** | 三条行为修复已生效（不再是第 8 步收摊）；但「降级排在 5 秒重试之后」还没修，38 步里 20 步各白等 5 秒 ⇒ 撞满上限 |
| 2 | `job-e892e399ccb5` | 8 分 16 秒，第 33 步失败 | 5 秒白等已修，跑到联系表单；`cdp form --strict` 的 datepicker 假错把 First/Last name 卡住（第四处） |
| 3 | `job-86fef6916bea` | **通过**（6 分 46 秒），`selftest.passed=1`，deliver 放行 | 联系表单 5 格全填上（fallback level 2/3，活帧），第 38 步 Submit 后判据成立 |

产物：`forms-222/sites/trials.py`，sha256 `fe5edf9186d452b8…`，341829 字节，
`SKELETON_SHA = af1e560d19524b8e`，`PROVENANCE.selftest = {runs:1, passed:1,
submissions:1, verdict:true}`。**没有上传 FMR、没动 8099。**

