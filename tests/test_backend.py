import pytest

from whatsapp_terminal.backend import WhatsApp
from whatsapp_terminal.store import Store


class FakeBridge:
    def __init__(self, data_dir, on_event):
        self.on_event = on_event
        self.requests = []
        self.results = {"send": {"id": "sent", "timestamp": 200}, "contacts": [], "groups": []}

    def request(self, method, **params):
        self.requests.append((method, params))
        return self.results.get(method)

    def start(self):
        pass

    def stop(self):
        pass


@pytest.fixture
def backend(monkeypatch, tmp_path):
    monkeypatch.setattr("whatsapp_terminal.backend.Bridge", FakeBridge)
    messages = []
    instance = WhatsApp(tmp_path, Store(":memory:"), messages.append)
    return instance, messages


def test_connection_readiness_and_failure(backend):
    instance, notices = backend
    with pytest.raises(RuntimeError, match="Not connected"):
        instance.send("123@lid", "Hello")
    instance.handle_event({"event": "connection", "state": "closed", "error": "Rejected (405)"})
    with pytest.raises(RuntimeError, match="405"):
        instance.send("123@lid", "Hello")
    instance.handle_event({"event": "connection", "state": "open"})
    assert instance.ready.is_set()
    assert instance.connection_error is None


def test_send_and_sync_use_known_direction(backend):
    instance, notices = backend
    instance.ready.set()
    with pytest.raises(ValueError, match="No sync anchor"):
        instance.sync("123@lid", 50)
    instance.send("123@lid", "Hello")
    instance.sync("123@lid", 50)
    assert instance.bridge.requests[-1] == (
        "history",
        {
            "chat": "123@lid",
            "message_id": "sent",
            "from_me": True,
            "timestamp": 200,
            "count": 50,
        },
    )
    assert instance.store.history("123@lid") == [("You", "Hello", 200)]


def test_contacts_history_groups_and_identity(backend):
    instance, notices = backend
    instance.ready.set()
    instance.bridge.results["contacts"] = [
        {"id": "123@lid", "phoneNumber": "441234567890@s.whatsapp.net", "name": "Alice"},
    ]
    instance.bridge.results["known_mutes"] = {"123@lid": -1}
    instance.refresh_contacts()
    assert instance.store.contacts() == [("123@lid", "Alice")]
    assert instance.store.canonical("441234567890@s.whatsapp.net") == "123@lid"
    assert instance.muting.is_muted("123@lid")
    instance.bridge.results["groups"] = [{"id": "123-456@g.us", "subject": "Friends"}]
    instance.refresh_groups()
    assert ("123-456@g.us", "Friends") in instance.store.chats()
    instance.handle_event({"event": "history", "messages": []})
    assert "History sync: 0 new messages" in notices[-1]


def test_live_messages_unread_but_history_not_unread(backend):
    instance, notices = backend
    message = {
        "key": {"remoteJid": "123@lid", "id": "live", "fromMe": False},
        "pushName": "Alice",
        "messageTimestamp": 1795593416000,
        "message": {"conversation": "Hi"},
    }
    instance.handle_event({"event": "messages", "live": True, "messages": [message]})
    assert instance.store.unread_counts() == {"123@lid": 1}
    instance.handle_event({"event": "messages", "live": True, "messages": [message]})
    message["key"]["id"] = "history"
    instance.handle_event({"event": "history", "messages": [message]})
    assert instance.store.unread_counts() == {"123@lid": 1}
    assert len(instance.store.history("123@lid")) == 2


def test_receipts_and_group_exclusion(backend):
    instance, notices = backend
    instance.store.add("123@lid", "one", "You", "Hello", 100, True)
    event = {
        "event": "receipt",
        "chat": "123@lid",
        "message_id": "one",
        "status": "read",
        "timestamp": 200,
    }
    instance.handle_event(event)
    assert "read your message" in notices[-1]
    assert instance.store.history_with_status("123@lid")[0][3] == "read"
    instance.handle_event({**event, "status": "delivered"})
    instance.handle_event({**event, "chat": "123@g.us"})
    assert len(notices) == 1


def test_recent_only_requests_five(backend):
    instance, notices = backend
    instance.ready.set()
    for index in range(7):
        instance.store.add(f"{index}@lid", str(index), "You", "Hi", index + 1, True)
    instance.sync_recent()
    assert [params["chat"] for method, params in instance.bridge.requests] == [
        "6@lid",
        "5@lid",
        "4@lid",
        "3@lid",
        "2@lid",
    ]


def test_shutdown_restores_before_stopping_even_on_failure(backend):
    instance, notices = backend
    calls = []
    instance.ready.set()
    instance.muting.close = lambda: calls.append(("restore", instance.ready.is_set()))
    instance.bridge.stop = lambda: calls.append(("stop", instance.ready.is_set()))
    instance.stop()
    assert calls == [("restore", True), ("stop", False)]

    def fail():
        raise RuntimeError("Cleanup failed")

    instance.muting.close = fail
    with pytest.raises(RuntimeError):
        instance.stop()
    assert calls[-1] == ("stop", False)


def test_qr_routes_to_interface(backend):
    instance, notices = backend
    instance.on_qr = notices.append
    instance.handle_event({"event": "qr", "code": "secret"})
    assert notices == ["secret"]


def test_synced_receipt_persists_across_restart_without_live_notice(backend, tmp_path):
    instance, notices = backend
    path = tmp_path / "messages.db"
    instance.store = Store(path)
    instance.store.add("123@lid", "sent", "You", "Hello", 100, True)
    instance.handle_event(
        {
            "event": "receipt",
            "chat": "123@lid",
            "message_id": "sent",
            "status": "read",
            "timestamp": 0,
            "historical": True,
        }
    )
    assert Store(path).history_with_status("123@lid")[0][3] == "read"
    assert notices == []


def test_resolved_identity_matches_lid_receipt_to_phone_send(backend):
    instance, notices = backend
    phone, lid = "441234567890@s.whatsapp.net", "123@lid"
    instance.store.add(phone, "sent", "You", "Hello", 100, True)
    instance.handle_event({"event": "identity", "pn": phone, "lid": lid})
    instance.handle_event(
        {
            "event": "receipt",
            "chat": lid,
            "message_id": "sent",
            "status": "read",
            "timestamp": 500,
        }
    )
    assert instance.store.history_with_status(phone)[0][3] == "read"
    assert "read your message" in notices[-1]
