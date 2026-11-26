import sqlite3

from whatsapp_terminal.history import import_batch, message_text
from whatsapp_terminal.store import Store


def message(message_id="one", from_me=False):
    return {
        "key": {"remoteJid": "12345@g.us", "id": message_id, "fromMe": from_me},
        "message": {"conversation": "Hello"},
        "messageTimestamp": 200,
        "pushName": "Alice",
    }


def test_batches_persist_deduplicate_and_page(tmp_path):
    path = tmp_path / "messages.db"
    store = Store(path)
    batch = {
        "contacts": [{"id": "441234567890@s.whatsapp.net", "name": "Alice"}],
        "chats": [{"id": "12345@g.us", "name": "Friends"}],
        "messages": [message("sent", True), message("received"), {"key": {"id": "metadata"}}],
    }
    assert import_batch(store, batch) == (1, 2, 1)
    assert import_batch(store, batch) == (1, 0, 1)
    reopened = Store(path)
    assert reopened.contacts() == [("441234567890@s.whatsapp.net", "Alice")]
    assert reopened.chats() == [("12345@g.us", "Friends")]
    assert len(reopened.history("12345@g.us")) == 2
    assert reopened.unread_counts() == {}


def test_media_wrappers_and_invalid_entries():
    assert (
        message_text(
            {"ephemeralMessage": {"message": {"extendedTextMessage": {"text": "Earlier"}}}}
        )
        == "Earlier"
    )
    assert message_text({"imageMessage": {"caption": "Photo"}}) == "Photo"
    assert message_text({"imageMessage": {}}) == "[image]"
    store = Store(":memory:")
    invalid = message()
    invalid["key"]["remoteJid"] = "status@broadcast"
    assert import_batch(store, {"messages": [invalid]}) == (0, 0, 1)


def test_old_database_migrates_without_losing_messages(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as database:
        database.execute(
            "CREATE TABLE messages (chat TEXT, id TEXT, sender TEXT, body TEXT, "
            "timestamp REAL, PRIMARY KEY(chat, id))"
        )
        database.execute("INSERT INTO messages VALUES ('12345@g.us', 'one', 'You', 'Hello', 200)")
    store = Store(path)
    assert store.oldest("12345@g.us") is None
    assert import_batch(store, {"messages": [message(from_me=True)]}) == (0, 0, 0)
    assert store.oldest("12345@g.us") == ("one", 1, 200)


def test_phone_activity_without_messages():
    store = Store(":memory:")
    import_batch(
        store,
        {
            "chats": [
                {"id": "123@lid", "name": "Alice", "conversationTimestamp": 300},
                {"id": "456@lid", "name": "Bob", "lastMessageRecvTimestamp": 400},
            ]
        },
    )
    assert store.recent() == [("456@lid", "Bob"), ("123@lid", "Alice")]
