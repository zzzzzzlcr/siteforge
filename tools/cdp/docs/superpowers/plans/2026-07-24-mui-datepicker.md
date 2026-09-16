# MUI DatePicker form --value 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `cdp form '#dob-field' --value '2026-07-04'` 自动检测 DOM（type=tel + placeholder 日期模式 + 日历按钮），识别 datepicker 后走 click-button→poll-popup→navigate-month→click-day 流程。

**Architecture:** `FillText` 在 scroll+wait 后插入日期检测分支（`isDatePickerJS`）→ 是则走日期流程：点击按钮 → poll 弹窗 → `navigateCalendarMonthJS` 月份导航 → `pickCalendarDayJS` 点日期；否则退回逐字输入。

**Tech Stack:** Go 1.26.2, chromedp, cdproto

## Global Constraints

- 复用 `FillText(selector, text, frameID, track)` 签名，不改变外部接口
- 所有 JS 生成函数放在 `internal/form.go`，保持 `build*JS` 命名惯例
- 不新增 CLI flag，不修改 `cmd/form.go`
- 集成测试需要 Chrome + `localhost:8080/mui-datepicker` 可用

---

## 文件结构

| 文件 | 职责 | 操作 |
|------|------|------|
| `internal/form.go` | `parseDateValue`、`isDatePickerJS`、`navigateCalendarMonthJS`、`pickCalendarDayJS`；`FillText` 分支 | 修改 |
| `internal/form_test.go` | 日期解析 + JS 生成函数的单元测试 | 修改 |
| `internal/form_integration_test.go` | mui-datepicker 集成测试 | 追加 |

### Task 1: `parseDateValue` — 日期字符串解析

**Files:**
- Modify: `internal/form.go` (after `escapeJS`)
- Modify: `internal/form_test.go`

**Interfaces:**
- Produces: `func parseDateValue(s string) (year, month, day int, ok bool)` — 解析 `-` 分隔的日期字符串

- [ ] **Step 1: 写单元测试**

在 `internal/form_test.go` 末尾追加：

```go
func TestParseDateValue(t *testing.T) {
	tests := []struct {
		input          string
		year, month, day int
		ok             bool
	}{
		{"2026-07-04", 2026, 7, 4, true},   // YMD
		{"07-04-2026", 2026, 7, 4, true},   // MDY
		{"12-25-2025", 2025, 12, 25, true},  // MDY
		{"01-02-03",   2003, 1, 2, true},    // ambiguous → YMD preferred
		{"07-04",      0, 7, 4, true},       // MD only, year=0 (caller fills current year)
		{"2026/07/04", 0, 0, 0, false},      // invalid separator
		{"abc",        0, 0, 0, false},       // not a date
		{"2026-13-01", 0, 0, 0, false},      // invalid month
	}
	for _, tt := range tests {
		y, m, d, ok := parseDateValue(tt.input)
		if ok != tt.ok || (ok && (y != tt.year || m != tt.month || d != tt.day)) {
			t.Errorf("parseDateValue(%q) = (%d,%d,%d,%v), want (%d,%d,%d,%v)",
				tt.input, y, m, d, ok, tt.year, tt.month, tt.day, tt.ok)
		}
	}
}
```

- [ ] **Step 2: 运行测试验证失败**

```bash
export PATH=$PATH:/usr/local/go/bin
go test ./internal/ -run TestParseDateValue -v -count=1
```
期望：编译失败，`parseDateValue` 未定义。

- [ ] **Step 3: 实现 `parseDateValue`**

在 `internal/form.go` 的 `escapeJS` 之后追加：

