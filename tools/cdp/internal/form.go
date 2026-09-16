package internal

import (
	"context"
	"encoding/json"
	"fmt"
	"math"
	mathrand "math/rand"
	"strconv"
	"strings"
	"time"

	"github.com/chromedp/cdproto/cdp"
	"github.com/chromedp/cdproto/input"
	"github.com/chromedp/chromedp"
)

// escapeJS escapes single quotes and backslashes for safe JS string embedding.
func escapeJS(s string) string {
	s = strings.ReplaceAll(s, "\\", "\\\\")
	s = strings.ReplaceAll(s, "'", "\\'")
	return s
}

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
	// Expand 2-digit year (0-99 → 2000+year)
	if year >= 0 && year < 100 {
		year += 2000
	}
	if month < 1 || month > 12 || day < 1 || day > 31 {
		return 0, 0, 0, false
	}
	return year, month, day, true
}

// isDatePickerJS generates JS to detect whether a form element has an attached
// datepicker (MUI-style: type=tel + date-pattern placeholder + calendar button).
// Resolves wrapper->input if needed, searches the input's container for a
// calendar trigger button via aria-label and onclick attributes.
func isDatePickerJS(selector string) string {
	escaped := escapeJS(selector)
	return withPierce(fmt.Sprintf(`(function(){
		var el = __cdpQ('%s');
		if (!el) return JSON.stringify({error: 'element not found'});

		// Step 0: resolve to input element
		var input = el;
		if (el.tagName !== 'INPUT') {
			input = __cdpQIn(el, 'input[type="tel"]') || __cdpQIn(el, 'input');
		}
		if (!input) return JSON.stringify({error: 'no input found in wrapper'});

		// Step 1: check input characteristics
		if (!(input.type === 'tel')) return JSON.stringify({'isDatePicker': false});
		var ph = (input.placeholder || '').toLowerCase();
		if (!/mm-dd-yyyy|yyyy-mm-dd/i.test(ph)) return JSON.stringify({'isDatePicker': false});

		// Step 2: search for calendar button in container
		var container = input.closest('[class*="MuiInputBase"], [class*="InputBase"], .MuiFormControl-root, form, .field');
		if (!container || container === input) container = input.parentElement;
		var btn = __cdpQIn(container, '[aria-label*="date"], [aria-label*="calendar"], [aria-label*="choose"], [onclick*="calendar"], [onclick*="toggle"], [onclick*="picker"]');
		if (!btn) return JSON.stringify({'isDatePicker': false});

		var r = btn.getBoundingClientRect();
		if (r.width === 0 || r.height === 0) return JSON.stringify({'isDatePicker': false});

		// Return unique selector for the input for later use
		var inputId = input.id ? ('#' + input.id) : '';
		return JSON.stringify({'isDatePicker': true, inputSelector: inputId, buttonX: r.x, buttonY: r.y, centerX: r.x+r.width/2, centerY: r.y+r.height/2, width: r.width, height: r.height});
	})()`, escaped))
}

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
			var curMonth, curYear;
			// Prefer header text over data attributes — some pages
			// reset data-month/data-year during re-render.
			var hdr = popup.querySelector('.calendar-month, .MuiPickersCalendarHeader-label');
			if (hdr) {
				var parts = hdr.textContent.trim().split(' ');
				if (parts.length >= 2) {
					curMonth = ['January','February','March','April','May','June','July','August','September','October','November','December'].indexOf(parts[0]);
					curYear = parseInt(parts[1]);
				}
			}
			if (isNaN(curMonth) || isNaN(curYear)) {
				curMonth = parseInt(popup.getAttribute('data-month'));
				curYear = parseInt(popup.getAttribute('data-year'));
			}
			if (isNaN(curMonth) || isNaN(curYear)) return JSON.stringify({error: 'cannot read calendar month/year'});

			if (curYear === targetYear && curMonth === targetMonth) {
				return JSON.stringify({success: true, month: curMonth, year: curYear});
			}

			// Determine direction
			var isForward = (targetYear > curYear) || (targetYear === curYear && targetMonth > curMonth);
			var delta = isForward ? 1 : -1;

			// Prefer direct function call over button click. Button clicks
			// can trigger innerHTML rewrites that destroy the button mid-event,
			// causing the popup to close via document click handler.
			if (typeof changeMonth === 'function') {
				changeMonth(delta);
			} else {
				var btn = isForward ?
					popup.querySelector('.calendar-header button:last-child') ||
					popup.querySelector('[onclick*="changeMonth(1)"]') :
					popup.querySelector('.calendar-header button:first-child') ||
					popup.querySelector('[onclick*="changeMonth(-1)"]');
				if (!btn) return JSON.stringify({error: 'navigation button not found'});
				if (btn.disabled || btn.classList.contains('Mui-disabled') || btn.getAttribute('aria-disabled') === 'true') {
					return JSON.stringify({error: 'navigation button is disabled — month out of range'});
				}
				btn.click();
			}
		}
		return JSON.stringify({error: 'exceeded 12 month navigation iterations'});
	})()`, targetMonth, targetYear)
}

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

// buildClearJS generates JS to focus an element and clear its value.
func buildClearJS(selector string) string {
	escaped := escapeJS(selector)
	return withPierce(fmt.Sprintf(`(function(){
		var el = __cdpQ('%s');
		if (!el) return JSON.stringify({error: 'element not found'});
		el.focus();
		el.select();
		el.value = '';
		el.dispatchEvent(new Event('input', {bubbles: true}));
		return JSON.stringify({'success': true});
	})()`, escaped))
}

// buildBlurJS generates JS to blur an element and fire a change event.
func buildBlurJS(selector string) string {
	escaped := escapeJS(selector)
	return withPierce(fmt.Sprintf(`(function(){
		var el = __cdpQ('%s');
		if (el) { el.blur(); el.dispatchEvent(new Event('change', {bubbles: true})); }
	})()`, escaped))
}

// buildCheckStateJS generates JS to query an element's checked property.
func buildCheckStateJS(selector string) string {
	escaped := escapeJS(selector)
	return withPierce(fmt.Sprintf(`(function(){
		var el = __cdpQ('%s');
		if (!el) return JSON.stringify({error: 'element not found'});
		return JSON.stringify({checked: el.checked});
	})()`, escaped))
}

// buildSelectOptionJS generates JS to select an option by value or textContent.
func buildSelectOptionJS(selector, option string) string {
	escapedSelector := escapeJS(selector)
	escapedOption := escapeJS(option)
	return withPierce(fmt.Sprintf(`(function(){
		var sel = __cdpQ('%s');
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
	})()`, escapedSelector, escapedOption, escapedOption, escapedOption))
}

// isNativeSelectJS generates JS to check whether the target element is a native <select>.
func isNativeSelectJS(selector string) string {
	escaped := escapeJS(selector)
	return withPierce(fmt.Sprintf(`(function(){
		var el = __cdpQ('%s');
		if (!el) return JSON.stringify({error: 'element not found'});
		return JSON.stringify({'isSelect': el.tagName === 'SELECT'});
	})()`, escaped))
}

// findControlJS generates JS to locate the clickable control inside a custom dropdown wrapper.
// Searches for elements with onclick containing "toggle", "menu", or "open"; falls back to wrapper.
// Returns bounding rect of the found control for DispatchMouseClick.
func findControlJS(wrapperSelector string) string {
	escaped := escapeJS(wrapperSelector)
	return withPierce(fmt.Sprintf(`(function(){
		var wrapper = __cdpQ('%s');
		if (!wrapper) return JSON.stringify({error: 'wrapper not found'});
		var control = __cdpQIn(wrapper, '[onclick*="toggle"], [onclick*="menu"], [onclick*="open"]');
		if (!control) control = wrapper;
		var r = control.getBoundingClientRect();
		if (r.width === 0 || r.height === 0) return JSON.stringify({error: 'no clickable control found'});
		return JSON.stringify({found: true, x: r.x, y: r.y, centerX: r.x+r.width/2, centerY: r.y+r.height/2, width: r.width, height: r.height});
	})()`, escaped))
}

// findCustomOptionJS generates JS to find a dropdown option by text match inside a wrapper.
// Skips elements with zero bounding rect (display:none). Matches exact textContent.trim()
// first, then falls back to substring match.
func findCustomOptionJS(wrapperSelector, optionText string) string {
	escapedWrapper := escapeJS(wrapperSelector)
	escapedOption := escapeJS(optionText)
	return withPierce(fmt.Sprintf(`(function(){
		var wrapper = __cdpQ('%s');
		if (!wrapper) return JSON.stringify({error: 'wrapper not found'});
		var all = __cdpQAIn(wrapper, '*');
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
	})()`, escapedWrapper, escapedOption, escapedOption, escapedOption))
}

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
// For datepicker inputs: detects MUI-style date fields and uses click-to-open/pick flow.
// For plain inputs: uses existing scroll → focus → clear → type-char-by-char → blur flow.
// Workflow: scroll into view → detect datepicker → (date flow | plain flow).
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

	// Brief delay for calendar popup to re-render after navigation
	time.Sleep(100 * time.Millisecond)

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

// CheckElement checks or unchecks a checkbox/radio input.
// Only dispatches a click if the current checked state differs from the target.
func (c *Client) CheckElement(selector string, checked bool, frameID string, track bool) error {
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

	var stateResultStr string
	if err := c.EvalInFrame(frameID, buildCheckStateJS(selector), &stateResultStr); err != nil {
		return err
	}
	var stateResult map[string]interface{}
	if err := json.Unmarshal([]byte(stateResultStr), &stateResult); err != nil {
		return fmt.Errorf("failed to parse check state result: %w", err)
	}
	if errMsg, ok := stateResult["error"]; ok {
		return fmt.Errorf("%v", errMsg)
	}

	isChecked, _ := stateResult["checked"].(bool)
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
