"""Independent account position streams and conservative cross-venue aggregation."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from perpetui.domain.models import Position
from perpetui.exchanges.base import DataSource
from perpetui.i18n import _
from perpetui.services.positions import PositionsService


@dataclass(frozen=True)
class AccountRef:
    id: str
    exchange: str
    network: str = "mainnet"
    label: str = ""


@dataclass(frozen=True)
class PortfolioPosition:
    account: AccountRef
    position: Position

    @property
    def id(self) -> str:
        # Exchange IDs are not globally unique, even within the same exchange.
        return f"{self.account.id}:{self.position.id}"

    def __getattr__(self, name: str) -> object:
        return getattr(self.position, name)


@dataclass(frozen=True)
class Exposure:
    instrument: str
    long_size: Decimal
    short_size: Decimal

    @property
    def net_size(self) -> Decimal:
        return self.long_size - self.short_size

    @property
    def gross_size(self) -> Decimal:
        return self.long_size + self.short_size


def instrument_key(position: Position) -> str | None:
    """Only normalize well-known linear contracts; never collapse HIP-3 or multipliers."""
    if position.contract_size is None or position.settlement.upper() not in {"USD", "USDC", "USDT"}:
        return None
    symbol = position.symbol.upper()
    if ":" in symbol:
        return None
    for suffix in ("_USDT", "_USDC", "_USD"):
        if symbol.endswith(suffix):
            symbol = symbol[: -len(suffix)]
            break
    return symbol or None


class PortfolioService:
    def __init__(
        self,
        sources: list[tuple[AccountRef, DataSource]],
        *,
        profiles: dict[str, tuple[str, ...]] | None = None,
        fx_rates: dict[str, Decimal] | None = None,
    ) -> None:
        self.accounts = {ref.id: ref for ref, _ in sources}
        if len(self.accounts) != len(sources):
            raise ValueError("Duplicate account ID")
        self.clients = {ref.id: client for ref, client in sources}
        self.services = {ref.id: PositionsService(client) for ref, client in sources}
        self.profiles = profiles or {}
        self.fx_rates = fx_rates or {}
        self.on_change: Callable[[], None] = lambda: None
        self.closed = False
        for service in self.services.values():
            service.on_change = self._changed

    def _changed(self) -> None:
        if not self.closed:
            self.on_change()

    @property
    def positions(self) -> list[PortfolioPosition]:
        return [
            PortfolioPosition(self.accounts[identifier], position)
            for identifier, service in self.services.items()
            for position in service.snapshot.items
        ]

    @property
    def ready(self) -> bool:
        return any(service.revision > 0 for service in self.services.values())

    @property
    def has_accounts(self) -> bool:
        return bool(self.services)

    @property
    def errors(self) -> dict[str, str]:
        result = {}
        for identifier, service in self.services.items():
            if service.error:
                result[identifier] = service.error
            elif service.connection == "reconnecting":
                result[identifier] = "reconnecting / stale"
            elif not service.revision:
                result[identifier] = "waiting for first snapshot"
        return result

    def options(self) -> list[tuple[str, str]]:
        entries = [(_("All accounts"), "all")]
        entries.extend(
            (_("Portfolio: {name}", name=name), f"portfolio:{name}")
            for name in self.profiles
        )
        entries.extend(
            (_("Exchange: {exchange}", exchange=exchange), f"exchange:{exchange}")
            for exchange in sorted({a.exchange for a in self.accounts.values()})
        )
        entries.extend(
            (f"{a.exchange} / {a.label or a.id}", f"account:{a.id}") for a in self.accounts.values()
        )
        return entries

    def view(self, scope: str = "all", group_by: str = "none") -> list[PortfolioPosition]:
        items = self.positions
        if scope.startswith("account:"):
            items = [p for p in items if p.account.id == scope[8:]]
        elif scope.startswith("exchange:"):
            items = [p for p in items if p.account.exchange == scope[9:]]
        elif scope.startswith("portfolio:"):
            included = set(self.profiles.get(scope[10:], ()))
            items = [p for p in items if p.account.id in included]
        if group_by == "exchange":
            return sorted(items, key=lambda p: (p.account.exchange, p.account.id, p.symbol))
        if group_by == "symbol":
            return sorted(
                items,
                key=lambda p: (
                    instrument_key(p.position) or p.symbol,
                    p.account.exchange,
                    p.account.id,
                ),
            )
        return items

    @staticmethod
    def totals(positions: list[PortfolioPosition]) -> dict[str, Decimal]:
        totals: dict[str, Decimal] = {}
        for item in positions:
            if (pnl := item.position.pnl) is not None:
                currency = item.position.settlement.upper()
                totals[currency] = totals.get(currency, Decimal(0)) + pnl
        return totals

    @staticmethod
    def exposures(positions: list[PortfolioPosition]) -> dict[str, Exposure]:
        # Never sum raw contract counts, ROE, leverage, liquidation or account margin.
        exposure: dict[str, Exposure] = {}
        for item in positions:
            p = item.position
            instrument = instrument_key(p)
            size = p.base_size
            if instrument is None or size is None:
                continue
            old = exposure.get(instrument, Exposure(instrument, Decimal(0), Decimal(0)))
            exposure[instrument] = Exposure(
                instrument,
                old.long_size + (size if p.side == "LONG" else Decimal(0)),
                old.short_size + (size if p.side == "SHORT" else Decimal(0)),
            )
        return exposure

    @staticmethod
    def margin_by_venue(
        positions: list[PortfolioPosition],
    ) -> dict[tuple[str, str], Decimal]:
        """Margin remains segregated by venue and denomination."""
        margins: dict[tuple[str, str], Decimal] = {}
        for item in positions:
            key = (item.account.exchange, item.position.settlement.upper())
            margins[key] = margins.get(key, Decimal(0)) + item.position.margin
        return margins

    def usd_pnl(self, positions: list[PortfolioPosition]) -> Decimal | None:
        # Explicit user-provided FX rates only; never assume stablecoins equal USD.
        if not positions or any(item.position.pnl is None for item in positions):
            return None
        totals = self.totals(positions)
        if not all(currency in self.fx_rates for currency in totals):
            return None
        return sum((value * self.fx_rates[c] for c, value in totals.items()), Decimal(0))

    async def refresh(self, *, restart_stream: bool = False) -> None:
        if self.closed:
            return
        active = [
            service
            for service in self.services.values()
            if not service.busy and (restart_stream or service.connection != "live")
        ]
        results = await asyncio.gather(
            *(service.load(restart_stream=restart_stream) for service in active),
            return_exceptions=True,
        )
        for service, result in zip(active, results, strict=True):
            if isinstance(result, Exception):
                service.error = f"Positions unavailable: {type(result).__name__}"
        self._changed()

    async def close(self) -> None:
        self.closed = True
        await asyncio.gather(
            *(service.close() for service in self.services.values()),
            return_exceptions=True,
        )
        await asyncio.gather(
            *(client.close() for client in self.clients.values()),
            return_exceptions=True,
        )
