"""Read mexc-cli accounts without migrating or writing their database."""

from __future__ import annotations

import base64
import hashlib
import os
import shutil
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

# PyCryptodome, not the deprecated PyCrypto package.
from Crypto.Cipher import ChaCha20  # nosec B413


class CredentialsError(ValueError):
    pass


@dataclass(frozen=True)
class Credentials:
    api_key: str = field(repr=False)
    api_secret: str = field(repr=False)
    label: str = "environment"


def _decrypt(value: str, command_path: str) -> str:
    if not value.startswith("enc_v1:"):
        return value
    try:
        nonce, payload = value[len("enc_v1:") :].split(":", 1)
        cipher = ChaCha20.new(
            key=hashlib.sha256(command_path.encode()).digest(),
            nonce=base64.urlsafe_b64decode(nonce),
        )
        result = cipher.decrypt(base64.urlsafe_b64decode(payload)).decode()
        if not result or not result.isprintable():
            raise ValueError("Invalid decrypted value")
        return result
    except (ValueError, UnicodeError) as exc:
        raise CredentialsError(
            "Cannot decrypt mexc-cli account. Use --mexc-command with the original installed "
            "mexc command path, or set MEXC_API_KEY and MEXC_API_SECRET."
        ) from exc


def load_credentials(
    alias: str | None = None, mexc_command: str | None = None
) -> Credentials | None:
    key, secret = os.getenv("MEXC_API_KEY", ""), os.getenv("MEXC_API_SECRET", "")
    # An explicit alias always selects that saved account; environment credentials
    # otherwise make a moved/reinstalled mexc command easy to recover from.
    if alias is None and (key or secret):
        if not (key and secret):
            raise CredentialsError("Set both MEXC_API_KEY and MEXC_API_SECRET")
        return Credentials(key, secret)
    directory = Path(os.getenv("MEXC_HOME", "~/.mexc")).expanduser()
    database = directory / "mexc.db"
    if not database.is_file():
        if alias:
            raise CredentialsError(f"Saved account {alias!r} not found; mexc-cli DB is missing")
        return None
    try:
        with closing(
            sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)
        ) as connection:
            if alias:
                row = connection.execute(
                    "SELECT alias, api_key, api_secret FROM accounts WHERE alias = ?", (alias,)
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT alias, api_key, api_secret FROM accounts "
                    "WHERE is_default = 1 ORDER BY id LIMIT 1"
                ).fetchone()
    except sqlite3.Error as exc:
        raise CredentialsError("Cannot read mexc-cli account database") from exc
    if row is None:
        if alias:
            raise CredentialsError(f"Saved account {alias!r} not found")
        return None
    command = mexc_command or os.getenv("MEXC_COMMAND") or shutil.which("mexc")
    if not command and any(value.startswith("enc_v1:") for value in row[1:]):
        raise CredentialsError(
            "mexc command not found; provide its original path with --mexc-command"
        )
    resolved = str(Path(shutil.which(command) or command).expanduser().resolve()) if command else ""
    return Credentials(_decrypt(row[1], resolved), _decrypt(row[2], resolved), row[0])
