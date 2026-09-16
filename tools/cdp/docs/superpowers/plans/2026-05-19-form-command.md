# form 子命令实现计划（TDD）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `form` 子命令，模拟人类手势填写表单元素（文本输入、checkbox/radio 切换、select 选项选择）。

**Architecture:** 纯函数层（`escapeJS`、JS 模板生成器、`validateFormFlags`）与应用 TDD；CDP 集成层（`FillText`、`CheckElement`、`SelectOption`）通过编译验证 + Chrome 实际运行测试。新增 `cmd/form.go` + `cmd/form_test.go` + `internal/form.go` + `internal/form_test.go`。

**Tech Stack:** Go 1.26.2, cobra CLI, chromedp/cdproto CDP 协议库, Go 标准 testing 包

---

### Task 1: TDD escapeJS 和 JS 模板生成器

**Files:**
- Create: `internal/form_test.go`
- Create: `internal/form.go`

#### RED: 写失败测试

- [ ] **Step 1: 创建 `internal/form_test.go` — escapeJS 测试**

```go
package internal

import (
	"strings"
	"testing"
)

func TestEscapeJS(t *testing.T) {
	tests := []struct {
		input    string
		expected string
	}{
		{"hello", "hello"},
		{"it's", "it\\'s"},
		{"a\\b", "a\\\\b"},
		{"'quoted'", "\\'quoted\\'"},
		{"", ""},
		{"foo\\'bar", "foo\\\\\\'bar"},
	}

	for _, tt := range tests {
		got := escapeJS(tt.input)
		if got != tt.expected {
			t.Errorf("escapeJS(%q) = %q, want %q", tt.input, got, tt.expected)
		}
	}
}

func TestBuildClearJS(t *testing.T) {
	js := buildClearJS("#myInput")
	if !strings.Contains(js, ".querySelector('#myInput')") {
		t.Errorf("missing selector: %s", js)
	}
	if !strings.Contains(js, "el.focus()") {
		t.Errorf("missing focus: %s", js)
	}
	if !strings.Contains(js, "el.select()") {
		t.Errorf("missing select: %s", js)
	}
	if !strings.Contains(js, "el.value = ''") {
		t.Errorf("missing clear: %s", js)
	}
	if !strings.Contains(js, "Event('input'") {
		t.Errorf("missing input event: %s", js)
	}
	if !strings.Contains(js, "'element not found'") {
		t.Errorf("missing error message: %s", js)
	}
	if !strings.Contains(js, "'success'") {
		t.Errorf("missing success: %s", js)
	}
}

func TestBuildBlurJS(t *testing.T) {
	js := buildBlurJS("#myInput")
	if !strings.Contains(js, ".querySelector('#myInput')") {
		t.Errorf("missing selector: %s", js)
	}
	if !strings.Contains(js, "el.blur()") {
		t.Errorf("missing blur: %s", js)
	}
	if !strings.Contains(js, "Event('change'") {
		t.Errorf("missing change event: %s", js)
	}
}

func TestBuildCheckStateJS(t *testing.T) {
	js := buildCheckStateJS("#cb")
	if !strings.Contains(js, ".querySelector('#cb')") {
		t.Errorf("missing selector: %s", js)
	}
	if !strings.Contains(js, ".checked") {
		t.Errorf("missing .checked: %s", js)
	}
}

func TestBuildSelectOptionJS(t *testing.T) {
	js := buildSelectOptionJS("#sel", "cn")
	if !strings.Contains(js, ".querySelector('#sel')") {
		t.Errorf("missing selector: %s", js)
	}
	if !strings.Contains(js, "opts[i].value === 'cn'") {
		t.Errorf("missing value match: %s", js)
	}
	if !strings.Contains(js, "opts[i].textContent.trim() === 'cn'") {
		t.Errorf("missing textContent fallback: %s", js)
	}
	if !strings.Contains(js, "sel.selectedIndex = i") {
		t.Errorf("missing selectedIndex: %s", js)
	}
	if !strings.Contains(js, "Event('input'") {
		t.Errorf("missing input event: %s", js)
	}
	if !strings.Contains(js, "Event('change'") {
		t.Errorf("missing change event: %s", js)
	}
	if !strings.Contains(js, "'element not found'") {
		t.Errorf("missing element not found: %s", js)
	}
	if !strings.Contains(js, "'option not found: cn'") {
		t.Errorf("missing option not found: %s", js)
	}
}
```

