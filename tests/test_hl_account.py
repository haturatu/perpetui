import base64
import hashlib
import sqlite3

import pytest
from Crypto.Cipher import ChaCha20

from perpetui.credentials import CredentialsError
from perpetui.hl_account import load_hl_account, validate_address

ADDRESS = "0x" + "a" * 40


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("HL_HOME", str(tmp_path))
    monkeypatch.delenv("HYPERLIQUID_WALLET_ADDRESS", raising=False)
    monkeypatch.delenv("HL_COMMAND", raising=False)


def test_missing_configuration_is_public_without_creating_files(tmp_path):
    assert load_hl_account() is None
    assert not list(tmp_path.iterdir())


def test_existing_hl_encryption_and_network_selection_are_read_only(tmp_path):
    command = str(tmp_path / "hl")
    nonce = b"123456789012"
    cipher = ChaCha20.new(key=hashlib.sha256(command.encode()).digest(), nonce=nonce)
    encrypted = (
        "enc_v1:"
        + base64.urlsafe_b64encode(nonce).decode()
        + ":"
        + base64.urlsafe_b64encode(cipher.encrypt(ADDRESS.encode())).decode()
    )
    database = tmp_path / "hl.db"
    with sqlite3.connect(database) as conn:
        conn.execute(
            "CREATE TABLE accounts (id INT, alias TEXT, network TEXT, user_address TEXT, "
            "is_default INT, api_wallet_private_key TEXT)"
        )
        conn.execute(
            "INSERT INTO accounts VALUES(1,'main','mainnet',?,1,'INVALID ENCRYPTED PRIVATE KEY')",
            (encrypted,),
        )
        conn.execute(
            "INSERT INTO accounts VALUES(2,'test','testnet',?,1,'DO NOT READ')", ("0x" + "b" * 40,)
        )
    before = database.read_bytes()
    account = load_hl_account(command=command)
    assert account.address == ADDRESS and account.label == "main"
    assert ADDRESS not in repr(account)
    assert load_hl_account(testnet=True).address == "0x" + "b" * 40
    with pytest.raises(CredentialsError, match="not found"):
        load_hl_account("main", command, testnet=True)
    with pytest.raises(CredentialsError):
        load_hl_account(command=str(tmp_path / "wrong"))
    assert database.read_bytes() == before


def test_explicit_address_works_without_reading_broken_database(tmp_path, monkeypatch):
    (tmp_path / "hl.db").write_bytes(b"not a database")
    monkeypatch.setenv("HYPERLIQUID_WALLET_ADDRESS", "wrong address")
    assert load_hl_account(address=ADDRESS).address == ADDRESS


@pytest.mark.parametrize("value", ["", "0x123", "x" * 42, "0x" + "g" * 40])
def test_invalid_addresses_are_rejected_without_echoing_input(value):
    with pytest.raises(CredentialsError):
        validate_address(value)
