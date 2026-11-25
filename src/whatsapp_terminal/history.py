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
            if payload.HasField(wrapper):
                payload = getattr(payload, wrapper).message
                break
        else:
            break
    body = payload.conversation or payload.extendedTextMessage.text
    if body:
        return body
    for field in ("imageMessage", "videoMessage", "documentMessage"):
        if payload.HasField(field):
            return getattr(payload, field).caption or f"[{field[:-7]}]"
    return "[Non-text message — view on your phone]"


def import_batch(store, data):
    contacts = []
    for entry in data.pushnames:
        try:
            jid = destination(entry.ID)
        except ValueError:
            continue
        if entry.pushname:
            contacts.append((jid, entry.pushname, 1))
    store.save_contacts(contacts)
    chats = []
    messages = []
    activity = []
    skipped = 0
    for conversation in data.conversations:
        try:
            chat = destination(conversation.ID)
        except ValueError:
            skipped += len(conversation.messages)
            continue
        chats.append((chat, conversation.name or conversation.displayName or chat))
        activity.append(
            (
                chat,
                max(
                    timestamp_seconds(conversation.lastMsgTimestamp),
                    timestamp_seconds(conversation.conversationTimestamp),
                ),
            )
        )
        for item in conversation.messages:
            message = item.message
            if not message.key.ID or not message.HasField("message"):
                skipped += 1
                continue
            sender = (
                "You"
                if message.key.fromMe
                else (message.pushName or message.key.participant or message.participant or chat)
            )
            messages.append(
                (
                    chat,
                    message.key.ID,
                    sender,
                    message_text(message.message),
                    message.messageTimestamp,
                    message.key.fromMe,
                )
            )
    added = store.import_history(chats, messages)
    store.record_activity(activity)
    return len(chats), added, skipped
