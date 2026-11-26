import argparse
import os
import signal
import sys
import time
import uuid
from pathlib import Path

from platformdirs import user_data_path
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.patch_stdout import patch_stdout

from .diagnostics import backend_error
from .display import display_text
from .store import Store, destination
from .timestamps import format_timestamp

HELP = """Commands:
  /contacts [filter]    Refresh and search WhatsApp contacts by name
  /chats [filter]       List locally known chats by name
  /groups [filter]      Refresh and list your WhatsApp groups
  /open NAME_OR_#       Open by name or # from the last list (numbers/JIDs also work)
  /history [count]      Show locally saved messages (default 30)
  /sync [count]         Request older messages from phone (default 50, max 500)
  /recent              Show the five most recently active synced chats
  /sync-recent         Request older history for those five chats
  /favorite [name]     Toggle favorite for a name or the selected chat
  /favorites           List your saved favorites
  /back                Deselect the chat
  /mute                Mute selected chat on phone until closing app
  /unmute              Restore this app's temporary mute early
  /send TEXT           Send text that begins with a slash
  /help                Show this help
  /quit                Disconnect and exit (session stays linked)
Type a message and press Enter to send to the selected chat.
Ctrl-C cancels input; Ctrl-D exits. Incoming messages appear above your prompt.
"""


def output(message):
    print(display_text(message))


