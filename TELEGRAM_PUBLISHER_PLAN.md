# Telegram Publisher

Implementation spec for the `telegram-publisher` branch. Adds a first-class
"publish to Telegram" feature alongside the existing Nostr publishing, designed
so additional destinations (Discord, Mastodon, Bluesky, etc.) can plug in later
without a rewrite.

## Goal

Let the user push a product update (or any markdown post) from the editor into
one or more Telegram groups / channels, as a bot, with optional scheduling.
First-run setup must be paste-token-and-go; sending must take three clicks
from a finished draft.

## Hard rule: independent of Nostr

Telegram has a vastly larger audience than Nostr. The Telegram feature must
work for a user who never opens the Nostr setup, has no bunker configured,
and never publishes an event to a relay.

Concretely:

- Nothing in `publishers/telegram/` imports from `nostr/`. Verified by a
  test that greps the package on CI.
- The `Tools > Publish to Telegram` menu is always present and enabled,
  regardless of Nostr profile state.
- The setup dialog and publish dialog never reference profiles, relays,
  bunkers, npub, signing, or any other Nostr concept.
- Settings live in their own file (`telegram.json`), not in any Nostr
  config.
- README documents Telegram as a standalone feature on equal footing with
  Nostr, not as a "you can also..." add-on.

A future user might enable Nostr later, or vice versa. Each feature stands
on its own.

## Source verification

All technical claims below were verified against the official docs:
`https://core.telegram.org/bots/api`, `/bots/features`, `/bots/faq`,
`/api/links`. Key findings that overturned earlier drafts of this plan:

| Earlier draft said | Truth from docs | Impact |
|---|---|---|
| `sendMessage` accepts `schedule_date` for server-side scheduling. | No `schedule_date` parameter exists in the Bot API. Server-side scheduling is MTProto-only. | All scheduling must be **client-side**. See the "Scheduling" section. |
| `getMessage` lets the v1.1 queue verify message state. | No such method exists in the Bot API. | The local queue is the source of truth; we cannot poll Telegram for "did it send". |
| Deeplink `t.me/<bot>?startchannel=true` adds the bot to a channel. | Only `?startchannel&admin=<permissions>` is documented. Channels reject bots without admin. | Invite helpers must include an `admin=` permission string. |
| BotFather `/token` retrieves the existing token. | `/token` **generates a new token, invalidating the old one**. Use `/mybots` to view a current token. | Add-bot dialog must instruct `/mybots > [bot] > API Token` to view; `/token` is for rotation. |
| HTML formatting set is `<b><i><s><code><pre><a><blockquote>`. | The full set also includes `<u>/<ins>`, `<tg-spoiler>`, `<blockquote expandable>`, `<tg-emoji>`, `<tg-time>`. | Formatter passes all supported tags through; escapes everything else. |
| Errors are surfaced generically. | Specific status codes drive specific UX: 403 `bot was blocked by the user`, 403 `bot can't initiate conversation with a user`, 403 `bot was kicked`, 400 `not enough rights to send text messages`, 401 invalid token, 429 `Too Many Requests` with `retry_after`. | Error handler maps each to a clear UI affordance (prune cache, prompt admin, re-paste token, back off, etc.). |
| Sending the same photo to N chats means N uploads. | The first send returns a `file_id` that can be reused for subsequent sends with zero upload. | Implement a `file_id` cache keyed on local-file hash + bot id. |
| The `allowed_updates` array we pass to `getUpdates` is just a filter. | Omitting `allowed_updates` enables all updates **except** `chat_member`, `message_reaction`, `message_reaction_count`. Passing it explicitly **must** include `my_chat_member` or we lose kick/block detection. | We pass an explicit allow-list and document the gotcha. |
| Posting to forum-topic groups works the same as plain groups. | Topic-enabled supergroups require `message_thread_id` on every send; otherwise the message lands in "General". `getChat().is_forum` flags these. | Chat record stores `is_forum` + topic id when known; UI exposes a topic selector when relevant. |
| `disable_web_page_preview: true` is the way to suppress link previews. | Deprecated in favor of `link_preview_options` (object with `is_disabled`, `url`, `prefer_small_media`, `prefer_large_media`, `show_above_text`). | Use the new object. |
| Plain "409 Conflict" is the only mode `getUpdates` can fail with. | Two distinct cases: (a) a webhook is active -> `409 Conflict: can't use getUpdates method while webhook is active`, (b) two pollers running -> `409 Conflict: terminated by other getUpdates request`. Mitigation: call `deleteWebhook` before our first `getUpdates`. | API wrapper auto-calls `deleteWebhook(drop_pending_updates=false)` on first use, with a confirmation if a webhook was actually present (the bot may belong to a separate system). |

Rate limits per the official FAQ (verbatim where it matters):

- "In a single chat, avoid sending more than one message per second."
- "In a group, bots are not able to send more than 20 messages per minute."
- "For bulk notifications, bots are not able to broadcast more than about
  30 messages per second, unless they enable paid broadcasts to increase
  the limit."

Paid broadcasts (raises broadcast cap to ~1000/sec at 0.1 Stars/msg) are
enabled in BotFather and surfaced as a per-send checkbox in v2; out of
scope for v1.

## Why bot (not userbot) for v1

Decided in design discussion:

- Product identity is separable from the user's personal Telegram.
- Token is revocable and team-shareable.
- No phone / SMS / 2FA login flow, no MTProto session file.
- Lower attack surface, lower install weight (no Telethon / TgCrypto).
- Subscribers join the bot's channel themselves; matches the "updates feed"
  shape the user is building for.

Userbot mode can be added as a parallel publisher later without disturbing v1.

## Scope (v1)

- **Multi-bot from day one.** Users can add as many bots as they want and
  switch between them on every publish. Each bot has its own chat cache,
  its own send history, and its own user-given display name. Common
  shape: one bot for public product updates, a second for an internal
  team channel, a third for a community group - all reachable from one
  switcher.
- HTTP-only transport via the Bot API at `https://api.telegram.org`. No webhooks.
- Discover chats each bot has been added to by long-polling `getUpdates`
  on demand, plus manual "add by chat id / @username / `t.me/...` invite
  link". Discovery is per-bot; switching the active bot in the publish
  dialog reloads the chat list from that bot's cache.
- Persistent per-bot chat cache: title, id, type (group / supergroup /
  channel / private), last-seen timestamp, per-chat default-select flag.
- Send `sendMessage` with HTML formatting (simpler escaping than MarkdownV2);
  auto-split into reply-threaded chunks above 4096 chars at paragraph
  boundaries with `1/n`, `2/n` suffixes.
- Inline images: detect the first `![alt](url)` or local-file drop, use
  `sendPhoto` with the body as caption when the body fits in 1024 chars,
  otherwise `sendPhoto` then `sendMessage` reply with the rest.
- **Client-side scheduling** (the Bot API has no server-side scheduling
  for bots - verified against the docs; see the Source verification
  section). A pending post is persisted to a local queue and fires when
  the editor process is running at the scheduled time. The UI is
  explicit about this: "Will send at 09:00 (your editor must be open)."
  An optional system-tray mode (v1.1) keeps the scheduler alive without
  the main window. v2 ships an optional headless daemon.
- Bot invite-link helpers: copy `t.me/<bot>?startgroup=true`,
  `t.me/<bot>?startchannel=true`, and QR codes for both (reuse `segno`).
- Per-send progress UI: same shape as the Nostr publish dialog
  (`status_changed` -> `completed` -> per-target results).
- Cross-platform: only stdlib + `QNetworkAccessManager` + existing
  `cryptography` / `segno` deps. Tested on Linux, macOS, Windows.

## Scheduling architecture (v1)

Because the Bot API does not expose `schedule_date`, scheduling is fully
client-side. The plan is honest about this in the UI and treats the
local queue as the source of truth.

