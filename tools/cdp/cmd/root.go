package cmd

import (
	"os"
	"strconv"

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

	rootCmd.PersistentPreRun = func(cmd *cobra.Command, args []string) {
		if !cmd.PersistentFlags().Changed("host") {
			if v := os.Getenv("CDP_HOST"); v != "" {
				host = v
			}
		}
		if !cmd.PersistentFlags().Changed("port") {
			if v := os.Getenv("CDP_PORT"); v != "" {
				if n, err := strconv.Atoi(v); err == nil {
					port = n
				}
			}
		}
	}
}

func GetHost() string {
	return host
}

func GetPort() int {
	return port
}