"""Persistent Telegram-publisher settings.

One JSON file at ``~/.config/my_editor/telegram.json`` holds the full
state: every bot the user has added, each bot's discovered chats, each
chat's forum topics, the last ``getUpdates`` cursor, and the UI's
remembered defaults.

The schema is intentionally explicit (no nested ``Any``) so a typo in
a future migration shows up at load time, not at send time.

Bot ``id`` is a local random string; it survives Telegram renaming
the bot's ``@username``. Every other module references bots by this
id, never by token or username.

Tokens are stored in plaintext inside a ``0o700`` directory with
``0o600`` file perms. Same threat model and storage scheme as the
Nostr bunker config in this app.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import List, Optional

from ._atomic import SETTINGS_DIR, load_json, save_json


SETTINGS_FILE: Path = SETTINGS_DIR / "telegram.json"

SCHEMA_VERSION: int = 1

# Eight muted, distinguishable colors (HSL-spaced) for the per-bot
# letter avatar. New bots cycle through this list, skipping colors
# already in use.
BOT_COLORS: tuple[str, ...] = (
    "#4F86C6",
    "#C65F5F",
    "#6FAF6F",
    "#C68F3E",
    "#9468B0",
    "#3FA3A3",
    "#B85C9E",
    "#7A8C99",
)


@dataclass
class Topic:
    """One forum-topic discovered inside a supergroup."""

    id: int
    name: str
    last_seen: int = 0


@dataclass
class Chat:
    """A chat the bot can post into.

    ``type`` is one of Telegram's chat types: ``"private"``,
    ``"group"``, ``"supergroup"``, ``"channel"``.
    """

    id: int
    type: str
    title: str
    username: str = ""
    is_forum: bool = False
    topics: List[Topic] = field(default_factory=list)
    last_seen: int = 0
    last_sent_at: int = 0
    default_select: bool = False
    starred: bool = False

    def topic_by_id(self, topic_id: int) -> Optional[Topic]:
        for t in self.topics:
            if t.id == topic_id:
                return t
        return None


@dataclass
class Bot:
    """One Telegram bot the user has connected."""

    id: str
    display_name: str
    color: str
    token: str
    telegram_username: str = ""
    telegram_first_name: str = ""
    telegram_user_id: int = 0
    added_at: int = 0
    last_verified: int = 0
    last_update_id: int = 0
    invalid: bool = False
    chats: List[Chat] = field(default_factory=list)

    def chat_by_id(self, chat_id: int) -> Optional[Chat]:
        for c in self.chats:
            if c.id == chat_id:
                return c
        return None


@dataclass
class UiPrefs:
    auto_split: bool = True
    silent: bool = False
    disable_link_preview: bool = False
    protect_content: bool = False
    pin_after_sending: bool = False


# ---------------------------------------------------------------------------- #
# Store                                                                        #
# ---------------------------------------------------------------------------- #


class TelegramSettings:
    """In-memory view over ``telegram.json`` with explicit save calls.

    A single instance lives for the lifetime of the editor process;
    every mutation method writes through to disk before returning.
    """

    def __init__(self, path: Path = SETTINGS_FILE) -> None:
        self._path = path
        self._default_bot_id: str = ""
        self._bots: List[Bot] = []
        self._ui = UiPrefs()
        self._load()

    # -- read -------------------------------------------------------- #

    @property
    def bots(self) -> List[Bot]:
        """All configured bots in stable (insertion) order."""
        return list(self._bots)

    @property
    def ui(self) -> UiPrefs:
        return self._ui

    @property
    def default_bot(self) -> Optional[Bot]:
        """Bot pre-selected by the publish dialog, or ``None``."""
        bot = self.bot_by_id(self._default_bot_id)
        if bot is not None:
            return bot
        return self._bots[0] if self._bots else None

    def bot_by_id(self, bot_id: str) -> Optional[Bot]:
        for b in self._bots:
            if b.id == bot_id:
                return b
        return None

    def bot_by_telegram_user_id(self, telegram_user_id: int) -> Optional[Bot]:
        for b in self._bots:
            if b.telegram_user_id == telegram_user_id:
                return b
        return None

    # -- mutate: bots ------------------------------------------------ #

    def add_bot(
        self,
        *,
        display_name: str,
        token: str,
        telegram_username: str,
        telegram_first_name: str,
        telegram_user_id: int,
        make_default: bool = False,
    ) -> Bot:
        """Persist a new bot and return its record.

        ``display_name`` is the user-facing name. ``token`` and the
        ``telegram_*`` fields come from a successful ``getMe`` round
        trip done by the caller.
        """
        if not display_name.strip():
            raise ValueError("display_name must be non-empty")
        if self.bot_by_telegram_user_id(telegram_user_id) is not None:
            raise ValueError("a bot with this Telegram user id already exists")
        bot = Bot(
            id=_new_bot_id(),
            display_name=display_name.strip(),
            color=self._next_color(),
            token=token.strip(),
            telegram_username=telegram_username.strip(),
            telegram_first_name=telegram_first_name.strip(),
            telegram_user_id=int(telegram_user_id),
            added_at=int(time.time()),
            last_verified=int(time.time()),
        )
        self._bots.append(bot)
        if make_default or not self._default_bot_id:
            self._default_bot_id = bot.id
        self._save()
        return bot

    def remove_bot(self, bot_id: str) -> None:
        before = len(self._bots)
        self._bots = [b for b in self._bots if b.id != bot_id]
        if len(self._bots) == before:
            return
        if self._default_bot_id == bot_id:
            self._default_bot_id = self._bots[0].id if self._bots else ""
        self._save()

    def rename_bot(self, bot_id: str, display_name: str) -> None:
        bot = self.bot_by_id(bot_id)
        if bot is None or not display_name.strip():
            return
        bot.display_name = display_name.strip()
        self._save()

    def recolor_bot(self, bot_id: str, color: str) -> None:
        bot = self.bot_by_id(bot_id)
        if bot is None or not color:
            return
        bot.color = color
        self._save()

    def set_default_bot(self, bot_id: str) -> None:
        if self.bot_by_id(bot_id) is None:
            return
        self._default_bot_id = bot_id
        self._save()

    def replace_token(self, bot_id: str, token: str) -> None:
        """Update a bot's token in place (e.g. after revoke + reissue)."""
        bot = self.bot_by_id(bot_id)
        if bot is None or not token.strip():
            return
        bot.token = token.strip()
        bot.invalid = False
        bot.last_verified = int(time.time())
        self._save()

    def mark_bot_invalid(self, bot_id: str) -> None:
        bot = self.bot_by_id(bot_id)
        if bot is None or bot.invalid:
            return
        bot.invalid = True
        self._save()

    def update_bot_identity(
        self,
        bot_id: str,
        *,
        telegram_username: str,
        telegram_first_name: str,
    ) -> None:
        bot = self.bot_by_id(bot_id)
        if bot is None:
            return
        changed = (
            bot.telegram_username != telegram_username
            or bot.telegram_first_name != telegram_first_name
        )
        bot.telegram_username = telegram_username
        bot.telegram_first_name = telegram_first_name
        bot.last_verified = int(time.time())
        if changed:
            self._save()

    # -- mutate: chats ----------------------------------------------- #

    def upsert_chat(self, bot_id: str, chat: Chat) -> None:
        """Merge ``chat`` into the bot's cache, preserving star/select state."""
        bot = self.bot_by_id(bot_id)
        if bot is None:
            return
        existing = bot.chat_by_id(chat.id)
        if existing is None:
            bot.chats.append(chat)
        else:
            existing.type = chat.type or existing.type
            existing.title = chat.title or existing.title
            existing.username = chat.username or existing.username
            existing.is_forum = chat.is_forum or existing.is_forum
            for t in chat.topics:
                existing_topic = existing.topic_by_id(t.id)
                if existing_topic is None:
                    existing.topics.append(t)
                else:
                    existing_topic.name = t.name or existing_topic.name
                    existing_topic.last_seen = max(
                        existing_topic.last_seen, t.last_seen
                    )
            if chat.last_seen:
                existing.last_seen = max(existing.last_seen, chat.last_seen)
        self._save()

    def remove_chat(self, bot_id: str, chat_id: int) -> None:
        bot = self.bot_by_id(bot_id)
        if bot is None:
            return
        before = len(bot.chats)
        bot.chats = [c for c in bot.chats if c.id != chat_id]
        if len(bot.chats) != before:
            self._save()

    def mark_chat_sent(self, bot_id: str, chat_id: int, when: int) -> None:
        bot = self.bot_by_id(bot_id)
        if bot is None:
            return
        chat = bot.chat_by_id(chat_id)
        if chat is None:
            return
        if when > chat.last_sent_at:
            chat.last_sent_at = when
            self._save()

    def set_chat_starred(self, bot_id: str, chat_id: int, starred: bool) -> None:
        bot = self.bot_by_id(bot_id)
        if bot is None:
            return
        chat = bot.chat_by_id(chat_id)
        if chat is None or chat.starred == starred:
            return
        chat.starred = starred
        self._save()

    def set_last_update_id(self, bot_id: str, update_id: int) -> None:
        bot = self.bot_by_id(bot_id)
        if bot is None:
            return
        if update_id > bot.last_update_id:
            bot.last_update_id = int(update_id)
            self._save()

    # -- mutate: UI prefs -------------------------------------------- #

    def set_ui(self, **kwargs) -> None:
        changed = False
        for key, value in kwargs.items():
            if hasattr(self._ui, key) and getattr(self._ui, key) != value:
                setattr(self._ui, key, value)
                changed = True
        if changed:
            self._save()

    # -- internals --------------------------------------------------- #

    def _next_color(self) -> str:
        in_use = {b.color for b in self._bots}
        for c in BOT_COLORS:
            if c not in in_use:
                return c
        return BOT_COLORS[len(self._bots) % len(BOT_COLORS)]

    def _load(self) -> None:
        data = load_json(self._path)
        if not isinstance(data, dict):
            return
        if data.get("version") != SCHEMA_VERSION:
            return
        self._default_bot_id = str(data.get("default_bot_id") or "")
        for raw in data.get("bots") or []:
            bot = _bot_from_dict(raw)
            if bot is not None:
                self._bots.append(bot)
        ui_raw = data.get("ui")
        if isinstance(ui_raw, dict):
            for k in ("auto_split", "silent", "disable_link_preview",
                      "protect_content", "pin_after_sending"):
                if k in ui_raw and isinstance(ui_raw[k], bool):
                    setattr(self._ui, k, ui_raw[k])

    def _save(self) -> None:
        payload = {
            "version": SCHEMA_VERSION,
            "default_bot_id": self._default_bot_id,
            "bots": [_bot_to_dict(b) for b in self._bots],
            "ui": asdict(self._ui),
        }
        save_json(self._path, payload)


