"""Small runtime catalogs, selected before constructing CLI and UI widgets."""

from __future__ import annotations

import json
import os
from importlib.resources import files

LANGUAGES = ("en", "ja")
_language = "en"
_catalog: dict[str, str] = {}


def current_language() -> str:
    return _language


def install_language(language: str | None = None) -> str:
    global _language, _catalog
    if language is None:
        for variable in ("PERPETUI_LANG", "LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
            if value := os.getenv(variable):
                language = value.split(":")[0].split(".")[0].split("_")[0].lower()
                break
    _language = language if language in LANGUAGES else "en"
    _catalog = (
        json.loads(files("perpetui").joinpath("locales", "ja.json").read_text(encoding="utf-8"))
        if _language == "ja"
        else {}
    )
    return _language


def _(message: str, **values: object) -> str:
    translated = _catalog.get(message, message)
    return translated.format(**values) if values else translated
