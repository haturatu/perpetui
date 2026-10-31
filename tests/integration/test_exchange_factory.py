import pytest

from perpetui.cli import main
from perpetui.exchanges.hyperliquid.account import HLAccount
from perpetui.exchanges.lighter.account import LighterAccount
from perpetui.exchanges.mexc.account import Credentials
from perpetui.i18n import install_language


@pytest.mark.parametrize("public", [False, True])
async def test_factory_keeps_account_aliases_and_endpoints_scoped_to_their_exchange(
    monkeypatch, public
):
    install_language("en")
    captured, aliases = {}, []

    class FakeApp:
        return_code = 0

        def __init__(self, client, **kwargs):
            captured.update(client=client, **kwargs)

        def run(self):
            pass

    def mexc_credentials(alias, command):
        assert not public
        aliases.append(("mexc", alias))
        return Credentials("key", "secret")

    def hl_account(alias, command, **kwargs):
        assert not public
        aliases.append(("hl", alias))
        return HLAccount("0x" + "1" * 40)

    monkeypatch.setattr("perpetui.cli.TradingApp", FakeApp)
    monkeypatch.setattr("perpetui.exchanges.factory.load_credentials", mexc_credentials)
    monkeypatch.setattr("perpetui.exchanges.factory.load_hl_account", hl_account)
    monkeypatch.setenv("MEXC_WS_URL", "wss://mexc.example/ws")
    monkeypatch.setenv("HYPERLIQUID_WS_URL", "wss://hl.example/ws")
    args = [
        "--exchange",
        "mexc",
        "--account",
        "mexc-only",
        "--websocket-url",
        "wss://custom-mexc.example/ws",
        "--no-websocket",
        "--lang",
        "en",
    ]
    if public:
        args.append("--public")
    assert main(args) == 0
    initial = captured["client"]
    factory = captured["client_factory"]
    hl, mexc = factory("hyperliquid"), factory("mexc")
    try:
        assert hl.websocket_url == "wss://hl.example/ws"
        assert mexc.websocket_url == initial.websocket_url == "wss://custom-mexc.example/ws"
        assert not hl.streaming and not mexc.streaming
        assert aliases == (
            [] if public else [("mexc", "mexc-only"), ("hl", None), ("mexc", "mexc-only")]
        )
    finally:
        for client in (initial, hl, mexc):
            await client.close()


async def test_lighter_factory_reads_only_lighter_accounts_and_its_own_endpoint(
    monkeypatch,
):
    install_language("en")
    captured, aliases = {}, []

    class FakeApp:
        return_code = 0

        def __init__(self, client, **kwargs):
            captured.update(client=client, **kwargs)

        def run(self):
            pass

    def lighter_account(alias, **kwargs):
        aliases.append((alias, kwargs.get("testnet")))
        return LighterAccount(42, "main")

    monkeypatch.setattr("perpetui.cli.TradingApp", FakeApp)
    monkeypatch.setattr("perpetui.exchanges.factory.load_lighter_account", lighter_account)
    monkeypatch.setattr("perpetui.exchanges.factory.load_hl_account", lambda *args, **kwargs: None)
    monkeypatch.setenv("LIGHTER_WS_URL", "wss://lighter.example/stream")
    assert main(["--exchange", "lighter", "--account", "main", "--lang", "en"]) == 0
    client = captured["client"]
    factory = captured["client_factory"]
    hyperliquid = factory("hyperliquid")
    try:
        assert client.exchange == "Lighter" and client.authenticated
        assert client.websocket_url == "wss://lighter.example/stream"
        assert aliases == [("main", False)]
        assert not hyperliquid.authenticated
    finally:
        await client.close()
        await hyperliquid.close()


def test_default_exchange_is_hyperliquid(monkeypatch):
    captured = {}

    class FakeApp:
        return_code = 0

        def __init__(self, client, **kwargs):
            captured.update(client=client, **kwargs)

        def run(self):
            pass

    monkeypatch.setattr("perpetui.cli.TradingApp", FakeApp)
    assert main(["--demo", "--lang", "en"]) == 0
    assert captured["client"].exchange == "Hyperliquid"


def test_mexc_options_require_the_mexc_exchange(monkeypatch):
    monkeypatch.setattr(
        "perpetui.cli.TradingApp", lambda *args, **kwargs: pytest.fail("App started")
    )
    with pytest.raises(SystemExit) as status:
        main(["--mexc-command", "/original/mexc", "--lang", "en"])
    assert status.value.code == 2


def test_demo_factory_never_reads_either_account_store(monkeypatch):
    captured = {}

    class FakeApp:
        return_code = 0

        def __init__(self, client, **kwargs):
            captured.update(client=client, **kwargs)

        def run(self):
            pass

    def forbidden(*args, **kwargs):
        pytest.fail("Demo accessed credentials")

    monkeypatch.setattr("perpetui.cli.TradingApp", FakeApp)
    monkeypatch.setattr("perpetui.exchanges.factory.load_credentials", forbidden)
    monkeypatch.setattr("perpetui.exchanges.factory.load_hl_account", forbidden)
    monkeypatch.setattr("perpetui.exchanges.factory.load_lighter_account", forbidden)
    assert main(["--demo", "--exchange", "hl", "--lang", "en"]) == 0
    assert captured["client_factory"]("mexc").exchange == "MEXC"
    assert captured["client_factory"]("hyperliquid").exchange == "Hyperliquid"
    assert captured["client_factory"]("lighter").exchange == "Lighter"


@pytest.mark.parametrize("exchange", ["mexc", "hyperliquid", "lighter"])
async def test_no_saved_credentials_automatically_starts_public_mode(
    exchange, monkeypatch, tmp_path
):
    captured = {}

    class FakeApp:
        return_code = 0

        def __init__(self, client, **kwargs):
            captured.update(client=client, **kwargs)

        def run(self):
            pass

    monkeypatch.setattr("perpetui.cli.TradingApp", FakeApp)
    monkeypatch.setenv("MEXC_HOME", str(tmp_path / "mexc"))
    monkeypatch.setenv("HL_HOME", str(tmp_path / "hl"))
    monkeypatch.setenv("LIGHTER_CONFIG", str(tmp_path / "lighter" / "accounts.db"))
    for key in (
        "MEXC_API_KEY",
        "MEXC_API_SECRET",
        "HYPERLIQUID_WALLET_ADDRESS",
        "LIGHTER_ACCOUNT_INDEX",
    ):
        monkeypatch.delenv(key, raising=False)
    assert main(["--exchange", exchange, "--lang", "en"]) == 0
    client = captured["client"]
    other = captured["client_factory"]("hyperliquid" if exchange == "mexc" else "mexc")
    try:
        assert not client.authenticated and not other.authenticated
        assert not client.position_streaming and not other.position_streaming
        assert list(tmp_path.iterdir()) == []
    finally:
        await client.close()
        await other.close()