# ---------------------------------------------------------------------------- #
# Serialization helpers                                                        #
# ---------------------------------------------------------------------------- #


def _new_bot_id() -> str:
    return "b_" + secrets.token_hex(4)


def _bot_to_dict(b: Bot) -> dict:
    return {
        "id": b.id,
        "display_name": b.display_name,
        "color": b.color,
        "token": b.token,
        "telegram_username": b.telegram_username,
        "telegram_first_name": b.telegram_first_name,
        "telegram_user_id": b.telegram_user_id,
        "added_at": b.added_at,
        "last_verified": b.last_verified,
        "last_update_id": b.last_update_id,
        "invalid": b.invalid,
        "chats": [_chat_to_dict(c) for c in b.chats],
    }


def _chat_to_dict(c: Chat) -> dict:
    return {
        "id": c.id,
        "type": c.type,
        "title": c.title,
        "username": c.username,
        "is_forum": c.is_forum,
        "topics": [asdict(t) for t in c.topics],
        "last_seen": c.last_seen,
        "last_sent_at": c.last_sent_at,
        "default_select": c.default_select,
        "starred": c.starred,
    }


def _bot_from_dict(raw: dict) -> Optional[Bot]:
    try:
        bot = Bot(
            id=str(raw["id"]),
            display_name=str(raw["display_name"]),
            color=str(raw.get("color") or BOT_COLORS[0]),
            token=str(raw["token"]),
            telegram_username=str(raw.get("telegram_username") or ""),
            telegram_first_name=str(raw.get("telegram_first_name") or ""),
            telegram_user_id=int(raw.get("telegram_user_id") or 0),
            added_at=int(raw.get("added_at") or 0),
            last_verified=int(raw.get("last_verified") or 0),
            last_update_id=int(raw.get("last_update_id") or 0),
            invalid=bool(raw.get("invalid") or False),
        )
    except (KeyError, TypeError, ValueError):
        return None
    for c_raw in raw.get("chats") or []:
        chat = _chat_from_dict(c_raw)
        if chat is not None:
            bot.chats.append(chat)
    return bot


def _chat_from_dict(raw: dict) -> Optional[Chat]:
    try:
        chat = Chat(
            id=int(raw["id"]),
            type=str(raw.get("type") or ""),
            title=str(raw.get("title") or ""),
            username=str(raw.get("username") or ""),
            is_forum=bool(raw.get("is_forum") or False),
            last_seen=int(raw.get("last_seen") or 0),
            last_sent_at=int(raw.get("last_sent_at") or 0),
            default_select=bool(raw.get("default_select") or False),
            starred=bool(raw.get("starred") or False),
        )
    except (KeyError, TypeError, ValueError):
        return None
    for t_raw in raw.get("topics") or []:
        try:
            chat.topics.append(
                Topic(
                    id=int(t_raw["id"]),
                    name=str(t_raw.get("name") or ""),
                    last_seen=int(t_raw.get("last_seen") or 0),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return chat
