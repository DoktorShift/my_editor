"""Per-bot chat discovery via ``getUpdates``.

The Bot API gives no "list my chats" endpoint, so we long-poll for
incoming updates and merge them into the per-bot chat cache. Each
``Refresh`` button click in the UI triggers one round trip:

  1. ``getUpdates(offset=last_update_id + 1, timeout=0,
                  allowed_updates=["message", "channel_post",
                                   "my_chat_member"])``
  2. Walk the response: for each ``chat`` we see, build / merge a
     :class:`Chat` record. For forum supergroups, harvest
     ``message_thread_id`` and ``forum_topic_created`` events into the
     chat's topic list.
  3. Persist the highest ``update_id`` so the next poll won't replay.

If Telegram returns 409 (webhook is active), we surface a
:class:`WebhookActive` error; callers offer the user a one-click
"Delete webhook" affordance via :meth:`ChatDiscovery.delete_webhook`.

A successful poll fires ``done(int_new_chats, int_new_topics)``; a
failure fires ``failed(ApiError)``.
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import QObject, Signal

from .api import BotApi
from .errors import ApiError, WebhookActive
from .settings import Chat, TelegramSettings, Topic


ALLOWED_UPDATES: List[str] = [
    "message",
    "edited_message",
    "channel_post",
    "edited_channel_post",
    "my_chat_member",
]


class ChatDiscovery(QObject):
    """Run one ``getUpdates`` round and merge results into settings."""

    done = Signal(int, int)              # (new_chats, new_topics)
    failed = Signal(object)              # ApiError

    def __init__(
        self,
        *,
        api: BotApi,
        settings: TelegramSettings,
        bot_id: str,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._api = api
        self._settings = settings
        self._bot_id = bot_id
        self._cancelled = False

    def start(self) -> None:
        bot = self._settings.bot_by_id(self._bot_id)
        if bot is None:
            self.failed.emit(ApiError("bot not found", status=0))
            return
        call = self._api.get_updates(
            offset=bot.last_update_id + 1,
            timeout=0,
            allowed_updates=ALLOWED_UPDATES,
            limit=100,
        )
        call.succeeded.connect(self._on_ok)
        call.failed.connect(self._on_fail)

    def cancel(self) -> None:
        self._cancelled = True

    def delete_webhook(self) -> None:
        """Call ``deleteWebhook`` then re-run discovery."""
        call = self._api.delete_webhook(drop_pending_updates=False)
        call.succeeded.connect(lambda _r: self.start())
        call.failed.connect(self._on_fail)

    # -- internals --------------------------------------------------- #

    def _on_ok(self, payload) -> None:
        if self._cancelled:
            return
        # ``payload`` is either a list (the result was an array) or
        # ``{"result": [...]}`` from our ``ApiCall`` shim. Normalize.
        if isinstance(payload, dict) and "result" in payload:
            updates = payload["result"]
        elif isinstance(payload, list):
            updates = payload
        else:
            updates = []
        new_chats, new_topics, highest = _merge_updates(
            updates, self._settings, self._bot_id
        )
        if highest > 0:
            self._settings.set_last_update_id(self._bot_id, highest)
        self.done.emit(new_chats, new_topics)

    def _on_fail(self, err) -> None:
        if self._cancelled:
            return
        # WebhookActive is a special case that the caller may want to
        # recover from with one click.
        self.failed.emit(err if isinstance(err, ApiError) else ApiError(str(err)))


# ---------------------------------------------------------------------------- #
# Pure merge logic (testable without Qt)                                       #
# ---------------------------------------------------------------------------- #


def _merge_updates(
    updates: list,
    settings: TelegramSettings,
    bot_id: str,
) -> tuple[int, int, int]:
    """Merge an ``Update[]`` response into ``settings``.

    Returns ``(new_chats, new_topics, highest_update_id)``.
    """
    bot = settings.bot_by_id(bot_id)
    if bot is None or not isinstance(updates, list):
        return (0, 0, 0)

    known_chat_ids = {c.id for c in bot.chats}
    known_topic_pairs = {
        (c.id, t.id) for c in bot.chats for t in c.topics
    }

    new_chats = 0
    new_topics = 0
    highest = bot.last_update_id

    for upd in updates:
        if not isinstance(upd, dict):
            continue
        try:
            uid = int(upd.get("update_id") or 0)
        except (TypeError, ValueError):
            uid = 0
        if uid > highest:
            highest = uid

        # Pick the chat off whichever container is present.
        msg = (
            upd.get("message")
            or upd.get("edited_message")
            or upd.get("channel_post")
            or upd.get("edited_channel_post")
            or upd.get("my_chat_member")
            or {}
        )
        if not isinstance(msg, dict):
            continue
        raw_chat = msg.get("chat") if "chat" in msg else None
        if not isinstance(raw_chat, dict):
            continue

        chat = _chat_from_raw(raw_chat)
        if chat is None:
            continue
        chat.last_seen = int(msg.get("date") or 0) or chat.last_seen

        # Forum topic discovery from ``message_thread_id`` + the
        # ``forum_topic_created`` service event.
        thread_id = msg.get("message_thread_id")
        topic_name = ""
        forum_created = msg.get("forum_topic_created")
        if isinstance(forum_created, dict):
            topic_name = str(forum_created.get("name") or "")
        if thread_id is not None and chat.is_forum:
            try:
                tid = int(thread_id)
                topic = Topic(
                    id=tid,
                    name=topic_name or f"Topic {tid}",
                    last_seen=chat.last_seen,
                )
                chat.topics = [topic]
                if (chat.id, tid) not in known_topic_pairs:
                    new_topics += 1
                    known_topic_pairs.add((chat.id, tid))
            except (TypeError, ValueError):
                pass

        if chat.id not in known_chat_ids:
            new_chats += 1
            known_chat_ids.add(chat.id)
        settings.upsert_chat(bot_id, chat)

    return (new_chats, new_topics, highest)


def _chat_from_raw(raw: dict) -> Optional[Chat]:
    try:
        chat_id = int(raw["id"])
    except (KeyError, TypeError, ValueError):
        return None
    chat_type = str(raw.get("type") or "")
    title = str(raw.get("title") or "")
    if not title:
        # Private chats use first_name / username instead.
        title = (
            str(raw.get("first_name") or "")
            + (" " + str(raw.get("last_name") or "") if raw.get("last_name") else "")
        ).strip()
        if not title:
            title = "@" + str(raw.get("username") or chat_id)
    return Chat(
        id=chat_id,
        type=chat_type,
        title=title,
        username=str(raw.get("username") or ""),
        is_forum=bool(raw.get("is_forum") or False),
    )
