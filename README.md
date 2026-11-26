# wat — WhatsApp in your terminal

A uv-managed Python terminal client with three interfaces: a modern TUI,
a discreet terminal-style TUI, and a basic `--plain` prompt.
This experimental **Baileys backend branch** removes
Neonize, its glibc-linked shared library, and libmagic. No browser or container
is required when Python and Node.js run on your host.

This is an unofficial WhatsApp linked-device client, not the Business API.
Protocol changes can break it, and unofficial clients carry account restriction
risk. You need an existing WhatsApp account and a phone to pair.

## Requirements and quick start

- Linux, Python 3.11+, [uv](https://docs.astral.sh/uv/), **Node.js 22+ and npm**.
  Session locking currently uses Linux/Unix `flock`; Windows is not supported.
- An interactive UTF-8 terminal (80×24 or larger for the TUI), outbound Internet,
  and a writable private data directory. SSH works.
- No libmagic, browser, GPU, database server, or container runtime is needed.

```sh
git switch feature/baileys-backend
node --version
npm --version
uv sync --locked
uv run wat --setup-backend
uv run wat --plain
# Or:
uv run wat
```

`--setup-backend` explicitly installs the locked JavaScript dependencies with
`npm ci` into a versioned user cache, then smoke-tests the Baileys import.
Install scripts and automatic peer dependencies are disabled: optional media
packages such as sharp are not needed for text chat. The pinned Rust bridge
dependency runs as WebAssembly, not a system-linked shared library.
Normal startup does not run npm or silently install anything. Rerun setup after
updating this branch if prompted. Do not run setup while a client is active.

On your phone: **WhatsApp → Linked devices → Link a device**, then scan the QR.
The entire code must fit on screen. Wait for **Connected** before sending.
You can preview either interface without Node, setup, or an account:

```sh
uv run wat --demo
uv run wat --demo --plain
```

### Older hosts and uvx

The old container was necessary because Neonize's prebuilt shared library
required a newer glibc than your host. That library is gone on this branch.
However, **Node.js and Python themselves must still be compatible with the host
OS**. This does not promise support for every old Linux installation. Check
`node --version` on the actual host; uv/uvx do not supply Node or replace glibc.
Do not replace your system glibc for this application.

The Python wheel bundles the helper source and npm lockfile, not Node or
`node_modules`. It can be run with uvx from the checkout without publishing:

```sh
uvx --from . wat --setup-backend
uvx --from . wat --plain
```

After publication the equivalent would be
`uvx --from whatsapp-terminal wat --setup-backend`, then
`uvx --from whatsapp-terminal wat`. **This branch has not been published to
PyPI**, and availability of that distribution name has not been verified.
It is not a Python-only, zero-prerequisite uvx installation.

### Moving from Neonize

1. Quit the existing app normally, allowing it to restore its temporary mutes.
2. Back up the entire data directory while the app is stopped.
3. Switch branches, run setup, and pair this backend as a **new linked device**.

`messages.db` is unchanged: history, names, favorites, aliases, unread counts,
and receipts remain available when using the same data directory. Baileys uses
`baileys/auth/` instead of `session.db`; sessions cannot be converted. The old
`session.db` is not opened, modified, or deleted. Keep it for rollback and retire
the old linked device on your phone when appropriate.

Do not run two clients against the same data directory. This branch locks its
Baileys session against another instance; the old Neonize branch does not honor
that lock. Check any outstanding mutes on your phone before switching backends.
Live phone behavior must be revalidated on this branch even if it worked on
Neonize.

## Interfaces and commands

```sh
uv run wat                 # full-screen TUI
uv run wat --discreet      # compact monochrome terminal-style TUI
uv run wat --plain         # line-based prompt
uv run wat --emoji         # enable emoji glyphs in TUI only
uv run wat --data-dir /private/path
```

Emoji render as text names by default, including in contact names and notices.
Plain mode always uses text names. Stored/sent content is unchanged; literal
emoticons such as ` :)` stay text. Terminal control characters are sanitized.

| Command | Purpose |
| --- | --- |
| `/contacts [filter]` | List synced contacts; refresh the helper's cached names. |
| `/groups [filter]` | Fetch joined groups, even without their history. |
| `/chats [filter]` | List locally known conversations. |
| `/open NAME\|#N\|NUMBER\|JID` | Select a person or group. Numbers include country code. |
| `/back` | Deselect the chat; return to the list. |
| `/history [count]` | Show saved messages, without a network history request. |
| `/sync [count]` | Request older messages (default 50, maximum 500). |
| `/recent`, `/sync-recent` | Show Recent 5 or request 50 older messages for each. |
| `/favorite [name]`, `/favorites` | Toggle/list local favorites. |
| `/mute`, `/unmute` | Opt-in one-to-one mute until exit, or release it early. |
| `/send TEXT` | Send text beginning with a slash. |
| `/help`, `/quit` | Help, or disconnect without unlinking. |

Type ordinary text and Enter to send to the selected chat. Selection itself
never sends anything. Ambiguous names produce a numbered list; use `/open #1`
from the most recent list. Confirmed WhatsApp phone/LID mappings merge duplicate
contacts; identical names alone never trigger a merge.

**TUI:** searchable Recent 5 / Saved / All / Groups sidebar, drafts, scrollable
history, and Favorite / Older / Contacts / Mute controls. Up/Down from the
composer/search focuses the list; further arrows move selection, Enter opens
it. Tab cycles controls; Left/Right edits input. Escape goes back, Ctrl+F
searches, Ctrl+S favorites, Ctrl+R syncs recent chats, F1 helps, Ctrl+Q quits.
F2 toggles mute/unmute for the selected one-to-one chat in either TUI, without
clearing your draft. It follows the same mute ownership and exit cleanup rules
as the button; it does nothing for groups, demo mode, or no selected chat.
Bells and pointer-shape escapes are disabled for older terminals.

**Plain:** Ctrl-C cancels input; Ctrl-D or `/quit` exits. Tab completes commands.
Failed sends are not automatically retried. TUI preserves failed drafts; plain
input history lets you retrieve the text.

**Discreet TUI:** `--discreet` (also spelled `--discrete`) keeps the same
sidebar, controls, shortcuts, drafts, and backend as the modern TUI, with a
monochrome palette, flat single-line inputs, no branding banner, and compact
`[date time] sender> message [read]` transcript lines. It always uses text names
for emoji, even with `--emoji`. This is a visual style, not a privacy mode:
names, message contents, and pairing QR codes remain visible. The three mode
flags are mutually exclusive; the modern TUI remains the default. Try it offline
with `uv run wat --demo --discreet`. Container launchers forward the same flag.

The sidebar uses `*` and `(N new)` only for locally unviewed incoming messages,
`[Fav]` for favorites, and `[Muted]` for known mute state. Opening a chat clears
local unread counts, not phone unread state, and does not send a read receipt.
Old history imports do not generate new-message counts.

## History, receipts, and muting

Initial phone sync imports names, activity, text, and media captions/placeholders.
Recent 5 follows the latest supplied activity, not exact phone pins/archive
ordering. Favorites are local, separate from phone favorites.
`/sync` needs a known message ID, sender direction, and timestamp as its anchor.
Keep your phone online; requests return before batches arrive. WhatsApp decides
how much history is available: this is not a guaranteed full backup.

One-to-one outgoing delivery/read receipts persist and never regress. Plain
mode prints notices and history labels; TUI shows colored Delivered/Read labels.
Status included in synced/replayed outgoing messages is also saved, without
printing a misleading new live-receipt notice. Switching interfaces uses the
same stored status. Missed receipts can only be recovered if WhatsApp supplies
them or includes the status in a later sync; reconnecting does not guarantee it.
**Status unknown** is not proof that a message is unread. Privacy settings and
receipt availability limit visibility. Group receipts are deliberately ignored.
Sending to a group sends to all its members.

After `/open`, plain mode asks whether to mute (default **No**). In TUI use
**Mute**. This changes synced WhatsApp mute state, including your phone, rather
than just suppressing terminal output. It is not phone-only.

- Mutes have **no timeout**; switching chats or `/back` keeps them active.
- Normal exit attempts to unmute every chat muted by this app. Explicit Unmute
  releases only the selected app-owned mute.
- Existing phone mutes are preserved. Unknown/unsynced mute state blocks the
  operation rather than assuming the chat is unmuted.
- Restoration refreshes app state and only clears a still-indefinite owned mute.
  Finite changes made elsewhere are left alone; a later indefinite phone mute
  cannot be distinguished from the app's own.
- Crash, SIGKILL, power loss, or disconnection can leave mutes indefinitely.
  `mute-recovery.json` persists ownership before sending. Restart and reconnect
  to retry restoration, or unmute manually on your phone.

Test with a noncritical chat: mute, check your phone, switch chats (it should
stay muted), exit normally, and confirm it unmutes. Group muting is not offered.
Network failures can delay exit while restoration requests time out.

## Architecture

```text
Textual TUI / prompt-toolkit plain prompt
                  │
           shared Terminal commands
             ┌────┴───────┐
      SQLite Store    Python WhatsApp adapter
       messages.db          │
                      JSON-lines pipes
                            │
                  Node.js / Baileys helper
                     baileys/auth/
                            │
                 WhatsApp linked-device service
```

| Module | Responsibility |
| --- | --- |
| `cli.py`, `tui.py` | Commands, views, QR display, draft management. |
| `backend.py` | Interface-independent messaging, contacts, sync, and receipt handling. |
| `transport.py` | Explicit npm setup, versioned cache, process/session lock, request IDs/timeouts, shutdown. |
| `bridge/bridge.mjs` | Baileys socket/auth/reconnection, protocol events, cached metadata, app-state requests. |
| `history.py` | Normalized JSON message/history parsing and placeholders. |
| `store.py` | SQLite, deduplication, local unread state, receipts, favorites, identity merging. |
| `muting.py` | Backend-independent mute ownership and crash recovery. |
| `timestamps.py`, `display.py`, `diagnostics.py` | Time normalization, safe display, startup guidance. |

Python and Node communicate over private stdin/stdout pipes: no HTTP service,
listening port, or remote bridge. A dedicated response reader and separate event
worker prevent callbacks from blocking command replies. SQLite uses a reentrant
lock. The TUI's one-second refresh reads local state, not the network.

Sending saves the backend-returned message ID after success. A timeout means
**outcome unknown**: check your phone before resending to avoid duplicates.
There is no offline outbox or application-level automatic send retry.
The helper disconnects on shutdown without logging out, then exits; Python
terminates it if graceful shutdown stalls. Pairing and credentials stay local.

## Storage and resources

Linux defaults to `~/.local/share/whatsapp-terminal` (honoring XDG_DATA_HOME).
Use `--data-dir` to override. Contents:

- `messages.db`: unencrypted messages, contacts, identities, favorites, activity,
  unread counts, and receipts.
- `baileys/auth/`: secret linked-device credentials and protocol keys.
- `baileys/metadata.json`: cached contacts, chat metadata, and mute settings.
- `mute-recovery.json`: outstanding app-owned mutes.
- `baileys.lock`: prevents concurrent use of this backend's session.
- Legacy `session.db`, if present: untouched Neonize credentials for rollback.

The helper installation lives separately under
`~/.cache/whatsapp-terminal/baileys/<content-hash>/` (honoring XDG_CACHE_HOME).
It contains code/dependencies, not account credentials. Different source
versions use different cache directories. Old versions are not automatically
removed. Do not publish either data or caches in bug reports.

New account files are private. Back up the whole data directory only with the
app stopped, including SQLite sidecars. Unlink on the phone to revoke access;
deleting files is not equivalent to revoking a device. Drafts are memory-only.
Disappearing-message deletion is not implemented: retained messages remain local.

There are no measured minimum RAM/CPU figures. This branch adds a Node process;
it does not claim lower RAM than Neonize. Syncs increase memory/CPU/disk use.
History and helper caches have no automatic size cap. Plain mode saves rendering
work, not protocol traffic.

## Network traffic

No app telemetry or public inbound ports are added. Baileys controls protocol
endpoints and traffic. Installation downloads Python packages, npm packages,
and optionally container layers; this is separate from messaging traffic.

Idle connections still exchange keepalives/events and may reconnect. Connecting
can sync offline messages, contacts, history, and joined groups. Initial/full
history may cause substantial bursts. `/sync-recent` requests at most five
batches of 50 older messages, not a byte-limited transfer.

Local history/search/favorites do not request older messages. Groups refresh
from WhatsApp; mute checks resync app state, and mute changes add network traffic.
The helper does not explicitly download attachment bodies or generate URL
previews. Media metadata and history-sync payloads still consume bandwidth.
There is no bandwidth cap or metered-network mode. No bytes/hour estimate or
savings over WhatsApp Web are claimed; measure on your host.

## Optional container fallback

The launcher remains available if a compatible Node/Python runtime is not
available on your host:

```sh
bash scripts/wat-container.sh --plain
# If your filesystem cannot accept SELinux labels:
WAT_SELINUX_LABEL=disable bash scripts/wat-container.sh --plain
```

The image includes Node 22, Python, uv, and the pinned helper installation.
It no longer installs libmagic. Prefer Podman by default, or set
`WAT_CONTAINER_RUNTIME=docker`. `WAT_DATA_DIR=/absolute/path` overrides the
mounted data directory. It runs as your uid/gid and mounts only that directory.
The default private `:Z` labeling can fail on network filesystems; `disable`
turns off labeling/separation only for this container, not host SELinux.
Do not run native/container sessions concurrently. Container images were not
built in the development sandbox for this branch.

## Development and troubleshooting

```sh
uv run pytest
uv run ruff check .
uv run ruff format --check src tests
npm ci --ignore-scripts --prefix src/whatsapp_terminal/bridge
node --test tests/bridge.test.mjs
uv build
```

Baileys is pinned in `bridge/package.json` and `package-lock.json`; Python
dependencies are pinned in `uv.lock`. Change the npm lock intentionally when
upgrading. Protocol compatibility can change independently of this code.
Tests use fake sockets/processes and real Baileys message utilities, not your
account. They do not prove live pairing, delivery, history, or phone mute behavior.

- **Missing helper:** run `uv run wat --setup-backend` using working Node/npm.
- **Node GLIBC error:** Node itself is incompatible with that host; use a
  supported host/runtime or the optional container. Reinstalling uv cannot fix it.
- **405/client outdated:** update the pinned Baileys version and reinstall the
  helper. Do not delete your message history as a first remedy.
- **Logged out/bad session:** check Linked devices. To re-pair, quit, back up
  the data directory, revoke this device, and move only `baileys/auth/` aside.
  Keep `messages.db` and mute recovery records. Restore phone mutes manually
  if the old session cannot reconnect.
- **Empty chats:** wait for Connected and phone sync, then try `/contacts`
  and `/groups`. History sync availability is controlled by WhatsApp.
- **Another instance running:** close it before opening the same data directory.

For missing one-to-one receipts, launch with
\`WAT_DEBUG_RECEIPTS=1 uv run wat --plain\`. Lines prefixed
\`[receipt-debug]\` correlate sends and receipt updates using a hashed message-ID
tag, address type, direction, and match counts, without raw IDs, phone numbers,
or message bodies. Share only those diagnostic lines, not surrounding chat output.
A zero new-match count can mean an unmatched/early receipt or a duplicate.

Report OS/architecture, Node/Python versions, mode, and redacted errors. Never
include QR codes, auth files, databases, message text, phone numbers, or JIDs.

Upstream: [Baileys](https://github.com/WhiskeySockets/Baileys),
[API documentation](https://baileys.wiki/).
