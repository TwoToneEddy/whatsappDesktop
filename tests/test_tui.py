import asyncio
import threading
from types import SimpleNamespace

import pytest
from textual.widgets import Input, RichLog

from whatsapp_terminal.store import Store
from whatsapp_terminal.tui import DiscreetUI, InfoScreen, Pairing, WhatsAppUI


@pytest.fixture(params=[WhatsAppUI, DiscreetUI])
def interface(request):
    return request.param


def demo_store():
    store = Store(":memory:")
    store.chat("123@lid", "Alice")
    store.save_contacts([("456@lid", "Bob", 2)])
    store.add("123@lid", "hello", "Alice", "Hello there", 100, False)
    return store


def test_pointer_changes_and_bells_emit_nothing(interface):
    app = interface(demo_store())
    writes = []
    app._driver = SimpleNamespace(write=writes.append)
    for shape in ("default", "pointer", "text", "wait", "grabbing"):
        app._set_pointer_shape(shape)
    app.bell()
    assert writes == []


def test_hover_stays_silent_and_buttons_still_work(monkeypatch, interface):
    async def scenario():
        app = interface(demo_store())
        async with app.run_test() as pilot:
            writes = []
            monkeypatch.setattr(app._driver, "write", writes.append)
            for selector in ("#favorites", "#all", "#search", "#composer"):
                await pilot.hover(selector)
            await pilot.click("#all")
            await pilot.pause()
            assert app.mode == "all"
            assert len(app.rows) == 2
            assert not any("\x1b]22;" in value or "\x07" in value for value in writes)

    asyncio.run(scenario())


def test_search_favorite_send_and_back(interface):
    async def scenario():
        store = demo_store()
        app = interface(store)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            assert app.query_one("#messages").size.height >= 8
            assert app.rows == [("123@lid", "Alice")]
            app.select_chat("123@lid")
            await pilot.press("ctrl+s")
            assert store.favorites() == [("123@lid", "Alice")]
            composer = app.query_one("#composer", Input)
            composer.value = "Draft for Alice"
            await pilot.press("escape")
            assert app.terminal.active is None
            await pilot.press("ctrl+f")
            await pilot.press("b", "o", "b")
            await pilot.pause()
            assert app.rows == [("456@lid", "Bob")]
            await pilot.press("enter", "enter")
            assert app.terminal.active == "456@lid"
            assert composer.value == ""
            composer.value = "Hi Bob"
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert store.history("456@lid")[0][1] == "Hi Bob"
            assert composer.value == ""
            app.select_chat("123@lid")
            assert composer.value == "Draft for Alice"
            await pilot.click("#favorites")
            assert app.rows == [("123@lid", "Alice")]
            await pilot.press("f1")
            assert isinstance(app.screen, InfoScreen)
            await pilot.press("escape")

    asyncio.run(scenario())


def test_failed_send_keeps_draft_and_recipient(interface):
    class Backend:
        ready = threading.Event()
        connection_error = None

        def send(self, chat, body):
            raise RuntimeError("Connection lost")

    async def scenario():
        store = demo_store()
        app = interface(store)
        async with app.run_test() as pilot:
            app.terminal.backend = Backend()
            app.select_chat("123@lid")
            composer = app.query_one("#composer", Input)
            composer.value = "Keep this message"
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert composer.value == "Keep this message"
            assert app.terminal.active == "123@lid"
            assert len(store.history("123@lid")) == 1
            assert not app.busy

    asyncio.run(scenario())


def test_pairing_screen_and_background_notice(interface):
    async def scenario():
        app = interface(demo_store())
        async with app.run_test(size=(100, 40)) as pilot:
            app.post_message(Pairing(b"offline-test-qr"))
            await pilot.pause()
            assert isinstance(app.screen, InfoScreen)
            app.report("Still connecting")
            await pilot.pause()
            await pilot.press("escape")

    asyncio.run(scenario())


def test_group_filter_opens_group_by_name(interface):
    async def scenario():
        store = demo_store()
        store.chat("12345@g.us", "Weekend plans")
        app = interface(store)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.click("#groups")
            assert app.rows == [("12345@g.us", "Weekend plans")]
            await pilot.click("#search")
            await pilot.press("w", "e", "e", "k")
            await pilot.press("enter", "enter")
            assert app.terminal.active == "12345@g.us"

    asyncio.run(scenario())


def test_arrow_navigation_from_search_and_composer_preserves_drafts(interface):
    async def scenario():
        store = demo_store()
        app = interface(store)
        async with app.run_test() as pilot:
            await pilot.click("#all")
            await pilot.press("ctrl+f", "down")
            assert app.focused.id == "people"
            await pilot.press("down", "enter")
            assert app.terminal.active == "456@lid"
            composer = app.query_one("#composer", Input)
            composer.value = "Bob draft"
            await pilot.press("up")
            assert app.focused.id == "people"
            await pilot.press("up", "enter")
            assert app.terminal.active == "123@lid"
            assert app.drafts["456@lid"] == "Bob draft"
            await pilot.press("ctrl+q")
        assert not app.is_running

    asyncio.run(scenario())


def test_discreet_is_compact_monochrome_and_keeps_receipts():
    async def scenario():
        store = demo_store()
        store.add("123@lid", "sent", "You", "Hello ❤️", 200, True)
        app = DiscreetUI(store, show_emoji=True)
        async with app.run_test(size=(80, 24)) as pilot:
            app.select_chat("123@lid")
            store.record_receipt("123@lid", ["sent"], "read", 300)
            app.refresh_view()
            await pilot.pause()
            assert not app.query_one("#brand").display
            assert app.query_one("#composer").size.height == 1
            assert app.query_one("#messages").size.height >= 16
            log = app.query_one("#messages", RichLog)
            rendered = "\n".join(line.text for line in log.lines)
            assert "You> Hello :red_heart: [read]" in rendered
            assert "❤️" not in rendered
            for selector in ("#messages", "#people", "#composer", "#favorite"):
                color = app.query_one(selector).styles.background
                assert color.r == color.g == color.b
            await pilot.press("f1")
            color = app.screen.query_one("#dialog").styles.background
            assert color.r == color.g == color.b

    asyncio.run(scenario())


@pytest.mark.parametrize("flag", ["--discreet", "--discrete", "--tui"])
def test_cli_selects_requested_tui(monkeypatch, flag):
    import sys
    from whatsapp_terminal import cli

    launched = []
    monkeypatch.setattr(sys, "argv", ["wat", "--demo", flag])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(WhatsAppUI, "run", lambda self: launched.append(type(self)))
    cli.main()
    assert launched == [WhatsAppUI if flag == "--tui" else DiscreetUI]


def test_cli_rejects_conflicting_modes(monkeypatch):
    import sys
    from whatsapp_terminal import cli

    monkeypatch.setattr(sys, "argv", ["wat", "--plain", "--discreet"])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
