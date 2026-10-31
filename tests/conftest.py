import pytest

from perpetui.i18n import install_language


@pytest.fixture(autouse=True)
def _default_english_catalog():
    install_language("en")
