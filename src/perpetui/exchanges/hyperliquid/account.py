"""Read only the monitoring address from hl's existing account database."""

from __future__ import annotations

import os
import re
import shutil
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

from perpetui.errors import CredentialsError
from perpetui.exchanges.account_crypto import _decrypt


@dataclass(frozen=True)
class HLAccount:
    address: str = field(repr=False)
    label: str = "wallet"


def validate_address(value: str) -> str:
    if not re.fullmatch(r"0x[0-9a-fA-F]{40}", value.strip()):
        raise CredentialsError("Use a Hyperliquid account address: 0x followed by 40 hex digits")
    return value.strip().lower()


def load_hl_account(
    alias: str | None = None,
    command: str | None = None,
    *,
    address: str | None = None,
    testnet: bool = False,
) -> HLAccount | None:
    if address:
        return HLAccount(validate_address(address))
    database = Path(os.getenv("HL_HOME", "~/.hl")).expanduser() / "hl.db"
    network = "testnet" if testnet else "mainnet"
    row = None
    if database.is_file():
        try:
            with closing(
                sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)
            ) as conn:
                columns = {item[1] for item in conn.execute("PRAGMA table_info(accounts)")}
                if "network" in columns or not testnet:
                    clause = "alias = ?" if alias else "is_default = 1"
                    values = [alias] if alias else []
                    if "network" in columns:
                        clause += " AND network = ?"
                        values.append(network)
                    # The clause is chosen from fixed strings; only values are interpolated.
                    row = conn.execute(
                        f"SELECT alias, user_address FROM accounts WHERE {clause} "  # nosec B608
                        "ORDER BY id LIMIT 1",
                        values,
                    ).fetchone()
        except sqlite3.Error as exc:
            raise CredentialsError("Cannot read hl account database") from exc
    if row is None:
        if alias:
            raise CredentialsError(f"Saved hl account not found on {network}")
        value = os.getenv("HYPERLIQUID_WALLET_ADDRESS")
        return HLAccount(validate_address(value), "environment") if value else None
    value = row[1]
    if value.startswith("enc_v1:"):
        executable = command or os.getenv("HL_COMMAND") or shutil.which("hl")
        if not executable:
            raise CredentialsError(
                "hl command not found; provide its original path with --hl-command"
            )
        resolved = str(Path(shutil.which(executable) or executable).expanduser().resolve())
        try:
            value = _decrypt(value, resolved)
        except CredentialsError as exc:
            raise CredentialsError("Cannot decrypt hl account; check --hl-command") from exc
    return HLAccount(validate_address(value), row[0])
