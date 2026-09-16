# form --select 自定义下拉框实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `cdp form '#wrapper' --select 'OptionText'` 自动检测目标是否为原生 `<select>`，否则走 click-control→click-option 流程支持 React-Select 等自定义下拉框。

**Architecture:** `SelectOption` 入口先 eval JS 判断 `tagName === 'SELECT'`→ 是走现有 native 流程；否走 custom 流程：JS 找 control 坐标 → DispatchMouseClick control → JS 找 option 坐标 → DispatchMouseClick option。不引入新 CLI flag，不修改 cmd/form.go。

**Tech Stack:** Go 1.26.2, chromedp, cdproto

## Global Constraints

- 复用现有 `SelectOption(selector, option, frameID, track)` 签名，不改变外部接口
- 所有 JS 生成函数放在 `internal/form.go`，保持 `build*JS` 命名惯例
- 集成测试需要 Chrome + `localhost:8080/react-select` 可用

---

## 文件结构

| 文件 | 职责 | 操作 |
|------|------|------|
| `internal/form.go` | `isNativeSelectJS`、`findControlJS`、`findCustomOptionJS` JS 生成函数；`SelectOption` 分支逻辑 | 修改 |
| `internal/form_test.go` | 新 JS 函数的单元测试 | 修改 |
| `internal/form_integration_test.go` | react-select 集成测试 | 新建 |

### Task 1: `isNativeSelectJS` — 检测是否为原生 select

**Files:**
- Modify: `internal/form.go` (after `buildSelectOptionJS`)
- Modify: `internal/form_test.go`

**Interfaces:**
- Produces: `func isNativeSelectJS(selector string) string` — 返回 JS 字符串，执行后返回 `{"isSelect": true/false}` 或 `{"error": "..."}`

- [ ] **Step 1: 写单元测试**

在 `internal/form_test.go` 末尾追加：

```go
func TestIsNativeSelectJS(t *testing.T) {
	js := isNativeSelectJS("#my-select")
	if !strings.Contains(js, ".querySelector('#my-select')") {
		t.Errorf("missing selector: %s", js)
	}
	if !strings.Contains(js, "el.tagName === 'SELECT'") {
		t.Errorf("missing tagName check: %s", js)
	}
	if !strings.Contains(js, "'isSelect'") {
		t.Errorf("missing isSelect key: %s", js)
	}
	if !strings.Contains(js, "'element not found'") {
		t.Errorf("missing error message: %s", js)
	}
}
```

- [ ] **Step 2: 运行测试验证失败**

```bash
go test ./internal/ -run TestIsNativeSelectJS -v -count=1
```
期望：编译失败，`isNativeSelectJS` 未定义。

- [ ] **Step 3: 实现 `isNativeSelectJS`**

在 `internal/form.go` 的 `buildSelectOptionJS` 之后追加：

```go
// isNativeSelectJS generates JS to check whether the target element is a native <select>.
func isNativeSelectJS(selector string) string {
	escaped := escapeJS(selector)
	return fmt.Sprintf(`(function(){
		var el = document.querySelector('%s');
		if (!el) return JSON.stringify({error: 'element not found'});
		return JSON.stringify({isSelect: el.tagName === 'SELECT'});
	})()`, escaped)
}
```

- [ ] **Step 4: 运行测试验证通过**

```bash
go test ./internal/ -run TestIsNativeSelectJS -v -count=1
```
期望：PASS。

- [ ] **Step 5: 提交**

```bash
git add internal/form.go internal/form_test.go
git commit -m "feat: add isNativeSelectJS helper for detecting native <select> elements"
```

---

### Task 2: `findControlJS` — 在 wrapper 内找到可点击的 control

**Files:**
- Modify: `internal/form.go`
- Modify: `internal/form_test.go`

**Interfaces:**
- Produces: `func findControlJS(wrapperSelector string) string` — 返回 JS 字符串，执行后返回 `{found: true, centerX, centerY, width, height}` 或 `{error: "no clickable control found"}`

- [ ] **Step 1: 写单元测试**

在 `internal/form_test.go` 末尾追加：

```go
func TestFindControlJS(t *testing.T) {
	js := findControlJS("#country-wrapper")
	if !strings.Contains(js, ".querySelector('#country-wrapper')") {
		t.Errorf("missing wrapper selector: %s", js)
	}
	if !strings.Contains(js, "[onclick*=\"toggle\"]") {
		t.Errorf("missing toggle onclick search: %s", js)
	}
	if !strings.Contains(js, "[onclick*=\"menu\"]") {
		t.Errorf("missing menu onclick search: %s", js)
	}
	if !strings.Contains(js, "getBoundingClientRect") {
		t.Errorf("missing bounding rect: %s", js)
	}
	if !strings.Contains(js, "'no clickable control found'") {
		t.Errorf("missing error message: %s", js)
	}
}
```

