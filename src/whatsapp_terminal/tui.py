from io import StringIO

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Input, Label, OptionList, RichLog, Static
from textual.widgets.option_list import Option

from .cli import HELP, Terminal
from .display import display_text
from .timestamps import format_timestamp


class Notice(Message):
    def __init__(self, text):
        super().__init__()
        self.text = text


class Finished(Message):
    def __init__(self, error=None, quit_requested=False):
        super().__init__()
        self.error = error
        self.quit_requested = quit_requested


class Pairing(Message):
    def __init__(self, code):
        super().__init__()
        self.code = code


class InfoScreen(ModalScreen):
    BINDINGS = [("escape", "dismiss", "Close")]
    CSS = """
    InfoScreen { align: center middle; background: #000000 65%; }
    #dialog { width: 76; max-width: 95%; height: 90%; padding: 1 2;
              background: #14242e; border: round #34d399; }
    #dialog Static { height: auto; margin-bottom: 1; }
    #dialog .qr { color: white; background: black; width: auto; }
    """

    def __init__(self, text, qr=False):
        super().__init__()
        self.text = text
        self.qr = qr

    def compose(self):
        with VerticalScroll(id="dialog"):
            yield Static(
                "Link WhatsApp · scan with your phone" if self.qr else "Keyboard & commands"
            )
            yield Static(Text(self.text), classes="qr" if self.qr else "")
            yield Button("Close · Esc", id="close-info")

    @on(Button.Pressed)
    def close_dialog(self):
        self.dismiss()


