package cmd

import (
	"os"

	"cdp/internal"

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
			// CDP_PORT 的读法（空 = 没设、前后空白不算数、坏的当场拒绝且报哪句错）
			// 只有一份：internal.EnvPort。这道门不再自带一段 Atoi —— 同一个规矩
			// 在两处各写一遍，正是它最容易分家的形态：改掉其中一句文案，两道门
			// 从此对同一个输入说两句不同的话，而两边的测试都还是绿的。
			// （跨门逐字对齐的闸门在 cmd/port_parity_test.go。）
			n, set, err := internal.EnvPort(os.Getenv("CDP_PORT"))
			if err != nil {
				return err
			}
			if set {
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
