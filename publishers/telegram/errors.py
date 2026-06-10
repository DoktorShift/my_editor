"""Mapped error taxonomy for Telegram Bot API responses.

Telegram returns rich human-readable error descriptions, but UX should
not display them verbatim: the wording varies, leaks API jargon, and
hides the actual fix from the user. ``classify()`` turns a Telegram
JSON response into a typed :class:`ApiError` carrying the field the
caller needs: status code, raw description, the proposed remedy, and
(for 429) a ``retry_after`` hint.

Reference: https://core.telegram.org/bots/api
Per-class fragments are stable substrings observed in the description
field, verified against the docs and community-maintained error tables.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


class ApiError(Exception):
    """Base for every Telegram API failure surfaced to the caller.

    ``status`` is the HTTP status code returned by api.telegram.org;
    ``description`` is Telegram's own error string (already
    user-readable but jargon-heavy).
    """

    status: int = 0
    description: str = ""

    def __init__(self, description: str, status: int = 0) -> None:
        super().__init__(description)
        self.description = description
        self.status = status

    @property
    def user_message(self) -> str:
        """Short, action-oriented sentence for a UI label."""
        return self.description or "Telegram API error."


class InvalidToken(ApiError):
    @property
    def user_message(self) -> str:
        return "The bot token is invalid or has been revoked."


class UserBlockedBot(ApiError):
    @property
    def user_message(self) -> str:
        return "This user has blocked the bot."


class UserNeverStarted(ApiError):
    @property
    def user_message(self) -> str:
        return "The user must send /start to the bot before it can DM them."


class BotRemoved(ApiError):
    @property
    def user_message(self) -> str:
        return "The bot was removed from this chat."


class NoPostRights(ApiError):
    @property
    def user_message(self) -> str:
        return "The bot is not allowed to post here. Make it an admin with post permission."


class StaleFileId(ApiError):
    @property
    def user_message(self) -> str:
        return "Cached file id is no longer valid; re-uploading."


class MessageTooLong(ApiError):
    @property
    def user_message(self) -> str:
        return "The message is too long for a single Telegram post."


class BadHtml(ApiError):
    @property
    def user_message(self) -> str:
        return "The formatted message could not be parsed by Telegram."


class ChatNotFound(ApiError):
    @property
    def user_message(self) -> str:
        return "Telegram cannot find this chat. It may have been deleted."


class WebhookActive(ApiError):
    @property
    def user_message(self) -> str:
        return "This bot has a webhook configured. Delete it to use polling."


class ConcurrentPoller(ApiError):
    @property
    def user_message(self) -> str:
        return "Another process is already polling this bot."


@dataclass(eq=False)
class RateLimited(ApiError):
    retry_after: int = 1

    def __init__(self, description: str, retry_after: int, status: int = 429) -> None:
        ApiError.__init__(self, description=description, status=status)
        self.retry_after = max(1, int(retry_after))

    @property
    def user_message(self) -> str:
        return f"Telegram rate-limited the bot. Retrying in {self.retry_after}s."


class Transport(ApiError):
    """Networking failure: DNS, TLS, connection refused, timeout, etc."""

    @property
    def user_message(self) -> str:
        return "Telegram unreachable. Check your internet connection."


# ---------------------------------------------------------------------------- #
# Classifier                                                                   #
# ---------------------------------------------------------------------------- #


_SUBSTRINGS_403 = (
    ("bot was blocked by the user", UserBlockedBot),
    ("bot can't initiate conversation", UserNeverStarted),
    ("bot was kicked", BotRemoved),
    ("bot is not a member", BotRemoved),
    ("user is deactivated", UserBlockedBot),
)

_SUBSTRINGS_400 = (
    ("not enough rights to send", NoPostRights),
    ("have no rights to send", NoPostRights),
    ("chat_admin_required", NoPostRights),
    ("need administrator rights", NoPostRights),
    ("wrong file_id", StaleFileId),
    ("wrong type of the web page content", StaleFileId),
    ("wrong remote file identifier", StaleFileId),
    ("message is too long", MessageTooLong),
    ("can't parse entities", BadHtml),
    ("unsupported start tag", BadHtml),
    ("unsupported end tag", BadHtml),
    ("chat not found", ChatNotFound),
)

_SUBSTRINGS_409 = (
    ("webhook", WebhookActive),
    ("terminated by other getupdates", ConcurrentPoller),
)


def classify(status: int, body: dict) -> ApiError:
    """Map a Telegram error response to a typed :class:`ApiError`.

    ``body`` is the parsed JSON returned by the API. We expect the
    ``"description"`` field; we tolerate it missing.
    """
    description = str(body.get("description") or "")
    desc_lower = description.lower()
    params = body.get("parameters") or {}

    if status == 429:
        retry_after = params.get("retry_after") if isinstance(params, dict) else None
        return RateLimited(description, retry_after or 1)

    if status == 401:
        return InvalidToken(description, status)

    if status == 403:
        for needle, cls in _SUBSTRINGS_403:
            if needle in desc_lower:
                return cls(description, status)
        return ApiError(description, status)

    if status == 400:
        for needle, cls in _SUBSTRINGS_400:
            if needle in desc_lower:
                return cls(description, status)
        return ApiError(description, status)

    if status == 409:
        for needle, cls in _SUBSTRINGS_409:
            if needle in desc_lower:
                return cls(description, status)
        return ApiError(description, status)

    if status == 404:
        return ChatNotFound(description or "not found", status)

    return ApiError(description or f"HTTP {status}", status)


def transport_error(reason: str) -> Transport:
    """Build a :class:`Transport` from a network-layer failure string."""
    return Transport(reason or "network error", status=0)
