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

func TestFindPythonFindsTheInstalledDataDirectoryVenv(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("the install layout uses POSIX paths")
	}
	dataHome := t.TempDir()
	python := filepath.Join(dataHome, "perpetui", "venv", "bin", "python")
	if err := os.MkdirAll(filepath.Dir(python), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(python, []byte("#!/bin/sh\n"), 0o755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("XDG_DATA_HOME", dataHome)
	t.Setenv("PERPETUI_PYTHON", "")
	got, err := FindPython()
	if err != nil {
		t.Fatal(err)
	}
	if got != python {
		t.Fatalf("got %q, want %q", got, python)
	}
}
