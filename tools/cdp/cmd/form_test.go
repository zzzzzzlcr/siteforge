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
