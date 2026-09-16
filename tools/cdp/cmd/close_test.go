package cmd

import (
	"testing"
)

func TestCloseCmdRegistered(t *testing.T) {
	found := false
	for _, sub := range rootCmd.Commands() {
		if sub.Name() == "close" {
			found = true
			if sub.Use != "close [target-id...]" {
				t.Errorf("Use = %q, want %q", sub.Use, "close [target-id...]")
			}
			if sub.Short == "" {
				t.Error("Short is empty")
			}
			break
		}
	}
	if !found {
		t.Error("close command not registered on rootCmd")
	}
}

func TestCloseCmdAllFlag(t *testing.T) {
	for _, sub := range rootCmd.Commands() {
		if sub.Name() == "close" {
			flag := sub.Flags().Lookup("all")
			if flag == nil {
				t.Fatal("--all flag not registered")
			}
			if flag.DefValue != "false" {
				t.Errorf("--all default = %q, want %q", flag.DefValue, "false")
			}
			return
		}
	}
	t.Fatal("close command not found")
}
