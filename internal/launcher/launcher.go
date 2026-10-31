// Package launcher locates the Python runtime and replaces the Go process with it.
package launcher

import (
	"fmt"
	"os"
	"path/filepath"
	"runtime"
)

// InstalledPython is embedded by `make install` with -ldflags. When set, an
// installed launcher only uses that interpreter and never searches arbitrary
// directories for a development .venv.
var InstalledPython string

// Error describes a launcher failure with the exit code the CLI should return.
type Error struct {
	Code    int
	Message string
}

func (e *Error) Error() string { return e.Message }

// FindPython returns the Python interpreter that runs the Textual application.
//
// The lookup order is:
//  1. PERPETUI_PYTHON (explicit override)
//  2. The path embedded by `make install` (installed binaries)
//  3. .venv next to the development checkout
//  4. .venv in the current working directory (development only)
func FindPython() (string, error) {
	if override := os.Getenv("PERPETUI_PYTHON"); override != "" {
		if executable(override) {
			return override, nil
		}
		return "", &Error{127, fmt.Sprintf("PERPETUI_PYTHON is not executable: %s", override)}
	}

	if InstalledPython != "" {
		if executable(InstalledPython) {
			return InstalledPython, nil
		}
		return "", &Error{
			127,
			fmt.Sprintf(
				"installed Python environment not found: %s; run `make install` again",
				InstalledPython,
			),
		}
	}

	candidates := []string{}
	if executablePath, err := os.Executable(); err == nil {
		if resolved, err := filepath.EvalSymlinks(executablePath); err == nil {
			executablePath = resolved
		}
		dir := filepath.Dir(executablePath)
		for _, root := range []string{dir, filepath.Dir(dir), filepath.Dir(filepath.Dir(dir))} {
			candidates = append(candidates, pythonIn(filepath.Join(root, ".venv")))
		}
	}
	if workingDirectory, err := os.Getwd(); err == nil {
		candidates = append(candidates, pythonIn(filepath.Join(workingDirectory, ".venv")))
	}

	seen := map[string]bool{}
	for _, candidate := range candidates {
		if seen[candidate] {
			continue
		}
		seen[candidate] = true
		if executable(candidate) {
			return candidate, nil
		}
	}
	return "", &Error{
		127,
		"development Python environment not found; run `make setup` or set PERPETUI_PYTHON",
	}
}

func pythonIn(venv string) string {
	if runtime.GOOS == "windows" {
		return filepath.Join(venv, "Scripts", "python.exe")
	}
	return filepath.Join(venv, "bin", "python")
}

func executable(path string) bool {
	info, err := os.Stat(path)
	if err != nil || info.IsDir() {
		return false
	}
	// Windows does not use Unix execute bits for normal files.
	return runtime.GOOS == "windows" || info.Mode()&0o111 != 0
}
