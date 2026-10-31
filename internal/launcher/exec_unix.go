//go:build !windows

package launcher

import (
	"os"
	"syscall"
)

// ExecPython replaces the launcher with `python -m perpetui`, preserving the
// terminal, standard streams, signals, and exit status.
func ExecPython(python string, args []string) error {
	argv := append([]string{python, "-m", "perpetui"}, args...)
	return syscall.Exec(python, argv, os.Environ())
}
