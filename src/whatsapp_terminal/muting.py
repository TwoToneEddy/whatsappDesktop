import json
import threading
import time


def mute_patch(chat, until):
    from neonize.proto.Neonize_pb2 import MutationInfo, PatchInfo
    from neonize.proto.waSyncAction.WAWebProtobufSyncAction_pb2 import MuteAction, SyncActionValue

    action = MuteAction(muted=until != 0)
    if until != 0:
        action.muteEndTimestamp = -1 if until == -1 else until * 1000
    return PatchInfo(
        Timestamp=time.time_ns(),
        Type=PatchInfo.REGULAR_HIGH,
        Mutations=[
            MutationInfo(Index=["mute", chat], Version=2, Value=SyncActionValue(muteAction=action))
        ],
    )


class TemporaryMute:
    def __init__(self, backend, recovery_path=None):
        self.backend = backend
        self.lock = threading.RLock()
        self.selected = None
        self.owned = None
        self.closing = False
        self.recovery_path = recovery_path
        if recovery_path and recovery_path.exists():
            chat = json.loads(recovery_path.read_text())["chat"]
            if not isinstance(chat, str) or not chat.endswith(("@lid", "@s.whatsapp.net")):
                raise ValueError("Invalid mute recovery file; check the chat mute on your phone.")
            self.owned = (chat, -1)

    def save_recovery(self):
        if self.recovery_path:
            if self.owned:
                temporary = self.recovery_path.with_suffix(".tmp")
                temporary.write_text(json.dumps({"chat": self.owned[0]}))
                temporary.chmod(0o600)
                temporary.replace(self.recovery_path)
            else:
                self.recovery_path.unlink(missing_ok=True)

    def close(self):
        with self.lock:
            self.closing = True
            self.release(force=True)

    def select(self, chat):
        self.selected = chat
        if self.owned and self.owned[0] != chat:
            threading.Thread(target=self.release, daemon=True).start()

    def settings(self, chat):
        from .backend import chat_jid

        if not self.backend.ready.is_set():
            raise RuntimeError("Not connected; unmute on your phone or reconnect this app.")
        self.backend.client.fetch_app_state("regular_high", False)
        return self.backend.client.chat_settings.get_chat_settings(chat_jid(chat))

    def enabled(self, chat):
        return bool(self.owned and self.owned[0] == chat)

    def enable(self, chat):
        if not chat or not chat.endswith(("@lid", "@s.whatsapp.net")):
            raise ValueError("Temporary mute is available for a selected one-to-one chat only.")
        with self.lock:
            if self.closing:
                raise RuntimeError("App is closing; mute cancelled.")
            if chat != self.selected:
                raise ValueError("Chat selection changed; select the chat and retry.")
            if self.enabled(chat):
                self.backend.output("This chat is already muted while open; there is no timeout.")
                return
            self.release()
            if self.owned:
                raise RuntimeError(
                    "Previous mute could not be restored; reconnect or unmute on your phone."
                )
            settings = self.settings(chat)
            if chat != self.selected:
                raise ValueError("Chat selection changed; mute cancelled.")
            if settings.MutedUntil > time.time() or settings.MutedUntil == -1:
                self.backend.output(
                    "Chat is already muted. Its existing setting was left unchanged."
                )
                return
            until = -1
            self.owned = (chat, until)
            self.save_recovery()
            self.backend.client.send_app_state(mute_patch(chat, until))
            self.backend.output(
                "Mute requested while this chat is open (shared with your phone, no timeout). "
                "Switch chats, /back, /unmute, or quit normally to restore. "
                "A crash or lost connection can leave it muted until recovery or manual unmute."
            )

    def release(self, force=False):
        with self.lock:
            if not self.owned:
                return
            chat, until = self.owned
            if not force and chat == self.selected:
                return
            try:
                current = self.settings(chat)
                if current.MutedUntil == -1 or current.MutedUntil >= 253402300799:
                    self.backend.client.send_app_state(mute_patch(chat, 0))
                    self.backend.output(
                        "Temporary mute removed; phone notifications restored when synced."
                    )
                else:
                    self.backend.output("Mute setting changed elsewhere; leaving it unchanged.")
                self.owned = None
                self.save_recovery()
            except Exception as error:
                self.backend.output(
                    f"Could not restore temporary mute: {error} "
                    "There is no expiry. Unmute on your phone or restart/reconnect this app."
                )