- [ ] **Step 2: 运行测试验证失败**

```bash
go test ./internal/ -run TestFindControlJS -v -count=1
```
期望：编译失败。

- [ ] **Step 3: 实现 `findControlJS`**

在 `internal/form.go` 的 `isNativeSelectJS` 之后追加：

```go
// findControlJS generates JS to locate the clickable control inside a custom dropdown wrapper.
// Searches for elements with onclick containing "toggle", "menu", or "open"; falls back to wrapper.
// Returns bounding rect of the found control for DispatchMouseClick.
func findControlJS(wrapperSelector string) string {
	escaped := escapeJS(wrapperSelector)
	return fmt.Sprintf(`(function(){
		var wrapper = document.querySelector('%s');
		if (!wrapper) return JSON.stringify({error: 'wrapper not found'});
		var control = wrapper.querySelector('[onclick*="toggle"], [onclick*="menu"], [onclick*="open"]');
		if (!control) control = wrapper;
		var r = control.getBoundingClientRect();
		if (r.width === 0 || r.height === 0) return JSON.stringify({error: 'no clickable control found'});
		return JSON.stringify({found: true, x: r.x, y: r.y, centerX: r.x+r.width/2, centerY: r.y+r.height/2, width: r.width, height: r.height});
	})()`, escaped)
}
```

- [ ] **Step 4: 运行测试验证通过**

```bash
go test ./internal/ -run TestFindControlJS -v -count=1
```
期望：PASS。

- [ ] **Step 5: 提交**

```bash
git add internal/form.go internal/form_test.go
git commit -m "feat: add findControlJS helper for locating custom dropdown control"
```

---

### Task 3: `findCustomOptionJS` — 在 wrapper 内找匹配文本的 option

**Files:**
- Modify: `internal/form.go`
- Modify: `internal/form_test.go`

**Interfaces:**
- Produces: `func findCustomOptionJS(wrapperSelector, optionText string) string` — 返回 JS 字符串，执行后返回 `{found: true, text, centerX, centerY, width, height}` 或 `{error: "option 'X' not found"}`

- [ ] **Step 1: 写单元测试**

在 `internal/form_test.go` 末尾追加：

```go
func TestFindCustomOptionJS(t *testing.T) {
	js := findCustomOptionJS("#country-wrapper", "Canada")
	if !strings.Contains(js, ".querySelector('#country-wrapper')") {
		t.Errorf("missing wrapper selector: %s", js)
	}
	if !strings.Contains(js, "textContent") {
		t.Errorf("missing textContent match: %s", js)
	}
	if !strings.Contains(js, "getBoundingClientRect") {
		t.Errorf("missing bounding rect: %s", js)
	}
	if !strings.Contains(js, "'option not found: Canada'") {
		t.Errorf("missing error message: %s", js)
	}
	if !strings.Contains(js, "rect.width === 0") {
		t.Errorf("missing zero-size skip: %s", js)
	}
}
```

- [ ] **Step 2: 运行测试验证失败**

```bash
go test ./internal/ -run TestFindCustomOptionJS -v -count=1
```
期望：编译失败。

- [ ] **Step 3: 实现 `findCustomOptionJS`**

在 `internal/form.go` 的 `findControlJS` 之后追加：

```go
// findCustomOptionJS generates JS to find a dropdown option by text match inside a wrapper.
// Skips elements with zero bounding rect (display:none). Matches exact textContent.trim()
// first, then falls back to substring match.
func findCustomOptionJS(wrapperSelector, optionText string) string {
	escapedWrapper := escapeJS(wrapperSelector)
	escapedOption := escapeJS(optionText)
	return fmt.Sprintf(`(function(){
		var wrapper = document.querySelector('%s');
		if (!wrapper) return JSON.stringify({error: 'wrapper not found'});
		var all = wrapper.querySelectorAll('*');
		var fallback = null;
		for (var i = 0; i < all.length; i++) {
			var el = all[i];
			var r = el.getBoundingClientRect();
			if (r.width === 0 || r.height === 0) continue;
			var text = (el.textContent || '').trim();
			if (text === '%s') {
				return JSON.stringify({found: true, text: text, x: r.x, y: r.y, centerX: r.x+r.width/2, centerY: r.y+r.height/2, width: r.width, height: r.height});
			}
			if (fallback === null && text.indexOf('%s') !== -1) {
				fallback = {el: el, r: r, text: text};
			}
		}
		if (fallback !== null) {
			var r = fallback.r;
			return JSON.stringify({found: true, text: fallback.text, x: r.x, y: r.y, centerX: r.x+r.width/2, centerY: r.y+r.height/2, width: r.width, height: r.height});
		}
		return JSON.stringify({error: 'option not found: %s'});
	})()`, escapedWrapper, escapedOption, escapedOption, escapedOption)
}
```

