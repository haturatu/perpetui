//go:build windows

package launcher

import (
	"errors"
	"os"
	"os/exec"
)

// ExecPython starts the Python TUI and mirrors its exit status on Windows,
// where replacing the current process is not available.
func ExecPython(python string, args []string) error {
	command := exec.Command(python, append([]string{"-m", "perpetui"}, args...)...)
	command.Stdin, command.Stdout, command.Stderr = os.Stdin, os.Stdout, os.Stderr
	err := command.Run()
	var exitError *exec.ExitError
	if errors.As(err, &exitError) {
		os.Exit(exitError.ExitCode())
	}
	return err
}
