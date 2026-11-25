import threading
import time

from .history import import_batch, message_text
from .timestamps import format_timestamp


def chat_jid(chat):
    from neonize.proto.Neonize_pb2 import JID

    user, server = chat.split("@", 1)
    return JID(User=user, Server=server, RawAgent=0, Device=0, Integrator=0)


class WhatsApp:
    def __init__(self, data_dir, store, output):
        from neonize.client import NewClient
        from neonize.events import (
            ClientOutdatedEv,
            ConnectedEv,
            ConnectFailureEv,
            DisconnectedEv,
            HistorySyncEv,
            LoggedOutEv,
            MessageEv,
            OfflineSyncCompletedEv,
            ReceiptEv,
        )

        self.store = store
        self.output = output
        self.ready = threading.Event()
        self.connection_error = None
        self.client = NewClient(str(data_dir / "session.db"))

        @self.client.event(ReceiptEv)
        def receipt(client, event):
            from neonize.proto.Neonize_pb2 import Receipt

            try:
                source = event.MessageSource
                if source.IsGroup or source.IsFromMe:
                    return
                status = {Receipt.DELIVERED: "delivered", Receipt.READ: "read"}.get(event.Type)
                if status is None:
                    return
                chat = f"{source.Chat.User}@{source.Chat.Server}"
                for message_id, sent_at in store.record_receipt(
                    chat, event.MessageIDs, status, event.Timestamp
                ):
                    stamp = format_timestamp(sent_at, "%m-%d %H:%M")
                    when = format_timestamp(event.Timestamp, "%H:%M")
                    action = "read your message" if status == "read" else "received your message"
                    output(f"{store.label(chat)} {action} ({stamp}, ID {message_id}) at {when}.")
            except Exception as error:
                output(f"Could not process a message receipt: {error}")

        @self.client.event(ConnectedEv)
        def connected(client, event):
            self.connection_error = None
            self.ready.set()
            output("Connected. Use /contacts to find people, then /open NAME.")
            self.refresh_contacts_safely()
            threading.Thread(target=self.refresh_groups_safely, daemon=True).start()

        @self.client.event(OfflineSyncCompletedEv)
        def synced(client, event):
            self.refresh_contacts_safely()

        @self.client.event(DisconnectedEv)
        def disconnected(client, event):
            self.ready.clear()
            output("Disconnected. Sending is disabled until reconnected.")

        @self.client.event(LoggedOutEv)
        def logged_out(client, event):
            self.ready.clear()
            output("Logged out by WhatsApp. Restart to pair again.")

        @self.client.event(ClientOutdatedEv)
        def outdated(client, event):
            self.ready.clear()
            self.connection_error = (
                "WhatsApp rejected the client version (405). You are not connected. "
                "Quit with /quit, update Neonize and rebuild the container before retrying."
            )
            output(self.connection_error)

        @self.client.event(ConnectFailureEv)
        def failed(client, event):
            self.ready.clear()
            self.connection_error = (
                f"WhatsApp connection failed: {event.Message or str(event.Reason)}. "
                "You are not connected. Quit with /quit before retrying."
            )
            output(self.connection_error)

        @self.client.event(HistorySyncEv)
        def history(client, event):
            try:
                chats, added, skipped = import_batch(store, event.Data)
                self.refresh_identities()
                output(
                    f"History sync: {added} new messages across {chats} chats "
                    f"({skipped} unsupported entries skipped). Use /chats and /history."
                )
            except Exception as error:
                output(f"History sync failed: {error}")

        @self.client.event(MessageEv)
        def message(client, event):
            try:
                source = event.Info.MessageSource
                chat = f"{source.Chat.User}@{source.Chat.Server}"
                sender = "You" if source.IsFromMe else (event.Info.Pushname or source.Sender.User)
                body = message_text(event.Message)
                if not source.IsFromMe and source.Chat.Server != "g.us":
                    store.chat(chat, event.Info.Pushname)
                if store.add(
                    chat, event.Info.ID, sender, body, event.Info.Timestamp, source.IsFromMe
                ):
                    output(f"\n[{chat}] {sender}: {body}")
            except Exception as error:
                output(f"Could not process an incoming message: {error}")

    def start(self):
        def connect():
            try:
                self.client.connect()
            except Exception as error:
                self.ready.clear()
                self.connection_error = f"Connection failed: {error}. Restart the app to retry."
                self.output(self.connection_error)

        threading.Thread(target=connect, daemon=True).start()

    def send(self, chat, body):
        if not self.ready.is_set():
            raise RuntimeError(
                self.connection_error or "Not connected yet. Wait for pairing or reconnection."
            )
        response = self.client.send_message(chat_jid(chat), body)
        self.store.add(chat, response.ID, "You", body, response.Timestamp or time.time(), True)

    def refresh_contacts_safely(self):
        try:
            self.refresh_contacts()
        except Exception as error:
            self.output(f"Contact refresh failed: {error}. Retry with /contacts.")

    def refresh_contacts(self):
        if not self.ready.is_set():
            return
        contacts = []
        for contact in self.client.contact.get_all_contacts():
            if contact.JID.Server not in {"s.whatsapp.net", "lid"}:
                continue
            info = contact.Info
            saved_name = info.FullName or info.FirstName
            name = saved_name or info.BusinessName or info.PushName
            if name:
                contacts.append(
                    (f"{contact.JID.User}@{contact.JID.Server}", name, 2 if saved_name else 1)
                )
        self.store.save_contacts(contacts)
        self.refresh_identities()

    def refresh_identities(self):
        if not self.ready.is_set():
            return
        for jid in self.store.names():
            if self.store.canonical(jid) != jid:
                continue
            try:
                if jid.endswith("@s.whatsapp.net"):
                    match = self.client.get_lid_from_pn(chat_jid(jid))
                    if match.User and match.Server == "lid":
                        self.store.link_identity(jid, f"{match.User}@lid")
                elif jid.endswith("@lid"):
                    match = self.client.get_pn_from_lid(chat_jid(jid))
                    if match.User and match.Server == "s.whatsapp.net":
                        self.store.link_identity(f"{match.User}@s.whatsapp.net", jid)
            except Exception:
                continue

    def sync(self, chat, count):
        if not self.ready.is_set():
            raise RuntimeError(self.connection_error or "Not connected yet.")
        anchor = self.store.oldest(chat)
        if anchor is None:
            raise ValueError(
                "No sync anchor yet. Send or receive a message in this chat, then retry /sync. "
                "To populate all chats, unlink this terminal in your phone's Linked devices "
                "and pair again with this updated app."
            )
        message_id, from_me, timestamp = anchor
        self.client.request_history_sync(
            chat_jid(chat), message_id, bool(from_me), int(timestamp), count
        )

    def refresh_groups_safely(self):
        try:
            self.refresh_groups()
        except Exception as error:
            self.output(f"Group refresh failed: {error}. Retry with /groups.")

    def refresh_groups(self):
        if not self.ready.is_set():
            raise RuntimeError(self.connection_error or "Not connected yet.")
        groups = [
            (f"{group.JID.User}@{group.JID.Server}", group.GroupName.Name or group.JID.User)
            for group in self.client.get_joined_groups()
            if group.JID.Server == "g.us"
        ]
        self.store.import_history(groups, [])

    def stop(self):
        self.ready.clear()
        self.client.disconnect()

    def sync_recent(self):
        if not self.ready.is_set():
            raise RuntimeError(self.connection_error or "Not connected yet.")
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
