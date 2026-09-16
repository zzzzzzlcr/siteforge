package cmd

import (
	"testing"

	"github.com/spf13/cobra"
)

func TestTargetsCmdRegistered(t *testing.T) {
	found := false
	for _, sub := range rootCmd.Commands() {
		if sub.Name() == "targets" {
			found = true
			if sub.Use != "targets" {
				t.Errorf("Use = %q, want %q", sub.Use, "targets")
			}
			if sub.Short == "" {
				t.Error("Short is empty")
			}
			break
		}
	}
	if !found {
		t.Error("targets command not registered on rootCmd")
	}
}

func findTargetsCmd() *cobra.Command {
	for _, sub := range rootCmd.Commands() {
		if sub.Name() == "targets" {
			return sub
		}
	}
	return nil
}
