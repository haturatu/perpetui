"""Optional configuration and compatibility with the existing saved-account stores."""

from __future__ import annotations

import textwrap

import pytest

from perpetui.config import load_config


def test_missing_configuration_preserves_legacy_mode(tmp_path) -> None:
    assert load_config(tmp_path / "absent.toml") is None


def test_account_portfolio_configuration(tmp_path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        textwrap.dedent("""
        [portfolio]
        default_view = "portfolio:hedge"
        group_by = "symbol"

        [portfolio.fx_rates]
        USDT = 1.0
        USDC = 0.999

        [[accounts]]
        id = "hl-main"
        exchange = "hyperliquid"
        account = "main"

        [[accounts]]
        id = "mexc"
        exchange = "mexc"
        account = "trading"

        [[accounts]]
        id = "disabled"
        exchange = "mexc"
        enabled = false

        [[portfolios]]
        id = "hedge"
        accounts = ["hl-main", "mexc"]
    """)
    )
    config = load_config(path)
    assert config is not None
    assert [item.id for item in config.accounts] == ["hl-main", "mexc"]
    assert config.default_view == "portfolio:hedge"
    assert config.group_by == "symbol"
    assert config.fx_rates["USDC"] > 0
    assert config.profiles["hedge"] == ("hl-main", "mexc")


@pytest.mark.parametrize(
    "fragment",
    [
        '[[accounts]]\nid="a"\nexchange="mexc"\n[[accounts]]\nid="a"\nexchange="mexc"',
        '[[accounts]]\nid="a"\nexchange="unknown"',
        '[[accounts]]\nid="bad:id"\nexchange="mexc"',
        '[[accounts]]\nid="a"\nexchange="mexc"\nwallet_address="0x123"',
        '[[accounts]]\nid="a"\nexchange="mexc"\ntestnet=true',
        '[[accounts]]\nid="a"\nexchange="mexc"\nenabled=false\n'
        '[[portfolios]]\nid="p"\naccounts=["a"]',
    ],
)
def test_bad_accounts_fail_before_connecting(tmp_path, fragment) -> None:
    path = tmp_path / "bad.toml"
    path.write_text(fragment)
    with pytest.raises(ValueError):
        load_config(path)
