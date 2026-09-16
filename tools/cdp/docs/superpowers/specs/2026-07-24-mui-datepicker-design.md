# Spec: form --value 支持 MUI DatePicker

## 背景

`cdp form --value` 目前对所有元素走逐字输入流程。MUI DatePicker 使用
`<input type="tel">` + 日历按钮 + 动态弹窗的交互模式，无法逐字输入。

## 目标

`cdp form '#dob-field' --value '2026-07-04'` 自动检测 DOM 特征，识别为
datepicker 后走 click-button→navigate-month→click-day 流程。

## 设计

### 触发条件（DOM 驱动）

在 `FillText` 入口 scroll+wait 之后，执行 JS 检测，全部满足才走日期流程：

**Step 0 — 解析目标元素**：selector 可能是 wrapper 也可能是 input 本身：
- `el.tagName === 'INPUT'` → 直接用 el
- 否则 → `el.querySelector('input[type="tel"]')` 或 `el.querySelector('input')`

**Step 1 — 检测 input 特征**：
- `inputEl.type === 'tel'`
- `inputEl.placeholder` 匹配 `/mm-dd-yyyy|yyyy-mm-dd|MM-DD-YYYY/i`

**Step 2 — 检测日历按钮**：以 input 为起点，用 `closest('[class*="MuiInputBase"], [class*="InputBase"], .MuiFormControl-root, form, .field')` 找到容器，在容器内搜索：
- `[onclick*="calendar"], [onclick*="toggle"], [onclick*="picker"]`
- `[aria-label*="date"], [aria-label*="calendar"], [aria-label*="choose"]`
- 搜到 → 记录按钮的 bounding rect，继续；搜不到 → 退回逐字输入

不满足 → 退回现有 `FillText` 逐字输入流程。

### 日期解析

`--value` 值先按 `-` split：

| 段数 | 示例 | 判断逻辑 | 结果 |
|------|------|----------|------|
| 3 | `2026-07-04` | 第一段 > 31 → YMD | year=2026, month=07, day=04 |
| 3 | `07-04-2026` | 第一段 ≤ 12 → MDY | year=2026, month=07, day=04 |
| 2 | `07-04` | 月-日 | year=当前年, month=07, day=04 |
| 其他 | `2026/07/04` | 无法解析 | 退回逐字输入 |

> **YMD vs MDY 歧义**：当三段都不是 4 位数年份时（如 `01-02-03`），
> 优先按 YMD 解析（year=2003, month=01, day=02）。LLM/API 默认输出
> YMD 格式的概率高于 MDY，此规则在实现时确定。

### 日期流程

1. **找日历按钮**：在 input 的容器（`closest('[class*="MuiInputBase"], [class*="InputBase"], .MuiFormControl-root, form, .field')`）内，按优先级搜索：
   - a) `[aria-label*="date"], [aria-label*="calendar"], [aria-label*="choose"]`
   - b) `[onclick*="calendar"], [onclick*="toggle"], [onclick*="picker"]`
   - 返回按钮的 bounding rect

2. **Click 按钮**：`DispatchMouseClick`，打开日历弹窗。**Poll** 等待 `.calendar-popup._open_` 或 `[role="dialog"]` 可见（每 50ms 检查一次，超时 2s 返回 error）。不硬等，慢页面 / lazy render 不会误判

3. **月份导航**：JS 在 **document 级别**搜弹窗（`.calendar-popup._open_` 或 `[role="dialog"]` 中可见者）。读取当前显示的月份/年份（data-month/data-year 或解析标题文本）。对比目标月份：
   - 目标在前 → click 前月按钮（`.calendar-header button:first-child` 或 `[onclick*="changeMonth(-1)"]`）
   - 目标在后 → click 后月按钮（`.calendar-header button:last-child` 或 `[onclick*="changeMonth(1)"]`）
   - **Disabled 检测**：点击前检查按钮是否有 `disabled` 属性或 `.Mui-disabled`、`[aria-disabled="true"]` class——有则返回 error 而非死循环
   - 最多 12 次迭代（一年范围），超限返回 error

