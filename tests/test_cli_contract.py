import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

from perpetui import __version__
from perpetui.cli import build_parser
from perpetui.i18n import install_language

ROOT = Path(__file__).resolve().parents[1]


def test_cobra_and_argparse_agree_on_options_defaults_choices_and_english_help():
    source = (ROOT / "internal/cli/root.go").read_text(encoding="utf-8")
    flags = {
        name: (kind, json.loads(default), json.loads(help_text))
        for kind, name, default, help_text in re.findall(
            r'flags\.(String|Bool|Float64)\("([^"]+)", ("[^"]*"|true|false|[0-9.]+), ("[^"]*")\)',
            source,
        )
    }
    completions = {
        name: json.loads("[" + values + "]")
        for name, values in re.findall(r'"(\w+)":\s*\{([^}]+)\}', source)
    }
    install_language("en")
    actions = {
        option.removeprefix("--"): action
        for action in build_parser()._actions
        for option in action.option_strings
        if option.startswith("--") and option not in {"--help", "--version"}
    }
    assert flags.keys() == actions.keys()
    for name, action in actions.items():
        kind, default, help_text = flags[name]
        assert default == (action.default if action.default is not None else ""), name
        assert help_text == action.help, name
        if isinstance(action.default, bool):
            assert kind == "Bool", name
        elif isinstance(action.default, (int, float)):
            assert kind == "Float64", name
        else:
            assert kind == "String", name
        if action.choices is not None:
            assert completions[name] == list(action.choices), name
    assert completions.keys() == {name for name, action in actions.items() if action.choices}


def test_python_distribution_and_generated_go_version_match_project_metadata():
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    assert __version__ == version
    subprocess.run([sys.executable, "scripts/sync_version.py", "--check"], cwd=ROOT, check=True)
    generated = (ROOT / "internal/cli/version_generated.go").read_text()
    assert f'const version = "{version}"' in generated
