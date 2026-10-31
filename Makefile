GO ?= go
GOFMT ?= gofmt
UV ?= uv
PYTHON ?= python3
PY_TOOL ?= auto
ARGS ?=

VENV := $(CURDIR)/.venv
VENV_PYTHON := $(VENV)/bin/python
BUILD_DIR := $(CURDIR)/build
BIN := $(BUILD_DIR)/perpetui
INSTALL_BIN := $(BUILD_DIR)/perpetui-install
COMPLETION_DIR := $(BUILD_DIR)/completions
MAN_DIR := $(BUILD_DIR)/man/man1
LOCK_RUNTIME := $(CURDIR)/requirements.lock
LOCK_DEV := $(CURDIR)/requirements-dev.lock

PREFIX ?= $(HOME)/.local
BINDIR ?= $(PREFIX)/bin
DATADIR ?= $(or $(XDG_DATA_HOME),$(PREFIX)/share)
APPDIR := $(DATADIR)/perpetui
INSTALL_VENV := $(APPDIR)/venv
INSTALL_PYTHON := $(INSTALL_VENV)/bin/python
INSTALL_LDFLAGS := -X github.com/haturatu/perpetui/internal/launcher.InstalledPython=$(INSTALL_PYTHON)
INSTALL_BASH := $(DATADIR)/bash-completion/completions/perpetui
INSTALL_ZSH := $(DATADIR)/zsh/site-functions/_perpetui
INSTALL_FISH := $(DATADIR)/fish/vendor_completions.d/perpetui.fish
INSTALL_PWSH := $(APPDIR)/completions/perpetui.ps1
INSTALL_MAN := $(DATADIR)/man/man1

.PHONY: setup build build-go build-install lock run demo test lint format check \
	completions man release install install-python install-completions install-man \
	uninstall clean sync-version

setup:
	@if [ "$(PY_TOOL)" = "uv" ] || \
	   { [ "$(PY_TOOL)" = "auto" ] && command -v "$(UV)" >/dev/null 2>&1; }; then \
		echo "Using uv"; \
		UV_PROJECT_ENVIRONMENT="$(VENV)" "$(UV)" sync --locked --dev; \
	elif [ "$(PY_TOOL)" = "auto" ] || [ "$(PY_TOOL)" = "pip" ]; then \
		echo "Using venv + pip"; \
		rm -rf "$(VENV)"; \
		"$(PYTHON)" -m venv "$(VENV)"; \
		"$(VENV_PYTHON)" -m pip install --upgrade "pip>=25.1"; \
		"$(VENV_PYTHON)" -m pip install -r "$(LOCK_DEV)"; \
		"$(VENV_PYTHON)" -m pip install -e . --no-deps; \
	else \
		echo "Unknown PY_TOOL: $(PY_TOOL)" >&2; \
		exit 2; \
	fi

sync-version:
	"$(PYTHON)" scripts/sync_version.py

build-go: sync-version
	@mkdir -p "$(BUILD_DIR)"
	"$(GO)" build -trimpath -o "$(BIN)" ./cmd/perpetui

build-install: sync-version
	@mkdir -p "$(BUILD_DIR)"
	"$(GO)" build -trimpath -ldflags "$(INSTALL_LDFLAGS)" -o "$(INSTALL_BIN)" ./cmd/perpetui

build: setup build-go

lock:
	"$(UV)" export --locked --no-dev --no-emit-project --format requirements-txt -o "$(LOCK_RUNTIME)"
	"$(UV)" export --locked --dev --no-emit-project --format requirements-txt -o "$(LOCK_DEV)"

run: build
	PERPETUI_PYTHON="$(VENV_PYTHON)" "$(BIN)" $(ARGS)

demo: build
	PERPETUI_PYTHON="$(VENV_PYTHON)" "$(BIN)" --demo

test: setup
	"$(VENV_PYTHON)" -m pytest
	"$(GO)" test ./...

lint: setup
	"$(VENV_PYTHON)" -m ruff check .
	"$(VENV_PYTHON)" -m ruff format --check .
	"$(GO)" vet ./...
	@test -z "$$($(GOFMT) -l cmd internal)" || \
		{ echo "gofmt needed"; "$(GOFMT)" -l cmd internal; exit 1; }

format: setup
	"$(VENV_PYTHON)" -m ruff format .
	"$(GO)" fmt ./...

check: lint test

completions: build-go
	@mkdir -p "$(COMPLETION_DIR)"
	"$(BIN)" completion bash > "$(COMPLETION_DIR)/perpetui.bash"
	"$(BIN)" completion zsh > "$(COMPLETION_DIR)/_perpetui"
	"$(BIN)" completion fish > "$(COMPLETION_DIR)/perpetui.fish"
	"$(BIN)" completion powershell > "$(COMPLETION_DIR)/perpetui.ps1"

man: build-go
	"$(BIN)" docs man --dir "$(MAN_DIR)"

release: build completions man
	@echo "Built $(BIN), completions and man pages under $(BUILD_DIR)"

install-python:
	@if [ "$(PY_TOOL)" = "uv" ] || \
	   { [ "$(PY_TOOL)" = "auto" ] && command -v "$(UV)" >/dev/null 2>&1; }; then \
		echo "Installing Python dependencies with uv"; \
		UV_PROJECT_ENVIRONMENT="$(INSTALL_VENV)" "$(UV)" sync --locked --no-dev --no-editable; \
	elif [ "$(PY_TOOL)" = "auto" ] || [ "$(PY_TOOL)" = "pip" ]; then \
		echo "Installing Python dependencies with pip"; \
		rm -rf "$(INSTALL_VENV)"; \
		"$(PYTHON)" -m venv "$(INSTALL_VENV)"; \
		"$(INSTALL_PYTHON)" -m pip install --upgrade "pip>=25.1"; \
		"$(INSTALL_PYTHON)" -m pip install -r "$(LOCK_RUNTIME)"; \
		"$(INSTALL_PYTHON)" -m pip install . --no-deps; \
	else \
		echo "Unknown PY_TOOL: $(PY_TOOL)" >&2; \
		exit 2; \
	fi

install-completions: completions
	@mkdir -p "$(dir $(INSTALL_BASH))" "$(dir $(INSTALL_ZSH))" \
		"$(dir $(INSTALL_FISH))" "$(dir $(INSTALL_PWSH))"
	install -m 644 "$(COMPLETION_DIR)/perpetui.bash" "$(INSTALL_BASH)"
	install -m 644 "$(COMPLETION_DIR)/_perpetui" "$(INSTALL_ZSH)"
	install -m 644 "$(COMPLETION_DIR)/perpetui.fish" "$(INSTALL_FISH)"
	install -m 644 "$(COMPLETION_DIR)/perpetui.ps1" "$(INSTALL_PWSH)"

install-man: man
	@mkdir -p "$(INSTALL_MAN)"
	install -m 644 "$(MAN_DIR)"/*.1 "$(INSTALL_MAN)/"

install: build-install install-python install-completions install-man
	@mkdir -p "$(BINDIR)"
	install -m 755 "$(INSTALL_BIN)" "$(BINDIR)/perpetui"
	@echo "Installed: $(BINDIR)/perpetui"
	@echo "Python venv: $(INSTALL_VENV)"

uninstall:
	rm -f "$(BINDIR)/perpetui"
	rm -rf "$(APPDIR)"
	rm -f "$(INSTALL_BASH)" "$(INSTALL_ZSH)" "$(INSTALL_FISH)" "$(INSTALL_PWSH)"
	rm -f "$(INSTALL_MAN)"/perpetui*.1

clean:
	rm -rf "$(BUILD_DIR)"
