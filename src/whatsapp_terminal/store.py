import sqlite3
import threading
import unicodedata
from pathlib import Path

from .timestamps import MILLISECOND_THRESHOLD, timestamp_seconds


def safe_text(value: str) -> str:
    return "".join(
        char for char in value if char in "\n\t" or not unicodedata.category(char).startswith("C")
    )


def destination(value: str) -> str:
    value = value.strip()
    if "@" in value:
        user, server = value.split("@", 1)
        if server not in {"s.whatsapp.net", "g.us", "lid"} or not user.replace("-", "").isdigit():
            raise ValueError("Use an international phone number or a valid chat JID.")
        return value
    number = value.removeprefix("+").replace(" ", "").replace("-", "")
    if not number.isascii() or not number.isdigit() or not 7 <= len(number) <= 15:
        raise ValueError("Use an international number including country code, e.g. +441234567890.")
    return f"{number}@s.whatsapp.net"


class Store:
    def __init__(self, path: Path | str):
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS chats (jid TEXT PRIMARY KEY, name TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS contacts (jid TEXT PRIMARY KEY, name TEXT NOT NULL,
                                                priority INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS favorites (jid TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS aliases (alias TEXT PRIMARY KEY, jid TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS chat_activity (jid TEXT PRIMARY KEY, timestamp REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS messages (
                chat TEXT NOT NULL, id TEXT NOT NULL, sender TEXT NOT NULL,
                body TEXT NOT NULL, timestamp REAL NOT NULL, PRIMARY KEY(chat, id)
            );
            CREATE INDEX IF NOT EXISTS timeline ON messages(chat, timestamp);
            CREATE TABLE IF NOT EXISTS unread (
                chat TEXT NOT NULL, id TEXT NOT NULL, PRIMARY KEY(chat, id)
            );
            CREATE TABLE IF NOT EXISTS receipts (
                chat TEXT NOT NULL, id TEXT NOT NULL, rank INTEGER NOT NULL,
                timestamp REAL NOT NULL, PRIMARY KEY(chat, id)
            );
        """)
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(messages)")}
        if "from_me" not in columns:
            self.db.execute("ALTER TABLE messages ADD COLUMN from_me INTEGER")
            self.db.commit()
        self.db.create_function("timestamp_seconds", 1, timestamp_seconds, deterministic=True)
        with self.db:
            for table in ("messages", "chat_activity"):
                self.db.execute(
                    f"UPDATE {table} SET timestamp=timestamp_seconds(timestamp) "
                    "WHERE timestamp >= ? OR timestamp < 0",
                    (MILLISECOND_THRESHOLD,),
                )

    def chat(self, jid: str, name: str = ""):
        with self.lock, self.db:
            if name == jid:
                name = ""
            jid = self.canonical(jid)
            self.db.execute(
                "INSERT INTO chats VALUES (?, ?) ON CONFLICT(jid) DO UPDATE SET name = "
                "CASE WHEN excluded.name = excluded.jid THEN chats.name ELSE excluded.name END",
                (jid, name or jid),
            )

    def add(
        self,
        chat: str,
        message_id: str,
        sender: str,
        body: str,
        timestamp: float,
        from_me: bool | None = None,
    ) -> bool:
        with self.lock, self.db:
            chat = self.canonical(chat)
            self.chat(chat)
            added = bool(
                self.db.execute(
                    "INSERT OR IGNORE INTO messages VALUES (?, ?, ?, ?, ?, ?)",
                    (chat, message_id, sender, body, timestamp_seconds(timestamp), from_me),
                ).rowcount
            )
            if added and from_me is False:
                self.db.execute("INSERT OR IGNORE INTO unread VALUES (?, ?)", (chat, message_id))
            return added

    def unread_counts(self):
        with self.lock:
            return dict(self.db.execute("SELECT chat, COUNT(*) FROM unread GROUP BY chat"))

    def import_history(self, chats, messages):
        with self.lock, self.db:
            chats = [
                (self.canonical(jid), name if name != jid else self.canonical(jid))
                for jid, name in chats
            ]
            self.db.executemany(
                "INSERT INTO chats VALUES (?, ?) ON CONFLICT(jid) DO UPDATE SET name = "
                "CASE WHEN excluded.name = excluded.jid THEN chats.name ELSE excluded.name END",
                chats,
            )
            inserted = 0
            for row in messages:
                row = (self.canonical(row[0]), *row[1:4], timestamp_seconds(row[4]), row[5])
                inserted += self.db.execute(
                    "INSERT OR IGNORE INTO messages VALUES (?, ?, ?, ?, ?, ?)", row
                ).rowcount
                self.db.execute(
                    "UPDATE messages SET from_me=? WHERE chat=? AND id=? AND from_me IS NULL",
                    (row[5], row[0], row[1]),
                )
            return inserted

    def oldest(self, chat):
        with self.lock:
            return self.db.execute(
                "SELECT id, from_me, timestamp FROM messages "
                "WHERE chat=? AND from_me IS NOT NULL AND timestamp > 0 "
                "ORDER BY timestamp, rowid LIMIT 1",
                (self.canonical(chat),),
            ).fetchone()

    def chats(self):
        with self.lock:
            return self.db.execute(
                "SELECT c.jid, COALESCE(p.name, c.name) FROM chats c "
                "LEFT JOIN contacts p ON p.jid=c.jid LEFT JOIN messages m ON m.chat=c.jid "
                "LEFT JOIN chat_activity a ON a.jid=c.jid "
                "GROUP BY c.jid ORDER BY MAX(COALESCE(MAX(m.timestamp), 0), "
                "COALESCE(a.timestamp, 0)) DESC, c.name"
            ).fetchall()

    def record_activity(self, rows):
        with self.lock, self.db:
            self.db.executemany(
                "INSERT INTO chat_activity VALUES (?, ?) ON CONFLICT(jid) DO UPDATE SET "
                "timestamp=MAX(chat_activity.timestamp, excluded.timestamp)",
                [(self.canonical(jid), timestamp_seconds(timestamp)) for jid, timestamp in rows],
            )

    def recent(self, limit=5):
        with self.lock:
            active = {
                row[0]
                for row in self.db.execute(
                    "SELECT chat FROM messages WHERE timestamp > 0 UNION "
                    "SELECT jid FROM chat_activity WHERE timestamp > 0"
                )
            }
            return [row for row in self.chats() if row[0] in active][:limit]

    def toggle_favorite(self, jid):
        with self.lock, self.db:
            jid = self.canonical(jid)
            if self.db.execute("DELETE FROM favorites WHERE jid=?", (jid,)).rowcount:
                return False
            self.db.execute("INSERT INTO favorites VALUES (?)", (jid,))
            return True

    def favorites(self):
        with self.lock:
            names = self.names()
            return sorted(
                [
                    (row[0], names.get(row[0], row[0]))
                    for row in self.db.execute("SELECT jid FROM favorites")
                ],
                key=lambda row: row[1].casefold(),
            )

    def history(self, chat: str, limit: int = 30):
        with self.lock:
            return list(
                reversed(
                    self.db.execute(
                        "SELECT sender, body, timestamp FROM messages WHERE chat=? "
                        "ORDER BY timestamp DESC, rowid DESC LIMIT ?",
                        (self.canonical(chat), limit),
                    ).fetchall()
                )
            )

    def record_receipt(self, chat, message_ids, status, timestamp):
        if status not in {"delivered", "read"} or not chat.endswith(("@lid", "@s.whatsapp.net")):
            return []
        rank = 2 if status == "read" else 1
        changed = []
        with self.lock, self.db:
            chat = self.canonical(chat)
            for message_id in dict.fromkeys(message_ids):
                updated = self.db.execute(
                    "INSERT INTO receipts VALUES (?, ?, ?, ?) ON CONFLICT(chat,id) "
                    "DO UPDATE SET rank=excluded.rank, timestamp=excluded.timestamp "
                    "WHERE excluded.rank > receipts.rank",
                    (chat, message_id, rank, timestamp_seconds(timestamp)),
                ).rowcount
                message = self.db.execute(
                    "SELECT timestamp FROM messages WHERE chat=? AND id=? AND from_me=1",
                    (chat, message_id),
                ).fetchone()
                if updated and message:
                    changed.append((message_id, message[0]))
        return changed

    def history_with_status(self, chat, limit=30, mark_seen=False):
        with self.lock, self.db:
            if mark_seen:
                self.db.execute("DELETE FROM unread WHERE chat=?", (self.canonical(chat),))
            return list(
                reversed(
                    self.db.execute(
                        "SELECT m.sender, m.body, m.timestamp, "
                        "CASE WHEN m.from_me=1 AND m.chat NOT LIKE '%@g.us' THEN "
                        "CASE r.rank WHEN 2 THEN 'read' WHEN 1 THEN 'delivered' "
                        "ELSE 'status unknown' END ELSE '' END "
                        "FROM messages m LEFT JOIN receipts r ON r.chat=m.chat AND r.id=m.id "
                        "WHERE m.chat=? ORDER BY m.timestamp DESC, m.rowid DESC LIMIT ?",
                        (self.canonical(chat), limit),
                    ).fetchall()
                )
            )

    def save_contacts(self, contacts):
        with self.lock, self.db:
            self.db.executemany(
                "INSERT INTO contacts VALUES (?, ?, ?) ON CONFLICT(jid) DO UPDATE SET "
                "name=excluded.name, priority=excluded.priority "
                "WHERE excluded.priority >= contacts.priority",
                [(self.canonical(jid), name, priority) for jid, name, priority in contacts],
            )

    def contacts(self):
        with self.lock:
            return self.db.execute("SELECT jid, name FROM contacts ORDER BY name, jid").fetchall()

    def names(self):
        return dict(self.chats()) | dict(self.contacts())

    def label(self, jid):
        return self.names().get(self.canonical(jid), jid)

    def canonical(self, jid):
        with self.lock:
            row = self.db.execute("SELECT jid FROM aliases WHERE alias=?", (jid,)).fetchone()
            return row[0] if row else jid

    def link_identity(self, phone, lid):
        if not phone.endswith("@s.whatsapp.net") or not lid.endswith("@lid"):
            raise ValueError("Expected a verified phone-number and LID pair.")
        with self.lock, self.db:
            existing = self.canonical(phone)
            if existing != phone:
                if existing != lid:
                    raise ValueError("Conflicting WhatsApp identity mapping; refusing to merge.")
                return
            self.db.execute("INSERT INTO aliases VALUES (?, ?)", (phone, lid))
            self.db.execute(
                "INSERT INTO contacts SELECT ?, name, priority FROM contacts WHERE jid=? "
                "ON CONFLICT(jid) DO UPDATE SET name=excluded.name, priority=excluded.priority "
                "WHERE excluded.priority >= contacts.priority",
                (lid, phone),
            )
            self.db.execute(
                "INSERT INTO chats SELECT ?, CASE WHEN name=? THEN ? ELSE name END FROM chats "
                "WHERE jid=? ON CONFLICT(jid) DO UPDATE SET name=excluded.name "
                "WHERE chats.name=chats.jid",
                (lid, phone, lid, phone),
            )
            self.db.execute(
                "INSERT INTO messages SELECT ?, id, sender, body, timestamp, from_me FROM messages "
                "WHERE chat=? ON CONFLICT(chat,id) DO UPDATE SET "
                "from_me=COALESCE(messages.from_me, excluded.from_me)",
                (lid, phone),
            )
            self.db.execute(
                "INSERT INTO chat_activity SELECT ?, timestamp FROM chat_activity WHERE jid=? "
                "ON CONFLICT(jid) DO UPDATE SET timestamp=MAX(chat_activity.timestamp, excluded.timestamp)",
                (lid, phone),
            )
            self.db.execute(
                "INSERT OR IGNORE INTO favorites SELECT ? FROM favorites WHERE jid=?", (lid, phone)
            )
            self.db.execute("DELETE FROM messages WHERE chat=?", (phone,))
            self.db.execute(
                "INSERT OR IGNORE INTO unread SELECT ?, id FROM unread WHERE chat=?", (lid, phone)
            )
            self.db.execute("DELETE FROM unread WHERE chat=?", (phone,))
            self.db.execute(
                "INSERT INTO receipts SELECT ?, id, rank, timestamp FROM receipts WHERE chat=? "
                "ON CONFLICT(chat,id) DO UPDATE SET rank=excluded.rank, timestamp=excluded.timestamp "
                "WHERE excluded.rank > receipts.rank",
                (lid, phone),
            )
            self.db.execute("DELETE FROM receipts WHERE chat=?", (phone,))
            for table in ("contacts", "chats", "chat_activity", "favorites"):
                self.db.execute(f"DELETE FROM {table} WHERE jid=?", (phone,))
