package cli

import (
	"bytes"
	"os"
	"path/filepath"
	"slices"
	"testing"
)

func TestRootForwardsOnlyChangedFlagsToPython(t *testing.T) {
	original := runTUI
	t.Cleanup(func() { runTUI = original })
	var forwarded []string
	runTUI = func(args []string) error {
		forwarded = args
		return nil
	}

	root := NewRootCommand()
	root.SetOut(&bytes.Buffer{})
	root.SetErr(&bytes.Buffer{})
	root.SetArgs([]string{
		"--demo",
		"--exchange", "lighter",
		"--refresh", "2.5",
		"--lang", "ja",
	})
	if err := root.Execute(); err != nil {
		t.Fatal(err)
	}
	want := []string{"--demo", "--exchange", "lighter", "--lang", "ja", "--refresh", "2.5"}
	if !slices.Equal(forwarded, want) {
		t.Fatalf("forwarded = %#v, want %#v", forwarded, want)
	}
}

func TestVersionAndHelpNeverStartPython(t *testing.T) {
	original := runTUI
	t.Cleanup(func() { runTUI = original })
	runTUI = func([]string) error {
		t.Fatal("Python was started")
		return nil
	}

	for _, args := range [][]string{{"--version"}, {"--help"}} {
		root := NewRootCommand()
		var out bytes.Buffer
		root.SetOut(&out)
		root.SetErr(&bytes.Buffer{})
		root.SetArgs(args)
		if err := root.Execute(); err != nil {
			t.Fatal(err)
		}
		if out.Len() == 0 {
			t.Fatalf("%v produced no output", args)
		}
	}
}

func TestCompletionScriptsAreGeneratedForEveryShell(t *testing.T) {
	for _, shell := range []string{"bash", "zsh", "fish", "powershell"} {
		root := NewRootCommand()
		var out bytes.Buffer
		root.SetOut(&out)
		root.SetErr(&bytes.Buffer{})
		root.SetArgs([]string{"completion", shell})
		if err := root.Execute(); err != nil {
			t.Fatalf("%s: %v", shell, err)
		}
		if out.Len() == 0 {
			t.Fatalf("%s produced no completion script", shell)
		}
	}
}

func TestManPageGeneration(t *testing.T) {
	directory := t.TempDir()
	root := NewRootCommand()
	root.SetOut(&bytes.Buffer{})
	root.SetErr(&bytes.Buffer{})
	root.SetArgs([]string{"docs", "man", "--dir", directory})
	if err := root.Execute(); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(filepath.Join(directory, "perpetui.1")); err != nil {
		t.Fatal(err)
	}
}
