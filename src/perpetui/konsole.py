"""Install an opt-in Konsole keymap without changing existing profiles."""

from __future__ import annotations

import os
from importlib.resources import files
from pathlib import Path


def install_keymap() -> Path:
    directory = Path(os.getenv("XDG_DATA_HOME", "~/.local/share")).expanduser() / "konsole"
    target = directory / "perpetui.keytab"
    content = files("perpetui").joinpath("resources", "konsole.keytab").read_text(encoding="utf-8")
    directory.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.read_text(encoding="utf-8") == content:
        return target
    with target.open("x", encoding="utf-8") as stream:
        stream.write(content)
    return target
