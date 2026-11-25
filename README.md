# wat — WhatsApp in your terminal

A small Python terminal client managed with **uv**. Scan a QR once, select a
chat, and send/receive text without running a browser. A full-screen interface
provides a searchable sidebar, favorites, Recent 5, a scrolling conversation pane,
and a message composer. Sessions, contacts, favorites, and message history persist.

This is an unofficial linked-device client, not WhatsApp Web in a terminal and
not the WhatsApp Business API. It needs an existing WhatsApp account and a phone
for pairing. Use it only if you accept the compatibility and account risks below.

## System requirements

| Component | Requirement |
| --- | --- |
| Native runtime | Python 3.11 or newer and uv; install dependencies from `uv.lock`. |
| Native libraries | System libmagic and a platform/glibc compatible with Neonize's bundled shared library. Linux x86-64 is the primary target; other platforms are not validated by this project. |
| Container alternative | Linux host with working Podman or Docker and Bash. Host Python, uv, and libmagic are not needed for this route. The image supplies Python 3.13 and Debian Bookworm libraries. |
| Terminal | Interactive UTF-8 terminal; at least 80 columns × 24 rows for the full-screen interface. SSH terminals work; QR pairing needs enough space to show the whole code. |
| Account/network | Existing WhatsApp account, phone for linking/history requests, and outbound Internet access for live operation. |
| Storage | Writable private data directory for SQLite databases; additional disk space for dependencies or container images and build caches. |

No browser, graphical desktop, GPU, or separate database server is required.
There is no measured minimum RAM/CPU specification or published bandwidth
benchmark. Resource use depends on contact count, retained messages, and history
batch sizes. Large syncs can temporarily increase memory, CPU, and disk activity;
the message database grows with retained history and has no automatic size cap.
`--plain` avoids the full-screen rendering work, but uses the same backend and
storage, so it does not inherently reduce WhatsApp network traffic. Containers
add image/cache disk overhead and still require a compatible host kernel/runtime;
they replace user-space libraries, not the host kernel.

## Architecture

```text
Interactive terminal
  ├─ Textual full-screen UI (default)
  └─ prompt-toolkit command prompt (--plain)
               │
       Shared command handling
          ┌────┴───────────┐
          │                │
     SQLite Store     WhatsApp adapter
     messages.db           │
                     Neonize (Python + native library)
                           │
                       whatsmeow
                           │
                   WhatsApp linked-device service
                     session.db (credentials/state)
```

| Module under `src/whatsapp_terminal/` | Responsibility |
| --- | --- |
| `cli.py` | Entry point, flags, shared slash commands, name selection, and plain interface. |
| `tui.py` | Textual layout, sidebar, drafts, history pane, QR display, and background command workers. |
| `backend.py` | Neonize lifecycle/events, sending, contact/group refresh, identity mapping, and history requests. |
| `history.py` | Converts history protobufs into chats/messages and extracts text or media placeholders. |
| `store.py` | SQLite persistence, deduplication, favorites, recent activity, and confirmed phone-number/LID merging. |
| `timestamps.py` | Normalizes seconds/milliseconds and formats timestamps defensively. |
| `display.py` | Display-only emoji conversion and terminal-control sanitization. |
| `diagnostics.py` | Actionable native-backend startup errors. |

The connection runs in a background thread. Backend callbacks import live and
historical messages into the store, then report activity to the active interface.
The shared SQLite connection is protected by a reentrant lock. The full-screen
UI runs commands in workers and refreshes its local view every second; that
timer does **not** poll WhatsApp over the network.

Sending checks connection readiness, calls the backend, and saves the returned
message ID/timestamp after success. A successful send call is not a delivery/read
receipt. There is no offline outbox or application-level automatic send retry.
History is deduplicated by chat and message ID; favorites and recent activity are
local data. Both interfaces share the same account, databases, and commands.

## Network traffic

The app has no application HTTP server, requires no published inbound port, and
adds no analytics/telemetry integration of its own. WhatsApp connectivity is
handled by Neonize/whatsmeow. Exact endpoints and protocol traffic are controlled
by those dependencies and WhatsApp, not by this project's UI.

