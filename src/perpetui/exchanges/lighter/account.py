"""Read the monitoring account index from lighter-cli's existing account database."""

from __future__ import annotations

import os
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from perpetui.errors import CredentialsError

DEFAULT_CONFIG = Path("~/.config/ligher-xyz-cli/accounts.db")


@dataclass(frozen=True)
class LighterAccount:
    account_index: int
    label: str = "account"


def validate_account_index(value: str | int) -> int:
    text = str(value).strip()
    if not re.fullmatch(r"\d{1,18}", text):
        raise CredentialsError("Use a Lighter account index (a non-negative integer)")
    index = int(text)
    if index >= 2**63:
        raise CredentialsError("Use a Lighter account index (a non-negative integer)")
    return index


def load_lighter_account(
    alias: str | None = None,
    *,
    account_index: str | int | None = None,
    testnet: bool = False,
    config: str | None = None,
) -> LighterAccount | None:
    if account_index is not None:
        return LighterAccount(validate_account_index(account_index), "environment")
    environment = os.getenv("LIGHTER_ACCOUNT_INDEX")
    if alias is None and environment:
        return LighterAccount(validate_account_index(environment), "environment")
    database = Path(config or os.getenv("LIGHTER_CONFIG") or DEFAULT_CONFIG).expanduser()
    network = "testnet" if testnet else "mainnet"
    row = None
    if database.is_file():
        try:
            with closing(
                sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)
            ) as connection:
                columns = {item[1] for item in connection.execute("PRAGMA table_info(accounts)")}
                if "network" in columns or not testnet:
                    clause = "alias = ?" if alias else "is_default = 1"
                    values: list[object] = [alias] if alias else []
                    if "network" in columns:
                        clause += " AND network = ?"
                        values.append(network)
                    # The clause is chosen from fixed strings; only values are interpolated.
                    row = connection.execute(
                        f"SELECT alias, account_index FROM accounts WHERE {clause} "  # nosec B608
                        "ORDER BY alias LIMIT 1",
                        values,
                    ).fetchone()
        except sqlite3.Error as exc:
            raise CredentialsError("Cannot read Lighter account database") from exc
    if row is None:
        if alias:
            raise CredentialsError(f"Saved Lighter account not found on {network}")
        return None
    return LighterAccount(validate_account_index(row[1]), row[0])
