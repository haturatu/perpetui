import sqlite3

import pytest

from perpetui.credentials import CredentialsError
from perpetui.lighter_account import load_lighter_account, validate_account_index


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("LIGHTER_CONFIG", str(tmp_path / "accounts.db"))
    monkeypatch.delenv("LIGHTER_ACCOUNT_INDEX", raising=False)


def test_missing_configuration_is_public_without_creating_files(tmp_path):
    assert load_lighter_account() is None
    assert not (tmp_path / "accounts.db").exists()


def test_network_scoped_default_and_alias_are_read_only(tmp_path):
    database = tmp_path / "accounts.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE accounts (network TEXT, alias TEXT, account_index INT, "
            "api_key_index INT, api_private_key TEXT, auth_token TEXT, is_default INT)"
        )
        connection.execute(
            "INSERT INTO accounts VALUES('mainnet','main',123,2,'DO NOT READ','DO NOT READ',1)"
        )
        connection.execute(
            "INSERT INTO accounts VALUES('testnet','test',456,2,'DO NOT READ','DO NOT READ',1)"
        )
    before = database.read_bytes()
    account = load_lighter_account()
    assert account.account_index == 123 and account.label == "main"
    assert load_lighter_account(testnet=True).account_index == 456
    assert load_lighter_account("test", testnet=True).account_index == 456
    with pytest.raises(CredentialsError, match="not found"):
        load_lighter_account("main", testnet=True)
    assert database.read_bytes() == before


def test_explicit_index_and_environment_skip_the_database(tmp_path, monkeypatch):
    (tmp_path / "accounts.db").write_bytes(b"not a database")
    monkeypatch.setenv("LIGHTER_ACCOUNT_INDEX", "789")
    assert load_lighter_account().account_index == 789
    assert load_lighter_account(account_index="42").account_index == 42


@pytest.mark.parametrize("value", ["", "-1", "1.5", "abc", "9" * 19])
def test_invalid_account_indexes_are_rejected(value):
    with pytest.raises(CredentialsError):
        validate_account_index(value)
