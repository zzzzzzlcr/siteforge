package cmd

import (
	"fmt"
	"os"
	"strconv"
	"strings"

	"github.com/spf13/cobra"
)

var (
	host string
	port int
)

var rootCmd = &cobra.Command{
	Use:   "cdp",
	Short: "CDP CLI tool for Chrome DevTools Protocol",
	Long:  `A CLI tool to interact with Chrome DevTools Protocol`,
}

func Execute() error {
	return rootCmd.Execute()
}

func init() {
	rootCmd.PersistentFlags().StringVar(&host, "host", "127.0.0.1", "Chrome host address")
	rootCmd.PersistentFlags().IntVar(&port, "port", 9222, "Chrome port")

	// PersistentPreRunE, not PersistentPreRun: a bad CDP_PORT now has to stop the
	// command, and PersistentPreRun cannot return an error (it would take a panic
	// or an os.Exit to report one).
	rootCmd.PersistentPreRunE = func(cmd *cobra.Command, args []string) error {
		// cmd here is the *subcommand* being executed, never rootCmd. Its
		// PersistentFlags() flagset is empty, so Changed() would always report
		// false and the env var would clobber an explicit flag. The inherited
		// (root) persistent flags are merged into cmd.Flags() during
		// ParseFlags, so that is the set to query.
		if !cmd.Flags().Changed("host") {
			// CDP_HOST is a plain string: every value is a host as far as this
			// layer is concerned, so there is no "present but invalid" case here
			// (an empty value stays "not set", as before).
			if v := os.Getenv("CDP_HOST"); v != "" {
				host = v
			}
		}
		if !cmd.Flags().Changed("port") {
			if v := os.Getenv("CDP_PORT"); v != "" {
				// TrimSpace as well: internal/mcp does `Atoi(TrimSpace(...))`,
				// so " 9999 " has to mean 9999 on both doors -- otherwise a
				// padded value is an error on one door and works on the other.
				n, err := strconv.Atoi(strings.TrimSpace(v))
				if err != nil {
					// Refuse it. Ignoring an unparseable CDP_PORT means "I set
					// CDP_PORT, but it had no effect": the operator believes the
					// command talks to the port they named while it silently
					// talks to the default one -- a *different browser*, and no
					// error anywhere. cmd/mcp already refuses this exact case
					// (internal/mcp/target.go, ResolveTarget); this is the same
					// ruling on the other door, and the message is word-for-word
					// the one that door produces, so both doors name the same
					// problem the same way.
					return fmt.Errorf("CDP_PORT=%q 不是端口号（要么改成数字，要么去掉它，要么显式给 --port）", v)
				}
				port = n
			}
		}
		return nil
	}
}

func GetHost() string {
	return host
}

func GetPort() int {
	return port
}
