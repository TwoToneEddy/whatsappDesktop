import asyncio
import threading
from types import SimpleNamespace

from textual.widgets import Input

from whatsapp_terminal.store import Store
from whatsapp_terminal.tui import InfoScreen, Pairing, WhatsAppUI


def demo_store():
    store = Store(":memory:")
    store.chat("123@lid", "Alice")
    store.save_contacts([("456@lid", "Bob", 2)])
    store.add("123@lid", "hello", "Alice", "Hello there", 100, False)
    return store


def test_pointer_changes_and_bells_emit_nothing():
    app = WhatsAppUI(demo_store())
    writes = []
    app._driver = SimpleNamespace(write=writes.append)
    for shape in ("default", "pointer", "text", "wait", "grabbing"):
        app._set_pointer_shape(shape)
    app.bell()
    assert writes == []


def test_hover_stays_silent_and_buttons_still_work(monkeypatch):
    async def scenario():
        app = WhatsAppUI(demo_store())
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


def test_search_favorite_send_and_back():
    async def scenario():
        store = demo_store()
        app = WhatsAppUI(store)
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


def test_failed_send_keeps_draft_and_recipient():
    class Backend:
        ready = threading.Event()
        connection_error = None

        def send(self, chat, body):
            raise RuntimeError("Connection lost")

    async def scenario():
        store = demo_store()
        app = WhatsAppUI(store)
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


def test_pairing_screen_and_background_notice():
    async def scenario():
        app = WhatsAppUI(demo_store())
        async with app.run_test(size=(100, 40)) as pilot:
            app.post_message(Pairing(b"offline-test-qr"))
            await pilot.pause()
            assert isinstance(app.screen, InfoScreen)
            app.report("Still connecting")
            await pilot.pause()
            await pilot.press("escape")

    asyncio.run(scenario())


def test_group_filter_opens_group_by_name():
    async def scenario():
        store = demo_store()
        store.chat("12345@g.us", "Weekend plans")
        app = WhatsAppUI(store)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.click("#groups")
            assert app.rows == [("12345@g.us", "Weekend plans")]
            await pilot.click("#search")
            await pilot.press("w", "e", "e", "k")
            await pilot.press("enter", "enter")
            assert app.terminal.active == "12345@g.us"

    asyncio.run(scenario())


def test_arrow_navigation_from_search_and_composer_preserves_drafts():
    async def scenario():
        store = demo_store()
        app = WhatsAppUI(store)
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
