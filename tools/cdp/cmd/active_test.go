package cmd

import (
	"testing"

	"github.com/spf13/cobra"
)

func TestActiveCmdRegistered(t *testing.T) {
	found := false
	for _, sub := range rootCmd.Commands() {
		if sub.Name() == "active" {
			found = true
			if sub.Use != "active [target]" {
				t.Errorf("Use = %q, want %q", sub.Use, "active [target]")
			}
			if sub.Short == "" {
				t.Error("Short is empty")
			}
			break
		}
	}
	if !found {
		t.Error("active command not registered on rootCmd")
	}
}

func TestActiveCmdTargetFlag(t *testing.T) {
	cmd := findActiveCmd()
	if cmd == nil {
		t.Fatal("active command not found")
	}
	flag := cmd.Flags().Lookup("target")
	if flag == nil {
		t.Fatal("--target flag not registered")
	}
	if flag.DefValue != "" {
		t.Errorf("--target default = %q, want %q", flag.DefValue, "")
	}
}

func TestResolveTarget(t *testing.T) {
	tests := []struct {
		name     string
		flagVal  string
		args     []string
		expected string
		wantErr  bool
	}{
		{
			name:     "from flag",
			flagVal:  "ABC123",
			args:     []string{},
			expected: "ABC123",
		},
		{
			name:     "from positional arg",
			flagVal:  "",
			args:     []string{"DEF456"},
			expected: "DEF456",
		},
		{
			name:     "flag takes precedence",
			flagVal:  "ABC123",
			args:     []string{"DEF456"},
			expected: "ABC123",
		},
		{
			name:    "no target",
			flagVal: "",
			args:    []string{},
			wantErr: true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			cmd := &cobra.Command{}
			cmd.Flags().String("target", "", "target ID")
			if tt.flagVal != "" {
				cmd.Flags().Set("target", tt.flagVal)
			}
			result, err := resolveTarget(cmd, tt.args)
			if tt.wantErr {
				if err == nil {
					t.Error("expected error, got nil")
				}
				return
			}
			if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			if result != tt.expected {
				t.Errorf("= %q, want %q", result, tt.expected)
			}
		})
	}
}

func findActiveCmd() *cobra.Command {
	for _, sub := range rootCmd.Commands() {
		if sub.Name() == "active" {
			return sub
		}
	}
	return nil
}
