import pytest

from whatsapp_terminal.cli import Terminal
from whatsapp_terminal.store import Store


def test_names_persist_and_saved_names_win(tmp_path):
    path = tmp_path / "contacts.db"
    store = Store(path)
    store.save_contacts([("12345@lid", "Alice Smith", 2)])
    store.save_contacts([("12345@lid", "Ali", 1)])
    store.chat("12345@lid", "Ali")
    assert Store(path).contacts() == [("12345@lid", "Alice Smith")]
    assert store.chats() == [("12345@lid", "Alice Smith")]


def test_open_names_and_ambiguous_selection(capsys):
    store = Store(":memory:")
    store.save_contacts([("12345@lid", "Alice Smith", 2), ("67890@lid", "Alice Jones", 2)])
    terminal = Terminal(store)
    terminal.command("/open alice smith")
    assert terminal.active == "12345@lid"
    with pytest.raises(ValueError, match="Multiple matches"):
        terminal.command("/open Alice")
    assert terminal.active == "12345@lid"
    terminal.command("/contacts Jones")
    terminal.command("/open #1")
    assert terminal.active == "67890@lid"
    assert "Alice Jones" in capsys.readouterr().out
    for invalid in ["#0", "#99", "#oops", "Nobody", ""]:
        with pytest.raises(ValueError):
            terminal.command(f"/open {invalid}")
        assert terminal.active == "67890@lid"


def test_contacts_refresh_before_listing_and_cached_fallback(capsys):
    store = Store(":memory:")

    class Backend:
        def refresh_contacts(self):
            store.save_contacts([("12345@lid", "Alice", 2)])

    terminal = Terminal(store, Backend())
    terminal.command("/contacts")
    assert "Alice" in capsys.readouterr().out
    terminal.command("/open Alice")
    assert terminal.active == "12345@lid"
