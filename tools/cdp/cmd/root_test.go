package cmd

import (
	"strconv"
	"testing"
)

// Resolution of --host/--port against CDP_HOST/CDP_PORT happens in
// rootCmd.PersistentPreRun, i.e. only when a subcommand actually executes, and
// it writes the package-level host/port vars that GetHost/GetPort report. So
// these tests drive the real root command with a real subcommand ("targets")
// and read the resolution that survived Execute().
//
// Nothing listens on the ports used here (they are deliberately not 9222, the
// port a live Chrome may occupy), so Execute() is *expected* to return a
// connection error. That error is irrelevant: it happens after the env/flag
// resolution under test has already run. The tests never need a browser.
const (
	portFromFlag = 12345 // passed via --port
	portFromEnv  = 9999  // passed via CDP_PORT
	portDefault  = 9222  // --port's default
)

// resetRootFlags restores the root singleton's flag state to its defaults.
// rootCmd is package-level and cobra/pflag keep parsed state — including
// pflag's Changed bit — across Execute() calls, so without this the cases below
// (and any later test) would observe leaked state.
func resetRootFlags(t *testing.T) {
	t.Helper()
	for _, name := range []string{"host", "port"} {
		f := rootCmd.PersistentFlags().Lookup(name)
		if f == nil {
			t.Fatalf("root persistent flag --%s is not registered", name)
		}
		// StringVar/IntVar bind the flag's Value to the package vars, so
		// setting it through the flag restores what GetHost/GetPort report.
		if err := f.Value.Set(f.DefValue); err != nil {
			t.Fatalf("reset --%s to %q: %v", name, f.DefValue, err)
		}
		f.Changed = false
	}
	rootCmd.SetArgs(nil)
}

// executeRoot runs the root command with args. The returned error is logged,
// not asserted on: the command is expected to fail to reach a browser, which
// says nothing about whether flag/env resolution was correct.
func executeRoot(t *testing.T, args []string) {
	t.Helper()
	rootCmd.SetArgs(args)

	// The expected connection failure would otherwise dump cobra's error and
	// the full usage text. Both fields live on the root singleton, so put them
	// back the way we found them.
	silenceErrors, silenceUsage := rootCmd.SilenceErrors, rootCmd.SilenceUsage
	rootCmd.SilenceErrors, rootCmd.SilenceUsage = true, true
	defer func() {
		rootCmd.SilenceErrors, rootCmd.SilenceUsage = silenceErrors, silenceUsage
	}()

	if err := rootCmd.Execute(); err != nil {
		t.Logf("cdp %v -> %v (expected: nothing listens on the test ports)", args, err)
	} else {
		t.Logf("cdp %v -> success (a browser answered on the resolved address)", args)
	}
}

func TestPortFlagNotClobberedByEnv(t *testing.T) {
	resetRootFlags(t)
	t.Cleanup(func() { resetRootFlags(t) })

	cases := []struct {
		name     string
		envPort  string // CDP_PORT; "" neutralizes any ambient value
		args     []string
		wantPort int
	}{
		{
			// The defect: an explicit --port was overwritten by CDP_PORT,
			// because the guard asked the subcommand's own (empty) persistent
			// flagset whether --port had been changed and always got "no".
			name:     "explicit flag wins over CDP_PORT",
			envPort:  strconv.Itoa(portFromEnv),
			args:     []string{"--port", strconv.Itoa(portFromFlag), "targets"},
			wantPort: portFromFlag,
		},
		{
			// The env var must still be honoured when the flag is absent.
			name:     "CDP_PORT applies when --port is absent",
			envPort:  strconv.Itoa(portFromEnv),
			args:     []string{"targets"},
			wantPort: portFromEnv,
		},
		{
			name:     "default when neither is given",
			envPort:  "",
			args:     []string{"targets"},
			wantPort: portDefault,
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			resetRootFlags(t)
			t.Setenv("CDP_PORT", tc.envPort)

			executeRoot(t, tc.args)

			if got := GetPort(); got != tc.wantPort {
				t.Errorf("GetPort() = %d, want %d", got, tc.wantPort)
			}
		})
	}
}

// TestHostFlagNotClobberedByEnv pins the same guard for --host/CDP_HOST, which
// had the identical defect. Both values are loopback aliases (Chrome binds only
// 127.0.0.1, so these refuse instantly) yet differ from --host's 127.0.0.1
// default, so an explicit flag is observable. Avoid unroutable addresses like
// 192.0.2.1 here: the dial then blocks until the TCP timeout and the test takes
// ten seconds instead of microseconds.
func TestHostFlagNotClobberedByEnv(t *testing.T) {
	const (
		hostFromFlag = "127.0.0.2"
		hostFromEnv  = "127.0.0.3"
		hostDefault  = "127.0.0.1"
	)

	resetRootFlags(t)
	t.Cleanup(func() { resetRootFlags(t) })

	t.Run("explicit flag wins over CDP_HOST", func(t *testing.T) {
		resetRootFlags(t)
		t.Setenv("CDP_HOST", hostFromEnv)
		t.Setenv("CDP_PORT", "")

		executeRoot(t, []string{"--host", hostFromFlag, "targets"})

		if got := GetHost(); got != hostFromFlag {
			t.Errorf("GetHost() = %q, want %q", got, hostFromFlag)
		}
	})

	t.Run("CDP_HOST applies when --host is absent", func(t *testing.T) {
		resetRootFlags(t)
		t.Setenv("CDP_HOST", hostFromEnv)
		t.Setenv("CDP_PORT", "")

		executeRoot(t, []string{"targets"})

		if got := GetHost(); got != hostFromEnv {
			t.Errorf("GetHost() = %q, want %q", got, hostFromEnv)
		}
	})

	t.Run("default when neither is given", func(t *testing.T) {
		resetRootFlags(t)
		t.Setenv("CDP_HOST", "")
		t.Setenv("CDP_PORT", "")

		executeRoot(t, []string{"targets"})

		if got := GetHost(); got != hostDefault {
			t.Errorf("GetHost() = %q, want %q", got, hostDefault)
		}
	})
}
