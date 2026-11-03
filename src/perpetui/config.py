"""Optional multi-account configuration. Credentials remain in the existing CLI stores."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

from perpetui.exchanges.factory import create_client_factory

if TYPE_CHECKING:
    from perpetui.exchanges.base import DataSource


EXCHANGES = {"hyperliquid", "mexc", "lighter"}
IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True)
class AccountConfig:
    id: str
    exchange: str
    account: str | None = None
    wallet_address: str | None = None
    lighter_account_index: int | None = None
    testnet: bool = False
    label: str = ""


@dataclass(frozen=True)
class PortfolioConfig:
    accounts: tuple[AccountConfig, ...] = ()
    profiles: dict[str, tuple[str, ...]] = field(default_factory=dict)
    default_view: str = "all"
    group_by: str = "none"
    fx_rates: dict[str, Decimal] = field(default_factory=dict)


def load_config(path: Path) -> PortfolioConfig | None:
    """Missing config means legacy single-account mode, not an empty portfolio."""
    path = path.expanduser()
    if not path.exists():
        return None
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    if not isinstance(data, dict):
        raise ValueError("Invalid perpetui config")
    accounts = []
    seen = set()
    for item in data.get("accounts", []):
        if not isinstance(item, dict):
            raise ValueError("Each account must be a TOML table")
        identifier, exchange = item.get("id"), item.get("exchange")
        if not isinstance(identifier, str) or not IDENTIFIER.fullmatch(identifier):
            raise ValueError("Account id must contain 1-64 letters, digits, _ or -")
        if identifier in seen:
            raise ValueError(f"Duplicate account id: {identifier}")
        seen.add(identifier)
        if exchange not in EXCHANGES:
            raise ValueError(f"Unsupported exchange for {identifier}")
        if not isinstance(item.get("enabled", True), bool):
            raise ValueError(f"Invalid enabled value for {identifier}")
        if not item.get("enabled", True):
            continue
        account = item.get("account")
        address = item.get("wallet_address")
        index = item.get("lighter_account_index")
        label = item.get("label", identifier)
        network = item.get("testnet", False)
        if not isinstance(network, bool) or not isinstance(label, str):
            raise ValueError(f"Invalid account settings for {identifier}")
        if account is not None and not isinstance(account, str):
            raise ValueError(f"Invalid account alias for {identifier}")
        if address is not None and (exchange != "hyperliquid" or not isinstance(address, str)):
            raise ValueError(f"wallet_address requires Hyperliquid: {identifier}")
        if index is not None and (exchange != "lighter" or isinstance(index, bool) or
                                  not isinstance(index, int) or not 0 <= index < 2**63):
            raise ValueError(f"lighter_account_index requires Lighter: {identifier}")
        if exchange == "mexc" and network:
            raise ValueError(f"MEXC testnet is unsupported: {identifier}")
        accounts.append(AccountConfig(identifier, exchange, account, address, index, network, label))

    profiles: dict[str, tuple[str, ...]] = {}
    for profile in data.get("portfolios", []):
        if not isinstance(profile, dict):
            raise ValueError("Each portfolio must be a TOML table")
        name, ids = profile.get("id"), profile.get("accounts")
        if not isinstance(name, str) or not IDENTIFIER.fullmatch(name) or name in profiles:
            raise ValueError("Invalid or duplicate portfolio id")
        if not isinstance(ids, list) or not ids or any(i not in seen for i in ids):
            raise ValueError(f"Portfolio {name} references unknown accounts")
        profiles[name] = tuple(ids)

    settings = data.get("portfolio", {})
    if not isinstance(settings, dict):
        raise ValueError("[portfolio] must be a table")
    view = settings.get("default_view", "all")
    group = settings.get("group_by", "none")
    if group not in {"none", "exchange", "symbol"}:
        raise ValueError("group_by must be none, exchange or symbol")
    valid_views = {"all"}
    valid_views.update(f"account:{x.id}" for x in accounts)
    valid_views.update(f"exchange:{x.exchange}" for x in accounts)
    valid_views.update(f"portfolio:{p}" for p in profiles)
    if view not in valid_views:
        raise ValueError(f"Unknown default_view: {view}")
    rates = {}
    for currency, rate in settings.get("fx_rates", {}).items():
        value = Decimal(str(rate))
        if not value.is_finite() or value <= 0:
            raise ValueError(f"Invalid FX rate for {currency}")
        rates[str(currency).upper()] = value
    return PortfolioConfig(tuple(accounts), profiles, view, group, rates)


def create_account_client(args: object, account: AccountConfig) -> DataSource:
    """Use the existing read-only account loaders, never persist credentials here."""
    options = dict(vars(args))
    options.update(
        exchange=account.exchange,
        account=account.account,
        wallet_address=account.wallet_address,
        lighter_account_index=account.lighter_account_index,
        testnet=account.testnet,
    )
    return create_client_factory(SimpleNamespace(**options))(account.exchange)