```go
// parseDateValue parses a dash-separated date string into year, month, day.
// Supports YMD (2026-07-04), MDY (07-04-2026), and MD (07-04) formats.
// For MD-only input, year is returned as 0 — caller fills in current year.
// When format is ambiguous (no 4-digit year), YMD is preferred.
func parseDateValue(s string) (year, month, day int, ok bool) {
	parts := strings.Split(s, "-")
	if len(parts) < 2 || len(parts) > 3 {
		return 0, 0, 0, false
	}

	nums := make([]int, len(parts))
	for i, p := range parts {
		n, err := strconv.Atoi(p)
		if err != nil || n < 0 {
			return 0, 0, 0, false
		}
		nums[i] = n
	}

	if len(parts) == 2 {
		month, day = nums[0], nums[1]
		if month < 1 || month > 12 || day < 1 || day > 31 {
			return 0, 0, 0, false
		}
		return 0, month, day, true // year=0, caller fills current year
	}

	// 3 segments
	a, b, c := nums[0], nums[1], nums[2]
	if a > 31 {
		// YMD: first segment is year
		year, month, day = a, b, c
	} else if c > 31 {
		// MDY: last segment is year
		year, month, day = c, a, b
	} else {
		// Ambiguous: prefer YMD
		year, month, day = c, a, b
	}
	if month < 1 || month > 12 || day < 1 || day > 31 {
		return 0, 0, 0, false
	}
	return year, month, day, true
}
```

需在文件顶部 import 中追加 `"strconv"`。

- [ ] **Step 4: 运行测试验证通过**

```bash
go test ./internal/ -run TestParseDateValue -v -count=1
```
期望：PASS。

- [ ] **Step 5: 提交**

```bash
git add internal/form.go internal/form_test.go
git commit -m "feat: add parseDateValue for YMD/MDY/MD date parsing"
```

---

### Task 2: `isDatePickerJS` — DOM 检测是否为 datepicker

**Files:**
- Modify: `internal/form.go`
- Modify: `internal/form_test.go`

**Interfaces:**
- Produces: `func isDatePickerJS(selector string) string` — 返回 JS 字符串
- 执行后返回：`{isDatePicker: true, inputSelector: "#dob-input", buttonX, buttonY, centerX, centerY, width, height}` 或 `{isDatePicker: false}`

- [ ] **Step 1: 写单元测试**

在 `internal/form_test.go` 末尾追加：

```go
func TestIsDatePickerJS(t *testing.T) {
	js := isDatePickerJS("#dob-field")
	if !strings.Contains(js, `type === 'tel'`) {
		t.Errorf("missing tel type check: %s", js)
	}
	if !strings.Contains(js, "placeholder") {
		t.Errorf("missing placeholder check: %s", js)
	}
	if !strings.Contains(js, "mm-dd-yyyy") {
		t.Errorf("missing date pattern: %s", js)
	}
	if !strings.Contains(js, `[aria-label*="date"]`) {
		t.Errorf("missing aria-label* date search: %s", js)
	}
	if !strings.Contains(js, `[aria-label*="calendar"]`) {
		t.Errorf("missing aria-label* calendar search: %s", js)
	}
	if !strings.Contains(js, `[aria-label*="choose"]`) {
		t.Errorf("missing aria-label* choose search: %s", js)
	}
	if !strings.Contains(js, `[onclick*="calendar"]`) {
		t.Errorf("missing onclick fallback: %s", js)
	}
	if !strings.Contains(js, `'isDatePicker'`) {
		t.Errorf("missing isDatePicker key: %s", js)
	}
	if !strings.Contains(js, `closest`) {
		t.Errorf("missing closest() container search: %s", js)
	}
}
```

- [ ] **Step 2: 运行测试验证失败**

```bash
go test ./internal/ -run TestIsDatePickerJS -v -count=1
```
期望：编译失败。

- [ ] **Step 3: 实现 `isDatePickerJS`**

在 `internal/form.go` 的 `parseDateValue` 之后追加：

```go
// isDatePickerJS generates JS to detect whether a form element has an attached
// datepicker (MUI-style: type=tel + date-pattern placeholder + calendar button).
// Resolves wrapper→input if needed, searches the input's container for a
// calendar trigger button via aria-label and onclick attributes.
func isDatePickerJS(selector string) string {
	escaped := escapeJS(selector)
	return fmt.Sprintf(`(function(){
		var el = document.querySelector('%s');
		if (!el) return JSON.stringify({error: 'element not found'});

		// Step 0: resolve to input element
		var input = el;
		if (el.tagName !== 'INPUT') {
			input = el.querySelector('input[type="tel"]') || el.querySelector('input');
		}
		if (!input) return JSON.stringify({error: 'no input found in wrapper'});

		// Step 1: check input characteristics
		if (input.type !== 'tel') return JSON.stringify({isDatePicker: false});
		var ph = (input.placeholder || '').toLowerCase();
		if (!/mm-dd-yyyy|yyyy-mm-dd/i.test(ph)) return JSON.stringify({isDatePicker: false});

		// Step 2: search for calendar button in container
		var container = input.closest('[class*="MuiInputBase"], [class*="InputBase"], .MuiFormControl-root, form, .field');
		if (!container) container = input.parentElement;
		var btn = container.querySelector('[aria-label*="date"], [aria-label*="calendar"], [aria-label*="choose"], [onclick*="calendar"], [onclick*="toggle"], [onclick*="picker"]');
		if (!btn) return JSON.stringify({isDatePicker: false});

		var r = btn.getBoundingClientRect();
		if (r.width === 0 || r.height === 0) return JSON.stringify({isDatePicker: false});

		// Return unique selector for the input for later use
		var inputId = input.id ? ('#' + input.id) : '';
		return JSON.stringify({isDatePicker: true, inputSelector: inputId, buttonX: r.x, buttonY: r.y, centerX: r.x+r.width/2, centerY: r.y+r.height/2, width: r.width, height: r.height});
	})()`, escaped)
}
```

