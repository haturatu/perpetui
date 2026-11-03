"""Cross-account identity, filtering, safety and stale-data behaviour."""

from __future__ import annotations

from decimal import Decimal

import pytest

from perpetui.domain.models import Position, Positions
from perpetui.errors import ExchangeError
from perpetui.services.portfolio import AccountRef, PortfolioService, instrument_key


def position(
    symbol: str,
    side: str,
    size: str,
    *,
    settlement: str = "USDT",
    contract_size: str | None = "1",
) -> Position:
    return Position(
        id="same-id",
        symbol=symbol,
        side=side,
        contracts=Decimal(size),
        entry=Decimal(100),
        liquidation=Decimal(0),
        leverage=10,
        margin=Decimal(10),
        margin_mode="Cross",
        mark=Decimal(110),
        contract_size=Decimal(contract_size) if contract_size is not None else None,
        settlement=settlement,
    )


class FakeSource:
    authenticated = True
    position_streaming = False

    def __init__(self, positions: list[Position]) -> None:
        self.snapshot = Positions(positions)
        self.fail = False
        self.closed = False

    async def positions(self) -> Positions:
        if self.fail:
            raise ExchangeError("disconnected")
        return self.snapshot

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_same_position_id_across_accounts_and_partial_failure() -> None:
    hl = FakeSource([position("BTC", "LONG", "0.5", settlement="USDC")])
    mexc = FakeSource([position("BTC_USDT", "SHORT", "3", contract_size="0.1")])
    service = PortfolioService(
        [
            (AccountRef("hl", "hyperliquid", label="main"), hl),
            (AccountRef("mexc", "mexc", label="trading"), mexc),
        ],
        profiles={"hedge": ("hl", "mexc")},
        fx_rates={
            "USDC": Decimal("1"),
            "USDT": Decimal("1"),
        },
    )
    try:
        await service.refresh()
        assert len(service.positions) == 2
        assert len({item.id for item in service.positions}) == 2
        assert len(service.view("account:hl")) == 1
        assert len(service.view("exchange:mexc")) == 1
        assert len(service.view("portfolio:hedge", "symbol")) == 2
        exposure = service.exposures(service.positions)["BTC"]
        assert exposure.long_size == Decimal("0.5")
        assert exposure.short_size == Decimal("0.3")
        assert exposure.net_size == Decimal("0.2")
        assert exposure.gross_size == Decimal("0.8")
        assert service.totals(service.positions) == {
            "USDC": Decimal("5.0"),
            "USDT": Decimal("-3.0"),
        }
        assert service.usd_pnl(service.positions) == Decimal("2.0")
        assert service.margin_by_venue(service.positions) == {
            ("hyperliquid", "USDC"): Decimal(10),
            ("mexc", "USDT"): Decimal(10),
        }

        mexc.fail = True
        await service.refresh(restart_stream=True)
        assert len(service.positions) == 2  # failed source remains visible
        assert "mexc" in service.errors
        assert "hl" not in service.errors
    finally:
        await service.close()
    assert hl.closed and mexc.closed


def test_unknown_contract_spec_and_fx_omit_cross_currency_total() -> None:
    unknown = position("1000PEPE", "LONG", "2", contract_size=None)
    inverse = position("BTC_USD", "SHORT", "1", settlement="BTC")
    assert instrument_key(unknown) is None
    assert instrument_key(inverse) is None
    assert instrument_key(position("xyz:CL", "LONG", "2")) is None
    service = PortfolioService([])
    assert service.usd_pnl([]) is None
    rows = [
        # This also ensures that merely matching ticker text is insufficient.
        type("Row", (), {"position": position("BTC", "LONG", "1", settlement="USDC")})(),
    ]
    assert service.usd_pnl(rows) is None


@pytest.mark.asyncio
async def test_no_position_snapshot_is_not_an_error() -> None:
    source = FakeSource([])
    portfolio = PortfolioService([(AccountRef("empty", "mexc"), source)])
    try:
        await portfolio.refresh()
        assert portfolio.ready
        assert portfolio.errors == {}
        assert portfolio.view() == []
    finally:
        await portfolio.close()
