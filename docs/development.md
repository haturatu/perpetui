# Development

**English** | [日本語](development.ja.md)

perpetui uses both Python and Go. Chart rendering and exchange communication run in Python, while Go provides the command-line entry point and launches Python.

The separation lets Go handle startup, shell completions, and man-page generation, while Textual powers the TUI.

## Tests and formatting

Run the following from the repository root:

```sh
make setup
make check
```

`make check` runs Python's Ruff checks and tests, and Go's `go vet`, `gofmt` checks, and tests. You can also run individual tasks:

```sh
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv build
```

Other targets include `make test`, `make lint`, `make build`, and `make demo`. See [installation](installation.md).

### Logging

DEBUG and INFO messages go to standard output; WARN and ERROR messages go to standard error. Startup errors are reported on standard error and the process exits with status 1.

API keys, signatures, and response bodies are intentionally omitted from logs. Do not include credentials when opening an issue.

## Chart rendering

Charts use [Plotext 6](https://github.com/piccolomo/plotext). Its ANSI rendering output is converted to Rich Text and displayed inside Textual widgets.

Candle bodies are rectangles and wicks are line segments. FINE mode uses Braille markers, giving each character a 2 × 4 subcell grid for bodies, wicks, and volume.

The application selects recent candles based on the available space and rerenders when data, entry prices, or terminal dimensions change. How individual dots look depends on the terminal font.

The MEXC adapter uses the [MEXC Futures API](https://www.mexc.com/api-docs/futures/integration-guide). Following the [API domain update notice](https://www.mexc.com/announcements/article/futures-api-access-domain-update-17827791532974), the default REST endpoint is `https://api.mexc.com`. You can override it with `MEXC_FUTURES_BASE_URL`, `MEXC_BASE_URL`, or `--futures-base-url`.

The application is read-only: there is no implementation for placing orders, closing positions, or changing accounts.

## Go and Python versions

The Python version is obtained from installed package metadata. The Go version string is generated from `pyproject.toml` and embedded in the binary.

`make build-go` and `make build-install` synchronize versions automatically. If you call `go build` directly, run the synchronization yourself:

```sh
python3 scripts/sync_version.py
go build ./cmd/perpetui
```

Tests check that Go and Python command-line options match in name, default, choices, and English help text.

Because both uv and pip are supported, the repository maintains `uv.lock`, `requirements.lock`, and `requirements-dev.lock`. Use `make lock` to update the pip lockfiles.

## Internal structure

For module responsibilities, dependency direction, shared WebSocket handling, and multi-chart state management, see [architecture](architecture.md).

[Back to README](../README.md)
