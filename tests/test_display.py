import asyncio

import pytest
from textual.widgets import Button, Input, RichLog

from whatsapp_terminal.cli import Terminal, output
from whatsapp_terminal.display import display_text
from whatsapp_terminal.store import Store
from whatsapp_terminal.tui import WhatsAppUI


@pytest.mark.parametrize("value", ["Hello 🙂", "❤️", "👍🏽", "🇬🇧", "👩‍💻", "👨‍👩‍👧‍👦", "1️⃣"])
def test_emoji_are_text_by_default_and_optional(value):
    assert display_text(value) != value
    assert ":" in display_text(value)
    assert display_text(value, show_emoji=True) == value


def test_regular_unicode_and_literal_emoticons_remain_text():
    text = "Zoë · 你好 :) :heart:"
    assert display_text(text) == text
    assert display_text("Hi\x1b\x07🙂").startswith("Hi:")


def test_plain_output_uses_text_without_changing_messages(capsys):
    store = Store(":memory:")
    store.chat("123@lid", "Alice 🙂")
    store.add("123@lid", "one", "Alice 🙂", "Hello ❤️", 100, False)
    terminal = Terminal(store)
    terminal.command("/chats")
    terminal.command("/open #1")
    terminal.command("Thanks 👍🏽")
    terminal.command("/history")
    output("Incoming from Alice 🙂: Hello ❤️")
    rendered = capsys.readouterr().out
    assert "🙂" not in rendered
    assert "❤️" not in rendered
    assert "👍" not in rendered
    assert "Alice :slightly_smiling_face:" in rendered
    assert "Hello :red_heart:" in rendered
    assert store.label("123@lid") == "Alice 🙂"
    assert store.history("123@lid")[0][1] == "Hello ❤️"
    assert store.history("123@lid")[-1][1] == "Thanks 👍🏽"


def test_tui_emoji_display_does_not_change_stored_or_sent_text():
    async def scenario():
        store = Store(":memory:")
        store.chat("123@lid", "Alice 🙂")
        store.add("123@lid", "one", "Alice 🙂", "Hello ❤️", 100, False)
        app = WhatsAppUI(store)
        async with app.run_test() as pilot:
            app.select_chat("123@lid")
            await pilot.pause()
            rendered = "".join(line.text for line in app.query_one("#messages", RichLog).lines)
            assert "❤️" not in rendered
            assert ":red_heart:" in rendered
            assert str(app.query_one("#favorite", Button).label) == "Favorite"
            composer = app.query_one("#composer", Input)
            composer.value = "Thanks 👍🏽"
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert store.history("123@lid")[-1][1] == "Thanks 👍🏽"
            assert store.history("123@lid")[0][1] == "Hello ❤️"

    asyncio.run(scenario())