4. **Click 日期**：在弹窗内（document 级别，不限 wrapper）搜索目标日期格子：
   - class 匹配：`.calendar-day`、`.MuiPickersDay-root`、`.ant-picker-cell`（任一命中）
   - 过滤：不含 `_empty_`、`Mui-disabled`、`.ant-picker-cell-disabled`
   - textContent.trim() === targetDay（字符串）
   - 返回 bounding rect，`DispatchMouseClick`

5. **不做 close**：页面自己的 handler（`pickDate()` 等）会关弹窗 + 设值

### 检测 JS: `isDatePickerJS`

```
输入: selector
输出: {isDatePicker: true, inputSelector: "#dob-input", buttonX, buttonY, ...}
      或 {isDatePicker: false}
检测:
  - Step 0: 解析目标 → 找到 input
  - Step 1: input.type==='tel' && placeholder 正则
  - Step 2: 搜日历按钮（onclick + aria-label 双模式）
  - 返回 input 的真实 selector + 按钮 bounding rect
```

### 月份导航 JS: `navigateCalendarMonthJS`

```
输入: targetMonth (0-11), targetYear
输出: {success: true, month: N, year: YYYY}
      或 {error: "..."}
逻辑:
  - document 级别搜弹窗: .calendar-popup._open_ / [role="dialog"]
  - 读 data-month/data-year 或解析标题
  - 循环导航，每次点击前检查 disabled
  - 最多 12 次
```

### 日期选择 JS: `pickCalendarDayJS`

```
输入: targetDay (1-31)
输出: {found: true, centerX, centerY}
      或 {error: "day N not found in calendar"}
逻辑:
  - document 级别搜弹窗内所有候选 class 的格子
  - 过滤 disabled/_empty_
  - 匹配 textContent.trim() === targetDay
  - 返回 bounding rect
```

### 涉及文件

| 文件 | 改动 |
|------|------|
| `internal/form.go` | `FillText` 加日期分支；新增 `isDatePickerJS`、`navigateCalendarMonthJS`、`pickCalendarDayJS` |
| `internal/form.go` | 新增 `parseDateValue(s string) (year, month, day int, ok bool)` 纯 Go 函数 |
| `internal/form_test.go` | 加单元测试：日期解析、JS 生成函数 |
| `internal/form_integration_test.go` | 加集成测试：mui-datepicker 页面选日期 |

### 测试策略

**单元测试**（不需要 Chrome）：
- `TestParseDateValue` — YMD/MDY/2段/无效 各一个 case
- `TestIsDatePickerJS` — 检查 JS 含 `type==='tel'` + placeholder 正则 + aria-label 搜索 + onclick 回退
- `TestNavigateCalendarMonthJS` — 含月份匹配 + 导航按钮定位 + disabled 检测
- `TestPickCalendarDayJS` — 含多 class 候选 + empty/disabled 过滤 + textContent 匹配

**集成测试**（需要 Chrome + localhost:8080/mui-datepicker）：
- `TestFillText_DatePicker_YMD`：`cdp form '#dob-field' --value '2026-07-04'`，验证 `#dob-input` value 为 `07-04-2026`
- `TestFillText_DatePicker_MDY`：`cdp form '#dob-field' --value '07-04-2026'`，验证 `#dob-input` value 为 `07-04-2026`
- `TestFillText_DatePicker_MD`：`cdp form '#dob-field' --value '07-04'`，验证当年
- `TestFillText_PlainInput_StillWorks`：对普通 text input 仍然逐字输入

### 不涉及

- 不支持范围选择（date range picker）
- 不支持时间选择器（time picker）
- 不新增 CLI flag
- 不修改 `cmd/form.go`
