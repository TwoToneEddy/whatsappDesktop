import asyncio
import sys
import threading
from types import SimpleNamespace

import pytest

from whatsapp_terminal.muting import TemporaryMute


@pytest.fixture
def muting(monkeypatch):
    monkeypatch.setattr("whatsapp_terminal.muting.time.time", lambda: 1000)
    settings = SimpleNamespace(MutedUntil=0, Found=True)
    patches, notices = [], []

    def send(chat, until):
        patches.append((chat, until))
        settings.MutedUntil = -1 if until else 0

    backend = SimpleNamespace(
        ready=threading.Event(),
        output=notices.append,
        client=SimpleNamespace(),
        mute_settings=lambda chat: settings,
        set_mute=send,
        known_mutes=lambda: {},
    )
    backend.ready.set()
    manager = TemporaryMute(backend)
    manager.select("123@lid")
    return manager, settings, patches, notices


def test_patch_and_restore(muting):
    manager, settings, patches, notices = muting
    manager.enable("123@lid")
    assert manager.enabled("123@lid")
    assert patches[0] == ("123@lid", -1)
    assert settings.MutedUntil == -1
    manager.release(force=True)
    assert patches[-1] == ("123@lid", 0)
    assert not manager.owned


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
    assert not manager.owned
    assert len(patches) == 2


def test_switch_and_back_keep_chat_muted(muting):
    manager, settings, patches, notices = muting
    manager.enable("123@lid")
    manager.release()
    assert len(patches) == 1
    manager.select("456@lid")
    manager.release()
    manager.select(None)
    manager.release()
    assert len(patches) == 1
    assert manager.enabled("123@lid")


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

    def fail(chat, until):
        raise RuntimeError("Network error")

    manager.backend.set_mute = fail
    with pytest.raises(RuntimeError):
        manager.enable("123@lid")
    assert not notices
    assert manager.owned


def test_unmute_uses_zero(muting):
    manager, settings, patches, notices = muting
    manager.enable("123@lid")
    manager.release(force=True)
    assert patches[-1] == ("123@lid", 0)


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


@pytest.mark.parametrize("discreet", [False, True])
@pytest.mark.parametrize("keyboard", [False, True])
def test_tui_mute_button_and_shortcut(muting, discreet, keyboard):
    from textual.widgets import Button, Input, OptionList

    from whatsapp_terminal.store import Store
    from whatsapp_terminal.tui import DiscreetUI, WhatsAppUI

    manager, settings, patches, notices = muting
    backend = manager.backend
    backend.muting = manager
    backend.start = lambda: None
    backend.connection_error = None
    backend.client.event = SimpleNamespace(qr=lambda callback: None)

    async def scenario():
        store = Store(":memory:")
        store.add("123@lid", "one", "You", "Hello", 100, True)
        app = (DiscreetUI if discreet else WhatsAppUI)(store, backend)
        async with app.run_test() as pilot:
            assert app.query_one("#mute", Button).disabled
            await pilot.press("f2")
            assert not patches
            app.select_chat("123@g.us")
            await pilot.press("f2")
            assert not patches
            app.select_chat("123@lid")
            composer = app.query_one("#composer", Input)
            composer.value = "Unsent draft"
            app.busy = True
            await pilot.press("f2")
            app.busy = False
            assert not patches
            await pilot.press("f1", "f2")
            assert not patches
            await pilot.press("escape")
            if keyboard:
                await pilot.press("f2")
            else:
                await pilot.click("#mute")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert manager.enabled("123@lid")
            assert composer.value == "Unsent draft"
            assert str(app.query_one("#mute", Button).label) == "Unmute"
            people = app.query_one("#people", OptionList)
            assert "[Muted]" in str(people.get_option_at_index(0).prompt)
            app.select_chat(None)
            await pilot.pause()
            assert manager.enabled("123@lid")
            assert "[Muted]" in str(people.get_option_at_index(0).prompt)
            app.select_chat("123@lid")
            await pilot.pause()
            if keyboard:
                await pilot.press("f2")
            else:
                await pilot.click("#mute")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert not manager.owned
            assert composer.value == "Unsent draft"
            assert str(app.query_one("#mute", Button).label) == "Mute"
            assert "[Muted]" not in str(people.get_option_at_index(0).prompt)

    asyncio.run(scenario())


def test_close_restores_and_prevents_new_mutes(muting):
    manager, settings, patches, notices = muting
    manager.enable("123@lid")
    manager.close()
    assert settings.MutedUntil == 0
    assert not manager.owned
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


def test_multiple_mutes_persist_and_cleanup_continues_after_failure(muting, tmp_path):
    manager, settings, patches, notices = muting
    states = {"123@lid": 0, "456@lid": 0, "789@lid": 5000}
    failed = set()

    def get_settings(chat):
        return SimpleNamespace(MutedUntil=states[chat])

    def send(chat, until):
        if chat in failed:
            raise RuntimeError("Network error")
        patches.append((chat, until))
        states[chat] = -1 if until else 0

    manager.backend.mute_settings = get_settings
    manager.backend.set_mute = send
    path = tmp_path / "mute-recovery.json"
    manager.recovery_path = path
    for chat in states:
        manager.select(chat)
        manager.enable(chat)
    assert manager.owned == {"123@lid", "456@lid"}
    assert manager.is_muted("789@lid")
    manager.release()
    assert len(patches) == 2
    failed.add("123@lid")
    manager.close()
    assert states["456@lid"] == 0
    assert states["789@lid"] == 5000
    assert manager.owned == {"123@lid"}
    restarted = TemporaryMute(manager.backend, path)
    assert restarted.pending_recovery == {"123@lid"}
    failed.clear()
    restarted.release()
    assert states["123@lid"] == 0
    assert not path.exists()


def test_legacy_recovery_file_is_supported(muting, tmp_path):
    manager, settings, patches, notices = muting
    path = tmp_path / "mute-recovery.json"
    path.write_text('{"chat": "123@lid"}')
    restored = TemporaryMute(manager.backend, path)
    assert restored.pending_recovery == {"123@lid"}


def test_explicit_unmute_only_releases_selected_chat(muting):
    from whatsapp_terminal.cli import Terminal
    from whatsapp_terminal.store import Store

    manager, settings, patches, notices = muting
    manager.backend.muting = manager
    manager.enable("123@lid")
    settings.MutedUntil = 0
    manager.select("456@lid")
    manager.enable("456@lid")
    terminal = Terminal(Store(":memory:"), manager.backend)
    terminal.active = "456@lid"
    terminal.command("/unmute")
    assert manager.owned == {"123@lid"}
