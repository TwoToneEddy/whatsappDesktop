import asyncio
import sys
import threading
from importlib import import_module
from importlib.metadata import distribution
from types import ModuleType, SimpleNamespace

import pytest

from whatsapp_terminal.muting import TemporaryMute, mute_patch


@pytest.fixture
def muting(monkeypatch):
    package = ModuleType("neonize")
    package.__path__ = [str(distribution("neonize").locate_file("neonize"))]
    monkeypatch.setitem(sys.modules, "neonize", package)
    import_module("neonize.proto.Neonize_pb2")
    monkeypatch.setattr("whatsapp_terminal.muting.time.time", lambda: 1000)
    settings = SimpleNamespace(MutedUntil=0, Found=True)
    patches, notices = [], []

    def send(patch):
        patch.SerializeToString()
        patches.append(patch)
        action = patch.Mutations[0].Value.muteAction
        settings.MutedUntil = 253402300799 if action.muted else 0

    backend = SimpleNamespace(
        ready=threading.Event(),
        output=notices.append,
        client=SimpleNamespace(
            fetch_app_state=lambda *args: None,
            send_app_state=send,
            chat_settings=SimpleNamespace(get_chat_settings=lambda jid: settings),
        ),
    )
    backend.ready.set()
    manager = TemporaryMute(backend)
    manager.select("123@lid")
    return manager, settings, patches, notices


def test_patch_and_restore(muting):
    manager, settings, patches, notices = muting
    manager.enable("123@lid")
    assert manager.enabled("123@lid")
    patch = patches[0]
    assert patch.IsInitialized()
    assert patch.Type == patch.REGULAR_HIGH
    assert list(patch.Mutations[0].Index) == ["mute", "123@lid"]
    assert patch.Mutations[0].Version == 2
    assert patch.Mutations[0].Value.muteAction.muted
    assert patch.Mutations[0].Value.muteAction.muteEndTimestamp == -1
    assert settings.MutedUntil == 253402300799
    manager.release(force=True)
    assert not patches[-1].Mutations[0].Value.muteAction.muted
    assert manager.owned is None


def test_preserves_existing_and_phone_changes(muting):
    manager, settings, patches, notices = muting
    settings.MutedUntil = 9999999
    manager.enable("123@lid")
    assert not patches
    settings.MutedUntil = 0
    manager.enable("123@lid")
    settings.MutedUntil = 5000
    manager.release(force=True)
    assert len(patches) == 1
    assert settings.MutedUntil == 5000


def test_disconnect_keeps_mute_until_reconnected(muting, monkeypatch):
    manager, settings, patches, notices = muting
    manager.enable("123@lid")
    manager.backend.ready.clear()
    manager.release(force=True)
    assert manager.owned
    assert "Could not restore" in notices[-1]
    monkeypatch.setattr("whatsapp_terminal.muting.time.time", lambda: 2000)
    assert manager.enabled("123@lid")
    manager.backend.ready.set()
    manager.release(force=True)
    assert manager.owned is None
    assert len(patches) == 2


def test_switch_restores_and_same_chat_does_not(muting):
    manager, settings, patches, notices = muting
    manager.enable("123@lid")
    manager.release()
    assert len(patches) == 1
    manager.selected = "456@lid"
    manager.release()
    assert len(patches) == 2


def test_invalid_or_disconnected_chat_never_sends(muting):
    manager, settings, patches, notices = muting
    with pytest.raises(ValueError):
        manager.enable("123@g.us")
    with pytest.raises(ValueError):
        manager.enable("456@lid")
    manager.backend.ready.clear()
    with pytest.raises(RuntimeError):
        manager.enable("123@lid")
    assert not patches


def test_failed_send_does_not_claim_success(muting):
    manager, settings, patches, notices = muting

    def fail(patch):
        raise RuntimeError("Network error")

    manager.backend.client.send_app_state = fail
    with pytest.raises(RuntimeError):
        manager.enable("123@lid")
    assert not notices
    assert manager.owned