### Components

- **`scheduler.py`** - a single `TelegramScheduler` `QObject` started by
  `main_window.py` on app launch. Owns a `QTimer` set to the next due
  send. Persists state via `ScheduledQueue` (below).
- **`queue.py`** - `ScheduledQueue` + atomic JSON store at
  `~/.config/my_editor/telegram_scheduled.json`. Each entry:
  ```json
  {
    "id": "sched_a3f2b81c",
    "bot_id": "p1",
    "targets": [-1001234567890, -1009876543210],
    "body_markdown": "...",
    "media": [{"path": "...", "kind": "photo"}],
    "scheduled_for": 1748131200,
    "created_at": 1748000000,
    "status": "pending",
    "attempts": 0,
    "last_error": null,
    "delivered": []          // [{target_id, message_id, sent_at}]
  }
  ```
- **`queue_dialog.py`** - "Scheduled posts" window listing all entries
  across bots, with filters (bot, status, time range), and per-row
  actions: `Send now`, `Reschedule...`, `Edit...`, `Duplicate...`,
  `Cancel`. Reached from `Tools > Publish to Telegram > Scheduled...`.

### Lifecycle

1. User picks `Schedule` in the publish dialog -> the post becomes a
   queue entry with `status="pending"`. The dialog confirms with an
   explicit notice: "This post will send at 09:00 on Monday. Your
   editor must be open at that time."
2. `TelegramScheduler` re-arms its timer for the new earliest-due entry.
3. When the timer fires, the scheduler dispatches a `TelegramPublishJob`
   for that entry. On success, `status="sent"` and per-target `delivered`
   ids are recorded.
4. On failure (network down, token revoked, chat unavailable), the
   entry's `attempts` increments. Retry policy: exponential back-off
   capped at 5 attempts over ~30 minutes; after the cap the entry flips
   to `status="failed"` with `last_error` populated and surfaces a tray
   notification.
5. If the editor was offline at the scheduled time, the next launch's
   scheduler scan finds the overdue entry and fires it immediately,
   with a banner that surfaces in the queue dialog: "Sent N minutes
   late because your editor was closed." User can opt out per-entry
   ("send only if on time, otherwise skip") via a future v2 setting.

### Honest UI about the constraint

The "Schedule" radio in the publish dialog is paired with a small
permanent helper text:

