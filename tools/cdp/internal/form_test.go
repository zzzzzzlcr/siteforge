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
		{"it's", `it\'s`},
		{`a\b`, `a\\b`},
		{`'quoted'`, `\'quoted\'`},
		{"", ""},
		{`foo\'bar`, `foo\\\'bar`},
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
	if !strings.Contains(js, "__cdpQ('#myInput')") {
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
	if !strings.Contains(js, "__cdpQ('#myInput')") {
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
	if !strings.Contains(js, "__cdpQ('#cb')") {
		t.Errorf("missing selector: %s", js)
	}
	if !strings.Contains(js, ".checked") {
		t.Errorf("missing .checked: %s", js)
	}
	if !strings.Contains(js, "'element not found'") {
		t.Errorf("missing error message: %s", js)
	}
	if !strings.Contains(js, "JSON.stringify") {
		t.Errorf("missing JSON.stringify: %s", js)
	}
}

func TestBuildClearJSEscapedSelector(t *testing.T) {
	js := buildClearJS("#it's")
	if !strings.Contains(js, "__cdpQ('#it\\'s')") {
		t.Errorf("selector not properly escaped: %s", js)
	}
}

func TestBuildSelectOptionJS(t *testing.T) {
	js := buildSelectOptionJS("#sel", "cn")
	if !strings.Contains(js, "__cdpQ('#sel')") {
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

func TestIsNativeSelectJS(t *testing.T) {
	js := isNativeSelectJS("#my-select")
	if !strings.Contains(js, "__cdpQ('#my-select')") {
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

func TestFindControlJS(t *testing.T) {
	js := findControlJS("#country-wrapper")
	if !strings.Contains(js, "__cdpQ('#country-wrapper')") {
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

func TestFindCustomOptionJS(t *testing.T) {
	js := findCustomOptionJS("#country-wrapper", "Canada")
	if !strings.Contains(js, "__cdpQ('#country-wrapper')") {
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
	if !strings.Contains(js, "r.width === 0") {
		t.Errorf("missing zero-size skip: %s", js)
	}
}

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
	if !strings.Contains(js, `var targetMonth = 6`) {
		t.Errorf("missing target month: %s", js)
	}
	if !strings.Contains(js, `var targetYear = 2026`) {
		t.Errorf("missing target year: %s", js)
	}
	if !strings.Contains(js, "calendar popup not found") {
		t.Errorf("missing popup error: %s", js)
	}
}

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
	if !strings.Contains(js, `var targetDay = 4`) {
		t.Errorf("missing target day: %s", js)
	}
}

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