- [ ] **Step 4: 运行测试验证通过**

```bash
go test ./internal/ -run TestFindCustomOptionJS -v -count=1
```
期望：PASS。

- [ ] **Step 5: 提交**

```bash
git add internal/form.go internal/form_test.go
git commit -m "feat: add findCustomOptionJS helper for matching dropdown options by text"
```

---

### Task 4: 修改 `SelectOption` 加入 native/custom 分支

**Files:**
- Modify: `internal/form.go:207-250` (`SelectOption` 方法)

**Interfaces:**
- Consumes: `isNativeSelectJS`, `findControlJS`, `findCustomOptionJS` (Tasks 1-3)
- Produces: `func (c *Client) SelectOption(selector, option, frameID string, track bool) error` — 现有签名，行为扩展

- [ ] **Step 1: 写集成测试（先不运行，等实现后验证）**

新建 `internal/form_integration_test.go`（如已存在则追加）：

```go
func TestSelectOption_CustomDropdown_Single(t *testing.T) {
	host := os.Getenv("CDP_HOST")
	if host == "" { host = "127.0.0.1" }
	port := 9222

	pages, err := ListPageTargets(host, port, false)
	if err != nil || len(pages) == 0 {
		t.Skipf("Chrome not available: %v", err)
	}

	client, err := NewClient(host, port)
	if err != nil { t.Fatalf("NewClient failed: %v", err) }
	defer client.Disconnect()

	// Navigate to react-select test page
	client.Navigate("http://localhost:8080/react-select", "")

	// Select "Canada" via custom dropdown wrapper
	err = client.SelectOption("#country-wrapper", "Canada", "", false)
	if err != nil {
		t.Fatalf("SelectOption failed: %v", err)
	}

	// Verify hidden input has the selected value
	var val string
	if err := client.EvalInFrame("", "document.getElementById('country-input').value", &val); err != nil {
		t.Fatalf("check input value failed: %v", err)
	}
	if val != "Canada" {
		t.Errorf("country-input value = %q, want %q", val, "Canada")
	}
}

func TestSelectOption_CustomDropdown_NotFound(t *testing.T) {
	host := os.Getenv("CDP_HOST")
	if host == "" { host = "127.0.0.1" }
	port := 9222

	pages, err := ListPageTargets(host, port, false)
	if err != nil || len(pages) == 0 {
		t.Skipf("Chrome not available: %v", err)
	}

	client, err := NewClient(host, port)
	if err != nil { t.Fatalf("NewClient failed: %v", err) }
	defer client.Disconnect()

	client.Navigate("http://localhost:8080/react-select", "")

	err = client.SelectOption("#country-wrapper", "Mars", "", false)
	if err == nil {
		t.Error("expected error for non-existent option, got nil")
	}
	if err != nil && !strings.Contains(err.Error(), "option not found") {
		t.Errorf("error should mention 'option not found', got: %v", err)
	}
}

func TestSelectOption_NativeSelect_StillWorks(t *testing.T) {
	host := os.Getenv("CDP_HOST")
	if host == "" { host = "127.0.0.1" }
	port := 9222

	pages, err := ListPageTargets(host, port, false)
	if err != nil || len(pages) == 0 {
		t.Skipf("Chrome not available: %v", err)
	}

	client, err := NewClient(host, port)
	if err != nil { t.Fatalf("NewClient failed: %v", err) }
	defer client.Disconnect()

	client.Navigate("http://localhost:8080/carwarranty", "")

	err = client.SelectOption("#car-year", "2025", "", false)
	if err != nil {
		t.Fatalf("native SelectOption failed: %v", err)
	}

	var val string
	if err := client.EvalInFrame("", "document.getElementById('car-year').value", &val); err != nil {
		t.Fatalf("check value failed: %v", err)
	}
	if val != "2025" {
		t.Errorf("car-year value = %q, want %q", val, "2025")
	}
}
```

- [ ] **Step 2: 运行集成测试验证失败（native select 不受影响，custom 测试应失败）**

```bash
go test ./internal/ -run "TestSelectOption_CustomDropdown_Single|TestSelectOption_NativeSelect_StillWorks" -v -count=1
```
期望：
- `TestSelectOption_NativeSelect_StillWorks` PASS（现有逻辑未改动）
- `TestSelectOption_CustomDropdown_Single` FAIL（custom 分支未实现）

- [ ] **Step 3: 在 `SelectOption` 方法内加入类型检测分支**

修改 `internal/form.go` 的 `SelectOption` 方法。在 `WaitForPositionStable` 之后（line 226-228 之后），将原有逻辑包裹在分支中：

