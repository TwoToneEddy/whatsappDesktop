import pytest

from whatsapp_terminal.cli import Terminal
from whatsapp_terminal.store import Store


PHONE = "441234567890@s.whatsapp.net"
LID = "123456789@lid"


def test_verified_identity_merges_history_names_favorites_and_activity(tmp_path):
    path = tmp_path / "messages.db"
    store = Store(path)
    store.save_contacts([(PHONE, "Alice Smith", 2), (LID, "Alice", 1)])
    store.add(PHONE, "one", "You", "First", 100, True)
    store.add(LID, "one", "You", "First", 100)
    store.add(LID, "two", "Alice", "Second", 200, False)
    store.toggle_favorite(PHONE)
    store.record_activity([(PHONE, 300), (LID, 200)])
    store.link_identity(PHONE, LID)
    store = Store(path)
    assert store.contacts() == [(LID, "Alice Smith")]
    assert store.chats() == [(LID, "Alice Smith")]
    assert store.favorites() == [(LID, "Alice Smith")]
    assert store.oldest(PHONE) == ("one", 1, 100)
    assert len(store.history(PHONE)) == 2
    assert store.db.execute(
        "SELECT timestamp FROM chat_activity WHERE jid=?", (LID,)
    ).fetchone() == (300,)
    assert store.label(PHONE) == "Alice Smith"
    terminal = Terminal(store, emit=lambda text: None)
    terminal.command("/open Alice")
    assert terminal.active == LID
    terminal.command("/open +441234567890")
    assert terminal.active == LID


def test_future_phone_updates_do_not_reintroduce_duplicate():
    store = Store(":memory:")
    store.link_identity(PHONE, LID)
    store.chat(PHONE)
    store.save_contacts([(PHONE, "Alice", 2)])
    store.add(PHONE, "live", "Alice", "Hello", 100, False)
    store.import_history([(PHONE, PHONE)], [(PHONE, "old", "You", "Earlier", 50, True)])
    store.record_activity([(PHONE, 200)])
    assert store.chats() == [(LID, "Alice")]
    assert store.contacts() == [(LID, "Alice")]
    assert len(store.history(LID)) == 2
    assert store.recent() == [(LID, "Alice")]
    store.link_identity(PHONE, LID)
    with pytest.raises(ValueError, match="Conflicting"):
        store.link_identity(PHONE, "999@lid")
    assert store.canonical(PHONE) == LID


def test_same_name_without_mapping_stays_ambiguous():
    store = Store(":memory:")
    store.save_contacts([(PHONE, "Alice", 2), (LID, "Alice", 2)])
    terminal = Terminal(store, emit=lambda text: None)
    with pytest.raises(ValueError, match="Multiple matches"):
        terminal.command("/open Alice")
    assert len(store.contacts()) == 2
