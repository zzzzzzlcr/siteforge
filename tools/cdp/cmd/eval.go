package cmd

import (
	"encoding/json"
	"fmt"
	"io"
	"os"

	"github.com/spf13/cobra"
	"cdp/internal"
)

var evalCmd = &cobra.Command{
	Use:   "eval",
	Short: "Execute JavaScript in a frame",
	RunE:  runEval,
}

func init() {
	rootCmd.AddCommand(evalCmd)
	evalCmd.Flags().String("frame-id", "", "Frame ID to execute in (empty for main frame)")
	evalCmd.Flags().String("file", "", "Read JS from file instead of argument")
}

func runEval(cmd *cobra.Command, args []string) error {
	frameID, _ := cmd.Flags().GetString("frame-id")
	filePath, _ := cmd.Flags().GetString("file")

	var js string
	var err error

	if filePath != "" {
		js, err = readFile(filePath)
		if err != nil {
			return fmt.Errorf("failed to read file: %w", err)
		}
	} else {
		if len(args) == 0 {
			return fmt.Errorf("no JS code provided")
		}
		js = args[0]
	}

	client, err := internal.NewClient(GetHost(), GetPort())
	if err != nil {
		return fmt.Errorf("failed to create client: %w", err)
	}
	defer client.Disconnect()

	var result json.RawMessage
	err = client.EvalInFrame(frameID, js, &result)
	if err != nil {
		return fmt.Errorf("JS exception: %s", err)
	}

	if len(result) > 0 {
		fmt.Fprintln(os.Stdout, string(result))
	}
	return nil
}

func readFile(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	data, err := io.ReadAll(f)
	return string(data), err
}