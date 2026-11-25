import asyncio

from textual.widgets import OptionList

from whatsapp_terminal.store import Store
from whatsapp_terminal.tui import WhatsAppUI


def test_unread_persists_deduplicates_and_excludes_sent_and_history(tmp_path):
    path = tmp_path / "messages.db"
    store = Store(path)
    store.add("123@lid", "one", "Alice", "Hi", 100, False)
    store.add("123@lid", "one", "Alice", "Hi", 100, False)
    store.add("123@lid", "two", "You", "Hello", 101, True)
    store.import_history([], [("123@lid", "old", "Alice", "Old", 1, False)])
    assert Store(path).unread_counts() == {"123@lid": 1}
    store.history_with_status("123@lid", mark_seen=True)
    assert store.unread_counts() == {}
    store.add("123@lid", "three", "Alice", "New", 102, False)
    assert store.unread_counts() == {"123@lid": 1}


def test_unread_identity_merge():
    store = Store(":memory:")
    phone, lid = "441234567890@s.whatsapp.net", "123@lid"
    for chat in (phone, lid):
        store.add(chat, "one", "Alice", "Hi", 100, False)
    store.link_identity(phone, lid)
    assert store.unread_counts() == {lid: 1}
    store.history_with_status(phone, mark_seen=True)
    assert store.unread_counts() == {}


def test_sidebar_star_means_unread_not_favorite():
    async def scenario():
        store = Store(":memory:")
        store.add("123@lid", "one", "Alice", "Hi", 100, False)
        store.add("456@lid", "two", "You", "Hello", 101, True)
        store.toggle_favorite("456@lid")
        app = WhatsAppUI(store)
        async with app.run_test() as pilot:
            people = app.query_one("#people", OptionList)

            def label(chat):
                return str(people.get_option(chat).prompt)

            assert label("123@lid").startswith("* ")
            assert "(1 new)" in label("123@lid")
            assert "[Fav]" in label("456@lid")
            assert "*" not in label("456@lid")
            app.select_chat("123@lid")
            await pilot.pause()
            assert "*" not in label("123@lid")
            store.add("456@lid", "three", "Bob", "New", 102, False)
            app.refresh_view()
            await pilot.pause()
            assert label("456@lid").startswith("* [Fav]")
            store.add("123@lid", "four", "Alice", "More", 103, False)
            app.refresh_view()
            assert "123@lid" not in store.unread_counts()

    asyncio.run(scenario())
