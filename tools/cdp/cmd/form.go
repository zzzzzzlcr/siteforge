package cmd

import (
	"fmt"

	"cdp/internal"

	"github.com/spf13/cobra"
)

var formCmd = &cobra.Command{
	Use:   "form [selector]",
	Short: "Fill form element with human-like gesture",
	Long: `Fill a form element using human-like mouse/keyboard gestures.

Three operation modes (mutually exclusive):
  --value   Type text, or auto-pick date on MUI-style datepickers
  --check   Toggle checkbox/radio state
  --select  Choose an option from native <select> or custom dropdown

Datepicker detection (--value):
  When the target has type=tel + date-pattern placeholder
  (mm-dd-yyyy / yyyy-mm-dd) + a calendar trigger button nearby,
  automatically opens the calendar, navigates to the target month,
  and clicks the day. Supports YMD, MDY, and MD formats.

Custom dropdown detection (--select):
  When the target is not a native <select>, searches for a
  clickable control (aria-label or onclick) to open the menu,
  then finds and clicks the matching option by text.`,
	Args: cobra.MaximumNArgs(1),
	RunE: runForm,
}

func init() {
	rootCmd.AddCommand(formCmd)
	formCmd.Flags().String("selector", "", "CSS selector")
	formCmd.Flags().String("value", "", "Text to type, or date (YYYY-MM-DD / MM-DD-YYYY / MM-DD) for datepickers")
	formCmd.Flags().String("check", "", "Check/uncheck: \"true\" or \"false\"")
	formCmd.Flags().String("select", "", "Option value or text (native <select> or custom dropdown)")
	formCmd.Flags().String("frame-id", "", "Frame ID (optional)")
	formCmd.Flags().Bool("track", false, "Enable track visualization")
}

func validateFormFlags(value, check, selectOpt string) error {
	set := 0
	if value != "" {
		set++
	}
	if check != "" {
		set++
	}
	if selectOpt != "" {
		set++
	}
	if set == 0 {
		return fmt.Errorf("one of --value, --check, --select is required")
	}
	if set > 1 {
		return fmt.Errorf("only one of --value, --check, --select can be specified")
	}
	if check != "" && check != "true" && check != "false" {
		return fmt.Errorf("--check must be \"true\" or \"false\", got: %s", check)
	}
	return nil
}

func runForm(cmd *cobra.Command, args []string) error {
	selector, err := resolveSelector(cmd, args)
	if err != nil {
		return err
	}

	frameID, _ := cmd.Flags().GetString("frame-id")
	track, _ := cmd.Flags().GetBool("track")
	value, _ := cmd.Flags().GetString("value")
	check, _ := cmd.Flags().GetString("check")
	selectOpt, _ := cmd.Flags().GetString("select")

	if err := validateFormFlags(value, check, selectOpt); err != nil {
		return err
	}

	client, err := internal.NewClient(GetHost(), GetPort())
	if err != nil {
		return fmt.Errorf("failed to create client: %w", err)
	}
	defer client.Disconnect()

	if value != "" {
		return client.FillText(selector, value, frameID, track)
	}
	if check != "" {
		return client.CheckElement(selector, check == "true", frameID, track)
	}
	return client.SelectOption(selector, selectOpt, frameID, track)
}
