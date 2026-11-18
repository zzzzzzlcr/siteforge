# 交接包 · homejolly

- 停因：`repair_noop`
- 产物：`/opt/siteforge/forms/loopcheck/homejolly.parked.py`（**没验过**：自测没过 —— 图停下来等人回话）
- 站点：https://homejolly.com/lp/walkinshower7
- 模式：`fix`
- 轮次：`diagnoses=1` `lint_bounces=0` `submissions=1`
- 失败指纹：`点名:填过了`（同一指纹连着两次 = 再改只会撞同一堵墙）

## 自测每一遍（逐字）

| 遍 | 结果 | 卡在第几步 | trace | 说明 |
|---|---|---|---|---|
| 第 1 遍：正常跑一遍（基线） | failed | 10 | `/opt/siteforge/runtime/selftest/homejolly-20261106-082937/baseline/homejolly.baseline.trace.jsonl` | 卡在第 10 步：这一格没填上（不是「填过了」）：回读是 '1 (450)'，不是填进去的 '1450 Sunset Blvd' |
| 第 2 遍：在同一个页面上接着再跑一遍（打状态残留 / 首次加载假设） | not_needed | — | — | 这一遍没跑：这一版**只跑基线**（人裁）：这一类扰动这一次不跑。它打的那一类失败（状态残留 / 首次加载假设）这一次**没验到**。 |
| 第 3 遍：每一步多停一会儿再跑（打时序竞争 / 没等就点） | not_needed | — | — | 这一遍没跑：这一版**只跑基线**（人裁）：这一类扰动这一次不跑。它打的那一类失败（时序竞争 / 没等就点）这一次**没验到**。 |
| 第 4 遍：换个窗口大小再跑（打折叠 / 遮挡 / 坐标假设） | not_needed | — | — | 这一遍没跑：这一版**只跑基线**（人裁）：这一类扰动这一次不跑。它打的那一类失败（折叠 / 遮挡 / 坐标假设）这一次**没验到**。 |
| 第 5 遍：换个代理国家再跑（打地区内容差异） | not_needed | — | — | 这一遍没跑：这一版**只跑基线**（人裁）：这一类扰动这一次不跑。它打的那一类失败（地区内容差异）这一次**没验到**。 |

## 最后一路诊断（逐字）

```json
{
 "failed_step": 10,
 "family": "fill",
 "note": "卡在第 10 步：这一格没填上（不是「填过了」）：回读是 '1 (450)'，不是填进去的 '1450 Sunset Blvd'",
 "per_run": [
  {
   "failed_step": 10,
   "name": "baseline",
   "note": "卡在第 10 步：这一格没填上（不是「填过了」）：回读是 '1 (450)'，不是填进去的 '1450 Sunset Blvd'",
   "ok": false,
   "status": "failed"
  },
  {
   "failed_step": null,
   "name": "rerun",
   "note": "这一遍没跑：这一版**只跑基线**（人裁）：这一类扰动这一次不跑。它打的那一类失败（状态残留 / 首次加载假设）这一次**没验到**。",
   "ok": null,
   "status": "not_needed"
  },
  {
   "failed_step": null,
   "name": "delay",
   "note": "这一遍没跑：这一版**只跑基线**（人裁）：这一类扰动这一次不跑。它打的那一类失败（时序竞争 / 没等就点）这一次**没验到**。",
   "ok": null,
   "status": "not_needed"
  },
  {
   "failed_step": null,
   "name": "viewport",
   "note": "这一遍没跑：这一版**只跑基线**（人裁）：这一类扰动这一次不跑。它打的那一类失败（折叠 / 遮挡 / 坐标假设）这一次**没验到**。",
   "ok": null,
   "status": "not_needed"
  },
  {
   "failed_step": null,
   "name": "country",
   "note": "这一遍没跑：这一版**只跑基线**（人裁）：这一类扰动这一次不跑。它打的那一类失败（地区内容差异）这一次**没验到**。",
   "ok": null,
   "status": "not_needed"
  }
 ],
 "run": "baseline",
 "say": "第 1 遍：正常跑一遍（基线） 这一遍挂了：卡在第 10 步 —— 卡在第 10 步：这一格没填上（不是「填过了」）：回读是 '1 (450)'，不是填进去的 '1450 Sunset Blvd'",
 "submissions": 1,
 "局部修复": {},
 "截图": [],
 "现场": {
  "为什么是这一步": "这一遍**没有硬失败**（没有 `ok=false` 的行）；摆的是第 10 步那条「点了但页面没动」的**嫌疑 click**（`ok=true` / `progress=false`）",
  "判不出来时为什么": null,
  "动作": "form",
  "哪一步": 10,
  "实际用的选择器": null,
  "当时网址": null,
  "当时页面上这段字（前 600 字）": null,
  "当时页面上这段字（末尾 240 字）": null,
  "现场": null,
  "脚本自己那句话": "填了 \u0004email（账本示例）",
  "这一步往前推了没有": false,
  "这一步的目标": {
   "testid": "\u0004email"
  },
  "重找记录": null
 }
}
```

## 接手须知

- 要改的是上面那份 py（盘上的现状），不是重跑整条漏斗。
- 不许放松成功判据；`--ws-url / --form-file / --correlation-id / --log-level`
  与 `sys.exit(0 if f.run() else 1)` 的形状要保住（`agent/fix.py::PATCH_DISCIPLINE`）。
- 改完走服务那条 `mode=fix` 重验；只跑一遍不算验过（同一环境要复跑）。
