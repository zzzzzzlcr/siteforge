package internal

import (
	"fmt"
	"os"
	"strings"
	"testing"
	"time"
)

func TestSelectOption_CustomDropdown_Single(t *testing.T) {
	host := os.Getenv("CDP_HOST")
	if host == "" {
		host = "127.0.0.1"
	}
	port := 9222

	pages, err := ListPageTargets(host, port, false)
	if err != nil || len(pages) == 0 {
		t.Skipf("Chrome not available: %v", err)
	}

	client, err := NewClient(host, port)
	if err != nil {
		t.Fatalf("NewClient failed: %v", err)
	}
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
	if host == "" {
		host = "127.0.0.1"
	}
	port := 9222

	pages, err := ListPageTargets(host, port, false)
	if err != nil || len(pages) == 0 {
		t.Skipf("Chrome not available: %v", err)
	}

	client, err := NewClient(host, port)
	if err != nil {
		t.Fatalf("NewClient failed: %v", err)
	}
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
	if host == "" {
		host = "127.0.0.1"
	}
	port := 9222

	pages, err := ListPageTargets(host, port, false)
	if err != nil || len(pages) == 0 {
		t.Skipf("Chrome not available: %v", err)
	}

	client, err := NewClient(host, port)
	if err != nil {
		t.Fatalf("NewClient failed: %v", err)
	}
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

func TestFillText_DatePicker_YMD(t *testing.T) {
	host := os.Getenv("CDP_HOST")
	if host == "" {
		host = "127.0.0.1"
	}
	port := 9222

	pages, err := ListPageTargets(host, port, false)
	if err != nil || len(pages) == 0 {
		t.Skipf("Chrome not available: %v", err)
	}

	client, err := NewClient(host, port)
	if err != nil {
		t.Fatalf("NewClient failed: %v", err)
	}
	defer client.Disconnect()

	client.Navigate("http://localhost:8080/mui-datepicker", "")

	err = client.FillText("#dob-field", "2026-07-04", "", false)
	if err != nil {
		t.Fatalf("FillText failed: %v", err)
	}

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
	if host == "" {
		host = "127.0.0.1"
	}
	port := 9222

	pages, err := ListPageTargets(host, port, false)
	if err != nil || len(pages) == 0 {
		t.Skipf("Chrome not available: %v", err)
	}

	client, err := NewClient(host, port)
	if err != nil {
		t.Fatalf("NewClient failed: %v", err)
	}
	defer client.Disconnect()

	client.Navigate("http://localhost:8080/mui-datepicker", "")

	err = client.FillText("#dob-field", "07-04-2026", "", false)
	if err != nil {
		t.Fatalf("FillText failed: %v", err)
	}

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
	if host == "" {
		host = "127.0.0.1"
	}
	port := 9222

	pages, err := ListPageTargets(host, port, false)
	if err != nil || len(pages) == 0 {
		t.Skipf("Chrome not available: %v", err)
	}

	client, err := NewClient(host, port)
	if err != nil {
		t.Fatalf("NewClient failed: %v", err)
	}
	defer client.Disconnect()

	client.Navigate("http://localhost:8080/mui-datepicker", "")

	err = client.FillText("#dob-field", "07-04", "", false)
	if err != nil {
		t.Fatalf("FillText failed: %v", err)
	}

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
	if host == "" {
		host = "127.0.0.1"
	}
	port := 9222

	pages, err := ListPageTargets(host, port, false)
	if err != nil || len(pages) == 0 {
		t.Skipf("Chrome not available: %v", err)
	}

	client, err := NewClient(host, port)
	if err != nil {
		t.Fatalf("NewClient failed: %v", err)
	}
	defer client.Disconnect()

	client.Navigate("http://localhost:8080/mui-datepicker", "")

	// Fill a plain text input — should use existing type-char-by-char flow
	err = client.FillText("#fullname", "John Doe", "", false)
	if err != nil {
		t.Fatalf("FillText plain input failed: %v", err)
	}

	var val string
	if err := client.EvalInFrame("", "document.getElementById('fullname').value", &val); err != nil {
		t.Fatalf("check value failed: %v", err)
	}
	if val != "John Doe" {
		t.Errorf("fullname value = %q, want %q", val, "John Doe")
	}
}
