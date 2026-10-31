from __future__ import annotations

import argparse

from perpetui.domain.intervals import INTERVAL_SECONDS
from perpetui.errors import CredentialsError
from perpetui.exchanges.factory import create_client_factory, intervals_for
from perpetui.exchanges.lighter.account import validate_account_index
from perpetui.i18n import LANGUAGES, _, install_language
from perpetui.konsole import install_keymap
from perpetui.logging_config import configure_logging
from perpetui.ui.app import TradingApp

from . import __version__


class LocalizedHelpFormatter(argparse.HelpFormatter):
    def start_section(self, heading):
        super().start_section(_(heading))

    def add_usage(self, usage, actions, groups, prefix=None):
        super().add_usage(usage, actions, groups, prefix=prefix or _("usage: "))


def positive_float(value: str) -> float:
    import math

    result = float(value)
    if not math.isfinite(result) or result < 1:
        raise argparse.ArgumentTypeError(_("Must be a finite number of at least 1 second"))
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=_("Hyperliquid / Lighter / MEXC charts and open positions"),
        add_help=False,
        formatter_class=LocalizedHelpFormatter,
    )
    parser.add_argument("-h", "--help", action="help", help=_("Show this help message and exit"))
    parser.add_argument("--lang", choices=LANGUAGES, help=_("UI language (default: system locale)"))
    parser.add_argument(
        "--install-konsole-keymap",
        action="store_true",
        help=_("Install the optional Konsole Ctrl+number keymap"),
    )
    parser.add_argument(
        "--exchange",
        choices=("hyperliquid", "hl", "lighter", "lt", "mexc"),
        default="hyperliquid",
        help=_("Exchange (default: hyperliquid)"),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"perpetui {__version__}",
        help=_("Show the version number and exit"),
    )
    parser.add_argument(
        "--demo", action="store_true", help=_("Use sample data, without network access")
    )
    parser.add_argument(
        "--demo-empty", action="store_true", help=_("Demo with no positions (BTC chart)")
    )
    parser.add_argument(
        "--public", action="store_true", help=_("Market data only; skip account lookup")
    )
    parser.add_argument("--symbol", help=_("Initial symbol, e.g. BTC, ETH_USDT or xyz:CL"))
    parser.add_argument(
        "--interval", choices=INTERVAL_SECONDS, default="15m", help=_("Initial candle interval")
    )
    parser.add_argument(
        "--refresh",
        type=positive_float,
        default=5,
        metavar="SECONDS",
        help=_("REST fallback refresh interval (default: 5)"),
    )
    parser.add_argument(
        "--no-websocket", action="store_true", help=_("Use REST polling for charts and positions")
    )
    parser.add_argument(
        "--websocket-url",
        help=_("Override the selected exchange's WebSocket endpoint"),
    )
    parser.add_argument("--account", help=_("Saved account alias (default: saved default)"))
    parser.add_argument(
        "--mexc-command", help=_("Original mexc command path for account decryption")
    )
    parser.add_argument("--hl-command", help=_("Original hl command path for account decryption"))
    parser.add_argument(
        "--wallet-address", help=_("Hyperliquid monitoring address (no private key required)")
    )
    parser.add_argument(
        "--testnet",
        action="store_true",
        help=_("Use the selected exchange's testnet (Hyperliquid or Lighter)"),
    )
    parser.add_argument("--hyperliquid-base-url", help=_("Override the Hyperliquid HTTP endpoint"))
    parser.add_argument(
        "--lighter-account-index",
        type=validate_account_index,
        help=_("Lighter account index (overrides the saved account)"),
    )
    parser.add_argument(
        "--lighter-config", help=_("Lighter account database path (default: lighter CLI config)")
    )
    parser.add_argument("--lighter-base-url", help=_("Override the Lighter HTTP endpoint"))
    parser.add_argument(
        "--futures-base-url",
        help=_("Override the MEXC HTTP endpoint"),
    )
    parser.add_argument(
        "--timeout",
        type=positive_float,
        default=10,
        metavar="SECONDS",
        help=_("Network timeout in seconds (default: 10)"),
    )
    parser.add_argument(
        "--debug", action="store_true", help=_("Enable DEBUG / INFO logs on stdout")
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    bootstrap = argparse.ArgumentParser(add_help=False)
    bootstrap.add_argument("--lang", choices=LANGUAGES)
    install_language(bootstrap.parse_known_args(argv)[0].lang)
    parser = build_parser()
    args = parser.parse_args(argv)
    hyperliquid_options = args.wallet_address or args.hl_command or args.hyperliquid_base_url
    lighter_options = args.lighter_account_index is not None or args.lighter_config
    lighter_options = lighter_options or args.lighter_base_url
    mexc_options = args.mexc_command or args.futures_base_url
    if args.exchange == "mexc" and (args.testnet or hyperliquid_options or lighter_options):
        parser.error(_("Testnet and provider options require --exchange hyperliquid or lighter"))
    if args.exchange in {"hyperliquid", "hl"} and lighter_options:
        parser.error(_("Lighter options require --exchange lighter"))
    if args.exchange in {"hyperliquid", "hl"} and mexc_options:
        parser.error(_("MEXC options require --exchange mexc"))
    if args.exchange in {"lighter", "lt"} and hyperliquid_options:
        parser.error(_("Hyperliquid options require --exchange hyperliquid"))
    if args.exchange in {"lighter", "lt"} and mexc_options:
        parser.error(_("MEXC options require --exchange mexc"))
    exchange = {"hl": "hyperliquid", "lt": "lighter"}.get(args.exchange, args.exchange)
    supported = intervals_for(exchange)
    if args.interval not in supported:
        parser.error(
            _(
                "The {interval} interval is not available on {exchange}",
                interval=args.interval,
                exchange=exchange,
            )
        )
    logger = configure_logging(args.debug)
    try:
        if args.install_konsole_keymap:
            path = install_keymap()
            print(
                _(
                    "Installed {path}. Select perpetui (Ctrl+1-9) "
                    "in the Konsole profile's Keyboard settings.",
                    path=path,
                )
            )
            return 0
        client_factory = create_client_factory(args)
        client = client_factory(
            {"hl": "hyperliquid", "lt": "lighter"}.get(args.exchange, args.exchange)
        )
        symbol = client.normalize_symbol(args.symbol) if args.symbol else None
        app = TradingApp(
            client,
            symbol=symbol,
            interval=args.interval,
            refresh_seconds=args.refresh,
            client_factory=client_factory,
        )
        app.run()
        return app.return_code or 0
    except (CredentialsError, ValueError, OSError) as exc:
        logger.error("%s", _(str(exc)))
        return 1
    except KeyboardInterrupt:
        return 0
    return 0
