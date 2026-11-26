from .store import destination
from .timestamps import timestamp_seconds


def message_text(payload):
    for _ in range(10):
        for wrapper in (
            "ephemeralMessage",
            "viewOnceMessage",
            "viewOnceMessageV2",
            "viewOnceMessageV2Extension",
            "documentWithCaptionMessage",
        ):
            if payload.get(wrapper):
                payload = payload[wrapper].get("message", {})
                break
        else:
            break
    body = payload.get("conversation") or payload.get("extendedTextMessage", {}).get("text")
    if body:
        return body
    for field in ("imageMessage", "videoMessage", "documentMessage"):
        if field in payload:
            return payload[field].get("caption") or f"[{field[:-7]}]"
    return "[Non-text message — view on your phone]"


def message_row(message):
    key = message.get("key", {})
    if not key.get("id") or not message.get("message"):
        return None
    try:
        chat = destination(key.get("remoteJid", ""))
    except ValueError:
        return None
    from_me = bool(key.get("fromMe"))
    sender = "You" if from_me else (message.get("pushName") or key.get("participant") or chat)
    return (
        chat,
        key["id"],
        sender,
        message_text(message["message"]),
        timestamp_seconds(message.get("messageTimestamp", 0)),
        from_me,
    )


def import_batch(store, data):
    contacts = []
    for entry in data.get("contacts", []):
        try:
            jid = destination(entry.get("id", ""))
        except ValueError:
            continue
        name = entry.get("name") or entry.get("notify") or entry.get("verifiedName")
        if name:
            contacts.append((jid, name, 2 if entry.get("name") else 1))
    store.save_contacts(contacts)
    chats, activity, messages = [], [], []
    skipped = 0
    for conversation in data.get("chats", []):
        try:
            chat = destination(conversation.get("id", ""))
        except ValueError:
            continue
        chats.append((chat, conversation.get("name") or conversation.get("displayName") or chat))
        activity.append(
            (
                chat,
                max(
                    timestamp_seconds(conversation.get("lastMessageRecvTimestamp", 0)),
                    timestamp_seconds(conversation.get("conversationTimestamp", 0)),
                ),
            )
        )
    for message in data.get("messages", []):
        row = message_row(message)
        if row is None:
            skipped += 1
        else:
            messages.append(row)
    added = store.import_history(chats, messages)
    store.record_activity(activity)
    return len(chats), added, skipped
