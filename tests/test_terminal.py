import pytest

from whatsapp_terminal.cli import Terminal
from whatsapp_terminal.store import Store, destination, safe_text


def test_number_and_group():
    assert destination("+44 1234-567890") == "441234567890@s.whatsapp.net"
    assert destination("123-456@g.us") == "123-456@g.us"
    for invalid in ["", "Alice", "123", "1234567@evil.com", "１２３４５６７"]:
        with pytest.raises(ValueError):
            destination(invalid)


def test_persistence_and_deduplication(tmp_path):
    path = tmp_path / "messages.db"
    store = Store(path)
    store.chat("chat", "Alice")
    assert store.add("chat", "one", "Alice", "First", 1)
    assert not store.add("chat", "one", "Alice", "First", 1)
    store.add("chat", "two", "You", "Second", 2)
    reopened = Store(path)
    assert reopened.chats() == [("chat", "Alice")]
    assert reopened.history("chat", 1) == [("You", "Second", 2)]
    assert len(reopened.history("chat")) == 2


def test_selection_required_and_explicit_send():
    store = Store(":memory:")
    terminal = Terminal(store)
    with pytest.raises(ValueError, match="Select"):
        terminal.command("hello")
    terminal.command("/open +441234567890")
    terminal.command("/send /hello")
    assert store.history(terminal.active)[0][1] == "/hello"
    with pytest.raises(ValueError, match="Unknown"):
        terminal.command("/oops")
    assert terminal.command("/quit") is False


def test_failed_send_is_not_saved():
    class Offline:
        def send(self, chat, body):
            raise RuntimeError("Offline")

    store = Store(":memory:")
    terminal = Terminal(store, Offline())
    terminal.command("/open +441234567890")
    with pytest.raises(RuntimeError):
        terminal.command("hello")
    assert store.history(terminal.active) == []


def test_remote_terminal_controls_removed():
    assert safe_text("hello\x1b]52;clipboard\x07\r\nworld") == "hello]52;clipboard\nworld"