- [ ] **Step 4: 运行测试验证通过**

```bash
go test ./internal/ -run TestIsDatePickerJS -v -count=1
```
期望：PASS。

- [ ] **Step 5: 提交**

```bash
git add internal/form.go internal/form_test.go
git commit -m "feat: add isDatePickerJS for DOM-driven datepicker detection"
```

---

### Task 3: `navigateCalendarMonthJS` — 月份导航

**Files:**
- Modify: `internal/form.go`
- Modify: `internal/form_test.go`

**Interfaces:**
- Produces: `func navigateCalendarMonthJS(targetMonth, targetYear int) string` — 返回 JS 字符串
- 执行后返回：`{success: true, month: N, year: YYYY}` 或 `{error: "..."}`

- [ ] **Step 1: 写单元测试**

在 `internal/form_test.go` 末尾追加：

```go
func TestNavigateCalendarMonthJS(t *testing.T) {
	js := navigateCalendarMonthJS(6, 2026) // target: July 2026
	if !strings.Contains(js, "calendar-popup") {
		t.Errorf("missing calendar-popup search: %s", js)
	}
	if !strings.Contains(js, `[role="dialog"]`) {
		t.Errorf("missing role=dialog fallback: %s", js)
	}
	if !strings.Contains(js, "data-month") {
		t.Errorf("missing data-month read: %s", js)
	}
	if !strings.Contains(js, "disabled") {
		t.Errorf("missing disabled check: %s", js)
	}
	if !strings.Contains(js, "Mui-disabled") {
		t.Errorf("missing Mui-disabled check: %s", js)
	}
	if !strings.Contains(js, `targetMonth === 6`) {
		t.Errorf("missing target month: %s", js)
	}
	if !strings.Contains(js, `targetYear === 2026`) {
		t.Errorf("missing target year: %s", js)
	}
	if !strings.Contains(js, "calendar popup not found") {
		t.Errorf("missing popup error: %s", js)
	}
}
```

- [ ] **Step 2: 运行测试验证失败**

```bash
go test ./internal/ -run TestNavigateCalendarMonthJS -v -count=1
```
期望：编译失败。

- [ ] **Step 3: 实现 `navigateCalendarMonthJS`**

在 `internal/form.go` 的 `isDatePickerJS` 之后追加：

