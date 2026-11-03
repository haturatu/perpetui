# Architecture

**English** | [日本語](architecture.ja.md)

perpetui separates fetching market data from rendering it in the terminal.

Putting everything inside the Textual application would duplicate REST requests and WebSocket subscriptions as more chart panes are added. Switching panes would also require reconnecting unnecessarily.

Instead, exchange-specific behavior lives in adapters, while chart state and market subscriptions are managed independently.

The startup path is:

```text
cmd/perpetui (Go CLI)
  → internal/launcher
  → perpetui.cli (Python CLI)
  → perpetui.ui.app (Textual)
```

## Module responsibilities

| Location | Responsibility |
| --- | --- |
| `domain/` | Decimal-based market and position models, intervals, CandleStore |
| `exchanges/base.py` | DataSource Protocol shared by exchanges |
| `exchanges/{mexc,hyperliquid,lighter}/` | REST, WebSocket, message parsing, saved accounts |
| `exchanges/websocket.py` | Reconnection, backoff, heartbeats, shutdown |
| `exchanges/account_crypto.py` | Compatibility with encryption formats of existing CLIs |
| `exchanges/factory.py` | Build exchange clients from configuration |
| `services/` | Shared market feeds, position updates, history retrieval, connection state |
| `ui/` | Textual screens, layouts, widgets, interactions |
| `internal/` | Go CLI and detection/launching of Python |

The domain, services, and exchange adapters do not depend on Textual, Rich, or Plotext. The service layer also avoids importing concrete exchange implementations directly.

This makes it possible to test market state and reconnection without starting a UI. Tests check dependency direction as well.

The old `perpetui.app.TradingApp` is a compatibility entry point. New code should use `perpetui.ui.app.TradingApp`.

## Separate charts from market subscriptions

Each chart consists of a `ChartSession` and a `ChartPanel`.

`ChartSession` manages data, subscriptions, and history. `ChartPanel` renders the state it receives. Sessions are held in `TradingApp.charts`, and `WorkspaceState.active_chart_id` identifies the currently controlled pane.

### Avoid fetching the same symbol repeatedly

If several panes show the same symbol and interval, making one API request per pane is unnecessary.

A `MarketFeedHub` is created for each exchange client. It shares concurrent REST requests and stream subscriptions for the same symbol and interval. The stream closes when its last subscriber leaves.

The receive buffer keeps the latest complete market snapshot, so a slow pane cannot cause an ever-growing queue.

If one pane cancels its fetch, the shared request is not canceled while other panes are waiting. Conversely, **history cursors, price-axis zoom, and rendering modes are independent for each pane**.

Different symbols are not multiplexed onto a single WebSocket connection. Sharing is limited to subscriptions for the same symbol and interval.

### Fetching historical data

`HistoryService` shares historical caches and in-progress backfill requests within a client. When one pane loads older candles, other panes can reuse them.

`ChartSession` increments a generation counter whenever the symbol or interval changes, preventing a delayed response from an earlier selection from overwriting the new chart.

`PositionsService` also compares revisions so a slower REST response cannot overwrite newer positions received over WebSocket.

## Switching exchanges

When switching exchanges, the application shuts down screen fetch workers, each `ChartSession`, position subscriptions, history fetches, and shared feeds before closing the old client.

The new exchange gets its own Hub and Services. This avoids carrying connections or account state over from the previous exchange.

## Rendering and layouts

`ui/app.tcss` defines styles. `ui/workspace.py` handles chart selection, pane counts, layouts, synchronization, and related interactions.

`ui/layout.py` contains layout calculations that do not depend on Textual widgets. Auto mode determines rows, columns, and spans based on pane count and terminal dimensions.

An important detail is that resizing, maximizing, and rearranging do not recreate widgets. Only the layout changes, preserving chart history and streams.

`ui/widgets/positions.py` updates position rows and adjusts the panel height to the number of positions. It handles collapsing when there are zero positions and scrolling when there are many.

## Testing and distribution

Tests live in `tests/domain`, `tests/exchanges`, `tests/services`, `tests/ui`, and `tests/integration`.

WebSocket behavior is verified against local test servers, without live trading accounts. Integration tests cover resizing, maximizing, changing symbols, switching exchanges, connection sharing, and shutdown.

Tests also verify consistency between the Python package version and the generated Go version, as well as CLI options, defaults, and English help text.

Because installation supports both uv and pip, pip lockfiles are maintained alongside `uv.lock`.

[Back to README](../README.md)
