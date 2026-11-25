from importlib import import_module
from importlib.metadata import distribution
import sqlite3
import sys
from types import ModuleType

import pytest

from whatsapp_terminal.history import import_batch, message_text
from whatsapp_terminal.store import Store


@pytest.fixture
def history_proto(monkeypatch):
    package = ModuleType("neonize")
    package.__path__ = [str(distribution("neonize").locate_file("neonize"))]
    monkeypatch.setitem(sys.modules, "neonize", package)
    return import_module("neonize.proto.waHistorySync.WAWebProtobufsHistorySync_pb2")


def test_real_history_batches_persist_deduplicate_and_page(tmp_path, history_proto):
    path = tmp_path / "messages.db"
    store = Store(path)
    batch = history_proto.HistorySync()
    batch.pushnames.add(ID="441234567890@s.whatsapp.net", pushname="Alice")
    chat = batch.conversations.add(ID="12345@g.us", name="Friends")
    outgoing = chat.messages.add().message
    outgoing.key.ID = "sent"
    outgoing.key.fromMe = True
    outgoing.messageTimestamp = 200
    outgoing.message.conversation = "Hello"
    incoming = chat.messages.add().message
    incoming.key.ID = "received"
    incoming.key.participant = "441234567890@s.whatsapp.net"
    incoming.pushName = "Alice"
    incoming.messageTimestamp = 100
    incoming.message.ephemeralMessage.message.extendedTextMessage.text = "Earlier"
    chat.messages.add().message.key.ID = "metadata-only"
    store.add(chat.ID, "sent", "You", "Hello", 200, True)
    assert import_batch(store, batch) == (1, 1, 1)
    assert store.contacts() == [("441234567890@s.whatsapp.net", "Alice")]
    assert import_batch(store, batch) == (1, 0, 1)
    reopened = Store(path)
    assert reopened.chats() == [(chat.ID, "Friends")]
    assert reopened.history(chat.ID) == [("Alice", "Earlier", 100), ("You", "Hello", 200)]
    assert reopened.oldest(chat.ID) == ("received", 0, 100)


def test_media_empty_chats_and_invalid_entries(history_proto):
    store = Store(":memory:")
    batch = history_proto.HistorySync()
    batch.conversations.add(ID="441234567890@s.whatsapp.net", displayName="Alice")
    chat = batch.conversations.add(ID="12345@lid")
    message = chat.messages.add().message
    message.key.ID = "media"
    message.messageTimestamp = 100
    message.message.imageMessage.caption = "Photo caption"
    batch.conversations.add(ID="status@broadcast").messages.add()
    assert import_batch(store, batch) == (2, 1, 1)
    assert store.history(chat.ID)[0][1] == "Photo caption"
    message.message.imageMessage.caption = ""
    assert message_text(message.message) == "[image]"
    assert len(store.chats()) == 2


def test_old_database_migrates_without_losing_messages(tmp_path, history_proto):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as database:
        database.execute(
            "CREATE TABLE messages (chat TEXT, id TEXT, sender TEXT, body TEXT, "
            "timestamp REAL, PRIMARY KEY(chat, id))"
        )
        database.execute("INSERT INTO messages VALUES ('12345@lid', 'old', 'You', 'Hi', 100)")
    store = Store(path)
    assert store.history("12345@lid") == [("You", "Hi", 100)]
    assert store.oldest("12345@lid") is None
    batch = history_proto.HistorySync()
    message = batch.conversations.add(ID="12345@lid").messages.add().message
    message.key.ID = "old"
    message.key.fromMe = True
    message.messageTimestamp = 100
    message.message.conversation = "Hi"
    assert import_batch(store, batch) == (1, 0, 0)
    assert store.oldest("12345@lid") == ("old", 1, 100)


def test_phone_conversation_timestamps_populate_recent_without_messages(history_proto):
    store = Store(":memory:")
    batch = history_proto.HistorySync()
    batch.conversations.add(ID="123@lid", name="Alice", lastMsgTimestamp=300)
    batch.conversations.add(ID="456@lid", name="Bob", conversationTimestamp=400)
    import_batch(store, batch)
    assert store.recent() == [("456@lid", "Bob"), ("123@lid", "Alice")]
