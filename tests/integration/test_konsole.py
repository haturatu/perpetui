import pytest

from perpetui.cli import main
from perpetui.konsole import install_keymap


def test_keymap_install_is_idempotent_and_does_not_modify_profiles(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    target = install_keymap()
    content = target.read_text()
    assert target == tmp_path / "konsole" / "perpetui.keytab"
    for number in range(1, 10):
        assert f'key {number} +Ctrl-Alt-Shift : "\\E[{48 + number};5u"' in content
    assert install_keymap().read_text() == content
    assert list(target.parent.iterdir()) == [target]
    target.write_text("User customizations")
    with pytest.raises(FileExistsError):
        install_keymap()
    assert target.read_text() == "User customizations"


def test_cli_installs_keymap_without_loading_accounts(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    def forbidden(*args, **kwargs):
        pytest.fail("Installer loaded an account")

    monkeypatch.setattr("perpetui.exchanges.factory.load_credentials", forbidden)
    monkeypatch.setattr("perpetui.exchanges.factory.load_hl_account", forbidden)
    assert main(["--install-konsole-keymap", "--lang", "en"]) == 0
    assert "Keyboard settings" in capsys.readouterr().out
