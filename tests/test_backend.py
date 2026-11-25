import sys
from importlib import import_module
from importlib.metadata import distribution
from types import ModuleType, SimpleNamespace

import pytest

from whatsapp_terminal.backend import WhatsApp
from whatsapp_terminal.store import Store


@pytest.fixture
def backend(monkeypatch, tmp_path):
    package = ModuleType("neonize")
    package.__path__ = [str(distribution("neonize").locate_file("neonize"))]
    monkeypatch.setitem(sys.modules, "neonize", package)
    import_module("neonize.proto.Neonize_pb2")
    events = ModuleType("neonize.events")
    for name in (
        "ClientOutdatedEv",
        "ConnectedEv",
        "ConnectFailureEv",
        "DisconnectedEv",
        "HistorySyncEv",
        "LoggedOutEv",
        "MessageEv",
        "OfflineSyncCompletedEv",
        "ReceiptEv",
    ):
        setattr(events, name, type(name, (), {}))

    class FakeClient:
        def __init__(self, path):
            self.handlers = {}
            self.requests = []
            self.contact = SimpleNamespace(get_all_contacts=lambda: [])

        def request_history_sync(self, *args):
            args[0].SerializeToString()
            self.requests.append(args)

        def send_message(self, jid, body):
            jid.SerializeToString()
            self.requests.append((jid, body))
            return SimpleNamespace(ID="sent", Timestamp=200)

        def get_joined_groups(self):
            return []

        def event(self, kind):
            def register(handler):
                self.handlers[kind] = handler

            return register

    client_module = ModuleType("neonize.client")
    client_module.NewClient = FakeClient
    monkeypatch.setitem(sys.modules, "neonize.client", client_module)
    monkeypatch.setitem(sys.modules, "neonize.events", events)
    messages = []
    instance = WhatsApp(tmp_path, Store(":memory:"), messages.append)
    return instance, events, messages


def test_outdated_event_disables_sending_and_explains_recovery(backend):
    instance, events, messages = backend
    instance.ready.set()
    instance.client.handlers[events.ClientOutdatedEv](instance.client, events.ClientOutdatedEv())
    assert not instance.ready.is_set()
    assert "405" in messages[-1]
    assert "not connected" in messages[-1]
    with pytest.raises(RuntimeError, match="405"):
        instance.send("441234567890@s.whatsapp.net", "hello")


def test_connection_failure_is_cleared_on_success(backend):
    instance, events, messages = backend
    instance.client.handlers[events.ConnectFailureEv](
        instance.client, SimpleNamespace(Message="Service unavailable", Reason=10)
    )
    assert "Service unavailable" in messages[-1]
    assert not instance.ready.is_set()
    instance.client.handlers[events.ConnectedEv](instance.client, events.ConnectedEv())
    assert instance.ready.is_set()
    assert instance.connection_error is None


def test_sync_uses_oldest_known_message_and_direction(backend):
    instance, events, messages = backend
    chat = "441234567890@s.whatsapp.net"
    with pytest.raises(RuntimeError, match="Not connected"):
        instance.sync(chat, 50)
    instance.ready.set()
    with pytest.raises(ValueError, match="No sync anchor"):
        instance.sync(chat, 50)
    instance.store.add(chat, "newer", "You", "New", 200, True)
    instance.store.add(chat, "older", "Alice", "Old", 100, False)
    instance.sync(chat, 50)
    jid, message_id, from_me, timestamp, count = instance.client.requests[0]
    assert jid.User == "441234567890"
    assert (message_id, from_me, timestamp, count) == ("older", False, 100, 50)


def test_history_event_imports_and_announces_batch(backend):
    instance, events, messages = backend
    instance.client.handlers[events.HistorySyncEv](
        instance.client, SimpleNamespace(Data=SimpleNamespace(conversations=[], pushnames=[]))
    )
    assert "History sync: 0 new messages" in messages[-1]


def test_contact_refresh_prefers_saved_names(backend):
    instance, events, messages = backend
    instance.client.contact.get_all_contacts = lambda: [
        SimpleNamespace(
            JID=SimpleNamespace(User="441234567890", Server="s.whatsapp.net"),
            Info=SimpleNamespace(
                FullName="Alice Smith", FirstName="Alice", PushName="Ali", BusinessName=""
            ),
        )
    ]
    instance.ready.set()
    instance.refresh_contacts()
    assert instance.store.contacts() == [("441234567890@s.whatsapp.net", "Alice Smith")]


