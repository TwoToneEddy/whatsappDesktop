import asyncio

from textual.widgets import RichLog

from whatsapp_terminal.cli import Terminal
from whatsapp_terminal.store import Store
from whatsapp_terminal.tui import WhatsAppUI


def test_receipts_survive_restart_and_never_regress(tmp_path):
    path = tmp_path / "messages.db"
    store = Store(path)
    chat = "123@lid"
    store.add(chat, "one", "You", "Hello", 100, True)
    assert store.history_with_status(chat)[0][3] == "status unknown"
    assert store.record_receipt(chat, ["one", "one"], "read", 200) == [("one", 100)]
    assert store.record_receipt(chat, ["one"], "delivered", 150) == []
    assert store.record_receipt(chat, ["one"], "read", 200) == []
    assert Store(path).history_with_status(chat)[0][3] == "read"


def test_early_receipts_identity_merge_and_incoming_messages():
    store = Store(":memory:")
    phone, lid = "441234567890@s.whatsapp.net", "123@lid"
    assert store.record_receipt(phone, ["early"], "read", 200) == []
    store.record_receipt(lid, ["early"], "delivered", 150)
    store.link_identity(phone, lid)
    store.add(phone, "early", "You", "Hello", 100, True)
    assert store.history_with_status(phone)[0][3] == "read"
    store.add(lid, "incoming", "Alice", "Hi", 101, False)
    assert store.record_receipt(lid, ["incoming"], "read", 201) == []
    assert store.history_with_status(lid)[-1][3] == ""
    assert store.record_receipt("123@g.us", ["group"], "read", 201) == []


def test_plain_history_status(capsys):
    store = Store(":memory:")
    store.add("123@lid", "one", "You", "Hello", 100, True)
    terminal = Terminal(store)
    terminal.active = "123@lid"
    terminal.command("/history")
    assert "[status unknown]" in capsys.readouterr().out
    store.record_receipt("123@lid", ["one"], "delivered", 200)
    terminal.command("/history")
    assert "[delivered]" in capsys.readouterr().out


def test_tui_refreshes_status_without_new_message():
    async def scenario():
        store = Store(":memory:")
        store.add("123@lid", "one", "You", "Hello", 100, True)
        app = WhatsAppUI(store)
        async with app.run_test() as pilot:
            app.select_chat("123@lid")
            await pilot.pause()
            store.record_receipt("123@lid", ["one"], "read", 200)
            app.refresh_view()
            await pilot.pause()
            rendered = "\n".join(line.text for line in app.query_one("#messages", RichLog).lines)
            assert "Read" in rendered
            assert "Status unknown" not in rendered

    asyncio.run(scenario())
