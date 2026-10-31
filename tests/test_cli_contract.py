import re
from pathlib import Path

from perpetui.cli import build_parser


def test_cobra_and_argparse_define_the_same_long_options():
    source = (Path(__file__).resolve().parents[1] / "internal/cli/root.go").read_text(
        encoding="utf-8"
    )
    cobra_flags = set(re.findall(r'flags\.(?:String|Bool|Float64)\("([^"]+)"', source))
    parser = build_parser()
    argparse_flags = {
        option.removeprefix("--")
        for action in parser._actions
        for option in action.option_strings
        if option.startswith("--")
    } - {"help", "version"}
    assert cobra_flags == argparse_flags