class WhatsAppUI(App):
    TITLE = "wat"
    BINDINGS = [
        Binding("up", "chat_list('up')", show=False),
        Binding("down", "chat_list('down')", show=False),
        ("ctrl+q", "quit", "Quit"),
        ("escape", "back", "Back"),
        ("ctrl+f", "search", "Search"),
        ("ctrl+s", "favorite", "Favorite"),
        ("ctrl+r", "sync_recent", "Sync recent"),
        ("f1", "help", "Help"),
        ("f2", "toggle_mute", "Mute/unmute"),
    ]
    CSS = """
    Screen { background: #0b141b; color: #dce7ed; }
    #brand { height: 2; padding: 0 2; background: #14242e; color: #4adeb2; text-style: bold; }
    #body { height: 1fr; }
    #sidebar { width: 32; min-width: 24; max-width: 40%; padding: 0 1; border-right: solid #263c49; }
    #search { margin-bottom: 0; }
    #filters { height: 1; }
    #filters Button { min-width: 7; height: 1; width: 1fr; padding: 0; border: none; }
    #groups { width: 100%; height: 1; padding: 0; border: none; }
    #list-note { height: 2; color: #8aa3b2; padding: 1 0 0 0; }
    #people { height: 1fr; background: #0b141b; border: none; }
    OptionList > .option-list--option-highlighted { background: #214336; color: #83f3c1; }
    #conversation { width: 1fr; padding: 0 1; }
    #chat-title { height: 2; text-style: bold; color: #f0f6fa; }
    #toolbar { height: 1; margin-bottom: 1; }
    #toolbar Button { min-width: 8; height: 1; width: 1fr; padding: 0; margin-right: 1; border: none; background: #20333f; }
    #messages { height: 1fr; border: round #263c49; padding: 0 1; background: #101e27; }
    #composer { margin-top: 1; border: tall #2e544d; }
    #status { height: 1; color: #8aa3b2; margin: 0 2; }
    #activity { height: 2; margin: 0 2; color: #8aa3b2; background: #0b141b; border: none; }
    Input { background: #14242e; }
    Input:focus { border: tall #34d399; }
    Button.-active { background: #1e5342; color: #8df4c9; }
    #mute.-muted { background: #1e5342; color: #8df4c9; }
    Footer { background: #14242e; }
    """

    def __init__(self, store, backend=None, show_emoji=False):
        super().__init__()
        self.store = store
        self.backend = backend
        self.show_emoji = show_emoji
        self.terminal = Terminal(store, backend, emit=self.report)
        self.mode = "recent"
        self.rows = []
        self.list_signature = None
        self.history_signature = None
        self.busy = False
        self.drafts = {}
        self.history_limit = 200

    def display(self, value):
        return display_text(value, self.show_emoji)

    def _set_pointer_shape(self, shape: str) -> None:
        """Avoid OSC 22 sequences that leak text and ring bells on older terminals."""
        return

    def bell(self) -> None:
        """Keep the interface silent, including invalid-input notifications."""
        return

    def favorite_label(self, saved):
        if self.show_emoji:
            return "★ Saved" if saved else "☆ Favorite"
        return "Saved" if saved else "Favorite"

    def welcome(self, log):
        log.write(Text("\nWelcome back.\n", style="bold #4adeb2"))
        log.write(
            "Pick a recent chat, save your favorites, or search for someone.\n"
            "Enter opens a highlighted person. Escape brings you back here.\n\n"
            "Recent chats update as WhatsApp syncs activity from your phone."
        )

    def write_message(self, log, sender, body, timestamp, status):
        stamp = format_timestamp(timestamp, "%d %b · %H:%M")
        log.write(
            Text(
                f"{self.display(sender)}  ·  {stamp}",
                style="bold #4adeb2" if sender == "You" else "bold #93c5fd",
            )
        )
        log.write(Text(self.display(body)))
        if status:
            log.write(
                Text(
                    status.capitalize(),
                    style={"read": "bold #4adeb2", "delivered": "#93c5fd"}.get(status, "dim"),
                )
            )
        log.write("")

    def compose(self) -> ComposeResult:
        yield Static(
            ("◉  " if self.show_emoji else "") + "wat     /     a little closer", id="brand"
        )
        with Horizontal(id="body"):
            with Vertical(id="sidebar"):
                yield Input(placeholder="Search people or chats…", id="search")
                with Horizontal(id="filters"):
                    yield Button("Recent 5", id="recent", classes="-active")
                    yield Button(self.favorite_label(True), id="favorites")
                    yield Button("All", id="all")
                yield Button("Groups", id="groups")
                yield Label("Latest synced activity", id="list-note")
                yield OptionList(id="people")
            with Vertical(id="conversation"):
                yield Static("Your conversations", id="chat-title")
                with Horizontal(id="toolbar"):
                    yield Button(self.favorite_label(False), id="favorite")
                    yield Button("Older", id="older")
                    yield Button("Contacts", id="refresh")
                    yield Button("Mute", id="mute")
                yield RichLog(id="messages", wrap=True, markup=False, highlight=False)
                yield Input(
                    placeholder="Select a chat to start · /help for commands", id="composer"
                )
        yield Static("Offline demo" if not self.backend else "Connecting to WhatsApp…", id="status")
        yield RichLog(id="activity", wrap=True, max_lines=200, markup=False, highlight=False)
        yield Footer()

    def on_mount(self):
        self.refresh_view()
        self.set_interval(1, self.refresh_view)
        self.query_one("#search", Input).focus()
        if self.backend:
            self.backend.on_qr = lambda code: self.post_message(Pairing(code))
            self.backend.start()

    def report(self, text):
        self.post_message(Notice(self.display(text)))

    def on_notice(self, event):
        text = event.text
        if text.startswith("\n["):
            text = "New message received."
        self.query_one("#activity", RichLog).write(Text(text))
        self.refresh_view()

    def on_pairing(self, event):
        import segno

        buffer = StringIO()
        segno.make_qr(event.code).terminal(out=buffer, compact=True)
        if isinstance(self.screen, InfoScreen) and self.screen.qr:
            self.pop_screen()
        self.push_screen(InfoScreen(buffer.getvalue(), qr=True))

    def refresh_view(self):
        if isinstance(self.screen, InfoScreen):
            if self.backend and self.backend.ready.is_set() and self.screen.qr:
                self.pop_screen()
            else:
                return
        query = self.query_one("#search", Input).value.casefold().strip()
        favorites = dict(self.store.favorites())
        if self.mode == "groups":
            rows = [row for row in self.store.chats() if row[0].endswith("@g.us")]
        elif query or self.mode == "all":
            rows = sorted(self.store.names().items(), key=lambda row: row[1].casefold())
        elif self.mode == "favorites":
            rows = list(favorites.items())
        else:
            rows = self.store.recent(5)
        rows = [row for row in rows if query in " ".join(row).casefold()]
        chat = self.store.canonical(self.terminal.active)
        history = (
            self.store.history_with_status(chat, self.history_limit, mark_seen=True) if chat else []
        )
        unread = self.store.unread_counts()
        muting = getattr(self.backend, "muting", None)
        muted_chats = frozenset(jid for jid, name in rows if muting and muting.is_muted(jid))
        signature = (rows, tuple(favorites), self.terminal.active, muted_chats, unread)
        if signature != self.list_signature:
            people = self.query_one("#people", OptionList)
            previous = (
                self.rows[people.highlighted][0]
                if (people.highlighted is not None and people.highlighted < len(self.rows))
                else None
            )
            self.rows = rows
            people.clear_options()
            people.add_options(
                [
                    Option(
                        Text(
                            f"{'* ' if unread.get(jid) else '  '}"
                            f"{'[Muted] ' if jid in muted_chats else ''}"
                            f"{'[Fav] ' if jid in favorites else ''}{self.display(name)}"
                            f"{f' ({unread[jid]} new)' if unread.get(jid) else ''}",
                            style="bold" if jid == self.terminal.active or unread.get(jid) else "",
                        ),
                        id=jid,
                    )
                    for jid, name in rows
                ]
            )
            if rows:
                people.highlighted = next(
                    (index for index, row in enumerate(rows) if row[0] == previous), 0
                )
            self.list_signature = signature
        self.query_one("#list-note", Label).update(
            f"{len(rows)} matches · {'groups' if self.mode == 'groups' else 'all contacts'}"
            if query
            else {
                "recent": "Recent 5 · latest synced activity",
                "favorites": "Favorites · saved on this device",
                "all": f"All people & chats · {len(rows)}",
                "groups": f"Groups · {len(rows)}",
            }[self.mode]
        )
        chat = self.store.canonical(self.terminal.active)
        self.terminal.active = chat
        name = self.display(self.store.label(chat)) if chat else "Your conversations"
        self.query_one("#chat-title", Static).update(Text(name))
        self.query_one("#favorite", Button).label = self.favorite_label(chat in favorites)
        mute = self.query_one("#mute", Button)
        mute.disabled = (
            not self.backend or not chat or not chat.endswith(("@lid", "@s.whatsapp.net"))
        )
        muted = (
            self.backend and hasattr(self.backend, "muting") and self.backend.muting.enabled(chat)
        )
        mute.label = "Unmute" if muted else "Mute"
        mute.set_class(bool(muted), "-muted")
        history_signature = (chat, history)
        if history_signature != self.history_signature:
            log = self.query_one("#messages", RichLog)
            at_bottom = log.is_vertical_scroll_end
            scroll = log.scroll_y
            switched = self.history_signature is None or self.history_signature[0] != chat
            log.clear()
            if not chat:
                self.welcome(log)
            elif not history:
                log.write("No saved messages yet. Send a message or wait for phone history sync.")
            for sender, body, timestamp, status in history:
                self.write_message(log, sender, body, timestamp, status)
            if switched or at_bottom:
                log.scroll_end(animate=False)
            else:
                log.scroll_to(y=scroll, animate=False)
            self.history_signature = history_signature
        state = (
            "DEMO · nothing is sent"
            if not self.backend
            else (
                "Connected"
                if self.backend.ready.is_set()
                else self.backend.connection_error or "Connecting / waiting for your phone"
            )
        )
        self.query_one("#status", Static).update(
            Text("Working…" if self.busy else self.display(state))
        )

    @on(Input.Changed, "#search")
    def search_changed(self):
        self.refresh_view()

    @on(Input.Submitted, "#search")
    def search_submitted(self):
        self.query_one("#people", OptionList).focus()

    @on(OptionList.OptionSelected, "#people")
    def choose_person(self, event):
        if self.busy:
            return
        self.select_chat(event.option.id)

    def select_chat(self, chat):
        composer = self.query_one("#composer", Input)
        if self.terminal.active:
            self.drafts[self.terminal.active] = composer.value
        self.terminal.active = chat
        if chat:
            self.store.chat(chat)
        composer.value = self.drafts.get(chat, "")
        composer.placeholder = (
            "Write a message… Enter to send · /help" if chat else "Select a chat to start"
        )
        self.refresh_view()
        (composer if chat else self.query_one("#people", OptionList)).focus()

    @on(Button.Pressed)
    def button_pressed(self, event):
        button = event.button.id
        if button in {"recent", "favorites", "all", "groups"}:
            self.mode = button
            self.query_one("#search", Input).value = ""
            for mode in ("recent", "favorites", "all", "groups"):
                self.query_one(f"#{mode}", Button).set_class(mode == button, "-active")
            self.refresh_view()
            if button == "groups" and self.backend:
                self.submit("/groups", clear=False)
        elif button == "favorite":
            self.action_favorite()
        elif button == "older":
            self.submit("/sync 50", clear=False)
        elif button == "refresh":
            self.submit("/contacts", clear=False)
        elif button == "mute":
            self.action_toggle_mute()

    @on(Input.Submitted, "#composer")
    def composer_submitted(self, event):
        self.submit(event.value)

    def submit(self, line, clear=True):
        if self.busy or not line.strip():
            return
        if line.strip() == "/help":
            self.action_help()
            return
        if line.split()[0] == "/history":
            try:
                count = int(line.partition(" ")[2] or "200")
                if not 1 <= count <= 1000:
                    raise ValueError("History count must be between 1 and 1000.")
                self.history_limit = count
                if clear:
                    self.query_one("#composer", Input).value = ""
                self.refresh_view()
            except ValueError as error:
                self.report(f"Error: {error}")
            return
        self.busy = True
        self.query_one("#composer", Input).disabled = True
        self.execute(line, clear)

    @work(thread=True, exclusive=True)
    def execute(self, line, clear):
        try:
            keep_running = self.terminal.command(line)
        except Exception as error:
            self.post_message(Finished(str(error)))
        else:
            self.call_from_thread(self.command_succeeded, clear)
            self.post_message(Finished(quit_requested=not keep_running))

    def command_succeeded(self, clear):
        if clear:
            self.query_one("#composer", Input).value = ""

    def on_finished(self, event):
        self.busy = False
        self.query_one("#composer", Input).disabled = False
        if event.error:
            self.report(f"Error: {event.error} · draft kept")
        if event.quit_requested:
            self.exit()
        else:
            self.refresh_view()
            self.query_one("#composer", Input).focus()

    def action_back(self):
        if not self.busy:
            self.select_chat(None)

    def action_search(self):
        self.query_one("#search", Input).focus()

    def action_chat_list(self, direction):
        if self.busy or isinstance(self.screen, InfoScreen):
            return
        people = self.query_one("#people", OptionList)
        if self.focused is people:
            if direction == "up":
                people.action_cursor_up()
            else:
                people.action_cursor_down()
        else:
            people.focus()
            if people.highlighted is None and self.rows:
                people.highlighted = 0

    def action_favorite(self):
        if not self.busy and self.terminal.active:
            self.store.toggle_favorite(self.terminal.active)
            self.refresh_view()

    def action_sync_recent(self):
        self.submit("/sync-recent", clear=False)

    def action_toggle_mute(self):
        chat = self.terminal.active
        if (
            self.busy
            or isinstance(self.screen, InfoScreen)
            or not self.backend
            or not hasattr(self.backend, "muting")
            or not chat
            or not chat.endswith(("@lid", "@s.whatsapp.net"))
        ):
            return
        command = "/unmute" if self.backend.muting.enabled(chat) else "/mute"
        self.submit(command, clear=False)

    def action_help(self):
        self.push_screen(
            InfoScreen(
                "Up/Down: focus/browse chats · Enter: select/send · Esc: back\n"
                "Tab/Shift+Tab: move focus · Left/Right: edit text\n"
                "Ctrl+F: search · Ctrl+S: favorite · Ctrl+R: sync Recent 5\n"
                "F2: mute/unmute selected chat · Ctrl+Q: quit\n\n" + HELP
            )
        )