- [ ] **Step 2: 运行测试，验证失败**

```bash
go test ./internal/ -run "TestEscapeJS|TestBuildClearJS|TestBuildBlurJS|TestBuildCheckStateJS|TestBuildSelectOptionJS" -v
```

预期：全部 FAIL — `undefined: escapeJS`、`undefined: buildClearJS` 等。

#### GREEN: 最小实现

- [ ] **Step 3: 创建 `internal/form.go` — escapeJS + JS 模板生成器**

```go
package internal

import (
	"fmt"
	"strings"
)

// escapeJS escapes single quotes and backslashes for safe JS string embedding.
func escapeJS(s string) string {
	s = strings.ReplaceAll(s, "\\", "\\\\")
	s = strings.ReplaceAll(s, "'", "\\'")
	return s
}

// buildClearJS generates JS to focus an element and clear its value.
func buildClearJS(selector string) string {
	escaped := escapeJS(selector)
	return fmt.Sprintf(`(function(){
		var el = document.querySelector('%s');
		if (!el) return JSON.stringify({error: 'element not found'});
		el.focus();
		el.select();
		el.value = '';
		el.dispatchEvent(new Event('input', {bubbles: true}));
		return JSON.stringify({success: true});
	})()`, escaped)
}

// buildBlurJS generates JS to blur an element and fire a change event.
func buildBlurJS(selector string) string {
	escaped := escapeJS(selector)
	return fmt.Sprintf(`(function(){
		var el = document.querySelector('%s');
		if (el) { el.blur(); el.dispatchEvent(new Event('change', {bubbles: true})); }
	})()`, escaped)
}

// buildCheckStateJS generates JS to query an element's checked property.
func buildCheckStateJS(selector string) string {
	escaped := escapeJS(selector)
	return fmt.Sprintf("document.querySelector('%s').checked", escaped)
}

// buildSelectOptionJS generates JS to select an option by value or textContent.
func buildSelectOptionJS(selector, option string) string {
	escapedSelector := escapeJS(selector)
	escapedOption := escapeJS(option)
	return fmt.Sprintf(`(function(){
		var sel = document.querySelector('%s');
		if (!sel) return JSON.stringify({error: 'element not found'});
		var opts = sel.options;
		for (var i = 0; i < opts.length; i++) {
			if (opts[i].value === '%s' || opts[i].textContent.trim() === '%s') {
				sel.selectedIndex = i;
				sel.dispatchEvent(new Event('input', {bubbles: true}));
				sel.dispatchEvent(new Event('change', {bubbles: true}));
				return JSON.stringify({success: true, value: opts[i].value, index: i});
			}
		}
		return JSON.stringify({error: 'option not found: %s'});
	})()`, escapedSelector, escapedOption, escapedOption, escapedOption)
}
```

- [ ] **Step 4: 运行测试，验证全部通过**

```bash
go test ./internal/ -run "TestEscapeJS|TestBuildClearJS|TestBuildBlurJS|TestBuildCheckStateJS|TestBuildSelectOptionJS" -v
```

预期：全部 PASS。

#### REFACTOR

- [ ] **Step 5: 确认测试仍通过**

```bash
go test ./internal/ -v
```

预期：全部 PASS。

- [ ] **Step 6: 提交**

```bash
git add internal/form_test.go internal/form.go
git commit -m "feat: add escapeJS and JS template builders for form command"
```

---

### Task 2: TDD CLI 参数校验

**Files:**
- Create: `cmd/form_test.go`
- Create: `cmd/form.go`