class Terminal:
    def __init__(self, store, backend=None, emit=output):
        self.store = store
        self.backend = backend
        self.active = None
        self.listed = []
        self.output = emit

    @property
    def active(self):
        return self._active

    @active.setter
    def active(self, chat):
        previous = getattr(self, "_active", None)
        self._active = chat
        if previous != chat and self.backend and hasattr(self.backend, "muting"):
            self.backend.muting.select(chat)

    def list_people(self, rows):
        self.listed = rows
        for index, (jid, name) in enumerate(rows, 1):
            self.output(f"{'>' if jid == self.active else ' '} #{index}  {name}  ({jid})")

    def resolve(self, value):
        if value.startswith("#"):
            try:
                index = int(value[1:])
                if index < 1:
                    raise IndexError
                return self.listed[index - 1][0]
            except (ValueError, IndexError):
                raise ValueError("Choose a # from the most recent /contacts or /chats list.")
        try:
            return self.store.canonical(destination(value))
        except ValueError:
            pass
        if not value:
            raise ValueError("Use /open NAME or /contacts to find someone.")
        people = self.store.names()
        exact = [(jid, name) for jid, name in people.items() if name.casefold() == value.casefold()]
        matches = exact or [
            (jid, name) for jid, name in people.items() if value.casefold() in name.casefold()
        ]
        if len(matches) == 1:
            return matches[0][0]
        if matches:
            self.list_people(matches)
            raise ValueError("Multiple matches. Use /open #NUMBER from this list.")
        raise ValueError("No matching name. Use /contacts to refresh and search your contacts.")

    def command(self, line):
        if not line.strip():
            return True
        command, _, argument = line.partition(" ")
        argument = argument.strip()
        if command == "/quit":
            return False
        if command in {"/mute", "/unmute"}:
            if not self.backend:
                raise ValueError("Phone muting is unavailable in demo mode.")
            if command == "/mute":
                self.backend.muting.enable(self.active)
            else:
                if not self.active:
                    raise ValueError("Select a chat first to unmute it.")
                self.backend.muting.release(force=True, chat=self.active)
            return True
        if command == "/help":
            self.output(HELP)
        elif command == "/back":
            self.active = None
        elif command == "/recent":
            self.list_people(self.store.recent())
        elif command == "/favorites":
            self.list_people(self.store.favorites())
        elif command == "/favorite":
            chat = self.resolve(argument) if argument else self.active
            if not chat:
                raise ValueError("Select a chat or use /favorite NAME.")
            added = self.store.toggle_favorite(chat)
            self.output(
                f"{'Added to' if added else 'Removed from'} favorites: {self.store.label(chat)}"
            )
        elif command == "/sync-recent":
            if not self.backend:
                raise ValueError("History sync is unavailable in offline demo mode.")
            self.backend.sync_recent()
        elif command == "/contacts":
            if self.backend:
                try:
                    self.backend.refresh_contacts()
                except Exception as error:
                    self.output(f"Contact refresh failed: {error}. Showing saved contacts.")
            contacts = [
                row
                for row in self.store.contacts()
                if argument.casefold() in " ".join(row).casefold()
            ]
            self.list_people(contacts)
            if not contacts:
                self.output(
                    "No matching contacts yet. Keep your phone online and retry /contacts "
                    "after WhatsApp finishes syncing."
                )
        elif command == "/groups":
            if self.backend:
                try:
                    self.backend.refresh_groups()
                except Exception as error:
                    self.output(f"Group refresh failed: {error}. Showing saved groups.")
            groups = [
                row
                for row in self.store.chats()
                if row[0].endswith("@g.us") and argument.casefold() in " ".join(row).casefold()
            ]
            self.list_people(groups)
            if not groups:
                self.output("No matching groups yet. Wait for connection and retry /groups.")
        elif command == "/chats":
            chats = [
                row for row in self.store.chats() if argument.casefold() in " ".join(row).casefold()
            ]
            self.list_people(chats)
            if not chats:
                self.output("No matching local chats. Use /contacts to find someone.")
        elif command == "/open":
            self.active = self.resolve(argument)
            self.store.chat(self.active)
            self.output(f"Selected {self.store.label(self.active)}")
            self.show_history(30)
        elif command == "/history":
            count = int(argument or "30")
            if not 1 <= count <= 1000:
                raise ValueError("History count must be between 1 and 1000.")
            self.show_history(count)
        elif command == "/sync":
            if not self.active:
                raise ValueError("Select a chat first with /contacts and /open NAME.")
            count = int(argument or "50")
            if not 1 <= count <= 500:
                raise ValueError("Sync count must be between 1 and 500.")
            if not self.backend:
                raise ValueError("History sync is unavailable in offline demo mode.")
            self.backend.sync(self.active, count)
            self.output(
                "History requested. Keep your phone online; results arrive asynchronously. "
                "Use /history after the sync notification."
            )
        elif command.startswith("/") and command != "/send":
            raise ValueError("Unknown command. Use /help, or /send to send a leading slash.")
        else:
            if not self.active:
                raise ValueError("Select a chat first with /contacts and /open NAME.")
            body = argument if command == "/send" else line
            if not body.strip():
                raise ValueError("Message cannot be empty.")
            if self.backend:
                self.backend.send(self.active, body)
                self.output("Sent.")
            else:
                self.store.add(self.active, uuid.uuid4().hex, "You", body, time.time())
                self.output("Saved in demo only; no message sent.")
        return True

    def show_history(self, count):
        if not self.active:
            raise ValueError("Select a chat first with /contacts and /open NAME.")
        messages = self.store.history_with_status(self.active, count, mark_seen=True)
        for sender, body, timestamp, status in messages:
            stamp = format_timestamp(timestamp, "%m-%d %H:%M")
            suffix = f" [{status}]" if status else ""
            self.output(f"{stamp}  {sender}: {body}{suffix}")
        if not messages:
            self.output("No local history yet.")