> Your editor must be running at the scheduled time. Set up
> [system tray mode](#) (v1.1) to keep the scheduler alive without the
> main window.

The link in v1 points to a doc explaining the constraint and what's
coming. In v1.1 it opens the tray-mode setting.

### v1.1: system tray mode

Keep the editor's main process running with the window hidden, so the
scheduler fires posts on time without the user re-opening the editor.

Cross-platform plan:

- `QSystemTrayIcon` for the icon, available on Linux (with KStatusNotifierItem
  fallback where libappindicator is unavailable), macOS (menubar), and
  Windows (system tray). No new dep.
- Menu items: `Open editor`, `Pending posts: N`, `Quit`.
- Triggered by a setting "Keep running in background when the window
  closes" (off by default).
- Honest about wake-from-sleep: a missed fire while the laptop was
  asleep gets sent on wake, with the late-banner from above.

### v2: optional headless daemon

For users who want true server-side reliability we offer a small
companion mode: `my-editor --telegram-daemon` runs without any window
at all. Optional systemd unit / launchd plist / Windows service
templates ship in `docs/`. Not in v1 or v1.1.

### What we do NOT do

- No call to a non-existent `getMessage` to "verify" anything; our
  local store is canonical.
- No claim that posts fire "even with the editor closed" - that's
  false until v1.1 (tray mode) or v2 (daemon).
- No recurring schedules in v1 ("every Friday"). Comes back as v2 once
  the basic queue is shaken out.

## Convenience & polish

The feature must be easy to use and easy to navigate. The list below is
the v1 minimum bar for "delightful, not just functional".

### Post-send: see what you just published

After every send, each successful target gets a clickable permalink.
Permalink construction (built in `permalinks.py`):

| Chat type | Permalink format | Available to |
|---|---|---|
| Public supergroup or channel (has `@username`) | `https://t.me/<username>/<message_id>` | Anyone with the link |
| Private supergroup or channel (`chat_id` starts `-100`) | `https://t.me/c/<chat_id_without_-100>/<message_id>` | Only members of the chat |
| Private group (legacy `chat_id` starts `-`, not `-100`) | none (Telegram has no permalink for legacy groups) | UI says "no link available; open Telegram to view" |
| Private chat with a user | none (no per-message public link) | UI links to `tg://user?id=<id>` to open the conversation |

`sendMessage` returns the `Message` object including `message_id`; we
persist `{target_id, message_id, sent_at}` into the queue entry's
`delivered` array (or, for "Send now", into a transient send-result
record) so permalinks are always reconstructible later.

### Send-results panel (`ui/send_results.py`)

After Send (Now or after a scheduled fire), the publish dialog flips
from "compose" to "results" without closing:

```
+----------------------------------------------------------------------+
|  Publish to Telegram - Sent                                      [x] |
+----------------------------------------------------------------------+
|                                                                      |
|  Sent as (P) Product Updates  @MyProductBot                          |
|                                                                      |
|  +----------------------------------------------------------------+  |
|  | OK  Product Updates       channel                              |  |
|  |     https://t.me/myproductupdates/482         [Copy] [Open]    |  |
|  |                                                                |  |
|  | OK  Beta Testers          group   (private link, members only) |  |
|  |     https://t.me/c/1234567890/118             [Copy] [Open]    |  |
|  |                                                                |  |
|  | !!  Internal Team         group                                |  |
|  |     Bot was kicked. Remove from cache?     [Remove] [Ignore]   |  |
|  +----------------------------------------------------------------+  |
|                                                                      |
|  [Copy all links]   [Open all in Telegram]                           |
|                                                                      |
|  [<- Edit & send again]              [Pin to chats...]    [Close]    |
+----------------------------------------------------------------------+
```

- **OK / !! markers** next to each target.
- **Copy** copies the permalink to the clipboard with a brief toast.
- **Open** launches the link via `QDesktopServices.openUrl`, which the
  Telegram desktop client picks up on all three OSes when installed
  (falls back to web Telegram in the browser).
- **Copy all links** copies a newline-separated list, plus a per-line
  "<chat title>: <url>" prefix for human readability.
- **Open all in Telegram** opens one link per OK target. Capped at 5
  to avoid spamming the OS; over 5, the button becomes "Open first 5
  (N total)" with a tooltip explaining.
- **Pin to chats...** opens a multi-select popover of the OK targets;
  on confirm calls `pinChatMessage` for each. Disabled for chats where
  the bot lacks `can_pin_messages` (we know from the `getChatMember`
  cache; see "Permissions cache" below).
- **Edit & send again** flips back to the compose view with the same
  body, same bot, same targets - useful for fix-and-resend.

### Per-chat row enhancements (in the compose-view target list)

- Right-aligned **last-sent** column: "2h ago", "yesterday", "3 days
  ago", "never". Tooltip shows the full timestamp.
- Hover reveals an **inline ">"** button per row that opens the last
  sent message in Telegram (for chats where a permalink exists).
- A small **star** icon to mark a chat as a favorite. Favorites sort
  to the top and pre-select by default in this bot's compose view.
- **Type badges** are subtle pill labels: `channel`, `group`, `topic`.
- **Permission badges** when relevant: `admin`, `can post`, `can pin`,
  `no rights` (red, with tooltip explaining the fix).

### Per-send options (the "More" disclosure under the Send row)

Compact by default; expanded on click. Options:

- **Silent** - sets `disable_notification: true`. Useful for low-key
  edits / replies-to-self / quick fixes.
- **Disable link preview** - sets `link_preview_options.is_disabled:
  true`. Tooltip: "Recommended for posts where the link is just a
  source reference, not the headline."
- **Protect content** - sets `protect_content: true`. Tooltip:
  "Prevents recipients from forwarding or saving this message."
- **Pin after sending** - calls `pinChatMessage` once the send
  succeeds. Useful for "latest release" posts.

Each setting is remembered as the default for the next post via
`ui.last_send_options` in `telegram.json`, but always re-asks visually
(no surprise re-toggles).

### Keyboard shortcuts (publish dialog)

| Key | Action |
|---|---|
| Cmd/Ctrl + Enter | Send Now |
| Cmd/Ctrl + Shift + S | Schedule... |
| Cmd/Ctrl + L | Focus targets search |
| Cmd/Ctrl + B | Open bot switcher |
| Cmd/Ctrl + K | Copy results as markdown links (post-send view) |
| Esc | Cancel / Close |

Shortcuts are listed at the bottom-right of the dialog in muted text
and shown on the editor's main keyboard-shortcuts dialog
(`shortcuts_dialog.py`).

### Drag-and-drop convenience

- **Image** dropped on the body editor: inlined as `![](file:...)`.
- **Image** dropped on the dialog (outside the body): same.
- **Markdown / text file** dropped on the body editor: contents
  inserted at cursor.
- **`.json` exported from another my-editor install** dropped on the
  bots manager: opens an import preview (out of v1 scope but the
  affordance is reserved).

### Per-document memory (side-car)

A tiny side-car JSON (`<document>.tg.json` next to the document,
gitignored by default in the docs UI) remembers:

- Last-used `bot_id`
- Last selected `target_id`s
- Last "Send options" toggles
- Last send timestamps + permalinks (so "Edit & send again" / "Send
  follow-up" flows know where the previous version went)

Opening the publish dialog from a document with a side-car pre-selects
all of the above. Documents without a side-car use the bots manager's
default bot and that bot's favorites for pre-selection.

### Recent sends & resend

A small "Recent" pill in the publish dialog header opens a popover
listing the last 10 sent posts (across all bots, all documents) with
their permalinks and a `Send follow-up...` action that pre-fills the
compose view with the same bot + same targets.

### Drafts dialog enhancements

(`ui/drafts_dialog.py`, the local drafts list - separate from the
scheduled queue)

- Live filter as the user types.
- Side-by-side: list on the left, preview on the right.
- One-click `Send` from a draft row.
- One-click `Schedule` from a draft row.
- One-click `Edit in document` if the draft was saved from a specific
  document (uses the side-car back-link).

### Empty-state guidance everywhere

Every list that can be empty (chats panel, scheduled queue, drafts,
bots) has a curated empty-state block with: one sentence of what
should be there, one CTA button, one "how do I do this" link to the
in-app help. No "no items" terminal-prompt sadness.

### Status bar integration

After every send, the editor's main status bar flashes a one-line
result: "Posted to 2 Telegram chats." with a small `View results`
link that re-opens the dialog's results view. Auto-fades after 8s.

### Toast notifications

For events that happen with the publish dialog closed (e.g. scheduled
post fires, scheduled post fails after retries), a Qt toast surfaces
in the editor's main window for 6s with a `View` button that opens
the queue dialog focused on that entry.

### Permissions cache

After every `my_chat_member` update (and after every successful send),
we cache per-bot, per-chat the relevant booleans: `is_member`,
`is_admin`, `can_post_messages`, `can_pin_messages`,
`can_delete_messages`. The UI uses this cache to:

- Pre-disable "Pin to chats..." entries the bot can't pin in.
- Show the "no rights" badge in the target list.
- Suppress the "promote to admin" CTA in channels where we already are
  admin.

When the cache is older than 24h for a chat being acted on, we call
`getChatMember` to refresh inline (fast, single HTTP round-trip).

## Out of scope (v2 or later)

- Userbot / MTProto mode (separate branch).
- Edit previously-*sent* messages from the editor (`editMessageText` -
  works for 48h; not core to a publishing flow). Native Telegram clients
  cover this.
- Delete previously-*sent* messages (`deleteMessage` - 48h window; same
  rationale).
- Inline keyboards / reply buttons.
- Polls, quizzes, location, contacts, venues.
- Recurring schedules ("every Friday at 9am"). Comes back as v2 once
  the v1 queue + v1.1 tray are in real use.
- Reactions, comments retrieval, analytics.
- Telegraph long-form publishing fallback.
- Local Bot API server for >50MB files.
- Paid broadcasts (raises 30/sec broadcast cap to ~1000/sec at 0.1
  Stars/msg). Useful for users with >30 chats; UI is a per-send
  checkbox once enabled in BotFather. Not needed for the 95% case.
- `business_connection_id` (Telegram Business proxy mode). Available on
  every send method but irrelevant to the publisher use-case.
- Webhook mode. Long-polling is simpler, sufficient for discovery, and
  works without a public hostname. Webhooks become interesting only if
  we add interactive features (callback queries, inline mode).
- Headless `--telegram-daemon` mode (lands in v2).

## Stack

| Concern | Choice |
|---|---|
| HTTP transport | `QNetworkAccessManager` (matches `nostr/rss/fetcher.py`) |
| JSON | stdlib |
| HTML escaping | stdlib `html.escape` |
| Markdown to Telegram HTML | small in-house converter (see Format rules) |
| QR rendering | existing `segno` |
| Settings persistence | atomic temp-file + rename, 0600 perms (mirrors `blossom/settings.py`) |
| Concurrency | Qt signals on a single thread; `QNetworkAccessManager` is async by nature |
| Tests | pytest, captured JSON fixtures for `getUpdates` / `sendMessage` responses |

Zero new pip dependencies.

## Module layout

```
publishers/
  __init__.py
  base.py                  Publisher protocol + PublishTarget / PublishResult dataclasses
  registry.py              ENABLED_PUBLISHERS list + lookup helpers
  telegram/
    __init__.py
    api.py                 thin Bot API wrapper over QNetworkAccessManager
                           (get_me, get_updates, get_chat, get_chat_member,
                           send_message, send_photo, send_document,
                           pin_chat_message, delete_webhook, error mapping)
    settings.py            TelegramSettings: bots[] + per-bot chats, atomic JSON
    bots.py                Bot dataclass, add/remove/rename, getMe validation
    chats.py               Chat dataclass + forum topics, per-bot discovery
    permissions.py         per-bot/per-chat permission cache via getChatMember
    file_ids.py            local cache of {sha256+bot_id -> file_id}
    permalinks.py          message_id + chat -> https://t.me/... URL
    format.py              markdown -> Telegram HTML, length checks, auto-split
    publisher.py           TelegramPublishJob (QObject + signals)
    queue.py               ScheduledQueue, atomic JSON, retry policy
    scheduler.py           TelegramScheduler (QTimer-driven, app-lifecycle)
    invites.py             link + QR helpers for ?startgroup / ?startchannel&admin
    errors.py              ApiError taxonomy mapped from Telegram responses
    ui/
      __init__.py
      bots_dialog.py       multi-bot manager: list, add, rename, set default, remove
      add_bot_dialog.py    paste-token + getMe + name flow (used from bots_dialog)
      publish_dialog.py    main composer with bot switcher in the header
      chats_panel.py       chat picker (multi-select, search, sort, favorites, topics)
      schedule_widget.py   "Now / Schedule" date+time row with editor-must-be-open hint
      send_options.py      "More" disclosure: silent, no-preview, protect, pin-after
      send_results.py      per-chat result list, permalinks, pin/open/copy actions
      queue_dialog.py      scheduled posts across bots, filter + per-row actions
      drafts_dialog.py     local drafts list + preview + one-click send/schedule
      bot_chip.py          reusable chip widget: avatar letter + name + dropdown
      empty_state.py       reusable empty-state block with CTA

tests/
  test_telegram_format.py
  test_telegram_chats.py
  test_telegram_settings.py
  test_telegram_invites.py
```

Sibling to `nostr/`, not nested under it. The Nostr publisher stays where
it is; a 30-line `publishers/nostr_adapter.py` wraps it to fit `Publisher`
once a second destination actually exists.

## The Publisher protocol (publishers/base.py)

Kept intentionally small. The point is to give the next destination an
obvious shape, not to over-engineer a plugin system.

```python
@dataclass(frozen=True)
class PublishTarget:
    id: str                 # opaque per-publisher target id
    label: str              # human label for the picker row
    kind: str               # "group" | "channel" | "dm" | "broadcast" | ...
    badge: str = ""         # optional short tag, e.g. "admin", "muted"

@dataclass(frozen=True)
class PublishResult:
    target_id: str
    ok: bool
    message: str            # error reason on failure, URL or id on success
    permalink: Optional[str] = None  # e.g. https://t.me/channel/123

class Publisher(Protocol):
    name: str               # "telegram"
    display_name: str       # "Telegram"

    def is_configured(self) -> bool: ...
    def open_setup(self, parent) -> None: ...
    def list_targets(self, on_done, on_error) -> None: ...
    def refresh_targets(self, on_done, on_error) -> None: ...
    def create_job(
        self,
        body_markdown: str,
        targets: Sequence[PublishTarget],
        schedule_at: Optional[int] = None,
    ) -> QObject: ...        # the job exposes status_changed / completed / failed
```

`registry.py` exports `ENABLED_PUBLISHERS = [TelegramPublisher()]`. Future
destinations append. The main window menu builds itself from this list.

## TelegramSettings

JSON at `~/.config/my_editor/telegram.json`. Atomic write, 0600 perms.

```json
{
  "version": 1,
  "default_bot_id": "p1",
  "bots": [
    {
      "id": "p1",
      "display_name": "Product Updates",
      "color": "#4F86C6",
      "token": "12345:ABC...",
      "telegram_username": "MyProductBot",
      "telegram_first_name": "My Product Updates",
      "telegram_user_id": 12345,
      "added_at": 1748000000,
      "last_verified": 1748131200,
      "last_update_id": 482910,
      "chats": [
        {
          "id": -1001234567890,
          "type": "channel",
          "title": "Product Updates",
          "username": "myproductupdates",
          "last_seen": 1748131200,
          "default_select": true
        }
      ]
    },
    {
      "id": "p2",
      "display_name": "Internal Alerts",
      "color": "#C65F5F",
      "token": "67890:DEF...",
      "telegram_username": "InternalAlertsBot",
      "telegram_first_name": "Internal Alerts",
      "telegram_user_id": 67890,
      "added_at": 1748050000,
      "last_verified": 1748131200,
      "last_update_id": 1245,
      "chats": []
    }
  ],
  "ui": {
    "default_format": "html",
    "auto_split": true,
    "remember_target_selection_per_bot": true
  }
}
```

Notes on the schema:

- **`id`** is a local-only stable id (8-char `secrets.token_hex(4)`).
  Survives the user renaming a bot or Telegram renaming the bot's
  `@username`. Used everywhere we reference a bot internally (drafts,
  scheduled queue, recent-target memory).
- **`display_name`** is user-given, free-form, and what the UI shows
  everywhere. Defaults to the bot's `first_name` from Telegram on add,
  but the user can change it (e.g. two bots both named "Bot" by their
  creator can become "Product" and "Internal" here).
- **`color`** is a small palette pick (8 preset colors); used for the
  letter-avatar background in the bot chip. Auto-assigned on add,
  rotates through the palette.
- **`chats`** is per-bot - a chat that's visible to bot A may not be to
  bot B. Switching bots in the publish dialog swaps in the relevant cache.
- **`last_update_id`** is per-bot - each bot has its own `getUpdates`
  cursor.
- **`default_bot_id`** controls which bot the publish dialog opens to
  by default. Can be reassigned from the bots manager or with a
  `(set default)` action in the switcher.

The tokens are stored in plaintext on disk inside a 0700 dir with 0600
file perms - same threat model as the Nostr bunker config and the
Blossom server list already in this app. A `keyring`-backed alternative
is noted in `Footguns` for users who want it; not in v1.

## Bots manager dialog

Opened from `Tools > Publish to Telegram > Manage bots...` or auto-opened
the first time the user picks "Publish to Telegram" with zero bots
configured.

The canonical place to add, rename, recolor, set-default, and remove
bots. Single-bot users see a list of one row; multi-bot users get a
clean curated view.

```
+--------------------------------------------------------------------+
|  Telegram bots                                                 [x] |
+--------------------------------------------------------------------+
|                                                                    |
|  +-------------------------------------------------------------+   |
|  | (P) Product Updates                                  *      |   |
|  |     @MyProductBot - 3 chats - last sent 2h ago              |   |
|  |                                                             |   |
|  | (I) Internal Alerts                                         |   |
|  |     @InternalAlertsBot - 1 chat - never used                |   |
|  |                                                             |   |
|  | (C) Community News                                          |   |
|  |     @CommunityNewsBot - 7 chats - last sent yesterday       |   |
|  +-------------------------------------------------------------+   |
|                                                                    |
|  Selected: Product Updates                                         |
|    Rename...    Change color...    Set as default *                |
|    Show token (hidden)             Test connection                 |
|    Open BotFather link             Copy invite link                |
|    Remove this bot                                                 |
|                                                                    |
|  [+ Add a bot...]                                          [Close] |
+--------------------------------------------------------------------+
```

- Rows show the per-bot color avatar (first letter of `display_name`),
  the user-given name, the Telegram `@username`, a chat-count, and a
  last-used hint.
- `*` marker on the row that is the current default.
- Right pane shows actions for the selected row. Multi-select is
  intentionally NOT supported - bot-management actions are per-bot and
  destructive actions should be one at a time.
- `Show token` reveals the stored token in a read-only field with a
  copy button (still hidden by default).
- `Remove this bot` confirms with a dialog spelling out exactly what is
  lost: "Removing 'Product Updates' will forget the token and the cached
  list of 3 chats. The bot itself stays alive on Telegram and you can
  re-add it later by pasting the same token."
- `[+ Add a bot...]` opens the add-bot flow.

## Add-bot dialog

```
+--------------------------------------------------------+
|  Add a Telegram bot                                [x] |
+--------------------------------------------------------+
|                                                        |
|  1. In Telegram, open chat with @BotFather             |
|     [Open BotFather in browser]                        |
|                                                        |
|  2. To create a new bot: send  /newbot  and follow     |
|     the prompts.                                       |
|     To use an existing bot: send  /mybots  ->          |
|     pick the bot -> "API Token".                       |
|     (Do NOT use /token unless you mean to rotate the   |
|     token; /token revokes the old one.)                |
|                                                        |
|  3. Paste the token here:                              |
|     [................................. (hidden) [eye]]|
|                                                        |
|  [Verify token]                                        |
|                                                        |
|     >>> Recognized @MyProductBot                       |
|         Telegram name: "My Product Updates"            |
|                                                        |
|  4. Display name in this app:                          |
|     [Product Updates                                  ]|
|     Color: ( ) [.] ( ) ( ) ( ) ( ) ( ) ( )             |
|                                                        |
|  [x] Make this the default bot                         |
|                                                        |
|              [Cancel]                    [Add bot]    |
+--------------------------------------------------------+
```

- `Verify token` calls `getMe`. On success unlocks step 4 (`display_name`,
  color, default checkbox). On failure shows the API's `description` text
  verbatim.
- `display_name` defaults to the bot's Telegram `first_name`; user can
  edit before saving.
- Color defaults to the next unused slot from an 8-color palette.
- `Add bot` persists, runs one round of `getUpdates` for that bot to
  pre-populate chats, then dismisses back to the bots manager.
- Token field auto-trims whitespace and strips any leading prose that
  ends with a colon followed by the token (covers BotFather's observed
  reply formats - we don't depend on the exact wording since it's not
  contractually stable in the docs).
- Duplicate detection: if the pasted token's `getMe` returns a bot we
  already have, the dialog says "You already added this bot as
  'Product Updates'." and offers to open that bot's row in the manager
  instead.

## Publish dialog

Three columns, scales on resize. Same dark/light CSS palette as
`publish_note_dialog.py`.

```
+----------------------------------------------------------------------+
|  Publish to Telegram                                             [x] |
+----------------------------------------------------------------------+
|                                                                      |
|  Sending as: [ (P) Product Updates  v ]    [Manage bots...]          |
|              @MyProductBot                                           |
|                                                                      |
|  +-- Targets ----------------+  +-- Preview ----------------------+  |
|  | [Refresh]   [+ Add chat]  |  | <b>New in v2.3</b>              |  |
|  |                           |  |                                 |  |
|  | [Search...           ]    |  | - Feature X                     |  |
|  |                           |  | - Bug fix Y                     |  |
|  | [x] Product Updates       |  |                                 |  |
|  |     channel  admin        |  | [photo: hero.png attached]      |  |
|  | [x] Beta Testers          |  |                                 |  |
|  |     group                 |  | Will send as: 1 message         |  |
|  | [ ] Internal Team         |  | Format: HTML                    |  |
|  |     group                 |  | Length: 412 / 4096              |  |
|  | [ ] Power Users           |  +---------------------------------+  |
|  |     channel               |                                       |
|  |                           |  Send:                                |
|  | How do I add more chats?  |   (o) Now                             |
|  +---------------------------+   ( ) Schedule  [2026-05-26 09:00]    |
|                                  ( ) Save draft (local)              |
|                                                                      |
|  Status: Ready.                                                      |
|                                                                      |
|                                      [Cancel]  [Send to 2 chats]    |
+----------------------------------------------------------------------+
```

The bot switcher (`[ (P) Product Updates  v ]`) is a `BotChip` widget
(see `ui/bot_chip.py`):

- Letter avatar = first character of `display_name`, on the bot's color.
- Click opens a popover listing all configured bots with the same
  letter-avatar treatment, plus a `[+ Add a bot...]` row at the bottom.
- Picking a different bot reloads the entire targets column from that
  bot's chat cache. The body text and schedule selection persist (the
  message you're composing doesn't care which bot sends it).
- Below the chip, the bot's Telegram `@username` is shown in muted text -
  this is the identity the message will appear as in Telegram.
- `[Manage bots...]` opens the bots manager.
- The dialog opens with the default bot pre-selected. If the last
  publish from the current document was via a non-default bot, that one
  wins (remembered in a small per-document side-car).

Behaviors:

- **Target list** populates from `TelegramSettings.chats`, sorted by:
  default-select first, then last-seen DESC, then title. Each row shows
  title + a one-word kind badge + an optional permissions badge (`admin`,
  `restricted`).
- **Search** filters by title substring (case-insensitive).
- **Refresh** runs one round of `getUpdates`, merging new chats into the
  cache without dropping cached ones the bot can still post to.
- **+ Add chat** opens a small popover with two inputs:
  - `@username` for public channels/groups
  - chat id (e.g. `-1001234567890`) for private groups
  - On submit, calls `getChat` to validate and to fetch title.
- **Preview** is the rendered HTML, escaped and formatted exactly as it
  will be sent. The "Will send as" line tells the user up front whether
  this is one message, a thread, or a photo+caption split.
- **Schedule** opens a calendar+time picker rooted in the system timezone.
  Telegram needs unix seconds; we display the converted UTC value in a
  tooltip on the row for transparency.
- **Save draft (local)** persists the body + selected targets + (optional)
  schedule into a `~/.config/my_editor/telegram_drafts.json`. The
  drafts list is reachable from `Tools > Publish to Telegram > Drafts...`.
  Not synced anywhere - purely a local convenience for "I'll finish this
  later".

## Format rules (publishers/telegram/format.py)

Telegram has two formatting modes; we pick HTML for v1 because:

- Escape rules are tiny (`<`, `>`, `&`) versus MarkdownV2's 19-character set.
- The set of accepted tags is fixed and small.
- Round-tripping is unambiguous.

### Accepted output tags

The full set Telegram documents (verified at
`https://core.telegram.org/bots/api#html-style`):
`<b>/<strong>`, `<i>/<em>`, `<u>/<ins>`, `<s>/<strike>/<del>`,
`<span class="tg-spoiler">` (alias `<tg-spoiler>`), `<a href="...">`,
`<tg-emoji emoji-id="...">`, `<code>`, `<pre>`,
`<pre><code class="language-X">`, `<blockquote>`,
`<blockquote expandable>`.

Mapping from the editor's markdown:

| Source | Output |
|---|---|
| `**bold**`, `__bold__` | `<b>...</b>` |
| `*italic*`, `_italic_` | `<i>...</i>` |
| `==underline==` (extension) | `<u>...</u>` |
| `~~strike~~` | `<s>...</s>` |
| `\|\|spoiler\|\|` (extension) | `<tg-spoiler>...</tg-spoiler>` |
| `` `code` `` | `<code>...</code>` |
| triple backtick fence with language tag | `<pre><code class="language-X">...</code></pre>` |
| triple backtick fence without language | `<pre>...</pre>` (note: per docs, `language` only valid inside `<pre><code>`, not standalone `<code>`) |
| `[text](url)` | `<a href="url">text</a>` |
| `> quote` (single block) | `<blockquote>...</blockquote>` |
| `>! collapse` (extension) or > 8 lines | `<blockquote expandable>...</blockquote>` |
| `# heading` | `<b>HEADING</b>` followed by a blank line (Telegram has no native headings; bold + uppercase + spacing reads well in Telegram clients) |
| `- bullet` | prepended with `* ` then space (Telegram has no list rendering; this is the conventional ascii) |
| `1. item` | prepended with `1. ` (numbering preserved literally) |
| `![alt](url)` (first only) | extracted, sent as `sendPhoto` with the alt text used as caption fallback only when the body is empty |
| anything else | passed through unchanged after HTML-escaping (`<`, `>`, `&`, and inside hrefs `"`) |

Escape rules (also verified):
- Outside tags, `<`, `>`, `&` must become `&lt;`, `&gt;`, `&amp;`.
- Inside `<a href="...">` the URL is additionally `"`-escaped.
- Inside `<pre>` and `<code>`, only `<`, `>`, `&` need escaping (no
  other markdown is processed - they're literal).
- `<tg-emoji emoji-id="N">EMOJI</tg-emoji>` requires custom-emoji
  access which the bot must specifically be granted; not used in v1.

The output is parsed via `parse_mode: "HTML"` on every send. Failures
in malformed HTML come back as 400 with a parser error message - we
never want to see that in practice because we control the generator.

### Length handling

- Threshold: 4096 for `sendMessage`, 1024 for photo captions.
- If body fits, send as one message.
- If body exceeds threshold, split at the deepest paragraph boundary that
  keeps every chunk under threshold. Append ` (1/3)`, ` (2/3)` on its own
  line at the start of each chunk.
- Chunks 2+ are sent as `reply_to_message_id` of the previous chunk's
  result so they thread visually on Telegram clients.
- A future `auto_split=False` setting will refuse to send instead of
  splitting; out of scope for v1.

### Media

- First inline image extracted; remaining images stay as links in the
  body (they unfurl in Telegram via `link_preview_options`).
- HTTPS URL: passed as the `photo` string to `sendPhoto`. Telegram
  fetches it server-side.
- Local file: multipart `sendPhoto`. Limits per docs: 10 MB max, width +
  height <= 10000 each, aspect ratio <= 20:1. On violation, fall back
  to `sendDocument` (50 MB on the public Bot API server).
- Above 50 MB: surface a "too large to attach inline; sent as a link"
  status note and embed the URL in the message body. We do not target
  the local Bot API server (raises the cap to 2000 MB) in v1.

### `file_id` cache

When `sendPhoto` / `sendDocument` succeeds, Telegram returns a
`file_id` that points to the uploaded media on their CDN. We cache it
keyed on (local-file SHA-256, bot id) in
`~/.config/my_editor/telegram_file_ids.json`. Subsequent sends of the
same image - to a different chat, a follow-up post, a duplicate
schedule - reuse the `file_id` and skip the upload entirely. This is a
real speed win when broadcasting to many chats.

`file_id`s are bot-scoped per Telegram's contract: they cannot be
reused across bots. The cache key includes the `bot_id` to enforce
this. Stale entries (Telegram occasionally rotates) are detected on
400 `wrong file_id`; we evict and re-upload transparently.

### Link previews

Every `sendMessage` includes:

```json
"link_preview_options": {
  "is_disabled": false,
  "prefer_large_media": true,
  "show_above_text": false
}
```

These are the most common defaults for a "product update with a link"
post; overridable per send via the "More" options panel (see the
Convenience section). The deprecated `disable_web_page_preview` flag
is not used.

### Forum topics

Supergroups can have topics enabled (`getChat().is_forum == true`).
Sending to a topic-enabled group without `message_thread_id` lands the
message in the "General" topic, which is rarely what the user wants.

Handling:

- `Chat` records store `is_forum` and a per-topic discovery list
  (`topics: [{id, name, last_seen}]`).
- When a `message` update is received whose `chat.is_forum` is true,
  we extract `message_thread_id` and `message_thread_id`'s topic from
  `forum_topic_created` events if present, keeping the list current.
- In the publish dialog, a topic-enabled chat row expands to show a
  topic sub-picker. Multi-select within topics is allowed (one post,
  multiple topics).
- If no topics have been discovered yet, the UI says "Open the chat in
  Telegram and post into each topic once - the bot will then see them
  here on Refresh."

## TelegramPublishJob

Mirrors the Nostr `PublishJob` shape. Lives in `publishers/telegram/publisher.py`.

```python
class TelegramPublishJob(QObject):
    status_changed = Signal(str)       # human-readable progress text
    target_done = Signal(str, bool, str)  # (target_id, ok, message_or_reason)
    completed = Signal(list)           # list[PublishResult] - terminal
    failed = Signal(str)               # terminal alternative
```

Pipeline per target:

1. Format body into chunks via `format.format_for_send(markdown)`.
2. For each chunk: `POST /bot<token>/sendMessage` (or `sendPhoto` if
   chunk 1 has a photo).
3. Honor `schedule_date` if scheduling.
4. On 429 with `parameters.retry_after`: wait, retry once. On second 429:
   mark target failed with "rate-limited, try again later".
5. On 400 with `chat not found` / `bot was kicked` / `bot was blocked`:
   mark target failed with the API description. Optionally prompt user
   "Remove from cache?" after job completes (batched - one prompt for all
   dead chats).
6. Emit `target_done` after each chat finishes.
7. Emit `completed` with the full result list once all targets finish.

Targets are dispatched serially in v1. The Bot API tolerates parallelism
fine, but serial keeps progress UI honest and avoids the 30-msg/sec
broadcast cap headache; for a typical "send to 2-5 chats" flow it makes
no human-perceptible difference.

## Chat discovery flow

The biggest UX trap with bots: the Bot API has no "list all my chats"
endpoint. We have to surface this honestly.

```
First-time experience after setup:

+-- Chats -------------------------------+
| Bot: @MyProductBot                     |
| [Refresh]   [+ Add chat]               |
|                                        |
|  No chats yet.                         |
|                                        |
|  To make a chat visible here:          |
|                                        |
|  1. Add @MyProductBot to a group       |
|     or channel.                        |
|     [Copy invite link]  [Show QR]      |
|                                        |
|  2. In the group, type:                |
|       /start@MyProductBot              |
|     (or, in a channel, post any        |
|     message after adding the bot       |
|     as admin)                          |
|                                        |
|  3. Click  [Refresh]  above.           |
+----------------------------------------+
```

After the first refresh succeeds, the empty-state block is replaced by the
populated list. The instructions remain reachable behind a small
`How do I add more chats?` link at the bottom of the populated list.

`getUpdates` is called with `offset=last_update_id+1`, `timeout=0`,
`allowed_updates=["message", "channel_post", "my_chat_member"]`.
We extract `update.message.chat`, `update.channel_post.chat`, and
`update.my_chat_member.chat`, merging into the cache.

## Invite link helpers (publishers/telegram/invites.py)

Exposed from a `Bot info` button in the publish dialog and from each
row of the bots manager. The link forms (verified at
`https://core.telegram.org/bots/features#deep-linking` and
`https://core.telegram.org/api/links#group-channel-bot-links`):

| Purpose | Link |
|---|---|
| DM the bot | `https://t.me/<bot>` |
| Pick a group to add the bot to (as a member) | `https://t.me/<bot>?startgroup=true` |
| Pick a group to add the bot to as admin | `https://t.me/<bot>?startgroup&admin=<perms>` |
| Pick a channel to add the bot to as admin | `https://t.me/<bot>?startchannel&admin=<perms>` |

`<perms>` is a `+`-joined list of permission flags. Plain
`?startchannel=true` (without `admin=`) is **not** documented to work,
because channels do not accept non-admin bots; the helper always
appends a sensible default permission set.

Default permission strings shipped with the helper:

| Affordance | Permissions |
|---|---|
| "Add to channel as poster" | `post_messages+edit_messages+delete_messages` |
| "Add to channel as full admin" | `post_messages+edit_messages+delete_messages+pin_messages+invite_users+manage_chat` |
| "Add to group as admin" | `delete_messages+pin_messages+invite_users+restrict_members+manage_chat` |
| "Add to group as member" | (no `admin=` query) |

Each link is shown as a labelled row with a `Copy` button and an
inline `Show QR` action. The QR is rendered as inline SVG via segno
into a `QLabel` to stay crisp on HiDPI.

Clicking any of the links launches the platform URL handler. On
desktops with Telegram installed, this opens the native client and
shows the chat-picker dialog described above. Without Telegram
installed, the browser opens t.me which shows a "Open in Telegram"
landing page.

## Cross-platform notes

- **Paths**: `pathlib.Path.home() / ".config" / "my_editor"` on all three;
  same pattern as Blossom. macOS users sometimes prefer
  `~/Library/Application Support`, but the existing codebase already uses
  `~/.config` everywhere - we stay consistent rather than fork.
- **Network**: `QNetworkAccessManager` handles TLS, proxies, and platform
  cert stores natively. No `certifi` dep needed.
- **URL launch**: `QDesktopServices.openUrl` works identically.
- **Clipboard**: `QApplication.clipboard()` works identically.
- **Date/time picker**: `QDateTimeEdit` is native on all three.
- **File pickers** (for image attach): `QFileDialog` with platform-native
  dialogs.
- **HiDPI**: SVG QR rendering scales; PNG would not.

No Windows-specific console allocation, no macOS bundle quirks beyond
what `main.py` already handles.

## Build order

Foundations (no UI):

1. `publishers/base.py` + `publishers/registry.py` - small dataclasses,
   no dep on anything else.
2. `publishers/telegram/errors.py` + test - the `ApiError` taxonomy.
3. `publishers/telegram/settings.py` + test - schema with `bots[]`,
   per-bot `chats[]`, per-chat `topics[]`, atomic save, full lifecycle
   (add/remove/rename/recolor/set-default).
4. `publishers/telegram/api.py` + test - `BotApi(token)` over
   `QNetworkAccessManager`. Methods: `get_me`, `get_updates`,
   `delete_webhook`, `get_chat`, `get_chat_member`, `send_message`,
   `send_photo`, `send_document`, `pin_chat_message`. Each returns an
   `ApiCall` QObject with `succeeded(dict)` / `failed(ApiError)`
   signals. Token redaction in any logging.
5. `publishers/telegram/bots.py` + test - `Bot` dataclass; add-bot flow
   (paste token -> getMe -> persist); duplicate detection; `BotApi`
   factory keyed on bot id.
6. `publishers/telegram/permalinks.py` + test - construct
   `https://t.me/<username>/<id>` and `https://t.me/c/<id>/<id>` from
   a `(chat, message_id)` pair; return `None` for legacy groups / DMs.
7. `publishers/telegram/file_ids.py` + test - sha256-keyed `file_id`
   cache, atomic JSON, per-bot scoping, evict on `StaleFileId`.
8. `publishers/telegram/permissions.py` + test - per-bot/per-chat
   permission cache via `getChatMember`, 24h freshness window.
9. `publishers/telegram/format.py` + test - full Telegram HTML tag
   set; auto-split; threaded chunk numbering.
10. `publishers/telegram/chats.py` + test - per-bot discovery via
    `getUpdates`; merge into settings; extract `forum_topic_*` events;
    handle the `WebhookActive` -> `deleteWebhook` flow.
11. `publishers/telegram/publisher.py` + test - `TelegramPublishJob`
    end-to-end orchestrator with `file_id` reuse, retry policy, and
    per-target result emission.
12. `publishers/telegram/queue.py` + test - persistent scheduled queue.
13. `publishers/telegram/scheduler.py` + test - `QTimer`-driven
    `TelegramScheduler`, started by `main_window.py` on launch.
14. `publishers/telegram/invites.py` + test - link helpers and QRs.

UI (reuses everything above):

15. `publishers/telegram/ui/empty_state.py` - reusable empty-state block.
16. `publishers/telegram/ui/bot_chip.py` - bot avatar + switcher.
17. `publishers/telegram/ui/add_bot_dialog.py`.
18. `publishers/telegram/ui/bots_dialog.py` - multi-bot manager.
19. `publishers/telegram/ui/chats_panel.py` - picker with topics,
    favorites, last-sent, permission badges.
20. `publishers/telegram/ui/schedule_widget.py` - "Now / Schedule"
    with the editor-must-be-open hint.
21. `publishers/telegram/ui/send_options.py` - "More" disclosure.
22. `publishers/telegram/ui/send_results.py` - post-send view with
    permalinks, copy/open/pin.
23. `publishers/telegram/ui/drafts_dialog.py`.
24. `publishers/telegram/ui/queue_dialog.py` - scheduled-posts view.
25. `publishers/telegram/ui/publish_dialog.py` - composes everything.

Integration:

26. Menu wiring in `main_window.py`: `Tools > Publish to Telegram >
    [Send current document... | Scheduled... | Drafts... | Manage
    bots...]`.
27. Per-document side-car save/load wired into the document
    open/close lifecycle.
28. Status-bar + toast hooks for send completion and scheduler events.
29. Keyboard shortcuts registered in `shortcuts_dialog.py`.
30. CI check: grep that `publishers/telegram/` does not import from
    `nostr/` (enforces the "no Nostr coupling" rule).
31. Smoke test against two real bots in different chats, covering
    the acceptance steps below.
32. README section + screenshots (publish dialog, bots manager,
    queue dialog, results view).

Each step is independently testable and shippable; nothing further in the
list requires anything not yet built.

## Error taxonomy

`errors.py` maps Telegram responses to a small set of `ApiError`
subclasses, each with a recommended user-facing action.

| HTTP / description fragment | Class | UI action |
|---|---|---|
| 401 Unauthorized | `InvalidToken` | Mark bot as broken; `Paste new token for this bot` action that preserves id, name, color, chat cache. |
| 403 `bot was blocked by the user` | `UserBlockedBot` | Mark the DM row inactive; prompt-to-remove after the job. |
| 403 `bot can't initiate conversation with a user` | `UserNeverStarted` | Hide the DM row; show inline help "Ask the user to send /start to @<bot> first." |
| 403 `bot was kicked` / `bot is not a member of the channel chat` | `BotRemoved` | Mark chat row inactive; prompt-to-remove. |
| 400 `not enough rights to send text messages` | `NoPostRights` | Inline help: "Make @<bot> an admin of this channel with the 'Post messages' permission." Link copies the bot's `?startchannel&admin=...` URL. |
| 400 `wrong file_id` / `wrong type of the web page content` | `StaleFileId` | Evict from `file_ids.py` cache, retry once with a fresh upload. |
| 400 `message is too long` | `MessageTooLong` | Should never happen (our splitter prevents); if it does, log and split-and-retry. |
| 400 `can't parse entities` | `BadHtml` | Our formatter bug. Log the offending input verbatim; send a plain-text fallback with `parse_mode` omitted so the post still ships. |
| 400 `chat not found` | `ChatNotFound` | Suggest re-adding the bot; offer to remove the cache row. |
| 409 Conflict `can't use getUpdates method while webhook is active` | `WebhookActive` | Prompt: "This bot has a webhook configured. Delete it?" -> calls `deleteWebhook(drop_pending_updates=false)`. |
| 409 Conflict `terminated by other getUpdates request` | `ConcurrentPoller` | Inline message: "Another tool is polling this bot. Stop the other poller, then click Refresh." Auto-retries every 30s in the chats panel up to 5 minutes. |
| 429 with `parameters.retry_after` | `RateLimited` | Sleep `retry_after` seconds; retry once. On second hit, mark target failed with "Telegram rate-limited the bot; try again later." |
| Network error / TLS / DNS | `Transport` | Toast "Telegram unreachable. Check your internet." Retry available. |

## Footguns

- **Token leak via clipboard.** The add-bot dialog hides the token
  field by default (`QLineEdit.Password` echo mode) with a reveal
  toggle. Hide-again on focus-out.
- **Token leak via logs.** Any code that logs API URLs must redact the
  `/bot<token>/` segment. Wrap once in `api.py._redact(url)` and use
  exclusively from there. Same rule applies to the new
  `file_ids.py` and `permissions.py` if they ever log calls.
- **Telegram unilaterally renames a bot.** `getMe` is called on every
  `Refresh` and before the first send of a session; we refresh the
  cached Telegram-side fields without touching the user-given
  `display_name`.
- **Editor closed at fire time.** Honest UI from day one (see
  Scheduling). Overdue entries fire on next launch with a "Sent N
  minutes late" banner. Per-entry "skip if late" toggle is a v2
  setting; out of scope for v1.
- **System clock skew.** The scheduler uses local-time epoch seconds.
  If the clock jumps backward (NTP correction, time-zone change), the
  scheduler re-arms; if it jumps forward, overdue entries fire
  immediately - same as the closed-editor case.
- **MarkdownV2 in user input.** Users pasting MarkdownV2 escape
  sequences (`\.`, `\!`) into the editor will see them rendered
  literally because we send HTML. Documented in `format.py`'s docstring
  with a small "Markdown flavor: standard Markdown, not Telegram's
  MarkdownV2" hint shown in the publish dialog on first run.
- **Forum-topic groups.** Sending without `message_thread_id` lands in
  "General". The chat row in the picker exposes a topic sub-selector;
  empty-topics state explains how to seed discovery (the bot has to
  see a message in each topic once).
- **Rate-limit nuance.** Per the FAQ: 1 msg/sec per chat, 20 msg/min
  per group, ~30 msg/sec broadcasting across chats. Sending serially
  with a 150 ms inter-target sleep stays safely below all three for
  any v1 workflow (single-doc, single-bot, <30 targets). Paid
  broadcasts (v2) would raise the broadcast cap.
- **Keyring on Linux varies.** Token storage in
  `~/.config/my_editor/telegram.json` (0600) matches the existing
  Nostr/Blossom pattern in this app, avoids a `keyring` dep, and
  avoids surprise `dbus` failures on minimal desktop environments. A
  `--keyring` opt-in is a v2 conversation.
- **Network egress in restricted environments.** `api.telegram.org`
  is blocked in some networks. We do not implement MTProxy in v1;
  the `Transport` error surfaces, the user knows.
- **Same token pasted twice.** Detected via `getMe.id` matching an
  existing bot row. We refuse to create a duplicate and link the user
  to the existing row.
- **`my_chat_member` opt-in.** When passing `allowed_updates`
  explicitly, `my_chat_member` must be in the list or we lose
  kick/block detection. The default (no `allowed_updates` arg)
  includes it - but we pass an explicit list, so we always include it.
- **`chat_member` requires bot admin.** Useful for "who joined the
  channel" analytics, but not needed for v1's publish flow. We do not
  request it.
- **Webhook conflict on first call.** A bot the user created for
  another tool may have a webhook set. Our first `getUpdates` would
  fail with `WebhookActive`. The chats panel surfaces a one-click
  "Delete the webhook" prompt explaining that this disables the other
  tool's incoming updates.
- **Bot id collision after restore from backup.** Local bot `id`
  strings are random; collisions are negligible. `chats` is a cache
  and rebuilds via Refresh.
- **Removing a bot with scheduled posts.** The scheduler queue is
  keyed by bot id. Remove-bot lists pending entries and offers
  "Cancel them" or "Keep them but disable" (entries flip to
  `status="orphaned"` and don't fire). No silent loss.
- **Permalinks for private chats.** `https://t.me/c/...` URLs only
  open for members of the chat. The send-results UI labels them as
  "(private link, members only)" so the user isn't surprised when
  pasting them externally and getting a 404 page.
- **Re-uploading after `file_id` rotation.** Telegram occasionally
  invalidates `file_id`s server-side. Our cache evicts on the
  `StaleFileId` error and uploads fresh, transparently to the user.

## Acceptance test (manual)

Run on Linux, macOS, and Windows. Use two test bots created via
@BotFather (call them Test A and Test B), each added to its own
private group plus one public test channel they share as admins.

**Foundations**

1. Fresh checkout, no `~/.config/my_editor/telegram.json`, no Nostr
   profile configured. Proves the Nostr-independence rule.
2. CI grep `grep -r "from nostr" publishers/telegram/` returns no
   results.

**Bots manager**

3. `Tools > Publish to Telegram > Manage bots...` opens to an empty
   state with a single CTA.
4. Click `[+ Add a bot...]` -> paste Test A's token -> Verify ->
   name "Test A" -> tick "default" -> Add. Settings file now has
   one `bots[]` entry with a stable random `id`.
5. Add Test B with a different color. Two entries, two distinct ids.
6. Paste Test A's token again in Add. Duplicate detection fires;
   user is offered "Open the existing 'Test A' row" instead of a
   duplicate add.
7. Rename "Test A" to "Renamed A" and change its color. Bot chip
   and bots list both reflect the change.
8. Set Test B as default. Close everything and re-open the publish
   dialog from a fresh document. Test B is pre-selected.

**Chat discovery**

9. Click `[Copy invite link]` for Test A -> the link is
   `https://t.me/<TestABot>?startgroup=true`. QR for the same URL is
   crisp on a 4K monitor.
10. Click `[Copy invite link]` for a channel -> the link includes
    `&admin=post_messages+edit_messages+delete_messages`.
11. Add Test A to a private group via the deeplink. Send
    `/start@TestABot` in the group. In the chats panel, click
    Refresh; the group appears with its title.
12. Add Test B to a forum-topic-enabled supergroup. Send one message
    in each of three topics. Refresh -> the chat row expands to
    expose the three topics.

**Send (now)**

13. Open any markdown file. Open publish dialog.
14. Tick Test A's group. Send Now. The dialog flips to the results
    view with an OK row and `https://t.me/c/<id>/<msg_id>` link.
    Click `Copy` -> link in clipboard. Click `Open` -> the message
    opens in the Telegram client (or web).
15. Repeat to a public channel both bots admin. Verify the result
    URL is `https://t.me/<channel-username>/<msg_id>`.
16. Send to two chats at once. `[Copy all links]` produces a
    newline-separated list. `[Open all in Telegram]` opens up to 5
    links.
17. Click `[Pin to chats...]`, tick the public channel, confirm. The
    message gets pinned in Telegram. Pin row is disabled for chats
    where the bot lacks `can_pin_messages`.

**Format & media**

18. Compose a 5000-character body. The preview shows "Will send as: 3
    messages" and the auto-split. Send. Verify the three messages
    arrive threaded via `reply_to_message_id`.
19. Compose a body starting with `![](path/to/local.png)`. Send to
    two chats. Verify the photo arrives in both; verify
    `telegram_file_ids.json` gained one entry; verify the second
    chat's send produced no second upload (inspect network traffic
    or log lines).
20. Compose a body with `<script>` literal in it. Verify it arrives
    rendered as text, not as a tag.

**Scheduling**

21. Click `Schedule` in the publish dialog. The helper text below
    explicitly says "Your editor must be running at the scheduled
    time." Pick +5 minutes. Confirm.
22. Entry appears in `Tools > Publish to Telegram > Scheduled...`
    with status `pending`.
23. Wait. The post fires at the scheduled time. Status flips to
    `sent` with the permalink(s) recorded.
24. Schedule another post at +3 minutes. Quit the editor before it
    fires. Re-launch after the scheduled time. The post fires
    immediately on the next event loop and the queue dialog shows
    "Sent N minutes late because your editor was closed."
25. Schedule a post; before it fires, open the queue dialog and
    click `Cancel`. The entry flips to `cancelled`; no message
    arrives in Telegram.

**Per-send options**

26. Tick `Silent` -> the resulting Telegram message arrives without
    a notification sound.
27. Tick `Disable link preview` on a post containing a URL -> the
    URL arrives without an unfurled preview card.
28. Tick `Protect content` -> the message in Telegram disables the
    forward / save buttons.
29. Tick `Pin after sending` -> the message is pinned automatically
    after delivery.

**Errors**

30. Revoke Test A's token via BotFather (`/revoke`). Try to send.
    The send fails with the `InvalidToken` UX: bots manager flags
    Test A as broken and offers `Paste new token for this bot`.
    Paste the new token; bot id, name, color, and chat cache survive.
31. Kick Test B from its group. Send. Result row shows "Bot was
    kicked. Remove from cache?" prompt.
32. Block Test A in a DM (use a personal Telegram account). Send to
    the DM. Result row shows "User blocked the bot" with the
    correct prompt.
33. Set a webhook on Test A out-of-band (`setWebhook`). Open the
    chats panel and click Refresh. UI surfaces "This bot has a
    webhook configured. Delete it?" -> confirm -> Refresh succeeds.

**Convenience**

34. Open the recent-sends popover; the last 3 sends appear with
    timestamps. Click `Send follow-up...` on one. Publish dialog
    opens with the same bot + same targets pre-selected, body empty.
35. Star a chat in the picker. Re-open the publish dialog from a
    new document. The starred chat is at the top and pre-ticked.
36. Use `Cmd/Ctrl + Enter` to send. Use `Esc` to dismiss the
    results view. Use `Cmd/Ctrl + B` to open the bot switcher.
37. Drag-drop a `.png` onto the body editor. Inlined as
    `![](file:...)`. Drag-drop a `.md` file -> contents inserted at
    cursor.

All 37 steps must pass on all three operating systems.
