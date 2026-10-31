package launcher

import (
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

func TestFindPythonHonorsTheExplicitOverride(t *testing.T) {
	python := filepath.Join(t.TempDir(), "python")
	if err := os.WriteFile(python, []byte("#!/bin/sh\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PERPETUI_PYTHON", python)
	got, err := FindPython()
	if err != nil {
		t.Fatal(err)
	}
	if got != python {
		t.Fatalf("got %q, want %q", got, python)
	}
}

func TestFindPythonRejectsAnInvalidOverride(t *testing.T) {
	t.Setenv("PERPETUI_PYTHON", filepath.Join(t.TempDir(), "missing"))
	_, err := FindPython()
	launchError, ok := err.(*Error)
	if !ok || launchError.Code != 127 {
		t.Fatalf("err = %v, want a 127 launcher error", err)
	}
}

func TestInstalledPythonIsUsedInsteadOfSearchingDevelopmentVenvs(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("the install layout uses POSIX paths")
	}
	original := InstalledPython
	t.Cleanup(func() { InstalledPython = original })
	t.Setenv("PERPETUI_PYTHON", "")

	installed := filepath.Join(t.TempDir(), "installed", "bin", "python")
	if err := os.MkdirAll(filepath.Dir(installed), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(installed, []byte("#!/bin/sh\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	InstalledPython = installed

	got, err := FindPython()
	if err != nil {
		t.Fatal(err)
	}
	if got != installed {
		t.Fatalf("got %q, want %q", got, installed)
	}
}

func TestMissingInstalledPythonDoesNotFallBackToAnotherVenv(t *testing.T) {
	original := InstalledPython
	t.Cleanup(func() { InstalledPython = original })
	t.Setenv("PERPETUI_PYTHON", "")
	InstalledPython = filepath.Join(t.TempDir(), "missing", "python")

	_, err := FindPython()
	launchError, ok := err.(*Error)
	if !ok || launchError.Code != 127 {
		t.Fatalf("err = %v, want a 127 launcher error", err)
	}
}
