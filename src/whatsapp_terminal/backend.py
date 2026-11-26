import io
import hashlib
import os
import threading
import time
from types import SimpleNamespace

from .history import import_batch, message_row
from .muting import TemporaryMute
from .timestamps import format_timestamp
from .transport import Bridge


class WhatsApp:
    def __init__(self, data_dir, store, output):
        self.store = store
        self.output = output
        self.ready = threading.Event()
        self.recovery_lock = threading.Lock()
        self.connection_error = None
        self.on_qr = self.print_qr
        self.bridge = Bridge(data_dir, self.handle_event)
        self.muting = TemporaryMute(self, data_dir / "mute-recovery.json")

    def print_qr(self, code):
        import segno

        buffer = io.StringIO()
        segno.make_qr(code).terminal(out=buffer, compact=True)
        self.output(buffer.getvalue())

    def start(self):
        try:
            self.bridge.start()
        except Exception as error:
            self.connection_error = f"Connection failed: {error}"
            self.output(self.connection_error)

    def connected(self):
        if not self.ready.is_set():
            raise RuntimeError(self.connection_error or "Not connected yet.")

    def handle_event(self, event):
        kind = event.get("event")
        if kind == "qr":
            self.on_qr(event["code"])
        elif kind == "connection":
            if event["state"] == "open":
                self.connection_error = None
                self.ready.set()
                self.output("Connected. Use /contacts to find people, then /open NAME.")
                self.recover_mutes()
            else:
                self.ready.clear()
                self.connection_error = event.get("error") or "Disconnected; waiting to reconnect."
                self.output(self.connection_error)
        elif kind == "notice":
            self.output(event["text"])
        elif kind == "history":
            self.save_contacts(event.get("contacts", []))
            chats, added, skipped = import_batch(self.store, event)
            self.output(
                f"History sync: {added} new messages across {chats} chats "
                f"({skipped} unsupported entries skipped)."
            )
        elif kind == "contacts":
            self.save_contacts(event["contacts"])
        elif kind == "chats":
            import_batch(self.store, {"chats": event["chats"]})
            for chat in event["chats"]:
                if "muteEndTime" in chat:
                    until = chat["muteEndTime"] or 0
                    if until > 100_000_000_000:
                        until /= 1000
                    canonical = self.store.canonical(chat["id"])
                    self.muting.known = {**self.muting.known, canonical: until}
            self.recover_mutes()
        elif kind == "identity":
            self.store.link_identity(event["pn"], event["lid"])
        elif kind == "messages":
            for message in event["messages"]:
                row = message_row(message)
                if row is None:
                    continue
                chat, message_id, sender, body, timestamp, from_me = row
                if not from_me and not chat.endswith("@g.us") and message.get("pushName"):
                    self.store.save_contacts([(chat, message["pushName"], 1)])
                if event.get("live"):
                    if self.store.add(*row):
                        self.output(f"\n[{chat}] {sender}: {body}")
                else:
                    self.store.import_history([], [row])
        elif kind == "receipt":
            chat = event["chat"]
            if not chat.endswith(("@lid", "@s.whatsapp.net")):
                return
            changed = self.store.record_receipt(
                chat, [event["message_id"]], event["status"], event["timestamp"]
            )
            if os.environ.get("WAT_DEBUG_RECEIPTS") == "1":
                tag = hashlib.sha256(event["message_id"].encode()).hexdigest()[:12]
                self.output(
                    f"[receipt-debug] {tag} stored={event['status']} "
                    f"new_outgoing_matches={len(changed)} (zero can also mean duplicate)"
                )
            for message_id, sent_at in changed:
                if event.get("historical"):
                    continue
                stamp = format_timestamp(sent_at, "%m-%d %H:%M")
                when = format_timestamp(event["timestamp"], "%H:%M")
                action = (
                    "read your message" if event["status"] == "read" else "received your message"
                )
                self.output(
                    f"{self.store.label(chat)} {action} ({stamp}, ID {message_id}) at {when}."
                )

    def recover_mutes(self):
        if not self.muting.pending_recovery or not self.ready.is_set():
            return
        if not self.recovery_lock.acquire(blocking=False):
            return

        def recover():
            try:
                self.muting.release()
            finally:
                self.recovery_lock.release()

        threading.Thread(target=recover, daemon=True).start()

    def save_contacts(self, contacts):
        rows = []
        for contact in contacts:
            chat = contact.get("id", "")
            if not chat.endswith(("@lid", "@s.whatsapp.net")):
                continue
            name = contact.get("name") or contact.get("verifiedName") or contact.get("notify")
            if name:
                rows.append((chat, name, 2 if contact.get("name") else 1))
            phone, lid = contact.get("phoneNumber"), contact.get("lid")
            if chat.endswith("@lid") and phone:
                self.store.link_identity(phone, chat)
            elif chat.endswith("@s.whatsapp.net") and lid:
                self.store.link_identity(chat, lid)
        self.store.save_contacts(rows)

    def send(self, chat, body):
        self.connected()
        response = self.bridge.request("send", chat=chat, body=body)
        self.store.add(
            chat, response["id"], "You", body, response["timestamp"] or time.time(), True
        )

    def refresh_contacts(self):
        if self.ready.is_set():
            self.save_contacts(self.bridge.request("contacts"))
            self.muting.refresh_known()

    def refresh_groups(self):
        self.connected()
        self.store.import_history(
            [(group["id"], group["subject"]) for group in self.bridge.request("groups")], []
        )

    def sync(self, chat, count):
        self.connected()
        anchor = self.store.oldest(chat)
        if anchor is None:
            raise ValueError(
                "No sync anchor yet. Wait for initial phone history, or send/receive a message."
            )
        message_id, from_me, timestamp = anchor
        self.bridge.request(
            "history",
            chat=chat,
            message_id=message_id,
            from_me=bool(from_me),
            timestamp=int(timestamp),
            count=count,
        )

    def sync_recent(self):
        self.connected()
        recent = self.store.recent(5)
        if not recent:
            self.output(
                "No recent chats synced yet. Keep your phone online for initial history sync."
            )
        for chat, name in recent:
            try:
                self.sync(chat, 50)
                self.output(f"Requested older history for {name}.")
            except Exception as error:
                self.output(f"Could not sync {name}: {error}")

    def mute_settings(self, chat):
        self.connected()
        return SimpleNamespace(MutedUntil=self.bridge.request("mute_settings", chat=chat))

    def known_mutes(self):
        self.connected()
        return {
            self.store.canonical(chat): until
            for chat, until in self.bridge.request("known_mutes").items()
        }

    def set_mute(self, chat, until):
        self.connected()
        self.bridge.request("mute", chat=chat, until=until)

    def stop(self):
        try:
            self.muting.close()
        finally:
            self.ready.clear()
            self.bridge.stop()