```go
// SelectOption selects an option in a <select> element or a custom dropdown.
// For native <select>: clicks the element, then sets selectedIndex via JS.
// For custom dropdown: clicks the control to open, then clicks the matching option.
func (c *Client) SelectOption(selector, option, frameID string, track bool) error {
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

	// Detect whether target is a native <select> or custom dropdown
	var isSelectResultStr string
	if err := c.EvalInFrame(frameID, isNativeSelectJS(selector), &isSelectResultStr); err != nil {
		return err
	}
	var isSelectResult map[string]interface{}
	if err := json.Unmarshal([]byte(isSelectResultStr), &isSelectResult); err != nil {
		return fmt.Errorf("failed to parse isSelect result: %w", err)
	}
	if errMsg, ok := isSelectResult["error"]; ok {
		return fmt.Errorf("%v", errMsg)
	}

	isSelect, _ := isSelectResult["isSelect"].(bool)
	if isSelect {
		// Native <select> flow
		clickX, clickY, err := c.calcClickCoords(selector, scrollSelector, frameID)
		if err == nil {
			c.DispatchMouseClick(clickX, clickY)
		}

		var selectResultStr string
		if err := c.EvalInFrame(frameID, buildSelectOptionJS(selector, option), &selectResultStr); err != nil {
			return err
		}
		var selectResult map[string]interface{}
		if err := json.Unmarshal([]byte(selectResultStr), &selectResult); err != nil {
			return fmt.Errorf("failed to parse select result: %w", err)
		}
		if errMsg, ok := selectResult["error"]; ok {
			return fmt.Errorf("%v", errMsg)
		}
		return nil
	}

	// Custom dropdown flow: find control → click → find option → click
	var controlResultStr string
	if err := c.EvalInFrame(frameID, findControlJS(selector), &controlResultStr); err != nil {
		return err
	}
	var controlResult map[string]interface{}
	if err := json.Unmarshal([]byte(controlResultStr), &controlResult); err != nil {
		return fmt.Errorf("failed to parse control result: %w", err)
	}
	if errMsg, ok := controlResult["error"]; ok {
		return fmt.Errorf("%v", errMsg)
	}

	// Click the control to open the dropdown menu
	cx := controlResult["centerX"].(float64)
	cy := controlResult["centerY"].(float64)
	if err := c.DispatchMouseClick(cx, cy); err != nil {
		return fmt.Errorf("failed to click control: %w", err)
	}

	// Small delay for menu to appear
	time.Sleep(150 * time.Millisecond)

	// Find and click the matching option
	var optionResultStr string
	if err := c.EvalInFrame(frameID, findCustomOptionJS(selector, option), &optionResultStr); err != nil {
		return err
	}
	var optionResult map[string]interface{}
	if err := json.Unmarshal([]byte(optionResultStr), &optionResult); err != nil {
		return fmt.Errorf("failed to parse option result: %w", err)
	}
	if errMsg, ok := optionResult["error"]; ok {
		return fmt.Errorf("%v", errMsg)
	}

	ox := optionResult["centerX"].(float64)
	oy := optionResult["centerY"].(float64)
	if err := c.DispatchMouseClick(ox, oy); err != nil {
		return fmt.Errorf("failed to click option: %w", err)
	}

	return nil
}
```

- [ ] **Step 4: 运行全部测试**

```bash
go test ./internal/ -run "TestSelectOption_Custom|TestSelectOption_Native|TestFind|TestIsNative|TestBuildSelect" -v -count=1
```
期望：
- `TestIsNativeSelectJS` PASS
- `TestFindControlJS` PASS
- `TestFindCustomOptionJS` PASS
- `TestSelectOption_CustomDropdown_Single` PASS
- `TestSelectOption_CustomDropdown_NotFound` PASS
- `TestSelectOption_NativeSelect_StillWorks` PASS

- [ ] **Step 5: 运行全量测试确认无回归**

```bash
go test ./... -count=1
```
期望：全部 PASS。

- [ ] **Step 6: 构建二进制并端到端验证**

```bash
go build -ldflags="-s -w" -o cdp main.go
./cdp navi http://localhost:8080/react-select > /dev/null
./cdp form '#country-wrapper' --select 'Canada'
./cdp eval "document.getElementById('country-input').value"
```
期望：输出 `"Canada"`。

- [ ] **Step 7: 提交**

```bash
git add internal/form.go internal/form_integration_test.go
git commit -m "feat: SelectOption auto-detects custom dropdown and uses click-to-open/pick flow

- Branch on isNativeSelectJS: native <select> keeps existing flow.
- Custom dropdown: findControlJS locates clickable control, click it,
  then findCustomOptionJS matches option by text, click it.
- Integration tests against localhost:8080/react-select."
```
