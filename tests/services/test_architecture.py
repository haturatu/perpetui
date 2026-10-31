import ast
from pathlib import Path

from perpetui.app import TradingApp
from perpetui.ui.app import TradingApp as PresentationApp

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "src/perpetui"


def dependencies(path):
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom):
            assert not node.level, f"Use explicit package imports in {path}"
            yield node.module or ""
        elif isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)


def test_domain_services_and_adapters_do_not_depend_on_presentation_or_each_other_implementation():
    for layer in ("domain", "services", "exchanges"):
        for path in (SOURCE / layer).rglob("*.py"):
            for module in dependencies(path):
                assert not module.startswith(("perpetui.ui", "textual", "rich", "plotext")), path
                if layer == "domain":
                    assert not module.startswith(("perpetui.services", "perpetui.exchanges")), path
                if layer == "services" and module.startswith("perpetui.exchanges"):
                    assert module == "perpetui.exchanges.base", path
                if layer == "exchanges":
                    assert not module.startswith("perpetui.services"), path


def test_app_compatibility_entry_point_exports_the_same_application():
    assert TradingApp is PresentationApp
