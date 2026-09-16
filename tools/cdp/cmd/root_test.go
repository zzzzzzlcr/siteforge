package cmd

import (
	"strconv"
	"strings"
	"testing"
)

// Resolution of --host/--port against CDP_HOST/CDP_PORT happens in
// rootCmd.PersistentPreRunE, i.e. only when a subcommand actually executes, and
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

// executeRoot runs the root command with args and returns the error Execute()
// reported, which the caller decides whether to assert on: resolution failures
// (see TestInvalidEnvPortIsRejected) are the command's own verdict and must be
// asserted on, whereas the connection error the command ends with either way
// says nothing about whether flag/env resolution was correct.
func executeRoot(t *testing.T, args []string) error {
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

	err := rootCmd.Execute()
	if err != nil {
		t.Logf("cdp %v -> %v (expected: nothing listens on the test ports)", args, err)
	} else {
		t.Logf("cdp %v -> success (a browser answered on the resolved address)", args)
	}
	return err
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

// TestInvalidEnvPortIsRejected pins the ruling that the two doors of this
// kernel must not disagree about a CDP_PORT they cannot parse. cmd/mcp refuses
// one (internal/mcp/target.go, ResolveTarget: "CDP_PORT=%q 不是端口号..."); the
// CLI used to ignore it and quietly use the default 9222 instead -- i.e. talk to
// a different browser than the operator asked for, with no error anywhere. The
// CLI now refuses it too, with the same sentence.
//
// The flag/env precedence is a separate rule and stays what ffac82b made it
// (explicit flag > env > default): an explicit --port wins outright, so a
// broken value in the environment is never even read. The two rules meet in the
// third case below, which is the one that matters most.
func TestInvalidEnvPortIsRejected(t *testing.T) {
	// Unparseable by any reading: not a number, and nothing TrimSpace can save.
	const badPort = "abc"

	resetRootFlags(t)
	t.Cleanup(func() { resetRootFlags(t) })

	t.Run("CDP_PORT=abc errors and names the offending value", func(t *testing.T) {
		resetRootFlags(t)
		t.Setenv("CDP_PORT", badPort)

		err := executeRoot(t, []string{"targets"})

		if err == nil {
			t.Fatalf("CDP_PORT=%q 被静默忽略了 —— 命令于是连默认的 %d，不是操作者点名的那个端口",
				badPort, portDefault)
		}
		if !strings.Contains(err.Error(), "CDP_PORT") {
			t.Errorf("报错没有点名 CDP_PORT: %v", err)
		}
		if !strings.Contains(err.Error(), badPort) {
			t.Errorf("报错没有点名那个值 %q: %v", badPort, err)
		}
	})

	t.Run("a valid CDP_PORT still applies", func(t *testing.T) {
		resetRootFlags(t)
		t.Setenv("CDP_PORT", strconv.Itoa(portFromEnv))

		executeRoot(t, []string{"targets"})

		if got := GetPort(); got != portFromEnv {
			t.Errorf("GetPort() = %d, want %d", got, portFromEnv)
		}
	})

	// Whitespace around a number is how a value usually arrives by accident
	// (a stray space in a script, a trailing newline from a file): the door
	// that already existed reads it through TrimSpace, so this one must too.
	// Refusing it here would be this same defect mirrored onto the other door.
	t.Run("a padded CDP_PORT applies as the number it wraps", func(t *testing.T) {
		resetRootFlags(t)
		t.Setenv("CDP_PORT", " "+strconv.Itoa(portFromEnv)+" ")

		executeRoot(t, []string{"targets"})

		if got := GetPort(); got != portFromEnv {
			t.Errorf("GetPort() = %d, want %d", got, portFromEnv)
		}
	})

	t.Run("CDP_PORT=\"\" is still not set", func(t *testing.T) {
		resetRootFlags(t)
		t.Setenv("CDP_PORT", "")

		err := executeRoot(t, []string{"targets"})

		if err != nil && strings.Contains(err.Error(), "CDP_PORT") {
			t.Errorf("空的 CDP_PORT 应当等于没设（既有行为），却被当成非法值: %v", err)
		}
		if got := GetPort(); got != portDefault {
			t.Errorf("GetPort() = %d, want %d", got, portDefault)
		}
	})

	// The interaction of the two rules: the flag wins, so the invalid env value
	// must not be consulted at all -- not to override the flag, and not to fail
	// the command either. Nothing listens on portFromFlag, so Execute() still
	// ends in a connection error; what must not show up is the env error.
	t.Run("explicit --port wins and the bad env value is never read", func(t *testing.T) {
		resetRootFlags(t)
		t.Setenv("CDP_PORT", badPort)

		err := executeRoot(t, []string{"--port", strconv.Itoa(portFromFlag), "targets"})

		if got := GetPort(); got != portFromFlag {
			t.Errorf("GetPort() = %d, want %d (显式 flag 应当赢过环境变量)", got, portFromFlag)
		}
		if err != nil && strings.Contains(err.Error(), "CDP_PORT") {
			t.Errorf("显式给了 --port，环境里那个坏的 CDP_PORT 就与本次运行无关，不该把命令拦下来: %v", err)
		}
	})
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