```go
// navigateCalendarMonthJS generates JS to navigate a calendar popup to the
// target month/year. Searches document-level for the open popup, reads current
// month/year from data attributes or header text, then clicks prev/next buttons
// until the target is reached. Checks for disabled buttons before each click.
// Max 12 iterations to prevent infinite loops.
func navigateCalendarMonthJS(targetMonth, targetYear int) string {
	return fmt.Sprintf(`(function(){
		var popup = document.querySelector('.calendar-popup._open_') || document.querySelector('[role="dialog"]');
		if (!popup) return JSON.stringify({error: 'calendar popup not found'});

		var targetMonth = %d;
		var targetYear = %d;

		for (var i = 0; i < 12; i++) {
			var curMonth = parseInt(popup.getAttribute('data-month'));
			var curYear = parseInt(popup.getAttribute('data-year'));
			if (isNaN(curMonth) || isNaN(curYear)) {
				// Try parsing header text
				var hdr = popup.querySelector('.calendar-month, .MuiPickersCalendarHeader-label');
				if (hdr) {
					var parts = hdr.textContent.trim().split(' ');
					if (parts.length >= 2) {
						curMonth = ['January','February','March','April','May','June','July','August','September','October','November','December'].indexOf(parts[0]);
						curYear = parseInt(parts[1]);
					}
				}
			}
			if (isNaN(curMonth) || isNaN(curYear)) return JSON.stringify({error: 'cannot read calendar month/year'});

			if (curYear === targetYear && curMonth === targetMonth) {
				return JSON.stringify({success: true, month: curMonth, year: curYear});
			}

			// Determine direction
			var isForward = (targetYear > curYear) || (targetYear === curYear && targetMonth > curMonth);
			var btn;
			if (isForward) {
				btn = popup.querySelector('.calendar-header button:last-child') ||
				      popup.querySelector('[onclick*="changeMonth(1)"]');
			} else {
				btn = popup.querySelector('.calendar-header button:first-child') ||
				      popup.querySelector('[onclick*="changeMonth(-1)"]');
			}
			if (!btn) return JSON.stringify({error: 'navigation button not found'});
			if (btn.disabled || btn.classList.contains('Mui-disabled') || btn.getAttribute('aria-disabled') === 'true') {
				return JSON.stringify({error: 'navigation button is disabled — month out of range'});
			}
			btn.click();
		}
		return JSON.stringify({error: 'exceeded 12 month navigation iterations'});
	})()`, targetMonth, targetYear)
}
```

- [ ] **Step 4: 运行测试验证通过**

```bash
go test ./internal/ -run TestNavigateCalendarMonthJS -v -count=1
```
期望：PASS。

- [ ] **Step 5: 提交**

```bash
git add internal/form.go internal/form_test.go
git commit -m "feat: add navigateCalendarMonthJS for calendar month navigation"
```

---

### Task 4: `pickCalendarDayJS` — 日期选择

**Files:**
- Modify: `internal/form.go`
- Modify: `internal/form_test.go`

**Interfaces:**
- Produces: `func pickCalendarDayJS(targetDay int) string` — 返回 JS 字符串
- 执行后返回：`{found: true, centerX, centerY}` 或 `{error: "day N not found"}`

- [ ] **Step 1: 写单元测试**

在 `internal/form_test.go` 末尾追加：

```go
func TestPickCalendarDayJS(t *testing.T) {
	js := pickCalendarDayJS(4)
	if !strings.Contains(js, "calendar-day") {
		t.Errorf("missing calendar-day class: %s", js)
	}
	if !strings.Contains(js, "MuiPickersDay-root") {
		t.Errorf("missing MuiPickersDay-root class: %s", js)
	}
	if !strings.Contains(js, "ant-picker-cell") {
		t.Errorf("missing ant-picker-cell class: %s", js)
	}
	if !strings.Contains(js, "_empty_") {
		t.Errorf("missing _empty_ filter: %s", js)
	}
	if !strings.Contains(js, "Mui-disabled") {
		t.Errorf("missing Mui-disabled filter: %s", js)
	}
	if !strings.Contains(js, "textContent") {
		t.Errorf("missing textContent match: %s", js)
	}
	if !strings.Contains(js, "getBoundingClientRect") {
		t.Errorf("missing bounding rect: %s", js)
	}
	if !strings.Contains(js, `targetDay === 4`) {
		t.Errorf("missing target day: %s", js)
	}
}
```

- [ ] **Step 2: 运行测试验证失败**

```bash
go test ./internal/ -run TestPickCalendarDayJS -v -count=1
```
期望：编译失败。

- [ ] **Step 3: 实现 `pickCalendarDayJS`**

在 `internal/form.go` 的 `navigateCalendarMonthJS` 之后追加：