def main():
    def shutdown(signum, frame):
        raise SystemExit(128 + signum)

    previous = {}
    try:
        for name in ("SIGTERM", "SIGHUP"):
            if hasattr(signal, name):
                signum = getattr(signal, name)
                previous[signum] = signal.signal(signum, shutdown)
        _main()
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def _main():
    parser = argparse.ArgumentParser(description="Lightweight WhatsApp terminal client")
    parser.add_argument("--demo", action="store_true", help="Try offline without an account")
    parser.add_argument(
        "--setup-backend", action="store_true", help="Install the pinned Baileys helper using npm"
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--tui", action="store_true", help="Use the full-screen interface (default)")
    modes.add_argument("--plain", action="store_true", help="Use the original line-based interface")
    modes.add_argument(
        "--discreet",
        "--discrete",
        action="store_true",
        help="Use a compact monochrome terminal-style TUI",
    )
    parser.add_argument(
        "--emoji", action="store_true", help="Show emoji glyphs in the full-screen interface"
    )
    parser.add_argument("--data-dir", type=Path, default=user_data_path("whatsapp-terminal"))
    args = parser.parse_args()
    if args.setup_backend:
        from .transport import setup_backend

        try:
            setup_backend()
        except (OSError, RuntimeError) as error:
            parser.exit(1, f"Backend setup failed: {error}\n")
        return
    if not sys.stdin.isatty():
        parser.error("An interactive terminal is required.")
    os.umask(0o077)
    backend = None
    sink = {"emit": output}

    def report(message):
        sink["emit"](message)

    if args.demo:
        store = Store(":memory:")
        store.chat("441234567890@s.whatsapp.net", "Demo chat")
        store.save_contacts([("441234567890@s.whatsapp.net", "Demo contact", 2)])
        store.add("441234567890@s.whatsapp.net", "welcome", "Demo", "Welcome to wat!", time.time())
        for index, name in enumerate(
            ["Alice Morgan", "Weekend plans", "Sam Chen", "Jo Taylor", "Book club"]
        ):
            chat = f"44123456789{index + 1}@s.whatsapp.net"
            store.chat(chat, name)
            store.save_contacts([(chat, name, 2)])
            store.add(
                chat,
                f"demo-{index}",
                name,
                [
                    "Coffee tomorrow? There’s a new place by the station.",
                    "Saturday works for everyone. See you there!",
                    "Thanks for the recommendation — I’ll check it out.",
                    "Just got back. Let’s catch up soon.",
                    "Next chapter for Thursday?",
                ][index],
                time.time() - 300 * (index + 1),
                False,
            )
            if index in (0, 2):
                store.toggle_favorite(chat)
    else:
        args.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        store = Store(args.data_dir / "messages.db")
        try:
            from .backend import WhatsApp

            backend = WhatsApp(args.data_dir, store, report)
        except (ImportError, OSError) as error:
            parser.exit(1, backend_error(error))
    if not args.plain:
        from .tui import DiscreetUI, WhatsAppUI

        interface = DiscreetUI if args.discreet else WhatsAppUI
        app = interface(store, backend, show_emoji=args.emoji)
        sink["emit"] = app.report
        try:
            app.run()
        finally:
            if backend:
                sink["emit"] = output
                output("Closing WhatsApp; restoring any app-owned mute…")
                backend.stop()
        return
    terminal = Terminal(store, backend)
    session = PromptSession(
        completer=WordCompleter(
            [
                "/contacts",
                "/groups",
                "/chats",
                "/open",
                "/history",
                "/sync",
                "/send",
                "/help",
                "/quit",
                "/favorite",
                "/favorites",
                "/recent",
                "/sync-recent",
                "/back",
                "/mute",
                "/unmute",
            ]
        )
    )
    output("wat · WhatsApp in your terminal" + (" · OFFLINE DEMO" if args.demo else ""))
    output(HELP)
    with patch_stdout():
        try:
            if backend:
                output("Connecting… On first use, scan the QR in WhatsApp → Linked devices.")
                backend.start()
            while True:
                try:
                    label = (
                        display_text(store.label(terminal.active)) if terminal.active else "no chat"
                    )
                    line = session.prompt(f"{label}> ")
                    if not terminal.command(line):
                        break
                    if (
                        backend
                        and line.split()[:1] == ["/open"]
                        and terminal.active
                        and terminal.active.endswith(("@lid", "@s.whatsapp.net"))
                    ):
                        answer = session.prompt(
                            "Mute this chat on your phone until closing the app (no timeout)? [y/N] "
                        )
                        if answer.strip().lower() in {"y", "yes"}:
                            terminal.command("/mute")
                except KeyboardInterrupt:
                    continue
                except EOFError:
                    break
                except Exception as error:
                    output(f"Error: {error}")
        finally:
            if backend:
                backend.stop()


if __name__ == "__main__":
    main()