#### RED: 写失败测试

- [ ] **Step 1: 创建 `cmd/form_test.go` — validateFormFlags 测试**

```go
package cmd

import (
	"strings"
	"testing"
)

func TestValidateFormFlagsNoAction(t *testing.T) {
	err := validateFormFlags("", "", "")
	if err == nil {
		t.Fatal("expected error when no action flag is set")
	}
	if !strings.Contains(err.Error(), "one of --value, --check, --select is required") {
		t.Errorf("unexpected error message: %v", err)
	}
}

func TestValidateFormFlagsMultipleActions(t *testing.T) {
	err := validateFormFlags("text", "true", "")
	if err == nil {
		t.Fatal("expected error when multiple action flags are set")
	}
	if !strings.Contains(err.Error(), "only one of") {
		t.Errorf("unexpected error message: %v", err)
	}
}

func TestValidateFormFlagsCheckInvalid(t *testing.T) {
	err := validateFormFlags("", "invalid", "")
	if err == nil {
		t.Fatal("expected error for invalid --check value")
	}
	if !strings.Contains(err.Error(), "--check must be") {
		t.Errorf("unexpected error message: %v", err)
	}
}

func TestValidateFormFlagsValueOnly(t *testing.T) {
	if err := validateFormFlags("hello", "", ""); err != nil {
		t.Errorf("unexpected error for --value only: %v", err)
	}
}

func TestValidateFormFlagsCheckTrue(t *testing.T) {
	if err := validateFormFlags("", "true", ""); err != nil {
		t.Errorf("unexpected error for --check true: %v", err)
	}
}

func TestValidateFormFlagsCheckFalse(t *testing.T) {
	if err := validateFormFlags("", "false", ""); err != nil {
		t.Errorf("unexpected error for --check false: %v", err)
	}
}

func TestValidateFormFlagsSelectOnly(t *testing.T) {
	if err := validateFormFlags("", "", "cn"); err != nil {
		t.Errorf("unexpected error for --select only: %v", err)
	}
}
```

- [ ] **Step 2: 运行测试，验证失败**

```bash
go test ./cmd/ -run "TestValidateFormFlags" -v
```

预期：全部 FAIL — `undefined: validateFormFlags`。

#### GREEN: 最小实现

- [ ] **Step 3: 创建 `cmd/form.go` — 命令定义 + validateFormFlags + runForm**

```go
package cmd

import (
	"fmt"

	"cdp/internal"

	"github.com/spf13/cobra"
)

var formCmd = &cobra.Command{
	Use:   "form [selector]",
	Short: "Fill form element with human-like gesture",
	Args:  cobra.MaximumNArgs(1),
	RunE:  runForm,
}

func init() {
	rootCmd.AddCommand(formCmd)
	formCmd.Flags().String("selector", "", "CSS selector")
	formCmd.Flags().String("value", "", "Text value to type")
	formCmd.Flags().String("check", "", "Check/uncheck: \"true\" or \"false\"")
	formCmd.Flags().String("select", "", "Option value or text to select")
	formCmd.Flags().String("frame-id", "", "Frame ID (optional)")
	formCmd.Flags().Bool("track", false, "Enable track visualization")
}

func validateFormFlags(value, check, selectOpt string) error {
	set := 0
	if value != "" {
		set++
	}
	if check != "" {
		set++
	}
	if selectOpt != "" {
		set++
	}
	if set == 0 {
		return fmt.Errorf("one of --value, --check, --select is required")
	}
	if set > 1 {
		return fmt.Errorf("only one of --value, --check, --select can be specified")
	}
	if check != "" && check != "true" && check != "false" {
		return fmt.Errorf("--check must be \"true\" or \"false\", got: %s", check)
	}
	return nil
}

func runForm(cmd *cobra.Command, args []string) error {
	selector, err := resolveSelector(cmd, args)
	if err != nil {
		return err
	}

	frameID, _ := cmd.Flags().GetString("frame-id")
	track, _ := cmd.Flags().GetBool("track")
	value, _ := cmd.Flags().GetString("value")
	check, _ := cmd.Flags().GetString("check")
	selectOpt, _ := cmd.Flags().GetString("select")

	if err := validateFormFlags(value, check, selectOpt); err != nil {
		return err
	}

	client, err := internal.NewClient(GetHost(), GetPort())
	if err != nil {
		return fmt.Errorf("failed to create client: %w", err)
	}

	if value != "" {
		return client.FillText(selector, value, frameID, track)
	}
	if check != "" {
		return client.CheckElement(selector, check == "true", frameID)
	}
	return client.SelectOption(selector, selectOpt, frameID)
}
```

