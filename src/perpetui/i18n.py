"""Small runtime catalogs, selected before constructing CLI and UI widgets."""

from __future__ import annotations

import json
import os
from importlib.resources import files

LANGUAGES = ("en", "ja", "ru", "zh-cn", "zh-tw")

# Environment locales may carry script or region variants. Keep the bundled
# catalogs small while still selecting the closest available language.
_ALIASES = {
    "zh": "zh-cn",
    "zh-hans": "zh-cn",
    "zh-sg": "zh-cn",
    "zh-hant": "zh-tw",
    "zh-hk": "zh-tw",
    "zh-mo": "zh-tw",
}

_language = "en"
_catalog: dict[str, str] = {}


def current_language() -> str:
    return _language


def normalize_language(language: str) -> str | None:
    candidate = language.strip().lower().replace("_", "-")
    candidate = candidate.split(":")[0].split(".")[0].split("@")[0]
    if candidate in LANGUAGES:
        return candidate
    if candidate in _ALIASES:
        return _ALIASES[candidate]
    base = candidate.split("-")[0]
    if base in LANGUAGES:
        return base
    return _ALIASES.get(base)


def install_language(language: str | None = None) -> str:
    global _language, _catalog
    if language is None:
        for variable in ("PERPETUI_LANG", "LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
            if value := os.getenv(variable):
                language = value
                break
    _language = normalize_language(language or "") or "en"
    _catalog = (
        json.loads(
            files("perpetui").joinpath("locales", f"{_language}.json").read_text(encoding="utf-8")
        )
        if _language != "en"
        else {}
    )
    return _language


def _(message: str, **values: object) -> str:
    translated = _catalog.get(message, message)
    return translated.format(**values) if values else translated
