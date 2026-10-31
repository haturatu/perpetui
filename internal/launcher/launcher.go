// Package launcher locates the Python runtime and replaces the Go process with it.
package launcher

import (
	"fmt"
	"os"
	"path/filepath"
	"runtime"
)

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
//  2. $XDG_DATA_HOME/perpetui/venv (or ~/.local/share) from `make install`
//  3. .venv next to the repository checkout, found relative to the executable
//  4. .venv in the current working directory
func FindPython() (string, error) {
	if override := os.Getenv("PERPETUI_PYTHON"); override != "" {
		if executable(override) {
			return override, nil
		}
		return "", &Error{127, fmt.Sprintf("PERPETUI_PYTHON is not executable: %s", override)}
	}

	candidates := []string{}
	if dataHome := os.Getenv("XDG_DATA_HOME"); dataHome != "" {
		candidates = append(candidates, pythonIn(filepath.Join(dataHome, "perpetui", "venv")))
	}
	if home, err := os.UserHomeDir(); err == nil {
		candidates = append(candidates, pythonIn(filepath.Join(home, ".local", "share", "perpetui", "venv")))
	}
	if executablePath, err := os.Executable(); err == nil {
		if resolved, err := filepath.EvalSymlinks(executablePath); err == nil {
			executablePath = resolved
		}
		dir := filepath.Dir(executablePath)
		prefix := filepath.Dir(dir)
		candidates = append(candidates, pythonIn(filepath.Join(prefix, "share", "perpetui", "venv")))
		for _, root := range []string{dir, prefix, filepath.Dir(prefix)} {
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
		"Python environment not found; run `make install` or set PERPETUI_PYTHON",
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
	return err == nil && !info.IsDir() && info.Mode()&0o111 != 0
}