| Operation | Traffic behavior |
| --- | --- |
| First install/container build | Downloads Python packages, and for containers the base image and Debian packages. This is separate from WhatsApp messaging traffic; cached rebuilds avoid many downloads. |
| Connect/reconnect | Establishes the linked-device connection and can receive offline messages, contact updates, and history batches. The app also fetches joined groups after connection. |
| Idle connection | Not zero traffic: the protocol maintains its connection and can deliver events or reconnect. No fixed bytes/hour figure has been measured. |
| Send/receive | Text plus protocol/encryption overhead; group activity and messages in unselected chats can still arrive. Selecting a single chat does not restrict synchronization to it. |
| History sync | Initial history and `/sync` responses may be the largest runtime bursts. `/sync-recent` makes up to five requests for 50 older messages each; these are message counts, not byte limits or delivery guarantees. |
| Local browsing | `/history`, cached chat lists, search, and favorites read local storage without requesting older messages. `/groups` refreshes from the service; `/contacts` reads the backend's available contact store. |
| Attachments | The application does not explicitly download attachment bodies. Captions/placeholders and history/protocol data still consume bandwidth. |
| Demo | No WhatsApp connection. `uv` or a container build may still need network access to prepare dependencies before the demo starts. |

There is no metered-network mode, bandwidth cap, or setting to disable all
automatic history delivery. On a constrained connection, avoid repeated history
requests and re-pairing, reuse installed dependencies/images, and quit when not
needed. For actual usage, measure the process/container on your own host across
idle, normal messaging, and initial sync separately. Container network statistics
(where supported by the runtime) exclude image-build downloads; host interface
counters may include unrelated applications. No numerical bandwidth savings
versus WhatsApp Web are claimed.

## Run