```go
// pickCalendarDayJS generates JS to find and return the bounding rect of a
// specific day cell in an open calendar popup. Searches document-level for
// the popup, then matches cells by textContent across multiple CSS class
// patterns (calendar-day, MuiPickersDay-root, ant-picker-cell). Filters
// out empty/disabled cells.
func pickCalendarDayJS(targetDay int) string {
	return fmt.Sprintf(`(function(){
		var targetDay = %d;
		var popup = document.querySelector('.calendar-popup._open_') || document.querySelector('[role="dialog"]');
		if (!popup) return JSON.stringify({error: 'calendar popup not found'});

		var cells = popup.querySelectorAll('.calendar-day, .MuiPickersDay-root, .ant-picker-cell');
		for (var i = 0; i < cells.length; i++) {
			var cell = cells[i];
			// Skip empty/disabled cells
			if (cell.classList.contains('_empty_')) continue;
			if (cell.classList.contains('Mui-disabled')) continue;
			if (cell.classList.contains('ant-picker-cell-disabled')) continue;
			if (cell.getAttribute('aria-disabled') === 'true') continue;

			var text = (cell.textContent || '').trim();
			if (text === String(targetDay)) {
				var r = cell.getBoundingClientRect();
				if (r.width === 0 || r.height === 0) continue;
				return JSON.stringify({found: true, centerX: r.x+r.width/2, centerY: r.y+r.height/2, width: r.width, height: r.height});
			}
		}
		return JSON.stringify({error: 'day ' + targetDay + ' not found in calendar'});
	})()`, targetDay)
}
```

- [ ] **Step 4: 运行测试验证通过**

```bash
go test ./internal/ -run TestPickCalendarDayJS -v -count=1
```
期望：PASS。

- [ ] **Step 5: 提交**

```bash
git add internal/form.go internal/form_test.go
git commit -m "feat: add pickCalendarDayJS for selecting day in open calendar popup"
```

---

### Task 5: `FillText` 日期分支 + 集成测试

**Files:**
- Modify: `internal/form.go:146-188` (`FillText` 方法)
- Modify: `internal/form_integration_test.go`

**Interfaces:**
- Consumes: `parseDateValue` (Task 1), `isDatePickerJS` (Task 2), `navigateCalendarMonthJS` (Task 3), `pickCalendarDayJS` (Task 4)
- Produces: `func (c *Client) FillText(selector, text, frameID string, track bool) error` — 现有签名，行为扩展

- [ ] **Step 1: 写集成测试（先不运行，等实现后验证）**

在 `internal/form_integration_test.go` 末尾追加：

```go
func TestFillText_DatePicker_YMD(t *testing.T) {
	host := os.Getenv("CDP_HOST")
	if host == "" { host = "127.0.0.1" }
	port := 9222

	pages, err := ListPageTargets(host, port, false)
	if err != nil || len(pages) == 0 { t.Skipf("Chrome not available: %v", err) }

	client, err := NewClient(host, port)
	if err != nil { t.Fatalf("NewClient failed: %v", err) }
	defer client.Disconnect()

	client.Navigate("http://localhost:8080/mui-datepicker", "")

	err = client.FillText("#dob-field", "2026-07-04", "", false)
	if err != nil { t.Fatalf("FillText failed: %v", err) }

	var val string
	if err := client.EvalInFrame("", "document.getElementById('dob-input').value", &val); err != nil {
		t.Fatalf("check value failed: %v", err)
	}
	if val != "07-04-2026" {
		t.Errorf("dob-input value = %q, want %q", val, "07-04-2026")
	}
}

func TestFillText_DatePicker_MDY(t *testing.T) {
	host := os.Getenv("CDP_HOST")
	if host == "" { host = "127.0.0.1" }
	port := 9222

	pages, err := ListPageTargets(host, port, false)
	if err != nil || len(pages) == 0 { t.Skipf("Chrome not available: %v", err) }

	client, err := NewClient(host, port)
	if err != nil { t.Fatalf("NewClient failed: %v", err) }
	defer client.Disconnect()

	client.Navigate("http://localhost:8080/mui-datepicker", "")

	err = client.FillText("#dob-field", "07-04-2026", "", false)
	if err != nil { t.Fatalf("FillText failed: %v", err) }

	var val string
	if err := client.EvalInFrame("", "document.getElementById('dob-input').value", &val); err != nil {
		t.Fatalf("check value failed: %v", err)
	}
	if val != "07-04-2026" {
		t.Errorf("dob-input value = %q, want %q", val, "07-04-2026")
	}
}

func TestFillText_DatePicker_MD(t *testing.T) {
	host := os.Getenv("CDP_HOST")
	if host == "" { host = "127.0.0.1" }
	port := 9222

	pages, err := ListPageTargets(host, port, false)
	if err != nil || len(pages) == 0 { t.Skipf("Chrome not available: %v", err) }

	client, err := NewClient(host, port)
	if err != nil { t.Fatalf("NewClient failed: %v", err) }
	defer client.Disconnect()

	client.Navigate("http://localhost:8080/mui-datepicker", "")

	err = client.FillText("#dob-field", "07-04", "", false)
	if err != nil { t.Fatalf("FillText failed: %v", err) }

	now := time.Now()
	want := fmt.Sprintf("07-04-%d", now.Year())

	var val string
	if err := client.EvalInFrame("", "document.getElementById('dob-input').value", &val); err != nil {
		t.Fatalf("check value failed: %v", err)
	}
	if val != want {
		t.Errorf("dob-input value = %q, want %q", val, want)
	}
}

func TestFillText_PlainInput_StillWorks(t *testing.T) {
	host := os.Getenv("CDP_HOST")
	if host == "" { host = "127.0.0.1" }
	port := 9222

	pages, err := ListPageTargets(host, port, false)
	if err != nil || len(pages) == 0 { t.Skipf("Chrome not available: %v", err) }

	client, err := NewClient(host, port)
	if err != nil { t.Fatalf("NewClient failed: %v", err) }
	defer client.Disconnect()

	client.Navigate("http://localhost:8080/mui-datepicker", "")

	// Fill a plain text input — should use existing type-char-by-char flow
	err = client.FillText("#fullname", "John Doe", "", false)
	if err != nil { t.Fatalf("FillText plain input failed: %v", err) }

	var val string
	if err := client.EvalInFrame("", "document.getElementById('fullname').value", &val); err != nil {
		t.Fatalf("check value failed: %v", err)
	}
	if val != "John Doe" {
		t.Errorf("fullname value = %q, want %q", val, "John Doe")
	}
}
```

