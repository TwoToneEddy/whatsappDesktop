from whatsapp_terminal.store import Store
from whatsapp_terminal.cli import Terminal


def test_favorites_persist_separately_from_recency(tmp_path):
    path = tmp_path / "messages.db"
    store = Store(path)
    store.save_contacts([("123@lid", "Alice", 2)])
    assert store.toggle_favorite("123@lid")
    assert Store(path).favorites() == [("123@lid", "Alice")]
    assert store.recent() == []
    assert not store.toggle_favorite("123@lid")
    assert store.favorites() == []


def test_recent_uses_phone_activity_and_does_not_regress():
    store = Store(":memory:")
    for index in range(7):
        jid = f"{index}@lid"
        store.chat(jid, f"Chat {index}")
        store.record_activity([(jid, index + 1)])
    store.record_activity([("6@lid", 1)])
    store.chat("999@lid", "Manually opened contact")
    assert [jid for jid, name in store.recent()] == ["6@lid", "5@lid", "4@lid", "3@lid", "2@lid"]
    store.add("0@lid", "new", "You", "Hello", 100, True)
    assert store.recent()[0][0] == "0@lid"


def test_plain_favorite_and_back_commands():
    store = Store(":memory:")
    store.save_contacts([("123@lid", "Alice", 2)])
    terminal = Terminal(store, emit=lambda message: None)
    terminal.command("/open Alice")
    terminal.command("/favorite")
    terminal.command("/favorites")
    assert terminal.listed == [("123@lid", "Alice")]
    terminal.command("/back")
    assert terminal.active is None
    terminal.command("/favorite Alice")
    assert store.favorites() == []
