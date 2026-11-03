# Installation and startup

**English** | [日本語](installation.ja.md)

perpetui uses Python for the TUI and Go for the launcher.

This may look like an unusual combination, but you normally only need the `perpetui` command. The Go launcher locates the Python environment and starts the Textual application.

## Requirements

| Component | Purpose |
| --- | --- |
| Python 3.11 or later | TUI application |
| Go 1.23 or later | Build the Go launcher; not required to run an installed binary |
| make | Setup and installation |
| ANSI/UTF-8-capable terminal | Display charts and the UI |
| uv | Resolve Python dependencies; venv + pip is used if uv is unavailable |
| pip 25.1 or later | Required if using pip |
| git | Fetch the source code |

A terminal of at least 120 columns × 40 rows is recommended. Smaller terminals work, but there may not be enough space for multiple charts.

Exchange accounts are optional. You can view public market data without authentication.

## Install for regular use

The simplest option is `make install`:

```sh
git clone https://github.com/haturatu/perpetui.git
cd perpetui
make install
```

By default, files are installed in these locations:

| Location | Contents |
| --- | --- |
| `~/.local/bin/perpetui` | Go launcher |
| `~/.local/share/perpetui/venv` | Dedicated Python environment |
| `~/.local/share/bash-completion/completions/perpetui`, etc. | Shell completions |
| `~/.local/share/man/man1/` | Man page |

Add `~/.local/bin` to your `PATH` if necessary:

```sh
export PATH="$HOME/.local/bin:$PATH"
perpetui --demo
```

Set `PREFIX`, `BINDIR`, or `DATADIR` to change the installation locations. Use `make uninstall` to uninstall.

```sh
make install PREFIX="$HOME/.local"
make uninstall
```

After installation, neither uv nor the Go toolchain is required at runtime. However, **the dedicated Python environment created during installation is still required**. If you move only the binary, reinstall with `make install`.

## Run from a development checkout

During development, it is convenient to use a local `.venv`:

```sh
make setup
make run
```

When uv is available, `make setup` runs `uv sync --locked --dev`. Otherwise it sets up venv with pip.

With uv, you can start the application directly:

```sh
uv sync --dev
uv run perpetui --demo
uv run perpetui
```

The pip path is supported too:

```sh
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/perpetui --demo
```

To select the dependency manager explicitly, use `make setup PY_TOOL=uv` or `make setup PY_TOOL=pip`. The pip path also uses the pinned versions in `requirements.lock` and `requirements-dev.lock`.

### Makefile targets

```sh
make setup         # Prepare the Python environment
make build         # Build the Go launcher to build/perpetui
make run           # Build and start the application
make demo          # Start with sample data
make test          # Run Python / Go tests
make lint          # Run Ruff / go vet / gofmt checks
make completions   # Generate Bash / Zsh / Fish / PowerShell completions
make man           # Generate the man page
make lock          # Update pip lockfiles
```

`make run` calls the build target each time. If you only want to run an installed copy, invoking `perpetui` directly is simpler.

## Use it without an account

```sh
perpetui --demo-empty
perpetui --demo=portfolio
perpetui --public
perpetui --symbol SOL --interval 1h --refresh 10
perpetui --account main
perpetui --no-websocket
```

`--demo-empty` shows sample charts without positions; `--demo=portfolio` shows a combined multi-exchange sample portfolio. `--public` uses only public market data and ignores account credentials. `--no-websocket` switches to periodic REST polling.

If no account is configured, the positions panel does not take up space, leaving more room for charts. The list also collapses when the position count is zero.

You can monitor Hyperliquid by wallet address and Lighter by account index without a private key. MEXC position monitoring requires API authentication. See [exchange configuration](exchanges.md) for details.

## How the launcher finds Python

The Go launcher selects a Python environment according to the installation type:

- `make install`: use the dedicated venv embedded into the installed launcher's configuration.
- Development build from `make build`: use `.venv` in the repository or working directory.
- If `PERPETUI_PYTHON` is set: use that Python interpreter in either case.

On Unix, the launcher ultimately replaces itself with Python via `exec`. Python handles all TUI rendering and exchange communication.

`perpetui --help`, `perpetui --version`, `perpetui completion ...`, and `perpetui docs man` are handled entirely by Go without launching Python.

[Back to README](../README.md)