- [ ] **Step 2: 运行集成测试验证失败（普通 input 应通过，datepicker 应失败）**

```bash
go test ./internal/ -run "TestFillText_DatePicker_YMD|TestFillText_PlainInput" -v -count=1
```
期望：
- `TestFillText_PlainInput_StillWorks` PASS（逐字输入未改动）
- `TestFillText_DatePicker_YMD` FAIL（日期分支未实现）

- [ ] **Step 3: 在 `FillText` 方法内加入日期检测分支**

在 `internal/form.go` 中，将 `FillText` 的 scroll+wait 之后的代码替换为：

```go
func (c *Client) FillText(selector, text, frameID string, track bool) error {
	scrollSelector := selector
	if frameID != "" {
		var err error
		scrollSelector, err = c.ResolveIframeSelector(frameID)
		if err != nil {
			return err
		}
	}

	if err := c.ScrollToElement(scrollSelector, track); err != nil {
		return err
	}
	if err := c.ScrollIntoView(selector, frameID); err != nil {
		return fmt.Errorf("scrollIntoView failed: %w", err)
	}
	if err := c.WaitForPositionStable(scrollSelector, ""); err != nil {
		return fmt.Errorf("position not stable: %w", err)
	}

	// Detect datepicker: DOM-driven check for MUI-style date inputs
	var dpResultStr string
	if err := c.EvalInFrame(frameID, isDatePickerJS(selector), &dpResultStr); err != nil {
		return err
	}
	var dpResult map[string]interface{}
	if err := json.Unmarshal([]byte(dpResultStr), &dpResult); err != nil {
		return fmt.Errorf("failed to parse datepicker detection: %w", err)
	}
	if errMsg, ok := dpResult["error"]; ok {
		return fmt.Errorf("datepicker detection: %v", errMsg)
	}

	if isDP, _ := dpResult["isDatePicker"].(bool); isDP {
		return c.fillDatePicker(selector, text, frameID, dpResult)
	}

	// Plain text input — existing flow
	var resultStr string
	if err := c.EvalInFrame(frameID, buildClearJS(selector), &resultStr); err != nil {
		return err
	}
	var clearResult map[string]interface{}
	if err := json.Unmarshal([]byte(resultStr), &clearResult); err != nil {
		return fmt.Errorf("failed to parse clear result: %w", err)
	}
	if errMsg, ok := clearResult["error"]; ok {
		return fmt.Errorf("%v", errMsg)
	}

	for _, ch := range text {
		if err := c.insertText(string(ch)); err != nil {
			return err
		}
		time.Sleep(time.Duration(20 + mathrand.Intn(40)) * time.Millisecond)
	}

	return c.EvalInFrame(frameID, buildBlurJS(selector), nil)
}

// fillDatePicker handles the datepicker flow: parse date → click calendar button
// → poll for popup → navigate month → pick day.
func (c *Client) fillDatePicker(selector, text, frameID string, dpResult map[string]interface{}) error {
	// Parse date value
	year, month, day, ok := parseDateValue(text)
	if !ok {
		return fmt.Errorf("cannot parse date value: %q (expected YYYY-MM-DD, MM-DD-YYYY, or MM-DD)", text)
	}
	if year == 0 {
		year = time.Now().Year()
	}

	// Click the calendar button to open the popup
	bx := dpResult["centerX"].(float64)
	by := dpResult["centerY"].(float64)
	if err := c.DispatchMouseClick(bx, by); err != nil {
		return fmt.Errorf("failed to click calendar button: %w", err)
	}

	// Poll for popup visibility
	var popupVisible bool
	for i := 0; i < 40; i++ { // 40 × 50ms = 2s timeout
		time.Sleep(50 * time.Millisecond)
		var vis bool
		js := `!!(document.querySelector('.calendar-popup._open_') || document.querySelector('[role="dialog"]'))`
		if err := c.EvalInFrame(frameID, js, &vis); err == nil && vis {
			popupVisible = true
			break
		}
	}
	if !popupVisible {
		return fmt.Errorf("calendar popup did not appear within 2s")
	}

	// Navigate to target month
	var navResultStr string
	if err := c.EvalInFrame(frameID, navigateCalendarMonthJS(month-1, year), &navResultStr); err != nil {
		return err
	}
	var navResult map[string]interface{}
	if err := json.Unmarshal([]byte(navResultStr), &navResult); err != nil {
		return fmt.Errorf("failed to parse navigation result: %w", err)
	}
	if errMsg, ok := navResult["error"]; ok {
		return fmt.Errorf("month navigation: %v", errMsg)
	}

	// Click the target day
	var dayResultStr string
	if err := c.EvalInFrame(frameID, pickCalendarDayJS(day), &dayResultStr); err != nil {
		return err
	}
	var dayResult map[string]interface{}
	if err := json.Unmarshal([]byte(dayResultStr), &dayResult); err != nil {
		return fmt.Errorf("failed to parse day pick result: %w", err)
	}
	if errMsg, ok := dayResult["error"]; ok {
		return fmt.Errorf("day selection: %v", errMsg)
	}

	dx := dayResult["centerX"].(float64)
	dy := dayResult["centerY"].(float64)
	if err := c.DispatchMouseClick(dx, dy); err != nil {
		return fmt.Errorf("failed to click day: %w", err)
	}

	return nil
}
```

