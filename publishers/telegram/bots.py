"""Bot registry: lifecycle and per-bot ``BotApi`` reuse.

The registry is owned by :class:`publishers.telegram.facade.TelegramPublisher`
and is the single source of ``BotApi`` instances. Keeping the API
clients pooled means TLS sessions stay warm and request setup is cheap.

The registry never touches the network directly: callers run a
``getMe`` themselves before asking the registry to persist a new bot.
This keeps the verify-then-save dance explicit at the UI layer.
"""

from __future__ import annotations

from typing import Dict, Optional

from PySide6.QtCore import QObject

from .api import BotApi
from .settings import Bot, TelegramSettings


class BotRegistry(QObject):
    """Pool of ``BotApi`` instances keyed by stable bot ``id``."""

    def __init__(self, settings: TelegramSettings, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._apis: Dict[str, BotApi] = {}

    def api_for(self, bot_id: str) -> Optional[BotApi]:
        """Return the ``BotApi`` for ``bot_id`` or ``None`` if no such bot."""
        bot = self._settings.bot_by_id(bot_id)
        if bot is None:
            return None
        api = self._apis.get(bot_id)
        if api is None:
            api = BotApi(bot.token, parent=self)
            self._apis[bot_id] = api
        return api

    def discard(self, bot_id: str) -> None:
        """Drop the cached ``BotApi`` (call after token replace or remove)."""
        api = self._apis.pop(bot_id, None)
        if api is not None:
            api.deleteLater()

    def discard_all(self) -> None:
        for api in self._apis.values():
            api.deleteLater()
        self._apis.clear()


def parse_token(raw: str) -> str:
    """Clean a pasted token: trim, drop common BotFather prose prefixes.

    BotFather's reply format isn't a stable contract, so rather than
    matching the exact phrase we drop everything up to and including
    the last colon-space before what looks like a token (``digits:rest``).
    """
    s = (raw or "").strip()
    if not s:
        return ""
    # Walk back from the end and find the first run that looks like
    # ``<digits>:<rest>`` with no whitespace.
    for chunk in s.split():
        if ":" in chunk and chunk.split(":", 1)[0].isdigit():
            return chunk
    # Fall back to whatever they pasted, after a single strip.
    return s


def looks_like_token(s: str) -> bool:
    """Sanity check: ``digits:non-empty``."""
    if not s or ":" not in s:
        return False
    head, tail = s.split(":", 1)
    return head.isdigit() and bool(tail.strip())


__all__ = ["Bot", "BotRegistry", "parse_token", "looks_like_token"]
