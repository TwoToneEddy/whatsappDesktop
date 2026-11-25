import asyncio

import pytest

from whatsapp_terminal.store import Store
from whatsapp_terminal.timestamps import format_timestamp, timestamp_seconds
from whatsapp_terminal.tui import WhatsAppUI


@pytest.mark.parametrize("value", [1795593416, 1795593416000, 1795593416000.0])
def test_seconds_and_milliseconds_have_same_date(value):
    assert timestamp_seconds(value) == 1795593416
    assert format_timestamp(value, "%Y") == "2026"


@pytest.mark.parametrize("value", [None, "bad", float("inf"), float("nan"), -1, 1e100])
def test_invalid_timestamps_are_safe(value):
    assert timestamp_seconds(value) == 0
    assert format_timestamp(value, "%Y") == "Unknown time"


def test_all_storage_paths_normalize_timestamps():
    store = Store(":memory:")
    store.add("123@lid", "live", "Alice", "Hello", 1795593416000, False)
    store.import_history(
        [("123@lid", "Alice")], [("123@lid", "history", "You", "Earlier", 1795593400000, True)]
    )
    store.record_activity([("123@lid", 1795593416000)])
    store.chat("456@lid", "Bob")
    store.record_activity([("456@lid", 1795593500)])
    assert store.oldest("123@lid") == ("history", 1, 1795593400)
    assert [row[2] for row in store.history("123@lid")] == [1795593400, 1795593416]
    assert store.recent()[0][0] == "456@lid"


def test_saved_millisecond_data_is_repaired_once_and_opens_in_tui(tmp_path):
    path = tmp_path / "messages.db"
    store = Store(path)
    store.chat("123@lid", "Alice")
    with store.db:
        store.db.execute(
            "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?)",
            ("123@lid", "old", "Alice", "Test message", 1795593416000, False),
        )
        store.db.execute("INSERT INTO chat_activity VALUES (?, ?)", ("123@lid", 1795593416000))
    store.db.close()
    for _ in range(2):
        store = Store(path)
        assert store.history("123@lid")[0][2] == 1795593416
        assert store.db.execute("SELECT timestamp FROM chat_activity").fetchone()[0] == 1795593416
        store.db.close()

    async def scenario():
        app = WhatsAppUI(Store(path))
        async with app.run_test() as pilot:
            await pilot.click("#people")
            await pilot.press("enter")
            await pilot.pause()
            assert app.terminal.active == "123@lid"
            assert app.history_signature[1][0][2] == 1795593416

    asyncio.run(scenario())