Requires Python 3.11+, [uv](https://docs.astral.sh/uv/), and the system **libmagic**
library required by Neonize (`libmagic1` on Debian/Ubuntu; `libmagic` on macOS
via Homebrew). Neonize also needs a supported platform for its bundled native
library; Linux x86-64 is the primary target.

```sh
uv sync --locked
uv run wat
```

On your phone, open WhatsApp → Settings → Linked devices → Link a device and
scan the terminal QR. Keep the terminal wide enough to display the entire QR.
Future launches reuse the session.

## Full-screen interface

Both interfaces are available:

```sh
uv run wat --tui          # Full-screen (also the default)
uv run wat --plain        # Basic command interface
uv run wat --tui --emoji  # Full-screen with emoji glyphs enabled
```

Both interfaces display emoji as text names by default (for example,
`:red_heart:`), and use plain labels for decorative icons. This applies to chat
names, messages, and activity notices. Original messages are preserved in storage
and when sending; the composer shows exactly what you type. Literal emoticons
such as `:)` stay as text and are never converted to pictures. The basic interface
always uses text names, even with `--emoji`; that flag only affects the full-screen interface.

For the container launcher, the same flags follow the script name:

```sh
WAT_SELINUX_LABEL=disable bash scripts/wat-container.sh
WAT_SELINUX_LABEL=disable bash scripts/wat-container.sh --plain
WAT_SELINUX_LABEL=disable bash scripts/wat-container.sh --emoji
```

The new interface is the default. Use a terminal at least 80 columns by 24 rows.
The TUI disables terminal bells and mouse-pointer shape escape sequences for
compatibility with older terminals (avoiding hover beeps and stray `22;default`
text). Mouse clicks, hover highlighting, and scrolling remain enabled.
Click a person in the sidebar or use Tab, arrow keys, and Enter. Search looks
through all known people and chats, even when Recent 5 is selected. Up/Down from
search or the composer focuses the chat list; further arrows move the highlight,
and Enter opens the highlighted chat. Left/Right still edit text in input fields.
Tab/Shift+Tab moves between controls. Type a message
in the composer and press Enter to send. Failed sends keep your draft; switching
chats preserves unsent drafts until you quit.

- **Recent 5** shows the five most recently active chats known from phone history
  batches and messages. Simply opening a contact does not make it recent.
- **Saved** shows your favorites. Click **Favorite** or press **Ctrl+S** to
  toggle the selected chat. Favorites are local to this app, independent of phone
  pins or WhatsApp's own favorites.
- **All** shows all known contacts and chats. **Contacts** refreshes contact names.
- **Older** requests 50 older messages for the current conversation.
- **Ctrl+R** requests 50 older messages for each of the Recent 5 chats. Your phone
  must be online and each chat needs a known message anchor. Results arrive later.
- **Escape** returns to the list without a selected chat. **Ctrl+F** focuses search,
  **F1** opens help, and **Ctrl+Q** quits. Mouse wheel scrolls message history.

Recent 5 follows the latest activity WhatsApp has supplied, not the phone's exact
pinned/archived ordering. It updates automatically when more history or live
messages arrive. It cannot discover unsynced chats by itself; see Phone history
sync below if your initial history was delivered before this feature was enabled.

Slash commands still work in the composer, including `/favorite [name]`,
`/favorites`, `/recent`, `/sync-recent`, and `/back`. `/history 1000` expands the
conversation pane to show up to 1,000 saved messages (default 200). Use
`uv run wat --plain` for the original line interface; container users can append
`--plain` to the launcher command.

```text
/open +441234567890
Hello from my terminal!
/chats
/chats Alice
/history 50
/open 123456789-123456789@g.us
/send /this starts with a slash
/quit
```

Use a full international number, without a national trunk prefix. `/chats`
lists chats imported from phone sync, observed while running, or manually opened; copy a JID
from that list to select a group. `/help` lists commands. In `--plain` mode, Tab
completes command names, Ctrl-C cancels input, and Ctrl-D quits; failed text is
available through the Up-arrow input history. Messages are never automatically retried.

Try the interface without a phone or native backend:

```sh
uv run wat --demo
```

Demo data lives only in memory and never connects to WhatsApp.

## Contacts and names

`/contacts` refreshes the address book available to this linked device and lists
saved contact names. Search and select without knowing a number:

```text
/contacts Alice
/open Alice Smith
```

Partial names work when there is only one match. If several people match, the
app lists them and asks you to choose with `/open #1` (or another result number).
Numbers always refer to the most recent displayed list, including `/chats`.
Selection never sends a message; type your message after selecting the person.
The prompt shows their name. Existing phone-number and JID commands still work.

Saved phone contact names take precedence over WhatsApp profile names. Contacts
are cached locally for future launches and refreshed on connection, after
offline sync, and whenever you run `/contacts`. History-sync profile names are
also imported. Availability depends on what WhatsApp has synced to the linked
device; if the list is empty, keep your phone online and retry `/contacts` after
sync finishes. No phone filesystem access or address-book upload is required.

WhatsApp may identify one person using both a phone-number address and an internal
LID. When its local identity mapping confirms they belong together, the app merges
their contact entries, history, favorites, and recent activity. This is checked on
contact refresh and history sync. If mappings haven't arrived yet, run `/contacts`
after syncing. Matching names alone never trigger a merge; genuinely ambiguous
names still require selecting a numbered result.

## Group chats

Joined groups are fetched directly after connecting, independently of message
history. Click **Groups** in the sidebar to refresh and filter the list, or use
`/groups [name]` in either interface. Open a group by name or numbered result,
just like a contact. Groups also appear under **All** and in name searches.

Sending to a group sends to all its members; incoming messages show the sender's
name when available. Favorites and history requests work for groups too. An empty
group conversation means its history has not arrived, not that you aren't a
member. Recent 5 only includes groups with known activity timestamps. Historical
groups stay in your local database if you later leave; sending is subject to
WhatsApp membership and group permissions.

## Temporary phone mute (experimental)

For one-to-one chats, you can request a **shared WhatsApp mute with no timeout**
while you keep the chat open. This changes the chat setting on linked devices, including the
phone; it is not a terminal-only or phone-only notification filter.

- **Plain mode:** after `/open`, answer the mute question with `y`. Enter or `n`
  leaves the setting alone. You can also use `/mute` later.
- **Full-screen:** select a person and click **Mute**. The button becomes
  **Unmute** while the temporary mute is active.
- Switch chats, use `/back`, quit normally, or use `/unmute` to attempt
  restoration. `/unmute` only releases a temporary mute owned by this app; it
  does not remove an existing mute you set on your phone.

Existing active mutes are left untouched. Before restoration the app refreshes
the synced settings and only clears a still-indefinite mute it recorded as its
own. A finite mute or an already-unmuted chat is left unchanged. An indefinite
mute you manually set later is indistinguishable from the app's own mute, so
avoid changing that setting concurrently while this feature is active.

Normal exit restores the app-owned mute before disconnecting. This includes
`/quit`, plain-mode Ctrl-D, TUI Ctrl-Q, and handled SIGTERM/SIGHUP signals (such
as a normal process stop or terminal hangup). **SIGKILL, power loss, a crash, or
loss of network connectivity can leave the chat muted indefinitely.** In that
case, unmute on your phone or restart the app and let it connect. A private
`mute-recovery.json` file in the data directory records ownership before sending
the mute request; restoration is retried on connection and the record is removed
after successful restoration or detection of a changed setting. Keep this file
with your session data. Group chats and offline demo mode do not offer this feature.

This uses a synced mute app-state patch, not a local database change. Requests
and restoration add network traffic. Phone notification behavior has not been
verified by the automated tests: for your first trial, choose a normally unmuted
chat, enable the feature, check its mute setting on your phone, then use `/back`
and check that it clears. Do not rely on it for important notifications until
you have confirmed propagation on your account.

## Phone history sync

### Delivery and read status

For one-to-one chats, incoming delivery/read receipts are saved locally and
matched to outgoing message IDs. The full-screen conversation shows colored
**Delivered** and **Read** labels beneath messages and refreshes them automatically.
Plain mode prints notices identifying the contact, message ID, and send time;
`/history` includes `[delivered]` or `[read]`. Status survives restarts and does
not regress when duplicate or out-of-order receipts arrive. Group receipts are
ignored. This uses the backend's [receipt events](https://krypton-byte.my.id/neonize/dev/core-concepts/event-model/),
not polling or an extra request for every message.

**Status unknown** means no usable receipt has been recorded, not that a message
is unread or undelivered. Privacy settings and receipt availability limit what
can be shown. Existing history is not backfilled with read status; this feature
records receipts received while the updated client is running. Opening a chat
does not explicitly send a read receipt. “Sent.” still only confirms the backend
send call succeeded, not delivery to the recipient.

### Requesting older messages

History batches supplied by WhatsApp are automatically imported into the local
database, including chat names, group senders, timestamps, text, and media
captions/placeholders. Duplicate batches and overlapping live messages are
deduplicated by chat and message ID. A summary appears after each batch.

```text
/chats
/open 441234567890@s.whatsapp.net
/history 100
/sync 50
```

`/sync [count]` requests up to 500 messages preceding the oldest known message
in the selected chat (default 50). Keep WhatsApp on your phone online. Requests
return immediately; wait for a history-sync notification, then use `/history`.
Repeat `/sync` to page further back. `/history` reads the local database only.
WhatsApp determines what is available; a request is not a guarantee of a reply
or a full archive.

Already paired before this feature was added? Restart the updated app first.
If the initial history was already delivered and discarded, it may not be
replayed. For one chat, send or receive a message with the updated app to give
`/sync` an anchor. To receive initial chat lists again, quit, unlink this terminal
from WhatsApp's **Linked devices**, then restart and scan the QR to pair again.
Local history is preserved; you do not need to delete either database. Older
local records without reliable sender-direction metadata are retained, but are
not used as request anchors until matched by a history batch.

## Older Linux hosts (GLIBC errors)

If startup reports `GLIBC_2.33 not found` (or another `GLIBC_` version), the
prebuilt Neonize shared library requires a newer glibc than your OS supplies.
Changing Python versions, reinstalling with uv, or installing libmagic does not
resolve that mismatch.

With **Podman or Docker available on your host**, run from this project:

```sh
bash scripts/wat-container.sh
```

The launcher builds a Debian Bookworm image with Python 3.13, uv, libmagic, and
the locked dependencies, then starts the terminal client. The image build checks
that Neonize's native library imports successfully before allowing startup.
Your host libraries and Python environment are unchanged. First build requires
internet access; later builds reuse cached layers. This adds container disk
storage, but does not run a browser or a separate application server.

The launcher prefers Podman; select Docker with
`WAT_CONTAINER_RUNTIME=docker bash scripts/wat-container.sh`. It runs as your
user and mounts only the app data directory, preserving sessions between runs.
By default this is `${XDG_DATA_HOME:-$HOME/.local/share}/whatsapp-terminal`.
Use `WAT_DATA_DIR=/absolute/path` to override it. The `:Z` mount option applies
a private SELinux label to that directory. Do not run a native client and the
container against the same session simultaneously.

If startup fails with `lsetxattr ... operation not supported`, the data
filesystem cannot accept SELinux labels (common with network home directories).
Run with relabeling disabled:

```sh
WAT_SELINUX_LABEL=disable bash scripts/wat-container.sh
```

This removes `:Z` and uses `--security-opt label=disable`, disabling SELinux
separation for **this container only**, not for the host. User permissions and
the limited data-directory mount remain in place. The default `private` mode
keeps SELinux labeling enabled on filesystems that support it. See
[Podman's volume labeling documentation](https://docs.podman.io/en/stable/markdown/podman-run.1.html).

Arguments are forwarded, e.g. `bash scripts/wat-container.sh --demo`. If no
container runtime is available, run the app on a newer Linux host or build
Neonize's native library from matching upstream sources on your target OS.
Do not replace the host's system glibc for this app.

## Storage and scope

The data directory contains two distinct SQLite databases:

- `session.db`: managed by Neonize, containing linked-device credentials and
  protocol state. Treat it as a secret, not merely a cache.
- `messages.db`: application-owned messages, contacts, chats, favorites, identity
  aliases, and recent-chat activity. It is not encrypted at rest by this app.

Quit the app before backing up or restoring the **whole data directory** so both
databases and any SQLite sidecar files are consistent. Store backups privately;
do not commit them, upload them with bug reports, or run two copies against the
same session. Restoring files does not override a device revocation on the phone.
Deleting local files is not the same as unlinking the device: revoke it under
WhatsApp's Linked devices when retiring this client. Drafts are in-memory only
and are not restored after exit.

Session credentials and unencrypted local message history live in your OS user
data directory (`~/.local/share/whatsapp-terminal` on Linux). Override with
`--data-dir PATH`. New files are created with private permissions. Protect this
directory like your WhatsApp account; unlink the device from your phone to revoke
access. Quit before removing local data. Message text is not stored in a separate
shell command-history file.

This first version handles live text messages and media captions/placeholders.
It imports history supplied by your phone, but does not download attachments, make calls, track
group delivery/read receipts, or implement disappearing-message deletion. Messages received
while running are retained locally until you remove the message database.

Connectivity uses [Neonize](https://github.com/krypton-byte/neonize), an unofficial
WhatsApp client built on whatsmeow. Protocol changes can break connectivity, and
unofficial clients carry account restriction risk. No business API credentials
are needed.

## Development

The lockfile uses Neonize 0.12.4. Older releases can be rejected by WhatsApp
with `err-client-outdated` / error 405 before a QR appears. The terminal banner
only means the interface is running; wait for the `Connected` message to confirm
login. After updating this project, rerun the container launcher to rebuild from
the updated lockfile; there is no need to delete your session database.

```sh
uv run pytest
uv run ruff check .
uv run ruff format --check src tests
```

Tests exercise offline behavior without an account or sending real messages.
They cover storage, commands, history parsing, identity merging, timestamp
normalization, display behavior, headless UI interaction, and launcher arguments.
They do not prove live protocol compatibility or replace testing native backend
loading on the target host. The container build performs an import smoke test,
not a WhatsApp login test.

When reporting a bug, include the command used, interface mode, OS/architecture,
Python version or container runtime, and a redacted traceback. Remove phone
numbers, JIDs, message contents, QR codes, and credentials. Never attach either
database. For `GLIBC_...` or SELinux mount errors, use the container guidance above;
for empty chats, check connection status and the history-sync section before
deleting data or re-pairing.
