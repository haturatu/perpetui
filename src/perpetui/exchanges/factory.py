from __future__ import annotations

import os
from collections.abc import Callable
from typing import Protocol

from perpetui.exchanges.base import DataSource
from perpetui.exchanges.demo import DemoClient
from perpetui.exchanges.hyperliquid.account import load_hl_account
from perpetui.exchanges.hyperliquid.client import HyperliquidClient
from perpetui.exchanges.lighter.account import load_lighter_account
from perpetui.exchanges.lighter.client import LighterClient
from perpetui.exchanges.mexc.account import load_credentials
from perpetui.exchanges.mexc.client import MEXCClient
from perpetui.exchanges.mexc.stream import DEFAULT_WS_URL


class ClientOptions(Protocol):
    """Read-only settings accepted from a CLI or another application entry point."""

    exchange: str
    account: str | None
    websocket_url: str | None
    demo: bool
    demo_empty: bool
    public: bool
    hl_command: str | None
    wallet_address: str | None
    testnet: bool
    timeout: float
    hyperliquid_base_url: str | None
    no_websocket: bool
    lighter_account_index: int | None
    lighter_config: str | None
    lighter_base_url: str | None
    mexc_command: str | None
    futures_base_url: str | None


def intervals_for(exchange: str) -> tuple[str, ...]:
    return {"mexc": MEXCClient, "hyperliquid": HyperliquidClient, "lighter": LighterClient}[
        exchange
    ].intervals


def create_client_factory(args: ClientOptions) -> Callable[[str], DataSource]:
    initial_exchange = {"hl": "hyperliquid", "lt": "lighter"}.get(args.exchange, args.exchange)

    def create(exchange: str) -> DataSource:
        alias = args.account if exchange == initial_exchange else None
        websocket_url = args.websocket_url if exchange == initial_exchange else None
        if args.demo or args.demo_empty:
            return DemoClient(empty=args.demo_empty, exchange=exchange)
        if exchange == "hyperliquid":
            account = (
                None
                if args.public
                else load_hl_account(
                    alias,
                    args.hl_command,
                    address=args.wallet_address,
                    testnet=args.testnet,
                )
            )
            return HyperliquidClient(
                account,
                testnet=args.testnet,
                timeout=args.timeout,
                base_url=args.hyperliquid_base_url or os.getenv("HYPERLIQUID_BASE_URL"),
                websocket_url=websocket_url or os.getenv("HYPERLIQUID_WS_URL"),
                streaming=not args.no_websocket,
            )
        if exchange == "lighter":
            account = (
                None
                if args.public
                else load_lighter_account(
                    alias,
                    account_index=args.lighter_account_index,
                    testnet=args.testnet,
                    config=args.lighter_config,
                )
            )
            return LighterClient(
                account,
                testnet=args.testnet,
                timeout=args.timeout,
                base_url=args.lighter_base_url or os.getenv("LIGHTER_BASE_URL"),
                websocket_url=websocket_url or os.getenv("LIGHTER_WS_URL"),
                streaming=not args.no_websocket,
            )
        if exchange != "mexc":
            raise ValueError("Unknown exchange")
        credentials = None if args.public else load_credentials(alias, args.mexc_command)
        return MEXCClient(
            credentials,
            args.futures_base_url
            or os.getenv("MEXC_FUTURES_BASE_URL")
            or os.getenv("MEXC_BASE_URL")
            or "https://api.mexc.com",
            args.timeout,
            websocket_url=websocket_url or os.getenv("MEXC_WS_URL") or DEFAULT_WS_URL,
            streaming=not args.no_websocket,
        )

    return create