- [ ] **Step 4: 运行测试，验证全部通过**

```bash
go test ./cmd/ -run "TestValidateFormFlags" -v
```

预期：全部 PASS。

#### REFACTOR

- [ ] **Step 5: 确认所有测试仍通过**

```bash
go test ./cmd/ -v
```

预期：全部 PASS。

- [ ] **Step 6: 提交**

```bash
git add cmd/form_test.go cmd/form.go
git commit -m "feat: add form CLI command with flag validation"
```

---

### Task 3: 实现 CDP 集成方法（FillText + CheckElement + SelectOption）

**Files:**
- Modify: `internal/form.go`（替换 import 块 + 追加全部 Client 方法）

**说明:** CDP 方法依赖 Chrome 运行环境，无法在单元测试中覆盖。JS 模板生成器已在 Task 1 TDD 覆盖。三个方法合并到一个任务以避免 Go 未使用导入错误（`math` 被 `calcClickCoords` 使用，但 `FillText`/`SelectOption` 不需要）。

- [ ] **Step 1: 替换 internal/form.go 的 import 块**

将文件开头的 import 块替换为：

```go
package internal

import (
	"context"
	"fmt"
	"math"
	mathrand "math/rand"
	"strings"
	"time"

	"github.com/chromedp/cdproto/cdp"
	"github.com/chromedp/cdproto/input"
	"github.com/chromedp/chromedp"
)
```

- [ ] **Step 2: 在 internal/form.go 末尾追加全部 CDP 方法**