- [ ] **Step 4: 运行全量测试**

```bash
go test ./internal/ -run "TestFillText_DatePicker|TestFillText_PlainInput|TestParse|TestIsDatePicker|TestNavigate|TestPickCalendarDay" -v -count=1
go test ./... -count=1
```
期望：
- `TestFillText_DatePicker_YMD` PASS
- `TestFillText_DatePicker_MDY` PASS
- `TestFillText_DatePicker_MD` PASS
- `TestFillText_PlainInput_StillWorks` PASS
- 所有单元测试 PASS
- `cdp/cmd` + `cdp/internal` 全 PASS

- [ ] **Step 5: 构建二进制并端到端验证**

```bash
go build -ldflags="-s -w" -o cdp main.go
./cdp navi http://localhost:8080/mui-datepicker > /dev/null
./cdp form '#dob-field' --value '2026-07-04'
./cdp eval "document.getElementById('dob-input').value"
```
期望：输出 `"07-04-2026"`。

- [ ] **Step 6: 提交**

```bash
git add internal/form.go internal/form_integration_test.go
git commit -m "feat: FillText auto-detects MUI DatePicker and uses click-to-open/pick flow

- Branch on isDatePickerJS: DOM-driven detection (type=tel + placeholder
  pattern + calendar button via aria-label/onclick search).
- Date flow: parseDateValue → click button → poll popup (2s) →
  navigateCalendarMonthJS → pickCalendarDayJS.
- Plain inputs keep existing type-char-by-char flow."
```
