import base64
import hashlib
import sqlite3

import pytest
from Crypto.Cipher import ChaCha20

from perpetui.errors import CredentialsError
from perpetui.exchanges.mexc.account import load_credentials


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("MEXC_HOME", str(tmp_path))
    for key in ("MEXC_API_KEY", "MEXC_API_SECRET", "MEXC_COMMAND"):
        monkeypatch.delenv(key, raising=False)


def test_no_credentials_does_not_create_db(tmp_path):
    assert load_credentials() is None
    assert not (tmp_path / "mexc.db").exists()


def test_environment_credentials_are_not_printed(monkeypatch):
    monkeypatch.setenv("MEXC_API_KEY", "private-api-key")
    monkeypatch.setenv("MEXC_API_SECRET", "private-secret")
    credentials = load_credentials()
    assert credentials.api_key == "private-api-key"
    assert "private" not in repr(credentials)


def test_incomplete_env_fails_instead_of_silently_using_public_mode(monkeypatch):
    monkeypatch.setenv("MEXC_API_KEY", "key")
    with pytest.raises(CredentialsError, match="both"):
        load_credentials()


def test_existing_cli_encryption_and_db_are_compatible(tmp_path, monkeypatch):
    command = str(tmp_path / "bin" / "mexc")

    def encrypt(value):
        nonce = b"123456789012"
        cipher = ChaCha20.new(key=hashlib.sha256(command.encode()).digest(), nonce=nonce)
        return (
            "enc_v1:"
            + base64.urlsafe_b64encode(nonce).decode()
            + ":"
            + (base64.urlsafe_b64encode(cipher.encrypt(value.encode())).decode())
        )

    db = tmp_path / "mexc.db"
    with sqlite3.connect(db) as connection:
        connection.execute(
            "CREATE TABLE accounts "
            "(id INTEGER, alias TEXT, api_key TEXT, api_secret TEXT, is_default INT)"
        )
        connection.execute(
            "INSERT INTO accounts VALUES (1, 'main', ?, ?, 1)",
            (encrypt("stored-key"), encrypt("stored-secret")),
        )
    before = db.read_bytes()
    credentials = load_credentials(mexc_command=command)
    assert credentials.api_key == "stored-key"
    assert credentials.api_secret == "stored-secret"
    assert credentials.label == "main"
    assert db.read_bytes() == before
    monkeypatch.setenv("MEXC_API_KEY", "environment-key")
    monkeypatch.setenv("MEXC_API_SECRET", "environment-secret")
    assert load_credentials().api_key == "environment-key"
    assert load_credentials("main", command).api_key == "stored-key"
    with pytest.raises(CredentialsError, match="decrypt"):
        load_credentials("main", str(tmp_path / "wrong-mexc"))
    with pytest.raises(CredentialsError, match="not found"):
        load_credentials("missing", command)
    assert db.read_bytes() == before