class DiscreetUI(WhatsAppUI):
    CSS = (
        WhatsAppUI.CSS
        + """
    Screen { background: #101010; color: #cccccc; }
    #brand { display: none; }
    #sidebar { width: 28; padding: 0 1 0 0; border-right: solid #505050; }
    #conversation { padding: 0 0 0 1; }
    #chat-title { height: 1; color: #dddddd; }
    #list-note { height: 1; padding: 0; color: #999999; }
    #toolbar { margin-bottom: 0; }
    #messages { border: none; padding: 0; background: #101010; }
    #people { background: #101010; color: #cccccc; }
    #people > .option-list--option-highlighted,
    #people > .option-list--option-hover-highlighted {
        background: #303030; color: #ffffff; text-style: bold;
    }
    Input, #composer, #search {
        height: 1; border: none; padding: 0; margin: 0;
        background: #202020; color: #dddddd;
    }
    Input:focus { border: none; background: #303030; }
    Input > .input--placeholder { color: #999999; }
    Input > .input--cursor { background: #cccccc; color: #101010; }
    Input > .input--selection { background: #505050; }
    Button, #toolbar Button, #filters Button, #groups {
        background: #101010; color: #aaaaaa; border: none;
        text-style: none; tint: transparent;
    }
    Button:hover, Button:focus, Button.-active, #mute.-muted,
    #toolbar Button:hover, #toolbar Button:focus, #filters Button.-active,
    #groups.-active, #groups:hover, #groups:focus {
        background: #303030; color: #ffffff; text-style: bold; tint: transparent;
    }
    #status, #activity { margin: 0; color: #999999; background: #101010; }
    #activity { height: 1; }
    Footer, FooterKey { background: #101010; color: #aaaaaa; }
    FooterKey:hover { background: #303030; }
    FooterKey > .footer-key--key { background: #303030; color: #dddddd; }
    FooterKey > .footer-key--description { color: #aaaaaa; }
    InfoScreen #dialog { background: #101010; border: solid #777777; color: #cccccc; }
    InfoScreen #dialog Button { height: 1; background: #303030; color: #dddddd; }
    * { scrollbar-color: #666666; scrollbar-color-hover: #888888;
        scrollbar-color-active: #aaaaaa; scrollbar-background: #101010;
        scrollbar-background-hover: #101010; scrollbar-background-active: #101010; }
    """
    )

    def __init__(self, store, backend=None, show_emoji=False):
        super().__init__(store, backend, show_emoji=False)

    def welcome(self, log):
        log.write("Select a chat. /help for commands. Ctrl+Q to quit.")

    def write_message(self, log, sender, body, timestamp, status):
        stamp = format_timestamp(timestamp, "%d %b %H:%M")
        line = Text(f"[{stamp}] {self.display(sender)}> ")
        line.append(self.display(body))
        if status:
            line.append(f" [{status}]", style="dim")
        log.write(line)