def test_unmute_patch_has_no_expiry(muting):
    patch = mute_patch("123@lid", 0)
    assert patch.IsInitialized()
    assert not patch.Mutations[0].Value.muteAction.HasField("muteEndTimestamp")


@pytest.mark.parametrize("answer, expected", [("", 0), ("n", 0), ("y", 1)])
def test_plain_asks_before_muting(monkeypatch, tmp_path, answer, expected):
    from whatsapp_terminal import cli

    enabled, prompts = [], []
    replies = iter(["/open 123@lid", answer, "/quit"])
    backend = SimpleNamespace(
        start=lambda: None,
        stop=lambda: None,
        muting=SimpleNamespace(select=lambda chat: None, enable=enabled.append),
    )

    class Session:
        def __init__(self, **kwargs):
            pass

        def prompt(self, text):
            prompts.append(text)
            return next(replies)

    monkeypatch.setattr("whatsapp_terminal.backend.WhatsApp", lambda *args: backend)
    monkeypatch.setattr(cli, "PromptSession", Session)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys, "argv", ["wat", "--plain", "--data-dir", str(tmp_path)])
    cli.main()
    assert "[y/N]" in prompts[1]
    assert len(enabled) == expected


def test_tui_mute_button(muting):
    from textual.widgets import Button

    from whatsapp_terminal.store import Store
    from whatsapp_terminal.tui import WhatsAppUI

    manager, settings, patches, notices = muting
    backend = manager.backend
    backend.muting = manager
    backend.start = lambda: None
    backend.connection_error = None
    backend.client.event = SimpleNamespace(qr=lambda callback: None)

    async def scenario():
        store = Store(":memory:")
        store.add("123@lid", "one", "You", "Hello", 100, True)
        app = WhatsAppUI(store, backend)
        async with app.run_test() as pilot:
            assert app.query_one("#mute", Button).disabled
            app.select_chat("123@lid")
            await pilot.click("#mute")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert manager.enabled("123@lid")
            assert str(app.query_one("#mute", Button).label) == "Unmute"
            await pilot.click("#mute")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert manager.owned is None
            assert str(app.query_one("#mute", Button).label) == "Mute"

    asyncio.run(scenario())


def test_close_restores_and_prevents_new_mutes(muting):
    manager, settings, patches, notices = muting
    manager.enable("123@lid")
    manager.close()
    assert settings.MutedUntil == 0
    assert manager.owned is None
    with pytest.raises(RuntimeError, match="closing"):
        manager.enable("123@lid")


def test_restart_recovers_failed_shutdown(muting, tmp_path):
    manager, settings, patches, notices = muting
    path = tmp_path / "mute-recovery.json"
    manager.recovery_path = path
    manager.enable("123@lid")
    assert path.exists()
    manager.backend.ready.clear()
    manager.close()
    assert path.exists()
    restarted = TemporaryMute(manager.backend, path)
    manager.backend.ready.set()
    restarted.release()
    assert settings.MutedUntil == 0
    assert not path.exists()


def test_signal_exit_runs_plain_cleanup(monkeypatch, tmp_path):
    import signal
    from whatsapp_terminal import cli

    stopped = []
    backend = SimpleNamespace(start=lambda: None, stop=lambda: stopped.append(True))

    class Session:
        def __init__(self, **kwargs):
            pass

        def prompt(self, text):
            signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)

    previous = signal.getsignal(signal.SIGTERM)
    monkeypatch.setattr("whatsapp_terminal.backend.WhatsApp", lambda *args: backend)
    monkeypatch.setattr(cli, "PromptSession", Session)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys, "argv", ["wat", "--plain", "--data-dir", str(tmp_path)])
    with pytest.raises(SystemExit):
        cli.main()
    assert stopped == [True]
    assert signal.getsignal(signal.SIGTERM) == previous