```go
// insertText dispatches a single Input.insertText CDP command to type one character.
func (c *Client) insertText(text string) error {
	return chromedp.Run(c.ctx, chromedp.ActionFunc(func(ctx context.Context) error {
		cc := chromedp.FromContext(ctx)
		if cc == nil || cc.Target == nil {
			return fmt.Errorf("invalid context")
		}
		exec := cdp.WithExecutor(ctx, cc.Target)
		return input.InsertText(text).Do(exec)
	}))
}

// FillText types text into a form element using human-like gestures.
// Workflow: scroll into view → focus → select-all + clear → type char-by-char → blur + change.
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

	var result map[string]interface{}
	if err := c.EvalInFrame(frameID, buildClearJS(selector), &result); err != nil {
		return err
	}
	if errMsg, ok := result["error"]; ok {
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

// CheckElement checks or unchecks a checkbox/radio input.
// Only dispatches a click if the current checked state differs from the target.
func (c *Client) CheckElement(selector string, checked bool, frameID string) error {
	scrollSelector := selector
	if frameID != "" {
		var err error
		scrollSelector, err = c.ResolveIframeSelector(frameID)
		if err != nil {
			return err
		}
	}

	if err := c.ScrollToElement(scrollSelector, false); err != nil {
		return err
	}
	if err := c.ScrollIntoView(selector, frameID); err != nil {
		return fmt.Errorf("scrollIntoView failed: %w", err)
	}
	if err := c.WaitForPositionStable(scrollSelector, ""); err != nil {
		return fmt.Errorf("position not stable: %w", err)
	}

	isChecked, err := c.EvalBool(frameID, buildCheckStateJS(selector))
	if err != nil {
		return fmt.Errorf("element not found: %w", err)
	}

	if isChecked == checked {
		return nil
	}

	clickX, clickY, err := c.calcClickCoords(selector, scrollSelector, frameID)
	if err != nil {
		return err
	}
	return c.DispatchMouseClick(clickX, clickY)
}

// calcClickCoords returns click coordinates for an element center with Gaussian offset.
// Handles iframe coordinate translation.
func (c *Client) calcClickCoords(selector, scrollSelector, frameID string) (float64, float64, error) {
	if frameID != "" {
		iframeRect, err := c.GetElementCenter(scrollSelector, "")
		if err != nil {
			return 0, 0, fmt.Errorf("failed to get iframe position: %w", err)
		}
		elemRect, err := c.GetElementCenter(selector, frameID)
		if err != nil {
			return 0, 0, fmt.Errorf("failed to get element position: %w", err)
		}
		clickX := iframeRect["x"] + elemRect["centerX"]
		clickY := iframeRect["y"] + elemRect["centerY"]
		sigma := math.Min(elemRect["width"], elemRect["height"]) / 4
		dx, dy := GaussianOffset(sigma)
		return clickX + dx, clickY + dy, nil
	}

	rect, err := c.GetElementCenter(selector, "")
	if err != nil {
		return 0, 0, fmt.Errorf("failed to get element position: %w", err)
	}
	sigma := math.Min(rect["width"], rect["height"]) / 4
	dx, dy := GaussianOffset(sigma)
	return rect["centerX"] + dx, rect["centerY"] + dy, nil
}

// SelectOption selects an option in a <select> element by matching value first,
// then falling back to textContent match. Triggers input and change events.
func (c *Client) SelectOption(selector, option, frameID string) error {
	scrollSelector := selector
	if frameID != "" {
		var err error
		scrollSelector, err = c.ResolveIframeSelector(frameID)
		if err != nil {
			return err
		}
	}

	if err := c.ScrollToElement(scrollSelector, false); err != nil {
		return err
	}
	if err := c.ScrollIntoView(selector, frameID); err != nil {
		return fmt.Errorf("scrollIntoView failed: %w", err)
	}
	if err := c.WaitForPositionStable(scrollSelector, ""); err != nil {
		return fmt.Errorf("position not stable: %w", err)
	}

	var result map[string]interface{}
	if err := c.EvalInFrame(frameID, buildSelectOptionJS(selector, option), &result); err != nil {
		return err
	}
	if errMsg, ok := result["error"]; ok {
		return fmt.Errorf("%v", errMsg)
	}
	return nil
}
```

- [ ] **Step 3: 编译验证**

```bash
go build -o cdp main.go
```

预期：编译成功。

- [ ] **Step 4: 确认已有单元测试不受影响**

```bash
go test ./internal/ -v
go test ./cmd/ -v
```

预期：Task 1 和 Task 2 的测试全部 PASS。

- [ ] **Step 5: 提交**

```bash
git add internal/form.go
git commit -m "feat: add FillText, CheckElement, SelectOption CDP methods"
```

---

### Task 4: CLI 集成验证 + 完整测试回归

- [ ] **Step 1: 运行全部单元测试**

```bash
go test ./... -v
```

预期：全部 PASS。

- [ ] **Step 2: 检查 form 帮助信息**

```bash
go run . form --help
```

预期：显示 form 命令的帮助，包含 `--selector`、`--value`、`--check`、`--select`、`--frame-id`、`--track`。

- [ ] **Step 3: 确认 form 在子命令列表中**

```bash
go run . --help
```

预期：在子命令列表中看到 `form`。

- [ ] **Step 4: 参数校验集成测试**

```bash
go run . form 2>&1; echo "exit: $?"
```

预期：报错 "one of --value, --check, --select is required"，exit code 非零。

```bash
go run . form --value a --check true 2>&1; echo "exit: $?"
```

预期：报错 "only one of --value, --check, --select can be specified"，exit code 非零。

```bash
go run . form --check invalid 2>&1; echo "exit: $?"
```

预期：报错 "--check must be \"true\" or \"false\""，exit code 非零。

---
