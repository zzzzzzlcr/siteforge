# Atticus `try.atticus.com/ssd/benefits`：循环型产物没上报 step（2026-10-30）

## 用户报的现象

「这个脚本好像没上报 step。」

## 根因（与别族对照）

生产那边靠 `common.report_url` 写 URL 记录看「任务走到哪了」。三族的做法：

| 族 | 上报点 | 例子 |
| --- | --- | --- |
| 固定步骤表（`agent/template.py`） | 每组判据成立、**动手之前** `_rpt_if_moved(name)`，**URL 变了才报** | `template.py:5079` |
| 手写固定步（`forms/sites/qualify.py`） | 每一步开头 `self._rpt("step", "第 N 步 …")` | `qualify.py:417+` |
| **循环型（`agent/template_loop.py` / 所有 `*.loop.py`）** | **只在 success / 收摊时报** | `try.loop.py` 只有 `navi failed` / `pre-steps failed` / `success` / `max_steps` |

⇒ 循环型一整趟跑下来，生产只看到最后一条 `success` 或 `max_steps`，中间 20–140 步全看不见。
`forms-222/sites/try.loop.py`、`agent/template_loop.py`、`compareinsulation` / `foodandwine` /
`goldenromances` 全是这个形状。

## 第二个根因（用户追问「这个站没上报 step / url」时量到的）

`formStep` 对**这个站的全部任务**都是 0 行（26919522 / 26919524 / 26829734 / 26786002 /
26940337 / 26956985），而固定步骤表的站（如 `www.gowizard.com/auto-warranty` 的
26287286…）有 4 行。差别不在上报点，在 **`task_id`**：

- 生产起 py 脚本（`ad-task.py::run_form_fill_subprocess`）只传
  `--correlation-id <task>_<ts>`，**不传 `--task-id`**；
- 固定步骤表那族自己拆：`self.tid = task_id or correlation_id.split('_')[0]`
  （`agent/template.py:2302`，线上固定脚本同）；
- 循环型原来只认 `task_id`（`agent/template_loop.py:554` / `try.loop.py:328`）
  ⇒ `tid` 恒空 ⇒ `report_url` 带 `task_id: ""` 被后端 **400「task_id 必填」** 拒掉
  ⇒ `formStep` 一条 step/url 都没有（用户看到的「只剩 success」是任务级的
  `/api/ai/task/complete`，不是产物上报）。

改：循环型也按同一条从 `correlation_id` 拆（模板 + 产物）。
测试：`test_the_task_id_comes_from_the_correlation_id_when_the_runner_does_not_pass_one`。
真站（correlation_id 用生产形状 `26999999_<ts>`）：10 条 `step` + 1 条 `success`，
`TIDS_OK True`（每条 `tid=26999999`）。

## 改了什么（模板 + 线上产物两处同改）

新增 `_rpt_if_moved(step, url, label)`：

```python
if not url or url == getattr(self, "_reported_url", ""):
    return None
label = "".join(ch for ch in str(label) if ch >= " ")   # 去掉选项 token 的 \x01 标记
self._reported_url = url
return self._rpt("step", "第 %d 步 %s" % (step, label))
```

四个成功动作后调用（`url` 用**这一轮已读到的**，不额外往返）：

- 填完：`第 N 步 填了 k 格`
- 选完：`第 N 步 答了第 k 题：<选项>`
- 点完 Next：`第 N 步 点了 Next（<题面>）`
- 结构化兜底真动手：`第 N 步 结构化兜底点了东西`

同一屏只报一次（URL 变了才报）—— 与固定步骤表那族同口径；**不报值**
（姓名/电话不进那条记录）。

文件：`agent/template_loop.py`（`_rpt_if_moved` + 4 个调用点）、
`forms-222/sites/try.loop.py`（同）。

## 测试

- 新增 `tests/test_loop_template.py::test_progress_is_reported_once_per_screen`：
  一屏答一题 ⇒ 恰好一条 `step`；同屏后续动作不重复报。
- `tests/test_loop_template.py + tests/test_tool_loop.py`：**68 passed, 5 skipped**（3m08s）。

- 新增 `test_the_task_id_comes_from_the_correlation_id_when_the_runner_does_not_pass_one`：
  `no_report=False`、只给 `correlation_id` ⇒ 每条上报的 `tid` 都是 `26956985`。

## 真站整单（siteforge-222 的 Bit 窗口，收集器代替生产接口）

`run_report_check.py`（本目录）把产物的 `report_url` 换成收集器、`no_report=False`：

- 第 1 趟：**50s，ok=True**，`REPORT_COUNT step=11 other=1`（落到 `rejection/legal-representation`）
- 第 2 趟（去掉 token 里的 `\x01` 之后）：**65s，ok=True**，`step=17 other=1`

节选：

```
第 1 步 填了 1 格
第 4 步 点了 Next（Atticus got 334 New Yorkers approved for）
第 9 步 答了第 1 题：Myself
第 15 步 填了 2 格
第 19 步 答了第 3 题：Yes
第 30 步 答了第 13 题：Yes
success（…/survey/qual/post-conversion-ah 或 rejection/…）
```

## 推 FMR（2026-10-30）

```
LOCAL   forms-222/sites/try.loop.py  94777 bytes  sha256 3665dcc52e75ffad…
BEFORE  153f144e2e5a981a…  93641 bytes   （留底 fmr_before_153f144e.py）
WRITE   status 200 / success / config_id 162 / created:false
READBACK 3665dcc52e75ffad…  逐字节一致（94777 字节）
AFTER   config 162 sites 1 [{site: try.atticus.com/ssd/benefits, config_id: 162, status: 1}]
```
结果：`fmr_push_result.json`。

第二次推（`task_id` 修复）：

```
LOCAL   forms-222/sites/try.loop.py  95247 bytes  sha256 da95aad7ea641b76…
BEFORE  3665dcc52e75ffad…  94777 字节  （留底 fmr_before_3665dcc5.py）
WRITE   status 200 / success / config_id 162 / created:false
READBACK da95aad7ea641b76…  逐字节一致（95247 字节）
AFTER   config 162 sites 1 [{site: try.atticus.com/ssd/benefits, config_id: 162, status: 1}]
```

结果：`fmr_push_result2.json`。

