// Package cli implements the Cobra command tree for the perpetui launcher.
package cli

import (
	"errors"
	"fmt"
	"io"
	"os"

	"github.com/haturatu/perpetui/internal/launcher"
	"github.com/spf13/cobra"
	"github.com/spf13/cobra/doc"
	"github.com/spf13/pflag"
)

const version = "0.1.0"

var runTUI = func(args []string) error {
	python, err := launcher.FindPython()
	if err != nil {
		return err
	}
	return launcher.ExecPython(python, args)
}

// NewRootCommand builds the public Cobra command tree.
func NewRootCommand() *cobra.Command {
	root := &cobra.Command{
		Use:           "perpetui",
		Short:         "Terminal dashboard for Hyperliquid, Lighter and MEXC perpetuals",
		Long:          "perpetui is a read-only terminal dashboard for Hyperliquid, Lighter and MEXC perpetual charts and open positions.",
		Version:       version,
		Args:          cobra.NoArgs,
		SilenceUsage:  true,
		SilenceErrors: true,
		RunE: func(cmd *cobra.Command, _ []string) error {
			return runTUI(pythonArgs(cmd))
		},
	}
	root.SetVersionTemplate("perpetui {{.Version}}\n")
	root.CompletionOptions.DisableDefaultCmd = true
	root.DisableAutoGenTag = true

	flags := root.Flags()
	flags.String("lang", "", "UI language: en or ja (default: system locale)")
	flags.String("exchange", "hyperliquid", "Exchange: hyperliquid, hl, lighter, lt or mexc")
	flags.Bool("demo", false, "Use sample data, without network access")
	flags.Bool("demo-empty", false, "Demo with no positions (BTC chart)")
	flags.Bool("public", false, "Market data only; skip account lookup")
	flags.String("symbol", "", "Initial symbol, e.g. BTC, ETH_USDT or xyz:CL")
	flags.String("interval", "15m", "Initial candle interval")
	flags.Float64("refresh", 5, "REST fallback refresh interval in seconds")
	flags.Bool("no-websocket", false, "Use REST polling for charts and positions")
	flags.String("websocket-url", "", "Override the selected exchange's WebSocket endpoint")
	flags.String("account", "", "Saved account alias (default: saved default)")
	flags.String("mexc-command", "", "Original mexc command path for account decryption")
	flags.String("hl-command", "", "Original hl command path for account decryption")
	flags.String("wallet-address", "", "Hyperliquid monitoring address (no private key required)")
	flags.Bool("testnet", false, "Use the selected exchange's testnet (Hyperliquid or Lighter)")
	flags.String("hyperliquid-base-url", "", "Override the Hyperliquid HTTP endpoint")
	flags.String("lighter-account-index", "", "Lighter account index (overrides the saved account)")
	flags.String("lighter-config", "", "Lighter account database path (default: lighter CLI config)")
	flags.String("lighter-base-url", "", "Override the Lighter HTTP endpoint")
	flags.String("futures-base-url", "", "Override the MEXC HTTP endpoint")
	flags.Float64("timeout", 10, "Network timeout in seconds")
	flags.Bool("debug", false, "Enable DEBUG / INFO logs on stdout")
	flags.Bool("install-konsole-keymap", false, "Install the optional Konsole Ctrl+number keymap")

	registerCompletions(root)
	root.AddCommand(newCompletionCommand(root), newDocsCommand(root))
	return root
}

func pythonArgs(cmd *cobra.Command) []string {
	args := []string{}
	cmd.Flags().Visit(func(flag *pflag.Flag) {
		if flag.Value.Type() == "bool" {
			if flag.Value.String() == "true" {
				args = append(args, "--"+flag.Name)
			}
			return
		}
		args = append(args, "--"+flag.Name, flag.Value.String())
	})
	return args
}

func registerCompletions(root *cobra.Command) {
	flagCompletion := map[string][]string{
		"lang":     {"en", "ja"},
		"exchange": {"hyperliquid", "hl", "lighter", "lt", "mexc"},
		"interval": {"1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"},
	}
	for name, values := range flagCompletion {
		values := values
		_ = root.RegisterFlagCompletionFunc(name, func(*cobra.Command, []string, string) ([]string, cobra.ShellCompDirective) {
			return values, cobra.ShellCompDirectiveNoFileComp
		})
	}
}

func newCompletionCommand(root *cobra.Command) *cobra.Command {
	command := &cobra.Command{
		Use:   "completion",
		Short: "Generate shell completion scripts",
	}
	shells := []struct {
		name string
		run  func(io.Writer) error
	}{
		{"bash", func(out io.Writer) error { return root.GenBashCompletionV2(out, true) }},
		{"zsh", func(out io.Writer) error { return root.GenZshCompletion(out) }},
		{"fish", func(out io.Writer) error { return root.GenFishCompletion(out, true) }},
		{"powershell", func(out io.Writer) error { return root.GenPowerShellCompletionWithDesc(out) }},
	}
	for _, shell := range shells {
		shell := shell
		command.AddCommand(&cobra.Command{
			Use:   shell.name,
			Short: "Generate the " + shell.name + " completion script",
			Args:  cobra.NoArgs,
			RunE: func(cmd *cobra.Command, _ []string) error {
				return shell.run(cmd.OutOrStdout())
			},
		})
	}
	return command
}

func newDocsCommand(root *cobra.Command) *cobra.Command {
	command := &cobra.Command{
		Use:   "docs",
		Short: "Generate documentation",
	}
	directory := "build/man/man1"
	man := &cobra.Command{
		Use:   "man",
		Short: "Generate man pages",
		Args:  cobra.NoArgs,
		RunE: func(*cobra.Command, []string) error {
			if err := os.MkdirAll(directory, 0o755); err != nil {
				return err
			}
			header := &doc.GenManHeader{
				Title:   "PERPETUI",
				Section: "1",
				Source:  "perpetui",
				Manual:  "perpetui manual",
			}
			return doc.GenManTree(root, header, directory)
		},
	}
	man.Flags().StringVar(&directory, "dir", directory, "Output directory for man pages")
	command.AddCommand(man)
	return command
}

// Execute runs the command tree and returns a process exit code.
func Execute() int {
	if err := NewRootCommand().Execute(); err != nil {
		var launchError *launcher.Error
		if errors.As(err, &launchError) {
			fmt.Fprintln(os.Stderr, "perpetui launcher:", launchError.Message)
			return launchError.Code
		}
		fmt.Fprintln(os.Stderr, "perpetui:", err)
		return 1
	}
	return 0
}