@pytest.mark.parametrize("chat", ["441234567890@s.whatsapp.net", "12345@lid", "123-456@g.us"])
def test_send_serializes_complete_address(backend, chat):
    instance, events, messages = backend
    instance.ready.set()
    instance.send(chat, "Hello")
    jid, body = instance.client.requests[0]
    assert jid.IsInitialized()
    assert f"{jid.User}@{jid.Server}" == chat
    assert (jid.RawAgent, jid.Device, jid.Integrator) == (0, 0, 0)
    assert instance.store.history(chat) == [("You", "Hello", 200)]


def test_recent_sync_requests_only_five_most_active_chats(backend):
    instance, events, messages = backend
    instance.ready.set()
    for index in range(7):
        instance.store.add(f"{index}@lid", f"msg-{index}", "You", "Hi", index + 1, True)
    instance.sync_recent()
    assert [request[0].User for request in instance.client.requests] == ["6", "5", "4", "3", "2"]
    assert all(request[-1] == 50 for request in instance.client.requests)


def test_groups_are_discovered_without_history(backend):
    instance, events, messages = backend
    instance.ready.set()
    instance.client.get_joined_groups = lambda: [
        SimpleNamespace(
            JID=SimpleNamespace(User="12345", Server="g.us"),
            GroupName=SimpleNamespace(Name="Weekend plans"),
        )
    ]
    instance.refresh_groups()
    assert instance.store.chats() == [("12345@g.us", "Weekend plans")]
    assert instance.store.recent() == []
    instance.client.get_joined_groups = lambda: [
        SimpleNamespace(
            JID=SimpleNamespace(User="12345", Server="g.us"),
            GroupName=SimpleNamespace(Name="New group name"),
        )
    ]
    instance.refresh_groups()
    assert instance.store.chats() == [("12345@g.us", "New group name")]


def test_identity_refresh_uses_whatsapp_mapping_not_names(backend):
    instance, events, messages = backend
    phone, lid = "441234567890@s.whatsapp.net", "12345@lid"
    instance.store.save_contacts([(phone, "Alice Smith", 2), (lid, "Alice", 1)])
    instance.client.get_lid_from_pn = lambda jid: SimpleNamespace(User="12345", Server="lid")
    instance.client.get_pn_from_lid = lambda jid: SimpleNamespace(
        User="441234567890", Server="s.whatsapp.net"
    )
    instance.ready.set()
    instance.refresh_identities()
    assert instance.store.contacts() == [(lid, "Alice Smith")]


def test_receipts_use_real_protobuf_and_ignore_self_and_groups(backend):
    from neonize.proto.Neonize_pb2 import Receipt

    instance, events, notices = backend
    chat = "123@lid"
    instance.store.add(chat, "one", "You", "Hello", 100, True)
    handler = instance.client.handlers[events.ReceiptEv]
    event = Receipt(Type=Receipt.DELIVERED, Timestamp=200, MessageIDs=["one"])
    event.MessageSource.Chat.User = "123"
    event.MessageSource.Chat.Server = "lid"
    event.MessageSource.IsGroup = False
    event.MessageSource.IsFromMe = False
    handler(instance.client, event)
    assert "received your message" in notices[-1]
    assert instance.store.history_with_status(chat)[0][3] == "delivered"
    for kind in (Receipt.READ_SELF, Receipt.SENDER, Receipt.PLAYED_SELF):
        event.Type = kind
        handler(instance.client, event)
    assert len(notices) == 1
    event.Type = Receipt.READ
    event.MessageSource.IsGroup = True
    handler(instance.client, event)
    event.MessageSource.IsGroup = False
    event.MessageSource.IsFromMe = True
    handler(instance.client, event)
    assert len(notices) == 1
    event.MessageSource.IsFromMe = False
    handler(instance.client, event)
    assert "read your message" in notices[-1]
    assert instance.store.history_with_status(chat)[0][3] == "read"
    handler(instance.client, event)
    assert len(notices) == 2


def test_shutdown_restores_mute_before_stopping_native_worker(backend):
    instance, events, messages = backend
    calls = []
    instance.ready.set()
    instance.muting.close = lambda: calls.append(("restore", instance.ready.is_set()))
    instance.client.stop = lambda: calls.append(("stop", instance.ready.is_set()))
    instance.client.disconnect = lambda: pytest.fail(
        "Disconnect alone leaves the native worker alive"
    )
    instance.stop()
    assert calls == [("restore", True), ("stop", False)]


def test_shutdown_stops_native_worker_even_if_mute_cleanup_fails(backend):
    instance, events, messages = backend
    calls = []

    def fail():
        raise RuntimeError("Cleanup failed")

    instance.muting.close = fail
    instance.client.stop = lambda: calls.append("stop")
    with pytest.raises(RuntimeError, match="Cleanup failed"):
        instance.stop()
    assert calls == ["stop"]
